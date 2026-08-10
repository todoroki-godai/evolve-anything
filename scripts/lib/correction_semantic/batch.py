"""correction_semantic.batch — バッチ LLM 意味判定の 2 相オーケストレーション（#431）。

auto_memory_broker（ADR-037）と同型の 2 相に分離し、Python から claude -p を完全に
追い出す（no-llm-in-tests と完全整合・テストは responses dict を直接渡す）:

  Phase A（決定論・LLM 非依存）: emit_judgement_requests
    utterances.db の dialogue 発話を query → 判定済み（correction_judged.jsonl）を除外 →
    N 件ずつバッチ化 → llm_broker.build_requests で {id, prompt, meta} を生成。
    meta に発話グループを持たせ、Phase C が verdict.index → 発話を引けるようにする。

  Phase B（LLM・assistant / Haiku）: 各 prompt にインライン or Task subagent で応答。
    本モジュール対象外（SKILL.md / evolve 配線が担う）。モデルは Haiku。

  Phase C（決定論・LLM 非依存）: ingest_judgement_results
    llm_broker.parse_responses で id→生テキスト回収 → prompt.parse_verdicts_result で JSON 解釈。
    修正と判定された発話を **weak_signals レーン（channel=llm_judge）に隔離記録** +
    **個人辞書（correction_idioms.jsonl）に provenance 付き蓄積**。判定し終えた発話の
    物理キーを correction_judged.jsonl に記録（再判定防止）。応答欠損バッチ・JSON パース失敗
    バッチはどちらも判定済みにせずスキップ（次 drain で再試行）。**#273**: 従来は応答欠損のみ
    スキップし、JSON が壊れているケースは parse_verdicts の [] フォールバックで「該当なし」と
    誤読され group 全件が判定済みに確定していた（欠損は再試行されるのに壊れた応答はされない
    非対称）。parse_verdicts_result の ok フラグで両者を区別し、同じスキップ経路に合流させる。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: ``dry_run=True`` のとき判定は走るが
weak_signals / 個人辞書 / 判定進捗のどれにも一切書かない（各 append が最下層で弾く）。

非対話 PJ 除外: utterance 側で担保する。query_utterances のデフォルト source_kinds=('dialogue',)
は long_paste / excluded_pj を含めないため、emit にはそもそも対話発話しか渡らない。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough  # noqa: E402

from . import DEFAULT_BATCH_SIZE, LLM_JUDGE_CHANNEL, MAX_CHARS_PER_UTTERANCE
from . import idiom_filter as _idiom_filter
from . import prompt as _prompt
from . import representative as _representative
from . import store as _store

# #410 [Must]C 是正: 旧実装は「件数 × 80 + バッチ数 × 400」の固定係数で本文長を一切見て
# おらず、長文1件を短文1件と同じ 80 トークンに見積もっていた（15万トークン上限が実質
# 無効化しうる）。実入力の文字数に連動する保守的な見積もりに変える。
#
# 日本語は概ね 1〜2 文字 ≈ 1 トークン（英数字混在では 4 字弱/トークンになることも多いが、
# 判定対象は日本語ユーザー発話が主）。安全側（過大に出す）に倒すため 2 文字/トークンで
# 見積もる。``prompt.build_batch_prompt`` と同じ ``MAX_CHARS_PER_UTTERANCE`` で本文長を
# 頭打ちし、見積もりと実送信の乖離を防ぐ（単一ソース）。
_CHARS_PER_TOKEN = 2.0
# 1 発話あたりのプロンプト整形オーバーヘッド（"[i] 直前のClaudeの操作: ...\n    ユーザー
# 発話: " のラベル + prev_action 分）。
_PER_UTTERANCE_OVERHEAD_TOKENS = 30
# プロンプト雛形（PROMPT_HEAD 相当の指示文・出力形式説明）の固定オーバーヘッド（バッチ単位）。
_PROMPT_OVERHEAD_TOKENS = 400


def _batch_id(pj_slug: str, group: List[Dict[str, Any]]) -> str:
    """バッチ ID = pj_slug + 先頭発話の物理キー（決定論・再実行で安定）。"""
    head = _store.utterance_key(group[0]) if group else "empty"
    return f"{pj_slug}:{head}"


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────────────────────────
# Phase A: emit
# ─────────────────────────────────────────────────────────────────
def emit_judgement_requests(
    pj_slug: str,
    *,
    utterances: Optional[List[Dict[str, Any]]] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    judged_path: Optional[Path] = None,
    source_kinds: Sequence[str] = ("dialogue",),
) -> Dict[str, Any]:
    """判定対象発話をバッチ化して LLM リクエスト一覧を生成する（決定論・IO 読取のみ）。

    utterances を渡さなければ utterances.db から query（dialogue のみ・非対話除外）。
    判定済み（correction_judged.jsonl）の発話は除外する。

    Returns:
        {"requests": [{"id", "prompt", "meta": {"utterances": [...]}}],
         "unjudged": int, "batches": int}
    """
    if utterances is None:
        try:
            from utterance_archive.query import query_utterances

            utterances = query_utterances(pj_slug, source_kinds=tuple(source_kinds))
        except Exception:
            utterances = []

    judged_keys = _store.read_judged_keys(judged_path)
    unjudged = _store.filter_unjudged(utterances or [], judged_keys)

    groups = _chunk(unjudged, batch_size)
    items: List[Dict[str, Any]] = [
        {"id": _batch_id(pj_slug, g), "utterances": g} for g in groups
    ]
    requests = build_requests(
        items, lambda item: _prompt.build_batch_prompt(item.get("utterances", []))
    )
    return {"requests": requests, "unjudged": len(unjudged), "batches": len(groups)}


# ─────────────────────────────────────────────────────────────────
# Phase C: ingest
# ─────────────────────────────────────────────────────────────────
def ingest_judgement_results(
    emitted: Dict[str, Any],
    responses: Dict[str, Any],
    *,
    dry_run: bool = False,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    judged_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """LLM 応答を回収し weak_signals 隔離記録 + 個人辞書蓄積する（決定論・LLM 非依存）。

    各バッチ:
      1. parse_responses + passthrough で生テキスト回収
      2. 空/missing はスキップ（判定済みにせずキューに残す＝再判定可能）
      3. prompt.parse_verdicts_result で JSON 解釈し ok=False（パース失敗）ならスキップ（#273:
         応答欠損と同じ「未判定のまま残す」経路に合流。verdict.index → 発話を引く（ok=True 時）
      4. is_correction=True → WeakSignal(channel=llm_judge) + CorrectionIdiom を蓄積
      5. バッチ内の発話の物理キーを judged に記録（修正/非修正どちらも。ok=False バッチは対象外）。
         verdicts が正当に空配列（モデルが「該当なし」と明示判定）ならバッチ全体を非修正
         として確定する（#273: 未判定に残すと再判定費用が際限なく積むため）。一部 index だけ
         verdict が欠けた**部分応答**は、揃っている index だけ確定し、欠落した index の発話は
         未判定のまま残す（judged に積まない・次回 drain で再試行。#410 [Must]D: バッチ全体を
         戻すと head-of-line blocking になるため避ける）。件数は omitted_verdicts に surface

    過汎用 idiom guard（#527）: floor（8 文字未満）/ stopword（相槌・推量・否定のみ）/
    文脈固有トークン（日付・割合・序数）に該当する idiom は **個人辞書に入れない**
    （weak_signal は隔離記録するので reflect で人間が拾える）。弾いた件数は idioms_filtered。

    Returns:
        {"corrections", "non_corrections", "skipped_batches", "parse_failed_batches",
         "omitted_verdicts", "weak_written", "idioms_written", "idioms_filtered",
         "judged_written", "dry_run"}
    """
    from weak_signals.store import WeakSignal, append_signals, now_iso

    requests = emitted.get("requests", [])
    parsed = parse_responses(requests, responses or {}, parser=passthrough)

    signals: List[WeakSignal] = []
    idioms: List[_store.CorrectionIdiom] = []
    judged_keys: List[str] = []
    # #410 [Must]B: 判定済みキーごとの推定トークンを残し、daily runner が当日累積を
    # read 時導出できるようにする（record_judged に渡す）。
    est_tokens_by_key: Dict[str, int] = {}
    corrections = 0
    non_corrections = 0
    skipped_batches = 0
    parse_failed_batches = 0  # #273: 応答は届いたが JSON が解釈不能だったバッチ数
    idioms_filtered = 0  # #527: 過汎用 idiom（floor/stopword/context token）で弾いた件数
    omitted_verdicts = 0  # #273: verdict が返らず非修正として確定した発話数（observability）

    for req in requests:
        key = req.get("id")
        if not key:
            continue
        group: List[Dict[str, Any]] = (req.get("meta") or {}).get("utterances", [])
        raw = parsed.get(key)
        text = raw.strip() if isinstance(raw, str) else ""
        if not text:
            # 応答欠損: 判定済みにせず次 drain で再試行
            skipped_batches += 1
            continue

        # 変数名は `parsed`（= parse_responses の応答マップ）と必ず分ける。
        # 同名にすると 2 バッチ目以降の `parsed.get(key)` が verdict dict を引いて
        # 全バッチ silent skip する（#273 レビューで検出した shadowing 回帰）。
        parsed_verdicts = _prompt.parse_verdicts_result(text)
        if not parsed_verdicts["ok"]:
            # #273: JSON 解釈不能。応答欠損と同様、判定済みにせず次 drain で再試行する
            # （[] フォールバックを「該当なし」と誤読して judged_keys に積むと desync する）。
            parse_failed_batches += 1
            continue

        by_index = {v["index"]: v for v in parsed_verdicts["verdicts"]}
        # #410 [Must]D: 「正当な全件非修正」（verdicts=[] を明示的に返した・#273）と
        # 「一部 index だけ欠落した部分応答」を区別する。前者はバッチ全体を確定させないと
        # 正当なケースが毎 drain 再判定され費用が積む（既存契約・
        # test_ingest_legitimate_empty_verdicts_still_marks_judged が固定）。後者は
        # バッチ全体を戻すと head-of-line blocking になるため、欠落した index の発話だけ
        # 未判定のまま残し、揃っている index は従来どおり確定する。
        legitimate_empty_batch = len(parsed_verdicts["verdicts"]) == 0

        for local_i, utt in enumerate(group):
            key = _store.utterance_key(utt)
            v = by_index.get(local_i)
            if v is None:
                if legitimate_empty_batch:
                    judged_keys.append(key)
                    est_tokens_by_key[key] = estimate_utterance_tokens(utt)
                    non_corrections += 1
                    continue
                # 部分応答の欠落: judged_keys に積まない（未判定のまま次回 drain で再試行）。
                # 件数だけ surface して silence にはしない（#273）。
                omitted_verdicts += 1
                continue
            judged_keys.append(key)
            est_tokens_by_key[key] = estimate_utterance_tokens(utt)
            if not v.get("is_correction"):
                non_corrections += 1
                continue
            corrections += 1
            idiom_text = v.get("idiom")
            prov = {
                "source_path": utt.get("source_path", ""),
                "line_no": utt.get("line_no", ""),
                "session_id": utt.get("session_id", ""),
                # #528-3: representative の判読性のため user 発話のみ保存（assistant の
                # 過去レポート引用ブロックを除去）+ 直前 AI 行動を evidence に添える。
                "text": _representative.user_only_text(utt.get("text") or "")[:200],
                "prev_action": (utt.get("prev_action") or "")[:120],
                "reason": v.get("reason", ""),
                # #253: Haiku が抽出した idiom を保存し、signal_text の多トピック発言トリム
                # （trim_to_idiom_sentence）に使う。idiom_eligible（個人辞書用ゲート）とは
                # 独立に保存する（トリム目的では eligibility を問わない）。
                "idiom": idiom_text or "",
                "judge": "llm_haiku",
            }
            detected_at = now_iso()
            signals.append(WeakSignal(
                channel=LLM_JUDGE_CHANNEL,
                provenance=prov,
                detected_at=detected_at,
                session_id=str(utt.get("session_id") or ""),
                pj_slug=str(utt.get("pj_slug") or ""),
            ))
            # #527: 過汎用 idiom（極短/相槌・推量/日付・数値断片）は個人辞書に入れない。
            # idiom 化を弾いても weak_signal は隔離記録済み（reflect で人間が拾える）。
            if idiom_text and _idiom_filter.idiom_eligible(idiom_text):
                idioms.append(_store.CorrectionIdiom(
                    idiom=idiom_text,
                    provenance=prov,
                    detected_at=detected_at,
                    pj_slug=str(utt.get("pj_slug") or ""),
                ))
            elif idiom_text:
                idioms_filtered += 1

    ws_res = append_signals(signals, path=weak_signals_path, dry_run=dry_run)
    idiom_res = _store.append_idioms(idioms, path=idioms_path, dry_run=dry_run)
    judged_res = _store.record_judged(
        judged_keys, path=judged_path, dry_run=dry_run,
        est_tokens_by_key=est_tokens_by_key,
    )

    return {
        "corrections": corrections,
        "non_corrections": non_corrections,
        "skipped_batches": skipped_batches,
        "parse_failed_batches": parse_failed_batches,
        "weak_written": ws_res["written"],
        "idioms_written": idiom_res["written"],
        "idioms_filtered": idioms_filtered,  # #527: 過汎用で弾いた idiom 件数（observability）
        "omitted_verdicts": omitted_verdicts,  # #273: verdict 欠落で非修正確定した発話数
        "judged_written": judged_res["written"],
        "dry_run": bool(dry_run),
    }


# ─────────────────────────────────────────────────────────────────
# トークン見積もり（llm-batch-guard: 実走前にユーザーへ提示）
# ─────────────────────────────────────────────────────────────────
def estimate_utterance_tokens(
    utterance: Dict[str, Any], *, max_chars: int = MAX_CHARS_PER_UTTERANCE
) -> int:
    """1 発話あたりの概算トークン（本文実長 + per-utterance オーバーヘッド）。

    ``estimate_tokens``（バッチ集計）と ``ingest_judgement_results``（#410 [Must]B:
    ``correction_judged.jsonl`` へ判定済みキーごとの推定コストを残し当日累積を導出する）が
    同じ単価を共有する単一ソース。プロンプト雛形分（``_PROMPT_OVERHEAD_TOKENS``）は
    バッチ単位のため含まない。
    """
    text = (utterance.get("text") or "")[:max_chars]
    return math.ceil(len(text) / _CHARS_PER_TOKEN) + _PER_UTTERANCE_OVERHEAD_TOKENS


def estimate_tokens(
    utterances: List[Dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    *,
    max_chars: int = MAX_CHARS_PER_UTTERANCE,
) -> Dict[str, Any]:
    """判定対象発話の概算トークン消費を返す（llm-batch-guard 用・決定論・#410 [Must]C）。

    各発話の本文実長（``max_chars`` で頭打ち・``build_batch_prompt`` と同じ切り詰め）に
    連動する。固定件数係数（旧実装）だと長文1件が短文1件と同じ見積もりになり、
    トークン上限が実効性を持たなかった。
    """
    items = utterances or []
    n = len(items)
    batches = (n + max(1, batch_size) - 1) // max(1, batch_size)
    body_tokens = sum(estimate_utterance_tokens(u, max_chars=max_chars) for u in items)
    est = body_tokens + batches * _PROMPT_OVERHEAD_TOKENS
    return {
        "utterances": n,
        "batches": batches,
        "batch_size": batch_size,
        "est_total_tokens": est,
    }
