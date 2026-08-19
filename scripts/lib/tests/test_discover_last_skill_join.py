"""discover.runner._fetch_corrections_with_last_skill（#478）の単体テスト。

pitfall_candidates 検出・instruction_violation 検出の共通入口。両検出器は
`query_corrections` の生データではなく本関数経由の（`last_skill` 補完済み）
corrections を使う（design-before-fanout: 同型 join を2箇所で独立実装しない）。
"""
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

import telemetry_query
from discover.runner import _fetch_corrections_with_last_skill


def test_fills_last_skill_from_usage_join(monkeypatch):
    monkeypatch.setattr(
        telemetry_query,
        "query_corrections",
        lambda **kw: [{"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z", "last_skill": None}],
    )
    monkeypatch.setattr(
        telemetry_query,
        "query_usage",
        lambda **kw: [
            {"skill_name": "evolve", "session_id": "s1", "ts": "2026-08-15T14:00:00Z", "outcome": "success"},
        ],
    )
    result = _fetch_corrections_with_last_skill("evolve-anything")
    assert result[0]["last_skill"] == "evolve"


def test_preserves_existing_last_skill(monkeypatch):
    monkeypatch.setattr(
        telemetry_query,
        "query_corrections",
        lambda **kw: [{"session_id": "s1", "timestamp": "2026-08-15T15:00:00Z", "last_skill": "hook-written"}],
    )
    monkeypatch.setattr(telemetry_query, "query_usage", lambda **kw: [])
    result = _fetch_corrections_with_last_skill("evolve-anything")
    assert result[0]["last_skill"] == "hook-written"


def test_project_scope_is_forwarded_to_both_queries(monkeypatch):
    """陽性対照: project フィルタが query_corrections / query_usage 両方に伝播すること
    （意味を変えない配線であり、フィルタが外れて全PJ混入する回帰を防ぐ）。"""
    seen = {}

    def _fake_corrections(**kw):
        seen["corrections_project"] = kw.get("project")
        return []

    def _fake_usage(**kw):
        seen["usage_project"] = kw.get("project")
        return []

    monkeypatch.setattr(telemetry_query, "query_corrections", _fake_corrections)
    monkeypatch.setattr(telemetry_query, "query_usage", _fake_usage)
    _fetch_corrections_with_last_skill("my-project")
    assert seen == {"corrections_project": "my-project", "usage_project": "my-project"}
