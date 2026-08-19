"""instruction_violations の plugin:skill 名前空間解決テスト（#467）。

run_discover の instruction violation 検出ブロックは last_skill を bare 名前提の
glob（``~/.claude/skills/<skill_name>/SKILL.md``）で探していたが、プラグイン由来の
last_skill は ``evolve-anything:report-feedback`` のような名前空間付き形式で記録され、
dir 名に ``:`` は含まれないため常に外れ、instruction_violations は無条件で 0 件になっていた。

TDD-first: skill_origin.resolve_plugin_skill_path 導入後の runner.py 配線を検証する。
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import discover  # noqa: E402
import skill_origin  # noqa: E402
import telemetry_query  # noqa: E402


_SKILL_MD_CONTENT = """\
# Report Feedback Skill

## Important Rules
- 古い項目は CHANGELOG.md へ移動すること（削除は禁止）
"""


def _write_installed_plugins(plugins_dir: Path, plugin_key: str, install_path: Path) -> Path:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    ip_path = plugins_dir / "installed_plugins.json"
    ip_path.write_text(
        json.dumps({"plugins": {plugin_key: [{"installPath": str(install_path)}]}}),
        encoding="utf-8",
    )
    return ip_path


@pytest.fixture(autouse=True)
def _clear_skill_origin_cache():
    skill_origin.invalidate_cache()
    yield
    skill_origin.invalidate_cache()


def _correction(last_skill: str) -> dict:
    return {
        "message": "削除じゃなくて CHANGELOG に移動して",
        "correction_type": "stop",
        "last_skill": last_skill,
        "timestamp": "2026-08-19T00:00:00Z",
    }


def test_namespaced_plugin_skill_resolves_and_detects_violation(tmp_path):
    """`plugin:skill` last_skill がプラグインの実体 SKILL.md を解決し違反を検出する。"""
    plugins_dir = tmp_path / "home" / ".claude" / "plugins"
    install_path = tmp_path / "cache" / "evolve-anything" / "1.125.0"
    skill_md = install_path / "skills" / "report-feedback" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(_SKILL_MD_CONTENT, encoding="utf-8")

    ip_path = _write_installed_plugins(
        plugins_dir, "evolve-anything@evolve-anything", install_path
    )

    corr = _correction("evolve-anything:report-feedback")
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    with mock.patch.object(skill_origin, "_installed_plugins_path", return_value=ip_path), \
         mock.patch.object(telemetry_query, "query_corrections", return_value=[corr]), \
         mock.patch("discover.runner.Path.home", return_value=tmp_path / "home"):
        result = discover.run_discover(project_root=project_root)

    assert "instruction_violations_error" not in result
    assert "instruction_violations" in result
    violations = result["instruction_violations"]
    assert len(violations) == 1
    assert violations[0]["file"] == str(skill_md)
    assert "instruction_violations_unresolved" not in result


def test_unresolvable_skill_is_counted_not_silently_zero(tmp_path):
    """未インストールのプラグイン由来 last_skill は無音で 0 件化せず件数として残る。"""
    plugins_dir = tmp_path / "home" / ".claude" / "plugins"
    ip_path = _write_installed_plugins(plugins_dir, "some-other@marketplace", tmp_path / "unused")

    corr = _correction("not-installed-plugin:some-skill")

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    with mock.patch.object(skill_origin, "_installed_plugins_path", return_value=ip_path), \
         mock.patch.object(telemetry_query, "query_corrections", return_value=[corr]), \
         mock.patch("discover.runner.Path.home", return_value=tmp_path / "home"):
        result = discover.run_discover(project_root=project_root)

    assert "instruction_violations_error" not in result
    assert "instruction_violations" not in result
    assert result.get("instruction_violations_unresolved") == 1


def test_bare_skill_name_still_resolves_via_global_skills_dir(tmp_path):
    """名前空間なし（従来仕様）の bare last_skill は引き続き global skills dir で解決する（回帰ゼロ）。"""
    home = tmp_path / "home"
    plugins_dir = home / ".claude" / "plugins"
    ip_path = _write_installed_plugins(plugins_dir, "unrelated@marketplace", tmp_path / "unused")

    skill_md = home / ".claude" / "skills" / "commit" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text(_SKILL_MD_CONTENT, encoding="utf-8")

    corr = _correction("commit")

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    with mock.patch.object(skill_origin, "_installed_plugins_path", return_value=ip_path), \
         mock.patch.object(telemetry_query, "query_corrections", return_value=[corr]), \
         mock.patch("discover.runner.Path.home", return_value=home):
        result = discover.run_discover(project_root=project_root)

    assert "instruction_violations_error" not in result
    assert "instruction_violations" in result
    assert result["instruction_violations"][0]["file"] == str(skill_md)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
