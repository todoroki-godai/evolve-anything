"""パターン × 既存スキルの Jaccard 照合 (旧 enrich.py 由来)。

discover/__init__.py から re-export される（後方互換）。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from similarity import jaccard_coefficient, tokenize

from .suppression import _load_skill_tokens


def _enrich_patterns(
    patterns: List[Dict[str, Any]],
    project_dir: Optional[Path] = None,
    max_matches: int = 3,
) -> Dict[str, Any]:
    """パターンを既存スキルに Jaccard 係数でマッチングする。

    enrich.py から統合。プラグイン由来のスキルは除外。
    Jaccard >= JACCARD_THRESHOLD のマッチのみ保持し、上位 max_matches 件を返す。

    Returns:
        {"matched_skills": [...], "unmatched_patterns": [...]}
    """
    from . import JACCARD_THRESHOLD, PLUGIN_ROOT

    proj = project_dir or Path.cwd()

    # audit.py から find_artifacts / classify_artifact_origin を遅延インポート
    import sys as _sys
    _audit_scripts = PLUGIN_ROOT / "skills" / "audit" / "scripts"
    if str(_audit_scripts) not in _sys.path:
        _sys.path.insert(0, str(_audit_scripts))
    from audit import classify_artifact_origin, find_artifacts

    artifacts = find_artifacts(proj)

    # 非プラグインスキルのトークンを事前計算
    skill_info = []
    for skill_path in artifacts.get("skills", []):
        origin = classify_artifact_origin(skill_path)
        if origin == "plugin":
            continue
        skill_info.append(_load_skill_tokens(skill_path))

    matched_skills: List[Dict[str, Any]] = []
    matched_pattern_texts: set = set()

    for pattern in patterns:
        pattern_text = pattern.get("pattern", "")
        pattern_type = pattern.get("type", "unknown")
        pattern_tokens = tokenize(pattern_text)

        if not pattern_tokens:
            continue

        scored = []
        for info in skill_info:
            score = jaccard_coefficient(pattern_tokens, info["tokens"])
            if score >= JACCARD_THRESHOLD:
                scored.append({
                    "pattern_type": pattern_type,
                    "pattern": pattern_text,
                    "matched_skill": info["name"],
                    "skill_path": str(info["path"]),
                    "jaccard_score": round(score, 4),
                })

        scored.sort(key=lambda x: x["jaccard_score"], reverse=True)
        if scored:
            matched_skills.extend(scored[:max_matches])
            matched_pattern_texts.add(pattern_text)

    # 未マッチパターン
    unmatched_patterns = []
    for pattern in patterns:
        pattern_text = pattern.get("pattern", "")
        if pattern_text not in matched_pattern_texts:
            unmatched_patterns.append({
                "pattern_type": pattern.get("type", "unknown"),
                "pattern": pattern_text,
                "suggestion": pattern.get("suggestion", "skill_candidate"),
            })

    return {
        "matched_skills": matched_skills,
        "unmatched_patterns": unmatched_patterns,
    }
