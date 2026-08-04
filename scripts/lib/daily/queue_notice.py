"""evolve-queue.json reader + SessionStart 通知メッセージ生成（#80 Phase 1b）。

毎朝の `fleet queue --json` が `$CLAUDE_PLUGIN_DATA/evolve-queue.json` に保存した待ち PJ を
SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。

すべて read 専用・純関数・決定論（LLM 非依存）。`evolve-queue.json` は派生物（SoR でない）ため
store_registry には登録しない。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import freshness as _freshness

QUEUE_FILE_NAME = "evolve-queue.json"

# generated_at がこの日数以上古ければ stale（#351 freshness gate の共通閾値として使う）。
DEFAULT_STALE_DAYS = 2

REMEDIATION_HINT = "bin/evolve-daily-install で日次更新が回っているか確認してください"


def read_queue(data_dir) -> "dict | None":
    """data_dir/evolve-queue.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(data_dir) / QUEUE_FILE_NAME
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def build_queue_notice(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "str | None":
    """待ち PJ 一覧の通知メッセージを生成する。

    評価順序（#351 freshness gate）: (a) generated_at の鮮度を先に判定する →
    (b) FRESH でなければ待ち PJ 一覧を一切解釈せず health notice を返す（queue が
    空でも stale なら通知する＝producer 停止の見逃しを防ぐ回帰対応）→
    (c) FRESH のときだけ待ち PJ 一覧を評価し、空なら沈黙する。

    - queue_data が None → None（daily runner 未実行 = 壊れているのでなく単に未セットアップ）
    - FRESH かつ待ち PJ あり → 「evolve 待ち: <pj…>（N 件）」
    - FRESH かつ待ち PJ 無し → None（沈黙）
    - STALE / UNKNOWN → 待ち PJ の中身に関わらず health notice（旧値は併記しない）
    """
    if not isinstance(queue_data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    state, age_days = _freshness.classify_freshness(
        queue_data.get("generated_at"), now=now, stale_days=stale_days
    )
    if state != _freshness.Freshness.FRESH:
        return _freshness.health_notice(
            label="evolve queue",
            freshness=state,
            age_days=age_days,
            remediation=REMEDIATION_HINT,
        )

    queue = queue_data.get("queue") or []
    if not queue:
        return None

    slugs = [item.get("pj_slug", "?") for item in queue if isinstance(item, dict)]
    count = len(slugs)
    joined = ", ".join(slugs)

    return f"[evolve-anything] evolve 待ち: {joined}（{count} 件）。対話セッションで `/evolve-anything:evolve` を回してください。"


def queue_notice_output(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。待ちが無ければ None。"""
    msg = build_queue_notice(queue_data, now=now, stale_days=stale_days)
    if msg is None:
        return None
    return {"systemMessage": msg}
