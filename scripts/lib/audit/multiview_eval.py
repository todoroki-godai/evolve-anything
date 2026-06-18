"""多視点評価（multiview evaluation）— evolve 提案を4視点で決定論分類する集約レイヤ（#564）。

背景: 自己進化（evolve）提案の評価が「単一の accept/reject」に潰れており、同じ提案でも
「再利用可能な改善なのか / 過学習で局所最適なのか / 既存を壊す退行リスクなのか / 手戻りコストを
増やすのか」という **多視点** が見えなかった。本モジュールは新しい重い機構を作らず、既に存在する
3 部品の結果を skill 名で join し、各 evolve 対象スキルを4視点ラベルに **決定論的**（LLM 非依存）
に分類する純関数の薄い集約レイヤを提供する。

統合する既存3部品（各ソース関数を Read で確認した返り値構造）:
  - chaos.compute_chaos_score → importance_ranking[]: {name, layer, delta_score, criticality}
                                single_point_of_failure[]: {name, layer, delta_score}
    （仮想アブレーション: 除去で coherence がどれだけ落ちるか = そのスキルの重要度/SPOF）
  - outcome_attribution.attribute_outcomes → {skill: {first_try_success, rework,
                                              n_sessions, degraded}}
    （per-skill の一発成功率 / rework 率）
  - usage.compute_negative_transfer → [{skill_name, delta_score, negative_transfer,
                                        before_score, after_score}]
    （スキル追加前後の既存スキル success 率 delta）

4視点ラベル（観点の単一ソース。report がこれに意味を添える）:
  - reusable_improvement（再利用可能な改善）: chaos で効いている（important/critical）かつ
        アウトカム良好（first_try_success 高い）→ 横展開価値のある改善。
  - overfit_suspect（過学習疑い）: 少数セッションでアウトカムが悪い（first_try_success 低い）
        → 局所最適 / 過学習の疑い。母集団が十分なら（n_sessions 多い）疑いを下げる。
  - regression_risk（退行リスク）: chaos が SPOF（除去で大きく劣化）、または negative_transfer
        フラグが立っている → 改変が既存を壊す退行リスク。
  - cost_increase（コスト増）: rework 率が高い → 手戻りでコストが増えている。

判定は決定論（LLM 非依存）。入力は **in-memory の dict/list のみ** で、DATA_DIR を再読込しない
（dry-run 安全）。degraded（テレメトリ不足）の outcome は outcome 由来ラベルを出さず、沈黙でなく
「データ不足で評価不能」を degraded フラグで明示する（#393-#396 準拠）。

将来拡張フック（replay スナップショット比較）:
  本モジュールは現状「現在のスコア/アブレーション結果」のスナップショット1枚を分類する。SEAGym
  系の replay（提案 **適用前/後** の OOD ベンチ・スナップショット差分）を将来足すときは、
  ``classify_skill_multiview`` に ``replay_delta`` のような **追加の任意引数** を渡し、判定関数
  ``_label_*`` を1つ増やすだけで4視点に第5視点（例: ood_regression）を加えられる設計にしてある
  （重い replay 実行・スナップショット store の新設は本 issue のスコープ外。ここはフックのみ）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 4視点ラベルの単一ソース（report/section がこれを参照して意味を添える）。
LABEL_REUSABLE = "reusable_improvement"
LABEL_OVERFIT = "overfit_suspect"
LABEL_REGRESSION = "regression_risk"
LABEL_COST = "cost_increase"
# どの部品にも信号が無く分類不能なときのラベル（沈黙でなく明示）。
LABEL_UNKNOWN = "unknown"

LABEL_DESCRIPTIONS: Dict[str, str] = {
    LABEL_REUSABLE: "再利用可能な改善（効いていてアウトカムも良好 → 横展開価値）",
    LABEL_OVERFIT: "過学習疑い（少数セッションでアウトカムが悪い → 局所最適の疑い）",
    LABEL_REGRESSION: "退行リスク（SPOF / negative transfer → 改変が既存を壊す恐れ）",
    LABEL_COST: "コスト増（rework 率が高い → 手戻りでコスト増）",
}

# --- 判定しきい値（chaos の config.py / outcome_attribution と整合させる） ---
# chaos の SPOF / 重要度しきい値（fitness/config.py CHAOS_THRESHOLDS と一致）。
# config.py を import すると fitness パッケージのパス解決に依存するため、値だけ複製し
# テストで契約一致を担保する方が audit 層からは疎結合（複製 drift の検出ゲートは
# test_multiview_eval.test_chaos_thresholds_match_fitness_config が担う）。
_SPOF_DELTA = 0.15        # これ以上の delta_score は SPOF（除去で大きく劣化）。
_IMPORTANT_DELTA = 0.02   # これ以上は「効いている」（low と important の境界）。

# outcome 由来ラベルのしきい値。
_LOW_SUCCESS = 0.5        # first_try_success がこれ未満は「アウトカムが悪い」。
_GOOD_SUCCESS = 0.7       # first_try_success がこれ以上は「アウトカム良好」。
_FEW_SESSIONS = 5         # n_sessions がこれ未満は「母集団が小さい」（過学習を疑える）。
_HIGH_REWORK = 0.3        # rework 率がこれ以上は「手戻りが多い」。


def classify_skill_multiview(
    *,
    skill: str,
    chaos_entry: Optional[Dict[str, Any]],
    outcome_attr: Optional[Dict[str, Any]],
    neg_transfer: Optional[Dict[str, Any]],
    replay_delta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """1 スキル分の3部品レコードを受け取り、多視点ラベル + evidence を返す（純関数）。

    Args:
        skill: 対象スキル名。
        chaos_entry: chaos の importance_ranking から該当スキルの 1 件
            （{name, layer, delta_score, criticality}）。無ければ None。
        outcome_attr: outcome_attribution の該当スキル値
            （{first_try_success, rework, n_sessions, degraded}）。無ければ None。
        neg_transfer: compute_negative_transfer の該当スキル 1 件
            （{skill_name, delta_score, negative_transfer, ...}）。無ければ None。
        replay_delta: 将来拡張用フック（提案 適用前/後 の replay/OOD ベンチ差分）。
            現状は未使用（issue #564 スコープ外）。渡されても無視する。

    Returns:
        {
            "skill": str,
            "labels": [str, ...],   # 4視点ラベル（複数共存可）。信号皆無なら ["unknown"]。
            "evidence": {...},      # 各ラベルの根拠（数字に意味を添える）。
            "degraded": bool,       # 分類に足る信号が1つも無いか。
        }
    """
    labels: List[str] = []
    evidence: Dict[str, Any] = {}

    # --- chaos 由来の信号（重要度 / SPOF） ---
    chaos_delta: Optional[float] = None
    is_spof = False
    is_important = False
    if isinstance(chaos_entry, dict):
        chaos_delta = _as_float(chaos_entry.get("delta_score"))
        if chaos_delta is not None:
            is_spof = chaos_delta >= _SPOF_DELTA
            is_important = chaos_delta >= _IMPORTANT_DELTA
            evidence["chaos_delta"] = chaos_delta
            evidence["chaos_criticality"] = chaos_entry.get("criticality")

    # --- outcome 由来の信号（degraded は評価しない） ---
    fts: Optional[float] = None
    rework: Optional[float] = None
    n_sessions = 0
    outcome_usable = isinstance(outcome_attr, dict) and not outcome_attr.get("degraded")
    if outcome_usable:
        fts = _as_float(outcome_attr.get("first_try_success"))
        rework = _as_float(outcome_attr.get("rework"))
        n_sessions = int(outcome_attr.get("n_sessions") or 0)
        if fts is not None:
            evidence["first_try_success"] = fts
        if rework is not None:
            evidence["rework"] = rework
        evidence["n_sessions"] = n_sessions

    # --- negative transfer 由来の信号 ---
    neg_flag = bool(isinstance(neg_transfer, dict) and neg_transfer.get("negative_transfer"))
    if isinstance(neg_transfer, dict):
        evidence["negative_transfer_delta"] = _as_float(neg_transfer.get("delta_score"))

    # --- 視点ラベルの判定（決定論・複数共存可） ---
    # 退行リスク: SPOF（除去で大きく劣化）または negative transfer フラグ。
    if is_spof or neg_flag:
        labels.append(LABEL_REGRESSION)

    # 過学習疑い: 母集団が小さく（n_sessions 少）アウトカムが悪い（first_try 低い）。
    if fts is not None and fts < _LOW_SUCCESS and 0 < n_sessions < _FEW_SESSIONS:
        labels.append(LABEL_OVERFIT)

    # コスト増: rework 率が高い。
    if rework is not None and rework >= _HIGH_REWORK:
        labels.append(LABEL_COST)

    # 再利用可能な改善: chaos で効いている（important）かつアウトカム良好。
    if is_important and fts is not None and fts >= _GOOD_SUCCESS:
        labels.append(LABEL_REUSABLE)

    has_any_signal = (
        chaos_delta is not None or outcome_usable or isinstance(neg_transfer, dict)
    )
    if not labels:
        if not has_any_signal:
            return {
                "skill": skill,
                "labels": [LABEL_UNKNOWN],
                "evidence": evidence,
                "degraded": True,
            }
        # 信号はあるが、どの視点しきい値にも触れない（= 中立・問題なし）。
        # 沈黙でなく「評価したが該当ラベルなし」を空 labels + degraded=False で表す。

    return {
        "skill": skill,
        "labels": labels,
        "evidence": evidence,
        "degraded": not has_any_signal,
    }


def classify_multiview(
    *,
    target_skills: List[str],
    chaos_result: Optional[Dict[str, Any]],
    outcome_attribution: Dict[str, Dict[str, Any]],
    negative_transfer: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """evolve 対象スキル群を3部品の結果で join し、各スキルを多視点分類する（純関数）。

    Args:
        target_skills: 分類したい evolve 対象スキル名のリスト。
        chaos_result: chaos.compute_chaos_score の返り値（None 許容 = chaos 未実行）。
        outcome_attribution: attribute_outcomes の返り値（{skill: {...}}）。
        negative_transfer: compute_negative_transfer の返り値（[{skill_name, ...}]）。

    Returns:
        {skill: classify_skill_multiview(...) の返り値}。target_skills だけを返す。

    DATA_DIR を再読込しない（入力済みの結果だけを join する）= dry-run 安全。
    """
    chaos_by_skill = _index_chaos(chaos_result)
    neg_by_skill = {
        rec.get("skill_name", ""): rec
        for rec in (negative_transfer or [])
        if rec.get("skill_name")
    }

    out: Dict[str, Dict[str, Any]] = {}
    for skill in target_skills:
        out[skill] = classify_skill_multiview(
            skill=skill,
            chaos_entry=chaos_by_skill.get(skill),
            outcome_attr=(outcome_attribution or {}).get(skill),
            neg_transfer=neg_by_skill.get(skill),
        )
    return out


def summarize_labels(
    classified: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """分類結果をラベル別件数に畳む（report 用の軽量サマリ・決定論）。

    1 スキルが複数ラベルを持てるため合計は target 数を超えうる。unknown も件数に含める
    （silence != evaluated: 評価不能を 0 でなく明示する）。
    """
    counts: Dict[str, int] = {}
    for rec in classified.values():
        for label in rec.get("labels", []):
            counts[label] = counts.get(label, 0) + 1
    return counts


def _index_chaos(chaos_result: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """chaos の importance_ranking を skill 名 → entry の dict に index する。

    chaos は rules と skills の両方をアブレーションするため、同名衝突を避けて
    layer=="skills" を優先する（evolve 対象はスキル）。
    """
    if not isinstance(chaos_result, dict):
        return {}
    ranking = chaos_result.get("importance_ranking") or []
    by_skill: Dict[str, Dict[str, Any]] = {}
    for entry in ranking:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        # skills layer を優先（既に skills が入っていれば上書きしない）。
        if name in by_skill and by_skill[name].get("layer") == "skills":
            continue
        by_skill[name] = entry
    return by_skill


def _as_float(value: Any) -> Optional[float]:
    """None / 非数を安全に float|None へ（None 比較落ち pitfall を入口で潰す）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
