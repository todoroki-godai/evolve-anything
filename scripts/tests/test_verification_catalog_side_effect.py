"""detect_side_effect_verification と side-effect 向け content-aware install チェックのテスト。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper は conftest.py を参照。
"""
import pytest

from conftest import _DB_CODE, _create_side_effect_files

from lib.verification_catalog import (
    SIDE_EFFECT_MIN_PATTERNS,
    check_verification_installed,
    detect_side_effect_verification,
)


class TestDetectSideEffectVerification:
    def test_db_above_threshold(self, tmp_path):
        _create_side_effect_files(tmp_path, "db", SIDE_EFFECT_MIN_PATTERNS + 1)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is True
        assert "db" in result["detected_categories"]
        assert 0.0 < result["confidence"] <= 0.7

    def test_mq_above_threshold(self, tmp_path):
        _create_side_effect_files(tmp_path, "mq", SIDE_EFFECT_MIN_PATTERNS + 1)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is True
        assert "mq" in result["detected_categories"]

    def test_api_above_threshold(self, tmp_path):
        _create_side_effect_files(tmp_path, "api", SIDE_EFFECT_MIN_PATTERNS + 1)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is True
        assert "api" in result["detected_categories"]

    def test_below_threshold(self, tmp_path):
        _create_side_effect_files(tmp_path, "db", SIDE_EFFECT_MIN_PATTERNS - 1)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is False

    def test_test_files_excluded(self, tmp_path):
        """テストファイルのみに副作用パターンがある場合は検出しない。"""
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        for i in range(5):
            (src / f"test_handler_{i}.py").write_text(_DB_CODE)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is False

    def test_test_dir_excluded(self, tmp_path):
        """__tests__/ 配下は除外される。"""
        tests_dir = tmp_path / "src" / "__tests__"
        tests_dir.mkdir(parents=True)
        for i in range(5):
            (tests_dir / f"handler_{i}.py").write_text(_DB_CODE)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is False

    def test_empty_project(self, tmp_path):
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is False

    def test_nonexistent_dir(self, tmp_path):
        result = detect_side_effect_verification(tmp_path / "nonexistent")
        assert result["applicable"] is False

    def test_confidence_capped_at_07(self, tmp_path):
        _create_side_effect_files(tmp_path, "db", 10)
        result = detect_side_effect_verification(tmp_path)
        assert result["confidence"] <= 0.7

    def test_evidence_is_plain_path_list(self, tmp_path):
        """evidence はプレーンなファイルパスリスト（カテゴリプレフィクスなし）。"""
        _create_side_effect_files(tmp_path, "db", SIDE_EFFECT_MIN_PATTERNS + 1)
        result = detect_side_effect_verification(tmp_path)
        for path in result["evidence"]:
            assert ":" not in path or path.count(":") == 0 or "/" in path.split(":")[0]
            # プレーンパスであること — "db: path" 形式でないこと
            assert not path.startswith("db:") and not path.startswith("mq:") and not path.startswith("api:")

    def test_detected_categories_field(self, tmp_path):
        """detected_categories が別フィールドとして存在する。"""
        _create_side_effect_files(tmp_path, "db", 2)
        _create_side_effect_files(tmp_path, "api", 2)
        result = detect_side_effect_verification(tmp_path)
        assert "detected_categories" in result
        cats = result["detected_categories"]
        assert isinstance(cats, list)
        if result["applicable"]:
            assert "db" in cats
            assert "api" in cats

    def test_llm_escalation_prompt(self, tmp_path):
        _create_side_effect_files(tmp_path, "db", SIDE_EFFECT_MIN_PATTERNS + 1)
        result = detect_side_effect_verification(tmp_path)
        assert "llm_escalation_prompt" in result
        assert "副作用" in result["llm_escalation_prompt"]

    def test_multiple_categories(self, tmp_path):
        """複数カテゴリが同時に検出される。"""
        _create_side_effect_files(tmp_path, "db", 2)
        _create_side_effect_files(tmp_path, "mq", 2)
        result = detect_side_effect_verification(tmp_path)
        assert result["applicable"] is True
        assert "db" in result["detected_categories"]
        assert "mq" in result["detected_categories"]


class TestCheckVerificationInstalledContentAware:
    def test_filename_match(self, tmp_path):
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify-side-effects.md").write_text("# rule")
        entry = {"id": "side-effect-verification", "rule_filename": "verify-side-effects.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_ja(self, tmp_path):
        """verify-side-effects.md は無いが、verification.md に「副作用」があるケース。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verification.md").write_text("# 検証ルール\n副作用も確認する。")
        entry = {"id": "side-effect-verification", "rule_filename": "verify-side-effects.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_en(self, tmp_path):
        """'side effect' キーワードでマッチ。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "testing.md").write_text("Check for side effect in tests.")
        entry = {"id": "side-effect-verification", "rule_filename": "verify-side-effects.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_no_match(self, tmp_path):
        """ファイルもキーワードもない。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "other.md").write_text("# Some other rule")
        entry = {"id": "side-effect-verification", "rule_filename": "verify-side-effects.md"}
        assert check_verification_installed(entry, tmp_path) is False

    def test_non_side_effect_entry_skips_content_check(self, tmp_path):
        """data-contract-verification は content-aware チェックをしない。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "other.md").write_text("副作用チェック")
        entry = {"id": "data-contract-verification", "rule_filename": "verify-data-contract.md"}
        assert check_verification_installed(entry, tmp_path) is False
