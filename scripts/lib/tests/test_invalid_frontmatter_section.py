"""invalid_frontmatter 検出器 + observability セクション（#167）のテスト。

すべて決定論・LLM 非依存。tmp_path に `.claude/skills/**/SKILL.md` を組んで検出関数へ
project_dir を渡すため、実 `~/.claude` を一切読まない。

「CC 発火不能」を直接 surface する検出器。atlas-breeaders の 3 スキルが frontmatter の
YAML 不正（description の非引用スカラーに `トリガー: ` colon-space）で yaml.safe_load が
ScannerError になり自動発火していなかった実バグの再発防止（#167）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.sections_invalid_frontmatter import (  # noqa: E402
    build_invalid_frontmatter_section,
    detect_invalid_frontmatter,
)


def _write_skill(skills_dir: Path, name: str, content: str) -> Path:
    """`.claude/skills/<name>/SKILL.md` を content で書いて path を返す。"""
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(content, encoding="utf-8")
    return md


_VALID = "---\nname: {name}\ndescription: valid skill\n---\n\nbody\n"
# atlas-breeaders の実際の壊れ方（description に `トリガー: ` colon-space）
_BROKEN = (
    "---\nname: {name}\n"
    "description: 血統管理スキル。トリガー: (1) 交配計画 (2) 血統照会\n"
    "---\n\nbody\n"
)


def _skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".claude" / "skills"
    d.mkdir(parents=True)
    return d


# --------------------------------------------------------------------------
# detect_invalid_frontmatter
# --------------------------------------------------------------------------


def test_detect_lists_only_invalid(tmp_path):
    """invalid 1 + valid 1 → invalid のみ列挙。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "good", _VALID.format(name="good"))
    _write_skill(skills, "atlas-breed", _BROKEN.format(name="atlas-breed"))

    found = detect_invalid_frontmatter(tmp_path)
    assert len(found) == 1
    entry = found[0]
    assert entry["skill_name"] == "atlas-breed"
    assert entry["skill_path"].endswith("atlas-breed/SKILL.md")
    assert entry["error"] and "\n" not in entry["error"]


def test_detect_all_valid_is_empty(tmp_path):
    """全 valid → 空リスト。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "a", _VALID.format(name="a"))
    _write_skill(skills, "b", _VALID.format(name="b"))
    assert detect_invalid_frontmatter(tmp_path) == []


def test_detect_no_skills_dir(tmp_path):
    """skills ディレクトリが無い → 空リスト。"""
    assert detect_invalid_frontmatter(tmp_path) == []


def test_detect_excludes_archived(tmp_path):
    """archived/backup 配下の壊れ frontmatter は対象外（is_excluded_skill_path）。"""
    skills = _skills_dir(tmp_path)
    arch = skills / "_archived"
    arch.mkdir()
    _write_skill(arch, "old", _BROKEN.format(name="old"))
    assert detect_invalid_frontmatter(tmp_path) == []


def test_detect_sorted(tmp_path):
    """複数 invalid はパスでソートされる（決定論出力）。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "zeta", _BROKEN.format(name="zeta"))
    _write_skill(skills, "alpha", _BROKEN.format(name="alpha"))
    found = detect_invalid_frontmatter(tmp_path)
    names = [e["skill_name"] for e in found]
    assert names == ["alpha", "zeta"]


# --------------------------------------------------------------------------
# build_invalid_frontmatter_section
# --------------------------------------------------------------------------


def test_section_lists_invalid(tmp_path):
    """invalid 1 + valid 1 → section が invalid のみ列挙、⚠ 付き。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "good", _VALID.format(name="good"))
    _write_skill(skills, "atlas-breed", _BROKEN.format(name="atlas-breed"))

    section = build_invalid_frontmatter_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert section[0].startswith("## ")
    assert section[-1] == ""
    assert "⚠" in body
    assert "atlas-breed" in body
    assert "good" not in body


def test_section_silent_when_all_valid(tmp_path):
    """全 valid → section は None（沈黙・silence != evaluated ではなく発火不能は clean 沈黙）。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "a", _VALID.format(name="a"))
    assert build_invalid_frontmatter_section(tmp_path) is None


def test_section_silent_when_no_skills_dir(tmp_path):
    """skills ディレクトリが無い → None（沈黙）。"""
    assert build_invalid_frontmatter_section(tmp_path) is None


def test_section_evidence_has_name_path_error(tmp_path):
    """evidence 各行に skill 名 + 相対 path + 1行 error が含まれる。"""
    skills = _skills_dir(tmp_path)
    _write_skill(skills, "atlas-breed", _BROKEN.format(name="atlas-breed"))
    section = build_invalid_frontmatter_section(tmp_path)
    body = "\n".join(section)
    assert "atlas-breed" in body
    assert "SKILL.md" in body
    # yaml エラー本文の代表語が出る
    assert "mapping" in body
