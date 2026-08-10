"""bootstrap 消化除外 predicate の単一ソース契約テスト（#94 非対称是正）。

同じ pj_slug に対して ``fleet.queue_materials``（queue の待ち件数）と
``correction_semantic.daily_review``（毎日の y/n 確認）が別々に bootstrap 消化除外を
実装していると、marker 設置以前に detected した weak を queue は落とすのに daily_review
は出し続ける（またはその逆）という split-brain が起こる
（learning_consumption_state_split / learning_detector_contradiction_shared_predicate と同型）。

本テストは:
1. 除外 predicate（``_exclude_bootstrap_consumed``）が ``correction_semantic.bootstrap_backlog``
   を単一ソースとし、``fleet.queue_materials`` はそれを re-export しているだけ（同一オブジェクト）
   であることを確認する。
2. 同一の合成 weak_signals ストア + marker に対し、queue 側の未処理集合と daily_review 側の
   新規集合が、bootstrap 消化除外について同じ判定（pre-marker weak は両方から消える）を
   通ることを確認する。channel フィルタ・既読(seen)フィルタは daily_review 固有の追加フィルタ
   であり両者の差として許容する（本テストは REVIEW_CHANNELS に含まれる llm_judge のみを使い
   その差を排除した上で比較する）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import daily_review as dr  # noqa: E402
from fleet import queue_materials  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

SLUG = "contract-test-pj"


def _sig(text: str, line_no: int, detected_at: str) -> WeakSignal:
    return WeakSignal(
        channel="llm_judge",
        provenance={"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"},
        detected_at=detected_at,
        session_id="s1",
        pj_slug=SLUG,
    )


def _write_marker(tmp_path: Path, slug: str, content: str) -> None:
    (tmp_path / f"bootstrap_done-{slug}.marker").write_text(content, encoding="utf-8")


def test_exclude_bootstrap_consumed_is_single_sourced():
    from correction_semantic.bootstrap_backlog import _exclude_bootstrap_consumed as canonical
    from fleet.queue_materials import _exclude_bootstrap_consumed as reexported

    assert reexported is canonical


def test_queue_and_daily_review_agree_on_pre_marker_exclusion(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    now = datetime.now(timezone.utc)
    pre_marker = _sig("marker前の指摘", 1, (now - timedelta(days=3)).isoformat())
    post_marker = _sig("marker後の指摘", 2, (now - timedelta(hours=1)).isoformat())
    append_signals([pre_marker, post_marker], path=ws)
    _write_marker(tmp_path, SLUG, (now - timedelta(days=1)).isoformat())

    # queue 側: 未処理件数は post_marker の 1 件のみ（pre_marker は bootstrap 消化済み扱い）。
    queue_count = queue_materials.weak_unprocessed_by_pj(
        SLUG, weak_signals_path=ws, marker_base=tmp_path
    )
    assert queue_count == 1

    # daily_review 側: 新規 group も post_marker の 1 件のみ（同じ predicate を通る）。
    res = dr.build_review(
        SLUG,
        weak_signals_path=ws,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_signal_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_signal_keys == {post_marker.signal_key}

    # 両者とも pre_marker を落とし post_marker を残す＝bootstrap 消化除外について合意している。
    assert pre_marker.signal_key not in daily_signal_keys


def test_queue_and_daily_review_agree_when_no_marker(tmp_path: Path):
    # marker が無ければ両者とも除外なし（挙動不変・素通し）で一致する。
    ws = tmp_path / "weak_signals.jsonl"
    now = datetime.now(timezone.utc)
    sig = _sig("marker無しの指摘", 1, (now - timedelta(days=2)).isoformat())
    append_signals([sig], path=ws)

    queue_count = queue_materials.weak_unprocessed_by_pj(
        SLUG, weak_signals_path=ws, marker_base=tmp_path
    )
    assert queue_count == 1

    res = dr.build_review(
        SLUG,
        weak_signals_path=ws,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_signal_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_signal_keys == {sig.signal_key}
