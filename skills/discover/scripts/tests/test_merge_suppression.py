#!/usr/bin/env python3
"""merge suppression 関連関数のユニットテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import discover


class TestLoadMergeSuppression:
    """load_merge_suppression のユニットテスト。"""

    @pytest.fixture
    def patch_suppression(self, tmp_path):
        """SUPPRESSION_FILE を tmp_path にパッチする。"""
        suppression_file = tmp_path / "discover-suppression.jsonl"
        with mock.patch.object(discover, "SUPPRESSION_FILE", suppression_file):
            yield suppression_file

    def test_empty_file(self, patch_suppression):
        """空ファイルの場合は空セットを返す。"""
        patch_suppression.write_text("")
        result = discover.load_merge_suppression()
        assert result == set()

    def test_file_not_exists(self, patch_suppression):
        """ファイルが存在しない場合は空セットを返す。"""
        # patch_suppression は存在しないファイルパス（書き込みしていない）
        result = discover.load_merge_suppression()
        assert result == set()

    def test_merge_entries_only(self, patch_suppression):
        """type: merge エントリのみが返される。"""
        lines = [
            json.dumps({"pattern": "alpha::beta", "type": "merge"}),
            json.dumps({"pattern": "some-error-pattern"}),
            json.dumps({"pattern": "gamma::delta", "type": "merge"}),
        ]
        patch_suppression.write_text("\n".join(lines))
        result = discover.load_merge_suppression()
        assert result == {"alpha::beta", "gamma::delta"}

    def test_discover_entries_excluded(self, patch_suppression):
        """type なしエントリ（discover 用）は含まれない。"""
        lines = [
            json.dumps({"pattern": "error-pattern-1"}),
            json.dumps({"pattern": "error-pattern-2", "type": "discover"}),
        ]
        patch_suppression.write_text("\n".join(lines))
        result = discover.load_merge_suppression()
        assert result == set()

    def test_duplicate_keys(self, patch_suppression):
        """重複エントリがあっても set なので一意になる。"""
        lines = [
            json.dumps({"pattern": "alpha::beta", "type": "merge"}),
            json.dumps({"pattern": "alpha::beta", "type": "merge"}),
        ]
        patch_suppression.write_text("\n".join(lines))
        result = discover.load_merge_suppression()
        assert result == {"alpha::beta"}
        assert len(result) == 1


class TestAddMergeSuppression:
    """add_merge_suppression のユニットテスト。"""

    @pytest.fixture
    def patch_suppression(self, tmp_path):
        """SUPPRESSION_FILE と DATA_DIR を tmp_path にパッチする。"""
        data_dir = tmp_path / "rl-anything"
        data_dir.mkdir()
        suppression_file = data_dir / "discover-suppression.jsonl"
        with mock.patch.object(discover, "DATA_DIR", data_dir), \
             mock.patch.object(discover, "SUPPRESSION_FILE", suppression_file):
            yield suppression_file

    def test_adds_sorted_pair(self, patch_suppression):
        """スキル名がソートされて :: 結合で記録される。"""
        discover.add_merge_suppression("beta", "alpha")
        content = patch_suppression.read_text()
        record = json.loads(content.strip())
        assert record["pattern"] == "alpha::beta"
        assert record["type"] == "merge"

    def test_already_sorted(self, patch_suppression):
        """既にソート順の場合もそのまま記録される。"""
        discover.add_merge_suppression("alpha", "beta")
        content = patch_suppression.read_text()
        record = json.loads(content.strip())
        assert record["pattern"] == "alpha::beta"

    def test_reverse_order_normalized(self, patch_suppression):
        """逆順入力が正規化される。"""
        discover.add_merge_suppression("zebra", "apple")
        content = patch_suppression.read_text()
        record = json.loads(content.strip())
        assert record["pattern"] == "apple::zebra"

    def test_write_failure_no_exception(self, tmp_path):
        """書き込み失敗時に例外を送出しない。"""
        # 読み取り専用ディレクトリを使って書き込み失敗をシミュレート
        bad_path = tmp_path / "nonexistent" / "deep" / "path" / "file.jsonl"
        with mock.patch.object(discover, "DATA_DIR", tmp_path / "nonexistent" / "deep" / "path"), \
             mock.patch.object(discover, "SUPPRESSION_FILE", bad_path), \
             mock.patch("builtins.open", side_effect=OSError("Permission denied")):
            # 例外が送出されないことを確認
            discover.add_merge_suppression("a", "b")

    def test_appends_to_existing(self, patch_suppression):
        """既存ファイルに追記される。"""
        patch_suppression.write_text(
            json.dumps({"pattern": "existing-error"}) + "\n"
        )
        discover.add_merge_suppression("skill-x", "skill-y")
        lines = patch_suppression.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[1])["pattern"] == "skill-x::skill-y"


class TestLoadSuppressionListExcludesMerge:
    """load_suppression_list が type: merge エントリを除外するテスト。"""

    @pytest.fixture
    def patch_suppression(self, tmp_path):
        """SUPPRESSION_FILE を tmp_path にパッチする。"""
        suppression_file = tmp_path / "discover-suppression.jsonl"
        with mock.patch.object(discover, "SUPPRESSION_FILE", suppression_file):
            yield suppression_file

    def test_excludes_merge_entries(self, patch_suppression):
        """type: merge エントリは load_suppression_list に含まれない。"""
        lines = [
            json.dumps({"pattern": "error-pattern"}),
            json.dumps({"pattern": "alpha::beta", "type": "merge"}),
            json.dumps({"pattern": "another-error"}),
        ]
        patch_suppression.write_text("\n".join(lines))
        result = discover.load_suppression_list()
        assert "error-pattern" in result
        assert "another-error" in result
        assert "alpha::beta" not in result

    def test_type_none_included(self, patch_suppression):
        """type 未指定エントリは load_suppression_list に含まれる。"""
        lines = [
            json.dumps({"pattern": "some-pattern"}),
        ]
        patch_suppression.write_text("\n".join(lines))
        result = discover.load_suppression_list()
        assert "some-pattern" in result
