#!/usr/bin/env python3
"""chaos.py のテスト"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

_chaos_path = _rl_dir / "fitness" / "chaos.py"
_spec = importlib.util.spec_from_file_location("chaos", _chaos_path)
chaos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chaos)


def _make_project(tmp_path):
    """テスト用プロジェクトを作成する（複数ルール・スキル付き）。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text("# Project\n\n## Skills\n\n- skill-a: Skill A\n- skill-b: Skill B\n")

    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "rule-a.md").write_text("# Rule A\nDo A.\n")
    (rules_dir / "rule-b.md").write_text("# Rule B\nDo B.\n")
    (rules_dir / "rule-c.md").write_text("# Rule C\nDo C.\n")

    skill_a_dir = claude_dir / "skills" / "skill-a"
    skill_a_dir.mkdir(parents=True, exist_ok=True)
    (skill_a_dir / "SKILL.md").write_text("# Skill A\n\n## Usage\n\nUse A.\n")

    skill_b_dir = claude_dir / "skills" / "skill-b"
    skill_b_dir.mkdir(parents=True, exist_ok=True)
    (skill_b_dir / "SKILL.md").write_text("# Skill B\n\n## Usage\n\nUse B.\n")

    mem_dir = claude_dir / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("# Memory\n\nSome notes.\n")

    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": ["echo test"]}]}}
    (claude_dir / "settings.json").write_text(json.dumps(settings))

    return tmp_path


class TestVirtualAblation:
    def test_real_files_not_modified(self, tmp_path):
        """仮想アブレーションで実ファイルが変更されないことを確認。"""
        project = _make_project(tmp_path)
        rule_a = project / ".claude" / "rules" / "rule-a.md"
        original_content = rule_a.read_text()

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score.return_value = {"overall": 0.8}

        with mock.patch.object(chaos, "_load_sibling", return_value=mock_coherence):
            chaos.compute_chaos_score(project)

        # 実ファイルが変更されていないことを確認
        assert rule_a.read_text() == original_content


class TestImportanceRanking:
    def test_sorted_by_delta_descending(self, tmp_path):
        """importance_ranking が delta_score の降順でソートされている。"""
        project = _make_project(tmp_path)
        call_count = [0]

        def mock_coherence_score(proj_dir):
            call_count[0] += 1
            if call_count[0] == 1:
                # ベースライン
                return {"overall": 0.8}
            # 各アブレーション — 異なる delta を返す
            scores = [0.7, 0.6, 0.75, 0.5, 0.65]
            idx = min(call_count[0] - 2, len(scores) - 1)
            return {"overall": scores[idx]}

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score.side_effect = mock_coherence_score

        with mock.patch.object(chaos, "_load_sibling", return_value=mock_coherence):
            result = chaos.compute_chaos_score(project)

        ranking = result["importance_ranking"]
        for i in range(len(ranking) - 1):
            assert ranking[i]["delta_score"] >= ranking[i + 1]["delta_score"]


class TestRobustnessScore:
    def test_formula(self, tmp_path):
        """robustness_score = max(0.0, 1.0 - (max_delta / max(baseline, 0.01)))"""
        project = _make_project(tmp_path)
        baseline = 0.8
        # 全てのアブレーションで baseline - 0.1 を返す（max_delta = 0.1）
        call_count = [0]

        def mock_coherence_score(proj_dir):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"overall": baseline}
            return {"overall": baseline - 0.1}

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score.side_effect = mock_coherence_score

        with mock.patch.object(chaos, "_load_sibling", return_value=mock_coherence):
            result = chaos.compute_chaos_score(project)

        expected = max(0.0, 1.0 - (0.1 / max(baseline, 0.01)))
        assert abs(result["robustness_score"] - round(expected, 4)) < 0.01

    def test_baseline_zero(self, tmp_path):
        """baseline = 0 のエッジケース。0.01 がフォールバックに使われる。"""
        project = _make_project(tmp_path)
        call_count = [0]

        def mock_coherence_score(proj_dir):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"overall": 0.0}
            return {"overall": 0.0}

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score.side_effect = mock_coherence_score

        with mock.patch.object(chaos, "_load_sibling", return_value=mock_coherence):
            result = chaos.compute_chaos_score(project)

        # baseline=0, max_delta=0 → robustness = max(0.0, 1.0 - (0.0 / 0.01)) = 1.0
        assert result["robustness_score"] == 1.0


class TestSPOFDetection:
    def test_spof_detected(self, tmp_path):
        """delta >= 0.15 の要素が SPOF として検出される。"""
        project = _make_project(tmp_path)
        baseline = 0.8
        call_count = [0]

        def mock_coherence_score(proj_dir):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"overall": baseline}
            if call_count[0] == 2:
                # 最初の要素で大きな delta (0.2)
                return {"overall": baseline - 0.2}
            # 他は小さな delta
            return {"overall": baseline - 0.01}

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score.side_effect = mock_coherence_score

        with mock.patch.object(chaos, "_load_sibling", return_value=mock_coherence):
            result = chaos.compute_chaos_score(project)

        assert len(result["single_point_of_failure"]) >= 1
        for spof in result["single_point_of_failure"]:
            assert spof["delta_score"] >= 0.15


class TestCriticalityClassification:
    def test_critical(self):
        assert chaos._classify_criticality(0.12) == "critical"

    def test_important(self):
        assert chaos._classify_criticality(0.05) == "important"

    def test_low(self):
        assert chaos._classify_criticality(0.01) == "low"

    def test_boundary_critical(self):
        assert chaos._classify_criticality(0.10) == "critical"

    def test_boundary_important(self):
        assert chaos._classify_criticality(0.02) == "important"


class TestScope:
    def test_only_rules_and_skills(self, tmp_path):
        """アブレーション対象は Rules と Skills のみ（CLAUDE.md/Memory は含まない）。"""
        project = _make_project(tmp_path)
        targets = chaos._collect_ablation_targets(project)

        layers = {t["layer"] for t in targets}
        assert layers <= {"rules", "skills"}
        assert "claude_md" not in layers
        assert "memory" not in layers

    def test_collects_all_rules_and_skills(self, tmp_path):
        """全ルールと全スキルが収集される。"""
        project = _make_project(tmp_path)
        targets = chaos._collect_ablation_targets(project)

        names = {t["name"] for t in targets}
        assert "rule-a" in names
        assert "rule-b" in names
        assert "rule-c" in names
        assert "skill-a" in names
        assert "skill-b" in names


class TestShadowExcludesWorktrees:
    """#523-1: shadow コピー対象から .claude/worktrees/ を除外する回帰テスト。

    chaos の _prepare_shadow_project は `.claude/` を丸ごと copytree していたため、
    `.claude/worktrees/` 配下の stale な agent worktree（壊れた symlink / ファイル不在）が
    あると copytree が生 Python タプルの長大 stderr を吐いてフル dry-run を汚していた。
    worktrees はアブレーション対象（rules/skills）でないので shadow に含める必要がない。
    """

    def test_shadow_excludes_worktrees_dir(self, tmp_path):
        project = _make_project(tmp_path)
        # stale worktree を模す（本来コピー不要なディレクトリ）
        wt = project / ".claude" / "worktrees" / "agent-stale" / ".claude" / "rules"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "leak.md").write_text("# Should not be copied\n")

        with tempfile.TemporaryDirectory() as tmp_root:
            shadow = chaos._prepare_shadow_project(project, Path(tmp_root))
            # worktrees ディレクトリは shadow に含まれない
            assert not (shadow / ".claude" / "worktrees").exists(), (
                ".claude/worktrees/ が shadow コピーに含まれている（stale worktree 汚染の原因）"
            )
            # 通常の rules/skills は引き続きコピーされる
            assert (shadow / ".claude" / "rules" / "rule-a.md").exists()
            assert (shadow / ".claude" / "skills" / "skill-a" / "SKILL.md").exists()

    def test_shadow_survives_broken_symlink_in_worktrees(self, tmp_path):
        """worktrees 配下に壊れた symlink があっても _prepare_shadow_project は例外を出さない。"""
        project = _make_project(tmp_path)
        wt = project / ".claude" / "worktrees" / "agent-stale"
        wt.mkdir(parents=True, exist_ok=True)
        broken = wt / "dangling"
        broken.symlink_to(project / "nonexistent-target")

        with tempfile.TemporaryDirectory() as tmp_root:
            # 例外を出さずに完走できること（worktrees を ignore するため）
            shadow = chaos._prepare_shadow_project(project, Path(tmp_root))
            assert shadow.exists()


class TestLoadSiblingPackage:
    """_load_sibling が package 化された coherence をロードできる回帰テスト（#277）。

    coherence は #129〜#143 で coherence/__init__.py パッケージへ分割されたが、
    chaos の _load_sibling は `{name}.py` 固定パスのままだったため、coherence
    ベースの chaos ロバストネス計測が FileNotFoundError で壊れていた。
    environment.py の package 対応版に追従したことを保証する。
    """

    def test_load_sibling_loads_coherence_package(self):
        mod = chaos._load_sibling("coherence")
        assert hasattr(mod, "compute_coherence_score"), (
            "coherence パッケージ (coherence/__init__.py) がロードできていない"
        )
