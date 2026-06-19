#!/usr/bin/env python3
"""Evolve オーケストレーター。

Observe データ確認 → Discover → Enrich → Optimize → Reorganize → Prune(+Merge) →
Fitness Evolution → Report の全フェーズを1つのコマンドで実行する。
"""
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"))

# Module-level references for testability (populated on first call)。
# #531 §3-1: self-mutation スロットはパッケージ evolve（__init__）に残す。phase 分割後も
# `import evolve as _evolve_mod; _evolve_mod.skill_evolve_assessment` の束縛先が __init__ で
# 一致するため self-mutation が維持される（_env.py 等の sub-module へ移さない）。
skill_evolve_assessment = None
collect_issues = None

# env / slug / tier 系 helper・定数は #531 PR2 で _env.py へ抽出。
# sys.path.insert（scripts/lib）後に import すること（_resolve_data_dir が rl_common を要する）。
# 全名前を re-export し `from evolve import X` の後方互換と setattr(evolve, ...) 束縛を保つ。
from ._env import (  # noqa: E402
    ENV_TIER_THRESHOLDS,
    _resolve_data_dir,
    _resolve_evolve_slug,
    _resolve_pj_slug,
    _compute_env_score_struct,
    _env_score_degraded,
    _apply_remediation_suppression,
    _surface_constitutional_status,
    _count_env_artifacts,
    _tier_from_count,
    _compute_env_tier,
)

# warning / stderr sink ヘルパーは _capture.py に分離（PR 3/8, refs #531）。
# `from evolve import _capture_warnings, _TeeStderr, _capture_audit_stderr` の後方互換を維持。
from ._capture import _capture_warnings, _TeeStderr, _capture_audit_stderr

# report / growth・データ不足ガイダンス系 helper は _report.py に分離（#8 から先行分離, refs #531）。
# 末端 helper（引数で完結・PLUGIN_ROOT 直参照のみ）。run_evolve 内の直接呼びは
# re-export で __init__ 名前空間に名前が入るため解決される。
from ._report import _emit_growth_crystallization, _warn_insufficient_data

# run_evolve のフェーズ間共有ローカルを束ねる dataclass は _context.py に分離（PR 5/8, refs #531）。
# `from evolve import EvolveContext` の後方互換を保つ。new_result() は束縛フェンスのため
# _resolve_evolve_slug を `import evolve as _ev` 経由で呼ぶ（_context.py の docstring 参照）。
from ._context import EvolveContext

# 診断フェーズ群（Phase 1〜3.4）は phases_diagnose.py に分離（PR 6/8, refs #531）。
# phases_diagnose は __init__ を module-level import しない（循環回避・evolve 参照は関数内）ため
# ここで import しても循環しない。
from .phases_diagnose import run_diagnose_phases

# #517: DATA_DIR / EVOLVE_STATE_FILE はパッケージ（__init__）load 時に env 優先で再解決する。
# `del sys.modules["evolve"]` + reimport で CLAUDE_PLUGIN_DATA を再評価させる契約
# （test_evolve_data_dir_env）を保つため、_env から frozen 値を re-export するのではなく
# __init__ で _resolve_data_dir() を呼び直して package 属性に束縛する。解決ロジック自体は
# _env._resolve_data_dir が単一ソース。
DATA_DIR = _resolve_data_dir()
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"

# state / データ十分性 / fitness 系 helper は #531 PR4 で _state.py へ抽出。
# 全名前を re-export し `from evolve import X` の後方互換と setattr(evolve, ...) 束縛を保つ。
# _state 側は DATA_DIR / EVOLVE_STATE_FILE を module-top で掴まず、呼び出し時に
# `import evolve as _ev` で遅延参照する（#517 reimport 契約のため上の package 属性が単一ソース）。
from ._state import (  # noqa: E402
    load_evolve_state,
    save_evolve_state,
    count_new_sessions,
    count_new_observations,
    _build_trigger_summary,
    compute_trend,
    check_data_sufficiency,
    _count_total_observations,
    check_fitness_function,
)


def run_evolve(
    project_dir: Optional[str] = None,
    dry_run: bool = False,
    skip_skills: Optional[set] = None,
    skip_llm_evolve: bool = False,
    confirmed_batch: bool = False,
    observe_first: bool = False,
) -> Dict[str, Any]:
    """全フェーズを実行する。

    Args:
        project_dir: プロジェクトディレクトリ
        dry_run: True の場合、レポートのみ出力し変更は行わない
        observe_first: True の場合、安価な observe + fitness ゲートだけ算出して
            重いフェーズ（discover/audit/skill_evolve/remediation/prune…）を回さず
            early-return する（#407）。SKILL Step 1 の lightweight/skip 分岐を
            「フル分析コストを払う前」に効かせるための pre-flight モード。

    Returns:
        各フェーズの結果を含む辞書
    """
    # #531 束縛フェンス: monkeypatch (setattr(evolve, ...)) と本体 self-mutation が効く束縛先を
    # パッケージ evolve（__init__）に集約する。フェーズ分割後に run_evolve が別 module へ移っても
    # 差し替え対象 helper を evolve.<name> 経由で呼べば、名前解決すり抜け（test 緑のまま実関数が
    # 走る silent fail / ADR-048 未明示の罠）を構造的に防げる。
    import evolve as _ev

    # #531 PR5: フェーズ間共有ローカル（引数 + 初期化フェーズで作る proj_root / generated_at /
    # warning_sink / tier / tier_breakdown）を EvolveContext に束ねる。振る舞いはゼロ変更で、
    # 以降 run_evolve 本体はローカル変数の代わりに ctx.<field> を参照する（phase 抽出 PR で
    # (result, ctx) シグネチャに乗せる前段）。result 初期 dict は ctx.new_result() が
    # キー・値とも完全一致で構築する（_resolve_evolve_slug は束縛フェンス経由で呼ぶ）。
    ctx = EvolveContext.create(
        project_dir, dry_run, skip_skills, skip_llm_evolve, confirmed_batch
    )
    result: Dict[str, Any] = ctx.new_result()

    # #531 PR6: 診断フェーズ群（Phase 1〜3.4）は phases_diagnose.run_diagnose_phases に抽出済み。
    # result を in-place mutate する。observe_first は ctx でなく明示引数で渡す（ctx は #4 dataclass
    # の契約・observe_first は pre-flight 制御フラグで状態でないため）。early-return は
    # result["skipped_heavy_phases"] フラグで表現し、ここで本体を打ち切る。
    run_diagnose_phases(result, ctx, observe_first=observe_first)
    if result.get("skipped_heavy_phases"):
        return result

    # Phase 3.5: Remediation（audit + discover + skill_evolve の結果を統合）
    try:
        import evolve as _evolve_mod2
        if _evolve_mod2.collect_issues is None:
            from audit import collect_issues as _ci
            _evolve_mod2.collect_issues = _ci
        collect_issues = _evolve_mod2.collect_issues
        from remediation import classify_issues as classify_remediation_issues
        proj = Path(project_dir) if project_dir else Path.cwd()
        issues = collect_issues(proj)

        # --- discover の tool_usage 結果を issue に変換 ---
        from issue_schema import (
            make_rule_candidate_issue,
            make_hook_candidate_issue,
            make_skill_evolve_issue,
            make_skill_triage_issue,
            VERIFICATION_RULE_CANDIDATE,
            make_verification_rule_issue,
            make_workflow_checkpoint_issue,
            make_stall_recovery_issue,
            make_skill_quality_issue,
        )
        discover_data = result["phases"].get("discover", {})
        tool_usage = discover_data.get("tool_usage_patterns", {})
        if tool_usage:
            rule_candidates = tool_usage.get("rule_candidates", [])
            rules_dir_str = str(Path.home() / ".claude" / "rules")
            for rc in rule_candidates:
                issues.append(make_rule_candidate_issue(
                    rc, rules_dir_str=rules_dir_str,
                ))
            hook_candidate = tool_usage.get("hook_candidate")
            if hook_candidate and not tool_usage.get("hook_status", {}).get("installed"):
                total_count = sum(
                    item.get("count", 0)
                    for item in tool_usage.get("builtin_replaceable", [])
                )
                issues.append(make_hook_candidate_issue(
                    hook_candidate, total_count,
                ))

        # --- 高頻度 rule_violation_observed を hook_candidate に昇格 (#585) ---
        # rule_installed_but_not_enforced（ルール導入済みだが実行が止まっていない）の
        # 高頻度違反は surface のみだったが、builtin_replaceable と同様に enforcement hook
        # 候補として remediation proposable に乗せる。レーン分離（discover）の出力を再利用。
        rule_violations = discover_data.get("rule_violation_observed", [])
        if rule_violations:
            from rule_violation_lane import (
                make_hook_candidate_issues_from_rule_violations,
            )
            issues.extend(
                make_hook_candidate_issues_from_rule_violations(rule_violations)
            )

        # --- skill_evolve の適性判定結果を issue に変換 ---
        se_phase = result["phases"].get("skill_evolve", {})
        for assessment in se_phase.get("assessments", []):
            suitability = assessment.get("suitability", "low")
            if suitability in ("high", "medium"):
                skill_md_path = str(Path(assessment["skill_dir"]) / "SKILL.md")
                issues.append(make_skill_evolve_issue(
                    assessment, skill_md_path,
                ))

        # --- skill_triage の結果を issue に変換 ---
        triage_phase = result["phases"].get("skill_triage", {})
        if not triage_phase.get("skipped"):
            for action in ("CREATE", "UPDATE", "SPLIT", "MERGE"):
                for triage in triage_phase.get(action, []):
                    issue = make_skill_triage_issue(triage)
                    if issue:
                        issues.append(issue)

        # --- skill_quality_pattern_gap を issue に変換 ---
        quality_patterns = result["phases"].get("quality_patterns", {})
        quality_traces = result["phases"].get("quality_traces", {})
        for skill_name, qr in quality_patterns.items():
            if isinstance(qr, dict) and "recommendation" in qr:
                rec = qr["recommendation"]
                missing_req = rec.get("required_missing", [])
                missing_rec = rec.get("recommended_missing", [])
                if missing_req:  # required が欠けている場合のみ issue 化
                    trace_info = quality_traces.get(skill_name, {})
                    issues.append(make_skill_quality_issue({
                        "skill_name": skill_name,
                        "domain": rec.get("domain", "default"),
                        "missing_required": missing_req,
                        "missing_recommended": missing_rec,
                        "pattern_score": qr["patterns"].get("score", 0.0),
                        "overall_score": trace_info.get("overall_score", 0.0),
                        "confidence": 0.7 if missing_req else 0.4,
                    }))

        # --- verification_needs を issue に変換 ---
        verification_needs = discover_data.get("verification_needs", [])
        for vn in verification_needs:
            detection_result = vn.get("detection_result", {})
            issues.append(make_verification_rule_issue(
                vn, detection_result,
                project_dir_str=str(proj),
            ))

        # --- stall_recovery_patterns を issue に変換 ---
        stall_patterns = discover_data.get("stall_recovery_patterns", [])
        for sp in stall_patterns:
            issues.append(make_stall_recovery_issue(sp))

        # --- workflow_checkpoint_gaps を issue に変換 ---
        workflow_gaps = discover_data.get("workflow_checkpoint_gaps", [])
        for wg in workflow_gaps:
            skill_name = wg.get("skill_name", "")
            for gap in wg.get("gaps", []):
                issues.append(make_workflow_checkpoint_issue(
                    gap,
                    skill_name=skill_name,
                    skill_dir=str(proj / ".claude" / "skills" / skill_name),
                ))

        classified = classify_remediation_issues(issues)

        # proposable を custom/global スコープ別に集計（#183 false positive 可視化）。
        # #477-1: impact_scope（impact 由来）を最終権威にして global へ寄せる。
        # ~/.claude/rules/ 配下の global rule は compute_impact_scope が "global" を返す
        # 一方 classify_artifact_origin は "custom" を返すため、origin 単独判定では
        # proposable_custom_individual に流れ込み proposable_global が 0 になっていた。
        # partition_proposable_by_scope が impact_scope OR origin=="global" で整合を取る。
        from audit import classify_artifact_origin  # artifact_scope は re-export しないため audit から直接 import
        from remediation import partition_proposable_by_scope

        def _origin_resolver(file_path):
            try:
                return classify_artifact_origin(Path(file_path))
            except Exception:
                return "custom"

        _scope_partition = partition_proposable_by_scope(
            classified["proposable"], origin_resolver=_origin_resolver
        )
        proposable_custom = _scope_partition["custom"]
        proposable_global = _scope_partition["global"]

        # #477-2 配線: 却下済み提案を suppression ledger で除外する（べき等性原則 =
        # 重複提案 MUST NOT）。個別承認に出す proposable_custom から、過去に却下/スキップ
        # して記録された提案（dedup_key 一致・TTL45日内）を取り除く。filter は読み取りのみ
        # （副作用なし）なので dry-run でも適用してよい。書込（record_rejection）は SKILL.md
        # 側が個別承認の確定時に行い、dry-run では呼ばない。
        # 抑制件数は observability として result に残す（silence != evaluated）。
        try:
            from remediation.suppression_ledger import resolve_slug as _rem_resolve_slug
            _suppress_slug = _rem_resolve_slug(cwd=proj)
        except Exception:
            _suppress_slug = proj.name
        proposable_custom, suppressed_count = _apply_remediation_suppression(
            proposable_custom, slug=_suppress_slug
        )

        # classified にも split リストを追加し、トップレベルの count と整合させる。
        # 修正前は classified に proposable_custom キーがなかったため、
        # jq で classified.proposable_custom を参照すると null になり、
        # phases.remediation.proposable_custom（例: 5）と食い違っていた (#353⑪)。
        classified["proposable_custom"] = proposable_custom
        classified["proposable_global"] = proposable_global

        # proposable_custom を confidence しきい値で「個別承認」「まとめてスキップ」に分割
        # （#377-3）。低 confidence FP 群（conf 0.5 中心）で per-item 承認 MUST が質問攻めに
        # なるのを防ぐ。判定は決定論コードに置き、SKILL.md は count を消費するだけにする。
        from remediation import partition_proposable_by_confidence
        _partition = partition_proposable_by_confidence(proposable_custom)
        classified["proposable_custom_individual"] = _partition["individual"]
        classified["proposable_custom_batch_skip"] = _partition["batch_skip"]

        # #494 発見1: record_rejection の決定論 fallback。SKILL.md Step 5.5 の inline
        # record_rejection を取りこぼしても、解決されないまま連続して個別承認に出続けた
        # 提案を自動却下し「毎回再出」を断つ。surfaced マーカーで連続提示回数を追跡する。
        # 読み取り→閾値判定は dry-run でも実行できるが、書込（marker / ledger）は persist で
        # ゲートする（dry-run 非書込・pitfall_dryrun_stateful_store_write）。import 失敗時は
        # フェーズを壊さず 0 件にフォールバックする。
        auto_rejected_count = 0
        try:
            from remediation.suppression_ledger import reconcile_surfaced as _reconcile
            _recon = _reconcile(
                _partition["individual"], slug=_suppress_slug, persist=not dry_run
            )
            auto_rejected_count = int(_recon.get("auto_rejected", 0))
        except Exception:
            auto_rejected_count = 0

        remediation_data = {
            "total_issues": len(issues),
            "auto_fixable": len(classified["auto_fixable"]),
            "proposable": len(classified["proposable"]),
            "proposable_custom": len(proposable_custom),
            "proposable_global": len(proposable_global),
            "proposable_custom_individual": len(_partition["individual"]),
            "proposable_custom_batch_skip": len(_partition["batch_skip"]),
            # #477-2: suppression ledger により次回再提示を抑制した件数（silence != evaluated）。
            "suppressed_by_ledger": suppressed_count,
            # #494: 連続再出で自動却下した件数（SKILL.md record_rejection の決定論 fallback）。
            "auto_rejected_by_reconcile": auto_rejected_count,
            "manual_required": len(classified["manual_required"]),
            "classified": classified,
        }
        result["phases"]["remediation"] = remediation_data
    except Exception as e:
        result["phases"]["remediation"] = {"error": str(e)}

    # Phase 3.7: Reorganize（Prune の前）
    # scipy のクラスタリングが NaN を含む距離行列で RuntimeWarning を出す（#340）。
    # この警告は例外として throw されず phase.error に乗らないため、capture して
    # result["warnings"] に記録し self_analysis が surface できるようにする（#341）。
    try:
        from reorganize import run_reorganize
        with _capture_warnings(ctx.warning_sink):
            reorganize_result = run_reorganize(project_dir)
        result["phases"]["reorganize"] = reorganize_result
    except Exception as e:
        result["phases"]["reorganize"] = {"error": str(e)}

    # Phase 4: Prune（dry-run 時は候補のみ）
    try:
        from prune import run_prune
        # Reorganize の merge_groups を Prune に渡す
        reorganize_data = result["phases"].get("reorganize", {})
        merge_groups = reorganize_data.get("merge_groups", []) if not reorganize_data.get("skipped") else []
        prune_result = run_prune(project_dir, reorganize_merge_groups=merge_groups)
        result["phases"]["prune"] = prune_result
    except Exception as e:
        result["phases"]["prune"] = {"error": str(e)}

    # Phase 4.1: split↔archive 相互排他 reconcile（#301 #302 root cause fix）
    # reorganize と prune が揃った後、archive 候補のスキルを split 候補から除外する
    # （消す対象を同じ run で分割提案する矛盾を本流で解消。決定論・LLM 非依存）。
    try:
        from evolve_introspect import reconcile_split_archive
        result["phases"]["split_archive_reconcile"] = reconcile_split_archive(result)
    except Exception as e:
        result["phases"]["split_archive_reconcile"] = {"error": str(e)}

    # Phase 4.2: skill_evolve↔archive 相互排他 reconcile（#400 バグ#2）
    # archive 候補のスキルを skill_evolve（自己進化提案）から除外する。消そうとする対象に
    # 自己進化を組み込めと提案する矛盾を本流で解消（決定論・LLM 非依存）。emit_decisions より
    # 前に降格させることで矛盾候補を fitness 母集団からも外す。
    try:
        from evolve_reconcile import reconcile_skill_evolve_archive
        result["phases"]["skill_evolve_archive_reconcile"] = reconcile_skill_evolve_archive(result)
    except Exception as e:
        result["phases"]["skill_evolve_archive_reconcile"] = {"error": str(e)}

    # Phase 4.3: remediation batch_skip を observability に強制昇格（#400 バグ#6）。
    # reconcile 後の最終 batch_skip 件数を result["observability"] に注入し、Step 3.8 が必ず
    # surface する構造化経路に乗せる（SKILL.md の surface MUST 依存をやめ silence != evaluated を担保）。
    try:
        from evolve_reconcile import build_remediation_batch_skip_observability
        _bs_line = build_remediation_batch_skip_observability(result)
        if _bs_line is not None:
            obs = result.get("observability")
            if not isinstance(obs, dict) or "error" in obs:
                obs = {} if not isinstance(obs, dict) else obs
                result["observability"] = obs
            obs["remediation_batch_skip"] = _bs_line
    except Exception:
        pass

    # Phase 4.5: Pitfall Hygiene（自己進化済みスキルの剪定）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from pitfall_manager import pitfall_hygiene as run_pitfall_hygiene
        # 適性判定から frequency_scores を取得
        se_phase = result["phases"].get("skill_evolve", {})
        freq_scores = {}
        for a in se_phase.get("assessments", []):
            if a.get("scores"):
                freq_scores[a["skill_name"]] = a["scores"].get("frequency", 1)
        proj = Path(project_dir) if project_dir else Path.cwd()
        hygiene_result = run_pitfall_hygiene(proj, frequency_scores=freq_scores)
        result["phases"]["pitfall_hygiene"] = hygiene_result
    except Exception as e:
        result["phases"]["pitfall_hygiene"] = {"error": str(e)}

    # Phase 4.6: Rationalization Table（合理化防止テーブル — pitfall_hygiene から取得）
    hygiene_data = result["phases"].get("pitfall_hygiene", {})
    rt = hygiene_data.get("rationalization_table", {})
    if rt and not rt.get("data_insufficient"):
        result["phases"]["rationalization_table"] = rt

    # 用語集 seed（CONTEXT.md 不在 + jargon ≥ 閾値）は #275 で独立 phase にしていたが、
    # #278 の observability contract に統合済み（build_glossary_drift_section が emit し
    # result["observability"]["glossary_drift"] に surface）。ここでの個別 emit は不要。

    # Phase 5: Fitness Evolution（評価関数の改善チェック）
    try:
        from fitness_evolution import run_fitness_evolution, fitness_next_action
        fitness_evo_result = run_fitness_evolution()
        # #400 バグ#5: insufficient_data の結論 1 行（next_action）を現 run の提案有無で確定する。
        # skill_evolve high/medium も discover matched_skills も 0 = 提案が構造的に出ない PJ →
        # 「fitness は使わない設計。対応不要」。1 つでも提案があれば「放置でOK（継続で貯まる）」。
        if fitness_evo_result.get("status") == "insufficient_data":
            _se = result["phases"].get("skill_evolve", {})
            _disc = result["phases"].get("discover", {})
            _proposals_available = (
                _se.get("high_suitability", 0) > 0
                or _se.get("medium_suitability", 0) > 0
                or len(_disc.get("matched_skills", []) or []) > 0
            )
            _na = fitness_next_action(_proposals_available)
            fitness_evo_result["next_action"] = _na
            # #559: 冗長フィールドは details に隔離済み。top-level の上書きを details にも追従させ
            # 矛盾を防ぐ（details.next_action が結論の正準位置）。
            if isinstance(fitness_evo_result.get("details"), dict):
                fitness_evo_result["details"]["next_action"] = _na
        result["phases"]["fitness_evolution"] = fitness_evo_result
    except Exception as e:
        result["phases"]["fitness_evolution"] = {"error": str(e)}

    # Phase 6: Self-Evolution（パイプライン自己改善）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from pipeline_reflector import (
            analyze_trajectory,
            calibrate_confidence,
            check_calibration_regression,
            check_control_chart,
            detect_false_positives,
            generate_adjustment_proposals,
            load_self_evolution_config,
            record_proposal,
        )
        se_config = load_self_evolution_config()
        analysis = analyze_trajectory(config=se_config)

        if not analysis["sufficient"]:
            result["phases"]["self_evolution"] = {
                "skipped": True,
                "reason": analysis["diagnosis"],
                "total": analysis["total"],
                "min_required": analysis["min_required"],
            }
        else:
            # Trajectory analysis
            fp_result = detect_false_positives(
                analysis.get("_outcomes", []),  # fallback: empty
                se_config,
            )

            # Calibration
            cal_result = calibrate_confidence(config=se_config)
            calibrations = cal_result.get("calibrations", {})

            # Control chart + regression check
            cc_result = check_control_chart(calibrations) if calibrations else {}
            from pipeline_reflector import load_outcomes
            outcomes = load_outcomes(lookback_days=se_config.get("analysis_lookback_days", 30))
            reg_result = check_calibration_regression(calibrations, outcomes, se_config) if calibrations else {"has_regression": False, "regressions": {}}

            # Generate proposals
            proposals = generate_adjustment_proposals(calibrations, cc_result, reg_result, se_config) if calibrations else []

            # Record proposals (unless dry-run)
            recorded_proposals = []
            for p in proposals:
                rec = record_proposal(p, dry_run=dry_run)
                if rec:
                    recorded_proposals.append(rec)

            result["phases"]["self_evolution"] = {
                "skipped": False,
                "analysis": {
                    "total": analysis["total"],
                    "by_type": analysis["by_type"],
                    "diagnosis": analysis["diagnosis"],
                },
                "false_positives": {
                    "high_confidence_count": len(fp_result.get("high_confidence_rejections", [])),
                    "systematic_flags": fp_result.get("systematic_rejections", {}),
                },
                "calibrations": calibrations,
                "control_chart": cc_result,
                "regression": reg_result,
                "proposals": proposals,
                "proposals_recorded": len(recorded_proposals),
            }
    except Exception as e:
        result["phases"]["self_evolution"] = {"error": str(e)}

    # Trigger history summary for report
    result["trigger_summary"] = _build_trigger_summary()

    # キャプチャした警告を self_analysis が読めるよう result に確定する（#341）。
    # 必ず self_analysis の前に格納する（runtime_errors が警告を surface するため）。
    result["warnings"] = ctx.warning_sink

    # Phase 7: Self-Analysis（#299 — evolve 自身の result を自己解析し issue 候補を生成）
    # 全フェーズが揃った後に実行する（phases の error / 提案矛盾 / 改善余地 / 警告を読む）。
    # 決定論・LLM 非依存。起票自体は SKILL が人間承認の後に行う（半自動）。
    try:
        from evolve_introspect import analyze_evolve_result
        result["self_analysis"] = analyze_evolve_result(result, project_dir)
    except Exception as e:
        result["self_analysis"] = {"error": str(e)}

    # State 更新（dry-run でない場合）
    if not dry_run:
        # Phase 1 の sufficiency は phases_diagnose で result["phases"]["observe"] に格納済み
        # （同一 dict・sessions/observations キーは不変）。診断フェーズ抽出（#531 PR6）後は
        # ここから取り直す（ローカル sufficiency が run_diagnose_phases 側へ移ったため）。
        sufficiency = result["phases"]["observe"]
        state = load_evolve_state()
        state.update({
            "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
            "sessions_processed": sufficiency["sessions"],
            "observations_processed": sufficiency["observations"],
        })
        # Self-evolution state
        se_phase = result["phases"].get("self_evolution", {})
        if not se_phase.get("skipped") and not se_phase.get("error"):
            state["last_calibration_timestamp"] = datetime.now(timezone.utc).isoformat()
            history = state.get("calibration_history", [])
            history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "calibrations": se_phase.get("calibrations", {}),
                "proposals_count": len(se_phase.get("proposals", [])),
            })
            state["calibration_history"] = history
        # Tool usage snapshot for trend tracking
        discover_data = result["phases"].get("discover", {})
        tool_usage = discover_data.get("tool_usage_patterns", {})
        if tool_usage:
            state["tool_usage_snapshot"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "builtin_replaceable": sum(
                    item.get("count", 0)
                    for item in tool_usage.get("builtin_replaceable", [])
                ),
                "sleep_patterns": sum(
                    p.get("count", 0)
                    for p in tool_usage.get("repeating_patterns", [])
                    if "sleep" in p.get("pattern", "").lower()
                ),
                "bash_ratio": (
                    tool_usage.get("bash_calls", 0) / tool_usage.get("total_tool_calls", 1)
                    if tool_usage.get("total_tool_calls", 0) > 0
                    else 0.0
                ),
            }
        save_evolve_state(state)

        # スヌーズ解除（evolve 実行完了で自動クリア）
        try:
            from trigger_engine import clear_snooze
            clear_snooze()
        except ImportError:
            pass

    # ── sessions.jsonl → sessions.db の batch ingest（#415 Phase A）────────
    # hot path（hooks）は jsonl 追記のみで、db への取り込みはこの batch 文脈に同居させる。
    # dry-run 時は DATA_DIR 非書込の規約に従い ingest しない。
    if not dry_run:
        try:
            import session_store
            ingested = session_store.ingest()
            result["sessions_ingested"] = ingested
        except Exception as e:
            print(f"[rl-anything:evolve] session ingest warning: {e}", file=sys.stderr)
            result["sessions_ingested"] = {"error": str(e)}

    # ── NFD: 結晶化イベント emit + growth キャッシュ更新 ────────
    if not dry_run:
        try:
            _emit_growth_crystallization(result, project_dir)
        except Exception as e:
            print(f"[rl-anything:evolve] growth emit warning: {e}", file=sys.stderr)

    # ── utterance アーカイブの増分 ingest（#430）────────────────────
    # 全PJ human 発話を恒久アーカイブに batch 取り込み（hot path ゼロ・ゼロ LLM）。
    # dry-run 時は ingest しない（実 DATA_DIR / utterances.db を書かない）。
    if not dry_run:
        try:
            from utterance_archive import ingest as _utt_ingest
            _utt_res = _utt_ingest.ingest_all_projects(progress=False)
            result["utterance_ingest"] = {
                "inserted": _utt_res.get("inserted", 0),
                "files_processed": _utt_res.get("files_processed", 0),
            }
        except Exception as e:
            result["utterance_ingest"] = {"error": str(e)}

    # ── 暗黙修正シグナルの決定論検出 → weak_signals レーン（#432）──────────
    # 4 チャネル（直後手編集 / permission deny / 言い直し / Esc 中断）をゼロ LLM で検出し、
    # weak_signals.jsonl に provenance 付きで記録（corrections 本流には入れない）。
    # 言い直し検出は上の utterance ingest で更新した utterances.db を入力に使うため後段に置く。
    # dry_run でも検出は走るが run_batch(dry_run=True) が store 書き込みを最下層で弾く。
    try:
        from weak_signals import batch as _ws_batch
        _ws_slug = _resolve_pj_slug(project_dir)
        result["weak_signals"] = _ws_batch.run_batch(_ws_slug, dry_run=dry_run)
    except Exception as e:
        result["weak_signals"] = {"error": str(e)}

    # ── weak_signals の TTL 失効マーク（#442・corrections decay 45 日と整合）─────
    # run_batch 直後・昇格候補を読む下流（#1 daily_review 等）の前に常時 emit。
    # detected_at から 45 日超かつ未昇格・未expired を expired=True にマーク（削除しない）し、
    # read_unpromoted(exclude_expired=True) から外す。dry_run は store に一切触れない（最下層 write ゲート）。
    # pj_slug を渡し当 PJ レコードのみ expired マーク（cross-PJ write 防止 #495）。
    try:
        from weak_signals import ttl as _ws_ttl
        result["weak_signals_ttl"] = _ws_ttl.mark_expired(
            dry_run=dry_run, pj_slug=_resolve_pj_slug(project_dir)
        )
    except Exception as e:
        result["weak_signals_ttl"] = {"error": str(e)}

    # ── correction capture の二層化: バッチ LLM 意味判定の Phase A emit（#431）─────
    # utterances.db の dialogue 発話を Haiku 判定リクエストに変換（決定論・ここでは LLM 非呼び出し）。
    # 実際の判定（Phase B）と weak_signals 隔離記録（Phase C）は SKILL.md 側が担う。
    # dry_run でも emit（読み取りのみ）は走るが書き込みはしない。ここでは件数とトークン見積もりのみ。
    try:
        from correction_semantic import batch as _cs_batch
        _cs_slug = _resolve_pj_slug(project_dir)
        _cs_emitted = _cs_batch.emit_judgement_requests(_cs_slug)
        result["correction_semantic"] = {
            "slug": _cs_slug,
            "unjudged": _cs_emitted.get("unjudged", 0),
            "batches": _cs_emitted.get("batches", 0),
        }
    except Exception as e:
        result["correction_semantic"] = {"error": str(e)}

    # ── 初回バックログ bootstrap モード（#443）─────────────────────────────
    # 既存の weak_signals バックログ（channel=llm_judge・未昇格）を初回 evolve で
    # まとめて確認する入口。決定論で「未消化 backlog の有無・PJ 別件数・group 化」を
    # **常時 emit** し、SKILL.md が is_bootstrap=True のとき AskUserQuestion で 3 択
    # （まとめて確認 / 日次 5 件 / TTL 失効に任せる）を人間に出す。機械は判定しない。
    # #C（correction_review）とキー共有: correction_review["bootstrap"] に相乗りさせる。
    # dry_run でも build（読み取りのみ）は走り marker を書かない。bootstrap は cwd の PJ
    # slug の backlog のみが対象（DATA_DIR 全PJ共通 pitfall）。
    try:
        from correction_semantic import bootstrap_backlog as _bb
        _bb_slug = _resolve_pj_slug(project_dir)
        _bb_res = _bb.build(_bb_slug, dry_run=dry_run)
        result.setdefault("correction_review", {})["bootstrap"] = _bb_res
    except Exception as e:
        result.setdefault("correction_review", {})["bootstrap"] = {"error": str(e)}

    # ── 今日の修正確認（daily_review・#446）─────────────────────────────────
    # 前回 evolve 以降の新規 weak_signal（channel=llm_judge・未昇格・非expired）のうち
    # 既読集合（correction_review_seen.jsonl）に無いものを idiom 単位で group 化し、頻度降順・
    # 上位 5 件を **常時 emit**（eligible でなくても groups=[] でキーを置く）。SKILL.md が
    # eligible のとき AskUserQuestion で y/n 確認し、「はい」を rl-reflect --promote-weak で昇格。
    # #443 bootstrap と同じ correction_review dict に相乗りさせる（setdefault で同居）。
    # build_review は読み取りのみ。既読追記は SKILL.md の apply（record_reviewed）に委ねる。
    # dry_run でも build（読み取りのみ）は走るが既読集合を一切書かない（最下層 write ゲート）。
    # #476-3: bootstrap が is_bootstrap=True で発火する run では、bootstrap groups が daily の
    # 対象シグナルを signal_key 単位で全包含している。Step 6.1→6.2 を順に実行すると同じシグナルを
    # 2 回質問するため、bootstrap-pending の signal_key を daily から除外する（二重提示の解消）。
    try:
        from correction_semantic import daily_review as _dr
        _dr_slug = _resolve_pj_slug(project_dir)
        _bootstrap = (result.get("correction_review") or {}).get("bootstrap") or {}
        _bootstrap_keys: set = set()
        if _bootstrap.get("is_bootstrap"):
            for _g in (_bootstrap.get("groups") or []):
                _bootstrap_keys.update(_g.get("signal_keys") or [])
        result.setdefault("correction_review", {})["daily"] = _dr.build_review(
            _dr_slug, exclude_signal_keys=_bootstrap_keys, dry_run=dry_run
        )
    except Exception as e:
        result.setdefault("correction_review", {})["daily"] = {"error": str(e)}

    # ── human-confirmed idiom の自動昇格（idiom_autopromote・ADR-047・#447）─────
    # confirmed=True（かつ未 revoke）の idiom テキストに一致する新規未昇格 weak_signal を、
    # 人間再確認なしで corrections へ自動昇格（source="idiom_dict"・重み 1.0）。
    # **最重要の不変条件**: confirmed が 1 件も無ければ promoted=0（雪崩防止）。現環境の
    # 313 idiom は全件未確認なので起動時点で一切発動しない。confirmed は #446 の人間 y/n でしか立たない。
    # 安全弁①: daily_cap（userConfig idiom_autopromote_daily_cap・既定 10）件で打ち切り、
    # 超過は capped で次回 run に持ち越す。error でもキーを置く（常時 emit）。promoted は int で
    # emit（#448 growth_report が (d.get("promoted") or 0) で読む契約）。
    # dry_run は promote_signals が corrections / weak_signals に一切触れない（最下層 write ゲート）。
    try:
        from correction_semantic import idiom_autopromote as _iap
        from rl_common.config import load_user_config as _luc
        _iap_slug = _resolve_pj_slug(project_dir)
        _iap_cap = int(_luc().get("idiom_autopromote_daily_cap", 10))
        result["idiom_autopromote"] = _iap.autopromote(
            _iap_slug,
            project_path=str(project_dir) if project_dir else "",
            daily_cap=_iap_cap,
            dry_run=dry_run,
        )
    except Exception as e:
        result["idiom_autopromote"] = {"error": str(e)}

    # ── evolve 提案 accept/reject の決定論キャプチャ（#360-A, ADR-041）────────
    # 候補スキルの before_sha をキューに emit。適用実績=accept / 明示却下=reject は
    # SKILL.md Step 7.8 の drain（ingest_decisions）が optimize_history に記録する。
    # dry_run 時は pending を計算するが書き込まない（emit_decisions が内部でガード）。
    try:
        from evolve_decisions import emit_decisions
        result["evolve_decisions"] = emit_decisions(result, project_dir, dry_run=dry_run)
    except Exception as e:
        result["evolve_decisions"] = {"error": str(e)}

    # ── 成長レポート（決定論・read-only）（#448）─────────────────────────────
    # audit phase 後に corrections を取得して「あと N 件で次フェーズ」「今日の昇格成果」を
    # 常時 emit（error でもキーを置く）。ファイル書き込みなし（growth_report は read-only）。
    try:
        from growth_report import build_growth_report
        from telemetry_query import query_corrections as _query_corrections_gr
        _gr_project_name = Path(project_dir).name if project_dir else Path.cwd().name
        _gr_corrections = _query_corrections_gr(project=_gr_project_name)
        result["growth_report"] = build_growth_report(
            _gr_project_name,
            corrections=_gr_corrections,
            review_result=result.get("correction_review"),
            autopromote_result=result.get("idiom_autopromote"),
        )
    except Exception as e:
        result["growth_report"] = {"error": str(e)}

    return result


def main() -> None:
    import argparse

    # #531 束縛フェンス: main から差し替え対象（run_evolve / _resolve_evolve_slug）を呼ぶときは
    # evolve.<name> 経由にする。main を cli.py へ抽出後も setattr(evolve, ...) が効き続ける。
    import evolve as _ev

    parser = argparse.ArgumentParser(description="Evolve オーケストレーター")
    parser.add_argument("--project-dir", default=None, help="プロジェクトディレクトリ")
    parser.add_argument("--dry-run", action="store_true", help="レポートのみ、変更なし")
    parser.add_argument("--skip-skills", default=None, help="評価をスキップするスキル名（カンマ区切り）")
    parser.add_argument("--skip-llm-evolve", action="store_true", help="skill_evolve の LLM 評価を全スキップ")
    parser.add_argument("--confirmed-batch", action="store_true", help="batch_guard_trigger 確認済み。件数が閾値を超えても LLM 評価を続行する")
    parser.add_argument(
        "--observe-first",
        action="store_true",
        help=(
            "安価な observe + fitness ゲートだけ算出して即返す pre-flight モード（#407）。"
            "重いフェーズ（discover/audit/skill_evolve/remediation/prune…）は回さない。"
            "SKILL Step 1 がまずこれで action（lightweight/skip/full）を判定し、"
            "フルが必要なときだけ --observe-first 無しの dry-run を別途走らせる。"
        ),
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help=(
            "evolve 本体を回さず、保留中の提案 accept/reject を optimize_history に drain する（#402）。"
            "apply 後の SKILL.md Step 7.8 で `rl-evolve --drain` を1コマンド実行する。"
            "pending は marker（emit が dry-run でも記録）か --result-json から取る。"
        ),
    )
    parser.add_argument(
        "--result-json",
        default=None,
        help="--drain 時の pending ソース result JSON（未指定なら marker を使う）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "指定すると result JSON 全体をこのパスに書き、stdout には1行サマリだけ出す。"
            "巨大 JSON の stdout 一発出力が head/Bash 出力上限で途中切断され invalid JSON 化する事故を防ぐ。"
            "未指定時は従来通り full JSON を stdout に出す（後方互換）"
        ),
    )
    parser.add_argument(
        "--print-out-path",
        action="store_true",
        help=(
            "evolve 本体を回さず、slug 解決済みの OUT パス `/tmp/rl_evolve_<slug>.json` の1行だけを"
            "print して即返す（#525-3）。SKILL.md Step 1 の SLUG/OUT 再導出ボイラープレートを短縮する"
            "（rl-evolve は既に slug を解決できるため）。"
        ),
    )

    args = parser.parse_args()

    # #525-3: OUT パスだけ印字する軽量モード（評価本体は回さない）。
    # slug 解決 + /tmp パス組み立てのみで DATA_DIR resolver には触れない（#517 と非競合）。
    if args.print_out_path:
        _root = Path(args.project_dir) if args.project_dir else Path.cwd()
        _slug = _ev._resolve_evolve_slug(_root)
        print(f"/tmp/rl_evolve_{_slug}.json")
        return

    # #402: drain モード — evolve 本体を回さず保留中の決定を optimize_history へ記録する。
    # CLI(=tool 文脈)で走るため reader と同一 DATA_DIR に書く＝#358(DATA_DIR split)を踏まない。
    if args.drain:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from evolve_decisions import drain_pending

        summary = drain_pending(project_dir=args.project_dir, result_json=args.result_json)

        # #484: 決定論 weak_signals を apply 境界で永続化する。
        # 標準フローは `rl-evolve --dry-run` 分析 → 対話適用なので、run_evolve 内の
        # run_batch(dry_run=True) は #491 契約で常にゼロ書き込みになる。決定論検出は冪等
        # （signal_key dedup）なので、tool 文脈・非 dry-run・正準 DATA_DIR で走る drain で
        # 永続化する（evolve_decisions の drain と同型・#400 の盲点修正と同じ構造）。
        try:
            from weak_signals import batch as _ws_batch

            _ws_slug = _resolve_pj_slug(args.project_dir)
            summary["weak_signals_persisted"] = _ws_batch.persist_weak_signals_drain(_ws_slug)
        except Exception as e:
            summary["weak_signals_persisted"] = {"error": str(e)}

        print(json.dumps(summary, ensure_ascii=False))
        return

    _skip_skills = {s.strip() for s in args.skip_skills.split(",") if s.strip()} if args.skip_skills else None

    result = _ev.run_evolve(
        project_dir=args.project_dir,
        dry_run=args.dry_run,
        skip_skills=_skip_skills,
        skip_llm_evolve=args.skip_llm_evolve,
        confirmed_batch=args.confirmed_batch,
        observe_first=args.observe_first,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(_summarize_result(result, out_path), ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _summarize_result(result: dict, output_path: Path) -> dict:
    """`--output` 時に stdout へ出す小さな1行サマリ。

    full result を stdout に混ぜず、保存先パス・実行フェーズ一覧・env_tier だけを
    surface する。Claude は `output` のファイルを Read で読んで各フェーズや env_score を
    参照する（巨大 JSON を stdout に出すと head/Bash 上限で途中切断され invalid JSON 化するため）。

    `phases` は実フェーズ名（`result["phases"]` 配下: observe/fitness/discover/...）を列挙する。
    env_score は #523-2/#526-2 で result のトップレベルに構造化 dict として surface される
    ようになったため、1 行サマリにも level/score（degraded 時はその旨）を出す。
    `env_tier`（small/medium/large 等）も併せて surface する。
    """
    if not isinstance(result, dict):
        return {"output": str(output_path), "phases": []}
    phases_obj = result.get("phases")
    phase_names = sorted(phases_obj.keys()) if isinstance(phases_obj, dict) else sorted(result.keys())
    summary: dict = {"output": str(output_path), "phases": phase_names}
    # 同一性 metadata を 1 行サマリにも出す（#408）。読み手は stdout だけで
    # 「どの PJ・いつの・本実行か」を即検証でき、stale/別 PJ ファイルの誤読を防げる。
    for k in ("slug", "project_dir", "generated_at", "dry_run", "env_tier"):
        if k in result:
            summary[k] = result[k]
    # env_score（#523-2/#526-2）: 成功時は level/score、degraded 時は取得失敗を 1 行に出す。
    es = result.get("env_score")
    if isinstance(es, dict):
        if es.get("degraded"):
            summary["env_score"] = {
                "degraded": True,
                "previous_level": es.get("previous_level"),
            }
        else:
            summary["env_score"] = {
                "score": es.get("score"),
                "level": es.get("level"),
            }
    if result.get("observe_first"):
        summary["observe_first"] = True
        observe = result.get("phases", {}).get("observe", {})
        if isinstance(observe, dict) and observe.get("action"):
            summary["observe_action"] = observe["action"]
    return summary


if __name__ == "__main__":
    main()
