"""Repository-facing documentation must stay aligned with plugin metadata."""
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
    stale = readme.read_text(encoding="utf-8").replace(
        "> Release metadata: **v1.123.0** · **21 userConfig options**",
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
