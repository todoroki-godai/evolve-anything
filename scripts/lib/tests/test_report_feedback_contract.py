"""report-feedback スキルの候補スキーマ契約テスト。

report-feedback（SKILL.md）は LLM がレポートをメタレビューして改善候補を生成し、
**evolve_introspect の dedup/起票ヘルパーを再利用**して起票する。LLM 部分は決定論で
検証できないが、SKILL が依存する「候補スキーマ ↔ flatten/filter/render の配線」は
決定論で固められる。ここが噛み合っていれば、起票経路の配線バグはゼロにできる。

LLM は一切呼ばない（no-llm-in-tests）。候補 dict は固定 fixture。
SKILL.md の python コードブロックが指示どおり動くことの回帰ガードでもある。
"""
import re
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


# ── 契約5: SKILL.md の python3 -c ブロックが構文的に有効（#229） ──────────


def _extract_python_c_blocks(skill_md_text):
    """`python3 -c '...'` 形式のシェルブロックから python ソースを抽出する。

    SKILL.md 全体は shell の single quote (`'...'`) で python コードを囲むため、
    python コード側に生の `'` を含めるとシェル解釈そのものが壊れる（#229 で実測済み。
    シェルが早期にクォートを終端し、後続テキストが未クォートのまま解釈される）。
    ブロック境界は「行頭が `'` である行」を閉じクォートとみなして検出する。
    """
    return re.findall(r"python3 -c '\n(.*?)\n'", skill_md_text, re.DOTALL)


def _skill_md_path():
    return Path(__file__).resolve().parents[3] / "skills" / "report-feedback" / "SKILL.md"


def test_skill_md_python_c_blocks_compile():
    """SKILL.md の全 `python3 -c` ブロックが構文的に有効であることを保証する（#229）。

    f-string 式内で `\\"` のようにバックスラッシュエスケープした二重引用符を使うと
    Python 3.12 未満で SyntaxError になる（このリポジトリの実行環境である 3.14 でも
    同様に SyntaxError になることを実測済み）。シェル側はブロック全体を single quote
    で囲むため、式内に生の `'` を書く回避策（`d['key']`）もシェル解釈を壊してしまう
    （#229 で実測: クォートが早期終端し変数名が未クォートのまま渡り NameError化する）。
    正しい回避策は dict アクセスを一旦変数へ代入してから f-string に埋め込むこと。
    """
    skill_md = _skill_md_path()
    text = skill_md.read_text(encoding="utf-8")
    blocks = _extract_python_c_blocks(text)
    assert len(blocks) >= 3, f"python3 -c ブロックが期待数見つからない: {len(blocks)}"

    for i, block in enumerate(blocks):
        # f-string 式内のバックスラッシュエスケープは #229 の実バグそのもの。
        assert "\\\"" not in block, f"block {i}: f-string 式内に \\\" が残存（#229 再発）"
        try:
            compile(block, f"<SKILL.md python3 -c block {i}>", "exec")
        except SyntaxError as e:  # pragma: no cover - 失敗時のみ到達
            pytest.fail(f"block {i} が構文エラー: {e}\n---\n{block}")
