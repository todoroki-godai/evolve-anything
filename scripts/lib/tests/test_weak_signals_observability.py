"""weak_signals observability builder + store_registry 宣言のテスト（#432）。

決定論・LLM 非依存。silence != evaluated と store_registry の契約緑を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import weak_signals.store as ws_store  # noqa: E402
from audit.sections_weak_signals import build_weak_signals_section  # noqa: E402


def _seed(tmp_path: Path, monkeypatch, records: list) -> Path:
    """default_store_path を tmp に向け、records を書く。"""
    store = tmp_path / "weak_signals.jsonl"
    if records:
        import json
        with open(store, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: store)
    return store


def test_builder_silent_when_store_empty(tmp_path: Path, monkeypatch) -> None:
    """store が空（レコード 0）なら None（まだ何も検出していない＝沈黙）。"""
    _seed(tmp_path, monkeypatch, [])
    assert build_weak_signals_section(tmp_path) is None


def test_builder_surfaces_channel_counts(tmp_path: Path, monkeypatch) -> None:
    """レコードありならチャネル別件数 + 未昇格数を surface。"""
    _seed(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False},
        {"channel": "rephrase", "promoted": True},
        {"channel": "esc_interrupt", "promoted": False},
        {"channel": "permission_deny", "promoted": False},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "4 件" in body
    assert "言い直し 2" in body
    assert "Esc 中断 1" in body
    assert "未昇格 3" in body


def test_builder_registered_in_observability_contract() -> None:
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "weak_signals" in keys


# ── store_registry の宣言契約 ─────────────────────────────────────

def test_weak_signals_declared_in_store_registry() -> None:
    """weak_signals.jsonl が writer/reader/retention 宣言済みであること（#434 事前ゲート）。"""
    import store_registry

    decl = store_registry.declaration_for("weak_signals.jsonl")
    assert decl is not None
    assert decl.writer
    assert decl.reader
    assert decl.retention == "permanent"
    assert decl.writer_locus == "batch"


def test_store_registry_declarations_valid() -> None:
    """宣言自身の整合性が緑であること。"""
    import store_registry

    assert store_registry.validate_declarations() == []


def test_batch_jsonl_excluded_from_stale() -> None:
    """writer_locus=batch の jsonl は stale 突合の除外対象に入る（#432）。"""
    import store_registry

    exempt = store_registry.stale_exempt_names()
    assert "weak_signals.jsonl" in exempt
    # db も従来通り除外対象に残る
    assert "utterances.db" in exempt


def test_contract_drift_does_not_flag_weak_signals_stale() -> None:
    """実プラグインツリーで weak_signals.jsonl が stale 誤検知されないこと（回帰）。"""
    import orphan_store

    drift = orphan_store.detect_store_contract_drift()
    assert "weak_signals.jsonl" not in drift.stale
