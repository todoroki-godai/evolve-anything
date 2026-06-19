#!/usr/bin/env python3
"""Stop hook: AI の先送り提案を検出し、subagent 即時委譲を促す。

last_assistant_message に先送り表現が含まれる場合:
1. deferred_tasks.jsonl に記録（追跡用）
2. decision: "block" で会話を続行させ、subagent 実行を指示
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CLAUDE_PLUGIN_DATA", str(Path.home() / ".claude" / "evolve-anything")))
DEFERRED_LOG = DATA_DIR / "deferred_tasks.jsonl"

# 先送り検出パターン（AI 出力向け）
DEFER_PATTERNS = [
    r"後で(?:やり|対応|実装|作成|追加|修正|確認|検討)",
    r"(?:実装|作業|タスク|対応)(?:が)?(?:終わ|完了)(?:った|し)(?:た)?(?:ら|後)",
    r"(?:別途|改めて|次回|後ほど)(?:対応|実装|作成|検討)",
    r"(?:しましょう|やりましょう)(?:か)?[？?]?\s*$",
    r"(?:change|PR|issue)を(?:起こし|作り|立て)(?:ましょう|ます)",
    r"(?:TODO|FIXME|HACK)(?:として|に)(?:残し|記録)",
    r"(?:一旦|いったん)(?:スキップ|飛ばし|後回し)",
    r"(?:後日|将来的に|そのうち)(?:対応|実装|検討)",
]

_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in DEFER_PATTERNS]


def detect_deferral(text: str) -> str | None:
    """先送り表現を検出し、マッチしたパターンを返す。"""
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def log_deferral(session_id: str, matched: str, snippet: str) -> None:
    """deferred_tasks.jsonl に記録。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "matched_pattern": matched,
        "snippet": snippet[:300],
    }
    with open(DEFERRED_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    # 無限ループ防止: stop_hook_active が true なら何もしない
    if data.get("stop_hook_active", False):
        sys.exit(0)

    message = data.get("last_assistant_message", "")
    if not message:
        sys.exit(0)

    matched = detect_deferral(message)
    if not matched:
        sys.exit(0)

    session_id = data.get("session_id", "unknown")

    # マッチ周辺のスニペットを抽出
    idx = message.find(matched)
    start = max(0, idx - 50)
    end = min(len(message), idx + len(matched) + 100)
    snippet = message[start:end]

    log_deferral(session_id, matched, snippet)

    # 会話続行を強制し、subagent 実行を指示
    result = {
        "decision": "block",
        "reason": (
            f"先送り表現を検出しました: 「{matched}」。"
            "ルール「no-defer-use-subagent」に従い、先送りせず background subagent を即座に起動して並行処理してください。"
        ),
    }
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
