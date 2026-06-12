"""correction_semantic.cross_pj_priority のテスト（#462 confirmed idiom の PJ 横断優先提示）。

ある PJ で confirmed=True になった idiom と**正規化テキスト一致**する他 PJ の未確認 idiom を
daily_review / bootstrap_backlog の group 提示で先頭に優先表示し、`cross_pj_confirmed`
（承認済み他 PJ slug 一覧）を付与する。自動 confirmed 化・自動昇格はしない（ADR-047 不変条件）。

検証観点（Success Criteria 逐条対応）:
- 正規化テキスト一致の検出が決定論（LLM 非依存）。正規化は autopromote と共有（normalize_idiom_text）。
- 他 PJ の confirmed idiom と一致する group が先頭に並び、cross_pj_confirmed ラベルが付く。
- 自 PJ の confirmed は cross-PJ シグナルにしない（cross = 他 slug のみ）。
- 一致しない group は cross_pj_confirmed=[] で並びは保たれる。
- read_cross_pj_confirmed_idiom_texts は他 slug の confirmed のみ集約し、自 slug を除外する。
- daily_review.build_review / bootstrap_backlog.build の出力に cross_pj_confirmed が付く。

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import bootstrap_backlog as bb  # noqa: E402
from correction_semantic import cross_pj_priority as xpj  # noqa: E402
from correction_semantic import daily_review as dr  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402
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


def _confirmed_idiom(
    text: str, pj_slug: str, line_no: int, *, confirmed: bool = True
) -> CorrectionIdiom:
    return CorrectionIdiom(
        idiom=text,
        provenance={"source_path": "/b.jsonl", "line_no": line_no},
        detected_at="2026-06-10T00:00:00+00:00",
        pj_slug=pj_slug,
        confirmed=confirmed,
    )


# ─────────────────────────────────────────────────────────────────
# normalize_idiom_text: autopromote と共有する正規化（決定論）
# ─────────────────────────────────────────────────────────────────
def test_normalize_idiom_text_strips_surrounding_whitespace():
    assert cs_store.normalize_idiom_text("  git diff  ") == "git diff"


def test_normalize_idiom_text_exact_match_preserved():
    # 既存 autopromote の exact-match を壊さない（正規化は superset で接地）
    assert cs_store.normalize_idiom_text("四国めたんじゃなくて") == "四国めたんじゃなくて"


def test_normalize_idiom_text_handles_none_and_empty():
    assert cs_store.normalize_idiom_text("") == ""
    assert cs_store.normalize_idiom_text(None) == ""


# ─────────────────────────────────────────────────────────────────
# read_cross_pj_confirmed_idiom_texts: 他 slug の confirmed のみ集約
# ─────────────────────────────────────────────────────────────────
def test_cross_pj_reader_excludes_own_slug(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms(
        [
            _confirmed_idiom("git diff", "rl-anything", 1),  # 自 PJ confirmed
            _confirmed_idiom("git diff", "figma-to-code", 2),  # 他 PJ confirmed
        ],
        path=idioms,
    )
    out = cs_store.read_cross_pj_confirmed_idiom_texts("rl-anything", idioms)
    # 自 slug の confirmed は cross にしない。他 slug のみ集約。
    assert "git diff" in out
    assert out["git diff"] == ["figma-to-code"]


def test_cross_pj_reader_aggregates_multiple_slugs(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms(
        [
            _confirmed_idiom("git diff", "figma-to-code", 1),
            _confirmed_idiom("git diff", "amamo", 2),
        ],
        path=idioms,
    )
    out = cs_store.read_cross_pj_confirmed_idiom_texts("rl-anything", idioms)
    assert sorted(out["git diff"]) == ["amamo", "figma-to-code"]


def test_cross_pj_reader_excludes_unconfirmed_and_revoked(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    unconfirmed = _confirmed_idiom("not confirmed", "figma-to-code", 1, confirmed=False)
    revoked = _confirmed_idiom("revoked one", "figma-to-code", 2)
    revoked.revoked_at = "2026-06-11T00:00:00+00:00"
    append_idioms([unconfirmed, revoked], path=idioms)
    out = cs_store.read_cross_pj_confirmed_idiom_texts("rl-anything", idioms)
    assert out == {}


def test_cross_pj_reader_normalizes_text(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms([_confirmed_idiom("  git diff  ", "figma-to-code", 1)], path=idioms)
    out = cs_store.read_cross_pj_confirmed_idiom_texts("rl-anything", idioms)
    # 正規化キーで引ける
    assert "git diff" in out


# ─────────────────────────────────────────────────────────────────
# prioritize: 一致 group を先頭へ + cross_pj_confirmed 付与（順序保存）
# ─────────────────────────────────────────────────────────────────
def test_prioritize_moves_matched_group_to_front_daily(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms([_confirmed_idiom("git diff", "figma-to-code", 1)], path=idioms)
    # daily_review 形の group（idiom + representative + evidence）
    groups = [
        {"idiom": None, "representative": "他の修正", "evidence": {"text": "他の修正"}},
        {"idiom": "git diff", "representative": "git diff", "evidence": {"text": "git status じゃなくて git diff"}},
    ]
    out = xpj.prioritize(groups, "rl-anything", idioms_path=idioms)
    # 一致 group が先頭に来る
    assert out[0]["idiom"] == "git diff"
    assert out[0]["cross_pj_confirmed"] == ["figma-to-code"]
    # 非一致 group は空ラベル + 後ろ
    assert out[1]["cross_pj_confirmed"] == []


def test_prioritize_matches_on_representative_when_no_idiom(tmp_path: Path):
    # bootstrap 形の group は idiom フィールドが無く representative のみ
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms([_confirmed_idiom("git diff", "figma-to-code", 1)], path=idioms)
    groups = [
        {"representative": "別件", "signal_keys": ["k1"]},
        {"representative": "git diff", "signal_keys": ["k2"]},
    ]
    out = xpj.prioritize(groups, "rl-anything", idioms_path=idioms)
    assert out[0]["representative"] == "git diff"
    assert out[0]["cross_pj_confirmed"] == ["figma-to-code"]


def test_prioritize_stable_order_for_unmatched(tmp_path: Path):
    idioms = tmp_path / "correction_idioms.jsonl"
    # confirmed 無し → 全 group 非一致 → 元の順序を保つ
    groups = [
        {"representative": "A", "signal_keys": ["k1"]},
        {"representative": "B", "signal_keys": ["k2"]},
        {"representative": "C", "signal_keys": ["k3"]},
    ]
    out = xpj.prioritize(groups, "rl-anything", idioms_path=idioms)
    assert [g["representative"] for g in out] == ["A", "B", "C"]
    assert all(g["cross_pj_confirmed"] == [] for g in out)


def test_prioritize_does_not_set_confirmed_on_groups(tmp_path: Path):
    # ADR-047 不変条件: 自動 confirmed 化しない。提示順とラベルのみ変える。
    idioms = tmp_path / "correction_idioms.jsonl"
    append_idioms([_confirmed_idiom("git diff", "figma-to-code", 1)], path=idioms)
    groups = [{"idiom": "git diff", "representative": "git diff", "evidence": {"text": "git diff"}}]
    out = xpj.prioritize(groups, "rl-anything", idioms_path=idioms)
    # group 自体に confirmed フラグを書かない（ストアにも書かない）
    assert "confirmed" not in out[0]
    # ストアは読み取りのみ（書き込みが無い）— append_idioms の 1 件のまま
    assert len(cs_store.read_idioms(idioms)) == 1


# ─────────────────────────────────────────────────────────────────
# build 出力への配線: daily_review / bootstrap_backlog
# ─────────────────────────────────────────────────────────────────
def test_build_review_emits_cross_pj_confirmed(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    seen = tmp_path / "correction_review_seen.jsonl"
    # rl-anything に未確認 weak_signal + 物理キー一致の idiom record（未確認）
    append_signals([_sig("git diff", 1, pj_slug="rl-anything")], path=ws)
    append_idioms(
        [
            CorrectionIdiom(
                idiom="git diff",
                provenance={"source_path": "/a.jsonl", "line_no": 1},
                detected_at="2026-06-10T00:00:00+00:00",
                pj_slug="rl-anything",
            ),
            # 他 PJ で同テキストが confirmed 済み
            _confirmed_idiom("git diff", "figma-to-code", 99),
        ],
        path=idioms,
    )
    res = dr.build_review(
        "rl-anything",
        weak_signals_path=ws,
        idioms_path=idioms,
        seen_path=seen,
    )
    assert res["groups"]
    assert res["groups"][0]["cross_pj_confirmed"] == ["figma-to-code"]


def test_build_review_no_cross_pj_when_no_other_confirmed(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    seen = tmp_path / "correction_review_seen.jsonl"
    append_signals([_sig("git diff", 1, pj_slug="rl-anything")], path=ws)
    res = dr.build_review(
        "rl-anything", weak_signals_path=ws, idioms_path=idioms, seen_path=seen
    )
    assert res["groups"][0]["cross_pj_confirmed"] == []


def test_bootstrap_build_emits_cross_pj_confirmed(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    marker = tmp_path / "bootstrap_done-rl-anything.marker"
    append_signals(
        [
            _sig("別件です", 1, pj_slug="rl-anything"),
            _sig("git diff", 2, pj_slug="rl-anything"),
        ],
        path=ws,
    )
    append_idioms([_confirmed_idiom("git diff", "figma-to-code", 99)], path=idioms)
    res = bb.build(
        "rl-anything",
        weak_signals_path=ws,
        marker_path=marker,
        idioms_path=idioms,
    )
    assert res["is_bootstrap"] is True
    # cross-PJ confirmed の "git diff" group が先頭
    assert res["groups"][0]["representative"] == "git diff"
    assert res["groups"][0]["cross_pj_confirmed"] == ["figma-to-code"]
    # 非一致 group は空ラベル
    assert res["groups"][1]["cross_pj_confirmed"] == []


def test_bootstrap_build_no_idioms_path_defaults_empty_label(tmp_path: Path):
    # idioms_path を渡さない経路でも cross_pj_confirmed キーは常時付く（常時 emit）
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    marker = tmp_path / "bootstrap_done-rl-anything.marker"
    append_signals([_sig("git diff", 1, pj_slug="rl-anything")], path=ws)
    res = bb.build(
        "rl-anything", weak_signals_path=ws, marker_path=marker, idioms_path=idioms
    )
    assert res["groups"][0]["cross_pj_confirmed"] == []
