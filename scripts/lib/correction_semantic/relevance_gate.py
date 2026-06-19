"""correction_semantic.relevance_gate — 過去経験の関連度ゲート＋無関係抑制（#565）。

FinAcumen（Financial Multimodal Reasoning via Self-Evolving Experience Memory Harness,
arXiv 2606.17642）の「意味的関連度が校正済み閾値を超えたときだけ経験を条件付け、無関係
メモリはフォールバックで明示的に抑制する」を evolve-anything の reflect / correction_semantic に
増分移植する。論文コードは未公開のため概念のみ移植し、類似度は既存 jaccard 流儀を再利用する
（独自の閾値学習機構はフルでは作らない — Issue #565 スコープ厳守）。

設計（最小スコープ）:
- 「現在の文脈キーワード集合」と「候補となる過去 correction / idiom（weak_signal レコードや
  idiom レコード）」を受け取る純関数を提供する。
- (a) 校正済み閾値（既存 JACCARD_THRESHOLD 流儀・引数で上書き可能）を超えた候補だけを kept、
  (b) 閾値未満の無関係候補は明示的に suppressed として分離し（フォールバックで黙って消さず
  「なぜ落としたか」を suppressed_reason に残す）、(c) 各候補に relevance_score を付与する。
- 文脈キーワードが空（文脈不明）のときは無関係抑制を働かせず全件 kept（gate_applied=False）。
  黙ってメモリを消さない安全側フォールバック。

決定論・LLM 非依存。類似度は bootstrap_backlog の extract_keywords / jaccard をそのまま使い、
daily_review / bootstrap_backlog と同じ「漢字・カタカナ 2 字以上を内容キーワードにする」流儀を
踏襲する（独自の tokenizer を作らない）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from correction_semantic.bootstrap_backlog import extract_keywords
from correction_semantic.representative import user_only_text

# 校正済みの関連度しきい値（jaccard 流儀の固定値・引数で上書き可能）。
#
# relevance（過去経験が現文脈に関係するか）は near-duplicate dedup より緩い関係。
# bootstrap_backlog / daily_review の grouping 用 JACCARD_THRESHOLD(=0.5) を流用すると、
# 実コーパスでは到達不能だった（典型的な自由文文脈の jaccard が max ~0.25 / 中央値 0.0 に
# 収まり、287 件中 kept=0 = 全件 suppressed の no-op に倒れる — #578 実PJ dogfood で確認）。
# そこで relevance 専用の校正値に decouple し、実コーパス分布に合わせて下げる。metric は
# jaccard 据え置き（汎用語1語一致を 1/N に自然減衰し、overlap 係数の tiny-set 偽陽性を避ける）。
# 閾値学習機構は Issue #565 のスコープ外（決定論・固定/設定可能な定数で十分）。
RELEVANCE_THRESHOLD = 0.2


# ─────────────────────────────────────────────────────────────────
# candidate_text: weak_signal / idiom 双方からテキストを取り出す
# ─────────────────────────────────────────────────────────────────
def candidate_text(candidate: Dict[str, Any]) -> str:
    """候補レコードから採点対象テキスト（発話断片 / idiom 本文）を取り出す。

    対応形式（既存ストアのレコード構造をそのまま受ける・verify-data-contract）:
    - weak_signal レコード: ``provenance.text``（daily_review / bootstrap_backlog と同経路）
    - idiom レコード: ``idiom``

    weak_signal は assistant の過去レポート引用混入を strip するため user_only_text を通す
    （#528-3 と同じ representative 抽出）。idiom は確定済みの正規化テキストなのでそのまま使う。
    """
    if not candidate:
        return ""
    prov = candidate.get("provenance") or {}
    text = prov.get("text")
    if text:
        return user_only_text(text)
    idiom = candidate.get("idiom")
    if idiom:
        return idiom
    return ""


# ─────────────────────────────────────────────────────────────────
# score_relevance: jaccard 流儀の関連度スコア（0.0-1.0）
# ─────────────────────────────────────────────────────────────────
def _jaccard(a: Set[str], b: Set[str]) -> float:
    """daily_review / bootstrap_backlog と同一の jaccard（流儀を共有）。"""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def score_relevance(context_keywords: Set[str], candidate_str: str) -> float:
    """文脈キーワード集合と候補テキストの関連度スコア（jaccard・0.0-1.0）を返す。

    context_keywords が空（文脈不明）なら 0.0（採点不能）。候補テキストからキーワードが
    1 つも取れない場合も 0.0。決定論・LLM 非依存。
    """
    if not context_keywords:
        return 0.0
    cand_kws = extract_keywords(candidate_str)
    if not cand_kws:
        return 0.0
    return _jaccard(set(context_keywords), cand_kws)


# ─────────────────────────────────────────────────────────────────
# gate_candidates: 閾値超過=kept / 未満=suppressed に分離
# ─────────────────────────────────────────────────────────────────
def gate_candidates(
    context: str,
    candidates: List[Dict[str, Any]],
    *,
    threshold: float = RELEVANCE_THRESHOLD,
) -> Dict[str, Any]:
    """候補を現在の文脈との関連度で kept / suppressed に分離する（純関数）。

    Args:
        context: 現在の文脈（自由文）。内部で内容キーワード集合に変換する。
        candidates: 過去経験の候補レコード（weak_signal / idiom）。入力は破壊しない。
        threshold: 関連度ゲートの閾値（既定 RELEVANCE_THRESHOLD）。校正済み定数だが
                   呼び出し側で上書き可能（#565 スコープ: 学習機構は作らない）。

    Returns:
        {
          "kept": [<candidate + relevance_score>, ...],        # 関連度降順
          "suppressed": [<candidate + relevance_score          # 関連度降順
                          + suppressed_reason>, ...],
          "gate_applied": bool,    # 文脈キーワードが取れてゲートを適用したか
          "threshold": float,
        }

    フォールバック（黙ってメモリを消さない安全側）: 文脈キーワードが空（文脈不明）のときは
    無関係抑制を働かせず全件 kept・suppressed=[]・gate_applied=False。kept にも relevance_score
    を付与する（観測可能性のため。この場合は全件 0.0）。
    """
    context_keywords = extract_keywords(context)
    gate_applied = bool(context_keywords)

    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []

    for cand in candidates or []:
        score = score_relevance(context_keywords, candidate_text(cand))
        # 入力を破壊しない: コピーに注釈を足す（純関数）。
        enriched = dict(cand)
        enriched["relevance_score"] = score

        if not gate_applied:
            # 文脈不明 → 抑制せず素通し（黙って消さない）。
            kept.append(enriched)
            continue

        if score >= threshold:
            kept.append(enriched)
        else:
            enriched["suppressed_reason"] = (
                f"relevance {score:.3f} below threshold {threshold}"
            )
            suppressed.append(enriched)

    # 関連度降順で安定ソート（同点は入力順を保つ）。提案根拠の優先順位として使える。
    kept.sort(key=lambda c: c["relevance_score"], reverse=True)
    suppressed.sort(key=lambda c: c["relevance_score"], reverse=True)

    return {
        "kept": kept,
        "suppressed": suppressed,
        "gate_applied": gate_applied,
        "threshold": threshold,
    }


# ─────────────────────────────────────────────────────────────────
# summarize_gate: observability 用の集計（レポートで確認できる形）
# ─────────────────────────────────────────────────────────────────
def summarize_gate(gate_result: Dict[str, Any]) -> Dict[str, Any]:
    """gate_candidates の結果を 1 段の集計に畳む（reflect レポート surface 用）。

    Returns:
        {"kept": int, "suppressed": int, "total": int,
         "threshold": float, "gate_applied": bool}
    """
    kept = gate_result.get("kept") or []
    suppressed = gate_result.get("suppressed") or []
    return {
        "kept": len(kept),
        "suppressed": len(suppressed),
        "total": len(kept) + len(suppressed),
        "threshold": gate_result.get("threshold", RELEVANCE_THRESHOLD),
        "gate_applied": bool(gate_result.get("gate_applied")),
    }
