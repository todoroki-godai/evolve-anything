#!/usr/bin/env python3
"""#186 CLI 配線テスト: `evolve --drain --result-json <path>` が reconcile_surfaced の
連続提示 count marker を apply 境界で永続化する。

根因（#186 / #494）: reconcile_surfaced（毎回再提示を断つ自動却下セーフティネット）は
phases_remediate の `persist=not ctx.dry_run` 経由でしか呼ばれない。標準フローは
`evolve --dry-run` 分析のみで常に persist=False → 連続提示 marker
（remediation_surfaced/<slug>.json）が永久に書かれず、閾値
DEFAULT_AUTO_REJECT_AFTER_RUNS=2 に届かず自動却下が発火しない（全 PJ 死蔵）。

対処: weak_signals #484 / reward_ema #64 / subagent_traces #135 と同型に、永続化を
drain の apply 境界へ移設する。dry-run は分析・表示のみ（persist=False）で marker を
書かず、drain（persist=True）だけが count marker を前進させ閾値到達で record_rejection する。

HOME / DATA_DIR 隔離はルート conftest（#457/#119）が autouse で行う。SURFACED_ROOT /
LEDGER_ROOT は module-top で DATA_DIR から計算されるため、本テストは tmp_path へ
monkeypatch して隔離する（既存 test_remediation_suppression_ledger と同流儀）。
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402
import remediation.suppression_ledger as sl  # noqa: E402


def _individual_issue(path="x.md"):
    return {
        "type": "line_limit_violation",
        "file": path,
        "detail": {"path": path},
    }


def _result_with_surfaceable_issue(rule_violations=None):
    """dry-run が `--output` に書く形の full result（remediation.classified を含む）。"""
    return {
        "generated_at": "2026-07-09T00:00:00+00:00",
        "phases": {
            "remediation": {
                "classified": {
                    "proposable_custom_individual": [_individual_issue("x.md")],
                    "proposable_global": [],
                },
            },
            "discover": {"rule_violation_observed": rule_violations or []},
        },
    }


def _stub_drain_neighbors(monkeypatch):
    """テスト対象（reconcile_surfaced 永続化）以外の drain persist を無害な固定値へ差し替える。"""
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    monkeypatch.setattr(
        ws_batch, "persist_weak_signals_drain",
        lambda slug, **kw: {"written": 0, "dry_run": False},
    )
    monkeypatch.setattr(
        re, "persist_reward_ema_batch", lambda project_dir, **kw: {"persisted": 0}
    )
    monkeypatch.setattr(
        qs, "persist_last_evolve", lambda slug, **kw: {"written": 0, "dry_run": False}
    )
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )
    monkeypatch.setattr(
        _state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False}
    )


def _isolate_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
    monkeypatch.setattr(sl, "SURFACED_ROOT", tmp_path / "remediation_surfaced")


# ── build_reconcile_tracked 単体 ─────────────────────────────────
class TestBuildReconcileTracked:
    def test_composes_individual_global_and_rule_violation_synthetics(self):
        classified = {
            "proposable_custom_individual": [_individual_issue("a.md")],
            "proposable_global": [{"type": "rule_candidate", "file": "g.md", "detail": {}}],
        }
        rule_violations = [{"violated_command": "cd", "pattern": "cd X", "count": 3}]
        tracked = evolve.build_reconcile_tracked(classified, rule_violations)
        # individual + global + synthetics(rule_violation) の順で連結。
        assert len(tracked) == 3
        assert tracked[0]["type"] == "line_limit_violation"
        assert tracked[1]["type"] == "rule_candidate"
        assert tracked[2]["type"] == "rule_violation_observed"
        assert tracked[2]["detail"]["target"] == "cd"

    def test_empty_inputs_return_empty(self):
        assert evolve.build_reconcile_tracked({}, []) == []
        assert evolve.build_reconcile_tracked(None, None) == []


# ── drain 永続化 E2E ─────────────────────────────────────────────
class TestDrainPersistsSurfaced:
    def test_drain_writes_surfaced_marker_count_one(self, tmp_path, monkeypatch, capsys):
        _isolate_ledger(tmp_path, monkeypatch)
        _stub_drain_neighbors(monkeypatch)

        rj = tmp_path / "result.json"
        rj.write_text(json.dumps(_result_with_surfaceable_issue()), encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(rj)],
        )

        evolve.main()

        out = json.loads(capsys.readouterr().out)
        assert out["remediation_surfaced_persisted"]["tracked"] == 1
        assert out["remediation_surfaced_persisted"]["auto_rejected"] == 0

        markers = list((tmp_path / "remediation_surfaced").glob("*.json"))
        assert len(markers) == 1
        entries = json.loads(markers[0].read_text(encoding="utf-8"))["entries"]
        assert len(entries) == 1
        assert list(entries.values())[0]["count"] == 1

    def test_second_drain_reaches_threshold_and_auto_rejects(self, tmp_path, monkeypatch, capsys):
        _isolate_ledger(tmp_path, monkeypatch)
        _stub_drain_neighbors(monkeypatch)

        rj = tmp_path / "result.json"
        rj.write_text(json.dumps(_result_with_surfaceable_issue()), encoding="utf-8")
        argv = ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(rj)]

        # 1 回目: count=1、まだ却下しない。
        monkeypatch.setattr(sys, "argv", argv)
        evolve.main()
        out1 = json.loads(capsys.readouterr().out)
        assert out1["remediation_surfaced_persisted"]["auto_rejected"] == 0

        # 2 回目: 同 issue 再提示 → count=2 で閾値到達 → 自動却下。
        monkeypatch.setattr(sys, "argv", argv)
        evolve.main()
        out2 = json.loads(capsys.readouterr().out)
        assert out2["remediation_surfaced_persisted"]["auto_rejected"] == 1

        # ledger（suppression）に却下が記録され、以後 is_suppressed=True。
        ledger_files = list((tmp_path / "remediation_suppression").glob("*.jsonl"))
        assert ledger_files
        assert sl.is_suppressed(_individual_issue("x.md"), slug=sl.resolve_slug(cwd=Path("/tmp/whatever")))

    def test_rule_violation_lane_tracked_in_drain(self, tmp_path, monkeypatch, capsys):
        _isolate_ledger(tmp_path, monkeypatch)
        _stub_drain_neighbors(monkeypatch)

        result = _result_with_surfaceable_issue(
            rule_violations=[{"violated_command": "cd", "pattern": "cd X", "count": 5}]
        )
        rj = tmp_path / "result.json"
        rj.write_text(json.dumps(result), encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(rj)],
        )

        evolve.main()

        out = json.loads(capsys.readouterr().out)
        # individual 1 + rule_violation synthetic 1 = 2 追跡。
        assert out["remediation_surfaced_persisted"]["tracked"] == 2

    def test_drain_without_result_json_skips_gracefully(self, tmp_path, monkeypatch, capsys):
        _isolate_ledger(tmp_path, monkeypatch)
        _stub_drain_neighbors(monkeypatch)

        monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])
        evolve.main()

        out = json.loads(capsys.readouterr().out)
        assert out["remediation_surfaced_persisted"] == {"skipped": "no_result_json"}
        # marker は書かれない（純度維持）。
        root = tmp_path / "remediation_surfaced"
        assert not root.exists() or list(root.glob("*.json")) == []
        # 他 persist は無傷（drain 本体を完走）。
        assert "weak_signals_persisted" in out


# ── dry-run 純度: count は drain だけが前進させる ─────────────────
class TestDryRunPurity:
    def test_persist_false_does_not_advance_count(self, tmp_path, monkeypatch):
        """persist=False（dry-run 表示用）は何回呼んでも count を進めず marker を書かない。

        drain（persist=True）だけが count を前進させる二相不変を固定する。
        """
        _isolate_ledger(tmp_path, monkeypatch)
        issue = _individual_issue("x.md")

        # dry-run 相当の read-only 呼び出しを複数回。
        for _ in range(3):
            sl.reconcile_surfaced([issue], slug="proj", persist=False)
        root = tmp_path / "remediation_surfaced"
        assert not root.exists() or list(root.glob("*.json")) == []

        # drain 相当（persist=True）を 1 回 → count=1（閾値未満・未却下）。
        r = sl.reconcile_surfaced([issue], slug="proj", persist=True)
        assert r["auto_rejected"] == 0
        entries = json.loads(sl.surfaced_path("proj").read_text(encoding="utf-8"))["entries"]
        assert list(entries.values())[0]["count"] == 1
