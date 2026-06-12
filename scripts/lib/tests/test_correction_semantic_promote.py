"""correction_semantic.promote のテスト（#431 reflect 昇格フロー）。

weak_signals レーンの未昇格レコードを人間確認後に corrections へ昇格する読み取り口・
昇格関数を検証する。昇格レコードは source=reflect_confirmed（human-source）で書かれ、
フェーズ昇格カウントを駆動する。weak_signal 側は promoted=True にマークされ二重昇格を防ぐ。
決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
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
                   "2026-06-10T00:00:00+00:00", "s1", "rl-anything"),
        WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 2,
                                 "text": "P6が違う", "reason": "ソフト指摘"},
                   "2026-06-10T00:01:00+00:00", "s1", "rl-anything"),
    ]
    append_signals(sigs, path=ws_path)
    return sigs


def test_read_unpromoted_returns_all_when_none_promoted(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    _seed_signals(ws)
    unp = cs_promote.read_unpromoted(weak_signals_path=ws)
    assert len(unp) == 2


def test_read_unpromoted_filters_by_channel(tmp_path: Path) -> None:
    ws = tmp_path / "weak_signals.jsonl"
    _seed_signals(ws)
    append_signals([WeakSignal("rephrase", {"x": 1}, "t", "s2", "rl-anything")], path=ws)
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
        project_path="/Users/x/rl-anything",
    )
    assert res["promoted"] == 2

    corr_recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(corr_recs) == 2
    # human-source で書かれる（フェーズ昇格カウント対象）
    assert all(r["source"] == "reflect_confirmed" for r in corr_recs)
    assert all(r["reflect_status"] == "applied" for r in corr_recs)
    assert all(r.get("project_path") == "/Users/x/rl-anything" for r in corr_recs)
    # provenance の言い回し本文が message に入る
    assert any("緑にして" in r.get("message", "") for r in corr_recs)


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
