#!/usr/bin/env python3
"""remediation.py の _independent_verify / FP_EXCLUSIONS テスト。"""
import sys
from pathlib import Path

import pytest

# remediation.py のパスを通す
_evolve_scripts = Path(__file__).resolve().parent.parent.parent / "skills" / "evolve" / "scripts"
sys.path.insert(0, str(_evolve_scripts))

from remediation import _independent_verify, FP_EXCLUSIONS, _should_exclude_fp


# ── _independent_verify ──────────────────────────────


def test_independent_verify_pass():
    """正常な修正が pass する。"""
    before = "# Title\n\nSome content.\n\n## Sub\n\nMore text.\n"
    after = "# Title\n\nUpdated content.\n\n## Sub\n\nMore text.\n"
    result = _independent_verify(
        {"type": "stale_ref", "file": "CLAUDE.md", "detail": {}},
        before,
        after,
    )
    assert result["passed"] is True
    assert result["confidence"] > 0.0


def test_independent_verify_heading_loss():
    """見出し削除を検出する。"""
    before = "# Title\n\n## Section A\n\nText\n\n## Section B\n\nText\n"
    after = "# Title\n\nText\n\nText\n"
    result = _independent_verify(
        {"type": "stale_ref", "file": "CLAUDE.md", "detail": {}},
        before,
        after,
    )
    assert result["passed"] is False
    assert "heading" in result["reason"].lower() or "見出し" in result["reason"]


def test_independent_verify_empty_file():
    """空ファイルを検出する。"""
    before = "# Title\n\nContent here.\n"
    after = ""
    result = _independent_verify(
        {"type": "stale_ref", "file": "CLAUDE.md", "detail": {}},
        before,
        after,
    )
    assert result["passed"] is False
    assert "empty" in result["reason"].lower() or "空" in result["reason"]


def test_independent_verify_codeblock_mismatch():
    """コードブロックの対応崩れを検出する。"""
    before = "# Title\n\n```python\nprint('hi')\n```\n"
    after = "# Title\n\n```python\nprint('hi')\n"
    result = _independent_verify(
        {"type": "stale_ref", "file": "CLAUDE.md", "detail": {}},
        before,
        after,
    )
    assert result["passed"] is False
    assert "code" in result["reason"].lower() or "コードブロック" in result["reason"]


def test_independent_verify_rules_line_limit():
    """Rules ファイルの行数制限超過を検出する。"""
    _scripts_dir = Path(__file__).resolve().parent.parent
    _lib_dir = _scripts_dir / "lib"
    sys.path.insert(0, str(_scripts_dir))
    sys.path.insert(0, str(_lib_dir))
    from line_limit import MAX_RULE_LINES

    before = "# Rule\nShort rule.\n"
    # MAX_RULE_LINES を超える after を生成
    after = "\n".join([f"Line {i}" for i in range(MAX_RULE_LINES + 5)])
    result = _independent_verify(
        {"type": "stale_rule", "file": ".claude/rules/my-rule.md", "detail": {}},
        before,
        after,
    )
    assert result["passed"] is False
    assert "line" in result["reason"].lower() or "行数" in result["reason"]


# ── FP_EXCLUSIONS / _should_exclude_fp ──────────────────────────────


def test_fp_exclusions_test_file():
    """テストファイル内の参照は FP として除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "scripts/tests/test_something.py",
        "detail": {"path": "some/missing/path.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "test_file"


def test_fp_exclusions_external_url():
    """http(s):// で始まる参照は FP として除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "https://example.com/doc"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "external_url"


def test_fp_exclusions_archive_path():
    """archive パス配下の参照は FP として除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "openspec/changes/archive/2026-01-01-old/tasks.md"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "archive_path"


def test_fp_exclusions_no_match():
    """除外条件に該当しない issue は None を返す。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "scripts/lib/missing_module.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason is None


def test_fp_exclusions_numeric_only():
    """数値のみパターンは除外される。"""
    issue = {
        "type": "hardcoded_value",
        "file": "rules.md",
        "detail": {"matched": "429", "path": ""},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "numeric_only"


def test_fp_exclusions_code_block_ref():
    """コードブロック内の参照は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "some/path.py", "in_code_block": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "code_block_ref"


def test_fp_exclusions_commented_out():
    """コメント内の参照は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "old/path.py", "commented_out": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "commented_out"


def test_fp_exclusions_changelog_entry():
    """CHANGELOG.md 内の参照は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CHANGELOG.md",
        "detail": {"path": "old/removed/module.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "changelog_entry"


def test_fp_exclusions_short_field_name():
    """短いフィールド名は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "foo/bar"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "short_field_name"


def test_fp_exclusions_plugin_managed():
    """plugin origin のスキル参照は除外される。"""
    issue = {
        "type": "claudemd_phantom_ref",
        "file": "CLAUDE.md",
        "detail": {"name": "my-plugin-skill", "plugin_managed": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "plugin_managed"


def test_fp_exclusions_frontmatter_path():
    """frontmatter の paths/globs 内の参照は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": ".claude/rules/my-rule.md",
        "detail": {"path": "scripts/**/*.py", "in_frontmatter": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "frontmatter_path"


def test_fp_exclusions_example_snippet():
    """例示コード内の参照は除外される。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "example/path.py", "in_example": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "example_snippet"


def test_fp_exclusions_memory_index_only():
    """MEMORY.md のインデックス行は除外される。"""
    issue = {
        "type": "stale_memory",
        "file": "MEMORY.md",
        "detail": {"path": "some_topic", "memory_index_only": True},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "memory_index_only"


def test_fp_exclusions_tmp_path():
    """/tmp/ 配下の一時ファイルパスは FP として除外される（#339）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/tmp/ab_test.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "tmp_path"


def test_fp_exclusions_tmp_path_macos_private():
    """macOS の /private/tmp/ 配下も tmp_path として除外される（#339）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/private/tmp/scratch.json"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "tmp_path"


def test_fp_exclusions_tmp_path_var_folders():
    """macOS の /var/folders/ 配下も tmp_path として除外される（#339）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/var/folders/xy/abc/T/tmpfile"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "tmp_path"


def test_fp_exclusions_ssm_logical_path():
    """SSM 風の論理パス（絶対・拡張子なし）は FP として除外される（#339）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/docs-platform/strategy"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "logical_path"


def test_fp_exclusions_ssm_logical_path_deep():
    """多段の SSM 風論理パスも logical_path として除外される（#339）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/my-service/prod/api/secret-key"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "logical_path"


def test_fp_exclusions_real_absolute_file_not_excluded():
    """拡張子付きの正当な絶対ファイル参照は除外されない（#339 回帰ガード）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/Users/me/proj/scripts/lib/missing_module.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason is None


def test_fp_exclusions_home_claude_md_not_excluded():
    """~/.claude/ 配下の拡張子付き参照は logical_path 扱いしない（#339 回帰ガード）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/Users/me/.claude/rules/my-rule.md"},
    }
    reason = _should_exclude_fp(issue)
    assert reason is None


def test_fp_exclusions_extensionless_real_dir_not_logical_path():
    """拡張子なしでも実ファイルシステムルート配下のディレクトリ参照は logical_path にしない（#339 回帰ガード）。

    /Users/... のような実ファイルシステムルートは SSM 論理パスではないため
    logical_path として誤分類してはならない（他の FP 条件には掛かり得る）。
    """
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/Users/me/proj/scripts/lib"},
    }
    reason = _should_exclude_fp(issue)
    assert reason != "logical_path"


def test_fp_exclusions_extensionless_long_real_dir_not_excluded():
    """8文字以上のセグメントを含む実ルート配下の拡張子なしパスは一切除外されない（#339 回帰ガード）。"""
    issue = {
        "type": "stale_ref",
        "file": "MEMORY.md",
        "detail": {"path": "/Users/developer/projects/repository/scripts"},
    }
    reason = _should_exclude_fp(issue)
    assert reason is None


def test_fp_exclusions_tmp_path_classify_not_auto_fixable():
    """tmp_path / logical_path は classify_issue で auto_fixable に入らない（#339 統合）。"""
    from remediation import classify_issue

    for ref in ("/tmp/ab_test.py", "/docs-platform/strategy"):
        classified = classify_issue({
            "type": "stale_ref",
            "file": "MEMORY.md",
            "detail": {"path": ref},
        })
        assert classified["category"] == "fp_excluded"
        assert classified["confidence_score"] == 0.0


def test_fp_exclusions_known_fp_relative_logical_path():
    """相対の拡張子なし論理パス（data/bots/wheeling）は known_fp_pattern で除外される（#357）。

    絶対パスでないため logical_path に掛からず、末尾セグメントが 8 文字あるため
    short_field_name にも掛からず、これまで auto_fixable に landing していた既知 FP。
    known_fp_patterns カタログ（extensionless_logical_path）で塞ぐ。
    """
    issue = {
        "type": "stale_ref",
        "file": "/Users/me/.claude/projects/proj/memory/MEMORY.md",
        "detail": {"path": "data/bots/wheeling"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "known_fp_pattern"


def test_fp_exclusions_known_fp_generic_abbreviation():
    """汎用略語（API 等）も known_fp_pattern で除外される（#357）。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "SSM"},
    }
    reason = _should_exclude_fp(issue)
    assert reason == "known_fp_pattern"


def test_fp_exclusions_known_fp_classify_not_auto_fixable():
    """known_fp_pattern 該当は classify_issue で auto_fixable に入らない（#357 統合）。"""
    from remediation import classify_issue

    classified = classify_issue({
        "type": "stale_ref",
        "file": "/Users/me/.claude/projects/proj/memory/MEMORY.md",
        "detail": {"path": "data/bots/wheeling"},
    })
    assert classified["category"] == "fp_excluded"
    assert classified["confidence_score"] == 0.0
    assert classified["fp_exclusion_reason"] == "known_fp_pattern"


def test_fp_exclusions_known_fp_does_not_swallow_real_ref():
    """拡張子付きの正当な相対参照は known_fp_pattern で誤除外しない（#357 回帰ガード）。"""
    issue = {
        "type": "stale_ref",
        "file": "CLAUDE.md",
        "detail": {"path": "scripts/lib/missing_module.py"},
    }
    reason = _should_exclude_fp(issue)
    assert reason is None


def test_fp_exclusions_list_completeness():
    """FP_EXCLUSIONS に全15パターンが含まれている。"""
    assert len(FP_EXCLUSIONS) == 15
    expected = {
        "test_file", "archive_path", "external_url", "numeric_only",
        "code_block_ref", "frontmatter_path", "example_snippet",
        "commented_out", "changelog_entry", "memory_index_only",
        "plugin_managed", "short_field_name", "tmp_path", "logical_path",
        "known_fp_pattern",
    }
    assert set(FP_EXCLUSIONS) == expected
