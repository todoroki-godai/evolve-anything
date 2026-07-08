"""エージェントのモデルティア適合ゲート（決定論・LLM 非依存）。

agent の frontmatter を読み、モデル割り振りポリシー（ティア↔model/effort の正典）
への適合を検査して advisory findings を返す。**auto-fix はしない**。agent は
protected（人間承認必須・#185）ため、このゲートは flag するだけで実ファイルは
編集しない。

ポリシー（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）は model-routing rule の
正典（HEAD=opus/xhigh, HARD=opus/high, NORMAL=sonnet/medium, MECH=haiku/effort無し,
REVIEW=fable/high）を単一ソースとして写す。

tier 宣言は **frontmatter の `tier:` キー**で行う。CC の `claude plugin validate`
は未知の frontmatter キー（`tier:`）を許容することを実プラグイン agent での検証で
確認済み（sidecar registry は不要）。

exact model ID pin の判定は `agent_quality_catalog`（#449 既存検出）の
`EXACT_MODEL_ID_PATTERN` / `MODEL_ALIASES` を再利用する。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional

from agent_quality_catalog import EXACT_MODEL_ID_PATTERN, MODEL_ALIASES

# ティア↔model/effort の正典。effort=None は「effort フィールドが無いのが適合」の意。
TIER_POLICY: Dict[str, Dict[str, Optional[str]]] = {
    "HEAD": {"model": "opus", "effort": "xhigh"},
    "HARD": {"model": "opus", "effort": "high"},
    "NORMAL": {"model": "sonnet", "effort": "medium"},
    "MECH": {"model": "haiku", "effort": None},
    "REVIEW": {"model": "fable", "effort": "high"},
}

SUBAGENT_MODEL_ENV = "CLAUDE_CODE_SUBAGENT_MODEL"

# exact ID → 推奨エイリアスの解決に使うエイリアス群（アルファ順に走査）。
_ALIAS_ORDER = ("opus", "sonnet", "haiku", "fable")


def _effective_alias(model_str: str) -> Optional[str]:
    """model 文字列を「実効エイリアス」に解決する。

    - エイリアス（opus/sonnet/haiku/fable/inherit）→ そのまま小文字化。
    - exact ID（claude-opus-4-8 等）→ 含まれるエイリアス語に解決（claude-opus-4-8 → opus）。
    - inherit / 解決不能 → None（mismatch 判定の対象外にする）。
    """
    lower = model_str.strip().lower()
    if lower == "inherit":
        return None
    if lower in MODEL_ALIASES:
        return lower
    if EXACT_MODEL_ID_PATTERN.match(model_str.strip()):
        for alias in _ALIAS_ORDER:
            if alias in lower:
                return alias
    return None


def check_agent_tier(frontmatter: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """1 agent の frontmatter dict を検査し advisory findings のリストを返す。

    findings の各要素: ``{"type", "agent", "detail", "severity"}``（check_quality の
    issues と同形）。findings が空 = 適合。

    検出タイプ:
    - ``missing_tier``: tier 宣言が無い / 未知の tier 値（tier 依存チェックは抑止）
    - ``exact_id_pin``: model がエイリアスでなく exact ID
    - ``tier_model_mismatch``: 宣言 tier と実効モデルが不一致
    - ``tier_effort_mismatch``: 宣言 tier と effort が不一致（MECH は effort 有りが違反）
    """
    findings: List[Dict[str, Any]] = []
    fm = frontmatter or {}
    name = str(fm.get("name") or "?")

    tier_raw = fm.get("tier")
    tier = str(tier_raw).strip().upper() if tier_raw not in (None, "") else None

    model_raw = fm.get("model")
    model_str = str(model_raw).strip() if model_raw not in (None, "") else None
    effort_raw = fm.get("effort")
    effort = str(effort_raw).strip().lower() if effort_raw not in (None, "") else None

    # exact ID pin は tier に依存せず常に検査（#449 既存検出と同基準）。
    if model_str is not None:
        lower = model_str.lower()
        if lower not in MODEL_ALIASES and EXACT_MODEL_ID_PATTERN.match(model_str):
            recommended = _effective_alias(model_str) or "sonnet"
            findings.append({
                "type": "exact_id_pin",
                "agent": name,
                "detail": (
                    f"model: {model_str!r} は exact ID pin — "
                    f"推奨エイリアス: {recommended!r}（silent stale の原因・model-routing）"
                ),
                "severity": "medium",
            })

    # tier が無い / 未知 → tier 依存チェックは判定不能ゆえ missing_tier のみ返す。
    if tier is None or tier not in TIER_POLICY:
        detail = (
            "tier 宣言が無い（frontmatter に `tier: HEAD|HARD|NORMAL|MECH|REVIEW`）"
            if tier is None
            else f"未知の tier 値 {tier_raw!r}（有効: HEAD/HARD/NORMAL/MECH/REVIEW）"
        )
        findings.append({
            "type": "missing_tier",
            "agent": name,
            "detail": detail,
            "severity": "low",
        })
        return findings

    policy = TIER_POLICY[tier]
    expected_model = policy["model"]
    expected_effort = policy["effort"]

    # model mismatch: model 未宣言（inherit）は FP 回避のため対象外。
    if model_str is not None:
        actual_alias = _effective_alias(model_str)
        if actual_alias is not None and actual_alias != expected_model:
            findings.append({
                "type": "tier_model_mismatch",
                "agent": name,
                "detail": (
                    f"tier:{tier} は model:{expected_model} を期待するが "
                    f"model:{model_str!r}（実効 {actual_alias!r}）"
                ),
                "severity": "medium",
            })

    # effort mismatch:
    #   - MECH（expected None）: effort フィールドがあれば違反（haiku は effort 非対応）。
    #   - その他: effort 宣言があり期待値と異なれば違反。未宣言はセッション既定に委ねる
    #     正当な選択なので対象外（FP 回避）。
    if expected_effort is None:
        if effort is not None:
            findings.append({
                "type": "tier_effort_mismatch",
                "agent": name,
                "detail": (
                    f"tier:{tier} は effort 非対応（haiku）だが effort:{effort!r} が宣言されている"
                ),
                "severity": "medium",
            })
    else:
        if effort is not None and effort != expected_effort:
            findings.append({
                "type": "tier_effort_mismatch",
                "agent": name,
                "detail": (
                    f"tier:{tier} は effort:{expected_effort} を期待するが effort:{effort!r}"
                ),
                "severity": "medium",
            })

    return findings


def check_subagent_model_env_override(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """環境変数 CLAUDE_CODE_SUBAGENT_MODEL が設定されていれば warning finding を返す。

    この env が非空だと **全 subagent の frontmatter model を実行時に上書き**する
    （解決順 env > 起動時 > frontmatter > session）。ティア適合ゲートが読む
    frontmatter の model 宣言が無効化されるため、設定中は advisory で surface する。

    env=None のときは実環境 os.environ を参照する。
    """
    source = env if env is not None else os.environ
    value = source.get(SUBAGENT_MODEL_ENV)
    if not value or not str(value).strip():
        return None
    return {
        "type": "subagent_model_env_override",
        "agent": "(env)",
        "detail": (
            f"{SUBAGENT_MODEL_ENV}={value!r} が設定されており、全 subagent の frontmatter "
            f"model を実行時に上書きします（ティア宣言が無効化される）。試運転が終わったら解除を検討"
        ),
        "severity": "low",
    }
