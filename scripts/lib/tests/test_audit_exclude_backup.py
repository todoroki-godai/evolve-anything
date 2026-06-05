"""`.gstack-backup/` 配下スキルが audit 収集・重複検出から除外されることの回帰テスト。

gstack は `~/.claude/skills/.gstack-backup/<name>/SKILL.md` にスキルのバックアップを
保持する。これが `find_artifacts` / `detect_duplicates_simple` に混入すると、実スキルと
バックアップが phantom duplicate として大量検出され（docs-platform evolve で 104件）、
remediation の manual_required を支配して本物の issue を埋もれさせる。

`.archive/` と同じ扱いで収集段階から除外するのが正。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from audit.artifacts import find_artifacts  # noqa: E402
from audit.scope import _is_plugin_managed_path, detect_duplicates_simple  # noqa: E402


def _make_skill(base: Path, *parts: str) -> Path:
    skill_md = base.joinpath(*parts, "SKILL.md")
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")
    return skill_md


class TestFindArtifactsExcludesBackup:
    def test_gstack_backup_配下スキルは収集されない(self, tmp_path: Path):
        claude = tmp_path / ".claude"
        active = _make_skill(claude / "skills", "my-skill")
        _make_skill(claude / "skills" / ".gstack-backup", "my-skill")

        result = find_artifacts(tmp_path)
        skills = result["skills"]

        assert active in skills
        assert not any(".gstack-backup" in p.parts for p in skills)

    def test_archive_除外は維持される(self, tmp_path: Path):
        claude = tmp_path / ".claude"
        _make_skill(claude / "skills", "live")
        _make_skill(claude / "skills" / ".archive", "old")

        skills = find_artifacts(tmp_path)["skills"]
        assert not any(".archive" in p.parts for p in skills)

    def test_underscore_archived_配下スキルは収集されない(self, tmp_path: Path):
        """`_archived/`（先頭ドットなし）も収集除外する（#337）。

        sys-bots は `.claude/skills/_archived/<name>/` にアーカイブを置くため、
        `.archive` だけの除外だと評価対象に混入し remediation がノイズまみれになる。
        """
        claude = tmp_path / ".claude"
        active = _make_skill(claude / "skills", "live")
        _make_skill(claude / "skills" / "_archived", "bot-tool-configuration")

        skills = find_artifacts(tmp_path)["skills"]
        assert active in skills
        assert not any("_archived" in p.parts for p in skills)

    def test_disabled_配下スキルは収集されない(self, tmp_path: Path):
        """`disabled/` 配下も収集除外する（#337）。"""
        claude = tmp_path / ".claude"
        _make_skill(claude / "skills", "live")
        _make_skill(claude / "skills" / "disabled", "old-skill")

        skills = find_artifacts(tmp_path)["skills"]
        assert not any("disabled" in p.parts for p in skills)


class TestIsPluginManagedPath:
    def test_gstack_backup_は管理パス扱い(self):
        p = Path.home() / ".claude" / "skills" / ".gstack-backup" / "review" / "SKILL.md"
        assert _is_plugin_managed_path(p) is True

    def test_gstack_本体コピーも管理パス扱い(self):
        p = Path("/x/.claude/skills/review/.hermes/gstack/SKILL.md")
        assert _is_plugin_managed_path(p) is True

    def test_通常スキルは管理パスでない(self):
        p = Path("/x/.claude/skills/review/SKILL.md")
        assert _is_plugin_managed_path(p) is False


class TestDetectDuplicatesExcludesBackup:
    def test_実スキルとbackupは重複検出されない(self):
        artifacts = {
            "skills": [
                Path("/h/.claude/skills/review/SKILL.md"),
                Path("/h/.claude/skills/.gstack-backup/review/SKILL.md"),
            ],
            "rules": [],
        }
        dups = detect_duplicates_simple(artifacts)
        assert dups == []

    def test_実スキル同士の重複は検出される(self):
        artifacts = {
            "skills": [
                Path("/proj/.claude/skills/review/SKILL.md"),
                Path("/other/.claude/skills/review/SKILL.md"),
            ],
            "rules": [],
        }
        dups = detect_duplicates_simple(artifacts)
        assert len(dups) == 1
        assert dups[0]["name"] == "skills:review"
