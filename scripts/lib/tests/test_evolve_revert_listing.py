"""evolve_revert_listing のテスト — `bin/evolve-revert --list`（ADR-054 Phase D PR4/D2）。

戦果ボード（``results_board.py``）の「取り下げ候補」は verdict==REGRESSED に絞った表示だが、
``--list`` は「戻せる可能性のある採用全体」を対象にする。build_revert_listing は
accepted entry を revert 可否つきで**全件**列挙する（revert 不可な entry も落とさず reason
つきで残す・#376「黙って落とさない」）。決定論・LLM 非依存・read-only。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import evolve_revert_listing as listing  # noqa: E402

_NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _full_entry(**overrides) -> dict:
    """revert フィールドが完全に揃った accepted entry（PR-1 パイプライン経由）。"""
    base = {
        "id": "p1",
        "human_accepted": True,
        "skill_name": "queue",
        "timestamp": _iso(1),
        "revert_schema_version": 1,
        "revert_encoding": "zlib+base64",
        "revert_before_b64": "eJw...",
        "revert_unavailable_reason": None,
        "scope": "project",
        "repo_id": "/repo",
        "relative_path": "skills/queue/SKILL.md",
        "after_sha": "deadbeef",
    }
    base.update(overrides)
    return base


@pytest.fixture
def stub_history(monkeypatch):
    def _set(entries):
        monkeypatch.setattr(listing, "load_effective_history", lambda slug: entries)
    return _set


class TestBuildRevertListingFiltering:
    def test_empty_history_returns_empty_list(self, stub_history):
        stub_history([])
        assert listing.build_revert_listing("evolve-anything") == []

    def test_rejected_entries_are_not_listed(self, stub_history):
        """採用されていない（rejected）entry は revert の対象になりえないため対象外。"""
        stub_history([
            {"id": "r1", "approved": False, "skill_name": "x", "timestamp": _iso(1)},
        ])
        assert listing.build_revert_listing("evolve-anything") == []

    def test_pending_entries_are_not_listed(self, stub_history):
        stub_history([
            {"id": "pe1", "skill_name": "x", "timestamp": _iso(1)},
        ])
        assert listing.build_revert_listing("evolve-anything") == []

    def test_excluded_entries_are_not_listed(self, stub_history):
        """#376 で無効化された（fitness_eligible=False）accept は採用実績とみなさない。"""
        stub_history([
            _full_entry(id="ex1", fitness_eligible=False),
        ])
        assert listing.build_revert_listing("evolve-anything") == []

    def test_accepted_pre_extension_entry_is_listed_not_dropped(self, stub_history):
        """revert 不可（lane 対象外・記録拡張前）でも黙って落とさず reason つきで残す。"""
        stub_history([
            {"id": "old1", "human_accepted": True, "skill_name": "legacy", "timestamp": _iso(5)},
        ])

        items = listing.build_revert_listing("evolve-anything")

        assert len(items) == 1
        assert items[0]["entry_id"] == "old1"
        assert items[0]["revert_available"] is False
        assert items[0]["revert_unavailable_reason"] == "pre_extension"

    def test_accepted_available_entry_is_listed(self, stub_history):
        stub_history([_full_entry()])

        items = listing.build_revert_listing("evolve-anything")

        assert len(items) == 1
        assert items[0]["entry_id"] == "p1"
        assert items[0]["revert_available"] is True
        assert items[0]["revert_unavailable_reason"] is None

    def test_history_load_failure_is_graceful(self, monkeypatch):
        def _boom(slug):
            raise RuntimeError("boom")
        monkeypatch.setattr(listing, "load_effective_history", _boom)

        assert listing.build_revert_listing("evolve-anything") == []


class TestBuildRevertListingSubsequentChange:
    """§8.2 後続変更検知。判定ロジック自体は再実装せず ``evolve_revert.detect_subsequent_change``
    （_apply.py の conflict 判定と同一ソース）を listing 時点で呼ぶだけ。ここではその呼び出しの
    配線だけを検証する（判定ロジック自体の分岐網羅は test_evolve_revert_apply.py 側）。
    """

    def test_available_entry_without_subsequent_change_is_marked_revertible(
        self, stub_history, monkeypatch
    ):
        stub_history([_full_entry()])
        monkeypatch.setattr(listing, "detect_subsequent_change", lambda entry: False)

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["revert_available"] is True
        assert items[0]["subsequent_change"] is False

    def test_available_entry_with_subsequent_change_is_marked(self, stub_history, monkeypatch):
        stub_history([_full_entry()])
        monkeypatch.setattr(listing, "detect_subsequent_change", lambda entry: True)

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["revert_available"] is True
        assert items[0]["subsequent_change"] is True

    def test_unavailable_entry_does_not_call_subsequent_change_check(self, stub_history, monkeypatch):
        """revert_available=False の entry は後続変更検知の対象外（判定材料が
        揃っていない・そもそも戻せない理由が別にある）。呼ばれたら壊す。"""
        stub_history([
            {"id": "old1", "human_accepted": True, "skill_name": "legacy", "timestamp": _iso(5)},
        ])

        def _boom(entry):
            raise AssertionError("unavailable entry で呼ばれるべきでない")

        monkeypatch.setattr(listing, "detect_subsequent_change", _boom)

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["revert_available"] is False
        assert items[0]["subsequent_change"] is None


class TestRenderRevertListingSubsequentChange:
    def test_subsequent_change_shown_as_not_revertible(self):
        items = [{
            "entry_id": "p1", "skill_name": "queue", "timestamp": _iso(1),
            "scope": "project", "revert_available": True, "revert_unavailable_reason": None,
            "subsequent_change": True,
        }]
        lines = listing.render_revert_listing(items)
        text = "\n".join(lines)
        assert "p1" in text
        assert "後続変更" in text
        assert "戻せません" in text or "戻せない" in text

    def test_subsequent_change_false_still_shows_revertible_command(self):
        items = [{
            "entry_id": "p1", "skill_name": "queue", "timestamp": _iso(1),
            "scope": "project", "revert_available": True, "revert_unavailable_reason": None,
            "subsequent_change": False,
        }]
        lines = listing.render_revert_listing(items)
        text = "\n".join(lines)
        assert "bin/evolve-revert p1" in text


class TestBuildRevertListingFields:
    def test_falls_back_to_target_when_skill_name_missing(self, stub_history):
        stub_history([_full_entry(skill_name=None, target="skills/foo/SKILL.md")])

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["skill_name"] == "skills/foo/SKILL.md"

    def test_unknown_when_both_missing(self, stub_history):
        stub_history([_full_entry(skill_name=None, target=None)])

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["skill_name"] == "(unknown)"

    def test_scope_is_carried_through(self, stub_history):
        stub_history([_full_entry(scope="global")])

        items = listing.build_revert_listing("evolve-anything")

        assert items[0]["scope"] == "global"


class TestBuildRevertListingSort:
    def test_sorted_newest_first(self, stub_history):
        stub_history([
            _full_entry(id="old", timestamp=_iso(10)),
            _full_entry(id="new", timestamp=_iso(1)),
            _full_entry(id="mid", timestamp=_iso(5)),
        ])

        items = listing.build_revert_listing("evolve-anything")

        assert [i["entry_id"] for i in items] == ["new", "mid", "old"]

    def test_missing_timestamp_sorts_last(self, stub_history):
        stub_history([
            _full_entry(id="has_ts", timestamp=_iso(1)),
            _full_entry(id="no_ts", timestamp=None),
        ])

        items = listing.build_revert_listing("evolve-anything")

        assert [i["entry_id"] for i in items] == ["has_ts", "no_ts"]


class TestBuildRevertListingSlugResolution:
    def test_slug_none_resolves_via_store(self, monkeypatch):
        monkeypatch.setattr(listing, "resolve_slug", lambda cwd=None: "resolved-slug")
        seen = {}

        def _fake_load(slug):
            seen["slug"] = slug
            return []

        monkeypatch.setattr(listing, "load_effective_history", _fake_load)

        listing.build_revert_listing(None)

        assert seen["slug"] == "resolved-slug"


class TestRenderRevertListing:
    def test_empty_shows_zero_message(self):
        lines = listing.render_revert_listing([])
        assert any("0件" in line or "0 件" in line for line in lines)

    def test_available_entry_shows_command(self):
        items = [{
            "entry_id": "p1", "skill_name": "queue", "timestamp": _iso(1),
            "scope": "project", "revert_available": True, "revert_unavailable_reason": None,
        }]
        lines = listing.render_revert_listing(items)
        text = "\n".join(lines)
        assert "p1" in text
        assert "bin/evolve-revert p1" in text

    def test_unavailable_entry_shows_japanese_reason(self):
        items = [{
            "entry_id": "old1", "skill_name": "legacy", "timestamp": _iso(5),
            "scope": "project", "revert_available": False,
            "revert_unavailable_reason": "pre_extension",
        }]
        lines = listing.render_revert_listing(items)
        text = "\n".join(lines)
        assert "old1" in text
        assert "戻せません" in text or "戻せない" in text or "対象外" in text

    def test_summary_counts_available_and_unavailable(self):
        items = [
            {
                "entry_id": "a", "skill_name": "x", "timestamp": _iso(1), "scope": "project",
                "revert_available": True, "revert_unavailable_reason": None,
            },
            {
                "entry_id": "b", "skill_name": "y", "timestamp": _iso(2), "scope": "project",
                "revert_available": False, "revert_unavailable_reason": "pre_extension",
            },
        ]
        lines = listing.render_revert_listing(items)
        text = "\n".join(lines)
        assert "2" in text  # 総件数


class TestBuildRevertListingReadOnly:
    def test_is_read_only_no_disk_write(self, tmp_path, monkeypatch):
        import optimize_history_store as store
        from evolve_decision_ids import (
            REVERT_ENCODING, REVERT_SCHEMA_VERSION, compress_before_content, sha256,
        )

        canonical = tmp_path / "evolve-anything"
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")

        before_text = "before content\n"
        store.append_entry(
            {
                "id": "e1", "human_accepted": True, "skill_name": "x",
                "before_sha": sha256(before_text),
                "after_sha": sha256("after content\n"),
                "revert_before_b64": compress_before_content(before_text),
                "revert_schema_version": REVERT_SCHEMA_VERSION,
                "revert_encoding": REVERT_ENCODING,
                "scope": "project", "repo_id": str(tmp_path), "relative_path": "SKILL.md",
                "timestamp": _iso(1),
            },
            "proj",
        )
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")

        before_snapshot = set(tmp_path.rglob("*"))
        items = listing.build_revert_listing("proj")
        after_snapshot = set(tmp_path.rglob("*"))

        assert before_snapshot == after_snapshot
        assert len(items) == 1
        assert items[0]["entry_id"] == "e1"
        assert items[0]["revert_available"] is True
