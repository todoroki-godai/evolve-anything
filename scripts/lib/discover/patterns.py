"""行動パターン検出 (usage/sessions) + Agent prompt 分類 + missed skill 検出。

discover/__init__.py から re-export される（後方互換）。
DATA_DIR / BEHAVIOR_THRESHOLD / MISSED_SKILL_THRESHOLD / PLUGIN_ROOT は
package 経由で遅延参照する（テスト patch / DATA_DIR 差し替え追従）。
"""
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_classifier import classify_agent_type
from skill_triggers import extract_skill_triggers, normalize_skill_name


def detect_behavior_patterns(
    threshold: int = 5,
    *,
    project_root: Optional[Path] = None,
    include_unknown: bool = False,
) -> List[Dict[str, Any]]:
    """繰り返し行動パターンの検出（usage + sessions、5+閾値）。

    parent_skill の有無で contextualized / ad-hoc / unknown を分類し、
    ad-hoc パターンのみをスキル候補として提案する。
    処理順序:
    1. プラグインスキルはメインランキングから除外し、plugin_summary に集約
    2. 組み込み Agent は agent_usage_summary に分離
    3. カスタム Agent はメインランキングに残留
    """
    # package 経由で参照することでテストの mock.patch.object(discover, "X", ...) に追従
    from . import (  # noqa: PLC0415
        DATA_DIR,
        _load_classify_usage_skill,
        load_suppression_list,
    )
    from telemetry_query import query_usage

    project_name = project_root.name if project_root else None
    usage = query_usage(
        project=project_name,
        include_unknown=include_unknown,
        usage_file=DATA_DIR / "usage.jsonl",
    )
    _is_plugin, _classify = _load_classify_usage_skill()

    # ad-hoc レコードのみカウント（contextualized/unknown を除外）
    ad_hoc_counter: Counter = Counter()
    all_counter: Counter = Counter()
    plugin_counter: Counter = Counter()  # プラグイン別集計
    for rec in usage:
        skill = rec.get("skill_name", "")
        if not skill:
            continue
        all_counter[skill] += 1

        # (1) プラグインスキルは別集計
        if _is_plugin(skill):
            plugin_name = _classify(skill) or "gstack"
            plugin_counter[plugin_name] += 1
            continue

        parent_skill = rec.get("parent_skill")
        source = rec.get("source", "")

        # backfill データ（parent_skill なし + source=backfill）は unknown として除外
        if parent_skill is None and source == "backfill":
            continue
        # contextualized（parent_skill あり）は除外
        if parent_skill is not None:
            continue
        # ad-hoc（parent_skill なし、backfill でない）のみカウント
        ad_hoc_counter[skill] += 1

    suppressed = load_suppression_list()
    patterns = []
    builtin_agent_counter: Counter = Counter()
    builtin_agent_prompts: Dict[str, List[str]] = defaultdict(list)

    for skill, ad_hoc_count in ad_hoc_counter.most_common():
        if ad_hoc_count < threshold or skill in suppressed:
            continue

        # (2) Agent:XX パターンの分類
        if skill.startswith("Agent:"):
            agent_name = skill[len("Agent:"):]
            agent_type = classify_agent_type(agent_name, project_root=project_root)

            prompts = [
                r.get("prompt", "") for r in usage
                if r.get("skill_name") == skill
                and r.get("prompt")
                and r.get("parent_skill") is None
                and r.get("source", "") != "backfill"
            ]

            if agent_type == "builtin":
                # 組み込み Agent → builtin_agent_counter に分離
                builtin_agent_counter[skill] = ad_hoc_count
                builtin_agent_prompts[skill] = prompts
                continue

            # (3) カスタム Agent → メインランキングに残留
            pattern: Dict[str, Any] = {
                "type": "behavior",
                "pattern": skill,
                "count": ad_hoc_count,
                "total_count": all_counter.get(skill, 0),
                "suggestion": "skill_candidate",
                "agent_type": agent_type,
            }
            subcategories = _classify_agent_prompts(prompts)
            if subcategories:
                pattern["subcategories"] = subcategories
            patterns.append(pattern)
            continue

        # 非 Agent パターン
        pattern = {
            "type": "behavior",
            "pattern": skill,
            "count": ad_hoc_count,
            "total_count": all_counter.get(skill, 0),
            "suggestion": "skill_candidate",
        }
        patterns.append(pattern)

    # プラグイン利用サマリを末尾に付加（保護状態を表示）
    if plugin_counter:
        patterns.append({
            "type": "plugin_summary",
            "pattern": "plugin_usage",
            "count": sum(plugin_counter.values()),
            "suggestion": "info_only",
            "plugin_breakdown": dict(plugin_counter.most_common()),
            "protected": True,
        })

    # 組み込み Agent 利用サマリを末尾に付加
    if builtin_agent_counter:
        agent_breakdown: Dict[str, Any] = {}
        for agent_skill, count in builtin_agent_counter.most_common():
            entry: Dict[str, Any] = {"count": count}
            subcategories = _classify_agent_prompts(builtin_agent_prompts.get(agent_skill, []))
            if subcategories:
                entry["subcategories"] = subcategories
            agent_breakdown[agent_skill] = entry

        patterns.append({
            "type": "agent_usage_summary",
            "pattern": "builtin_agent_usage",
            "count": sum(builtin_agent_counter.values()),
            "suggestion": "info_only",
            "agent_breakdown": agent_breakdown,
        })

    return patterns


def _classify_agent_prompts(prompts: List[str]) -> List[Dict[str, Any]]:
    """Agent の prompt リストをキーワードベースで簡易分類する。

    common.PROMPT_CATEGORIES / common.classify_prompt() を利用。
    """
    from . import PLUGIN_ROOT
    # hooks/common.py をインポート
    import sys as _sys
    if str(PLUGIN_ROOT / "hooks") not in _sys.path:
        _sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
    import common as _common

    category_counts: Counter = Counter()
    category_examples: Dict[str, str] = {}

    for prompt in prompts:
        cat = _common.classify_prompt(prompt)
        category_counts[cat] += 1
        if cat != "other" and cat not in category_examples:
            category_examples[cat] = prompt[:120]

    results = []
    for cat, count in category_counts.most_common():
        entry: Dict[str, Any] = {
            "category": cat,
            "count": count,
        }
        if cat in category_examples:
            entry["example"] = category_examples[cat]
        results.append(entry)
    return results


def detect_missed_skills(
    threshold: int = 2,
    *,
    project_root: Optional[Path] = None,
    include_unknown: bool = False,
) -> Dict[str, Any]:
    """スキルのトリガーワードにマッチしたがスキルが使われなかったセッションを検出する。

    Returns:
        {"missed": [...], "message": str or None}
        missed: [{"skill": str, "triggers_matched": [str], "session_count": int}, ...]
    """
    from . import DATA_DIR  # noqa: PLC0415  package 経由で patch 追従
    from telemetry_query import query_sessions, query_usage

    # CLAUDE.md からスキルトリガーを取得
    skill_triggers = extract_skill_triggers(project_root=project_root)
    if not skill_triggers:
        return {"missed": [], "message": "No CLAUDE.md found, skipping missed skill detection"}

    project_name = project_root.name if project_root else None

    # sessions テーブルからセッションデータを取得
    sessions = query_sessions(
        project=project_name,
        include_unknown=include_unknown,
    )
    if not sessions:
        return {"missed": [], "message": "No session data (run backfill first), skipping missed skill detection"}

    # usage.jsonl からスキル使用実績を取得
    usage = query_usage(
        project=project_name,
        include_unknown=include_unknown,
        usage_file=DATA_DIR / "usage.jsonl",
    )

    # session_id ごとに使用されたスキル名を集約
    used_skills_by_session: Dict[str, set] = defaultdict(set)
    for rec in usage:
        sid = rec.get("session_id", "")
        skill = rec.get("skill_name", "")
        if sid and skill:
            used_skills_by_session[sid].add(normalize_skill_name(skill))

    # セッションごとにトリガーマッチ → スキル使用チェック
    # skill -> {triggers_matched: set, sessions: set}
    missed_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"triggers_matched": set(), "sessions": set()})

    for session in sessions:
        sid = session.get("session_id", "")
        user_prompts = session.get("user_prompts", [])
        if not sid or not user_prompts:
            continue

        prompts_text = " ".join(user_prompts).lower()
        used_in_session = used_skills_by_session.get(sid, set())

        for skill_entry in skill_triggers:
            skill_name = skill_entry["skill"]
            if skill_name in used_in_session:
                continue

            for trigger in skill_entry["triggers"]:
                if trigger.lower() in prompts_text:
                    missed_map[skill_name]["triggers_matched"].add(trigger)
                    missed_map[skill_name]["sessions"].add(sid)

    # 閾値フィルタリング
    missed = []
    for skill, data in sorted(missed_map.items(), key=lambda x: len(x[1]["sessions"]), reverse=True):
        session_count = len(data["sessions"])
        if session_count >= threshold:
            entry = {
                "skill": skill,
                "triggers_matched": sorted(data["triggers_matched"]),
                "session_count": session_count,
            }
            # eval set ステータスを付与
            eval_set_path = Path.home() / ".claude" / "rl-anything" / "eval-sets" / f"{skill}.json"
            if eval_set_path.exists():
                entry["eval_set_path"] = str(eval_set_path)
                entry["eval_set_status"] = "available"
            else:
                entry["eval_set_path"] = None
                entry["eval_set_status"] = "not_generated"
            missed.append(entry)

    return {"missed": missed, "message": None}
