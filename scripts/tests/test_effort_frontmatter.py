"""effort frontmatter 検出+推定のテスト。

detect_missing_effort_frontmatter() と infer_effort_level() を検証する。
"""
import sys
from pathlib import Path

import pytest

# プロジェクトルートを sys.path に追加
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib.effort_detector import detect_missing_effort_frontmatter, infer_effort_level
from lib.issue_schema import make_missing_effort_issue, MISSING_EFFORT_CANDIDATE


@pytest.fixture
def skills_dir(tmp_path):
    """テスト用スキルディレクトリを作成する。"""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    return skills


def _make_skill(skills_dir, name, frontmatter_lines, body_lines=10):
    """ヘルパー: SKILL.md を作成する。"""
    skill_dir = skills_dir / name
    skill_dir.mkdir()
    fm = "\n".join(frontmatter_lines)
    body = "\n".join([f"Line {i}" for i in range(body_lines)])
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\n\n{body}\n")
    return skill_dir / "SKILL.md"


class TestInferEffortLevel:
    """effort レベル推定ロジックのテスト。"""

    def test_low_disable_model_invocation(self, skills_dir):
        """disable-model-invocation: true のスキルは low。"""
        path = _make_skill(
            skills_dir, "simple",
            ["name: simple", "disable-model-invocation: true"],
            body_lines=50,
        )
        result = infer_effort_level(path)
        assert result["level"] == "low"

    def test_low_short_skill(self, skills_dir):
        """短いスキル（< LOW_LINE_THRESHOLD）は low。"""
        path = _make_skill(
            skills_dir, "tiny",
            ["name: tiny", "description: tiny skill"],
            body_lines=30,
        )
        result = infer_effort_level(path)
        assert result["level"] == "low"

    def test_high_long_skill(self, skills_dir):
        """長いスキル（>= HIGH_LINE_THRESHOLD）は high。"""
        path = _make_skill(
            skills_dir, "big",
            ["name: big", "description: big skill"],
            body_lines=350,
        )
        result = infer_effort_level(path)
        assert result["level"] == "high"

    def test_high_agent_in_allowed_tools(self, skills_dir):
        """allowed-tools に Agent を含むスキルは high。"""
        path = _make_skill(
            skills_dir, "orchestrator",
            ["name: orchestrator", "allowed-tools: Read, Write, Agent"],
            body_lines=80,
        )
        result = infer_effort_level(path)
        assert result["level"] == "high"

    def test_high_pipeline_keywords(self, skills_dir):
        """パイプライン系キーワードを含むスキルは high。"""
        skill_dir = skills_dir / "pipeline"
        skill_dir.mkdir()
        content = "---\nname: pipeline\ndescription: run pipeline\n---\n\n"
        content += "## Steps\n\nOrchestrate multiple phases\n" * 30
        (skill_dir / "SKILL.md").write_text(content)

        result = infer_effort_level(skill_dir / "SKILL.md")
        assert result["level"] == "high"

    def test_medium_default(self, skills_dir):
        """low でも high でもないスキルは medium。"""
        path = _make_skill(
            skills_dir, "moderate",
            ["name: moderate", "description: moderate skill"],
            body_lines=150,
        )
        result = infer_effort_level(path)
        assert result["level"] == "medium"

    def test_result_has_confidence(self, skills_dir):
        """推定結果に confidence が含まれる。"""
        path = _make_skill(
            skills_dir, "any",
            ["name: any", "description: any skill"],
            body_lines=50,
        )
        result = infer_effort_level(path)
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0


class TestDetectMissingEffortFrontmatter:
    """effort 未設定スキルの検出テスト。"""

    def test_detects_missing_effort(self, skills_dir):
        """effort がないスキルを検出する。"""
        _make_skill(skills_dir, "no-effort", ["name: no-effort", "description: test"])
        result = detect_missing_effort_frontmatter(skills_dir.parent.parent)
        assert result["applicable"] is True
        assert len(result["evidence"]) == 1
        assert result["evidence"][0]["skill_name"] == "no-effort"

    def test_skips_skills_with_effort(self, skills_dir):
        """effort があるスキルはスキップする。"""
        _make_skill(skills_dir, "has-effort", ["name: has-effort", "effort: medium"])
        result = detect_missing_effort_frontmatter(skills_dir.parent.parent)
        assert result["applicable"] is False
        assert len(result["evidence"]) == 0

    def test_mixed_skills(self, skills_dir):
        """effort あり/なし混在時、なしのみ検出する。"""
        _make_skill(skills_dir, "with", ["name: with", "effort: high"])
        _make_skill(skills_dir, "without", ["name: without", "description: test"])
        result = detect_missing_effort_frontmatter(skills_dir.parent.parent)
        assert result["applicable"] is True
        assert len(result["evidence"]) == 1
        assert result["evidence"][0]["skill_name"] == "without"

    def test_evidence_includes_proposed_effort(self, skills_dir):
        """evidence に proposed_effort が含まれる。"""
        _make_skill(skills_dir, "test", ["name: test", "description: test"])
        result = detect_missing_effort_frontmatter(skills_dir.parent.parent)
        assert "proposed_effort" in result["evidence"][0]

    def test_no_skills_dir(self, tmp_path):
        """スキルディレクトリがない場合は applicable=False。"""
        result = detect_missing_effort_frontmatter(tmp_path)
        assert result["applicable"] is False

    def test_skips_archived_skills(self, skills_dir):
        """`_archived/` / `.archive/` / `disabled/` 配下スキルは検出対象外（#337）。

        アーカイブ済みスキルに effort frontmatter 付与を提案しても無意味。
        sys-bots で missing_effort 5件が全て _archived 配下という誤検知が起きていた。
        """
        _make_skill(skills_dir, "live", ["name: live", "description: test"])
        for sub, sname in (("_archived", "old1"), (".archive", "old2"), ("disabled", "old3")):
            sub_dir = skills_dir / sub
            sub_dir.mkdir(exist_ok=True)
            _make_skill(sub_dir, sname, [f"name: {sname}", "description: x"])

        result = detect_missing_effort_frontmatter(skills_dir.parent.parent)
        names = {e["skill_name"] for e in result["evidence"]}
        assert names == {"live"}  # アーカイブ配下は1件も入らない


class TestFixMissingEffort:
    """remediation の fix_missing_effort ハンドラテスト。"""

    def test_fix_adds_effort_frontmatter(self, skills_dir):
        """effort を frontmatter に追加する。"""
        # remediation の sys.path 設定
        _remediation_root = Path(__file__).resolve().parent.parent.parent / "skills" / "evolve" / "scripts"
        sys.path.insert(0, str(_remediation_root))
        from remediation import fix_missing_effort

        path = _make_skill(skills_dir, "target", ["name: target", "description: test"])
        issue = {
            "type": "missing_effort",
            "file": str(path),
            "detail": {
                "skill_name": "target",
                "skill_path": str(path),
                "proposed_effort": "medium",
                "confidence": 0.75,
                "reason": "default",
            },
            "source": "effort_detector",
        }
        results = fix_missing_effort([issue])
        assert len(results) == 1
        assert results[0]["fixed"] is True

        # frontmatter に effort が追加されたか確認
        from lib.frontmatter import parse_frontmatter
        fm = parse_frontmatter(path)
        assert fm["effort"] == "medium"

    def test_fix_file_not_found(self):
        """存在しないファイルは fixed=False。"""
        _remediation_root = Path(__file__).resolve().parent.parent.parent / "skills" / "evolve" / "scripts"
        sys.path.insert(0, str(_remediation_root))
        from remediation import fix_missing_effort

        issue = {
            "type": "missing_effort",
            "file": "/nonexistent/SKILL.md",
            "detail": {
                "skill_name": "ghost",
                "skill_path": "/nonexistent/SKILL.md",
                "proposed_effort": "low",
                "confidence": 0.75,
                "reason": "test",
            },
            "source": "effort_detector",
        }
        results = fix_missing_effort([issue])
        assert results[0]["fixed"] is False

    def test_fix_resolves_path_from_file_when_detail_lacks_skill_path(self, skills_dir):
        """#166: audit/issues.py の producer は detail に skill_path を入れず top-level
        file に path を置く。fixer はその shape でも path を解決して成功しなければならない
        （旧実装は detail['skill_path'] だけ読み Path("") → 'file not found: .' で全滅）。"""
        _remediation_root = Path(__file__).resolve().parent.parent.parent / "skills" / "evolve" / "scripts"
        sys.path.insert(0, str(_remediation_root))
        from remediation import fix_missing_effort

        path = _make_skill(skills_dir, "target", ["name: target", "description: test"])
        # audit/issues.py:322-333 が実際に出す shape（detail に skill_path 無し）
        issue = {
            "type": "missing_effort",
            "file": str(path),
            "detail": {
                "skill_name": "target",
                "proposed_effort": "medium",
                "confidence": 0.75,
                "reason": "default",
            },
            "source": "detect_missing_effort_frontmatter",
        }
        results = fix_missing_effort([issue])
        assert len(results) == 1
        assert results[0]["fixed"] is True, results[0].get("error")

        from lib.frontmatter import parse_frontmatter
        assert parse_frontmatter(path)["effort"] == "medium"


class TestCollectIssuesMissingEffortSchema:
    """#166: audit の producer(collect_issues) が missing_effort issue の detail に
    skill_path を含む（正準スキーマ issue_schema と揃える）ことを検証する。"""

    def test_collect_issues_missing_effort_includes_skill_path(self, skills_dir):
        from lib.audit.issues import collect_issues

        path = _make_skill(skills_dir, "needseffort", ["name: needseffort", "description: test"])
        project_dir = skills_dir.parent.parent
        issues = collect_issues(project_dir)
        me = [i for i in issues if i["type"] == "missing_effort"]
        assert me, "missing_effort issue が collect_issues から出ていない"
        target = [i for i in me if i["file"] == str(path)]
        assert target, "作成したスキルの missing_effort issue が見つからない"
        assert target[0]["detail"].get("skill_path") == str(path)


class TestMakeEffortIssue:
    """issue_schema の factory 関数テスト。"""

    def test_make_missing_effort_issue(self):
        issue = make_missing_effort_issue(
            skill_name="test-skill",
            skill_path="/path/to/SKILL.md",
            proposed_effort="medium",
            confidence=0.85,
        )
        assert issue["type"] == MISSING_EFFORT_CANDIDATE
        assert issue["detail"]["proposed_effort"] == "medium"
        assert issue["detail"]["confidence"] == 0.85
        assert issue["detail"]["skill_name"] == "test-skill"


class TestMissingEffortTypeConsistency:
    """type 不一致バグの回帰防止。

    検出側 (audit/issues.py) は LIVE type "missing_effort" を生成する。
    定数・fix/verify ハンドラ・dispatch がこの LIVE type と一致していないと
    「追加する」を選んでも修正が no-op になる（過去に発生したバグ）。
    """

    def test_constant_matches_live_detection_type(self):
        """定数は検出側が生成する LIVE type と一致する。"""
        assert MISSING_EFFORT_CANDIDATE == "missing_effort"

    def test_fix_and_verify_dispatch_keyed_on_live_type(self):
        _remediation_root = (
            Path(__file__).resolve().parent.parent.parent
            / "skills" / "evolve" / "scripts"
        )
        sys.path.insert(0, str(_remediation_root))
        from remediation import FIX_DISPATCH, VERIFY_DISPATCH

        assert "missing_effort" in FIX_DISPATCH
        assert "missing_effort" in VERIFY_DISPATCH
