"""audit レポートの 3 段構成 + 推奨アクション（#49 / #52・決定論・LLM 非依存）。

audit の標準出力が冗長で「今すぐ見るべき Top3」と「次の一手」が無い問題（issue #49 / #52）
を解消するためのサマリ層。report.py が肥大化しないよう独立モジュールに切り出す
（file-size-budget）。observability.py の単一ソース契約（ADR-028）は壊さない:
ここは observability が生成した行を **読みやすく並べ替えるだけ** で、surface 対象の
追加・削除はしない。畳んだクリーンセクションも `## ✓ クリーン: ...` の1行に名前を残すため
silence != evaluated は保たれる（evolve SKILL.md Step 9 と同じ折り畳み規則を audit 単体に移植）。
"""
from typing import Any, Dict, List, Tuple

# セクション本文が「要対応」「データ不足/情報」「クリーン」のどれかを判定するマーカー。
# 各 observability builder の慣習に合わせる: ⚠/🔴=要対応, ℹ/データ不足=観察, ✓=clean。
_CRITICAL_MARKERS = ("⚠", "🔴")
_WATCH_MARKERS = ("ℹ", "データ不足")


def classify_section(lines: List[str]) -> str:
    """observability セクション 1 本を critical / watch / clean に分類する。

    優先順位: critical（⚠/🔴）> watch（ℹ/データ不足）> clean（✓ のみ）。
    要対応マーカーが 1 行でもあれば critical（混在時も要対応を埋もれさせない）。
    """
    text = "\n".join(lines)
    if any(m in text for m in _CRITICAL_MARKERS):
        return "critical"
    if any(m in text for m in _WATCH_MARKERS):
        return "watch"
    return "clean"


def fold_clean_observability(
    sections: Dict[str, List[str]],
) -> Tuple[List[str], List[str], List[str]]:
    """observability の dict を「要対応のみ展開」「クリーン/観察は名前だけ」に畳む。

    Returns:
        (expanded_lines, clean_names, watch_names)
        - expanded_lines: critical セクションの行を順に連結したもの（full-text 展開）
        - clean_names: 全 ✓ で畳まれたセクションの key 名（## ✓ クリーン: ... に出す）
        - watch_names: ℹ/データ不足で畳まれたセクションの key 名

    dict は挿入順（= _OBSERVABILITY_BUILDERS の登録順）を保持する前提。
    """
    expanded: List[str] = []
    clean_names: List[str] = []
    watch_names: List[str] = []
    for key, lines in sections.items():
        kind = classify_section(lines)
        if kind == "critical":
            expanded.extend(lines)
        elif kind == "watch":
            watch_names.append(key)
        else:
            clean_names.append(key)
    return expanded, clean_names, watch_names


def build_tldr_block(critical: int, watch: int, clean: int) -> List[str]:
    """レポート冒頭の TL;DR ブロック（#49-5）。

    3 つの数字（要対応 / 観察中 / クリーン）を1行で出し、詳細を全部読まずとも
    「今 audit で何が要対応か」が即わかるようにする。沈黙しない（全 0 でも出す）。
    """
    return [
        "## TL;DR",
        f"要対応 {critical} 件 / 観察中 {watch} 件 / 評価済みクリーン {clean} 件",
        "",
    ]


def build_clean_fold_line(clean_names: List[str], watch_names: List[str]) -> List[str]:
    """クリーン/観察セクションを1行に畳む（#49-1 / #49-5）。

    silence != evaluated を担保するため、畳んだセクション名は必ず列挙する
    （「評価したが該当なし」のものも名前が残ることで「評価したこと」が見える）。
    """
    lines: List[str] = []
    if clean_names:
        lines.append("## ✓ 評価済みクリーン（該当なし / drift なし）")
        lines.append(", ".join(clean_names))
        lines.append("")
    if watch_names:
        lines.append("## ℹ 観察中（データ不足 / 参考値 — 詳細は対象セクション参照）")
        lines.append(", ".join(watch_names))
        lines.append("")
    return lines


def build_recommended_actions_section(
    violations: List[Dict[str, Any]],
    token_uninitialized: bool,
    capture_starved: bool,
    scope_candidates: List[Dict[str, Any]],
) -> List[str]:
    """推奨アクション判定カードをレポート末尾に出す（#52-1・MUST — スキップ厳禁）。

    🔴 要対応（実行コマンドあり）/ 🟡 情報 / ✅ 問題なし の3段に分類する。
    該当ゼロなら ✅ 1行。判定軸（audit 文脈）:
      - Line Limit Violations ≥1 → 🔴 evolve Step4 or 手動分割
      - Token Consumption 未初期化 → 🔴 evolve-fleet tokens --backfill
      - Correction Capture 枯渇 → 🔴 corrections.jsonl 確認 + hook 見直し
      - Scope Advisory の project-scope 候補 → 🟡 スコープ移動 or prune
    """
    red: List[str] = []
    yellow: List[str] = []

    if violations:
        # 800 行超（分割必須）を優先する旨を1行で添える（#52-3 と整合）。
        red.append(
            f"  - Line Limit Violations {len(violations)}件 — "
            "対象ファイルを `/evolve-anything:evolve` Step4 または手動分割（800行超を優先）"
        )
    if token_uninitialized:
        red.append(
            "  - Token Consumption 未初期化 — `bin/evolve-fleet tokens --backfill`"
            "（実行後: PJ別 LLM コスト TOP3 + 異常検出が見える）"
        )
    if capture_starved:
        red.append(
            "  - Correction Capture 0%（報酬入力枯渇の可能性） — "
            "`corrections.jsonl` の中身を確認し、漏れなら `correction_detect` hook の発火条件を見直す"
        )
    if scope_candidates:
        yellow.append(
            f"  - Scope Advisory: project-scope 候補 {len(scope_candidates)}件 — "
            "スコープ移動 or `/evolve-anything:prune`（cross-PJ 使用状況を確認）"
        )

    lines = ["## 推奨アクション", ""]
    if red:
        lines.append("🔴 要対応（実行コマンドあり）:")
        lines.extend(red)
        lines.append("")
    if yellow:
        lines.append("🟡 情報（対策候補・参考値・観察継続）:")
        lines.extend(yellow)
        lines.append("")
    if not red and not yellow:
        lines.append("✅ 問題なし: 要対応・要観察の項目はありません。")
        lines.append("")
    return lines
