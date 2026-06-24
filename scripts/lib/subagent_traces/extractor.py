"""subagent_traces.extractor — transcript 1 本 → 軌跡メトリクス（#38）。

決定論・ゼロ LLM。design「軌跡メトリクス」の SoT 実装。

transcript jsonl の各行は ``{"type": "user"|"assistant", "message": {...}, ...}``。
``message.content`` が list のとき、各ブロック dict の ``type``:
- ``tool_use``    : ``{"type":"tool_use","name":<tool名>, ...}``
- ``tool_result`` : ``{"type":"tool_result","is_error":<bool>, ...}`` ← is_error==True が失敗
- ``text``        : ``{"type":"text","text":...}``
``message.content`` が str のときは tool 集計対象外（テキスト発話のみ）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union


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
    }
