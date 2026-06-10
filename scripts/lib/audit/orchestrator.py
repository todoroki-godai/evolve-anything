"""audit 実行オーケストレーター + history 記録 + Growth Report。

audit パッケージから切り出された Orchestrator モジュール。
- _record_audit_completion / _extract_score_from_report /
  _append_audit_history / _check_degradation: audit 履歴管理 + 劣化検出
- run_audit: 全コレクター + スコア計算 + レポート生成のメインエントリ
- _build_growth_report: NFD Growth Report セクション生成

DATA_DIR / _AUDIT_HISTORY_FILE はテスト後方互換のため audit 名前空間
（`audit._AUDIT_HISTORY_FILE`）からも再エクスポートされる。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
from rl_common import DATA_DIR
from .artifacts import find_artifacts, check_line_limits, check_python_source_budgets
from .usage import load_usage_data, aggregate_usage, aggregate_plugin_usage
from .scope import detect_duplicates_simple, load_usage_registry, scope_advisory
from .quality import load_quality_baselines
from .gstack import build_gstack_analytics_section, _load_global_retro
from .issues import (
    detect_untagged_reference_candidates,
    claude_md_unparseable,
    collect_hardcoded_value_issues,
)
from .sections import _format_constitutional_report
from .report import generate_report


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

        history_record: Dict[str, Any] = {"timestamp": now}
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
        _check_degradation(history_record)
    except Exception as e:
        print(f"[rl-anything:audit] history recording error: {e}", file=sys.stderr)


def _extract_score_from_report(lines: List[str]) -> Optional[float]:
    """レポート行からスコア値を抽出する。"""
    import re
    for line in lines:
        m = re.search(r'(?:Score|Overall|Total)[:\s]+(\d+\.?\d*)', line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _append_audit_history(record: Dict[str, Any]) -> None:
    """audit-history.jsonl にレコードを追記し、pruning する。"""
    # テストが audit.DATA_DIR / audit._AUDIT_HISTORY_FILE を patch するため遅延参照
    import audit as _audit
    _data_dir = _audit.DATA_DIR
    _hist = _audit._AUDIT_HISTORY_FILE
    _data_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    if _hist.exists():
        lines = _hist.read_text(encoding="utf-8").strip().splitlines()
    lines.append(json.dumps(record, ensure_ascii=False))
    if len(lines) > _MAX_AUDIT_HISTORY:
        lines = lines[-_MAX_AUDIT_HISTORY:]
    _hist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_degradation(current: Dict[str, Any]) -> None:
    """前回スコアとの比較で 10% 以上低下時に警告を出力する。"""
    import audit as _audit
    _hist = _audit._AUDIT_HISTORY_FILE
    if not _hist.exists():
        return
    lines = _hist.read_text(encoding="utf-8").strip().splitlines()
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


def run_audit(
    project_dir: Optional[str] = None,
    skip_rescore: bool = False,
    coherence_score: bool = False,
    telemetry_score: bool = False,
    constitutional_score: bool = False,
    pipeline_health: bool = False,
    memory_trace: bool = False,
    cross_project: bool = False,
    growth: bool = False,
) -> str:
    """Audit を実行してレポートを返す。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)
    violations = check_line_limits(artifacts)
    # Python source 行数バジェット (Slice 13 — 肥大化予防)
    violations.extend(check_python_source_budgets(proj))
    usage_records = load_usage_data(project_root=proj)
    usage = aggregate_usage(usage_records, exclude_plugins=True)
    plugin_usage = aggregate_plugin_usage(usage_records)
    duplicates = detect_duplicates_simple(artifacts)
    registry = load_usage_registry()
    advisories = scope_advisory(registry)

    # 品質再スコアは claude -p を伴うため audit パイプライン（Python）からは呼ばない（[ADR-037]）。
    # ここでは既存 baselines を読むのみ（決定論）。再スコアは audit SKILL.md が
    # quality_monitor の2相（--emit-requests → assistant 採点 → --ingest）でオーケストレーションする。
    # skip_rescore は後方互換のため受け取るが、本パイプラインでは LLM を起動しない。
    quality_baselines = None
    _ = skip_rescore  # 後方互換（CLI --skip-rescore）。LLM 起動は廃止済み
    baselines = load_quality_baselines()
    if baselines:
        quality_baselines = baselines

    gstack_analytics = build_gstack_analytics_section(usage_records)
    untagged = detect_untagged_reference_candidates(artifacts, usage, project_dir=proj)
    # CLAUDE.md は在るが Skills trigger 抽出 0 → 除外ロジックが効かず誤検知になる (#295)。
    # その状態で出た untagged 候補は誤検知の可能性が高いので suppress し、明示的に surface する。
    untagged_skipped_count = 0
    if untagged and claude_md_unparseable(proj):
        untagged_skipped_count = len(untagged)
        untagged = []

    # issues.py の collect_issues と同一の検出関数を共有し origin 除外を通す（#419）
    hardcoded_values = collect_hardcoded_value_issues(artifacts)

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

    environment_report_lines = None
    score_count = sum([coherence_score, telemetry_score, constitutional_score])
    if score_count >= 2:
        try:
            from fitness.environment import compute_environment_fitness, format_environment_report
            env_result = compute_environment_fitness(proj)
            environment_report_lines = format_environment_report(env_result)
        except Exception as e:
            print(f"Environment Fitness スキップ: {e}", file=sys.stderr)

    pipeline_health_report_lines = None
    if pipeline_health:
        try:
            from pipeline_reflector import build_pipeline_health_section
            pipeline_health_report_lines = build_pipeline_health_section()
        except Exception as e:
            print(f"Pipeline Health スキップ: {e}", file=sys.stderr)

    memory_trace_report_lines = None
    if memory_trace:
        try:
            from .memory import build_memory_trace_audit_section
            memory_trace_report_lines = build_memory_trace_audit_section(
                project_path=str(proj),
            )
        except Exception as e:
            print(f"Memory Trace スキップ: {e}", file=sys.stderr)

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

    _record_audit_completion(
        coherence_report=coherence_report_lines,
        telemetry_report=telemetry_report_lines,
        environment_report=environment_report_lines,
    )

    growth_report_lines = None
    if growth:
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

    from rl_common.config import load_user_config
    from .usage import aggregate_contribution_scores
    _user_cfg = load_user_config()
    _max_skill_count = int(_user_cfg.get("max_skill_count", 30))
    _contribution_scores = aggregate_contribution_scores(usage_records)

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
        memory_trace_report=memory_trace_report_lines,
        cross_project_report=cross_project_report_lines,
        growth_report=growth_report_lines,
        contribution_scores=_contribution_scores if _contribution_scores else None,
        max_skill_count=_max_skill_count,
        untagged_skipped_count=untagged_skipped_count,
    )


def _build_growth_report(
    proj: Path, *, skip_llm: bool = False, issues_summary: Optional[Any] = None
) -> List[str]:
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

        from telemetry_query import query_sessions, query_corrections
        sessions = query_sessions(project=project_name)
        corrections = query_corrections(project=project_name)
        crystallized = count_crystallized_rules(project=project_name)
        sessions_count = len(sessions) if sessions else 0
        corrections_count = len(corrections) if corrections else 0

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

        level_info = compute_level(env_score)

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

        events = query_crystallizations(project=project_name)
        if events:
            lines.append("### Crystallization Log")
            for ev in events[-10:]:
                ts = ev.get("ts", "")[:10]
                targets = ", ".join(ev.get("targets", [])[:3]) or "(no targets)"
                lines.append(f"- {ts}: {targets}")
            lines.append("")

        profile = compute_profile(project_name)
        if profile.strengths or profile.personality_traits:
            lines.append("### Environment Profile")
            if profile.strengths:
                lines.append(f"**Strengths:** {', '.join(profile.strengths)}")
            if profile.personality_traits:
                lines.append(f"**Traits:** {', '.join(profile.personality_traits)}")
            lines.append(f"**Style:** {profile.crystallization_style}")
            lines.append("")

        story = generate_story(project_name)
        if story and "まだ" not in story:
            lines.append("### Growth Story")
            lines.append(story)
            lines.append("")

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
