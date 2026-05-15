"""合理化防止テーブル (superpowers-knowledge-integration)。

corrections のスキップ/バイパス言い訳をパターン抽出し、
テレメトリ突合（前後 OUTCOME_WINDOW_DAYS のエラー率）で
「言い訳 vs 実際の結果」テーブルを生成する。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from similarity import jaccard_coefficient, tokenize
from skill_evolve import (
    RATIONALIZATION_MIN_CORRECTIONS,
    RATIONALIZATION_OUTCOME_WINDOW_DAYS,
    RATIONALIZATION_SKIP_KEYWORDS,
    ROOT_CAUSE_JACCARD_THRESHOLD,
)


def detect_rationalization_patterns(
    corrections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """corrections からスキップ/バイパスの合理化パターンを検出する。

    Returns:
        [{"excuse": str, "corrections": [dict], "sample_count": int}]
    """
    keyword_set = {kw.lower() for kw in RATIONALIZATION_SKIP_KEYWORDS}
    groups: Dict[str, List[Dict[str, Any]]] = {}

    for rec in corrections:
        if not isinstance(rec, dict):
            continue
        message = rec.get("message", "")
        if not message:
            continue
        msg_lower = message.lower()
        matched = [kw for kw in keyword_set if kw in msg_lower]
        if not matched:
            continue
        # グルーピングキー: 最初にマッチしたキーワード
        key = sorted(matched, key=lambda k: msg_lower.index(k))[0]
        excuse = message[:120]
        if excuse not in groups:
            groups[excuse] = []
        groups[excuse].append(rec)

    return [
        {"excuse": excuse, "corrections": recs, "sample_count": len(recs)}
        for excuse, recs in groups.items()
        if len(recs) >= 1
    ]


def generate_rationalization_table(
    corrections: List[Dict[str, Any]],
    usage: Optional[List[Dict[str, Any]]] = None,
    errors: Optional[List[Dict[str, Any]]] = None,
    *,
    existing_pitfalls: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """合理化防止テーブルを生成する。

    corrections のスキップパターンをテレメトリと突合し、
    「言い訳 vs 実際の結果」テーブルを生成する。

    Args:
        corrections: corrections.jsonl のレコード群
        usage: usage.jsonl のレコード群
        errors: errors.jsonl のレコード群
        existing_pitfalls: 既存 pitfall セクション（重複チェック用）

    Returns:
        {"data_insufficient": bool, "table": [...], "enriched_pitfalls": [...]}
    """
    patterns = detect_rationalization_patterns(corrections)
    total_skip_corrections = sum(p["sample_count"] for p in patterns)

    if total_skip_corrections < RATIONALIZATION_MIN_CORRECTIONS:
        return {"data_insufficient": True, "table": [], "enriched_pitfalls": []}

    error_list = errors or []
    table: List[Dict[str, Any]] = []
    enriched_pitfalls: List[Dict[str, Any]] = []

    for pattern in patterns:
        excuse = pattern["excuse"]
        sample_count = pattern["sample_count"]

        # テレメトリ突合: パターン発生時期の前後でエラー率を算出
        outcome_error_rate: Optional[float] = None
        telemetry_source = "corrections_only"

        if error_list and pattern["corrections"]:
            # corrections のタイムスタンプ前後 OUTCOME_WINDOW_DAYS のエラーを集計
            post_errors = 0
            for corr in pattern["corrections"]:
                corr_ts = corr.get("timestamp", "")
                if not corr_ts:
                    continue
                try:
                    corr_dt = datetime.fromisoformat(corr_ts.replace("Z", "+00:00"))
                    window_end = corr_dt + timedelta(days=RATIONALIZATION_OUTCOME_WINDOW_DAYS)
                    for err in error_list:
                        err_ts = err.get("timestamp", "")
                        if not err_ts:
                            continue
                        try:
                            err_dt = datetime.fromisoformat(err_ts.replace("Z", "+00:00"))
                            if corr_dt <= err_dt <= window_end:
                                post_errors += 1
                        except (ValueError, TypeError):
                            continue
                except (ValueError, TypeError):
                    continue

            if sample_count > 0:
                outcome_error_rate = round(post_errors / sample_count, 2)
                telemetry_source = "usage+errors"

        entry = {
            "excuse": excuse,
            "outcome_error_rate": outcome_error_rate,
            "sample_count": sample_count,
            "telemetry_source": telemetry_source,
        }
        table.append(entry)

        # 既存 pitfall との Jaccard 重複チェック → エンリッチ
        if existing_pitfalls:
            excuse_tokens = tokenize(excuse)
            for section_key in ("active", "candidate"):
                for pitfall in existing_pitfalls.get(section_key, []):
                    root_cause = pitfall["fields"].get("Root-cause", "")
                    pitfall_tokens = tokenize(root_cause)
                    if excuse_tokens and pitfall_tokens:
                        score = jaccard_coefficient(excuse_tokens, pitfall_tokens)
                        if score >= ROOT_CAUSE_JACCARD_THRESHOLD:
                            enriched_pitfalls.append({
                                "pitfall_title": pitfall["title"],
                                "matched_excuse": excuse,
                                "jaccard_score": round(score, 3),
                                "telemetry_data": entry,
                            })

    # sample_count で降順ソート
    table.sort(key=lambda x: x["sample_count"], reverse=True)

    return {
        "data_insufficient": False,
        "table": table,
        "enriched_pitfalls": enriched_pitfalls,
    }
