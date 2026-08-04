"""daily.freshness（#351 P0: freshness gate の共通化）の単体テスト。

icebox_notice.build_icebox_notice / queue_notice.build_queue_notice が独立に持っていた
「generated_at を検証せず業務値を先に判定する」穴を単一ソースへ集約したモジュール。

- classify_freshness: FRESH / STALE / UNKNOWN の分類 + 経過日数
- health_notice: FRESH でないときの fail-safe メッセージ（旧値を併記しない）

すべて決定論・LLM 非依存。
"""
from datetime import datetime, timedelta, timezone

from daily import freshness as fr

NOW = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc)


# ===== classify_freshness =====
def test_fresh_when_within_stale_days():
    generated_at = (NOW - timedelta(hours=1)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.FRESH
    assert age_days == 0


def test_stale_when_at_or_over_stale_days():
    generated_at = (NOW - timedelta(days=16)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.STALE
    assert age_days == 16


def test_boundary_exactly_at_stale_days_is_stale():
    generated_at = (NOW - timedelta(days=2)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.STALE
    assert age_days == 2


def test_missing_generated_at_is_unknown():
    result, age_days = fr.classify_freshness(None, now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_empty_string_generated_at_is_unknown():
    result, age_days = fr.classify_freshness("", now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_non_string_generated_at_is_unknown():
    result, age_days = fr.classify_freshness(12345, now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_unparseable_generated_at_is_unknown():
    result, age_days = fr.classify_freshness("not-a-timestamp", now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_future_generated_at_is_unknown():
    generated_at = (NOW + timedelta(hours=1)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_naive_datetime_without_tz_is_unknown():
    """timezone 無しは既知 pitfall（Z 終端と +00:00 の辞書順比較崩れ）を避けるため判定不能扱い。"""
    generated_at = "2026-08-04T08:00:00"
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.UNKNOWN
    assert age_days is None


def test_z_suffix_and_offset_suffix_are_equivalent_instant():
    """Z 終端と +00:00 終端は同一 instant として扱う（辞書順比較しない）。"""
    z_form = "2026-08-04T08:00:00Z"
    offset_form = "2026-08-04T08:00:00+00:00"
    assert fr.classify_freshness(z_form, now=NOW, stale_days=2) == fr.classify_freshness(
        offset_form, now=NOW, stale_days=2
    )


def test_oldest_days_zero_is_not_confused_with_unknown():
    """FRESH のとき age_days=0 は正当な値。UNKNOWN の None と型的にも意味的にも区別される。"""
    generated_at = NOW.isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=2)
    assert result == fr.Freshness.FRESH
    assert age_days == 0
    assert age_days is not None


def test_now_defaults_to_current_time_when_omitted():
    # 呼び出し側が now を省略しても例外にならない（実運用は now 省略で呼ばれる）。
    # 未来判定に巻き込まれないよう十分に過去の generated_at を使う。
    past = "2020-01-01T00:00:00+00:00"
    result, age_days = fr.classify_freshness(past, stale_days=2)
    assert result == fr.Freshness.STALE
    assert isinstance(age_days, int)


# ===== health_notice =====
def test_health_notice_stale_includes_age_and_remediation_no_old_values():
    msg = fr.health_notice(
        label="icebox 集計",
        freshness=fr.Freshness.STALE,
        age_days=16,
        remediation="bin/evolve-daily-install",
    )
    assert "16日" in msg
    assert "現在値は不明です" in msg
    assert "bin/evolve-daily-install" in msg
    assert "\n" not in msg


def test_health_notice_unknown_has_no_day_count():
    msg = fr.health_notice(
        label="evolve queue",
        freshness=fr.Freshness.UNKNOWN,
        age_days=None,
        remediation="bin/evolve-daily-install",
    )
    assert "現在値は不明です" in msg
    assert "bin/evolve-daily-install" in msg
    # 日数を語れない状態なので数値の日数表現を含まない
    assert "日前" not in msg


# ===== 既定閾値の意図（#351 レビュー時に 2→3 日へ緩和） =====
def test_default_stale_days_tolerates_a_closed_weekend():
    """金曜朝に生成 → 月曜朝のセッションで FRESH（通知本体が消えない）。

    #351 以前は stale 判定が「本来の通知に一文添える」だけだったので 2 日で足りたが、
    gate 化で通知本体（待ち PJ 一覧 / icebox 件数）が差し替わるようになり誤検知コストが
    上がった。PC を週末閉じて launchd が走らないだけで本体が消えるのを防ぐ既定値を固定する。
    """
    from daily import icebox_notice as ic
    from daily import queue_notice as qn

    friday_morning = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
    monday_morning = datetime(2026, 8, 3, 8, 0, 0, tzinfo=timezone.utc)

    for stale_days in (qn.DEFAULT_STALE_DAYS, ic.STALE_STATUS_DAYS):
        state, _ = fr.classify_freshness(
            friday_morning.isoformat(), now=monday_morning, stale_days=stale_days
        )
        assert state == fr.Freshness.FRESH

    # 一方、#351 の恒久障害（16 日沈黙）は依然として STALE として捕まる
    state, age = fr.classify_freshness(
        (monday_morning - timedelta(days=16)).isoformat(),
        now=monday_morning,
        stale_days=qn.DEFAULT_STALE_DAYS,
    )
    assert state == fr.Freshness.STALE
    assert age == 16
