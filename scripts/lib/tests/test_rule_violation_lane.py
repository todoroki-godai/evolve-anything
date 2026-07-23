#!/usr/bin/env python3
"""#522-3 (lane): rule_violation_observed 専用レーンのテスト。

既存 rules で禁止済みのコマンド（例: `cd` 禁止なのに cd を 626 回観測）が
repeating_patterns で「スキル候補」提案されるのを防ぐ。ルール導入済みだが
実行が止まっていない違反観測は別レーン rule_violation_observed に分離し、
スキル候補レーンから除外する（rule installed != enforced）。

決定論・LLM 非依存。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib))

from rule_violation_lane import (  # noqa: E402
    extract_prohibited_command_heads,
    partition_rule_violations,
)


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
