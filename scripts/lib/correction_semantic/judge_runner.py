"""correction_semantic.judge_runner — llm_judge Phase B の非対話 daily runner（#408）。

**背景（根因）**: ``batch.py`` の Phase A（emit）/ Phase C（ingest）は決定論で生きているが、
Phase B（LLM 判定）は docstring どおり「SKILL.md Step 6.6 の対話 y/n 承認時にセッション
自身がインライン判定する区間」にしか実体がなく、長い対話フローの奥にあるこのステップへ
2ヶ月人間が到達しなかった（2026-06 313件 → 07月 0件 → 08月 0件）。決定論チャネル
（rephrase 等）は daily runner が毎朝自動で回すため生きているのと対照的（#99 の非対称と
同型の「対話の奥にある MUST は本番機能でない」再発）。

本 runner は Phase B を **非対話** に置き換え、daily runner から直接呼べるようにする。
実装様式は ``verbosity/judge.py`` に完全に寄せる（新しい様式を発明しない）:

- **dry-run 既定**（llm-batch-guard 準拠）: 未判定件数 + 選定件数 + 推定トークンを
  print して終わる。実 LLM を呼ばない・1 バイトも書かない。
- ``run=True``（CLI は ``--run``）で実判定。LLM 呼び出しは ``call_haiku`` 1 箇所に集約
  （単体テストはここを mock する。no-llm-in-tests 完全整合）。実体は ``safe_llm_call``
  （#410 [Must]A: 判定対象の生ログに prompt injection が混入していてもツールを一切
  実行させない無人セーフガード。``verbosity.judge.call_haiku`` と共有）。
- 1 日の上限（件数・トークン）は呼び出し側が設定値として渡す（コード埋め込み禁止・
  ``rl_common.config`` の userConfig 流儀に合わせる。既定値 ``DEFAULT_DAILY_*`` は
  ユーザー承認済みの標準運用値: 200 件 / 150,000 トークン）。上限超過分は選定から
  外れ次回 run に持ち越す（**新しい発話を優先**して選ぶ・#410 [Must]G — weak_signal は
  TTL 45日で腐るため、在庫を古い順に流すと判定した端から失効しかねない。加えて直近の
  発話ほど記憶が新しく修正としての価値が高い。timestamp パース不能/欠落は最下位に
  落とす＝有効な日時を持つ発話を優先する）。

**fleet 横断の設計判断**: Phase A（``emit_judgement_requests``）は ``pj_slug`` 引数を
取るが、これは (a) ``utterances=None`` のときの自動クエリと (b) batch_id のラベル生成
にしか使われず、実際の pj_slug 帰属は ``ingest_judgement_results`` が各 utterance の
``pj_slug`` フィールドから個別に読む（batch_id とは無関係）。そのため本 runner は
全 PJ の未判定発話を ``query_utterances_all_projects()`` で 1 回に集約し、1 つの
バッチ列として Phase A/B/C に通す（PJ ごとに runner を分ける必要がない・PJ 混在
バッチでも pj_slug 帰属は壊れない・:func:`test_run_writes_weak_signal_with_correct_pj_slug_across_mixed_pj_batch`
が固定する契約）。

**欠けた verdict / 応答欠損・JSON 壊れの契約**: ``llm_broker.parse_responses`` は
リクエスト全 id を走査し、``responses`` に無い id は ``passthrough(None)`` で
空文字列に穴埋めする。``ingest_judgement_results`` は空文字列を「応答欠損」として
judged にせずスキップする（#273）ので、``call_haiku`` が例外を送出したリクエストは
``responses`` dict に一切書かず（キーごと省略）、この既存経路にそのまま合流させる。
バッチ内の verdict 個別欠落（応答全体は解釈できたが特定 index だけ無い＝部分応答）は
``ingest_judgement_results`` が欠落 index の発話だけ judged に積まず次回 drain へ残す
（#410 [Must]D）。``verdicts=[]`` を明示的に返した「正当な全件非修正」だけがバッチ全体を
確定させる（#273 の既存契約）。本 runner はどちらの契約も変更しない。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import safe_llm_call as _safe_llm_call  # noqa: E402
from rl_common.file_lock import file_lock as _file_lock  # noqa: E402
from weak_signals.ttl import _parse_iso  # noqa: E402

from . import DEFAULT_BATCH_SIZE  # noqa: E402
from . import batch as _batch  # noqa: E402
from . import store as _store  # noqa: E402

# 承認済み標準運用値（#408・ユーザー standing approval）。呼び出し側が override しない
# 場合の既定。userConfig（judge_daily_utterance_limit / judge_daily_token_limit）から
# 渡されるのが production 経路（daily runner 配線）で、ここはライブラリ関数の
# フォールバックに留まる（icebox_notice.DEFAULT_THRESHOLD_DAYS と同型）。
DEFAULT_DAILY_UTTERANCE_LIMIT = 200
DEFAULT_DAILY_TOKEN_LIMIT = 150_000

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def call_haiku(prompt: str, model: str = "haiku") -> str:
    """Haiku を 1 回呼ぶ（呼び出しの唯一の集約点・単体テストはここを mock する）。

    実体は ``safe_llm_call.call_claude_headless`` — 判定対象の生ログに prompt injection が
    混入していても無人実行でツールを一切動かさないことを実測済みの安全な呼び出し
    （#410 [Must]A）。``verbosity.judge.call_haiku`` と同じ実装を共有する（片方だけ直す
    partial fix を避けるため）。非ゼロ終了は ``safe_llm_call.ClaudeCallError`` を送出し
    （#410 [Must]F）、呼び出し側の既存の「呼び出し失敗 → 未判定のまま次回に残す」経路に
    合流させる。
    """
    return _safe_llm_call.call_claude_headless(prompt, model=model)


def _sort_key(u: Dict[str, Any]):
    """新しい順（降順）優先のソートキー（#410 [Must]G）。

    timestamp パース不能/欠落は「有効な日時グループの外」として最下位に落とす
    （曖昧な日付を実発話の新しさより優先させない・安全側）。有効な日時同士は
    ``sorted(..., reverse=True)`` により新しい順に並ぶ。
    """
    dt = _parse_iso(u.get("timestamp"))
    if dt is None:
        return (0, _EPOCH)
    return (1, dt)


def select_daily_batch(
    unjudged: List[Dict[str, Any]],
    *,
    daily_utterance_limit: int,
    daily_token_limit: int,
    batch_size: int,
) -> List[Dict[str, Any]]:
    """未判定発話から 1 日の件数・トークン上限内に収まる分だけを新しい順に選ぶ（#408, #410 [Must]G）。

    weak_signal は TTL 45日で腐るため、在庫を古い順に流すと判定した端から失効しかねない。
    直近の発話ほど記憶が新しく修正としての価値も高いため、新しい順に優先して選定する
    （旧実装は古い順だったが issue #408 のスコープに反していたため是正）。

    件数上限で先に打ち切ってから、推定トークン（``batch.estimate_tokens``・#431 の
    係数思想を再利用）が上限を超えない最大件数まで追加で切り詰める。
    """
    ordered = sorted(unjudged, key=_sort_key, reverse=True)
    by_count = ordered[: max(0, int(daily_utterance_limit))]
    n = len(by_count)
    while n > 0:
        est = _batch.estimate_tokens(by_count[:n], batch_size)["est_total_tokens"]
        if est <= daily_token_limit:
            break
        n -= 1
    return by_count[:n]


def _select_for_today(
    utterances: List[Dict[str, Any]],
    judged_path: Optional[Path],
    *,
    daily_utterance_limit: int,
    daily_token_limit: int,
    batch_size: int,
    out,
) -> "tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]":
    """未判定発話を読み、当日累積を差し引いた残り枠で選定する（read-only・副作用なし）。

    dry-run（read のみ）と run（read の後にロック下で選定〜記録）の両方が同じロジックを
    共有する単一ソース（#410 round2 [Must]dry-run: dry-run はこの関数を file_lock の外で
    呼ぶため sidecar ``.lock`` を一切生成しない）。

    Returns:
        (unjudged_all, selected, capped)
    """
    judged_keys = _store.read_judged_keys(judged_path)
    unjudged_all = _store.filter_unjudged(utterances or [], judged_keys)
    print(f"[judge_runner] 提示: 全PJ未判定発話 {len(unjudged_all)} 件", file=out)

    today = _store.count_judged_today(judged_path)
    remaining_utterance_limit = max(0, daily_utterance_limit - today["count"])
    remaining_token_limit = max(0, daily_token_limit - today["est_tokens"])
    if today["count"] or today["est_tokens"]:
        print(
            f"[judge_runner] 当日実績: {today['count']} 件 / 推定 {today['est_tokens']} "
            f"トークン消費済み（残り枠 {remaining_utterance_limit} 件 / "
            f"{remaining_token_limit} トークン）",
            file=out,
        )

    selected = select_daily_batch(
        unjudged_all,
        daily_utterance_limit=remaining_utterance_limit,
        daily_token_limit=remaining_token_limit,
        batch_size=batch_size,
    )
    capped = len(selected) < len(unjudged_all)
    return unjudged_all, selected, capped


def run_daily_judge(
    *,
    run: bool = False,
    daily_utterance_limit: int = DEFAULT_DAILY_UTTERANCE_LIMIT,
    daily_token_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    model: str = "haiku",
    utterances: Optional[List[Dict[str, Any]]] = None,
    judged_path: Optional[Path] = None,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    out=None,
) -> Dict[str, Any]:
    """全 PJ 横断の未判定発話を Haiku で判定する（dry-run 既定・#408）。

    Args:
        run:    True で実判定。False（既定）は dry-run（コスト先出しのみ・LLM 非呼出・非書込）。
        utterances: DI 用。None なら ``utterance_archive.query.query_utterances_all_projects``
                    から dialogue 発話を取得する（production 既定）。
        out:    出力先（既定 stdout）。フェーズ遷移ログ（提示/実行/応答/永続化）をここに書く。

    Returns:
        dry-run: {"dry_run": True, "unjudged_total", "selected", "capped", "cost",
                   "source_failed", "source_error"}
        run:     {"dry_run": False, "requested", "responded", "call_failed",
                   "corrections", "non_corrections", "skipped_batches",
                   "parse_failed_batches", "weak_written", "idioms_written",
                   "judged_written", "unjudged_total", "selected", "capped",
                   "source_failed", "source_error"}

        ``source_failed``（#410 [Must]E）: True なら発話ソース（utterances.db）取得が例外
        送出し 0 件として fail-open した（DB/schema 障害等）。``unjudged_total=0`` と正当な
        「未判定なし」を区別するための observability フィールド（沈黙させない）。
        ``source_error`` は例外の型+メッセージ（source_failed=False のときは None）。
    """
    out = out if out is not None else sys.stdout

    # #410 [Must]E: 発話ソース（utterances.db）の import/query 例外を「未判定0件」と
    # 区別できるよう surface する。fail-open（0件として継続）は維持するが、沈黙させない
    # （silence != evaluated）— DB/schema 障害で今回と同じ無期限供給停止が再発しても
    # 「call_failed=0・capped=false」の健康そうなサマリだけが残る事故を防ぐ。
    source_failed = False
    source_error: Optional[str] = None
    if utterances is None:
        try:
            from utterance_archive.query import query_utterances_all_projects

            utterances = query_utterances_all_projects()
        except Exception as e:  # noqa: BLE001 - fail-open（0件として継続）だが沈黙しない
            source_failed = True
            source_error = f"{type(e).__name__}: {e}"
            print(
                f"[judge_runner] 発話ソース取得に失敗しました: {source_error}（0件として継続）",
                file=sys.stderr,
            )
            utterances = []

    # #410 round2 [Must]dry-run: dry-run は read のみで排他不要（pitfall_dryrun_stateful_store_write
    # と同型の再発防止 — 以前は run 判定より前に file_lock へ入っており、dry-run でも sidecar
    # `.lock` を生成し「1バイトも書かない」契約に違反していた）。file_lock は run=True の
    # ときだけ取得する。
    if not run:
        unjudged_all, selected, capped = _select_for_today(
            utterances, judged_path,
            daily_utterance_limit=daily_utterance_limit,
            daily_token_limit=daily_token_limit,
            batch_size=batch_size,
            out=out,
        )
        cost = _batch.estimate_tokens(selected, batch_size)
        print(
            f"[judge_runner] [dry-run] 今日処理する対象: {len(selected)} 件 "
            f"（推定 {cost['batches']} バッチ / 推定 ~{cost['est_total_tokens']} トークン、"
            f"上限 {daily_utterance_limit} 件 / {daily_token_limit} トークン）",
            file=out,
        )
        if capped:
            print(
                f"[judge_runner] → 上限により {len(unjudged_all) - len(selected)} 件は"
                "次回 run に持ち越します。",
                file=out,
            )
        return {
            "dry_run": True,
            "unjudged_total": len(unjudged_all),
            "selected": len(selected),
            "capped": capped,
            "cost": cost,
            "source_failed": source_failed,
            "source_error": source_error,
        }

    # #410 [Must]B: 選定〜記録（判定済み記録の read-modify-write）を排他する。日次上限は
    # 「当日どれだけ既に消費したか」を read 時導出して差し引かないと、cron 再実行や手動 --run
    # の重ね掛けで実質「1 回の呼び出し上限」になってしまう（同じ日に何度呼んでも毎回フル
    # 予算が使える）。ロックは対象ファイルでなく sidecar（file_lock の設計どおり）。
    # flock は open file description 単位で入れ子取得すると自己 deadlock するが、
    # store_write/store_write_raw は対象ファイル自身に別途ロックを取るだけで、この sidecar
    # ロックとは異なるファイルのため入れ子にならない（rl_common.file_lock の注意点を参照）。
    judged_target = Path(judged_path) if judged_path is not None else _store.default_judged_path()
    lock_path = judged_target.with_name(judged_target.name + ".lock")

    with _file_lock(lock_path):
        unjudged_all, selected, capped = _select_for_today(
            utterances, judged_path,
            daily_utterance_limit=daily_utterance_limit,
            daily_token_limit=daily_token_limit,
            batch_size=batch_size,
            out=out,
        )

        if not selected:
            print("[judge_runner] 未判定の対象がありません。", file=out)
            return {
                "dry_run": False,
                "requested": 0,
                "responded": 0,
                "call_failed": 0,
                "corrections": 0,
                "non_corrections": 0,
                "skipped_batches": 0,
                "parse_failed_batches": 0,
                "weak_written": 0,
                "idioms_written": 0,
                "judged_written": 0,
                "unjudged_total": len(unjudged_all),
                "selected": 0,
                "capped": capped,
                "source_failed": source_failed,
                "source_error": source_error,
            }

        # Phase A（決定論）: "daily" はラベルに過ぎない（batch_id 構成のみに使われ、
        # pj_slug 帰属は各 utterance の pj_slug フィールドが単一ソース。docstring 参照）。
        emitted = _batch.emit_judgement_requests(
            "daily", utterances=selected, batch_size=batch_size, judged_path=judged_path,
        )
        print(
            f"[judge_runner] 実行: {emitted['batches']} バッチ（{emitted['unjudged']} 件）を"
            " Haiku へ送信します",
            file=out,
        )

        # Phase B（LLM・本 runner が非対話で肩代わりする区間）。
        responses: Dict[str, str] = {}
        call_failed = 0
        for req in emitted["requests"]:
            key = req.get("id")
            prompt = req.get("prompt", "")
            try:
                raw = call_haiku(prompt, model)
            except Exception as e:  # noqa: BLE001 - 1 バッチの失敗は次バッチへ継続（fail-open）
                call_failed += 1
                print(
                    f"[judge_runner]   {key}: Haiku 呼び出し失敗 ({e}) — 未判定のまま次回に残す",
                    file=sys.stderr,
                )
                continue  # responses に書かない → parse_responses が空文字列で穴埋めし
                # ingest_judgement_results の「応答欠損」スキップ経路に自然に合流する。
            responses[key] = raw
        print(
            f"[judge_runner] 応答: {len(responses)}/{len(emitted['requests'])} バッチ受信"
            f"（失敗 {call_failed}）",
            file=out,
        )

        # Phase C（決定論）。
        result = _batch.ingest_judgement_results(
            emitted, responses, dry_run=False,
            weak_signals_path=weak_signals_path, idioms_path=idioms_path, judged_path=judged_path,
        )
        print(
            f"[judge_runner] 永続化: corrections={result['corrections']} "
            f"non_corrections={result['non_corrections']} "
            f"skipped_batches={result['skipped_batches']} "
            f"parse_failed_batches={result['parse_failed_batches']} "
            f"weak_written={result['weak_written']} judged_written={result['judged_written']}",
            file=out,
        )

        return {
            "dry_run": False,
            "requested": emitted["unjudged"],
            "responded": len(responses),
            "call_failed": call_failed,
            "corrections": result["corrections"],
            "non_corrections": result["non_corrections"],
            "skipped_batches": result["skipped_batches"],
            "parse_failed_batches": result["parse_failed_batches"],
            "weak_written": result["weak_written"],
            "idioms_written": result["idioms_written"],
            "judged_written": result["judged_written"],
            "unjudged_total": len(unjudged_all),
            "selected": len(selected),
            "capped": capped,
            "source_failed": source_failed,
            "source_error": source_error,
        }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="llm_judge Phase B（意味判定）の非対話バッチ実行（#408）"
    )
    ap.add_argument("--run", action="store_true", help="実際に Haiku を呼ぶ（既定は dry-run）")
    ap.add_argument(
        "--limit", type=int, default=DEFAULT_DAILY_UTTERANCE_LIMIT,
        help="今回処理する最大件数（既定は1日の標準上限。パイロット時は小さい値に override）",
    )
    ap.add_argument(
        "--token-limit", type=int, default=DEFAULT_DAILY_TOKEN_LIMIT,
        help="今回処理する推定トークン上限",
    )
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--model", default="haiku")
    args = ap.parse_args(argv)

    run_daily_judge(
        run=args.run,
        daily_utterance_limit=args.limit,
        daily_token_limit=args.token_limit,
        batch_size=args.batch_size,
        model=args.model,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
