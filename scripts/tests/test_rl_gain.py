"""bin/evolve-gain のユニット + E2E テスト。"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest import mock

import pytest

# bin/evolve-gain を importlib でロード（.py 拡張子なし）
_BIN = Path(__file__).resolve().parent.parent.parent / "bin" / "evolve-gain"


def _load_rl_gain():
    loader = importlib.machinery.SourceFileLoader("rl_gain", str(_BIN))
    spec = importlib.util.spec_from_loader("rl_gain", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rl_gain():
    return _load_rl_gain()


# ─── ユーティリティ ────────────────────────────────────────────────


def test_is_rl_anything_skill_true(rl_gain):
    assert rl_gain.is_rl_anything_skill("evolve-anything:evolve") is True
    assert rl_gain.is_rl_anything_skill("evolve-anything:audit") is True
    assert rl_gain.is_rl_anything_skill("evolve-anything:implement") is True


def test_is_rl_anything_skill_false(rl_gain):
    assert rl_gain.is_rl_anything_skill("ship") is False
    assert rl_gain.is_rl_anything_skill("review") is False
    assert rl_gain.is_rl_anything_skill("") is False


def test_get_skill_short_name(rl_gain):
    assert rl_gain.get_skill_short_name("evolve-anything:evolve") == "evolve"
    assert rl_gain.get_skill_short_name("evolve-anything:spec-keeper") == "spec-keeper"
    assert rl_gain.get_skill_short_name("ship") == "ship"


# ─── usage.jsonl ──────────────────────────────────────────────────


@pytest.fixture
def usage_file(tmp_path):
    records = [
        {"skill_name": "evolve-anything:evolve", "ts": "2026-03-01T00:00:00Z", "project": "evolve-anything"},
        {"skill_name": "evolve-anything:audit", "ts": "2026-03-02T00:00:00Z", "project": "evolve-anything"},
        {"skill_name": "evolve-anything:evolve", "ts": "2026-03-03T00:00:00Z", "project": "evolve-anything"},
        {"skill_name": "ship", "ts": "2026-03-04T00:00:00Z", "project": "evolve-anything"},
        {"skill_name": "evolve-anything:implement", "ts": "2026-03-05T00:00:00Z", "project": "evolve-anything"},
    ]
    f = tmp_path / "usage.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records))
    return tmp_path


def test_load_usage_records(rl_gain, usage_file):
    records = rl_gain.load_usage_records(usage_file)
    assert len(records) == 5


def test_load_usage_records_missing(rl_gain, tmp_path):
    records = rl_gain.load_usage_records(tmp_path)
    assert records == []


def test_get_rl_skill_counts(rl_gain, usage_file):
    records = rl_gain.load_usage_records(usage_file)
    counts = rl_gain.get_rl_skill_counts(records)
    assert isinstance(counts, Counter)
    assert counts["evolve"] == 2
    assert counts["audit"] == 1
    assert counts["implement"] == 1
    assert "ship" not in counts  # evolve-anything 外はカウントしない


def test_get_since_date_rl_records(rl_gain, usage_file):
    records = rl_gain.load_usage_records(usage_file)
    rl_records = [r for r in records if r.get("skill_name", "").startswith("evolve-anything:")]
    since = rl_gain.get_since_date(rl_records)
    assert since == "2026-03-01"


def test_get_since_date_empty(rl_gain):
    assert rl_gain.get_since_date([]) is None


# ─── evolve-state.json ────────────────────────────────────────────


@pytest.fixture
def evolve_state_file(tmp_path):
    state = {
        "trigger_history": [
            {"ts": "2026-03-01T00:00:00Z"},
            {"ts": "2026-03-02T00:00:00Z"},
            {"ts": "2026-03-03T00:00:00Z"},
        ]
    }
    (tmp_path / "evolve-state.json").write_text(json.dumps(state))
    return tmp_path


def test_get_auto_triggered_count(rl_gain, evolve_state_file):
    count = rl_gain.get_auto_triggered_count(evolve_state_file)
    assert count == 3


def test_get_auto_triggered_count_missing(rl_gain, tmp_path):
    assert rl_gain.get_auto_triggered_count(tmp_path) == 0


# ─── audit-history.jsonl ─────────────────────────────────────────


@pytest.fixture
def audit_history_file(tmp_path):
    records = [
        {"timestamp": "2026-03-01T00:00:00Z"},
        {"timestamp": "2026-03-02T00:00:00Z", "environment_score": 0.42},
        {"timestamp": "2026-03-03T00:00:00Z", "environment_score": 0.55},
    ]
    f = tmp_path / "audit-history.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records))
    return tmp_path


def test_get_env_score(rl_gain, audit_history_file):
    score = rl_gain.get_env_score(audit_history_file)
    assert score == pytest.approx(0.55)


def test_get_env_score_no_score(rl_gain, tmp_path):
    # timestamp のみ（env_score なし）
    (tmp_path / "audit-history.jsonl").write_text('{"timestamp": "2026-03-01T00:00:00Z"}\n')
    assert rl_gain.get_env_score(tmp_path) is None


def test_get_env_score_missing(rl_gain, tmp_path):
    assert rl_gain.get_env_score(tmp_path) is None


# ─── sessions.db ─────────────────────────────────────────────────


def test_get_session_count_no_db(rl_gain, tmp_path):
    count = rl_gain.get_session_count(tmp_path)
    assert count == 0


def test_get_session_count_with_db(rl_gain, tmp_path):
    try:
        import duckdb
    except ImportError:
        pytest.skip("duckdb not installed")
    db_path = tmp_path / "sessions.db"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions (session_id VARCHAR, timestamp VARCHAR, project VARCHAR,"
        " type VARCHAR, skill_count INT, error_count INT, raw_json VARCHAR)"
    )
    conn.execute("INSERT INTO sessions VALUES ('s1','2026-03-01','proj','session',0,0,'{}')")
    conn.execute("INSERT INTO sessions VALUES ('s2','2026-03-02','proj','session',0,0,'{}')")
    conn.close()

    count = rl_gain.get_session_count(tmp_path)
    assert count == 2


# ─── compute_report E2E ──────────────────────────────────────────


@pytest.fixture
def full_data_dir(tmp_path):
    # usage.jsonl
    records = [
        {"skill_name": "evolve-anything:evolve", "ts": "2026-03-01T00:00:00Z"},
        {"skill_name": "evolve-anything:audit", "ts": "2026-03-02T00:00:00Z"},
        {"skill_name": "evolve-anything:reflect", "ts": "2026-03-03T00:00:00Z"},
        {"skill_name": "evolve-anything:reflect", "ts": "2026-03-04T00:00:00Z"},
        {"skill_name": "ship", "ts": "2026-03-05T00:00:00Z"},
    ]
    (tmp_path / "usage.jsonl").write_text("\n".join(json.dumps(r) for r in records))

    # evolve-state.json
    (tmp_path / "evolve-state.json").write_text(
        json.dumps({"trigger_history": [{"ts": "2026-03-01T00:00:00Z"}]})
    )

    # audit-history.jsonl
    (tmp_path / "audit-history.jsonl").write_text(
        '{"timestamp": "2026-03-01T00:00:00Z", "environment_score": 0.40}\n'
    )

    return tmp_path


def test_compute_report_invocation_count(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    # evolve:1 + audit:1 + reflect:2 = 4
    assert report["total_invocations"] == 4


def test_compute_report_saved_minutes(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    # evolve(10) + audit(15) + reflect(5×2=10) = 35
    assert report["saved_minutes"] == 35


def test_compute_report_auto_triggered(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    assert report["auto_triggered"] == 1


def test_compute_report_growth_level(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    gi = report["growth_info"]
    assert gi is not None
    assert gi.level == 4  # 0.40 → Lv.4 (Growing, threshold=0.35)


def test_compute_report_no_audit(rl_gain, tmp_path):
    # audit-history.jsonl なし → growth_info は None
    (tmp_path / "usage.jsonl").write_text(
        '{"skill_name": "evolve-anything:evolve", "ts": "2026-03-01T00:00:00Z"}\n'
    )
    report = rl_gain.compute_report(tmp_path)
    assert report["growth_info"] is None


def test_compute_report_since_date(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    assert report["since_date"] == "2026-03-01"


def test_compute_report_skill_breakdown(rl_gain, full_data_dir):
    report = rl_gain.compute_report(full_data_dir)
    names = [s["name"] for s in report["skill_breakdown"]]
    assert "reflect" in names
    assert "evolve" in names
    assert "ship" not in names  # evolve-anything 外は除外


# ─── print_report smoke test ─────────────────────────────────────


def test_print_report_no_crash(rl_gain, full_data_dir, capsys):
    report = rl_gain.compute_report(full_data_dir)
    rl_gain.print_report(report)
    out = capsys.readouterr().out
    assert "Evolve-Anything ROI Report" in out
    assert "Est. manual work saved" in out
    assert "Growth Level" in out


def test_print_report_na_growth(rl_gain, tmp_path, capsys):
    # audit なし → N/A 表示
    report = rl_gain.compute_report(tmp_path)
    rl_gain.print_report(report)
    out = capsys.readouterr().out
    assert "N/A" in out


# ─── subprocess E2E ──────────────────────────────────────────────


def test_main_runs(tmp_path):
    """bin/evolve-gain を実行して 3 秒以内にレポートが出ることを確認。"""
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(_BIN)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Evolve-Anything ROI Report" in result.stdout
