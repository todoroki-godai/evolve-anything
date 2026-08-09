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


def test_next_milestone_lines_structured_notes_mature_pending():
    """Structured Nurturing は #379 Step 4 で Mature 判定が保留中である旨を明示する。

    crystallized_rules 計測が growth-journal harness 削除で失われたため、Mature への
    「requires: crystallized_rules >= 10」等の到達不能な条件文言は出してはいけない。
    """
    from audit.sections_milestone import _next_milestone_lines
    from growth_engine import Phase

    text = "\n".join(_next_milestone_lines(Phase.STRUCTURED_NURTURING))
    # 到達不能な「requires: crystallized_rules >= 10」形式の条件文言は出さない
    # （crystallized_rules 自体への言及は「廃止した」旨の説明としてなら許容する）。
    assert "requires:" not in text
    assert "379" in text or "保留" in text


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
    """cache が無くても telemetry から軽算出して Next Milestone を出す（沈黙しない）。

    #379 Step 4: crystallized_rules ソース（growth_journal）削除後は
    detect_phase_no_crystallization（human corrections ベース）で判定する。
    """
    import growth_engine
    import telemetry_query
    from audit.sections_milestone import build_next_milestone_section

    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path / "no-cache")
    # telemetry を空に向ける（sessions/corrections 0 → BOOTSTRAP）
    monkeypatch.setattr(telemetry_query, "query_sessions", lambda **k: [])
    monkeypatch.setattr(telemetry_query, "query_corrections", lambda **k: [])

    proj = tmp_path / "fresh"
    proj.mkdir()
    section = build_next_milestone_section(proj)
    assert section is not None
    text = "\n".join(section)
    assert "Next Milestone" in text
    # sessions 0 → BOOTSTRAP → 次は Initial Nurturing
    assert "Initial Nurturing" in text


def test_next_milestone_section_no_cache_uses_human_corrections(tmp_path, monkeypatch):
    """crystallized_rules 抜きでも sessions+human corrections が揃えば Structured へ判定する。"""
    import growth_engine
    import telemetry_query
    from audit.sections_milestone import build_next_milestone_section
    from growth_engine import STRUCTURED_SESSIONS_TARGET, STRUCTURED_CORRECTIONS_TARGET

    monkeypatch.setattr(growth_engine, "_DATA_DIR", tmp_path / "no-cache-2")
    monkeypatch.setattr(
        telemetry_query, "query_sessions",
        lambda **k: [{} for _ in range(STRUCTURED_SESSIONS_TARGET)],
    )
    monkeypatch.setattr(
        telemetry_query, "query_corrections",
        lambda **k: [
            {"source": "reflect_confirmed"} for _ in range(STRUCTURED_CORRECTIONS_TARGET)
        ],
    )

    proj = tmp_path / "structured-fresh"
    proj.mkdir()
    section = build_next_milestone_section(proj)
    assert section is not None
    text = "\n".join(section)
    assert "Structured Nurturing" in text
    assert "requires:" not in text
