"""stall recovery パターン検出のテスト。

tool_usage_analyzer の extract_tool_calls_by_session / detect_stall_recovery_patterns、
issue_schema の make_stall_recovery_issue、pitfall candidate 変換をテストする。
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from issue_schema import (
    STALL_RECOVERY_CANDIDATE,
    SRC_COMMAND_PATTERN,
    SRC_SESSION_COUNT,
    SRC_RECOVERY_ACTIONS,
    SRC_CONFIDENCE,
    make_stall_recovery_issue,
)
from tool_usage_analyzer import (
    LONG_COMMAND_PATTERNS,
    INVESTIGATION_COMMANDS,
    RECOVERY_COMMANDS,
    STALL_RECOVERY_MIN_SESSIONS,
    STALL_RECOVERY_RECENCY_DAYS,
    extract_tool_calls_by_session,
    detect_stall_recovery_patterns,
)


# ── helpers ──────────────────────────────────────────


def _make_session_jsonl(commands: list[str]) -> str:
    """Bash コマンドリストからセッション JSONL 文字列を生成する。"""
    lines = []
    for cmd in commands:
        rec = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": cmd},
                    }
                ]
            },
        }
        lines.append(json.dumps(rec))
    return "\n".join(lines)


def _setup_sessions(tmp_path: Path, sessions: dict[str, list[str]]) -> Path:
    """tmp_path にプロジェクトセッションディレクトリを作成する。

    Args:
        sessions: {session_id: [commands]}

    Returns:
        projects_dir（_resolve_session_dir に渡す）
    """
    proj_dir = tmp_path / "projects" / "-Users-test-my-project"
    proj_dir.mkdir(parents=True)
    for sid, commands in sessions.items():
        f = proj_dir / f"{sid}.jsonl"
        f.write_text(_make_session_jsonl(commands), encoding="utf-8")
    return tmp_path / "projects"


# ── Section 1: Core Detection ────────────────────────


class TestConstants:
    def test_long_command_patterns_exist(self):
        assert len(LONG_COMMAND_PATTERNS) > 0
        # 主要コマンドが正規表現でマッチすること
        import re
        for cmd in ("cdk deploy --all", "docker build .", "npm install", "pip install foo"):
            assert any(re.search(p, cmd) for p in LONG_COMMAND_PATTERNS), f"{cmd} not matched"

    def test_investigation_commands_exist(self):
        assert "pgrep" in INVESTIGATION_COMMANDS
        assert "lsof" in INVESTIGATION_COMMANDS

    def test_recovery_commands_exist(self):
        assert "kill" in RECOVERY_COMMANDS
        assert "pkill" in RECOVERY_COMMANDS

    def test_min_sessions_threshold(self):
        assert STALL_RECOVERY_MIN_SESSIONS == 2

    def test_recency_days(self):
        assert STALL_RECOVERY_RECENCY_DAYS == 30


class TestExtractToolCallsBySession:
    def test_commands_grouped_by_session(self, tmp_path):
        projects_dir = _setup_sessions(tmp_path, {
            "session-a": ["ls", "cdk deploy"],
            "session-b": ["git status"],
        })
        project_root = Path("/Users/test/my-project")
        result = extract_tool_calls_by_session(
            project_root, projects_dir=projects_dir,
        )
        assert "session-a" in result
        assert "session-b" in result
        assert result["session-a"] == ["ls", "cdk deploy"]
        assert result["session-b"] == ["git status"]

    def test_no_session_dir_returns_empty(self, tmp_path):
        result = extract_tool_calls_by_session(
            Path("/nonexistent"), projects_dir=tmp_path,
        )
        assert result == {}

    def test_recency_filter_excludes_old(self, tmp_path):
        projects_dir = _setup_sessions(tmp_path, {
            "old-session": ["cdk deploy"],
            "new-session": ["docker build"],
        })
        # old-session のファイルを 60 日前に設定
        old_file = tmp_path / "projects" / "-Users-test-my-project" / "old-session.jsonl"
        old_mtime = time.time() - 60 * 86400
        os.utime(old_file, (old_mtime, old_mtime))

        result = extract_tool_calls_by_session(
            Path("/Users/test/my-project"),
            projects_dir=projects_dir,
            max_age_days=30,
        )
        assert "old-session" not in result
        assert "new-session" in result

    def test_no_max_age_includes_all(self, tmp_path):
        projects_dir = _setup_sessions(tmp_path, {
            "old-session": ["cdk deploy"],
        })
        old_file = tmp_path / "projects" / "-Users-test-my-project" / "old-session.jsonl"
        old_mtime = time.time() - 60 * 86400
        os.utime(old_file, (old_mtime, old_mtime))

        result = extract_tool_calls_by_session(
            Path("/Users/test/my-project"),
            projects_dir=projects_dir,
        )
        assert "old-session" in result


class TestDetectStallRecoveryPatterns:
    """detect_stall_recovery_patterns() のテスト。"""

    def test_two_sessions_detected(self):
        """2セッション以上で検出される。"""
        session_commands = {
            "s1": ["cdk deploy --all", "pgrep cdk", "kill 1234", "cdk deploy --all"],
            "s2": ["cdk deploy --all", "ps aux | grep cdk", "kill 5678", "cdk deploy --all"],
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert len(result) >= 1
        pattern = result[0]
        assert "cdk deploy" in pattern["command_pattern"]
        assert pattern["session_count"] >= 2
        assert "kill" in pattern["recovery_actions"]

    def test_single_session_not_detected(self):
        """1セッションのみでは検出されない。"""
        session_commands = {
            "s1": ["cdk deploy --all", "pgrep cdk", "kill 1234", "cdk deploy --all"],
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert result == []

    def test_no_investigation_not_detected(self):
        """Investigation なしでは検出されない。"""
        session_commands = {
            "s1": ["cdk deploy --all", "kill 1234", "cdk deploy --all"],
            "s2": ["cdk deploy --all", "kill 5678", "cdk deploy --all"],
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert result == []

    def test_empty_data_returns_empty(self):
        """空データで空リスト、エラーなし。"""
        assert detect_stall_recovery_patterns({}) == []

    def test_confidence_calculation(self):
        """confidence = min(0.5 + session_count * 0.1, 0.95)"""
        session_commands = {
            f"s{i}": ["docker build .", "lsof -i", "kill 100", "docker build ."]
            for i in range(5)
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert len(result) >= 1
        # 5 sessions → min(0.5 + 5*0.1, 0.95) = 0.95
        assert result[0]["confidence"] == pytest.approx(0.95)

    def test_confidence_for_2_sessions(self):
        """2 sessions → confidence = 0.7"""
        session_commands = {
            "s1": ["npm install", "pgrep node", "kill 1", "npm install"],
            "s2": ["npm install", "pgrep node", "kill 2", "npm install"],
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert len(result) >= 1
        assert result[0]["confidence"] == pytest.approx(0.7)

    def test_interleaved_commands_still_detected(self):
        """間に他のコマンドが挟まっても検出される。"""
        session_commands = {
            "s1": ["cdk deploy", "echo hello", "pgrep cdk", "echo world", "kill 1", "cdk deploy"],
            "s2": ["cdk deploy", "ls", "ps aux", "kill 2", "cdk deploy"],
        }
        result = detect_stall_recovery_patterns(session_commands)
        assert len(result) >= 1


# ── Section 2: issue_schema ──────────────────────────


class TestMakeStallRecoveryIssue:
    def test_issue_structure(self):
        pattern = {
            "command_pattern": "cdk deploy",
            "session_count": 3,
            "recovery_actions": ["kill"],
            "confidence": 0.8,
        }
        issue = make_stall_recovery_issue(pattern)
        assert issue["type"] == STALL_RECOVERY_CANDIDATE
        assert issue["source"] == "discover_stall_recovery"
        assert issue["detail"][SRC_COMMAND_PATTERN] == "cdk deploy"
        assert issue["detail"][SRC_SESSION_COUNT] == 3
        assert issue["detail"][SRC_RECOVERY_ACTIONS] == ["kill"]
        assert issue["detail"][SRC_CONFIDENCE] == 0.8

    def test_scope_is_project(self):
        issue = make_stall_recovery_issue({
            "command_pattern": "docker build",
            "session_count": 2,
            "recovery_actions": ["kill"],
            "confidence": 0.7,
        })
        assert issue["detail"].get("scope") == "project"


# ── Section 5: pitfall candidate ─────────────────────


# ── Section 3: discover integration ───────────────────


class TestDiscoverIntegration:
    @pytest.fixture(autouse=True)
    def _setup_paths(self):
        _plugin_root = Path(__file__).resolve().parent.parent.parent
        paths = [
            str(_plugin_root / "scripts"),
            str(_plugin_root / "scripts" / "lib"),
            str(_plugin_root / "skills" / "discover" / "scripts"),
            str(_plugin_root / "skills" / "audit" / "scripts"),
        ]
        for p in paths:
            if p not in sys.path:
                sys.path.insert(0, p)

    def test_recommended_artifacts_has_process_stall_guard(self):
        from discover import RECOMMENDED_ARTIFACTS

        ids = [a["id"] for a in RECOMMENDED_ARTIFACTS]
        assert "process-stall-guard" in ids

    def test_stall_recovery_patterns_in_run_discover(self):
        """run_discover 結果に stall_recovery_patterns フィールドが存在する。"""
        from discover import run_discover

        result = run_discover(project_root=Path("/nonexistent-project"))
        assert "stall_recovery_patterns" in result
        assert isinstance(result["stall_recovery_patterns"], list)


# ── Section 4: evolve report integration ─────────────


class TestEvolveIssueConversion:
    def test_stall_recovery_issue_created(self):
        pattern = {
            "command_pattern": "cdk deploy",
            "session_count": 3,
            "recovery_actions": ["kill"],
            "confidence": 0.8,
        }
        issue = make_stall_recovery_issue(pattern)
        assert issue["type"] == STALL_RECOVERY_CANDIDATE
        assert issue["detail"]["scope"] == "project"


# ── Section 5: pitfall candidate ─────────────────────


class TestStallRecoveryPitfallConversion:
    def test_root_cause_format(self):
        from tool_usage_analyzer import stall_pattern_to_pitfall_candidate

        pattern = {
            "command_pattern": "cdk deploy",
            "session_count": 3,
            "recovery_actions": ["kill"],
            "confidence": 0.8,
        }
        candidate = stall_pattern_to_pitfall_candidate(pattern)
        assert candidate["root_cause"] == "stall_recovery — cdk deploy: 3 sessions"
        assert candidate["fields"]["Occurrence-count"] == "3"

    def test_duplicate_deduplication(self):
        from tool_usage_analyzer import stall_pattern_to_pitfall_candidate

        pattern = {
            "command_pattern": "cdk deploy",
            "session_count": 3,
            "recovery_actions": ["kill"],
            "confidence": 0.8,
        }
        existing = [
            {
                "fields": {
                    "Root-cause": "stall_recovery — cdk deploy: 2 sessions",
                    "Occurrence-count": "2",
                },
            }
        ]
        candidate = stall_pattern_to_pitfall_candidate(pattern, existing_candidates=existing)
        # 重複 → None（既存候補の Occurrence-count が更新される）
        assert candidate is None
        assert existing[0]["fields"]["Occurrence-count"] == "3"
