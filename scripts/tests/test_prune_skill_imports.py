"""prune SKILL.md 記載コードブロックのインポートパス回帰テスト (#488)。

背景: prune/SKILL.md Step4/Step5 が `from scripts.prune import ...` を指示していたが、
`scripts/__init__.py` が存在しないため ModuleNotFoundError になっていた。
evolve SKILL.md と完全同型 (#479 / PR #480)。

正準パターン:
    sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
    from prune import archive_file, check_import_dependencies, SkillDependencyError
    from prune import restore_file, list_archive

このテストはインポートが実際に成功することを決定論で検証する（LLM 非依存）。
"""
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"

if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def test_step4_imports_resolve():
    """Step4 コードブロックと同じ import が ModuleNotFoundError を起こさない (#488)。"""
    import importlib
    import prune as prune_mod

    # archive_file / check_import_dependencies / SkillDependencyError が prune から取れること
    archive_file = getattr(prune_mod, "archive_file", None)
    check_import_dependencies = getattr(prune_mod, "check_import_dependencies", None)
    SkillDependencyError = getattr(prune_mod, "SkillDependencyError", None)

    assert callable(archive_file), "archive_file が prune から import できない"
    assert callable(check_import_dependencies), "check_import_dependencies が prune から import できない"
    assert SkillDependencyError is not None and isinstance(SkillDependencyError, type), (
        "SkillDependencyError が prune から import できない"
    )


def test_step5_imports_resolve():
    """Step5 コードブロックと同じ import が ModuleNotFoundError を起こさない (#488)。"""
    import prune as prune_mod

    restore_file = getattr(prune_mod, "restore_file", None)
    list_archive = getattr(prune_mod, "list_archive", None)

    assert callable(restore_file), "restore_file が prune から import できない"
    assert callable(list_archive), "list_archive が prune から import できない"


def test_prune_skill_md_uses_plugin_root_path():
    """prune/SKILL.md の sys.path 設定行が ${CLAUDE_PLUGIN_ROOT} ベースの絶対パスを使っていること。

    `sys.path.insert(0, 'scripts/lib')` のような相対パスが残っていないことを確認する。
    """
    import re

    skill_md = _PLUGIN_ROOT / "skills" / "prune" / "SKILL.md"
    assert skill_md.exists(), f"prune/SKILL.md が見つからない: {skill_md}"

    content = skill_md.read_text(encoding="utf-8")
    # 相対 sys.path パターン（違反）
    relative_syspath = re.compile(r"""sys\.path\.insert\(\s*0\s*,\s*['"]scripts/lib['"]""")
    violations = [
        line.strip()
        for line in content.splitlines()
        if relative_syspath.search(line)
    ]
    assert not violations, (
        "prune/SKILL.md に相対 sys.path 参照が残っています (#488):\n"
        + "\n".join(violations)
    )

    # 修正後: sys.path.insert が CLAUDE_PLUGIN_ROOT ベースのパスを使っていること
    syspath_lines = [
        line.strip()
        for line in content.splitlines()
        if "sys.path.insert" in line
    ]
    assert syspath_lines, "prune/SKILL.md に sys.path.insert 行が見つからない（コードブロックが削除された？）"
    for line in syspath_lines:
        assert "CLAUDE_PLUGIN_ROOT" in line or "_root" in line, (
            f"sys.path.insert 行が _root (CLAUDE_PLUGIN_ROOT 由来) を使っていない: {line!r}"
        )
