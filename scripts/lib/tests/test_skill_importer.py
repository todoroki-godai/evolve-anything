"""skill_importer モジュールのユニットテスト。

6 ケース:
- test_parse_github_short: "todoroki-godai/my-skill" → GitHubSource
- test_parse_local: "/tmp/my-skill" → LocalSource
- test_validate_missing_name: name フィールドなし → ValidationResult(valid=False)
- test_validate_name_collision: 既存スキルと同名 → errors に衝突エラー
- test_validate_ok: 正常な SKILL.md → valid=True
- test_install_copies_files: install_skill が skills/ にコピーする

注意: subprocess.run(["git", "clone", ...]) は unittest.mock.patch で mock する。
ファイルシステム操作は tmp_path fixture（pytest）で隔離する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import pytest

from skill_importer import (  # noqa: E402
    GitHubSource,
    LocalSource,
    SkillMetadata,
    ValidationResult,
    fetch_skill,
    install_skill,
    parse_source,
    validate_skill,
)


class TestParseSource:
    """parse_source() のソース解析テスト。"""

    def test_parse_github_short(self):
        """"todoroki-godai/my-skill" → GitHubSource(owner="todoroki-godai", repo="my-skill", subpath=None)"""
        result = parse_source("todoroki-godai/my-skill")
        assert isinstance(result, GitHubSource)
        assert result.owner == "todoroki-godai"
        assert result.repo == "my-skill"
        assert result.subpath is None

    def test_parse_github_with_subpath(self):
        """"todoroki-godai/my-repo/skills/my-skill" → GitHubSource(owner, repo, subpath)"""
        result = parse_source("todoroki-godai/my-repo/skills/my-skill")
        assert isinstance(result, GitHubSource)
        assert result.owner == "todoroki-godai"
        assert result.repo == "my-repo"
        assert result.subpath == "skills/my-skill"

    def test_parse_github_https_url(self):
        """https://github.com/... → GitHubSource"""
        result = parse_source("https://github.com/todoroki-godai/my-skill")
        assert isinstance(result, GitHubSource)
        assert result.owner == "todoroki-godai"
        assert result.repo == "my-skill"

    def test_parse_local(self):
        """/local/path → LocalSource(Path(...))"""
        result = parse_source("/tmp/my-skill")
        assert isinstance(result, LocalSource)
        assert result.path == Path("/tmp/my-skill")

    def test_parse_relative_existing_dir(self, tmp_path):
        """実在する相対パスは GitHubSource ではなく LocalSource になる。"""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        orig_cwd = Path.cwd()
        import os
        os.chdir(tmp_path)
        try:
            result = parse_source("my-skill")
            assert isinstance(result, LocalSource)
            assert result.path == Path("my-skill")
        finally:
            os.chdir(orig_cwd)

    def test_parse_nonexistent_relative_is_github(self):
        """実在しない相対パス文字列は owner/repo として解釈される。"""
        result = parse_source("todoroki-godai/nonexistent-skill-xyz")
        assert isinstance(result, GitHubSource)
        assert result.owner == "todoroki-godai"


class TestValidateSkill:
    """validate_skill() の検証テスト。"""

    def _make_skill_dir(self, tmp_path: Path, frontmatter: str) -> Path:
        """スキルディレクトリを作成し SKILL.md を書く。"""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\n{frontmatter}---\n\n# My Skill\n\nThis is a test skill.\n",
            encoding="utf-8",
        )
        return skill_dir

    def test_validate_missing_name(self, tmp_path):
        """name フィールドなし → ValidationResult(valid=False)"""
        skill_dir = self._make_skill_dir(
            tmp_path,
            "description: A test skill\nallowed-tools: [Bash]\n",
        )
        metadata, result = validate_skill(skill_dir)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_validate_missing_description(self, tmp_path):
        """description フィールドなし → ValidationResult(valid=False)"""
        skill_dir = self._make_skill_dir(
            tmp_path,
            "name: my-skill\n",
        )
        metadata, result = validate_skill(skill_dir)
        assert result.valid is False
        assert any("description" in e for e in result.errors)

    def test_validate_name_collision(self, tmp_path):
        """既存スキルと同名 → errors に衝突エラー"""
        # 既存スキルディレクトリを作成
        skills_dir = tmp_path / "skills"
        (skills_dir / "my-skill").mkdir(parents=True)

        # インポートするスキルディレクトリ
        skill_dir = self._make_skill_dir(
            tmp_path / "source",
            "name: my-skill\ndescription: A test skill\n",
        )
        metadata, result = validate_skill(skill_dir, skills_dir=skills_dir)
        assert result.valid is False
        assert any("collision" in e or "conflict" in e or "exist" in e for e in result.errors)

    def test_validate_ok(self, tmp_path):
        """正常な SKILL.md → valid=True"""
        skill_dir = self._make_skill_dir(
            tmp_path,
            "name: my-skill\ndescription: A test skill\nallowed-tools: [Bash]\n",
        )
        metadata, result = validate_skill(skill_dir)
        assert result.valid is True
        assert len(result.errors) == 0
        assert metadata.name == "my-skill"
        assert metadata.description == "A test skill"

    def test_validate_ok_with_scripts(self, tmp_path):
        """scripts/ ディレクトリがある場合は has_scripts=True"""
        skill_dir = self._make_skill_dir(
            tmp_path,
            "name: my-skill\ndescription: A test skill\n",
        )
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "helper.py").write_text("print('hello')", encoding="utf-8")

        metadata, result = validate_skill(skill_dir)
        assert result.valid is True
        assert metadata.has_scripts is True
        assert "scripts/helper.py" in metadata.script_files

    def test_validate_no_skill_md(self, tmp_path):
        """SKILL.md なし → valid=False"""
        skill_dir = tmp_path / "no-skill-md"
        skill_dir.mkdir()
        metadata, result = validate_skill(skill_dir)
        assert result.valid is False
        assert any("SKILL.md" in e for e in result.errors)


class TestInstallSkill:
    """install_skill() がファイルをコピーするテスト。"""

    def _make_metadata(self, source_path: Path, name: str = "my-skill") -> SkillMetadata:
        return SkillMetadata(
            name=name,
            description="A test skill",
            allowed_tools=["Bash"],
            source_path=source_path,
            has_scripts=False,
            script_files=[],
        )

    def test_install_copies_files(self, tmp_path):
        """install_skill が skills/ にコピーする"""
        # ソースディレクトリを準備
        source_dir = tmp_path / "source" / "my-skill"
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill\n---\n\n# My Skill\n",
            encoding="utf-8",
        )

        # インストール先
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        metadata = self._make_metadata(source_dir)
        install_skill(metadata, skills_dir)

        installed = skills_dir / "my-skill"
        assert installed.is_dir()
        assert (installed / "SKILL.md").exists()

    def test_install_force_overwrites(self, tmp_path):
        """force=True で名前衝突時に上書きする"""
        source_dir = tmp_path / "source" / "my-skill"
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text("new content", encoding="utf-8")

        skills_dir = tmp_path / "skills"
        existing = skills_dir / "my-skill"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("old content", encoding="utf-8")

        metadata = self._make_metadata(source_dir)
        install_skill(metadata, skills_dir, force=True)

        assert (skills_dir / "my-skill" / "SKILL.md").read_text() == "new content"

    def test_install_no_force_raises_on_collision(self, tmp_path):
        """force=False で名前衝突時は FileExistsError を投げる"""
        source_dir = tmp_path / "source" / "my-skill"
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text("content", encoding="utf-8")

        skills_dir = tmp_path / "skills"
        (skills_dir / "my-skill").mkdir(parents=True)

        metadata = self._make_metadata(source_dir)

        try:
            install_skill(metadata, skills_dir, force=False)
            assert False, "FileExistsError should have been raised"
        except FileExistsError:
            pass

    def test_install_copies_nested_files(self, tmp_path):
        """scripts/ サブディレクトリを含むスキルもすべてコピーする"""
        source_dir = tmp_path / "source" / "my-skill"
        scripts_dir = source_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text("content", encoding="utf-8")
        (scripts_dir / "helper.py").write_text("print('hello')", encoding="utf-8")

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        metadata = self._make_metadata(source_dir)
        metadata.has_scripts = True
        metadata.script_files = ["scripts/helper.py"]

        install_skill(metadata, skills_dir)

        assert (skills_dir / "my-skill" / "scripts" / "helper.py").exists()


class TestFetchSkill:
    """fetch_skill() のモックテスト。"""

    def test_fetch_github_calls_git_clone(self, tmp_path):
        """GitHubSource の fetch は git clone --depth=1 を呼ぶ"""
        import subprocess
        from unittest import mock

        (tmp_path / "my-skill").mkdir()
        source = GitHubSource(owner="todoroki-godai", repo="my-skill", subpath=None)
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            fetch_skill(source, tmp_path)
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "git"
            assert "--depth=1" in args

    def test_fetch_rejects_traversal_in_repo(self, tmp_path):
        """owner/../../etc のようなパス・トラバーサルは parse_source で弾かれる"""
        with pytest.raises(ValueError):
            parse_source("todoroki-godai/../../../etc")


class TestSecurityValidations:
    """セキュリティ修正のテスト。"""

    def test_http_url_rejected(self):
        """http:// URL は ValueError を投げる"""
        with pytest.raises(ValueError, match="http://"):
            parse_source("http://github.com/todoroki-godai/my-skill")

    def test_invalid_skill_name_rejected(self, tmp_path):
        """スキル名にパス区切り文字が含まれる場合は invalid"""
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ../evil\ndescription: bad\n---\n",
            encoding="utf-8",
        )
        metadata, result = validate_skill(skill_dir)
        assert result.valid is False
        assert any("スキル名" in e for e in result.errors)

    def test_traversal_in_owner_rejected(self):
        """owner に dotdot が含まれる場合は ValueError"""
        with pytest.raises(ValueError):
            parse_source("https://github.com/ow..ner/my-skill")

    def test_install_skill_path_traversal_guard(self, tmp_path):
        """install_skill はパス・トラバーサルを拒否する"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # metadata.name に .. を含む不正なパスを直接渡す
        metadata = SkillMetadata(
            name="../evil",
            description="evil skill",
            allowed_tools=[],
            source_path=source_dir,
            has_scripts=False,
            script_files=[],
        )
        with pytest.raises(ValueError, match="不正なインストール先"):
            install_skill(metadata, skills_dir)
