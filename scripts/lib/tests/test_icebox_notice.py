"""icebox-status.json reader + SessionStart 通知メッセージ生成のテスト（#194, #352）。

- read_icebox_status: 正常 / ファイル無し / 壊れた JSON
- build_icebox_notice: None（沈黙） / threshold 未満（沈黙） / threshold 以上（1行集約メッセージ）
- icebox_notice_output: systemMessage dict / 沈黙
- read_icebox_verdicts / unseen_met_verdicts / build_met_notice（#352 レーン1「成立」通知）

すべて決定論・LLM 非依存。
"""
import json
from datetime import datetime, timedelta, timezone

from daily import icebox_notice as ibn


def test_read_icebox_status_returns_parsed_dict(tmp_path):
    payload = {"count": 5, "oldest_days": 120, "generated_at": "2026-07-11T09:00:00Z"}
    (tmp_path / "icebox-status.json").write_text(json.dumps(payload), encoding="utf-8")
    assert ibn.read_icebox_status(tmp_path) == payload


def test_read_icebox_status_missing_file_returns_none(tmp_path):
    assert ibn.read_icebox_status(tmp_path) is None


def test_read_icebox_status_corrupt_file_returns_none(tmp_path):
    (tmp_path / "icebox-status.json").write_text("{not valid json", encoding="utf-8")
    assert ibn.read_icebox_status(tmp_path) is None


def test_build_icebox_notice_none_status_is_silent():
    assert ibn.build_icebox_notice(None) is None


def test_build_icebox_notice_non_dict_status_is_silent():
    assert ibn.build_icebox_notice("not a dict") is None


def test_build_icebox_notice_below_threshold_is_silent():
    status = {"count": 3, "oldest_days": 45, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status, threshold_days=90) is None


def test_build_icebox_notice_at_threshold_fires():
    status = {"count": 12, "oldest_days": 90, "generated_at": "2026-07-11T09:00:00Z"}
    msg = ibn.build_icebox_notice(status, threshold_days=90)
    assert msg is not None
    assert "12件" in msg
    assert "90日" in msg
    assert "gh issue list --label icebox --state closed" in msg


def test_build_icebox_notice_above_threshold_fires():
    status = {"count": 12, "oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    msg = ibn.build_icebox_notice(status, threshold_days=90)
    assert msg is not None
    assert "200日" in msg


def test_build_icebox_notice_is_single_line_no_per_issue_listing():
    """個別 issue ごとの表示は絶対にしない = 1行に集約されていること。"""
    status = {"count": 12, "oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    msg = ibn.build_icebox_notice(status, threshold_days=90)
    assert "\n" not in msg


def test_build_icebox_notice_missing_oldest_days_is_silent():
    status = {"count": 12, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status) is None


def test_build_icebox_notice_missing_count_is_silent():
    status = {"oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status) is None


def test_build_icebox_notice_non_numeric_count_is_silent():
    status = {"count": "12", "oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status) is None


def test_build_icebox_notice_bool_count_is_silent():
    status = {"count": True, "oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status) is None


def test_build_icebox_notice_default_threshold_is_90():
    status = {"count": 12, "oldest_days": 89, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.build_icebox_notice(status) is None
    status["oldest_days"] = 90
    assert ibn.build_icebox_notice(status) is not None


def test_icebox_notice_output_dict():
    status = {"count": 12, "oldest_days": 200, "generated_at": "2026-07-11T09:00:00Z"}
    out = ibn.icebox_notice_output(status, threshold_days=90)
    assert out == {"systemMessage": ibn.build_icebox_notice(status, threshold_days=90)}


def test_icebox_notice_output_silent_when_below_threshold():
    status = {"count": 3, "oldest_days": 10, "generated_at": "2026-07-11T09:00:00Z"}
    assert ibn.icebox_notice_output(status, threshold_days=90) is None


def test_icebox_notice_output_silent_when_status_none():
    assert ibn.icebox_notice_output(None) is None


# ─────────────────────────────────────────────────────────────────
# #352: icebox-verdicts.json reader + レーン1「成立」通知
# ─────────────────────────────────────────────────────────────────
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _verdict(number=1, lane="met", reason="weak_signals.unprocessed_count = 10 > 5 を満たしました"):
    return {"number": number, "lane": lane, "reason": reason, "value": 10}


def test_read_icebox_verdicts_returns_parsed_dict(tmp_path):
    payload = {"generated_at": "2026-08-01T09:00:00Z", "verdicts": [_verdict()]}
    (tmp_path / "icebox-verdicts.json").write_text(json.dumps(payload), encoding="utf-8")
    assert ibn.read_icebox_verdicts(tmp_path) == payload


def test_read_icebox_verdicts_missing_file_returns_none(tmp_path):
    assert ibn.read_icebox_verdicts(tmp_path) is None


def test_read_icebox_verdicts_corrupt_file_returns_none(tmp_path):
    (tmp_path / "icebox-verdicts.json").write_text("{not valid", encoding="utf-8")
    assert ibn.read_icebox_verdicts(tmp_path) is None


class TestUnseenMetVerdicts:
    def test_filters_to_met_lane_only(self):
        payload = {
            "verdicts": [
                _verdict(number=1, lane="met"),
                _verdict(number=2, lane="observer_missing"),
                _verdict(number=3, lane="archive_candidate"),
                _verdict(number=4, lane=None),
            ]
        }
        out = ibn.unseen_met_verdicts(payload, seen_keys=set())
        assert [v["number"] for v in out] == [1]

    def test_none_payload_returns_empty(self):
        assert ibn.unseen_met_verdicts(None, seen_keys=set()) == []

    def test_excludes_already_seen(self):
        import icebox_verdict_seen as seen

        v = _verdict(number=1)
        payload = {"verdicts": [v]}
        seen_keys = {seen.verdict_key(v)}
        assert ibn.unseen_met_verdicts(payload, seen_keys=seen_keys) == []

    def test_missing_verdicts_key_returns_empty(self):
        assert ibn.unseen_met_verdicts({}, seen_keys=set()) == []


class TestBuildMetNotice:
    def test_empty_list_is_silent(self):
        assert ibn.build_met_notice([]) is None

    def test_single_issue_names_it(self):
        msg = ibn.build_met_notice([_verdict(number=42)])
        assert msg is not None
        assert "#42" in msg
        assert "weak_signals.unprocessed_count = 10 > 5" in msg

    def test_multiple_issues_all_named(self):
        msg = ibn.build_met_notice([_verdict(number=1), _verdict(number=2)])
        assert "#1" in msg
        assert "#2" in msg

    def test_stale_generated_at_appends_advisory(self):
        old_generated_at = (NOW - timedelta(days=10)).isoformat()
        msg = ibn.build_met_notice(
            [_verdict(number=1)], generated_at=old_generated_at, now=NOW
        )
        assert "日経過" in msg or "古い" in msg

    def test_fresh_generated_at_has_no_stale_advisory(self):
        fresh = (NOW - timedelta(hours=1)).isoformat()
        msg = ibn.build_met_notice([_verdict(number=1)], generated_at=fresh, now=NOW)
        assert "日経過" not in msg and "古い" not in msg

    def test_malformed_generated_at_does_not_crash(self):
        msg = ibn.build_met_notice([_verdict(number=1)], generated_at="not-a-date", now=NOW)
        assert msg is not None  # 判定不能でも本体メッセージは出す（沈黙より安全側）
