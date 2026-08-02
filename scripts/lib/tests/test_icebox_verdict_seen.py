"""icebox_verdict_seen の既読ストアテスト（#352）。

一度提示した (issue番号, 評価値ハッシュ) は再提示しない。評価値が変わればハッシュが変わり
再提示対象に戻る。既読ストアは daily_review.py の物理キー集合パターンを踏襲する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import icebox_verdict_seen as seen  # noqa: E402


def _verdict(number=1, lane="met", value=10, reason="x satisfied", closed_at=None):
    return {
        "number": number,
        "lane": lane,
        "value": value,
        "reason": reason,
        "closed_at": closed_at,
    }


class TestFingerprint:
    def test_same_verdict_same_fingerprint(self):
        v1 = _verdict()
        v2 = _verdict()
        assert seen.verdict_fingerprint(v1) == seen.verdict_fingerprint(v2)

    def test_different_lane_different_fingerprint(self):
        v1 = _verdict(lane="met")
        v2 = _verdict(lane="archive_candidate")
        assert seen.verdict_fingerprint(v1) != seen.verdict_fingerprint(v2)

    # ── B5 回帰テスト: value/reason だけの変化では fingerprint は変わらない ──
    # （毎日値が動く token_usage.total_tokens 等の metric で永久に再通知され続ける
    # バグの根治。同じ lane/closed_at のまま value/reason だけ変わっても再提示しない）
    def test_different_value_same_fingerprint(self):
        v1 = _verdict(value=10)
        v2 = _verdict(value=20)
        assert seen.verdict_fingerprint(v1) == seen.verdict_fingerprint(v2)

    def test_different_reason_same_fingerprint(self):
        v1 = _verdict(reason="A")
        v2 = _verdict(reason="B")
        assert seen.verdict_fingerprint(v1) == seen.verdict_fingerprint(v2)

    # ── 再凍結（closed_at が変わる）は再提示対象に戻す ──────────────
    def test_different_closed_at_different_fingerprint(self):
        v1 = _verdict(closed_at="2026-01-01T00:00:00Z")
        v2 = _verdict(closed_at="2026-07-01T00:00:00Z")
        assert seen.verdict_fingerprint(v1) != seen.verdict_fingerprint(v2)


class TestReadEmpty:
    def test_missing_file_returns_empty_set(self, tmp_path):
        assert seen.read_seen_keys(tmp_path / "no-such.jsonl") == set()


class TestRecordAndFilter:
    def test_record_then_filter_excludes_seen(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v = _verdict(number=42)
        result = seen.record_seen([v], path=path)
        assert result["written"] == 1
        assert result["dry_run"] is False

        keys = seen.read_seen_keys(path)
        assert seen.verdict_key(v) in keys

        unseen = seen.filter_unseen([v], keys)
        assert unseen == []

    def test_changed_value_does_not_reappear(self, tmp_path):
        """B5 回帰テスト: value だけ変わっても（lane/closed_at 不変なら）再提示しない。"""
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v1 = _verdict(number=42, value=10)
        seen.record_seen([v1], path=path)
        keys = seen.read_seen_keys(path)

        v2 = _verdict(number=42, value=20)  # 値だけ変わった
        unseen = seen.filter_unseen([v2], keys)
        assert unseen == []

    def test_recloses_after_reopen_reappears(self, tmp_path):
        """再凍結（closed_at が変わる）は fingerprint が変わり再提示対象へ戻る。"""
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v1 = _verdict(number=42, closed_at="2026-01-01T00:00:00Z")
        seen.record_seen([v1], path=path)
        keys = seen.read_seen_keys(path)

        v2 = _verdict(number=42, closed_at="2026-07-01T00:00:00Z")  # 再凍結
        unseen = seen.filter_unseen([v2], keys)
        assert unseen == [v2]

    def test_dry_run_writes_nothing(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        result = seen.record_seen([_verdict()], path=path, dry_run=True)
        assert result["written"] == 1
        assert result["dry_run"] is True
        assert not path.exists()

    def test_duplicate_record_is_idempotent(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v = _verdict()
        seen.record_seen([v], path=path)
        result = seen.record_seen([v], path=path)
        assert result["written"] == 0  # 既に既読なので追記なし
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_filter_unseen_keeps_multiple_new_and_drops_seen(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        v1 = _verdict(number=1)
        v2 = _verdict(number=2)
        seen.record_seen([v1], path=path)
        keys = seen.read_seen_keys(path)
        unseen = seen.filter_unseen([v1, v2], keys)
        assert unseen == [v2]

    # ── P2: 書込失敗を成功と報告しない（verify-after-write） ────────────
    def test_write_failure_reports_zero_written_and_warns(self, tmp_path, monkeypatch, capsys):
        """append_jsonl は OSError を stderr に出すだけで例外化しない（サイレント失敗）。
        record_seen はその失敗を「written」に反映し stderr でも警告する。"""
        path = tmp_path / "icebox_verdict_seen.jsonl"

        def _noop_write_raw(filepath, record):
            pass  # 書込が失敗した状況を模擬（ファイルに一切触れない）

        import rl_common

        monkeypatch.setattr(rl_common, "store_write_raw", _noop_write_raw)
        result = seen.record_seen([_verdict()], path=path)
        assert result["written"] == 0
        assert "確認できませんでした" in capsys.readouterr().err

    def test_write_success_still_reports_written(self, tmp_path):
        path = tmp_path / "icebox_verdict_seen.jsonl"
        result = seen.record_seen([_verdict()], path=path)
        assert result["written"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
