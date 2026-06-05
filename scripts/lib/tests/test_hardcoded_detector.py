"""hardcoded_detector のユニットテスト。"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from hardcoded_detector import compute_confidence_score, detect_hardcoded_values


@pytest.fixture
def tmp_md(tmp_path):
    """一時 Markdown ファイルを作成するヘルパー。"""
    def _create(content: str) -> str:
        f = tmp_path / "test.md"
        f.write_text(content, encoding="utf-8")
        return str(f)
    return _create


# ---------- パターン検出テスト ----------

class TestDetectPatterns:
    def test_slack_channel_id_doc_reference_excluded(self, tmp_md):
        """doc 文脈の Slack channel ID（C0...）は秘匿対象でないため検出しない（#337）。

        SKILL.md の運用手順に書かれた `C0ACWGEA5BR` 等は意図的な参照値で、
        ハードコード秘匿として 41 件も誤検知していた。
        """
        path = tmp_md("Post to channel C0ACWGEA5BR for alerts")
        results = detect_hardcoded_values(path)
        slack = [r for r in results if r["pattern_type"] == "slack_id"]
        assert slack == []

    def test_slack_app_id_doc_reference_excluded(self, tmp_md):
        """doc 文脈の Slack App ID（A0...）も除外する（#337）。"""
        path = tmp_md("slack_app_id: A04K8RZLM3Q")
        results = detect_hardcoded_values(path)
        slack = [r for r in results if r["pattern_type"] == "slack_id"]
        assert slack == []

    def test_slack_doc_id_exclusion_does_not_hide_real_token(self, tmp_md):
        """Slack ID 除外で本物の bot token（xoxb-）の検出を弱めない（#337）。"""
        # secret 形リテラルを public repo の diff に残さないため実行時に連結する
        # （pre-push secret guard / GitHub push protection の false positive 回避）
        fake_token = "xoxb-" + "123456789012-FAKEFAKEFAKE"
        path = tmp_md(f"token: {fake_token} and channel C0ACWGEA5BR")
        results = detect_hardcoded_values(path)
        kinds = {r["pattern_type"] for r in results}
        assert "api_key" in kinds  # bot token は依然検出
        assert "slack_id" not in kinds  # channel ID は除外

    def test_aws_arn(self, tmp_md):
        path = tmp_md("resource: arn:aws:lambda:ap-northeast-1:123456789012:function:my-func")
        results = detect_hardcoded_values(path)
        arn = [r for r in results if r["pattern_type"] == "aws_arn"]
        assert len(arn) == 1
        assert "arn:aws:lambda" in arn[0]["matched"]

    def test_api_key_xoxb(self, tmp_md):
        path = tmp_md("token: xoxb-1234567890-abcdefghij")
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert len(api) == 1
        assert api[0]["matched"].startswith("xoxb-")

    def test_api_key_akia(self, tmp_md):
        path = tmp_md("aws_key: AKIAIOSFODNN7EXAMPLE")
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert len(api) == 1

    def test_service_url(self, tmp_md):
        path = tmp_md("webhook: https://hooks.slack.com/services/T00000000/B00000000/xxxx")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert len(url) == 1

    def test_numeric_id(self, tmp_md):
        path = tmp_md("account: 123456789012")
        results = detect_hardcoded_values(path)
        num = [r for r in results if r["pattern_type"] == "numeric_id"]
        assert len(num) >= 1

    def test_result_format(self, tmp_md):
        path = tmp_md("slack_app_id: U04K8RZLM3Q")
        results = detect_hardcoded_values(path)
        assert len(results) >= 1
        r = results[0]
        assert "line" in r
        assert "matched" in r
        assert "pattern_type" in r
        assert "context" in r
        assert "confidence_score" in r
        assert isinstance(r["confidence_score"], float)


# ---------- 許容パターン除外テスト ----------

class TestExclusions:
    def test_placeholder_dollar_brace(self, tmp_md):
        path = tmp_md("export SLACK_APP_ID=${SLACK_APP_ID}")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_placeholder_angle_bracket(self, tmp_md):
        path = tmp_md("app_id: <YOUR_APP_ID>")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_dummy_sequential(self, tmp_md):
        path = tmp_md("例: A0123456789")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_dummy_zeros(self, tmp_md):
        path = tmp_md("account: 000000000000")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_localhost_url(self, tmp_md):
        path = tmp_md("url: http://localhost:3000/api/test")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_example_com_url(self, tmp_md):
        path = tmp_md("url: https://api.example.com/v1/test")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_version_number(self, tmp_md):
        path = tmp_md("version: 202401011234")
        results = detect_hardcoded_values(path)
        num = [r for r in results if r["pattern_type"] == "numeric_id"]
        assert len(num) == 0

    def test_semver(self, tmp_md):
        path = tmp_md("version: v1.2.3")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_arithmetic(self, tmp_md):
        path = tmp_md("timeout = 1000 * 60 * 24")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_timestamp_10digit(self, tmp_md):
        path = tmp_md("created_at: 1704067200")
        results = detect_hardcoded_values(path)
        num = [r for r in results if r["pattern_type"] == "numeric_id"]
        assert len(num) == 0

    def test_iso_date_adjacent(self, tmp_md):
        path = tmp_md("date: 2024-01-01 1704067200123")
        results = detect_hardcoded_values(path)
        num = [r for r in results if r["pattern_type"] == "numeric_id"]
        assert len(num) == 0


# ---------- インライン抑制テスト ----------

class TestSuppression:
    def test_suppressed_line(self, tmp_md):
        path = tmp_md("slack_app_id: U04K8RZLM3Q <!-- rl-allow: hardcoded -->")
        results = detect_hardcoded_values(path)
        assert len(results) == 0

    def test_suppression_only_affects_same_line(self, tmp_md):
        path = tmp_md("<!-- rl-allow: hardcoded -->\nslack_app_id: U04K8RZLM3Q")
        results = detect_hardcoded_values(path)
        slack = [r for r in results if r["pattern_type"] == "slack_id"]
        assert len(slack) == 1
        assert slack[0]["line"] == 2


# ---------- エラーハンドリングテスト ----------

class TestErrorHandling:
    def test_nonexistent_file(self):
        results = detect_hardcoded_values("/nonexistent/file.md")
        assert results == []

    def test_binary_file(self, tmp_path):
        f = tmp_path / "binary.md"
        f.write_bytes(b"some\x00binary\x00content")
        results = detect_hardcoded_values(str(f))
        assert results == []

    def test_permission_error(self, tmp_path):
        f = tmp_path / "noperm.md"
        f.write_text("A04K8RZLM3Q")
        f.chmod(0o000)
        try:
            results = detect_hardcoded_values(str(f))
            assert results == []
        finally:
            f.chmod(0o644)


# ---------- confidence_score テスト ----------

class TestConfidenceScore:
    def test_api_key_confidence(self):
        assert compute_confidence_score("api_key") == 0.85

    def test_aws_arn_confidence(self):
        assert compute_confidence_score("aws_arn") == 0.75

    def test_slack_id_confidence(self):
        assert compute_confidence_score("slack_id") == 0.65

    def test_service_url_confidence(self):
        assert compute_confidence_score("service_url") == 0.55

    def test_numeric_id_confidence(self):
        assert compute_confidence_score("numeric_id") == 0.45

    def test_unknown_type_confidence(self):
        assert compute_confidence_score("unknown") == 0.5

    def test_detection_includes_confidence(self, tmp_md):
        path = tmp_md("slack_app_id: U04K8RZLM3Q")
        results = detect_hardcoded_values(path)
        assert results[0]["confidence_score"] == 0.65


# ---------- #352: 正規 API URL の FP 除外 / 裸の AWS account 番号検出 ----------

class TestApiUrlFpAndAwsAccount:
    """#352: service_url が公式 API パスを FP 検出 / 裸の AWS account ID が未検出。"""

    def test_slack_official_api_url_not_detected(self, tmp_md):
        """Slack 公式 API エンドポイント は FP 除外される。

        https://slack.com/api/chat.postMessage は公式 API URL であり
        ハードコード秘匿ではない。confidence 0.55 で検出されていた FP を解消する。
        """
        path = tmp_md("curl https://slack.com/api/chat.postMessage -d '{}'")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"公式 Slack API URL が FP 検出された: {url}"

    def test_amazonaws_official_api_url_not_detected(self, tmp_md):
        """amazonaws 公式 API エンドポイントも FP 除外される。

        https://s3.amazonaws.com/my-bucket/key のようなエンドポイントは
        参照ドキュメントに書かれることが多く、秘匿対象ではない。
        """
        path = tmp_md("endpoint: https://s3.amazonaws.com/bucket-name/object")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"公式 amazonaws URL が FP 検出された: {url}"

    def test_amazonaws_regional_endpoint_not_detected(self, tmp_md):
        """region 込みの多段ラベルエンドポイントも FP 除外される（#352 review fix）。

        https://sqs.us-east-1.amazonaws.com/... のような region 付きホストは
        単一ラベル正規表現では取りこぼしていた。
        """
        path = tmp_md("queue: https://sqs.us-east-1.amazonaws.com/123456789012/my-queue")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"region 付き amazonaws URL が FP 検出された: {url}"

    def test_slack_webhook_url_still_detected(self, tmp_md):
        """Slack webhook URL（services/ パス）は依然として検出する。

        https://hooks.slack.com/services/T00000000/B00000000/xxxx は
        incoming webhook の URL で秘匿対象のため除外しない。
        """
        path = tmp_md("webhook: https://hooks.slack.com/services/T00000000/B00000000/xxxx")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert len(url) == 1, "Slack webhook URL は依然検出されるべき"

    def test_bare_aws_account_id_with_context_hint_detected(self, tmp_md):
        """文脈ヒント（account:）付きの裸 12 桁 AWS account ID を low confidence で検出。

        ARN 外の bare account number（060361059038 等）は従来未検出だった。
        文脈ヒント（account, aws, account_id 等）があるものを low confidence で検出。
        """
        path = tmp_md("aws_account_id: 060361059038")
        results = detect_hardcoded_values(path)
        # aws_account_id パターンで検出されるか、または numeric_id で検出される
        aws_acct = [r for r in results if r["pattern_type"] in ("aws_account_id", "numeric_id")]
        assert len(aws_acct) >= 1, "文脈ヒント付き bare AWS account ID が未検出"
        # confidence は low (<=0.45 程度)
        for r in aws_acct:
            assert r["confidence_score"] <= 0.45, f"confidence が高すぎる: {r['confidence_score']}"

    def test_bare_aws_account_without_hint_confidence_low(self, tmp_md):
        """文脈ヒントなしの裸 12 桁数字は low confidence のまま（既存 numeric_id）。

        電話番号・タイムスタンプとの区別が困難なため、
        文脈なしは既存 numeric_id (0.45) の範囲に留める。
        """
        path = tmp_md("id: 060361059038")
        results = detect_hardcoded_values(path)
        # 既存の numeric_id として検出されうる（12桁）
        detected = [r for r in results if r["pattern_type"] in ("aws_account_id", "numeric_id")]
        # confidence は 0.45 以下
        for r in detected:
            assert r["confidence_score"] <= 0.45


# ---------- extra_patterns / extra_allowlist テスト ----------

class TestExtensibility:
    def test_extra_pattern(self, tmp_md):
        path = tmp_md("custom_id: CUST-12345-ABCDE")
        extra = [{"name": "custom_id", "regex": r"CUST-\d{5}-[A-Z]{5}", "confidence": 0.7}]
        results = detect_hardcoded_values(path, extra_patterns=extra)
        custom = [r for r in results if r["pattern_type"] == "custom_id"]
        assert len(custom) == 1
        assert custom[0]["confidence_score"] == 0.7

    def test_extra_allowlist(self, tmp_md):
        path = tmp_md("slack_app_id: A04K8RZLM3Q")
        results = detect_hardcoded_values(path, extra_allowlist=[r"A04K8RZLM3Q"])
        assert len(results) == 0

    def test_extra_pattern_with_compiled_regex(self, tmp_md):
        import re
        path = tmp_md("token: MY-SECRET-TOKEN-123")
        extra = [{"name": "my_token", "regex": re.compile(r"MY-SECRET-TOKEN-\d+"), "confidence": 0.9}]
        results = detect_hardcoded_values(path, extra_patterns=extra)
        assert len([r for r in results if r["pattern_type"] == "my_token"]) == 1
