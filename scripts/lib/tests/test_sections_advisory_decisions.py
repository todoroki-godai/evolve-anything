"""Advisory Decisions observability section のテスト（#284 / #267 Sprint 1 / #381）。

記録0件のPJでは沈黙する（observability を空に保つ既存契約）ことと、
detector 別 surfaced/accept/reject/未判断/ever deferred テーブル・cohort 内 accept+reject
が閾値以上で accept 0 件の detector の ⚠ を byte で固定する。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import advisory_decision_log as adl  # noqa: E402
import rl_common  # noqa: E402
from audit.sections_advisory_decisions import (  # noqa: E402
    MIN_DECIDED_FOR_WARNING,
    build_advisory_decisions_section,
)
from evolve_decisions import resolve_slug  # noqa: E402


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(rl_common, "DATA_DIR", data_dir)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return data_dir


def _record(slug, detector_id, decision, proposal_id, now=None):
    adl.record_advisory_decision(
        slug=slug,
        proposal_id=proposal_id,
        detector_id=detector_id,
        target_path="pytest.ini",
        decision=decision,
        now=now,
    )


def test_section_is_silent_when_no_records(isolated, tmp_path):
    assert build_advisory_decisions_section(tmp_path) is None


def test_section_renders_per_detector_table(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    _record(slug, "invalid_frontmatter", "surfaced", "adv_1")
    _record(slug, "invalid_frontmatter", "accept", "adv_1")
    _record(slug, "invalid_frontmatter", "surfaced", "adv_2")
    _record(slug, "invalid_frontmatter", "accept", "adv_2")
    _record(slug, "invalid_frontmatter", "surfaced", "adv_3")
    _record(slug, "invalid_frontmatter", "reject", "adv_3")

    lines = build_advisory_decisions_section(tmp_path)

    # surfaced=3, accept=2(cohort内・legacy無し), reject=1(cohort内)、未判断=0（全件 terminal 済）
    assert "| invalid_frontmatter | 3 | 2 | 1 | 0 | 0 | 67% |" in lines
    assert not any(line.startswith("⚠") for line in lines)


def test_section_does_not_flag_below_decision_threshold(isolated, tmp_path):
    """判断済み（cohort 内 accept+reject）が閾値未満なら accept 0 件でも ⚠ を出さない。

    #381 tacchi レビュー: 旧条件は accept==0 単独判定で、まだ人間が y/n していないだけの
    detector にも冤罪の ⚠ を出していた。
    """
    slug = resolve_slug(tmp_path)
    _record(slug, "testpaths_coverage", "surfaced", "adv_1")
    _record(slug, "testpaths_coverage", "reject", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert not any(line.startswith("⚠") for line in lines)


def test_section_flags_detector_with_zero_accepts_after_threshold(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    for i in range(MIN_DECIDED_FOR_WARNING):
        pid = f"adv_{i}"
        _record(slug, "testpaths_coverage", "surfaced", pid)
        _record(slug, "testpaths_coverage", "reject", pid)

    lines = build_advisory_decisions_section(tmp_path)

    assert any(
        line.startswith("⚠") and "testpaths_coverage" in line for line in lines
    )


def test_open_column_shows_current_undecided_count(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    _record(slug, "testpaths_coverage", "surfaced", "adv_1")
    _record(slug, "testpaths_coverage", "deferred", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    # surfaced=1・未判断(open)=1・ever deferred=1
    assert "| testpaths_coverage | 1 | 0 | 0 | 1 | 1 | 0% |" in lines


def test_open_excludes_terminal_but_ever_deferred_persists(isolated, tmp_path):
    """deferred のち accept された提案は 未判断(open) から外れるが ever deferred は残る（非排他）。"""
    slug = resolve_slug(tmp_path)
    _record(slug, "testpaths_coverage", "surfaced", "adv_1")
    _record(slug, "testpaths_coverage", "deferred", "adv_1")
    _record(slug, "testpaths_coverage", "accept", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| testpaths_coverage | 1 | 1 | 0 | 0 | 1 | 100% |" in lines


def test_section_shows_dashed_rate_for_legacy_accept_without_surfaced(isolated, tmp_path):
    """#284 時点（surfaced 未実装）の既存 accept 記録は surfaced=0 のまま残る。

    分母（surfaced）が無い過去データを 0% と誤表示せず「-」にする（accept はあるのに
    分母ゼロで割り算すると誤解を招くため）。accept 列は cohort 内 0 + legacy 1 の内訳表示。
    """
    slug = resolve_slug(tmp_path)
    _record(slug, "invalid_frontmatter", "accept", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| invalid_frontmatter | 0 | 0 (+1 legacy) | 0 | 0 | 0 | - |" in lines
    assert any("surfaced 記録開始前の accept が 1 件" in line for line in lines)


def test_rate_excludes_accepts_recorded_before_surfaced_existed(isolated, tmp_path):
    """移行期間: surfaced 記録開始**前**の accept を分子に混ぜると採用率が 100% を超える。

    surfaced=1 に対し legacy accept が 2 件あっても採用率は 0%（cohort 内 accept ゼロ）で、
    除外した件数は行内内訳（accept 列）と注記の双方に surface する。
    """
    slug = resolve_slug(tmp_path)
    _record(slug, "invalid_frontmatter", "accept", "legacy_1")
    _record(slug, "invalid_frontmatter", "accept", "legacy_2")
    _record(slug, "invalid_frontmatter", "surfaced", "adv_new")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| invalid_frontmatter | 1 | 0 (+2 legacy) | 0 | 1 | 0 | 0% |" in lines
    assert any("surfaced 記録開始前の accept が 2 件" in line for line in lines)


def test_rate_counts_accept_that_has_surfaced(isolated, tmp_path):
    """cohort 内（surfaced 記録がある提案）の accept は分子に入る。"""
    slug = resolve_slug(tmp_path)
    _record(slug, "invalid_frontmatter", "surfaced", "adv_1")
    _record(slug, "invalid_frontmatter", "accept", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| invalid_frontmatter | 1 | 1 | 0 | 0 | 0 | 100% |" in lines


def test_reject_column_shows_legacy_breakdown(isolated, tmp_path):
    """reject にも cohort 分離が無いと (accept+reject)/surfaced が 100% を超えうる（#381 C-2）。

    行内の cohort 内件数だけなら合計が surfaced を超えないことを検算できる。
    """
    slug = resolve_slug(tmp_path)
    _record(slug, "testpaths_coverage", "reject", "legacy_1")
    _record(slug, "testpaths_coverage", "surfaced", "adv_new")
    _record(slug, "testpaths_coverage", "reject", "adv_new")

    lines = build_advisory_decisions_section(tmp_path)

    assert "| testpaths_coverage | 1 | 0 | 1 (+1 legacy) | 0 | 0 | 0% |" in lines
    assert any("reject が 1 件あり" in line for line in lines)


def test_section_shows_record_period(isolated, tmp_path):
    slug = resolve_slug(tmp_path)
    _record(
        slug, "invalid_frontmatter", "surfaced", "adv_1",
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    _record(
        slug, "invalid_frontmatter", "accept", "adv_1",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    lines = build_advisory_decisions_section(tmp_path)

    assert "ℹ 記録期間: 2026-05-01 〜 2026-08-12（103日）" in lines


def test_section_ignores_other_projects(isolated, tmp_path):
    _record("some-other-pj", "invalid_frontmatter", "accept", "adv_1")

    assert build_advisory_decisions_section(tmp_path) is None


def test_section_renders_header_and_trailer(isolated, tmp_path):
    _record(resolve_slug(tmp_path), "invalid_frontmatter", "accept", "adv_1")

    lines = build_advisory_decisions_section(tmp_path)

    assert lines[0] == "## Advisory Decisions"
    assert lines[-1] == ""
