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
  外れ次回 run に持ち越す（**古い発話を優先**して選ぶ＝在庫の頭から着実に消化する）。

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
バッチ内の verdict 個別欠落（応答全体は解釈できたが特定 index だけ無い）は
``ingest_judgement_results`` 側の既存契約どおり「非修正」として確定する（本 runner は
その契約を変更しない）。
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
    """timestamp 昇順（古い順優先）。パース不能/欠落は最も古い扱いで先頭に残す（安全側）。"""
    return _parse_iso(u.get("timestamp")) or _EPOCH


def select_daily_batch(
    unjudged: List[Dict[str, Any]],
    *,
    daily_utterance_limit: int,
    daily_token_limit: int,
    batch_size: int,
) -> List[Dict[str, Any]]:
    """未判定発話から 1 日の件数・トークン上限内に収まる分だけを古い順に選ぶ（#408）。

    件数上限で先に打ち切ってから、推定トークン（``batch.estimate_tokens``・#431 の
    係数思想を再利用）が上限を超えない最大件数まで追加で切り詰める。
    """
    ordered = sorted(unjudged, key=_sort_key)
    by_count = ordered[: max(0, int(daily_utterance_limit))]
    n = len(by_count)
    while n > 0:
        est = _batch.estimate_tokens(by_count[:n], batch_size)["est_total_tokens"]
        if est <= daily_token_limit:
            break
        n -= 1
    return by_count[:n]


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
        dry-run: {"dry_run": True, "unjudged_total", "selected", "capped", "cost"}
        run:     {"dry_run": False, "requested", "responded", "call_failed",
                   "corrections", "non_corrections", "skipped_batches",
                   "parse_failed_batches", "weak_written", "idioms_written",
                   "judged_written", "unjudged_total", "selected", "capped"}
    """
    out = out if out is not None else sys.stdout

    if utterances is None:
        try:
            from utterance_archive.query import query_utterances_all_projects

            utterances = query_utterances_all_projects()
        except Exception:  # noqa: BLE001 - DB 未セットアップ等は空扱い（fail-open）
            utterances = []

    judged_keys = _store.read_judged_keys(judged_path)
    unjudged_all = _store.filter_unjudged(utterances or [], judged_keys)
    print(f"[judge_runner] 提示: 全PJ未判定発話 {len(unjudged_all)} 件", file=out)

    selected = select_daily_batch(
        unjudged_all,
        daily_utterance_limit=daily_utterance_limit,
        daily_token_limit=daily_token_limit,
        batch_size=batch_size,
    )
    capped = len(selected) < len(unjudged_all)

    if not run:
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
        }

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
