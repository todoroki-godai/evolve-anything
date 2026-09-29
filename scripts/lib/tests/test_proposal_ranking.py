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
    """``cross_pj`` の ``None`` と ``[]`` は意味が違う。

    - ``None``（既定）= **未指定**。`_group` が `cross_pj_confirmed` を伝播する
    - ``[]`` = **明示的に空**。伝播しない（キー単位で confirmed を持たない状態を検査したいとき）
    """
    return {"uttered_at": uttered_at, "detected_at": detected_at, "cross_pj": cross_pj}


def _group(
    signal_keys, meta_by_key, *, cross_pj_confirmed=None, origin_pjs=None,
) -> dict:
    """production の group 形を再現する。

    ``cross_pj_confirmed`` を渡したら**各キーの meta にも同じ値を載せる**。これは
    `daily.proposal_digest._slim_group` の不変条件（group の cross_pj_confirmed を
    全 signal_key の meta へ書く）と一致させるため。composite_sort_key は残存キーの
    meta から tier1 を再計算するので、fixture がこの不変条件を破っていると
    「実装では起きない状態」を検査してしまう（#443 codex cold review [Must]）。
    伝播するのは ``cross_pj`` が **未指定（None）** のキーだけ。``_meta(cross_pj=[])`` で
    明示した空値は上書きしない（キー単位で confirmed を持たない状態を検査できるように
    するため・2巡目 [Should]）。
    """
    meta = {}
    for key, m in meta_by_key.items():
        cross = m.get("cross_pj")
        if cross is None:
            cross = list(cross_pj_confirmed or [])
        meta[key] = {**m, "cross_pj": list(cross)}
    g = {
        "signal_keys": list(signal_keys),
        "signal_meta_by_key": meta,
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


def test_empty_meta_map_does_not_fall_back_to_top_level_cross_pj():
    """既読差し引きで `signal_meta_by_key` が空 dict になっても top-level に戻らない。

    フォールバックは「キー自体が無い」旧形式に限る。空 dict でフォールバックすると、
    confirmed 情報を持つキーだけが既読で落ちた group が top-level 経由で tier 1 に
    復帰する（2巡目 codex cold review [Must]）。
    """
    pruned = {
        "signal_keys": ["plain"],
        "signal_meta_by_key": {},          # 既読差し引きで空になった
        "cross_pj_confirmed": ["other-pj"],  # 落ちたキーが持っていた残骸
    }
    assert pr._remaining_cross_pj(pruned) == []
    assert pr.composite_sort_key(pruned)[0] == 1   # tier 2
    assert pr.cross_pj_note(pruned, "pj-a") is None

    # 旧形式（キー自体が無い）だけは従来どおり top-level にフォールバックする。
    legacy = {"signal_keys": ["plain"], "cross_pj_confirmed": ["other-pj"]}
    assert pr._remaining_cross_pj(legacy) == ["other-pj"]
    assert pr.composite_sort_key(legacy)[0] == 0   # tier 1


def test_group_fixture_keeps_explicit_empty_cross_pj():
    """`_meta(cross_pj=[])` の明示的な空は `_group` が上書きしない（2巡目 [Should]）。"""
    g = _group(
        ["with", "without"],
        {"with": _meta(), "without": _meta(cross_pj=[])},
        cross_pj_confirmed=["other-pj"],
    )
    assert g["signal_meta_by_key"]["with"]["cross_pj"] == ["other-pj"]
    assert g["signal_meta_by_key"]["without"]["cross_pj"] == []


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


def test_relative_time_label_late_night_utterance_shown_next_morning_is_yesterday():
    """#441 レビュー[Must]1: JST 23:00 の発話を翌朝 JST 09:00（経過10時間・24時間未満）に
    表示すると、経過時間の24時間切り捨てでは「今日の発話」に化けるが、JST 暦日で
    「昨日の発話」と正しく出て、``relative_date_warning`` が出す発話日（暦日）と矛盾しない。
    """
    uttered_iso = "2026-09-25T14:00:00+00:00"  # 2026-09-25T23:00 JST
    now = datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc)  # 2026-09-26T09:00 JST
    assert pr.relative_time_label(uttered_iso, now=now) == "昨日の発話"


def test_relative_time_label_same_jst_calendar_day_is_today():
    uttered_iso = "2026-09-26T00:30:00+00:00"  # 2026-09-26T09:30 JST
    now = datetime(2026, 9, 26, 1, 0, 0, tzinfo=timezone.utc)  # 2026-09-26T10:00 JST 同日
    assert pr.relative_time_label(uttered_iso, now=now) == "今日の発話"


def test_relative_time_label_week_boundary_uses_calendar_day_not_elapsed_time():
    """#441 レビュー巡2[Must]A: 週・月区分も暦日差で数える。経過13.5日でも暦日14日前なら
    「2週間前」（旧: 経過時間ベースだと13.5日<14日で「13日前」に化けていた）。
    """
    now = datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc)  # 2026-09-26T09:00 JST
    uttered_iso = "2026-09-12T12:00:00+00:00"  # 2026-09-12T21:00 JST（経過13.5日・暦日14日前）
    assert pr.relative_time_label(uttered_iso, now=now) == "2週間前の発話"


def test_relative_time_label_exactly_14_days_is_two_weeks_ago():
    now = datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc)
    uttered_iso = "2026-09-12T00:00:00+00:00"  # ちょうど14日前（暦日・経過とも14）
    assert pr.relative_time_label(uttered_iso, now=now) == "2週間前の発話"


def test_relative_time_label_calendar_13_days_is_still_days_ago():
    now = datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc)
    uttered_iso = "2026-09-13T00:00:00+00:00"  # 暦日13日前
    assert pr.relative_time_label(uttered_iso, now=now) == "13日前の発話"


# ─────────────────────────────────────────────────────────────────
# group_freshness_iso / cross_pj_note（PR2-d 提示文の判断材料）
# ─────────────────────────────────────────────────────────────────
def test_group_freshness_iso_prefers_uttered_at():
    ts = _iso(21)
    g = _group(["k1"], {"k1": _meta(uttered_at=ts, detected_at=_iso(1))})
    assert pr.group_freshness_iso(g) == ts


def test_group_freshness_iso_falls_back_to_detected_at():
    ts = _iso(5)
    g = _group(["k1"], {"k1": _meta(detected_at=ts)})
    assert pr.group_freshness_iso(g) == ts


def test_group_freshness_iso_none_when_unparsable():
    g = _group(["k1"], {"k1": _meta()})
    assert pr.group_freshness_iso(g) is None


def test_cross_pj_note_observed_global_lane():
    g = _group(["k1"], {"k1": _meta()}, origin_pjs=["pj-a", "amamo", "figma-to-code"])
    note = pr.cross_pj_note(g, "pj-a")
    assert "他2PJ" in note
    assert "amamo" in note and "figma-to-code" in note


def test_cross_pj_note_confirmed_stronger_wording_when_not_global():
    g = _group(["k1"], {"k1": _meta()}, cross_pj_confirmed=["amamo"])
    note = pr.cross_pj_note(g, "pj-a")
    assert "確認済み" in note
    assert "amamo" in note


def test_cross_pj_note_none_when_neither():
    g = _group(["k1"], {"k1": _meta()})
    assert pr.cross_pj_note(g, "pj-a") is None


def test_cross_pj_note_global_takes_priority_over_confirmed():
    g = _group(
        ["k1"], {"k1": _meta()},
        origin_pjs=["pj-a", "amamo"], cross_pj_confirmed=["figma-to-code"],
    )
    note = pr.cross_pj_note(g, "pj-a")
    assert "amamo" in note
    assert "確認済み" not in note


# ─────────────────────────────────────────────────────────────────
# relative_date_warning（#441: 相対日付・曜日表現の陳腐化警告）
# ─────────────────────────────────────────────────────────────────
def test_relative_date_warning_none_when_no_relative_expression():
    assert pr.relative_date_warning("普通の改善提案です", _now_iso()) is None


def test_relative_date_warning_includes_uttered_date_and_weekday():
    # 2026-09-10 は木曜日。
    ts = "2026-09-10T03:00:00+00:00"
    warning = pr.relative_date_warning("来週やること", ts)
    assert warning is not None
    assert "2026-09-10" in warning
    assert "(木)" in warning


def test_relative_date_warning_wording_distinguishes_from_group_freshness_label():
    """#441 レビュー巡2[Should]C: 「この文の発話日」と明記し、同じ行に並ぶ N日前ラベル
    （群全体の最新の発話）とは別の・検出対象の文自身の発話であると読めるようにする。
    """
    ts = "2026-09-10T03:00:00+00:00"
    warning = pr.relative_date_warning("来週やること", ts)
    assert warning == "⚠ 相対日付あり：この文の発話日 2026-09-10(木) 基準で読むこと"


def test_relative_date_warning_uses_jst_date_not_utc_date():
    """UTC 深夜（JST では日付が繰り上がる）の発話は JST の日付・曜日で出す（UTC 固定は誤り）。

    2026-09-09T16:00:00+00:00 は UTC では 9/9（水）だが JST（+9h）では 9/10（木）になる。
    """
    ts = "2026-09-09T16:00:00+00:00"
    warning = pr.relative_date_warning("明日までにやる", ts)
    assert warning is not None
    assert "2026-09-10" in warning
    assert "(木)" in warning
    assert "2026-09-09" not in warning


def test_relative_date_warning_unparsable_uttered_at():
    warning = pr.relative_date_warning("来週やること", "not-a-date")
    assert warning == "⚠ 相対日付あり：発話日不明のため日付を確認すること"
    assert pr.relative_date_warning("来週やること", None) == warning


def test_relative_date_warning_none_for_none_or_empty_text():
    assert pr.relative_date_warning(None, _now_iso()) is None
    assert pr.relative_date_warning("", _now_iso()) is None


# 陽性対照: 相対日付を含まない・含んでよい文面
def test_relative_date_warning_positive_control_plain_text_no_warning():
    assert pr.relative_date_warning("テストを追加してください", _now_iso()) is None


def test_relative_date_warning_positive_control_weekday_past_reference_may_fire():
    """「月曜日に作った」のような過去言及は相対性が薄いが、表示用補助のため過検出を許容する。"""
    assert pr.relative_date_warning("月曜日に作ったファイルを直す", _now_iso()) is not None


# 陽性対照: 固有名詞の誤爆を避ける（既知の除外のみ・#441 の指定ケース）
def test_relative_date_warning_positive_control_proper_noun_asuka_no_false_positive():
    assert pr.relative_date_warning("明日香さんに確認する", _now_iso()) is None


def test_relative_date_warning_positive_control_weekday_compound_word_may_fire():
    """「日曜大工」のような曜日を含む一般語は本パターンにも一致する。

    固有名詞の誤爆（明日香）は既知の除外で塞ぐが、それ以外の曜日複合語まで塞ぐと
    正規表現が閉じない除外リスト化する。過検出は表示用補助として許容する方針
    （#441 レビュー[Nit]5・relative_date_warning docstring 参照）なので、ここでは
    「出ないこと」でなく「出てよいこと」を陽性対照として確認する。
    """
    assert pr.relative_date_warning("日曜大工の道具を買う話", _now_iso()) is not None


# ─────────────────────────────────────────────────────────────────
# representative_uttered_at / relative_date_warning_for_group
# （#441 レビュー[Must]2・[Should]3: 代表文自身の発話時刻を使い、merge 済み全代表文を検出する）
# ─────────────────────────────────────────────────────────────────
def test_representative_uttered_at_uses_first_signal_key_not_max():
    """代表文（signal_keys[0]）の uttered_at を返す。group_freshness_iso（最新時刻）とは
    値が異なってよい — 実データで21日ずれた群があった（#441 レビュー[Must]2）。
    """
    g = _group(
        ["k1", "k2"],
        {"k1": _meta(uttered_at=_iso(21)), "k2": _meta(uttered_at=_iso(1))},
    )
    assert pr.representative_uttered_at(g) == g["signal_meta_by_key"]["k1"]["uttered_at"]
    assert pr.representative_uttered_at(g) != pr.group_freshness_iso(g)


def test_representative_uttered_at_none_when_first_key_meta_missing():
    """既読差し引きで signal_keys[0] の meta が残っていなければ None（発話日不明扱い）。"""
    g = {"signal_keys": ["k1"], "signal_meta_by_key": {}}
    assert pr.representative_uttered_at(g) is None


def test_representative_uttered_at_none_when_only_detected_at():
    """uttered_at が無く detected_at しか無い場合は None（detected_at を発話日と呼ばない）。"""
    g = _group(["k1"], {"k1": _meta(detected_at=_iso(1))})
    assert pr.representative_uttered_at(g) is None


def test_representative_uttered_at_none_when_no_signal_keys():
    assert pr.representative_uttered_at({"signal_keys": []}) is None


def test_relative_date_warning_for_group_uses_representative_key_time_not_freshness_max():
    """freshness（最新時刻・k2）ではなく代表文の時刻（signal_keys[0]・k1）で発話日を出す。"""
    ts_rep = "2026-09-10T03:00:00+00:00"  # 木曜
    g = _group(
        ["k1", "k2"],
        {"k1": _meta(uttered_at=ts_rep), "k2": _meta(uttered_at=_iso(1))},
    )
    g["representative"] = "来週やる案"
    warning = pr.relative_date_warning_for_group(g)
    assert warning is not None
    assert "2026-09-10" in warning
    assert "(木)" in warning


def test_relative_date_warning_for_group_uses_date_when_primary_representative_matches():
    """[Should]3: 先頭代表文が一致すれば、他の代表文が同居していても通常どおり発話日を出す。"""
    g = {
        "signal_keys": ["k1"],
        "representative": "来週やる案",
        "all_representatives": ["来週やる案", "別の代表文"],
        "signal_meta_by_key": {"k1": {"uttered_at": "2026-09-10T03:00:00+00:00", "detected_at": None}},
    }
    warning = pr.relative_date_warning_for_group(g)
    assert warning is not None
    assert "2026-09-10" in warning


def test_relative_date_warning_for_group_scans_all_representatives_but_unknown_when_only_secondary_matches():
    """[Should]3: 検出は先頭以外の代表文にも及ぶ（警告そのものは出る）。ただし一致が
    先頭代表文（signal_keys[0] の発話に対応する文）以外にしか無いときは、その文の発話
    時刻を持っていないため「発話日不明」に倒す（#441 レビュー巡2[Must]B-2）。
    """
    g = {
        "signal_keys": ["k1"],
        "representative": "普通の提案",
        "all_representatives": ["普通の提案", "来週やる別提案"],
        "signal_meta_by_key": {"k1": {"uttered_at": "2026-09-10T03:00:00+00:00", "detected_at": None}},
    }
    warning = pr.relative_date_warning_for_group(g)
    assert warning is not None  # 検出（Should3）は効いている
    assert "発話日不明" in warning
    assert "2026-09-10" not in warning  # 日付は言い切らない（Must B-2）


def test_relative_date_warning_for_group_none_when_no_representatives_match():
    g = {
        "signal_keys": ["k1"],
        "representative": "普通の提案",
        "all_representatives": ["普通の提案", "別の普通の提案"],
        "signal_meta_by_key": {"k1": {"uttered_at": _now_iso(), "detected_at": None}},
    }
    assert pr.relative_date_warning_for_group(g) is None


def test_representative_uttered_at_none_when_signal_keys_were_subtracted():
    """[Must]B-1: 既読差し引きで signal_keys が減った群（count と件数が食い違う）は、
    詰め直しにより signal_keys[0] が代表文の key と限らないため None を返す。
    """
    g = {
        "signal_keys": ["k2"],  # 元は ["k0", "k2"] で k0（代表文の key）が既読除外された
        "count": 2,
        "signal_meta_by_key": {"k2": {"uttered_at": _iso(1), "detected_at": None}},
    }
    assert pr.representative_uttered_at(g) is None


def test_representative_uttered_at_uses_signal_keys_when_count_matches():
    """count が signal_keys 件数と一致（既読差し引きが起きていない）なら通常どおり使う。"""
    g = {
        "signal_keys": ["k0"],
        "count": 1,
        "signal_meta_by_key": {"k0": {"uttered_at": _iso(1), "detected_at": None}},
    }
    assert pr.representative_uttered_at(g) == g["signal_meta_by_key"]["k0"]["uttered_at"]


def test_relative_date_warning_for_group_unknown_when_signal_keys_were_subtracted():
    """[Must]B-1 の統合確認: count 食い違いの群は、先頭代表文が一致していても発話日不明。"""
    g = {
        "signal_keys": ["k2"],
        "count": 2,
        "representative": "来週やる案",
        "signal_meta_by_key": {"k2": {"uttered_at": _iso(1), "detected_at": None}},
    }
    warning = pr.relative_date_warning_for_group(g)
    assert warning is not None
    assert "発話日不明" in warning
