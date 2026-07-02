"""MEMORY.md 索引孤児の決定論検出テスト（#127, advisory）。

MEMORY.md 本文の ``[text](file.md)`` リンク集合と ``memory/*.md`` の実ファイル集合を突合し、
索引に無い実ファイル（unindexed）・実体の無いリンク（stale リンク）を検出する。

検出関数 ``detect_index_orphans`` は memory dir を **引数で受ける**（実 ~/.claude を読まない）。
テストは tmp_path に fixture を組んで渡す。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import memory_index_orphan  # noqa: E402
from audit.sections_memory import build_memory_index_orphan_section  # noqa: E402
import memory_capability  # noqa: E402


def _mem(tmp_path: Path) -> Path:
    mem = tmp_path / "memory"
    mem.mkdir()
    return mem


def _write(mem: Path, name: str, body: str = "本文\n") -> None:
    (mem / name).write_text(body, encoding="utf-8")


# ── detect_index_orphans ──────────────────────────────────────────────


def test_no_index_returns_has_index_false(tmp_path):
    """MEMORY.md が無ければ has_index=False（索引が無いので検査対象外）。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.has_index is False
    assert report.has_findings is False


def test_all_indexed_no_findings(tmp_path):
    """索引リンクと実ファイルが完全一致なら findings なし。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    _write(mem, "bar.md")
    (mem / "MEMORY.md").write_text(
        "# index\n- [Foo](foo.md) — hook\n- [Bar](bar.md) — hook\n", encoding="utf-8"
    )
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.has_index is True
    assert report.has_findings is False
    assert report.unindexed_files == []
    assert report.indexed_missing == []


def test_unindexed_real_file_detected(tmp_path):
    """索引に無い実ファイルを unindexed_files に列挙する。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    _write(mem, "orphan.md")  # 索引未掲載
    (mem / "MEMORY.md").write_text("# index\n- [Foo](foo.md)\n", encoding="utf-8")
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.unindexed_files == ["orphan.md"]
    assert report.indexed_missing == []
    assert report.has_findings is True


def test_indexed_missing_detected(tmp_path):
    """実体の無い索引リンクを indexed_missing に列挙する。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    (mem / "MEMORY.md").write_text(
        "# index\n- [Foo](foo.md)\n- [Gone](gone.md)\n", encoding="utf-8"
    )
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.indexed_missing == ["gone.md"]
    assert report.unindexed_files == []


def test_memory_md_self_reference_ignored(tmp_path):
    """MEMORY.md 自身へのリンクは索引対象にも実体対象にも数えない。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    (mem / "MEMORY.md").write_text(
        "# index\n- [self](MEMORY.md)\n- [Foo](foo.md)\n", encoding="utf-8"
    )
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.has_findings is False


def test_relative_path_link_normalized_to_basename(tmp_path):
    """``[x](sub/foo.md)`` のような相対パスリンクも basename で突合する。"""
    mem = _mem(tmp_path)
    _write(mem, "foo.md")
    (mem / "MEMORY.md").write_text("# index\n- [Foo](./foo.md)\n", encoding="utf-8")
    report = memory_index_orphan.detect_index_orphans(mem)
    assert report.has_findings is False


# ── build_memory_index_orphan_section ─────────────────────────────────


def _project_with_memory(tmp_path: Path) -> Path:
    """resolve_cc_memory_dir 経由の実 memory dir を作る（HOME は conftest autouse で隔離済み）。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = memory_capability._resolve_memory_dir(project_dir)
    mem.mkdir(parents=True, exist_ok=True)
    return project_dir


def test_section_none_when_no_findings(tmp_path):
    """孤児が無ければ section は None（無ければ非表示）。"""
    project_dir = _project_with_memory(tmp_path)
    mem = memory_capability._resolve_memory_dir(project_dir)
    _write(mem, "foo.md")
    (mem / "MEMORY.md").write_text("- [Foo](foo.md)\n", encoding="utf-8")
    assert build_memory_index_orphan_section(project_dir) is None


def test_section_none_when_no_index(tmp_path):
    """MEMORY.md が無ければ section は None（検査対象外）。"""
    project_dir = _project_with_memory(tmp_path)
    mem = memory_capability._resolve_memory_dir(project_dir)
    _write(mem, "foo.md")
    assert build_memory_index_orphan_section(project_dir) is None


def test_section_lists_orphans(tmp_path):
    """孤児があれば section に unindexed / stale リンクが列挙される。"""
    project_dir = _project_with_memory(tmp_path)
    mem = memory_capability._resolve_memory_dir(project_dir)
    _write(mem, "foo.md")
    _write(mem, "orphan.md")
    (mem / "MEMORY.md").write_text(
        "# index\n- [Foo](foo.md)\n- [Gone](gone.md)\n", encoding="utf-8"
    )
    section = build_memory_index_orphan_section(project_dir)
    assert section is not None
    combined = "\n".join(section)
    assert "orphan.md" in combined
    assert "gone.md" in combined
