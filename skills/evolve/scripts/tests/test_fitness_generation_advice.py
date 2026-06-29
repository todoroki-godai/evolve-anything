"""Step 2 fitness 生成提案の structural 抑制判定（#105）。

`has_fitness: false` でも fitness_evolution が structural に「貯まらない」PJ
（skill_evolve 未採点 + 提案も構造的に出ない）では fitness 生成が空振りになるため、
Step 2 の MUST AskUserQuestion を抑制すべき。fitness_evolution の
`one_liner`「このPJでは fitness は使わない設計。対応不要」と矛盾しないようにする。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))

from evolve import fitness_generation_advice  # noqa: E402


def _result(*, has_fitness=False, fe=None, env_tier="medium",
            skill_evolve=None, discover=None):
    phases = {"fitness": {"has_fitness": has_fitness}}
    if fe is not None:
        phases["fitness_evolution"] = fe
    if skill_evolve is not None:
        phases["skill_evolve"] = skill_evolve
    if discover is not None:
        phases["discover"] = discover
    return {"env_tier": env_tier, "phases": phases}


def test_suppress_when_skill_evolve_not_scored_and_no_proposals():
    """structural_reason=skill_evolve_not_scored + 提案ゼロ → suppress=True。"""
    fe = {
        "status": "insufficient_data",
        "structural_reason": "skill_evolve_not_scored",
        "next_action": "このPJでは fitness は使わない設計。対応不要",
    }
    advice = fitness_generation_advice(
        _result(fe=fe, skill_evolve={"high_suitability": 0, "medium_suitability": 0},
                discover={"matched_skills": []})
    )
    assert advice["suppress"] is True
    assert advice["reason"] == "skill_evolve_not_scored"
    assert advice["note"]


def test_no_suppress_when_proposals_available():
    """同じ structural_reason でも現 run に skill 提案があれば母集団は貯まる → 抑制しない。"""
    fe = {
        "status": "insufficient_data",
        "structural_reason": "skill_evolve_not_scored",
    }
    advice = fitness_generation_advice(
        _result(fe=fe, skill_evolve={"high_suitability": 2, "medium_suitability": 0},
                discover={"matched_skills": []})
    )
    assert advice["suppress"] is False
    assert advice["reason"] is None


def test_no_suppress_when_discover_matched_skills():
    """discover が matched_skills を持つ場合も提案ありとみなし抑制しない。"""
    fe = {
        "status": "insufficient_data",
        "structural_reason": "skill_evolve_not_scored",
    }
    advice = fitness_generation_advice(
        _result(fe=fe, skill_evolve={"high_suitability": 0, "medium_suitability": 0},
                discover={"matched_skills": ["my-skill"]})
    )
    assert advice["suppress"] is False


def test_structural_reason_read_from_details_fallback():
    """top-level に structural_reason が無くても details 配下から拾える（#559 隔離）。"""
    fe = {
        "status": "insufficient_data",
        "details": {"structural_reason": "skill_evolve_not_scored"},
    }
    advice = fitness_generation_advice(
        _result(fe=fe, skill_evolve={"high_suitability": 0, "medium_suitability": 0},
                discover={"matched_skills": []})
    )
    assert advice["suppress"] is True
    assert advice["reason"] == "skill_evolve_not_scored"


def test_suppress_when_env_tier_small():
    """structural シグナルが無くても env_tier=small は生成効果が薄い見込み → suppress。"""
    advice = fitness_generation_advice(_result(env_tier="small"))
    assert advice["suppress"] is True
    assert advice["reason"] == "env_tier_small"
    assert advice["note"]


def test_no_suppress_when_has_fitness_true():
    """既に固有 fitness がある PJ ではそもそも生成提案が出ない → suppress=False。"""
    advice = fitness_generation_advice(_result(has_fitness=True, env_tier="small"))
    assert advice["suppress"] is False
    assert advice["reason"] is None


def test_no_suppress_for_plain_medium_pj_without_structural_signal():
    """structural_reason 無し・env_tier=medium の通常 PJ は従来どおり生成提案を出す。"""
    advice = fitness_generation_advice(_result(env_tier="medium"))
    assert advice["suppress"] is False
    assert advice["reason"] is None
    assert advice["note"] is None


def test_skill_evolve_not_scored_takes_precedence_over_env_tier():
    """structural_reason 抑制は env_tier より優先（reason は skill_evolve_not_scored）。"""
    fe = {
        "status": "insufficient_data",
        "structural_reason": "skill_evolve_not_scored",
    }
    advice = fitness_generation_advice(
        _result(fe=fe, env_tier="small",
                skill_evolve={"high_suitability": 0, "medium_suitability": 0},
                discover={"matched_skills": []})
    )
    assert advice["suppress"] is True
    assert advice["reason"] == "skill_evolve_not_scored"
