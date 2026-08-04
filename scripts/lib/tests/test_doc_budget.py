"""doc_budget（hot ドキュメントの byte/セクション/ポインタ予算検査）のテスト（#319）。

決定論・LLM 非依存。tmp_path に疑似リポジトリツリー（SPEC.md/CLAUDE.md/spec/**.md）を作って
静的検査する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import doc_budget  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def tiny_file_budgets(monkeypatch: pytest.MonkeyPatch):
    """file 予算の healthy を極小にして、セクション検査そのものを単離して検証する。

    セクション検査は「healthy 超過ファイル」を入口にする（`_section_budget_scope`）。
    実際の healthy（SPEC.md 20KB）まで fixture を膨らませると、比率ベースの閾値
    （全体の 40% 超）を満たすセクションを同時に作れず（5KB は 20KB の 25%）、
    2 つの閾値を1つの fixture で表現できなくなる。ゲート自体は
    test_section_budget_skips_files_within_healthy が実閾値で検証する。
    """
    tiny = doc_budget.FileBudget(must_bytes=64 * 1024, healthy_bytes=64)
    monkeypatch.setattr(doc_budget, "SPEC_MD_BUDGET", tiny)
    monkeypatch.setattr(doc_budget, "CLAUDE_MD_BUDGET", tiny)
    monkeypatch.setattr(doc_budget, "SINGLE_MD_BUDGET", tiny)
    return tiny


# --- (a) ファイル単位の byte 予算 --------------------------------------------


def test_file_budgets_empty_when_all_within_healthy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "x" * 100)
    _write(root / "CLAUDE.md", "x" * 100)
    assert doc_budget.check_file_budgets(root) == []


def test_spec_md_healthy_exceeded_not_must(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    # healthy(20KB) 超・MUST(35KB) 未満
    _write(root / "SPEC.md", "x" * (25 * 1024))
    findings = doc_budget.check_file_budgets(root)
    assert len(findings) == 1
    assert findings[0].path == "SPEC.md"
    assert findings[0].severity == "healthy"


def test_spec_md_must_exceeded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "x" * (36 * 1024))
    findings = doc_budget.check_file_budgets(root)
    assert len(findings) == 1
    assert findings[0].severity == "must"


def test_claude_md_budget_thresholds(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", "x" * (45 * 1024))  # healthy(40KB)超・MUST(60KB)未満
    findings = doc_budget.check_file_budgets(root)
    assert len(findings) == 1
    assert findings[0].path == "CLAUDE.md"
    assert findings[0].severity == "healthy"


def test_spec_dir_md_uses_single_file_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "spec" / "components.md", "x" * (60 * 1024))  # healthy(50KB)超・MUST(100KB)未満
    findings = doc_budget.check_file_budgets(root)
    assert len(findings) == 1
    assert findings[0].path == "spec/components.md"
    assert findings[0].severity == "healthy"
    assert findings[0].must_bytes == 100 * 1024


def test_missing_files_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert doc_budget.check_file_budgets(root) == []


# --- (b) セクション単位の byte 予算 -------------------------------------------


def test_section_budget_flags_large_section_by_abs_threshold(tmp_path: Path, tiny_file_budgets) -> None:
    root = tmp_path / "repo"
    big_section = "z" * (9 * 1024)  # 8KB 絶対閾値超
    _write(
        root / "SPEC.md",
        f"# Title\n\n## Small\nabc\n\n## Big\n{big_section}\n",
    )
    findings = doc_budget.check_section_budgets(root)
    headings = {f.heading for f in findings}
    assert "Big" in headings
    assert "Small" not in headings


def test_section_budget_flags_by_pct_and_min_bytes(tmp_path: Path, tiny_file_budgets) -> None:
    root = tmp_path / "repo"
    # 全体の50%を超え、かつ 4KB 超（8KB 絶対閾値未満）のセクションを作る。
    dominant = "y" * (5 * 1024)
    _write(
        root / "SPEC.md",
        f"# Title\n\n## Dominant\n{dominant}\n\n## Rest\nsmall\n",
    )
    findings = doc_budget.check_section_budgets(root)
    headings = {f.heading for f in findings}
    assert "Dominant" in headings
    assert "Rest" not in headings


def test_section_budget_no_findings_when_all_small(tmp_path: Path, tiny_file_budgets) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "# Title\n\n## A\nabc\n\n## B\ndef\n")
    assert doc_budget.check_section_budgets(root) == []


def test_section_budget_scans_spec_dir_files(tmp_path: Path, tiny_file_budgets) -> None:
    root = tmp_path / "repo"
    big_section = "w" * (9 * 1024)
    _write(root / "spec" / "architecture.md", f"# T\n\n## Huge\n{big_section}\n")
    findings = doc_budget.check_section_budgets(root)
    assert any(f.file == "spec/architecture.md" and f.heading == "Huge" for f in findings)


def test_section_budget_does_not_confuse_h3_with_h2(tmp_path: Path, tiny_file_budgets) -> None:
    """`### ` 見出しは `## ` セクションの一部として計上され、独立セクション扱いされない。"""
    root = tmp_path / "repo"
    big = "v" * (9 * 1024)
    _write(root / "SPEC.md", f"# Title\n\n## Parent\n### Child\n{big}\n")
    findings = doc_budget.check_section_budgets(root)
    headings = {f.heading for f in findings}
    assert "Parent" in headings
    assert "Child" not in headings


def test_section_budget_skips_files_within_healthy(tmp_path: Path) -> None:
    """healthy 内のファイルは、内部に大きなセクションがあってもセクション検査の対象外。

    実閾値（monkeypatch なし）で検証する。セクション粒度が要るのは「ファイルが予算に
    触れた時にどこが太っているかを指す」ためで、健全サイズのファイル内の比率は無害。
    全ファイルを対象にすると実データで恒久表示 6 件の advisory ノイズになる（#319）。
    """
    root = tmp_path / "repo"
    big = "z" * (9 * 1024)  # 8KB 絶対閾値は超えるが…
    _write(root / "SPEC.md", f"# Title\n\n## Big\n{big}\n")  # …ファイルは healthy(20KB) 内
    assert doc_budget.check_file_budgets(root) == []
    assert doc_budget.check_section_budgets(root) == []


def test_section_budget_flags_once_file_exceeds_healthy(tmp_path: Path) -> None:
    """ファイルが healthy を超えた瞬間、肥大セクションが surface する（#318 の検出力）。"""
    root = tmp_path / "repo"
    bloat = "z" * (12 * 1024)
    rest = "a" * (10 * 1024)
    _write(root / "SPEC.md", f"# Title\n\n## Bloat\n{bloat}\n\n## Rest\n{rest}\n")
    assert doc_budget.check_file_budgets(root)  # healthy(20KB) 超
    headings = {f.heading for f in doc_budget.check_section_budgets(root)}
    assert "Bloat" in headings


# --- (c) ポインタ実在の突合 ----------------------------------------------------


def test_pointer_refs_ok_when_target_exists(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "# Architecture\n\n## Foo\nbody\n")
    _write(root / "SPEC.md", "詳細は [spec/architecture.md](spec/architecture.md) を参照。\n")
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "詳細は [spec/ghost.md](spec/ghost.md) を参照。\n")
    findings = doc_budget.check_pointer_refs(root)
    assert len(findings) == 1
    assert findings[0].kind == "missing_file"
    assert findings[0].raw_target == "spec/ghost.md"
    assert findings[0].source_file == "SPEC.md"


def test_pointer_refs_anchor_exists(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "# Architecture\n\n## Observe 詳細\nbody\n")
    _write(
        root / "SPEC.md",
        "詳細は [spec/architecture.md](spec/architecture.md#observe-詳細) を参照。\n",
    )
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_missing_anchor(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "# Architecture\n\n## Real Section\nbody\n")
    _write(
        root / "SPEC.md",
        "詳細は [spec/architecture.md](spec/architecture.md#does-not-exist) を参照。\n",
    )
    findings = doc_budget.check_pointer_refs(root)
    assert len(findings) == 1
    assert findings[0].kind == "missing_anchor"


def test_pointer_refs_resolves_root_relative_links(tmp_path: Path) -> None:
    """GitHub 慣習のルート相対リンク `/spec/x.md` を repo_root 起点で解決する。

    `src.parent / "/spec/x.md"` は Path の仕様で左辺が捨てられ、ホストの絶対パスを
    見に行くため実在するリンクを missing_file と誤検出する（codex cold-read 指摘）。
    """
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "# Architecture\n\n## Foo\nbody\n")
    _write(root / "SPEC.md", "詳細は [architecture](/spec/architecture.md) を参照。\n")
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_root_relative_missing_is_still_detected(tmp_path: Path) -> None:
    """ルート相対でも実在しなければ missing_file として検出する（沈黙で潰さない）。"""
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "詳細は [ghost](/spec/ghost.md) を参照。\n")
    findings = doc_budget.check_pointer_refs(root)
    assert len(findings) == 1
    assert findings[0].kind == "missing_file"


def test_pointer_refs_root_relative_anchor_is_checked(tmp_path: Path) -> None:
    """ルート相対リンクでもアンカー突合が働く。"""
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "# Architecture\n\n## Real Section\nbody\n")
    _write(root / "SPEC.md", "[x](/spec/architecture.md#does-not-exist)\n")
    findings = doc_budget.check_pointer_refs(root)
    assert len(findings) == 1
    assert findings[0].kind == "missing_anchor"


def test_pointer_refs_skips_external_urls(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(
        root / "SPEC.md",
        "外部参照 [example](https://example.com/ghost) と [mail](mailto:a@example.com)。\n",
    )
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_skips_links_inside_fenced_code_blocks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(
        root / "SPEC.md",
        "```\n[example](spec/ghost.md)\n```\n",
    )
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_recognizes_summary_pseudo_headings(tmp_path: Path) -> None:
    """`<details><summary>` を疑似見出しとして扱い、実 README 慣習での偽陽性を避ける。"""
    root = tmp_path / "repo"
    _write(
        root / "README.ja.md",
        "<details>\n<summary><strong>適応度関数</strong></summary>\n\nbody\n\n</details>\n",
    )
    _write(root / "CLAUDE.md", "詳細は [README.ja.md](README.ja.md#適応度関数) を参照。\n")
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_directory_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "docs" / "decisions").mkdir(parents=True)
    _write(root / "SPEC.md", "詳細は [docs/decisions/](docs/decisions/) を参照。\n")
    assert doc_budget.check_pointer_refs(root) == []


def test_pointer_refs_missing_directory_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root / "SPEC.md", "詳細は [docs/decisions/](docs/decisions/) を参照。\n")
    findings = doc_budget.check_pointer_refs(root)
    assert len(findings) == 1
    assert findings[0].kind == "missing_file"


def test_pointer_refs_ignores_non_scope_files(tmp_path: Path) -> None:
    """SPEC.md / CLAUDE.md 以外（例: spec/architecture.md 内のリンク）は対象外。"""
    root = tmp_path / "repo"
    _write(root / "spec" / "architecture.md", "参照 [ghost](spec/ghost.md)\n")
    assert doc_budget.check_pointer_refs(root) == []


# --- 集約レポート ---------------------------------------------------------------


def test_check_doc_budget_not_applicable_when_no_hot_docs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    report = doc_budget.check_doc_budget(root)
    assert report.applicable is False
    assert report.has_findings() is False


def test_check_doc_budget_applicable_and_clean(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "# Title\n\n## A\nabc\n")
    report = doc_budget.check_doc_budget(root)
    assert report.applicable is True
    assert report.has_findings() is False


def test_check_doc_budget_aggregates_all_three_checks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "x" * (36 * 1024) + "\n[ghost](ghost.md)\n")
    report = doc_budget.check_doc_budget(root)
    assert report.has_findings() is True
    assert any(f.severity == "must" for f in report.file_findings)
    assert any(f.kind == "missing_file" for f in report.pointer_findings)
