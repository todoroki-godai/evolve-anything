"""Eval 関連の observability セクション生成（#292）。

sections.py が hard 行数バジェット（800）に達したため、eval saturation 系セクションを
独立モジュールに切り出した。observability contract から参照される `build_*_section`
契約（`(project_dir) -> Optional[List[str]]`）は sections.py の他 builder と同一。
"""
from pathlib import Path
from typing import Dict, List, Optional


def _triggers_by_skill(project_dir: Path) -> Optional[Dict[str, List[str]]]:
    """現 PJ の CLAUDE.md から skill→trigger 語の dict を取得する（取れなければ None）。

    eval_saturation の easy_negatives 判定に渡す。eval-sets は環境グローバルだが
    trigger 語は PJ 依存のため、取れた skill だけ near-miss 判定が効く（graceful degrade）。
    """
    try:
        from skill_triggers import extract_skill_triggers, normalize_skill_name
    except ImportError:
        return None
    try:
        entries = extract_skill_triggers(project_root=project_dir)
    except Exception:
        return None
    return {
        normalize_skill_name(e["skill"]): e.get("triggers", [])
        for e in entries
        if e.get("skill")
    }


def build_eval_saturation_section(project_dir: Path) -> Optional[List[str]]:
    """trigger eval set の飽和度（緑なのに頑健でない）を surface（#292, TASTE）。

    trigger_eval_generator は sessions → evals.json の *順生成* のみで、生成した eval が
    頑健か飽和かを判別する経路が無い。eval_saturation が forward-gen eval set の飽和兆候
    （positive 偏重 / 易しい negative / クエリ過少）を eval 実行なし・決定論で測り、
    calibration drift と同セクション帯で surface する。evolve は audit を消費するので
    evolve のたびに「緑の eval セットが信頼できるか」が可視化される — 手動確認に依存しない配線。

    観測可能性（calibration_drift と同じデータ駆動の適用判定）:
    - eval-sets ディレクトリが空 / 不在 → None（対象外。eval 未生成の環境）
    - eval set はあるが飽和なし → 「評価したが飽和兆候なし ✓」（silence != evaluated）
    - 飽和あり → ⚠ で対象スキルと飽和理由、eval 再生成 / near-miss 強化を提案
    """
    try:
        import eval_saturation
    except ImportError:
        return None

    triggers = _triggers_by_skill(project_dir)
    try:
        result = eval_saturation.compute_eval_saturation(triggers_by_skill=triggers)
    except Exception:
        return None

    if not result.get("applicable"):
        return None  # eval 未生成の環境 → 対象外

    header = ["## Eval Saturation (trigger eval 飽和度)", ""]
    evaluated = result.get("evaluated", 0)
    saturated = result.get("saturated", [])
    if not saturated:
        return header + [
            f"✓ 評価したが飽和兆候なし（{evaluated} 件の eval set を診断、緑＝頑健とみなせる）",
            "",
        ]

    lines = header + [
        "⚠ trigger eval set に飽和兆候あり（緑でも頑健性を保証しない）。"
        "fresh session で eval 再生成 or near-miss negative の追加を検討:",
    ]
    for s in saturated:
        labels = ", ".join(
            eval_saturation.REASON_LABELS.get(r, r) for r in s.get("reasons", [])
        )
        nratio = s.get("negative_ratio", 0.0)
        lines.append(
            f"  - {s['skill']}: {labels} "
            f"(queries={s.get('total', 0)}, neg={nratio:.0%})"
        )
    lines.append("")
    return lines
