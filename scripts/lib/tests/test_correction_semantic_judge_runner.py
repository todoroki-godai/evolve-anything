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


# ── #410 [Must]B: 「日次上限」が1回の呼び出し上限になっていた欠陥の是正 ──────
# select_daily_batch は当日の累積使用量を見ておらず、cron 再実行や手動 --run の重ね掛け
# のたびに上限までフル処理できてしまっていた。run_daily_judge は当日既に判定済みの件数・
# 推定トークンを差し引いた「残り枠」で選定すること。


def test_daily_cap_subtracts_already_judged_today(tmp_path):
    from correction_semantic.store import record_judged

    judged = tmp_path / "correction_judged.jsonl"
    # 今日すでに 198 件処理済み（cron の1回目実行を模す）→ 残り枠は 2 件のみ。
    record_judged([f"/prior.jsonl:{i}" for i in range(198)], path=judged)

    utterances = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(5)]
    res = judge_runner.run_daily_judge(
        run=False, utterances=utterances, judged_path=judged, daily_utterance_limit=200,
    )
    assert res["selected"] == 2
    assert res["capped"] is True


def test_daily_cap_already_at_limit_selects_nothing(tmp_path):
    from correction_semantic.store import record_judged

    judged = tmp_path / "correction_judged.jsonl"
    record_judged([f"/prior.jsonl:{i}" for i in range(200)], path=judged)

    utterances = [_utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))]
    res = judge_runner.run_daily_judge(
        run=False, utterances=utterances, judged_path=judged, daily_utterance_limit=200,
    )
    assert res["selected"] == 0
    assert res["capped"] is True


def test_daily_cap_token_budget_subtracted_from_prior_usage(tmp_path):
    """#410 [Must]B: トークン上限側も当日累積を差し引く。"""
    from correction_semantic.store import record_judged

    judged = tmp_path / "correction_judged.jsonl"
    record_judged(
        ["/prior.jsonl:1"], path=judged, est_tokens_by_key={"/prior.jsonl:1": 100},
    )

    utterances = [_utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))]
    res = judge_runner.run_daily_judge(
        run=False, utterances=utterances, judged_path=judged,
        daily_utterance_limit=200, daily_token_limit=100,  # 当日既に100消費済み→残り0
    )
    assert res["selected"] == 0
    assert res["capped"] is True


def test_daily_cap_yesterday_usage_does_not_count(tmp_path):
    """前日分は当日累積に含めない（日付境界の正しさ）。"""
    from datetime import timedelta, timezone as _tz

    from correction_semantic.store import record_judged

    judged = tmp_path / "correction_judged.jsonl"
    record_judged(["/prior.jsonl:1"], path=judged)
    # 書き込んだ行の judged_at を昨日に書き換える（実ファイルを直接編集してテスト用に固定）。
    import json as _json

    lines = [_json.loads(l) for l in judged.read_text(encoding="utf-8").splitlines() if l.strip()]
    from datetime import datetime

    yesterday = datetime.fromisoformat(lines[0]["judged_at"]) - timedelta(days=1)
    lines[0]["judged_at"] = yesterday.isoformat()
    judged.write_text("".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in lines), encoding="utf-8")

    utterances = [_utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))]
    res = judge_runner.run_daily_judge(
        run=False, utterances=utterances, judged_path=judged, daily_utterance_limit=1,
    )
    assert res["selected"] == 1  # 前日分は差し引かれない
    assert res["capped"] is False


def test_daily_cap_second_run_same_day_respects_first_runs_consumption(tmp_path, monkeypatch):
    """同日内の複数回実行（cron 再実行相当）を通しで検証する。1回目 run=True で2件処理
    → 記録される → 2回目 run=False（dry-run 見積もり）は残り枠だけを見る。
    """
    def _fake_call_haiku(prompt, model="haiku"):
        return _ok_verdict_response([(i, False) for i in range(1)])

    monkeypatch.setattr(judge_runner, "call_haiku", _fake_call_haiku)

    judged = tmp_path / "correction_judged.jsonl"
    first_batch = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(2)]
    res1 = judge_runner.run_daily_judge(
        run=True, utterances=first_batch, judged_path=judged,
        daily_utterance_limit=3, batch_size=1,
    )
    assert res1["selected"] == 2

    second_batch = [_utt("/b.jsonl", i, f"other{i}", "pj-b", ts=_ts(1)) for i in range(5)]
    res2 = judge_runner.run_daily_judge(
        run=False, utterances=second_batch, judged_path=judged, daily_utterance_limit=3,
    )
    assert res2["selected"] == 1  # 3 - 2(1回目) = 1 件分だけ残っている
    assert res2["capped"] is True


# ── #410 [Must]B: 選定〜記録の排他（ロック保持中は別経路が進めないことを検査）──
# learning_concurrency_test_by_lock_holding: N プロセス同時起動は競合窓が µs で再現せず
# 偽の安全網になる。ロックを外部で保持した状態で別スレッドが run_daily_judge を呼び、
# 短いデッドライン内に完了しない（＝ブロックされている）ことを確認したうえで解放し、
# 解放後に完了することまで確認する（hang→fail 変換）。


def test_select_and_record_are_mutually_exclusive_via_lock(tmp_path):
    import threading

    from rl_common.file_lock import file_lock

    judged = tmp_path / "correction_judged.jsonl"
    lock_path = judged.with_name(judged.name + ".lock")

    utterances = [_utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))]

    completed = threading.Event()
    started = threading.Event()

    def _worker():
        started.set()
        judge_runner.run_daily_judge(run=False, utterances=utterances, judged_path=judged)
        completed.set()

    with file_lock(lock_path):
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        assert started.wait(timeout=2), "worker スレッドが開始しなかった"
        # ロック保持中は run_daily_judge が完了しない（select_daily_batch の手前でブロック）。
        blocked = not completed.wait(timeout=0.5)
        assert blocked, "ロック保持中なのに run_daily_judge が完了した（排他が効いていない）"

    # ロック解放後は速やかに完了する。
    assert completed.wait(timeout=5), "ロック解放後も run_daily_judge が完了しなかった（hang）"
    t.join(timeout=5)
    assert not t.is_alive()


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


def test_daily_token_limit_exact_boundary_is_inclusive(tmp_path):
    """#410 [Must]C: est_total_tokens == daily_token_limit ちょうどは選定に含める境界。"""
    from correction_semantic.batch import estimate_tokens

    utterances = [_utt("/a.jsonl", i, f"text{i}", "pj-a", ts=_ts(1)) for i in range(3)]
    exact_budget_for_one = estimate_tokens(utterances[:1], batch_size=30)["est_total_tokens"]

    res = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=tmp_path / "correction_judged.jsonl",
        daily_utterance_limit=200,
        daily_token_limit=exact_budget_for_one,
    )
    assert res["selected"] == 1  # ちょうど収まる1件目までは選ばれる
    assert res["capped"] is True

    res_short_by_one = judge_runner.run_daily_judge(
        run=False,
        utterances=utterances,
        judged_path=tmp_path / "correction_judged2.jsonl",
        daily_utterance_limit=200,
        daily_token_limit=exact_budget_for_one - 1,
    )
    assert res_short_by_one["selected"] == 0  # 1トークンでも超えれば選ばれない


def test_newest_utterances_prioritized_when_capped(tmp_path, monkeypatch):
    """#410 [Must]G: 上限で切り詰めるときは新しい発話を優先する（TTL 45日で腐る前に判定する
    ため・旧実装は逆に古い順だった）。selected 件数だけでなく emit 対象そのものを固定する
    （件数だけの検証だとソート方向を消しても通ってしまう・[Should] 補強）。
    """
    from correction_semantic.store import read_judged_keys, utterance_key

    old = _utt("/a.jsonl", 1, "old", "pj-a", ts=_ts(days_ago=10))
    new = _utt("/a.jsonl", 2, "new", "pj-a", ts=_ts(days_ago=0))
    judged = tmp_path / "correction_judged.jsonl"

    monkeypatch.setattr(
        judge_runner, "call_haiku", lambda prompt, model="haiku": _ok_verdict_response([(0, False)])
    )
    res = judge_runner.run_daily_judge(
        run=True,
        utterances=[old, new],  # あえて old を先頭に渡し、ソート未実装でも通る偽陽性を排除
        judged_path=judged,
        daily_utterance_limit=1,
    )
    assert res["selected"] == 1
    judged_keys = read_judged_keys(judged)
    assert judged_keys == {utterance_key(new)}, judged_keys
    assert utterance_key(old) not in judged_keys


def test_unparseable_timestamp_sorts_last(tmp_path):
    """timestamp パース不能/欠落は最下位（有効日時を持つ発話を優先）。"""
    bad = _utt("/a.jsonl", 1, "bad-ts", "pj-a", ts="not-a-date")
    good = _utt("/a.jsonl", 2, "good-ts", "pj-a", ts=_ts(days_ago=5))
    selected = judge_runner.select_daily_batch(
        [bad, good], daily_utterance_limit=1, daily_token_limit=10_000, batch_size=30,
    )
    assert len(selected) == 1
    assert selected[0]["text"] == "good-ts"


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

    # #410 [Must]G: 選定は新しい順（降順）。index 0 = 最新であることを固定するため
    # 明確に異なる timestamp を与える（同一 timestamp は呼び出し順の微小な実時刻差に
    # 依存し偽の安定性になるため避ける）。
    u_a = _utt("/a.jsonl", 1, "つむぎにしてほしい、四国めたんじゃなくて", "pj-a", ts=_ts(1))
    u_b = _utt("/b.jsonl", 1, "ありがとう", "pj-b", ts=_ts(2))

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
    assert res["source_failed"] is False


# ─────────────────────────────────────────────────────────────────
# #410 [Must]E: 発話ソース（utterances.db）例外を「0件」に化かさず surface する
# ─────────────────────────────────────────────────────────────────
def test_source_exception_surfaces_source_failed_dry_run(monkeypatch, tmp_path, capsys):
    """utterances=None（production 経路）で query が例外を送出したら 0 件と区別可能にする。"""
    def _raise(*a, **kw):
        raise RuntimeError("duckdb schema mismatch")

    import utterance_archive.query as _uq
    monkeypatch.setattr(_uq, "query_utterances_all_projects", _raise)

    res = judge_runner.run_daily_judge(run=False, judged_path=tmp_path / "correction_judged.jsonl")
    assert res["source_failed"] is True
    assert "duckdb schema mismatch" in (res.get("source_error") or "")
    assert res["unjudged_total"] == 0
    assert "発話ソース取得に失敗" in capsys.readouterr().err


def test_source_exception_surfaces_source_failed_run_true(monkeypatch, tmp_path):
    def _raise(*a, **kw):
        raise RuntimeError("duckdb schema mismatch")

    import utterance_archive.query as _uq
    monkeypatch.setattr(_uq, "query_utterances_all_projects", _raise)

    res = judge_runner.run_daily_judge(run=True, judged_path=tmp_path / "correction_judged.jsonl")
    assert res["source_failed"] is True
    assert res["requested"] == 0
    assert res["capped"] is False  # 0件はcapped扱いにしない（別の障害シグナルとして区別する）


def test_no_source_exception_source_failed_is_false(tmp_path):
    """DI で utterances を明示注入するテストパス（多数）は source_failed=False を維持する。"""
    u = _utt("/a.jsonl", 1, "text", "pj-a", ts=_ts(1))
    res = judge_runner.run_daily_judge(
        run=False, utterances=[u], judged_path=tmp_path / "correction_judged.jsonl",
    )
    assert res["source_failed"] is False
    assert res.get("source_error") is None


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
