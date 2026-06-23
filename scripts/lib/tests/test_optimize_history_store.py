#!/usr/bin/env python3
"""optimize_history_store.py のテスト — accept/reject 履歴の DATA_DIR / project スコープ集約（ADR-031）。

worktree 安全な slug 解決（split-brain 防止の核心）と per-slug 分離を検証する。
git は subprocess で叩くが LLM は呼ばない（no-llm-in-tests 遵守）。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import optimize_history_store as store


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


class TestResolveSlug:
    def test_in_normal_repo_returns_repo_basename(self, tmp_path):
        repo = tmp_path / "my-project"
        _init_repo(repo)
        assert store.resolve_slug(cwd=repo) == "my-project"

    def test_in_worktree_returns_main_repo_basename(self, tmp_path):
        """worktree 内で worktree 名でなく本体 repo 名を返す（ADR-031 Decision 2 / 核心バグ）。"""
        repo = tmp_path / "main-repo"
        _init_repo(repo)
        wt = tmp_path / "worktrees" / "feature-x"
        _git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wt))
        # 素直な show-toplevel basename は "feature-x" になるが、store は本体名を返すべき
        assert store.resolve_slug(cwd=wt) == "main-repo"

    def test_outside_git_returns_unattributed(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert store.resolve_slug(cwd=plain) == store.UNATTRIBUTED_SLUG


class TestHistoryPath:
    def test_under_history_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        assert store.history_path("foo") == tmp_path / "optimize_history" / "foo.jsonl"

    def test_clean_slug_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        assert store.history_path("evolve-anything").name == "evolve-anything.jsonl"

    def test_unsafe_chars_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        # スペース・パス区切りは _ へ（traversal は構造上不可だが防御として）
        assert store.history_path("foo bar").name == "foo_bar.jsonl"
        assert store.history_path("a/b").name == "a_b.jsonl"

    def test_unattributed_preserved(self, tmp_path, monkeypatch):
        """先頭 _ の UNATTRIBUTED_SLUG がサニタイズで壊れない（routing 維持）。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        assert store.history_path(store.UNATTRIBUTED_SLUG).name == "_unattributed.jsonl"


class TestAppendAndLoad:
    def test_append_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        e1 = {"id": "a", "human_accepted": True, "best_fitness": 0.5}
        e2 = {"id": "b", "human_accepted": False, "best_fitness": 0.3}
        store.append_entry(e1, "proj")
        store.append_entry(e2, "proj")
        loaded = store.load_history("proj")
        assert loaded == [e1, e2]

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        assert store.load_history("nope") == []

    def test_append_creates_parent_dirs(self, tmp_path, monkeypatch):
        root = tmp_path / "deep" / "optimize_history"
        monkeypatch.setattr(store, "HISTORY_ROOT", root)
        store.append_entry({"id": "x"}, "proj")
        assert (root / "proj.jsonl").exists()

    def test_per_slug_separation(self, tmp_path, monkeypatch):
        """別 slug のレコードは混ざらない（pitfall_global_datadir_single_file 対策）。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        store.append_entry({"id": "a1"}, "proj-a")
        store.append_entry({"id": "b1"}, "proj-b")
        store.append_entry({"id": "a2"}, "proj-a")
        assert [r["id"] for r in store.load_history("proj-a")] == ["a1", "a2"]
        assert [r["id"] for r in store.load_history("proj-b")] == ["b1"]

    def test_load_skips_blank_and_malformed_lines(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        path = store.history_path("proj")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"id": "ok"}\n\nnot-json\n{"id": "ok2"}\n')
        loaded = store.load_history("proj")
        assert [r["id"] for r in loaded] == ["ok", "ok2"]


class TestLoadHistoryUnion:
    """load_history は canonical + legacy/plugins-data を cross-dir union read する（#45）。

    optimize_history/<slug>.jsonl が rename（rl-anything→evolve-anything）で legacy にのみ
    残ると、canonical だけ読む reader は fitness calibration の母集団を取り逃す。
    iter_read_data_dirs が HISTORY_ROOT.parent（=DATA_DIR）の親から候補を導出するため、
    canonical を tmp/evolve-anything にして兄弟 tmp/rl-anything を作れば hermetic に検証できる。
    write 側（append_entry）は canonical 固定のまま（ADR-049）。
    """

    @staticmethod
    def _canonical(root: Path) -> Path:
        c = root / "evolve-anything"
        (c / "optimize_history").mkdir(parents=True, exist_ok=True)
        return c

    @staticmethod
    def _write(dir_: Path, slug: str, records: list) -> None:
        oh = dir_ / "optimize_history"
        oh.mkdir(parents=True, exist_ok=True)
        (oh / f"{slug}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def test_unions_canonical_and_legacy(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy = tmp_path / "rl-anything"
        self._write(canonical, "proj", [{"id": "c1", "best_fitness": 0.5}])
        self._write(legacy, "proj", [{"id": "l1", "best_fitness": 0.3}])
        loaded = store.load_history("proj")
        assert sorted(r["id"] for r in loaded) == ["c1", "l1"]

    def test_canonical_wins_on_duplicate_id(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy = tmp_path / "rl-anything"
        self._write(canonical, "proj", [{"id": "x", "best_fitness": 0.9}])
        self._write(legacy, "proj", [{"id": "x", "best_fitness": 0.1}])
        loaded = store.load_history("proj")
        assert len(loaded) == 1
        assert loaded[0]["best_fitness"] == 0.9  # canonical 優先

    def test_records_without_id_all_kept(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy = tmp_path / "rl-anything"
        self._write(canonical, "proj", [{"best_fitness": 0.5}])
        self._write(legacy, "proj", [{"best_fitness": 0.3}])
        loaded = store.load_history("proj")
        assert len(loaded) == 2  # id 無しは dedup せず全件

    def test_hermetic_tmp_only_reads_canonical(self, tmp_path, monkeypatch):
        """兄弟 dir を作らなければ canonical のみ（実 home legacy を読まない）。"""
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(canonical, "proj", [{"id": "c1"}])
        loaded = store.load_history("proj")
        assert [r["id"] for r in loaded] == ["c1"]

    def test_empty_when_no_data(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        assert store.load_history("nope") == []

    def test_write_stays_canonical_only(self, tmp_path, monkeypatch):
        """append_entry は canonical のみへ書く（union read 化で write が漏れない・ADR-049）。"""
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        store.append_entry({"id": "new"}, "proj")
        assert (canonical / "optimize_history" / "proj.jsonl").exists()
        assert not (legacy / "optimize_history" / "proj.jsonl").exists()
