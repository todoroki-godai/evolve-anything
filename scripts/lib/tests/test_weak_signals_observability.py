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


def test_builder_labels_counts_as_all_projects(tmp_path: Path, monkeypatch) -> None:
    """weak_signals 件数はグローバル集計なので (全PJ) ラベルを付ける（#476-2）。

    bootstrap の pj_total は (当PJ) 集計でラベルなしのため、桁が違って見える混乱を防ぐ。
    """
    _seed(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False, "pj_slug": "a"},
        {"channel": "rephrase", "promoted": False, "pj_slug": "b"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "全PJ" in body


def test_builder_includes_evolve_hint(tmp_path: Path, monkeypatch) -> None:
    """未昇格ありなら 'evolve' と '今日の修正確認' の誘導行が出る（#444）。"""
    _seed(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False},
        {"channel": "esc_interrupt", "promoted": False},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # ADR-028 / issue #444: markdown と構造化経路の両方に同じ行が出る単一ソース契約
    assert "evolve" in body
    assert "今日の修正確認" in body


def test_builder_registered_in_observability_contract() -> None:
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "weak_signals" in keys


# ── idiom_dict 自動昇格の surface（安全弁②・ADR-047） ────────────────


def _seed_autopromote(tmp_path: Path, monkeypatch, *, corrections, idioms=None) -> None:
    """weak_signals は空のまま、corrections / correction_idioms を tmp に向ける。"""
    import json

    import correction_semantic.store as cs_store

    ws_store_path = tmp_path / "weak_signals.jsonl"
    # weak_signals を 1 件置いて builder が None で沈黙しないようにする
    with open(ws_store_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"channel": "rephrase", "promoted": False}) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: ws_store_path)

    corr_path = tmp_path / "corrections.jsonl"
    with open(corr_path, "w", encoding="utf-8") as f:
        for r in corrections:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # sections_weak_signals が読む corrections パス解決を tmp に向ける
    import audit.sections_weak_signals as sws
    monkeypatch.setattr(sws, "_corrections_path", lambda: corr_path)

    idioms_path = tmp_path / "correction_idioms.jsonl"
    if idioms:
        with open(idioms_path, "w", encoding="utf-8") as f:
            for r in idioms:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(cs_store, "default_idioms_path", lambda base=None: idioms_path)


def test_builder_surfaces_idiom_autopromote_count(tmp_path: Path, monkeypatch) -> None:
    """corrections に idiom_dict 昇格があれば N 件 + idiom 一覧を surface する（安全弁②）。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "四国めたんじゃなくて（後置型）", "invalidated": False},
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "緑じゃなくて赤", "invalidated": False},
            {"source": "reflect_confirmed", "message": "別経路"},  # 数えない
        ],
    )
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "idiom_dict" in body and "自動昇格" in body
    assert "2 件" in body


def test_builder_excludes_invalidated_from_autopromote_count(tmp_path: Path, monkeypatch) -> None:
    """invalidated=True（revoke 済み）は自動昇格件数から除外する。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "生きてる", "invalidated": False},
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "取り消し済み", "invalidated": True},
        ],
    )
    section = build_weak_signals_section(tmp_path)
    body = "\n".join(section or [])
    assert "1 件" in body


def test_builder_no_autopromote_line_when_none(tmp_path: Path, monkeypatch) -> None:
    """idiom_dict 昇格が 0 件なら自動昇格行は出さない（ノイズを足さない）。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[{"source": "reflect_confirmed", "message": "x"}],
    )
    section = build_weak_signals_section(tmp_path)
    body = "\n".join(section or [])
    assert "自動昇格" not in body


# ── store_registry の宣言契約 ─────────────────────────────────────

def test_weak_signals_declared_in_store_registry() -> None:
    """weak_signals.jsonl が writer/reader/retention 宣言済みであること（#434 事前ゲート）。
    #442 で retention が permanent→ttl(45日) に変更された。
    """
    import store_registry

    decl = store_registry.declaration_for("weak_signals.jsonl")
    assert decl is not None
    assert decl.writer
    assert decl.reader
    assert decl.retention == "ttl"  # #442: TTL 45 日（corrections decay と整合）
    assert decl.ttl_days == 45
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
