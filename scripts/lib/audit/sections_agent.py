"""Agent 編成の observability セクション生成（Issue #326）。

sections.py / sections_eval.py と同じ observability builder 契約
（`(project_dir) -> Optional[List[str]]`）を満たす。検出ロジックは `agent_team.py`
に置き、ここは「audit に載せる行」への整形だけを担う。

agent 定義はグローバル（~/.claude/agents/）+ PJ 固有（<project>/.claude/agents/）の
両方を `scan_agents(project_root=...)` で走査する。eval_saturation が DATA_DIR の
環境グローバルを読むのと同様、builder が project_dir を起点に環境グローバルへ手を
伸ばすのは許容パターン。

contract: エージェントが 2 個未満（編成という概念が成立しない）なら None を返し、
2 個以上あれば編成ギャップが無くても「✓ 評価したが編成ギャップなし」を返す
（silence != evaluated）。
"""
from pathlib import Path
from typing import Any, List, Optional

from agent_quality import scan_agents
from agent_team import analyze_agent_team

from .advisory import build_advisory_section


def build_agent_team_section(project_dir: Path) -> Optional[List[str]]:
    """エージェント編成ギャップ（役割重複・孤立）を audit に surface する。"""

    def compute(proj: Path) -> Optional[List[Any]]:
        try:
            return scan_agents(project_root=proj)
        except Exception:
            return None

    def applicable(agents: List[Any]) -> bool:
        # エージェントが 1 個以下 → 「編成」が成立しない PJ。対象外。
        return len(agents) >= 2

    def render(agents: List[Any]) -> List[str]:
        result = analyze_agent_team(agents)
        if not result.has_gap:
            return [
                f"✓ 評価したが編成ギャップなし（{result.total_agents} エージェントを評価）",
            ]

        lines: List[str] = []
        if result.role_overlaps:
            # 役割重複は実際の編成問題。⚠ で agent-brushup を促し、孤立も同じブロックに併記する。
            lines.append(
                "⚠ エージェント編成に改善余地。"
                "`/evolve-anything:agent-brushup` で役割整理・編成見直しを検討:"
            )
            for ov in result.role_overlaps:
                lines.append(
                    f"- 役割重複: {ov.agent_a} / {ov.agent_b} (Jaccard {ov.similarity:.2f})"
                )
            for name in result.isolated:
                lines.append(f"- 孤立: {name}（他エージェントから未参照）")
        else:
            # 孤立のみ。design-review / doc-writer のようなユーザー直接起動型の専門家は
            # ルーターから参照されず孤立判定されるが設計上正常。⚠「改善余地」は過剰なので
            # ℹ に下げ、直接起動型なら正常である旨を明示して誤解を防ぐ（整理可否は人間判断）。
            lines.append(
                "ℹ 他エージェントから未参照のエージェントあり（ユーザー直接起動型なら正常）。"
                "ルーター統合や整理が要るかは `/evolve-anything:agent-brushup` で確認:"
            )
            for name in result.isolated:
                lines.append(f"- {name}: 他エージェントから未参照")
        return lines

    return build_advisory_section(
        project_dir,
        title="Agent Team (編成ギャップ)",
        compute=compute,
        applicable=applicable,
        render=render,
    )
