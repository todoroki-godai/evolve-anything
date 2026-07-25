"""The contributor dependency groups are intentionally split by runtime role."""
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_optional_dependency_groups_cover_runtime_imports() -> None:
    project = _project()
    extras = project["optional-dependencies"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert project["version"] == plugin["version"]
    assert project["dependencies"] == ["PyYAML>=6.0"]
    assert extras["core"] == ["PyYAML>=6.0"]
    assert extras["storage"] == ["duckdb>=1.1.0"]
    assert extras["analysis"] == [
        "numpy>=1.24",
        "scipy>=1.10",
        "scikit-learn>=1.3",
    ]
    assert {"pytest>=8.0", "pytest-xdist>=3.0"}.issubset(extras["dev"])
    assert not set(extras["analysis"]) & set(extras["dev"])


def test_release_rule_mentions_all_version_sources() -> None:
    rule = (ROOT / ".claude" / "rules" / "commit-version.md").read_text(
        encoding="utf-8"
    )

    for source in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "pyproject.toml",
        "CHANGELOG.md",
    ):
        assert source in rule


def test_package_metadata_can_be_installed_without_resolving_dependencies(tmp_path: Path) -> None:
    """A clean checkout can at least build/install the phase-one metadata package."""
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", package_dir / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", package_dir / "README.md")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "--no-deps",
            "--target",
            str(tmp_path / "installed"),
            ".",
        ],
        cwd=package_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "installed").glob("evolve_anything-*.dist-info"))


def test_legacy_storage_requirements_resolve_relative_to_its_directory(tmp_path: Path) -> None:
    """The retained requirements file delegates to the canonical storage extra."""
    package_dir = tmp_path / "package"
    requirements_dir = package_dir / "scripts"
    requirements_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", package_dir / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", package_dir / "README.md")
    shutil.copy2(ROOT / "scripts" / "requirements.txt", requirements_dir / "requirements.txt")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "--no-deps",
            "--target",
            str(tmp_path / "installed"),
            "-r",
            "requirements.txt",
        ],
        cwd=requirements_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list((tmp_path / "installed").glob("evolve_anything-*.dist-info"))
