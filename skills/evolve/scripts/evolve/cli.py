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


def _known_pending_ids(project_dir, result_json):
    """現在 pending の提案 id 集合を返す（read-only。#444 の未知 ID 検査専用）。

    `drain_pending` と同じソース優先順位（result_json > marker）を使うが、ロックを取らず
    marker/queue を一切変更しない。実際の drain 実行までの間に marker が変化していれば、
    その時点の判定（deferred 化等）は `drain_pending` 側の通常ロジックに従う。

    marker 経路の母集団は `drain_pending` が実際に ingest 対象にする集合と**一致させる**
    （#450 codex cold review [Must]3）。`drain_pending`（`_drain.py`）は marker 経路でのみ
    orphan worktree（既に消えた worktree に属する pending）を `_partition_orphaned` で
    ingest 対象から除外する。ここで同じ除外をしないと、orphan の ID へ明示 reject を渡した
    ときに「検証は通過するが実際には記録されず marker だけ削除されて終わる」サイレント消失が
    起きる。除外条件は `evolve_decisions._partition_orphaned`（`is_orphaned_worktree` が単一
    ソース）を呼ぶだけにし、CLI 側で判定ロジックを再実装しない。result_json 経路は
    `drain_pending` 自身も orphan 判定をしない（result_json はその場で渡された snapshot で
    marker のような永続 residue ではないため）ので、ここでも除外しない。
    """
    if result_json:
        data = json.loads(Path(result_json).read_text(encoding="utf-8"))
        pending = (data.get("evolve_decisions") or {}).get("pending") or []
    else:
        import evolve_decisions as _ed

        slug = _ed.resolve_slug(Path(project_dir) if project_dir else None)
        marker = _ed.read_pending_marker(slug)
        all_pending = (marker.get("pending") if marker else None) or []
        pending, _orphaned = _ed._partition_orphaned(all_pending)
    return {entry.get("id") for entry in pending if entry.get("id")}


def _validate_decision_args(accepted_arg, rejected_arg, *, project_dir, result_json):
    """`--accepted`/`--rejected` を検証し `(accepted_ids, rejected_map, errors)` を返す（#444）。

    エラーがあれば呼び出し側は `drain_pending` を呼ばずに中断する（部分書込を防ぐ）。
    検証内容（issue #444 設計要件3）:
      - `--accepted` 内の重複 ID
      - `--rejected` 内の重複 ID
      - `--accepted` と `--rejected` の両方に同じ ID が指定された場合
      - 理由が空/空白のみの `--rejected`
      - 現在 pending に存在しない未知 ID（`--accepted`/`--rejected` 双方）

    `--accepted`/`--rejected` のどちらも未指定なら検証も pending 解決も一切行わず
    `(None, None, [])` を返す（既存の decision 引数無し呼び出しと完全同一の挙動を保つ）。
    """
    errors = []

    accepted_list = list(accepted_arg) if accepted_arg else []
    accepted_dupes = sorted({pid for pid in accepted_list if accepted_list.count(pid) > 1})
    if accepted_dupes:
        errors.append(f"--accepted に重複 ID があります: {accepted_dupes}")
    accepted_ids = set(accepted_list)

    rejected_pairs = rejected_arg or []
    rejected_map = {}
    rejected_dupes = set()
    for pid, reason in rejected_pairs:
        if not reason or not reason.strip():
            errors.append(f"--rejected {pid} の理由が空です（reason は必須）")
            continue
        if pid in rejected_map:
            rejected_dupes.add(pid)
            continue
        rejected_map[pid] = reason
    if rejected_dupes:
        errors.append(f"--rejected に重複 ID があります: {sorted(rejected_dupes)}")

    overlap = accepted_ids & set(rejected_map)
    if overlap:
        errors.append(f"--accepted と --rejected の両方に指定された ID があります: {sorted(overlap)}")

    if not errors and (accepted_ids or rejected_map):
        try:
            known_ids = _known_pending_ids(project_dir, result_json)
        except Exception as e:
            errors.append(f"pending 提案の解決に失敗しました: {e}")
            known_ids = None
        if known_ids is not None:
            unknown = sorted((accepted_ids | set(rejected_map)) - known_ids)
            if unknown:
                errors.append(f"pending に存在しない未知 ID が指定されました: {unknown}")

    if errors:
        return None, None, errors
    return (accepted_ids or None), (rejected_map or None), []


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
        "--accepted",
        nargs="+",
        default=None,
        metavar="ID",
        help=(
            "--drain 時に明示 accept する提案 ID の複数指定（空白区切りで複数可、例: "
            "--accepted ID1 ID2）。直前の対話（Step 3 の承認）で確定した proposal ID を渡す。"
            "重複指定・未知 ID は拒否する。既存の genetic-prompt-optimizer --accept"
            "（直近結果を丸ごと受理する単数フラグ・別コマンド）とは別物 (#444)。"
        ),
    )
    parser.add_argument(
        "--rejected",
        nargs=2,
        action="append",
        default=None,
        metavar=("ID", "REASON"),
        help=(
            "--drain 時に明示 reject する提案 ID の複数指定（ID と理由のペア。複数指定は "
            "--rejected ID1 REASON1 --rejected ID2 REASON2 のように繰り返す）。理由は必須で、"
            "空/空白のみは拒否する。既存の genetic-prompt-optimizer --reject"
            "（直近結果を丸ごと却下する単数フラグ・別コマンド）とは別物 (#444)。"
        ),
    )
    parser.add_argument(
        "--correction-responses",
        default=None,
        help=(
            "--drain 時に correction_semantic Phase C（ingest_judgement_results）へ渡す"
            "{request_id: 生テキスト} JSON ファイル（#339）。SKILL.md の Step 6.6 で Phase A→B"
            "（emit→インライン Haiku 判定）を行った後、responses をこのファイルへ書いて渡す。"
            "未指定なら Phase C は実行しない（Phase B は本質的に対話的なので --drain 単体では"
            "判定できない）。"
        ),
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

    # #450 codex cold review [Should]1: --drain 無しで --accepted/--rejected を受理すると、
    # 通常の evolve（drain を経由しない）が走って明示 decision が黙って捨てられる。
    # --accepted/--rejected は --drain 必須とし、無ければ他の判定より先にエラーで中断する。
    if (args.accepted or args.rejected) and not args.drain:
        print(json.dumps(
            {
                "error": "invalid_decision_args",
                "details": ["--accepted/--rejected は --drain と併用必須です（--drain 無しでは判断が記録されません）"],
            },
            ensure_ascii=False,
        ))
        sys.exit(1)

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

        # #444: accepted/rejected ID は直前の対話結果からここへ明示的に渡す。検証に失敗したら
        # drain_pending を一切呼ばず中断する（重複指定・未知 ID・理由なし reject の部分書込防止）。
        accepted_ids, rejected_map, decision_errors = _validate_decision_args(
            args.accepted, args.rejected,
            project_dir=args.project_dir, result_json=args.result_json,
        )
        if decision_errors:
            print(json.dumps(
                {"error": "invalid_decision_args", "details": decision_errors},
                ensure_ascii=False,
            ))
            sys.exit(1)

        summary = drain_pending(
            project_dir=args.project_dir, result_json=args.result_json,
            accepted=accepted_ids, rejected=rejected_map,
        )

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

        # #339: correction_semantic の Phase C（ingest_judgement_results）を apply 境界で
        # 実効化する。Phase A（emit・決定論・LLM 非呼出）は phases_capture が run_evolve 内で
        # 常時走らせるが、Phase B（Haiku 判定）は本質的に対話的（assistant がインラインで
        # 応答を生成する）ため、非対話の --drain 単体では実行できない。SKILL.md 側の新 Step
        # （references/correction-semantic-drain.md）が Phase A→B を行い、生成した
        # responses（{request_id: 生テキスト}）を JSON ファイルへ書き出し、このオプションで
        # 渡す。emitted は Phase A 実行時と同じ入力（utterances.db・judged 進捗）が変化して
        # いない前提で drain 側が emit_judgement_requests を再実行して再構成する（決定論・
        # weak_signals #484 と同じ「apply 境界で確定させる」設計）。未指定/未読/不正 JSON は
        # graceful skip で他 persist を継続する（result-json #146 と同型の skip 理由 surface）。
        if args.correction_responses:
            _cr_responses = None
            _cr_skip_reason = None
            try:
                _cr_path = Path(args.correction_responses)
                if _cr_path.exists():
                    _cr_loaded = json.loads(_cr_path.read_text(encoding="utf-8"))
                    if isinstance(_cr_loaded, dict):
                        _cr_responses = _cr_loaded
                    else:
                        _cr_skip_reason = "correction_responses_not_dict"
                else:
                    _cr_skip_reason = "correction_responses_not_found"
            except Exception as e:
                _cr_skip_reason = f"correction_responses_unreadable: {e}"

            if _cr_responses is not None:
                try:
                    from correction_semantic import batch as _cs_batch

                    _cs_slug = _resolve_pj_slug(args.project_dir)
                    _cs_emitted = _cs_batch.emit_judgement_requests(_cs_slug)
                    summary["correction_semantic_persisted"] = _cs_batch.ingest_judgement_results(
                        _cs_emitted, _cr_responses, dry_run=False
                    )
                except Exception as e:
                    summary["correction_semantic_persisted"] = {"error": str(e)}
            else:
                summary["correction_semantic_persisted"] = {"skipped": _cr_skip_reason}
        else:
            summary["correction_semantic_persisted"] = {"skipped": "no_correction_responses"}

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

        # #146 (ADR-051): result 依存2項目（calibration state / tool_usage_snapshot）を
        # apply 境界で発火する。上の result 非依存 persist 群（#150 で移植）と違い、これらは
        # run_evolve が result に書いた phases 値を必要とする。dry-run が `--output "$OUT"` で
        # 書いた full result JSON を drain が読み、値を運搬して確定する（emit→drain 2相の
        # 「値運搬」版）。標準フロー（dry-run→drain）は run_evolve(dry_run=False) に到達せず
        # phases_capture の該当ブロックが死蔵する #146 の根治。
        # graceful degradation: --result-json 無し / 読めない / phases 欠落 → skip し
        # 他 persist は継続（silence≠evaluated を summary に surface）。時刻は drain 時刻。
        # #379 Step 4: growth crystallization（journal 記録）は growth-journal harness
        # 削除に伴い本 apply 境界から削除した（元は3項目だった）。
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
    # #287-5: pending marker の書込失敗を 1 行サマリにも出す。標準フロー（dry-run 分析 →
    # 対話適用 → drain）では marker が pending の唯一の情報源なので、無音で失敗すると
    # 判断がまるごと失われる。emit は落とさない設計なので、ここで surface しないと
    # ユーザーは「提案は出たのに drain で何も記録されない」を後から知ることになる。
    _ed = result.get("evolve_decisions")
    if isinstance(_ed, dict) and _ed.get("marker_error"):
        summary["marker_error"] = _ed["marker_error"]
    # #402 PR-2 §0.3: sidecar 単調性契約違反の痕跡（warn + 続行）を 1 行サマリにも出す。
    # marker_error（#287-5）と同じ理由: envelope に入れるだけでは reader が居ない
    # 書きっぱなしフィールドになり、警告が実質無音になる（orphan_store / advisory
    # 書きっぱなしとしてこの repo が継続的に潰してきた型）。
    if isinstance(_ed, dict) and _ed.get("dry_run_snapshot_warning"):
        summary["dry_run_snapshot_warning"] = _ed["dry_run_snapshot_warning"]
    # #446: reject 抑制の meta を 1 行サマリにも出す。marker_error（#287-5）/
    # dry_run_snapshot_warning（#402 PR-2 §0.3）と同じ理由 — envelope に入れるだけでは
    # reader が居ない書きっぱなしフィールドになる（設計 §3.3 が当初想定した daily-run
    # 近傍への追記は、そのフィールド自体が daily-run に存在せず宛先が無かったため、
    # marker_error と同じこの配線に codex round3 [Must]4 で訂正した）。0件/None は
    # 既存の「ノイズを足さない」流儀でスキップする。
    if isinstance(_ed, dict) and _ed.get("reject_suppressed_total"):
        summary["reject_suppressed_total"] = _ed["reject_suppressed_total"]
    if isinstance(_ed, dict) and _ed.get("suppression_ledger_read_error"):
        summary["suppression_ledger_read_error"] = _ed["suppression_ledger_read_error"]
    if isinstance(_ed, dict) and _ed.get("suppression_candidate_errors"):
        summary["suppression_candidate_errors"] = _ed["suppression_candidate_errors"]
    # #458: auto trigger の発火状況を 1 行サマリに出す。envelope の trigger_summary は
    # `_state.py:_build_trigger_summary` が生成して `phases_capture.py` が result へ入れる
    # だけで、production/test どちらからも読まれない **読み手ゼロ** のフィールドだった
    # （#457 の棚卸しで D 判定）。trigger_engine が corrections 蓄積・セッション終了で
    # evolve/audit を自動提案する仕組みの稼働状況を、ユーザーも Claude も知る手段が
    # 無かった。marker_error（#287-5）/ reject_suppressed_total（#446）と同じ配線。
    # 未発火（total_fires=0）は既存の「0件ならノイズを足さない」流儀でスキップする。
    _tsum = result.get("trigger_summary")
    if isinstance(_tsum, dict) and _tsum.get("total_fires"):
        summary["trigger_summary"] = _tsum
    if result.get("observe_first"):
        summary["observe_first"] = True
        observe = result.get("phases", {}).get("observe", {})
        if isinstance(observe, dict) and observe.get("action"):
            summary["observe_action"] = observe["action"]
    return summary


if __name__ == "__main__":
    main()
