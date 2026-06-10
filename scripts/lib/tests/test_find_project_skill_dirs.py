"""`find_project_skill_dirs` の回帰テスト（#423）。

plugin レイアウト（リポジトリ直下 `skills/`）と通常レイアウト（`.claude/skills/`）の
両方を走査し、#419 の収集除外（node_modules / dot-dir / アーカイブ）を共有することを確認。
telemetry.utilization が plugin レイアウト PJ で恒久 0 だった根因の修理。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from audit.artifacts import find_project_skill_dirs  # noqa: E402


def _make_skill(base: Path, *parts: str) -> None:
    skill_md = base.joinpath(*parts, "SKILL.md")
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")


def test_plugin_layout_root_skills(tmp_path: Path):
    # plugin レイアウト: リポジトリ直下 skills/
    _make_skill(tmp_path / "skills", "alpha")
    _make_skill(tmp_path / "skills", "beta")
    result = find_project_skill_dirs(tmp_path)
    assert set(result) == {"alpha", "beta"}


def test_normal_layout_dotclaude_skills(tmp_path: Path):
    _make_skill(tmp_path / ".claude" / "skills", "gamma")
    result = find_project_skill_dirs(tmp_path)
    assert result == ["gamma"]


def test_both_layouts_deduped_and_sorted(tmp_path: Path):
    _make_skill(tmp_path / "skills", "beta")
    _make_skill(tmp_path / ".claude" / "skills", "alpha")
    _make_skill(tmp_path / ".claude" / "skills", "beta")  # 重複名
    result = find_project_skill_dirs(tmp_path)
    assert result == ["alpha", "beta"]


def test_excludes_archive_and_vendor(tmp_path: Path):
    _make_skill(tmp_path / "skills", "real")
    _make_skill(tmp_path / "skills" / ".archive", "archived")
    _make_skill(tmp_path / "skills" / "node_modules" / "pkg", "vendored")
    _make_skill(tmp_path / "skills" / ".gstack-backup", "real")
    result = find_project_skill_dirs(tmp_path)
    assert result == ["real"]


def test_no_skills_returns_empty(tmp_path: Path):
    assert find_project_skill_dirs(tmp_path) == []
