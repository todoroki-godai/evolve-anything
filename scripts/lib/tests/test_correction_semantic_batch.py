"""correction_semantic.batch のテスト（#431 バッチ LLM 判定 2 相）。

auto_memory_broker と同型の 2 相: emit（決定論・LLM 非依存・Phase A）と
ingest（assistant 応答を受け取る・Phase C）。テストは responses dict を直接渡すので
**LLM を一切呼ばない**（no-llm-in-tests 準拠）。

検証:
- emit: 判定済み発話を除外し、N 件ずつバッチ化したリクエストを生成
- ingest: verdict を weak_signals(channel=llm_judge)隔離 + 個人辞書に記録
- dry-run: weak_signals / 個人辞書 / 判定進捗のどれにも一切書かない
- 非対話 PJ 除外: excluded_pj は query 側 source_kinds デフォルトで担保（emit に渡さない）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import batch as cs_batch  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402


def _utts():
    return [
        {"source_path": "/a.jsonl", "line_no": 1, "session_id": "s1",
         "text": "ボタンは緑にして、赤じゃなくて", "prev_action": "Edit",
         "pj_slug": "evolve-anything", "timestamp": "2026-06-01T00:00:00+00:00"},
        {"source_path": "/a.jsonl", "line_no": 2, "session_id": "s1",
         "text": "ありがとう完璧", "prev_action": None,
         "pj_slug": "evolve-anything", "timestamp": "2026-06-01T00:01:00+00:00"},
        {"source_path": "/a.jsonl", "line_no": 3, "session_id": "s1",
         "text": "P6のデザインが違うんだけど", "prev_action": "Write",
         "pj_slug": "evolve-anything", "timestamp": "2026-06-01T00:02:00+00:00"},
    ]


# ── Phase A: emit ────────────────────────────────────────────────


def test_emit_batches_unjudged(tmp_path: Path) -> None:
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=2,
        judged_path=tmp_path / "judged.jsonl",
    )
    # 3 発話 / batch_size 2 → 2 リクエスト
    assert len(emitted["requests"]) == 2
    # 各 request は id / prompt / meta（meta に発話グループ）を持つ
    req0 = emitted["requests"][0]
    assert "prompt" in req0 and "id" in req0
    assert "四国めたん" in req0["prompt"]


def test_emit_skips_already_judged(tmp_path: Path) -> None:
    judged = tmp_path / "judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=judged)
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    # 残り 1 件（line_no 3）だけ → 1 リクエスト
    assert len(emitted["requests"]) == 1
    assert "P6" in emitted["requests"][0]["prompt"]
    # line_no 1 の発話本文（一意・雛形と非衝突）は判定対象に現れない（除外された）
    assert "ボタンは緑" not in emitted["requests"][0]["prompt"]


def test_emit_empty_when_all_judged(tmp_path: Path) -> None:
    judged = tmp_path / "judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"], path=judged)
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    assert emitted["requests"] == []


# ── Phase C: ingest ──────────────────────────────────────────────


def _responses_for(emitted, mapping):
    """各 request id に mapping[index]→verdict を組んだ JSON 応答を作る。"""
    responses = {}
    for req in emitted["requests"]:
        group = req["meta"]["utterances"]
        verdicts = []
        for local_i, _u in enumerate(group):
            v = mapping.get((req["id"], local_i))
            if v is not None:
                verdicts.append({"index": local_i, **v})
        responses[req["id"]] = json.dumps({"verdicts": verdicts}, ensure_ascii=False)
    return responses


def test_ingest_records_correction_to_weak_signals_and_dictionary(tmp_path: Path) -> None:
    ws_store = tmp_path / "weak_signals.jsonl"
    idioms_store = tmp_path / "idioms.jsonl"
    judged = tmp_path / "judged.jsonl"

    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # index 0 (四国めたん) と 2 (P6) を修正と判定、1 は非修正
    responses = _responses_for(emitted, {
        (rid, 0): {"is_correction": True, "idiom": "四国めたんじゃなくて", "reason": "後置型"},
        (rid, 1): {"is_correction": False, "idiom": None, "reason": ""},
        # #527: idiom_filter の floor を通る eligible idiom を使う
        # （極短「違うんだけど」は guard で個人辞書から弾かれる別テストで検証）
        (rid, 2): {"is_correction": True, "idiom": "色が違うから赤にして", "reason": "ソフト指摘"},
    })

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["corrections"] == 2
    assert res["non_corrections"] == 1
    # weak_signals に channel=llm_judge で 2 件
    ws_lines = [json.loads(l) for l in ws_store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(ws_lines) == 2
    assert all(r["channel"] == "llm_judge" for r in ws_lines)
    assert all(r["promoted"] is False for r in ws_lines)
    # 個人辞書に 2 件
    idiom_lines = [json.loads(l) for l in idioms_store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(idiom_lines) == 2
    assert {r["idiom"] for r in idiom_lines} == {"四国めたんじゃなくて", "色が違うから赤にして"}
    # 判定進捗に 3 件（修正/非修正どちらも判定済みに記録）
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"}


def test_ingest_stores_idiom_in_weak_signal_provenance(tmp_path: Path) -> None:
    """#253: provenance.idiom を保存し signal_text の多トピックトリムに使えるようにする。"""
    ws_store = tmp_path / "weak_signals.jsonl"
    idioms_store = tmp_path / "idioms.jsonl"
    judged = tmp_path / "judged.jsonl"

    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = _responses_for(emitted, {
        (rid, 0): {"is_correction": True, "idiom": "四国めたんじゃなくて", "reason": "後置型"},
    })
    cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    ws_lines = [json.loads(l) for l in ws_store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert ws_lines[0]["provenance"]["idiom"] == "四国めたんじゃなくて"


def test_ingest_filters_overbroad_idioms_from_dictionary(tmp_path: Path) -> None:
    """#527: 過汎用 idiom（極短/相槌/日付断片）は weak_signal は残すが個人辞書に入れない。"""
    ws_store = tmp_path / "weak_signals.jsonl"
    idioms_store = tmp_path / "idioms.jsonl"
    judged = tmp_path / "judged.jsonl"

    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = _responses_for(emitted, {
        (rid, 0): {"is_correction": True, "idiom": "つむぎにしてほしいんだけど", "reason": "後置"},  # eligible
        (rid, 1): {"is_correction": True, "idiom": "気がする", "reason": "推量"},  # too_short
        (rid, 2): {"is_correction": True, "idiom": "いや、2/24の", "reason": "断片"},  # context_token
    })
    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    # corrections は 3 件すべて検出され weak_signals に隔離記録される
    assert res["corrections"] == 3
    ws_lines = [json.loads(l) for l in ws_store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(ws_lines) == 3
    # 個人辞書には eligible な 1 件のみ。過汎用 2 件は idioms_filtered。
    assert res["idioms_filtered"] == 2
    idiom_lines = [json.loads(l) for l in idioms_store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {r["idiom"] for r in idiom_lines} == {"つむぎにしてほしいんだけど"}


def test_ingest_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws_store = tmp_path / "weak_signals.jsonl"
    idioms_store = tmp_path / "idioms.jsonl"
    judged = tmp_path / "judged.jsonl"

    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = _responses_for(emitted, {
        (rid, 0): {"is_correction": True, "idiom": "x", "reason": "r"},
    })
    res = cs_batch.ingest_judgement_results(
        emitted, responses, dry_run=True,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["dry_run"] is True
    assert res["corrections"] == 1  # 判定は走る
    assert not ws_store.exists()
    assert not idioms_store.exists()
    assert not judged.exists()


def test_ingest_missing_response_does_not_mark_judged(tmp_path: Path) -> None:
    """応答欠損のバッチは判定済みにせず、次回再判定できる（broker の skip 方針と同型）。"""
    ws_store = tmp_path / "weak_signals.jsonl"
    idioms_store = tmp_path / "idioms.jsonl"
    judged = tmp_path / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    # responses 空（assistant が応答しなかった）
    res = cs_batch.ingest_judgement_results(
        emitted, {}, weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["corrections"] == 0
    assert res["skipped_batches"] == 1
    # 判定進捗に何も記録されない（再判定可能）
    assert cs_store.read_judged_keys(judged) == set()


def test_estimate_tokens() -> None:
    est = cs_batch.estimate_tokens(_utts(), batch_size=30)
    assert est["utterances"] == 3
    assert est["batches"] == 1
    assert est["est_total_tokens"] > 0
