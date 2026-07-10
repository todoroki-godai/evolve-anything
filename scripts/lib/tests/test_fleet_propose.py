#!/usr/bin/env python3
"""fleet propose（#81 Phase 2）のテスト — queue 待ち PJ への evolve --dry-run 提案バッチ生成。

決定論・LLM 非依存。検証対象:
  - ``select_targets``: queue schema からの対象選定（max_pj 上限・壊れた要素の無視）
  - ``estimate_cost`` / ``format_cost_confirmation``: llm-batch-guard のコスト提示
    （material_count は proxy であり実測トークン数を捏造しないことを含む）
  - ``confirm_batch``: --yes バイパス / y/n プロンプト
  - ``filter_previously_rejected_candidates``: optimize_history の reject 済み候補を
    再提示から除外（evolve_decisions._extract_candidates / optimize_history_store の
    既存 API を再利用。DI で hermetic）
  - ``summarize_pj_result``: evolve result（canonical schema）からの提案件数集計
  - ``run_propose_batch``: 順次実行・1 PJ 失敗は他を止めない・stub run_evolve_fn で LLM ゼロ
  - ``build_batch_report`` / ``render_markdown_report`` / ``write_reports``: 集約レポート
  - CLI ``propose`` サブコマンドの配線
  - dry-run 純度 E2E: 実 run_evolve(dry_run=True) 前後で DATA_DIR 配下が不変
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet import propose as fp  # noqa: E402


# --- select_targets -----------------------------------------------------------


class TestSelectTargets:
    def test_none_queue_data_returns_empty(self):
        assert fp.select_targets(None, max_pj=5) == []

    def test_missing_queue_key_returns_empty(self):
        assert fp.select_targets({}, max_pj=5) == []

    def test_takes_up_to_max_pj_in_order(self):
        queue_data = {
            "queue": [
                {"pj_slug": "a", "project_path": "/x/a", "material_count": 10},
                {"pj_slug": "b", "project_path": "/x/b", "material_count": 8},
                {"pj_slug": "c", "project_path": "/x/c", "material_count": 6},
            ]
        }
        out = fp.select_targets(queue_data, max_pj=2)
        assert [t["pj_slug"] for t in out] == ["a", "b"]

    def test_skips_malformed_entries(self):
        queue_data = {"queue": [{"pj_slug": ""}, "not-a-dict", {"pj_slug": "ok", "material_count": 3}]}
        out = fp.select_targets(queue_data, max_pj=10)
        assert [t["pj_slug"] for t in out] == ["ok"]

    def test_max_pj_zero_returns_empty(self):
        queue_data = {"queue": [{"pj_slug": "a", "material_count": 1}]}
        assert fp.select_targets(queue_data, max_pj=0) == []


# --- estimate_cost / format_cost_confirmation ---------------------------------


class TestEstimateCost:
    def test_aggregates_pj_count_and_material_count(self):
        targets = [
            {"pj_slug": "a", "material_count": 5},
            {"pj_slug": "b", "material_count": 3},
        ]
        cost = fp.estimate_cost(targets)
        assert cost["pj_count"] == 2
        assert cost["pjs"] == ["a", "b"]
        assert cost["per_pj_material_count"] == {"a": 5, "b": 3}
        assert cost["total_material_count"] == 8

    def test_does_not_fabricate_token_count(self):
        """factual-claims 準拠: 実測不能なトークン数を数値として出さない。"""
        cost = fp.estimate_cost([{"pj_slug": "a", "material_count": 5}])
        assert "estimated_tokens" not in cost
        assert "token" not in json.dumps(cost, ensure_ascii=False).lower().replace("トークン", "")

    def test_proxy_note_labels_material_count_as_proxy(self):
        cost = fp.estimate_cost([{"pj_slug": "a", "material_count": 1}])
        assert "proxy" in cost["proxy_note"] or "実測トークン数ではありません" in cost["proxy_note"]


class TestFormatCostConfirmation:
    def test_includes_pj_names_and_material_counts(self):
        cost = fp.estimate_cost(
            [{"pj_slug": "alpha", "material_count": 5}, {"pj_slug": "beta", "material_count": 2}]
        )
        text = fp.format_cost_confirmation(cost)
        assert "alpha" in text and "beta" in text
        assert "alpha=5" in text
        assert "beta=2" in text
        assert "Haiku" in text


# --- confirm_batch -------------------------------------------------------------


class TestConfirmBatch:
    def test_yes_flag_bypasses_prompt(self):
        assert fp.confirm_batch(yes=True, input_func=lambda _: (_ for _ in ()).throw(AssertionError("called"))) is True

    def test_y_answer_confirms(self):
        assert fp.confirm_batch(yes=False, input_func=lambda _: "y") is True

    def test_n_answer_cancels(self):
        assert fp.confirm_batch(yes=False, input_func=lambda _: "n") is False

    def test_empty_answer_cancels(self):
        assert fp.confirm_batch(yes=False, input_func=lambda _: "") is False

    def test_eof_cancels(self):
        def _raise(_):
            raise EOFError()

        assert fp.confirm_batch(yes=False, input_func=_raise) is False


# --- filter_previously_rejected_candidates ------------------------------------


def _result_with_skill_evolve(names_and_suitability):
    return {
        "phases": {
            "skill_evolve": {
                "assessments": [
                    {"skill_name": n, "skill_dir": f"/skills/{n}", "suitability": s}
                    for n, s in names_and_suitability
                ]
            }
        }
    }


class TestFilterPreviouslyRejectedCandidates:
    def test_no_candidates_returns_empty(self):
        out = fp.filter_previously_rejected_candidates({"phases": {}}, "slug", history=[])
        assert out == {"kept": [], "suppressed": []}

    def test_rejected_skill_is_suppressed(self):
        result = _result_with_skill_evolve([("skill-a", "high")])
        history = [
            {
                "source": "evolve_diff",
                "skill_name": "skill-a",
                "human_accepted": False,
                "timestamp": "2026-07-01T00:00:00+00:00",
                "rejection_reason": "not now",
            }
        ]
        out = fp.filter_previously_rejected_candidates(result, "slug", history=history)
        assert out["kept"] == []
        assert len(out["suppressed"]) == 1
        assert out["suppressed"][0]["skill_name"] == "skill-a"
        assert out["suppressed"][0]["rejection_reason"] == "not now"

    def test_accepted_skill_is_kept(self):
        result = _result_with_skill_evolve([("skill-a", "high")])
        history = [
            {
                "source": "evolve_diff",
                "skill_name": "skill-a",
                "human_accepted": True,
                "timestamp": "2026-07-01T00:00:00+00:00",
            }
        ]
        out = fp.filter_previously_rejected_candidates(result, "slug", history=history)
        assert len(out["kept"]) == 1
        assert out["suppressed"] == []

    def test_latest_decision_wins_over_older_reject(self):
        """古い reject の後に accept された場合、最新（accept）が優先され再提示される。"""
        result = _result_with_skill_evolve([("skill-a", "high")])
        history = [
            {
                "source": "evolve_diff",
                "skill_name": "skill-a",
                "human_accepted": False,
                "timestamp": "2026-06-01T00:00:00+00:00",
            },
            {
                "source": "evolve_diff",
                "skill_name": "skill-a",
                "human_accepted": True,
                "timestamp": "2026-07-01T00:00:00+00:00",
            },
        ]
        out = fp.filter_previously_rejected_candidates(result, "slug", history=history)
        assert len(out["kept"]) == 1
        assert out["suppressed"] == []

    def test_no_history_keeps_all(self):
        result = _result_with_skill_evolve([("skill-a", "medium")])
        out = fp.filter_previously_rejected_candidates(result, "slug", history=[])
        assert len(out["kept"]) == 1

    def test_non_evolve_diff_source_ignored(self):
        """source が evolve_diff 以外の history レコードは判定に使わない。"""
        result = _result_with_skill_evolve([("skill-a", "high")])
        history = [
            {"source": "optimize", "skill_name": "skill-a", "human_accepted": False, "timestamp": "z"}
        ]
        out = fp.filter_previously_rejected_candidates(result, "slug", history=history)
        assert len(out["kept"]) == 1


# --- summarize_pj_result -------------------------------------------------------


def _canonical_result(
    remediation_proposable=0,
    se_high=0,
    se_medium=0,
    triage=None,
    split_candidates=None,
):
    triage = triage or {}
    return {
        "phases": {
            "remediation": {"proposable": remediation_proposable},
            "skill_evolve": {"high_suitability": se_high, "medium_suitability": se_medium},
            "skill_triage": {
                "CREATE": triage.get("CREATE", []),
                "UPDATE": triage.get("UPDATE", []),
                "SPLIT": triage.get("SPLIT", []),
                "MERGE": triage.get("MERGE", []),
            },
            "reorganize": {"split_candidates": split_candidates or []},
        }
    }


class TestSummarizePjResult:
    def test_zero_everything(self):
        s = fp.summarize_pj_result(_canonical_result())
        assert s["total_proposals"] == 0

    def test_sums_all_proposal_sources(self):
        result = _canonical_result(
            remediation_proposable=2,
            se_high=1,
            se_medium=1,
            triage={"CREATE": ["x"], "UPDATE": ["y", "z"]},
            split_candidates=[{"skill_name": "s", "line_count": 900}],
        )
        s = fp.summarize_pj_result(result)
        assert s["remediation_proposable"] == 2
        assert s["skill_evolve_high"] == 1
        assert s["skill_evolve_medium"] == 1
        assert s["skill_triage"] == {"CREATE": 1, "UPDATE": 2, "SPLIT": 0, "MERGE": 0}
        assert s["reorganize_split_candidates"] == 1
        # 2 + (1+1) + (1+2) + 1 = 8
        assert s["total_proposals"] == 8

    def test_suppressed_rejected_reduces_effective_skill_evolve_total(self):
        result = _canonical_result(se_high=2, se_medium=0)
        s = fp.summarize_pj_result(result, suppressed_rejected_count=1)
        assert s["skill_evolve_high"] == 2  # 生カウントは維持
        assert s["skill_evolve_suppressed_rejected"] == 1
        assert s["total_proposals"] == 1  # 2 - 1 = 1（抑制分を差し引いた実効数）

    def test_suppressed_count_floor_at_zero(self):
        """suppressed が生カウントを超えても total は負にならない。"""
        result = _canonical_result(se_high=1, se_medium=0)
        s = fp.summarize_pj_result(result, suppressed_rejected_count=5)
        assert s["total_proposals"] == 0

    def test_missing_phases_defaults_to_zero(self):
        s = fp.summarize_pj_result({})
        assert s["total_proposals"] == 0


# --- run_propose_batch ---------------------------------------------------------


class TestRunProposeBatch:
    def test_calls_stub_for_each_target_sequentially(self, tmp_path):
        calls = []

        def _stub(*, project_dir, dry_run):
            calls.append((project_dir, dry_run))
            return _canonical_result(remediation_proposable=1)

        p_a = tmp_path / "a"
        p_a.mkdir()
        p_b = tmp_path / "b"
        p_b.mkdir()
        targets = [
            {"pj_slug": "a", "project_path": str(p_a), "material_count": 5},
            {"pj_slug": "b", "project_path": str(p_b), "material_count": 3},
        ]
        out = fp.run_propose_batch(targets, run_evolve_fn=_stub)
        assert calls == [(str(p_a), True), (str(p_b), True)]
        assert [e["status"] for e in out] == ["ok", "ok"]
        assert out[0]["summary"]["remediation_proposable"] == 1

    def test_one_pj_exception_does_not_stop_others(self, tmp_path):
        p_a = tmp_path / "a"
        p_a.mkdir()
        p_b = tmp_path / "b"
        p_b.mkdir()

        def _stub(*, project_dir, dry_run):
            if project_dir == str(p_a):
                raise RuntimeError("boom")
            return _canonical_result(remediation_proposable=2)

        targets = [
            {"pj_slug": "a", "project_path": str(p_a), "material_count": 5},
            {"pj_slug": "b", "project_path": str(p_b), "material_count": 3},
        ]
        out = fp.run_propose_batch(targets, run_evolve_fn=_stub)
        assert out[0]["status"] == "error"
        assert "boom" in out[0]["error"]
        assert out[1]["status"] == "ok"
        assert out[1]["summary"]["remediation_proposable"] == 2

    def test_missing_project_path_recorded_as_error(self):
        targets = [{"pj_slug": "ghost", "project_path": None, "material_count": 1}]
        out = fp.run_propose_batch(targets, run_evolve_fn=lambda **kw: {})
        assert out[0]["status"] == "error"
        assert "project_path" in out[0]["error"]

    def test_nonexistent_dir_recorded_as_error(self, tmp_path):
        targets = [
            {"pj_slug": "ghost", "project_path": str(tmp_path / "does-not-exist"), "material_count": 1}
        ]
        out = fp.run_propose_batch(targets, run_evolve_fn=lambda **kw: {})
        assert out[0]["status"] == "error"

    def test_filters_rejected_candidates_using_history_from_optimize_history(self, tmp_path, monkeypatch):
        """デフォルト（history 未注入）は optimize_history_store.load_history を通す。"""
        import optimize_history_store as ohs

        monkeypatch.setattr(ohs, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "data" / "optimize_history")
        ohs.append_entry(
            {
                "id": "evdiff_1",
                "source": "evolve_diff",
                "skill_name": "skill-a",
                "human_accepted": False,
                "timestamp": "2026-07-01T00:00:00+00:00",
            },
            slug="proj-x",
        )

        p = tmp_path / "proj-x-dir"
        p.mkdir()

        def _stub(*, project_dir, dry_run):
            return _result_with_skill_evolve([("skill-a", "high")])

        targets = [{"pj_slug": "proj-x", "project_path": str(p), "material_count": 1}]
        out = fp.run_propose_batch(targets, run_evolve_fn=_stub)
        assert out[0]["status"] == "ok"
        assert len(out[0]["suppressed_candidates"]) == 1
        assert out[0]["summary"]["skill_evolve_suppressed_rejected"] == 1


# --- build_batch_report / render_markdown_report -------------------------------


class TestBuildBatchReportAndRenderMarkdown:
    def test_report_aggregates_ok_and_error_counts(self):
        batch = [
            {
                "pj_slug": "a",
                "project_path": "/x/a",
                "material_count": 5,
                "status": "ok",
                "result": {"slug": "a", "env_tier": "small"},
                "summary": fp.summarize_pj_result(_canonical_result(remediation_proposable=3)),
                "suppressed_candidates": [],
            },
            {
                "pj_slug": "b",
                "project_path": "/x/b",
                "material_count": 2,
                "status": "error",
                "error": "boom",
            },
        ]
        cost = fp.estimate_cost([{"pj_slug": "a", "material_count": 5}, {"pj_slug": "b", "material_count": 2}])
        report = fp.build_batch_report(batch, generated_at="2026-07-10T00:00:00+00:00", cost=cost)
        assert report["pj_count"] == 2
        assert report["ok_count"] == 1
        assert report["error_count"] == 1
        assert report["total_proposals"] == 3
        # distilled entries must not carry the full raw result verbatim
        assert "result" not in report["pjs"][0]
        assert report["pjs"][0]["slug"] == "a"
        assert report["pjs"][0]["env_tier"] == "small"
        assert report["pjs"][1]["error"] == "boom"

    def test_markdown_contains_pj_summaries_and_error(self):
        batch = [
            {
                "pj_slug": "a",
                "project_path": "/x/a",
                "material_count": 5,
                "status": "ok",
                "result": {},
                "summary": fp.summarize_pj_result(_canonical_result(remediation_proposable=3)),
                "suppressed_candidates": [],
            },
            {
                "pj_slug": "b",
                "project_path": "/x/b",
                "material_count": 2,
                "status": "error",
                "error": "boom",
            },
        ]
        cost = fp.estimate_cost([{"pj_slug": "a", "material_count": 5}])
        report = fp.build_batch_report(batch, generated_at="2026-07-10T00:00:00+00:00", cost=cost)
        md = fp.render_markdown_report(report)
        assert "# evolve 提案バッチ" in md
        assert "a" in md and "b" in md
        assert "エラー — boom" in md
        assert "remediation.proposable: 3" in md


# --- write_reports --------------------------------------------------------------


class TestWriteReports:
    def test_writes_md_and_json_with_date_in_filename(self, tmp_path):
        cost = fp.estimate_cost([{"pj_slug": "a", "material_count": 1}])
        report = fp.build_batch_report(
            [
                {
                    "pj_slug": "a",
                    "project_path": "/x/a",
                    "material_count": 1,
                    "status": "ok",
                    "result": {},
                    "summary": fp.summarize_pj_result(_canonical_result(remediation_proposable=1)),
                    "suppressed_candidates": [],
                }
            ],
            generated_at="2026-07-10T00:00:00+00:00",
            cost=cost,
        )
        data_dir = tmp_path / "data"
        md_path, json_path = fp.write_reports(report, data_dir=data_dir, date_str="20260710")
        assert md_path == data_dir / "evolve-proposals-20260710.md"
        assert json_path == data_dir / "evolve-proposals-20260710.json"
        assert md_path.exists() and json_path.exists()
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert loaded["pj_count"] == 1

        # 同日再実行は上書き（evolve-queue.json と同じ運用）で余分なファイルを増やさない。
        before = sorted(p.name for p in data_dir.iterdir())
        fp.write_reports(report, data_dir=data_dir, date_str="20260710")
        after = sorted(p.name for p in data_dir.iterdir())
        assert before == after


# --- CLI propose サブコマンド ----------------------------------------------------


class TestProposeCli:
    def test_default_reads_evolve_queue_json(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_propose

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        queue_json = {
            "generated_at": "2026-07-10T00:00:00+00:00",
            "queue": [{"pj_slug": "alpha", "project_path": str(tmp_path / "alpha"), "material_count": 5}],
        }
        (tmp_path / "alpha").mkdir()
        (data_dir / "evolve-queue.json").write_text(json.dumps(queue_json), encoding="utf-8")

        monkeypatch.setattr(cli_propose, "_current_data_dir", lambda: data_dir)
        monkeypatch.setattr(
            fp, "run_propose_batch",
            lambda targets, **kw: [
                {
                    "pj_slug": "alpha",
                    "project_path": str(tmp_path / "alpha"),
                    "material_count": 5,
                    "status": "ok",
                    "result": {},
                    "summary": fp.summarize_pj_result(_canonical_result(remediation_proposable=1)),
                    "suppressed_candidates": [],
                }
            ],
        )

        rc = fcli.main(["propose", "--yes", "--max-pj", "5"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert (data_dir / f"evolve-proposals-{date_str}.md").exists()

    def test_missing_evolve_queue_json_returns_1(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_propose

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(cli_propose, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["propose"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "evolve-queue.json" in out

    def test_empty_queue_returns_0_without_writing_reports(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_propose

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "evolve-queue.json").write_text(
            json.dumps({"generated_at": "2026-07-10T00:00:00+00:00", "queue": []}), encoding="utf-8"
        )
        monkeypatch.setattr(cli_propose, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["propose"])
        assert rc == 0
        assert not any(data_dir.glob("evolve-proposals-*"))

    def test_cancel_at_confirmation_returns_1_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_propose

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (tmp_path / "alpha").mkdir()
        queue_json = {
            "generated_at": "2026-07-10T00:00:00+00:00",
            "queue": [{"pj_slug": "alpha", "project_path": str(tmp_path / "alpha"), "material_count": 5}],
        }
        (data_dir / "evolve-queue.json").write_text(json.dumps(queue_json), encoding="utf-8")
        monkeypatch.setattr(cli_propose, "_current_data_dir", lambda: data_dir)
        monkeypatch.setattr(fp, "confirm_batch", lambda **kw: False)

        rc = fcli.main(["propose"])
        assert rc == 1
        assert not any(data_dir.glob("evolve-proposals-*"))

    def test_live_flag_uses_gather_queue_result(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_propose

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (tmp_path / "beta").mkdir()

        def _fake_gather(args):
            return {
                "generated_at": "2026-07-10T00:00:00+00:00",
                "queue": [{"pj_slug": "beta", "project_path": str(tmp_path / "beta"), "material_count": 9}],
            }

        monkeypatch.setattr(cli_propose, "_current_data_dir", lambda: data_dir)
        monkeypatch.setattr(fcli, "_gather_queue_result", _fake_gather)
        monkeypatch.setattr(
            fp, "run_propose_batch",
            lambda targets, **kw: [
                {
                    "pj_slug": "beta",
                    "project_path": str(tmp_path / "beta"),
                    "material_count": 9,
                    "status": "ok",
                    "result": {},
                    "summary": fp.summarize_pj_result(_canonical_result(remediation_proposable=1)),
                    "suppressed_candidates": [],
                }
            ],
        )

        rc = fcli.main(["propose", "--live", "--yes"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "beta" in out


# --- dry-run 純度 E2E: SoR store 書込ゼロ ---------------------------------------


_DOCUMENTED_DRY_RUN_WRITES = (
    "evolve_pending/",
    "skill-evolve-cache.json",
    "constitutional_cache.json",
)


def _is_documented_write(rel_path: str) -> bool:
    return any(token in rel_path for token in _DOCUMENTED_DRY_RUN_WRITES)


def _snapshot(root: Path) -> dict:
    snap = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


class TestDryRunPurityE2E:
    """#81 受け入れ条件: dry-run なので一切 apply しない（store 書込ゼロを E2E assert）。

    実 run_evolve(dry_run=True) を使う（skills/evolve/scripts を on-demand で
    sys.path に追加する ``fp._default_run_evolve`` 経由）。空の project では
    dry-run 経路は評価系を skip/cache 参照するため実 LLM は呼ばれない
    （既存 test_dry_run_no_write_e2e.py と同じ前提）。
    """

    def test_run_propose_batch_with_real_run_evolve_writes_nothing_unexpected(
        self, tmp_path, monkeypatch
    ):
        import evolve_decisions as ed

        marker_root = tmp_path / "isolated-home" / ".claude" / "evolve-anything" / "evolve_pending"
        monkeypatch.setattr(ed, "MARKER_ROOT", marker_root)

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))

        project_a = tmp_path / "project-a"
        project_a.mkdir()
        project_b = tmp_path / "project-b"
        project_b.mkdir()

        targets = [
            {"pj_slug": "project-a", "project_path": str(project_a), "material_count": 1},
            {"pj_slug": "project-b", "project_path": str(project_b), "material_count": 1},
        ]

        before = _snapshot(tmp_path)
        out = fp.run_propose_batch(targets)  # run_evolve_fn=None → 実 run_evolve
        after = _snapshot(tmp_path)

        assert [e["status"] for e in out] == ["ok", "ok"]

        added = sorted(k for k in set(after) - set(before) if not _is_documented_write(k))
        removed = sorted(k for k in set(before) - set(after) if not _is_documented_write(k))
        modified = sorted(
            k
            for k in before.keys() & after.keys()
            if before[k] != after[k] and not _is_documented_write(k)
        )
        assert not added, f"dry-run が新規ファイルを作成した: {added}"
        assert not removed, f"dry-run が既存ファイルを削除した: {removed}"
        assert not modified, f"dry-run が既存ファイルを変更した: {modified}"
