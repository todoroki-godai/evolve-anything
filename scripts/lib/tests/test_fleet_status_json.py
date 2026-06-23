#!/usr/bin/env python3
"""`evolve-fleet status --json` の出力テスト（#53）。

#53 の結論「レポート HTML 化は大半不要・fleet status のみ HTML 候補だが先に --json を整備」
に従い、`tokens` / `plugins` / `test-guard status` が既に持つ `--json` を `status` にも追加した。
本テストは format_status_json の構造化出力（datetime→ISO / ネスト dataclass 展開）と
CLI `status --json` の配線（JSON のみを stdout に出す）を封じる。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_LIB = Path(__file__).resolve().parents[1]
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from fleet import (  # noqa: E402
    STATUS_ENABLED,
    STATUS_NOT_ENABLED,
    STATUS_STALE,
    FleetRow,
    format_status_json,
)
from fleet.audit_runner import IssuesSummary  # noqa: E402


def test_format_status_json_is_valid_json_with_projects_key():
    rows = [FleetRow(pj_name="alpha", status=STATUS_ENABLED, env_score=0.72, growth_level=5)]
    data = json.loads(format_status_json(rows))
    assert "projects" in data
    assert len(data["projects"]) == 1
    p = data["projects"][0]
    assert p["pj_name"] == "alpha"
    assert p["status"] == STATUS_ENABLED
    assert p["env_score"] == 0.72
    assert p["growth_level"] == 5


def test_format_status_json_latest_audit_isoformat():
    dt = datetime(2026, 6, 23, 1, 0, 0, tzinfo=timezone.utc)
    rows = [FleetRow(pj_name="a", status=STATUS_ENABLED, latest_audit=dt)]
    data = json.loads(format_status_json(rows))
    assert data["projects"][0]["latest_audit"] == dt.isoformat()


def test_format_status_json_latest_audit_none_is_null():
    rows = [FleetRow(pj_name="a", status=STATUS_NOT_ENABLED)]
    data = json.loads(format_status_json(rows))
    assert data["projects"][0]["latest_audit"] is None


def test_format_status_json_issues_summary_nested_dict():
    iss = IssuesSummary(line_violations=2, hardcoded_values=1)
    rows = [FleetRow(pj_name="a", status=STATUS_ENABLED, issues_summary=iss)]
    data = json.loads(format_status_json(rows))
    summary = data["projects"][0]["issues_summary"]
    assert summary["line_violations"] == 2
    assert summary["hardcoded_values"] == 1


def test_format_status_json_empty_rows():
    data = json.loads(format_status_json([]))
    assert data == {"projects": []}


def test_format_status_json_preserves_token_and_cache_fields():
    rows = [FleetRow(
        pj_name="a", status=STATUS_ENABLED,
        subagents_30d=12, tokens_30d=123456, cache_hit_pct=0.8, cache_reuse_factor=1.2,
    )]
    p = json.loads(format_status_json(rows))["projects"][0]
    assert p["subagents_30d"] == 12
    assert p["tokens_30d"] == 123456
    assert p["cache_hit_pct"] == 0.8
    assert p["cache_reuse_factor"] == 1.2


# ─── CLI 配線: status --json は JSON のみを stdout に出す ───────────────────

def test_cli_status_json_outputs_only_json(capsys, monkeypatch):
    """`status --json` は table/alarm/hint を出さず JSON のみを stdout に書く。"""
    import fleet_config
    from fleet import cli

    sample = [
        FleetRow(pj_name="alpha", status=STATUS_ENABLED, env_score=0.5),
        FleetRow(pj_name="beta", status=STATUS_STALE),  # --all なしで除外される
    ]
    monkeypatch.setattr(fleet_config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "collect_fleet_status", lambda **kw: list(sample))
    monkeypatch.setattr(cli, "_inject_token_metrics", lambda rows, days=30: None)

    rc = cli.main(["status", "--json", "--no-write"])
    assert rc == 0
    out = capsys.readouterr().out
    # stdout 全体が単一の JSON（hint 行が混じれば json.loads が失敗する）
    data = json.loads(out)
    names = [p["pj_name"] for p in data["projects"]]
    assert "alpha" in names
    assert "beta" not in names  # STALE は --all なしで除外


def test_cli_status_json_all_includes_stale(capsys, monkeypatch):
    """`status --json --all` は STALE PJ も含める。"""
    import fleet_config
    from fleet import cli

    sample = [
        FleetRow(pj_name="alpha", status=STATUS_ENABLED),
        FleetRow(pj_name="beta", status=STATUS_STALE),
    ]
    monkeypatch.setattr(fleet_config, "load_config", lambda: {})
    monkeypatch.setattr(cli, "collect_fleet_status", lambda **kw: list(sample))
    monkeypatch.setattr(cli, "_inject_token_metrics", lambda rows, days=30: None)

    rc = cli.main(["status", "--json", "--all", "--no-write"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    names = [p["pj_name"] for p in data["projects"]]
    assert "alpha" in names and "beta" in names
