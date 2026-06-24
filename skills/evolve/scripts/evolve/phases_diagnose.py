#!/usr/bin/env python3
"""診断フェーズ群（Phase 1〜3.4）を run_evolve から抽出した module（#531 PR 6/8）。

Phase 1（Observe データ確認）〜 Phase 3.4（Skill Self-Evolution Assessment）までを
`run_diagnose_phases(result, ctx, observe_first=False)` に束ねる。振る舞いはゼロ変更で、
run_evolve 本体のローカル/引数参照を `ctx.<field>`（EvolveContext）から取るよう置換しただけ。

⚠️ 束縛フェンス（#531 §3）:
分割後に `setattr(evolve, "<name>", ...)` / `mock.patch.object(evolve, "<name>")` での差し替えが
本 module の名前空間ですり抜けると、テスト緑のまま実関数が走る silent fail になる。
monkeypatch 対象の名前は **必ず `import evolve as _ev; _ev.<name>(...)` のパッケージ namespace
経由**で呼ぶこと。本 module で `_ev.` 経由が必須なのは:
  - `check_data_sufficiency`（test_evolve_binding_paths）
  - `check_fitness_function`（test_evolve_binding_paths）
  - `_compute_env_score_struct`（test_evolve_env_score_wiring が patch.object + spy.called を assert）

self-mutation スロット（skill_evolve_assessment）は __init__（パッケージ evolve）に残し、
Phase 3.4 は `import evolve as _evolve_mod` 経由で参照・束縛する（現コードと同型）。

monkeypatch されない末端 helper（_warn_insufficient_data / _capture_audit_stderr /
_surface_constitutional_status）は sub-module から直接 import してよい（PR#5 の流儀）。
"""
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

from ._report import _warn_insufficient_data
from ._capture import _capture_audit_stderr
from ._env import _surface_constitutional_status


def _load_corrections(project_dir: Path) -> list:
    """当 PJ スコープの corrections レコードを in-memory で読む（#10 correction 軸の素）。

    corrections.jsonl は現状ほぼ空なので空リストになるのが通常（attribute_outcomes が
    graceful に correction_recurrence=None を返す）。読み取りのみで一切書かない（dry-run
    安全）。outcome_metrics の純ヘルパ（jsonl 読み / PJ slug 正規化）を再利用して
    worktree 安全に当 PJ 分のみへ絞る（重複実装回避）。解決不能環境では空リスト。
    """
    sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
    try:
        from audit import outcome_metrics as _om
    except ImportError:
        return []
    try:
        base = _om.DATA_DIR
        proj_slug = _om._normalize_pj(str(project_dir))
        return [
            r
            for r in _om._read_jsonl(base / "corrections.jsonl")
            if _om._project_match(r, proj_slug)
        ]
    except Exception:
        return []


def run_diagnose_phases(result: Dict[str, Any], ctx, observe_first: bool = False) -> None:
    """診断フェーズ（Phase 1〜3.4）を result に in-place で書き込む。

    Args:
        result: run_evolve が ctx.new_result() で作った result dict。in-place mutate する。
        ctx: EvolveContext（引数 + 初期化フェーズ共有ローカル）。
        observe_first: True の場合、Phase 1.6 で observe + fitness ゲートだけ算出して
            重いフェーズを回さず early-return する（#407）。run_evolve 側は
            `result.get("skipped_heavy_phases")` を見て本体を打ち切る。
    """
    # #531 束縛フェンス: monkeypatch (setattr(evolve, ...)) と本体 self-mutation が効く束縛先を
    # パッケージ evolve（__init__）に集約する。本 module へ抽出後も差し替え対象 helper を
    # evolve.<name> 経由で呼べば、名前解決すり抜け（test 緑のまま実関数が走る silent fail）を
    # 構造的に防げる。module-level の `import evolve` は循環 import になるため関数内で行う。
    import evolve as _ev

    # Phase 1: Observe データ確認
    sufficiency = _ev.check_data_sufficiency()
    result["phases"]["observe"] = sufficiency

    if not sufficiency["sufficient"]:
        if sufficiency.get("backfill_recommended"):
            # テレメトリ未取得 = 初回導入直後。backfill を先に実行するよう提案する
            # （自動実行はせず、副作用が大きいためユーザー判断に委ねる）。
            result["phases"]["observe"]["action"] = "backfill_recommended"
        else:
            # スキップ推奨だがユーザー選択に委ねる
            result["phases"]["observe"]["action"] = "skip_recommended"
        _warn_insufficient_data(sufficiency)
    elif sufficiency.get("no_new_observations"):
        # データは十分だが前回 evolve 以降の新規観測がゼロ（#396）。フル実行は
        # no-op になりやすいので軽量モードを提案する（SKILL.md が surface）。
        # 自動スキップはしない — べき等性は保ちつつユーザーに選択させる。
        result["phases"]["observe"]["action"] = "lightweight_recommended"

    # Phase 1.5: Fitness 関数チェック
    fitness_check = _ev.check_fitness_function(ctx.project_dir)
    result["phases"]["fitness"] = fitness_check

    # Phase 1.6: observe-first pre-flight early-return（#407）
    # observe（新規観測の有無）と fitness はどちらもファイル走査だけで安価に算出できる。
    # observe_first 時はここで打ち切り、重いフェーズ（discover/audit/skill_evolve/
    # remediation/reorganize/prune…）を回さずに action だけ返す。SKILL Step 1 が action を
    # 見て「軽量/スキップ/フル」を選び、フルが必要なときだけ重い dry-run を別途走らせる。
    # これで lightweight_recommended の判定が「フル分析コストを払う前」に効く。
    if observe_first:
        result["observe_first"] = True
        result["skipped_heavy_phases"] = True
        return

    # Phase 2: Discover
    try:
        from discover import run_discover
        project_root = Path(ctx.project_dir) if ctx.project_dir else None
        discover_result = run_discover(project_root=project_root, tool_usage=True)
        result["phases"]["discover"] = discover_result
    except Exception as e:
        # traceback を捨てると root cause が永久に観測不能になり result が緑に見える（#521）。
        # self_analysis が後で参照できるよう traceback を残す。
        # discover が全クラッシュすると reflect_data_count キー自体が欠落し、下流が
        # None で比較して `None < 0` 二次クラッシュする（#32）。degraded 表現を1つに
        # 正準化するため、全クラッシュ経路でも degraded sentinel -1（int）を必ずセット
        # する（runner.py の部分失敗フォールバックと同じ契約に揃える・#526-3）。
        result["phases"]["discover"] = {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "reflect_data_count": -1,
        }

    # Phase 2.5: Enrich（discover に統合済み — discover 出力から取得）
    discover_data = result["phases"].get("discover", {})
    result["phases"]["enrich"] = {
        "enrichments": discover_data.get("matched_skills", []),
        "unmatched_patterns": discover_data.get("unmatched_patterns", []),
        "total_enrichments": len(discover_data.get("matched_skills", [])),
        "total_unmatched": len(discover_data.get("unmatched_patterns", [])),
        "skipped_reason": "no_patterns_available" if not discover_data.get("matched_skills") and not discover_data.get("unmatched_patterns") else None,
    }

    # Phase 2.6: Skill Triage（trigger eval + CREATE/UPDATE/SPLIT/MERGE/OK 判定）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from skill_triage import triage_all_skills
        from telemetry_query import query_sessions, query_usage
        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        sessions = query_sessions(project=proj.name)
        usage_data = query_usage(project=proj.name)
        missed = discover_data.get("missed_skill_opportunities", [])
        triage_result = triage_all_skills(
            sessions=sessions,
            usage=usage_data,
            missed_skills=missed,
            project_root=proj,
            dry_run=ctx.dry_run,  # #308: --dry-run 時は triage_ledger に書き込まない
        )
        # #433 先行スコープ: outcome 軸をスキル単位に分解し triage 候補の順位に自動入力
        # （advisory→閉ループ配線）。in-memory の sessions/usage_data を渡すので triage 順位
        # 計算では DATA_DIR を書かない（dry-run 安全）。
        #   #10: correction 再発率を3軸目に追加（corrections.jsonl 由来・現状ほぼ空なので
        #        graceful None）＋ negative_transfer 検出スキルの昇格抑制 gate。
        #   #28: reward 分散（RODS 学習余地）列を advisory に付与。
        from audit.outcome_attribution import apply_outcome_ranking
        from audit.usage import compute_negative_transfer
        try:
            neg_transfer = compute_negative_transfer(usage_data)
        except Exception:
            neg_transfer = []
        corrections_data = _load_corrections(proj)
        #   #64 MAA: バッチ跨ぎ符号付き EMA を読み advisory 列を付与（read のみ＝dry-run 安全・
        #            順位は変えない）。書込は evolve --drain の apply 境界で行う。
        try:
            from pj_slug import resolve_pj_slug
            from audit.reward_ema import read_reward_ema
            _ema_map = read_reward_ema(resolve_pj_slug(proj))
        except Exception:
            _ema_map = {}
        triage_result = apply_outcome_ranking(
            triage_result,
            usage=usage_data,
            sessions=sessions,
            corrections=corrections_data,
            negative_transfer=neg_transfer,
            reward_ema=_ema_map,
        )
        result["phases"]["skill_triage"] = triage_result
    except Exception as e:
        result["phases"]["skill_triage"] = {"error": str(e), "skipped": True}

    # Phase 2.65: Skill Quality Pattern Detection（テレメトリ不要）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from instruction_patterns import detect_patterns, check_defaults_first, analyze_context_efficiency
        from quality_engine import recommend_patterns

        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        claude_md_path = proj / "CLAUDE.md"
        claude_md_content = claude_md_path.read_text(encoding="utf-8") if claude_md_path.is_file() else None

        quality_results = {}
        skills_dir = proj / ".claude" / "skills"
        if skills_dir.is_dir():
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                content = skill_md.read_text(encoding="utf-8")
                patterns = detect_patterns(content)
                defaults = check_defaults_first(content)
                ctx_eff = analyze_context_efficiency(content, claude_md_content)
                recommendation = recommend_patterns(patterns, content)
                quality_results[skill_dir.name] = {
                    "patterns": patterns,
                    "defaults_first_score": defaults,
                    "context_efficiency": ctx_eff,
                    "recommendation": recommendation,
                }
        result["phases"]["quality_patterns"] = quality_results
    except Exception as e:
        result["phases"]["quality_patterns"] = {"error": str(e)}

    # Phase 2.7: Layer Diagnose（全レイヤー診断）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from layer_diagnose import diagnose_all_layers
        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        layer_result = diagnose_all_layers(proj)
        result["phases"]["layer_diagnose"] = layer_result
    except Exception as e:
        result["phases"]["layer_diagnose"] = {"error": str(e)}

    # Phase 3: Audit
    try:
        from audit import run_audit
        # MemTrace(#264) と constitutional(slop_detector #255 を 10% ブレンド) は opt-in だが、
        # evolve では既定で有効化し「evolve するだけで全機能が効く」状態にする。
        # MemTrace は決定論(LLM ゼロ)、constitutional は haiku×最大4 だがレイヤ単位キャッシュで
        # 通常 0〜1 コール（constitutional_cache.json）。
        # audit 実行中の stderr（Chaos/Constitutional スキップ等）を self_analysis に
        # 渡せるよう捕捉する（#523-1）。Python warnings ではないため _capture_warnings
        # では拾えない経路。
        with _capture_audit_stderr(ctx.warning_sink):
            audit_report = run_audit(
                ctx.project_dir, memory_trace=True, constitutional_score=True, dry_run=ctx.dry_run
            )
        result["phases"]["audit"] = {"report": audit_report}
    except Exception as e:
        result["phases"]["audit"] = {"error": str(e)}

    # 構造化 env_score の surface（#523-2/#526-2）: run_audit は markdown レポート文字列
    # だけを返し構造化 env_score を捨てるため、SKILL.md / references/report-narration.md が
    # 読むトップレベル `result["env_score"]` が常に欠落し成長レベル演出が一度も発火しなかった。
    # 同じ権威ソース（compute_environment_fitness）から取り直して compute_level まで解決する。
    # 算出失敗時も黙らず degraded=True を置く（silence != evaluated の自己適用）。
    # #531 束縛フェンス: test_evolve_env_score_wiring が patch.object(evolve, ...) + spy.called を
    # assert するため _ev. 経由で呼ぶ（直接 import 束縛は patch をすり抜ける）。
    result["env_score"] = _ev._compute_env_score_struct(ctx.project_dir, dry_run=ctx.dry_run)

    # Observability contract（#272 後続）: audit の 217KB markdown に埋もれて surface されない
    # observability 行（unmanaged_pitfalls / glossary_drift …）を構造化フィールドに昇格させ、
    # assistant が必ずサマリに出せるようにする。silence != evaluated 原則を契約として明文化。
    try:
        from audit import collect_observability

        _obs_proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        result["observability"] = collect_observability(_obs_proj)
    except Exception as e:
        result["observability"] = {"error": str(e)}

    # #528-4: observability.skill_triage（案内行のみ）に triage の実件数 findings を注入する。
    # collect_observability は triage を再実行しない設計で件数を持てないが、evolve は
    # Phase 2.6 で算出済みの triage_result を `result["phases"]["skill_triage"]` に持つ。
    # findings レーンに実データ（CREATE/UPDATE/SPLIT/MERGE 件数）が無いのは contract 違反
    # だったため、ここで件数行を追記する（silence != evaluated）。
    try:
        from audit.sections_triage import build_skill_triage_counts_lines

        _obs = result.get("observability")
        if isinstance(_obs, dict) and isinstance(_obs.get("skill_triage"), list):
            _count_lines = build_skill_triage_counts_lines(
                result["phases"].get("skill_triage")
            )
            if _count_lines:
                _obs["skill_triage"] = _obs["skill_triage"] + _count_lines
    except Exception:
        pass

    # Phase 3.2: Constitutional cache 状態の surface（#408-D）
    _surface_constitutional_status(
        Path(ctx.project_dir) if ctx.project_dir else Path.cwd(),
        ctx.warning_sink,
        result.get("observability"),
    )

    # Phase 3.3: Skill Quality Trace Analysis（テレメトリ依存 — data_sufficiency 後）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from quality_engine import analyze_traces, compute_overall_score, record_quality_score

        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        quality_patterns = result["phases"].get("quality_patterns", {})
        trace_results = {}
        for skill_name, qr in quality_patterns.items():
            if isinstance(qr, dict) and "patterns" in qr:
                trace = analyze_traces(skill_name, project=proj.name)
                pattern_score = qr["patterns"].get("score", 0.0)
                confusion = trace.get("confusion_score") if trace else None
                ctx_eff = qr.get("context_efficiency", {}).get("efficiency_score", 0.5)
                defaults = qr.get("defaults_first_score", 1.0)
                overall = compute_overall_score(pattern_score, confusion, ctx_eff, defaults)
                trace_results[skill_name] = {
                    "confusion_score": confusion,
                    "overall_score": overall,
                }
                if not ctx.dry_run:
                    record_quality_score(skill_name, {
                        "pattern_score": pattern_score,
                        "confusion_score": confusion,
                        "context_efficiency": ctx_eff,
                        "defaults_first_score": defaults,
                        "overall": overall,
                    })
        result["phases"]["quality_traces"] = trace_results
    except Exception as e:
        result["phases"]["quality_traces"] = {"error": str(e)}

    # Phase 3.4: Skill Self-Evolution Assessment（適性判定 — remediation の前に実行）
    try:
        import evolve as _evolve_mod
        if _evolve_mod.skill_evolve_assessment is None:
            sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
            from skill_evolve import skill_evolve_assessment as _sea
            _evolve_mod.skill_evolve_assessment = _sea
        skill_evolve_assessment = _evolve_mod.skill_evolve_assessment
        proj = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        se_assessment = skill_evolve_assessment(
            proj, project=proj.name,
            skip_skills=ctx.skip_skills,
            skip_llm_evolve=ctx.skip_llm_evolve,
            confirmed_batch=ctx.confirmed_batch,
        )
        # _meta エントリを分離
        _excluded_meta = next((a for a in se_assessment if a.get("_meta") == "excluded_globals"), {})
        _batch_guard = next((a for a in se_assessment if a.get("_meta") == "batch_guard_trigger"), None)
        _assessments = [a for a in se_assessment if not a.get("_meta")]
        result["phases"]["skill_evolve"] = {
            "assessments": _assessments,
            "total_skills": len(_assessments),
            "already_evolved": sum(1 for a in _assessments if a.get("already_evolved")),
            "high_suitability": sum(1 for a in _assessments if a.get("suitability") == "high"),
            "medium_suitability": sum(1 for a in _assessments if a.get("suitability") == "medium"),
            "insufficient_usage": sum(1 for a in _assessments if a.get("suitability") == "insufficient_usage"),
            "rejected": sum(1 for a in _assessments if a.get("suitability") == "rejected"),
            "excluded_global_count": _excluded_meta.get("excluded_global_count", 0),
            "excluded_global_hint": _excluded_meta.get("hint", ""),
            "batch_guard_trigger": _batch_guard,
        }
    except Exception as e:
        result["phases"]["skill_evolve"] = {"error": str(e)}
