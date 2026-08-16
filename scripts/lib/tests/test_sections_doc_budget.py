"""Doc Budget observability セクションのテスト（#319）。

決定論ロジック自体は test_doc_budget.py でカバー済み。ここでは build_advisory_section への
配線（None 沈黙 / clean marker / ⚠・ℹ の evidence 表示）だけを検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.sections_doc_budget import build_doc_budget_section  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_section_none_when_no_hot_docs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert build_doc_budget_section(root) is None


def test_section_clean_marker_when_within_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "# Title\n\n## A\nabc\n")
    section = build_doc_budget_section(root)
    assert section is not None
    assert any("✓" in line for line in section)


def test_section_warns_on_must_exceeded_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "x" * (36 * 1024))
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "SPEC.md" in joined


def test_section_info_on_healthy_exceeded_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", "x" * (45 * 1024))
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "ℹ" in joined
    assert "CLAUDE.md" in joined


def test_section_shows_pointer_findings(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "SPEC.md", "詳細は [spec/ghost.md](spec/ghost.md) を参照。\n")
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "リンク先ファイル不在" in joined
    assert "spec/ghost.md" in joined


def test_section_shows_claude_md_contract_missing(tmp_path: Path) -> None:
    """CLAUDE.md 契約不変条件の欠落（#415）が同一 doc_budget セクションに相乗り表示される。"""
    root = tmp_path / "repo"
    text = (
        "# Title\n\n"
        "## 目指すユーザー体験（全機能の判断基準）\n"
        "到達状況の数値をこのファイルに書かない。\n"
    )
    _write(root / "CLAUDE.md", text)
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "CLAUDE.md 契約不変条件の欠落" in joined


def test_section_shows_must_stay_section_missing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "CLAUDE.md", "# Title\n\nno agent contract header, no compaction section.\n")
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "移設禁止セクション" in joined
    assert "Compaction Instructions" in joined


def test_section_shows_section_findings(tmp_path: Path) -> None:
    # セクション検査は healthy 超過ファイルを入口にするため、ファイル自体も healthy(20KB) を
    # 超えるサイズにする（#319 `_section_budget_scope`）。
    root = tmp_path / "repo"
    big = "z" * (12 * 1024)
    rest = "a" * (10 * 1024)
    _write(root / "SPEC.md", f"# Title\n\n## Small\n{rest}\n\n## Big\n{big}\n")
    section = build_doc_budget_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "Big" in joined
