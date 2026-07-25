#!/usr/bin/env python3
"""Verify that the two public READMEs match the plugin files we ship.

This is deliberately dependency-free so contributors can run it before the
optional storage or analysis packages are installed.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


README_NAMES = ("README.md", "README.ja.md")
PLUGIN_PREFIX = "evolve-anything:"


@dataclass(frozen=True)
class PluginInventory:
    """The user-facing items derived from the checked-in plugin files."""

    skills: tuple[str, ...]
    commands: tuple[str, ...]
    registered_hooks: int
    hook_events: int
    hook_scripts: tuple[str, ...]
    version: str
    user_config_options: int


def _frontmatter_name(skill_file: Path) -> str:
    """Read a skill's public command name without requiring PyYAML."""
    content = skill_file.read_text(encoding="utf-8")
    close = content.find("\n---", 3)
    if not content.startswith("---") or close == -1:
        raise ValueError(f"{skill_file}: missing YAML frontmatter")

    match = re.search(r"^name:\s*(\S.*?)\s*$", content[3:close], re.MULTILINE)
    if not match:
        raise ValueError(f"{skill_file}: frontmatter has no name")

    name = match.group(1).strip().strip('"\'')
    return name.removeprefix(PLUGIN_PREFIX)


def collect_inventory(root: Path) -> PluginInventory:
    """Collect public skill, bare-CLI, and hook counts from their sources."""
    skills = tuple(sorted(_frontmatter_name(path) for path in (root / "skills").glob("*/SKILL.md")))
    if len(skills) != len(set(skills)):
        raise ValueError("duplicate public skill names in skills/*/SKILL.md")

    commands = tuple(sorted(path.name for path in (root / "bin").iterdir() if path.is_file()))

    plugin_manifest = json.loads(
        (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    hooks_path = root / "hooks" / "hooks.json"
    hook_config = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    hook_entries = [
        hook
        for groups in hook_config.values()
        for group in groups
        for hook in group.get("hooks", ())
    ]
    hook_scripts = set()
    for hook in hook_entries:
        match = re.search(r"/hooks/([^/\"\s]+)\.py", str(hook.get("command", "")))
        if match is None:
            raise ValueError(f"unrecognized hook command: {hook.get('command')!r}")
        hook_scripts.add(match.group(1))

    return PluginInventory(
        skills=skills,
        commands=commands,
        registered_hooks=len(hook_entries),
        hook_events=len(hook_config),
        hook_scripts=tuple(sorted(hook_scripts)),
        version=str(plugin_manifest["version"]),
        user_config_options=len(plugin_manifest.get("userConfig", {})),
    )


def _section(content: str, heading_match: re.Match[str]) -> str:
    """Return the markdown section beginning at a matched level-two heading."""
    next_heading = re.search(r"^## ", content[heading_match.end():], re.MULTILINE)
    end = heading_match.end() + next_heading.start() if next_heading else len(content)
    return content[heading_match.end():end]


def _table_first_column(section: str) -> set[str]:
    """Extract code-formatted values from the first cell of Markdown tables."""
    table = _first_table(section)
    return set(re.findall(r"^\|\s*`([^`]+)`\s*\|", table, re.MULTILINE))


def _table_code_cells(section: str) -> set[str]:
    """Extract every code-formatted value from table rows in one section."""
    return {
        value
        for line in _first_table(section).splitlines()
        if line.startswith("|")
        for value in re.findall(r"`([^`]+)`", line)
    }


def _first_table(section: str) -> str:
    """Return the first contiguous Markdown table after a section heading."""
    table: list[str] = []
    for line in section.splitlines():
        if line.startswith("|"):
            table.append(line)
        elif table:
            break
    return "\n".join(table)


def _one_heading(
    path: Path,
    content: str,
    pattern: str,
    label: str,
    errors: list[str],
) -> re.Match[str] | None:
    match = re.search(pattern, content, re.MULTILINE)
    if match is None:
        errors.append(f"{path.name}: missing {label} heading")
    return match


def _format_difference(expected: Iterable[str], documented: Iterable[str]) -> str:
    expected_set = set(expected)
    documented_set = set(documented)
    missing = sorted(expected_set - documented_set)
    extra = sorted(documented_set - expected_set)
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("extra: " + ", ".join(extra))
    return "; ".join(details)


def validate_readme(root: Path, path: Path, *, content: str | None = None) -> list[str]:
    """Return all documentation-drift errors for one localized README."""
    inventory = collect_inventory(root)
    content = path.read_text(encoding="utf-8") if content is None else content
    errors: list[str] = []
    japanese = path.name.endswith(".ja.md")

    release_pattern = (
        r"^> リリース情報: \*\*v(?P<version>[^*]+)\*\* ・ "
        r"\*\*(?P<config_count>\d+)個の userConfig\*\*$"
        if japanese
        else r"^> Release metadata: \*\*v(?P<version>[^*]+)\*\* · "
        r"\*\*(?P<config_count>\d+) userConfig options\*\*$"
    )
    release_heading = _one_heading(path, content, release_pattern, "release metadata", errors)
    if release_heading is not None:
        if release_heading["version"] != inventory.version:
            errors.append(
                f"{path.name}: release version is {release_heading['version']}, "
                f"but plugin.json defines {inventory.version}"
            )
        if int(release_heading["config_count"]) != inventory.user_config_options:
            errors.append(
                f"{path.name}: userConfig count is {release_heading['config_count']}, "
                f"but plugin.json defines {inventory.user_config_options}"
            )

    skill_pattern = (
        r"^## スキル一覧（(?P<count>\d+)個の公開スキル）$"
        if japanese
        else r"^## Skill Catalog \((?P<count>\d+) user-invocable skills\)$"
    )
    skill_heading = _one_heading(path, content, skill_pattern, "skill catalog", errors)
    if skill_heading is not None:
        if int(skill_heading["count"]) != len(inventory.skills):
            errors.append(
                f"{path.name}: skill count is {skill_heading['count']}, "
                f"but skills/*/SKILL.md defines {len(inventory.skills)}"
            )
        documented = _table_first_column(_section(content, skill_heading))
        if documented != set(inventory.skills):
            errors.append(
                f"{path.name}: skill catalog entries do not match SKILL.md "
                f"({_format_difference(inventory.skills, documented)})"
            )

    hooks_pattern = (
        r"^## Hooks（(?P<count>\d+)件の登録、(?P<events>\d+)イベント）$"
        if japanese
        else r"^## Hooks \((?P<count>\d+) registered entries across (?P<events>\d+) events\)$"
    )
    hooks_heading = _one_heading(path, content, hooks_pattern, "hook inventory", errors)
    if hooks_heading is not None:
        if int(hooks_heading["count"]) != inventory.registered_hooks:
            errors.append(
                f"{path.name}: registered hook count is {hooks_heading['count']}, "
                f"but hooks/hooks.json defines {inventory.registered_hooks}"
            )
        if int(hooks_heading["events"]) != inventory.hook_events:
            errors.append(
                f"{path.name}: hook event count is {hooks_heading['events']}, "
                f"but hooks/hooks.json defines {inventory.hook_events}"
            )
        documented = _table_first_column(_section(content, hooks_heading))
        if documented != set(inventory.hook_scripts):
            errors.append(
                f"{path.name}: hook script inventory does not match hooks/hooks.json "
                f"({_format_difference(inventory.hook_scripts, documented)})"
            )

    command_pattern = (
        r"^## bare CLI 一覧（(?P<count>\d+)コマンド）$"
        if japanese
        else r"^## Bare CLI inventory \((?P<count>\d+) commands\)$"
    )
    command_heading = _one_heading(path, content, command_pattern, "bare CLI inventory", errors)
    if command_heading is not None:
        if int(command_heading["count"]) != len(inventory.commands):
            errors.append(
                f"{path.name}: bare CLI command count is {command_heading['count']}, "
                f"but bin/ defines {len(inventory.commands)}"
            )
        documented = _table_code_cells(_section(content, command_heading))
        if documented != set(inventory.commands):
            errors.append(
                f"{path.name}: bare CLI command inventory does not match bin/ "
                f"({_format_difference(inventory.commands, documented)})"
            )

    return errors


def validate_repository(root: Path) -> list[str]:
    """Validate both public README translations against the local plugin."""
    errors: list[str] = []
    for name in README_NAMES:
        errors.extend(validate_readme(root, root / name))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_repository(root)
    if errors:
        print("README documentation drift detected:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1

    inventory = collect_inventory(root)
    print(
        "README documentation matches plugin inventory: "
        f"v{inventory.version}, {len(inventory.skills)} skills, "
        f"{inventory.registered_hooks} hook registrations, {len(inventory.commands)} bare CLI commands."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
