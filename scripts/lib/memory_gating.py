"""ストレージゲーティング: corrections の重要度スコアリングによる保存前フィルタリング。

LLM 呼び出し一切なし。再発頻度 / 新規性 / 影響度の3軸スコアで
composite >= threshold のものだけ LLM memory 生成に進む。

Phase 1 (issue #239)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

try:
    from similarity import jaccard_coefficient
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from similarity import jaccard_coefficient


# デフォルト閾値
DEFAULT_THRESHOLD = 0.5

# 再発頻度計算のウィンドウサイズ（直近 N 件）
RECURRENCE_WINDOW = 50

# 重みパラメータ
_W_RECURRENCE = 0.4
_W_NOVELTY = 0.4
_W_SEVERITY = 0.2


@dataclass
class GatingScore:
    """ゲーティングスコアの結果。"""

    recurrence_score: float  # 再発頻度 (0.0-1.0)
    novelty_score: float     # 既存メモリとの非重複度 (0.0-1.0)
    severity_score: float    # 修正の影響度 (0.0-1.0)
    composite: float         # 加重平均
    should_store: bool       # composite >= threshold


def _jaccard_similarity(a: str, b: str) -> float:
    """2文字列間の単語 Jaccard 類似度を計算する（大文字小文字を区別しない）。

    トークン化は空白分割（.lower().split()）を維持し、係数計算のみ
    similarity.jaccard_coefficient に委譲する（数式の単一ソース化）。
    """
    return jaccard_coefficient(set(a.lower().split()), set(b.lower().split()))


def _compute_recurrence_score(correction: dict, all_corrections: List[dict]) -> float:
    """corrections.jsonl の直近 RECURRENCE_WINDOW 件から同一パターンの再発頻度を計算する。

    correction の `pattern` キーが all_corrections の直近ウィンドウ内で何回出現するかを
    正規化して返す（1回出現 = 0.0、複数回出現で高スコア）。
    `pattern` キーがない場合は `message` の先頭 40 文字をキーとする。
    """
    window = all_corrections[-RECURRENCE_WINDOW:]

    # pattern キー優先、なければ message 先頭 40 文字
    def _key(c: dict) -> str:
        return str(c.get("pattern") or c.get("message", "")[:40])

    target_key = _key(correction)
    if not target_key:
        return 0.0

    count = sum(1 for c in window if _key(c) == target_key)
    # 1回のみ出現 → 0.0、2回 → 0.5、3回以上 → 1.0 に近づく
    if count <= 1:
        return 0.0
    # min(1.0, (count - 1) / 2) で 3回出現で最大
    return min(1.0, (count - 1) / 2.0)


def _compute_novelty_score(correction: dict, existing_memories: List[str]) -> float:
    """既存メモリとの非重複度を計算する（高い = 新規性あり）。

    correction の `message` を既存メモリの各テキストと Jaccard 類似度で比較し、
    最大類似度の逆数を novelty_score とする。
    既存メモリが空の場合は 1.0（完全新規）を返す。
    """
    message = str(correction.get("message", ""))
    if not message:
        return 0.5  # メッセージ不明は中程度

    if not existing_memories:
        return 1.0

    max_sim = max(
        (
            _jaccard_similarity(message, mem)
            for mem in existing_memories
            if mem  # 空文字列除外
        ),
        default=0.0,
    )

    return 1.0 - max_sim


def _compute_severity_score(correction: dict) -> float:
    """correction の type フィールドから影響度を計算する。

    - "correction" → 0.8（直接的な誤り修正）
    - "feedback"   → 0.5（フィードバック）
    - それ以外      → 0.3
    """
    ctype = str(correction.get("type") or correction.get("correction_type") or "")
    if ctype == "correction":
        return 0.8
    if ctype == "feedback":
        return 0.5
    return 0.3


def score_correction(
    correction: dict,
    existing_memories: List[str],
    all_corrections: List[dict] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> GatingScore:
    """correction の保存優先度を3軸スコアで評価する。

    Args:
        correction:        評価対象の correction dict。
        existing_memories: 既存の memory テキスト一覧（novelty 比較用）。
        all_corrections:   corrections.jsonl の全レコード（recurrence 計算用）。
                           None の場合は [correction] として扱う（単体評価）。
        threshold:         should_store の判定閾値（デフォルト 0.5）。

    Returns:
        GatingScore: スコア詳細と should_store フラグ。
    """
    _all = all_corrections if all_corrections is not None else [correction]

    recurrence = _compute_recurrence_score(correction, _all)
    novelty = _compute_novelty_score(correction, existing_memories)
    severity = _compute_severity_score(correction)

    composite = (
        recurrence * _W_RECURRENCE
        + novelty * _W_NOVELTY
        + severity * _W_SEVERITY
    )
    composite = max(0.0, min(1.0, composite))

    return GatingScore(
        recurrence_score=recurrence,
        novelty_score=novelty,
        severity_score=severity,
        composite=composite,
        should_store=composite >= threshold,
    )
