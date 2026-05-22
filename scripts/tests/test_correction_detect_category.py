"""_classify_error_category のユニットテスト (#194)。"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))

import correction_detect


class TestClassifyErrorCategory:
    def test_guardrail_type(self):
        # dont-unless-asked は type=guardrail → "guardrail"
        result = correction_detect._classify_error_category("dont-unless-asked")
        assert result == "guardrail"

    def test_guardrail_type_minimal_changes(self):
        # minimal-changes も guardrail
        result = correction_detect._classify_error_category("minimal-changes")
        assert result == "guardrail"

    def test_correction_type_iya(self):
        # iya は type=correction → "behavioral"
        result = correction_detect._classify_error_category("iya")
        assert result == "behavioral"

    def test_correction_type_no(self):
        # no は type=correction → "behavioral"
        result = correction_detect._classify_error_category("no")
        assert result == "behavioral"

    def test_correction_type_stop(self):
        # stop は type=correction → "behavioral"
        result = correction_detect._classify_error_category("stop")
        assert result == "behavioral"

    def test_positive_type_returns_none(self):
        # perfect は type=positive → None（フィールド省略）
        result = correction_detect._classify_error_category("perfect")
        assert result is None

    def test_positive_type_great_approach(self):
        # great-approach も positive → None
        result = correction_detect._classify_error_category("great-approach")
        assert result is None

    def test_explicit_type(self):
        # remember は type=explicit → "explicit"
        result = correction_detect._classify_error_category("remember")
        assert result == "explicit"

    def test_unknown_key(self):
        # 存在しないキー → "unknown"
        result = correction_detect._classify_error_category("nonexistent-pattern")
        assert result == "unknown"

    def test_empty_string_key(self):
        # 空文字列も存在しないキー → "unknown"
        result = correction_detect._classify_error_category("")
        assert result == "unknown"
