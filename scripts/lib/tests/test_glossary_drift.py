"""glossary_drift.py のテスト — #554 stoplist 拡張（汎用テック語 + AWS サービス名）。

既存の DEFAULT_STOPLIST に universal-tech-term と AWS サービス名を追加し、
amamo PJ 等で FP として報告された汎用語が find_undefined_terms から除外されることを
回帰テストで封じる。

TDD: 失敗テスト先→実装→緑。
"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from glossary_drift import (  # noqa: E402
    DEFAULT_STOPLIST,
    GlossaryEntry,
    find_undefined_terms,
)


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------

def _entries(*terms: str) -> list[GlossaryEntry]:
    """ダミーの GlossaryEntry リストを生成する（already-defined 扱い）。"""
    return [GlossaryEntry(term=t, meaning="dummy", first_seen="#0") for t in terms]


def _make_source(tmp_path: Path, content: str, name: str = "SOURCE.md") -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# #554 回帰テスト: 汎用テック語が FP として検出されない
# ---------------------------------------------------------------------------

class TestUniversalTechTermsExcluded:
    """issue #554 — 汎用テック語が DEFAULT_STOPLIST で除外されること。"""

    # 各 FP 語が DEFAULT_STOPLIST に含まれることを構造的に確認
    @pytest.mark.parametrize("tok", [
        "GET", "POST", "PUT", "DELETE", "PATCH",  # HTTP メソッド
        "JS", "TS",                                 # 言語略語
        "JWT", "CRUD", "SHA", "RPC",               # 汎用テックプロトコル/概念
        "IaC", "CDN", "SaaS", "PaaS", "IaaS",     # クラウド概念
        "TypeScript", "JavaScript",                 # 言語名 CamelCase
    ])
    def test_in_default_stoplist(self, tok: str):
        """FP 語が DEFAULT_STOPLIST に直接含まれる（find_undefined_terms の除外源泉）。"""
        assert tok in DEFAULT_STOPLIST, (
            f"'{tok}' は汎用テック語として DEFAULT_STOPLIST に含まれるべき (#554)"
        )

    @pytest.mark.parametrize("tok", [
        "GET", "JS", "JWT", "CRUD", "SHA", "RPC", "IaC", "CDN", "SaaS", "TypeScript",
    ])
    def test_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        """SoT に出現しても find_undefined_terms が汎用テック語を返さない。"""
        source = _make_source(tmp_path, f"This uses {tok} extensively.", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, (
            f"'{tok}' は汎用テック語として undefined_terms に現れてはいけない (#554)"
        )


class TestAWSServiceNamesExcluded:
    """issue #554 — AWS サービス名が DEFAULT_STOPLIST で除外されること。"""

    @pytest.mark.parametrize("tok", [
        "CloudFront", "DynamoDB", "EventBridge",   # issue 例示
        "Lambda", "Cognito", "CloudWatch",          # 代表的 CamelCase サービス
        "Kinesis", "Athena", "Glue",               # 代表的 CamelCase サービス
    ])
    def test_aws_camelcase_in_default_stoplist(self, tok: str):
        """CamelCase AWS サービス名が DEFAULT_STOPLIST に含まれる。"""
        assert tok in DEFAULT_STOPLIST, (
            f"'{tok}' は AWS サービス名として DEFAULT_STOPLIST に含まれるべき (#554)"
        )

    @pytest.mark.parametrize("tok", [
        "CloudFront", "DynamoDB", "EventBridge",
    ])
    def test_aws_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        """SoT に出現しても find_undefined_terms が AWS サービス名を返さない。"""
        source = _make_source(tmp_path, f"Data is stored in {tok}.", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, (
            f"'{tok}' は AWS サービス名として undefined_terms に現れてはいけない (#554)"
        )


# ---------------------------------------------------------------------------
# 非除外: PJ 固有語は誤ってスキップしない
# ---------------------------------------------------------------------------

class TestProjectJargonNotExcluded:
    """汎用語追加が PJ 固有語を巻き込まないことを確認する。"""

    @pytest.mark.parametrize("tok", [
        "AMAMO", "AnchorRegistry", "JCM", "MRV", "EOA", "PKCE",
    ])
    def test_pj_jargon_not_in_stoplist(self, tok: str):
        """PJ 固有語は DEFAULT_STOPLIST に入っていない。"""
        assert tok not in DEFAULT_STOPLIST, (
            f"'{tok}' は PJ 固有語なので DEFAULT_STOPLIST に含まれてはいけない"
        )

    @pytest.mark.parametrize("tok", [
        "AMAMO", "AnchorRegistry", "JCM",
    ])
    def test_pj_jargon_reported_as_undefined(self, tmp_path: Path, tok: str):
        """PJ 固有語は用語集未登録のとき undefined_terms に上がる。"""
        source = _make_source(tmp_path, f"The {tok} component processes events.", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok in result, (
            f"'{tok}' は PJ 固有語として undefined_terms に現れるべき"
        )

    def test_pj_jargon_suppressed_when_defined(self, tmp_path: Path):
        """用語集に登録済みの PJ 固有語は undefined_terms に上がらない。"""
        source = _make_source(tmp_path, "The AMAMO system uses AnchorRegistry.")
        entries = _entries("AMAMO", "AnchorRegistry")
        result = find_undefined_terms(entries, [source])
        assert "AMAMO" not in result
        assert "AnchorRegistry" not in result


# ---------------------------------------------------------------------------
# 既存動作の回帰: 既存 stoplist がそのまま機能する
# ---------------------------------------------------------------------------

class TestExistingStoplistIntact:
    """既存 DEFAULT_STOPLIST のエントリが引き続き除外される。"""

    @pytest.mark.parametrize("tok", [
        "API", "CLI", "LLM", "JSON", "SQL", "ARN", "CDK", "SNS",
        "IAM", "AWS", "EC2", "S3", "SQS",
    ])
    def test_existing_entries_still_excluded(self, tmp_path: Path, tok: str):
        source = _make_source(tmp_path, f"Uses {tok} heavily.", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, (
            f"既存 stoplist の '{tok}' が除外されなくなった（回帰）"
        )
