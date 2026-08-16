"""reflect_apply_match の正規化・一致判定テスト（#475 §6.2）。"""
from pathlib import Path

import pytest

import reflect_apply_match as ram


# --- classify_file ---

class TestClassifyFile:
    def test_bullet_file(self):
        assert ram.classify_file(["# heading", "- item one", "- item two"]) == "bullet"

    def test_plain_file(self):
        assert ram.classify_file(["# heading", "本文の1行目です。", "本文の2行目です。"]) == "plain"

    def test_bullet_wins_with_single_bullet_line(self):
        """1行でも `- ` 始まりがあれば箇条書きファイル判定。"""
        assert ram.classify_file(["段落。", "- 唯一の箇条書き行"]) == "bullet"

    def test_indented_bullet_detected(self):
        assert ram.classify_file(["  - ネストした箇条書き"]) == "bullet"


# --- check_line_applied: bullet file ---

class TestCheckLineAppliedBulletFile:
    def test_match_with_bullet_prefix_and_whitespace(self, tmp_path):
        target = tmp_path / "rule.md"
        target.write_text("# rule\n\n- 既存の1行目\n- 対象の起草行そのもの\n", encoding="utf-8")
        result = ram.check_line_applied(target, "対象の起草行そのもの")
        assert result == {"matched": True, "reason": None}

    def test_match_draft_line_with_own_bullet_prefix(self, tmp_path):
        """draft_line 自体が `- ` 付きで渡されても正規化後一致すれば match。"""
        target = tmp_path / "rule.md"
        target.write_text("- 対象の起草行\n", encoding="utf-8")
        result = ram.check_line_applied(target, "- 対象の起草行")
        assert result["matched"] is True

    def test_no_match_when_line_absent(self, tmp_path):
        target = tmp_path / "rule.md"
        target.write_text("- 既存の1行目\n- 別の1行\n", encoding="utf-8")
        result = ram.check_line_applied(target, "書いていない行")
        assert result == {"matched": False, "reason": "no_match"}


# --- check_line_applied: plain sentence file ---

class TestCheckLineAppliedPlainFile:
    def test_match_plain_sentence_file(self, tmp_path):
        """箇条書きを一切使わないファイル（tdd-first.md 等の実例）でも一致判定できる。"""
        target = tmp_path / "tdd-first.md"
        target.write_text(
            "# TDD First\n\n実装前にテストを書く。「後でテスト」の合理化を許容しない。\n",
            encoding="utf-8",
        )
        result = ram.check_line_applied(target, "実装前にテストを書く。「後でテスト」の合理化を許容しない。")
        assert result["matched"] is True

    def test_no_bullet_stripping_in_plain_file(self, tmp_path):
        """素の文ファイルでは行頭 `- ` を剥がさない（前後空白除去のみ）。"""
        target = tmp_path / "plain.md"
        # ファイル中に `- ` 始まりの行が無いので plain 判定。行はハイフンで始まる散文。
        target.write_text("素の文です。-これは箇条書きではない\n", encoding="utf-8")
        result = ram.check_line_applied(target, "素の文です。-これは箇条書きではない")
        assert result["matched"] is True


# --- unknown line prefix ---

class TestUnknownLinePrefix:
    @pytest.mark.parametrize("draft_line", [
        "1. 番号付きの行",
        "- [ ] チェックボックスの行",
        "- [x] 完了チェックボックスの行",
        "> 引用行",
        "| 表 | の行 |",
    ])
    def test_unknown_prefix_returns_apply_unverified_reason(self, tmp_path, draft_line):
        """未知の行頭記号は「一致なし」でなく unknown_line_prefix を返す。"""
        target = tmp_path / "rule.md"
        target.write_text("- 何か既存の行\n", encoding="utf-8")
        result = ram.check_line_applied(target, draft_line)
        assert result == {"matched": False, "reason": "unknown_line_prefix"}


# --- file not found ---

class TestFileNotFound:
    def test_missing_file(self, tmp_path):
        target = tmp_path / "does-not-exist.md"
        result = ram.check_line_applied(target, "何かの行")
        assert result == {"matched": False, "reason": "file_not_found"}
