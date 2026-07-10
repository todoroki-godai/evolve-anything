"""judge_audit（judge false-pass 欠陥注入監査）テスト — #188, The Blind Curator arXiv 2607.07436。

決定論・ゼロ LLM: judge の呼び出し（call_judge_llm）は **必ず mock**（no-llm-in-tests）。
read は書込を一切しない（read-only 純度）。write は store_write barrier（ADR-049）経由。
pj_slug スコープ。HOME 隔離（#457）は scripts/lib/tests/conftest の autouse fixture。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
from judge_audit import harness as _harness  # noqa: E402
from judge_audit import query as _q  # noqa: E402
from judge_audit import store as _jstore  # noqa: E402
from judge_audit.fixtures import FIXTURES  # noqa: E402
from audit.sections_judge_audit import build_judge_audit_section  # noqa: E402

SLUG = "evolve-anything"


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """DATA_DIR を tmp に向ける（read 側 store.DATA_DIR + write 側 rl_common.DATA_DIR）。"""
    d = tmp_path / "evolve-anything"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_jstore, "DATA_DIR", d)
    monkeypatch.setattr(rl_common, "DATA_DIR", d)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return d


def _write_verdicts(d: Path, recs: list) -> None:
    path = d / _jstore.VERDICTS_STORE
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )


def _verdict(fid: str, *, slug: str = SLUG, false_pass: bool = False, score: float = 0.2) -> dict:
    return {
        "id": fid,
        "pj_slug": slug,
        "principle_id": "user-consent",
        "score": score,
        "judge_passed": false_pass,
        "false_pass": false_pass,
        "judged_at": "2026-07-10T00:00:00+00:00",
    }


# ───────────────────────────── fixtures ───────────────────────────

def test_fixtures_are_deterministic_and_have_required_fields():
    """fixture は LLM 生成でなく決定論（id/principle_id/layer_name/content を持つ）。"""
    assert len(FIXTURES) >= 3
    ids = [f["id"] for f in FIXTURES]
    assert len(ids) == len(set(ids)), "fixture id は重複しない"
    for f in FIXTURES:
        assert f["id"] and f["principle_id"] and f["layer_name"] and f["content"]


# ───────────────────────────── store ──────────────────────────────

def test_read_verdicts_filters_by_slug(data_dir):
    """pj_slug でフィルタする。"""
    _write_verdicts(data_dir, [
        _verdict("user-consent-missing-confirm", slug=SLUG),
        _verdict("single-responsibility-god-rule", slug="other-pj"),
    ])
    out = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert set(out) == {"user-consent-missing-confirm"}


def test_read_verdicts_folds_legacy_slug_alias(data_dir):
    """#112: PJ rename の legacy slug（rl-anything）も canonical slug の read で拾う。"""
    _write_verdicts(data_dir, [
        _verdict("user-consent-missing-confirm", slug="evolve-anything"),
        _verdict("single-responsibility-god-rule", slug="rl-anything"),  # legacy
    ])
    out = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert set(out) == {"user-consent-missing-confirm", "single-responsibility-god-rule"}


def test_read_verdicts_missing_file_returns_empty_and_writes_nothing(data_dir):
    """ファイル不在でも {} を返し、ファイルを作らない（read-only 純度）。"""
    out = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert out == {}
    assert not (data_dir / _jstore.VERDICTS_STORE).exists()


def test_read_verdicts_last_append_wins(data_dir):
    """同一 id は append 順で last-append-wins（再実行の上書き）。"""
    path = data_dir / _jstore.VERDICTS_STORE
    path.write_text(
        "\n".join(json.dumps(r) for r in [
            _verdict("user-consent-missing-confirm", false_pass=False),
            _verdict("user-consent-missing-confirm", false_pass=True),
        ]) + "\n",
        encoding="utf-8",
    )
    v = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert v["user-consent-missing-confirm"]["false_pass"] is True


def test_write_verdict_goes_through_store_write_barrier(data_dir):
    """write_verdict は store_write("judge_audit_verdicts.jsonl") 経由（場所は内部解決）。"""
    import importlib

    sw_mod = importlib.import_module("rl_common.store_write")
    captured: dict = {}

    def fake(name, record, **kw):
        captured["name"] = name
        captured["record"] = record

    with mock.patch.object(sw_mod, "store_write", fake):
        _jstore.write_verdict(_verdict("user-consent-missing-confirm"))
    assert captured["name"] == _jstore.VERDICTS_STORE


# ───────────────────────────── query ──────────────────────────────

def test_false_pass_summary_floor_gate_hides_rate(data_dir):
    """判定済みが floor 未満なら false_pass_rate は None（不足を明示）。"""
    _write_verdicts(data_dir, [_verdict("user-consent-missing-confirm", false_pass=True)])
    s = _q.false_pass_summary(SLUG, data_dir=data_dir)
    assert s["judged"] == 1
    assert s["false_pass_rate"] is None


def test_false_pass_summary_rate_at_floor(data_dir):
    """判定済みが floor 以上なら false-pass 率を集計する。"""
    recs = [
        _verdict(f["id"], false_pass=(i == 0))
        for i, f in enumerate(FIXTURES[:4])
    ] + [_verdict("extra-5th-fixture", false_pass=False)]
    _write_verdicts(data_dir, recs)
    s = _q.false_pass_summary(SLUG, min_judged=5, data_dir=data_dir)
    assert s["judged"] == 5
    assert s["false_pass"] == 1
    assert s["false_pass_rate"] == pytest.approx(1 / 5, abs=1e-4)


def test_total_fixtures_matches_registered_count():
    assert _q.total_fixtures() == len(FIXTURES)


def test_effective_min_judged_caps_at_total_fixtures_when_smaller(monkeypatch):
    """fixture 総数が DEFAULT_MIN_JUDGED 未満なら実効 floor は総数にキャップされる。"""
    monkeypatch.setattr(_q, "FIXTURES", [{"id": f"f{i}"} for i in range(4)])
    assert _q.effective_min_judged() == 4


def test_effective_min_judged_uses_default_when_total_is_larger(monkeypatch):
    """fixture 総数が DEFAULT_MIN_JUDGED 以上なら既定 floor をそのまま使う。"""
    monkeypatch.setattr(_q, "FIXTURES", [{"id": f"f{i}"} for i in range(10)])
    assert _q.effective_min_judged() == _q.DEFAULT_MIN_JUDGED


def test_false_pass_summary_rate_available_once_all_fixtures_judged_even_below_default_floor(
    monkeypatch, data_dir
):
    """#188 レビュー修正の回帰: fixture 総数 < DEFAULT_MIN_JUDGED でも、全 fixture を
    判定済みなら false_pass_rate は必ず not None（永遠にデータ不足のまま、という
    構造的欠陥がないことを固定する）。
    """
    monkeypatch.setattr(_q, "FIXTURES", [{"id": f"f{i}"} for i in range(4)])
    _write_verdicts(data_dir, [_verdict(f"f{i}", false_pass=(i == 0)) for i in range(4)])
    s = _q.false_pass_summary(SLUG, data_dir=data_dir)
    assert s["judged"] == 4
    assert s["effective_min_judged"] == 4
    assert s["false_pass_rate"] is not None
    assert s["false_pass_rate"] == pytest.approx(1 / 4, abs=1e-4)


# ─────────────────────────── harness: dry-run ─────────────────────

def test_harness_dryrun_does_not_call_llm_or_write(data_dir, capsys):
    """dry-run は call_judge_llm を呼ばず、1 バイトも書かない（コストだけ print）。"""
    before = sorted(p.name for p in data_dir.iterdir())

    with mock.patch.object(_harness, "call_judge_llm") as m_call:
        res = _harness.run_audit(SLUG, run=False, data_dir=data_dir)

    m_call.assert_not_called()
    assert res["dry_run"] is True
    assert res["pending"] == len(FIXTURES)
    assert sorted(p.name for p in data_dir.iterdir()) == before
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "--run" in out


# ─────────────────────────── harness: --run (mock) ────────────────

def _fake_judge_response(score: float, principle_id: str = "user-consent") -> str:
    return json.dumps({
        "evaluations": [
            {"principle_id": principle_id, "score": score, "rationale": "x", "violations": []},
        ],
    })


def test_harness_run_persists_verdicts_and_computes_false_pass(data_dir, capsys):
    """--run は judge（mock）判定を verdicts へ永続化し false-pass を集計する。"""
    target = FIXTURES[0]
    with mock.patch.object(
        _harness, "call_judge_llm", return_value=_fake_judge_response(0.9, target["principle_id"])
    ) as m_call:
        res = _harness.run_audit(SLUG, run=True, limit=1, data_dir=data_dir)

    assert m_call.call_count == 1
    assert res["dry_run"] is False
    assert res["judged_now"] == 1
    # 既知の欠陥 fixture を judge が 0.9（合格閾値 0.8 以上）と誤判定 → false-pass。
    assert res["false_pass"] == 1
    assert res["false_pass_rate"] == 1.0
    assert res["verdicts_written"] == 1

    v = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert v[target["id"]]["false_pass"] is True
    assert v[target["id"]]["score"] == 0.9


def test_harness_run_detects_correct_low_score_as_not_false_pass(data_dir):
    """judge が既知の欠陥を正しく低スコア判定したら false-pass ではない。"""
    target = FIXTURES[0]
    with mock.patch.object(
        _harness, "call_judge_llm", return_value=_fake_judge_response(0.1, target["principle_id"])
    ):
        res = _harness.run_audit(SLUG, run=True, limit=1, data_dir=data_dir)
    assert res["false_pass"] == 0
    v = _jstore.read_verdicts(SLUG, data_dir=data_dir)
    assert v[target["id"]]["false_pass"] is False


def test_harness_run_no_pending_is_noop(data_dir):
    """全 fixture 判定済みなら何もしない。"""
    _write_verdicts(data_dir, [_verdict(f["id"]) for f in FIXTURES])
    with mock.patch.object(_harness, "call_judge_llm") as m_call:
        res = _harness.run_audit(SLUG, run=True, data_dir=data_dir)
    m_call.assert_not_called()
    assert res["judged_now"] == 0


def test_harness_skips_already_judged(data_dir):
    """判定済み fixture id は再判定対象から除外する（dedup）。"""
    _write_verdicts(data_dir, [_verdict(FIXTURES[0]["id"])])
    with mock.patch.object(
        _harness, "call_judge_llm", return_value=_fake_judge_response(0.9)
    ) as m_call:
        res = _harness.run_audit(SLUG, run=True, data_dir=data_dir)
    # 1件目は既判定なので除外、残り fixture のみ判定される。
    assert m_call.call_count == len(FIXTURES) - 1
    assert res["judged_now"] == len(FIXTURES) - 1


def test_build_fixture_prompt_reuses_constitutional_eval_prompt():
    """build_fixture_prompt は constitutional._build_eval_prompt を再利用する（judge 経路に流す）。"""
    prompt = _harness.build_fixture_prompt(FIXTURES[0])
    assert FIXTURES[0]["content"] in prompt
    assert FIXTURES[0]["principle_text"] in prompt


# ─────────────────────────── audit section ────────────────────────

def test_section_silent_when_no_verdicts(data_dir):
    """判定 0 件なら None（沈黙・ハーネス未実行）。"""
    with mock.patch("audit.sections_judge_audit._slug_for", return_value=SLUG):
        assert build_judge_audit_section(Path("/x/evolve-anything")) is None


def test_section_shows_insufficient_data_below_floor(data_dir):
    """判定ありで floor 未満 → データ不足 + harness --run 誘導を明示（silence != evaluated）。"""
    _write_verdicts(data_dir, [_verdict(FIXTURES[0]["id"], false_pass=True)])
    with mock.patch("audit.sections_judge_audit._slug_for", return_value=SLUG):
        lines = build_judge_audit_section(Path("/x/evolve-anything"))
    assert lines is not None
    body = "\n".join(lines)
    assert "データ不足" in body
    assert "harness.py --run" in body


def test_section_shows_rate_once_all_fixtures_judged_even_below_default_floor(
    monkeypatch, data_dir
):
    """#188 レビュー修正の回帰: fixture 総数 < DEFAULT_MIN_JUDGED でも、全 fixture 判定後は
    section が率を表示できる（永遠に「データ不足」のまま抜けられない構造的欠陥がない）。
    """
    from judge_audit import query as _jq

    monkeypatch.setattr(_jq, "FIXTURES", [{"id": f"f{i}"} for i in range(4)])
    _write_verdicts(data_dir, [_verdict(f"f{i}", false_pass=False) for i in range(4)])
    with mock.patch("audit.sections_judge_audit._slug_for", return_value=SLUG):
        lines = build_judge_audit_section(Path("/x/evolve-anything"))
    body = "\n".join(lines)
    assert "データ不足" not in body
    assert "false-pass 率" in body


def test_section_shows_warn_when_rate_above_threshold(data_dir):
    """false-pass 率が閾値超 → ⚠ を出す。"""
    recs = [_verdict(f"fixture-{i}", false_pass=(i < 2)) for i in range(5)]
    _write_verdicts(data_dir, recs)
    with mock.patch("audit.sections_judge_audit._slug_for", return_value=SLUG):
        lines = build_judge_audit_section(Path("/x/evolve-anything"))
    body = "\n".join(lines)
    assert "⚠" in body
    assert "false-pass 率" in body
    assert "The Blind Curator" in body


def test_section_shows_ok_when_rate_below_threshold(data_dir):
    """false-pass 率が閾値以下 → ✓ を出す。"""
    recs = [_verdict(f"fixture-{i}", false_pass=False) for i in range(5)]
    _write_verdicts(data_dir, recs)
    with mock.patch("audit.sections_judge_audit._slug_for", return_value=SLUG):
        lines = build_judge_audit_section(Path("/x/evolve-anything"))
    body = "\n".join(lines)
    assert "✓" in body
    assert "⚠" not in body


# ──────────────────── harness: __main__ 直接起動（回帰）───────────

def test_harness_runs_as_direct_script_dry_run(tmp_path):
    """audit が案内する `python3 scripts/lib/judge_audit/harness.py` を __main__ 直接起動できる。

    絶対 import（from judge_audit import ...）だと __main__ で ImportError にならないことを
    回帰検証する。dry-run（既定）なので LLM は呼ばない（subprocess で起動するのは python のみ）。
    """
    import os
    import subprocess

    harness_path = _lib_dir / "judge_audit" / "harness.py"
    data = tmp_path / "evolve-anything"
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
    proc = subprocess.run(
        [sys.executable, str(harness_path), "--slug", "evolve-anything"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout
    # dry-run は 1 バイトも書かない。
    assert not (data / _jstore.VERDICTS_STORE).exists()
