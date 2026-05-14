"""gstack ワークフロー分析・retro 結果取得ロジック。

audit パッケージから切り出された gstack lifecycle 関連モジュール。
- _load_flow_chain_phases: ~/.gstack/flow-chain.json から lifecycle/phase_map 読込
- _match_gstack_phase / _is_gstack_skill: スキル名 → フェーズ判定
- build_gstack_analytics_section: ファネル・効率・品質トレンドのレポート生成
- _load_global_retro: ~/.gstack/retros/global-*.json から最新 retro 取得
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_FLOW_CHAIN_FILE = Path.home() / ".gstack" / "flow-chain.json"

# fallback 値（flow-chain.json がない場合に使用）
_FALLBACK_GSTACK_LIFECYCLE = ["plan", "ship", "document", "spec", "retro"]
_FALLBACK_GSTACK_SKILL_PHASE_MAP: Dict[str, str] = {
    "office-hours": "plan",
    "plan-eng-review": "plan",
    "plan-ceo-review": "plan",
    "plan-design-review": "plan",
    "ship": "ship",
    "document-release": "document",
    "spec-keeper": "spec",
    "retro": "retro",
}


def _load_flow_chain_phases(
    path: Optional[Path] = None,
) -> tuple:
    """flow-chain.json から lifecycle と phase_map を構築する。

    Returns:
        (lifecycle, phase_map) — ファイル不在・不正時は fallback 値
    """
    p = path or _FLOW_CHAIN_FILE
    try:
        if not p.exists():
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP
        data = json.loads(p.read_text(encoding="utf-8"))
        chain = data.get("chain")
        if not isinstance(chain, dict) or not chain:
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP

        phase_map: Dict[str, str] = {}
        seen_phases: list = []
        for skill_name, entry in chain.items():
            if not isinstance(entry, dict):
                continue
            phase = entry.get("phase")
            if not phase or not isinstance(phase, str):
                continue
            phase_map[skill_name] = phase
            if phase not in seen_phases:
                seen_phases.append(phase)

        if not phase_map:
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP

        return seen_phases, phase_map
    except (json.JSONDecodeError, OSError, KeyError):
        return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP


# 動的読み込み（モジュールロード時に1回実行）
_GSTACK_LIFECYCLE, _GSTACK_SKILL_PHASE_MAP = _load_flow_chain_phases()

# gstack スキル名の集合（高速判定用）
_GSTACK_SKILL_NAMES = frozenset(_GSTACK_SKILL_PHASE_MAP.keys())


def _match_gstack_phase(skill_name: str) -> Optional[str]:
    """スキル名から gstack ライフサイクルフェーズを推定する。"""
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return _GSTACK_SKILL_PHASE_MAP.get(base)


def _is_gstack_skill(skill_name: str) -> bool:
    """スキル名が gstack 関連かどうかを判定する。"""
    if not skill_name:
        return False
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return base in _GSTACK_SKILL_NAMES


def build_gstack_analytics_section(
    records: List[Dict[str, Any]],
) -> List[str]:
    """gstack ワークフロー分析セクションを構築する。

    ファネル（plan→refine→ship→document→spec→retro の完走率）、
    フェーズ別効率、品質トレンド、最適化候補を表示。
    """
    # gstack レコードのみ抽出
    gstack_records = [r for r in records if _is_gstack_skill(r.get("skill_name", ""))]
    if not gstack_records:
        return []

    # フェーズ別集計
    phase_counts: Dict[str, int] = {}
    phase_records: Dict[str, List[Dict[str, Any]]] = {}
    for rec in gstack_records:
        phase = _match_gstack_phase(rec.get("skill_name", ""))
        if phase:
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
            if phase not in phase_records:
                phase_records[phase] = []
            phase_records[phase].append(rec)

    if not phase_counts:
        return []

    lines = ["## gstack Workflow Analytics", ""]

    # ファネル表示
    funnel_parts = []
    for phase in _GSTACK_LIFECYCLE:
        count = phase_counts.get(phase, 0)
        if count > 0:
            funnel_parts.append(f"{phase}({count})")
    if funnel_parts:
        lines.append(f"Funnel: {' → '.join(funnel_parts)}")

    # plan → retro 比率
    plan_count = phase_counts.get("plan", 0)
    retro_count = phase_counts.get("retro", 0)
    if plan_count > 0:
        ratio = retro_count / plan_count
        if ratio <= 1.0:
            lines.append(f"Completion rate: {int(ratio * 100)}% ({retro_count}/{plan_count})")
        else:
            lines.append(f"Plan→Retro ratio: {ratio:.1f}x ({retro_count}/{plan_count})")
    lines.append("")

    # フェーズ別効率テーブル
    lines.append("Phase efficiency:")
    for phase in _GSTACK_LIFECYCLE:
        recs = phase_records.get(phase, [])
        if not recs:
            continue
        count = len(recs)
        # セッション別グルーピングで平均ステップ数を推定
        sessions: Dict[str, int] = {}
        for r in recs:
            sid = r.get("session_id", "unknown")
            sessions[sid] = sessions.get(sid, 0) + 1
        avg_steps = sum(sessions.values()) / len(sessions) if sessions else 0
        # スキル名のばらつき（一貫性指標）
        skill_names = [r.get("skill_name", "") for r in recs]
        unique_ratio = len(set(skill_names)) / len(skill_names) if skill_names else 1.0
        consistency = 1.0 - unique_ratio  # 名前が統一されているほど高い
        warn = " LOW" if consistency < 0.5 and count >= 5 else ""
        lines.append(f"- {phase}: {count} runs, avg {avg_steps:.1f} steps/session, consistency {consistency:.2f}{warn}")

    lines.append("")

    # 品質トレンド（quality-baselines.jsonl から gstack スキルのみ）
    # load_quality_baselines は audit/__init__.py に定義されているため遅延 import
    from . import load_quality_baselines

    baselines = load_quality_baselines()
    if baselines:
        gstack_baselines = [b for b in baselines if _is_gstack_skill(b.get("skill_name", ""))]
        if gstack_baselines:
            lines.append("Quality trends:")
            skill_scores: Dict[str, float] = {}
            for b in gstack_baselines:
                skill_scores[b["skill_name"]] = b.get("score", 0.0)
            for name, score in sorted(skill_scores.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- {name}: {score:.2f}")
            lines.append("")

    # 最適化候補（一貫性が最も低いフェーズ）
    worst_phase = None
    worst_consistency = 1.0
    for phase in _GSTACK_LIFECYCLE:
        recs = phase_records.get(phase, [])
        if len(recs) < 5:
            continue
        skill_names = [r.get("skill_name", "") for r in recs]
        unique_ratio = len(set(skill_names)) / len(skill_names) if skill_names else 1.0
        consistency = 1.0 - unique_ratio
        if consistency < worst_consistency:
            worst_consistency = consistency
            worst_phase = phase
    if worst_phase and worst_consistency < 0.5:
        lines.append(f"Optimization candidate: {worst_phase} (consistency {worst_consistency:.2f})")
        lines.append("")

    return lines


def _load_global_retro(gstack_dir: Path = None) -> Optional[Dict[str, Any]]:
    """~/.gstack/retros/global-*.json から最新のグローバルretro結果を取得。

    Args:
        gstack_dir: gstack データディレクトリ（None の場合 ~/.gstack）

    Returns:
        parsed JSON dict or None
        スキーマ: {type, date, window, projects[], totals{}}
    """
    if gstack_dir is None:
        gstack_dir = Path.home() / ".gstack"
    retros_dir = gstack_dir / "retros"
    if not retros_dir.exists():
        return None
    try:
        files = sorted(retros_dir.glob("global-*.json"))
        if not files:
            return None
        # 最新ファイル（ソート順で最後）を読む
        latest_file = files[-1]
        return json.loads(latest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
