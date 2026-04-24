#!/usr/bin/env python3
"""fleet_config.py のテスト — fleet の tracked/ignored projects config."""

import json
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import fleet_config


class TestLoadSaveConfig:
    def test_load_missing_config_returns_empty_default(self, tmp_path, monkeypatch):
        config_path = tmp_path / "fleet-config.json"
        monkeypatch.setattr(fleet_config, "CONFIG_PATH", config_path)
        result = fleet_config.load_config()
        assert result == {
            "tracked_projects": [],
            "ignored_projects": [],
            "last_discovery": None,
        }

    def test_load_existing_config(self, tmp_path, monkeypatch):
        config_path = tmp_path / "fleet-config.json"
        data = {
            "tracked_projects": ["/home/u/a", "/home/u/b"],
            "ignored_projects": ["/home/u/c"],
            "last_discovery": "2026-04-24T00:00:00Z",
        }
        config_path.write_text(json.dumps(data))
        monkeypatch.setattr(fleet_config, "CONFIG_PATH", config_path)
        assert fleet_config.load_config() == data

    def test_save_creates_parent_dir(self, tmp_path, monkeypatch):
        config_path = tmp_path / "nested" / "dir" / "fleet-config.json"
        monkeypatch.setattr(fleet_config, "CONFIG_PATH", config_path)
        fleet_config.save_config({"tracked_projects": ["/x"]})
        assert config_path.exists()
        assert json.loads(config_path.read_text())["tracked_projects"] == ["/x"]

    def test_load_corrupt_config_returns_default(self, tmp_path, monkeypatch):
        config_path = tmp_path / "fleet-config.json"
        config_path.write_text("{ corrupted json")
        monkeypatch.setattr(fleet_config, "CONFIG_PATH", config_path)
        # 破損時はデフォルトを返す（クラッシュしない）
        result = fleet_config.load_config()
        assert result["tracked_projects"] == []


class TestDiscoverCCProjects:
    def test_reads_cwd_from_jsonl_session(self, tmp_path, monkeypatch):
        projects_root = tmp_path / "projects"
        slug_dir = projects_root / "-Users-foo-work-sys-bots"
        slug_dir.mkdir(parents=True)
        # Session JSONL with cwd field on line 2 (first line has no cwd)
        lines = [
            '{"type":"permission-mode","permissionMode":"default"}',
            '{"cwd":"/Users/foo/work/sys-bots","other":"data"}',
        ]
        (slug_dir / "session-1.jsonl").write_text("\n".join(lines))
        monkeypatch.setattr(fleet_config, "CC_PROJECTS_ROOT", projects_root)

        result = fleet_config.discover_cc_projects()
        assert Path("/Users/foo/work/sys-bots") in result

    def test_skips_slug_dir_without_jsonl(self, tmp_path, monkeypatch):
        projects_root = tmp_path / "projects"
        (projects_root / "-empty-dir").mkdir(parents=True)
        monkeypatch.setattr(fleet_config, "CC_PROJECTS_ROOT", projects_root)
        result = fleet_config.discover_cc_projects()
        assert result == []

    def test_dedups_multiple_slug_dirs_same_cwd(self, tmp_path, monkeypatch):
        """worktree 等で複数の slug dir が同じ cwd を指す場合は 1 個に寄せる。"""
        projects_root = tmp_path / "projects"
        for name in ("-Users-a-proj", "-Users-a-proj-wt1"):
            slug_dir = projects_root / name
            slug_dir.mkdir(parents=True)
            (slug_dir / "s.jsonl").write_text(
                '{"cwd":"/Users/a/proj"}\n'
            )
        monkeypatch.setattr(fleet_config, "CC_PROJECTS_ROOT", projects_root)
        result = fleet_config.discover_cc_projects()
        assert result == [Path("/Users/a/proj")]

    def test_handles_missing_projects_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fleet_config, "CC_PROJECTS_ROOT", tmp_path / "nonexistent")
        assert fleet_config.discover_cc_projects() == []

    def test_skips_malformed_jsonl_lines(self, tmp_path, monkeypatch):
        projects_root = tmp_path / "projects"
        slug_dir = projects_root / "-Users-foo-bar"
        slug_dir.mkdir(parents=True)
        lines = [
            "not valid json",
            '{"no_cwd":"here"}',
            '{"cwd":"/Users/foo/bar"}',
        ]
        (slug_dir / "s.jsonl").write_text("\n".join(lines))
        monkeypatch.setattr(fleet_config, "CC_PROJECTS_ROOT", projects_root)
        result = fleet_config.discover_cc_projects()
        assert Path("/Users/foo/bar") in result


class TestFilterValidProjects:
    def test_keeps_only_paths_with_claude_md_or_dot_claude(self, tmp_path):
        pj_with_md = tmp_path / "pj_md"
        pj_with_md.mkdir()
        (pj_with_md / "CLAUDE.md").write_text("# Test")

        pj_with_dot = tmp_path / "pj_dot"
        (pj_with_dot / ".claude").mkdir(parents=True)

        pj_plain = tmp_path / "pj_plain"
        pj_plain.mkdir()

        nonexistent = tmp_path / "gone"

        result = fleet_config.filter_valid_projects(
            [pj_with_md, pj_with_dot, pj_plain, nonexistent]
        )
        assert pj_with_md in result
        assert pj_with_dot in result
        assert pj_plain not in result
        assert nonexistent not in result

    def test_ignores_files(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")
        assert fleet_config.filter_valid_projects([f]) == []

    def test_excludes_home_directory_itself(self, tmp_path, monkeypatch):
        """$HOME 自体は CC 本体の .claude/ を持つが PJ 実体ではないので除外。"""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        (fake_home / ".claude").mkdir()  # 本体のインストール痕跡
        pj = fake_home / "real_pj"
        pj.mkdir()
        (pj / "CLAUDE.md").write_text("# Test")

        monkeypatch.setenv("HOME", str(fake_home))
        # Path.home() は HOME env var を見る
        result = fleet_config.filter_valid_projects([fake_home, pj])
        assert fake_home not in result
        assert pj in result


class TestDiffCandidates:
    def test_new_paths_excluding_tracked_and_ignored(self):
        config = {
            "tracked_projects": ["/a/tracked"],
            "ignored_projects": ["/a/ignored"],
        }
        discovered = [Path("/a/tracked"), Path("/a/ignored"), Path("/a/new")]
        result = fleet_config.diff_candidates(config, discovered)
        assert result == [Path("/a/new")]

    def test_empty_config_returns_all(self):
        config = {"tracked_projects": [], "ignored_projects": []}
        discovered = [Path("/a"), Path("/b")]
        result = fleet_config.diff_candidates(config, discovered)
        assert set(result) == {Path("/a"), Path("/b")}

    def test_missing_keys_treated_as_empty(self):
        """古い config（tracked_projects のみ）でも動く。"""
        config = {"tracked_projects": ["/a"]}
        discovered = [Path("/a"), Path("/b")]
        result = fleet_config.diff_candidates(config, discovered)
        assert result == [Path("/b")]


class TestAddProject:
    def test_track_adds_to_tracked_and_removes_from_ignored(self):
        config = {
            "tracked_projects": [],
            "ignored_projects": ["/a/shared"],
        }
        fleet_config.track_project(config, Path("/a/shared"))
        assert "/a/shared" in config["tracked_projects"]
        assert "/a/shared" not in config["ignored_projects"]

    def test_ignore_adds_to_ignored_and_removes_from_tracked(self):
        config = {
            "tracked_projects": ["/a/shared"],
            "ignored_projects": [],
        }
        fleet_config.ignore_project(config, Path("/a/shared"))
        assert "/a/shared" in config["ignored_projects"]
        assert "/a/shared" not in config["tracked_projects"]

    def test_track_is_idempotent(self):
        config = {"tracked_projects": ["/a"], "ignored_projects": []}
        fleet_config.track_project(config, Path("/a"))
        assert config["tracked_projects"].count("/a") == 1
