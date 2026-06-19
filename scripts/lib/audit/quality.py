"""品質ベースライン読み込み・スパークライン・品質推移セクション生成。

audit パッケージから切り出された Quality trends モジュール。
- load_quality_baselines: quality-baselines.jsonl から全レコード取得
- generate_sparkline: スコアリスト → スパークライン文字列
- build_quality_trends_section: スキル別の品質推移をレポート行として生成
"""
import json
import sys
from typing import Any, Dict, List

from rl_common import DATA_DIR
from plugin_root import PLUGIN_ROOT


def load_quality_baselines() -> List[Dict[str, Any]]:
    """quality-baselines.jsonl から全レコードを読み込む。"""
    baselines_file = DATA_DIR / "quality-baselines.jsonl"
    if not baselines_file.exists():
        return []
    records = []
    for line in baselines_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def generate_sparkline(scores: List[float]) -> str:
    """スコアリストからスパークライン文字列を生成する。"""
    if not scores:
        return ""
    blocks = " ▁▂▃▄▅▆▇"
    min_s = min(scores)
    max_s = max(scores)
    span = max_s - min_s if max_s > min_s else 1.0
    result = ""
    for s in scores:
        idx = int((s - min_s) / span * (len(blocks) - 1))
        idx = max(0, min(len(blocks) - 1, idx))
        result += blocks[idx]
    return result


def build_quality_trends_section(
    baselines: List[Dict[str, Any]],
    usage: Dict[str, int],
) -> List[str]:
    """品質推移セクションの行リストを生成する。"""
    if not baselines:
        return []

    # 遅延 import（循環参照回避）
    _scripts_dir = PLUGIN_ROOT / "scripts"
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))
    from quality_monitor import (
        DEGRADATION_THRESHOLD,
        compute_baseline_score,
        compute_moving_average,
        get_skill_records,
        needs_rescore,
    )

    # スキル名を収集
    skill_names = sorted(set(r.get("skill_name", "") for r in baselines if r.get("skill_name")))
    if not skill_names:
        return []

    lines = ["## Skill Quality Trends", ""]

    for skill_name in skill_names:
        skill_recs = get_skill_records(baselines, skill_name)
        if not skill_recs:
            continue

        # criteria キーがあって空のレコード（採点失敗）を除いた valid レコードを使用
        valid_recs = [r for r in skill_recs if not ("criteria" in r and not r["criteria"])]
        display_recs = valid_recs if valid_recs else skill_recs
        scores = [r.get("score", 0.0) for r in display_recs]
        latest_score = scores[-1] if scores else 0.0

        # スパークライン（2件以上必要）
        if len(scores) >= 2:
            sparkline = generate_sparkline(scores)
        else:
            sparkline = ""

        # 劣化判定
        degraded = False
        if len(skill_recs) >= 2:
            baseline = compute_baseline_score(skill_recs)
            avg = compute_moving_average(skill_recs)
            if baseline > 0:
                decline_rate = (baseline - avg) / baseline
                degraded = decline_rate >= DEGRADATION_THRESHOLD

        # 再スコアリング判定
        current_usage = usage.get(skill_name, 0)
        rescore_needed = needs_rescore(skill_name, current_usage, baselines)

        # 行を組み立て
        parts = [f"- {skill_name}"]
        if sparkline:
            parts.append(f" {sparkline}")
        parts.append(f" {latest_score:.2f}")
        if degraded:
            parts.append(f" DEGRADED → /evolve-anything:evolve-skill {skill_name}")
        elif rescore_needed:
            parts.append(" RESCORE NEEDED")
        lines.append("".join(parts))

    lines.append("")
    return lines
