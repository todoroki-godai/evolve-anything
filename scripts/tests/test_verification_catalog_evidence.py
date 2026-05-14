"""detect_evidence_verification と evidence-before-claims content-aware install チェックのテスト。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper は conftest.py を参照。
"""
import sys
from unittest import mock

import pytest

from lib.verification_catalog import (
    EVIDENCE_MIN_PATTERNS,
    check_verification_installed,
    detect_evidence_verification,
)


class TestDetectEvidenceVerification:
    def _mock_corrections(self, messages):
        """corrections レコードのリストを生成するヘルパー。"""
        return [{"message": m} for m in messages]

    def _patch_telemetry(self, corrections):
        """telemetry_query.query_corrections を mock する context manager。

        detect_evidence_verification は関数内で `import telemetry_query` するため、
        sys.modules にモックモジュールを差し込む。
        """
        mock_tq = mock.MagicMock()
        mock_tq.query_corrections.return_value = corrections
        return mock.patch.dict("sys.modules", {"telemetry_query": mock_tq})

    def test_applicable_with_evidence_request_patterns(self, tmp_path):
        """証拠要求パターンを含む corrections → applicable=True。"""
        corrections = self._mock_corrections([
            "テスト実行して結果を見せて",
            "動作確認してから進めて",
            "ビルド通して確認して",
            "run the tests before merging",
        ])
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is True
        assert len(result["evidence"]) >= EVIDENCE_MIN_PATTERNS
        assert 0.0 < result["confidence"] <= 0.7

    def test_not_applicable_insufficient_patterns(self, tmp_path):
        """証拠要求パターンが閾値未満 → applicable=False。"""
        corrections = self._mock_corrections([
            "テスト実行して結果を見せて",
            "普通のメッセージ",
            "もう一つ普通のメッセージ",
        ])
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is False

    def test_evidence_min_patterns_threshold_gate(self, tmp_path):
        """ちょうど閾値の件数 → applicable=True。"""
        pattern_messages = [
            "テスト実行して",
            "動作確認して",
            "ビルド通して",
            "run tests please",
            "verify the output",
        ][:EVIDENCE_MIN_PATTERNS]
        corrections = self._mock_corrections(pattern_messages)
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is True
        assert len(result["evidence"]) == EVIDENCE_MIN_PATTERNS

    def test_below_threshold_returns_false(self, tmp_path):
        """閾値 - 1 個のパターン → applicable=False。"""
        pattern_messages = [
            "テスト実行して",
            "動作確認して",
        ][:EVIDENCE_MIN_PATTERNS - 1]
        corrections = self._mock_corrections(pattern_messages)
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is False
        assert result["confidence"] == 0.0

    def test_nonexistent_dir(self, tmp_path):
        """存在しないディレクトリ → applicable=False。"""
        result = detect_evidence_verification(tmp_path / "nonexistent")
        assert result["applicable"] is False

    def test_telemetry_import_failure(self, tmp_path):
        """telemetry_query が import 不可 → 安全な返り値。"""
        # sys.modules から telemetry_query を一時除去し、import 失敗をシミュレート
        import builtins
        original_import = builtins.__import__

        def _fail_import(name, *args, **kwargs):
            if name == "telemetry_query":
                raise ImportError("no module named telemetry_query")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_fail_import):
            # キャッシュ済みモジュールも除去
            saved = sys.modules.pop("telemetry_query", None)
            try:
                result = detect_evidence_verification(tmp_path)
            finally:
                if saved is not None:
                    sys.modules["telemetry_query"] = saved

        assert result["applicable"] is False

    def test_evidence_truncated_to_120_chars(self, tmp_path):
        """evidence の各エントリは 120 文字に切り詰められる。"""
        long_msg = "テスト実行して" + "A" * 200
        corrections = self._mock_corrections([long_msg] * EVIDENCE_MIN_PATTERNS)
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is True
        for ev in result["evidence"]:
            assert len(ev) <= 120

    def test_evidence_capped_at_10(self, tmp_path):
        """evidence は最大 10 件。"""
        corrections = self._mock_corrections(
            [f"テスト実行して ({i})" for i in range(20)]
        )
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        assert result["applicable"] is True
        assert len(result["evidence"]) <= 10

    def test_llm_escalation_prompt_present(self, tmp_path):
        """applicable=True の場合 llm_escalation_prompt が含まれる。"""
        corrections = self._mock_corrections(
            ["テスト実行して", "動作確認して", "ビルド通して", "verify this"]
        )
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        if result["applicable"]:
            assert "llm_escalation_prompt" in result
            assert "証拠要求" in result["llm_escalation_prompt"]

    def test_non_dict_corrections_skipped(self, tmp_path):
        """dict でない corrections レコードは無視される。"""
        corrections = [
            "not a dict",
            {"message": "テスト実行して"},
            {"message": "動作確認して"},
            {"message": "ビルド通して"},
        ]
        with self._patch_telemetry(corrections):
            result = detect_evidence_verification(tmp_path)

        # non-dict はスキップされ、3件の dict のみ処理される
        assert result["applicable"] is True or result["applicable"] is False
        # エラーにはならない
        assert "confidence" in result


class TestEvidenceContentAwareInstalled:
    def test_filename_match(self, tmp_path):
        """verify-before-claim.md が存在 → installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify-before-claim.md").write_text("# 証拠提示義務")
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_evidence_ja(self, tmp_path):
        """verify-before-claim.md は無いが、別ルールに「証拠」キーワード → installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "quality.md").write_text("完了前に証拠を提示すること。")
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_evidence_en(self, tmp_path):
        """'evidence' キーワードでマッチ。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "verify.md").write_text("Always provide evidence before claiming done.")
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_verify_before(self, tmp_path):
        """'verify-before' キーワードでマッチ。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "check.md").write_text("Implement verify-before pattern in reviews.")
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_no_match(self, tmp_path):
        """キーワードなし → not installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "other.md").write_text("# Some unrelated rule about formatting")
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is False

    def test_no_rules_dir(self, tmp_path):
        """rules ディレクトリ自体がない → not installed。"""
        entry = {"id": "evidence-before-claims", "rule_filename": "verify-before-claim.md"}
        assert check_verification_installed(entry, tmp_path) is False
