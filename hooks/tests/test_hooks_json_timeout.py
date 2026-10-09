"""hooks.json の timeout は秒単位（Claude Code の hook 仕様）。

過去に 3000 / 5000 をミリ秒のつもりで書いており、実際は 50〜83 分待つ設定だった。
timeout は「固まった hook を切る歯止め」であって処理時間の予算ではない。hook 内部の
時間予算（save_state の git 2秒×複数回・live_checkout の git 10秒×5回 など）より
短くすると正常処理を切るので、歯止めの値に揃える。SessionStart だけは live_checkout の
内部予算が最大 100 秒（環境変数一覧の取得 10 秒 + 本体 10 秒が 5 回）あるため長くする。
"""
import json
from pathlib import Path

HOOKS_JSON = Path(__file__).resolve().parents[1] / "hooks.json"

# 歯止めの値（秒）。hook 内部の時間予算より長く、分単位では待たせない。
HOOK_TIMEOUT_SECONDS = 60
EVENT_TIMEOUT_SECONDS = {"SessionStart": 120}


def _handlers():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in data["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                yield event, handler


def test_hooks_json_has_handlers():
    assert list(_handlers())


def test_every_hook_timeout_is_the_backstop_value():
    offenders = [
        (event, handler.get("command"), handler.get("timeout"))
        for event, handler in _handlers()
        if type(handler.get("timeout")) is not int
        or handler["timeout"] != EVENT_TIMEOUT_SECONDS.get(event, HOOK_TIMEOUT_SECONDS)
    ]
    assert offenders == []


def test_no_hook_runs_async():
    # async: true の command hook には timeout が適用されない（歯止めが消える）
    offenders = [(event, handler.get("command")) for event, handler in _handlers() if handler.get("async")]
    assert offenders == []
