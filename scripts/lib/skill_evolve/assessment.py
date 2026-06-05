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

# Lazy-initialized module-level references for testability (populated on first call)
find_artifacts = None
classify_artifact_origin = None
load_user_config = None

# batch_guard のトークン見積もり定数（#337）。
# 実際の judgment/customize プロンプトは SKILL.md 先頭 _TRUNCATE_CHARS 字に truncate される
# （build_judgment_prompt / build_customize_prompt と一致）。旧概算は固定 47,000/skill で
# 全文×全スキルを仮定していたため実コストの約50倍に膨らんでいた（sys-bots で 893k と桁違い）。
_TRUNCATE_CHARS = 2000
_PROMPT_SCAFFOLD_CHARS = 600  # 固定の指示文 + テンプレ見出しの概算
_CHARS_PER_TOKEN = 3.0  # 日英混在の保守的概算（1 token ≈ 3 chars）


def _estimate_skill_tokens(skill_path) -> int:
    """Phase B プロンプト1スキル分の概算トークン数（truncate 後プロンプト長ベース、#337）。

    skill_path は SKILL.md のパス。読めない場合は scaffold 分の最小見積もりを返す。
    """
    try:
        content = Path(skill_path).read_text(encoding="utf-8")
    except OSError:
        content = ""
    prompt_chars = min(len(content), _TRUNCATE_CHARS) + _PROMPT_SCAFFOLD_CHARS
    return max(1, int(prompt_chars / _CHARS_PER_TOKEN))


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
    skip_skills: Optional[set] = None,
    skip_llm_evolve: bool = False,
    confirmed_batch: bool = False,
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
    import skill_evolve.assessment as _self_mod
    if _self_mod.find_artifacts is None:
        sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
        from audit import classify_artifact_origin as _ca, find_artifacts as _fa
        _self_mod.find_artifacts = _fa
        _self_mod.classify_artifact_origin = _ca
    if _self_mod.load_user_config is None:
        from rl_common.config import load_user_config as _luc
        _self_mod.load_user_config = _luc
    find_artifacts = _self_mod.find_artifacts
    classify_artifact_origin = _self_mod.classify_artifact_origin
    load_user_config = _self_mod.load_user_config

    _cfg = load_user_config()
    _allowlist_raw = _cfg.get("evolve_global_allowlist", "")
    _global_allowlist: set = {
        s.strip() for s in _allowlist_raw.split(",") if s.strip()
    }

    if skip_llm_evolve:
        return []

    proj = project_dir or Path.cwd()
    artifacts = find_artifacts(proj)

    # Pre-flight guard: バッチ件数の事前確認。
    # [ADR-037] Phase 1c で compute_llm_scores は LLM-free（cache-read + 静的フォールバック）
    # になったため、本評価ループ自体は LLM を呼ばない。LLM コストは後段の SKILL Phase B
    # （judgment refresh の emit→assistant inline）と evolve apply（テンプレカスタマイズ）に移動した。
    # 本ガードはその後段バッチの規模を承認させる確認ゲートとして残置する。トークン見積もりは
    # `_estimate_skill_tokens`（truncate 後プロンプト長ベース、#337）でスキルごとに算出する。
    # custom + allowlist に含まれる global の両方を対象件数に含める
    _MAX_AUTO_SKILLS = 10
    _all_llm_targets = [
        p for p in artifacts.get("skills", [])
        if (
            (classify_artifact_origin(p) == "custom" and not Path(p).parent.is_symlink())
            or (classify_artifact_origin(p) == "global" and p.parent.name in _global_allowlist)
        )
    ]

    # denylist + 一時 skip_skills で effective targets を絞り込む
    from .denylist import get_denied_skill_names
    _denied = get_denied_skill_names()
    if skip_skills:
        _denied = _denied | set(skip_skills)

    _effective_targets = [p for p in _all_llm_targets if p.parent.name not in _denied]
    _already_denied = [p.parent.name for p in _all_llm_targets if p.parent.name in _denied]

    n_effective = len(_effective_targets)
    if n_effective > _MAX_AUTO_SKILLS and not confirmed_batch:
        _groups: List[Dict[str, Any]] = []
        for _origin in ("custom", "global"):
            _group_skills = [p for p in _effective_targets if classify_artifact_origin(p) == _origin]
            if _group_skills:
                _groups.append({
                    "origin": _origin,
                    "skills": [p.parent.name for p in _group_skills],
                    "skill_dirs": [str(p.parent) for p in _group_skills],
                    "estimated_tokens": sum(_estimate_skill_tokens(p) for p in _group_skills),
                    "skill_count": len(_group_skills),
                })
        return [{
            "_meta": "batch_guard_trigger",
            "groups": _groups,
            "total_effective": n_effective,
            "already_denied": _already_denied,
        }]

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

        # denylist / skip_skills 除外
        if skill_name in _denied:
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
