"""icebox-status.json reader + SessionStart 通知メッセージ生成（#194）。

毎朝の `gh issue list --label icebox --state closed` が `$CLAUDE_PLUGIN_DATA/icebox-status.json`
に保存した凍結 issue の件数・最古経過日数（`oldest_days`）を、対話セッション開始時に
systemMessage（ADR-038 = user 向けチャネル）として surface する軽量な気づきトリガー。

icebox は evolve-anything 自身の GitHub issue backlog なので、本体リポジトリで作業している
ときだけ配信対象になるべきだが、その plugin_self 判定は呼び出し側（hooks/restore_state.py）の
責務とし、本モジュールは read 専用・純関数・決定論（LLM 非依存）に留める。

`icebox-status.json` は派生物（SoR でない）ため store_registry には登録しない。
"""
import json
from pathlib import Path

ICEBOX_FILE_NAME = "icebox-status.json"

# oldest_days がこの日数以上なら通知する既定閾値。
DEFAULT_THRESHOLD_DAYS = 90


def read_icebox_status(data_dir) -> "dict | None":
    """data_dir/icebox-status.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(data_dir) / ICEBOX_FILE_NAME
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def build_icebox_notice(
    status: "dict | None",
    now: "datetime | None" = None,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
) -> "str | None":
    """icebox 棚卸しの気づき通知メッセージを生成する。閾値未満なら None（沈黙）。

    - status が None / dict でない → None
    - `oldest_days` が欠落・非数値 → None（判定不能として沈黙、queue_notice の
      パース不能 generated_at と同じ fail-safe 方針）
    - `oldest_days` が `threshold_days` 未満 → None（沈黙）
    - `oldest_days` が `threshold_days` 以上 → 1行に集約したメッセージを返す
      （個別 issue ごとの表示は絶対にしない）

    `now` は queue_notice.build_queue_notice とのシグネチャ整合のために受け取るが、
    `oldest_days` は生成時点（bin/evolve-daily-run）で計算済みのため現時点では未使用。
    """
    if not isinstance(status, dict):
        return None
    oldest_days = status.get("oldest_days")
    count = status.get("count")
    if not isinstance(oldest_days, (int, float)) or isinstance(oldest_days, bool):
        return None
    if not isinstance(count, (int, float)) or isinstance(count, bool):
        return None
    if oldest_days < threshold_days:
        return None

    return (
        f"[evolve-anything] icebox {int(count)}件・最古{int(oldest_days)}日。"
        "`gh issue list --label icebox --state closed` で棚卸しを検討してください。"
    )


def icebox_notice_output(
    status: "dict | None",
    now: "datetime | None" = None,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。閾値未満なら None。"""
    msg = build_icebox_notice(status, now=now, threshold_days=threshold_days)
    if msg is None:
        return None
    return {"systemMessage": msg}
