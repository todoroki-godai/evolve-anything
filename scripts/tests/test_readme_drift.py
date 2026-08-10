"""Repository-facing documentation must stay aligned with plugin metadata."""
import json
from pathlib import Path

import check_readme_drift


ROOT = Path(__file__).resolve().parents[2]


def test_repository_readmes_match_live_plugin_inventory() -> None:
    """Both localized READMEs describe the skills, hooks, and CLI we ship."""
    assert check_readme_drift.validate_repository(ROOT) == []


def test_validator_reports_a_stale_skill_count() -> None:
    """A hand-edited catalog count must fail before it is published."""
    readme = ROOT / "README.md"
    stale = readme.read_text(encoding="utf-8").replace(
        "Skill Catalog (23 user-invocable skills)",
        "Skill Catalog (22 user-invocable skills)",
        1,
    )

    errors = check_readme_drift.validate_readme(
        ROOT,
        readme,
        content=stale,
    )

    assert any("skill count" in error for error in errors)


def test_validator_reports_a_missing_documented_command() -> None:
    """Adding or renaming a bin command cannot silently drift the README."""
    readme = ROOT / "README.md"
    stale = readme.read_text(encoding="utf-8").replace(
        "| `evolve-audit` |",
        "| `not-a-command` |",
        1,
    )

    errors = check_readme_drift.validate_readme(
        ROOT,
        readme,
        content=stale,
    )

    assert any("bare CLI command inventory" in error for error in errors)


def test_validator_reports_a_missing_documented_hook() -> None:
    """The documentation cannot omit a hook configured in hooks.json."""
    readme = ROOT / "README.md"
    stale = readme.read_text(encoding="utf-8").replace(
        "| `ctx_guard` |",
        "| `not-a-hook` |",
        1,
    )

    errors = check_readme_drift.validate_readme(
        ROOT,
        readme,
        content=stale,
    )

    assert any("hook script inventory" in error for error in errors)


def test_validator_reports_stale_release_metadata() -> None:
    """Version and configurable-option claims are checked against plugin.json."""
    readme = ROOT / "README.md"
    # 現行バージョンは plugin.json から導出する（この fixture に版番号をハードコードすると
    # リリース bump のたびにこのテストが落ち、release 手順に無関係な追従作業が生える）。
    version = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    current = f"> Release metadata: **v{version}** · **23 userConfig options**"
    content = readme.read_text(encoding="utf-8")
    assert current in content, f"README の Release metadata 行が想定と異なる: {current!r}"
    stale = content.replace(
        current,
        "> Release metadata: **v0.0.0** · **20 userConfig options**",
        1,
    )

    errors = check_readme_drift.validate_readme(
        ROOT,
        readme,
        content=stale,
    )

    assert any("release version" in error for error in errors)
    assert any("userConfig count" in error for error in errors)


def test_inventory_ignores_hidden_bin_files(tmp_path: Path) -> None:
    """OS metadata in bin/ is not a public CLI command."""
    (tmp_path / "skills" / "sample").mkdir(parents=True)
    (tmp_path / "skills" / "sample" / "SKILL.md").write_text(
        "---\nname: sample\ndescription: sample\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "sample").write_text("", encoding="utf-8")
    (tmp_path / "bin" / ".DS_Store").write_text("", encoding="utf-8")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.0.0", "userConfig": {}}),
        encoding="utf-8",
    )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {}}),
        encoding="utf-8",
    )

    inventory = check_readme_drift.collect_inventory(tmp_path)

    assert inventory.commands == ("sample",)


def test_non_python_hook_is_reported_as_drift_instead_of_crashing(tmp_path: Path) -> None:
    """Unsupported hook commands remain an actionable validation error."""
    (tmp_path / "skills" / "sample").mkdir(parents=True)
    (tmp_path / "skills" / "sample" / "SKILL.md").write_text(
        "---\nname: sample\ndescription: sample\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "bin").mkdir()
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "1.0.0", "userConfig": {}}),
        encoding="utf-8",
    )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"command": "bash -c 'echo done'"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text("", encoding="utf-8")

    errors = check_readme_drift.validate_readme(tmp_path, readme)

    assert any("unrecognized hook command" in error for error in errors)
