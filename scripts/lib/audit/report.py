"""generate_report — audit の最終レポート組み立て。

audit パッケージから切り出された Report モジュール。
複数のサブモジュール (memory / quality / sections) のセクションを順序付けて
1 本の Markdown レポートに結合する。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .classification import classify_artifact_origin
from .memory import build_memory_health_section
from .quality import build_quality_trends_section
from .sections import _build_test_guard_section, build_corrections_insights_section, build_lsp_suggestion_section, build_token_consumption_section
from .sections_summary import (
    build_clean_fold_line,
    build_recommended_actions_section,
    build_tldr_block,
    classify_section,
    fold_clean_observability,
)


def generate_report(
    artifacts: Dict[str, List[Path]],
    violations: List[Dict[str, Any]],
    usage: Dict[str, int],
    duplicates: List[Dict[str, Any]],
    advisories: List[Dict[str, Any]],
    quality_baselines: Optional[List[Dict[str, Any]]] = None,
    project_dir: Optional[Path] = None,
    plugin_usage: Optional[Dict[str, int]] = None,
    gstack_analytics: Optional[List[str]] = None,
    untagged_reference_candidates: Optional[List[Dict[str, Any]]] = None,
    hardcoded_values: Optional[List[Dict[str, Any]]] = None,
    coherence_report: Optional[List[str]] = None,
    telemetry_report: Optional[List[str]] = None,
    constitutional_report: Optional[List[str]] = None,
    environment_report: Optional[List[str]] = None,
    pipeline_health_report: Optional[List[str]] = None,
    memory_trace_report: Optional[List[str]] = None,
    cross_project_report: Optional[List[str]] = None,
    growth_report: Optional[List[str]] = None,
    next_milestone: Optional[List[str]] = None,
    contribution_scores: Optional[Dict[str, Any]] = None,
    max_skill_count: Optional[int] = None,
    untagged_skipped_count: int = 0,
) -> str:
    """1画面レポートを生成する。"""
    lines = ["# Environment Audit Report", ""]

    if growth_report:
        lines.extend(growth_report)

    # セクション順序: Environment Fitness → Constitutional → Coherence → Telemetry → Pipeline Health
    if environment_report:
        lines.extend(environment_report)

    if constitutional_report:
        lines.extend(constitutional_report)

    if coherence_report:
        lines.extend(coherence_report)

    if telemetry_report:
        lines.extend(telemetry_report)

    if pipeline_health_report:
        lines.extend(pipeline_health_report)

    if memory_trace_report:
        lines.extend(memory_trace_report)

    if cross_project_report:
        lines.extend(cross_project_report)

    total = sum(len(v) for v in artifacts.values())
    lines.append(f"## Summary: {total} artifacts found")
    for category, paths in artifacts.items():
        count = len(paths)
        if category == "skills" and max_skill_count is not None:
            origin_counts: Dict[str, int] = {"custom": 0, "global": 0, "plugin": 0}
            for p in paths:
                origin = classify_artifact_origin(p)
                origin_counts[origin] = origin_counts.get(origin, 0) + 1
            custom_count = origin_counts["custom"]
            indicator = " ⚠️" if custom_count > max_skill_count else ""
            if custom_count == count:
                lines.append(f"- {category}: {count} / 推奨上限 {max_skill_count}{indicator}")
            else:
                lines.append(
                    f"- {category}: {count}件"
                    f"（custom: {custom_count} / global: {origin_counts['global']}"
                    f" / plugin: {origin_counts['plugin']}）"
                    f" / custom 推奨上限 {max_skill_count}{indicator}"
                )
        else:
            lines.append(f"- {category}: {count}")
    lines.append("")

    if violations:
        lines.append(f"## Line Limit Violations ({len(violations)})")
        for v in violations:
            lines.append(f"- {v['file']}: {v['lines']}/{v['limit']} lines")
        # #52-3: 次アクション導線（800行超=分割必須を優先）。
        lines.append(
            "→ `/evolve-anything:evolve` Step4 または手動分割で対処。"
            "800行超（分割必須）を優先する。"
        )
        lines.append("")

    if project_dir is not None:
        memory_health = build_memory_health_section(artifacts, project_dir)
        if memory_health:
            lines.extend(memory_health)

    if project_dir is not None:
        tg_section = _build_test_guard_section(project_dir)
        if tg_section:
            lines.extend(tg_section)

    if project_dir is not None:
        lsp_section = build_lsp_suggestion_section(project_dir)
        if lsp_section:
            lines.extend(lsp_section)

    # Observability セクション（glossary_drift / unmanaged_pitfalls …）は
    # observability.py の collect_observability を単一ソースとして消費する（ADR-028）。
    # evolve が surface する構造化経路と同じ順序・同じ内容を保証し、項目追加時に markdown 側
    # だけ漏れる drift を防ぐ。
    #
    # #49-1/#49-5: 全 ✓（クリーン）/ ℹ・データ不足（観察中）のセクションは展開せず
    # 1行に畳む。要対応（⚠/🔴）のセクションだけ full-text 展開する。畳んだセクション名は
    # `## ✓ 評価済みクリーン: ...` に列挙して残すため silence != evaluated は保たれる。
    obs_critical_count = 0
    obs_clean_names: List[str] = []
    obs_watch_names: List[str] = []
    observability: Dict[str, List[str]] = {}
    if project_dir is not None:
        from .observability import collect_observability

        observability = collect_observability(project_dir)
        expanded, obs_clean_names, obs_watch_names = fold_clean_observability(observability)
        obs_critical_count = len(observability) - len(obs_clean_names) - len(obs_watch_names)
        lines.extend(expanded)
        lines.extend(build_clean_fold_line(obs_clean_names, obs_watch_names))

    if usage:
        lines.append("## Usage (last 30 days)")
        for skill, count in list(usage.items())[:15]:
            score_info = ""
            if contribution_scores:
                entry = contribution_scores.get(skill)
                if entry is not None:
                    score = entry.get("score")
                    score_info = f" | contribution: {score:.0%}" if score is not None else " | contribution: N/A"
            lines.append(f"- {skill}: {count} invocations{score_info}")
        lines.append("")

    if plugin_usage:
        summary_parts = [f"{name}({count})" for name, count in plugin_usage.items()]
        lines.append(f"Plugin usage: {' / '.join(summary_parts)}")
        lines.append("")

    if quality_baselines is not None:
        trends = build_quality_trends_section(quality_baselines, usage)
        if trends:
            lines.extend(trends)

    if gstack_analytics:
        lines.extend(gstack_analytics)

    token_section = build_token_consumption_section(days=30)
    if token_section:
        lines.extend(token_section)

    corrections_insights_section = build_corrections_insights_section()
    if corrections_insights_section:
        lines.extend(corrections_insights_section)

    if duplicates:
        lines.append(f"## Potential Duplicates ({len(duplicates)})")
        for d in duplicates:
            lines.append(f"- {d['name']}: {', '.join(d['paths'])}")
        lines.append("")

    if hardcoded_values:
        lines.append(f"## Hardcoded Values ({len(hardcoded_values)})")
        for hv in hardcoded_values:
            detail = hv.get("detail", {})
            lines.append(
                f"- {hv['file']}:{detail.get('line', '?')} "
                f"`{detail.get('matched', '?')}` ({detail.get('pattern_type', '?')}, "
                f"confidence={detail.get('confidence_score', 0):.2f})"
            )
        lines.append("")

    if untagged_reference_candidates:
        lines.append(f"## Reference Type Warning ({len(untagged_reference_candidates)})")
        lines.append("以下のスキルはゼロ呼び出しかつ `type` 未設定です。参照型スキルの場合は frontmatter に `type: reference` を追加してください。")
        for c in untagged_reference_candidates:
            lines.append(f"- {c['skill_name']}")
        lines.append("")

    # CLAUDE.md は在るが Skills セクションから trigger を抽出できなかった場合、
    # CLAUDE.md 記載スキルの除外が効かず誤検知になるため untagged 検出をスキップした旨を明示する (#295)。
    # 沈黙させず surface することで「環境解決失敗による誤検出」と「問題なし」を取り違えない。
    if untagged_skipped_count:
        lines.append("## Reference Type Warning (skipped — CLAUDE.md unparseable)")
        lines.append(
            f"CLAUDE.md は存在しますが Skills セクションから skill trigger を抽出できませんでした。"
            f"CLAUDE.md 記載スキルの除外が効かず誤検知になるため、untagged_reference 検出 "
            f"{untagged_skipped_count} 件をスキップしました。Skills セクションの記法を確認してください"
            f"（`- **ラベル**: \\`/skill\\`` 形式は対応済み）。"
        )
        lines.append("")

    if advisories:
        lines.append("## Scope Advisory")
        for a in advisories:
            lines.append(
                f"- {a['skill']}: {a['project_count']} projects, "
                f"last used {a['last_used'][:10] if a['last_used'] else 'never'} → {a['recommendation']}"
            )
        lines.append("")

    # #52-2: 標準実行でも「次フェーズ到達条件」を出す（フル growth report は重いので
    # Next Milestone 1ブロックだけ）。growth=True 時は growth_report に含まれるので None。
    if next_milestone:
        lines.append("## 🌱 成長の次の一手")
        lines.extend(next_milestone)

    # #52-1: 推奨アクション判定カードを末尾に必ず出す（MUST — スキップ厳禁）。
    # Token 未初期化は build_token_consumption_section の出力本文から、capture 枯渇は
    # observability の correction_capture セクションが要対応（⚠）かで決定論判定する。
    token_uninitialized = bool(token_section) and any(
        "not initialized" in ln for ln in token_section
    )
    _capture_section = observability.get("correction_capture")
    capture_starved = (
        _capture_section is not None and classify_section(_capture_section) == "critical"
    )
    lines.extend(
        build_recommended_actions_section(
            violations=violations,
            token_uninitialized=token_uninitialized,
            capture_starved=capture_starved,
            scope_candidates=advisories or [],
        )
    )

    # #49-5: TL;DR を冒頭（タイトル直後）に挿入する。observability の要対応 / 観察中 /
    # クリーン件数を1行で先出しし、詳細を全部読まずとも全体像が掴めるようにする。
    tldr = build_tldr_block(
        critical=obs_critical_count,
        watch=len(obs_watch_names),
        clean=len(obs_clean_names),
    )
    lines[2:2] = tldr

    return "\n".join(lines)
