#!/usr/bin/env python3
"""ワークフロー統計分析スクリプト

workflows.jsonl からスキル別のワークフロー統計を算出し JSON で出力する。
optimizer / scorer / generate-fitness が参照する共通データソース。

使用方法:
    python3 workflow_analysis.py                          # 基本統計
    python3 workflow_analysis.py --min-workflows 5        # 最小5回
    python3 workflow_analysis.py --hints                  # mutation ヒント生成
    python3 workflow_analysis.py --for-fitness             # fitness 統合用出力
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path.home() / ".claude" / "rl-anything"
WORKFLOWS_PATH = DATA_DIR / "workflows.jsonl"
OUTPUT_PATH = DATA_DIR / "workflow_stats.json"


def load_workflows(path: Path) -> List[Dict[str, Any]]:
    """workflows.jsonl を読み込む。存在しない/空の場合は空リストを返す。"""
    if not path.exists():
        print(
            f"警告: {path} が見つかりません。空の統計を出力します。",
            file=sys.stderr,
        )
        return []

    workflows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                workflows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not workflows:
        print(
            f"警告: {path} にワークフローが含まれていません。空の統計を出力します。",
            file=sys.stderr,
        )

    return workflows


def compress_pattern(steps: List[Dict[str, Any]]) -> str:
    """ステップリストから抽象パターンを生成。連続同一エージェントを圧縮する。

    例: [Explore, Explore, Explore, Plan] -> "Explore -> Plan"
    """
    if not steps:
        return ""

    agents = []
    for step in steps:
        tool = step.get("tool", "")
        # "Agent:Explore" -> "Explore", "Agent:general-purpose" -> "general-purpose"
        if ":" in tool:
            agent = tool.split(":", 1)[1]
        else:
            agent = tool
        agents.append(agent)

    # 連続同一エージェントを圧縮
    compressed = []
    for agent in agents:
        if not compressed or compressed[-1] != agent:
            compressed.append(agent)

    return " \u2192 ".join(compressed) if len(compressed) > 1 else (compressed[0] if compressed else "")


def workflow_key(wf: Dict[str, Any]) -> str:
    """ワークフローの統計キーを決定する。

    skill-driven -> skill_name
    team-driven -> "team:<team_name>"
    agent-burst -> "(agent-burst)"
    """
    wf_type = wf.get("workflow_type", "")
    if wf_type == "team-driven":
        team_name = wf.get("team_name", "unknown")
        return f"team:{team_name}"
    elif wf_type == "agent-burst":
        return "(agent-burst)"
    else:
        return wf.get("skill_name", "unknown")


def compute_stats(
    workflows: List[Dict[str, Any]], min_workflows: int = 3
) -> Dict[str, Dict[str, Any]]:
    """スキル別のワークフロー統計を算出する。"""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for wf in workflows:
        key = workflow_key(wf)
        grouped[key].append(wf)

    stats: Dict[str, Dict[str, Any]] = {}

    for key, wfs in grouped.items():
        if len(wfs) < min_workflows:
            continue

        # 抽象パターンの集計
        pattern_counter: Counter = Counter()
        step_counts: List[int] = []

        for wf in wfs:
            steps = wf.get("steps", [])
            step_counts.append(wf.get("step_count", len(steps)))
            pattern = compress_pattern(steps)
            if pattern:
                pattern_counter[pattern] += 1

        # 統計値の算出
        total_wf = len(wfs)
        avg_steps = sum(step_counts) / total_wf if total_wf > 0 else 0
        step_std = (
            math.sqrt(sum((s - avg_steps) ** 2 for s in step_counts) / total_wf)
            if total_wf > 0
            else 0
        )

        # 一貫性 = 最頻パターンの占有率
        dominant_count = pattern_counter.most_common(1)[0][1] if pattern_counter else 0
        consistency = dominant_count / total_wf if total_wf > 0 else 0
        dominant_pattern = (
            pattern_counter.most_common(1)[0][0] if pattern_counter else ""
        )

        stats[key] = {
            "workflow_count": total_wf,
            "abstract_patterns": dict(pattern_counter.most_common()),
            "consistency": round(consistency, 3),
            "avg_steps": round(avg_steps, 1),
            "step_std": round(step_std, 1),
            "dominant_pattern": dominant_pattern,
        }

    return stats


def generate_hints(stats: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """optimizer 向けの mutation ヒントテキストを生成する。"""
    hints: Dict[str, str] = {}

    for key, s in stats.items():
        consistency = s["consistency"]
        wf_count = s["workflow_count"]
        dominant = s["dominant_pattern"]
        patterns = s["abstract_patterns"]

        lines = [
            f"- このスキルは {wf_count} 回実行され、{dominant} パターンが {consistency * 100:.1f}%",
        ]

        if consistency >= 0.7:
            lines.append(
                f"- ワークフローは安定している（一貫性 {consistency:.2f}）。現在のエージェント戦略を維持"
            )
        elif consistency >= 0.4:
            # 上位2パターンを示す
            top_patterns = list(patterns.keys())[:2]
            lines.append(
                f"- 一貫性が中程度（{consistency:.2f}）。"
                f"{'と'.join(top_patterns)} の使い分け基準を明確にする指示を検討"
            )
        else:
            lines.append(
                f"- 一貫性が低い（{consistency:.2f}）。"
                "エージェントの選択・順序の指針を追加し、ワークフローを安定させることを検討"
            )

        avg = s["avg_steps"]
        std = s["step_std"]
        if std > avg * 0.8:
            lines.append(
                f"- ステップ数のばらつきが大きい（平均 {avg}、標準偏差 {std}）。"
                "タスクの粒度を明確にする指示を検討"
            )

        hints[key] = "\n".join(lines)

    return hints


def generate_fitness_output(
    stats: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """generate-fitness 統合用の出力を生成する。"""
    workflow_stats: Dict[str, Dict[str, Any]] = {}

    for key, s in stats.items():
        workflow_stats[key] = {
            "consistency": s["consistency"],
            "avg_steps": s["avg_steps"],
            "dominant_pattern": s["dominant_pattern"],
        }

    return {"workflow_stats": workflow_stats}


def main():
    parser = argparse.ArgumentParser(description="ワークフロー統計分析")
    parser.add_argument(
        "--min-workflows",
        type=int,
        default=3,
        help="最小ワークフロー数（デフォルト: 3）",
    )
    parser.add_argument(
        "--hints",
        action="store_true",
        help="optimizer 向け mutation ヒントテキストを生成",
    )
    parser.add_argument(
        "--for-fitness",
        action="store_true",
        help="generate-fitness 統合用出力",
    )
    parser.add_argument(
        "--workflows-path",
        type=str,
        default=None,
        help="workflows.jsonl のパス（デフォルト: ~/.claude/rl-anything/workflows.jsonl）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="出力先パス（デフォルト: ~/.claude/rl-anything/workflow_stats.json）",
    )

    args = parser.parse_args()

    wf_path = Path(args.workflows_path) if args.workflows_path else WORKFLOWS_PATH
    workflows = load_workflows(wf_path)

    if not workflows:
        output = {}
        if args.for_fitness:
            output = {"workflow_stats": {}}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    stats = compute_stats(workflows, min_workflows=args.min_workflows)

    if args.hints:
        hints = generate_hints(stats)
        output = {"stats": stats, "hints": hints}
    elif args.for_fitness:
        output = generate_fitness_output(stats)
    else:
        output = stats

    json_output = json.dumps(output, ensure_ascii=False, indent=2)
    print(json_output)

    # ファイルにも保存
    out_path = Path(args.output) if args.output else OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json_output, encoding="utf-8")


if __name__ == "__main__":
    main()
