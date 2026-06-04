#!/usr/bin/env python3
"""LLM セマンティック検証モジュール。

[ADR-037] Phase 1d-i: claude -p を全廃しファイルベース2相化。
- validate_corrections / detect_contradictions は決定論フォールバック（LLM-free）。
- LLM 品質は emit_validation_requests / ingest_validation_results の2相（SKILL 駆動）で回復する。
- detect_contradictions は emit_contradiction_request / ingest_contradictions の2相（SKILL 駆動）で回復する。
"""
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# common.py の sanitize_message を利用
_hooks_dir = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(_hooks_dir))
from common import sanitize_message

# llm_broker を import（constitutional.py と同じ流儀）
_plugin_root = Path(__file__).resolve().parent.parent.parent
_lib_dir = str(_plugin_root / "scripts" / "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)
_scripts_dir = str(_plugin_root / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
from llm_broker import build_requests, parse_responses, passthrough

BATCH_SIZE = 20

ANALYSIS_PROMPT = """以下の corrections リストを分析し、各項目が本当に「学習すべき修正」かどうか判定してください。

判定基準:
- is_learning: true — ユーザーが Claude の行動を変えてほしいと明示的に指示している
- is_learning: false — 質問、タスク依頼、エラー報告、雑談、文脈なしの否定

各項目について以下の JSON 配列を返してください（他のテキストは不要）:
[
  {{"index": 0, "is_learning": true/false, "extracted_learning": "簡潔な学習文 or null"}},
  ...
]

corrections:
{corrections_json}
"""


CONTRADICTION_PROMPT = """以下の corrections リストで、矛盾するペアを見つけてください。
矛盾 = 同じ対象について相反する指示（例: 「日本語で応答して」と「英語で応答して」）

corrections:
{corrections_json}

矛盾ペアがあれば以下の JSON 配列で回答してください（なければ空配列 []）:
[{{"pair": [index_a, index_b], "reason": "矛盾の理由"}}]
"""


def _extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """テキストから JSON 配列を抽出する。"""
    # まず直接パース
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # コードブロック内を探す
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # [ から ] までを探す
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def validate_corrections(
    corrections: List[Dict[str, Any]],
    model: str = "sonnet",
) -> List[Dict[str, Any]]:
    """決定論フォールバック。全件 is_learning=True / extracted_learning=None を返す。

    model 引数は後方互換のため残すが無視する。
    LLM 品質は emit_validation_requests / ingest_validation_results の2相（SKILL 駆動）で回復する。
    """
    return [{"is_learning": True, "extracted_learning": None} for _ in corrections]


def detect_contradictions(
    corrections: List[Dict[str, Any]],
    model: str = "sonnet",
) -> List[Dict[str, Any]]:
    """決定論フォールバック。常に空リストを返す（矛盾ペア検出なし）。

    model 引数は後方互換のため残すが無視する。
    LLM による矛盾検出は emit_contradiction_request / ingest_contradictions の2相（SKILL 駆動）で回復する。
    """
    return []


# ---------------------------------------------------------------------------
# Phase A: emit（決定論で「何を聞くか」を作るだけ。LLM を呼ばない）
# ---------------------------------------------------------------------------


def emit_validation_requests(corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase A: corrections の is_learning 判定リクエストを生成する（決定論・LLM 非依存）。

    BATCH_SIZE ごとにバッチ化し、各バッチを1リクエストにする。
    欠損 responses は ingest_validation_results 側で全件 is_learning=True にフォールバックする。

    Returns:
        {"requests": [{"id": "validate:<offset>", "prompt": str, "meta": {"offset", "size"}}, ...]}
    """
    if not corrections:
        return {"requests": []}

    items: List[Dict[str, Any]] = []
    for batch_start in range(0, len(corrections), BATCH_SIZE):
        batch = corrections[batch_start:batch_start + BATCH_SIZE]
        batch_items = [
            {"index": i, "message": sanitize_message(c.get("message", ""))}
            for i, c in enumerate(batch)
        ]
        items.append({
            "id": f"validate:{batch_start}",
            "offset": batch_start,
            "size": len(batch),
            "_batch_items": batch_items,
        })

    def prompt_fn(item: Dict[str, Any]) -> str:
        return ANALYSIS_PROMPT.format(
            corrections_json=json.dumps(item["_batch_items"], ensure_ascii=False, indent=2)
        )

    requests = build_requests(items, prompt_fn)
    # 一時フィールドを meta から除去
    for req in requests:
        req["meta"].pop("_batch_items", None)

    return {"requests": requests}


def emit_contradiction_request(corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase A: corrections 内の矛盾検出リクエストを生成する（決定論・LLM 非依存）。

    0件または1件の場合は空（矛盾が成立しない）。
    2件以上の場合は単一リクエストを生成する。

    Returns:
        {"requests": [{"id": "contradictions", "prompt": str, "meta": {}}]}
    """
    if len(corrections) <= 1:
        return {"requests": []}

    items_data = [
        {"index": i, "message": sanitize_message(c.get("message", ""))}
        for i, c in enumerate(corrections)
    ]
    items = [{"id": "contradictions", "_items": items_data}]

    def prompt_fn(item: Dict[str, Any]) -> str:
        return CONTRADICTION_PROMPT.format(
            corrections_json=json.dumps(item["_items"], ensure_ascii=False, indent=2)
        )

    requests = build_requests(items, prompt_fn)
    # 一時フィールドを meta から除去
    for req in requests:
        req["meta"].pop("_items", None)

    return {"requests": requests}


# ---------------------------------------------------------------------------
# Phase C: ingest（assistant の応答を回収してパースするだけ。LLM を呼ばない）
# ---------------------------------------------------------------------------


def ingest_validation_results(
    corrections: List[Dict[str, Any]],
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Phase C: is_learning 判定結果を回収してパースする（決定論・LLM 非依存）。

    既定値は全件 {"is_learning": True, "extracted_learning": None}。
    各リクエストの応答をパースし、index ベースでマッチングする。
    欠損応答（None）・パース失敗・index 欠落は既定値のままにする。

    Args:
        corrections: emit_validation_requests に渡した corrections と同じリスト。
        requests: emit_validation_requests の返り値の "requests" リスト。
        responses: {request_id: 生テキスト} の応答マップ（assistant が埋める）。

    Returns:
        corrections と同数・同順のリスト。各要素は {"is_learning": bool, "extracted_learning": Optional[str]}。
    """
    # 既定値で全件初期化
    result: List[Dict[str, Any]] = [
        {"is_learning": True, "extracted_learning": None} for _ in corrections
    ]

    parsed_map = parse_responses(requests, responses, parser=passthrough)

    for req in requests:
        req_id = req["id"]
        offset = req["meta"].get("offset", 0)
        size = req["meta"].get("size", 0)
        raw = parsed_map.get(req_id)
        if raw is None:
            continue  # 応答欠損 → 既定値のまま

        parsed = _extract_json_array(raw) if isinstance(raw, str) else None
        if not parsed:
            continue  # パース失敗 → 既定値のまま

        matched = {item.get("index", pos): item for pos, item in enumerate(parsed)}
        for i in range(size):
            global_idx = offset + i
            if i in matched:
                item = matched[i]
                result[global_idx] = {
                    "is_learning": item.get("is_learning", True),
                    "extracted_learning": item.get("extracted_learning"),
                }

    return result


def ingest_contradictions(
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Phase C: 矛盾検出結果を回収してパースする（決定論・LLM 非依存）。

    requests が空・応答欠損・パース失敗はいずれも [] を返す。
    pair が [int, int] 形式のエントリのみ返す（不正エントリは除外）。

    Args:
        requests: emit_contradiction_request の返り値の "requests" リスト。
        responses: {request_id: 生テキスト} の応答マップ（assistant が埋める）。

    Returns:
        矛盾ペアのリスト。各要素は {"pair": [int, int], "reason": str}。
    """
    if not requests:
        return []

    parsed_map = parse_responses(requests, responses, parser=passthrough)
    raw = parsed_map.get("contradictions")
    if raw is None:
        return []

    parsed = _extract_json_array(raw) if isinstance(raw, str) else None
    if not parsed:
        return []

    validated = []
    for item in parsed:
        pair = item.get("pair")
        reason = item.get("reason", "")
        if (
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(idx, int) for idx in pair)
        ):
            validated.append({"pair": pair, "reason": reason})

    return validated
