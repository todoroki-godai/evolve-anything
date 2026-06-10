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
    llm_broker.parse_responses で id→生テキスト回収 → prompt.parse_verdicts で JSON 解釈。
    修正と判定された発話を **weak_signals レーン（channel=llm_judge）に隔離記録** +
    **個人辞書（correction_idioms.jsonl）に provenance 付き蓄積**。判定し終えた発話の
    物理キーを correction_judged.jsonl に記録（再判定防止）。応答欠損バッチは判定済みに
    せずスキップ（次 drain で再試行）。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: ``dry_run=True`` のとき判定は走るが
weak_signals / 個人辞書 / 判定進捗のどれにも一切書かない（各 append が最下層で弾く）。

非対話 PJ 除外: utterance 側で担保する。query_utterances のデフォルト source_kinds=('dialogue',)
は long_paste / excluded_pj を含めないため、emit にはそもそも対話発話しか渡らない。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough  # noqa: E402

from . import DEFAULT_BATCH_SIZE, LLM_JUDGE_CHANNEL
from . import prompt as _prompt
from . import store as _store

# 1 発話あたりの概算トークン（プロンプト雛形 + 発話本文）。est 用の粗い係数。
# 日本語は 1 字 ≈ 1 トークン超だが、入力 + 出力 + 雛形オーバーヘッドを丸めた係数で十分。
_TOKENS_PER_UTTERANCE = 80
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
      3. prompt.parse_verdicts で JSON 解釈し、verdict.index → 発話を引く
      4. is_correction=True → WeakSignal(channel=llm_judge) + CorrectionIdiom を蓄積
      5. バッチ内の全発話の物理キーを judged に記録（修正/非修正どちらも）

    Returns:
        {"corrections", "non_corrections", "skipped_batches",
         "weak_written", "idioms_written", "judged_written", "dry_run"}
    """
    from weak_signals.store import WeakSignal, append_signals, now_iso

    requests = emitted.get("requests", [])
    parsed = parse_responses(requests, responses or {}, parser=passthrough)

    signals: List[WeakSignal] = []
    idioms: List[_store.CorrectionIdiom] = []
    judged_keys: List[str] = []
    corrections = 0
    non_corrections = 0
    skipped_batches = 0

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

        verdicts = _prompt.parse_verdicts(text)
        by_index = {v["index"]: v for v in verdicts}

        for local_i, utt in enumerate(group):
            judged_keys.append(_store.utterance_key(utt))
            v = by_index.get(local_i)
            if v is None or not v.get("is_correction"):
                non_corrections += 1
                continue
            corrections += 1
            prov = {
                "source_path": utt.get("source_path", ""),
                "line_no": utt.get("line_no", ""),
                "session_id": utt.get("session_id", ""),
                "text": (utt.get("text") or "")[:200],
                "reason": v.get("reason", ""),
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
            idiom_text = v.get("idiom")
            if idiom_text:
                idioms.append(_store.CorrectionIdiom(
                    idiom=idiom_text,
                    provenance=prov,
                    detected_at=detected_at,
                    pj_slug=str(utt.get("pj_slug") or ""),
                ))

    ws_res = append_signals(signals, path=weak_signals_path, dry_run=dry_run)
    idiom_res = _store.append_idioms(idioms, path=idioms_path, dry_run=dry_run)
    judged_res = _store.record_judged(judged_keys, path=judged_path, dry_run=dry_run)

    return {
        "corrections": corrections,
        "non_corrections": non_corrections,
        "skipped_batches": skipped_batches,
        "weak_written": ws_res["written"],
        "idioms_written": idiom_res["written"],
        "judged_written": judged_res["written"],
        "dry_run": bool(dry_run),
    }


# ─────────────────────────────────────────────────────────────────
# トークン見積もり（llm-batch-guard: 実走前にユーザーへ提示）
# ─────────────────────────────────────────────────────────────────
def estimate_tokens(
    utterances: List[Dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    """判定対象発話の概算トークン消費を返す（llm-batch-guard 用・決定論）。"""
    n = len(utterances or [])
    batches = (n + max(1, batch_size) - 1) // max(1, batch_size)
    est = n * _TOKENS_PER_UTTERANCE + batches * _PROMPT_OVERHEAD_TOKENS
    return {
        "utterances": n,
        "batches": batches,
        "batch_size": batch_size,
        "est_total_tokens": est,
    }
