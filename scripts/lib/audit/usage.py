"""usage.jsonl 読み込み + スキル使用集計。

audit パッケージから切り出された Usage モジュール。
- load_usage_data: usage.jsonl から直近N日のレコードを取得
- _is_openspec_skill / _is_plugin_skill: スキル名分類ヘルパー
- aggregate_usage: スキル使用回数（基本ツール除外、プラグイン除外オプション）
- aggregate_plugin_usage: プラグイン名で集計
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_classifier import BUILTIN_AGENT_NAMES

from .classification import classify_usage_skill
from .gstack import _is_gstack_skill


_BUILTIN_TOOLS = {f"Agent:{n}" for n in BUILTIN_AGENT_NAMES} | {"commit"}


def load_usage_data(
    days: int = 30,
    *,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """usage.jsonl から直近N日のデータを読み込む。

    Args:
        days: 直近何日分のデータを読み込むか。
        project_root: 指定時は該当プロジェクトのレコードのみ返す。
    """
    from telemetry_query import query_usage

    # DATA_DIR は audit パッケージ経由で取得（テストが audit.DATA_DIR を
    # mock.patch.object で差し替えるケースに追従するため遅延参照）
    from . import DATA_DIR as _DATA_DIR

    project_name = project_root.name if project_root else None
    include_unknown = project_root is None
    records = query_usage(
        project=project_name,
        include_unknown=include_unknown,
        usage_file=_DATA_DIR / "usage.jsonl",
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r for r in records if (r.get("ts") or r.get("timestamp") or "") >= cutoff]


def _is_openspec_skill(skill_name: str) -> bool:
    """スキル名が OpenSpec 関連（レガシー）かどうかを判定する。"""
    if not skill_name:
        return False
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return "openspec" in base or base.startswith("opsx:")


def _is_plugin_skill(skill_name: str) -> bool:
    """スキル名がプラグイン由来かどうかを判定する。

    classify_usage_skill（完全一致 + prefix マッチ）、_is_gstack_skill、
    _is_openspec_skill（レガシー）を併用。
    """
    if classify_usage_skill(skill_name) is not None:
        return True
    if _is_gstack_skill(skill_name):
        return True
    if _is_openspec_skill(skill_name):
        return True
    return False


def aggregate_usage(
    records: List[Dict[str, Any]],
    exclude_plugins: bool = False,
) -> Dict[str, int]:
    """スキル使用回数を集計する。基本ツールはノイズのため除外。

    Args:
        records: usage レコードのリスト
        exclude_plugins: True の場合、プラグインスキルを除外して PJ 固有のみ返す
    """
    counts: Dict[str, int] = {}
    for rec in records:
        skill = rec.get("skill_name", "unknown")
        if skill in _BUILTIN_TOOLS:
            continue
        if exclude_plugins and _is_plugin_skill(skill):
            continue
        counts[skill] = counts.get(skill, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def aggregate_contribution_scores(
    records: List[Dict[str, Any]],
    min_invocations: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """スキル別の貢献スコアを算出する。

    outcome フィールドを持つレコードのみを集計対象とする。
    invocations が min_invocations 未満のスキルは score=None（データ不足）とする。

    Returns:
        {skill_name: {"score": float|None, "success": int, "error": int, "total": int}}
    """
    buckets: Dict[str, Dict[str, int]] = {}
    for rec in records:
        skill = rec.get("skill_name", "")
        outcome = rec.get("outcome")
        if not skill or outcome not in ("success", "error", "skip"):
            continue
        if skill in _BUILTIN_TOOLS:
            continue
        b = buckets.setdefault(skill, {"success": 0, "error": 0, "skip": 0})
        b[outcome] = b.get(outcome, 0) + 1

    result: Dict[str, Dict[str, Any]] = {}
    for skill, b in buckets.items():
        total = b["success"] + b["error"] + b.get("skip", 0)
        score: float | None = None
        if total >= min_invocations:
            score = b["success"] / total if total > 0 else None
        result[skill] = {
            "score": score,
            "success": b["success"],
            "error": b["error"],
            "total": total,
        }
    return result


def aggregate_plugin_usage(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """プラグイン別の使用回数を集計する。

    classify_usage_skill でプラグイン名が判定できるものはプラグイン名で集計。
    gstack スキルは "gstack" として、OpenSpec レガシーは "openspec(legacy)" として集計。

    Returns:
        {plugin_name: total_count} の辞書（降順ソート）
    """
    plugin_counts: Dict[str, int] = {}
    for rec in records:
        skill = rec.get("skill_name", "unknown")
        if skill in _BUILTIN_TOOLS:
            continue
        plugin_name = classify_usage_skill(skill)
        if plugin_name:
            plugin_counts[plugin_name] = plugin_counts.get(plugin_name, 0) + 1
        elif _is_gstack_skill(skill):
            key = "gstack"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
        elif _is_openspec_skill(skill):
            key = "openspec(legacy)"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
    return dict(sorted(plugin_counts.items(), key=lambda x: x[1], reverse=True))
