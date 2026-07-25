"""配布 manifest・JSON・skill frontmatter・主要 CLI の決定論検証。

``plugin.json`` を、marketplace の plugin entry と重複するメタデータの
single source of truth とする。CI は read-only の check を実行し、リリース作業では
``scripts/check_distribution.py --sync-manifest`` で marketplace 側を再生成できる。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import yaml


SYNCED_PLUGIN_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "userConfig",
)

DEFAULT_SMOKE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("bin/evolve", "--help"),
    ("bin/evolve-audit", "--help"),
    ("bin/evolve-fleet", "--help"),
    ("bin/evolve-reflect", "--help"),
    ("bin/evolve-tier", "--help"),
)

_IGNORED_DIRS = {".git", ".venv", "node_modules", "__pycache__"}


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _manifest_paths(root: Path) -> tuple[Path, Path]:
    manifest_dir = root / ".claude-plugin"
    return manifest_dir / "plugin.json", manifest_dir / "marketplace.json"


def _manifest_objects(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    plugin_path, marketplace_path = _manifest_paths(root)
    plugin = _load_json(plugin_path)
    marketplace = _load_json(marketplace_path)
    if not isinstance(plugin, dict):
        raise ValueError(".claude-plugin/plugin.json must contain a JSON object")
    if not isinstance(marketplace, dict):
        raise ValueError(".claude-plugin/marketplace.json must contain a JSON object")
    return plugin, marketplace


def _marketplace_entry(
    plugin: dict[str, object],
    marketplace: dict[str, object],
) -> dict[str, object]:
    plugin_name = plugin.get("name")
    if not isinstance(plugin_name, str) or not plugin_name:
        raise ValueError(".claude-plugin/plugin.json is missing non-empty 'name'")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError(".claude-plugin/marketplace.json 'plugins' must be a list")
    entries = [entry for entry in plugins if isinstance(entry, dict)]
    matches = [
        entry
        for entry in entries
        if entry.get("name") == plugin_name
    ]
    if len(matches) == 1:
        return matches[0]
    # 単一 plugin marketplace なら name 自体の drift も検出・自動修復できる。
    if not matches and len(entries) == 1:
        return entries[0]
    raise ValueError(
        ".claude-plugin/marketplace.json must contain exactly one plugin "
        f"entry named {plugin_name!r}"
    )


def check_manifest_sync(root: Path) -> list[str]:
    """plugin.json と marketplace plugin entry の重複フィールドを突合する。"""
    try:
        plugin, marketplace = _manifest_objects(root)
        entry = _marketplace_entry(plugin, marketplace)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return [f"manifest: {error}"]

    plugin_name = str(plugin["name"])
    errors: list[str] = []
    for field in SYNCED_PLUGIN_FIELDS:
        if field not in plugin:
            errors.append(f".claude-plugin/plugin.json is missing {field!r}")
        elif entry.get(field) != plugin[field]:
            errors.append(
                ".claude-plugin/marketplace.json plugin "
                f"{plugin_name!r} field {field!r} differs from plugin.json"
            )
    return errors


def sync_marketplace_manifest(root: Path) -> bool:
    """plugin.json の正典フィールドを marketplace entry へ同期して保存する。"""
    plugin, marketplace = _manifest_objects(root)
    entry = _marketplace_entry(plugin, marketplace)
    for field in SYNCED_PLUGIN_FIELDS:
        if field not in plugin:
            raise ValueError(f".claude-plugin/plugin.json is missing {field!r}")

    changed = any(entry.get(field) != plugin[field] for field in SYNCED_PLUGIN_FIELDS)
    if not changed:
        return False

    for field in SYNCED_PLUGIN_FIELDS:
        entry[field] = copy.deepcopy(plugin[field])

    _, marketplace_path = _manifest_paths(root)
    marketplace_path.write_text(
        json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _repo_files(root: Path, pattern: str) -> Iterable[Path]:
    for path in sorted(root.rglob(pattern)):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if path.is_file() and not (_IGNORED_DIRS & set(relative.parts)):
            yield path


def check_json_files(root: Path) -> list[str]:
    """リポジトリ内の JSON ファイルがすべて parse 可能か検証する。"""
    errors: list[str] = []
    for path in _repo_files(root, "*.json"):
        try:
            _load_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            relative = path.relative_to(root).as_posix()
            errors.append(f"{relative}: invalid JSON: {error}")
    return errors


def _frontmatter_text(path: Path) -> tuple[str | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, f"cannot read: {error}"
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, "missing opening '---'"
    try:
        close_index = lines.index("---", 1)
    except ValueError:
        return None, "missing closing '---'"
    return "\n".join(lines[1:close_index]), None


def check_skill_frontmatter(root: Path) -> list[str]:
    """全 SKILL.md の YAML と必須 name/description を検証する。"""
    errors: list[str] = []
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return ["skills/: directory not found"]

    skill_files = list(_repo_files(skills_root, "SKILL.md"))
    if not skill_files:
        return ["skills/: no SKILL.md files found"]

    for path in skill_files:
        relative = path.relative_to(root).as_posix()
        yaml_text, delimiter_error = _frontmatter_text(path)
        if delimiter_error:
            errors.append(f"{relative}: {delimiter_error}")
            continue
        try:
            frontmatter = yaml.safe_load(yaml_text)
        except yaml.YAMLError as error:
            first_line = str(error).splitlines()[0] if str(error) else "parse error"
            errors.append(f"{relative}: invalid YAML: {first_line}")
            continue
        if not isinstance(frontmatter, dict):
            errors.append(f"{relative}: frontmatter must be a mapping")
            continue
        for field in ("name", "description"):
            value = frontmatter.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{relative}: missing non-empty {field!r}")
    return errors


def check_cli_smoke(
    root: Path,
    *,
    commands: Sequence[Sequence[str]] = DEFAULT_SMOKE_COMMANDS,
) -> list[str]:
    """主要 CLI の ``--help`` がクリーンな HOME / data dir で成功するか確認する。"""
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="evolve-anything-ci-smoke-") as temp_dir:
        env = os.environ.copy()
        env.update(
            {
                "CLAUDE_PLUGIN_DATA": temp_dir,
                "HOME": temp_dir,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for command in commands:
            display = " ".join(command)
            try:
                result = subprocess.run(
                    [sys.executable, str(root / command[0]), *command[1:]],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"{display}: failed to run: {error}")
                continue
            if result.returncode == 0:
                continue
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")
            if len(detail) > 500:
                detail = detail[:497] + "..."
            errors.append(
                f"{display}: exited {result.returncode}"
                + (f": {detail}" if detail else "")
            )
    return errors


def run_checks(root: Path, *, include_cli_smoke: bool = True) -> list[str]:
    """CI 向け配布物チェック一式を実行する。"""
    errors = [
        *check_json_files(root),
        *check_skill_frontmatter(root),
        *check_manifest_sync(root),
    ]
    if include_cli_smoke:
        errors.extend(check_cli_smoke(root))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="plugin repository root (default: inferred from script path)",
    )
    parser.add_argument(
        "--sync-manifest",
        action="store_true",
        help="copy plugin.json-owned fields into marketplace.json before checking",
    )
    parser.add_argument(
        "--skip-cli-smoke",
        action="store_true",
        help="skip major CLI --help smoke tests",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    if args.sync_manifest:
        try:
            changed = sync_marketplace_manifest(root)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"ERROR: manifest sync failed: {error}", file=sys.stderr)
            return 1
        print("marketplace manifest synced" if changed else "marketplace manifest already synced")

    errors = run_checks(root, include_cli_smoke=not args.skip_cli_smoke)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("distribution checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
