"""measurement_bug メタ検査（#445, advisory / Closes #185）。

learning_measurement_layer_diagnosis: 「全 PJ 同値カウント = 測定バグ強シグナル」。
#419-#423 で手動診断した「独立に走査したはずの PJ が偶然 bit-exact に揃う」現象を
自動化して audit/evolve に advisory surface する。スコア重みには入れない。

決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出する。
0 同値は未測定・データ不足で正当に起きる（#423 既出）ため除外し FP を構造的に避ける。
precision 優先は ADR-043 の方針と整合。fleet status 側の `detect_equal_issue_counts`
（#419）と同じ検出方針を共有する（閾値は ≥3 PJ で精度を上げる — audit は全 PJ 横断の
集計値を見るため、issues 単独より広い metric 群を扱う）。

データ源は growth-state-*.json walk（evolve-fleet status と同経路）。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

# テストは ``monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)`` で
# 直接この module 属性を差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

# ≥3 PJ で bit-exact 一致したら候補（1-2 PJ 一致は偶然で起きうるため無視）。
_MIN_MATCHING_PJ = 3

# growth-state cache の issues_summary を構成する 5 フィールド（#419 と共有する契約）。
# audit 側の issues_summary.IssuesSummary / fleet 側 audit_runner.IssuesSummary と
# 同名で繋がる。total はこの合計。
_ISSUES_FIELDS = (
    "line_violations",
    "hardcoded_values",
    "potential_duplicates",
    "corrections_unprocessed",
    "skill_quality_degraded_count",
)


def _is_trivial(value: Any) -> bool:
    """0 / 0.0 / None / 非数値は非自明値でない（一致検出の対象外）。"""
    if value is None:
        return True
    if isinstance(value, bool):  # bool は int サブクラスだが metric ではない
        return True
    if not isinstance(value, (int, float)):
        return True
    return value == 0


def detect_measurement_bug(
    metrics_by_pj: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """metric 別に PJ 間の bit-exact 一致を検出し、測定バグ候補を返す。

    Args:
        metrics_by_pj: {metric_name: {pj_name: value}}。

    決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出する。
    ≥3 PJ が同一の非自明値を共有したら 1 件の候補として返す。

    Returns:
        [{"metric": str, "value": <int|float>, "projects": [pj, ...]}]。
        一致グループごとに 1 件。候補なしなら空リスト。
    """
    alarms: List[Dict[str, Any]] = []
    for metric, by_pj in metrics_by_pj.items():
        by_value: Dict[Any, List[str]] = {}
        for pj, value in by_pj.items():
            if _is_trivial(value):
                continue  # 0 / 0.0 / None 同値は健全（#423）→ 一致判定から除外
            by_value.setdefault(value, []).append(pj)
        for value, projects in by_value.items():
            if len(projects) >= _MIN_MATCHING_PJ:
                alarms.append(
                    {"metric": metric, "value": value, "projects": sorted(projects)}
                )
    return alarms


def _issues_total(summary: Any) -> Any:
    """growth-state cache の issues_summary dict から total（5 フィールド合計）を算出。

    dict でない / 全フィールド欠落なら None（未測定として一致判定から除外される）。
    """
    if not isinstance(summary, dict):
        return None
    total = 0
    seen = False
    for field in _ISSUES_FIELDS:
        v = summary.get(field)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += v
            seen = True
    return total if seen else None


def collect_cross_pj_metrics(data_dir: Path | None = None) -> Dict[str, Dict[str, Any]]:
    """DATA_DIR 配下の growth-state-*.json を walk し、PJ 横断の metric を集める。

    evolve-fleet status と同経路（growth-state cache を唯一の真実とする）。

    Returns:
        {metric_name: {pj_name: value}}。読めない / 破損ファイルは skip する。
        現状の metric: env_score（float）/ issues_total（int, 5 フィールド合計）。
    """
    base = data_dir if data_dir is not None else DATA_DIR
    base = Path(base)
    if not base.is_dir():
        return {}

    metrics: Dict[str, Dict[str, Any]] = {"env_score": {}, "issues_total": {}}
    prefix = "growth-state-"
    suffix = ".json"
    for path in sorted(base.glob(f"{prefix}*{suffix}")):
        pj_name = path.name[len(prefix) : -len(suffix)]
        if not pj_name:
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue  # 破損 1 ファイルを skip（全件落とさない）
        if not isinstance(state, dict):
            continue
        env_score = state.get("env_score")
        if isinstance(env_score, (int, float)) and not isinstance(env_score, bool):
            metrics["env_score"][pj_name] = env_score
        issues_total = _issues_total(state.get("issues_summary"))
        if issues_total is not None:
            metrics["issues_total"][pj_name] = issues_total

    # 空 metric は除去して呼び出し側を簡潔に保つ
    return {m: d for m, d in metrics.items() if d}
