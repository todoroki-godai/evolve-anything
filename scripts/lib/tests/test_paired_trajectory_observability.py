"""paired trajectory observability builder のテスト（#15）。

決定論・LLM 非依存。silence != evaluated の境界と、usage/sessions 集約 → paired デルタ
surface を検証する。telemetry ストアは monkeypatch で in-memory に差し替える
（DATA_DIR / duckdb に依存しない）。monkeypatch は import した module を直接参照する
（文字列ターゲット回避 — 既知 pitfall 準拠）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import sections_paired as sp  # noqa: E402
from audit import usage as usage_mod  # noqa: E402


def _make_skill(project_dir: Path, name: str) -> None:
    skill_dir = project_dir / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# " + name, encoding="utf-8")


def _patch_inputs(monkeypatch, *, usage, sessions) -> None:
    monkeypatch.setattr(usage_mod, "load_usage_data",
                        lambda days=30, project_root=None: usage)
    import telemetry_query
    monkeypatch.setattr(telemetry_query, "query_sessions",
                        lambda project=None: sessions)


def test_silent_when_usage_empty(tmp_path: Path, monkeypatch) -> None:
    """usage が空（テレメトリ未蓄積）→ None（沈黙）。"""
    _patch_inputs(monkeypatch, usage=[], sessions=[])
    assert sp.build_paired_trajectory_section(tmp_path) is None


def test_clean_when_no_pairing(tmp_path: Path, monkeypatch) -> None:
    """usage はあるが paired バケットが組めない → 「評価したが対照対象なし」を出す。"""
    # review が常に ship と同居 → without 腕が無く paired 不成立。
    usage = [
        {"skill_name": "ship", "session_id": "s1"},
        {"skill_name": "review", "session_id": "s1"},
        {"skill_name": "ship", "session_id": "s2"},
        {"skill_name": "review", "session_id": "s2"},
    ]
    sessions = [
        {"session_id": "s1", "error_count": 0},
        {"session_id": "s2", "error_count": 1},
    ]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    section = sp.build_paired_trajectory_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "対照対象なし" in body or "該当なし" in body


def test_surfaces_regression(tmp_path: Path, monkeypatch) -> None:
    """同一 task-type で skill 有のほうが一発成功率が低い → regression を ⚠ surface。"""
    usage = [
        {"skill_name": "ship", "session_id": "s1"},
        {"skill_name": "flaky", "session_id": "s1"},
        {"skill_name": "ship", "session_id": "s2"},
        {"skill_name": "flaky", "session_id": "s2"},
        {"skill_name": "ship", "session_id": "s3"},
        {"skill_name": "ship", "session_id": "s4"},
    ]
    sessions = [
        {"session_id": "s1", "error_count": 2},
        {"session_id": "s2", "error_count": 1},
        {"session_id": "s3", "error_count": 0},
        {"session_id": "s4", "error_count": 0},
    ]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    section = sp.build_paired_trajectory_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "**flaky**" in body
    # evidence に有/無の一発成功率が添う。
    assert "有=0" in body or "有 0" in body or "0.00" in body


def test_registered_in_observability_contract() -> None:
    from audit.observability import _OBSERVABILITY_BUILDERS
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "paired_trajectory" in keys


def test_collect_observability_includes_paired(tmp_path: Path, monkeypatch) -> None:
    """collect_observability 経由でも paired セクションが出る（両経路の単一ソース確認）。"""
    from audit.observability import collect_observability

    usage = [
        {"skill_name": "ship", "session_id": "s1"},
        {"skill_name": "flaky", "session_id": "s1"},
        {"skill_name": "ship", "session_id": "s2"},
        {"skill_name": "flaky", "session_id": "s2"},
        {"skill_name": "ship", "session_id": "s3"},
        {"skill_name": "ship", "session_id": "s4"},
    ]
    sessions = [
        {"session_id": "s1", "error_count": 2},
        {"session_id": "s2", "error_count": 1},
        {"session_id": "s3", "error_count": 0},
        {"session_id": "s4", "error_count": 0},
    ]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    result = collect_observability(tmp_path)
    assert "paired_trajectory" in result
    assert any("Paired Trajectory" in line for line in result["paired_trajectory"])
