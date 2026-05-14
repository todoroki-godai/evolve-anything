"""VERIFICATION_CATALOG の構造 / make_verification_rule_issue / カタログエントリ確認。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper (`_PY_CROSS_MODULE`, `_create_py_files` 等) は conftest.py を参照。
"""
import pytest

from conftest import _create_py_files, _create_ts_files

from lib.verification_catalog import (
    VERIFICATION_CATALOG,
    check_verification_installed,
    get_rule_template,
)
from lib.issue_schema import (
    VERIFICATION_RULE_CANDIDATE,
    VRC_CATALOG_ID,
    VRC_DETECTION_CONFIDENCE,
    VRC_EVIDENCE,
    VRC_RULE_FILENAME,
    make_verification_rule_issue,
)


class TestCatalogStructure:
    """カタログ定義の構造テスト。"""

    REQUIRED_FIELDS = {"id", "type", "description", "rule_template", "rule_filename", "applicability"}

    def test_required_fields_present(self):
        for entry in VERIFICATION_CATALOG:
            missing = self.REQUIRED_FIELDS - set(entry.keys())
            assert not missing, f"entry {entry.get('id')} missing fields: {missing}"

    def test_rule_filename_unique(self):
        filenames = [e["rule_filename"] for e in VERIFICATION_CATALOG]
        assert len(filenames) == len(set(filenames)), "duplicate rule_filename found"

    def test_id_unique(self):
        ids = [e["id"] for e in VERIFICATION_CATALOG]
        assert len(ids) == len(set(ids)), "duplicate id found"

    def test_conditional_has_detection_fn(self):
        for entry in VERIFICATION_CATALOG:
            if entry["applicability"] == "conditional":
                assert "detection_fn" in entry, f"{entry['id']} is conditional but has no detection_fn"


class TestCheckVerificationInstalled:
    def test_installed_returns_true(self, tmp_path):
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify-data-contract.md").write_text("# rule")

        entry = {"rule_filename": "verify-data-contract.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_not_installed_returns_false(self, tmp_path):
        entry = {"rule_filename": "verify-data-contract.md"}
        assert check_verification_installed(entry, tmp_path) is False

    def test_empty_filename_returns_false(self, tmp_path):
        entry = {"rule_filename": ""}
        assert check_verification_installed(entry, tmp_path) is False


class TestGetRuleTemplate:
    def test_python_template(self, tmp_path):
        _create_py_files(tmp_path, 5)
        entry = VERIFICATION_CATALOG[0]
        template = get_rule_template(entry, tmp_path)
        assert "dictキー" in template

    def test_typescript_template(self, tmp_path):
        _create_ts_files(tmp_path, 5)
        entry = VERIFICATION_CATALOG[0]
        template = get_rule_template(entry, tmp_path)
        assert "interface/type" in template


class TestMakeVerificationRuleIssue:
    def test_basic_structure(self):
        entry = {
            "id": "data-contract-verification",
            "rule_filename": "verify-data-contract.md",
            "rule_template": "# rule content",
            "description": "desc",
        }
        detection_result = {
            "evidence": ["a.py", "b.py"],
            "confidence": 0.6,
        }
        issue = make_verification_rule_issue(entry, detection_result)
        assert issue["type"] == VERIFICATION_RULE_CANDIDATE
        assert issue["source"] == "verification_catalog"
        assert issue["file"] == "verify-data-contract.md"
        assert issue["detail"][VRC_CATALOG_ID] == "data-contract-verification"
        assert issue["detail"][VRC_EVIDENCE] == ["a.py", "b.py"]
        assert issue["detail"][VRC_DETECTION_CONFIDENCE] == 0.6

    def test_with_project_dir(self):
        entry = {
            "id": "test",
            "rule_filename": "test-rule.md",
            "rule_template": "# test",
            "description": "test desc",
        }
        issue = make_verification_rule_issue(
            entry,
            {"evidence": [], "confidence": 0.5},
            project_dir_str="/home/user/project",
        )
        assert issue["file"] == "/home/user/project/.claude/rules/test-rule.md"

    def test_missing_fields_default(self):
        issue = make_verification_rule_issue({}, {})
        assert issue["detail"][VRC_CATALOG_ID] == ""
        assert issue["detail"][VRC_RULE_FILENAME] == ""
        assert issue["detail"][VRC_EVIDENCE] == []
        assert issue["detail"][VRC_DETECTION_CONFIDENCE] == 0.0


class TestSideEffectCatalogEntry:
    def test_entry_exists(self):
        ids = [e["id"] for e in VERIFICATION_CATALOG]
        assert "side-effect-verification" in ids

    def test_rule_template_3_lines(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "side-effect-verification")
        lines = entry["rule_template"].strip().split("\n")
        assert len(lines) <= 3

    def test_language_independent_template(self, tmp_path):
        """side-effect-verification は言語非依存テンプレートを返す。"""
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "side-effect-verification")
        _create_py_files(tmp_path, 5)
        py_template = get_rule_template(entry, tmp_path)
        ts_dir = tmp_path / "ts_proj"
        ts_dir.mkdir()
        _create_ts_files(ts_dir, 5)
        ts_template = get_rule_template(entry, ts_dir)
        assert py_template == ts_template


class TestCrossLayerCatalogEntry:
    def test_entry_exists(self):
        ids = [e["id"] for e in VERIFICATION_CATALOG]
        assert "cross-layer-consistency" in ids

    def test_has_content_patterns(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "cross-layer-consistency")
        assert "content_patterns" in entry
        assert len(entry["content_patterns"]) > 0

    def test_conditional_with_detection_fn(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "cross-layer-consistency")
        assert entry["applicability"] == "conditional"
        assert entry["detection_fn"] == "detect_cross_layer_consistency"


class TestEvidenceCatalogEntry:
    def test_entry_exists(self):
        ids = [e["id"] for e in VERIFICATION_CATALOG]
        assert "evidence-before-claims" in ids

    def test_has_content_patterns(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "evidence-before-claims")
        assert "content_patterns" in entry
        assert len(entry["content_patterns"]) > 0

    def test_has_detection_fn(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "evidence-before-claims")
        assert entry["detection_fn"] == "detect_evidence_verification"
        assert entry["applicability"] == "conditional"
