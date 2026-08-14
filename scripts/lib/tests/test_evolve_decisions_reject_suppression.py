"""reject された提案が次回 emit で再抑制される配線テスト（#446）。

`_emit.py`（advisory マージ完了後の chokepoint）で `filter_rejected` を通し、
`_ingest.py`（advisory/skill 両レーンの合流点）で `record_pending_rejection` を呼ぶ。
既存の `remediation.suppression_ledger` を流用し新規ストアは作らない（#379 非抵触）。

設計: docs/decisions/drafts/446-reject-resuppression-design.md
すべて LLM-free・決定論。
"""
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decisions as ed  # noqa: E402
import remediation.suppression_ledger as sl  # noqa: E402


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def skill_file(tmp_path):
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    p = skill_dir / "SKILL.md"
    p.write_text("# my-skill\n\nトリガー: foo bar\n\n手順を踏む。\n", encoding="utf-8")
    return p


@pytest.fixture
def result_with_match(skill_file):
    return {
        "phases": {
            "discover": {
                "matched_skills": [
                    {
                        "matched_skill": "my-skill",
                        "skill_path": str(skill_file),
                        "pattern": "cat -> Read 多用",
                        "jaccard_score": 0.6,
                    }
                ]
            }
        }
    }


@pytest.fixture
def hist(tmp_path):
    return tmp_path / "optimize_history" / "testslug.jsonl"


_BROKEN_SKILL = """---
name: broken-skill
description: [unclosed
---

# broken-skill
"""


@pytest.fixture
def advisory_project(tmp_path):
    """invalid frontmatter を1件持つ最小 PJ（advisory レーン発火用）。"""
    skill = tmp_path / ".claude" / "skills" / "broken-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_BROKEN_SKILL, encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """queue / marker / suppression ledger を全て temp に隔離する。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")


def _advisory_entries(pending):
    return [p for p in pending if p.get("proposal_type") == "advisory"]


# ─── emit 側: suppression の適用 ────────────────────────────────────────────


class TestEmitAppliesSuppression:
    def test_rejected_skill_proposal_excluded_from_next_emit(
        self, result_with_match, skill_file, hist
    ):
        out1 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out1["pending"][0]["id"]
        ed.ingest_decisions("testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist)

        # 内容不変（before_sha 不変）のまま再 emit → 同じ proposal_id → 抑制される
        out2 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out2["pending"] == []
        assert out2["count"] == 0
        assert out2["reject_suppressed_total"] == 1
        assert out2["reject_suppressed"] == [{"id": pid, "file": "evolve_decisions"}]

    def test_rejected_proposal_reappears_after_content_changes(
        self, result_with_match, skill_file, hist
    ):
        """proposal_id は before_sha 込みなので、内容が変われば新 ID になり抑制対象から
        自動的に外れる（設計 §3.1: 早期解除の専用ロジックを持たない代わりの挙動）。"""
        out1 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out1["pending"][0]["id"]
        ed.ingest_decisions("testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist)

        skill_file.write_text("# my-skill\n\n書き換えたトリガー\n\n新手順。\n", encoding="utf-8")
        out2 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert len(out2["pending"]) == 1
        assert out2["pending"][0]["id"] != pid
        assert out2["reject_suppressed_total"] == 0

    def test_advisory_rejected_proposal_excluded_from_next_emit(self, advisory_project):
        """[Must]3 の核心: advisory レーンの reject も次回 emit で抑制される。"""
        out1 = ed.emit_decisions({}, project_dir=str(advisory_project), dry_run=True, slug="pj")
        pid = _advisory_entries(out1["pending"])[0]["id"]
        ed.ingest_decisions("pj", rejected={pid: "後で直す"}, dry_run=False, pending=out1["pending"])

        out2 = ed.emit_decisions({}, project_dir=str(advisory_project), dry_run=True, slug="pj")
        assert _advisory_entries(out2["pending"]) == []
        assert out2["reject_suppressed_total"] == 1

    def test_emit_returns_zero_suppression_meta_when_nothing_rejected(
        self, result_with_match
    ):
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out["reject_suppressed_total"] == 0
        assert out["reject_suppressed"] == []
        assert out["suppression_ledger_read_error"] is None

    def test_emit_fail_open_when_ledger_read_fails(
        self, result_with_match, monkeypatch
    ):
        """境界①の fail-open が emit まで伝播する: ledger が読めなくても emit は全件返す。"""

        def _boom(_slug):
            raise OSError("disk full")

        monkeypatch.setattr(sl, "load_ledger", _boom)
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out["count"] == 1
        assert "OSError" in (out["suppression_ledger_read_error"] or "")

    def test_advisory_collection_failure_does_not_disable_suppression_filter(
        self, result_with_match, skill_file, hist, monkeypatch
    ):
        """設計 §3.1-b「明示的に禁止する実装」: advisory 収集の except と filter_rejected を
        混ぜていないことの回帰防止。advisory detector が壊れても skill レーンの抑制は効く。"""

        def _boom(*_a, **_kw):
            raise RuntimeError("detector exploded")

        monkeypatch.setattr(ed, "_collect_advisory_proposals", _boom)
        out1 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out1["pending"][0]["id"]
        ed.ingest_decisions("testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist)

        out2 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out2["pending"] == []  # advisory detector が壊れていても skill 側は抑制される
        assert out2["reject_suppressed_total"] == 1


# ─── ingest 側: reject 記録の配線 ───────────────────────────────────────────


class TestIngestRecordsRejection:
    def test_skill_lane_reject_writes_to_ledger(self, result_with_match, skill_file, hist):
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out["pending"][0]["id"]
        summary = ed.ingest_decisions(
            "testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist
        )
        assert summary["rejected"] == [pid]
        assert summary["suppression_ledger_errors"] == []
        # ledger に実際に書かれたかを直接確認
        ledger = sl.load_ledger("testslug")
        assert len(ledger) == 1

    def test_advisory_lane_reject_writes_to_ledger(self, advisory_project):
        out = ed.emit_decisions({}, project_dir=str(advisory_project), dry_run=True, slug="pj")
        pid = _advisory_entries(out["pending"])[0]["id"]
        summary = ed.ingest_decisions(
            "pj", rejected={pid: "後で直す"}, dry_run=False, pending=out["pending"]
        )
        assert summary["rejected"] == [pid]
        assert summary["suppression_ledger_errors"] == []
        ledger = sl.load_ledger("pj")
        assert len(ledger) == 1

    def test_accept_does_not_write_to_ledger(self, result_with_match, skill_file, hist):
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out["pending"][0]["id"]
        skill_file.write_text("# my-skill\n\n改善: foo bar baz\n", encoding="utf-8")
        ed.ingest_decisions("testslug", accepted={pid}, dry_run=False, history_file=hist)
        assert sl.load_ledger("testslug") == {}

    def test_skip_does_not_write_to_ledger(self, result_with_match, hist):
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        ed.ingest_decisions("testslug", dry_run=False, history_file=hist)
        assert sl.load_ledger("testslug") == {}

    def test_dry_run_reject_writes_zero_bytes(self, result_with_match, skill_file, hist):
        """dry-run 純度: reject 分類はするが suppression ledger には一切書かない。"""
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out["pending"][0]["id"]
        summary = ed.ingest_decisions(
            "testslug", rejected={pid: "不一致"}, dry_run=True, history_file=hist
        )
        assert len(summary["rejected"]) == 1  # 分類はする
        assert sl.load_ledger("testslug") == {}  # でも書かない
        root = sl.ledger_path("testslug").parent
        assert not root.exists() or list(root.glob("*.jsonl")) == []

    def test_ledger_write_failure_does_not_block_reject_processing(
        self, result_with_match, skill_file, hist, monkeypatch
    ):
        """fail-open: ledger 書込が失敗しても record_evolve_diff_decision・キュー消化は続行する。"""

        def _boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(sl, "record_rejection", _boom)
        out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        pid = out["pending"][0]["id"]
        summary = ed.ingest_decisions(
            "testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist
        )
        assert summary["rejected"] == [pid]  # reject 処理自体は成功
        assert ed.read_queue("testslug") == []  # キュー消化も続行
        assert len(summary["suppression_ledger_errors"]) == 1
        assert summary["suppression_ledger_errors"][0]["id"] == pid
        assert "OSError" in summary["suppression_ledger_errors"][0]["error"]


# ─── E2E: 実ストア round-trip（#446 の受け入れ条件そのもの） ───────────────


class TestEndToEndRoundTrip:
    def test_full_cycle_reject_then_resurface_suppressed(
        self, result_with_match, skill_file, hist
    ):
        """emit → reject → 再emit で「reject された提案が再提示される」バグが再現しないことを
        実際の emit/ingest 呼び出し列で固定する（#446 の受け入れ条件）。"""
        out1 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out1["count"] == 1
        pid = out1["pending"][0]["id"]

        ed.ingest_decisions("testslug", rejected={pid: "ドメイン不一致"}, dry_run=False, history_file=hist)

        # queue が消化された後でも、同じ discover 結果を渡せば通常は再提示される、というのが
        # #446 のバグ。再 emit で pending が 0 件であることが直った証拠。
        out2 = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
        assert out2["count"] == 0
        assert ed.read_queue("testslug") == []
