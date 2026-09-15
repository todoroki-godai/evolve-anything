"""#467 / #660: 実ストア・LLM に依存しない計測経路の回帰検証。"""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_BENCH = Path(__file__).resolve().parents[2] / "bench"
sys.path.insert(0, str(_BENCH))
import measure_467_instruction_violation_pipeline as m  # noqa: E402


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    import discover
    import discover.runner as runner
    import skill_origin

    # HOME は既存 conftest が tmp に隔離。対象外の検出をスタブ化し、
    # run_discover 本体・critical 行抽出・違反判定は実物を通す。
    for name in (
        "detect_behavior_patterns", "detect_error_patterns", "detect_rejection_patterns",
        "load_claude_reflect_data", "detect_recommended_artifacts",
        "detect_installed_artifacts", "detect_repeated_correction_patterns",
    ):
        monkeypatch.setattr(discover, name, Mock(return_value=[]))
    monkeypatch.setattr(discover, "detect_missed_skills", Mock(return_value={}))
    for name, value in (
        ("skill_extractor.extract_skill_candidates", []),
        ("verification_catalog.detect_verification_needs", []),
        ("telemetry_query.query_errors", []),
        ("pitfall_manager.extract_pitfall_candidates", {"candidates": []}),
        ("discover.patterns.detect_constraint_decay", []),
        ("tool_usage_analyzer.extract_tool_calls_by_session", {}),
        ("tool_usage_analyzer.detect_stall_recovery_patterns", []),
    ):
        monkeypatch.setattr(name, Mock(return_value=value))
    monkeypatch.setattr(runner, "_existing_skill_names", Mock(return_value=set()))
    project = tmp_path / "project"
    project.mkdir()
    skill = tmp_path / "SKILL.md"
    skill.write_text("MUST preserve unique sentinel records safely.\n", encoding="utf-8")
    monkeypatch.setattr(skill_origin, "resolve_plugin_skill_path", lambda _: (skill, None))
    # 全 subprocess / socket も遮断。LLM-free 対象経路以外の実呼出しを防ぐ。
    with m.guard_no_network():
        yield runner, project, skill


@pytest.mark.parametrize("violation_index, expected", [(20, 1), (1, 1), (0, 0)])
def test_latest_twenty_matches_production(pipeline, monkeypatch, violation_index, expected):
    runner, project, skill = pipeline
    corrections = [
        {"last_skill": "fixture", "timestamp": f"2026-01-{i + 1:02d}T00:00:00Z",
         "session_id": f"synthetic-{(i * 11 + 5) % 21:02d}",
         "message": skill.read_text() if i == violation_index else "unrelated weather"}
        for i in range(21)
    ]
    # 入力順で切る変異も落とすため、時系列順には渡さない。
    corrections = corrections[10:] + corrections[:10]
    fetch = Mock(side_effect=lambda _: [dict(c) for c in corrections])
    monkeypatch.setattr(runner, "_fetch_corrections_with_last_skill", fetch)
    decomposed = m.decompose(project)
    production = runner.run_discover(project_root=project)
    assert fetch.call_count >= 2
    assert "instruction_violations_error" not in production
    assert len(production.get("instruction_violations", [])) == expected
    assert decomposed["stages"]["S5_violations"] == expected
    assert decomposed["stages"]["S2_after_max_checks_slice"] == 20
    assert decomposed["stages"]["S2_truncated"] is True


@pytest.mark.parametrize("empty_first", [False, True])
def test_positive_control_uses_critical_candidate(pipeline, monkeypatch, empty_first):
    runner, project, skill = pipeline
    if empty_first:
        # プラグイン候補は critical 0、PJ 候補は critical 1。
        local = project / ".claude/skills/fixture/SKILL.md"
        local.parent.mkdir(parents=True)
        local.write_text(skill.read_text(), encoding="utf-8")
        skill.write_text("ordinary prose\n", encoding="utf-8")
    fetch = Mock(return_value=[{"last_skill": "fixture", "message": "unrelated weather"}])
    monkeypatch.setattr(runner, "_fetch_corrections_with_last_skill", fetch)
    decomposed = m.decompose(project)
    checked = decomposed["per_correction"][0]["skill_mds_checked"]
    assert [c["critical_lines"] for c in checked] == ([0, 1] if empty_first else [1])
    result = m.positive_control(project, decomposed)
    assert result["injected_skill_md"] == m._home_rel(local if empty_first else skill)
    assert result.get("both_detect_one") is True, result
    assert result["decomposed_S5_violations"] == 1
    assert result["run_discover_instruction_violations"] == 1
    assert runner._fetch_corrections_with_last_skill is fetch


@pytest.fixture
def output_cli(monkeypatch, tmp_path):
    # 出力境界のみ実行。実ストア計測も git 子プロセスも呼ばない。
    monkeypatch.setattr(m, "decompose", Mock(return_value={"synthetic": True}))
    monkeypatch.setattr(m, "_git_sha", Mock(return_value="synthetic-sha"))
    def invoke(output):
        monkeypatch.setattr(sys, "argv", [m.__file__, "--project-root", str(tmp_path),
                                          "--skip-cross-check", "--output", str(output)])
        m.main()
    return invoke


@pytest.mark.parametrize("route", ["direct", "directory_link", "file_link", "relative_parent", "missing_parent"])
def test_output_rejects_home_claude(output_cli, tmp_path, monkeypatch, route):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    protected = Path.home() / ".claude/store.json"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("preserve existing store\n", encoding="utf-8")
    output = protected
    if route == "directory_link":
        alias = tmp_path / "alias"
        alias.symlink_to(protected.parent, target_is_directory=True)
        output = alias / protected.name
    elif route == "file_link":
        output = tmp_path / "alias.json"
        output.symlink_to(protected)
    elif route == "relative_parent":
        work = Path.home() / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        output = Path("../.claude/store.json")
    elif route == "missing_parent":
        output = protected.parent / "not-created/x.json"
        assert not output.parent.exists()
    # 未処理例外は CLI の非0終了になる。通常 return を認めない。
    with pytest.raises(m.WriteGuardViolation, match="blocked"):
        output_cli(output)
    if route == "missing_parent":
        assert not output.parent.exists()
    assert protected.read_text() == "preserve existing store\n"


def test_output_outside_home_claude_succeeds(output_cli, tmp_path):
    output = tmp_path / "new/subdir/report.json"
    output_cli(output)
    payload = json.loads(output.read_text())
    assert payload["result"] == {"synthetic": True}
    assert payload["safety_verification"]["write_guard"] == "passed"
