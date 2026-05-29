#!/usr/bin/env python3
"""PostToolUse hook — 管理対象 pitfalls.md の Edit/Write 直後に正準フォーマットを lint。

「最新版を入れて enable を 1 回叩くと、以後 pitfalls の追加/修正/削除に自動でルールが
当たる」を実現する編集時ステージ。`enable` で登録された pitfalls.md にのみ反応する
（オプトイン: pitfall_registry が空なら無反応）。

方針（ユーザー確認済み）:
- **警告のみ・非ブロッキング**。自動書き換えはしない（編集途中の中間状態を壊さないため）。
- drift（正準形と差分）→ 提案 diff の冒頭を警告表示。
- danger（index/TOC 等 wipe 危険）→ 強めの警告。ただし編集時はブロックしない
  （確定状態をブロックするのは commit 時ゲート pitfall_commit_gate.py の役割）。
- ok → 無音。
LLM は呼ばない（MUST NOT）。失敗時はセッションをブロックしない（サイレント失敗）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

_HOOK_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _HOOK_DIR.parent
for _p in (
    _PLUGIN_ROOT / "scripts" / "lib",
    _PLUGIN_ROOT / "skills" / "pitfall-curate" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pitfall_registry
from parse import check_normalized

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
_DIFF_PREVIEW_LINES = 12


def _diff_preview(diff: str) -> str:
    lines = diff.splitlines()
    head = lines[:_DIFF_PREVIEW_LINES]
    if len(lines) > _DIFF_PREVIEW_LINES:
        head.append(f"  … (他 {len(lines) - _DIFF_PREVIEW_LINES} 行)")
    return "\n".join(head)


def evaluate(event: dict, project_dir: str) -> Optional[str]:
    """イベントを評価し、警告メッセージ（無ければ None）を返す純粋関数。

    副作用なし（ファイルを読むだけ。書き換えない）。テストはこの関数を直接叩く。
    """
    if event.get("tool_name") not in _EDIT_TOOLS:
        return None
    tool_result = event.get("tool_result") or {}
    if isinstance(tool_result, dict) and tool_result.get("is_error"):
        return None
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None
    if not project_dir or not pitfall_registry.is_managed(project_dir, file_path):
        return None  # enable されていないファイルには反応しない（オプトイン）
    path = Path(file_path)
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    res = check_normalized(content)
    if res["state"] == "ok":
        return None
    if res["state"] == "danger":
        return (
            f"[rl-anything:pitfall_lint] ⚠ {path.name}: {res['reason']}\n"
            "  このまま commit すると内容を失う恐れがあります。"
            "`### タイトル` 形式のエントリへ再構成してください。"
        )
    return (
        f"[rl-anything:pitfall_lint] ⚠ {path.name} が正準フォーマットと差分があります"
        "（自動修正はしません）。正準化の提案:\n"
        f"{_diff_preview(res['diff'])}\n"
        f"  揃えるには: pitfall_curate.py normalize --pitfalls {file_path} --out {file_path}"
    )


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        msg = evaluate(event, project_dir)
        if msg:
            print(msg, flush=True)
    except Exception:
        pass  # サイレント失敗（セッションをブロックしない）


if __name__ == "__main__":
    main()
