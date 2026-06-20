"""query_sessions の cross-dir union + read 層 slug 別名テスト（#45 ① 残課題 ⒜）。

telemetry_query.query_sessions は multiview / paired / missed-skill / growth_narrative が
使う**第2の session reader**。PR1（#469 / bb83ae7）が outcome 系の reader を
`read_session_records_union`（cross-dir）に張替えた一方、query_sessions の本体
（`_query_sessions_via_store`）は `session_store.query()` ＝**単一 DATA_DIR 内の union のみ**
（db + 未 ingest jsonl）で cross-dir 未対応のまま残っていた（PR1 の partial fix 残り・
[[pitfall_copied_parse_convention_partial_fix]]）。

本テストは:
  - query_sessions が canonical + legacy/plugins-data を cross-dir union する
  - PJ rename（rl-anything→evolve-anything）の legacy を read 層 slug 別名で回収する
    （cross-dir union 単独では現 slug filter が旧 slug 行を弾くため回収ゼロ・PR2 の罠）
  - 他 PJ の legacy は当 PJ に誤帰属しない
  - 兄弟 dir を作らなければ canonical のみ（実 home を読まない hermetic）
  - `_filter_by_project` の既定は exact-match のまま（usage/errors の PR2 ループ＝
    accept_slug ごとの exact 呼び出しを二重カウントさせない）

iter_read_data_dirs が canonical.parent から候補を導出するため、canonical を
``tmp/evolve-anything`` にして兄弟 ``tmp/rl-anything`` を作るだけで hermetic に検証できる。
決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parents[1] / "lib"
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import session_store  # noqa: E402
import telemetry_query  # noqa: E402
from telemetry_query.helpers import _filter_by_project  # noqa: E402


requires_duckdb = pytest.mark.skipif(
    not session_store.HAS_DUCKDB, reason="duckdb が無い環境（HAS_DUCKDB=False では単一 dir fallback）"
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _session(sid: str, ts: str, project: str | None = "evolve-anything", **extra) -> dict:
    rec = {"session_id": sid, "timestamp": ts, "project": project}
    rec.update(extra)
    return rec


def _point_session_store(monkeypatch, canonical: Path) -> None:
    """session_store の DATA_DIR を canonical に向ける（union の既定 canonical 解決）。"""
    monkeypatch.setattr(session_store, "DATA_DIR", canonical)
    monkeypatch.setattr(session_store, "SESSIONS_DB", canonical / "sessions.db")
    monkeypatch.setattr(session_store, "SESSIONS_JSONL", canonical / "sessions.jsonl")


@pytest.fixture
def canonical(tmp_path, monkeypatch):
    c = tmp_path / "evolve-anything"
    c.mkdir(parents=True, exist_ok=True)
    _point_session_store(monkeypatch, c)
    return c


class TestQuerySessionsCrossDirUnion:
    @requires_duckdb
    def test_unions_canonical_and_legacy(self, canonical, tmp_path):
        """canonical の s1 と legacy 兄弟 dir の s2 を union して両方返す。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(canonical / "sessions.jsonl", [_session("s1", "2026-06-01T00:00:00+00:00")])
        _write_jsonl(legacy / "sessions.jsonl", [_session("s2", "2026-06-02T00:00:00+00:00")])

        result = telemetry_query.query_sessions(project="evolve-anything")
        assert sorted(r["session_id"] for r in result) == ["s1", "s2"]

    @requires_duckdb
    def test_legacy_rl_anything_attributed_to_evolve_anything(self, canonical, tmp_path):
        """legacy が旧 slug ``project='rl-anything'`` でも当 PJ として回収する（read 層別名）。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(canonical / "sessions.jsonl", [_session("cur", "2026-06-01T00:00:00+00:00")])
        # legacy 行は rename 前の旧 slug でタグ付けされている。
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("old", "2026-06-02T00:00:00+00:00", project="rl-anything")],
        )
        result = telemetry_query.query_sessions(project="evolve-anything")
        assert sorted(r["session_id"] for r in result) == ["cur", "old"]

    @requires_duckdb
    def test_other_pj_legacy_not_attributed(self, canonical, tmp_path):
        """他 PJ（bots）の legacy 行は当 PJ に誤帰属しない。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(canonical / "sessions.jsonl", [_session("mine", "2026-06-01T00:00:00+00:00")])
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("theirs", "2026-06-02T00:00:00+00:00", project="bots")],
        )
        result = telemetry_query.query_sessions(project="evolve-anything")
        assert [r["session_id"] for r in result] == ["mine"]

    @requires_duckdb
    def test_hermetic_tmp_only_reads_canonical(self, canonical):
        """兄弟 dir を作らなければ canonical のみ（実 home の legacy を読まない）。"""
        _write_jsonl(canonical / "sessions.jsonl", [_session("s1", "2026-06-01T00:00:00+00:00")])
        result = telemetry_query.query_sessions(project="evolve-anything")
        assert [r["session_id"] for r in result] == ["s1"]

    @requires_duckdb
    def test_include_unknown_null_project_cross_dir(self, canonical, tmp_path):
        """include_unknown=True で project=None の legacy 行も含める（cross-dir）。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(canonical / "sessions.jsonl", [_session("mine", "2026-06-01T00:00:00+00:00")])
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("nullproj", "2026-06-02T00:00:00+00:00", project=None)],
        )
        result = telemetry_query.query_sessions(project="evolve-anything", include_unknown=True)
        assert sorted(r["session_id"] for r in result) == ["mine", "nullproj"]
        # include_unknown=False では null project を除外する。
        result2 = telemetry_query.query_sessions(project="evolve-anything")
        assert [r["session_id"] for r in result2] == ["mine"]

    @requires_duckdb
    def test_canonical_wins_on_duplicate_key(self, canonical, tmp_path):
        """同一 (session_id, timestamp) が canonical と legacy 両方にあれば canonical 優先。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(
            canonical / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00", error_count=0)],
        )
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("s1", "2026-06-01T00:00:00+00:00", project="rl-anything", error_count=9)],
        )
        result = telemetry_query.query_sessions(project="evolve-anything")
        assert len(result) == 1
        assert result[0]["error_count"] == 0  # canonical 優先

    @requires_duckdb
    def test_since_filter_inclusive_boundary(self, canonical, tmp_path):
        """since（包含 >=）境界は cross-dir union 後も維持される。"""
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        _write_jsonl(
            canonical / "sessions.jsonl",
            [
                _session("old", "2026-05-01T00:00:00+00:00"),
                _session("boundary", "2026-06-01T00:00:00+00:00"),
            ],
        )
        _write_jsonl(
            legacy / "sessions.jsonl",
            [_session("new-legacy", "2026-06-10T00:00:00+00:00", project="rl-anything")],
        )
        result = telemetry_query.query_sessions(
            project="evolve-anything", since="2026-06-01T00:00:00+00:00"
        )
        # since=境界そのものは包含（>=）。
        assert sorted(r["session_id"] for r in result) == ["boundary", "new-legacy"]


class TestFilterByProjectAliasOptIn:
    """_filter_by_project の既定は exact-match、alias_aware=True でのみ別名解決。"""

    def test_default_is_exact_match(self):
        """既定（alias_aware 省略）では rl-anything は evolve-anything に一致しない。

        usage/errors は PR2 で accept_slug ごとに exact 呼び出しするループ設計のため、
        ここで別名を効かせると no-duckdb fallback 経路で二重カウントする。
        """
        records = [
            {"session_id": "a", "project": "evolve-anything"},
            {"session_id": "b", "project": "rl-anything"},
        ]
        got = _filter_by_project(records, "evolve-anything")
        assert [r["session_id"] for r in got] == ["a"]

    def test_alias_aware_folds_legacy(self):
        """alias_aware=True では rl-anything を evolve-anything に畳んで一致させる。"""
        records = [
            {"session_id": "a", "project": "evolve-anything"},
            {"session_id": "b", "project": "rl-anything"},
            {"session_id": "c", "project": "bots"},
        ]
        got = _filter_by_project(records, "evolve-anything", alias_aware=True)
        assert sorted(r["session_id"] for r in got) == ["a", "b"]

    def test_alias_aware_include_unknown(self):
        """alias_aware=True + include_unknown で project=None も含める。"""
        records = [
            {"session_id": "a", "project": "evolve-anything"},
            {"session_id": "b", "project": None},
            {"session_id": "c", "project": "bots"},
        ]
        got = _filter_by_project(records, "evolve-anything", include_unknown=True, alias_aware=True)
        assert sorted(r["session_id"] for r in got) == ["a", "b"]

    def test_alias_aware_none_project_returns_all(self):
        """project=None なら全件（alias_aware は無関係）。"""
        records = [{"session_id": "a", "project": "x"}, {"session_id": "b", "project": "y"}]
        got = _filter_by_project(records, None, alias_aware=True)
        assert len(got) == 2
