#!/usr/bin/env python3
"""#522-3 (lane): rule_violation_observed 専用レーンのテスト。

既存 rules で禁止済みのコマンド（例: `cd` 禁止なのに cd を 626 回観測）が
repeating_patterns で「スキル候補」提案されるのを防ぐ。ルール導入済みだが
実行が止まっていない違反観測は別レーン rule_violation_observed に分離し、
スキル候補レーンから除外する（rule installed != enforced）。

決定論・LLM 非依存。
"""
import json
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))

from rule_violation_lane import (  # noqa: E402
    apply_hook_enforcement_status,
    extract_prohibited_command_heads,
    partition_rule_violations,
)


def _write_hook_script(path, prohibited):
    """テスト用の enforcement hook スクリプトを書く（本物の PROHIBITED = {...} 形式）。"""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\nimport sys\n\n"
        f"PROHIBITED = {prohibited!r}\n\n"
        "def main():\n    pass\n",
        encoding="utf-8",
    )


def _write_session(projects_dir, project_root, records):
    """project_root 用のセッション jsonl を1本書く。records は (command, timestamp) の list。"""
    slug_dir = projects_dir / f"-Users-someone-{project_root.name}"
    slug_dir.mkdir(parents=True, exist_ok=True)
    session_file = slug_dir / "session1.jsonl"
    lines = []
    for command, ts in records:
        rec = {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ]
            },
        }
        lines.append(json.dumps(rec))
    session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestExtractProhibitedCommandHeads:
    def test_extracts_backtick_token_near_prohibition_keyword(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "code-quality.md").write_text(
            "# コード品質\n- Bashで `cd` 禁止（複合 `cd X && ...` も）。絶対パスを使う。\n"
        )
        heads = extract_prohibited_command_heads([rules_dir])
        assert "cd" in heads

    def test_ignores_backtick_token_without_prohibition_keyword(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "x.md").write_text("- `git status` で状態を確認する\n")
        heads = extract_prohibited_command_heads([rules_dir])
        assert "git" not in heads

    def test_must_not_keyword(self, tmp_path):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "x.md").write_text("- `pkill` is MUST NOT in parallel workers\n")
        heads = extract_prohibited_command_heads([rules_dir])
        assert "pkill" in heads

    def test_multiple_rule_dirs_merged(self, tmp_path):
        d1 = tmp_path / "global"
        d2 = tmp_path / "project"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.md").write_text("- `cd` は禁止\n")
        (d2 / "b.md").write_text("- `sudo` を使うのは禁止\n")
        heads = extract_prohibited_command_heads([d1, d2])
        assert "cd" in heads
        assert "sudo" in heads

    def test_missing_dir_returns_empty(self, tmp_path):
        heads = extract_prohibited_command_heads([tmp_path / "nope"])
        assert heads == set()

    def test_multiword_banned_command_is_not_collapsed_to_first_word(self, tmp_path):
        """#222: `git checkout -b` のような複数語禁止コマンドは先頭語 `git` に
        縮約せずトークン列全体を保持する。"""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "worktree.md").write_text(
            "- `git checkout -b` は worktree ワークフロー外で禁止\n"
        )
        heads = extract_prohibited_command_heads([rules_dir])
        assert "git checkout -b" in heads
        assert "git" not in heads


class TestPartitionRuleViolations:
    def test_splits_prohibited_pattern_into_violation_lane(self):
        patterns = [
            {"pattern": "cd somewhere", "count": 626, "subcategory": "cli", "examples": []},
            {"pattern": "git status", "count": 30, "subcategory": "vcs", "examples": []},
        ]
        out = partition_rule_violations(patterns, prohibited_heads={"cd"})
        assert len(out["skill_candidates"]) == 1
        assert out["skill_candidates"][0]["pattern"] == "git status"
        assert len(out["rule_violation_observed"]) == 1
        viol = out["rule_violation_observed"][0]
        assert viol["pattern"] == "cd somewhere"
        assert viol["count"] == 626
        assert viol["violated_command"] == "cd"
        assert "enforce" in viol["recommendation"]

    def test_no_prohibited_heads_keeps_all_as_skill_candidates(self):
        patterns = [{"pattern": "git status", "count": 10, "subcategory": "vcs", "examples": []}]
        out = partition_rule_violations(patterns, prohibited_heads=set())
        assert len(out["skill_candidates"]) == 1
        assert out["rule_violation_observed"] == []

    def test_head_extracted_from_pattern_first_token(self):
        patterns = [{"pattern": "pkill -f next-server", "count": 12}]
        out = partition_rule_violations(patterns, prohibited_heads={"pkill"})
        assert len(out["rule_violation_observed"]) == 1
        assert out["rule_violation_observed"][0]["violated_command"] == "pkill"

    def test_empty_patterns(self):
        out = partition_rule_violations([], prohibited_heads={"cd"})
        assert out["skill_candidates"] == []
        assert out["rule_violation_observed"] == []

    def test_multiword_prohibited_command_does_not_match_unrelated_head(self):
        """#222: 禁止指定が `git checkout -b` のとき、無関係な `git status` は
        誤マッチしない（先頭語 `git` への縮約バグの再発防止）。"""
        patterns = [
            {"pattern": "git status", "count": 50, "examples": []},
            {"pattern": "git checkout -b x", "count": 30, "examples": []},
        ]
        out = partition_rule_violations(patterns, prohibited_heads={"git checkout -b"})
        assert [p["pattern"] for p in out["skill_candidates"]] == ["git status"]
        assert len(out["rule_violation_observed"]) == 1
        viol = out["rule_violation_observed"][0]
        assert viol["pattern"] == "git checkout -b x"
        assert viol["violated_command"] == "git checkout -b"

    def test_single_word_prohibited_command_still_matches_head_as_before(self):
        """単一語の禁止コマンド（例: `cd`）は従来通り head 一致で判定する。"""
        patterns = [{"pattern": "cd foo", "count": 10, "examples": []}]
        out = partition_rule_violations(patterns, prohibited_heads={"cd"})
        assert len(out["rule_violation_observed"]) == 1
        assert out["rule_violation_observed"][0]["violated_command"] == "cd"


class TestApplyHookEnforcementStatus:
    """#479: 既に導入済みの enforcement hook を再度作れと提案し続ける問題の修正。"""

    def _violation(self, head="cd", count=25):
        return {
            "pattern": f"{head} somewhere",
            "count": count,
            "examples": [],
            "violated_command": head,
            "reason": "rule_installed_but_not_enforced",
            "recommendation": f"既存 rules で `{head}` は禁止済みだが {count} 回観測。",
        }

    def test_hook_not_installed_keeps_proposal_unchanged(self, tmp_path):
        """分岐1: hook が存在しない → 従来どおり提案を出す（逆方向固定）。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"  # 実在させない
        violations = [self._violation()]
        out = apply_hook_enforcement_status(
            violations, hook_path=hook_path, project_root=tmp_path / "proj",
        )
        assert out == violations
        assert out[0]["reason"] == "rule_installed_but_not_enforced"

    def test_hook_installed_and_no_post_install_occurrence_drops_proposal(self, tmp_path):
        """分岐2: hook 実在 + PROHIBITED に含まれ + 導入後の観測が0 → 提案から除外（対処済み）。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        hook_path.parent.mkdir(parents=True)
        _write_hook_script(hook_path, {"cd"})

        project_root = tmp_path / "proj"
        projects_dir = tmp_path / "projects"
        # hook 導入前（未来の mtime にするため、後で hook の mtime を過去に固定する）の観測のみ
        _write_session(
            projects_dir, project_root,
            [("cd /tmp", "2020-01-01T00:00:00.000Z")],
        )
        # hook の mtime をこの観測より後にする
        import os
        import time as time_mod
        future = time_mod.time()
        os.utime(hook_path, (future, future))

        out = apply_hook_enforcement_status(
            [self._violation()], hook_path=hook_path,
            project_root=project_root, projects_dir=projects_dir,
        )
        assert out == []

    def test_hook_installed_but_still_violated_after_install_changes_reason(self, tmp_path):
        """分岐3: hook 実在 + PROHIBITED に含まれるが導入後も観測がある → reason 変更・件数を導入後観測数に更新。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        hook_path.parent.mkdir(parents=True)
        _write_hook_script(hook_path, {"cd"})
        import os
        import time as time_mod
        past = time_mod.time() - 3600
        os.utime(hook_path, (past, past))

        project_root = tmp_path / "proj"
        projects_dir = tmp_path / "projects"
        from datetime import datetime, timedelta, timezone
        after = (datetime.now(timezone.utc) + timedelta(seconds=10)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        before = "2020-01-01T00:00:00.000Z"
        _write_session(
            projects_dir, project_root,
            [("cd /tmp", before), ("cd /tmp", after), ("cd /var", after)],
        )

        out = apply_hook_enforcement_status(
            [self._violation(count=626)], hook_path=hook_path,
            project_root=project_root, projects_dir=projects_dir,
        )
        assert len(out) == 1
        assert out[0]["reason"] == "enforced_but_still_violated"
        assert out[0]["count"] == 2  # 導入後の2回のみ
        assert "hook" in out[0]["recommendation"]
        assert "作れ" not in out[0]["recommendation"]

    def test_head_not_in_prohibited_set_is_unaffected(self, tmp_path):
        """hook は実在するが violated_command が PROHIBITED に含まれない → 変更なし。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        hook_path.parent.mkdir(parents=True)
        _write_hook_script(hook_path, {"cd"})

        violations = [self._violation(head="pkill", count=12)]
        out = apply_hook_enforcement_status(
            violations, hook_path=hook_path, project_root=tmp_path / "proj",
        )
        assert out == violations

    def test_malformed_hook_script_fails_open(self, tmp_path):
        """hook の PROHIBITED パースに失敗 → fail-open（従来どおり提案を出す）。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("this is not valid python for PROHIBITED extraction\n")

        violations = [self._violation()]
        out = apply_hook_enforcement_status(
            violations, hook_path=hook_path, project_root=tmp_path / "proj",
        )
        assert out == violations

    def test_project_root_none_time_window_undecidable_keeps_proposal(self, tmp_path):
        """project_root が無く時間窓判定不能 → 安全側で変更なし（false negative を避ける）。"""
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        hook_path.parent.mkdir(parents=True)
        _write_hook_script(hook_path, {"cd"})

        violations = [self._violation()]
        out = apply_hook_enforcement_status(violations, hook_path=hook_path, project_root=None)
        assert out == violations

    def test_empty_violations_returns_empty(self, tmp_path):
        hook_path = tmp_path / "hooks" / "enforce-prohibited-commands.py"
        out = apply_hook_enforcement_status([], hook_path=hook_path, project_root=tmp_path / "proj")
        assert out == []
