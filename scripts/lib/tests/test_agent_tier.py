"""agent_tier（モデルティア適合ゲート）のテスト。

決定論・LLM 非依存。frontmatter の tier 宣言とモデル/effort 割り振りポリシーの
適合を検査する純関数 `check_agent_tier` と、env override 検査
`check_subagent_model_env_override` を検証する。

ポリシー（ティア↔model/effort の正典）:
    HEAD   → model=opus,   effort=xhigh
    HARD   → model=opus,   effort=high
    NORMAL → model=sonnet, effort=medium
    MECH   → model=haiku,  effort=無し（haiku は effort 非対応）
    REVIEW → model=fable,  effort=high
"""
import agent_tier


def _types(findings):
    return {f["type"] for f in findings}


# --- 正例（適合）: findings 無し ------------------------------------------


def test_head_opus_xhigh_is_clean():
    fm = {"name": "head-agent", "tier": "HEAD", "model": "opus", "effort": "xhigh"}
    assert agent_tier.check_agent_tier(fm) == []


def test_hard_opus_high_is_clean():
    fm = {"name": "hard-agent", "tier": "HARD", "model": "opus", "effort": "high"}
    assert agent_tier.check_agent_tier(fm) == []


def test_normal_sonnet_medium_is_clean():
    fm = {"name": "n", "tier": "NORMAL", "model": "sonnet", "effort": "medium"}
    assert agent_tier.check_agent_tier(fm) == []


def test_mech_haiku_no_effort_is_clean():
    # MECH は effort フィールドが無いのが適合
    fm = {"name": "m", "tier": "MECH", "model": "haiku"}
    assert agent_tier.check_agent_tier(fm) == []


def test_review_fable_high_is_clean():
    fm = {"name": "r", "tier": "REVIEW", "model": "fable", "effort": "high"}
    assert agent_tier.check_agent_tier(fm) == []


def test_tier_is_case_insensitive():
    fm = {"name": "n", "tier": "normal", "model": "sonnet", "effort": "medium"}
    assert agent_tier.check_agent_tier(fm) == []


# --- tier_model_mismatch ---------------------------------------------------


def test_tier_model_mismatch_review_with_opus():
    fm = {"name": "r", "tier": "REVIEW", "model": "opus", "effort": "high"}
    findings = agent_tier.check_agent_tier(fm)
    assert "tier_model_mismatch" in _types(findings)
    mm = next(f for f in findings if f["type"] == "tier_model_mismatch")
    assert mm["agent"] == "r"
    assert "fable" in mm["detail"]  # 期待モデルを detail に含める


def test_tier_model_mismatch_mech_with_sonnet():
    fm = {"name": "m", "tier": "MECH", "model": "sonnet"}
    assert "tier_model_mismatch" in _types(agent_tier.check_agent_tier(fm))


def test_model_absent_does_not_flag_mismatch():
    # model 未宣言（inherit）は mismatch にしない（FP 回避）
    fm = {"name": "n", "tier": "NORMAL", "effort": "medium"}
    assert "tier_model_mismatch" not in _types(agent_tier.check_agent_tier(fm))


# --- tier_effort_mismatch --------------------------------------------------


def test_tier_effort_mismatch_head_with_medium():
    fm = {"name": "h", "tier": "HEAD", "model": "opus", "effort": "medium"}
    findings = agent_tier.check_agent_tier(fm)
    assert "tier_effort_mismatch" in _types(findings)
    em = next(f for f in findings if f["type"] == "tier_effort_mismatch")
    assert "xhigh" in em["detail"]


def test_mech_with_effort_is_mismatch():
    # haiku は effort 非対応。effort フィールドがあれば違反
    fm = {"name": "m", "tier": "MECH", "model": "haiku", "effort": "high"}
    findings = agent_tier.check_agent_tier(fm)
    assert "tier_effort_mismatch" in _types(findings)


def test_effort_absent_not_flagged_for_non_mech():
    # effort 未宣言はセッション既定に委ねる正当な選択。mismatch にしない
    fm = {"name": "h", "tier": "HEAD", "model": "opus"}
    assert "tier_effort_mismatch" not in _types(agent_tier.check_agent_tier(fm))


# --- exact_id_pin ----------------------------------------------------------


def test_exact_id_pin_detected():
    fm = {"name": "h", "tier": "HEAD", "model": "claude-opus-4-8", "effort": "xhigh"}
    findings = agent_tier.check_agent_tier(fm)
    assert "exact_id_pin" in _types(findings)
    pin = next(f for f in findings if f["type"] == "exact_id_pin")
    assert "opus" in pin["detail"]  # 推奨エイリアスを提示


def test_exact_id_pin_does_not_produce_model_mismatch_when_tier_matches():
    # claude-opus-4-8 は HEAD の期待モデル opus に解決されるので mismatch は出ない
    fm = {"name": "h", "tier": "HEAD", "model": "claude-opus-4-8", "effort": "xhigh"}
    types = _types(agent_tier.check_agent_tier(fm))
    assert "tier_model_mismatch" not in types


def test_alias_model_not_flagged_as_pin():
    fm = {"name": "h", "tier": "HEAD", "model": "opus", "effort": "xhigh"}
    assert "exact_id_pin" not in _types(agent_tier.check_agent_tier(fm))


# --- missing_tier ----------------------------------------------------------


def test_missing_tier_when_absent():
    fm = {"name": "x", "model": "opus"}
    findings = agent_tier.check_agent_tier(fm)
    assert "missing_tier" in _types(findings)


def test_missing_tier_when_unknown_value():
    fm = {"name": "x", "tier": "TURBO", "model": "opus"}
    findings = agent_tier.check_agent_tier(fm)
    assert "missing_tier" in _types(findings)
    mt = next(f for f in findings if f["type"] == "missing_tier")
    assert "TURBO" in mt["detail"]


def test_missing_tier_suppresses_tier_dependent_checks():
    # tier が読めないと model/effort mismatch は判定できない → 出さない
    fm = {"name": "x", "model": "sonnet", "effort": "low"}
    types = _types(agent_tier.check_agent_tier(fm))
    assert "tier_model_mismatch" not in types
    assert "tier_effort_mismatch" not in types


# --- subagent_model_env_override ------------------------------------------


def test_env_override_detected_when_set():
    finding = agent_tier.check_subagent_model_env_override(
        env={"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}
    )
    assert finding is not None
    assert finding["type"] == "subagent_model_env_override"
    assert "haiku" in finding["detail"]


def test_env_override_none_when_unset():
    assert agent_tier.check_subagent_model_env_override(env={}) is None


def test_env_override_none_when_empty_string():
    assert agent_tier.check_subagent_model_env_override(
        env={"CLAUDE_CODE_SUBAGENT_MODEL": ""}
    ) is None


# --- 複合 ------------------------------------------------------------------


def test_multiple_findings_accumulate():
    # REVIEW なのに model=opus + effort=medium（両方ずれ）
    fm = {"name": "r", "tier": "REVIEW", "model": "opus", "effort": "medium"}
    types = _types(agent_tier.check_agent_tier(fm))
    assert {"tier_model_mismatch", "tier_effort_mismatch"} <= types


def test_findings_have_required_fields():
    fm = {"name": "r", "tier": "REVIEW", "model": "opus", "effort": "medium"}
    for f in agent_tier.check_agent_tier(fm):
        assert set(f) >= {"type", "agent", "detail", "severity"}
        assert f["severity"] in ("low", "medium", "high")
