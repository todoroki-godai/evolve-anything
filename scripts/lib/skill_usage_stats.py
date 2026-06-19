#!/usr/bin/env python3
"""skill_activations.jsonl からグローバルスキルの使用状況を集計する。

skill_activation_log.py フック（PostToolUse Skill）が記録した
skill_activations.jsonl を読み込み、インストール済みスキルと照合して
未使用スキルを検出する。

prune / audit がこのモジュールを使って、データドリブンなスキル整理を実現する。

## invocation_trigger の活用
各レコードには "top-level"（ユーザーが直接呼んだ）/ "nested-skill"（別スキルから呼ばれた）
が記録される。この情報を使って以下を判別できる:
- top_level_count=0, nested_count>0 → ユーザーが直接呼ばない参照/ユーティリティ型
  → 単独削除よりも呼び出し元スキルへのマージを検討
- top_level_count>0 → ユーザーが直接使うアクション型 → 使用頻度で削除判断

## 将来拡張
親スキル特定（"どのスキルから呼ばれたか"）は skill_activation_log.py に
parent_skill フィールドを追加することで実現可能。現時点では呼び出し元不明。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from rl_common import hook_store_path

GLOBAL_SKILLS_DIR = Path.home() / ".claude" / "skills"
DEFAULT_DAYS = 90


def _default_activations_file() -> Path:
    """skill_activations.jsonl の正準パス（hook が書く plugin-data dir）。

    hook（PostToolUse Skill）が plugin-data dir に書くため、tool 実行時（env 未設定）でも
    hook-writer 系 resolver で正準 dir を解決する（#358）。
    """
    return hook_store_path("skill_activations.jsonl")


def load_skill_activations(
    days: int = DEFAULT_DAYS,
    activations_file: Optional[Path] = None,
) -> Dict[str, dict]:
    """skill_activations.jsonl を読み込み、スキル別集計を返す。

    Returns:
        {skill_name: {
            "count": int,
            "top_level_count": int,   # ユーザーが直接呼んだ回数
            "nested_count": int,      # 別スキルから呼ばれた回数
            "last_used": str,
            "days_since": float,
        }}

    Note:
        プラグイン prefix（例: "evolve-anything:audit"）は除去して "audit" として集計。
        元の形式と除去後の形式の両方をキーとして登録する。
    """
    filepath = activations_file or _default_activations_file()
    if not filepath.exists():
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats: Dict[str, dict] = {}

    def _blank() -> dict:
        return {"count": 0, "top_level_count": 0, "nested_count": 0, "last_used": "", "days_since": 0.0, "callers": {}}

    with filepath.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = rec.get("ts", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < cutoff:
                continue

            skill = rec.get("skill", "")
            if not skill:
                continue

            trigger = rec.get("invocation_trigger", "unknown")
            is_nested = trigger == "nested-skill"

            parent = rec.get("parent_skill")

            # 正規化: "evolve-anything:audit" → "audit" も別キーで登録
            base = skill.split(":")[-1] if ":" in skill else skill
            for key in ({skill, base} if base != skill else {skill}):
                if key not in stats:
                    stats[key] = _blank()
                stats[key]["count"] += 1
                if is_nested:
                    stats[key]["nested_count"] += 1
                    # parent_skill ごとの呼び出し回数を集計（マージ先特定用）
                    if parent:
                        parent_base = parent.split(":")[-1] if ":" in parent else parent
                        stats[key]["callers"][parent_base] = stats[key]["callers"].get(parent_base, 0) + 1
                else:
                    stats[key]["top_level_count"] += 1
                if ts_str > stats[key]["last_used"]:
                    stats[key]["last_used"] = ts_str

    now = datetime.now(timezone.utc)
    for info in stats.values():
        try:
            last = datetime.fromisoformat(info["last_used"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            info["days_since"] = (now - last).total_seconds() / 86400
        except ValueError:
            info["days_since"] = float(days)

    return stats


def get_installed_global_skills() -> List[str]:
    """~/.claude/skills/ 配下の有効なスキル名を返す。

    SKILL.md が存在するディレクトリのみを対象とする（壊れたシンボリックリンク除外）。
    """
    if not GLOBAL_SKILLS_DIR.exists():
        return []
    skills = []
    for p in GLOBAL_SKILLS_DIR.iterdir():
        if p.is_dir() and (p / "SKILL.md").exists():
            skills.append(p.name)
    return sorted(skills)


def find_unused_global_skills(
    days: int = DEFAULT_DAYS,
    activations_file: Optional[Path] = None,
) -> List[Dict]:
    """指定期間内に一度も使用されていないグローバルスキルを返す。

    skill_activations.jsonl にデータがない場合（蓄積前）は空リストを返す。
    参照型スキルの判定は呼び出し元（prune.py）が行う。

    Returns:
        [{"skill_name": str, "days_no_use": int}]
    """
    filepath = activations_file or _default_activations_file()
    if not filepath.exists():
        return []  # データなし → 蓄積待ち

    installed = get_installed_global_skills()
    stats = load_skill_activations(days=days, activations_file=activations_file)

    return [
        {"skill_name": name, "days_no_use": days}
        for name in installed
        if name not in stats
    ]


def find_rarely_used_global_skills(
    days: int = DEFAULT_DAYS,
    threshold: int = 3,
    activations_file: Optional[Path] = None,
) -> List[Dict]:
    """指定期間内の使用が threshold 未満（1以上）のグローバルスキルを返す。

    Returns:
        [{"skill_name": str, "count": int, "last_used": str, "days_since": float,
          "top_level_count": int, "nested_count": int}]
    """
    filepath = activations_file or _default_activations_file()
    if not filepath.exists():
        return []

    installed = get_installed_global_skills()
    stats = load_skill_activations(days=days, activations_file=activations_file)

    rarely = []
    for name in installed:
        if name in stats:
            count = stats[name]["count"]
            if 0 < count < threshold:
                rarely.append({
                    "skill_name": name,
                    "count": count,
                    "top_level_count": stats[name]["top_level_count"],
                    "nested_count": stats[name]["nested_count"],
                    "last_used": stats[name]["last_used"],
                    "days_since": round(stats[name]["days_since"], 1),
                })

    return sorted(rarely, key=lambda x: x["count"])


def find_nested_only_skills(
    days: int = DEFAULT_DAYS,
    activations_file: Optional[Path] = None,
) -> List[Dict]:
    """top-level 呼び出しがなく nested-skill としてのみ使われているスキルを返す。

    これらは「ユーザーが直接呼ばない参照/ユーティリティ型スキル」の候補。
    削除よりも呼び出し元スキルへのマージが適切なことが多い。
    注: 現時点では呼び出し元スキル名は特定できない（parent_skill フィールド未実装）。

    Returns:
        [{"skill_name": str, "nested_count": int, "last_used": str}]
    """
    filepath = activations_file or _default_activations_file()
    if not filepath.exists():
        return []

    installed = get_installed_global_skills()
    stats = load_skill_activations(days=days, activations_file=activations_file)

    result = []
    for name in installed:
        if name in stats:
            s = stats[name]
            if s["top_level_count"] == 0 and s["nested_count"] > 0:
                result.append({
                    "skill_name": name,
                    "nested_count": s["nested_count"],
                    "last_used": s["last_used"],
                    "days_since": round(s["days_since"], 1),
                })

    return sorted(result, key=lambda x: x["nested_count"], reverse=True)


def find_merge_candidates(
    days: int = DEFAULT_DAYS,
    activations_file: Optional[Path] = None,
) -> List[Dict]:
    """マージ推奨スキルを返す。

    nested-only かつ呼び出し元が1スキルに集中している場合、
    そのスキルへのマージを提案する。

    Returns:
        [{
            "skill_name": str,       # マージされるべきスキル
            "nested_count": int,
            "merge_into": str,       # マージ先候補（最多呼び出し元）
            "confidence": str,       # "high"（1スキルのみ）or "low"（複数呼び出し元）
            "callers": dict,         # {caller: count}
        }]
    """
    nested_only = find_nested_only_skills(days=days, activations_file=activations_file)
    if not nested_only:
        return []

    stats = load_skill_activations(days=days, activations_file=activations_file)
    candidates = []

    for item in nested_only:
        name = item["skill_name"]
        callers = stats.get(name, {}).get("callers", {})
        if not callers:
            # parent_skill 未記録（旧データ）→ マージ先不明
            candidates.append({
                "skill_name": name,
                "nested_count": item["nested_count"],
                "merge_into": None,
                "confidence": "unknown",
                "callers": {},
            })
            continue

        # 最多呼び出し元を merge_into 候補とする
        top_caller = max(callers, key=lambda k: callers[k])
        confidence = "high" if len(callers) == 1 else "low"
        candidates.append({
            "skill_name": name,
            "nested_count": item["nested_count"],
            "merge_into": top_caller,
            "confidence": confidence,
            "callers": callers,
        })

    return candidates


def get_skill_activation_summary(
    days: int = DEFAULT_DAYS,
    activations_file: Optional[Path] = None,
) -> Dict:
    """audit 向けサマリー: 未使用数・低頻度数・nested-only 数・データ有無を返す。

    Returns:
        {
            "has_data": bool,
            "days": int,
            "total_installed": int,
            "unused_count": int,
            "rarely_used_count": int,
            "nested_only_count": int,   # マージ候補
            "unused": [...],
            "rarely_used": [...],
            "nested_only": [...],
        }
    """
    installed = get_installed_global_skills()
    unused = find_unused_global_skills(days=days, activations_file=activations_file)
    rarely = find_rarely_used_global_skills(days=days, activations_file=activations_file)
    nested_only = find_nested_only_skills(days=days, activations_file=activations_file)
    merge_candidates = find_merge_candidates(days=days, activations_file=activations_file)

    filepath = activations_file or _default_activations_file()
    return {
        "has_data": filepath.exists(),
        "days": days,
        "total_installed": len(installed),
        "unused_count": len(unused),
        "rarely_used_count": len(rarely),
        "nested_only_count": len(nested_only),
        "merge_candidate_count": len(merge_candidates),
        "unused": unused,
        "rarely_used": rarely,
        "nested_only": nested_only,
        "merge_candidates": merge_candidates,
    }
