"""correction_semantic.promote のテスト（#431 reflect 昇格フロー）。

weak_signals レーンの未昇格レコードを人間確認後に corrections へ昇格する読み取り口・
昇格関数を検証する。昇格レコードは source=reflect_confirmed（human-source）で書かれ、
フェーズ昇格カウントを駆動する。weak_signal 側は promoted=True にマークされ二重昇格を防ぐ。
決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import promote as cs_promote  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _seed_signals(ws_path: Path):
    sigs = [
        WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 1,
                                 "text": "緑にして赤じゃなくて", "reason": "後置型"},
                   "2026-06-10T00:00:00+00:00", "s1", "evolve-anything"),
        WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 2,
                                 "text": "P6が違う", "reason": "ソフト指摘"},
                   "2026-06-10T00:01:00+00:00", "s1", "evolve-anything"),
    ]
    append_signals(sigs, path=ws_path)
    return sigs


def test_read_unpromoted_returns_all_when_none_promoted(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    _seed_signals(ws)
    unp = cs_promote.read_unpromoted(weak_signals_path=ws)
    assert len(unp) == 2


def test_promote_permission_deny_has_meaningful_message(tmp_path: Path) -> None:
    # #99: text/reason 無しの決定論チャネルは channel 名でなく拒否コマンドを message にする。
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    deny = WeakSignal(
        "permission_deny",
        {"tool_name": "Bash", "tool_input_summary": "git push --force-with-lease",
         "denial_reason": "unknown"},
        "2026-06-10T00:00:00+00:00", "s1", "evolve-anything",
    )
    append_signals([deny], path=ws)
    res = cs_promote.promote_signals(
        [deny.signal_key], weak_signals_path=ws, corrections_path=corr,
        project_path="/Users/x/evolve-anything",
    )
    assert res["promoted"] == 1
    rec = json.loads(corr.read_text(encoding="utf-8").splitlines()[0])
    msg = rec.get("message", "")
    # message=channel 名の空 correction にならず、拒否されたコマンドが入る。
    assert msg != "permission_deny"
    assert "Bash" in msg
    assert "git push --force-with-lease" in msg


def test_read_unpromoted_filters_by_channel(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    _seed_signals(ws)
    append_signals([WeakSignal("rephrase", {"x": 1}, "t", "s2", "evolve-anything")], path=ws)
    # channel フィルタ無しなら 3、llm_judge のみなら 2
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws)) == 3
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws, channel="llm_judge")) == 2


def test_promote_writes_human_source_correction(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    keys = [s.signal_key for s in sigs]

    res = cs_promote.promote_signals(
        keys, weak_signals_path=ws, corrections_path=corr,
        project_path="/Users/x/evolve-anything",
    )
    assert res["promoted"] == 2

    corr_recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(corr_recs) == 2
    # human-source で書かれる（フェーズ昇格カウント対象）
    assert all(r["source"] == "reflect_confirmed" for r in corr_recs)
    assert all(r["reflect_status"] == "applied" for r in corr_recs)
    # #593: project_path は書込時に worktree 安全 slug へ正規化される
    # （/Users/x/evolve-anything → basename evolve-anything）。
    assert all(r.get("project_path") == "evolve-anything" for r in corr_recs)
    # provenance の言い回し本文が message に入る
    assert any("緑にして" in r.get("message", "") for r in corr_recs)


def test_promote_normalizes_worktree_project_path(tmp_path: Path) -> None:
    """#593: worktree フルパスが渡されても project_path は本体 repo slug で書かれる。

    reflect_confirmed / idiom_dict 昇格は呼び出し側が worktree フルパスを渡しうる。
    cross-PJ 統計に幻PJ slug を混入させないため、書込境界（_build_correction_record）で
    project（#492）と同じ pj_slug_fast 経由の正規化を通す。
    """
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)

    wt_path = "/Users/x/tools/amamo/.claude/worktrees/evolve"
    cs_promote.promote_signals(
        [sigs[0].signal_key], weak_signals_path=ws, corrections_path=corr,
        project_path=wt_path,
    )
    corr_recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(corr_recs) == 1
    # worktree フルパスが幻PJ slug ではなく本体 repo slug amamo に畳まれる。
    assert corr_recs[0]["project_path"] == "amamo"


def test_promote_empty_project_path_preserved(tmp_path: Path) -> None:
    """#593: 空 project_path は空のまま（正規化が None→"" を増幅しない）。"""
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    cs_promote.promote_signals(
        [sigs[0].signal_key], weak_signals_path=ws, corrections_path=corr,
        project_path="",
    )
    corr_recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert corr_recs[0]["project_path"] == ""


def test_promote_marks_weak_signal_promoted(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    cs_promote.promote_signals([sigs[0].signal_key], weak_signals_path=ws,
                               corrections_path=corr, project_path="/p")
    # ws[0] は promoted=True、ws[1] は False のまま
    recs = [json.loads(l) for l in ws.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_key = {r["signal_key"]: r for r in recs}
    assert by_key[sigs[0].signal_key]["promoted"] is True
    assert by_key[sigs[1].signal_key]["promoted"] is False
    # 未昇格読み取りは 1 件に減る
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws)) == 1


def test_promote_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    before_ws = ws.read_text(encoding="utf-8")
    res = cs_promote.promote_signals([s.signal_key for s in sigs], weak_signals_path=ws,
                                     corrections_path=corr, project_path="/p", dry_run=True)
    assert res["dry_run"] is True
    assert res["promoted"] == 2  # 昇格するはずだった件数
    assert not corr.exists()  # corrections に書かない
    assert ws.read_text(encoding="utf-8") == before_ws  # weak_signals 不変


def test_promote_skips_unknown_keys(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    _seed_signals(ws)
    res = cs_promote.promote_signals(["nonexistent"], weak_signals_path=ws,
                                     corrections_path=corr, project_path="/p")
    assert res["promoted"] == 0
    assert not corr.exists()


def _seed_with_expired(ws_path: Path):
    """1 件目を expired=True にして seed する（#442 TTL）。"""
    sigs = _seed_signals(ws_path)
    recs = [json.loads(line) for line in ws_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    recs[0]["expired"] = True
    recs[0]["expired_at"] = "2026-06-12T00:00:00+00:00"
    ws_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )
    return sigs


def test_read_unpromoted_excludes_expired_by_default(tmp_path: Path) -> None:
    """exclude_expired=True（既定）で expired レコードは昇格候補から外れる（#442）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_with_expired(ws)
    # 既定で expired を除外 → 1 件
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws)) == 1


def test_read_unpromoted_can_include_expired(tmp_path: Path) -> None:
    """exclude_expired=False なら expired も含めて返す（後方互換）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_with_expired(ws)
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws, exclude_expired=False)) == 2


# ── #89: read 時 age 計算で 45 日 TTL 失効を write 非依存化 ──
# 標準フロー（--dry-run → --drain）は mark_expired を通らず expired フラグが書かれない
# ため、read 側が detected_at から age を再計算しないと腐った signal が material_count
# から落ちない。下記は **フラグ書込ゼロ** でも除外されること（write 非依存）を assert する。


def _iso_days_ago(days: int) -> str:
    """実時刻基準で days 日前の ISO8601 UTC（read_unpromoted は real now を使うため）。"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_aged_unflagged(ws_path: Path, days_ago: int):
    """detected_at が days_ago 日前・expired フラグ無し・promoted=False の 1 件を seed。

    フラグを一切立てない（mark_expired を通さない標準フローの状態を再現）。
    """
    sig = WeakSignal("rephrase", {"line_no": 1}, _iso_days_ago(days_ago), "s1", "evolve-anything")
    append_signals([sig], path=ws_path)
    return sig


def test_read_unpromoted_excludes_aged_signal_without_expired_flag(tmp_path: Path) -> None:
    """detected_at 46 日前・expired フラグ無しは exclude_expired=True で除外（write 非依存）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_aged_unflagged(ws, days_ago=46)
    # フラグが書かれていないことを確認（mark_expired を通していない）
    recs = [json.loads(l) for l in ws.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all("expired" not in r or r.get("expired") is False for r in recs)
    # それでも read 時 age 計算で除外される
    assert cs_promote.read_unpromoted(weak_signals_path=ws) == []


def test_read_unpromoted_includes_aged_signal_when_exclude_off(tmp_path: Path) -> None:
    """exclude_expired=False なら 46 日前でも返る（後方互換）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_aged_unflagged(ws, days_ago=46)
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws, exclude_expired=False)) == 1


def test_read_unpromoted_keeps_signal_within_ttl(tmp_path: Path) -> None:
    """44 日前（TTL 内）は除外されない（境界）。"""
    ws = tmp_path / "weak_signals.jsonl"
    _seed_aged_unflagged(ws, days_ago=44)
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws)) == 1


def test_read_unpromoted_keeps_signal_with_unparsable_detected_at(tmp_path: Path) -> None:
    """detected_at が parse 不能なら除外しない（安全側＝昇格候補に残す）。"""
    ws = tmp_path / "weak_signals.jsonl"
    sig = WeakSignal("rephrase", {"line_no": 1}, "not-a-date", "s1", "evolve-anything")
    append_signals([sig], path=ws)
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws)) == 1


# ── idiom_dict 昇格（ADR-047・source/promoted_by/idiom_key を残す） ────


def test_promote_with_idiom_dict_source(tmp_path: Path) -> None:
    """source="idiom_dict" を渡すと corrections レコードがその source で書かれる。"""
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    res = cs_promote.promote_signals(
        [sigs[0].signal_key], weak_signals_path=ws, corrections_path=corr,
        project_path="/p", source="idiom_dict", idiom_keys={sigs[0].signal_key: "abc123"},
    )
    assert res["promoted"] == 1
    rec = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()][0]
    assert rec["source"] == "idiom_dict"
    assert rec["promoted_by"] == "idiom_dict"
    assert rec["idiom_key"] == "abc123"
    assert rec["invalidated"] is False


def test_promote_default_source_has_no_promoted_by(tmp_path: Path) -> None:
    """既定（reflect_confirmed）昇格は後方互換: promoted_by を強制しない。"""
    ws = tmp_path / "weak_signals.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sigs = _seed_signals(ws)
    cs_promote.promote_signals(
        [sigs[0].signal_key], weak_signals_path=ws, corrections_path=corr, project_path="/p",
    )
    rec = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()][0]
    assert rec["source"] == "reflect_confirmed"
    # 既定は invalidated=False（巻き戻し対象でない通常レコードも統一して持つ）
    assert rec.get("invalidated") is False


# ── invalidate_idiom_corrections（安全弁③・revoke の corrections 巻き戻し） ──


def _write_corrections(path: Path, recs):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_invalidate_marks_matching_idiom_dict_records(tmp_path: Path) -> None:
    corr = tmp_path / "corrections.jsonl"
    _write_corrections(corr, [
        {"source": "idiom_dict", "promoted_by": "idiom_dict", "idiom_key": "k1", "invalidated": False},
        {"source": "idiom_dict", "promoted_by": "idiom_dict", "idiom_key": "k2", "invalidated": False},
        {"source": "reflect_confirmed", "message": "x"},  # 対象外
    ])
    res = cs_promote.invalidate_idiom_corrections({"k1"}, corrections_path=corr)
    assert res["invalidated"] == 1
    recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_key = {r.get("idiom_key"): r for r in recs if r.get("idiom_key")}
    assert by_key["k1"]["invalidated"] is True
    assert by_key["k2"]["invalidated"] is False  # 別 idiom_key は不変
    # reflect_confirmed レコードは触らない
    assert recs[2].get("invalidated") in (None, False)


def test_invalidate_dry_run_writes_nothing(tmp_path: Path) -> None:
    corr = tmp_path / "corrections.jsonl"
    _write_corrections(corr, [
        {"source": "idiom_dict", "promoted_by": "idiom_dict", "idiom_key": "k1", "invalidated": False},
    ])
    before = corr.read_text(encoding="utf-8")
    res = cs_promote.invalidate_idiom_corrections({"k1"}, corrections_path=corr, dry_run=True)
    assert res["dry_run"] is True
    assert res["invalidated"] == 1
    assert corr.read_text(encoding="utf-8") == before


def test_invalidate_noop_when_no_match(tmp_path: Path) -> None:
    corr = tmp_path / "corrections.jsonl"
    _write_corrections(corr, [
        {"source": "idiom_dict", "promoted_by": "idiom_dict", "idiom_key": "k1", "invalidated": False},
    ])
    res = cs_promote.invalidate_idiom_corrections({"nonexistent"}, corrections_path=corr)
    assert res["invalidated"] == 0


# ── resolve_idiom_keys_for_signals（signal→idiom provenance 突合・#463 配線） ──


def _seed_signal_and_idiom(ws_path: Path, idioms_path: Path, *, line_no, text, slug="evolve-anything"):
    """同じ provenance（pj_slug, source_path, line_no）を共有する weak_signal + idiom を seed。

    batch.py は同じ prov で WeakSignal と CorrectionIdiom を作るため、(pj_slug, source_path,
    line_no) で signal→idiom が突合できる。
    """
    import correction_semantic.store as cs_store
    prov = {"source_path": "/a.jsonl", "line_no": line_no,
            "session_id": "s1", "text": text, "reason": "後置型", "judge": "llm_haiku"}
    sig = WeakSignal("llm_judge", prov, "2026-06-10T00:00:00+00:00", "s1", slug)
    append_signals([sig], path=ws_path)
    it = cs_store.CorrectionIdiom(
        idiom=text, provenance=prov, detected_at="2026-06-10T00:00:00+00:00", pj_slug=slug,
    )
    cs_store.append_idioms([it], path=idioms_path)
    return sig, it


def test_resolve_idiom_keys_maps_signal_to_idiom_key(tmp_path: Path) -> None:
    """signal_key → 対応する idiom_key を provenance 突合で解決する。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    sig, it = _seed_signal_and_idiom(ws, idioms, line_no=1, text="四国めたんじゃなくて")
    mapping = cs_promote.resolve_idiom_keys_for_signals(
        [sig.signal_key], weak_signals_path=ws, idioms_path=idioms,
    )
    assert mapping == {sig.signal_key: it.idiom_key}


def test_resolve_idiom_keys_works_after_promotion(tmp_path: Path) -> None:
    """promote 済み（promoted=True）の signal でも idiom_key を解決できる（配線順序の保証）。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    corr = tmp_path / "corrections.jsonl"
    sig, it = _seed_signal_and_idiom(ws, idioms, line_no=1, text="四国めたんじゃなくて")
    cs_promote.promote_signals([sig.signal_key], weak_signals_path=ws,
                               corrections_path=corr, project_path="/p")
    # promote 後（promoted=True）でも解決できないと confirm が後段で空振りする
    mapping = cs_promote.resolve_idiom_keys_for_signals(
        [sig.signal_key], weak_signals_path=ws, idioms_path=idioms,
    )
    assert mapping == {sig.signal_key: it.idiom_key}


def test_resolve_idiom_keys_skips_signals_without_idiom(tmp_path: Path) -> None:
    """対応 idiom が無いシグナル（rephrase 等）は mapping に含めない。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    # idiom を持たない rephrase シグナルのみ
    sig = WeakSignal("rephrase", {"source_path": "/b.jsonl", "line_no": 9},
                     "t", "s2", "evolve-anything")
    append_signals([sig], path=ws)
    mapping = cs_promote.resolve_idiom_keys_for_signals(
        [sig.signal_key], weak_signals_path=ws, idioms_path=idioms,
    )
    assert mapping == {}


def test_resolve_idiom_keys_unknown_signal_returns_empty(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    _seed_signal_and_idiom(ws, idioms, line_no=1, text="四国めたんじゃなくて")
    mapping = cs_promote.resolve_idiom_keys_for_signals(
        ["nonexistent"], weak_signals_path=ws, idioms_path=idioms,
    )
    assert mapping == {}


# ── #185 claim3: reject/既読（record_reviewed）を read_unpromoted の除外条件に含める ──
# daily_review.record_reviewed(decision="rejected") で却下された weak_signal は promoted=True
# にならないため、既読ストア（correction_review_seen.jsonl）を参照しないと read_unpromoted /
# material_count に永遠に残り続け「reject しても件数が減らない」非対称が起きる（#185）。


def test_read_unpromoted_excludes_rejected_signal(tmp_path: Path) -> None:
    """既読ストアで decision=rejected と記録された signal は候補から除外される。"""
    from correction_semantic import daily_review as cs_daily_review

    ws = tmp_path / "weak_signals.jsonl"
    seen = tmp_path / "correction_review_seen.jsonl"
    sigs = _seed_signals(ws)
    cs_daily_review.record_reviewed(
        [sigs[0].signal_key], "evolve-anything", decision="rejected", path=seen,
    )
    unp = cs_promote.read_unpromoted(weak_signals_path=ws, seen_path=seen)
    assert [r["signal_key"] for r in unp] == [sigs[1].signal_key]


def test_read_unpromoted_can_include_reviewed_when_disabled(tmp_path: Path) -> None:
    """exclude_reviewed=False なら既読でも除外しない（後方互換）。"""
    from correction_semantic import daily_review as cs_daily_review

    ws = tmp_path / "weak_signals.jsonl"
    seen = tmp_path / "correction_review_seen.jsonl"
    sigs = _seed_signals(ws)
    cs_daily_review.record_reviewed(
        [sigs[0].signal_key], "evolve-anything", decision="rejected", path=seen,
    )
    unp = cs_promote.read_unpromoted(
        weak_signals_path=ws, seen_path=seen, exclude_reviewed=False,
    )
    assert len(unp) == 2


def test_read_unpromoted_unaffected_when_no_seen_store(tmp_path: Path) -> None:
    """既読ストアが存在しなければ従来どおり全件返る（後方互換）。"""
    ws = tmp_path / "weak_signals.jsonl"
    seen = tmp_path / "correction_review_seen.jsonl"  # 未作成
    _seed_signals(ws)
    assert len(cs_promote.read_unpromoted(weak_signals_path=ws, seen_path=seen)) == 2
