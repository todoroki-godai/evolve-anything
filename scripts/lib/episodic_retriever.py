"""episodic_retriever — episodic 層への昇格・重複検出ユーティリティ。

このモジュールは reflect.py から呼ばれる。直接的な DuckDB 操作は
episodic_store に委譲し、reflect.py が 800 行上限を超えないように分離する。

公開関数:
  promote_to_episodic     -- 適用済み correction を episodic 層に昇格
  find_episodic_duplicates -- corrections と episodic events の重複候補を返す
"""
from __future__ import annotations

import re
from typing import Any

try:
    from episodic_store import HAS_DUCKDB, insert_event, prune_expired, query_relevant
    from similarity import tokenize
except ImportError:
    # フォールバック: episodic 機能なしで動作継続
    HAS_DUCKDB = False  # type: ignore[assignment]

    def insert_event(*_, **__) -> bool:  # type: ignore[misc]
        return False

    def prune_expired(*_, **__) -> int:  # type: ignore[misc]
        return 0

    def query_relevant(*_, **__) -> list:  # type: ignore[misc]
        return []

    def tokenize(text: str) -> set:  # type: ignore[misc]
        return set(re.split(r"[\s\W_]+", text.lower())) - {""}


_MIN_KEYWORDS = 2  # キーワードが少なすぎると false positive が多い
_MIN_SCORE = 0.15  # これ未満のスコアは false positive として除外


def promote_to_episodic(correction: dict[str, Any]) -> bool:
    """適用済み correction を episodic 層に昇格する。

    reflect.py で ユーザーが correction を approve したタイミングで呼び出す。
    DuckDB 未インストール時・空メッセージ時は False を返す。
    DB エラー時も False (insert_event が stderr に warn を出す)。

    Args:
        correction: corrections.jsonl の 1 レコード。
            必須フィールド: message (content), session_id
            任意フィールド: project_path, correction_type, confidence

    Returns:
        True if successfully stored, False on skip or error.
    """
    if not HAS_DUCKDB:
        return False

    content = correction.get("message", "").strip()
    if not content:
        return False

    return insert_event(
        session_id=correction.get("session_id", "unknown"),
        project_path=correction.get("project_path"),
        content=content,
        correction_type=correction.get("correction_type"),
        confidence=correction.get("confidence"),
    )


def find_episodic_duplicates(
    corrections: list[dict[str, Any]],
    project_path: str | None,
) -> list[dict[str, Any]]:
    """corrections の各エントリと episodic events の重複候補を返す。

    reflect の build_output() から呼ばれ、各 correction に
    episodic_context を付与するためのデータを提供する。

    Args:
        corrections: corrections.jsonl の pending レコードリスト。
        project_path: 現在のプロジェクトパス。None = 全件対象。

    Returns:
        [{
            "correction_index": int,   # corrections リスト内のインデックス
            "episodic_id": str,
            "episodic_content": str,
            "days_ago": int,
            "score": float,
        }, ...]
        マッチがない場合・エラー時は空リスト。
    """
    if not HAS_DUCKDB or not corrections:
        return []

    # TTL 期限切れ行を機会的に掃除（呼び出し元でタイマーは管理しない）
    try:
        prune_expired()
    except Exception:
        pass

    results: list[dict[str, Any]] = []
    for idx, correction in enumerate(corrections):
        message = correction.get("message", "")
        if not message:
            continue

        keywords = tokenize(message)
        keywords -= _STOP_WORDS
        if len(keywords) < _MIN_KEYWORDS:
            continue

        matches = query_relevant(keywords, project_path, limit=1)
        if matches and matches[0]["score"] >= _MIN_SCORE:
            best = matches[0]
            results.append(
                {
                    "correction_index": idx,
                    "episodic_id": best["id"],
                    "episodic_content": best["content"],
                    "days_ago": best["days_ago"],
                    "score": best["score"],
                }
            )

    return results


# 短すぎて false positive になりやすい汎用語
_STOP_WORDS: frozenset[str] = frozenset(
    {
        # 英語
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "on", "at", "for", "with", "and", "or",
        "not", "no", "it", "its", "that", "this", "from", "by", "as",
        "if", "but", "do", "does", "did", "has", "have", "had",
        "will", "would", "can", "could", "should", "use", "get", "set",
        # 日本語助詞・助動詞・汎用語（単体では意味が薄い）
        "て", "に", "を", "は", "が", "で", "の", "も", "と", "や", "し",
        "た", "だ", "な", "ない", "する", "ある", "いる", "なる",
    }
)
