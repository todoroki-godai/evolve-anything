"""skill_extractor の 4軸構造分解 (Workflow-to-Skill, arXiv 2606.06893) テスト。

TDD-first: 実装前にテストを書いている。
ワークフローを routing / workflow / semantics / attachments の4要素へ
決定論的に分解する `decompose_candidate` を検証する。LLM 非依存。

Issue #381
"""
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from skill_extractor.trajectory_sampler import TrajectoryRecord
from skill_extractor.decomposition import (
    decompose_candidate,
    ROUTING_KEYWORD_LIMIT,
)
from skill_extractor.skill_extractor import extract_skill_candidates


def _rec(
    skill_name="rl-anything:implement",
    user_prompt="実装して",
    outcome="success",
    session_id="s1",
    timestamp="t1",
    source_file="/home/u/.claude/projects/rl-anything/abc.jsonl",
) -> TrajectoryRecord:
    return TrajectoryRecord(
        skill_name=skill_name,
        user_prompt=user_prompt,
        outcome=outcome,
        session_id=session_id,
        timestamp=timestamp,
        extra={"source_file": source_file},
    )


# ── 4軸の存在 ─────────────────────────────────────────────


class TestFourAxesPresent:
    def test_returns_all_four_axes(self):
        records = [_rec(), _rec(user_prompt="コードを書いて")]
        d = decompose_candidate(records)
        assert set(d.keys()) == {"routing", "workflow", "semantics", "attachments"}

    def test_empty_records_still_returns_four_axes(self):
        """空入力でも4軸の骨格は壊さない（silence≠evaluated）。"""
        d = decompose_candidate([])
        assert set(d.keys()) == {"routing", "workflow", "semantics", "attachments"}
        assert d["workflow"]["invocations"] == 0
        assert d["attachments"]["projects"] == []
        assert d["routing"]["trigger_keywords"] == []


# ── routing: いつ/どんな文脈で発火するか ─────────────────────


class TestRouting:
    def test_trigger_keywords_extracted_from_prompts(self):
        records = [
            _rec(user_prompt="バッチ処理を実装して"),
            _rec(user_prompt="バッチ処理のテストを実装して"),
        ]
        d = decompose_candidate(records)
        kws = d["routing"]["trigger_keywords"]
        # 頻出語が拾われる（"バッチ" や "実装" 等）。ストップワードは除外。
        assert any("バッチ" in k or "実装" in k for k in kws)
        assert "して" not in kws  # 日本語ストップワード

    def test_trigger_keywords_capped(self):
        records = [
            _rec(user_prompt=f"word{i} alpha beta gamma delta epsilon zeta")
            for i in range(10)
        ]
        d = decompose_candidate(records)
        assert len(d["routing"]["trigger_keywords"]) <= ROUTING_KEYWORD_LIMIT

    def test_sample_triggers_collected(self):
        records = [_rec(user_prompt="A"), _rec(user_prompt="B"), _rec(user_prompt="C")]
        d = decompose_candidate(records)
        assert d["routing"]["sample_triggers"]
        assert all(isinstance(s, str) for s in d["routing"]["sample_triggers"])


# ── workflow: 実行プロファイル（手順は軌跡に残らないので近似）──────


class TestWorkflow:
    def test_invocations_counts_records(self):
        records = [_rec(), _rec(), _rec()]
        d = decompose_candidate(records)
        assert d["workflow"]["invocations"] == 3

    def test_outcome_distribution(self):
        records = [
            _rec(outcome="success"),
            _rec(outcome="success"),
            _rec(outcome="failure"),
            _rec(outcome="unknown"),
        ]
        d = decompose_candidate(records)
        dist = d["workflow"]["outcomes"]
        assert dist["success"] == 2
        assert dist["failure"] == 1
        assert dist["unknown"] == 1


# ── semantics: 何をするか ─────────────────────────────────


class TestSemantics:
    def test_base_name_strips_namespace(self):
        records = [_rec(skill_name="rl-anything:implement")]
        d = decompose_candidate(records)
        assert d["semantics"]["base_name"] == "implement"
        assert d["semantics"]["namespace"] == "rl-anything"

    def test_namespace_none_when_absent(self):
        records = [_rec(skill_name="audit")]
        d = decompose_candidate(records)
        assert d["semantics"]["base_name"] == "audit"
        assert d["semantics"]["namespace"] is None


# ── attachments: どの文脈/資源に anchor されているか ─────────────
# 単一PJ scope で採掘される wired discover では projects は弁別しないため、
# distinct session 数で「一過性バースト（単一セッション）か定着パターンか」を測る。


class TestAttachments:
    def test_single_session_is_bound(self):
        """全 invoke が同一セッション由来 = 一過性バースト → session_bound=True。"""
        records = [
            _rec(session_id="s1"),
            _rec(session_id="s1"),
            _rec(session_id="s1"),
        ]
        d = decompose_candidate(records)
        assert d["attachments"]["session_count"] == 1
        assert d["attachments"]["session_bound"] is True

    def test_multi_session_not_bound(self):
        """複数セッションにまたがる = 定着パターン → session_bound=False。"""
        records = [
            _rec(session_id="s1"),
            _rec(session_id="s2"),
            _rec(session_id="s3"),
        ]
        d = decompose_candidate(records)
        assert d["attachments"]["session_count"] == 3
        assert d["attachments"]["session_bound"] is False

    def test_projects_still_tracked_for_cross_project(self):
        """projects は cross-project 直接API用に残置（多PJ入力で弁別）。"""
        records = [
            _rec(source_file="/h/.claude/projects/rl-anything/a.jsonl", session_id="s1"),
            _rec(source_file="/h/.claude/projects/docs-platform/b.jsonl", session_id="s2"),
        ]
        d = decompose_candidate(records)
        assert set(d["attachments"]["projects"]) == {"rl-anything", "docs-platform"}

    def test_empty_session_ids_tolerated(self):
        """session_id 欠落（空文字）は distinct から除外し、0 件なら bound 扱い。"""
        rec = TrajectoryRecord(
            skill_name="audit", user_prompt="p", outcome="success",
            session_id="", timestamp="t", extra={},
        )
        d = decompose_candidate([rec])
        assert d["attachments"]["session_count"] == 0
        assert d["attachments"]["session_bound"] is True
        assert d["attachments"]["projects"] == []


# ── extract_skill_candidates への統合 ─────────────────────────


class TestCandidateIntegration:
    def test_candidate_includes_decomposition(self, tmp_path, monkeypatch):
        """extract_skill_candidates の各候補に decomposition が付く。"""
        records = [
            _rec(user_prompt="実装して", session_id="s1"),
            _rec(user_prompt="コードを書いて", session_id="s2"),
        ]
        monkeypatch.setattr(
            "skill_extractor.skill_extractor.sample_trajectories",
            lambda **kw: records,
        )
        candidates = extract_skill_candidates(projects_root=tmp_path)
        assert candidates
        for c in candidates:
            assert "decomposition" in c
            assert set(c["decomposition"].keys()) == {
                "routing", "workflow", "semantics", "attachments",
            }
