#!/usr/bin/env python3
"""修正フェーズ群（Phase 3.5〜6）を run_evolve から抽出した module（#531 PR 7/8）。

Phase 3.5（Remediation）〜 Phase 6（Self-Evolution）までを
`run_remediate_phases(result, ctx)` に束ねる。振る舞いはゼロ変更で、run_evolve 本体の
ローカル/引数参照を `ctx.<field>`（EvolveContext）から取るよう置換しただけ。observe_first も
early-return もこのブロックには存在しない（result を in-place mutate するだけで返り値なし）。

⚠️ 束縛フェンス（#531 §3）:
分割後に `setattr(evolve, "<name>", ...)` / `mock.patch.object(evolve, "<name>")` での差し替えが
本 module の名前空間ですり抜けると、テスト緑のまま実関数が走る silent fail になる。
本ブロックの grep（test_evolve_binding_paths / test_evolve_*）の結果、抽出範囲が呼ぶ
`evolve.<name>` 直接差し替え対象は **存在しない**（check_data_sufficiency / check_fitness_function /
_compute_env_score_struct / DATA_DIR / count_new_* / run_evolve / _resolve_evolve_slug は
いずれも他フェーズ＝phases_diagnose / state / main 側）。

self-mutation スロット `collect_issues` は __init__（パッケージ evolve）に残し、Phase 3.5 は
`import evolve as _evolve_mod2` 経由で参照・束縛する（現コードと同型）。`collect_issues = None`
スロット定義自体は __init__ にあり、本 module へ移さない。

monkeypatch されない末端 helper（_capture_warnings / _apply_remediation_suppression）は
sub-module（_capture / _env）から直接 import する（PR#5/#6 の流儀）。
`_apply_remediation_suppression` は test_remediation_suppression_wiring が evolve.<name>
属性の存在のみ assert し（setattr/patch.object はしない）、__init__ の re-export で担保される。

各 Phase 内の `from remediation import ...` / `from prune import run_prune` /
`from fitness_evolution import ...` / `from evolve_introspect import ...` /
`from evolve_reconcile import ...` 等の関数内 import は現状通り関数内に残す
（module 名前空間の汚染を避け既存挙動・sys.modules patch 互換を維持）。
"""
import sys
from pathlib import Path
from typing import Any, Dict

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

from ._capture import _capture_warnings
from ._env import _apply_remediation_suppression


def run_remediate_phases(result: Dict[str, Any], ctx) -> None:
    """修正フェーズ（Phase 3.5〜6）を result に in-place で書き込む。

    Args:
        result: run_evolve が ctx.new_result() で作り、run_diagnose_phases が
            Phase 1〜3.4 を書き込んだ result dict。in-place mutate する。
        ctx: EvolveContext（引数 + 初期化フェーズ共有ローカル）。
    """
    # Phase 3.5: Remediation（audit + discover + skill_evolve の結果を統合）
    try:
        import evolve as _evolve_mod2
        if _evolve_mod2.collect_issues is None:
            from audit import collect_issues as _ci
            _evolve_mod2.collect_issues = _ci
        collect_issues = _evolve_mod2.collect_issues
        from remediation import classify_issues as classify_remediation_issues
        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
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

        # #103: 情報レーンの advisory（proposable_global / rule_violation_observed）にも
        # dismiss（確認済み・以後抑制）の入口を配線する。従来 suppression が効くのは
        # proposable_custom_individual のみで、情報レーンは却下記録の経路すら無く「毎回再提示」
        # されていた（#26 の対象外レーンで同型再発）。filter_suppressed は読み取りのみ（副作用なし）
        # なので dry-run でも適用してよい。書込（record_rejection）は下の reconcile / SKILL.md が担う。
        proposable_global_suppressed = 0
        rule_violation_suppressed = 0
        _rv_synthetics_surface = []
        try:
            from remediation.suppression_ledger import (
                filter_suppressed as _filter_suppressed,
                is_suppressed as _is_suppressed,
            )
            # (a) proposable_global: issue dict そのままで dedup 可能。
            _pg = _filter_suppressed(proposable_global, slug=_suppress_slug)
            proposable_global = _pg["surface"]
            proposable_global_suppressed = len(_pg["suppressed"])

            # (b) rule_violation_observed: issue 形を持たないため violated_command 単位の
            # 安定 identity へ変換して判定し、抑制済みは discover 出力から落とす（surface 元）。
            _rv_list = discover_data.get("rule_violation_observed", []) or []
            if _rv_list:
                from rule_violation_lane import rule_violation_suppression_issue
                _rv_kept = []
                for _v in _rv_list:
                    _syn = rule_violation_suppression_issue(_v)
                    if _is_suppressed(_syn, slug=_suppress_slug):
                        rule_violation_suppressed += 1
                    else:
                        _rv_kept.append(_v)
                        _rv_synthetics_surface.append(_syn)
                # discover_data は result["phases"]["discover"] と同一 dict（surface 元）。
                # 抑制済みを落とした list を書き戻すと SKILL.md の rule_violation_observed 表示が畳まれる。
                if "discover" in result["phases"]:
                    result["phases"]["discover"]["rule_violation_observed"] = _rv_kept
        except Exception:
            proposable_global_suppressed = 0
            rule_violation_suppressed = 0
            _rv_synthetics_surface = []

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
        # #103: proposable_global / rule_violation_observed の情報レーンも同じ safety net で
        # 追跡し、未対応のまま連続提示された advisory を閾値回数で自動畳み込みする（dismiss 入口が
        # 無くても「毎回再提示」を断つ）。reconcile_surfaced は marker を全置換するため、二重書込を
        # 避けるべく **1 回の呼び出しに全レーンの surface 対象を束ねて**渡す（dedup_key はレーンごとに
        # 相異なるため衝突しない）。読み取り→閾値判定は dry-run でも実行できるが、書込（marker /
        # ledger）は persist でゲートする（dry-run 非書込・pitfall_dryrun_stateful_store_write）。
        auto_rejected_count = 0
        try:
            from remediation.suppression_ledger import reconcile_surfaced as _reconcile
            _tracked = (
                list(_partition["individual"])
                + list(proposable_global)
                + list(_rv_synthetics_surface)
            )
            _recon = _reconcile(
                _tracked, slug=_suppress_slug, persist=not ctx.dry_run
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
            # #103: 情報レーンの dismiss 済み（TTL 内）を surface から畳んだ件数（silence != evaluated）。
            "proposable_global_suppressed": proposable_global_suppressed,
            "rule_violation_suppressed": rule_violation_suppressed,
            # #494: 連続再出で自動却下した件数（SKILL.md record_rejection の決定論 fallback）。
            # #103 以降は情報レーンの自動畳み込みも含む。
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
            reorganize_result = run_reorganize(ctx.project_dir)
        result["phases"]["reorganize"] = reorganize_result
    except Exception as e:
        result["phases"]["reorganize"] = {"error": str(e)}

    # Phase 4: Prune（dry-run 時は候補のみ）
    try:
        from prune import run_prune
        # Reorganize の merge_groups を Prune に渡す
        reorganize_data = result["phases"].get("reorganize", {})
        merge_groups = reorganize_data.get("merge_groups", []) if not reorganize_data.get("skipped") else []
        prune_result = run_prune(
            ctx.project_dir, reorganize_merge_groups=merge_groups, dry_run=ctx.dry_run
        )
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
    # #141-7a: 注入は audit フェーズが Markdown/TL;DR を確定した後に走るため、そのままだと
    # レンダリング済み phases.audit.report に反映されず TL;DR 件数（要対応 6）が top-level
    # observability の再集計（要対応 7）と食い違い、注入行が Markdown 本文に grep 0 件になる。
    # reconcile_injected_observability で Markdown 側の TL;DR を +1 し本文へ追記して一致させる。
    try:
        from evolve_reconcile import build_remediation_batch_skip_observability
        _bs_line = build_remediation_batch_skip_observability(result)
        if _bs_line is not None:
            obs = result.get("observability")
            if not isinstance(obs, dict) or "error" in obs:
                obs = {} if not isinstance(obs, dict) else obs
                result["observability"] = obs
            obs["remediation_batch_skip"] = _bs_line
            # Markdown レポート（phases.audit.report）と TL;DR を注入行に追随させる（#141-7a）。
            try:
                from audit.sections_summary import reconcile_injected_observability
                audit_phase = result.get("phases", {}).get("audit")
                if isinstance(audit_phase, dict) and isinstance(
                    audit_phase.get("report"), str
                ):
                    audit_phase["report"] = reconcile_injected_observability(
                        audit_phase["report"], "Remediation Batch Skip", _bs_line
                    )
            except Exception:
                pass
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
        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
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

        # #105: Step 2 の fitness 生成提案を fitness_evolution の structural 判定と整合させる。
        # custom skill を持たない PJ で「fitness を生成しろ」と「この PJ では fitness を使わない設計」を
        # 同時提示する矛盾を断つ。判定・note は _state.annotate_fitness_generation_advice に集約（単体テスト用）。
        import evolve as _evolve_mod3
        _evolve_mod3.annotate_fitness_generation_advice(
            result["phases"].get("fitness"), fitness_evo_result
        )
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
                rec = record_proposal(p, dry_run=ctx.dry_run)
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
