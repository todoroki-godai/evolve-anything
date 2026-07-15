"""subagent_traces.extractor — transcript 1 本 → 軌跡メトリクス（#38, #200）。

決定論・ゼロ LLM。design「軌跡メトリクス」の SoT 実装。

transcript jsonl の各行は ``{"type": "user"|"assistant", "message": {...}, ...}``。
``message.content`` が list のとき、各ブロック dict の ``type``:
- ``tool_use``    : ``{"type":"tool_use","name":<tool名>, ...}``
- ``tool_result`` : ``{"type":"tool_result","is_error":<bool>, ...}`` ← is_error==True が失敗
- ``text``        : ``{"type":"text","text":...}``
``message.content`` が str のときは tool 集計対象外（テキスト発話のみ）。

#200: 委任内容（何を頼んだか）を事後監査可能にするため、transcript の起点行
（``parentUuid is None and type == "user"``）から委任プロンプト先頭 300 字を
``delegation_prompt`` として併せて抽出する（詳細は各 helper の docstring 参照）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

# harness が user content に注入する <system-reminder>...</system-reminder> 等の
# reminder タグ相当ブロックを除去する正規表現。除去してから truncate する。
_REMINDER_TAG_RE = re.compile(
    r"<[a-zA-Z_-]*reminder[^>]*>.*?</[a-zA-Z_-]*reminder[^>]*>", re.DOTALL
)

# 委任プロンプトの truncate 上限。store_registry.py の note にも同じ根拠を明記している
# （retention=permanent ゆえ transcript 削除後もこの文字数分は平文で残り続ける露出）。
DELEGATION_PROMPT_MAX_CHARS = 300


def _iter_content_blocks(message: Any):
    """1 メッセージの content から dict ブロックを順に yield する。

    content が list でなければ（str 等）何も yield しない（tool 集計対象外）。
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict):
            yield block


def _is_delegation_root_line(rec: Dict[str, Any]) -> bool:
    """委任プロンプトの起点行か（先頭行決め打ちにせず走査で探すための述語）。

    compaction が入った transcript では先頭行が summary 行になるケースがあるため、
    ``parentUuid is None and type == "user"`` を満たす **最初の** 行を委任プロンプトの
    起点とする。
    """
    return rec.get("parentUuid") is None and rec.get("type") == "user"


def _extract_message_text(message: Any) -> str:
    """メッセージから委任プロンプト候補のテキストを取り出す。

    content が str ならそのまま。list なら最初の ``type=="text"`` ブロックのみ採用
    （他ブロックは無視）。どちらにも該当しなければ空文字。
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                return text if isinstance(text, str) else ""
    return ""


def _build_delegation_prompt(raw_text: str) -> "tuple[str, bool]":
    """委任プロンプト候補テキストから reminder タグ除去 + truncate 済みの結果を作る。

    Returns:
        (delegation_prompt, delegation_prompt_truncated)
    """
    if not raw_text:
        return "", False
    cleaned = _REMINDER_TAG_RE.sub("", raw_text).strip()
    if not cleaned:
        return "", False
    if len(cleaned) <= DELEGATION_PROMPT_MAX_CHARS:
        return cleaned, False
    return cleaned[:DELEGATION_PROMPT_MAX_CHARS] + "…", True


def extract_trace(transcript_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """transcript を 1 本パースし軌跡メトリクスを返す。

    存在しない / 壊れた（全行 parse 不能で 0 行）ファイルは None。

    Returns:
        {
          "tool_use_count":    int,
          "tool_result_count": int,
          "tool_error_count":  int,   # is_error==True の tool_result 数
          "text_block_count":  int,
          "first_try_success": bool,  # tool_error_count == 0（ヒューリスティック）
          "tools":             {tool名: 回数},
          "delegation_prompt": str,   # #200: 委任プロンプト先頭300字（見つからなければ ""）
          "delegation_prompt_truncated": bool,  # 300字超で truncate したら True
        }
    """
    path = Path(transcript_path)
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    tool_use_count = 0
    tool_result_count = 0
    tool_error_count = 0
    text_block_count = 0
    tools: Dict[str, int] = {}
    parsed_any = False
    delegation_prompt = ""
    delegation_prompt_truncated = False
    delegation_line_found = False

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        parsed_any = True

        if not delegation_line_found and _is_delegation_root_line(rec):
            delegation_line_found = True
            raw_text = _extract_message_text(rec.get("message"))
            delegation_prompt, delegation_prompt_truncated = _build_delegation_prompt(raw_text)

        for block in _iter_content_blocks(rec.get("message")):
            btype = block.get("type")
            if btype == "tool_use":
                tool_use_count += 1
                name = block.get("name")
                if name:
                    tools[name] = tools.get(name, 0) + 1
            elif btype == "tool_result":
                tool_result_count += 1
                if block.get("is_error") is True:
                    tool_error_count += 1
            elif btype == "text":
                text_block_count += 1

    if not parsed_any:
        return None

    return {
        "tool_use_count": tool_use_count,
        "tool_result_count": tool_result_count,
        "tool_error_count": tool_error_count,
        "text_block_count": text_block_count,
        "first_try_success": tool_error_count == 0,
        "tools": tools,
        "delegation_prompt": delegation_prompt,
        "delegation_prompt_truncated": delegation_prompt_truncated,
    }
