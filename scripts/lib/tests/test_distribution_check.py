"""配布物と CI smoke の決定論チェック。

plugin.json を重複メタデータの正典とし、marketplace.json の drift、
JSON 構文、SKILL.md frontmatter、主要 CLI の起動可否を検証する。
"""
from __future__ import annotations

import json
from pathlib import Path

import distribution_check


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_manifests(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    plugin = {
        "name": "sample-plugin",
        "version": "1.2.3",
        "author": {"name": "owner"},
        "homepage": "https://example.com/home",
        "repository": "https://example.com/repo",
        "license": "MIT",
        "keywords": ["sample"],
        "userConfig": {
            "enabled": {
                "title": "Enabled",
                "type": "boolean",
                "description": "Enable the feature",
                "sensitive": False,
            }
        },
    }
    marketplace = {
        "name": "marketplace",
        "plugins": [
            {
                **plugin,
                "source": "./",
                "description": "Marketplace-specific copy",
                "category": "development",
            }
        ],
    }
    _write_json(root / ".claude-plugin" / "plugin.json", plugin)
    _write_json(root / ".claude-plugin" / "marketplace.json", marketplace)
    return plugin, marketplace


def test_manifest_drift_reports_user_config_difference(tmp_path: Path) -> None:
    _, marketplace = _make_manifests(tmp_path)
    marketplace["plugins"][0]["userConfig"]["enabled"]["description"] = "stale"
    _write_json(tmp_path / ".claude-plugin" / "marketplace.json", marketplace)

    errors = distribution_check.check_manifest_sync(tmp_path)

    assert errors == [
        ".claude-plugin/marketplace.json plugin 'sample-plugin' field "
        "'userConfig' differs from plugin.json"
    ]


def test_sync_manifest_copies_owned_fields_and_preserves_marketplace_metadata(
    tmp_path: Path,
) -> None:
    plugin, marketplace = _make_manifests(tmp_path)
    entry = marketplace["plugins"][0]
    entry["version"] = "0.0.1"
    entry["userConfig"] = {}
    _write_json(tmp_path / ".claude-plugin" / "marketplace.json", marketplace)

    changed = distribution_check.sync_marketplace_manifest(tmp_path)

    assert changed is True
    synced = json.loads(
        (tmp_path / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    synced_entry = synced["plugins"][0]
    for field in distribution_check.SYNCED_PLUGIN_FIELDS:
        assert synced_entry[field] == plugin[field]
    assert synced_entry["source"] == "./"
    assert synced_entry["description"] == "Marketplace-specific copy"
    assert synced_entry["category"] == "development"
    assert distribution_check.check_manifest_sync(tmp_path) == []


def test_sync_manifest_repairs_single_marketplace_entry_name(tmp_path: Path) -> None:
    plugin, marketplace = _make_manifests(tmp_path)
    marketplace["plugins"][0]["name"] = "stale-name"
    _write_json(tmp_path / ".claude-plugin" / "marketplace.json", marketplace)

    assert distribution_check.check_manifest_sync(tmp_path) == [
        ".claude-plugin/marketplace.json plugin 'sample-plugin' field "
        "'name' differs from plugin.json"
    ]
    assert distribution_check.sync_marketplace_manifest(tmp_path) is True

    synced = json.loads(
        (tmp_path / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert synced["plugins"][0]["name"] == plugin["name"]


def test_json_check_reports_invalid_file(tmp_path: Path) -> None:
    (tmp_path / "valid.json").write_text('{"ok": true}\n', encoding="utf-8")
    (tmp_path / "broken.json").write_text('{"missing": }\n', encoding="utf-8")

    errors = distribution_check.check_json_files(tmp_path)

    assert len(errors) == 1
    assert errors[0].startswith("broken.json: invalid JSON:")


def test_skill_frontmatter_requires_parseable_name_and_description(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "skills" / "valid" / "SKILL.md"
    valid.parent.mkdir(parents=True)
    valid.write_text(
        "---\nname: valid\ndescription: Valid skill\n---\n\n# Valid\n",
        encoding="utf-8",
    )
    invalid = tmp_path / "skills" / "invalid" / "SKILL.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text(
        "---\nname: invalid\ndescription: broken: yaml\n---\n\n# Invalid\n",
        encoding="utf-8",
    )
    missing = tmp_path / "skills" / "missing" / "SKILL.md"
    missing.parent.mkdir(parents=True)
    missing.write_text("---\nname: missing\n---\n\n# Missing\n", encoding="utf-8")

    errors = distribution_check.check_skill_frontmatter(tmp_path)

    assert any(error.startswith("skills/invalid/SKILL.md: invalid YAML:") for error in errors)
    assert "skills/missing/SKILL.md: missing non-empty 'description'" in errors
    assert not any("skills/valid/SKILL.md" in error for error in errors)


def test_cli_smoke_reports_nonzero_exit(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ok").write_text(
        "import argparse\nargparse.ArgumentParser().parse_args()\n",
        encoding="utf-8",
    )
    (bin_dir / "broken").write_text(
        "raise RuntimeError('cannot start')\n",
        encoding="utf-8",
    )

    errors = distribution_check.check_cli_smoke(
        tmp_path,
        commands=(("bin/ok", "--help"), ("bin/broken", "--help")),
    )

    assert len(errors) == 1
    assert errors[0].startswith("bin/broken --help: exited ")
    assert "cannot start" in errors[0]


def test_repository_distribution_contract() -> None:
    root = Path(__file__).resolve().parents[3]

    assert distribution_check.check_json_files(root) == []
    assert distribution_check.check_skill_frontmatter(root) == []
    assert distribution_check.check_manifest_sync(root) == []
