"""detect_data_contract_verification と detect_verification_needs (全体オーケストレーション) のテスト。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper は conftest.py を参照。
"""
import pytest

from conftest import _create_py_files, _create_ts_files

from lib.verification_catalog import (
    DATA_CONTRACT_MIN_PATTERNS,
    detect_data_contract_verification,
    detect_verification_needs,
)


class TestDetectDataContractVerification:
    def test_above_threshold(self, tmp_path):
        _create_py_files(tmp_path, DATA_CONTRACT_MIN_PATTERNS + 1)
        result = detect_data_contract_verification(tmp_path)
        assert result["applicable"] is True
        assert len(result["evidence"]) >= DATA_CONTRACT_MIN_PATTERNS
        assert 0.0 < result["confidence"] <= 0.7

    def test_below_threshold(self, tmp_path):
        _create_py_files(tmp_path, DATA_CONTRACT_MIN_PATTERNS - 1)
        result = detect_data_contract_verification(tmp_path)
        assert result["applicable"] is False
        assert result["confidence"] == 0.0

    def test_no_pattern_files(self, tmp_path):
        _create_py_files(tmp_path, 5, cross_module=False)
        result = detect_data_contract_verification(tmp_path)
        assert result["applicable"] is False

    def test_nonexistent_dir(self, tmp_path):
        result = detect_data_contract_verification(tmp_path / "nonexistent")
        assert result["applicable"] is False
        assert result["confidence"] == 0.0

    def test_typescript_detection(self, tmp_path):
        _create_ts_files(tmp_path, DATA_CONTRACT_MIN_PATTERNS + 1)
        result = detect_data_contract_verification(tmp_path)
        assert result["applicable"] is True

    def test_evidence_max_10(self, tmp_path):
        _create_py_files(tmp_path, 15)
        result = detect_data_contract_verification(tmp_path)
        assert len(result["evidence"]) <= 10

    def test_llm_escalation_prompt_present(self, tmp_path):
        _create_py_files(tmp_path, DATA_CONTRACT_MIN_PATTERNS + 1)
        result = detect_data_contract_verification(tmp_path)
        assert "llm_escalation_prompt" in result

    def test_confidence_capped_at_07(self, tmp_path):
        _create_py_files(tmp_path, 10)
        result = detect_data_contract_verification(tmp_path)
        assert result["confidence"] <= 0.7


class TestDetectVerificationNeeds:
    def test_skips_installed(self, tmp_path):
        # ルールを設置 → スキップされる
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify-data-contract.md").write_text("# rule")
        _create_py_files(tmp_path, 5)

        needs = detect_verification_needs(tmp_path)
        assert len(needs) == 0

    def test_conditional_detected(self, tmp_path):
        _create_py_files(tmp_path, DATA_CONTRACT_MIN_PATTERNS + 1)
        needs = detect_verification_needs(tmp_path)
        assert len(needs) == 1
        assert needs[0]["id"] == "data-contract-verification"
        assert "detection_result" in needs[0]

    def test_conditional_not_detected(self, tmp_path):
        _create_py_files(tmp_path, 1)
        needs = detect_verification_needs(tmp_path)
        assert len(needs) == 0

    def test_nonexistent_dir(self, tmp_path):
        needs = detect_verification_needs(tmp_path / "nonexistent")
        assert needs == []
