"""#401: weekly snapshot, read health and read-only SessionStart contracts."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from evolve_revert_listing import build_revert_listing as real_revert_listing
from measurement_result import MeasuredDict, MeasuredList
from session_notify import collectors

NOW = datetime(2026, 9, 7, 9, tzinfo=timezone.utc)


@pytest.fixture
def sources(monkeypatch, tmp_path):
    import weekly_board as wb

    p2 = Mock(return_value={"measured": True, "count": 7})
    weeks = [{"week_id": "2026-W35", "measured": True, "rate": 0.125},
             {"week_id": "2026-W36", "measured": False, "rate": None}]
    p3 = Mock(return_value=MeasuredDict({
        "gate": wb.correction_rate.compute_display_gate(weeks),
        "latest_coverage": {"week_id": "2026-W36", "rate": None},
    }))
    p4 = Mock(return_value=MeasuredList([
        {"revert_available": True, "subsequent_change": None},
        {"revert_available": True, "subsequent_change": True},
        {"revert_available": False, "subsequent_change": None},
    ]))
    slug = Mock(return_value="evolve-anything")
    monkeypatch.setattr(wb.pillar2_metrics, "count_applied_reflections", p2)
    monkeypatch.setattr(wb.correction_rate, "build_correction_rate_summary", p3)
    monkeypatch.setattr(wb.evolve_revert_listing, "build_revert_listing", p4)
    monkeypatch.setattr(wb.pj_slug, "resolve_pj_slug", slug)
    return wb, tmp_path / "evolve-queue.json", tmp_path, p2, p3, p4, slug


def build(sources, previous=None, now=NOW):
    wb, path, root, *_ = sources
    if previous is not None:
        path.write_text(json.dumps({"weekly_board": previous}))
    return wb.build_weekly_board(path, root, now=now)


def test_first_week_uses_source_fields_and_scopes(sources):
    board = build(sources, {"week_id": "2026-W36", "computed_on": "2026-09-01"})
    assert board == {"week_id": "2026-W37", "computed_on": "2026-09-07", "measured": True,
                     "pillar2_count": 7, "point_week": {"week_id": "2026-W35", "rate": 0.125, "measured": True},
                     "pillar4_count": 1}
    _, _, root, p2, p3, p4, slug = sources
    p2.assert_called_once_with(root, now=NOW)
    p3.assert_called_once_with(now=NOW)
    slug.assert_called_once_with(root)
    p4.assert_called_once_with("evolve-anything")


@pytest.mark.parametrize("days", [0, 1, 6])
def test_same_week_preserves_snapshot_without_reading(sources, days):
    previous = build(sources)
    for reader in sources[3:]:
        reader.reset_mock()
    assert build(sources, previous, NOW + timedelta(days=days)) == previous
    for reader in sources[3:]:
        reader.assert_not_called()


@pytest.mark.parametrize("previous", [[], {}, {"week_id": "2026-W37"},
    {"week_id": "2026-W37", "computed_on": "2026-09-07", "measured": False},
    {"week_id": "2026-W37", "computed_on": "20260907"},
    {"week_id": "2026-W37", "computed_on": "2026-02-30"}])
def test_invalid_previous_is_recomputed(sources, previous):
    assert build(sources, previous)["pillar2_count"] == 7
    sources[3].assert_called_once()


@pytest.mark.parametrize("raw", ['[]', 'null', '{broken'])
def test_invalid_queue_is_recomputed(sources, raw):
    sources[1].write_text(raw)
    assert build(sources)["week_id"] == "2026-W37"


@pytest.mark.parametrize("failure", ["p2_outer", "p2_inner", "p3", "p4"])
def test_measured_failure_does_not_advance_week(sources, failure):
    _, _, _, p2, p3, p4, _ = sources
    if failure == "p2_outer":
        p2.side_effect = OSError("fixture read failure")
    elif failure == "p2_inner":
        p2.return_value = {"measured": False, "count": 7}
    elif failure == "p3":
        p3.return_value.measured = False
        p3.return_value.reason = "fixture p3 failure"
    else:
        p4.return_value.measured = False
        p4.return_value.reason = "fixture p4 failure"
    board = build(sources)
    assert "week_id" not in board
    assert set(board) == {"measured", "reason", "generated_at"}
    assert board["measured"] is False
    assert board["reason"]
    assert board["generated_at"] == NOW.isoformat()


def notify(tmp_path, board, now=NOW):
    queue = {"weekly_board": board}
    path = tmp_path / "evolve-queue.json"
    path.write_text(json.dumps(queue))
    before = path.read_bytes()
    item = collectors._build_weekly_board_output((tmp_path, queue, "ok"), now=now)
    assert path.read_bytes() == before
    return item


def test_no_point_week_is_healthy_accumulating(sources):
    sources[4].return_value["gate"]["point_week"] = None
    board = build(sources)
    assert board["week_id"] == "2026-W37"
    item = notify(sources[2], board)
    assert "データ蓄積中" in item.text
    assert "データ蓄積中" in item.digest


def test_today_repeats_read_only_and_scopes_survive_digest(sources):
    board = build(sources)
    for _ in range(2):
        item = notify(sources[2], board)
        assert item.tier == 1
        assert item.commit is None
        for text in (item.text, item.digest):
            assert "柱2・4は本体／指摘率は全PJ" in text
            assert "7件" in text and "1件" in text
            assert "12.5%" in text and "2026-W35" in text


def test_yesterday_is_silent(sources):
    assert notify(sources[2], build(sources), NOW + timedelta(days=1)) is None


@pytest.mark.parametrize("queue", [[], None, {"weekly_board": None}, {"weekly_board": []},
    {"weekly_board": {"week_id": "2026-W37"}},
    {"weekly_board": {"measured": False, "reason": "fixture failure"}},
    {"weekly_board": {"week_id": 37, "computed_on": "2026-09-06"}}])
def test_health_precedes_date_filter(tmp_path, queue):
    (tmp_path / "evolve-queue.json").write_text(json.dumps(queue))
    item = collectors._build_weekly_board_output((tmp_path, queue, "ok"), now=NOW)
    assert item is not None, "health must be visible"
    assert item.tier == 1 and item.commit is None
    assert "戦果ボードの要約を読めません" in item.text
    if isinstance(queue, dict) and isinstance(queue.get("weekly_board"), dict):
        if queue["weekly_board"].get("reason"):
            assert "fixture failure" in item.text


def test_missing_file_is_silent(tmp_path):
    assert collectors._build_weekly_board_output((tmp_path, None, "absent"), now=NOW) is None


def test_queue_without_weekly_board_key_is_silent(tmp_path):
    queue = {"queue": [], "generated_at": NOW.isoformat()}
    (tmp_path / "evolve-queue.json").write_text(json.dumps(queue))
    assert collectors._build_weekly_board_output((tmp_path, queue, "ok"), now=NOW) is None


def test_weekly_board_stays_silent_on_corrupt_queue(tmp_path):
    (tmp_path / "evolve-queue.json").write_text("{broken")
    assert collectors._build_weekly_board_output((tmp_path, None, "corrupt"), now=NOW) is None


def test_import_failure_visible_under_cc_redirect_layout(tmp_path, monkeypatch):
    import rl_common
    base = tmp_path / ".claude" / "plugins" / "data"
    source = base / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    canonical = tmp_path / ".claude" / "evolve-anything"
    canonical.mkdir()
    (canonical / ".data-dir-unified").touch()
    (canonical / "evolve-queue.json").write_text('{}')
    monkeypatch.setattr(rl_common, "_CC_PLUGIN_DATA_BASE", base)
    monkeypatch.setattr(rl_common, "_DEFAULT_DATA_DIR", canonical)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    monkeypatch.setattr(collectors, "_queue_notice", None)
    item = collectors._build_weekly_board_output(now=NOW)
    assert item is not None and item.tier == 1
    assert "戦果ボードの要約を読めません" in item.text


@pytest.mark.parametrize("migration_available", [True, False])
def test_import_failure_custom_layout_is_silent(tmp_path, monkeypatch, migration_available):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    (tmp_path / "evolve-queue.json").write_text('{}')
    monkeypatch.setattr(collectors, "_queue_notice", None)
    if not migration_available:
        monkeypatch.setattr(collectors, "_data_dir_migration", None)
    assert collectors._build_weekly_board_output(now=NOW) is None


def test_collector_exception_is_visible(monkeypatch):
    monkeypatch.setattr(collectors, "_resolve_queue_data", Mock(side_effect=OSError("fixture failure")))
    item = collectors._build_weekly_board_output(now=NOW)
    assert item.tier == 1 and "fixture failure" in item.text


def test_zero_counts_are_measured(sources):
    sources[3].return_value["count"] = 0
    sources[5].return_value.clear()
    board = build(sources)
    assert board["measured"] is True
    assert board["pillar2_count"] == board["pillar4_count"] == 0


def test_revert_count_from_fixed_history_and_actual_files(sources, monkeypatch):
    import hashlib
    wb, _, root, *_ = sources
    target = root / "skills" / "example" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("applied")
    entry = {"id": "one", "human_accepted": True, "scope": "project",
             "repo_id": str(root), "relative_path": "skills/example/SKILL.md",
             "timestamp": "2026-09-06T00:00:00Z", "revert_schema_version": 1,
             "revert_before_b64": "eJwDAAAAAAE=", "revert_encoding": "zlib+base64",
             "after_sha": hashlib.sha256(b"applied").hexdigest()}
    history = [entry, dict(entry, id="older", timestamp="2026-08-01T00:00:00Z",
                           after_sha=hashlib.sha256(b"previous").hexdigest()),
               {"id": "legacy", "human_accepted": True, "timestamp": "2026-07-01T00:00:00Z"}]
    reader = Mock(return_value=MeasuredList(history))
    monkeypatch.setattr(wb.evolve_revert_listing, "load_effective_history", reader)
    monkeypatch.setattr(wb.evolve_revert_listing, "build_revert_listing", real_revert_listing)
    assert build(sources)["pillar4_count"] == 1
    assert target.read_text() == "applied"
    reader.assert_called_once_with("evolve-anything")


@pytest.mark.parametrize("stamp,week,day", [
    ("2026-09-07T00:30:00+09:00", "2026-W37", "2026-09-07"),
    ("2027-01-01T00:30:00+09:00", "2026-W53", "2027-01-01"),
])
def test_injected_local_date_and_iso_year(sources, stamp, week, day):
    now = datetime.fromisoformat(stamp)
    board = build(sources, now=now)
    assert board["computed_on"] == day
    assert board["week_id"] == week
    assert notify(sources[2], board, now) is not None


def test_runner_reads_previous_before_overwrite_and_embeds(sources, monkeypatch):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    from pathlib import Path
    from types import SimpleNamespace

    script = Path(__file__).resolve().parents[3] / "bin" / "evolve-daily-run"
    loader = SourceFileLoader("daily_weekly_test", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    runner = importlib.util.module_from_spec(spec)
    loader.exec_module(runner)
    _, path, root, *_ = sources
    monkeypatch.setattr(runner, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(runner.rl_common, "resolve_data_dir", lambda env: root)
    monkeypatch.setattr(runner, "load_user_config", lambda: {})
    monkeypatch.setattr(runner.judge_runner, "run_daily_judge", Mock(return_value={}))
    monkeypatch.setattr(runner._proposal_digest, "build_proposal_digest", Mock(return_value={}))
    monkeypatch.setattr(runner.icebox_reconcile, "build_verdicts", Mock(return_value={"verdicts": []}))
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stderr="", stdout='[]' if cmd[0] == "gh" else '{"queue": []}')
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    previous = build(sources, now=datetime.now().astimezone())
    path.write_text(json.dumps({"weekly_board": previous}))
    for reader in sources[3:]:
        reader.reset_mock()
    assert runner.main() == 0
    assert json.loads(path.read_text())["weekly_board"] == previous
    for reader in sources[3:]:
        reader.assert_not_called()
    path.write_text('{}')
    assert runner.main() == 0
    assert json.loads(path.read_text())["weekly_board"]["pillar2_count"] == 7
    sources[3].assert_called_once()
