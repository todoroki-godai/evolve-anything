"""#477-4: markdown コードブロック内の ARN/ID は doc 文脈の既知 FP。

ドキュメント用スキルのフェンス付きコードブロック（``` ... ```）内に意図的に
記載された AWS ARN / 数値 ID / Slack ID は設定値でなく例示・参照であり、
hardcoded_value として個別提案に上げてはならない。

設計指針（ADR-043 / 「検出器FPは値でなく文脈で落とす」）: allowlist 個別パス列挙でなく
「コードブロック内」という文脈で除外する。代入文脈（resource: arn:...）は壊さない。
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from hardcoded_detector import detect_hardcoded_values  # noqa: E402


@pytest.fixture
def tmp_md(tmp_path):
    def _create(content: str) -> str:
        f = tmp_path / "test.md"
        f.write_text(content, encoding="utf-8")
        return str(f)
    return _create


def test_arn_in_code_block_excluded(tmp_md):
    content = (
        "# 例\n"
        "```bash\n"
        "aws lambda invoke --function arn:aws:lambda:ap-northeast-1:123456789012:function:my-func\n"
        "```\n"
    )
    path = tmp_md(content)
    results = detect_hardcoded_values(path)
    arn = [r for r in results if r["pattern_type"] == "aws_arn"]
    assert arn == [], f"コードブロック内 ARN は除外すべき: {arn}"


def test_arn_outside_code_block_still_detected(tmp_md):
    """代入文脈（コードブロック外）の ARN は従来どおり検出する。"""
    path = tmp_md("resource: arn:aws:lambda:ap-northeast-1:123456789012:function:my-func")
    results = detect_hardcoded_values(path)
    arn = [r for r in results if r["pattern_type"] == "aws_arn"]
    assert len(arn) == 1


def test_numeric_id_in_code_block_excluded(tmp_md):
    content = (
        "```\n"
        "account_id = 123456789012345\n"
        "```\n"
    )
    path = tmp_md(content)
    results = detect_hardcoded_values(path)
    num = [r for r in results if r["pattern_type"] == "numeric_id"]
    assert num == []


def test_code_block_toggle_reopens_detection(tmp_md):
    """閉じた後の行（コードブロック外）はまた検出対象に戻る。"""
    content = (
        "```\n"
        "arn:aws:s3:::bucket-one/arn:aws:iam::123456789012:role/r\n"
        "```\n"
        "resource: arn:aws:lambda:ap-northeast-1:123456789012:function:after\n"
    )
    path = tmp_md(content)
    results = detect_hardcoded_values(path)
    arn = [r for r in results if r["pattern_type"] == "aws_arn"]
    matched = [r["matched"] for r in arn]
    assert any("after" in m for m in matched), f"閉じた後の ARN は検出すべき: {matched}"
    assert not any("bucket-one" in m or "role/r" in m for m in matched), (
        f"コードブロック内 ARN は除外すべき: {matched}"
    )


def test_api_key_in_code_block_still_detected(tmp_md):
    """本物の token（api_key）はコードブロック内でも秘匿対象なので検出を弱めない。"""
    fake_token = "xoxb-" + "123456789012-FAKEFAKEFAKE"
    content = f"```\nexport TOKEN={fake_token}\n```\n"
    path = tmp_md(content)
    results = detect_hardcoded_values(path)
    kinds = {r["pattern_type"] for r in results}
    assert "api_key" in kinds, "token はコードブロック内でも検出する"
