"""store_registry（ストア新設の事前契約ゲート宣言）のテスト（#434）。

決定論・LLM 非依存。宣言 SoT 自身の整合性と、実プラグインツリーの全 hook writer が
宣言バックフィル済みであること（issue の Success Criteria）を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import orphan_store  # noqa: E402
import store_registry  # noqa: E402
from store_registry import StoreDeclaration  # noqa: E402


# --- 宣言 SoT 自身の整合性 ---------------------------------------------------

def test_real_declarations_are_internally_consistent() -> None:
    """同梱の宣言 SoT は validate_declarations を通過する（retention 不整合なし・重複なし）。"""
    assert store_registry.validate_declarations() == []


def test_declared_names_are_sorted_and_unique() -> None:
    names = store_registry.declared_store_names()
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_declaration_for_returns_entry_or_none() -> None:
    assert store_registry.declaration_for("corrections.jsonl") is not None
    assert store_registry.declaration_for("no_such_store.jsonl") is None


# --- validate_declarations のルール ------------------------------------------

def test_ttl_requires_ttl_days() -> None:
    bad = [StoreDeclaration(name="x.jsonl", writer="w", reader="r", retention="ttl")]
    problems = store_registry.validate_declarations(bad)
    assert any("ttl_days" in p for p in problems)


def test_compaction_requires_condition() -> None:
    bad = [
        StoreDeclaration(name="x.jsonl", writer="w", reader="r", retention="compaction")
    ]
    problems = store_registry.validate_declarations(bad)
    assert any("compaction" in p for p in problems)


def test_permanent_rejects_ttl_or_compaction() -> None:
    bad = [
        StoreDeclaration(
            name="x.jsonl", writer="w", reader="r", retention="permanent", ttl_days=7
        )
    ]
    problems = store_registry.validate_declarations(bad)
    assert any("不整合" in p for p in problems)


def test_duplicate_names_flagged() -> None:
    dup = [
        StoreDeclaration(name="x.jsonl", writer="w", reader="r", retention="permanent"),
        StoreDeclaration(name="x.jsonl", writer="w2", reader="r2", retention="permanent"),
    ]
    problems = store_registry.validate_declarations(dup)
    assert any("重複" in p for p in problems)


def test_valid_ttl_and_compaction_pass() -> None:
    good = [
        StoreDeclaration(
            name="a.jsonl", writer="w", reader="r", retention="ttl", ttl_days=14
        ),
        StoreDeclaration(
            name="b.jsonl",
            writer="w",
            reader="r",
            retention="compaction",
            compaction="1MB でローテーション",
        ),
    ]
    assert store_registry.validate_declarations(good) == []


# --- Success Criteria: 既存全ストアの宣言バックフィル完了 ----------------------

def test_all_live_hook_writers_are_declared() -> None:
    """実プラグインツリーで登録 hook が書く全 jsonl ストアが store_registry に宣言済み。

    宣言バックフィルの完了を保証する回帰テスト（#434 Success Criteria）。
    将来、宣言を足さずに新 writer hook を追加すると、このテストが落ちて気付ける。
    """
    writers = set(orphan_store.find_store_writers())
    declared = set(store_registry.declared_store_names())
    missing = sorted(writers - declared)
    assert missing == [], f"宣言なしの hook writer ストア: {missing}"


def test_current_orphan_disposition_is_declared() -> None:
    """orphan_store が現在挙げる orphan（reader 0）は disposition 宣言を持つ（#434）。"""
    report = orphan_store.detect_orphan_stores()
    for name in report.orphans:
        decl = store_registry.declaration_for(name)
        assert decl is not None, f"orphan {name} が未宣言"
        assert decl.disposition is not None, f"orphan {name} に disposition がない"


# --- .db ストア対応（#430）---------------------------------------------------

def test_utterances_db_declared_as_permanent_db() -> None:
    """utterances.db が kind='db' / retention='permanent' で宣言されている（#430）。"""
    decl = store_registry.declaration_for("utterances.db")
    assert decl is not None
    assert decl.kind == "db"
    assert decl.retention == "permanent"


def test_db_stores_excluded_from_hook_writer_backfill() -> None:
    """db ストアは hook-writer 突合の母集団でない（writer が batch ingest）。

    declarations_by_kind('jsonl') に db ストアが混ざらないことを保証する。
    """
    jsonl_names = {d.name for d in store_registry.declarations_by_kind("jsonl")}
    db_names = {d.name for d in store_registry.declarations_by_kind("db")}
    assert "utterances.db" in db_names
    assert "utterances.db" not in jsonl_names


def test_db_declaration_does_not_appear_as_stale_drift() -> None:
    """db ストア宣言が contract-drift の stale に誤検知されない（#430）。"""
    drift = orphan_store.detect_store_contract_drift()
    assert "utterances.db" not in drift.stale


# --- weak_signals.jsonl の TTL 宣言（#442）-----------------------------------

def test_weak_signals_declared_with_ttl_45() -> None:
    """weak_signals.jsonl が retention='ttl' / ttl_days=45 で宣言されている（#442）。"""
    decl = store_registry.declaration_for("weak_signals.jsonl")
    assert decl is not None
    assert decl.retention == "ttl"
    assert decl.ttl_days == 45
