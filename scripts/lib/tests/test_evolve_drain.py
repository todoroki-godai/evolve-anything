"""evolve drain enforcement のユニットテスト（#402）。

#402: ingest（Step 7.8 drain）が SKILL.md prose 依存だった enforcement gap を是正する。
本テストは drain の3要素を決定論で固定する:

  1. **pending marker**（emit が dry-run でも書く運用ポインタ。評価 store/queue とは別物）
  2. **drain_pending**（`evolve --drain` の実体。marker or result-json から pending を取り
     ingest→冪等記録→marker クリア）
  3. **undrained_applied**（SessionStart リマインドの signal。store を読まず #358 を踏まない）

すべて LLM-free・決定論。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402


_BEFORE = "# my-skill\n\nトリガー: foo\n\n旧手順。\n"
_AFTER = "# my-skill\n\nトリガー: foo bar baz\n\n改善された手順を踏む。\n"


@pytest.fixture
def skill_file(tmp_path):
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text(_BEFORE, encoding="utf-8")
    return p


@pytest.fixture
def result_with_match(skill_file):
    return {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p"}
                ]
            }
        }
    }


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """marker / queue / store を全て temp に隔離する。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    return tmp_path


def _store_count(slug="testslug"):
    return len(ohs.load_history(slug))


# ─── 1. marker は環境非依存（hook/tool で割れない＝#358 回避） ────────────────


@pytest.mark.real_marker_root
@pytest.mark.real_home  # MARKER_ROOT は import 時に実 home で凍結。autouse の HOME 隔離をオプトアウト（#471）
def test_marker_root_is_home_based_not_env_derived():
    # QUEUE_ROOT は DATA_DIR(env 派生) 配下だが、MARKER_ROOT は home 固定。
    # これにより emit(tool 文脈)と SessionStart(hook 文脈)が同一パスに合意する。
    assert ed.MARKER_ROOT == Path.home() / ".claude" / "evolve-anything" / "evolve_pending"
    assert "evolve_pending" in str(ed.marker_path("anything"))


# ─── 2. emit は dry-run でも marker を書くが store/queue は触らない ───────────


def test_emit_dry_run_writes_marker_but_not_queue_or_store(result_with_match, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    assert out["count"] == 1
    # marker は書かれる（drain 検出の signal）
    marker = ed.read_pending_marker("testslug")
    assert marker is not None
    assert len(marker["pending"]) == 1
    assert marker["pending"][0]["before_sha"]
    # 評価状態（queue / optimize_history）は dry-run で一切触らない
    assert ed.read_queue("testslug") == []
    assert _store_count() == 0


def test_emit_empty_pending_clears_stale_marker(result_with_match, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    assert ed.read_pending_marker("testslug") is not None
    # 候補ゼロの run → 古い marker を消す（drain 待ちは無い）
    ed.emit_decisions({"phases": {}}, dry_run=True, slug="testslug")
    assert ed.read_pending_marker("testslug") is None


# ─── 3. undrained_applied は apply 済みのみ返し store を読まない ──────────────


def test_undrained_applied_empty_when_nothing_applied(result_with_match, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    # まだ apply していない → 未 drain だが「適用済み」ではない → 沈黙
    assert ed.undrained_applied("testslug") == []


def test_undrained_applied_returns_applied_skill(result_with_match, skill_file, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply 境界
    applied = ed.undrained_applied("testslug")
    assert len(applied) == 1
    assert applied[0]["skill_name"] == "my-skill"


def test_undrained_applied_empty_when_no_marker(isolated):
    assert ed.undrained_applied("nope") == []


# ─── 4. drain_pending: apply 後に記録し marker をクリア（CLI 実体） ───────────


def test_drain_pending_records_accept_and_clears_marker(result_with_match, skill_file, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply
    summary = ed.drain_pending(slug="testslug")
    assert len(summary["accepted"]) == 1
    assert _store_count() == 1  # 母集団 +1
    assert ohs.load_history("testslug")[-1]["human_accepted"] is True
    assert ed.read_pending_marker("testslug") is None  # marker クリア


def test_drain_pending_nothing_applied_records_nothing(result_with_match, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    summary = ed.drain_pending(slug="testslug")  # 未 apply
    assert summary["accepted"] == []
    assert _store_count() == 0
    assert ed.read_pending_marker("testslug") is None


def test_drain_pending_reads_result_json_when_given(result_with_match, skill_file, isolated, tmp_path):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    rj = tmp_path / "result.json"
    rj.write_text(json.dumps({"evolve_decisions": out}), encoding="utf-8")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply
    summary = ed.drain_pending(slug="testslug", result_json=str(rj))
    assert len(summary["accepted"]) == 1
    assert _store_count() == 1


def test_drain_pending_idempotent_second_call_no_double(result_with_match, skill_file, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")
    ed.drain_pending(slug="testslug")
    # 2回目（marker 再生成して再 drain しても二重記録なし＝冪等）
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    ed.drain_pending(slug="testslug")
    assert _store_count() == 1


def test_drain_pending_explicit_reject_records_negative(result_with_match, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    pid = out["pending"][0]["id"]
    summary = ed.drain_pending(slug="testslug", rejected={pid: "ドメイン不一致"})
    assert summary["rejected"] == [pid]
    assert _store_count() == 1
    assert ohs.load_history("testslug")[-1]["human_accepted"] is False
