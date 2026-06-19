"""`find_artifacts` の plugin_self スキャン回帰テスト（#185）。

プラグイン本体リポジトリ（`.claude-plugin/plugin.json` 存在）では repo 直下 skills/ を
追加スキャンし、origin が plugin_self に分類されることを確認する。`.claude-plugin/plugin.json`
が無い通常 PJ では挙動を一切変えない（回帰ゼロ）。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from audit.artifacts import find_artifacts  # noqa: E402
from audit.classification import classify_artifact_origin  # noqa: E402


def _make_skill(base: Path, *parts: str) -> Path:
    skill_md = base.joinpath(*parts, "SKILL.md")
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")
    return skill_md


def _make_plugin_manifest(repo: Path) -> None:
    manifest = repo / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"name": "evolve-anything"}', encoding="utf-8")


def test_plugin_self_repo_root_skills_included(tmp_path):
    """plugin.json があれば repo 直下 skills/ を追加スキャンする（#185）。"""
    _make_plugin_manifest(tmp_path)
    _make_skill(tmp_path / "skills", "evolve")
    _make_skill(tmp_path / "skills", "reflect")

    result = find_artifacts(tmp_path)
    names = {p.parent.name for p in result["skills"]}
    assert "evolve" in names
    assert "reflect" in names


def test_plugin_self_origin_is_plugin_self(tmp_path):
    """追加された repo 直下 skills/ は origin=plugin_self に分類される（#185）。"""
    _make_plugin_manifest(tmp_path)
    skill_md = _make_skill(tmp_path / "skills", "evolve")

    result = find_artifacts(tmp_path)
    matched = [p for p in result["skills"] if p.parent.name == "evolve"]
    assert matched, "evolve skill should be collected"
    assert classify_artifact_origin(matched[0]) == "plugin_self"


def test_no_manifest_repo_root_skills_not_scanned(tmp_path):
    """plugin.json が無い通常 PJ では repo 直下 skills/ をスキャンしない（回帰ゼロ）。"""
    _make_skill(tmp_path / "skills", "evolve")  # マニフェスト無し

    result = find_artifacts(tmp_path)
    names = {p.parent.name for p in result["skills"]}
    assert "evolve" not in names


def test_plugin_self_excludes_archive_and_vendor(tmp_path):
    """plugin_self スキャンでも #419 の収集除外を共有する。"""
    _make_plugin_manifest(tmp_path)
    _make_skill(tmp_path / "skills", "real")
    _make_skill(tmp_path / "skills" / ".archive", "archived")
    _make_skill(tmp_path / "skills" / "node_modules" / "pkg", "vendored")

    result = find_artifacts(tmp_path)
    names = {p.parent.name for p in result["skills"]}
    assert "real" in names
    assert "archived" not in names
    assert "vendored" not in names


def test_dotclaude_skills_still_scanned_with_manifest(tmp_path):
    """plugin.json があっても .claude/skills/（ユーザー自作）は従来どおりスキャンされる。"""
    _make_plugin_manifest(tmp_path)
    _make_skill(tmp_path / ".claude" / "skills", "user-custom")

    result = find_artifacts(tmp_path)
    names = {p.parent.name for p in result["skills"]}
    assert "user-custom" in names
