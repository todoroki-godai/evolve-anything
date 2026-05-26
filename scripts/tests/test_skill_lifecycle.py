"""Issue #186: スキルライフサイクル管理 — 4つの機能の単体テスト。

- aggregate_contribution_scores: outcome フィールドからスコア算出
- generate_report: スキル数キャップ表示
- detect_retirement_candidates: 低貢献スコアのアーカイブ候補検出
- evaluate_corrections: per-skill Pre-flight ガードレール
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


# ─── Task 1: 貢献スコア追跡 ─────────────────────────────────────────────────


def test_aggregate_contribution_scores_basic():
    """success/error を持つレコードから正しくスコアを算出する。"""
    from audit.usage import aggregate_contribution_scores

    records = [
        {"skill_name": "my-skill", "outcome": "success"},
        {"skill_name": "my-skill", "outcome": "success"},
        {"skill_name": "my-skill", "outcome": "error"},
        {"skill_name": "bad-skill", "outcome": "error"},
        {"skill_name": "bad-skill", "outcome": "error"},
        {"skill_name": "bad-skill", "outcome": "error"},
    ]
    scores = aggregate_contribution_scores(records, min_invocations=3)

    assert "my-skill" in scores
    assert abs(scores["my-skill"]["score"] - 2 / 3) < 0.001
    assert scores["my-skill"]["success"] == 2
    assert scores["my-skill"]["error"] == 1

    assert "bad-skill" in scores
    assert scores["bad-skill"]["score"] == 0.0


def test_aggregate_contribution_scores_min_invocations():
    """invocations が閾値未満のスキルは score=None になる。"""
    from audit.usage import aggregate_contribution_scores

    records = [
        {"skill_name": "rare-skill", "outcome": "success"},
        {"skill_name": "rare-skill", "outcome": "success"},
    ]
    scores = aggregate_contribution_scores(records, min_invocations=5)

    assert scores["rare-skill"]["score"] is None
    assert scores["rare-skill"]["total"] == 2


def test_aggregate_contribution_scores_no_outcome():
    """outcome フィールドがないレコードは集計対象外。"""
    from audit.usage import aggregate_contribution_scores

    records = [
        {"skill_name": "old-skill"},
        {"skill_name": "old-skill", "ts": "2026-01-01"},
    ]
    scores = aggregate_contribution_scores(records)
    assert "old-skill" not in scores


def test_aggregate_contribution_scores_skip_outcome():
    """outcome="skip" はエラーではなくスキップ扱いで total に加算される。"""
    from audit.usage import aggregate_contribution_scores

    records = [
        {"skill_name": "s", "outcome": "success"},
        {"skill_name": "s", "outcome": "skip"},
        {"skill_name": "s", "outcome": "skip"},
        {"skill_name": "s", "outcome": "error"},
        {"skill_name": "s", "outcome": "success"},
    ]
    scores = aggregate_contribution_scores(records, min_invocations=3)
    assert scores["s"]["total"] == 5
    # success=2, total=5 → score=0.4
    assert abs(scores["s"]["score"] - 2 / 5) < 0.001


def test_aggregate_contribution_scores_builtin_excluded():
    """_BUILTIN_TOOLS に含まれるスキルは集計対象外。"""
    from audit.usage import aggregate_contribution_scores

    records = [
        {"skill_name": "commit", "outcome": "success"},
        {"skill_name": "commit", "outcome": "success"},
        {"skill_name": "commit", "outcome": "success"},
    ]
    scores = aggregate_contribution_scores(records, min_invocations=3)
    assert "commit" not in scores


# ─── Task 3: スキル数キャップ ────────────────────────────────────────────────


def test_generate_report_skill_count_cap():
    """スキル数が推奨上限以内の場合、上限表示が含まれる。"""
    from pathlib import Path
    from audit.report import generate_report

    artifacts = {"skills": [Path(f"/tmp/s{i}/SKILL.md") for i in range(5)]}
    report = generate_report(
        artifacts=artifacts,
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        max_skill_count=30,
    )
    assert "skills: 5 / 推奨上限 30" in report


def test_generate_report_skill_count_cap_exceeded():
    """スキル数が推奨上限を超えた場合、警告インジケータが付く。"""
    from pathlib import Path
    from audit.report import generate_report

    artifacts = {"skills": [Path(f"/tmp/s{i}/SKILL.md") for i in range(35)]}
    report = generate_report(
        artifacts=artifacts,
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        max_skill_count=30,
    )
    assert "skills: 35 / 推奨上限 30 ⚠️" in report


def test_generate_report_contribution_score_displayed():
    """contribution_scores が渡された場合、Usage セクションに表示される。"""
    from pathlib import Path
    from audit.report import generate_report

    artifacts: Dict = {}
    usage = {"my-skill": 5}
    contribution_scores = {"my-skill": {"score": 0.8, "success": 4, "error": 1, "total": 5}}

    report = generate_report(
        artifacts=artifacts,
        violations=[],
        usage=usage,
        duplicates=[],
        advisories=[],
        contribution_scores=contribution_scores,
    )
    assert "contribution: 80%" in report


def test_generate_report_contribution_score_none_shows_na():
    """score=None のスキルは「contribution: N/A」と表示される。"""
    from audit.report import generate_report

    usage = {"rare-skill": 2}
    contribution_scores = {"rare-skill": {"score": None, "success": 1, "error": 1, "total": 2}}

    report = generate_report(
        artifacts={},
        violations=[],
        usage=usage,
        duplicates=[],
        advisories=[],
        contribution_scores=contribution_scores,
    )
    assert "contribution: N/A" in report


# ─── Task 2 (Retirement): detect_retirement_candidates ───────────────────────


def test_detect_retirement_candidates_basic(tmp_path):
    """貢献スコアが閾値以下のスキルが Retirement 候補として返る。"""
    from prune.detection import detect_retirement_candidates

    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Bad Skill\n")

    artifacts = {"skills": [skill_file]}
    contribution_scores = {
        "bad-skill": {"score": 0.1, "success": 1, "error": 9, "total": 10},
    }

    candidates = detect_retirement_candidates(
        artifacts, contribution_scores, contribution_threshold=0.3, min_invocations=5
    )
    assert len(candidates) == 1
    assert candidates[0]["skill_name"] == "bad-skill"
    assert candidates[0]["reason"] == "low_contribution"


def test_detect_retirement_candidates_skip_above_threshold(tmp_path):
    """貢献スコアが閾値以上のスキルは候補に含まれない。"""
    from prune.detection import detect_retirement_candidates

    skill_dir = tmp_path / "good-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Good Skill\n")

    artifacts = {"skills": [skill_file]}
    contribution_scores = {
        "good-skill": {"score": 0.9, "success": 9, "error": 1, "total": 10},
    }

    candidates = detect_retirement_candidates(artifacts, contribution_scores)
    assert len(candidates) == 0


def test_detect_retirement_candidates_skip_insufficient_invocations(tmp_path):
    """invocations が min_invocations 未満（score=None）はスキップ。"""
    from prune.detection import detect_retirement_candidates

    skill_dir = tmp_path / "rare-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Rare Skill\n")

    artifacts = {"skills": [skill_file]}
    contribution_scores = {
        "rare-skill": {"score": None, "success": 1, "error": 1, "total": 2},
    }

    candidates = detect_retirement_candidates(artifacts, contribution_scores)
    assert len(candidates) == 0


def test_detect_retirement_candidates_empty_scores():
    """contribution_scores が空の場合は空リスト。"""
    from prune.detection import detect_retirement_candidates

    candidates = detect_retirement_candidates({"skills": []}, {})
    assert candidates == []


def test_run_prune_has_retirement_candidates_key(tmp_path):
    """run_prune の返り値に retirement_candidates キーが含まれる。"""
    from unittest import mock

    # run_prune は外部依存が多いためすべて mock する
    with (
        mock.patch("prune.find_artifacts", return_value={"skills": [], "rules": []}),
        mock.patch("prune.detect_dead_globs", return_value=[]),
        mock.patch("prune.detect_zero_invocations", return_value=([], [])),
        mock.patch("prune.safe_global_check", return_value=[]),
        mock.patch("prune.detect_duplicates", return_value=[]),
        mock.patch("prune.detect_decay_candidates", return_value=[]),
        mock.patch("prune.detect_reference_drift", return_value=[]),
        mock.patch("prune.detect_retirement_candidates", return_value=[]),
        mock.patch("prune.cleanup_corrections", return_value={"removed": 0}),
        mock.patch("prune.merge_duplicates", return_value={"merged": 0}),
        mock.patch("audit.load_usage_data", return_value=[]),
        mock.patch("audit.aggregate_contribution_scores", return_value={}),
    ):
        from prune.runner import run_prune
        result = run_prune(str(tmp_path))

    assert "retirement_candidates" in result
    assert isinstance(result["retirement_candidates"], list)


# ─── Task 4: Pre-flight ガードレール能動化 ───────────────────────────────────


def test_evaluate_corrections_preflight_warning(tmp_path):
    """per-skill 閾値に達したスキルの Pre-flight 警告がメッセージに含まれる。"""
    import trigger_engine
    from trigger_engine.session_corrections import evaluate_corrections

    corrections_file = tmp_path / "corrections.jsonl"
    records = []
    for i in range(12):
        skill = "bad-skill" if i < 5 else "other-skill"
        records.append({"timestamp": "2026-01-01T00:00:00+00:00", "last_skill": skill})
    corrections_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    state = {"last_run_timestamp": ""}
    with (
        mock.patch.object(trigger_engine, "DATA_DIR", tmp_path),
        mock.patch("trigger_engine.session_corrections._is_in_cooldown", return_value=False),
        mock.patch("trigger_engine.session_corrections._record_trigger", side_effect=lambda s, r: s),
        mock.patch("trigger_engine.session_corrections._save_state"),
        mock.patch(
            "rl_common.config.load_user_config",
            return_value={"correction_preflight_threshold": 3},
        ),
    ):
        result = evaluate_corrections(state)

    assert result.triggered
    assert "Pre-flight 警告" in result.message
    assert "bad-skill" in result.message
    assert "per_skill_preflight" in result.details
    assert "bad-skill" in result.details["per_skill_preflight"]


def test_evaluate_corrections_no_preflight_below_threshold(tmp_path):
    """per-skill 閾値に達していない場合、Pre-flight 警告が出ない。"""
    import trigger_engine
    from trigger_engine.session_corrections import evaluate_corrections

    corrections_file = tmp_path / "corrections.jsonl"
    records = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "last_skill": "some-skill"}
        for _ in range(12)
    ]
    corrections_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    state = {"last_run_timestamp": ""}
    with (
        mock.patch.object(trigger_engine, "DATA_DIR", tmp_path),
        mock.patch("trigger_engine.session_corrections._is_in_cooldown", return_value=False),
        mock.patch("trigger_engine.session_corrections._record_trigger", side_effect=lambda s, r: s),
        mock.patch("trigger_engine.session_corrections._save_state"),
        mock.patch(
            "rl_common.config.load_user_config",
            return_value={"correction_preflight_threshold": 20},
        ),
    ):
        result = evaluate_corrections(state)

    assert result.triggered
    assert "Pre-flight 警告" not in result.message
    assert result.details.get("per_skill_preflight") == []


# ─── Bug fix: .archive/ 除外 + max_skill_count を custom のみで判定 ──────────


def test_find_artifacts_excludes_archive_dir(tmp_path):
    """.archive/ 配下の SKILL.md は find_artifacts に含まれない。"""
    from audit.artifacts import find_artifacts

    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "active-skill").mkdir()
    (skills_dir / "active-skill" / "SKILL.md").write_text("---\nname: active-skill\n---\n")

    archive_dir = skills_dir / ".archive" / "old-skill"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text("---\nname: old-skill\n---\n")

    artifacts = find_artifacts(tmp_path)
    skill_paths = [str(p) for p in artifacts["skills"]]

    assert any("active-skill" in p for p in skill_paths), "active skill が含まれるべき"
    assert not any(".archive" in p for p in skill_paths), ".archive 配下は除外されるべき"


def test_generate_report_skill_count_cap_uses_custom_only(tmp_path):
    """global スキルが多数あっても custom のみで推奨上限を判定する。"""
    from pathlib import Path
    from audit.report import generate_report
    from unittest import mock

    # custom: 5件、global: 100件 → custom <= 30 なので ⚠️ なし
    custom_paths = [tmp_path / f"proj/.claude/skills/s{i}/SKILL.md" for i in range(5)]
    global_home = Path.home() / ".claude" / "skills"
    global_paths = [global_home / f"g{i}" / "SKILL.md" for i in range(100)]

    def fake_classify(path):
        if str(path).startswith(str(global_home)):
            return "global"
        return "custom"

    with mock.patch("audit.report.classify_artifact_origin", side_effect=fake_classify):
        report = generate_report(
            artifacts={"skills": custom_paths + global_paths},
            violations=[],
            usage={},
            duplicates=[],
            advisories=[],
            max_skill_count=30,
        )

    assert "⚠️" not in report, f"custom が5件なので警告なしのはず"
    assert "custom: 5" in report
    assert "global: 100" in report


def test_generate_report_skill_count_cap_warns_on_custom_excess(tmp_path):
    """custom スキルが上限超過したら ⚠️ が付く（global があっても）。"""
    from pathlib import Path
    from audit.report import generate_report
    from unittest import mock

    # custom: 35件（>30）、global: 10件
    custom_paths = [tmp_path / f"proj/.claude/skills/s{i}/SKILL.md" for i in range(35)]
    global_home = Path.home() / ".claude" / "skills"
    global_paths = [global_home / f"g{i}" / "SKILL.md" for i in range(10)]

    def fake_classify(path):
        if str(path).startswith(str(global_home)):
            return "global"
        return "custom"

    with mock.patch("audit.report.classify_artifact_origin", side_effect=fake_classify):
        report = generate_report(
            artifacts={"skills": custom_paths + global_paths},
            violations=[],
            usage={},
            duplicates=[],
            advisories=[],
            max_skill_count=30,
        )

    assert "⚠️" in report
    assert "custom: 35" in report
