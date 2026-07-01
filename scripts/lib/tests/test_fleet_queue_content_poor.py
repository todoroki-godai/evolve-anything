"""fleet.queue: content-poor channel を material から除外し footer 透明化する（#113）。

``weak_unprocessed_by_pj`` は content-rich（``REVIEW_CHANNELS`` = llm_judge / rephrase /
permission_deny）だけを material 計数に載せる。content-poor（esc_interrupt /
manual_edit_after_ai 等）は (a) y/n 確認から REVIEW_CHANNELS フィルタで除外され (b) promote
しても signal_text が空で昇格不能なので、material_count に載せると「今 evolve すべき PJ」判定を
歪める死荷重になる。除外件数は無音で落とさず footer / --json に透明化する。

detected_at は now 相対で生成し TTL（45日・#89・read 時導出）と干渉させない
（固定過去日付だとテスト実行日次第で先に expired 除外されてしまう）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet.queue import (  # noqa: E402
    build_queue_result,
    weak_content_poor_by_pj,
    weak_unprocessed_by_pj,
)
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

SLUG = "figma-to-code"


def _sig(channel: str, line_no: int, detected_at: str, pj_slug: str = SLUG) -> WeakSignal:
    return WeakSignal(
        channel=channel,
        provenance={
            "source_path": "/a.jsonl",
            "line_no": line_no,
            "text": "some correction text",
        },
        detected_at=detected_at,
        session_id="s1",
        pj_slug=pj_slug,
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _recent(days: int = 1) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


# --- weak_unprocessed_by_pj は content-rich のみ計上する ----------------------


def test_content_poor_not_counted_as_material(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("esc_interrupt", 1, _recent()),
            _sig("manual_edit_after_ai", 2, _recent()),
        ],
        path=ws,
    )
    # content-poor のみ → material 計数は 0（昇格手段が無い死荷重ゆえ除外）。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 0


def test_content_rich_counted_as_material(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("llm_judge", 1, _recent()),
            _sig("rephrase", 2, _recent()),
            _sig("permission_deny", 3, _recent()),
        ],
        path=ws,
    )
    # content-rich 3 channel は全て material に載る。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 3


def test_mixed_counts_only_content_rich(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("llm_judge", 1, _recent()),
            _sig("esc_interrupt", 2, _recent()),
            _sig("manual_edit_after_ai", 3, _recent()),
            _sig("permission_deny", 4, _recent()),
        ],
        path=ws,
    )
    # content-rich 2 のみ material、content-poor 2 は除外。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 2
    assert weak_content_poor_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 2


def test_content_poor_counter_zero_when_all_rich(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("llm_judge", 1, _recent())], path=ws)
    assert weak_content_poor_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 0


def test_content_poor_scoped_to_pj(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("esc_interrupt", 1, _recent(), pj_slug=SLUG),
            _sig("esc_interrupt", 2, _recent(), pj_slug="other-pj"),
        ],
        path=ws,
    )
    # 別 PJ の content-poor は当該 PJ の除外件数に混ざらない（slug スコープ）。
    assert weak_content_poor_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1


# --- build_queue_result / footer 透明化 --------------------------------------


def _corr(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    return path


def test_build_queue_result_surfaces_content_poor_key(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("llm_judge", i, _recent(), pj_slug="alpha")
            for i in range(3)
        ]
        + [
            _sig("esc_interrupt", 100 + i, _recent(), pj_slug="alpha")
            for i in range(4)
        ],
        path=ws,
    )
    corr = _corr(tmp_path / "corrections.jsonl")
    result = build_queue_result(
        pj_slugs=["alpha"],
        threshold=3,
        weak_signals_path=ws,
        corrections_path=corr,
        last_evolve_map={},
        activity_map={},
        generated_at="2026-07-02T09:00:00Z",
    )
    # 新キーが出る（後方互換の追加キー）。
    assert "weak_content_poor" in result
    assert result["weak_content_poor"] == [{"pj_slug": "alpha", "content_poor": 4}]
    # material は content-rich 3 のみ（content-poor 4 は載らない）。
    item = result["queue"][0]
    assert item["weak_unprocessed"] == 3
    assert item["material_count"] == 3


def test_build_queue_result_no_content_poor_key_when_none(tmp_path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [_sig("llm_judge", i, _recent(), pj_slug="alpha") for i in range(3)],
        path=ws,
    )
    corr = _corr(tmp_path / "corrections.jsonl")
    result = build_queue_result(
        pj_slugs=["alpha"],
        threshold=3,
        weak_signals_path=ws,
        corrections_path=corr,
        last_evolve_map={},
        activity_map={},
        generated_at="2026-07-02T09:00:00Z",
    )
    # content-poor 0 件でもキー自体は存在（値は空リスト）。
    assert result["weak_content_poor"] == []


def test_footer_shows_content_poor_excluded():
    from fleet.formatters import format_queue_table

    result = {
        "queue": [],
        "tracked_total": 5,
        "threshold": 5,
        "weak_content_poor": [{"pj_slug": "figma-to-code", "content_poor": 42}],
    }
    out = format_queue_table(result)
    assert "content-poor" in out
    assert "figma-to-code 42件" in out


def test_footer_silent_when_no_content_poor():
    from fleet.formatters import format_queue_table

    out = format_queue_table({"queue": [], "tracked_total": 5, "threshold": 5})
    assert "content-poor" not in out
