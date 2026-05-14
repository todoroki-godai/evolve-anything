#!/usr/bin/env python3
"""環境の健康診断スクリプト。

全 skills / rules / memory の棚卸し + 行数チェック + 使用状況集計を行い、
1画面レポートを出力する。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from rl_common import DATA_DIR  # noqa: F401 — re-exported for backward compat (audit.DATA_DIR / bloat_control / test patches)
from reflect_utils import read_all_memory_entries, read_auto_memory, split_memory_sections
from hardcoded_detector import detect_hardcoded_values
from frontmatter import count_content_lines
from path_extractor import extract_paths_outside_codeblocks as _extract_paths_outside_codeblocks, KNOWN_DIR_PREFIXES
from skill_origin import (
    get_plugin_skill_names as _so_get_plugin_skill_names,
    invalidate_cache as _so_invalidate_cache,
)

# 行数制限 — line_limit.py を Single Source of Truth として参照
from line_limit import (
    MAX_PROJECT_RULE_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
    NEAR_LIMIT_RATIO,
)

# LIMITS / _STOPWORDS は audit/_constants.py に集約済み（後方互換のため再エクスポート）
from ._constants import LIMITS, _STOPWORDS  # noqa: F401

# DATA_DIR は rl_common.DATA_DIR を再エクスポート（L19 の import 参照）
# - CLAUDE_PLUGIN_DATA env var サポート（cross-project / fleet 用途）
# - 真の源を rl_common.py に一本化、bloat_control.py と test patch の互換維持
#   詳細: docs/decisions/022-data-dir-unification.md（予定）

# KNOWN_DIR_PREFIXES は path_extractor から import 済み

# 分類ロジックは audit/classification.py に集約済み（後方互換のため再エクスポート）
# テストが `audit._plugin_skill_map_cache` を直接セットしていた箇所は
# `audit.classification._plugin_skill_map_cache` に追従させること（Phase 2 第五弾）。
from .classification import (  # noqa: F401
    _load_plugin_skill_map,
    _build_plugin_prefixes,
    _load_plugin_skill_names,
    classify_usage_skill,
    classify_artifact_origin,
)


# find_artifacts / check_line_limits は audit/artifacts.py に集約済み（後方互換のため再エクスポート）
from .artifacts import find_artifacts, check_line_limits  # noqa: F401


# Memory verification functions are extracted to audit/memory.py
# 後方互換のため audit パッケージから直接 import 可能にする
from .memory import (  # noqa: F401, E402
    _extract_section_keywords,
    _find_archive_mentions,
    _is_project_specific_section,
    build_memory_verification_context,
    build_memory_health_section,
    build_temporal_memory_warnings,
)


# Usage 集計は audit/usage.py に集約済み（後方互換のため再エクスポート）
# テストが `audit.load_usage_data` 等を patch している箇所は __init__.py の
# 名前空間を上書きするため引き続き機能する（Phase 2 第七弾）。
from .usage import (  # noqa: F401
    _BUILTIN_TOOLS,
    load_usage_data,
    _is_openspec_skill,
    _is_plugin_skill,
    aggregate_usage,
    aggregate_plugin_usage,
)


# Scope advisory / 重複検出 / 類似度は audit/scope.py に集約済み（後方互換のため再エクスポート）
from .scope import (  # noqa: F401
    detect_duplicates_simple,
    semantic_similarity_check,
    load_usage_registry,
    scope_advisory,
)


# Quality trends は audit/quality.py に分離 (Phase 2 第三弾)
from .quality import (  # noqa: F401, E402
    build_quality_trends_section,
    generate_sparkline,
    load_quality_baselines,
)


# gstack 関連は audit/gstack.py に分離 (Phase 2 第二弾)
from .gstack import (  # noqa: F401, E402
    _FALLBACK_GSTACK_LIFECYCLE,
    _FALLBACK_GSTACK_SKILL_PHASE_MAP,
    _FLOW_CHAIN_FILE,
    _GSTACK_LIFECYCLE,
    _GSTACK_SKILL_NAMES,
    _GSTACK_SKILL_PHASE_MAP,
    _is_gstack_skill,
    _load_flow_chain_phases,
    _match_gstack_phase,
    build_gstack_analytics_section,
)


# Issues collection は audit/issues.py に分離 (Phase 2 第四弾)
from .issues import (  # noqa: F401, E402
    _is_user_invocable_heuristic,
    collect_issues,
    detect_untagged_reference_candidates,
)


# Sections (Constitutional / Token / Test Guard) は audit/sections.py に集約済み（後方互換のため再エクスポート）
from .sections import (  # noqa: F401
    _format_constitutional_report,
    _short_int,
    build_token_consumption_section,
    _build_test_guard_section,
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
    cross_project_report: Optional[List[str]] = None,
    growth_report: Optional[List[str]] = None,
) -> str:
    """1画面レポートを生成する。"""
    lines = ["# Environment Audit Report", ""]

    # Growth Report (NFD) — 最上部に表示
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

    # Pipeline Health（既存スコアセクションの後に配置）
    if pipeline_health_report:
        lines.extend(pipeline_health_report)

    # Cross-Project Summary
    if cross_project_report:
        lines.extend(cross_project_report)

    # サマリ
    total = sum(len(v) for v in artifacts.values())
    lines.append(f"## Summary: {total} artifacts found")
    for category, paths in artifacts.items():
        lines.append(f"- {category}: {len(paths)}")
    lines.append("")

    # 行数超過
    if violations:
        lines.append(f"## Line Limit Violations ({len(violations)})")
        for v in violations:
            lines.append(f"- {v['file']}: {v['lines']}/{v['limit']} lines")
        lines.append("")

    # Memory Health（Line Limit Violations の直後）
    if project_dir is not None:
        memory_health = build_memory_health_section(artifacts, project_dir)
        if memory_health:
            lines.extend(memory_health)

    # Test Guard 導入状況
    if project_dir is not None:
        tg_section = _build_test_guard_section(project_dir)
        if tg_section:
            lines.extend(tg_section)

    # 使用状況（PJ 固有のみ）
    if usage:
        lines.append("## Usage (last 30 days)")
        for skill, count in list(usage.items())[:15]:
            lines.append(f"- {skill}: {count} invocations")
        lines.append("")

    # プラグイン利用サマリ
    if plugin_usage:
        summary_parts = [f"{name}({count})" for name, count in plugin_usage.items()]
        lines.append(f"Plugin usage: {' / '.join(summary_parts)}")
        lines.append("")

    # 品質推移
    if quality_baselines is not None:
        trends = build_quality_trends_section(quality_baselines, usage)
        if trends:
            lines.extend(trends)

    # gstack ワークフロー分析
    if gstack_analytics:
        lines.extend(gstack_analytics)

    # Token Consumption (PJ別 LLM トークン消費)
    token_section = build_token_consumption_section(days=30)
    if token_section:
        lines.extend(token_section)

    # 重複候補
    if duplicates:
        lines.append(f"## Potential Duplicates ({len(duplicates)})")
        for d in duplicates:
            lines.append(f"- {d['name']}: {', '.join(d['paths'])}")
        lines.append("")

    # Hardcoded Values 警告
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

    # Reference Type 未設定警告
    if untagged_reference_candidates:
        lines.append(f"## Reference Type Warning ({len(untagged_reference_candidates)})")
        lines.append("以下のスキルはゼロ呼び出しかつ `type` 未設定です。参照型スキルの場合は frontmatter に `type: reference` を追加してください。")
        for c in untagged_reference_candidates:
            lines.append(f"- {c['skill_name']}")
        lines.append("")

    # Scope Advisory
    if advisories:
        lines.append("## Scope Advisory")
        for a in advisories:
            lines.append(
                f"- {a['skill']}: {a['project_count']} projects, "
                f"last used {a['last_used'][:10] if a['last_used'] else 'never'} → {a['recommendation']}"
            )
        lines.append("")

    return "\n".join(lines)


_AUDIT_HISTORY_FILE = DATA_DIR / "audit-history.jsonl"
_MAX_AUDIT_HISTORY = 100
_DEGRADATION_THRESHOLD = 0.10  # 10% drop


def _record_audit_completion(
    coherence_report: Optional[List[str]] = None,
    telemetry_report: Optional[List[str]] = None,
    environment_report: Optional[List[str]] = None,
) -> None:
    """audit 完了時: last_audit_timestamp 更新 + audit-history.jsonl 記録 + 劣化検出。"""
    try:
        # Update last_audit_timestamp in evolve-state.json
        state_file = DATA_DIR / "evolve-state.json"
        state = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        now = datetime.now(timezone.utc).isoformat()
        state["last_audit_timestamp"] = now
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Record to audit-history.jsonl
        history_record: Dict[str, Any] = {"timestamp": now}
        # Extract scores from report lines if available
        for report_lines, key in [
            (coherence_report, "coherence_score"),
            (telemetry_report, "telemetry_score"),
            (environment_report, "environment_score"),
        ]:
            if report_lines:
                score = _extract_score_from_report(report_lines)
                if score is not None:
                    history_record[key] = score

        _append_audit_history(history_record)

        # Degradation detection
        _check_degradation(history_record)
    except Exception as e:
        print(f"[rl-anything:audit] history recording error: {e}", file=sys.stderr)


def _extract_score_from_report(lines: List[str]) -> Optional[float]:
    """レポート行からスコア値を抽出する。"""
    import re
    for line in lines:
        # Match patterns like "Score: 0.85" or "Overall: 0.72"
        m = re.search(r'(?:Score|Overall|Total)[:\s]+(\d+\.?\d*)', line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _append_audit_history(record: Dict[str, Any]) -> None:
    """audit-history.jsonl にレコードを追記し、pruning する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    if _AUDIT_HISTORY_FILE.exists():
        lines = _AUDIT_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    lines.append(json.dumps(record, ensure_ascii=False))
    # Pruning
    if len(lines) > _MAX_AUDIT_HISTORY:
        lines = lines[-_MAX_AUDIT_HISTORY:]
    _AUDIT_HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_degradation(current: Dict[str, Any]) -> None:
    """前回スコアとの比較で 10% 以上低下時に警告を出力する。"""
    if not _AUDIT_HISTORY_FILE.exists():
        return
    lines = _AUDIT_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return
    try:
        prev = json.loads(lines[-2])
    except (json.JSONDecodeError, IndexError):
        return

    for key in ("coherence_score", "telemetry_score", "environment_score"):
        prev_val = prev.get(key)
        curr_val = current.get(key)
        if prev_val is not None and curr_val is not None and prev_val > 0:
            drop = (prev_val - curr_val) / prev_val
            if drop >= _DEGRADATION_THRESHOLD:
                label = key.replace("_", " ").title()
                print(
                    f"⚠ {label} が {drop:.0%} 低下しています "
                    f"({prev_val:.2f} → {curr_val:.2f})",
                    file=sys.stderr,
                )


from .gstack import _load_global_retro  # noqa: F401, E402


def run_audit(project_dir: Optional[str] = None, skip_rescore: bool = False, coherence_score: bool = False, telemetry_score: bool = False, constitutional_score: bool = False, pipeline_health: bool = False, cross_project: bool = False, growth: bool = False) -> str:
    """Audit を実行してレポートを返す。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)
    violations = check_line_limits(artifacts)
    usage_records = load_usage_data(project_root=proj)
    usage = aggregate_usage(usage_records, exclude_plugins=True)
    plugin_usage = aggregate_plugin_usage(usage_records)
    duplicates = detect_duplicates_simple(artifacts)
    registry = load_usage_registry()
    advisories = scope_advisory(registry)

    # 品質計測統合
    quality_baselines = None
    if not skip_rescore:
        try:
            _scripts_dir = PLUGIN_ROOT / "scripts"
            if str(_scripts_dir) not in sys.path:
                sys.path.insert(0, str(_scripts_dir))
            from quality_monitor import run_quality_monitor
            run_quality_monitor()
        except Exception as e:
            print(f"品質計測スキップ: {e}", file=sys.stderr)

    # ベースラインがあればレポートに含める
    baselines = load_quality_baselines()
    if baselines:
        quality_baselines = baselines

    # gstack ワークフロー分析
    gstack_analytics = build_gstack_analytics_section(usage_records)

    # Reference type 未設定警告
    untagged = detect_untagged_reference_candidates(artifacts, usage, project_dir=proj)

    # Hardcoded values 検出
    hardcoded_values = []
    for category in ("skills", "rules"):
        for path in artifacts.get(category, []):
            detections = detect_hardcoded_values(str(path))
            for det in detections:
                hardcoded_values.append({
                    "type": "hardcoded_value",
                    "file": str(path),
                    "detail": det,
                    "source": "detect_hardcoded_values",
                })

    # Coherence Score
    coherence_report_lines = None
    if coherence_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.coherence import compute_coherence_score, format_coherence_report
            result = compute_coherence_score(proj)
            coherence_report_lines = format_coherence_report(result)
        except Exception as e:
            print(f"Coherence Score スキップ: {e}", file=sys.stderr)

    # Telemetry Score
    telemetry_report_lines = None
    if telemetry_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.telemetry import compute_telemetry_score, format_telemetry_report
            tel_result = compute_telemetry_score(proj)
            telemetry_report_lines = format_telemetry_report(tel_result)
        except Exception as e:
            print(f"Telemetry Score スキップ: {e}", file=sys.stderr)

    # Constitutional Score
    constitutional_report_lines = None
    if constitutional_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.constitutional import compute_constitutional_score
            from fitness.chaos import compute_chaos_score, format_chaos_report
            con_result = compute_constitutional_score(proj)
            constitutional_report_lines = _format_constitutional_report(con_result)
            # Chaos Testing
            try:
                chaos_result = compute_chaos_score(proj)
                chaos_lines = format_chaos_report(chaos_result)
                if constitutional_report_lines is not None:
                    constitutional_report_lines.extend(chaos_lines)
                else:
                    constitutional_report_lines = chaos_lines
            except Exception as e:
                print(f"Chaos Testing スキップ: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Constitutional Score スキップ: {e}", file=sys.stderr)

    # Environment Fitness（複数スコア指定時）
    environment_report_lines = None
    score_count = sum([coherence_score, telemetry_score, constitutional_score])
    if score_count >= 2:
        try:
            from fitness.environment import compute_environment_fitness, format_environment_report
            env_result = compute_environment_fitness(proj)
            environment_report_lines = format_environment_report(env_result)
        except Exception as e:
            print(f"Environment Fitness スキップ: {e}", file=sys.stderr)

    # Pipeline Health（LLM 不使用）
    pipeline_health_report_lines = None
    if pipeline_health:
        try:
            from pipeline_reflector import build_pipeline_health_section
            pipeline_health_report_lines = build_pipeline_health_section()
        except Exception as e:
            print(f"Pipeline Health スキップ: {e}", file=sys.stderr)

    # Cross-project summary from gstack retro global
    cross_project_report_lines = None
    if cross_project:
        retro = _load_global_retro()
        if retro is not None:
            cross_project_report_lines = ["## Cross-Project Summary (from /retro global)", ""]
            projects = retro.get("projects", [])
            totals = retro.get("totals", {})
            cross_project_report_lines.append(f"- Projects: {len(projects)}")
            if "sessions" in totals:
                cross_project_report_lines.append(f"- Total sessions: {totals['sessions']}")
            if "streak" in totals:
                cross_project_report_lines.append(f"- Streak: {totals['streak']}")
            cross_project_report_lines.append("")

    # Record audit completion: update last_audit_timestamp + audit-history.jsonl
    _record_audit_completion(
        coherence_report=coherence_report_lines,
        telemetry_report=telemetry_report_lines,
        environment_report=environment_report_lines,
    )

    # ── NFD Growth Report ──────────────────────────────────────
    growth_report_lines = None
    if growth:
        # skip_rescore=True のとき LLM eval（constitutional）をスキップして
        # fleet の 10s timeout 内で完了させる (#86)
        # issues_summary は audit run の検出結果から組み立てて growth-state に
        # 書き込む（fleet status の ISSUES 列で読まれる、#22）
        from issues_summary import compute_issues_summary
        from telemetry_query import query_corrections
        _project_name_for_issues = proj.resolve().name
        _corrections = query_corrections(project=_project_name_for_issues)
        _issues = compute_issues_summary(
            violations=violations,
            hardcoded_values=hardcoded_values,
            duplicates=duplicates,
            corrections=_corrections,
            quality_baselines=quality_baselines,
        )
        growth_report_lines = _build_growth_report(
            proj, skip_llm=skip_rescore, issues_summary=_issues,
        )

    return generate_report(
        artifacts, violations, usage, duplicates, advisories,
        quality_baselines, project_dir=proj,
        plugin_usage=plugin_usage if plugin_usage else None,
        gstack_analytics=gstack_analytics if gstack_analytics else None,
        untagged_reference_candidates=untagged if untagged else None,
        hardcoded_values=hardcoded_values if hardcoded_values else None,
        coherence_report=coherence_report_lines,
        telemetry_report=telemetry_report_lines,
        constitutional_report=constitutional_report_lines,
        environment_report=environment_report_lines,
        pipeline_health_report=pipeline_health_report_lines,
        cross_project_report=cross_project_report_lines,
        growth_report=growth_report_lines,
    )


def _build_growth_report(proj: Path, *, skip_llm: bool = False, issues_summary: Optional[Any] = None) -> List[str]:
    """NFD Growth Report セクションを生成する。

    Args:
        proj: プロジェクトディレクトリ
        skip_llm: True の場合、compute_environment_fitness に skip_llm=True を伝播し、
            LLM（constitutional）軸をスキップして軽量軸のみで env_score を算出する。
            rl-fleet status の 10s timeout 対応 (#86)。
        issues_summary: IssuesSummary instance — growth-state cache の `issues_summary`
            キーに dict として書き込む。fleet status (#22) が読み取る。None なら
            未書き込み（旧 cache 互換）。
    """
    lines = ["## 🌱 Growth Report (NFD)", ""]
    project_name = proj.resolve().name
    try:
        _scripts_lib = PLUGIN_ROOT / "scripts" / "lib"
        if str(_scripts_lib) not in sys.path:
            sys.path.insert(0, str(_scripts_lib))

        from growth_engine import read_cache, detect_phase, compute_phase_progress, update_cache, PHASE_DISPLAY_NAMES, Phase
        from growth_journal import query_crystallizations, count_crystallized_rules
        from growth_narrative import compute_profile, generate_story
        from growth_level import compute_level

        # テレメトリからフェーズ判定
        from telemetry_query import query_sessions, query_corrections
        sessions = query_sessions(project=project_name)
        corrections = query_corrections(project=project_name)
        crystallized = count_crystallized_rules(project=project_name)
        sessions_count = len(sessions) if sessions else 0
        corrections_count = len(corrections) if corrections else 0

        # env_score 計算（coherence 含む正確なスコア）
        _fitness_dir = PLUGIN_ROOT / "scripts" / "rl" / "fitness"
        if str(_fitness_dir) not in sys.path:
            sys.path.insert(0, str(_fitness_dir))
        env_score = 0.0
        coherence_score = 0.0
        try:
            from environment import compute_environment_fitness
            env_result = compute_environment_fitness(proj, skip_llm=skip_llm)
            env_score = env_result.get("overall", 0.0) if isinstance(env_result, dict) else 0.0
            coherence_score = env_result.get("axes", {}).get("coherence", {}).get("score", 0.0) if isinstance(env_result, dict) else 0.0
        except Exception:
            pass

        phase = detect_phase(sessions_count, corrections_count, crystallized, coherence_score)
        progress = compute_phase_progress(phase, sessions_count, corrections_count, crystallized, coherence_score)
        names = PHASE_DISPLAY_NAMES[phase]

        # Level 計算
        level_info = compute_level(env_score)

        # キャッシュ更新（env_score + level + issues_summary を含む）
        _cache_extra = {
            "sessions_count": sessions_count,
            "crystallizations_count": crystallized,
            "env_score": round(env_score, 4),
            "level": level_info.level,
            "title_en": level_info.title_en,
            "title_ja": level_info.title_ja,
        }
        if issues_summary is not None and hasattr(issues_summary, "to_dict"):
            _cache_extra["issues_summary"] = issues_summary.to_dict()
        update_cache(project_name, phase, progress, _cache_extra)

        progress_pct = int(progress * 100)
        bar_filled = int(progress * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        lines.append(f"**Level:** Lv.{level_info.level} {level_info.title_en} ({level_info.title_ja})")
        lines.append(f"**Environment Score:** {env_score:.2f}")
        lines.append(f"**Phase:** {names['en']} ({names['ja']})")
        lines.append(f"**Progress:** [{bar}] {progress_pct}%")
        lines.append(f"**Sessions:** {sessions_count} | **Corrections:** {corrections_count} | **Crystallizations:** {crystallized}")
        lines.append("")

        # 結晶化ログ
        events = query_crystallizations(project=project_name)
        if events:
            lines.append("### Crystallization Log")
            for ev in events[-10:]:  # 最新10件
                ts = ev.get("ts", "")[:10]
                targets = ", ".join(ev.get("targets", [])[:3]) or "(no targets)"
                lines.append(f"- {ts}: {targets}")
            lines.append("")

        # Environment Profile
        profile = compute_profile(project_name)
        if profile.strengths or profile.personality_traits:
            lines.append("### Environment Profile")
            if profile.strengths:
                lines.append(f"**Strengths:** {', '.join(profile.strengths)}")
            if profile.personality_traits:
                lines.append(f"**Traits:** {', '.join(profile.personality_traits)}")
            lines.append(f"**Style:** {profile.crystallization_style}")
            lines.append("")

        # Growth Story
        story = generate_story(project_name)
        if story and "まだ" not in story:
            lines.append("### Growth Story")
            lines.append(story)
            lines.append("")

        # Next Milestone
        lines.append("### Next Milestone")
        if phase == Phase.MATURE_OPERATION:
            lines.append("最終フェーズに到達しています。")
        else:
            next_phases = {
                Phase.BOOTSTRAP: ("Initial Nurturing", "sessions >= 10"),
                Phase.INITIAL_NURTURING: ("Structured Nurturing", "sessions >= 50, corrections >= 10, crystallized_rules >= 3"),
                Phase.STRUCTURED_NURTURING: ("Mature Operation", "sessions > 200, crystallized_rules >= 10, coherence >= 0.7"),
            }
            next_name, next_req = next_phases.get(phase, ("?", "?"))
            lines.append(f"Next phase: **{next_name}** — requires: {next_req}")
        lines.append("")

    except Exception as e:
        lines.append(f"Growth Report の生成に失敗しました: {e}")
        lines.append("")

    return lines


def main() -> None:
    """bin/rl-audit エントリポイント。"""
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(description="環境の健康診断")
    _parser.add_argument("project", nargs="?", default=None, help="プロジェクトディレクトリ")
    _parser.add_argument("--skip-rescore", action="store_true", help="品質計測をスキップ")
    _parser.add_argument("--memory-context", action="store_true", help="MEMORY 検証コンテキストを JSON 出力")
    _parser.add_argument("--coherence-score", action="store_true", help="Coherence Score セクションを表示")
    _parser.add_argument("--telemetry-score", action="store_true", help="Telemetry Score セクションを表示")
    _parser.add_argument("--constitutional-score", action="store_true", help="Constitutional Score セクションを表示")
    _parser.add_argument("--pipeline-health", action="store_true", help="Pipeline Health セクションを表示")
    _parser.add_argument("--growth", action="store_true", help="NFD Growth Report セクションを表示")
    _args = _parser.parse_args()
    if _args.memory_context:
        proj = Path(_args.project) if _args.project else Path.cwd()
        ctx = build_memory_verification_context(proj)
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(run_audit(_args.project, skip_rescore=_args.skip_rescore, coherence_score=_args.coherence_score, telemetry_score=_args.telemetry_score, constitutional_score=_args.constitutional_score, pipeline_health=_args.pipeline_health, growth=_args.growth))


if __name__ == "__main__":
    main()
