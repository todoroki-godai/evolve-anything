"""discover/patterns.py の detect_constraint_decay テスト (TDD)。"""
import json
import time
from pathlib import Path

import pytest


# モジュールパス解決
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.discover.patterns import detect_constraint_decay


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, records: list) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )


def _make_sessions(tmp_path: Path, sessions: list) -> Path:
    """sessions.jsonl を tmp_path に作成して Path を返す。"""
    p = tmp_path / "sessions.jsonl"
    _write_jsonl(p, sessions)
    return p


def _make_corrections(tmp_path: Path, corrections: list) -> Path:
    """corrections.jsonl を tmp_path に作成して Path を返す。"""
    p = tmp_path / "corrections.jsonl"
    _write_jsonl(p, corrections)
    return p


# ---------------------------------------------------------------------------
# テストケース 1: decay あり（後半 30% に correction 集中）
# ---------------------------------------------------------------------------

class TestConstraintDecayDetected:
    """セッション後半 30% に correction が集中する場合 WARNING を返す。"""

    def test_high_decay_rate_returns_warning(self, tmp_path):
        # session "s1": 10 ターン
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        # turn_index 7, 8, 9 (> 7.0, つまり turn_ratio > 0.7) に 3 件
        # turn_index 1 に 1 件（前半）
        # session_decay_rate = 3/4 = 0.75 > 0.3 → WARNING
        corrections = [
            {"session_id": "s1", "turn_index": 1},
            {"session_id": "s1", "turn_index": 7},
            {"session_id": "s1", "turn_index": 8},
            {"session_id": "s1", "turn_index": 9},
        ]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        results = detect_constraint_decay(sp, cp, decay_threshold=0.3)

        assert len(results) == 1
        r = results[0]
        assert r["type"] == "constraint_decay"
        assert r["session_id"] == "s1"
        assert r["severity"] == "WARNING"
        assert r["decay_rate"] > 0.3
        assert "message" in r


# ---------------------------------------------------------------------------
# テストケース 2: decay なし（correction が均等分布）
# ---------------------------------------------------------------------------

class TestConstraintDecayNotDetected:
    """correction が均等分布の場合は WARNING を返さない。"""

    def test_uniform_distribution_no_warning(self, tmp_path):
        # session "s1": 10 ターン
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        # turn_index 1, 3, 5 → turn_ratio 0.1, 0.3, 0.5 → すべて <= 0.7
        # session_decay_rate = 0/3 = 0.0 <= 0.3 → 結果なし
        corrections = [
            {"session_id": "s1", "turn_index": 1},
            {"session_id": "s1", "turn_index": 3},
            {"session_id": "s1", "turn_index": 5},
        ]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        results = detect_constraint_decay(sp, cp, decay_threshold=0.3)

        # WARNING なし（INFO も含めゼロ or WARNING なし）
        warnings = [r for r in results if r["severity"] == "WARNING"]
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# テストケース 3: corrections なし
# ---------------------------------------------------------------------------

class TestNoCorrections:
    """corrections.jsonl が空の場合は空リストを返す。"""

    def test_empty_corrections_returns_empty(self, tmp_path):
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        corrections = []
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        results = detect_constraint_decay(sp, cp)

        assert results == []

    def test_no_decay_rate_field_when_empty(self, tmp_path):
        """corrections が空なら decay_rate フィールドを持つレコードがないこと。"""
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, [])

        results = detect_constraint_decay(sp, cp)

        assert not any("decay_rate" in r for r in results)


# ---------------------------------------------------------------------------
# テストケース 4: sessions.jsonl 不在
# ---------------------------------------------------------------------------

class TestSessionsFileMissing:
    """sessions.jsonl が存在しない場合は例外なく空リストを返す。"""

    def test_missing_sessions_returns_empty(self, tmp_path):
        corrections = [{"session_id": "s1", "turn_index": 8}]
        sp = tmp_path / "sessions.jsonl"  # 作成しない
        cp = _make_corrections(tmp_path, corrections)

        results = detect_constraint_decay(sp, cp)

        assert results == []


# ---------------------------------------------------------------------------
# テストケース 5: max_turn_index == 0 → ZeroDivision なし
# ---------------------------------------------------------------------------

class TestMaxTurnIndexZero:
    """max_turn_index == 0 のセッションは skip して ZeroDivision が起きないこと。"""

    def test_zero_max_turn_index_skipped(self, tmp_path):
        sessions = [{"session_id": "s1", "max_turn_index": 0}]
        corrections = [{"session_id": "s1", "turn_index": 0}]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        # ZeroDivisionError が起きないこと
        results = detect_constraint_decay(sp, cp)
        # ZeroDivision ガードでスキップされるため結果なし
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# テストケース 6: 古い sessions.jsonl（30日超）→ 空リスト
# ---------------------------------------------------------------------------

class TestOldSessionsFile:
    """30日超の sessions.jsonl は mtime フィルタでスキップされ空リストを返す。"""

    def test_old_sessions_returns_empty(self, tmp_path):
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        corrections = [
            {"session_id": "s1", "turn_index": 8},
            {"session_id": "s1", "turn_index": 9},
        ]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        # mtime を 31 日前に設定
        old_mtime = time.time() - (31 * 24 * 3600)
        import os
        os.utime(sp, (old_mtime, old_mtime))

        results = detect_constraint_decay(sp, cp)

        assert results == []


# ---------------------------------------------------------------------------
# テストケース 7: session_id が sessions.jsonl に存在しない correction はスキップ
# ---------------------------------------------------------------------------

class TestUnknownSessionId:
    """sessions.jsonl に存在しない session_id の correction は無視される。"""

    def test_unknown_session_id_skipped(self, tmp_path):
        sessions = [{"session_id": "s1", "max_turn_index": 10}]
        # s2 は sessions に存在しない
        corrections = [
            {"session_id": "s2", "turn_index": 8},
            {"session_id": "s2", "turn_index": 9},
        ]
        sp = _make_sessions(tmp_path, sessions)
        cp = _make_corrections(tmp_path, corrections)

        results = detect_constraint_decay(sp, cp)

        # s2 はインデックスにないのでスキップ → s1 の corrections は 0 件 → 結果なし
        warnings = [r for r in results if r["severity"] == "WARNING"]
        assert len(warnings) == 0
