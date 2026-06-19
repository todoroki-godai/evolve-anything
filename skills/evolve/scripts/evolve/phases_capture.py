#!/usr/bin/env python3
"""キャプチャ／後段フェーズ群（block D）を run_evolve から抽出した module（#531 PR 8/8）。

trigger_summary 確定 → Phase 7 Self-Analysis → state 更新（dry_run gate）→ session ingest →
growth crystallization → utterance ingest → weak_signals batch/ttl → correction_semantic emit →
bootstrap_backlog → daily_review → idiom_autopromote → evolve_decisions emit → growth_report
までを `run_capture_phases(result, ctx)` に束ねる。振る舞いはゼロ変更で、run_evolve 本体の
ローカル/引数参照を `ctx.<field>`（EvolveContext）から取るよう置換しただけ（result を
in-place mutate するだけで返り値なし）。

⚠️ dry-run 書込ゲート（このブロック最大のリスク・#491/#513 契約）:
post-batch 群（weak_signals batch/ttl・correction_semantic・bootstrap_backlog・daily_review・
idiom_autopromote・evolve_decisions emit）と state 更新・各種 ingest は **`dry_run` を最下層
write まで貫通**して「dry-run では 1 バイトも書かない」契約を守っている。抽出では `dry_run` を
`ctx.dry_run` に置換するだけで、各関数への `dry_run=...` 引数渡しと `if not dry_run:` ガードは
一字一句保つ（壊すと dogfood Layer1 の dry-run SHA256 不変が落ちる）。

⚠️ 束縛フェンス（#531 §3）:
分割後に `setattr(evolve, "<name>", ...)` / `mock.patch.object(evolve, "<name>")` での差し替えが
本 module の名前空間ですり抜けると、テスト緑のまま実関数が走る silent fail になる。
本ブロックの grep（test_evolve_binding_paths / test_evolve_*）の結果、抽出範囲が呼ぶ
`evolve.<name>` 直接差し替え対象（setattr / patch.object）は **存在しない**
（_build_trigger_summary / load_evolve_state / save_evolve_state / _resolve_pj_slug /
_emit_growth_crystallization はいずれも setattr/patch.object 対象でなく、テストは
`from evolve import _resolve_pj_slug` 等の re-export 参照のみ）。よって monkeypatch されない
これら末端 helper は sub-module（_state / _env / _report）から直接 import する（PR#5/#6/#7 の流儀）。
self-mutation スロット（skill_evolve_assessment / collect_issues）の代入は本ブロックに無い。

各 Phase 内の `from evolve_introspect import ...` / `from session_store import ...` /
`from utterance_archive import ...` / `from weak_signals import ...` /
`from correction_semantic import ...` / `from evolve_decisions import ...` /
`from growth_report import ...` / `from trigger_engine import ...` 等の関数内 import は
現状通り関数内に残す（module 名前空間の汚染を避け既存挙動・sys.modules patch 互換を維持）。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

from ._env import _resolve_pj_slug
from ._report import _emit_growth_crystallization
from ._state import _build_trigger_summary, load_evolve_state, save_evolve_state


def run_capture_phases(result: Dict[str, Any], ctx) -> None:
    """キャプチャ／後段フェーズ（block D）を result に in-place で書き込む。

    Args:
        result: run_evolve が ctx.new_result() で作り、run_diagnose_phases /
            run_remediate_phases が各フェーズを書き込んだ result dict。in-place mutate する。
        ctx: EvolveContext（引数 + 初期化フェーズ共有ローカル）。
    """
    project_dir = ctx.project_dir
    dry_run = ctx.dry_run

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
