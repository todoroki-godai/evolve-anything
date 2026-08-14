"""daily.proposal_ranking のテスト（ADR-054 PR2-c）。

朝の提示の composite sort（順位キー4点）と、utterances.db への O(U+S) 一括発話時刻 join を
検証する。決定論・read-only。DuckDB を使う join テストは HAS_DUCKDB でスキップ可能。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from daily import proposal_ranking as pr  # noqa: E402
from utterance_archive import store as ustore  # noqa: E402
from utterance_archive.extractor import Utterance  # noqa: E402

pytestmark_duckdb = pytest.mark.skipif(not ustore.HAS_DUCKDB, reason="DuckDB 未インストール")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _meta(uttered_at=None, detected_at=None, cross_pj=None) -> dict:
    return {"uttered_at": uttered_at, "detected_at": detected_at, "cross_pj": cross_pj or []}


def _group(
    signal_keys, meta_by_key, *, cross_pj_confirmed=None, origin_pjs=None,
) -> dict:
    g = {
        "signal_keys": list(signal_keys),
        "signal_meta_by_key": dict(meta_by_key),
        "cross_pj_confirmed": cross_pj_confirmed or [],
    }
    if origin_pjs is not None:
        g["origin_pjs"] = list(origin_pjs)
    return g


# ─────────────────────────────────────────────────────────────────
# is_global_group
# ─────────────────────────────────────────────────────────────────
def test_is_global_group_true_when_origin_pjs_present():
    g = _group(["k1"], {"k1": _meta()}, origin_pjs=["pj-a", "pj-b"])
    assert pr.is_global_group(g) is True


def test_is_global_group_false_for_plain_per_pj_group():
    g = _group(["k1"], {"k1": _meta()})
    assert pr.is_global_group(g) is False


# ─────────────────────────────────────────────────────────────────
# composite_sort_key — 4キー
# ─────────────────────────────────────────────────────────────────
def test_composite_key_tier1_cross_pj_or_global_ranks_first():
    confirmed = _group(["a1"], {"a1": _meta(detected_at=_now_iso())}, cross_pj_confirmed=["other-pj"])
    plain = _group(["p1"], {"p1": _meta(detected_at=_now_iso())})
    ranked = sorted([plain, confirmed], key=pr.composite_sort_key)
    assert ranked[0] is confirmed


def test_composite_key_global_lane_ranks_with_confirmed_tier():
    global_g = _group(["g1"], {"g1": _meta(detected_at=_now_iso())}, origin_pjs=["pj-a", "pj-b"])
    plain = _group(["p1"], {"p1": _meta(detected_at=_now_iso())})
    ranked = sorted([plain, global_g], key=pr.composite_sort_key)
    assert ranked[0] is global_g


def test_composite_key_count_breaks_tie_within_tier():
    few = _group(["a1"], {"a1": _meta(detected_at=_now_iso())})
    many = _group(
        ["b1", "b2", "b3"],
        {f"b{i}": _meta(detected_at=_now_iso()) for i in (1, 2, 3)},
    )
    ranked = sorted([few, many], key=pr.composite_sort_key)
    assert ranked[0] is many  # 再発回数（count）が多い方が先頭


def test_composite_key_freshness_prefers_recent_utterance():
    old = _group(["o1"], {"o1": _meta(uttered_at=_iso(60))})
    new = _group(["n1"], {"n1": _meta(uttered_at=_iso(1))})
    ranked = sorted([old, new], key=pr.composite_sort_key)
    assert ranked[0] is new


def test_composite_key_freshness_uses_detected_at_when_uttered_at_missing():
    """uttered_at が無い（join 失敗）場合は detected_at にフォールバックする。"""
    old = _group(["o1"], {"o1": _meta(detected_at=_iso(60))})
    new = _group(["n1"], {"n1": _meta(detected_at=_iso(1))})
    ranked = sorted([old, new], key=pr.composite_sort_key)
    assert ranked[0] is new


def test_composite_key_deterministic_tiebreak_by_min_signal_key():
    """キー1〜3 が全て同値なら min(signal_keys) で決定論に並ぶ。"""
    same_ts = _iso(5)
    g_b = _group(["b1"], {"b1": _meta(uttered_at=same_ts)})
    g_a = _group(["a1"], {"a1": _meta(uttered_at=same_ts)})
    ranked = sorted([g_b, g_a], key=pr.composite_sort_key)
    assert ranked[0] is g_a  # "a1" < "b1"

    # 入力順を逆にしても結果は同じ（安定性でなく決定論のキー4で確定）。
    ranked2 = sorted([g_a, g_b], key=pr.composite_sort_key)
    assert ranked2[0] is g_a


def test_composite_key_order_preserved_when_subtraction_does_not_affect_priority():
    """既読差し引き後も4キーの順序が保たれる（PR2-e）: 優先度に影響しない既読差し引き
    （群Bのタイに影響しないキーの除去）では、A/B の相対順序は変わらない。
    """
    now_iso = _now_iso()
    group_a = _group(["a1"], {"a1": _meta(uttered_at=now_iso)}, cross_pj_confirmed=["other-pj"])
    group_b = _group(
        ["b1", "b2"], {"b1": _meta(detected_at=_iso(30)), "b2": _meta(detected_at=_iso(30))},
    )
    before = sorted([group_b, group_a], key=pr.composite_sort_key)
    assert before[0] is group_a  # tier1（confirmed）が優先

    # group_b から b2 を既読差し引き（count 2→1）しても、tier1 の group_a が変わらず先頭。
    group_b_after = _group(["b1"], {"b1": _meta(detected_at=_iso(30))})
    after = sorted([group_b_after, group_a], key=pr.composite_sort_key)
    assert after[0] is group_a


def test_composite_key_missing_freshness_sorts_last_within_tier():
    """uttered_at/detected_at ともに無い group は同 tier 内で最後に回る（クラッシュしない）。"""
    unknown = _group(["u1"], {"u1": _meta()})
    known = _group(["k1"], {"k1": _meta(detected_at=_iso(1))})
    ranked = sorted([unknown, known], key=pr.composite_sort_key)
    assert ranked[0] is known


# ─────────────────────────────────────────────────────────────────
# build_uttered_at_map — O(U+S) 一括 join + 失敗4種の区別
# ─────────────────────────────────────────────────────────────────
def test_build_uttered_at_map_db_missing(tmp_path: Path):
    if not ustore.HAS_DUCKDB:
        pytest.skip("DuckDB 未インストール")
    m, stats = pr.build_uttered_at_map(tmp_path / "does-not-exist.db")
    assert m == {}
    assert stats["db_missing"] == 1
    assert stats["duckdb_missing"] == 0
    assert stats["query_error"] == 0


def test_build_uttered_at_map_duckdb_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ustore, "HAS_DUCKDB", False)
    m, stats = pr.build_uttered_at_map(tmp_path / "u.db")
    assert m == {}
    assert stats["duckdb_missing"] == 1


@pytestmark_duckdb
def test_build_uttered_at_map_builds_physical_key_map(tmp_path: Path):
    db = tmp_path / "u.db"
    rows = [
        Utterance("/p/a.jsonl", 1, "evolve-anything", "s1", "2026-05-01T00:00:00+00:00",
                  "発話1", "h1", None, "dialogue", 1),
        Utterance("/p/b.jsonl", 2, "otherpj", "s2", "2026-06-01T00:00:00+00:00",
                  "発話2", "h2", None, "dialogue", 1),
    ]
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, rows)

    m, stats = pr.build_uttered_at_map(db)
    assert m[("/p/a.jsonl", 1)] == "2026-05-01T00:00:00+00:00"
    assert m[("/p/b.jsonl", 2)] == "2026-06-01T00:00:00+00:00"
    assert stats == {"db_missing": 0, "duckdb_missing": 0, "query_error": 0}


@pytestmark_duckdb
def test_build_uttered_at_map_query_error(tmp_path: Path, monkeypatch):
    db = tmp_path / "u.db"
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, [
            Utterance("/p/a.jsonl", 1, "evolve-anything", "s1", "2026-05-01T00:00:00+00:00",
                      "発話1", "h1", None, "dialogue", 1),
        ])

    def _boom(*a, **k):
        raise RuntimeError("boom")

    from utterance_archive import query as uquery
    monkeypatch.setattr(uquery, "query_utterances_all_projects", _boom)
    m, stats = pr.build_uttered_at_map(db)
    assert m == {}
    assert stats["query_error"] == 1


# ─────────────────────────────────────────────────────────────────
# relative_time_label（PR2-d 表示用）
# ─────────────────────────────────────────────────────────────────
def test_relative_time_label_weeks_ago():
    label = pr.relative_time_label(_iso(21))
    assert "週間前" in label


def test_relative_time_label_days_ago():
    label = pr.relative_time_label(_iso(3))
    assert "日前" in label


def test_relative_time_label_months_ago():
    label = pr.relative_time_label(_iso(90))
    assert "ヶ月前" in label


def test_relative_time_label_none_when_unparsable():
    assert pr.relative_time_label(None) is None
    assert pr.relative_time_label("not-a-date") is None
