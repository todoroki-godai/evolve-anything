"""#568 T1-T3: measurement foundation contracts."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve_revert_listing
import evolve_revert_cli
import optimize_history_store
import results_board


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def _closed_summary() -> dict:
    return {
        "gate": {
            "gate_open": False,
            "display_start_week": None,
            "required": 4,
            "best_run_length": 0,
            "point_week": None,
            "current_run_length": 0,
        },
        "displayed_weeks": [],
        "latest_coverage": None,
        "diagnostics": {},
        "generated_at": NOW.isoformat(),
    }


def test_corrupt_jsonl_lines_are_counted_and_carried(tmp_path, monkeypatch):
    root = tmp_path / "data" / "optimize_history"
    root.mkdir(parents=True)
    (root / "proj.jsonl").write_text(
        '{"id":"ok","human_accepted":true}\nnot-json\n{"broken":\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(optimize_history_store, "HISTORY_ROOT", root)
    monkeypatch.setattr(
        optimize_history_store,
        "_aliased_raw_records",
        lambda slug: optimize_history_store._read_jsonl(root / f"{slug}.jsonl"),
    )

    records = optimize_history_store.load_effective_history("proj")

    assert len(records) == 1
    assert records.measured is True
    assert records.dropped_lines == 2
    assert records.reason == "破損 JSONL を 2 行スキップ"


def test_all_corrupt_jsonl_is_unmeasured_not_zero(tmp_path, monkeypatch):
    path = tmp_path / "broken.jsonl"
    path.write_text("not-json\n{\"broken\":\n", encoding="utf-8")
    monkeypatch.setattr(
        optimize_history_store,
        "_aliased_raw_records",
        lambda slug: optimize_history_store._read_jsonl(path),
    )

    records = optimize_history_store.load_effective_history("proj")

    assert records == []
    assert records.measured is False
    assert records.dropped_lines == 2
    assert records.reason


def test_large_partial_corruption_reports_exact_dropped_count(tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text("broken\n" * 10_000 + '{"id":"ok"}\n', encoding="utf-8")

    records = optimize_history_store._read_jsonl(path)

    assert records.measured is True
    assert len(records) == 1
    assert records.dropped_lines == 10_000
    assert records.reason == "破損 JSONL を 10000 行スキップ"


def test_results_board_reader_failures_are_distinct_and_rendered(monkeypatch):
    monkeypatch.setattr(
        results_board,
        "build_correction_rate_summary",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("rate denied")),
    )
    monkeypatch.setattr(
        results_board,
        "load_effective_history",
        lambda slug: (_ for _ in ()).throw(OSError("history denied")),
    )
    monkeypatch.setattr(
        results_board,
        "load_revert_events",
        lambda slug: (_ for _ in ()).throw(UnicodeError("revert broken")),
    )
    monkeypatch.setattr(
        results_board,
        "_build_capture_recall",
        lambda: {"measured": False, "reason": "fixture"},
    )

    board = results_board.build_results_board("proj", now=NOW)
    text = "\n".join(results_board.render_results_board(board))

    assert board["measurements"]["correction_rate"]["measured"] is False
    assert board["measurements"]["decisions"]["measured"] is False
    assert board["measurements"]["revert_events"]["measured"] is False
    assert "PermissionError" in text
    assert "OSError" in text
    assert "UnicodeError" in text
    assert "accepted 0 件" not in text


def test_revert_listing_failure_is_unmeasured_and_rendered(monkeypatch):
    monkeypatch.setattr(
        evolve_revert_listing,
        "load_effective_history",
        lambda slug: (_ for _ in ()).throw(PermissionError("denied")),
    )

    items = evolve_revert_listing.build_revert_listing("proj")
    text = "\n".join(evolve_revert_listing.render_revert_listing(items))

    assert items == []
    assert items.measured is False
    assert items.reason
    assert "測定不能" in text
    assert "PermissionError" in text
    assert "0件" not in text


def test_empty_all_corrupt_measurement_is_not_lost_by_listing(monkeypatch):
    corrupt = evolve_revert_listing.MeasuredList(
        [], measured=False, reason="破損 JSONL を 2 行スキップ", dropped_lines=2
    )
    monkeypatch.setattr(evolve_revert_listing, "load_effective_history", lambda slug: corrupt)

    items = evolve_revert_listing.build_revert_listing("proj")

    assert items.measured is False
    assert items.dropped_lines == 2
    assert "測定不能" in "\n".join(evolve_revert_listing.render_revert_listing(items))


def test_revert_list_json_surfaces_unmeasured(monkeypatch, capsys):
    items = evolve_revert_listing.MeasuredList(
        [], measured=False, reason="読取失敗: PermissionError"
    )
    monkeypatch.setattr(evolve_revert_cli, "build_revert_listing", lambda: items)

    assert evolve_revert_cli.main(["--list", "--json"]) == 0
    output = capsys.readouterr().out

    assert '"measured": false' in output
    assert '"reason": "読取失敗: PermissionError"' in output
    assert '"total": null' in output


def test_four_pillar_scopes_are_structured_and_rendered(monkeypatch):
    monkeypatch.setattr(results_board, "build_correction_rate_summary", lambda **kwargs: _closed_summary())
    monkeypatch.setattr(results_board, "load_effective_history", lambda slug: [])
    monkeypatch.setattr(results_board, "load_revert_events", lambda slug: [])
    monkeypatch.setattr(results_board, "_build_capture_recall", lambda: {"measured": False, "reason": "fixture"})

    board = results_board.build_results_board("proj", now=NOW)
    scopes = board["measurement_scopes"]
    text = "\n".join(results_board.render_results_board(board))

    assert scopes["capture_recall"]["kind"] == "plugin_bundled_eval_set"
    assert scopes["accepted_improvements"] == {
        "kind": "project",
        "slug": "proj",
        "label": "当PJ: proj",
    }
    assert scopes["correction_rate"]["kind"] == "all_projects"
    assert scopes["withdrawal_candidates"]["slug"] == "proj"
    for scope in scopes.values():
        assert scope["label"] in text
def _board_with_gate(monkeypatch, **gate_fields):
    """gate だけ差し替えた board を組む（gate 検算テストの共通足場）。"""
    summary = _closed_summary()
    summary["gate"].update(**gate_fields)
    monkeypatch.setattr(results_board, "build_correction_rate_summary", lambda **kwargs: summary)
    monkeypatch.setattr(results_board, "load_effective_history", lambda slug: [])
    monkeypatch.setattr(results_board, "load_revert_events", lambda slug: [])
    monkeypatch.setattr(results_board, "_build_capture_recall", lambda: {"measured": False, "reason": "fixture"})
    return summary, results_board.build_results_board("proj", now=NOW)


def test_gate_open_is_rechecked_against_best_run_length(monkeypatch):
    """`gate_open=True` なのに最長連続週数が足りない不整合を検出して系列表示を止める。"""
    summary, board = _board_with_gate(
        monkeypatch,
        gate_open=True,
        display_start_week="2026-W30",
        required=4,
        best_run_length=1,
        current_run_length=1,
    )
    health = board["measurements"]["correction_rate_gate"]
    text = "\n".join(results_board.render_results_board(board))

    assert health["reported_gate_open"] is True
    assert health["gate_open_effective"] is False
    assert health["measured"] is False
    assert "gate_open と最長連続週数が不一致" in text
    assert "1/4" in text
    # 検算結果が**表示分岐にも配線されている**こと。health の1行だけを出して系列を
    # そのまま描いてしまうと、不整合を報告しながら不整合な系列を見せることになる。
    assert "全量判定の確定週が 4 週連続で揃うまで系列は表示しません。" in text
    # 検算結果は summary を書き換えず health 側だけに載る（pass-through 契約）。
    assert board["correction_rate"] == summary
    assert "reported_gate_open" not in board["correction_rate"]["gate"]


def test_inverse_gate_mismatch_is_also_closed(monkeypatch):
    """`gate_open=False` なのに最長連続週数が足りている不整合も同じく検出する。"""
    _summary, board = _board_with_gate(
        monkeypatch,
        gate_open=False,
        display_start_week=None,
        required=4,
        best_run_length=4,
        current_run_length=4,
    )
    health = board["measurements"]["correction_rate_gate"]

    assert health["gate_open_effective"] is False
    assert health["measured"] is False


def test_broken_streak_after_a_past_run_stays_open(monkeypatch):
    """#508 が凍結した系列表示を検算が黙って止めないこと（#568 実装レビューの回帰）。

    `_decide_display_gate` の判定式は `best_run >= k`（`correction_rate.py:551`）で、
    `current_run_length` は #508 で追加された**点表示専用**（同 `:566-568`「gate_open の
    判定には使わない」）。過去に4週連続が成立して以降途切れた状態
    （best=4 / current=1）は**正常な開ゲート**であり、検算に `current_run_length` を
    使うとこれを「不一致」と誤判定して系列表示を止めてしまう。
    """
    _summary, board = _board_with_gate(
        monkeypatch,
        gate_open=True,
        display_start_week="2026-W30",
        required=4,
        best_run_length=4,
        current_run_length=1,
    )
    health = board["measurements"]["correction_rate_gate"]
    text = "\n".join(results_board.render_results_board(board))

    assert health["gate_open_effective"] is True
    assert health["measured"] is True
    assert "不一致" not in text


def test_normal_data_is_positive_control(monkeypatch):
    _summary, board = _board_with_gate(
        monkeypatch,
        gate_open=True,
        display_start_week="2026-W30",
        required=4,
        best_run_length=4,
        current_run_length=4,
    )
    health = board["measurements"]["correction_rate_gate"]

    assert health["gate_open_effective"] is True
    assert health["measured"] is True
