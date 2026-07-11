"""Agent モデルティア適合ゲートの observability セクション生成。

`agent_tier.check_agent_tier`（純関数）を全 agent の frontmatter に適用し、
モデル割り振りポリシー（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）への不適合を
audit に surface する。検出ロジックは `agent_tier.py` に置き、ここは「audit に載せる
行」への整形だけを担う（sections_agent.py と同契約）。

agent 定義はグローバル（~/.claude/agents/）+ PJ 固有（<project>/.claude/agents/）の
両方を `scan_agents(project_root=...)` で走査する。

**advisory のみ・auto-fix しない**（agent は protected・#185）。スコア重み非関与。

contract: 評価対象 agent が 0 個かつ env override も無ければ None（沈黙）。
1 個以上あれば不適合が無くても「✓ 評価したが不適合なし」を返す（silence != evaluated）。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_quality import scan_agents
from agent_tier import check_agent_tier, check_subagent_model_env_override

from .advisory import build_advisory_section

# monkeypatch 用に元関数への参照を保持（テストが env を注入して呼べるように）。
_orig_env_check = check_subagent_model_env_override


def build_agent_tier_section(project_dir: Path) -> Optional[List[str]]:
    """エージェントのモデルティア不適合を audit に surface する。"""

    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        try:
            agents = scan_agents(project_root=proj)
        except Exception:
            return None
        env_finding = check_subagent_model_env_override()
        return {"agents": agents, "env_finding": env_finding}

    def applicable(data: Dict[str, Any]) -> bool:
        return bool(data["agents"]) or data["env_finding"] is not None

    def render(data: Dict[str, Any]) -> List[str]:
        agents = data["agents"]
        env_finding = data["env_finding"]

        # per-agent findings を「実不適合（mismatch/pin）」と「missing_tier」に分ける。
        mismatch_lines: List[str] = []
        missing_tier_agents: List[str] = []
        for agent in agents:
            for f in check_agent_tier(agent.frontmatter or {}):
                if f["type"] == "missing_tier":
                    missing_tier_agents.append(f["agent"])
                else:
                    mismatch_lines.append(
                        f"- {f['agent']}: [{f['type']}] {f['detail']}"
                    )

        has_hard = bool(mismatch_lines)
        lines: List[str] = []

        if has_hard:
            lines.append(
                "⚠ エージェントのモデルティア割り振りに不適合。"
                "`/evolve-anything:agent-brushup` で確認（advisory・auto-fix しません）:"
            )
            lines.extend(mismatch_lines)
        elif not missing_tier_agents and env_finding is None:
            return [
                f"✓ 評価したがティア不適合なし（{len(agents)} エージェントを評価）",
            ]

        # missing_tier は件数集約で ℹ（tier 宣言は新規慣習ゆえ per-agent 列挙は過剰）。
        if missing_tier_agents:
            names = ", ".join(sorted(missing_tier_agents))
            lines.append(
                f"ℹ {len(missing_tier_agents)} エージェントに tier 宣言なし"
                f"（frontmatter に `tier: HEAD|HARD|NORMAL|MECH|REVIEW`）: {names}"
            )

        if env_finding is not None:
            lines.append(f"ℹ {env_finding['detail']}")

        return lines

    return build_advisory_section(
        project_dir,
        title="Agent Model Tier (ティア適合)",
        compute=compute,
        applicable=applicable,
        render=render,
    )
