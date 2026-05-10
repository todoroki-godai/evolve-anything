"""fleet tokens 拡張テスト — TOKENS_30d/CACHE_HIT 列 + tokens サブコマンド。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    import token_usage_store as tus
    monkeypatch.setattr(tus, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tus, "USAGE_DB", tmp_path / "token_usage.db")
    monkeypatch.setattr(tus, "USAGE_JSONL", tmp_path / "token_usage.jsonl")
    return tus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rec(uuid, days_ago, pj_id="-pj-foo-rl-anything", input_tokens=0, cache_creation=0, cache_read=0):
    return {
        "uuid": uuid,
        "ts": (_now() - timedelta(days=days_ago)).isoformat(),
        "pj_id": pj_id,
        "pj_slug": pj_id.rstrip("-").split("-")[-1],
        "session_id": "s1",
        "parent_uuid": None,
        "is_sidechain": False,
        "model": "claude-sonnet-4-7",
        "role": "assistant",
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "web_search_requests": 0,
        "web_fetch_requests": 0,
    }


class TestStatusTokenColumns:
    def test_status_includes_token_columns(self):
        """空 DB なら TOKENS_30d / CACHE_HIT は -- 表示。"""
        from fleet import FleetRow, STATUS_ENABLED, format_status_table
        rows = [FleetRow(pj_name="pj-a", status=STATUS_ENABLED)]
        out = format_status_table(rows)
        assert "TOKENS_30d" in out
        assert "CACHE_HIT" in out
        assert "--" in out

    def test_status_with_data(self):
        """tokens_30d/cache_hit_pct を入れると整形される。"""
        from fleet import FleetRow, STATUS_ENABLED, format_status_table
        rows = [
            FleetRow(
                pj_name="pj-a", status=STATUS_ENABLED,
                tokens_30d=8_400_000, cache_hit_pct=72.0,
            )
        ]
        out = format_status_table(rows)
        assert "8.4M" in out
        assert "72%" in out


class TestTokensSubcommand:
    def test_summary_empty_db_prints_backfill_hint(self, store, capsys):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        from fleet import main
        rc = main(["tokens"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "backfill" in captured.err.lower()

    def test_summary_with_data_shows_top3(self, store, capsys):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("a1", 1, pj_id="-pj-a", input_tokens=5_000_000, cache_read=3_000_000, cache_creation=1_000_000),
            _rec("b1", 1, pj_id="-pj-b", input_tokens=2_000_000),
            _rec("c1", 1, pj_id="-pj-c", input_tokens=500_000),
        ])
        from fleet import main
        rc = main(["tokens"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "TOP 3 consumers" in out
        # pj_slug 末尾セグメント "a"/"b"/"c"
        assert " a" in out or "-pj-a" in out

    def test_pj_breakdown(self, store, capsys):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("u1", 1, pj_id="-pj-a", input_tokens=100),
            _rec("u2", 2, pj_id="-pj-a", input_tokens=200),
        ])
        from fleet import main
        rc = main(["tokens", "--pj=-pj-a", "--by", "session"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "-pj-a" in out
        assert "breakdown" in out

    def test_json_schema_keys_stable(self, store, capsys):
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("a1", 1, pj_id="-pj-a", input_tokens=5_000_000, cache_read=3_000_000, cache_creation=1_000_000),
        ])
        from fleet import main
        rc = main(["tokens", "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out.strip())
        assert "top" in payload
        assert payload["top"], "top should not be empty"
        first = payload["top"][0]
        # キー安定性 (この順序は API 契約)
        for key in ("pj_id", "tokens", "cache_hit_pct"):
            assert key in first, f"missing key: {key}"
