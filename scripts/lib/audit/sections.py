"""レポートセクション生成（Constitutional Score / Token Consumption / Test Guard）。

audit パッケージから切り出された Sections モジュール。generate_report が呼ぶ
セクション生成関数を集約。
- _format_constitutional_report: Constitutional Score → Markdown
- _short_int: 大きい整数 → 短縮表記 (1.2K / 3.4M / 5.6B)
- build_token_consumption_section: PJ別トークン消費 TOP3 + 異常検知
- _build_test_guard_section: LLM SDK 利用 PJ への guard 導入推奨
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


def _format_constitutional_report(result: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Constitutional Score をレポート用にフォーマットする。"""
    if result is None:
        return ["## Constitutional Score", "", "LLM 評価に失敗しました", ""]

    if result.get("overall") is None:
        skip_reason = result.get("skip_reason", "unknown")
        coverage = result.get("coverage_value", "N/A")
        return [
            "## Constitutional Score",
            "",
            f"Skipped: {skip_reason} (coverage={coverage})",
            "",
        ]

    lines = [f"## Constitutional Score: {result['overall']:.2f}", ""]

    per_principle = result.get("per_principle", [])
    if per_principle:
        lines.append("### Per-Principle Scores")
        for p in per_principle:
            score = p.get("score", 0.0)
            bar_filled = int(score * 20)
            bar_empty = 20 - bar_filled
            bar = "█" * bar_filled + "░" * bar_empty
            lines.append(f"  {p.get('id', '?'):30s} {score:.2f} {bar}")
        lines.append("")

    cost = result.get("estimated_cost_usd", 0)
    calls = result.get("llm_calls_count", 0)
    lines.append(f"LLM calls: {calls}, Estimated cost: ${cost:.4f}")
    lines.append("")

    return lines


def _short_int(n: int | None) -> str:
    if n is None:
        return "--"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def build_token_consumption_section(days: int = 30) -> List[str]:
    """Token Consumption セクションを生成する。

    データ無し → 1 行ヒントのみ返す。
    データあり → TOP 3 / Anomalies / Hints。
    """
    try:
        import token_usage_query as tuq  # type: ignore
        import token_usage_store as tus  # type: ignore
    except ImportError:
        return []

    db_empty = (not tus.HAS_DUCKDB) or (not tus.USAGE_DB.exists())
    if not db_empty:
        try:
            row = tus.query("SELECT COUNT(*) FROM token_usage")
            db_empty = (not row) or (row[0][0] == 0)
        except Exception:
            db_empty = True

    if db_empty:
        return [
            "## Token Consumption",
            "",
            "(Token tracking not initialized — run `rl-fleet tokens --backfill` to enable)",
            "",
        ]

    try:
        top = tuq.top_n_consumers(days=days, n=3)
        wow = tuq.wow_anomalies()
        cache = tuq.cache_hit_anomalies()
    except Exception:
        return []

    lines: List[str] = [f"## Token Consumption (last {days} days)", ""]
    if top:
        lines.append("TOP 3 consumers:")
        for i, c in enumerate(top, 1):
            hit = (
                f"  (cache hit {c['cache_hit_pct']:.0f}%)"
                if c.get("cache_hit_pct") is not None
                else ""
            )
            label = c.get("pj_slug") or c["pj_id"]
            lines.append(f"  {i}. {label}\t{_short_int(c['tokens'])}{hit}")
        lines.append("")
    if wow or cache:
        lines.append("Anomalies detected:")
        for a in wow:
            lines.append(
                f"  • {a['pj_id']}: WoW +{a['wow_pct']:.0f}% "
                f"({_short_int(a['last_week'])} → {_short_int(a['this_week'])})"
            )
        for a in cache:
            lines.append(
                f"  • {a['pj_id']}: cache hit {a['last_hit_pct']:.0f}% → "
                f"{a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)"
            )
        lines.append("")
    lines.append("Hints:")
    lines.append("  • Low cache hit (<40%) often means CLAUDE.md / system prompt changes per session")
    lines.append("  • WoW spikes often correlate with subagent loops — check SUBAGENTS_30d column")
    lines.append("")
    return lines


def _build_test_guard_section(project_dir: Path) -> Optional[List[str]]:
    """PJ が LLM SDK を使うのに no-llm-in-tests / pytest-no-llm が未導入なら勧める。"""
    try:
        import test_guard
    except ImportError:
        return None
    rows = test_guard.collect_test_guard_rows([project_dir])
    if not rows:
        return None
    r = rows[0]
    if not r.uses_llm:
        return None
    if not r.needs_attention and not r.preventive_candidate:
        return None
    lines = ["## Test Guard", ""]
    lines.append(f"このPJはLLM SDKを利用しています ({', '.join(sorted(r.languages))})。")
    if r.preventive_candidate:
        lines.append("現在テストフレームワーク未導入のため即時の事故リスクは低いですが、")
        lines.append("テスト追加時に備え以下のguardを予防的に導入することを推奨します:")
    else:
        lines.append("ユニットテストでLLMを誤って実呼び出ししないよう、以下のguardの導入を推奨します:")
    if not r.has_precommit_hook:
        lines.append("- pre-commit: `no-llm-in-tests` (静的検出、全言語)")
    if "python" in r.languages and not r.has_pytest_no_llm:
        lines.append("- pip: `pytest-no-llm` (実行時 guard、Python のみ)")
    lines.append("")
    lines.append("導入方法は ~/tools/no-llm-in-tests/README.md, ~/tools/pytest-no-llm/README.md を参照。")
    lines.append("")
    return lines
