"""dogfood.ingest_check のユニットテスト（#496 Layer 1, 35秒テスト移設）。

合成 transcript で check_ingest_e2e のロジック（wall time / DB size / rows assertion）を
高速検証する。実 PJ E2E は bin/evolve-dogfood-gate --layer all の実機1周で別途確認する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


from dogfood import ingest_check  # noqa: E402

try:
    from utterance_archive import store as ustore  # noqa: E402
    _HAS_DUCKDB = ustore.HAS_DUCKDB
except Exception:
    _HAS_DUCKDB = False

pytestmark = pytest.mark.skipif(not _HAS_DUCKDB, reason="DuckDB 未インストール")


def _user_line(text, ts, sid, uuid, cwd="/Users/x/tools/evolve-anything"):
    return json.dumps(
        {
            "type": "user", "uuid": uuid, "sessionId": sid, "timestamp": ts,
            "cwd": cwd, "message": {"role": "user", "content": text},
        }
    )


def _make_pj(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    pj = root / "-Users-x-tools-evolve-anything"
    pj.mkdir(parents=True)
    (pj / "s1.jsonl").write_text(
        _user_line("発話A", "2026-06-01T00:00:00Z", "s1", "u1") + "\n"
        + _user_line("発話B", "2026-06-01T00:01:00Z", "s1", "u2") + "\n",
        encoding="utf-8",
    )
    return pj


def test_check_ingest_e2e_passes(tmp_path: Path):
    pj = _make_pj(tmp_path)
    res = ingest_check.check_ingest_e2e(pj_dir=pj, db_dir=tmp_path / "db")
    assert res["status"] == "pass", res
    assert res["rows"] == 2
    assert res["elapsed_sec"] < 60.0
    assert res["db_size_bytes"] > 0


def test_check_ingest_e2e_fails_on_empty_pj(tmp_path: Path):
    root = tmp_path / "projects"
    pj = root / "-Users-x-tools-evolve-anything"
    pj.mkdir(parents=True)
    (pj / "empty.jsonl").write_text("", encoding="utf-8")
    res = ingest_check.check_ingest_e2e(pj_dir=pj, db_dir=tmp_path / "db")
    # 発話ゼロ → 抽出バグ扱いで fail
    assert res["status"] == "fail"


def test_find_real_pj_returns_none_when_absent(monkeypatch, tmp_path):
    # 隔離 HOME 配下に evolve-anything PJ が無い → None
    assert ingest_check.find_real_rl_anything_pj() is None
