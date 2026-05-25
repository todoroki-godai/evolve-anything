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


def compute_negative_transfer(
    usage_data: List[Dict[str, Any]],
    delta_threshold: float = -0.05,
    window: int = 10,
) -> List[Dict[str, Any]]:
    """スキル追加前後の成功率 delta を計算して負の転移を検出する。

    arXiv 2605.23899 の知見: スキル追加が他スキルのパフォーマンスを下げる
    「負の転移」を測定する。

    実際の usage.jsonl スキーマ（{skill_name, ts, session_id, outcome}）に対応。

    アルゴリズム:
    - 「スキル追加」= usage_data に初めて登場するスキル名の最初のレコード
    - 「fitness_score」= outcome=="success" の比率（rolling window）で代替
    - before_rate: スキル追加前 window 件の success 率
    - after_rate: スキル追加後 window 件の success 率
    - delta = after_rate - before_rate < delta_threshold → negative_transfer フラグ

    Args:
        usage_data: usage.jsonl 由来のレコードリスト（{skill_name, ts, outcome, ...}）
        delta_threshold: 負の転移と判定するスコア変化の閾値（デフォルト -0.05）
        window: before/after それぞれ最大 N 件のレコードを使用

    Returns:
        [{"skill_name": str, "delta_score": float,
          "negative_transfer": bool, "before_score": float, "after_score": float}]

    エッジケース:
    - usage_data が空 → []
    - スキルが1件のみ（追加イベントなし）→ []
    - before/after データが不足（window 内のレコードなし）→ skip
    """
    if not usage_data:
        return []

    def _get_ts(rec: Dict[str, Any]) -> str:
        """ts または timestamp フィールドを取得する。"""
        return rec.get("ts") or rec.get("timestamp") or ""

    # タイムスタンプでソート
    sorted_data = sorted(
        [r for r in usage_data if _get_ts(r)],
        key=_get_ts,
    )

    if not sorted_data:
        return []

    # 各スキルの「初回登場タイムスタンプ」を収集（スキル追加の代理）
    skill_first_seen: Dict[str, str] = {}
    for rec in sorted_data:
        skill = rec.get("skill_name", "")
        if not skill:
            continue
        if skill not in skill_first_seen:
            skill_first_seen[skill] = _get_ts(rec)

    # スキルが1種類以下（新規追加スキルを検出できない）
    if len(skill_first_seen) < 2:
        return []

    # 最初に登場したスキル（既存スキルの基準）と、後から登場したスキル（新規追加）を分離
    # 最も早い登場時刻を「既存環境の基準点」とする
    sorted_skills_by_ts = sorted(skill_first_seen.items(), key=lambda x: x[1])
    baseline_ts = sorted_skills_by_ts[0][1]  # 最初のスキルの登場時刻

    # 2番目以降に登場したスキルをスキル追加イベントとして扱う
    # 追加スキルの中で最初に登場したものを「転移点」とする
    skill_added_ts: Optional[str] = None
    for skill, ts in sorted_skills_by_ts[1:]:
        if ts > baseline_ts:
            skill_added_ts = ts
            break

    if skill_added_ts is None:
        return []

    # skill_added_ts の前後で既存スキルの success 率を比較
    # 「既存スキル」= skill_added_ts より前に初回登場したスキル
    existing_skills = {skill for skill, ts in skill_first_seen.items() if ts < skill_added_ts}

    before_records: Dict[str, List[str]] = {}
    after_records: Dict[str, List[str]] = {}

    for rec in sorted_data:
        skill = rec.get("skill_name", "")
        outcome = rec.get("outcome")
        ts = _get_ts(rec)
        if not skill or outcome not in ("success", "error") or not ts:
            continue
        if skill not in existing_skills:
            continue
        if ts < skill_added_ts:
            before_records.setdefault(skill, []).append(outcome)
        elif ts > skill_added_ts:
            after_records.setdefault(skill, []).append(outcome)

    # 前後両方データがあるスキルのみ delta を計算
    results: List[Dict[str, Any]] = []
    all_skills = set(before_records.keys()) & set(after_records.keys())
    for skill in sorted(all_skills):
        before_window = before_records[skill][-window:]
        after_window = after_records[skill][:window]
        if not before_window or not after_window:
            continue
        before_rate = sum(1 for o in before_window if o == "success") / len(before_window)
        after_rate = sum(1 for o in after_window if o == "success") / len(after_window)
        delta = after_rate - before_rate
        results.append({
            "skill_name": skill,
            "delta_score": delta,
            "negative_transfer": delta < delta_threshold,
            "before_score": before_rate,
            "after_score": after_rate,
        })

    return results


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
