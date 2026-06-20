"""標準 audit に Next Milestone を常時出す（#52-2・決定論・LLM 非依存）。

フル growth report は重い（環境 fitness 計算）ため、Next Milestone（次フェーズ到達条件）
だけを軽量サブセットとして growth=False の標準実行でも出す。phase は growth-state cache を
優先し、無ければ telemetry から軽算出する（fitness/LLM は呼ばない）。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_next_milestone_lines_for_each_phase():
    """_next_milestone_lines は各 phase で次フェーズ条件を返す。"""
    from audit.sections_milestone import _next_milestone_lines
    from growth_engine import Phase

    boot = "\n".join(_next_milestone_lines(Phase.BOOTSTRAP))
    assert "Next phase" in boot
    assert "Initial Nurturing" in boot

    mature = "\n".join(_next_milestone_lines(Phase.MATURE_OPERATION))
    assert "最終フェーズ" in mature


def test_next_milestone_section_uses_cache(tmp_path, monkeypatch):
    """growth-state cache があれば fitness 計算なしで phase を解決して Next Milestone を出す。"""
    import growth_engine
    from audit.sections_milestone import build_next_milestone_section

    # cache を tmp に向ける
    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path)
    proj = tmp_path / "myproj"
    proj.mkdir()
    growth_engine.update_cache(
        proj.resolve().name,
        growth_engine.Phase.INITIAL_NURTURING,
        0.5,
        {"sessions_count": 20},
    )

    section = build_next_milestone_section(proj)
    assert section is not None
    text = "\n".join(section)
    assert "Next Milestone" in text
    assert "Structured Nurturing" in text


def test_next_milestone_section_no_cache_falls_back_to_telemetry(tmp_path, monkeypatch):
    """cache が無くても telemetry から軽算出して Next Milestone を出す（沈黙しない）。"""
    import growth_engine
    import telemetry_query
    from audit import sections_milestone
    from audit.sections_milestone import build_next_milestone_section

    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path / "no-cache")
    # telemetry を空に向ける（sessions/corrections 0 → BOOTSTRAP）
    monkeypatch.setattr(telemetry_query, "query_sessions", lambda **k: [])
    monkeypatch.setattr(telemetry_query, "query_corrections", lambda **k: [])
    monkeypatch.setattr(sections_milestone, "_count_crystallized_safe", lambda name: 0)

    proj = tmp_path / "fresh"
    proj.mkdir()
    section = build_next_milestone_section(proj)
    assert section is not None
    text = "\n".join(section)
    assert "Next Milestone" in text
    # sessions 0 → BOOTSTRAP → 次は Initial Nurturing
    assert "Initial Nurturing" in text
