#!/usr/bin/env python3
"""PostToolUse hook (matcher=Bash): 長時間コマンドを検出し subagent 移譲を提案する。

Bash ツールの tool_input.command を正規表現でマッチし、
デプロイ・ビルド等の長時間コマンドの場合のみ移譲を提案する。
同一セッションで同じカテゴリの提案は繰り返さない。
"""
import json
import re
import sys
import time
from pathlib import Path

try:
    from common import DATA_DIR
except ImportError:
    DATA_DIR = Path.home() / ".claude" / "rl-anything"

COUNTER_DIR = DATA_DIR / "session-counters"

# --- 長時間コマンドパターン ---
LONG_RUNNING_PATTERNS: dict[str, list[str]] = {
    "deploy": [
        r"\b(cdk|sam)\s+(deploy|synth|destroy)",
        r"\bterraform\s+(apply|plan|destroy)",
        r"\bpulumi\s+(up|preview|destroy)",
        r"\bserverless\s+deploy",
        r"\baws\s+cloudformation\s+(create|update|delete)-stack",
    ],
    "build": [
        r"\bdocker\s+(build|compose\s+up)\b",
        r"\bnpm\s+run\s+build\b",
        r"\byarn\s+build\b",
        r"\bpnpm\s+build\b",
        r"\bnext\s+build\b",
        r"\bcargo\s+build\b",
        r"\bgo\s+build\b",
    ],
    "test-suite": [
        r"\bpytest\b(?!.*-k\b)(?!.*::)",
        r"\bnpm\s+test\b",
        r"\byarn\s+test\b",
        r"\bcargo\s+test\b(?!.*--\s)",
    ],
    "install": [
        r"\bnpm\s+install\b(?!\s+--save)",
        r"\bpip\s+install\s+-r\b",
        r"\bbrew\s+install\b",
    ],
    "push": [
        r"\bgit\s+push\b",
        r"\bdocker\s+push\b",
    ],
    "migration": [
        r"\b(alembic|prisma|knex|flyway|liquibase)\s+(migrate|push|deploy)",
        r"\bmanage\.py\s+migrate\b",
        r"\bdjango.*migrate\b",
    ],
}

CATEGORY_MESSAGES: dict[str, str] = {
    "deploy": "デプロイコマンドを検出しました。完了を待つ間、他の作業を background subagent に移譲できます。",
    "build": "ビルドコマンドを検出しました。ビルド待ちの間、他のタスクを並行で進められます。",
    "test-suite": "テストスイート全体の実行を検出しました。結果待ちの間、他の作業を進められます。",
    "install": "パッケージインストールを検出しました。完了待ちの間、他の作業を進められます。",
    "push": "push コマンドを検出しました。",
    "migration": "マイグレーションを検出しました。完了を待つ間、他の作業を background subagent に移譲できます。",
}

COUNTER_TTL_SECONDS = 3600 * 4


def _counter_path(session_id: str) -> Path:
    safe_id = session_id.replace("/", "_")[:64]
    return COUNTER_DIR / f"delegation-{safe_id}.json"


def _load_suggested(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("started_at", 0) > COUNTER_TTL_SECONDS:
            return {"started_at": time.time(), "suggested_categories": []}
        return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {"started_at": time.time(), "suggested_categories": []}


def _save_suggested(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _detect_category(command: str) -> str | None:
    for category, patterns in LONG_RUNNING_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return category
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    category = _detect_category(command)
    if not category:
        sys.exit(0)

    session_id = data.get("session_id", "unknown")
    path = _counter_path(session_id)
    state = _load_suggested(path)

    if category in state.get("suggested_categories", []):
        sys.exit(0)

    state.setdefault("suggested_categories", []).append(category)
    _save_suggested(path, state)

    message = CATEGORY_MESSAGES.get(category, "長時間コマンドを検出しました。")
    result = {
        "systemMessage": (
            f"[subagent 移譲提案] {message}"
            "移譲する場合: 現在の作業状態を要約し、Agent ツール (run_in_background=true) で委譲。"
            "メインで継続する場合: そのまま作業を続行。"
        ),
    }
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
