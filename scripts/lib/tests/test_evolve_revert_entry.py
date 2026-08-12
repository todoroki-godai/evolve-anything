"""evolve_revert._entry のユニットテスト（#402 段階3 §2 手順1 / M-A / C1）。

entry 検索は raw × alias 集合で行う（revert 済み entry は load_effective_history から
消えるため、冪等判定に raw が要る・M-A）。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import optimize_history_store as store  # noqa: E402
from evolve_revert._entry import find_entry  # noqa: E402


def _write(dir_: Path, slug: str, records: list) -> None:
    oh = dir_ / "optimize_history"
    oh.mkdir(parents=True, exist_ok=True)
    (oh / f"{slug}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def test_finds_entry_by_id(tmp_path, monkeypatch):
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(canonical, "proj", [{"id": "x1", "human_accepted": True}])

    result = find_entry("x1", slug="proj")

    assert result.entry == {"id": "x1", "human_accepted": True}
    assert result.duplicate is False
    assert result.slug == "proj"


def test_returns_none_entry_when_not_found(tmp_path, monkeypatch):
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(canonical, "proj", [{"id": "x1"}])

    result = find_entry("nope", slug="proj")

    assert result.entry is None
    assert result.duplicate is False


def test_finds_revert_already_folded_entry_via_raw_not_effective(tmp_path, monkeypatch):
    """M-A の核心: revert 済み entry は load_effective_history から消えるが、entry 検索
    （冪等パスの再実行判定）には raw が使われるため見つかる。"""
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(
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
    assert store.load_effective_history("proj") == []  # 対比: effective からは消える

    result = find_entry("x1", slug="proj")

    assert result.entry == {"id": "x1", "human_accepted": True}


def test_finds_entry_across_pj_rename_alias(tmp_path, monkeypatch):
    """§2 手順1（v2 round3 codex [Must]）: 旧 slug にしか存在しない entry_id も見つかる。"""
    import pj_slug

    monkeypatch.setattr(pj_slug, "PJ_SLUG_ALIASES", {"old-proj": "new-proj"})
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(canonical, "old-proj", [{"id": "x1", "human_accepted": True}])

    result = find_entry("x1", slug="new-proj")

    assert result.entry == {"id": "x1", "human_accepted": True}


def test_duplicate_flag_set_when_id_exists_in_multiple_sources(tmp_path, monkeypatch):
    """C1: 同一 id 重複時は優先順位で1件を採るが不整合は明示する。"""
    import pj_slug

    monkeypatch.setattr(pj_slug, "PJ_SLUG_ALIASES", {"old-proj": "new-proj"})
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(canonical, "new-proj", [{"id": "x1", "value": "canonical_wins"}])
    _write(canonical, "old-proj", [{"id": "x1", "value": "alias_loses"}])

    result = find_entry("x1", slug="new-proj")

    assert result.entry["value"] == "canonical_wins"
    assert result.duplicate is True


def test_slug_defaults_to_resolve_slug_when_omitted(tmp_path, monkeypatch):
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")
    _write(canonical, "proj", [{"id": "x1"}])

    result = find_entry("x1")

    assert result.entry == {"id": "x1"}
    assert result.slug == "proj"
