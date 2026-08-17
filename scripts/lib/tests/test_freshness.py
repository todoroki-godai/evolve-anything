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


# ===== stale_hours（#466: 日単位だと最大72時間気づけないため時間単位を追加） =====
def test_stale_hours_none_preserves_days_behavior():
    """stale_hours=None（既定）なら従来どおり stale_days で判定する（後方互換）。"""
    generated_at = (NOW - timedelta(days=1, hours=1)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_days=1)
    assert result == fr.Freshness.STALE
    assert age_days == 1


def test_stale_hours_overrides_stale_days_when_given():
    """stale_hours が指定されたら stale_days は無視される。"""
    generated_at = (NOW - timedelta(hours=40)).isoformat()
    # stale_days=100 (満たさない) でも stale_hours=30 が優先されて STALE になる
    result, _age = fr.classify_freshness(
        generated_at, now=NOW, stale_days=100, stale_hours=30
    )
    assert result == fr.Freshness.STALE


def test_stale_hours_boundary_just_under_is_fresh():
    """29 時間経過（30 時間閾値未満）は FRESH のまま。"""
    generated_at = (NOW - timedelta(hours=29)).isoformat()
    result, _age = fr.classify_freshness(generated_at, now=NOW, stale_hours=30)
    assert result == fr.Freshness.FRESH


def test_stale_hours_boundary_exactly_at_is_stale():
    """30 時間ちょうどで STALE（>= 判定）。"""
    generated_at = (NOW - timedelta(hours=30)).isoformat()
    result, _age = fr.classify_freshness(generated_at, now=NOW, stale_hours=30)
    assert result == fr.Freshness.STALE


def test_stale_hours_normal_overnight_gap_is_fresh():
    """09:00 実行 → 翌朝 08:00 のセッションは 23 時間経過。正常な沈黙で FRESH のまま。"""
    generated_at = (NOW - timedelta(hours=23)).isoformat()
    result, _age = fr.classify_freshness(generated_at, now=NOW, stale_hours=30)
    assert result == fr.Freshness.FRESH


def test_stale_hours_age_days_field_unchanged_type():
    """stale_hours 指定時も戻り値2要素目は age_days（互換のため型を変えない）。"""
    generated_at = (NOW - timedelta(hours=40)).isoformat()
    result, age_days = fr.classify_freshness(generated_at, now=NOW, stale_hours=30)
    assert result == fr.Freshness.STALE
    assert age_days == 1  # 40時間 = 1日と16時間 → .days は1


# ===== age_in_hours =====
def test_age_in_hours_computes_elapsed_hours():
    generated_at = (NOW - timedelta(hours=47)).isoformat()
    assert fr.age_in_hours(generated_at, now=NOW) == 47


def test_age_in_hours_truncates_partial_hour():
    generated_at = (NOW - timedelta(hours=47, minutes=59)).isoformat()
    assert fr.age_in_hours(generated_at, now=NOW) == 47


def test_age_in_hours_unparseable_is_none():
    assert fr.age_in_hours("not-a-timestamp", now=NOW) is None


def test_age_in_hours_naive_datetime_is_none():
    assert fr.age_in_hours("2026-08-04T08:00:00", now=NOW) is None


def test_age_in_hours_future_is_none():
    generated_at = (NOW + timedelta(hours=1)).isoformat()
    assert fr.age_in_hours(generated_at, now=NOW) is None


# ===== format_elapsed: 時間／日数表示の切替（#466・#490 で health_notice から分離） =====
# #490 codex [Should]: 旧 health_notice(age_hours=...) は production から到達不能な
# dead code だったため削除し、切替ロジックを format_elapsed に単一ソース化した。
def test_format_elapsed_uses_hours_when_under_48():
    """47 時間なら「N時間前」表示（日数だと「1日前」に潰れて緊急度が消える）。"""
    assert fr.format_elapsed(47, 1) == "47時間前"


def test_format_elapsed_exactly_48_hours_uses_days():
    """48 時間ちょうどは日数表示（実装は `< 48`・#490 codex [Should] で境界を明示）。"""
    assert fr.format_elapsed(48, 2) == "2日前"


def test_format_elapsed_uses_days_when_49_hours_or_more():
    """49 時間（48時間以上）なら日数表示に戻る。"""
    assert fr.format_elapsed(49, 2) == "2日前"


def test_format_elapsed_without_hours_falls_back_to_days():
    """age_hours が None なら日数表示。"""
    assert fr.format_elapsed(None, 5) == "5日前"


def test_format_elapsed_unknown_when_both_none():
    """どちらも判定できないときは 0 や 1 を捏造せず「不明」を返す。"""
    assert fr.format_elapsed(None, None) == "不明"


def test_health_notice_no_longer_accepts_age_hours():
    """到達不能だった age_hours 引数は削除済み（再導入を検知する契約テスト）。"""
    import inspect

    assert "age_hours" not in inspect.signature(fr.health_notice).parameters


def test_health_notice_falls_back_to_days():
    msg = fr.health_notice(
        label="毎朝の自動記録",
        freshness=fr.Freshness.STALE,
        age_days=5,
        remediation="bin/evolve-daily-run",
    )
    assert "5日前" in msg
    assert "時間前" not in msg


# ===== 既定閾値の意図（icebox は週末許容 / queue は #466 で意図的に非許容へ） =====
FRIDAY_MORNING = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
MONDAY_MORNING = datetime(2026, 8, 3, 8, 0, 0, tzinfo=timezone.utc)


def test_icebox_default_still_tolerates_a_closed_weekend():
    """icebox は従来どおり金曜朝→月曜朝を FRESH に保つ（通知本体が消えない）。

    #351 以前は stale 判定が「本来の通知に一文添える」だけだったので 2 日で足りたが、
    gate 化で通知本体（icebox 件数）が差し替わるようになり誤検知コストが上がった。
    PC を週末閉じて launchd が走らないだけで本体が消えるのを防ぐ既定値を固定する。
    """
    from daily import icebox_notice as ic

    state, _ = fr.classify_freshness(
        FRIDAY_MORNING.isoformat(), now=MONDAY_MORNING, stale_days=ic.STALE_STATUS_DAYS
    )
    assert state == fr.Freshness.FRESH


def test_queue_default_intentionally_fires_over_a_closed_weekend():
    """queue は #466 で週末許容をやめた（意図的なトレードオフの固定）。

    停止に「その日のうち」に気づくことを優先し、30 時間で発火する。週末に PC を閉じれば
    月曜朝に警告が出るが、queue 側は通知本体が「待ち PJ 一覧」であり、消えても失うのは
    一覧表示だけ。対して見逃したときに失うのは週の締切であり、非対称なので発火側に倒す。
    """
    from daily import queue_notice as qn

    state, _ = fr.classify_freshness(
        FRIDAY_MORNING.isoformat(),
        now=MONDAY_MORNING,
        stale_hours=qn.DEFAULT_STALE_HOURS,
    )
    assert state == fr.Freshness.STALE


def test_permanent_outage_still_detected():
    """#351 の恒久障害（16 日沈黙）は依然として STALE として捕まる。"""
    from daily import queue_notice as qn

    state, age = fr.classify_freshness(
        (MONDAY_MORNING - timedelta(days=16)).isoformat(),
        now=MONDAY_MORNING,
        stale_hours=qn.DEFAULT_STALE_HOURS,
    )
    assert state == fr.Freshness.STALE
    assert age == 16
