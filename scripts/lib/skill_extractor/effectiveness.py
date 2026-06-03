"""effectiveness — 軌跡有効性の実証特徴量を計算する。

"What Makes Interaction Trajectories Effective for Training Terminal Agents?"
(arXiv:2606.03461) は、端末エージェント訓練に有効な相互作用軌跡の条件を実証的に
調査した。本モジュールは、その実証基準のうち TrajectoryRecord（skill_name /
user_prompt / outcome / session_id / timestamp）から決定論的に観測可能な特徴を
抽出し、generalizability_score の算定根拠を補強する。

実証基準 → 観測特徴の対応:

- 多様性 (diversity): 同一スキルが多様な文脈（異なる user_prompt / session）で
  呼ばれているほど汎用的。単一プロンプトの機械的反復は汎用性が低い。
- 反復性 (recurrence): 複数の独立セッションに分散して再発しているほど、一過性
  でない恒常的なニーズを示す。1 セッションに集中した連投は recurrence が低い。
- 成功/失敗のコントラスト (contrast): 成功と失敗の両方を含む軌跡は学習信号
  （何が効いて何が効かないか）が豊富。全成功・全失敗で contrast が無い軌跡は
  情報量が乏しい。論文は contrastive trajectory の有効性を報告している。

これらを 0.0〜1.0 の係数 `effectiveness_multiplier` に統合し、既存スコア式に
乗算ブレンドする。signal が無い（または記録が乏しい）場合は中立値に倒し、
スコアを既存挙動から大きく動かさない（後方互換）。

LLM 呼び出し一切なし・決定論。

Issue #306
"""
from __future__ import annotations

from typing import Any, List, Sequence

# ── 定数 ──────────────────────────────────────────────────

# effectiveness 各特徴のブレンド重み（合計 1.0）。
DIVERSITY_WEIGHT = 0.4
RECURRENCE_WEIGHT = 0.4
CONTRAST_WEIGHT = 0.2

# effectiveness_multiplier が既存スコアに与える振れ幅。
# multiplier = MIN_MULTIPLIER + (1 - MIN_MULTIPLIER) * effectiveness
# effectiveness=0 → MIN_MULTIPLIER, effectiveness=1 → 1.0。
# 有効性が低い軌跡を「割り引く」が 0 にはしない（既存閾値挙動を温存）。
MIN_MULTIPLIER = 0.6

# contrast が満たされた（success と failure の両方を含む）ときの満点。
# 片側のみ（全成功 / 全失敗）の場合は CONTRAST_NEUTRAL を返す。
# 全成功は「失敗例が無い＝学習信号が少ない」が、悪い軌跡ではないので
# 中立寄り。全失敗は下げる。
CONTRAST_BOTH = 1.0
CONTRAST_ALL_SUCCESS = 0.6
CONTRAST_ALL_FAILURE = 0.3
CONTRAST_NEUTRAL = 0.5  # outcome が unknown のみ等、判定不能


# ── 公開関数 ──────────────────────────────────────────────


def compute_effectiveness(records: Sequence[Any]) -> float:
    """軌跡有効性スコア（0.0〜1.0）を計算する。

    diversity / recurrence / contrast の加重平均。

    Args:
        records: 同一スキルの TrajectoryRecord 風オブジェクトのシーケンス。
            各要素は user_prompt / outcome / session_id 属性を持つこと。

    Returns:
        0.0〜1.0 の有効性スコア。records が空の場合は 0.0。
    """
    if not records:
        return 0.0

    diversity = compute_diversity(records)
    recurrence = compute_recurrence(records)
    contrast = compute_contrast(records)

    score = (
        DIVERSITY_WEIGHT * diversity
        + RECURRENCE_WEIGHT * recurrence
        + CONTRAST_WEIGHT * contrast
    )
    return _clamp01(score)


def effectiveness_multiplier(records: Sequence[Any]) -> float:
    """effectiveness を既存スコアに乗算する係数（MIN_MULTIPLIER〜1.0）に変換する。

    records が空の場合は 1.0（中立 = 既存挙動を変えない）を返す。

    Args:
        records: 同一スキルの TrajectoryRecord 風オブジェクトのシーケンス。

    Returns:
        MIN_MULTIPLIER〜1.0 の乗算係数。
    """
    if not records:
        return 1.0
    eff = compute_effectiveness(records)
    return MIN_MULTIPLIER + (1.0 - MIN_MULTIPLIER) * eff


def compute_diversity(records: Sequence[Any]) -> float:
    """多様性スコア: 異なる user_prompt の比率（0.0〜1.0）。

    空でない user_prompt のうちユニークな割合。同一プロンプトの反復は
    多様性を下げる。プロンプトが全て空の場合は中立 (0.5) を返す。
    """
    prompts = [_norm_prompt(r) for r in records]
    non_empty = [p for p in prompts if p]
    if not non_empty:
        return 0.5
    unique = len(set(non_empty))
    return _clamp01(unique / len(non_empty))


def compute_recurrence(records: Sequence[Any]) -> float:
    """反復性スコア: 独立セッションへの分散度（0.0〜1.0）。

    distinct_sessions / total を基準に、1 セッション集中なら低く、
    多数のセッションに分散していれば高い。session_id が全て空/不明の
    場合は中立 (0.5)。total=1 の場合も判定不能として中立に倒す。
    """
    total = len(records)
    if total <= 1:
        return 0.5

    sessions = [_session_id(r) for r in records]
    non_empty = [s for s in sessions if s]
    if not non_empty:
        return 0.5

    distinct = len(set(non_empty))
    # distinct=1（全部同一セッション）→ 0、distinct=total（全部別）→ 1。
    # (distinct - 1) / (total - 1) で正規化。
    return _clamp01((distinct - 1) / (total - 1))


def compute_contrast(records: Sequence[Any]) -> float:
    """成功/失敗コントラストスコア（0.0〜1.0）。

    success と failure の両方を含むと CONTRAST_BOTH。
    全成功は CONTRAST_ALL_SUCCESS、全失敗は CONTRAST_ALL_FAILURE。
    判定可能な outcome（success/failure）が無ければ CONTRAST_NEUTRAL。
    """
    outcomes = [_outcome(r) for r in records]
    # success / failure を明確に数える（unknown は contrast 判定に含めない）。
    n_success = sum(1 for o in outcomes if o == "success")
    n_failure = sum(1 for o in outcomes if o == "failure")

    if n_success > 0 and n_failure > 0:
        return CONTRAST_BOTH
    if n_failure > 0:
        return CONTRAST_ALL_FAILURE
    if n_success > 0:
        return CONTRAST_ALL_SUCCESS
    return CONTRAST_NEUTRAL


# ── 内部ヘルパー ──────────────────────────────────────────


def _norm_prompt(record: Any) -> str:
    p = getattr(record, "user_prompt", "") or ""
    return p.strip()


def _session_id(record: Any) -> str:
    s = getattr(record, "session_id", "") or ""
    return s.strip()


def _outcome(record: Any) -> str:
    return getattr(record, "outcome", "") or ""


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
