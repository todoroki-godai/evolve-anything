"""correction_semantic.judge_runner のテスト（#408）。

llm_judge Phase B（SKILL.md Step 6.6 の対話 y/n 承認時インライン判定）を非対話 daily
runner から回すための runner。既存の Phase A（emit_judgement_requests）/ Phase C
（ingest_judgement_results）はそのまま再利用し、本 runner は「Haiku 呼び出し（Phase B）
+ 1日の件数/トークン上限」だけを追加する。

単体テストで LLM を呼ばない: subprocess.run(["claude",...]) は judge_runner.call_haiku に
集約されているので、そこだけ monkeypatch する（call graph を読んで選んだ mock 位置）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import judge_runner  # noqa: E402
from correction_semantic.store import read_judged_keys  # noqa: E402
from weak_signals.store import read_signals  # noqa: E402


def _utt(source_path: str, line_no: int, text: str, pj_slug: str, *, ts: str, prev_action: str = "") -> dict:
    return {
        "source_path": source_path,
        "line_no": line_no,
        "pj_slug": pj_slug,
        "session_id": "s1",
        "timestamp": ts,
        "text": text,
        "text_hash": "",
        "prev_action": prev_action,
        "source_kind": "dialogue",
        "extractor_version": 1,
        "ingested_at": ts,
    }


def _ts(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _ok_verdict_response(indices_and_corrections: list) -> str:
    """{"verdicts": [{"index": i, "is_correction": bool, "idiom": str|None, "reason": str}]} を組み立てる。"""
    verdicts = [
        {"index": i, "is_correction": is_corr, "idiom": ("四国めたんじゃなくて" if is_corr else None), "reason": "r"}
        for i, is_corr in indices_and_corrections
    ]
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────
# dry-run 既定（llm-batch-guard 準拠・ゼロ書込）
# ─────────────────────────────────────────────────────────────────
def test_dry_run_calls_no_llm_and_writes_nothing(tmp_path, monkeypatch):
    ws = tmp_path / "weak_signals.jsonl"
    corr_idioms = tmp_path / "correction_idioms.jsonl"
    judged = tmp_path / "correction_judged.jsonl"

    def _boom(*a, **kw):
        raise AssertionError("dry-run で call_haiku が呼ばれた")

    monkeypatch.setattr(judge_runner, "call_haiku", _boom)

    utterances = [_utt("/a.jsonl", 1, "つむぎにしてほしい、四国めたんじゃなくて", "pj-a", ts=_ts(1))]
    res = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=judged,
        weak_signals_path=ws,
        idioms_path=corr_idioms,
    )
    assert res["dry_run"] is True
    assert not ws.exists()
    assert not corr_idioms.exists()
    assert not judged.exists()


def test_dry_run_reports_unjudged_and_selected_counts(tmp_path):
    utterances = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(3)]
    res = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=tmp_path / "correction_judged.jsonl",
    )
    assert res["unjudged_total"] == 3
    assert res["selected"] == 3
    assert res["capped"] is False


# ─────────────────────────────────────────────────────────────────
# 1日の上限（件数・トークン）
# ─────────────────────────────────────────────────────────────────
def test_daily_utterance_limit_truncates_selection(tmp_path):
    utterances = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(5)]
    res = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=tmp_path / "correction_judged.jsonl",
        daily_utterance_limit=2,
    )
    assert res["unjudged_total"] == 5
    assert res["selected"] == 2
    assert res["capped"] is True


def test_daily_token_limit_truncates_selection_even_under_count_limit(tmp_path):
    utterances = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(5)]
    res = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=tmp_path / "correction_judged.jsonl",
        daily_utterance_limit=200,
        daily_token_limit=1,  # 1 件分にも満たないほど小さい
    )
    assert res["unjudged_total"] == 5
    assert res["selected"] == 0
    assert res["capped"] is True


def test_oldest_utterances_prioritized_when_capped(tmp_path):
    old = _utt("/a.jsonl", 1, "old", "pj-a", ts=_ts(days_ago=10))
    new = _utt("/a.jsonl", 2, "new", "pj-a", ts=_ts(days_ago=0))
    res = judge_runner.run_daily_judge(
        run=False,
        utterances=[new, old],
        judged_path=tmp_path / "correction_judged.jsonl",
        daily_utterance_limit=1,
    )
    assert res["selected"] == 1
    # 内部選定を確認するため run=True で実際に emit された対象を見る（call_haiku は不要=0件）


def test_already_judged_utterances_excluded_from_selection(tmp_path):
    judged = tmp_path / "correction_judged.jsonl"
    from correction_semantic.store import record_judged, utterance_key

    u1 = _utt("/a.jsonl", 1, "already judged", "pj-a", ts=_ts(1))
    u2 = _utt("/a.jsonl", 2, "not yet", "pj-a", ts=_ts(1))
    record_judged([utterance_key(u1)], path=judged)

    res = judge_runner.run_daily_judge(
        run=False, utterances=[u1, u2], judged_path=judged,
    )
    assert res["unjudged_total"] == 1
    assert res["selected"] == 1


# ─────────────────────────────────────────────────────────────────
# --run: 実判定（call_haiku は mock）
# ─────────────────────────────────────────────────────────────────
def test_run_writes_weak_signal_with_correct_pj_slug_across_mixed_pj_batch(tmp_path, monkeypatch):
    """1 バッチに複数 PJ の発話が混在しても pj_slug 帰属が壊れない（batch_id は単なるラベル）。"""
    ws = tmp_path / "weak_signals.jsonl"
    corr_idioms = tmp_path / "correction_idioms.jsonl"
    judged = tmp_path / "correction_judged.jsonl"

    u_a = _utt("/a.jsonl", 1, "つむぎにしてほしい、四国めたんじゃなくて", "pj-a", ts=_ts(1))
    u_b = _utt("/b.jsonl", 1, "ありがとう", "pj-b", ts=_ts(1))

    def _fake_call_haiku(prompt, model="haiku"):
        return _ok_verdict_response([(0, True), (1, False)])

    monkeypatch.setattr(judge_runner, "call_haiku", _fake_call_haiku)

    res = judge_runner.run_daily_judge(
        run=True,
        utterances=[u_a, u_b],
        judged_path=judged,
        weak_signals_path=ws,
        idioms_path=corr_idioms,
        batch_size=30,
    )
    assert res["corrections"] == 1
    assert res["non_corrections"] == 1
    signals = read_signals(ws)
    assert len(signals) == 1
    assert signals[0]["pj_slug"] == "pj-a"
    assert signals[0]["channel"] == "llm_judge"

    judged_keys = read_judged_keys(judged)
    assert len(judged_keys) == 2  # 修正/非修正どちらも判定済みになる


def test_run_call_failure_leaves_utterance_unjudged(tmp_path, monkeypatch):
    """Haiku 呼び出し失敗（例外）は未判定のまま残す（silent false negative 禁止）。"""
    ws = tmp_path / "weak_signals.jsonl"
    judged = tmp_path / "correction_judged.jsonl"
    u = _utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))

    def _raise(prompt, model="haiku"):
        raise TimeoutError("haiku timeout")

    monkeypatch.setattr(judge_runner, "call_haiku", _raise)

    res = judge_runner.run_daily_judge(
        run=True, utterances=[u], judged_path=judged, weak_signals_path=ws,
    )
    assert res["call_failed"] == 1
    assert read_judged_keys(judged) == set()  # 判定済みに確定しない
    assert not ws.exists() or read_signals(ws) == []


def test_run_claude_call_error_from_nonzero_returncode_leaves_utterance_unjudged(tmp_path, monkeypatch):
    """#410 [Must]F: safe_llm_call.ClaudeCallError（非ゼロ終了）も call_failed 経路に合流する。"""
    import safe_llm_call

    ws = tmp_path / "weak_signals.jsonl"
    judged = tmp_path / "correction_judged.jsonl"
    u = _utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))

    def _raise(prompt, *, model="haiku", **kwargs):
        raise safe_llm_call.ClaudeCallError("claude -p exited 1: boom")

    monkeypatch.setattr(judge_runner._safe_llm_call, "call_claude_headless", _raise)

    res = judge_runner.run_daily_judge(
        run=True, utterances=[u], judged_path=judged, weak_signals_path=ws,
    )
    assert res["call_failed"] == 1
    assert read_judged_keys(judged) == set()
    assert not ws.exists() or read_signals(ws) == []


def test_run_malformed_json_response_leaves_utterance_unjudged(tmp_path, monkeypatch):
    """応答は届くが JSON 解釈不能（#273）→ 未判定のまま残す。"""
    judged = tmp_path / "correction_judged.jsonl"
    u = _utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))

    monkeypatch.setattr(judge_runner, "call_haiku", lambda prompt, model="haiku": "not json at all")

    res = judge_runner.run_daily_judge(run=True, utterances=[u], judged_path=judged)
    assert res["parse_failed_batches"] == 1
    assert read_judged_keys(judged) == set()


def test_run_no_pending_utterances_is_noop(tmp_path):
    res = judge_runner.run_daily_judge(run=True, utterances=[], judged_path=tmp_path / "j.jsonl")
    assert res["requested"] == 0
    assert res["corrections"] == 0
    assert res["capped"] is False


# ─────────────────────────────────────────────────────────────────
# フェーズ遷移ログ（提示された/実行された/判定が返った/永続化された、を区別可能にする）
# ─────────────────────────────────────────────────────────────────
def test_phase_transition_log_lines_are_distinguishable(tmp_path, monkeypatch):
    import io

    out = io.StringIO()
    u = _utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))
    monkeypatch.setattr(
        judge_runner, "call_haiku", lambda prompt, model="haiku": _ok_verdict_response([(0, False)])
    )
    judge_runner.run_daily_judge(
        run=True, utterances=[u], judged_path=tmp_path / "correction_judged.jsonl",
        weak_signals_path=tmp_path / "weak_signals.jsonl", out=out,
    )
    log = out.getvalue()
    assert "提示" in log
    assert "実行" in log
    assert "応答" in log
    assert "永続化" in log


# ─────────────────────────────────────────────────────────────────
# call_haiku: subprocess の唯一の集約点
# ─────────────────────────────────────────────────────────────────
def test_call_haiku_delegates_to_safe_llm_call(monkeypatch):
    """#410 [Must]A: call_haiku の実体は safe_llm_call.call_claude_headless（低レベルの
    subprocess 呼び出し・ツール封じフラグ組み立ての契約は test_safe_llm_call.py が担う）。
    ここでは judge_runner.call_haiku がその共有実装へ正しく委譲することだけを確認する。
    """
    captured = {}

    def _fake(prompt, *, model="haiku", **kwargs):
        captured["prompt"] = prompt
        captured["model"] = model
        return "ok"

    monkeypatch.setattr(judge_runner._safe_llm_call, "call_claude_headless", _fake)
    out = judge_runner.call_haiku("prompt text", model="haiku")
    assert out == "ok"
    assert captured["prompt"] == "prompt text"
    assert captured["model"] == "haiku"


# ─────────────────────────────────────────────────────────────────
# CLI（verbosity/judge.py と同じ形式: --run 既定 False・--limit で件数上限を上書き）
# ─────────────────────────────────────────────────────────────────
def test_main_dry_run_default(monkeypatch, capsys):
    captured = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {"dry_run": True, "unjudged_total": 0, "selected": 0, "capped": False}

    monkeypatch.setattr(judge_runner, "run_daily_judge", _fake)
    rc = judge_runner.main([])
    assert rc == 0
    assert captured["run"] is False


def test_main_limit_flag_overrides_daily_utterance_limit(monkeypatch):
    captured = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {"dry_run": True, "unjudged_total": 0, "selected": 0, "capped": False}

    monkeypatch.setattr(judge_runner, "run_daily_judge", _fake)
    judge_runner.main(["--run", "--limit", "60"])
    assert captured["daily_utterance_limit"] == 60
    assert captured["run"] is True
