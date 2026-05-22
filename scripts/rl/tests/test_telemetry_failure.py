"""score_failure_distribution のユニットテスト (#194)。"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

_telemetry_path = _rl_dir / "fitness" / "telemetry.py"
_spec = importlib.util.spec_from_file_location("telemetry", _telemetry_path)
telemetry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(telemetry)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _write_corrections(path: Path, records: list) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class TestScoreFailureDistribution:
    def test_empty_no_file(self, tmp_path):
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result == {"total": 0, "by_category": {}, "dominant_category": None}

    def test_empty_file(self, tmp_path):
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        (data_dir / "corrections.jsonl").write_text("", encoding="utf-8")
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result == {"total": 0, "by_category": {}, "dominant_category": None}

    def test_no_error_category_field(self, tmp_path):
        # error_category フィールドなしのレコードはカウントされない
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        records = [
            {"correction_type": "iya", "timestamp": _now_iso()},
            {"correction_type": "no", "timestamp": _now_iso()},
        ]
        _write_corrections(data_dir / "corrections.jsonl", records)
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result == {"total": 0, "by_category": {}, "dominant_category": None}

    def test_mixed_categories(self, tmp_path):
        # behavioral 5件 + guardrail 3件 → dominant=behavioral
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        records = (
            [{"correction_type": "iya", "error_category": "behavioral", "timestamp": _now_iso()}] * 5
            + [{"correction_type": "dont-unless-asked", "error_category": "guardrail", "timestamp": _now_iso()}] * 3
        )
        _write_corrections(data_dir / "corrections.jsonl", records)
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result["total"] == 8
        assert result["by_category"]["behavioral"] == 5
        assert result["by_category"]["guardrail"] == 3
        assert result["dominant_category"] == "behavioral"

    def test_explicit_category(self, tmp_path):
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        records = [{"correction_type": "remember", "error_category": "explicit", "timestamp": _now_iso()}] * 2
        _write_corrections(data_dir / "corrections.jsonl", records)
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result["total"] == 2
        assert result["by_category"]["explicit"] == 2
        assert result["dominant_category"] == "explicit"

    def test_none_error_category_skipped(self, tmp_path):
        # error_category=None は positive pattern → カウントしない
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        records = [
            {"correction_type": "perfect", "error_category": None, "timestamp": _now_iso()},
            {"correction_type": "iya", "error_category": "behavioral", "timestamp": _now_iso()},
        ]
        _write_corrections(data_dir / "corrections.jsonl", records)
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result["total"] == 1
        assert result["by_category"] == {"behavioral": 1}

    def test_old_records_excluded(self, tmp_path):
        # days=7 の場合、31日前のレコードは除外される
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        old_ts = _days_ago_iso(31)
        recent_ts = _now_iso()
        records = [
            {"error_category": "behavioral", "timestamp": old_ts},
            {"error_category": "guardrail", "timestamp": recent_ts},
        ]
        _write_corrections(data_dir / "corrections.jsonl", records)
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path, days=7)
        assert result["total"] == 1
        assert result["by_category"] == {"guardrail": 1}

    def test_invalid_json_lines_skipped(self, tmp_path):
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        content = (
            '{"error_category": "behavioral", "timestamp": "' + _now_iso() + '"}\n'
            "not valid json\n"
            '{"error_category": "guardrail", "timestamp": "' + _now_iso() + '"}\n'
        )
        (data_dir / "corrections.jsonl").write_text(content, encoding="utf-8")
        with mock.patch("rl_common.DATA_DIR", data_dir):
            result = telemetry.score_failure_distribution(tmp_path)
        assert result["total"] == 2
