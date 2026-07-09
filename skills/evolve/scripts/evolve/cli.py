#!/usr/bin/env python3
"""CLI エントリポイント（main / _summarize_result）を run_evolve から抽出した module（#531 PR 8/8）。

`main()`（argparse + print-out-path / drain / output 各モード）と stdout 1 行サマリ
`_summarize_result()` を束ねる。振る舞いはゼロ変更。

⚠️ 束縛フェンス（#531 §3）:
`main()` から差し替え対象（`run_evolve` / `_resolve_evolve_slug`）を呼ぶときは
`import evolve as _ev; _ev.<name>(...)` のパッケージ namespace 経由にする。main を本 module へ
抽出後も `setattr(evolve, "run_evolve", X)` / `setattr(evolve, "_resolve_evolve_slug", X)`
（test_evolve_binding_paths）が main()→呼び出しに効き続ける。

`--drain` 内の `_resolve_pj_slug` は setattr / patch.object 対象でない（test は
`from evolve import _resolve_pj_slug` の re-export 参照のみ）ため、sub-module（_env）から
直接 import する（PR#5/#6/#7 の流儀）。

`_summarize_result` は test_evolve_output_flag が `evolve._summarize_result(...)` で直接呼ぶため
__init__ で re-export する。各モード内の `from evolve_decisions import drain_pending` /
`from weak_signals import batch` 等の関数内 import は現状通り関数内に残す。
"""
import json
import sys
from pathlib import Path

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT

from ._env import _resolve_pj_slug, build_reconcile_tracked


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
            "apply 後の SKILL.md Step 7.8 で `evolve --drain` を1コマンド実行する。"
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
            "（evolve は既に slug を解決できるため）。"
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
        # 標準フローは `evolve --dry-run` 分析 → 対話適用なので、run_evolve 内の
        # run_batch(dry_run=True) は #491 契約で常にゼロ書き込みになる。決定論検出は冪等
        # （signal_key dedup）なので、tool 文脈・非 dry-run・正準 DATA_DIR で走る drain で
        # 永続化する（evolve_decisions の drain と同型・#400 の盲点修正と同じ構造）。
        try:
            from weak_signals import batch as _ws_batch

            _ws_slug = _resolve_pj_slug(args.project_dir)
            summary["weak_signals_persisted"] = _ws_batch.persist_weak_signals_drain(_ws_slug)
        except Exception as e:
            summary["weak_signals_persisted"] = {"error": str(e)}

        # #64 MAA: バッチ跨ぎ符号付き advantage の EMA を apply 境界で永続化する
        # （weak_signals #484 と同型・非 dry-run・正準 DATA_DIR）。plant-the-seed 型。
        try:
            from audit.reward_ema import persist_reward_ema_batch

            _ema_slug = _resolve_pj_slug(args.project_dir)
            # --project-dir 既定は None。reward_ema は project_dir を直接 Path() に渡す
            # （load_usage_data の project_root）ため、None だと Path(None) で落ちる。
            # weak_signals / queue_state は slug だけ使うので None を吸収するが、ここは
            # cwd にフォールバックする（line 92 の非 drain パスと同じ idiom・#64 drain 盲点）。
            summary["reward_ema_persisted"] = persist_reward_ema_batch(
                args.project_dir or str(Path.cwd()), slug=_ema_slug
            )
        except Exception as e:
            summary["reward_ema_persisted"] = {"error": str(e)}

        # #79: fleet queue の per-PJ last_evolve state を apply 境界で更新する
        # （reward_ema #64 / weak_signals #484 と同型・非 dry-run・正準 DATA_DIR）。
        # 次回 fleet queue が「前回 evolve 以降」を PJ 別に測れるようにする。
        try:
            from fleet.queue_state import persist_last_evolve

            _q_slug = _resolve_pj_slug(args.project_dir)
            summary["queue_state_persisted"] = persist_last_evolve(_q_slug)
        except Exception as e:
            summary["queue_state_persisted"] = {"error": str(e)}

        # #135: subagent 内部軌跡（subagents.jsonl → subagent_traces.jsonl）の増分 ingest を
        # apply 境界で実効化する。根因: run_evolve(dry_run=False) に到達する標準経路が存在
        # せず、phases_capture の `if not dry_run:` 配下（subagent_traces ingest）が構造的
        # 死蔵だった＝代替経路ゼロで全PJ横断 2026-06-23 以降ゼロ成長（唯一の実害）。
        # weak_signals #484 / reward_ema #64 / queue_state #79 と同型に drain 境界へ移植する。
        # 既存セマンティクス（max_new cap・agent_transcript_path に名指しされた本のみ読む・
        # 決定論ゼロ LLM）は ingest_all_projects 側で維持され、ここでは呼ぶだけ。
        try:
            from subagent_traces import ingest as _st_ingest

            _st_res = _st_ingest.ingest_all_projects(progress=False)
            summary["subagent_traces_ingest"] = {
                "ingested": _st_res.get("ingested", 0),
                "skipped": _st_res.get("skipped", 0),
                "capped": _st_res.get("capped", False),
                "remaining": _st_res.get("remaining", 0),
            }
        except Exception as e:
            summary["subagent_traces_ingest"] = {"error": str(e)}

        # #135/#136: last_run_timestamp を apply 境界で前進させる。死蔵で永久未書込だった
        # ため fleet queue / count_new_* / trigger の「前回 evolve 以降」時間フィルタが
        # 進まず #136 の直接原因になっていた。drain は observe を回さず sessions/observations
        # カウントを持たないので、読み手のいない informational snapshot は触らず時間フィルタが
        # 依存する last_run_timestamp のみ前進させる（persist_last_run_timestamp が state の
        # 他キーを保つ・#135）。他 persist と同じく error は握り潰して drain 本体を完走する。
        try:
            from ._state import persist_last_run_timestamp

            summary["last_run_persisted"] = persist_last_run_timestamp()
        except Exception as e:
            summary["last_run_persisted"] = {"error": str(e)}

        # #150 (#415 Phase A): sessions.jsonl → sessions.db の batch ingest を apply 境界で
        # 実効化する。根因: run_evolve(dry_run=False) に到達する標準経路が無く、phases_capture の
        # `if not dry_run:` 配下（session_store.ingest）が構造死蔵で sessions.db が stale・
        # sessions.jsonl が単調肥大していた。weak_signals #484 / subagent_traces #135 と同型に
        # drain 境界へ移植する。session_store は call-time に DATA_DIR を解決する
        # （env/marker ベース）ので slug/project_dir を渡さない＝Path(None) の懸念もない。
        # phases_capture 側の既存ブロックは run_evolve(dry_run=False) 直接実行時の互換で残す
        # （ingest は (session_id, timestamp) dedup で冪等なので二重実行は無害）。
        try:
            import session_store

            summary["sessions_ingested"] = session_store.ingest()
        except Exception as e:
            summary["sessions_ingested"] = {"error": str(e)}

        # #150: evolve 実行完了によるスヌーズ自動解除を apply 境界で実効化する。
        # apply 境界＝drain 時点が「evolve を回した」意味論と一致する（標準フローは
        # dry-run 分析 → 対話適用 → drain なので phases_capture の clear_snooze は通らない）。
        # 既存項目と同型に error を surface（無音握り潰しで新しい死蔵を作らない）。
        try:
            from trigger_engine import clear_snooze

            clear_snooze()
            summary["snooze_cleared"] = True
        except Exception as e:
            summary["snooze_cleared"] = {"error": str(e)}

        # #146 (ADR-051): result 依存3項目（calibration state / tool_usage_snapshot /
        # growth crystallization）を apply 境界で発火する。上の result 非依存 persist 群
        # （#150 で移植）と違い、これらは run_evolve が result に書いた phases 値を必要とする。
        # dry-run が `--output "$OUT"` で書いた full result JSON を drain が読み、値を運搬して
        # 確定する（emit→drain 2相の「値運搬」版）。標準フロー（dry-run→drain）は
        # run_evolve(dry_run=False) に到達せず phases_capture の該当ブロックが死蔵する #146 の根治。
        # graceful degradation: --result-json 無し / 読めない / phases 欠落 → 3項目のみ skip し
        # 他 persist は継続（silence≠evaluated を summary に surface）。時刻は drain 時刻。
        _evolve_result = None
        _result_skip_reason = None
        if args.result_json:
            try:
                _rj_path = Path(args.result_json)
                if _rj_path.exists():
                    _loaded = json.loads(_rj_path.read_text(encoding="utf-8"))
                    if isinstance(_loaded, dict):
                        _evolve_result = _loaded
                    else:
                        _result_skip_reason = "result_json_not_dict"
                else:
                    _result_skip_reason = "result_json_not_found"
            except Exception as e:
                _result_skip_reason = f"result_json_unreadable: {e}"
        else:
            _result_skip_reason = "no_result_json"

        # calibration state + tool_usage_snapshot（result 依存・グローバル state 確定）。
        try:
            if _evolve_result is not None:
                from ._state import persist_result_dependent_state

                summary["result_state_persisted"] = persist_result_dependent_state(
                    _evolve_result
                )
            else:
                summary["result_state_persisted"] = {"skipped": _result_skip_reason}
        except Exception as e:
            summary["result_state_persisted"] = {"error": str(e)}

        # growth crystallization emit（result 依存・journal 記録）。
        try:
            if _evolve_result is not None:
                from ._report import _emit_growth_crystallization

                _emit_growth_crystallization(_evolve_result, args.project_dir)
                summary["growth_crystallized"] = True
            else:
                summary["growth_crystallized"] = {"skipped": _result_skip_reason}
        except Exception as e:
            summary["growth_crystallized"] = {"error": str(e)}

        # #186: remediation reconcile_surfaced の連続提示 count marker を apply 境界で永続化する。
        # #494 の「毎回再提示を断つ」自動却下セーフティネットは phases_remediate の
        # persist=not ctx.dry_run 経由でしか呼ばれず、標準フロー（evolve --dry-run のみ）では
        # 常に persist=False → marker（remediation_surfaced/<slug>.json）が永久未書込で閾値
        # DEFAULT_AUTO_REJECT_AFTER_RUNS に届かず全 PJ で死蔵していた。weak_signals #484 /
        # reward_ema #64 / subagent_traces #135 と同型に、count marker の実書込 + 閾値到達時の
        # record_rejection を drain（非 dry-run・正準 DATA_DIR）へ移設する。_tracked は
        # build_reconcile_tracked で phases 側と同一構成に再構築する（result 由来・#186）。
        # slug は phases_remediate / SKILL.md inline record_rejection と同じ
        # remediation.suppression_ledger.resolve_slug（git-common-dir 親）で解決し read/write を一致させる。
        # graceful degradation: --result-json 無し/不読/phases 欠落 → skip して他 persist は継続。
        try:
            if _evolve_result is not None:
                from remediation.suppression_ledger import (
                    reconcile_surfaced as _reconcile_surfaced,
                    resolve_slug as _rem_resolve_slug,
                )

                _rem_proj = Path(args.project_dir) if args.project_dir else Path.cwd()
                _rem_slug = _rem_resolve_slug(cwd=_rem_proj)
                _phases = _evolve_result.get("phases", {}) or {}
                _classified = (_phases.get("remediation", {}) or {}).get("classified", {}) or {}
                _rv_observed = (_phases.get("discover", {}) or {}).get(
                    "rule_violation_observed", []
                ) or []
                _tracked = build_reconcile_tracked(_classified, _rv_observed)
                _recon = _reconcile_surfaced(_tracked, slug=_rem_slug, persist=True)
                summary["remediation_surfaced_persisted"] = {
                    "tracked": _recon.get("tracked", 0),
                    "auto_rejected": _recon.get("auto_rejected", 0),
                    "resolved": _recon.get("resolved", 0),
                }
            else:
                summary["remediation_surfaced_persisted"] = {"skipped": _result_skip_reason}
        except Exception as e:
            summary["remediation_surfaced_persisted"] = {"error": str(e)}

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
