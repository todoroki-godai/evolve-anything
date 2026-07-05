"""artifact 衛生検出器（#124 #125 #126 #129）のテスト。

すべて決定論・LLM 非依存。tmp_path に疑似ツリー（グローバル home / .claude/skills）を
組んで検出関数へ **引数で渡す** ため、実 `~/.claude` を一切読まない。

- #124: グローバル CLAUDE.md の未存在 / 空チェック
- #125: SKILL.md 欠落ディレクトリ検出（skill-creator の workspace 残骸）
- #126: skills 配下の残置バックアップファイル（*.md.bak / *.backup / *.orig）
- #129: skill name の跨 scope 重複検出（symlink wrapper は誤検知から除外）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import json  # noqa: E402

from audit.sections_artifacts import (  # noqa: E402
    build_backup_files_section,
    build_duplicate_skill_names_section,
    build_global_claude_md_section,
    build_global_hook_plugin_dup_section,
    build_missing_skill_md_section,
    detect_backup_files,
    detect_duplicate_skill_names,
    detect_global_claude_md,
    detect_global_hook_plugin_dup,
    detect_missing_skill_md,
    skill_roots,
)


def _write_hooks_file(path: Path, hooks: dict) -> Path:
    """settings.json / hooks.json 形式（{"hooks": {<event>: [...]}}）を書いて返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return path


def _matcher_group(command: str) -> dict:
    """1 コマンドの matcher-group（settings/hooks の hooks[event] 要素）を返す。"""
    return {"matcher": "", "hooks": [{"type": "command", "command": command}]}


def _write_skill(skill_dir: Path, name: str) -> Path:
    """SKILL.md（frontmatter name 付き）を書いて path を返す。"""
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(f"---\nname: {name}\ndescription: x\n---\n\nbody\n", encoding="utf-8")
    return md


# --------------------------------------------------------------------------
# #124 グローバル CLAUDE.md
# --------------------------------------------------------------------------


def test_global_claude_md_missing(tmp_path: Path) -> None:
    report = detect_global_claude_md(home=tmp_path)
    assert report.exists is False
    assert report.healthy is False


def test_global_claude_md_empty(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("   \n\n  ", encoding="utf-8")
    report = detect_global_claude_md(home=tmp_path)
    assert report.exists is True
    assert report.is_empty is True
    assert report.healthy is False


def test_global_claude_md_has_content(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("# 指示\n本文", encoding="utf-8")
    report = detect_global_claude_md(home=tmp_path)
    assert report.healthy is True


def test_global_claude_md_section_silent_when_healthy(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("# 指示\n本文", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # project_dir は無関係（グローバル home を見る）
    assert build_global_claude_md_section(tmp_path / "proj") is None


def test_global_claude_md_section_warns_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    section = build_global_claude_md_section(tmp_path / "proj")
    assert section is not None
    joined = "\n".join(section)
    assert section[0].startswith("## ")
    assert section[-1] == ""
    assert "⚠" in joined


# --------------------------------------------------------------------------
# #125 SKILL.md 欠落ディレクトリ
# --------------------------------------------------------------------------


def _skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj" / ".claude" / "skills"
    root.mkdir(parents=True)
    return root


def test_missing_skill_md_detects_workspace_residue(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "good-skill", "good-skill")
    # workspace 残骸: SKILL.md が無い
    (root / "good-skill-workspace").mkdir()
    (root / "good-skill-workspace" / "notes.md").write_text("x", encoding="utf-8")

    missing = detect_missing_skill_md([root])
    names = [d.name for d in missing]
    assert "good-skill-workspace" in names
    assert "good-skill" not in names


def test_missing_skill_md_ignores_excluded_dirs(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    (root / ".archive").mkdir()
    (root / ".archive" / "old.md").write_text("x", encoding="utf-8")
    (root / "_archived").mkdir()
    missing = detect_missing_skill_md([root])
    names = [d.name for d in missing]
    assert ".archive" not in names
    assert "_archived" not in names


def test_missing_skill_md_treats_nested_skill_as_present(tmp_path: Path) -> None:
    """SKILL.md を配下に持つ（ネストした）ディレクトリは欠落扱いしない。"""
    root = _skills_root(tmp_path)
    _write_skill(root / "group" / "nested-skill", "nested-skill")
    missing = detect_missing_skill_md([root])
    assert [d.name for d in missing] == []


def test_missing_skill_md_section_clean(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "good-skill", "good-skill")
    section = build_missing_skill_md_section(tmp_path / "proj")
    assert section is not None
    assert any("✓" in line for line in section)


def test_missing_skill_md_section_warns_with_evidence(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    (root / "ghost-workspace").mkdir()
    section = build_missing_skill_md_section(tmp_path / "proj")
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "ghost-workspace" in joined


def test_missing_skill_md_section_silent_when_no_skills_dir(tmp_path: Path) -> None:
    # .claude/skills が無い PJ は非該当 → None
    assert build_missing_skill_md_section(tmp_path / "empty") is None


# --------------------------------------------------------------------------
# #126 残置バックアップファイル
# --------------------------------------------------------------------------


def test_backup_files_detected(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "s", "s")
    (root / "s" / "SKILL.md.bak").write_text("old", encoding="utf-8")
    (root / "s" / "config.backup").write_text("old", encoding="utf-8")
    (root / "s" / "notes.orig").write_text("old", encoding="utf-8")
    (root / "s" / "keep.md").write_text("current", encoding="utf-8")

    found = detect_backup_files([root])
    names = {f.name for f in found}
    assert names == {"SKILL.md.bak", "config.backup", "notes.orig"}


def test_backup_files_none_when_clean(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "s", "s")
    assert detect_backup_files([root]) == []


def test_backup_files_section_warns(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "s", "s")
    (root / "s" / "SKILL.md.bak").write_text("old", encoding="utf-8")
    section = build_backup_files_section(tmp_path / "proj")
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "SKILL.md.bak" in joined


def test_backup_files_section_clean(tmp_path: Path) -> None:
    root = _skills_root(tmp_path)
    _write_skill(root / "s", "s")
    section = build_backup_files_section(tmp_path / "proj")
    assert section is not None
    assert any("✓" in line for line in section)


# --------------------------------------------------------------------------
# #129 skill name 跨 scope 重複
# --------------------------------------------------------------------------


def test_duplicate_skill_names_detected(tmp_path: Path) -> None:
    a = _write_skill(tmp_path / "proj" / ".claude" / "skills" / "dup", "shared")
    b = _write_skill(tmp_path / "home" / ".claude" / "skills" / "dup2", "shared")
    unique = _write_skill(tmp_path / "proj" / ".claude" / "skills" / "solo", "solo")

    groups = detect_duplicate_skill_names([a, b, unique])
    assert len(groups) == 1
    g = groups[0]
    assert g.name == "shared"
    assert set(g.paths) == {a, b}


def test_duplicate_skill_names_none_when_unique(tmp_path: Path) -> None:
    a = _write_skill(tmp_path / "x" / "a", "alpha")
    b = _write_skill(tmp_path / "x" / "b", "beta")
    assert detect_duplicate_skill_names([a, b]) == []


def test_duplicate_skill_names_symlink_wrapper_excluded(tmp_path: Path) -> None:
    """symlink wrapper（_gstack-command 等）による重複は誤検知にしない。"""
    real = _write_skill(tmp_path / "skills" / "real", "gstack-cmd")
    # symlink wrapper: 別ディレクトリだが同じ SKILL.md を指す symlink
    wrapper_dir = tmp_path / "skills" / "_gstack-command"
    os.symlink(tmp_path / "skills" / "real", wrapper_dir)
    link_md = wrapper_dir / "SKILL.md"

    groups = detect_duplicate_skill_names([real, link_md])
    # 実体は 1 つだけ → 跨 scope 重複ではない
    assert groups == []


def test_duplicate_skill_names_real_dup_with_symlink_label(tmp_path: Path) -> None:
    """実体 2 つ + symlink 1 つ → 発火し symlink は区別ラベルに入る。"""
    a = _write_skill(tmp_path / "s1" / "dup", "shared")
    b = _write_skill(tmp_path / "s2" / "dup", "shared")
    wrapper_dir = tmp_path / "s3" / "_gstack-command"
    wrapper_dir.parent.mkdir(parents=True)
    os.symlink(tmp_path / "s1" / "dup", wrapper_dir)
    link_md = wrapper_dir / "SKILL.md"

    groups = detect_duplicate_skill_names([a, b, link_md])
    assert len(groups) == 1
    g = groups[0]
    assert set(g.paths) == {a, b}
    assert g.symlink_paths == [link_md]


def test_duplicate_skill_names_section_silent_when_no_skills(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "emptyhome"))
    assert build_duplicate_skill_names_section(tmp_path / "empty-proj") is None


# --------------------------------------------------------------------------
# skill_roots helper
# --------------------------------------------------------------------------


def test_skill_roots_includes_plugin_layout(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".claude-plugin").mkdir()
    (proj / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (proj / "skills").mkdir()
    roots = skill_roots(proj)
    assert (proj / ".claude" / "skills") in roots
    assert (proj / "skills") in roots


def test_skill_roots_excludes_plugin_layout_without_manifest(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / "skills").mkdir()  # plugin.json 無し → plugin レイアウトとして扱わない
    roots = skill_roots(proj)
    assert (proj / "skills") not in roots


# --------------------------------------------------------------------------
# #155 グローバル hook × プラグイン hook 残骸重複
# --------------------------------------------------------------------------


def _write_global_settings(tmp_path: Path, hooks: dict) -> Path:
    return _write_hooks_file(tmp_path / ".claude" / "settings.json", hooks)


def test_global_hook_plugin_dup_same_event_normalized_match(tmp_path: Path) -> None:
    """(a) 同一 Stop で ハイフン版グローバル vs アンダースコア版プラグイン → 1 件検出。"""
    _write_global_settings(
        tmp_path,
        {"Stop": [_matcher_group('python3 "~/.claude/hooks/record-verbosity.py"')]},
    )
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    dups = detect_global_hook_plugin_dup(home=tmp_path, plugin_hooks_path=plugin)
    assert len(dups) == 1
    d = dups[0]
    assert d.event == "Stop"
    assert d.normalized == "recordverbosity"
    assert "record-verbosity.py" in d.global_command
    assert "record_verbosity.py" in d.plugin_command


def test_global_hook_plugin_dup_different_event_no_match(tmp_path: Path) -> None:
    """(b) 同一 basename だが別イベント（global PreToolUse / plugin Stop）→ 0 件。"""
    _write_global_settings(
        tmp_path,
        {"PreToolUse": [_matcher_group('python3 "~/.claude/hooks/record-verbosity.py"')]},
    )
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    assert detect_global_hook_plugin_dup(home=tmp_path, plugin_hooks_path=plugin) == []


def test_global_hook_plugin_dup_no_intersection(tmp_path: Path) -> None:
    """(c) 交差なし（別 script）→ 空。"""
    _write_global_settings(
        tmp_path,
        {"Stop": [_matcher_group('python3 "~/.claude/hooks/some-other.py"')]},
    )
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    assert detect_global_hook_plugin_dup(home=tmp_path, plugin_hooks_path=plugin) == []


def test_global_hook_plugin_dup_missing_settings(tmp_path: Path) -> None:
    """settings.json 未存在 → 空（握り確認）。"""
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    assert detect_global_hook_plugin_dup(home=tmp_path, plugin_hooks_path=plugin) == []


def test_global_hook_plugin_dup_broken_json(tmp_path: Path) -> None:
    """settings.json 壊れ JSON → 空（握り確認、advisory を壊さない）。"""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ not valid json ", encoding="utf-8")
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    assert detect_global_hook_plugin_dup(home=tmp_path, plugin_hooks_path=plugin) == []


def test_global_hook_plugin_dup_section_silent_when_clean(tmp_path: Path, monkeypatch) -> None:
    """(d) 残骸ゼロ → None（沈黙）。"""
    _write_global_settings(
        tmp_path,
        {"Stop": [_matcher_group('python3 "~/.claude/hooks/some-other.py"')]},
    )
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from audit import sections_artifacts

    monkeypatch.setattr(
        sections_artifacts,
        "_default_plugin_hooks_path",
        lambda: plugin,
    )
    assert build_global_hook_plugin_dup_section(tmp_path / "proj") is None


def test_global_hook_plugin_dup_section_warns_with_evidence(tmp_path: Path, monkeypatch) -> None:
    """(d) 残骸あり → advisory 行が返り event / 両コマンドが載る。"""
    _write_global_settings(
        tmp_path,
        {"Stop": [_matcher_group('python3 "~/.claude/hooks/record-verbosity.py"')]},
    )
    plugin = _write_hooks_file(
        tmp_path / "plugin" / "hooks.json",
        {"Stop": [_matcher_group('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/record_verbosity.py"')]},
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from audit import sections_artifacts

    monkeypatch.setattr(
        sections_artifacts,
        "_default_plugin_hooks_path",
        lambda: plugin,
    )
    section = build_global_hook_plugin_dup_section(tmp_path / "proj")
    assert section is not None
    joined = "\n".join(section)
    assert section[0].startswith("## ")
    assert section[-1] == ""
    assert "⚠" in joined
    assert "Stop" in joined
    assert "record-verbosity.py" in joined
    assert "record_verbosity.py" in joined
