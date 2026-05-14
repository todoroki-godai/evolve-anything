"""detect_iac_project / detect_cross_layer_consistency / cross-layer 連動の
verification needs テスト。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper は conftest.py を参照。
"""
import pytest

from conftest import (
    _ENV_VAR_PY_CODE,
    _create_aws_sdk_files,
    _create_env_var_files,
    _create_iac_project,
)

from lib.verification_catalog import (
    MIN_CROSS_LAYER_PATTERNS,
    detect_cross_layer_consistency,
    detect_iac_project,
    detect_verification_needs,
)


class TestDetectIacProject:
    def test_cdk_project(self, tmp_path):
        (tmp_path / "cdk.json").write_text("{}")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "cdk"
        assert result["marker_path"] == "cdk.json"

    def test_serverless_yml(self, tmp_path):
        (tmp_path / "serverless.yml").write_text("service: my-app")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "serverless"

    def test_serverless_yaml(self, tmp_path):
        (tmp_path / "serverless.yaml").write_text("service: my-app")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "serverless"

    def test_sam_project(self, tmp_path):
        (tmp_path / "template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\nTransform: AWS::Serverless")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "sam"

    def test_cloudformation_template_json(self, tmp_path):
        (tmp_path / "stack.template.json").write_text('{"AWSTemplateFormatVersion": "2010-09-09"}')
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "cloudformation"

    def test_cloudformation_template_yaml(self, tmp_path):
        (tmp_path / "stack.template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is True
        assert result["iac_type"] == "cloudformation"

    def test_no_iac_markers(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        result = detect_iac_project(tmp_path)
        assert result["is_iac"] is False
        assert result["iac_type"] is None
        assert result["marker_path"] is None

    def test_nonexistent_dir(self, tmp_path):
        result = detect_iac_project(tmp_path / "nonexistent")
        assert result["is_iac"] is False

    def test_multiple_markers_cdk_wins(self, tmp_path):
        """複数 AWS マーカー一致時: CDK > SAM > Serverless > CloudFormation 優先度順"""
        (tmp_path / "cdk.json").write_text("{}")
        (tmp_path / "serverless.yml").write_text("service: my-app")
        result = detect_iac_project(tmp_path)
        assert result["iac_type"] == "cdk"

    def test_multiple_markers_sam_over_serverless(self, tmp_path):
        """SAM と Serverless が両方存在する場合は SAM 優先"""
        (tmp_path / "template.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\nTransform: AWS::Serverless")
        (tmp_path / "serverless.yml").write_text("service: my-app")
        result = detect_iac_project(tmp_path)
        assert result["iac_type"] == "sam"


class TestDetectCrossLayerConsistency:
    def test_env_var_detection_above_threshold(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is True
        assert len(result["evidence"]) >= MIN_CROSS_LAYER_PATTERNS
        assert "env_var" in result["detected_categories"]

    def test_aws_service_detection(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_aws_sdk_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is True
        assert "aws_service" in result["detected_categories"]

    def test_test_files_excluded(self, tmp_path):
        _create_iac_project(tmp_path)
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        for i in range(5):
            (src / f"test_handler_{i}.py").write_text(_ENV_VAR_PY_CODE)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is False

    def test_below_threshold(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS - 1)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is False
        assert result["confidence"] == 0.0

    def test_non_iac_project_skips_scan(self, tmp_path):
        """IaC マーカーがないプロジェクトではスキャンせず False を返す。"""
        _create_env_var_files(tmp_path, 10)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is False

    def test_detected_categories_both(self, tmp_path):
        """env_var と aws_service 両方検出時、detected_categories に両方含まれる。"""
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, 2)
        _create_aws_sdk_files(tmp_path, 2)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is True
        cats = result["detected_categories"]
        assert "env_var" in cats
        assert "aws_service" in cats

    def test_detected_categories_single(self, tmp_path):
        """env_var のみ検出時、detected_categories は ["env_var"] のみ。"""
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["detected_categories"] == ["env_var"]

    def test_error_handling_nonexistent_dir(self, tmp_path):
        result = detect_cross_layer_consistency(tmp_path / "nonexistent")
        assert result["applicable"] is False
        assert result["evidence"] == []
        assert result["confidence"] == 0.0

    def test_confidence_calculation(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, 5)
        result = detect_cross_layer_consistency(tmp_path)
        assert result["confidence"] <= 0.7
        assert result["confidence"] > 0.0

    def test_llm_escalation_prompt(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        result = detect_cross_layer_consistency(tmp_path)
        assert "llm_escalation_prompt" in result
        assert "IaC" in result["llm_escalation_prompt"]

    def test_typescript_env_var(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1, lang="ts")
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is True
        assert "env_var" in result["detected_categories"]

    def test_typescript_aws_sdk_v3(self, tmp_path):
        _create_iac_project(tmp_path)
        _create_aws_sdk_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1, lang="ts")
        result = detect_cross_layer_consistency(tmp_path)
        assert result["applicable"] is True
        assert "aws_service" in result["detected_categories"]

    def test_evidence_is_plain_path(self, tmp_path):
        """evidence はプレーンパスリスト。"""
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        result = detect_cross_layer_consistency(tmp_path)
        for path in result["evidence"]:
            assert not path.startswith("env_var:") and not path.startswith("aws_service:")


class TestCrossLayerVerificationNeeds:
    def test_iac_project_detected(self, tmp_path):
        """IaC プロジェクトで cross-layer が検出される。"""
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "cross-layer-consistency" in ids

    def test_non_iac_project_not_detected(self, tmp_path):
        """非 IaC プロジェクトでは cross-layer が検出されない。"""
        _create_env_var_files(tmp_path, 10)
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "cross-layer-consistency" not in ids

    def test_installed_skipped(self, tmp_path):
        """ルールインストール済みならスキップ。"""
        _create_iac_project(tmp_path)
        _create_env_var_files(tmp_path, MIN_CROSS_LAYER_PATTERNS + 1)
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify-cross-layer.md").write_text("# rule")
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "cross-layer-consistency" not in ids
