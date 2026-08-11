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

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import batch as cs_batch  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402


@pytest.fixture
def scratch_dir(tmp_path_factory) -> Path:
    """weak_signals/idioms/judged スクラッチ用の独立ディレクトリ。

    root conftest の autouse `_isolate_plugin_data` は per-test の ``tmp_path`` を
    そのまま ``rl_common.DATA_DIR`` に rebase する（#420）。本ファイルは
    ``weak_signals.jsonl`` 等の**非登録の短縮 basename**（judged.jsonl / idioms.jsonl）を
    直接ファイル名として使うため、素の ``tmp_path`` を使うと #379 Step 1 修正4
    （store_write_raw の凍結ゲート）が「正準 DATA_DIR 配下の未登録 basename」と誤認する。
    ``tmp_path_factory`` の別 mktemp で DATA_DIR と兄弟の独立ディレクトリを使う。
    """
    return tmp_path_factory.mktemp("scratch")


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


def test_emit_batches_unjudged(scratch_dir: Path) -> None:
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=2,
        judged_path=scratch_dir / "judged.jsonl",
    )
    # 3 発話 / batch_size 2 → 2 リクエスト
    assert len(emitted["requests"]) == 2
    # 各 request は id / prompt / meta（meta に発話グループ）を持つ
    req0 = emitted["requests"][0]
    assert "prompt" in req0 and "id" in req0
    assert "四国めたん" in req0["prompt"]


def test_emit_skips_already_judged(scratch_dir: Path) -> None:
    judged = scratch_dir / "judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=judged)
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    # 残り 1 件（line_no 3）だけ → 1 リクエスト
    assert len(emitted["requests"]) == 1
    assert "P6" in emitted["requests"][0]["prompt"]
    # line_no 1 の発話本文（一意・雛形と非衝突）は判定対象に現れない（除外された）
    assert "ボタンは緑" not in emitted["requests"][0]["prompt"]


def test_emit_empty_when_all_judged(scratch_dir: Path) -> None:
    judged = scratch_dir / "judged.jsonl"
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


def test_ingest_records_correction_to_weak_signals_and_dictionary(scratch_dir: Path) -> None:
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"

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
    # #410 [Must]B: 判定済みキーごとの推定トークンが記録され、count_judged_today で
    # 当日累積として合算できる（daily runner の日次上限が「1回の呼び出し上限」に
    # なっていた欠陥の是正・record_judged への配線を固定する）。
    today_count = cs_store.count_judged_today(path=judged)
    assert today_count["count"] == 3
    assert today_count["est_tokens"] > 0


# ── #410 round2 codex [Must]B: 当日累積がバッチ固定費を取りこぼす是正 ───────────
# estimate_tokens は batches × _PROMPT_OVERHEAD_TOKENS(400) を含むのに、record_judged に
# 残す est_tokens は発話単価のみだった。batch_size=1 で再実行を繰り返すと毎回 400 トークン
# ずつ上限を超過できる（次回実行が前回のバッチ固定費を全く覚えていない）。


def test_ingest_distributes_batch_fixed_cost_into_est_tokens_by_key(scratch_dir: Path) -> None:
    """バッチ固定費（_PROMPT_OVERHEAD_TOKENS）を、このバッチで実際に確定したキーへ均等
    按分して記録する。按分方式を選んだ理由: 特定1キー（例: 先頭）に全額寄せると、そのキーが
    別の事情で再判定対象になった場合に固定費だけ二重計上/消失する非対称が生まれるため、
    確定した全キーに均等に配る方が扱いやすい。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    utts = _utts()  # 3 発話・batch_size=30 → 1 バッチ

    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=utts, batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = _responses_for(emitted, {
        (rid, i): {"is_correction": False, "idiom": None, "reason": "r"} for i in range(3)
    })
    cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )

    today = cs_store.count_judged_today(path=judged)
    per_utterance_only_sum = sum(cs_batch.estimate_utterance_tokens(u) for u in utts)
    # バッチ固定費（400トークン）が含まれるため、発話単価だけの合計より明確に大きい
    # （按分丸めの誤差はキー数未満に収まる）。
    assert today["est_tokens"] > per_utterance_only_sum
    assert today["est_tokens"] - per_utterance_only_sum >= cs_batch._PROMPT_OVERHEAD_TOKENS - len(utts)


def test_ingest_then_reemit_excludes_judged_utterances(scratch_dir: Path) -> None:
    """#339 回帰: Phase C を通した発話は次の Phase A emit に再度現れない。

    production 未配線だった間は ingest が一度も呼ばれず、同じ未判定発話が毎回 emit され
    続けていた（#339 の症状そのもの）。Phase C → 再 emit の往復を1テストで固定する。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"

    utts = _utts()
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=utts, batch_size=30, judged_path=judged,
    )
    assert emitted["unjudged"] == 3  # 消化前は全 3 件が対象

    rid = emitted["requests"][0]["id"]
    responses = _responses_for(emitted, {
        (rid, 0): {"is_correction": True, "idiom": "四国めたんじゃなくて", "reason": "後置型"},
        (rid, 1): {"is_correction": False, "idiom": None, "reason": ""},
        (rid, 2): {"is_correction": True, "idiom": "色が違うから赤にして", "reason": "ソフト指摘"},
    })
    cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )

    # 同じ utterances を渡しても、判定済みになった 3 件は再び emit されない。
    re_emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=utts, batch_size=30, judged_path=judged,
    )
    assert re_emitted["unjudged"] == 0
    assert re_emitted["requests"] == []


def test_ingest_stores_idiom_in_weak_signal_provenance(scratch_dir: Path) -> None:
    """#253: provenance.idiom を保存し signal_text の多トピックトリムに使えるようにする。"""
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"

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


def test_ingest_filters_overbroad_idioms_from_dictionary(scratch_dir: Path) -> None:
    """#527: 過汎用 idiom（極短/相槌/日付断片）は weak_signal は残すが個人辞書に入れない。"""
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"

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


def test_ingest_dry_run_writes_nothing(scratch_dir: Path) -> None:
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"

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


def test_ingest_missing_response_does_not_mark_judged(scratch_dir: Path) -> None:
    """応答欠損のバッチは判定済みにせず、次回再判定できる（broker の skip 方針と同型）。"""
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
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


def test_ingest_malformed_json_does_not_mark_judged(scratch_dir: Path) -> None:
    """#273: 応答は届いたが JSON が壊れているバッチも、応答欠損と同様に未判定のまま残す。

    従来は parse_verdicts が [] にフォールバックし、group 全件が「判定済み・非修正」として
    judged_keys に確定していた（応答欠損は再試行されるのに壊れた応答は再試行されない非対称）。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # 応答テキストは届いているが JSON として壊れている。
    responses = {rid: "```json\n{not valid json at all"}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["corrections"] == 0
    assert res["non_corrections"] == 0
    assert res["parse_failed_batches"] == 1
    # 判定進捗に何も記録されない（対象発話が judged_keys に入らない = 次回再判定される）
    assert cs_store.read_judged_keys(judged) == set()
    # weak_signals / 個人辞書にも何も書かれない。
    assert not ws_store.exists()
    assert not idioms_store.exists()


def test_ingest_invalid_verdict_element_does_not_mark_judged(scratch_dir: Path) -> None:
    """#273 P1-1: 構文上 valid だが意味的に壊れた要素（型違い）混じりも、応答欠損と同様に未判定で残す。

    従来は不正要素だけ黙って parse_verdicts から捨てられ、残りの空リストが
    「該当なし（正当な空）」と誤読されて group 全件が judged_keys に確定していた。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # index が文字列型（"0"）の不正要素を含む応答（構文上は valid JSON）。
    responses = {rid: json.dumps({"verdicts": [{"index": "0", "is_correction": True}]})}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["corrections"] == 0
    assert res["non_corrections"] == 0
    assert res["parse_failed_batches"] == 1
    assert cs_store.read_judged_keys(judged) == set()
    assert not ws_store.exists()
    assert not idioms_store.exists()


def test_ingest_bool_coerced_is_correction_string_does_not_mark_judged(scratch_dir: Path) -> None:
    """#273 P1-1: is_correction が文字列 "false" は bool("false")==True の罠を踏まず失格にする。"""
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = {rid: json.dumps({"verdicts": [{"index": 0, "is_correction": "false"}]})}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["parse_failed_batches"] == 1
    assert res["corrections"] == 0
    assert cs_store.read_judged_keys(judged) == set()


def test_ingest_all_out_of_range_index_treated_as_legitimate_empty(scratch_dir: Path) -> None:
    """#410 round3 [Should]⑤ 是正: バッチ対象外の index はその要素だけ無視し、件数を
    out_of_range_verdicts に surface する（round2 の「バッチ全体を parse_failed にして
    無限再試行させる」設計は過剰だったため方針変更・詳細は prompt.parse_verdicts_result の
    docstring 参照）。全件が範囲外だと verdicts=[] に収束するため、モデルが明示的に
    「該当なし」と答えた場合（legitimate_empty_batch）と同様にバッチ全体を非修正確定する
    （無限再試行を避けるための既定の挙動・#273 契約と同型）。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # _utts() は3件（index 0..2）。99 はバッチ対象外。
    responses = {rid: json.dumps({"verdicts": [{"index": 99, "is_correction": True}]})}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["parse_failed_batches"] == 0
    assert res["out_of_range_verdicts"] == 1
    assert res["corrections"] == 0
    assert res["non_corrections"] == 3
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"}


def test_ingest_legitimate_empty_verdicts_still_marks_judged(scratch_dir: Path) -> None:
    """正しい JSON で verdicts が空配列（モデルが「該当なし」と判定）は従来どおり判定済みにする。

    パース失敗（ok=False）と正当な空リスト（ok=True・該当なし）を区別できないと、
    正当な「該当なし」判定まで再試行対象にしてしまい無駄な再判定ループになる。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    responses = {rid: json.dumps({"verdicts": []})}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["parse_failed_batches"] == 0
    assert res["non_corrections"] == 3
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"}


def test_ingest_processes_every_batch_not_just_the_first(scratch_dir: Path) -> None:
    """#273: 2 バッチ目以降も応答が回収される（変数 shadowing の回帰テスト）。

    ingest 内でバッチごとのパース結果を、外側の応答マップと同名の変数に代入すると、
    2 週目の `parsed.get(key)` が verdict dict を引いて空文字になり、以降の全バッチが
    「応答欠損」として silent skip される（判定は永久に前進しない）。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=1, judged_path=judged,
    )
    assert len(emitted["requests"]) == 3  # 1 発話 = 1 バッチ

    responses = _responses_for(
        emitted,
        {(req["id"], 0): {"is_correction": False, "idiom": None, "reason": "r"}
         for req in emitted["requests"]},
    )
    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )

    assert res["skipped_batches"] == 0
    assert res["non_corrections"] == 3
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"}


def test_ingest_counts_omitted_verdicts(scratch_dir: Path) -> None:
    """#410 [Must]D 是正: 個別 index の verdict 欠落は「非修正」で恒久確定させず、その
    index の発話だけ未判定のまま残す（次回 drain で再試行）。揃っている index は
    従来どおり確定する（head-of-line blocking を避ける — バッチ全体は戻さない）。
    欠落件数は omitted_verdicts に維持して observability を保つ（#273 由来）。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # index 0 だけ返し、1/2 は省略された応答
    responses = {rid: json.dumps(
        {"verdicts": [{"index": 0, "is_correction": False, "idiom": None, "reason": "r"}]}
    )}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["omitted_verdicts"] == 2
    assert res["non_corrections"] == 1  # 揃っていた index 0 のみ確定
    # index 0（/a.jsonl:1）だけ判定済み。1/2（欠落）は次回 drain で再試行できるよう未判定のまま。
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1"}


def test_ingest_counts_out_of_range_verdicts_without_failing_batch(scratch_dir: Path) -> None:
    """#410 round3 [Should]⑤: 範囲外 index はバッチ全体を失格にせず、その要素だけ無視して
    件数を surface する（round2 は全体失格だったが range3 で方針変更）。範囲内の index は
    通常どおり確定する。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=_utts(), batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # index 0/1/2 は正当（3発話バッチ）だが、範囲外の index 99 も混じって返る
    responses = {rid: json.dumps({"verdicts": [
        {"index": 0, "is_correction": False, "idiom": None, "reason": "r"},
        {"index": 1, "is_correction": False, "idiom": None, "reason": "r"},
        {"index": 2, "is_correction": False, "idiom": None, "reason": "r"},
        {"index": 99, "is_correction": True, "idiom": "x", "reason": "y"},
    ]})}

    res = cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )
    assert res["out_of_range_verdicts"] == 1
    assert res["parse_failed_batches"] == 0  # 全体失格にはしない
    assert res["non_corrections"] == 3
    assert cs_store.read_judged_keys(judged) == {"/a.jsonl:1", "/a.jsonl:2", "/a.jsonl:3"}


def test_ingest_partial_omission_retries_only_missing_indices_on_reemit(scratch_dir: Path) -> None:
    """#410 [Must]D E2E: 欠落した index の発話は次の emit で再度対象になり、確定済みの
    発話は対象にならない（部分応答の欠落だけを retry する、というのが本修正の目的）。
    """
    ws_store = scratch_dir / "weak_signals.jsonl"
    idioms_store = scratch_dir / "idioms.jsonl"
    judged = scratch_dir / "judged.jsonl"
    utts = _utts()
    emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=utts, batch_size=30, judged_path=judged,
    )
    rid = emitted["requests"][0]["id"]
    # index 0 のみ返り、1/2 は欠落。
    responses = {rid: json.dumps(
        {"verdicts": [{"index": 0, "is_correction": False, "idiom": None, "reason": "r"}]}
    )}
    cs_batch.ingest_judgement_results(
        emitted, responses,
        weak_signals_path=ws_store, idioms_path=idioms_store, judged_path=judged,
    )

    re_emitted = cs_batch.emit_judgement_requests(
        "evolve-anything", utterances=utts, batch_size=30, judged_path=judged,
    )
    assert re_emitted["unjudged"] == 2  # 欠落していた 1/2（/a.jsonl:2, /a.jsonl:3）だけ
    re_group = re_emitted["requests"][0]["meta"]["utterances"]
    re_keys = {cs_store.utterance_key(u) for u in re_group}
    assert re_keys == {"/a.jsonl:2", "/a.jsonl:3"}


def test_batch_prompt_requires_verdict_for_every_index() -> None:
    """#273: プロンプトが「全 index を省略せず返す」契約を明示している。"""
    prompt = cs_batch._prompt.build_batch_prompt(_utts())
    assert "全 index" in prompt
    assert "省略しない" in prompt


def test_estimate_tokens() -> None:
    est = cs_batch.estimate_tokens(_utts(), batch_size=30)
    assert est["utterances"] == 3
    assert est["batches"] == 1
    assert est["est_total_tokens"] > 0


# ── #410 [Must]C: 固定係数でなく実入力長に連動する見積もりに固定する ──────────


def test_estimate_tokens_scales_with_actual_text_length() -> None:
    """#410: 「件数×80」固定だと長文1件と短文1件が同じ扱いになる。実長差を反映すること。"""
    short = [{"text": "短い", "prev_action": None}]
    long_utt = [{"text": "あ" * 5000, "prev_action": None}]
    est_short = cs_batch.estimate_tokens(short, batch_size=30)
    est_long = cs_batch.estimate_tokens(long_utt, batch_size=30)
    # 長文側が短文側より明確に高いこと（固定係数だと同数になっていた）。
    assert est_long["est_total_tokens"] > est_short["est_total_tokens"] * 5


def test_estimate_tokens_reflects_truncation_cap() -> None:
    """本文が MAX_CHARS_PER_UTTERANCE を超えても、実送信されるプロンプトと同じ切り詰め後の
    長さで見積もる（青天井にしない・#410）。
    """
    from correction_semantic import MAX_CHARS_PER_UTTERANCE

    huge = [{"text": "あ" * (MAX_CHARS_PER_UTTERANCE * 10), "prev_action": None}]
    capped = [{"text": "あ" * MAX_CHARS_PER_UTTERANCE, "prev_action": None}]
    est_huge = cs_batch.estimate_tokens(huge, batch_size=30)
    est_capped = cs_batch.estimate_tokens(capped, batch_size=30)
    assert est_huge["est_total_tokens"] == est_capped["est_total_tokens"]


def test_estimate_tokens_empty_text_is_small() -> None:
    est = cs_batch.estimate_tokens([{"text": "", "prev_action": None}], batch_size=30)
    assert est["est_total_tokens"] > 0  # per-batch/per-utterance overhead は残る
    assert est["est_total_tokens"] < 500  # だが本文ゼロなら十分小さい


# ── #410 round2 codex [Must]C: 見積もりが実送信（prev_action 含む）と一致しない是正 ──
# build_batch_prompt は prev_action を最大300字送るのに、旧見積もりは本文長のみを測り
# prev_action・ラベル・出力形式を固定オーバーヘッドで丸めていた。長い日本語 prev_action が
# 多いバッチで大幅な過小評価になりうる。


def test_estimate_utterance_tokens_reflects_prev_action_length() -> None:
    short_prev = {"text": "x", "prev_action": "a"}
    long_prev = {"text": "x", "prev_action": "あ" * 300}
    assert (
        cs_batch.estimate_utterance_tokens(long_prev)
        > cs_batch.estimate_utterance_tokens(short_prev) * 5
    )


def test_estimate_utterance_tokens_measures_actual_prompt_line(scratch_dir: Path) -> None:
    """理想解: 実際に組み立てるプロンプト行（prompt.format_utterance_line）の長さを
    そのまま測る。見積もりロジックを prompt.py 側の組み立てロジックと重複実装すると、
    どちらかだけ切り詰め幅を変えたときに乖離が再発するため、単一ソースにする。
    """
    from correction_semantic import prompt as cs_prompt

    u = {"text": "本文テキスト", "prev_action": "直前のツール操作の要約"}
    line = cs_prompt.format_utterance_line(0, u)
    expected = -(-len(line) // 2)  # ceil(len(line) / _CHARS_PER_TOKEN=2.0)
    assert cs_batch.estimate_utterance_tokens(u) == expected
