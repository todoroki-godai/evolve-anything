"""_build_growth_report のフェーズ昇格が human-source のみで駆動されることの保証（#431 提案3）。

機械ノイズ（Stop hook の source=hook/backfill）が 10 件あっても、human-confirmed が
閾値未満なら Structured Nurturing に昇格しないことを E2E（レンダリング結果）で assert する。
LLM 非依存（env fitness は skip_llm + mock で軽量化）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


@pytest.fixture
def _stub_growth_deps(monkeypatch, tmp_path):
    """_build_growth_report が呼ぶ依存を軽量 stub に差し替える（LLM 非依存）。"""
    import audit.orchestrator as orch

    # corrections: 機械ノイズ 10 + human 2（昇格条件 corrections>=10 を total では満たすが human では満たさない）
    machine = [{"source": "backfill", "correction_type": "stop"} for _ in range(10)]
    human = [{"source": "reflect_confirmed", "correction_type": "idiom"} for _ in range(2)]
    corrections = machine + human

    import telemetry_query
    monkeypatch.setattr(telemetry_query, "query_sessions",
                        lambda **k: [{"session_id": f"s{i}"} for i in range(60)])
    monkeypatch.setattr(telemetry_query, "query_corrections", lambda **k: corrections)

    import growth_journal
    monkeypatch.setattr(growth_journal, "count_crystallized_rules", lambda **k: 5)
    monkeypatch.setattr(growth_journal, "query_crystallizations", lambda **k: [])

    # env fitness を軽量 stub（coherence 0.5）
    _fitness_dir = orch.PLUGIN_ROOT / "scripts" / "rl" / "fitness"
    if str(_fitness_dir) not in sys.path:
        sys.path.insert(0, str(_fitness_dir))
    import environment
    monkeypatch.setattr(environment, "compute_environment_fitness",
                        lambda proj, skip_llm=False: {"overall": 0.5,
                                                      "axes": {"coherence": {"score": 0.5}}})

    # cache 書き込みを tmp へ隔離（実 DATA_DIR を汚さない）
    import growth_engine
    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path, raising=False)
    return corrections


def test_phase_not_promoted_on_machine_noise(_stub_growth_deps, tmp_path) -> None:
    import audit.orchestrator as orch

    proj = tmp_path / "evolve-anything"
    proj.mkdir()
    lines = orch._build_growth_report(proj, skip_llm=True)
    text = "\n".join(lines)

    # human=2 / total=12 の両方が表示される
    assert "2 (human)" in text
    assert "12 (total)" in text
    # sessions>=50 + total corrections>=10 + crystallized>=3 でも、human=2<10 のため
    # Structured Nurturing に昇格しない（Initial Nurturing のまま）。
    # 判定はフェーズ header 行（"**Phase:**" 行）で行う（Next Milestone のヒント文と区別）。
    phase_line = next(l for l in lines if l.startswith("**Phase:**"))
    assert "Initial Nurturing" in phase_line
    assert "Structured Nurturing" not in phase_line


def test_phase_promotes_when_human_corrections_sufficient(monkeypatch, tmp_path) -> None:
    import audit.orchestrator as orch
    import telemetry_query, growth_journal, growth_engine

    human = [{"source": "reflect_confirmed", "correction_type": "idiom"} for _ in range(10)]
    monkeypatch.setattr(telemetry_query, "query_sessions",
                        lambda **k: [{"session_id": f"s{i}"} for i in range(60)])
    monkeypatch.setattr(telemetry_query, "query_corrections", lambda **k: human)
    monkeypatch.setattr(growth_journal, "count_crystallized_rules", lambda **k: 5)
    monkeypatch.setattr(growth_journal, "query_crystallizations", lambda **k: [])
    _fitness_dir = orch.PLUGIN_ROOT / "scripts" / "rl" / "fitness"
    if str(_fitness_dir) not in sys.path:
        sys.path.insert(0, str(_fitness_dir))
    import environment
    monkeypatch.setattr(environment, "compute_environment_fitness",
                        lambda proj, skip_llm=False: {"overall": 0.5,
                                                      "axes": {"coherence": {"score": 0.5}}})
    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path, raising=False)

    proj = tmp_path / "evolve-anything"
    proj.mkdir()
    lines = orch._build_growth_report(proj, skip_llm=True)
    text = "\n".join(lines)
    assert "10 (human)" in text
    # human=10 で sessions>=50 + crystallized>=3 → Structured Nurturing に昇格
    phase_line = next(l for l in lines if l.startswith("**Phase:**"))
    assert "Structured Nurturing" in phase_line
