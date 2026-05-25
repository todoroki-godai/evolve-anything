"""メタ品質チェック: 汎用性・再利用頻度・意味的重複度を評価する。

LLM 不使用。単語トークン集合の Jaccard 類似度で意味的重複を近似する。
Issue #203 / PR-D1b
"""
from typing import Dict, List


# 再利用頻度の閾値
LOW_REUSE_THRESHOLD = 0.1

# Jaccard 類似度の重複判定閾値
DUPLICATE_JACCARD_THRESHOLD = 0.6

# 特化型判定: スキル説明に固有名詞（大文字英数字・PJ 名）が多い場合のスコア閾値
_SPECIALIZED_RATIO_THRESHOLD = 0.4


def _jaccard_similarity(a: str, b: str) -> float:
    """2文字列間の単語 Jaccard 類似度を計算する（大文字小文字を区別しない）。"""
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _is_specialized(skill_content: str) -> bool:
    """スキル説明に特定 PJ 固有の名詞が多い場合に True を返す。

    ヒューリスティック: 大文字英字トークン（略語・固有名詞）の割合が
    _SPECIALIZED_RATIO_THRESHOLD を超える場合を特化型とみなす。
    """
    tokens = skill_content.split()
    if not tokens:
        return False
    specialized = sum(1 for t in tokens if t and t[0].isupper() and len(t) > 1)
    return (specialized / len(tokens)) > _SPECIALIZED_RATIO_THRESHOLD


def meta_quality_check(
    skill_name: str,
    skill_content: str,
    usage_stats: Dict,
    all_skills: List[str],
) -> Dict:
    """メタ品質チェック: 汎用性・再利用頻度・意味的重複度を評価する。

    評価軸:
    1. 再利用頻度: trigger_count / session_count < 0.1 → 低頻度フラグ
    2. 汎用性: スキル説明に特定 PJ 固有の名詞が多い場合 → 特化型フラグ
    3. 意味的重複: 既存スキル名との Jaccard 類似度 > 0.6 → 重複候補フラグ

    判定ルール:
    - low_reuse=True AND duplicate_candidates 非空 → "SKIP"
    - duplicate_candidates 非空 → "REVIEW"
    - それ以外 → "CREATE"

    Returns:
        {
            "skill_name": str,
            "reuse_rate": float,
            "low_reuse": bool,
            "is_specialized": bool,
            "duplicate_candidates": list[str],
            "recommendation": "CREATE" | "SKIP" | "REVIEW",
            "reason": str,
        }
    """
    trigger_count = usage_stats.get("trigger_count", 0)
    session_count = usage_stats.get("session_count", 0)

    # ZeroDivision ガード
    reuse_rate: float = 0.0
    if session_count > 0:
        reuse_rate = trigger_count / session_count

    low_reuse = reuse_rate < LOW_REUSE_THRESHOLD
    is_spec = _is_specialized(skill_content)

    # 意味的重複チェック（自分自身を除く）
    duplicate_candidates: List[str] = []
    for existing in all_skills:
        if existing == skill_name:
            continue
        sim = _jaccard_similarity(skill_name, existing)
        if sim > DUPLICATE_JACCARD_THRESHOLD:
            duplicate_candidates.append(existing)

    # 判定
    if low_reuse and duplicate_candidates:
        recommendation = "SKIP"
        reason = f"低頻度 (reuse_rate={reuse_rate:.3f}) かつ重複候補: {duplicate_candidates}"
    elif duplicate_candidates:
        recommendation = "REVIEW"
        reason = f"重複候補あり: {duplicate_candidates}"
    else:
        recommendation = "CREATE"
        reason = "重複なし"
        if low_reuse:
            reason += f" (低頻度: reuse_rate={reuse_rate:.3f})"

    return {
        "skill_name": skill_name,
        "reuse_rate": reuse_rate,
        "low_reuse": low_reuse,
        "is_specialized": is_spec,
        "duplicate_candidates": duplicate_candidates,
        "recommendation": recommendation,
        "reason": reason,
    }
