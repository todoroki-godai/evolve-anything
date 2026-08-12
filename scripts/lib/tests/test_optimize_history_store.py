#!/usr/bin/env python3
"""optimize_history_store.py のテスト — accept/reject 履歴の DATA_DIR / project スコープ集約（ADR-031）。

worktree 安全な slug 解決（split-brain 防止の核心）と per-slug 分離を検証する。
git は subprocess で叩くが LLM は呼ばない（no-llm-in-tests 遵守）。
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import optimize_history_store as store


@pytest.fixture
def fixed_tz_tokyo():
    """プロセス TZ を Asia/Tokyo に固定し、テスト後に必ず元へ戻す。

    ``time.tzset()`` は環境全体（プロセス）に効くため、元に戻し忘れると同一
    xdist worker 内の後続テストの ``datetime.astimezone()`` 解釈まで汚染する
    （restore は monkeypatch の自動リストアに頼らず try/finally で自前保証する）。
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


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

    def test_outside_git_returns_basename(self, tmp_path):
        # #47: 非git dir は basename（writer pj_slug_fast と一致・resolve_pj_slug 単一ソース）。
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert store.resolve_slug(cwd=plain) == "not-a-repo"


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

    def test_append_entry_normalizes_naive_timestamp(self, tmp_path, monkeypatch):
        """#297: append_entry を通した naive timestamp は aware UTC で永続化される。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        entry = {"id": "a", "timestamp": "2026-07-31T09:00:00"}
        store.append_entry(entry, "proj")
        loaded = store.load_history("proj")
        dt = datetime.fromisoformat(loaded[0]["timestamp"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_append_entry_adds_timestamp_when_missing(self, tmp_path, monkeypatch):
        """#297: timestamp キー無し entry にも store 側が aware UTC を付与する。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        entry = {"id": "a"}
        store.append_entry(entry, "proj")
        loaded = store.load_history("proj")
        assert "timestamp" in loaded[0]
        dt = datetime.fromisoformat(loaded[0]["timestamp"])
        assert dt.tzinfo is not None


class TestNormalizeEntryTimestamp:
    """#297: optimize_history の timestamp tz 不統一を chokepoint で吸収する。"""

    def test_missing_timestamp_gets_aware_utc(self):
        entry = {"id": "a"}
        store.normalize_entry_timestamp(entry)
        dt = datetime.fromisoformat(entry["timestamp"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_none_timestamp_gets_aware_utc(self):
        entry = {"id": "a", "timestamp": None}
        store.normalize_entry_timestamp(entry)
        dt = datetime.fromisoformat(entry["timestamp"])
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_naive_timestamp_interpreted_as_local_then_converted_to_utc(self):
        """naive は queue_verify._parse_iso と同じ流儀（astimezone）でローカル解釈してから
        UTC 化する。読み書きの解釈がずれると 9 時間ずれが別の形で再発するため、
        astimezone() を直接使った期待値と突き合わせて一致を検証する。
        """
        naive = "2026-07-31T09:00:00"
        entry = {"id": "a", "timestamp": naive}
        store.normalize_entry_timestamp(entry)

        expected = datetime.fromisoformat(naive).astimezone().astimezone(timezone.utc)
        dt = datetime.fromisoformat(entry["timestamp"])
        assert dt.tzinfo is not None
        assert dt == expected

    def test_aware_timestamp_normalized_to_utc_without_changing_instant(self):
        aware = "2026-07-31T09:00:00+09:00"
        entry = {"id": "a", "timestamp": aware}
        store.normalize_entry_timestamp(entry)

        dt = datetime.fromisoformat(entry["timestamp"])
        original = datetime.fromisoformat(aware)
        assert dt.tzinfo is not None
        assert dt == original  # instant は不変
        assert dt.utcoffset() == timedelta(0)  # 表記は UTC に統一

    def test_zulu_timestamp_normalized(self):
        entry = {"id": "a", "timestamp": "2026-07-31T00:00:00Z"}
        store.normalize_entry_timestamp(entry)
        dt = datetime.fromisoformat(entry["timestamp"])
        assert dt.utcoffset() == timedelta(0)

    def test_non_string_timestamp_left_unchanged(self):
        entry = {"id": "a", "timestamp": 12345}
        store.normalize_entry_timestamp(entry)
        assert entry["timestamp"] == 12345

    def test_unparsable_timestamp_left_unchanged(self):
        entry = {"id": "a", "timestamp": "not-a-timestamp"}
        store.normalize_entry_timestamp(entry)
        assert entry["timestamp"] == "not-a-timestamp"


class TestNormalizeEntryTimestampTZDependent:
    """#297 fixup (P1-2): naive 解釈が実行機 TZ に依存する核心動作を固定値で検証する。

    ``TestNormalizeEntryTimestamp`` の naive ケースは期待値・実装の両方を同じ
    プロセスローカル ``astimezone()`` から計算しているため、CI が UTC 環境だと
    「naive を誤って ``replace(tzinfo=UTC)`` する（TZ 変換をしない）実装」でも
    green になり、#297 が直そうとしたバグそのものを検出できない。TZ=Asia/Tokyo に
    固定し、期待値をハードコードして初めて「ローカル解釈→UTC変換」を検査できる。
    """

    def test_naive_interpreted_as_jst_converts_to_utc(self, fixed_tz_tokyo):
        entry = {"id": "a", "timestamp": "2026-07-31T09:00:00"}
        store.normalize_entry_timestamp(entry)
        assert entry["timestamp"] == "2026-07-31T00:00:00+00:00"

    def test_naive_interpreted_as_jst_across_midnight(self, fixed_tz_tokyo):
        """日付を跨ぐケース（JST 03:00 = 前日 UTC 18:00）でも変換方向を取り違えない。"""
        entry = {"id": "a", "timestamp": "2026-07-31T03:00:00"}
        store.normalize_entry_timestamp(entry)
        assert entry["timestamp"] == "2026-07-30T18:00:00+00:00"

    def test_aware_timestamp_unaffected_by_process_tz(self, fixed_tz_tokyo):
        """aware 入力は instant を持つため、プロセス TZ に関わらず変換結果は同じ。"""
        entry = {"id": "a", "timestamp": "2026-07-31T09:00:00+09:00"}
        store.normalize_entry_timestamp(entry)
        assert entry["timestamp"] == "2026-07-31T00:00:00+00:00"


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


class TestLoadRawHistoryAndBackCompat:
    """#402 PR-2 段階2 §5: 正準名は ``load_raw_history``。``load_history`` は後方互換 wrapper。

    ``load_raw_history`` は旧 ``load_history`` の実装そのもの（挙動不変・単なる rename）。
    """

    def test_load_raw_history_matches_load_history_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        store.append_entry({"id": "a"}, "proj")
        store.append_entry({"id": "b"}, "proj")
        assert store.load_raw_history("proj") == store.load_history("proj")

    def test_load_history_delegates_to_load_raw_history(self, tmp_path, monkeypatch):
        """``load_history`` は単なる別名（代入）でなく ``load_raw_history`` を呼ぶ wrapper。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        store.append_entry({"id": "a"}, "proj")
        calls = []
        original = store.load_raw_history

        def spy(slug):
            calls.append(slug)
            return original(slug)

        monkeypatch.setattr(store, "load_raw_history", spy)
        result = store.load_history("proj")
        assert calls == ["proj"]
        assert [r["id"] for r in result] == ["a"]

    def test_load_history_and_load_raw_history_are_distinct_functions(self):
        """単なる別名（``load_history = load_raw_history``）にしない契約。"""
        assert store.load_history is not store.load_raw_history
        assert store.load_history.__doc__ != store.load_raw_history.__doc__


class TestRevertEventSchema:
    """#402 PR-2 段階2 §1: revert イベントの必須フィールド契約。"""

    def test_is_revert_event_true_for_revert_type(self):
        assert store.is_revert_event({"event_type": "revert"}) is True

    def test_is_revert_event_false_for_accept_entry(self):
        assert store.is_revert_event({"id": "x", "human_accepted": True}) is False

    def test_is_revert_event_false_for_missing_event_type(self):
        assert store.is_revert_event({}) is False

    def test_missing_fields_lists_all_when_record_empty(self):
        missing = store.missing_revert_event_fields({})
        assert set(missing) == set(store.REVERT_EVENT_REQUIRED_FIELDS)

    def test_missing_fields_empty_when_record_complete(self):
        rec = {field: "x" for field in store.REVERT_EVENT_REQUIRED_FIELDS}
        rec["revert_generation"] = 1  # int 契約（他は文字列でも形式検査はしない）
        assert store.missing_revert_event_fields(rec) == []

    def test_required_fields_match_pr1_field_names(self):
        """PR-1（_revert_generation_for_target）が判定に使う3フィールドと食い違わない。"""
        required = set(store.REVERT_EVENT_REQUIRED_FIELDS)
        assert {"scope", "repo_id", "relative_path"} <= required
        assert "reverted_entry_id" in required
        assert "revert_generation" in required
        assert "revert_event_id" in required
        assert "event_type" in required


class TestFoldEffective:
    """#402 PR-2 段階2 §1: 出力契約（純粋関数・副作用ゼロ・I/O ゼロ）。"""

    def test_excludes_reverted_accept_entry(self):
        records = [
            {"id": "x1", "human_accepted": True},
            {
                "event_type": "revert",
                "reverted_entry_id": "x1",
                "revert_event_id": "r1",
                "revert_generation": 1,
                "scope": "project",
                "repo_id": "r",
                "relative_path": "p",
            },
        ]
        assert store.fold_effective(records) == []

    def test_revert_event_itself_excluded_even_without_matching_accept(self):
        records = [{"event_type": "revert", "reverted_entry_id": "missing"}]
        assert store.fold_effective(records) == []

    def test_non_reverted_entries_pass_through_unchanged(self):
        records = [
            {"id": "a1", "human_accepted": True},
            {"id": "a2", "human_accepted": False},
        ]
        assert store.fold_effective(records) == records

    def test_order_preserved_stable_filter(self):
        records = [{"id": "b"}, {"id": "a"}]
        assert store.fold_effective(records) == records

    def test_only_reverted_entry_removed_survivor_kept(self):
        records = [
            {"id": "a1", "human_accepted": True},
            {"id": "a2", "human_accepted": True},
            {"event_type": "revert", "reverted_entry_id": "a1"},
        ]
        out = store.fold_effective(records)
        assert [r["id"] for r in out] == ["a2"]

    def test_records_without_id_never_matched_as_reverted(self):
        """``reverted_entry_id`` が None の revert（壊れたレコード）が id 無しレコードを
        誤って畳まないことを保証する（``rec.get("id") not in reverted_ids`` の False Positive 防止）。
        """
        records = [{"human_accepted": True}, {"event_type": "revert"}]
        out = store.fold_effective(records)
        assert out == [{"human_accepted": True}]

    def test_no_io_pure_function(self, tmp_path, monkeypatch):
        """I/O ゼロ: HISTORY_ROOT を存在しないパスにしても動く（ファイルシステムに触れない）。"""
        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "does-not-exist")
        records = [{"id": "a1"}]
        assert store.fold_effective(records) == records


class TestAliasAggregationAndEffectiveView:
    """#402 PR-2 段階2 §1: alias 6段階集約 + fold_effective の1回適用。

    - data-dir（major）: canonical → iter_read_data_dirs の順
    - slug（minor）    : canonical_slug → sorted(aliases - {canonical_slug})
    - 異なる canonical slug 間では畳まない
    """

    @staticmethod
    def _write(dir_: Path, slug: str, records: list) -> None:
        oh = dir_ / "optimize_history"
        oh.mkdir(parents=True, exist_ok=True)
        (oh / f"{slug}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def test_fold_crosses_data_dir_boundary(self, tmp_path, monkeypatch):
        """accept entry が legacy 側、revert イベントが canonical 側でも fold が両者を見つける。"""
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy = tmp_path / "rl-anything"
        self._write(
            canonical,
            "proj",
            [
                {
                    "event_type": "revert",
                    "reverted_entry_id": "x1",
                    "revert_event_id": "rev1",
                    "revert_generation": 1,
                    "scope": "project",
                    "repo_id": "r",
                    "relative_path": "p",
                }
            ],
        )
        self._write(legacy, "proj", [{"id": "x1", "human_accepted": True}])
        assert store.load_effective_history("proj") == []
        events = store.load_revert_events("proj")
        assert len(events) == 1
        assert events[0]["reverted_entry_id"] == "x1"

    def test_fold_crosses_pj_rename_slug_alias(self, tmp_path, monkeypatch):
        """PJ rename alias（旧 slug↔現 slug）をまたいでも fold が両者を見つける（単一 data-dir）。"""
        import pj_slug

        monkeypatch.setattr(pj_slug, "PJ_SLUG_ALIASES", {"old-proj": "new-proj"})
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        # revert イベントは現 slug（new-proj）側、accept entry は旧 slug（old-proj）側。
        self._write(
            canonical,
            "new-proj",
            [
                {
                    "event_type": "revert",
                    "reverted_entry_id": "x1",
                    "revert_event_id": "rev1",
                    "revert_generation": 1,
                    "scope": "project",
                    "repo_id": "r",
                    "relative_path": "p",
                }
            ],
        )
        self._write(canonical, "old-proj", [{"id": "x1", "human_accepted": True}])
        assert store.load_effective_history("new-proj") == []
        # 旧 slug を指定して呼んでも同じ canonical family に解決され結果は同じ。
        assert store.load_effective_history("old-proj") == []

    def test_does_not_fold_across_different_canonical_slugs(self, tmp_path, monkeypatch):
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(
            canonical,
            "proj-a",
            [
                {
                    "event_type": "revert",
                    "reverted_entry_id": "x1",
                    "scope": "project",
                    "repo_id": "r",
                    "relative_path": "p",
                }
            ],
        )
        self._write(canonical, "proj-b", [{"id": "x1", "human_accepted": True}])
        # proj-a と proj-b は別 canonical slug なので proj-b の x1 は畳まれない。
        out = store.load_effective_history("proj-b")
        assert [r["id"] for r in out] == ["x1"]

    def test_dedup_priority_is_datadir_major_slug_minor(self, tmp_path, monkeypatch):
        """全順序: data-dir が優先軸、slug は劣後軸（将来 rename で slug 辞書順が逆でも壊れない）。"""
        import pj_slug

        monkeypatch.setattr(pj_slug, "PJ_SLUG_ALIASES", {"old-proj": "new-proj"})
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        legacy_dd = tmp_path / "rl-anything"
        # canonical data-dir + alias slug（劣後 slug 軸）
        self._write(canonical, "old-proj", [{"id": "x1", "value": "canonical_dd_alias_slug"}])
        # legacy data-dir + canonical slug（優先 slug 軸だが data-dir で劣後）
        self._write(legacy_dd, "new-proj", [{"id": "x1", "value": "legacy_dd_canonical_slug"}])
        out = store._aliased_raw_records("new-proj")
        assert len(out) == 1
        assert out[0]["value"] == "canonical_dd_alias_slug"

    def test_load_revert_events_excludes_non_revert_records(self, tmp_path, monkeypatch):
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(
            canonical,
            "proj",
            [
                {"id": "a1", "human_accepted": True},
                {"event_type": "revert", "reverted_entry_id": "a1"},
            ],
        )
        events = store.load_revert_events("proj")
        assert [e.get("reverted_entry_id") for e in events] == ["a1"]

    def test_load_effective_history_empty_when_no_data(self, tmp_path, monkeypatch):
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        assert store.load_effective_history("nope") == []
        assert store.load_revert_events("nope") == []


class TestLoadRawHistoryWithAliases:
    """#402 段階3 M-A: revert の entry 検索専用の公開 API。

    revert 済み entry は ``load_effective_history`` から消えるため、冪等判定
    （同じ entry_id で再実行）には raw が要る。``_aliased_raw_records`` の公開版。
    """

    @staticmethod
    def _write(dir_: Path, slug: str, records: list) -> None:
        oh = dir_ / "optimize_history"
        oh.mkdir(parents=True, exist_ok=True)
        (oh / f"{slug}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def test_matches_aliased_raw_records(self, tmp_path, monkeypatch):
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(canonical, "proj", [{"id": "x1", "human_accepted": True}])
        assert store.load_raw_history_with_aliases("proj") == store._aliased_raw_records("proj")

    def test_includes_revert_events_unlike_effective_view(self, tmp_path, monkeypatch):
        """revert 済み entry は raw では消えない（冪等判定に raw が必要な理由そのもの）。"""
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(
            canonical,
            "proj",
            [
                {"id": "x1", "human_accepted": True},
                {
                    "event_type": "revert",
                    "reverted_entry_id": "x1",
                    "revert_event_id": "rev1",
                    "revert_generation": 1,
                    "scope": "project",
                    "repo_id": "r",
                    "relative_path": "p",
                },
            ],
        )
        records = store.load_raw_history_with_aliases("proj")
        # raw は accept entry も revert イベントも両方保持する（フィルタしない）。
        assert any(r.get("id") == "x1" for r in records)
        assert any(store.is_revert_event(r) for r in records)
        assert store.load_effective_history("proj") == []  # 対比: effective からは消える

    def test_duplicate_ids_out_param_flags_multi_source_id(self, tmp_path, monkeypatch):
        """#402 段階3 C1: 同一 id が複数 source（data-dir × alias）に存在する不整合を明示する。"""
        import pj_slug

        monkeypatch.setattr(pj_slug, "PJ_SLUG_ALIASES", {"old-proj": "new-proj"})
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(canonical, "new-proj", [{"id": "x1", "value": "canonical_wins"}])
        self._write(canonical, "old-proj", [{"id": "x1", "value": "alias_loses"}])

        dup_ids: set = set()
        records = store.load_raw_history_with_aliases("new-proj", duplicate_ids=dup_ids)

        assert [r["value"] for r in records if r.get("id") == "x1"] == ["canonical_wins"]
        assert dup_ids == {"x1"}

    def test_duplicate_ids_out_param_empty_when_no_conflict(self, tmp_path, monkeypatch):
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(canonical, "proj", [{"id": "x1"}])

        dup_ids: set = set()
        store.load_raw_history_with_aliases("proj", duplicate_ids=dup_ids)
        assert dup_ids == set()

    def test_duplicate_ids_default_none_does_not_change_behavior(self, tmp_path, monkeypatch):
        """既存呼び出し元（duplicate_ids 未指定）は挙動不変（後方互換）。"""
        canonical = tmp_path / "evolve-anything"
        (canonical / "optimize_history").mkdir(parents=True)
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
        self._write(canonical, "proj", [{"id": "x1"}])
        assert store.load_raw_history_with_aliases("proj") == [{"id": "x1"}]
