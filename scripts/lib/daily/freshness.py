"""daily runner が書く派生 JSON（icebox-status.json / evolve-queue.json）の generated_at
鮮度判定を単一ソース化する（#351 P0）。

icebox_notice.build_icebox_notice / queue_notice.build_queue_notice が同じ穴
（generated_at を検証せず業務値の判定を先に行う）を独立に持っていた
（コピー慣習の partial fix、issue #351）ため、判定順序と分類ロジックを本モジュールに集約する。

呼び出し側が必ず守る評価順序:
  (a) generated_at の存在・パース可否・未来日時・tz 有無を classify_freshness() で検証する
  (b) FRESH でなければ業務値を一切解釈せず health_notice() を返す
  (c) FRESH のときだけ業務値（count / oldest_days / queue 内容）を評価する

ISO8601 は辞書順比較しない（Z 終端と +00:00 終端は同一 instant でも文字列としては
不一致になる既知 pitfall）。必ず datetime にパースしてから比較する。
"""
from datetime import datetime, timezone
from enum import Enum


class Freshness(Enum):
    """generated_at の鮮度分類。"""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


def parse_generated_at(value) -> "datetime | None":
    """ISO8601（末尾 Z 許容）文字列を datetime にパースする。

    tz 情報の無い文字列（naive）は補完せずそのまま返す — classify_freshness 側が
    UNKNOWN に倒す判断材料として tzinfo の有無を保持する。パース不能・非文字列・
    空文字は None。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_freshness(
    generated_at,
    now: "datetime | None" = None,
    stale_days: int = 2,
) -> "tuple[Freshness, int | None]":
    """generated_at を (Freshness, age_days) に分類する。

    age_days は FRESH / STALE のときだけ整数（経過日数、0 以上）。UNKNOWN のときは
    「経過日数を語れない」状態そのものを表すため常に None（``oldest_days=0`` のような
    正当な業務値の 0 と絶対に混同しない）。

    UNKNOWN になる条件: generated_at が欠落・非文字列・空文字 / ISO8601 パース不能 /
    tz 情報なし（naive datetime）/ now より未来。
    """
    now = now or datetime.now(timezone.utc)
    dt = parse_generated_at(generated_at)
    if dt is None or dt.tzinfo is None:
        return Freshness.UNKNOWN, None
    if dt > now:
        return Freshness.UNKNOWN, None
    age_days = (now - dt).days
    if age_days >= stale_days:
        return Freshness.STALE, age_days
    return Freshness.FRESH, age_days


def health_notice(
    *,
    label: str,
    freshness: Freshness,
    age_days: "int | None",
    remediation: str,
) -> str:
    """FRESH でないときの fail-safe 通知メッセージを組み立てる。

    旧値を現在値らしく併記しない — 業務値（count / queue 内容等）には一切触れず、
    「更新が止まっている」または「判定不能」であること自体だけを伝える。
    STALE なら経過日数を明示し、UNKNOWN なら日数を語らない（age_days=None を捏造しない）。
    """
    if freshness == Freshness.STALE:
        return (
            f"⚠ {label}は{age_days}日前から更新されていません。"
            f"現在値は不明です。修復: {remediation}"
        )
    return (
        f"⚠ {label}の生成時刻を判定できません（欠落・不正な形式・未来日時のいずれか）。"
        f"現在値は不明です。修復: {remediation}"
    )
