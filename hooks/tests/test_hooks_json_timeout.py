"""hooks.json の timeout は秒単位（Claude Code の hook 仕様）。

過去に 3000 / 5000 をミリ秒のつもりで書いており、実際は 50〜83 分待つ設定だった。
hook が固まったときの歯止めとして働く値であることを検査する。
"""
import json
from pathlib import Path

HOOKS_JSON = Path(__file__).resolve().parents[1] / "hooks.json"

# 歯止めとして許す上限（秒）。実測は全 hook 0.31 秒以内（2026-10-09）。
MAX_TIMEOUT_SECONDS = 60


def _handlers():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in data["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, handler


def test_hooks_json_has_handlers():
    assert list(_handlers())


def test_every_hook_timeout_is_seconds_within_bound():
    offenders = [
        (event, handler.get("command"), handler.get("timeout"))
        for event, handler in _handlers()
        if not isinstance(handler.get("timeout"), (int, float))
        or isinstance(handler.get("timeout"), bool)
        or not 0 < handler["timeout"] <= MAX_TIMEOUT_SECONDS
    ]
    assert offenders == []
