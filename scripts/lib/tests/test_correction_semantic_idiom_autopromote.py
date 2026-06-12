"""correction_semantic.idiom_autopromote のテスト（ADR-047 / #447）。

human-confirmed idiom に一致する新規 weak_signal を人間再確認なしで corrections へ
自動昇格する。最重要の安全特性: **confirmed=True が 1 件も無ければ promoted=0**
（雪崩防止）。daily_cap で打ち切り、超過分は capped。dry-run はファイル不変。
決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import idiom_autopromote as iap  # noqa: E402
from correction_semantic import provenance_weight as pw  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

SLUG = "rl-anything"


def _prov(line_no, text="四国めたんじゃなくて"):
    return {"source_path": "/a.jsonl", "line_no": line_no,
            "session_id": "s1", "text": text, "reason": "後置型", "judge": "llm_haiku"}


def _seed_idiom(idioms_path: Path, line_no, text="四国めたんじゃなくて", confirmed=False):
    """idiom を 1 件 seed。confirmed=True なら confirm_idioms で確認済みにする。"""
    it = cs_store.CorrectionIdiom(
        idiom=text, provenance=_prov(line_no, text),
        detected_at="2026-06-10T00:00:00+00:00", pj_slug=SLUG,
    )
    cs_store.append_idioms([it], path=idioms_path)
    if confirmed:
        cs_store.confirm_idioms([it.idiom_key], path=idioms_path, confirmed_by="daily_review")
    return it


def _seed_signal(ws_path: Path, line_no, text="四国めたんじゃなくて"):
    """idiom と同じ物理キー（prov）を共有する weak_signal を 1 件 seed。"""
    sig = WeakSignal(
        channel="llm_judge", provenance=_prov(line_no, text),
        detected_at="2026-06-10T00:00:00+00:00", session_id="s1", pj_slug=SLUG,
    )
    append_signals([sig], path=ws_path)
    return sig


# ── 最重要: 起動時無発火（confirmed が無ければ promoted=0） ──────────


def test_no_promotion_when_no_confirmed(tmp_path: Path) -> None:
    """confirmed=True が 1 件も無ければ一致シグナルがあっても promoted=0（雪崩防止）。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    _seed_idiom(idioms, line_no=1, confirmed=False)  # 未確認
    _seed_signal(ws, line_no=1)

    res = iap.autopromote(
        SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr,
    )
    assert res["promoted"] == 0
    assert not corr.exists()  # corrections に一切書かれない


# ── confirmed=True の idiom にだけ一致して昇格 ───────────────────────


def test_promotes_only_confirmed_match(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    it = _seed_idiom(idioms, line_no=1, text="四国めたんじゃなくて", confirmed=True)
    _seed_idiom(idioms, line_no=2, text="緑じゃなくて赤", confirmed=False)  # 未確認
    _seed_signal(ws, line_no=1, text="四国めたんじゃなくて")  # confirmed と一致
    _seed_signal(ws, line_no=2, text="緑じゃなくて赤")  # 未確認 idiom と一致 → 昇格しない

    res = iap.autopromote(
        SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr,
    )
    assert res["promoted"] == 1
    recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1
    assert recs[0]["source"] == "idiom_dict"
    assert recs[0]["promoted_by"] == "idiom_dict"
    assert recs[0]["idiom_key"] == it.idiom_key


def test_promotes_same_text_new_occurrence(tmp_path: Path) -> None:
    """**本機能の核心**: 承認済み idiom テキストの「新規発話」（別 phys・別 idiom record）が昇格する。

    人間が line_no=1 の出現を確認 → 後日、同じ言い回しが line_no=99 の新発話で再発し、
    batch が別 phys の新 idiom record（それ自体は unconfirmed）と新 weak_signal を作る。
    テキスト単位の confirmed なのでこの新規再発が昇格対象になる（idiom_key 単位だと no-op）。
    """
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    # 確認済みの出現（line_no=1）
    _seed_idiom(idioms, line_no=1, text="四国めたんじゃなくて", confirmed=True)
    # 新規再発: 同テキスト・別 phys（line_no=99）の idiom record（unconfirmed）+ 新シグナル
    it99 = cs_store.CorrectionIdiom(
        idiom="四国めたんじゃなくて", provenance=_prov(99, "四国めたんじゃなくて"),
        detected_at="2026-06-20T00:00:00+00:00", pj_slug=SLUG,
    )
    cs_store.append_idioms([it99], path=idioms)
    _seed_signal(ws, line_no=99, text="四国めたんじゃなくて")

    res = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr)
    assert res["promoted"] == 1
    rec = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()][0]
    assert rec["source"] == "idiom_dict"
    assert rec["idiom_key"] == it99.idiom_key  # 昇格元の idiom_key を残す


def test_promoted_counts_as_human_correction(tmp_path: Path) -> None:
    """idiom_dict 昇格は count_human_corrections に重み 1.0 でカウントされる。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    _seed_idiom(idioms, line_no=1, confirmed=True)
    _seed_signal(ws, line_no=1)
    iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr)
    recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert pw.count_human_corrections(recs) == 1


# ── daily_cap 超過は capped で持ち越し（安全弁①） ────────────────────


def test_daily_cap_limits_and_caps_overflow(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    # confirmed idiom + 一致シグナルを 5 件用意し cap=2 で打ち切る
    for i in range(1, 6):
        _seed_idiom(idioms, line_no=i, text=f"修正{i}", confirmed=True)
        _seed_signal(ws, line_no=i, text=f"修正{i}")

    res = iap.autopromote(
        SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr, daily_cap=2,
    )
    assert res["promoted"] == 2
    assert res["capped"] == 3  # 超過 3 件は持ち越し
    recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 2


def test_capped_overflow_promoted_next_run(tmp_path: Path) -> None:
    """打ち切った超過分は次回 run で昇格される（持ち越し）。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    for i in range(1, 4):
        _seed_idiom(idioms, line_no=i, text=f"修正{i}", confirmed=True)
        _seed_signal(ws, line_no=i, text=f"修正{i}")

    r1 = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms,
                         corrections_path=corr, daily_cap=2)
    assert r1["promoted"] == 2 and r1["capped"] == 1
    r2 = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms,
                         corrections_path=corr, daily_cap=2)
    assert r2["promoted"] == 1 and r2["capped"] == 0  # 残り 1 件のみ


# ── revoke 済み idiom は対象外（安全弁③） ──────────────────────────


def test_revoked_idiom_not_promoted(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    it = _seed_idiom(idioms, line_no=1, confirmed=True)
    _seed_signal(ws, line_no=1)
    cs_store.revoke_idiom(it.idiom_key, path=idioms)  # 取り消し

    res = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr)
    assert res["promoted"] == 0
    assert not corr.exists()


# ── dry-run はファイル不変（最下層 write ゲート） ───────────────────


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    _seed_idiom(idioms, line_no=1, confirmed=True)
    _seed_signal(ws, line_no=1)
    before_ws = ws.read_text(encoding="utf-8")

    res = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms,
                          corrections_path=corr, dry_run=True)
    assert res["dry_run"] is True
    assert res["promoted"] == 1  # 昇格するはずだった件数
    assert not corr.exists()  # corrections に書かない
    assert ws.read_text(encoding="utf-8") == before_ws  # weak_signals 不変


# ── 常時 emit（対象 0 でもキーを置く） ──────────────────────────────


def test_always_emits_keys_when_empty(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    res = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr)
    assert res["promoted"] == 0
    assert res["capped"] == 0
    assert "promoted_idioms" in res
    assert res["slug"] == SLUG


def test_pj_slug_scoping(tmp_path: Path) -> None:
    """別 PJ slug の weak_signal は対象外（全PJ共通 DATA_DIR pitfall）。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    _seed_idiom(idioms, line_no=1, confirmed=True)
    # 別 slug のシグナル（物理キーは一致するが slug が違う）
    sig = WeakSignal(channel="llm_judge", provenance=_prov(1),
                     detected_at="t", session_id="s1", pj_slug="other-pj")
    append_signals([sig], path=ws)
    res = iap.autopromote(SLUG, weak_signals_path=ws, idioms_path=idioms, corrections_path=corr)
    assert res["promoted"] == 0
