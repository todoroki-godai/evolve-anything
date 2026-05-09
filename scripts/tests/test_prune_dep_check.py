#!/usr/bin/env python3
"""prune.check_import_dependencies / archive_file dep guard のテスト。

Issue #25: skill 削除時に Python import 依存の検査漏れ。
内部スキル削除時、scripts/ 配下のモジュールが他スキル/CLI から import されているかを
検査せずに archive し、依存先が壊れた事例があった。
"""
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))


def _git_init(repo: Path) -> None:
    """テスト用 repo を git 初期化（git grep 経路をテストするため）。失敗時は無視。"""
    import subprocess
    try:
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, timeout=10)
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, timeout=10)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "init"],
            cwd=str(repo), check=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # git 未利用環境では fallback 経路のみテストされる


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """テスト用の repo レイアウトを生成する。

    skills/foo/scripts/foo_helper.py を持ち、
    skills/bar/scripts/bar.py が `from foo_helper import ...` で参照する。
    """
    repo = tmp_path / "repo"
    (repo / "skills" / "foo" / "scripts").mkdir(parents=True)
    (repo / "skills" / "foo" / "SKILL.md").write_text("# foo skill\n", encoding="utf-8")
    (repo / "skills" / "foo" / "scripts" / "foo_helper.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (repo / "skills" / "foo" / "scripts" / "__init__.py").write_text("", encoding="utf-8")

    (repo / "skills" / "bar" / "scripts").mkdir(parents=True)
    (repo / "skills" / "bar" / "SKILL.md").write_text("# bar skill\n", encoding="utf-8")
    (repo / "skills" / "bar" / "scripts" / "bar.py").write_text(
        "from foo_helper import helper\nprint(helper())\n", encoding="utf-8"
    )

    (repo / "bin").mkdir()
    (repo / "bin" / "rl-tool").write_text(
        "#!/bin/bash\npython3 skills/foo/scripts/foo_helper.py\n", encoding="utf-8"
    )
    _git_init(repo)
    return repo


@pytest.fixture
def dotted_import_repo(tmp_path: Path) -> Path:
    """`import foo_helper.sub` / `import foo_helper as fh` 形式の参照（F2）。"""
    repo = tmp_path / "repo"
    (repo / "skills" / "foo" / "scripts").mkdir(parents=True)
    (repo / "skills" / "foo" / "SKILL.md").write_text("# foo\n", encoding="utf-8")
    (repo / "skills" / "foo" / "scripts" / "foo_helper.py").write_text(
        "def h(): return 1\n", encoding="utf-8"
    )
    (repo / "skills" / "bar1" / "scripts").mkdir(parents=True)
    (repo / "skills" / "bar1" / "SKILL.md").write_text("# bar1\n", encoding="utf-8")
    (repo / "skills" / "bar1" / "scripts" / "bar1.py").write_text(
        "import foo_helper.sub\n", encoding="utf-8"
    )
    (repo / "skills" / "bar2" / "scripts").mkdir(parents=True)
    (repo / "skills" / "bar2" / "SKILL.md").write_text("# bar2\n", encoding="utf-8")
    (repo / "skills" / "bar2" / "scripts" / "bar2.py").write_text(
        "import foo_helper as fh\n", encoding="utf-8"
    )
    _git_init(repo)
    return repo


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Path:
    """foo skill が誰からも参照されていない repo。"""
    repo = tmp_path / "repo"
    (repo / "skills" / "foo" / "scripts").mkdir(parents=True)
    (repo / "skills" / "foo" / "SKILL.md").write_text("# foo skill\n", encoding="utf-8")
    (repo / "skills" / "foo" / "scripts" / "foo_helper.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    # foo 内部のみ参照（自身ディレクトリは除外されるべき）
    (repo / "skills" / "foo" / "scripts" / "main.py").write_text(
        "from foo_helper import helper\n", encoding="utf-8"
    )
    return repo


class TestCheckImportDependencies:
    def test_detects_import_from_other_skill(self, fake_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = fake_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, fake_repo)
        # bar.py が foo_helper を import している
        assert any(
            d["kind"] == "import" and "bar.py" in d["referrer"] for d in deps
        ), f"Expected bar.py import detected, got: {deps}"

    def test_detects_path_ref_from_bin(self, fake_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = fake_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, fake_repo)
        # bin/rl-tool が skills/foo/ パスを参照
        assert any(
            d["kind"] == "path_ref" and "rl-tool" in d["referrer"] for d in deps
        ), f"Expected rl-tool path_ref detected, got: {deps}"

    def test_excludes_self_dir(self, isolated_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = isolated_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, isolated_repo)
        # 自身ディレクトリ内の import は除外される
        assert deps == [], f"Expected no deps (self-dir excluded), got: {deps}"

    def test_returns_list_of_dicts(self, fake_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = fake_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, fake_repo)
        for d in deps:
            assert "referrer" in d
            assert "kind" in d
            assert d["kind"] in ("import", "path_ref")
            assert "match" in d


class TestArchiveFileDepGuard:
    def test_skill_dir_archive_raises_on_deps(
        self, fake_repo: Path, monkeypatch
    ) -> None:
        from prune import archive_file, SkillDependencyError
        # ARCHIVE_DIR をテスト用に差し替え
        import prune
        monkeypatch.setattr(prune, "ARCHIVE_DIR", fake_repo / "_archive")
        # repo_root を fake_repo に固定
        skill_path = fake_repo / "skills" / "foo"
        with pytest.raises(SkillDependencyError) as exc:
            archive_file(str(skill_path), "test_archive", repo_root=fake_repo)
        assert "foo" in str(exc.value) or "depend" in str(exc.value).lower()

    def test_skill_dir_archive_force_succeeds(
        self, fake_repo: Path, monkeypatch
    ) -> None:
        from prune import archive_file
        import prune
        monkeypatch.setattr(prune, "ARCHIVE_DIR", fake_repo / "_archive")
        skill_path = fake_repo / "skills" / "foo"
        result = archive_file(
            str(skill_path), "test_archive", force=True, repo_root=fake_repo
        )
        assert result is not None
        assert not skill_path.exists(), "skill dir should be moved"

    def test_isolated_skill_archive_succeeds(
        self, isolated_repo: Path, monkeypatch
    ) -> None:
        from prune import archive_file
        import prune
        monkeypatch.setattr(prune, "ARCHIVE_DIR", isolated_repo / "_archive")
        skill_path = isolated_repo / "skills" / "foo"
        result = archive_file(
            str(skill_path), "test_archive", repo_root=isolated_repo
        )
        assert result is not None
        assert not skill_path.exists()

    def test_single_file_archive_unaffected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """skill ディレクトリでない単一ファイルの archive は dep check しない（既存動作維持）。"""
        from prune import archive_file
        import prune
        monkeypatch.setattr(prune, "ARCHIVE_DIR", tmp_path / "_archive")
        target = tmp_path / "some_file.md"
        target.write_text("hello", encoding="utf-8")
        result = archive_file(str(target), "test")
        assert result is not None
        assert not target.exists()


class TestSkillDependencyError:
    def test_error_class_exists(self) -> None:
        from prune import SkillDependencyError
        err = SkillDependencyError("foo has 2 referrers", referrers=[])
        assert isinstance(err, Exception)


class TestImportRegexCoverage:
    """F2: dotted import / aliased import を正しく検出する。"""

    def test_detects_dotted_import(self, dotted_import_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = dotted_import_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, dotted_import_repo)
        assert any(
            d["kind"] == "import" and "bar1.py" in d["referrer"] for d in deps
        ), f"Expected `import foo_helper.sub` detected, got: {deps}"

    def test_detects_aliased_import(self, dotted_import_repo: Path) -> None:
        from prune import check_import_dependencies
        skill_path = dotted_import_repo / "skills" / "foo"
        deps = check_import_dependencies(skill_path, dotted_import_repo)
        assert any(
            d["kind"] == "import" and "bar2.py" in d["referrer"] for d in deps
        ), f"Expected `import foo_helper as fh` detected, got: {deps}"


class TestErrorMessageQuality:
    """F1: SkillDependencyError メッセージが運用判断に必要な情報を含む。"""

    def test_message_includes_module_collision_hint(
        self, fake_repo: Path, monkeypatch
    ) -> None:
        from prune import archive_file, SkillDependencyError
        import prune
        monkeypatch.setattr(prune, "ARCHIVE_DIR", fake_repo / "_archive")
        skill_path = fake_repo / "skills" / "foo"
        with pytest.raises(SkillDependencyError) as exc:
            archive_file(str(skill_path), "test", repo_root=fake_repo)
        msg = str(exc.value).lower()
        assert "force=true" in msg, "メッセージに force=True バイパス手順が含まれること"
