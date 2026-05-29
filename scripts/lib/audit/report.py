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
    contribution_scores: Optional[Dict[str, Any]] = None,
    max_skill_count: Optional[int] = None,
    negative_transfer_results: Optional[List[Dict[str, Any]]] = None,
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
    # observability.py の _OBSERVABILITY_BUILDERS を単一ソースとして消費する。
    # collect_observability（evolve が surface する構造化経路）と同じ順序・同じ内容を保証し、
    # 項目追加時に markdown 側だけ漏れる drift を防ぐ。
    if project_dir is not None:
        from .observability import _OBSERVABILITY_BUILDERS

        for _key, _builder in _OBSERVABILITY_BUILDERS:
            section = _builder(project_dir)
            if section:
                lines.extend(section)

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

    if negative_transfer_results:
        flagged = [r for r in negative_transfer_results if r.get("negative_transfer")]
        if flagged:
            lines.append(f"## ⚠ Negative Transfer Detected ({len(flagged)} skills)")
            lines.append("")
            for r in flagged:
                skill = r["skill_name"]
                delta = r["delta_score"]
                before = r["before_score"]
                after = r["after_score"]
                lines.append(
                    f"- **{skill}**: before={before:.0%} → after={after:.0%} (Δ{delta:+.0%})"
                )
            lines.append("")
            lines.append(
                "  スキル追加がパフォーマンスを低下させた可能性があります。"
                "`/rl-anything:evolve-skill` で見直しを検討してください。"
            )
            lines.append("")

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

    if advisories:
        lines.append("## Scope Advisory")
        for a in advisories:
            lines.append(
                f"- {a['skill']}: {a['project_count']} projects, "
                f"last used {a['last_used'][:10] if a['last_used'] else 'never'} → {a['recommendation']}"
            )
        lines.append("")

    return "\n".join(lines)
