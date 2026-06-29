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
    load_common_english_words,
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

    # 各 FP 語が「stoplist または辞書フィルタ」で除外されることを構造的に確認。
    # #567 以降、辞書に小文字形を持つ語（GET/JS/JavaScript 等）は stoplist から
    # 辞書フィルタへ移譲したため、源泉は stoplist 直接含有に限らない。
    @pytest.mark.parametrize("tok", [
        "GET", "POST", "PUT", "DELETE", "PATCH",  # HTTP メソッド
        "JS", "TS",                                 # 言語略語
        "JWT", "CRUD", "SHA", "RPC",               # 汎用テックプロトコル/概念
        "IaC", "CDN", "SaaS", "PaaS", "IaaS",     # クラウド概念
        "TypeScript", "JavaScript",                 # 言語名 CamelCase
    ])
    def test_excluded_by_stoplist_or_dictionary(self, tok: str):
        """FP 語が stoplist 直接 or 辞書フィルタのいずれかで除外される（#554/#567）。"""
        assert tok in DEFAULT_STOPLIST or tok.lower() in load_common_english_words(), (
            f"'{tok}' は汎用テック語として stoplist or 辞書で除外されるべき (#554/#567)"
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
    def test_aws_camelcase_excluded(self, tok: str):
        """CamelCase AWS サービス名が stoplist or 辞書フィルタで除外される。

        #567 以降、辞書に載るサービス名（Lambda 等）は辞書フィルタが落とす。
        """
        assert tok in DEFAULT_STOPLIST or tok.lower() in load_common_english_words(), (
            f"'{tok}' は AWS サービス名として stoplist or 辞書で除外されるべき (#554/#567)"
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

    # NOTE: EOA / PKCE は #554 当初 PJ 固有語と分類したが、#106 で「特定 PJ に
    # 固有でない汎用略語」と再分類し DEFAULT_STOPLIST へ移した（errno/JS 標準型と同列）。
    # ここでは真に PJ 固有な語のみを対象にする。
    @pytest.mark.parametrize("tok", [
        "AMAMO", "AnchorRegistry", "JCM", "MRV",
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


# ---------------------------------------------------------------------------
# #567 辞書ベース一般英単語フィルタ
# ---------------------------------------------------------------------------

class TestCommonEnglishWordsLoader:
    """同梱の常用英単語リストが load できること。"""

    def test_loads_nonempty_frozenset(self):
        words = load_common_english_words()
        assert isinstance(words, frozenset)
        # google-10000-english は ~9900 語ある
        assert len(words) > 5000

    def test_all_lowercase(self):
        words = load_common_english_words()
        # 代表的な語が小文字で含まれる
        assert "begin" in words
        assert "select" in words
        assert "info" in words

    def test_cached_same_object(self):
        """lazy load は同一 frozenset を返す（毎回ファイル読みしない）。"""
        assert load_common_english_words() is load_common_english_words()


class TestDictionaryFilterExcludesGenericWords:
    """issue #567 — 一般英単語が stoplist に無くても辞書フィルタで除外される。"""

    # stoplist に載っていなくても辞書フィルタで除外される語を選ぶ
    @pytest.mark.parametrize("tok", [
        "BEGIN", "FAILED", "SELECT", "INFO", "GROUP",
    ])
    def test_generic_word_excluded_by_dictionary(self, tmp_path: Path, tok: str):
        """stoplist を空にしても辞書フィルタが一般語を除外する。"""
        source = _make_source(tmp_path, f"The query said {tok} now.", f"src_{tok}.md")
        # stoplist を空にして「辞書フィルタ単独」で効くことを示す
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok not in result, (
            f"'{tok}' は一般英単語なので辞書フィルタで除外されるべき (#567)"
        )

    @pytest.mark.parametrize("tok", [
        "BEGIN", "FAILED", "SELECT", "INFO", "GROUP",
    ])
    def test_lower_in_common_words(self, tok: str):
        """対象語の .lower() が同梱辞書に含まれる（除外の源泉）。"""
        assert tok.lower() in load_common_english_words()


class TestDictionaryFilterKeepsProjectJargon:
    """issue #567 — PJ・framework 固有語は辞書に無いので保持される。"""

    @pytest.mark.parametrize("tok", [
        "FastAPI", "NestJS", "UPDATER", "AMAMO", "DuckDB",
    ])
    def test_project_jargon_not_in_common_words(self, tok: str):
        """PJ 固有語の .lower() は辞書に無い。"""
        assert tok.lower() not in load_common_english_words()

    @pytest.mark.parametrize("tok", [
        "FastAPI", "NestJS", "UPDATER", "AMAMO", "DuckDB",
    ])
    def test_project_jargon_reported_as_undefined(self, tmp_path: Path, tok: str):
        """PJ 固有語は辞書フィルタを通り抜けて undefined_terms に上がる。"""
        source = _make_source(tmp_path, f"The {tok} layer handles it.", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok in result, (
            f"'{tok}' は PJ/framework 固有語として undefined_terms に現れるべき (#567)"
        )


class TestNoFalseNegativeAfterStoplistShrink:
    """issue #567 回帰 — stoplist 縮小後も、変更前に除外されていた全 token が
    「辞書 or 残存 stoplist」のいずれかで必ず除外される（FN を作らない）。

    変更前の DEFAULT_STOPLIST 全体を真実集合とし、各 token が現行ロジックで
    undefined に上がらないことを source 経由で確認する。
    """

    # #567 変更前に DEFAULT_STOPLIST が除外していた全 token のスナップショット。
    # この集合のどれ一つも、変更後に undefined として再浮上してはならない。
    _PRE_567_STOPLIST = frozenset({
        # 汎用テック頭字語
        "API", "CLI", "LLM", "JSON", "JSONL", "YAML", "HTML", "CSS", "HTTP",
        "HTTPS", "URL", "URI", "SQL", "DB", "ID", "UUID", "PK", "OK", "TODO",
        "README", "SPEC", "CLAUDE", "CONTEXT", "ADR", "PR", "CI", "CD", "PJ", "SoT",
        "MUST", "NOT", "AND", "OR", "TTL", "CPU", "OSS", "UX", "UI", "E2E",
        "TDD", "SDD", "MCP", "SDK", "CC", "WoW", "TOP", "N", "AI", "ASCII",
        "ROI", "CJK", "NFD", "NaN", "LR", "GitHub",
        "INSERT", "INTO", "ON", "DO", "NOTHING", "IGNORE", "CONFLICT", "BLOCK",
        "CREATE", "UPDATE", "MERGE", "SPLIT", "SKIP", "REVIEW",
        "PreToolUse", "PostToolUse", "UserPromptSubmit", "AskUserQuestion",
        "MEMORY", "CHANGELOG",
        "ALWAYS", "FIRST", "INFO", "CUSTOM", "DIR", "WARN", "ERROR", "DEBUG",
        "ENV", "TMP", "SRC", "DST", "MAX", "MIN",
        "MB", "KB", "GB", "TB", "MD",
        "ARN", "CDK", "SNS", "SQS", "S3", "IAM", "VPC", "AWS",
        "EC2", "ECS", "EKS", "RDS", "DMS", "EMR", "KMS", "ACM",
        "ALB", "NLB", "ELB", "WAF", "ACL", "NAT", "IGW", "AMI",
        "ECR", "EFS", "EBS", "SSM", "SES", "STS", "SLA", "SLO",
        "SLI", "GW",
        "HEAD", "IO", "FP", "HOLD", "DEPRECATED", "FALLBACK", "RM", "SKILL",
        "PDF", "QA", "FAQ", "CSV", "XML", "TSV", "MVP", "KPI", "OKR",
        "PII", "GDPR", "FYI", "ETA", "WIP", "EOD",
        "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS",
        "JS", "TS",
        "JWT", "CRUD", "SHA", "RPC", "gRPC", "REST", "SOAP",
        "OAuth", "SAML", "CORS", "CSRF", "XSS",
        "CDN", "IaC", "SaaS", "PaaS", "IaaS",
        "TypeScript", "JavaScript", "GraphQL", "OpenAPI", "WebSocket",
        "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch",
        "CloudFront", "DynamoDB", "EventBridge", "Lambda",
        "Cognito", "CloudWatch", "CloudFormation", "CloudTrail",
        "Kinesis", "Athena", "Glue", "Redshift",
        "CodeBuild", "CodePipeline", "CodeDeploy",
        "StepFunctions",
        "BEGIN", "END", "COMMIT", "ROLLBACK", "SELECT",
        "DELETE", "WHERE", "JOIN", "GROUP", "ORDER",
        "FAILED", "FAIL", "PASS", "PASSED", "ERROR", "WARN", "WARNING",
        "TRACE", "FATAL", "GENERATED",
        "SKIPPED", "PENDING", "RUNNING", "DONE", "FIXME",
        "OFF", "LOW", "HIGH", "MID", "TRUE", "FALSE", "YES", "NO",
        "NULL", "NONE", "ENABLED", "DISABLED", "START", "STOP",
        "WEB", "APP", "DEV", "PROD", "STG", "TEST", "XXX",
        "SPA", "BFF", "RAG", "ORM", "OWASP", "MVC", "DAO", "DTO",
        "VM", "OS",
    })

    def test_no_pre_567_token_resurfaces(self, tmp_path: Path):
        """変更前に除外されていた全 token が現行ロジックでも undefined に出ない。"""
        # 全 token を 1 ファイルにまとめ、defined 無しで undefined を取る
        content = " ".join(sorted(self._PRE_567_STOPLIST))
        source = _make_source(tmp_path, content, "all_pre567.md")
        result = set(find_undefined_terms(_entries(), [source]))
        resurfaced = sorted(self._PRE_567_STOPLIST & result)
        assert not resurfaced, (
            f"#567 で FN 発生: 以下が undefined に再浮上した = {resurfaced}"
        )

    def test_each_pre_567_token_excluded_individually(self, tmp_path: Path):
        """各 token が個別 source でも除外される（regex 単体マッチ含む）。"""
        failures = []
        for tok in sorted(self._PRE_567_STOPLIST):
            source = _make_source(tmp_path, f"x {tok} y", f"s_{tok}.md")
            if tok in find_undefined_terms(_entries(), [source]):
                failures.append(tok)
        assert not failures, f"#567 FN: 個別 source で除外されない = {failures}"


# ---------------------------------------------------------------------------
# #23: 汎用技術略語 / フォーマットプレースホルダ / stdlib シンボルの除外
# ---------------------------------------------------------------------------

class TestFormatPlaceholdersExcluded:
    """issue #23 — 全大文字の日付/時刻プレースホルダ（YYYY/MM/DD 等）が
    undefined_terms に出ないこと。これらは jargon でなく整形トークン。"""

    @pytest.mark.parametrize("tok", [
        "YYYY", "MM", "DD", "HH", "SS", "YY", "MMM", "DDD",
        "YYYYMMDD", "HHMMSS",
    ])
    def test_placeholder_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        source = _make_source(
            tmp_path, f"Date format is {tok} here.", f"src_{tok}.md"
        )
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok not in result, (
            f"'{tok}' はフォーマットプレースホルダなので undefined に出てはいけない (#23)"
        )

    def test_full_date_pattern_not_reported(self, tmp_path: Path):
        """`YYYY/MM/DD` のような連結プレースホルダの各成分が出ない。"""
        source = _make_source(tmp_path, "Use YYYY/MM/DD or YYYY-MM-DD.", "d.md")
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        for tok in ("YYYY", "MM", "DD"):
            assert tok not in result, f"'{tok}' が undefined に出た (#23)"

    @pytest.mark.parametrize("tok", [
        # placeholder 除外が PJ 固有の頭字語（日付成分文字でも 1 種でない）を
        # 巻き込まないこと。MRV/JCM 等は除外しない。
        "MRV", "JCM", "DMS",
    ])
    def test_placeholder_filter_keeps_other_acronyms(self, tmp_path: Path, tok: str):
        """日付成分文字を含むが placeholder でない頭字語は誤除外しない。"""
        source = _make_source(tmp_path, f"The {tok} term.", f"src_{tok}.md")
        # DMS は stoplist 由来なので stoplist 込みで判定、MRV/JCM は固有語
        result_no_stop = find_undefined_terms(
            _entries(), [source], stoplist=frozenset()
        )
        if tok in ("MRV", "JCM"):
            assert tok in result_no_stop, (
                f"'{tok}' は固有頭字語なので placeholder フィルタで誤除外してはいけない (#23)"
            )


class TestStdlibSymbolsExcluded:
    """issue #23 — Python 標準ライブラリ/フレームワークの既知シンボル名が
    undefined_terms に出ないこと（ThreadPoolExecutor 等）。"""

    @pytest.mark.parametrize("tok", [
        "ThreadPoolExecutor", "ProcessPoolExecutor", "OrderedDict",
        "BytesIO", "StringIO", "ABCMeta", "Decimal", "Counter",
        "DataFrame",
    ])
    def test_stdlib_symbol_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        source = _make_source(
            tmp_path, f"We use {tok} in the pool.", f"src_{tok}.md"
        )
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok not in result, (
            f"'{tok}' は stdlib シンボルなので undefined に出てはいけない (#23)"
        )


class TestGenericTechAbbreviationsExcluded:
    """issue #23 — 広く知られた技術略語（MP4/OAuth2/TTS/HF/HN 等）が
    undefined_terms に出ないこと。"""

    @pytest.mark.parametrize("tok", [
        "MP4", "OAuth2", "TTS", "ASR", "HF", "HN",
        "UTC", "RGBA", "RGB", "GPU", "TPU", "MD5", "SHA256", "SHA1",
        "UTF", "ISO8601",
    ])
    def test_generic_abbrev_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        source = _make_source(
            tmp_path, f"Output is {tok} encoded.", f"src_{tok}.md"
        )
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, (
            f"'{tok}' は汎用技術略語なので undefined に出てはいけない (#23)"
        )


class TestIssue23DoesNotSuppressProjectJargon:
    """#23 の3フィルタが PJ 固有語を巻き込まないことを確認。"""

    @pytest.mark.parametrize("tok", [
        "AMAMO", "AnchorRegistry", "JCM", "MRV", "MemTrace", "DuckDB",
    ])
    def test_project_jargon_still_reported(self, tmp_path: Path, tok: str):
        source = _make_source(
            tmp_path, f"The {tok} component runs.", f"src_{tok}.md"
        )
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok in result, (
            f"'{tok}' は PJ 固有語として undefined に残るべき (#23)"
        )


class TestMaskedPlaceholderExcluded:
    """#72 — 同一文字反復のマスク/伏せ字（XXXXXXXXXXXX 等）が jargon 候補に出ない。"""

    @pytest.mark.parametrize("tok", ["XXXXXXXXXXXX", "XXXX", "AAAAAA"])
    def test_masked_token_not_reported(self, tmp_path: Path, tok: str):
        source = _make_source(tmp_path, f"アカウント番号は {tok} です。", f"src_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, f"'{tok}' はマスク値なので jargon 候補に現れてはいけない (#72)"

    def test_real_acronym_still_reported(self, tmp_path: Path):
        """マスク除外が本物の頭字語（複数文字種）まで巻き込まないこと。"""
        source = _make_source(tmp_path, "BES と RRF を使う。", "src_real.md")
        result = find_undefined_terms(_entries(), [source])
        assert "BES" in result and "RRF" in result


# ---------------------------------------------------------------------------
# #106: errno コード / JS 標準型 / 汎用略語の除外（#23 regression 修正）
# ---------------------------------------------------------------------------

class TestIssue106GenericTermsExcluded:
    """issue #106 — PJ 固有でない汎用語が jargon 候補に出ない（#23 の regression）。

    glossary jargon 検出が errno コード（ENOENT/EACCES 等）・JS 標準型
    （Uint8Array/ArrayBuffer/BigInt 等）・一般略語（ESM/IoT/OAI/PKCE/EOA）を
    誤って undefined_terms に出していた。これらは特定 PJ に固有でなくどの分野でも
    通用する汎用語のため除外する。
    """

    # issue 報告の混入語 + 同種の代表語。
    @pytest.mark.parametrize("tok", [
        # errno 系（POSIX / Node）
        "ENOENT", "EACCES", "EEXIST", "EPERM", "EISDIR", "ENOTDIR",
        "ECONNREFUSED", "ETIMEDOUT", "EADDRINUSE", "ENOTFOUND",
        # JS 標準型（CamelCase）
        "Uint8Array", "ArrayBuffer", "BigInt", "Int8Array",
        "Float32Array", "Uint16Array",
        # 一般略語
        "ESM", "IoT", "OAI", "PKCE", "EOA",
    ])
    def test_generic_term_not_reported_as_undefined(self, tmp_path: Path, tok: str):
        source = _make_source(tmp_path, f"It throws {tok} sometimes.", f"src106_{tok}.md")
        result = find_undefined_terms(_entries(), [source])
        assert tok not in result, (
            f"'{tok}' は汎用語なので undefined_terms に出てはいけない (#106)"
        )

    @pytest.mark.parametrize("tok", [
        # errno / 一般略語は DEFAULT_STOPLIST、JS 標準型は STDLIB_SYMBOLS に
        # 単一ソースで登録される（並行除外辞書を新設しない）。
        "ENOENT", "EACCES", "ESM", "IoT", "OAI", "PKCE", "EOA",
        "Uint8Array", "ArrayBuffer", "BigInt",
    ])
    def test_generic_term_in_single_source(self, tok: str):
        from glossary_drift import STDLIB_SYMBOLS

        assert tok in DEFAULT_STOPLIST or tok in STDLIB_SYMBOLS, (
            f"'{tok}' は単一ソース（DEFAULT_STOPLIST / STDLIB_SYMBOLS）で除外されるべき (#106)"
        )

    @pytest.mark.parametrize("tok", [
        "AMAMO", "AnchorRegistry", "JCM", "MRV", "BES", "RRF", "MemTrace",
    ])
    def test_does_not_suppress_project_jargon(self, tmp_path: Path, tok: str):
        """#106 の除外が PJ 固有語を巻き込まない（過剰除外の回帰防止）。

        stoplist を空にしても STDLIB_SYMBOLS/辞書フィルタに無いので残るべき。
        """
        source = _make_source(tmp_path, f"The {tok} runs.", f"src106pj_{tok}.md")
        result = find_undefined_terms(_entries(), [source], stoplist=frozenset())
        assert tok in result, (
            f"'{tok}' は PJ 固有語として undefined_terms に残るべき (#106)"
        )
