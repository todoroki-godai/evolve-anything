"""自己進化適性判定 (バルク + 単一スキル) + プロジェクトルート推定。

Phase 8 / Slice 4 で `skill_evolve.py` から切り出し。
`compute_telemetry_scores` / `compute_llm_scores` / `is_self_evolved_skill` /
`is_verification_skill` / `classify_suitability` / `detect_anti_patterns` /
`ANTI_PATTERN_REJECTION_COUNT` / `_plugin_root` は `__init__.py` を SoT として
`from . import X` 関数本体内 lazy lookup で参照
（`mock.patch("skill_evolve.compute_telemetry_scores")` 等の互換維持）。
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _find_project_dir(skill_dir: Path) -> Optional[Path]:
    """skill_dir からプロジェクトルートを推定する。

    .claude/skills/<name>/ の2階層上をプロジェクトルートとみなす。
    見つからない場合は None。
    """
    # .claude/skills/<skill_name>/SKILL.md → .claude → project_root
    candidate = skill_dir.resolve()
    for _ in range(5):
        parent = candidate.parent
        if parent.name == "skills" and parent.parent.name == ".claude":
            return parent.parent.parent
        candidate = parent
    return None


def skill_evolve_assessment(
    project_dir: Optional[Path] = None,
    *,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """全カスタムスキルの自己進化適性を判定する。

    Args:
        project_dir: プロジェクトディレクトリ
        project: テレメトリのプロジェクトフィルタ

    Returns:
        [{"skill_name": str, "skill_dir": str, "scores": {...},
          "total_score": int, "suitability": str,
          "anti_patterns": [...], "recommendation": str}, ...]
    """
    from . import (
        _plugin_root,
        compute_telemetry_scores,
        compute_llm_scores,
        is_self_evolved_skill,
        is_verification_skill,
        classify_suitability,
        detect_anti_patterns,
        ANTI_PATTERN_REJECTION_COUNT,
    )
    sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
    from audit import classify_artifact_origin, find_artifacts

    from rl_common.config import load_user_config
    _cfg = load_user_config()
    _allowlist_raw = _cfg.get("evolve_global_allowlist", "")
    _global_allowlist: set = {
        s.strip() for s in _allowlist_raw.split(",") if s.strip()
    }

    proj = project_dir or Path.cwd()
    artifacts = find_artifacts(proj)

    # Pre-flight guard: LLM呼び出し件数の事前確認
    # custom + allowlist に含まれる global の両方を対象件数に含める
    _TOKENS_PER_SKILL = 47_000
    _MAX_AUTO_SKILLS = 10
    _all_llm_targets = [
        p for p in artifacts.get("skills", [])
        if (
            (classify_artifact_origin(p) == "custom" and not Path(p).parent.is_symlink())
            or (classify_artifact_origin(p) == "global" and p.parent.name in _global_allowlist)
        )
    ]
    n = len(_all_llm_targets)
    if n > _MAX_AUTO_SKILLS:
        estimated = n * _TOKENS_PER_SKILL
        raise RuntimeError(
            f"[llm-batch-guard] skill_evolve_assessment: {n}件のスキルが対象です。\n"
            f"推定トークン消費: {estimated:,} tokens ({n} × {_TOKENS_PER_SKILL:,})。\n"
            f"evolve --skip-llm-evolve で LLM 評価をスキップできます。"
        )

    results: List[Dict[str, Any]] = []
    _excluded_global_count = 0

    for skill_path in artifacts.get("skills", []):
        skill_dir = skill_path.parent
        skill_name = skill_dir.name

        # 対象フィルタ: ユーザー自作のプロジェクトローカルスキルのみ
        # plugin = rl-anything 本体、global = gstack 等インストール済み → 除外
        # ただし evolve_global_allowlist に含まれる global スキルは評価対象に含める
        origin = classify_artifact_origin(skill_path)
        if origin == "plugin":
            continue
        if origin == "global":
            if skill_name not in _global_allowlist:
                _excluded_global_count += 1
                continue

        # symlink 除外
        if skill_dir.is_symlink():
            continue

        # 既に自己進化済みは除外
        if is_self_evolved_skill(skill_dir):
            results.append({
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "already_evolved": True,
                "suitability": "already_evolved",
            })
            continue

        # テレメトリ3軸
        telemetry = compute_telemetry_scores(skill_name, project=project)

        # LLM 2軸
        llm = compute_llm_scores(skill_name, skill_dir)

        scores = {
            "frequency": telemetry["frequency"],
            "diversity": telemetry["diversity"],
            "evaluability": telemetry["evaluability"],
            "external_dependency": llm["external_dependency"],
            "judgment_complexity": llm["judgment_complexity"],
            "error_count": telemetry["error_count"],
        }

        total_score = sum(scores.values())
        suitability = classify_suitability(total_score)

        # アンチパターン検出
        anti_patterns = detect_anti_patterns(scores, skill_dir)
        rejection_count = sum(
            1 for ap in anti_patterns
            if ap["pattern"] in ("Noise Collector", "Context Bloat", "Band-Aid")
        )

        verification_bypass = False
        if rejection_count >= ANTI_PATTERN_REJECTION_COUNT:
            recommendation = "変換非推奨: 評価時アンチパターン{}件該当".format(rejection_count)
            suitability = "rejected"
        elif suitability == "high":
            recommendation = "変換を推奨"
        elif suitability == "medium":
            recommendation = "変換可能 — ユーザー判断に委ねます"
        elif is_verification_skill(skill_name, skill_dir):
            suitability = "medium"
            verification_bypass = True
            recommendation = "変換可能 — 検証系スキルのため自己進化を推奨"
        else:
            recommendation = "変換非推奨"

        entry = {
            "skill_name": skill_name,
            "skill_dir": str(skill_dir),
            "already_evolved": False,
            "scores": scores,
            "total_score": total_score,
            "suitability": suitability,
            "anti_patterns": anti_patterns,
            "recommendation": recommendation,
            "telemetry_detail": {
                "usage_count": telemetry["usage_count"],
                "error_count": telemetry["error_count"],
                "error_categories": telemetry["error_categories"],
            },
            "llm_cached": llm["cached"],
        }
        if verification_bypass:
            entry["verification_bypass"] = True
        results.append(entry)

    # excluded_globals サマリ（evolve レポート用）
    if _excluded_global_count > 0:
        results.append({
            "_meta": "excluded_globals",
            "excluded_global_count": _excluded_global_count,
            "hint": (
                f"global スキル {_excluded_global_count} 件は評価対象外 "
                f"(ダウンロード済みスキルは除外)。"
                f" 自作グローバルスキルがあれば evolve_global_allowlist に追加してください。"
            ),
        })

    return results


def assess_single_skill(
    skill_name: str,
    skill_dir: Path,
    *,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """1スキルの自己進化適性判定結果を返す。

    Returns:
        {"skill_name": str, "skill_dir": str, "already_evolved": bool,
         "suitability": str, "scores": {...}, "total_score": int,
         "anti_patterns": [...], "recommendation": str}
    """
    from . import (
        compute_telemetry_scores,
        compute_llm_scores,
        is_self_evolved_skill,
        is_verification_skill,
        classify_suitability,
        detect_anti_patterns,
        ANTI_PATTERN_REJECTION_COUNT,
    )
    skill_dir = Path(skill_dir)

    # 既に自己進化済み
    if is_self_evolved_skill(skill_dir):
        return {
            "skill_name": skill_name,
            "skill_dir": str(skill_dir),
            "already_evolved": True,
            "suitability": "already_evolved",
            "workflow_checkpoints": None,
        }

    # テレメトリ3軸
    telemetry = compute_telemetry_scores(skill_name, project=project)

    # LLM 2軸
    llm = compute_llm_scores(skill_name, skill_dir)

    scores = {
        "frequency": telemetry["frequency"],
        "diversity": telemetry["diversity"],
        "evaluability": telemetry["evaluability"],
        "external_dependency": llm["external_dependency"],
        "judgment_complexity": llm["judgment_complexity"],
        "error_count": telemetry["error_count"],
    }

    total_score = sum(scores.values())
    suitability = classify_suitability(total_score)

    # アンチパターン検出
    anti_patterns = detect_anti_patterns(scores, skill_dir)
    rejection_count = sum(
        1 for ap in anti_patterns
        if ap["pattern"] in ("Noise Collector", "Context Bloat", "Band-Aid")
    )

    # 検証系スキルのバイパス判定
    verification_bypass = False
    if rejection_count >= ANTI_PATTERN_REJECTION_COUNT:
        recommendation = "変換非推奨: 評価時アンチパターン{}件該当".format(rejection_count)
        suitability = "rejected"
    elif suitability == "high":
        recommendation = "変換を推奨"
    elif suitability == "medium":
        recommendation = "変換可能 — ユーザー判断に委ねます"
    elif is_verification_skill(skill_name, skill_dir):
        # 検証系スキルは low でも medium に昇格
        suitability = "medium"
        verification_bypass = True
        recommendation = "変換可能 — 検証系スキルのため自己進化を推奨"
    else:
        recommendation = "変換非推奨"

    # ワークフローチェックポイント検出
    workflow_checkpoints = None
    try:
        from workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
    except ImportError:
        try:
            from lib.workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
        except ImportError:
            is_workflow_skill = None

    if is_workflow_skill is not None and is_workflow_skill(skill_dir):
        try:
            project_dir = _find_project_dir(skill_dir)
            if project_dir:
                workflow_checkpoints = detect_checkpoint_gaps(
                    skill_name, skill_dir, project_dir,
                )
        except Exception:
            workflow_checkpoints = []

    result = {
        "skill_name": skill_name,
        "skill_dir": str(skill_dir),
        "already_evolved": False,
        "scores": scores,
        "total_score": total_score,
        "suitability": suitability,
        "anti_patterns": anti_patterns,
        "recommendation": recommendation,
        "telemetry_detail": {
            "usage_count": telemetry["usage_count"],
            "error_count": telemetry["error_count"],
            "error_categories": telemetry["error_categories"],
        },
        "llm_cached": llm["cached"],
        "workflow_checkpoints": workflow_checkpoints,
    }
    if verification_bypass:
        result["verification_bypass"] = True
    return result
