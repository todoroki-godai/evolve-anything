"""#555: discover examples truncate + cross_pj meta tests (TDD).

rule_violation_observed / tool_usage_patterns の examples フィールドが
巨大な多行スクリプト丸ごとで表示が極端に重い問題への対処:

1. examples を 1行 truncate（先頭 120字、複数行は最初の1行＋省略記号「…」）
2. 違反 example のパスが別PJのソースツリーを指す場合は cross_pj: true メタを付与
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))


# ===== 1. truncate_example ヘルパ =====

class TestTruncateExample:
    """discover.truncate_example の単体テスト。"""

    def _fn(self):
        from discover import truncate_example
        return truncate_example

    def test_short_single_line_unchanged(self):
        fn = self._fn()
        assert fn("git status") == "git status"

    def test_long_single_line_truncated_at_120(self):
        fn = self._fn()
        long_line = "a" * 200
        result = fn(long_line)
        assert len(result) <= 121 + 1  # 120 chars + "…"
        assert result.endswith("…")

    def test_exactly_120_chars_unchanged(self):
        fn = self._fn()
        line = "x" * 120
        result = fn(line)
        assert result == line
        assert not result.endswith("…")

    def test_121_chars_truncated(self):
        fn = self._fn()
        line = "x" * 121
        result = fn(line)
        assert result == "x" * 120 + "…"

    def test_multiline_keeps_first_line_only(self):
        fn = self._fn()
        cmd = "git status\nsome more lines\neven more"
        result = fn(cmd)
        assert "\n" not in result
        assert result == "git status…"

    def test_multiline_long_first_line_truncated(self):
        fn = self._fn()
        first = "x" * 200
        cmd = first + "\nother line"
        result = fn(cmd)
        assert "\n" not in result
        assert result.endswith("…")
        assert len(result) <= 122  # 120 + "…"

    def test_empty_string_unchanged(self):
        fn = self._fn()
        assert fn("") == ""

    def test_single_newline_truncated(self):
        fn = self._fn()
        result = fn("cmd\n")
        assert result == "cmd…"


# ===== 2. detect_repeating_commands — examples truncate =====

class TestDetectRepeatingCommandsExamplesTruncate:
    """detect_repeating_commands が返す examples が truncate 済みであること。"""

    def _fn(self):
        from tool_usage_analyzer import detect_repeating_commands
        return detect_repeating_commands

    def test_multiline_example_truncated_to_single_line(self):
        fn = self._fn()
        # 5回以上繰り返されるコマンド（threshold=5）で多行スクリプトを含む
        multiline = "git status\necho done\nls -la"
        commands = [multiline] * 6
        patterns = fn(commands, threshold=5)
        assert len(patterns) == 1
        for ex in patterns[0]["examples"]:
            assert "\n" not in ex
            assert ex == "git status…"

    def test_long_example_truncated(self):
        fn = self._fn()
        long_cmd = "python3 " + "a" * 200
        commands = [long_cmd] * 6
        patterns = fn(commands, threshold=5)
        assert len(patterns) == 1
        for ex in patterns[0]["examples"]:
            assert len(ex) <= 122
            assert ex.endswith("…")

    def test_short_example_unchanged(self):
        fn = self._fn()
        short_cmd = "git status"
        commands = [short_cmd] * 6
        patterns = fn(commands, threshold=5)
        assert len(patterns) == 1
        for ex in patterns[0]["examples"]:
            assert ex == "git status"
            assert "…" not in ex


# ===== 3. partition_rule_violations — cross_pj meta =====

class TestPartitionRuleViolationsCrossPj:
    """partition_rule_violations が cross_pj: true を付与すること。"""

    def _fn(self):
        from rule_violation_lane import partition_rule_violations
        return partition_rule_violations

    def test_cross_pj_true_when_example_references_other_project(self):
        fn = self._fn()
        # 自PJは /home/user/my-project、例が別PJのパスを含む
        project_root = Path("/home/user/my-project")
        other_path = "/home/user/other-project/script.py"
        patterns = [
            {
                "pattern": "cd",
                "count": 5,
                "examples": [f"cd {other_path}"],
            },
        ]
        out = fn(patterns, prohibited_heads={"cd"}, project_root=project_root)
        assert len(out["rule_violation_observed"]) == 1
        viol = out["rule_violation_observed"][0]
        assert viol.get("cross_pj") is True

    def test_no_cross_pj_when_example_references_same_project(self):
        fn = self._fn()
        project_root = Path("/home/user/my-project")
        patterns = [
            {
                "pattern": "cd",
                "count": 5,
                "examples": [f"cd {project_root}/src"],
            },
        ]
        out = fn(patterns, prohibited_heads={"cd"}, project_root=project_root)
        assert len(out["rule_violation_observed"]) == 1
        viol = out["rule_violation_observed"][0]
        assert viol.get("cross_pj") is not True

    def test_no_cross_pj_when_project_root_not_provided(self):
        fn = self._fn()
        patterns = [
            {
                "pattern": "cd",
                "count": 5,
                "examples": ["/home/user/other-project/run.sh"],
            },
        ]
        out = fn(patterns, prohibited_heads={"cd"})
        assert len(out["rule_violation_observed"]) == 1
        viol = out["rule_violation_observed"][0]
        # project_root なしの場合は cross_pj を付与しない（判定不能）
        assert "cross_pj" not in viol

    def test_cross_pj_true_when_any_example_references_other_project(self):
        fn = self._fn()
        project_root = Path("/home/user/my-project")
        same_path = f"{project_root}/sub/script.py"
        other_path = "/home/user/another-project/run.sh"
        patterns = [
            {
                "pattern": "cd",
                "count": 5,
                "examples": [same_path, other_path],
            },
        ]
        out = fn(patterns, prohibited_heads={"cd"}, project_root=project_root)
        viol = out["rule_violation_observed"][0]
        assert viol.get("cross_pj") is True

    def test_backward_compat_without_project_root(self):
        """project_root 省略時、既存テストと同じ動作をすること。"""
        fn = self._fn()
        patterns = [
            {"pattern": "git status", "count": 10, "subcategory": "vcs", "examples": []},
        ]
        out = fn(patterns, prohibited_heads=set())
        assert len(out["skill_candidates"]) == 1
        assert out["rule_violation_observed"] == []


# ===== 4. run_discover integration — examples in rule_violation_observed =====

class TestRunDiscoverRuleViolationExamplesTruncate:
    """run_discover の rule_violation_observed に truncate 済み examples が入ること。

    tool_usage_analyzer / extract_prohibited_command_heads を mock して
    実際の JSONL 読み込みなしにテストする。
    """

    def test_rule_violation_examples_truncated_in_run_discover(self, monkeypatch):
        """run_discover が rule_violation_observed を返すとき examples が truncate されていること。"""
        import lib.discover as discover

        multiline_cmd = "cd /tmp\necho hello\nls -la"
        tool_result_stub = {
            "total_tool_calls": 10,
            "repeating_patterns": [
                {
                    "pattern": "cd",
                    "count": 10,
                    "subcategory": "cli",
                    "examples": [multiline_cmd],
                },
            ],
            "builtin_replaceable": [],
            "cli_summary": {},
            "bash_calls": 5,
            "bash_ratio": 0.5,
        }

        monkeypatch.setattr(discover, "detect_behavior_patterns", lambda **kw: [])
        monkeypatch.setattr(discover, "detect_error_patterns", lambda **kw: [])
        monkeypatch.setattr(discover, "detect_rejection_patterns", lambda: [])
        monkeypatch.setattr(discover, "load_claude_reflect_data", lambda: [])
        monkeypatch.setattr(discover, "detect_missed_skills", lambda **kw: {"missed": [], "message": ""})
        monkeypatch.setattr(discover, "detect_recommended_artifacts", lambda **kw: [])
        monkeypatch.setattr(discover, "detect_installed_artifacts", lambda **kw: [])
        monkeypatch.setattr(discover, "determine_scope", lambda p: "global")
        monkeypatch.setattr(discover, "_enrich_patterns", lambda patterns, **kw: {"matched_skills": [], "unmatched_patterns": []})

        import tool_usage_analyzer as tua
        monkeypatch.setattr(tua, "analyze_tool_usage", lambda **kw: tool_result_stub)

        import rule_violation_lane as rvl
        monkeypatch.setattr(rvl, "extract_prohibited_command_heads", lambda dirs: {"cd"})

        result = discover.run_discover(tool_usage=True)

        violations = result.get("rule_violation_observed", [])
        assert len(violations) == 1
        for ex in violations[0].get("examples", []):
            assert "\n" not in ex, f"example contains newline: {ex!r}"
