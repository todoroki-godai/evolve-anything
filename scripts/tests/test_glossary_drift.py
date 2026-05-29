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
