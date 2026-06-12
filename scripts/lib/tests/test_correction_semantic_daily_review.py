"""correction_semantic.daily_review のテスト（#446 evolve 内「今日の修正確認」phase）。

前回 evolve 以降の新規 weak_signal（channel=llm_judge・未昇格・非expired）を idiom 単位で
group 化し、頻度降順・上位 max_groups を返す決定論 phase を検証する。

検証観点（Acceptance Criteria 逐条対応）:
- 新規 0 件 → eligible=False, groups=[] を emit（常時 emit）。
- 既読集合（correction_review_seen）に含まれる signal_key は除外（= 新規のみ）。
- 「いいえ」相当 decision="rejected" 追記後は再提示しない。
- 既読集合の重複追記が read 側 set 化で無害（冪等性）。
- record_reviewed は dry_run でファイル不変（最下層まで dry-run ゲート貫通）。
- group は頻度（同 idiom の再発回数）降順・max_groups で切り、remaining を返す。
- 別 PJ slug の件数が混入しない（DATA_DIR 全PJ共通 pitfall）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import daily_review as dr  # noqa: E402
from correction_semantic.store import CorrectionIdiom, append_idioms  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _sig(text: str, line_no: int, pj_slug: str = "rl-anything", **prov_extra) -> WeakSignal:
    prov = {"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    prov.update(prov_extra)
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="s1",
        pj_slug=pj_slug,
    )


def _seen(tmp_path: Path) -> Path:
    return tmp_path / "correction_review_seen.jsonl"


# ─────────────────────────────────────────────────────────────────
# 既読ストア（correction_review_seen.jsonl）
# ─────────────────────────────────────────────────────────────────
def test_seen_keys_empty_when_no_file(tmp_path: Path):
    assert dr.read_reviewed_keys(path=_seen(tmp_path)) == set()


def test_record_reviewed_appends_and_reads_back(tmp_path: Path):
    seen = _seen(tmp_path)
    res = dr.record_reviewed(
        ["k1", "k2"], "rl-anything", decision="promoted", path=seen
    )
    assert res["written"] == 2
    assert res["dry_run"] is False
    assert dr.read_reviewed_keys(path=seen) == {"k1", "k2"}


def test_record_reviewed_dry_run_no_write(tmp_path: Path):
    # 最下層まで dry-run ゲートを貫通（pitfall_dryrun_stateful_store_write）
    seen = _seen(tmp_path)
    res = dr.record_reviewed(
        ["k1"], "rl-anything", decision="rejected", path=seen, dry_run=True
    )
    assert res["dry_run"] is True
    assert not seen.exists()


def test_record_reviewed_dedup_is_idempotent(tmp_path: Path):
    # 既読集合の重複追記が read 側 set 化で無害（冪等性）
    seen = _seen(tmp_path)
    dr.record_reviewed(["k1"], "rl-anything", decision="rejected", path=seen)
    dr.record_reviewed(["k1"], "rl-anything", decision="rejected", path=seen)
    assert dr.read_reviewed_keys(path=seen) == {"k1"}


# ─────────────────────────────────────────────────────────────────
# build_review: 新規 0 件 → eligible=False（常時 emit）
# ─────────────────────────────────────────────────────────────────
def test_build_review_eligible_false_when_no_signals(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    assert res["eligible"] is False
    assert res["groups"] == []
    assert res["remaining"] == 0
    assert res["dry_run"] is False


def test_build_review_eligible_true_with_new_signals(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    assert res["eligible"] is True
    assert len(res["groups"]) == 1
    g = res["groups"][0]
    assert g["channel"] == "llm_judge"
    assert g["signal_keys"]
    assert "text" in g["evidence"]


# ─────────────────────────────────────────────────────────────────
# build_review: 既読集合に含まれる signal_key は除外（= 新規のみ）
# ─────────────────────────────────────────────────────────────────
def test_build_review_excludes_seen_keys(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    # その 1 件を既読化（rejected）→ 再提示されない
    recs = dr._read_new(  # 内部ヘルパで signal_key を取得
        "rl-anything", weak_signals_path=ws, seen_keys=set()
    )
    key = recs[0]["signal_key"]
    dr.record_reviewed([key], "rl-anything", decision="rejected", path=seen)

    res = dr.build_review("rl-anything", weak_signals_path=ws, seen_path=seen)
    assert res["eligible"] is False
    assert res["groups"] == []


def test_build_review_reviewed_keys_count(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    dr.record_reviewed(["x1", "x2"], "rl-anything", decision="promoted", path=seen)
    res = dr.build_review("rl-anything", weak_signals_path=ws, seen_path=seen)
    assert res["reviewed_keys_count"] == 2


# ─────────────────────────────────────────────────────────────────
# build_review: PJ slug スコープ / 未昇格 / channel / expired 除外
# ─────────────────────────────────────────────────────────────────
def test_build_review_scopes_to_pj_slug(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("金額がきれてる", 1, pj_slug="rl-anything"),
            _sig("別件です", 2, pj_slug="figma-to-code"),
        ],
        path=ws,
    )
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    # rl-anything の 1 idiom のみ
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    assert total == 1


def test_build_review_excludes_promoted_and_expired(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    fresh = _sig("金額がきれてる", 1)
    promoted = _sig("昇格済み", 2)
    promoted.promoted = True
    expired = _sig("古い話", 3)
    expired.expired = True
    append_signals([fresh, promoted, expired], path=ws)
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    assert total == 1


def test_build_review_only_llm_judge_channel(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    other = WeakSignal("rephrase", {"text": "別チャネル"}, "t", "s", "rl-anything")
    append_signals([other], path=ws)
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    assert total == 1


# ─────────────────────────────────────────────────────────────────
# build_review: 頻度降順 + max_groups 切り + remaining
# ─────────────────────────────────────────────────────────────────
def test_build_review_orders_by_frequency_and_caps(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    # 「金額」系を 3 件、「カテゴリ」系を 1 件 → group 化後、金額 group が先頭
    append_signals(
        [
            _sig("金額がきれてる", 1),
            _sig("金額の表示がきれてる", 2),
            _sig("金額のずれ", 3),
            _sig("カテゴリの並び", 4),
        ],
        path=ws,
    )
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=_seen(tmp_path), max_groups=1
    )
    assert len(res["groups"]) == 1
    # max_groups=1 で切ったので残り 1 group は remaining
    assert res["remaining"] == 1
    top = res["groups"][0]
    # 頻度降順: 金額 group（3 件）が先頭
    assert top["evidence"]["count"] == 3
    assert len(top["signal_keys"]) == 3


def test_build_review_uses_idiom_dict_representative(tmp_path: Path):
    # 個人辞書（correction_idioms）の idiom と物理キーで突合し代表 idiom を付ける
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    append_idioms(
        [
            CorrectionIdiom(
                idiom="金額表示の見切れ",
                provenance={"source_path": "/a.jsonl", "line_no": 1},
                detected_at="2026-06-10T00:00:00+00:00",
                pj_slug="rl-anything",
            )
        ],
        path=idioms,
    )
    res = dr.build_review(
        "rl-anything",
        weak_signals_path=ws,
        idioms_path=idioms,
        seen_path=_seen(tmp_path),
    )
    assert res["groups"][0]["idiom"] == "金額表示の見切れ"


# ─────────────────────────────────────────────────────────────────
# build_review: dry-run ファイル不変
# ─────────────────────────────────────────────────────────────────
def test_build_review_dry_run_no_write(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, seen_path=seen, dry_run=True
    )
    assert res["dry_run"] is True
    # build は読み取りのみ。既読集合に一切書かない（追記は apply 時のみ）。
    assert not seen.exists()
