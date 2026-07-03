"""session_store.query の project フィルタ（#136）。

`check_data_sufficiency` のデータ十分性集計が PJ フィルタなしで全 PJ を通していた
（旧 slug rl-anything の 8 万件が全 PJ の判定を支配）バグの読み側修正。query に
project 引数を追加し、当 PJ の session だけを数えられるようにする。

意味論は telemetry_query の `_filter_by_project(alias_aware=True)` に揃える:
  - project=None は全レコード（既存 caller の後方互換）
  - project 指定時は canonical_pj_slug で両辺を畳んで rename alias（rl-anything→
    evolve-anything）を回収
  - project 欠落（None）レコードは他 PJ 誤混入を避けるため strict に除外

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

import session_store  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _sess(sid: str, ts: str, project) -> dict:
    return {"session_id": sid, "timestamp": ts, "project": project}


def _point_module_at(tmp_path, monkeypatch) -> None:
    """query が読む解決先を tmp に向け、db は使わず jsonl のみを読ませる（#137 慣習）。

    SESSIONS_JSONL/SESSIONS_DB は #137 で module `__getattr__` 化されており、
    monkeypatch.setattr は「getattr の計算値」を元値として保存→teardown で実属性に
    pin してしまい後続テストの `__getattr__` を恒久 shadow する（xdist で発覚した
    汚染の根因）。隔離は `_DATA_DIR_OVERRIDE` 1 本で行う。
    """
    monkeypatch.setattr(session_store, "_DATA_DIR_OVERRIDE", tmp_path)


class TestQueryProjectFilter:
    def test_project_none_returns_all(self, tmp_path, monkeypatch):
        """project 未指定は全 PJ のレコードを返す（後方互換）。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("a", "2026-06-01T00:00:00+00:00", "proj-a"),
                _sess("b", "2026-06-02T00:00:00+00:00", "proj-b"),
            ],
        )
        recs = session_store.query()
        assert sorted(r["session_id"] for r in recs) == ["a", "b"]

    def test_project_scopes_to_matching(self, tmp_path, monkeypatch):
        """project 指定で当 PJ のレコードだけを返す。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("a1", "2026-06-01T00:00:00+00:00", "proj-a"),
                _sess("a2", "2026-06-02T00:00:00+00:00", "proj-a"),
                _sess("b1", "2026-06-03T00:00:00+00:00", "proj-b"),
            ],
        )
        recs = session_store.query(project="proj-a")
        assert sorted(r["session_id"] for r in recs) == ["a1", "a2"]

    def test_excludes_unknown_project_when_scoped(self, tmp_path, monkeypatch):
        """project 指定時、project 欠落（None）レコードは除外する（strict scope）。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("a1", "2026-06-01T00:00:00+00:00", "proj-a"),
                _sess("u1", "2026-06-02T00:00:00+00:00", None),
                {"session_id": "u2", "timestamp": "2026-06-03T00:00:00+00:00"},  # project キー欠落
            ],
        )
        recs = session_store.query(project="proj-a")
        assert [r["session_id"] for r in recs] == ["a1"]

    def test_alias_fold_recovers_legacy_slug(self, tmp_path, monkeypatch):
        """rename 旧 slug（rl-anything）は canonical fold で現 slug に回収される。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("legacy", "2026-06-01T00:00:00+00:00", "rl-anything"),
                _sess("current", "2026-06-02T00:00:00+00:00", "evolve-anything"),
                _sess("other", "2026-06-03T00:00:00+00:00", "atlas-breeaders"),
            ],
        )
        recs = session_store.query(project="evolve-anything")
        assert sorted(r["session_id"] for r in recs) == ["current", "legacy"]

    def test_since_and_project_combined(self, tmp_path, monkeypatch):
        """since と project を併用すると両方で絞る。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("old-a", "2026-05-01T00:00:00+00:00", "proj-a"),
                _sess("new-a", "2026-06-10T00:00:00+00:00", "proj-a"),
                _sess("new-b", "2026-06-11T00:00:00+00:00", "proj-b"),
            ],
        )
        recs = session_store.query(since="2026-06-01T00:00:00+00:00", project="proj-a")
        assert [r["session_id"] for r in recs] == ["new-a"]

    def test_limit_applies_after_project_filter(self, tmp_path, monkeypatch):
        """limit は project フィルタ後の件数に適用する。"""
        _point_module_at(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                _sess("a1", "2026-06-01T00:00:00+00:00", "proj-a"),
                _sess("b1", "2026-06-02T00:00:00+00:00", "proj-b"),
                _sess("a2", "2026-06-03T00:00:00+00:00", "proj-a"),
                _sess("a3", "2026-06-04T00:00:00+00:00", "proj-a"),
            ],
        )
        recs = session_store.query(project="proj-a", limit=2)
        # proj-a を timestamp 昇順で 2 件（b1 を挟まない）。
        assert [r["session_id"] for r in recs] == ["a1", "a2"]
