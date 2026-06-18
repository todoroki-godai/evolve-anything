"""report-feedback スキルの候補スキーマ契約テスト。

report-feedback（SKILL.md）は LLM がレポートをメタレビューして改善候補を生成し、
**evolve_introspect の dedup/起票ヘルパーを再利用**して起票する。LLM 部分は決定論で
検証できないが、SKILL が依存する「候補スキーマ ↔ flatten/filter/render の配線」は
決定論で固められる。ここが噛み合っていれば、起票経路の配線バグはゼロにできる。

LLM は一切呼ばない（no-llm-in-tests）。候補 dict は固定 fixture。
SKILL.md の python コードブロックが指示どおり動くことの回帰ガードでもある。
"""
import sys
from pathlib import Path

import pytest

# evolve_introspect は scripts/lib 直下。conftest が sys.path を通す前提だが収集経路差の保険。
if Path(__file__).resolve().parents[1].exists():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evolve_introspect import (
    filter_duplicates,
    render_issue_body,
    flatten_candidates,
    extract_marker,
)


def _llm_candidate(dedup_key="evolve-report-missing-denominator", title="evolve レポートの母数欠落"):
    """report-feedback の SKILL.md が LLM に生成させる候補スキーマ（Step 3）。

    SKILL の候補スキーマ定義（category/title/body/suggested_label/dedup_key/severity）を
    そのまま写したもの。これが filter_duplicates・render_issue_body を通ることを保証する。
    """
    return {
        "category": "improvement_opportunities",
        "title": title,
        "body": "## 背景\nレポートに割合だけ出て母数が無い\n## 提案\n母数を併記\n## 根拠\n該当行",
        "suggested_label": "enhancement",
        "dedup_key": dedup_key,
        "severity": "medium",
    }


# ── 契約1: 候補 → render_issue_body → extract_marker の往復 ──────────────


def test_llm_candidate_round_trips_through_render_and_extract():
    """SKILL Step 6 の render_issue_body が候補 body にマーカーを埋め、Step 4 の
    extract_marker で dedup_key を取り戻せる（毎回起票の重複防止の根幹）。"""
    cand = _llm_candidate()
    body = render_issue_body(cand)
    assert cand["body"].splitlines()[0] in body  # 本文は保持
    assert extract_marker(body) == cand["dedup_key"]  # マーカー往復


# ── 契約2: 同一 dedup_key の既存 issue があれば重複として落ちる ──────────


def test_existing_issue_with_same_marker_is_deduped():
    """既存 issue の body に同じマーカーが入っていれば filter_duplicates が重複判定する。
    SKILL Step 4 が毎 evolve で同じ問題を重複起票しないことの保証。"""
    cand = _llm_candidate()
    existing = [{"number": 321, "title": "別タイトルでも", "body": render_issue_body(cand)}]
    res = filter_duplicates([cand], existing)
    assert len(res["unique"]) == 0
    assert len(res["duplicates"]) == 1
    assert res["duplicates"][0]["existing_number"] == 321
    assert res["duplicates"][0]["reason"] == "marker"


def test_new_candidate_passes_dedup_when_no_match():
    """既存に該当が無ければ unique に残り起票対象になる。"""
    cand = _llm_candidate()
    res = filter_duplicates([cand], existing_issues=[])
    assert [c["dedup_key"] for c in res["unique"]] == [cand["dedup_key"]]
    assert res["duplicates"] == []


# ── 契約3: 決定論 seed（flatten_candidates）と LLM 候補の統合 ──────────────


def test_seed_and_llm_candidates_merge_and_filter_together():
    """SKILL Step 2-4: evolve の self_analysis を flatten した決定論 seed と、
    LLM が出した候補を 1 リストに統合し、filter_duplicates が両方を扱える。

    seed 候補（category 別に candidates を持つ analysis dict）の形は
    flatten_candidates が平坦化する実構造に合わせる。
    """
    analysis = {
        "self_detection": {
            "candidates": [
                {
                    "category": "self_detection",
                    "title": "split↔archive 矛盾",
                    "body": "矛盾の説明",
                    "dedup_key": "split-archive-contradiction",
                    "severity": "high",
                }
            ],
            "summary_line": "1 件",
        },
        "runtime_errors": {"candidates": [], "summary_line": "✓ 評価したが該当なし"},
        "improvement_opportunities": {"candidates": [], "summary_line": "✓ 評価したが該当なし"},
    }
    seed = flatten_candidates(analysis)
    assert len(seed) == 1

    merged = seed + [_llm_candidate()]
    res = filter_duplicates(merged, existing_issues=[])
    # seed・LLM の両 dedup_key が unique に揃う
    keys = {c["dedup_key"] for c in res["unique"]}
    assert keys == {"split-archive-contradiction", "evolve-report-missing-denominator"}

    # 各候補が render を通り、それぞれのマーカーを取り戻せる
    for c in res["unique"]:
        assert extract_marker(render_issue_body(c)) == c["dedup_key"]


# ── 契約4: 近いタイトルは marker 無しでも重複検知（手動起票済みケース）──────


def test_similar_title_without_marker_is_deduped():
    """SKILL の dedup は marker 一致だけでなくタイトル類似でも効く。手動で先に
    起票された issue（マーカー無し）との重複も拾えることを固定する。"""
    cand = _llm_candidate(title="evolve レポートの母数欠落")
    existing = [{"number": 99, "title": "evolve レポートの母数欠落", "body": "マーカー無し手動起票"}]
    res = filter_duplicates([cand], existing)
    assert len(res["duplicates"]) == 1
    assert res["duplicates"][0]["existing_number"] == 99
    assert res["duplicates"][0]["reason"].startswith("title_similarity")
