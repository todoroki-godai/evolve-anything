"""correction_semantic.verify — 2段目（確かめ直し）判定（#682）。

#682 柱1 精度回復の設計書（issue #682「精度回復の設計書」§3・§5）: 1段目
（``prompt.build_batch_prompt`` / ``judge_runner.call_haiku``）が「修正」と判定した発話
**だけ**を、直前の Claude 発言（先頭400字）と並べてもう一度確かめる。2段目は
**陽性→陰性にしか動かせない**（reject のときだけ 1段目の verdict を書き換える）。

評価（``scripts/bench/judge_eval.py``）と本番（``judge_runner.run_daily_judge``）は
どちらもこの ``apply_verification`` を通す（完成条件①: 入力・判定関数が同一）。
``prior_fn`` の既定実装（``_default_prior_fn``）が両方の入力形を吸収する:
  - 評価: 凍結済み評価セットの ``prior_assistant_text``（``a0_capture_replay`` が凍結時に
    ``fetch_prior_assistant_text`` で作った値）をそのまま使う。
  - 本番: utterance dict に ``prior_assistant_text`` が無いため、``source_path`` /
    ``line_no`` から ``fetch_prior_assistant_text`` を都度呼んで取得する。

失敗・文脈欠落は「1段目の陽性を残す」方向にしか倒れない（完成条件③）:
  - 直前発言が取得できない → 2段目を呼ばず ``no_context`` に計上し 1段目のまま。
  - 2段目の呼び出し例外・応答パース失敗・該当 index 欠落 → ``failed`` に計上し 1段目のまま。
  - 2段目が明示的に ``reject`` と答えたときだけ ``is_correction=False`` へ書き換える。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from . import DEFAULT_BATCH_SIZE, DEFAULT_JUDGE_MODEL
from . import prompt as _prompt
from . import store as _store

# a0_capture_replay.py から移設（#682）。assistant テキストを遡って探す行数。round2 codex
# [Must-6] で判明: IDE メタデータ行（attachment/last-prompt/ai-title/mode/permission-mode/
# pr-link/file-history-snapshot 等、type が user/assistant でない行）が直前の実 assistant
# ターンとの間に 30 行を超えて挟まるケースが実測で複数見つかった（例: 44行・36行離れていた）。
# 30→150 に拡張済みの値をそのまま引き継ぐ。
_PRIOR_LOOKBACK_LINES = 150


def fetch_prior_assistant_text(source_path: str, line_no: int, max_chars: int = 400) -> Optional[str]:
    """raw transcript から、当該行より前の直近 assistant テキストを取得する。

    ``scripts/bench/a0_capture_replay.py`` から移設（#682・単一化）。凍結済み評価コーパスの
    ``prior_assistant_text`` はコーパス構築時に本関数を1度だけ呼んで作られた値（凍結済み・
    以後変わらない）。本番の2段目はここを都度呼んで直前発言を取得する（``_default_prior_fn``
    経由）。

    utterances.db の prev_action 列はツール名列のみ（実内容なし）で、かつ実測で
    extractor_version=2（2026-07-14 以降の再抽出分）は 0/1971 件が非 null という
    データ欠損があり、本コーパス窓（07-27以降）では使用不能と判明した
    （codex [Must]5 への回答は本関数による raw transcript 直読みで代替する）。
    """
    p = Path(source_path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    # line_no は 1-indexed（utterances.db 保存規約に合わせる）
    idx = line_no - 1
    for i in range(idx - 1, max(-1, idx - _PRIOR_LOOKBACK_LINES), -1):
        if i < 0 or i >= len(lines):
            continue
        try:
            obj = json.loads(lines[i])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    parts.append(f"[tool_use:{block.get('name')}]")
            text = " ".join(parts)
        if text.strip():
            return text.strip()[:max_chars]
    return None


def _default_prior_fn(utterance: Dict[str, Any]) -> Optional[str]:
    """評価・本番の両方で使う既定の直前発言取得（完成条件①の単一ソース）。

    評価コーパス行は凍結済み ``prior_assistant_text`` キーを持つ（None でも「キーがある」
    ことで区別する）。本番 utterance dict はこのキーを持たないため、``source_path`` /
    ``line_no`` から都度 ``fetch_prior_assistant_text`` を呼ぶ。
    """
    if "prior_assistant_text" in utterance:
        return utterance.get("prior_assistant_text")
    source_path = utterance.get("source_path")
    line_no = utterance.get("line_no")
    if not source_path or not line_no:
        return None
    return fetch_prior_assistant_text(source_path, line_no)


# #682 §5: 2段目の固定オーバーヘッド（``batch._PROMPT_OVERHEAD_TOKENS`` と同型・同じ
# _CHARS_PER_TOKEN 保守係数=2文字/トークン）。batch.py の private 定数を再定義せず import して
# 単一ソースを保つ（循環 import なし: batch.py は verify.py を import しない）。
from . import batch as _batch  # noqa: E402

_VERIFY_PROMPT_OVERHEAD_TOKENS = math.ceil(
    len(_prompt.build_verify_prompt([])) / _batch._CHARS_PER_TOKEN
) + math.ceil(len(_prompt.VERIFY_JSON_SCHEMA) / _batch._CHARS_PER_TOKEN)

# 出力（{"index": N, "verdict": "reject"} 1件）の概算トークン。keep/reject 2択なので
# 1段目の verdict（idiom/reason/category 込み）より小さい固定値で見積もる。
_VERIFY_OUTPUT_TOKENS_PER_ITEM = 15


def _estimate_verify_tokens(items: List[Dict[str, Any]]) -> int:
    """1バッチ試行分の概算トークン（``batch._batch_cost_tokens`` と同型・#682）。"""
    body = sum(
        math.ceil(len(_prompt.format_verify_line(i, it)) / _batch._CHARS_PER_TOKEN)
        for i, it in enumerate(items)
    )
    return body + _VERIFY_PROMPT_OVERHEAD_TOKENS + len(items) * _VERIFY_OUTPUT_TOKENS_PER_ITEM


def reserve_verify_cost(
    items: List[Dict[str, Any]], *, judged_path: Optional[Path] = None, dry_run: bool = False
) -> Dict[str, Any]:
    """2段目1バッチの見積コストを、呼び出し**前**に予約記録する（完成条件④）。

    ``batch.reserve_batch_cost`` と同じ設計（round4 [Must]1+2）: 呼んだ時点で「課金される
    可能性がある」とみなし無条件に予約するため、応答欠損・パース失敗・呼び出し例外の
    いずれでも予約は残る。1段目と同じ ``correction_judged.jsonl``（keyless billed attempts）
    に積むため、1日の当日累積（``count_judged_today``）にそのまま合流する。
    """
    return _store.record_billed_attempts(
        [_estimate_verify_tokens(items)], path=judged_path, dry_run=dry_run,
    )


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


def apply_verification(
    groups: Dict[str, List[Dict[str, Any]]],
    stage1_verdicts: Dict[str, List[Dict[str, Any]]],
    *,
    prior_fn: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    call_fn: Callable[[str, str], str],
    reserve_fn: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    model: str = DEFAULT_JUDGE_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    """1段目の陽性だけを、直前発言と並べてもう一度確かめる（#682・完成条件①〜④の実体）。

    Args:
        groups: ``{key: [utterance, ...]}``。1段目のバッチ構造（judge_runner の
            ``emitted["requests"]`` の meta.utterances、または judge_eval が組む合成キー）。
        stage1_verdicts: ``{key: [verdict_dict, ...]}``。``prompt.parse_verdicts_result``
            が返す ``verdicts`` リストと同型（``index`` は ``groups[key]`` 内のローカル位置）。
            ``ok=False`` だった key はここに含めないこと（呼び出し側の責務）。
        prior_fn: 直前発言取得（既定 ``_default_prior_fn`` — 評価・本番共通の単一ソース）。
        call_fn: 2段目 LLM 呼び出し（``judge_runner.call_haiku_verify`` 相当）。
        reserve_fn: 呼び出し**前**の費用予約（None なら予約しない＝eval 用途の既定）。
        batch_size: 2段目のバッチサイズ。1段目とは独立に、陽性を**全体から集めて**
            再チャンクする（1段目バッチをまたいで集約するほど呼び出し回数が減る・設計§3）。

    Returns:
        {"verdicts": {key: [verdict_dict, ...]}}  — **変更が入った key だけ**を含む
            （reject が1件も出なかった key は含めない。呼び出し側はこれを「元の応答は
            書き換え不要」の合図として使ってよい）。書き換えられた verdict は
            ``is_correction=False``・``idiom=None``・``category=None``。
        "calls": 2段目のバッチ呼び出し回数
        "rejected": reject（陽性→陰性へ反転）件数
        "kept": 2段目が明示的に keep と答えた件数
        "failed": 2段目の呼び出し例外・パース失敗・該当 index 欠落の件数（1段目を維持）
        "no_context": 直前発言が取得できず2段目を呼ばなかった件数（1段目を維持）
    """
    prior_fn = prior_fn or _default_prior_fn

    # 陽性候補を集める: (key, verdict のリスト内位置, utterance, verdict)。書き換えは
    # 「verdicts リストの位置」で行う（``verdict["index"]`` は group 内の発話位置で、
    # 部分応答・順不同のときリスト位置とは一致しない）。
    candidates: List[tuple] = []
    for key, verdicts in stage1_verdicts.items():
        group = groups.get(key, [])
        for pos, v in enumerate(verdicts):
            if not v.get("is_correction"):
                continue
            idx = v.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(group)):
                continue
            candidates.append((key, pos, group[idx], v))

    counts = {"calls": 0, "rejected": 0, "kept": 0, "failed": 0, "no_context": 0}
    result_verdicts: Dict[str, List[Dict[str, Any]]] = {}

    def _ensure_copy(key: str) -> List[Dict[str, Any]]:
        if key not in result_verdicts:
            result_verdicts[key] = [dict(v) for v in stage1_verdicts[key]]
        return result_verdicts[key]

    to_verify: List[tuple] = []
    for key, pos, utt, v in candidates:
        try:
            prior_text = prior_fn(utt)
        except Exception:  # noqa: BLE001 - 取得失敗は「文脈なし」＝1段目を残す
            prior_text = None
        if not prior_text:
            counts["no_context"] += 1
            continue
        to_verify.append((key, pos, utt, v, prior_text))

    for chunk in _chunk(to_verify, batch_size):
        items = [
            {"text": utt.get("text") or "", "prior_assistant_text": prior_text}
            for (_key, _pos, utt, _v, prior_text) in chunk
        ]
        if reserve_fn is not None:
            try:
                reserve_fn(items)
            except Exception:  # noqa: BLE001 - 予約を記録できないときは呼ばない（予算の歯止め）
                counts["failed"] += len(chunk)  # 1段目の陽性は残す
                continue
        counts["calls"] += 1
        try:
            raw = call_fn(_prompt.build_verify_prompt(items), model)
        except Exception:  # noqa: BLE001 - 2段目の失敗は1段目の陽性を残す（黙って消さない）
            counts["failed"] += len(chunk)
            continue
        parsed = _prompt.parse_verify_result(raw, expected_len=len(chunk))
        if not parsed["ok"]:
            counts["failed"] += len(chunk)
            continue
        by_local = {d["index"]: d for d in parsed["verdicts"]}
        for local_i, (key, pos, _utt, _v, _prior_text) in enumerate(chunk):
            d = by_local.get(local_i)
            if d is None:
                counts["failed"] += 1
                continue
            if d["verdict"] == "reject":
                vlist = _ensure_copy(key)
                vlist[pos]["is_correction"] = False
                vlist[pos]["idiom"] = None
                vlist[pos]["category"] = None
                counts["rejected"] += 1
            else:
                counts["kept"] += 1

    return {"verdicts": result_verdicts, **counts}
