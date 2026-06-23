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


def _rec(uuid, days_ago, pj_id="-pj-foo-evolve-anything", input_tokens=0, cache_creation=0, cache_read=0):
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

    def test_pj_breakdown_resolves_slug(self, store, capsys):
        """--pj に pj_slug を渡しても解決される (TOP-N 表示の slug をコピペできる)。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("u1", 1, pj_id="-Users-foo-projects-anything", input_tokens=100),
        ])
        from fleet import main
        rc = main(["tokens", "--pj", "anything", "--by", "session"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "breakdown" in out
        assert "-Users-foo-projects-anything" in out

    def test_pj_breakdown_ambiguous_slug_lists_candidates(self, store, capsys):
        """slug が複数 pj_id に該当する場合は候補を出して非ゼロ。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("u1", 1, pj_id="-Users-foo-a-shared", input_tokens=100),
            _rec("u2", 1, pj_id="-Users-bar-b-shared", input_tokens=200),
        ])
        from fleet import main
        rc = main(["tokens", "--pj", "shared", "--by", "session"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "ambiguous" in err.lower() or "multiple" in err.lower()
        assert "-Users-foo-a-shared" in err
        assert "-Users-bar-b-shared" in err

    def test_pj_breakdown_not_found(self, store, capsys):
        """マッチしない場合は not found エラー。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        store.append_batch([
            _rec("u1", 1, pj_id="-pj-a", input_tokens=100),
        ])
        from fleet import main
        rc = main(["tokens", "--pj", "no-such-pj", "--by", "session"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "not found" in err.lower() or "no match" in err.lower()

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


class TestPjSlugDerivation:
    """#68: token_usage の pj_slug 化け（figma-to-code→code / sys-bots→bots）の根治。"""

    def test_parse_transcript_line_uses_cwd_for_slug(self, tmp_path):
        """cwd フィールドがあれば pj_id 末尾 split でなく cwd basename を採る。"""
        import token_usage_ingest as tui
        (tmp_path / "figma-to-code").mkdir()
        line = json.dumps({
            "uuid": "x1", "sessionId": "s",
            "cwd": str(tmp_path / "figma-to-code"),
            "message": {"role": "assistant", "usage": {"input_tokens": 1}},
        })
        rec = tui.parse_transcript_line(line, pj_id="-irrelevant-figma-to-code")
        assert rec is not None
        assert rec["pj_slug"] == "figma-to-code"  # 旧バグなら "code"

    def test_top_n_consumers_display_slug_decodes_hyphen(self, store, tmp_path):
        """旧 ingest が DB に書いた化け slug に依存せず、表示は pj_id から復元する。"""
        try:
            import duckdb  # noqa
        except ImportError:
            pytest.skip("duckdb not installed")
        pj = tmp_path / "sys-bots"
        pj.mkdir()
        pj_id = "-" + str(pj).lstrip("/").replace("/", "-")
        # DB には敢えて化けた slug "bots" を書く（旧 ingest 相当）
        store.append_batch([_rec("a1", 1, pj_id=pj_id, input_tokens=10_000)])
        import token_usage_query as tuq
        top = tuq.top_n_consumers(days=30, n=3)
        assert top, "top should not be empty"
        assert top[0]["pj_slug"] == "sys-bots"  # 化けた "bots" でない


class TestInjectTokenMetrics:
    """#68 回帰: pj_slug 修正後も TOKENS 列 join が basename 一致で正しく当たる。"""

    def test_basename_join_handles_collision(self, monkeypatch):
        """sys-bots が bots のトークンを誤って拾わず、figma-to-code も "--" にならない。"""
        import token_usage_query
        from fleet import FleetRow, STATUS_ENABLED, cli_tokens
        consumers = [
            {"pj_id": "-Users-x-updater-sys-bots", "pj_slug": "sys-bots",
             "tokens": 500, "cache_hit_pct": 90.0, "cache_reuse_factor": 9.0},
            {"pj_id": "-Users-x-tools-bots", "pj_slug": "bots",
             "tokens": 200, "cache_hit_pct": 80.0, "cache_reuse_factor": 4.0},
            {"pj_id": "-Users-x-updater-figma-to-code", "pj_slug": "figma-to-code",
             "tokens": 999, "cache_hit_pct": 95.0, "cache_reuse_factor": 20.0},
        ]
        monkeypatch.setattr(token_usage_query, "top_n_consumers", lambda days=30, n=10_000: consumers)
        rows = [
            FleetRow(pj_name="sys-bots", status=STATUS_ENABLED),
            FleetRow(pj_name="bots", status=STATUS_ENABLED),
            FleetRow(pj_name="figma-to-code", status=STATUS_ENABLED),
        ]
        cli_tokens._inject_token_metrics(rows)
        by_name = {r.pj_name: r for r in rows}
        assert by_name["sys-bots"].tokens_30d == 500   # bots の 200 を拾わない（衝突回避）
        assert by_name["bots"].tokens_30d == 200
        assert by_name["figma-to-code"].tokens_30d == 999  # "--" にならない
