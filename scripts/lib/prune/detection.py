"""dead glob / zero invocation / global safe / duplicate / decay 検出（旧 prune.py 由来）。

prune/__init__.py から re-export される（後方互換）。
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from frontmatter import parse_frontmatter
from skill_usage_stats import (
    find_nested_only_skills,
    find_rarely_used_global_skills,
    find_unused_global_skills,
)
from audit import (
    aggregate_usage,  # noqa: F401  # 互換のため再公開可能（未使用）
    classify_artifact_origin,
    load_usage_data,
    load_usage_registry,
    semantic_similarity_check,
)

from .config import (
    DEFAULT_DECAY_DAYS,
    ZERO_INVOCATION_DAYS,
    load_decay_threshold,
)
from .skill_inspect import (
    _enrich_candidate,
    compute_decay_score,
    is_pinned,
    is_reference_skill,
)
from .corrections import load_corrections


def _expand_glob_pattern(pattern: str) -> List[str]:
    """ブレース展開とカンマ区切りを処理して個別パターンのリストを返す。

    例: "src/**/*.{ts,tsx}" → ["src/**/*.ts", "src/**/*.tsx"]
    例: "a/*.ts, b/*.ts" → ["a/*.ts", "b/*.ts"]
    """
    import re

    # 1. まずブレース展開（カンマ分割より先に処理）
    def expand_braces(s: str) -> List[str]:
        m = re.search(r"\{([^}]+)\}", s)
        if not m:
            return [s]
        alternatives = m.group(1).split(",")
        results = []
        for alt in alternatives:
            results.append(s[: m.start()] + alt.strip() + s[m.end() :])
        return results

    # 2. ブレースを含まないカンマでのみ分割
    # ブレース内のカンマは分割しない
    parts = []
    depth = 0
    current = []
    for ch in pattern:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    parts.append("".join(current).strip())

    # 3. 各パートでブレース展開
    expanded = []
    for part in parts:
        if not part:
            continue
        expanded.extend(expand_braces(part))
    return expanded


def detect_dead_globs(project_dir: Path) -> List[Dict[str, Any]]:
    """rules の paths/globs 対象がマッチするファイルが存在しないものを検出。

    parse_frontmatter() を使い、paths キーと globs キーの両方を処理する。
    """
    dead = []
    rules_dir = project_dir / ".claude" / "rules"
    if not rules_dir.exists():
        return dead

    for rule_file in rules_dir.glob("*.md"):
        fm = parse_frontmatter(rule_file)

        # paths と globs の両キーを統合
        all_patterns_raw: List[str] = []
        for key in ("paths", "globs"):
            val = fm.get(key)
            if isinstance(val, str):
                all_patterns_raw.append(val)
            elif isinstance(val, list):
                all_patterns_raw.extend(str(v) for v in val if v)

        if not all_patterns_raw:
            continue

        for raw_pattern in all_patterns_raw:
            patterns = _expand_glob_pattern(raw_pattern)
            # いずれかのパターンがマッチすればOK
            any_match = any(
                list(project_dir.glob(p))[:1] for p in patterns
            )
            if not any_match:
                dead.append({
                    "file": str(rule_file),
                    "pattern": raw_pattern,
                    "reason": "dead_glob",
                })
    return dead


def detect_zero_invocations(
    artifacts: Dict[str, List[Path]], days: int = ZERO_INVOCATION_DAYS
) -> List[Dict[str, Any]]:
    """usage.jsonl で指定日数間使用記録がないアーティファクトを検出。

    直接の Skill tool_use に加えて、usage.jsonl の parent_skill として
    参照されている回数もカウントする（MUST）。
    subagents.jsonl は参照対象外。
    """
    usage_records = load_usage_data(days=days)
    used_skills = set()
    for rec in usage_records:
        used_skills.add(rec.get("skill_name", ""))
        # parent_skill 経由の使用もカウント
        parent = rec.get("parent_skill")
        if parent:
            used_skills.add(parent)

    zero = []
    plugin_unused = []
    for path in artifacts.get("skills", []):
        skill_name = path.parent.name
        origin = classify_artifact_origin(path)
        # global スキルはここでは検出しない（safe_global_check で処理）
        if origin == "global":
            continue
        # .pin ファイルによる淘汰保護
        if is_pinned(path):
            continue
        # 参照型スキルは zero invocation 検出から除外
        if is_reference_skill(path):
            continue
        if skill_name not in used_skills:
            if origin == "plugin":
                plugin_unused.append({
                    "file": str(path),
                    "skill_name": skill_name,
                    "reason": "plugin_unused",
                })
            else:
                zero.append(_enrich_candidate({
                    "file": str(path),
                    "skill_name": skill_name,
                    "reason": "zero_invocation",
                    "days": days,
                }))

    # rules は毎ターン system prompt に注入されるため使用回数を測定できない。
    # 淘汰対象にしない。

    return zero, plugin_unused


def safe_global_check(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """global スキルの安全な判断。

    優先順: skill_activations.jsonl（PostToolUse hook 蓄積データ）→ usage-registry.jsonl フォールバック。
    どちらもデータがない場合は空リストを返し、蓄積を待つ。
    """
    # skill_activations.jsonl ベースの未使用検出（90日間）
    activation_unused = find_unused_global_skills(days=90)
    activation_rarely = find_rarely_used_global_skills(days=90, threshold=3)
    activation_nested_only = find_nested_only_skills(days=90)

    if activation_unused or activation_rarely or activation_nested_only:
        # skill_path マップ（audit の artifacts から逆引き）
        path_map: Dict[str, Path] = {}
        for path in artifacts.get("skills", []):
            if str(path).startswith(str(Path.home() / ".claude" / "skills")):
                path_map[path.parent.name] = path

        candidates = []
        seen: set = set()

        for item in activation_unused:
            name = item["skill_name"]
            if name in seen:
                continue
            seen.add(name)
            path = path_map.get(name)
            if path and is_pinned(path):
                continue
            # 参照型スキルは削除候補から除外（ドリフトチェックは別途実施）
            if path and is_reference_skill(path):
                continue
            candidates.append(_enrich_candidate({
                "file": str(path) if path else f"~/.claude/skills/{name}/SKILL.md",
                "skill_name": name,
                "reason": "no_activation_90d",
                "days_no_use": item["days_no_use"],
            }))

        for item in activation_rarely:
            name = item["skill_name"]
            if name in seen:
                continue
            seen.add(name)
            path = path_map.get(name)
            if path and is_pinned(path):
                continue
            # 参照型スキルは削除候補から除外
            if path and is_reference_skill(path):
                continue
            candidates.append(_enrich_candidate({
                "file": str(path) if path else f"~/.claude/skills/{name}/SKILL.md",
                "skill_name": name,
                "reason": "rarely_used_90d",
                "count": item["count"],
                "days_since": item["days_since"],
                "top_level_count": item.get("top_level_count", 0),
                "nested_count": item.get("nested_count", 0),
            }))

        # nested-only スキル → 削除でなくマージ提案
        for item in activation_nested_only:
            name = item["skill_name"]
            if name in seen:
                continue
            seen.add(name)
            path = path_map.get(name)
            if path and is_pinned(path):
                continue
            candidates.append(_enrich_candidate({
                "file": str(path) if path else f"~/.claude/skills/{name}/SKILL.md",
                "skill_name": name,
                "reason": "nested_only_merge_candidate",
                "nested_count": item["nested_count"],
                "days_since": item["days_since"],
                "recommendation": "merge_into_caller",
            }))

        return candidates

    # フォールバック: usage-registry.jsonl
    registry = load_usage_registry()
    if not registry:
        return []

    candidates = []
    for path in artifacts.get("skills", []):
        if not str(path).startswith(str(Path.home() / ".claude" / "skills")):
            continue
        if is_pinned(path):
            continue
        skill_name = path.parent.name
        usages = registry.get(skill_name, [])
        projects = set(r.get("project_path", "") for r in usages)
        projects.discard("")

        if len(projects) == 0:
            candidates.append(_enrich_candidate({
                "file": str(path),
                "skill_name": skill_name,
                "reason": "no_usage_in_registry",
                "project_count": 0,
            }))
        elif len(projects) == 1:
            candidates.append(_enrich_candidate({
                "file": str(path),
                "skill_name": skill_name,
                "reason": "single_project_only",
                "project_count": 1,
                "projects": list(projects),
            }))

    return candidates


def detect_duplicates(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """audit-report の重複検出結果（意味的類似度判定、閾値 80%）を利用する。

    audit.py の semantic_similarity_check() を再利用。
    plugin / global スキルは診断対象外のため事前に除外する。
    """
    from artifact_scope import filter_artifacts_to_target
    return semantic_similarity_check(filter_artifacts_to_target(artifacts), threshold=0.80)


def detect_decay_candidates(
    artifacts: Dict[str, List[Path]],
    decay_days: float = DEFAULT_DECAY_DAYS,
) -> List[Dict[str, Any]]:
    """decay スコアが閾値以下のスキルを検出する。"""
    threshold = load_decay_threshold()
    corrections_by_skill = load_corrections()
    usage_records = load_usage_data()
    now = datetime.now(timezone.utc)

    # スキルごとの最新使用日を計算
    last_used: Dict[str, datetime] = {}
    for rec in usage_records:
        skill = rec.get("skill_name", "")
        parent = rec.get("parent_skill")
        ts = rec.get("timestamp", "")
        if not ts:
            continue
        try:
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
        except (ValueError, TypeError):
            continue
        for name in [skill, parent]:
            if name and (name not in last_used or dt > last_used[name]):
                last_used[name] = dt

    candidates = []
    for path in artifacts.get("skills", []):
        skill_name = path.parent.name
        if is_pinned(path):
            continue
        if skill_name not in last_used:
            continue  # zero_invocations で処理済み
        age_days = (now - last_used[skill_name]).total_seconds() / 86400
        correction_count = len(corrections_by_skill.get(skill_name, []))
        score = compute_decay_score(age_days, correction_count, decay_days)
        if score < threshold:
            candidates.append(_enrich_candidate({
                "file": str(path),
                "skill_name": skill_name,
                "reason": "decay_below_threshold",
                "decay_score": round(score, 4),
                "age_days": round(age_days, 1),
                "correction_count": correction_count,
                "threshold": threshold,
            }))

    return candidates
