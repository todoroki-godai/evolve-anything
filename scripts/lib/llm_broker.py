"""LLM ブローカ — claude -p 全廃のためのファイルベース2相パターン共通基盤 ([ADR-037])。

3 相に分離することで Python から LLM 呼び出し（claude -p）を完全に追い出す:
  Phase A（決定論・Python）: build_requests で「何を LLM に投げるか」を JSON 化
  Phase B（LLM・assistant）: インライン or Task subagent が各 prompt に応答（本モジュール対象外）
  Phase C（決定論・Python）: parse_responses で id→応答を回収し欠損を fallback で穴埋め

Python は一切 claude -p を呼ばないため no-llm-in-tests と完全整合（mock 不要）。
採点系 consumer は parser=parse_score、生成系 consumer は parser=passthrough を使う。
"""

import re
from typing import Any, Callable, Dict, List

FALLBACK_SCORE = 0.5
_SCORE_RE = re.compile(r"(0\.\d+|1\.0|0|1)")


def parse_score(raw: Any, fallback: float = FALLBACK_SCORE) -> float:
    """LLM 出力（テキスト or 数値）からスコア float を抽出する。抽出不能なら fallback。

    bool は数値扱いしない（True/False を 1.0/0.0 に化けさせて採点値を取り違えないため）。
    """
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, (int, float)):
        return float(raw)
    if raw is None:
        return fallback
    match = _SCORE_RE.search(str(raw).strip())
    return float(match.group(1)) if match else fallback


def passthrough(raw: Any, fallback: str = "") -> Any:
    """生成系 consumer 用の素通しパーサ。欠損(None)のみ fallback に置換する。"""
    return fallback if raw is None else raw


def build_requests(items: List[Dict], prompt_fn: Callable[[Dict], str]) -> List[Dict]:
    """items から LLM リクエスト一覧を決定論で生成する（Phase A）。

    各 item は ``{"id": str, ...}`` を持つ dict。id 以外のフィールドは meta に保持され、
    Phase C で集約に使える（例: run / axis / target）。prompt_fn(item) -> プロンプト文字列。

    Returns:
        List[{"id": str, "prompt": str, "meta": dict}]
    """
    requests: List[Dict] = []
    for item in items:
        if "id" not in item:
            raise ValueError(f"item に 'id' が必要です: {item!r}")
        meta = {k: v for k, v in item.items() if k != "id"}
        requests.append({"id": item["id"], "prompt": prompt_fn(item), "meta": meta})
    return requests


def parse_responses(
    requests: List[Dict],
    responses: Dict[str, Any],
    parser: Callable[[Any], Any] = passthrough,
) -> Dict[str, Any]:
    """Phase B の応答(id→生テキスト/値)を回収し parser で正規化する（Phase C）。

    requests を単一ソースとして全 id を走査するため、responses の欠損 id は
    parser(None)=fallback で穴埋めされ、assistant の応答漏れで壊れない。
    responses 側の余分な id は無視される。

    Returns:
        {request_id: parser(response)}
    """
    return {req["id"]: parser(responses.get(req["id"])) for req in requests}
