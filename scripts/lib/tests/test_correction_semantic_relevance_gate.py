"""correction_semantic.relevance_gate のテスト（#565 FinAcumen 流関連度ゲート）。

過去経験（correction / idiom）の提案を「現在の文脈キーワード集合」との意味的関連度で
選別し、校正済み閾値を超えた候補だけを kept、未満を suppressed として明示的に分離する
決定論ゲートを検証する。LLM 非依存。

検証観点（Acceptance Criteria 逐条対応）:
- 文脈と語彙が重なる候補は閾値超過で kept に入り relevance_score が付く。
- 文脈と無関係な候補は閾値未満で suppressed に分離され、suppressed_reason が残る
  （フォールバックで黙って消さない）。
- 各候補に必ず relevance_score（0.0-1.0）が付く。
- 文脈キーワードが空（文脈不明）のときは全件 kept・gate_applied=False
  （無関係抑制を働かせず黙って消さない安全側）。
- 閾値は引数で上書きできる（校正済み定数だが設定可能）。
- weak_signal レコード（provenance.text）と idiom レコード（idiom）のどちらでも
  テキストを抽出して採点できる。
- 入力を破壊しない（純関数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import relevance_gate as rg  # noqa: E402
from correction_semantic.bootstrap_backlog import JACCARD_THRESHOLD  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# candidate_text: weak_signal / idiom 双方からテキストを取り出せる
# ─────────────────────────────────────────────────────────────────
def test_candidate_text_from_weak_signal_provenance():
    rec = {"provenance": {"text": "認証ルーティングの設定を確認"}, "signal_key": "k1"}
    assert rg.candidate_text(rec) == "認証ルーティングの設定を確認"


def test_candidate_text_from_idiom_field():
    rec = {"idiom": "デプロイ完了を確認してから", "idiom_key": "i1"}
    assert rg.candidate_text(rec) == "デプロイ完了を確認してから"


def test_candidate_text_empty_when_no_text():
    assert rg.candidate_text({"signal_key": "x"}) == ""


# ─────────────────────────────────────────────────────────────────
# score_relevance: jaccard 流儀の関連度スコア（0.0-1.0）
# ─────────────────────────────────────────────────────────────────
def test_score_relevance_full_overlap():
    ctx = rg.extract_keywords("認証ルーティング設定")
    score = rg.score_relevance(ctx, "認証ルーティング設定")
    assert score == 1.0


def test_score_relevance_no_overlap():
    ctx = rg.extract_keywords("認証ルーティング")
    score = rg.score_relevance(ctx, "デプロイ完了確認")
    assert score == 0.0


def test_score_relevance_partial_overlap_in_range():
    ctx = rg.extract_keywords("認証 ルーティング 設定")
    score = rg.score_relevance(ctx, "認証 トークン 確認")
    assert 0.0 < score < 1.0


def test_score_relevance_empty_context_returns_zero():
    assert rg.score_relevance(set(), "認証ルーティング") == 0.0


# ─────────────────────────────────────────────────────────────────
# gate_candidates: 閾値超過=kept / 未満=suppressed に分離
# ─────────────────────────────────────────────────────────────────
def _ws(text: str, key: str) -> dict:
    return {"provenance": {"text": text, "reason": "r"}, "signal_key": key, "channel": "llm_judge"}


def test_gate_splits_kept_and_suppressed():
    context = "認証ルーティングの設定を直す"
    candidates = [
        _ws("認証ルーティング設定", "rel"),       # 文脈と強く重なる → kept
        _ws("チョコレートケーキのレシピ", "unrel"),  # 完全に無関係 → suppressed
    ]
    res = rg.gate_candidates(context, candidates)

    kept_keys = {c["signal_key"] for c in res["kept"]}
    sup_keys = {c["signal_key"] for c in res["suppressed"]}
    assert "rel" in kept_keys
    assert "unrel" in sup_keys
    assert res["gate_applied"] is True


def test_kept_candidates_carry_relevance_score():
    context = "認証ルーティング設定"
    candidates = [_ws("認証ルーティング設定", "rel")]
    res = rg.gate_candidates(context, candidates)
    kept = res["kept"][0]
    assert "relevance_score" in kept
    assert kept["relevance_score"] == 1.0


def test_suppressed_candidates_carry_reason_and_score():
    context = "認証ルーティング"
    candidates = [_ws("チョコレートケーキ", "unrel")]
    res = rg.gate_candidates(context, candidates)
    sup = res["suppressed"][0]
    # フォールバックで黙って消さない: なぜ落としたかを残す
    assert "suppressed_reason" in sup
    assert "relevance_score" in sup
    assert sup["relevance_score"] == 0.0
    # reason に閾値が記録され判読できる
    assert str(JACCARD_THRESHOLD) in sup["suppressed_reason"] or "below" in sup["suppressed_reason"]


def test_threshold_is_overridable():
    context = "認証 ルーティング 設定"
    candidates = [_ws("認証 トークン 確認", "partial")]
    # 低い閾値なら kept、極端に高い閾値なら suppressed になる（同じ候補で切り替わる）
    lenient = rg.gate_candidates(context, candidates, threshold=0.01)
    strict = rg.gate_candidates(context, candidates, threshold=0.99)
    assert any(c["signal_key"] == "partial" for c in lenient["kept"])
    assert any(c["signal_key"] == "partial" for c in strict["suppressed"])


def test_empty_context_keeps_all_without_gating():
    # 文脈不明（空）のときは無関係抑制を働かせず黙って消さない（安全側フォールバック）。
    candidates = [_ws("認証ルーティング", "a"), _ws("チョコレートケーキ", "b")]
    res = rg.gate_candidates("", candidates)
    assert len(res["kept"]) == 2
    assert res["suppressed"] == []
    assert res["gate_applied"] is False
    # gate 不適用でも relevance_score は付与（観測可能性）
    for c in res["kept"]:
        assert "relevance_score" in c


def test_gate_does_not_mutate_input():
    context = "認証ルーティング"
    candidates = [_ws("認証ルーティング", "a")]
    before = candidates[0].copy()
    rg.gate_candidates(context, candidates)
    assert candidates[0] == before
    assert "relevance_score" not in candidates[0]


def test_kept_sorted_by_relevance_desc():
    context = "認証 ルーティング 設定 確認"
    candidates = [
        _ws("認証 確認", "low"),                  # 部分一致（低）
        _ws("認証 ルーティング 設定 確認", "high"),  # 完全一致（高）
    ]
    res = rg.gate_candidates(context, candidates)
    kept = res["kept"]
    assert len(kept) == 2
    # 関連度降順
    assert kept[0]["signal_key"] == "high"
    assert kept[1]["signal_key"] == "low"
    assert kept[0]["relevance_score"] >= kept[1]["relevance_score"]


def test_idiom_records_are_gated_too():
    context = "デプロイ完了確認"
    candidates = [
        {"idiom": "デプロイ完了確認", "idiom_key": "rel"},
        {"idiom": "認証ルーティング", "idiom_key": "unrel"},
    ]
    res = rg.gate_candidates(context, candidates)
    assert any(c.get("idiom_key") == "rel" for c in res["kept"])
    assert any(c.get("idiom_key") == "unrel" for c in res["suppressed"])


# ─────────────────────────────────────────────────────────────────
# summarize_gate: observability 用の集計（レポートで確認できる形）
# ─────────────────────────────────────────────────────────────────
def test_summarize_gate_counts():
    context = "認証ルーティング"
    candidates = [
        _ws("認証ルーティング", "a"),
        _ws("チョコレートケーキ", "b"),
        _ws("認証ルーティング設定", "c"),
    ]
    res = rg.gate_candidates(context, candidates)
    summary = rg.summarize_gate(res)
    assert summary["kept"] == len(res["kept"])
    assert summary["suppressed"] == len(res["suppressed"])
    assert summary["total"] == 3
    assert summary["threshold"] == JACCARD_THRESHOLD
    assert summary["gate_applied"] is True
