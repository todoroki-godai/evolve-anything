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


# ---------- #359: doc 文脈の URL/ARN 過剰検出の抑制 ----------

class TestDocContextSuppression:
    """#359: SKILL.md の手順説明・例示コマンド中の URL/ARN は設定値でないため抑制。

    evolve の proposable が doc URL/ARN の FP で埋まり本来の検出が埋もれる問題。
    A: 公式・非秘匿エンドポイント（api.slack.com/, slack.com/oauth/）の allowlist 拡張。
    B: 手順番号行・例示コマンド行の service_url / aws_arn を doc 文脈として抑制。
    """

    # --- A: allowlist 拡張（公式・非秘匿の公開エンドポイント） ---

    def test_api_slack_com_apps_not_detected(self, tmp_md):
        """Slack 開発者ポータル api.slack.com/apps は秘匿でない公開 URL（#359）。"""
        path = tmp_md("App 設定: https://api.slack.com/apps?new_app=1")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"api.slack.com の開発者ポータル URL が FP 検出された: {url}"

    def test_slack_oauth_url_not_detected(self, tmp_md):
        """Slack OAuth authorize エンドポイントは公開・非秘匿（#359）。"""
        path = tmp_md("認可: https://slack.com/oauth/v2/authorize?client_id=foo")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"Slack OAuth URL が FP 検出された: {url}"

    # --- B: doc 文脈（手順番号・例示コマンド）の抑制 ---

    def test_numbered_step_url_suppressed(self, tmp_md):
        """手順番号行の URL は設定値でなく参照なので抑制する（#359）。"""
        path = tmp_md("1. https://my-portal.slack.com/admin にアクセスする")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"手順番号行の URL が FP 検出された: {url}"

    def test_example_aws_command_arn_suppressed(self, tmp_md):
        """例示 aws CLI コマンド中の ARN は設定値でなく例示なので抑制する（#359）。"""
        path = tmp_md(
            "aws secretsmanager get-secret-value --secret-id "
            "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:slack-token"
        )
        results = detect_hardcoded_values(path)
        arn = [r for r in results if r["pattern_type"] == "aws_arn"]
        assert arn == [], f"例示コマンド中の ARN が FP 検出された: {arn}"

    def test_example_curl_arn_suppressed(self, tmp_md):
        """例示 curl コマンド中の ARN も抑制する（#359）。"""
        path = tmp_md(
            "curl -X POST --data arn:aws:sns:us-east-1:123456789012:my-topic https://x"
        )
        results = detect_hardcoded_values(path)
        arn = [r for r in results if r["pattern_type"] == "aws_arn"]
        assert arn == [], f"例示 curl 中の ARN が FP 検出された: {arn}"

    # --- 回帰: 代入文脈の検出は維持する（doc 文脈と構文的に交わらない） ---

    def test_assignment_arn_still_detected(self, tmp_md):
        """`resource: arn:...` の代入文脈 ARN は依然検出する（doc 文脈でない、#359 回帰）。"""
        path = tmp_md("resource: arn:aws:lambda:ap-northeast-1:123456789012:function:my-func")
        results = detect_hardcoded_values(path)
        arn = [r for r in results if r["pattern_type"] == "aws_arn"]
        assert len(arn) == 1, "代入文脈の ARN 検出が doc 文脈抑制で壊れた"

    def test_assignment_webhook_still_detected(self, tmp_md):
        """`webhook: https://hooks.slack.com/services/...` の secret は依然検出（#359 回帰）。"""
        path = tmp_md("webhook: https://hooks.slack.com/services/T00000000/B00000000/xxxx")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert len(url) == 1, "代入文脈の webhook secret 検出が doc 文脈抑制で壊れた"


# ---------- #377-2: Bot ID / markdown テーブルの doc 文脈抑制 ----------

class TestDocContextBotIdAndTable:
    """#377-2: 説明文中の実 Bot ID（B0...）と markdown テーブル内の ARN を抑制。

    Bot ID は token（xoxb-）と違い公開参照値で、C0(channel)/A0(app) と同様に
    doc 参照として slack_id 検出から除外する。markdown テーブル行は散文 doc 文脈
    なので service_url / aws_arn を抑制する（手順番号・例示コマンドと同じ扱い）。
    """

    # --- Bot ID（B0...）の除外 ---

    def test_bot_id_not_detected(self, tmp_md):
        """説明文中の実 Bot ID（B0...）は公開参照値なので抑制する（#377-2）。"""
        path = tmp_md("通知 Bot は B0AJRU27Z2Q を使う")
        results = detect_hardcoded_values(path)
        slack = [r for r in results if r["pattern_type"] == "slack_id"]
        assert slack == [], f"doc 参照の Bot ID が FP 検出された: {slack}"

    def test_channel_id_still_excluded(self, tmp_md):
        """C0... channel ID の除外は維持（#337 回帰）。"""
        path = tmp_md("チャンネル C0AJRU27Z2Q に投稿する")
        results = detect_hardcoded_values(path)
        assert [r for r in results if r["pattern_type"] == "slack_id"] == []

    def test_app_id_still_excluded(self, tmp_md):
        """A0... app ID の除外は維持（#337 回帰）。"""
        path = tmp_md("App ID A04K8RZLM3Q を確認")
        results = detect_hardcoded_values(path)
        assert [r for r in results if r["pattern_type"] == "slack_id"] == []

    def test_user_id_still_detected(self, tmp_md):
        """U(user)/W 始まりの slack_id は doc 参照前提でないため依然検出（過剰除外防止）。"""
        path = tmp_md("owner: U0AJRU27Z2Q")
        results = detect_hardcoded_values(path)
        slack = [r for r in results if r["pattern_type"] == "slack_id"]
        assert len(slack) == 1, "U 始まりまで除外すると過剰抑制になる"

    # --- markdown テーブル行の doc 文脈抑制 ---

    def test_markdown_table_arn_suppressed(self, tmp_md):
        """markdown テーブル行の ARN は散文 doc 文脈なので抑制する（#377-2）。"""
        path = tmp_md(
            "| 環境 | Secret ARN |\n"
            "|------|------------|\n"
            "| dev | arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:slack |\n"
        )
        results = detect_hardcoded_values(path)
        arn = [r for r in results if r["pattern_type"] == "aws_arn"]
        assert arn == [], f"テーブル行の ARN が FP 検出された: {arn}"

    def test_markdown_table_url_suppressed(self, tmp_md):
        """markdown テーブル行の service_url も抑制する（#377-2）。"""
        path = tmp_md("| portal | https://my-portal.slack.com/admin |")
        results = detect_hardcoded_values(path)
        url = [r for r in results if r["pattern_type"] == "service_url"]
        assert url == [], f"テーブル行の URL が FP 検出された: {url}"

    # --- 回帰: 代入文脈は依然検出（テーブル抑制が壊さない） ---

    def test_assignment_arn_still_detected_after_table_rule(self, tmp_md):
        """`resource: arn:...` の代入文脈はテーブル抑制追加後も検出（#377-2 回帰）。"""
        path = tmp_md("resource: arn:aws:lambda:ap-northeast-1:123456789012:function:f")
        results = detect_hardcoded_values(path)
        assert len([r for r in results if r["pattern_type"] == "aws_arn"]) == 1


# ---------- #419: api_key regex の単語境界（sk- 部分一致 FP） ----------

class TestApiKeyWordBoundary:
    """#419: `sk-` パターンが英単語内部に部分一致して大量 FP を出していた。

    gstack スキル散文中の `ask-only-for-one-way` 等が `(...|sk-|...)` に単語途中で
    マッチし、fleet ISSUES の 92%（552/599）を占める偽陽性だった。
    `sk-` の前に単語境界（英数字でない）を要求して防ぐ。
    """

    def test_sk_prose_no_word_boundary_not_detected(self, tmp_md):
        """散文 `ask-only-for-one-way` は `sk-` の部分一致 FP として検出しない（#419）。"""
        path = tmp_md("This rule is ask-only-for-one-way and never two-way.")
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert api == [], f"散文中の sk- 部分一致が FP 検出された: {api}"

    def test_various_sk_suffixed_words_not_detected(self, tmp_md):
        """`task-`/`risk-`/`disk-` 等 sk で終わる単語＋ハイフンも FP にしない（#419）。"""
        path = tmp_md(
            "task-runner risk-averse disk-usage flask-app brisk-walking "
            "mask-required desk-setup"
        )
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert api == [], f"sk 終わり単語の sk- 部分一致が FP 検出された: {api}"

    def test_real_sk_token_still_detected(self, tmp_md):
        """単語境界を持つ本物の `sk-` API キーは引き続き検出する（#419 回帰）。

        secret 形リテラルを public repo の diff に残さないため実行時に連結する
        （pre-push secret guard / GitHub push protection の FP 回避）。
        """
        fake_key = "sk" + "-" + "a" * 20
        path = tmp_md(f"openai_api_key: {fake_key}")
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert len(api) == 1, f"本物の sk- API キーが検出されなかった: {results}"
        assert api[0]["matched"].startswith("sk-")

    def test_sk_token_at_line_start_detected(self, tmp_md):
        """行頭の `sk-` トークン（前に文字なし）も検出する（境界 = 行頭, #419 回帰）。"""
        fake_key = "sk" + "-" + "b" * 20
        path = tmp_md(fake_key)
        results = detect_hardcoded_values(path)
        api = [r for r in results if r["pattern_type"] == "api_key"]
        assert len(api) == 1

    def test_other_api_key_prefixes_still_detected(self, tmp_md):
        """xoxb-/xapp-/AKIA の検出は単語境界追加後も維持する（#419 回帰）。"""
        fake_xoxb = "xoxb-" + "123456789012-FAKEFAKEFAKE"
        path = tmp_md(
            f"slack: {fake_xoxb}\naws: AKIAIOSFODNN7EXAMPLE"
        )
        results = detect_hardcoded_values(path)
        api = {r["matched"][:4] for r in results if r["pattern_type"] == "api_key"}
        assert "xoxb" in api
        assert "AKIA" in api


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
