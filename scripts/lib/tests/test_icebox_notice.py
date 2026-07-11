"""icebox-status.json reader + SessionStart 通知メッセージ生成のテスト（#194）。

- read_icebox_status: 正常 / ファイル無し / 壊れた JSON
- build_icebox_notice: None（沈黙） / threshold 未満（沈黙） / threshold 以上（1行集約メッセージ）
- icebox_notice_output: systemMessage dict / 沈黙

すべて決定論・LLM 非依存。
"""
import json

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
