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
    stale_hours: "int | None" = None,
) -> "tuple[Freshness, int | None]":
    """generated_at を (Freshness, age_days) に分類する。

    age_days は FRESH / STALE のときだけ整数（経過日数、0 以上）。UNKNOWN のときは
    「経過日数を語れない」状態そのものを表すため常に None（``oldest_days=0`` のような
    正当な業務値の 0 と絶対に混同しない）。戻り値の型は ``stale_hours`` 指定時も互換の
    ため ``age_days`` のまま変えない（時間単位が要る呼び出し側は ``age_in_hours`` を
    別途使う）。

    UNKNOWN になる条件: generated_at が欠落・非文字列・空文字 / ISO8601 パース不能 /
    tz 情報なし（naive datetime）/ now より未来。

    ``stale_hours`` が指定された場合は ``stale_days`` を無視し、経過時間（時間単位）で
    STALE 判定する（#466: 日単位だと最大 72 時間気づけないため、当日中に気づける粒度に
    緩和する用途）。``stale_hours=None``（既定）のときは従来どおり日単位で判定する。
    """
    now = now or datetime.now(timezone.utc)
    dt = parse_generated_at(generated_at)
    if dt is None or dt.tzinfo is None:
        return Freshness.UNKNOWN, None
    if dt > now:
        return Freshness.UNKNOWN, None
    age_days = (now - dt).days
    if stale_hours is not None:
        elapsed_hours = (now - dt).total_seconds() / 3600
        if elapsed_hours >= stale_hours:
            return Freshness.STALE, age_days
        return Freshness.FRESH, age_days
    if age_days >= stale_days:
        return Freshness.STALE, age_days
    return Freshness.FRESH, age_days


def age_in_hours(generated_at, now: "datetime | None" = None) -> "int | None":
    """generated_at から now までの経過時間を時間単位（整数、切り捨て）で返す。

    ``classify_freshness`` の UNKNOWN 条件（パース不能・非文字列・空文字・naive
    datetime・未来日時）と同じ場合は None を返す。表示側（``health_notice``）が
    「N時間前」と「N日前」を切り替えるための補助関数（#466）。
    """
    now = now or datetime.now(timezone.utc)
    dt = parse_generated_at(generated_at)
    if dt is None or dt.tzinfo is None:
        return None
    if dt > now:
        return None
    return int((now - dt).total_seconds() // 3600)


def health_notice(
    *,
    label: str,
    freshness: Freshness,
    age_days: "int | None",
    remediation: str,
    age_hours: "int | None" = None,
) -> str:
    """FRESH でないときの fail-safe 通知メッセージを組み立てる。

    旧値を現在値らしく併記しない — 業務値（count / queue 内容等）には一切触れず、
    「更新が止まっている」または「判定不能」であること自体だけを伝える。
    STALE なら経過日数を明示し、UNKNOWN なら日数を語らない（age_days=None を捏造しない）。

    ``age_hours`` が渡され、かつ 48 時間未満のときは「N時間前」と表示する（#466: 「1日前」
    は 25 時間でも 47 時間でも同じ文字列になり緊急度が伝わらないため）。48 時間以上、または
    ``age_hours`` 省略時は従来どおり日数表示。
    """
    if freshness == Freshness.STALE:
        if age_hours is not None and age_hours < 48:
            return (
                f"⚠ {label}は{age_hours}時間前から更新されていません。"
                f"現在値は不明です。修復: {remediation}"
            )
        return (
            f"⚠ {label}は{age_days}日前から更新されていません。"
            f"現在値は不明です。修復: {remediation}"
        )
    return (
        f"⚠ {label}の生成時刻を判定できません（欠落・不正な形式・未来日時のいずれか）。"
        f"現在値は不明です。修復: {remediation}"
    )
