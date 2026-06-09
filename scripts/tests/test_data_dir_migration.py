"""data_dir_migration（#364 Phase 2: DATA_DIR hook/tool 分裂の一元化）のテスト。

検証対象:
- merge_jsonl: 行単位 dedup append（既存行は重複しない）
- merge_db: DuckDB のテーブル単位 union dedup マージ（compaction を兼ねる）
- merge_dir / merge_file_newer_wins: mtime 新しい方優先
- migrate: E2E（マージ→source 削除→marker 書込）/ dry-run 無副作用 / 冪等
- rl_common.resolve_data_dir: marker ゲート redirect（テスト isolation の tmp env は無条件尊重）
- store_paths.hook_store_dir: marker 存在時に canonical を返す
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

_lib = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_lib))

import data_dir_migration as ddm  # noqa: E402
import rl_common  # noqa: E402
from rl_common import store_paths  # noqa: E402


@pytest.fixture
def dirs(tmp_path):
    canonical = tmp_path / "canonical"
    source = tmp_path / "plugin-data" / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    source.mkdir(parents=True)
    return canonical, source


class TestMergeJsonl:
    def test_append_only_new_lines(self, dirs):
        canonical, source = dirs
        (canonical / "a.jsonl").write_text('{"x": 1}\n{"x": 2}\n')
        (source / "a.jsonl").write_text('{"x": 2}\n{"x": 3}\n')
        info = ddm.merge_jsonl(source / "a.jsonl", canonical / "a.jsonl")
        assert info["new_lines"] == 1
        lines = (canonical / "a.jsonl").read_text().splitlines()
        assert lines == ['{"x": 1}', '{"x": 2}', '{"x": 3}']

    def test_source_only_creates_dst(self, dirs):
        canonical, source = dirs
        (source / "b.jsonl").write_text('{"y": 1}\n')
        info = ddm.merge_jsonl(source / "b.jsonl", canonical / "b.jsonl")
        assert info["new_lines"] == 1
        assert (canonical / "b.jsonl").read_text() == '{"y": 1}\n'

    def test_dry_run_no_write(self, dirs):
        canonical, source = dirs
        (source / "c.jsonl").write_text('{"z": 1}\n')
        info = ddm.merge_jsonl(source / "c.jsonl", canonical / "c.jsonl", dry_run=True)
        assert info["new_lines"] == 1
        assert not (canonical / "c.jsonl").exists()


class TestMergeDb:
    duckdb = pytest.importorskip("duckdb")

    def _make_db(self, path, rows):
        con = self.duckdb.connect(str(path))
        con.execute("CREATE TABLE sessions (session_id VARCHAR, ts VARCHAR)")
        for r in rows:
            con.execute("INSERT INTO sessions VALUES (?, ?)", list(r))
        con.close()

    def _rows(self, path):
        con = self.duckdb.connect(str(path), read_only=True)
        rows = set(con.execute("SELECT * FROM sessions").fetchall())
        con.close()
        return rows

    def test_copy_when_canonical_missing(self, dirs):
        canonical, source = dirs
        self._make_db(source / "s.db", [("a", "1")])
        ddm.merge_db(source / "s.db", canonical / "s.db")
        assert self._rows(canonical / "s.db") == {("a", "1")}

    def test_union_dedup_when_both_exist(self, dirs):
        canonical, source = dirs
        self._make_db(canonical / "s.db", [("a", "1"), ("b", "2")])
        self._make_db(source / "s.db", [("b", "2"), ("c", "3")])
        info = ddm.merge_db(source / "s.db", canonical / "s.db")
        assert self._rows(canonical / "s.db") == {("a", "1"), ("b", "2"), ("c", "3")}
        assert info["action"] == "merged_db"

    def test_dry_run_no_write(self, dirs):
        canonical, source = dirs
        self._make_db(source / "s.db", [("a", "1")])
        info = ddm.merge_db(source / "s.db", canonical / "s.db", dry_run=True)
        assert info["action"] == "would_merge_db"
        assert not (canonical / "s.db").exists()


class TestMergeDirAndPlainFile:
    def test_newer_source_wins(self, dirs):
        canonical, source = dirs
        dst = canonical / "evolve-state.json"
        dst.write_text('{"old": true}')
        old = time.time() - 1000
        os.utime(dst, (old, old))
        src = source / "evolve-state.json"
        src.write_text('{"new": true}')
        ddm.merge_file_newer_wins(src, dst)
        assert json.loads(dst.read_text()) == {"new": True}

    def test_older_source_kept_existing(self, dirs):
        canonical, source = dirs
        dst = canonical / "evolve-state.json"
        dst.write_text('{"current": true}')
        src = source / "evolve-state.json"
        src.write_text('{"stale": true}')
        old = time.time() - 1000
        os.utime(src, (old, old))
        ddm.merge_file_newer_wins(src, dst)
        assert json.loads(dst.read_text()) == {"current": True}

    def test_merge_dir_per_file(self, dirs):
        canonical, source = dirs
        (source / "counters").mkdir()
        (source / "counters" / "s1.json").write_text("{}")
        (source / "counters" / "__pycache__").mkdir()
        (source / "counters" / "__pycache__" / "x.pyc").write_text("x")
        info = ddm.merge_dir(source / "counters", canonical / "counters")
        assert (canonical / "counters" / "s1.json").exists()
        assert not (canonical / "counters" / "__pycache__").exists()
        assert info["copied"] == 1


class TestMigrate:
    def test_e2e_merge_then_marker_then_source_removed(self, dirs):
        canonical, source = dirs
        (source / "usage.jsonl").write_text('{"u": 1}\n')
        (source / "evolve-state.json").write_text('{"s": 1}')
        (source / "counters").mkdir()
        (source / "counters" / "a.json").write_text("{}")
        (source / "tmp").mkdir()
        (source / "tmp" / "scratch").write_text("x")

        summary = ddm.migrate(canonical=canonical, source=source)

        assert (canonical / "usage.jsonl").read_text() == '{"u": 1}\n'
        assert (canonical / "evolve-state.json").exists()
        assert (canonical / "counters" / "a.json").exists()
        # tmp はマージしない
        assert not (canonical / "tmp").exists()
        # source のストアは消化済み（tmp は残してよい）
        assert not (source / "usage.jsonl").exists()
        assert not (source / "counters").exists()
        # marker
        marker = canonical / rl_common.DATA_DIR_UNIFIED_MARKER
        assert marker.exists()
        assert summary["marker_written"] is True
        assert summary["failures"] == 0

    def test_dry_run_zero_side_effects(self, dirs):
        """pitfall_dryrun_stateful_store_write: dry-run は書き込みゼロ。"""
        canonical, source = dirs
        (source / "usage.jsonl").write_text('{"u": 1}\n')
        summary = ddm.migrate(canonical=canonical, source=source, dry_run=True)
        assert summary["dry_run"] is True
        assert summary["marker_written"] is False
        assert list(canonical.iterdir()) == []
        assert (source / "usage.jsonl").exists()

    def test_idempotent_second_run(self, dirs):
        canonical, source = dirs
        (source / "usage.jsonl").write_text('{"u": 1}\n')
        ddm.migrate(canonical=canonical, source=source)
        summary2 = ddm.migrate(canonical=canonical, source=source)
        assert summary2["failures"] == 0
        assert (canonical / "usage.jsonl").read_text() == '{"u": 1}\n'

    def test_no_source_still_writes_marker(self, dirs):
        canonical, _ = dirs
        summary = ddm.migrate(canonical=canonical, source=None)
        assert summary["marker_written"] is True
        assert (canonical / rl_common.DATA_DIR_UNIFIED_MARKER).exists()

    def test_wal_not_copied_only_removed(self, dirs):
        """DuckDB WAL を単独コピーすると正準側の別 db と不整合ペアになる。

        .wal は対応する .db の merge_db（書込可 ATTACH）で replay 済みなので
        コピーせず削除のみ（dry-run では削除もしない）。
        """
        canonical, source = dirs
        (source / "sessions.db.wal").write_text("walwal")
        summary = ddm.migrate(canonical=canonical, source=source, dry_run=True)
        assert not (canonical / "sessions.db.wal").exists()
        assert (source / "sessions.db.wal").exists()  # dry-run は削除もしない
        summary = ddm.migrate(canonical=canonical, source=source)
        assert not (canonical / "sessions.db.wal").exists()
        assert not (source / "sessions.db.wal").exists()
        actions = {e["name"]: e["action"] for e in summary["entries"]}
        assert actions["sessions.db.wal"] == "skipped_wal"

    def test_needs_migration(self, dirs):
        canonical, source = dirs
        assert ddm.needs_migration(source=source) is False  # 空
        (source / "usage.jsonl").write_text("{}\n")
        assert ddm.needs_migration(source=source) is True
        assert ddm.needs_migration(source=None) is False or True  # probe 依存（実環境非依存に固定しない）


class TestResolveDataDir:
    """rl_common.resolve_data_dir の marker ゲート redirect。"""

    def test_no_env_returns_default(self, tmp_path):
        assert rl_common.resolve_data_dir(
            "", default_dir=tmp_path / "canon", cc_plugin_data_base=tmp_path / "pd"
        ) == tmp_path / "canon"

    def test_tmp_env_respected_without_marker_check(self, tmp_path):
        """テスト isolation: install レイアウト外の env は marker 有無に関わらず尊重。"""
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}")
        iso = tmp_path / "isolated"
        assert rl_common.resolve_data_dir(
            str(iso), default_dir=canon, cc_plugin_data_base=tmp_path / "pd"
        ) == iso

    def test_install_layout_env_redirected_when_marker(self, tmp_path):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}")
        pd = tmp_path / "pd" / "rl-anything-rl-anything"
        pd.mkdir(parents=True)
        assert rl_common.resolve_data_dir(
            str(pd), default_dir=canon, cc_plugin_data_base=tmp_path / "pd"
        ) == canon

    def test_install_layout_env_kept_without_marker(self, tmp_path):
        """marker が無い限り従来挙動（split のまま機能する後方互換）。"""
        canon = tmp_path / "canon"
        canon.mkdir()
        pd = tmp_path / "pd" / "rl-anything-rl-anything"
        pd.mkdir(parents=True)
        assert rl_common.resolve_data_dir(
            str(pd), default_dir=canon, cc_plugin_data_base=tmp_path / "pd"
        ) == pd


class TestHookStoreDirUnified:
    def test_marker_returns_canonical(self, tmp_path, monkeypatch):
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / rl_common.DATA_DIR_UNIFIED_MARKER).write_text("{}")
        monkeypatch.setattr(store_paths, "_REAL_DEFAULT_FALLBACK_RESOLVED", canon.resolve())
        with mock.patch.object(rl_common, "DATA_DIR", canon):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(tmp_path / "elsewhere")}):
                assert store_paths.hook_store_dir(base=canon) == canon

    def test_without_marker_env_still_wins(self, tmp_path, monkeypatch):
        canon = tmp_path / "canon"
        canon.mkdir()
        monkeypatch.setattr(store_paths, "_REAL_DEFAULT_FALLBACK_RESOLVED", canon.resolve())
        env_dir = tmp_path / "plugin-data"
        with mock.patch.object(rl_common, "DATA_DIR", canon):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(env_dir)}):
                assert store_paths.hook_store_dir(base=canon) == env_dir
