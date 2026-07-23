"""rules 重複候補の triage ガイダンス付与テスト（#226）。

prune の duplicate_candidates は skills 側には merge サブフロー（merge_duplicates）
という消費者があるが、rules ペアには何もない。lexical 類似度だけで実重複と
早合点されないよう、rules 同士のペアには triage_note を付与する。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from prune.detection import _annotate_duplicate_candidate  # noqa: E402


def test_rules_pair_gets_triage_note():
    """rules 同士のペアには kind='rules' + triage_note が付く。"""
    item = {
        "path_a": "/repo/.claude/rules/code-quality.md",
        "path_b": "/repo/.claude/rules/testing.md",
        "similarity": 0.92,
    }

    result = _annotate_duplicate_candidate(item)

    assert result["kind"] == "rules"
    assert "triage_note" in result
    assert "両ファイルを読み" in result["triage_note"]
    # 元のフィールドは保持される
    assert result["path_a"] == item["path_a"]
    assert result["path_b"] == item["path_b"]
    assert result["similarity"] == 0.92


def test_skills_pair_has_no_triage_note():
    """skills 同士のペアには triage_note を付けない（既存 merge サブフローが消費者）。"""
    item = {
        "path_a": "/repo/.claude/skills/alpha/SKILL.md",
        "path_b": "/repo/.claude/skills/beta/SKILL.md",
        "similarity": 0.85,
    }

    result = _annotate_duplicate_candidate(item)

    assert result["kind"] == "skills"
    assert "triage_note" not in result


def test_global_rules_pair_gets_triage_note():
    """グローバル rules（~/.claude/rules）同士のペアも triage_note の対象。"""
    item = {
        "path_a": str(Path.home() / ".claude" / "rules" / "safety.md"),
        "path_b": str(Path.home() / ".claude" / "rules" / "workflow.md"),
        "similarity": 0.90,
    }

    result = _annotate_duplicate_candidate(item)

    assert result["kind"] == "rules"
    assert "triage_note" in result


def test_detect_duplicates_applies_annotation(monkeypatch):
    """detect_duplicates() が semantic_similarity_check() の結果に注釈を適用する。"""
    from prune import detection

    fake_result = [
        {
            "path_a": "/repo/.claude/rules/a.md",
            "path_b": "/repo/.claude/rules/b.md",
            "similarity": 0.95,
        }
    ]
    monkeypatch.setattr(detection, "semantic_similarity_check", lambda artifacts, threshold=0.80: fake_result)
    monkeypatch.setattr("artifact_scope.filter_artifacts_to_target", lambda artifacts: artifacts)

    result = detection.detect_duplicates({"skills": [], "rules": []})

    assert len(result) == 1
    assert result[0]["kind"] == "rules"
    assert "triage_note" in result[0]
