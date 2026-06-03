#!/usr/bin/env python3
"""SkillPyramid — 階層的統合（hierarchical consolidation）のユニットテスト（#303）。

低レベルスキル群を上位スキルへ束ねる提案を検出する `detect_hierarchy_candidates`
と、その issue 変換・run_reorganize への配線をテストする。決定論ロジックのため LLM 非依存。
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "reorganize" / "scripts"))

import reorganize  # noqa: E402
import issue_schema  # noqa: E402


def _make_skill(skills_dir: Path, name: str, lines: int) -> Path:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(f"# {name}\n" + "\n".join(f"line {i}" for i in range(lines)))
    return md


class TestDetectHierarchyCandidates:
    """detect_hierarchy_candidates のユニットテスト。"""

    def test_consolidates_cluster_of_low_level_skills(self):
        """低レベルスキル3つ以上のクラスタが階層統合候補として検出される。"""
        clusters = [
            {
                "cluster_id": 1,
                "skills": ["deploy-ecs", "deploy-lambda", "deploy-s3"],
                "centroid_keywords": ["deploy", "aws", "infra", "stack", "region"],
            },
        ]
        line_counts = {"deploy-ecs": 40, "deploy-lambda": 35, "deploy-s3": 50}

        candidates = reorganize.detect_hierarchy_candidates(clusters, line_counts)

        assert len(candidates) == 1
        c = candidates[0]
        assert c["reason"] == "hierarchical_consolidation"
        assert c["member_count"] == 3
        assert set(c["member_skills"]) == {"deploy-ecs", "deploy-lambda", "deploy-s3"}
        assert c["centroid_keywords"] == ["deploy", "aws", "infra", "stack", "region"]
        # parent suggestion はキーワード由来の非空文字列
        assert isinstance(c["parent_skill_suggestion"], str)
        assert c["parent_skill_suggestion"]

    def test_small_cluster_not_consolidated(self):
        """メンバーが MIN 未満（2つ）のクラスタは統合候補にしない。"""
        clusters = [
            {
                "cluster_id": 1,
                "skills": ["a", "b"],
                "centroid_keywords": ["x"],
            },
        ]
        line_counts = {"a": 30, "b": 30}
        candidates = reorganize.detect_hierarchy_candidates(clusters, line_counts)
        assert candidates == []

    def test_large_skills_not_consolidated(self):
        """メンバーが大型スキル中心のクラスタは統合候補にしない（上位は別問題）。"""
        clusters = [
            {
                "cluster_id": 1,
                "skills": ["big-a", "big-b", "big-c"],
                "centroid_keywords": ["x", "y"],
            },
        ]
        # すべて HIERARCHY_LINE_CEILING を大きく超える
        line_counts = {"big-a": 400, "big-b": 380, "big-c": 420}
        candidates = reorganize.detect_hierarchy_candidates(clusters, line_counts)
        assert candidates == []

    def test_singleton_clusters_ignored(self):
        """単独スキルのクラスタは統合候補にしない。"""
        clusters = [
            {"cluster_id": 1, "skills": ["solo"], "centroid_keywords": ["k"]},
        ]
        line_counts = {"solo": 20}
        candidates = reorganize.detect_hierarchy_candidates(clusters, line_counts)
        assert candidates == []

    def test_empty_input(self):
        """クラスタなしなら空リスト。"""
        assert reorganize.detect_hierarchy_candidates([], {}) == []


class TestHierarchyIssue:
    """make_hierarchy_candidate_issue のユニットテスト。"""

    def test_issue_shape(self):
        candidate = {
            "reason": "hierarchical_consolidation",
            "parent_skill_suggestion": "deploy-suite",
            "member_skills": ["deploy-ecs", "deploy-lambda", "deploy-s3"],
            "member_count": 3,
            "centroid_keywords": ["deploy", "aws"],
        }
        issue = issue_schema.make_hierarchy_candidate_issue(candidate)
        assert issue["type"] == issue_schema.HIERARCHY_CANDIDATE
        assert issue["source"] == "reorganize"
        assert issue["detail"]["parent_skill_suggestion"] == "deploy-suite"
        assert issue["detail"]["member_skills"] == [
            "deploy-ecs", "deploy-lambda", "deploy-s3"
        ]
        assert issue["detail"]["member_count"] == 3


class TestRunReorganizeWiring:
    """run_reorganize の出力に hierarchy_candidates が配線されていることを確認。"""

    def test_hierarchy_candidates_in_output(self, tmp_path):
        pytest.importorskip("scipy")
        pytest.importorskip("sklearn")

        project_dir = tmp_path / "project"
        skills_dir = project_dir / ".claude" / "skills"

        # 同一ドメインの低レベルスキル群（クラスタ化されるはず）
        contents = {
            "deploy-ecs": "Deploy ECS service to AWS using CloudFormation stack.\n",
            "deploy-lambda": "Deploy Lambda function to AWS with CloudFormation stack.\n",
            "deploy-s3": "Deploy S3 bucket to AWS via CloudFormation stack template.\n",
            "unrelated-music": "Music theory chords and scales analysis melody.\n",
            "unrelated-cooking": "Cooking pasta recipe with tomato sauce and basil.\n",
        }
        skill_paths = []
        for name, content in contents.items():
            md = _make_skill(skills_dir, name, 10)
            md.write_text(f"# {name}\n{content}")
            skill_paths.append(md)

        fake_artifacts = {"skills": skill_paths, "rules": [], "memory": [], "claude_md": []}
        with mock.patch("reorganize.find_artifacts", return_value=fake_artifacts):
            result = reorganize.run_reorganize(str(project_dir))

        assert result["skipped"] is False
        assert "hierarchy_candidates" in result
        assert "total_hierarchy_candidates" in result
        assert isinstance(result["hierarchy_candidates"], list)
        # issues に階層統合 issue が混ざる（型で判定）
        types = {i["type"] for i in result["issues"]}
        # 少なくとも split か hierarchy のどちらかの issue 形式が存在しうる
        assert issue_schema.HIERARCHY_CANDIDATE in types or result["total_hierarchy_candidates"] == 0
