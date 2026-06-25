"""evolve-queue.json reader + SessionStart 通知メッセージ生成（#80 Phase 1b）。

毎朝の `fleet queue --json` が `$CLAUDE_PLUGIN_DATA/evolve-queue.json` に保存した待ち PJ を
SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。

すべて read 専用・純関数・決定論（LLM 非依存）。`evolve-queue.json` は派生物（SoR でない）ため
store_registry には登録しない。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

QUEUE_FILE_NAME = "evolve-queue.json"

# generated_at がこの日数より古ければ stale advisory を付ける。
DEFAULT_STALE_DAYS = 2


def read_queue(data_dir) -> "dict | None":
    """data_dir/evolve-queue.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(data_dir) / QUEUE_FILE_NAME
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _parse_iso(ts: str) -> "datetime | None":
    """ISO8601（末尾 Z 許容）を aware datetime にパースする。失敗時 None。"""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _stale_days(generated_at: str, now: datetime) -> "int | None":
    """generated_at が now から何日前か。パース不能なら None。"""
    gen = _parse_iso(generated_at)
    if gen is None:
        return None
    delta = now - gen
    return delta.days


def build_queue_notice(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "str | None":
    """待ち PJ 一覧の通知メッセージを生成する。待ちが無ければ None（沈黙）。

    - queue_data が None / queue 空 → None
    - 待ち PJ あり → 「evolve 待ち: <pj…>（N 件）」
    - generated_at が stale_days より古い → advisory に「queue が N 日前」を付す
      （パース不能な generated_at は stale 判定不能として advisory を付けない＝沈黙）
    """
    if not isinstance(queue_data, dict):
        return None
    queue = queue_data.get("queue") or []
    if not queue:
        return None

    now = now or datetime.now(timezone.utc)
    slugs = [item.get("pj_slug", "?") for item in queue if isinstance(item, dict)]
    count = len(slugs)
    joined = ", ".join(slugs)

    msg = f"[evolve-anything] evolve 待ち: {joined}（{count} 件）。対話セッションで `/evolve-anything:evolve` を回してください。"

    age = _stale_days(queue_data.get("generated_at", ""), now)
    if age is not None and age >= stale_days:
        msg += f" ⚠ queue が {age} 日前に生成されています（`bin/evolve-daily-install` で日次更新が回っているか確認してください）。"

    return msg


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
