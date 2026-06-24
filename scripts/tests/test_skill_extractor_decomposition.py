"""skill_extractor の 4軸構造分解 (Workflow-to-Skill, arXiv 2606.06893) テスト。

TDD-first: 実装前にテストを書いている。
ワークフローを routing / workflow / semantics / attachments の4要素へ
決定論的に分解する `decompose_candidate` を検証する。LLM 非依存。

Issue #381
"""
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from skill_extractor.trajectory_sampler import TrajectoryRecord
from skill_extractor.decomposition import (
    decompose_candidate,
    corpus_frequent_tokens,
    ROUTING_KEYWORD_LIMIT,
    SAMPLE_TRIGGER_LIMIT,
)
from skill_extractor.skill_extractor import extract_skill_candidates


def _rec(
    skill_name="evolve-anything:implement",
    user_prompt="実装して",
    outcome="success",
    session_id="s1",
    timestamp="t1",
    source_file="/home/u/.claude/projects/evolve-anything/abc.jsonl",
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
    def test_returns_all_five_axes(self):
        records = [_rec(), _rec(user_prompt="コードを書いて")]
        d = decompose_candidate(records)
        assert set(d.keys()) == {
            "routing", "workflow", "semantics", "attachments", "failure_analysis",
        }

    def test_empty_records_still_returns_five_axes(self):
        """空入力でも5軸の骨格は壊さない（silence≠evaluated）。"""
        d = decompose_candidate([])
        assert set(d.keys()) == {
            "routing", "workflow", "semantics", "attachments", "failure_analysis",
        }
        assert d["workflow"]["invocations"] == 0
        assert d["attachments"]["projects"] == []
        assert d["routing"]["trigger_keywords"] == []
        assert d["failure_analysis"]["failure_count"] == 0
        assert d["failure_analysis"]["failure_rate"] == 0.0
        assert d["failure_analysis"]["is_failure_derived"] is False


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


# ── routing: stopword 拡充（#387 — 実 PJ で if/not/md ノイズ露見）──────
# 合成 fixture では見えず実コーパスで露見した（learning_synthetic_fixture_false_confidence）。
# 普遍語（英語機能語・拡張子）は static stopword で、環境固有の遍在語は DF で落とす。


class TestStopwordExpansion:
    def test_english_function_words_excluded(self):
        """英語機能語（if/not/is/the/then/it）は trigger に残さない。"""
        records = [
            _rec(user_prompt="if this is not the review then run it"),
            _rec(user_prompt="review the diff if not broken so we can ship it"),
        ]
        d = decompose_candidate(records)
        kws = d["routing"]["trigger_keywords"]
        for noise in ("if", "not", "is", "the", "then", "it", "so", "we"):
            assert noise not in kws, f"機能語 {noise!r} が trigger に残った: {kws}"
        # 発火文脈を表す content word は残る
        assert "review" in kws

    def test_file_extensions_excluded(self):
        """ファイル名由来の拡張子 token（md/py/json）は trigger に残さない。"""
        records = [
            _rec(user_prompt="update the spec.md and notes.md files"),
            _rec(user_prompt="edit config.json and main.py for the spec"),
        ]
        d = decompose_candidate(records)
        kws = d["routing"]["trigger_keywords"]
        for ext in ("md", "py", "json"):
            assert ext not in kws, f"拡張子 {ext!r} が trigger に残った: {kws}"
        assert "spec" in kws


# ── corpus document-frequency（環境固有の遍在語を弁別語から除外）──────
# claude/gstack のようなツール名はどのスキルのプロンプトにも出るため弁別しない。
# ハードコード（allowlist）はモグラ叩きなので、corpus 全体の DF で決定論に落とす。


class TestCorpusFrequentTokens:
    def _corpus(self):
        # "claude" は全5スキルに出る遍在語。topic 語は各1スキルのみ。
        return {
            "review": ["claude review the diff", "claude check the code"],
            "spec": ["claude update spec", "claude write the spec"],
            "implement": ["claude implement feature", "claude build it"],
            "audit": ["claude run audit", "claude audit env"],
            "cleanup": ["claude cleanup branches", "claude prune worktrees"],
        }

    def test_ubiquitous_token_flagged(self):
        freq = corpus_frequent_tokens(self._corpus(), min_skills=5, df_ratio=0.8)
        assert "claude" in freq

    def test_minority_token_not_flagged(self):
        freq = corpus_frequent_tokens(self._corpus(), min_skills=5, df_ratio=0.8)
        # 各スキル固有の content 語は弁別するので残す
        for kept in ("review", "spec", "audit", "cleanup"):
            assert kept not in freq

    def test_small_corpus_returns_empty(self):
        """min_skills 未満では DF 減衰しない（少数コーパスでの過剰除外を防ぐ）。"""
        small = {"a": ["claude x"], "b": ["claude y"]}
        assert corpus_frequent_tokens(small, min_skills=5, df_ratio=0.8) == set()

    def test_static_stopwords_not_counted(self):
        """static stopword（the/is 等）は DF 計算前に落ちるので freq に出ない。"""
        freq = corpus_frequent_tokens(self._corpus(), min_skills=5, df_ratio=0.8)
        assert "the" not in freq
        assert "is" not in freq


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
        records = [_rec(skill_name="evolve-anything:implement")]
        d = decompose_candidate(records)
        assert d["semantics"]["base_name"] == "implement"
        assert d["semantics"]["namespace"] == "evolve-anything"

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
            _rec(source_file="/h/.claude/projects/evolve-anything/a.jsonl", session_id="s1"),
            _rec(source_file="/h/.claude/projects/docs-platform/b.jsonl", session_id="s2"),
        ]
        d = decompose_candidate(records)
        assert set(d["attachments"]["projects"]) == {"evolve-anything", "docs-platform"}

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


# ── failure_analysis: 失敗の罠（どの文脈で失敗したか）#27 ──────────


class TestFailureAnalysis:
    def test_failures_counted_and_rated(self):
        records = [
            _rec(outcome="failure", user_prompt="認証を直して"),
            _rec(outcome="failure", user_prompt="ビルドが壊れた"),
            _rec(outcome="success", user_prompt="実装して"),
            _rec(outcome="unknown", user_prompt="調べて"),
        ]
        fa = decompose_candidate(records)["failure_analysis"]
        assert fa["failure_count"] == 2
        assert fa["failure_rate"] == pytest.approx(0.5)
        assert fa["is_failure_derived"] is True
        assert set(fa["sample_failure_triggers"]) == {"認証を直して", "ビルドが壊れた"}

    def test_sample_triggers_capped_and_distinct(self):
        records = [
            _rec(outcome="failure", user_prompt=f"fail prompt {i}") for i in range(5)
        ]
        # 同一プロンプトの重複は distinct で1つに畳む
        records.append(_rec(outcome="failure", user_prompt="fail prompt 0"))
        # 空プロンプトは除外
        records.append(_rec(outcome="failure", user_prompt=""))
        fa = decompose_candidate(records)["failure_analysis"]
        assert len(fa["sample_failure_triggers"]) == SAMPLE_TRIGGER_LIMIT
        assert "" not in fa["sample_failure_triggers"]
        assert len(set(fa["sample_failure_triggers"])) == len(
            fa["sample_failure_triggers"]
        )

    def test_all_success_no_failure(self):
        records = [_rec(outcome="success") for _ in range(3)]
        fa = decompose_candidate(records)["failure_analysis"]
        assert fa["failure_count"] == 0
        assert fa["failure_rate"] == 0.0
        assert fa["sample_failure_triggers"] == []
        assert fa["is_failure_derived"] is False


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
                "routing", "workflow", "semantics", "attachments", "failure_analysis",
            }

    def test_corpus_frequent_token_removed_across_candidates(
        self, tmp_path, monkeypatch
    ):
        """全候補に共通して出る遍在語は本流経路でも trigger から外れ、
        各スキル固有語は残る（#387 受け入れ条件の合成版）。"""
        skills = ["s-a", "s-b", "s-c", "s-d", "s-e"]
        topics = {
            "s-a": "alpha", "s-b": "beta", "s-c": "gamma",
            "s-d": "delta", "s-e": "epsilon",
        }
        records = []
        for sk in skills:
            t = topics[sk]
            records.append(
                _rec(skill_name=sk, user_prompt=f"claude {t} task", session_id=sk + "1")
            )
            records.append(
                _rec(skill_name=sk, user_prompt=f"claude {t} again", session_id=sk + "2")
            )
        monkeypatch.setattr(
            "skill_extractor.skill_extractor.sample_trajectories",
            lambda **kw: records,
        )
        candidates = extract_skill_candidates(projects_root=tmp_path)
        by_name = {c["skill_name"]: c for c in candidates}
        for sk in skills:
            kws = by_name[sk]["decomposition"]["routing"]["trigger_keywords"]
            assert "claude" not in kws, f"{sk}: 遍在語 claude が残った: {kws}"
            assert topics[sk] in kws, f"{sk}: 固有語 {topics[sk]} が消えた: {kws}"
