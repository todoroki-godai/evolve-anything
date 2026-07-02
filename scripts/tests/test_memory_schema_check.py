"""auto-memory frontmatter スキーマ検証テスト（#128, advisory）。

``memory/*.md`` の frontmatter を解析し ``name``（kebab-case）/ ``description`` /
``metadata.type``（user|feedback|project|reference）が揃っているか検証する。

検出関数 ``detect_schema_violations`` は memory dir を **引数で受ける**（実 ~/.claude を
読まない）。MEMORY.md（索引ファイル）は frontmatter を持たない仕様なので検証対象外。
決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import memory_schema_check  # noqa: E402
from audit.sections_memory import build_memory_schema_section  # noqa: E402
import memory_capability  # noqa: E402


def _mem(tmp_path: Path) -> Path:
    mem = tmp_path / "memory"
    mem.mkdir()
    return mem


def _write_fm(mem: Path, name: str, fm_lines: list, body: str = "本文") -> None:
    fm = "\n".join(fm_lines)
    (mem / name).write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")


_VALID_FM = [
    "name: good-slug",
    "description: 一行の説明",
    "metadata:",
    "  type: user",
]


# ── detect_schema_violations ──────────────────────────────────────────


def test_valid_frontmatter_no_violations(tmp_path):
    """全フィールド揃った正しい frontmatter は違反なし。"""
    mem = _mem(tmp_path)
    _write_fm(mem, "good.md", _VALID_FM)
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is False


def test_index_file_excluded(tmp_path):
    """MEMORY.md は frontmatter なしでも検証対象外（違反にしない）。"""
    mem = _mem(tmp_path)
    (mem / "MEMORY.md").write_text("# index（frontmatter なし）\n", encoding="utf-8")
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is False


def test_missing_frontmatter_flagged(tmp_path):
    """frontmatter が全く無い memory ファイルは違反。"""
    mem = _mem(tmp_path)
    (mem / "bare.md").write_text("frontmatter なしの本文\n", encoding="utf-8")
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is True
    assert report.violations[0].filename == "bare.md"


def test_missing_fields_flagged(tmp_path):
    """description / metadata.type 欠落を検出する。"""
    mem = _mem(tmp_path)
    _write_fm(mem, "partial.md", ["name: some-name"])
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is True
    issues = report.violations[0].issues
    joined = " ".join(issues)
    assert "description" in joined
    assert "metadata.type" in joined


def test_non_kebab_name_flagged(tmp_path):
    """name の kebab-case 逸脱（大文字・アンダースコア）を検出する。"""
    mem = _mem(tmp_path)
    _write_fm(
        mem, "bad_name.md",
        ["name: Bad_Name", "description: x", "metadata:", "  type: user"],
    )
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is True
    assert any("kebab" in i for i in report.violations[0].issues)


def test_invalid_type_flagged(tmp_path):
    """metadata.type が許可外の値なら違反。"""
    mem = _mem(tmp_path)
    _write_fm(
        mem, "badtype.md",
        ["name: ok-name", "description: x", "metadata:", "  type: bogus"],
    )
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is True
    assert any("type" in i for i in report.violations[0].issues)


def test_valid_types_all_accepted(tmp_path):
    """user/feedback/project/reference は全て許可。"""
    mem = _mem(tmp_path)
    for i, t in enumerate(("user", "feedback", "project", "reference")):
        _write_fm(
            mem, f"f{i}.md",
            [f"name: slug-{i}", "description: x", "metadata:", f"  type: {t}"],
        )
    report = memory_schema_check.detect_schema_violations(mem)
    assert report.has_findings is False


# ── build_memory_schema_section ───────────────────────────────────────


def _project_with_memory(tmp_path: Path) -> Path:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mem = memory_capability._resolve_memory_dir(project_dir)
    mem.mkdir(parents=True, exist_ok=True)
    return project_dir


def test_section_none_when_no_violations(tmp_path):
    """違反が無ければ section は None（無ければ非表示）。"""
    project_dir = _project_with_memory(tmp_path)
    mem = memory_capability._resolve_memory_dir(project_dir)
    _write_fm(mem, "good.md", _VALID_FM)
    assert build_memory_schema_section(project_dir) is None


def test_section_none_when_no_memory(tmp_path):
    """memory 実体が無ければ section は None。"""
    project_dir = _project_with_memory(tmp_path)
    assert build_memory_schema_section(project_dir) is None


def test_section_lists_violations(tmp_path):
    """違反があれば section にファイル名と違反内容が列挙される。"""
    project_dir = _project_with_memory(tmp_path)
    mem = memory_capability._resolve_memory_dir(project_dir)
    _write_fm(mem, "partial.md", ["name: Bad_Name"])
    section = build_memory_schema_section(project_dir)
    assert section is not None
    combined = "\n".join(section)
    assert "partial.md" in combined
