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


def test_audit_section_none_without_context_md(tmp_path):
    """CONTEXT.md が無い PJ では audit section は None（spec-keeper init 前は対象外）。"""
    from lib.audit.sections import build_glossary_drift_section

    assert build_glossary_drift_section(tmp_path) is None


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
