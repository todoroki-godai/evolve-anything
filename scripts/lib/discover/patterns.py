"""行動パターン検出 (usage/sessions) + Agent prompt 分類 + missed skill 検出。

discover/__init__.py から re-export される（後方互換）。
DATA_DIR / BEHAVIOR_THRESHOLD / MISSED_SKILL_THRESHOLD / PLUGIN_ROOT は
package 経由で遅延参照する（テスト patch / DATA_DIR 差し替え追従）。
"""
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_classifier import classify_agent_type
from rl_common import hook_store_path
from skill_triggers import (
    extract_skill_triggers,
    normalize_skill_name,
    resolve_claude_md_path,
)

# constraint decay: mtime フィルタ（30日）
_THIRTY_DAYS_SEC = 30 * 24 * 3600


def detect_constraint_decay(
    sessions_path: Path,
    corrections_path: Path,
    decay_threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """セッション後半30%のターンに集中する correction を検出し decay_rate を返す。

    arXiv 2605.06445 の知見: LLM はコンテキストが長くなると制約を忘れる (constraint decay)。
    セッション後半30%での correction 密度を測定して decay_rate として記録する。

    アルゴリズム:
    1. sessions.jsonl を読み込み、session_id → max_turn_index の dict を作る
       (O(N) pre-index、30日以内の mtime フィルタ付き)
    2. corrections.jsonl を走査し、各 correction の session_id を引いて
       turn_index / max_turn_index を計算
    3. turn_ratio > 0.7（後半30%）の correction 数 / 全 correction 数 = session_decay_rate
    4. session_decay_rate > decay_threshold → WARNING レコードを返す

    返り値:
        [{"type": "constraint_decay", "session_id": ..., "decay_rate": float,
          "late_corrections": int, "total_corrections": int,
          "severity": "WARNING"|"INFO", "message": str}]

    エッジケース:
    - sessions.jsonl が空 or 不在 → []
    - corrections.jsonl が空 or 不在 → []
    - max_turn_index == 0 → skip（ZeroDivision 防止）
    - session_id が sessions.jsonl に存在しない correction → skip
    """
    # 30日 mtime フィルタ
    if not sessions_path.exists():
        return []
    if time.time() - sessions_path.stat().st_mtime > _THIRTY_DAYS_SEC:
        return []

    if not corrections_path.exists():
        return []

    # Step 1: session_id → max_turn_index の pre-index（O(N)）
    session_index: Dict[str, int] = {}
    try:
        for line in sessions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = rec.get("session_id", "")
            max_turn = rec.get("max_turn_index")
            if sid and max_turn is not None:
                session_index[sid] = int(max_turn)
    except Exception:
        return []

    if not session_index:
        return []

    # Step 2: corrections を走査して session 別に集計（O(M)）
    # session_id → {"total": int, "late": int}
    session_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "late": 0})
    try:
        for line in corrections_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = rec.get("session_id", "")
            turn_index = rec.get("turn_index")
            if not sid or turn_index is None:
                continue
            if sid not in session_index:
                continue  # unknown session → skip
            max_turn = session_index[sid]
            if max_turn == 0:
                continue  # ZeroDivision ガード

            turn_ratio = int(turn_index) / max_turn
            session_stats[sid]["total"] += 1
            if turn_ratio > 0.7:
                session_stats[sid]["late"] += 1
    except Exception:
        return []

    # Step 3 & 4: decay_rate を計算して閾値超過を WARNING に
    results: List[Dict[str, Any]] = []
    for sid, stats in session_stats.items():
        total = stats["total"]
        late = stats["late"]
        if total == 0:
            continue
        decay_rate = late / total
        severity = "WARNING" if decay_rate > decay_threshold else "INFO"
        results.append({
            "type": "constraint_decay",
            "session_id": sid,
            "decay_rate": round(decay_rate, 4),
            "late_corrections": late,
            "total_corrections": total,
            "severity": severity,
            "message": (
                f"Session {sid}: {late}/{total} corrections in the last 30% of turns "
                f"(decay_rate={decay_rate:.2f})"
            ),
        })

    return results


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
        usage_file=hook_store_path("usage.jsonl", base=DATA_DIR),
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
        # 「CLAUDE.md が無い」と「CLAUDE.md は在るが trigger 抽出 0」を区別する (#295)。
        # 後者は Skills セクションの記法がパーサ非対応など環境/記法側の問題で、
        # 「No CLAUDE.md found」と出すとミスリードになる。
        if resolve_claude_md_path(project_root=project_root) is None:
            msg = "No CLAUDE.md found, skipping missed skill detection"
        else:
            msg = (
                "CLAUDE.md present but no skill triggers extracted "
                "(check Skills section format), skipping missed skill detection"
            )
        return {"missed": [], "message": msg}

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
        usage_file=hook_store_path("usage.jsonl", base=DATA_DIR),
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
            eval_set_path = Path.home() / ".claude" / "evolve-anything" / "eval-sets" / f"{skill}.json"
            if eval_set_path.exists():
                entry["eval_set_path"] = str(eval_set_path)
                entry["eval_set_status"] = "available"
            else:
                entry["eval_set_path"] = None
                entry["eval_set_status"] = "not_generated"
            missed.append(entry)

    return {"missed": missed, "message": None}
