"""glossary_drift のテスト（決定論・LLM 非依存）。

CONTEXT.md（Ubiquitous Language 用語集）が腐る = 用語が追加されず
SoT から乖離する、という drift を構造チェック + 頭字語照合で検出する。
spec-keeper の update フローが advisory として消費する。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib import glossary_drift as gd


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


_VALID = """# 用語集

| 用語 | 意味 | 初出 |
|------|------|------|
| BES | 後ろ向きサブゴール分解 | #253 |
| MemTrace | 帰属診断 | #254 |
"""


def test_parse_valid_glossary(tmp_path):
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    entries, malformed = gd.parse_glossary(str(ctx))
    assert [e.term for e in entries] == ["BES", "MemTrace"]
    assert entries[0].meaning == "後ろ向きサブゴール分解"
    assert entries[0].first_seen == "#253"
    assert malformed == []


def test_parse_malformed_row(tmp_path):
    body = _VALID + "| 列が2つしかない |\n"
    ctx = _write(tmp_path, "CONTEXT.md", body)
    entries, malformed = gd.parse_glossary(str(ctx))
    assert [e.term for e in entries] == ["BES", "MemTrace"]
    assert len(malformed) == 1
    assert "列が2つしかない" in malformed[0][1]


def test_duplicate_terms(tmp_path):
    body = _VALID + "| BES | 別定義 | #999 |\n"
    ctx = _write(tmp_path, "CONTEXT.md", body)
    report = gd.check_glossary(str(ctx), [])
    assert report.duplicate_terms == ["BES"]
    assert report.has_drift()


def test_missing_first_seen(tmp_path):
    body = _VALID + "| RRF | 順位融合 |  |\n"
    ctx = _write(tmp_path, "CONTEXT.md", body)
    report = gd.check_glossary(str(ctx), [])
    assert "RRF" in report.missing_first_seen
    assert report.has_drift()


def test_find_undefined_terms(tmp_path):
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    # SPEC に MemTrace(登録済) と FooBar(未登録) が出現
    src = _write(tmp_path, "SPEC.md", "MemTrace と FooBar を統合する。API も使う。")
    # API はデフォルト stoplist で除外される想定
    undefined = gd.find_undefined_terms(
        gd.parse_glossary(str(ctx))[0], [str(src)]
    )
    assert "FooBar" in undefined
    assert "MemTrace" not in undefined  # 登録済みは出ない
    assert "API" not in undefined       # stoplist


def test_uppercase_stopwords_excluded(tmp_path):
    """英大文字ストップワード（ALWAYS/FIRST/INFO/CUSTOM/DIR/MB/MD）は jargon でない（#337）。

    CONTEXT.md 不在の sys-bots で「未登録 jargon 56件」のうち 45件がこの種のノイズだった。
    """
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(
        tmp_path, "SPEC.md",
        "ALWAYS run FIRST. INFO level. CUSTOM DIR is 100 MB. See README.MD.",
    )
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    for noise in ("ALWAYS", "FIRST", "INFO", "CUSTOM", "DIR", "MB", "MD"):
        assert noise not in undefined, f"{noise} はストップワード"


def test_generic_meta_words_excluded(tmp_path):
    """git/メタ/汎用状態語は jargon でない。

    rl-anything 自身の evolve で CONTEXT.md 候補に HEAD/IO/FP/HOLD/DEPRECATED/
    FALLBACK/RM/SKILL の汎用・メタ語が混入していた。PJ 固有語（DuckDB 等）は
    残しつつ、これらの一般語のみ除外する（#353⑫ の AWS denylist と同種の拡張）。
    """
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(
        tmp_path, "SPEC.md",
        "git HEAD を見る。IO 層の FP を HOLD する。DEPRECATED な FALLBACK。"
        "RM 報酬。SKILL.md を編集。DuckDB は固有語として残す。",
    )
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    for noise in ("HEAD", "IO", "FP", "HOLD", "DEPRECATED", "FALLBACK", "RM", "SKILL"):
        assert noise not in undefined, f"{noise} は汎用/メタ語で除外されるべき"
    assert "DuckDB" in undefined  # PJ 固有語は残す


def test_generic_doc_abbreviations_excluded(tmp_path):
    """ドキュメント汎用略語（PDF/QA/FAQ 等）は jargon でない（#477-4）。

    glossary の jargon 候補に PDF/QA 等の汎用略語が並ぶノイズを denylist で塞ぐ。
    PJ 固有語（DuckDB 等の CamelCase）は小文字を含むため誤除外しない。
    """
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(
        tmp_path, "SPEC.md",
        "PDF を出力。QA を実施。FAQ を更新。CSV/XML をパース。MVP を出す。"
        "KPI を測る。DuckDB は固有語として残す。",
    )
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    for noise in ("PDF", "QA", "FAQ", "CSV", "XML", "MVP", "KPI"):
        assert noise not in undefined, f"{noise} は汎用ドキュメント略語で除外されるべき"
    assert "DuckDB" in undefined  # PJ 固有語は残す


def test_slack_id_excluded_from_jargon(tmp_path):
    """Slack ID（C05KMHFDPB9 等）は jargon 候補から除外する（#337）。"""
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", "Post to C05KMHFDPB9 and notify A04K8RZLM3Q.")
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    assert "C05KMHFDPB9" not in undefined
    assert "A04K8RZLM3Q" not in undefined


def test_real_jargon_still_detected_after_stoplist_expansion(tmp_path):
    """ストップリスト拡張後も本物の固有語（DuckDB 等）は検出する（#337 回帰）。"""
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", "DuckDB と BM25 と FooBar を使う。INFO は無視。")
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    assert "DuckDB" in undefined
    assert "FooBar" in undefined
    assert "INFO" not in undefined


def test_undefined_alone_does_not_gate(tmp_path):
    # 用語集は構造的に健全だが SoT に未登録 jargon がある場合:
    # has_drift（gate）は False、has_undefined（advisory）は True。
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", "FooBar という新概念を追加した。")
    report = gd.check_glossary(str(ctx), [str(src)])
    assert "FooBar" in report.undefined_terms
    assert report.has_undefined()
    assert not report.has_drift()  # advisory はオオカミ少年化させない


def test_has_drift_clean(tmp_path):
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", "BES と MemTrace を使う。")
    report = gd.check_glossary(str(ctx), [str(src)])
    assert not report.has_drift()
    assert report.undefined_terms == []


def test_real_context_md_no_structural_drift():
    """実 CONTEXT.md を実コーパスでドッグフード（合成 fixture の false confidence 回避）。

    undefined_terms は SPEC.md の頭字語量で揺れるため assert しない。
    構造的健全性（malformed / duplicate / missing_first_seen）のみ保証する。
    """
    repo = Path(__file__).resolve().parent.parent.parent
    ctx = repo / "CONTEXT.md"
    if not ctx.exists():
        import pytest

        pytest.skip("CONTEXT.md 未作成（T3 で生成）")
    report = gd.check_glossary(str(ctx), [])
    assert report.malformed_lines == []
    assert report.duplicate_terms == []
    assert report.missing_first_seen == []


def test_audit_section_none_without_context_md_when_jargon_thin(tmp_path):
    """CONTEXT.md 不在 + jargon 候補が閾値未満なら None（薄い PJ に空の用語集を作らない）。

    #275 で seed ケースを emit するようにしたが、候補が SEED_MIN_CANDIDATES 未満の
    PJ では従来どおり沈黙する（オオカミ少年化回避）。
    """
    from lib.audit.sections import build_glossary_drift_section

    _write(tmp_path, "SPEC.md", "ふつうの日本語の文章です。特別な用語はありません。")
    assert build_glossary_drift_section(tmp_path) is None


def test_audit_section_seeds_when_context_absent(tmp_path):
    """CONTEXT.md 不在 + 未登録 jargon ≥ SEED_MIN_CANDIDATES なら seed 提案 section を出す（#275）。

    #278 の observability contract に統合した形。glossary_seed を独立 phase でなく
    build_glossary_drift_section が emit するため、markdown と result['observability']
    の両経路に自動 surface する（whack-a-mole 回避）。creation→detection を一本化。
    """
    from lib.audit.sections import build_glossary_drift_section

    _write(tmp_path, "SPEC.md", "FooBar と BazQux と MemTrace と QuuxThing を導入した。")
    section = build_glossary_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "Glossary Drift" in body
    assert "CONTEXT.md" in body  # 不在を明示
    assert "用語集未作成" in body  # seed 提案見出し
    assert "4" in body  # 候補件数（FooBar/BazQux/MemTrace/QuuxThing）


def test_audit_section_surfaces_undefined(tmp_path):
    """CONTEXT.md があれば evolve(audit) の section に未登録 jargon が出る。"""
    from lib.audit.sections import build_glossary_drift_section

    _write(tmp_path, "CONTEXT.md", _VALID)
    _write(tmp_path, "SPEC.md", "BES に加えて NewJargon を導入した。")
    section = build_glossary_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "Glossary Drift" in body
    assert "NewJargon" in body  # 未登録 jargon が advisory に出る


def test_audit_section_flags_structural_drift(tmp_path):
    """構造 drift（初出欠落）は section で ⚠ として明示される。"""
    from lib.audit.sections import build_glossary_drift_section

    broken = "# 用語集\n\n| 用語 | 意味 | 初出 |\n|------|------|------|\n| Foo | バー | |\n"
    _write(tmp_path, "CONTEXT.md", broken)
    section = build_glossary_drift_section(tmp_path)
    assert section is not None
    assert any("⚠" in ln for ln in section)


_SEEDED = """# proj — 用語集

| 用語 | 意味 | 初出 |
|------|------|------|
| Foo | LLM 推定の意味 | ⚠UNVERIFIED |
| Bar | 確定済みの意味 | #100 |
"""


def test_unverified_parsed_and_not_gated(tmp_path):
    """UNVERIFIED 行は unverified_terms に入るが構造 drift には載らない（gate しない）。"""
    ctx = _write(tmp_path, "CONTEXT.md", _SEEDED)
    report = gd.check_glossary(str(ctx), [])
    assert report.unverified_terms == ["Foo"]
    assert report.has_unverified()
    assert not report.has_drift()  # advisory であって gate しない
    assert report.missing_first_seen == []  # マーカーは非空なので初出欠落にしない


def test_unverified_counts_as_documented_for_undefined(tmp_path):
    """UNVERIFIED でも用語集にある語は undefined（未登録）に二重計上しない。"""
    ctx = _write(tmp_path, "CONTEXT.md", _SEEDED)
    src = _write(tmp_path, "SPEC.md", "Foo と Bar を使う。")
    report = gd.check_glossary(str(ctx), [str(src)])
    assert "Foo" not in report.undefined_terms
    assert "Bar" not in report.undefined_terms


def test_write_context_seed_non_destructive(tmp_path):
    """既存 CONTEXT.md は overwrite=False で上書きしない（silent wipe 防止）。"""
    import pytest

    ctx = str(tmp_path / "CONTEXT.md")
    gd.write_context_seed(ctx, [("Foo", "意味A")], project_name="proj")
    with pytest.raises(FileExistsError):
        gd.write_context_seed(ctx, [("Foo", "別意味")], project_name="proj")


def test_write_context_seed_roundtrip(tmp_path):
    """seed は UNVERIFIED マーカー付きで書かれ、再パースで unverified として読める。"""
    ctx = str(tmp_path / "CONTEXT.md")
    gd.write_context_seed(
        ctx, [("Foo", "意味A"), ("Bar", "意味B")], project_name="proj"
    )
    report = gd.check_glossary(ctx, [])
    assert {e.term for e in report.entries} == {"Foo", "Bar"}
    assert sorted(report.unverified_terms) == ["Bar", "Foo"]
    assert not report.has_drift()


def test_write_context_seed_escapes_pipe(tmp_path):
    """意味に | が含まれてもテーブルが壊れない（malformed にならない）。"""
    ctx = str(tmp_path / "CONTEXT.md")
    gd.write_context_seed(ctx, [("Foo", "a | b の両方")], project_name="proj")
    report = gd.check_glossary(ctx, [])
    assert report.malformed_lines == []
    assert report.entries[0].term == "Foo"


def test_audit_section_surfaces_unverified(tmp_path):
    """seed 直後の CONTEXT.md は audit section で未検証 advisory を出す。"""
    from lib.audit.sections import build_glossary_drift_section

    _write(tmp_path, "CONTEXT.md", _SEEDED)
    section = build_glossary_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "未検証" in body
    assert "Foo" in body


# ---------- #353⑫: jargon denylist テスト ----------

_AWS_TECH_NOISE = """# SPEC

We use ARN to identify resources. The CDK stack uses SNS, SQS, S3, and IAM.
VPC settings are defined in the CDK config. The API returns JSON over HTTP.
AWS credentials use IAM roles. Lambda functions invoke via API Gateway.
"""

_PROJECT_JARGON = """# SPEC

BES is a backward error scoring method.
MemTrace is used for attribution diagnosis.
SkillRM is the skill-axis reward model.
"""


def test_aws_tech_abbreviations_excluded_by_denylist(tmp_path):
    """#353⑫: AWS・汎用技術略語（ARN, CDK, SNS, SQS, S3, IAM, VPC 等）が jargon 候補から除外される。

    これらは 46件ものノイズを出していた。PJ 固有語ではなく汎用技術略語のため
    denylist でフィルタする。
    """
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", _AWS_TECH_NOISE)
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])

    # これらはすべて denylist で除外されるべき
    aws_tech_terms = ["ARN", "CDK", "SNS", "SQS", "S3", "IAM", "VPC", "JSON", "HTTP", "AWS", "API"]
    for term in aws_tech_terms:
        assert term not in undefined, f"{term} は汎用技術略語なので jargon 候補に出てはいけない"


def test_project_jargon_not_excluded_by_denylist(tmp_path):
    """#353⑫: PJ 固有語（BES, MemTrace, SkillRM 等）は denylist に含まれず候補に残る。"""
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", _PROJECT_JARGON)
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])

    # BES, MemTrace, SkillRM は PJ 固有語で残るはず
    assert "BES" not in undefined  # すでに _VALID 用語集に登録済み
    assert "MemTrace" not in undefined  # すでに _VALID 用語集に登録済み
    assert "SkillRM" in undefined  # 未登録の PJ 固有語は出る


def test_denylist_is_explicit_constant(tmp_path):
    """#353⑫: denylist は DEFAULT_STOPLIST として拡張可能な定数で存在する。"""
    # DEFAULT_STOPLIST に新規 denylist 語が含まれていることを確認
    new_denylist_terms = ["ARN", "CDK", "SNS", "SQS", "S3", "IAM", "VPC"]
    for term in new_denylist_terms:
        assert term in gd.DEFAULT_STOPLIST, (
            f"{term} は DEFAULT_STOPLIST に追加されるべき汎用技術略語"
        )


def test_lambda_and_gateway_excluded(tmp_path):
    """Lambda / Gateway 等もノイズになりうる場合 denylist で対応する。"""
    ctx = _write(tmp_path, "CONTEXT.md", _VALID)
    src = _write(tmp_path, "SPEC.md", "ARN and CDK and SNS are common in AWS.")
    undefined = gd.find_undefined_terms(gd.parse_glossary(str(ctx))[0], [str(src)])
    # ARN, CDK, SNS は除外済み
    assert "ARN" not in undefined
    assert "CDK" not in undefined
    assert "SNS" not in undefined
