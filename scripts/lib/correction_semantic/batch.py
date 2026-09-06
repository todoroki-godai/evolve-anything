"""correction_semantic.batch — バッチ LLM 意味判定の 2 相オーケストレーション（#431）。

auto_memory_broker（ADR-037）と同型の 2 相に分離し、Python から claude -p を完全に
追い出す（no-llm-in-tests と完全整合・テストは responses dict を直接渡す）:

  Phase A（決定論・LLM 非依存）: emit_judgement_requests
    utterances.db の dialogue 発話を query → 判定済み（correction_judged.jsonl）を除外 →
    N 件ずつバッチ化 → llm_broker.build_requests で {id, prompt, meta} を生成。
    meta に発話グループを持たせ、Phase C が verdict.index → 発話を引けるようにする。

  Phase B（LLM・assistant / Haiku）: 各 prompt にインライン or Task subagent で応答。
    本モジュール対象外（SKILL.md / evolve 配線が担う）。モデルは Haiku。

  Phase C（決定論・LLM 非依存）: ingest_judgement_results
    llm_broker.parse_responses で id→生テキスト回収 → prompt.parse_verdicts_result で JSON 解釈。
    修正と判定された発話を **weak_signals レーン（channel=llm_judge）に隔離記録** +
    **個人辞書（correction_idioms.jsonl）に provenance 付き蓄積**。判定し終えた発話の
    物理キーを correction_judged.jsonl に記録（再判定防止）。応答欠損バッチ・JSON パース失敗
    バッチはどちらも判定済みにせずスキップ（次 drain で再試行）。**#273**: 従来は応答欠損のみ
    スキップし、JSON が壊れているケースは parse_verdicts の [] フォールバックで「該当なし」と
    誤読され group 全件が判定済みに確定していた（欠損は再試行されるのに壊れた応答はされない
    非対称）。parse_verdicts_result の ok フラグで両者を区別し、同じスキップ経路に合流させる。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: ``dry_run=True`` のとき判定は走るが
weak_signals / 個人辞書 / 判定進捗のどれにも一切書かない（各 append が最下層で弾く）。

非対話 PJ 除外: utterance 側で担保する。query_utterances のデフォルト source_kinds=('dialogue',)
は long_paste / excluded_pj を含めないため、emit にはそもそも対話発話しか渡らない。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough  # noqa: E402

from . import DEFAULT_BATCH_SIZE, LLM_JUDGE_CHANNEL, MAX_CHARS_PER_UTTERANCE
from . import idiom_filter as _idiom_filter
from . import prompt as _prompt
from . import representative as _representative
from . import store as _store

# #410 [Must]C 是正: 旧実装は「件数 × 80 + バッチ数 × 400」の固定係数で本文長を一切見て
# おらず、長文1件を短文1件と同じ 80 トークンに見積もっていた（15万トークン上限が実質
# 無効化しうる）。実入力の文字数に連動する保守的な見積もりに変える。
#
# 日本語は概ね 1〜2 文字 ≈ 1 トークン（英数字混在では 4 字弱/トークンになることも多いが、
# 判定対象は日本語ユーザー発話が主）。安全側（過大に出す）に倒すため 2 文字/トークンで
# 見積もる。``prompt.build_batch_prompt`` と同じ ``MAX_CHARS_PER_UTTERANCE`` で本文長を
# 頭打ちし、見積もりと実送信の乖離を防ぐ（単一ソース）。
_CHARS_PER_TOKEN = 2.0
# #410 round2 [Must]C: per-utterance の固定オーバーヘッド係数は廃止した。
# estimate_utterance_tokens が実際に組み立てるプロンプト行（prompt.format_utterance_line、
# ラベル文言 + prev_action + text）の長さをそのまま測るため、別途の固定加算が不要になった。
#
# プロンプト雛形（指示文・カテゴリ語彙表・出力形式説明）の固定オーバーヘッド（バッチ単位）。
# #400 A5（設計 §2.2 codex [Should]）是正: 旧実装は 400 のハードコード定数で、カテゴリ語彙表
# 追加のようなプロンプト伸長で見積もりが実態から乖離した。``prompt.build_batch_prompt([])``
# （発話ゼロ件＝固定部分のみ）の実長から導出し、テンプレート変更に自動追従させる
# （estimate_tokens の固定費と reserve_batch_cost の予約額の両方がこの1定数を共有する単一ソース）。
#
# #625 [Should]: judge_runner.call_haiku は毎バッチ ``prompt.VERDICT_JSON_SCHEMA``
# を safe_llm_call の json_schema 引数として送るが、旧実装は本体プロンプト
# （build_batch_prompt）の長さしか見積もっておらず schema 分が予約から漏れていた
# （呼ぼうとした時点で必ず予約する reserved_batches の思想＝#410 round4 と矛盾する隙間）。
# schema 側も同じ _CHARS_PER_TOKEN で換算して加算する。
_PROMPT_OVERHEAD_TOKENS = math.ceil(
    len(_prompt.build_batch_prompt([])) / _CHARS_PER_TOKEN
) + math.ceil(len(_prompt.VERDICT_JSON_SCHEMA) / _CHARS_PER_TOKEN)

# #400 A5（設計 §2.2）: 出力（verdict JSON 1件分）の概算トークン。旧実装は入力のみを見積もり、
# 出力 token を一切計上していなかった（プロンプト伸長で入力は直るが出力の見落としは別問題）。
# {"index": N, "is_correction": true, "idiom": "...", "category": "...", "reason": "..."} の
# JSON 1件は日本語 idiom/reason を含め概ね 40〜80字。安全側に倒し 60 字相当（30 token）を見積もる。
_OUTPUT_TOKENS_PER_VERDICT = 30


def _batch_id(pj_slug: str, group: List[Dict[str, Any]]) -> str:
    """バッチ ID = pj_slug + 先頭発話の物理キー（決定論・再実行で安定）。"""
    head = _store.utterance_key(group[0]) if group else "empty"
    return f"{pj_slug}:{head}"


def _chunk(items: List[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


# ─────────────────────────────────────────────────────────────────
# Phase A: emit
# ─────────────────────────────────────────────────────────────────
def emit_judgement_requests(
    pj_slug: str,
    *,
    utterances: Optional[List[Dict[str, Any]]] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    judged_path: Optional[Path] = None,
    source_kinds: Sequence[str] = ("dialogue",),
) -> Dict[str, Any]:
    """判定対象発話をバッチ化して LLM リクエスト一覧を生成する（決定論・IO 読取のみ）。

    utterances を渡さなければ utterances.db から query（dialogue のみ・非対話除外）。
    判定済み（correction_judged.jsonl）の発話は除外する。

    Returns:
        {"requests": [{"id", "prompt", "meta": {"utterances": [...]}}],
         "unjudged": int, "batches": int}
    """
    if utterances is None:
        try:
            from utterance_archive.query import query_utterances

            utterances = query_utterances(pj_slug, source_kinds=tuple(source_kinds))
        except Exception:
            utterances = []

    judged_keys = _store.read_judged_keys(judged_path)
    unjudged = _store.filter_unjudged(utterances or [], judged_keys)

    groups = _chunk(unjudged, batch_size)
    items: List[Dict[str, Any]] = [
        {"id": _batch_id(pj_slug, g), "utterances": g} for g in groups
    ]
    requests = build_requests(
        items, lambda item: _prompt.build_batch_prompt(item.get("utterances", []))
    )
    return {"requests": requests, "unjudged": len(unjudged), "batches": len(groups)}


# ─────────────────────────────────────────────────────────────────
# Phase C: ingest
# ─────────────────────────────────────────────────────────────────
def _batch_cost_tokens(
    group: List[Dict[str, Any]], *, max_chars: int = MAX_CHARS_PER_UTTERANCE
) -> int:
    """1 バッチ試行分の概算トークン（本文合計 + バッチ固定費 + 出力予算）。

    #410 round4 [Must]1+2: ``reserve_batch_cost`` が呼び出し直前の予約記録に使う唯一の
    コスト算出関数（誰にも確定的に帰属しない試行なのでバッチ丸ごと 1 件のコストとして扱う。
    round3 の per-key 按分方式は round4 で廃止した）。

    #400 A5: **``estimate_tokens`` と同一の式でなければならない**（``estimate_tokens`` は
    この関数を全バッチに適用した総和として定義される）。同じ量に式が2つあると、片方だけ
    直したときに「見積もりは出力込み・予算ガードは入力のみ」という desync が生まれる
    （実際 A5 の初版がその状態だった）。予算を実際に守るのは**こちら**なので、出力予算
    （``_OUTPUT_TOKENS_PER_VERDICT`` × バッチ内発話数）はここに含める。
    """
    return (
        sum(estimate_utterance_tokens(u, index=i, max_chars=max_chars) for i, u in enumerate(group))
        + _PROMPT_OVERHEAD_TOKENS
        + len(group) * _OUTPUT_TOKENS_PER_VERDICT
    )


def reserve_batch_cost(
    group: List[Dict[str, Any]],
    *,
    judged_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """1 バッチの見積コストを、LLM を呼ぶ**前**に予約記録する（#410 round4 [Must]1+2）。

    **設計変更（round3→round4）**: round3 は「呼び出し後に課金が確定したと判明した試行だけ
    事後記録する」方式だったが、codex round4 レビューで構造的な穴を指摘された:
      - 範囲内 verdict が1件でもあれば billed_attempt を作らないため、未返却 index の
        本文コスト（同じプロンプトで送信・課金済み）が丸ごと未計上になっていた
      - CLI 例外を「未課金」とみなす前提が成立しない（API 到達後のタイムアウト・応答受信後の
        異常終了・プロセス中断は課金済みでありうる）
      - 全呼び出し後にまとめて記録するため、Phase B 中のプロセス終了で当日分が全損する

    round4 は ``judge_runner`` の Phase B ループが本関数を **各 call_haiku 呼び出しの直前に
    1バッチずつ即時に** 呼ぶ。呼んだ時点で「課金される可能性がある」とみなし無条件に予約
    するため、事後の「課金されたか否か」判別が一切不要になる（例外・タイムアウト・プロセス
    中断のいずれでも予約は残る）。過大計上（実際には無課金だった呼び出し分も予約される）に
    倒れるが、予算の歯止めとしては保守側が正しい。

    このコストが当日累積の**唯一のソース**になる（``ingest_judgement_results``（Phase C）
    側の per-key est_tokens 記録・バッチ固定費按分は round4 で完全に廃止した。二重計上を
    避けるため）。実体は ``_store.record_billed_attempts``（"key" を持たない keyless
    record・#379 新設凍結中のため新ストアは作らず既存 ``correction_judged.jsonl`` を再利用）。

    Returns:
        {"written": int, "dry_run": bool}
    """
    return _store.record_billed_attempts(
        [_batch_cost_tokens(group)], path=judged_path, dry_run=dry_run,
    )


def ingest_judgement_results(
    emitted: Dict[str, Any],
    responses: Dict[str, Any],
    *,
    dry_run: bool = False,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    judged_path: Optional[Path] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM 応答を回収し weak_signals 隔離記録 + 個人辞書蓄積する（決定論・LLM 非依存）。

    各バッチ:
      1. parse_responses + passthrough で生テキスト回収
      2. 空/missing はスキップ（判定済みにせずキューに残す＝再判定可能）
      3. prompt.parse_verdicts_result で JSON 解釈し ok=False（パース失敗）ならスキップ（#273:
         応答欠損と同じ「未判定のまま残す」経路に合流。verdict.index → 発話を引く（ok=True 時）
      4. is_correction=True → WeakSignal(channel=llm_judge) + CorrectionIdiom を蓄積。
         ただし発話が全行 assistant 引用（human 発言が0行）なら書き込まず
         assistant_only_skipped に計上する（#445: representative.is_assistant_only_text）
      5. バッチ内の発話の物理キーを judged に記録（修正/非修正どちらも。ok=False バッチは対象外）。
         verdicts が正当に空配列（モデルが「該当なし」と明示判定）ならバッチ全体を非修正
         として確定する（#273: 未判定に残すと再判定費用が際限なく積むため）。一部 index だけ
         verdict が欠けた**部分応答**は、揃っている index だけ確定し、欠落した index の発話は
         未判定のまま残す（judged に積まない・次回 drain で再試行。#410 [Must]D: バッチ全体を
         戻すと head-of-line blocking になるため避ける）。件数は omitted_verdicts に surface

    過汎用 idiom guard（#527）: floor（8 文字未満）/ stopword（相槌・推量・否定のみ）/
    文脈固有トークン（日付・割合・序数）に該当する idiom は **個人辞書に入れない**
    （weak_signal は隔離記録するので reflect で人間が拾える）。弾いた件数は idioms_filtered。

    ``model``（#400 A5・設計 §2.4）: 呼び出し元が実際に使ったモデル alias（例 "haiku"）を
    渡す。修正と判定された verdict の provenance に category（判定した対象軸）と一緒に
    ``model`` / ``prompt_fingerprint`` / ``category_schema_version`` を **producer 時点**
    （この ingest 呼び出し時点）で保存する。category は「事実」でなく「その judge 実行時の
    測定値」であり（同一物理発話が応答欠損・部分応答で再判定される経路が実在する・
    設計 §2.4）、集計時に現在値を付けるのではなく判定時の条件を記録することで系列断絶
    （プロンプト変更）を後から検出できるようにする。省略時（None）は「呼び出し元が
    model を渡さなかった」ことを表し、そのまま None が provenance に残る（推測しない）。

    **課金コストはここでは記録しない（#410 round4 [Must]1+2）**: round3 まではここで
    per-key est_tokens・バッチ固定費按分・billed_attempt を記録していたが、応答の解釈結果
    次第で課金の一部が計上漏れになる構造的な穴があった。round4 は判定結果に一切依存しない
    「呼ぶ前に予約する」方式（``reserve_batch_cost``。呼び出し元 ``judge_runner`` の Phase B
    ループが担う）に一本化し、この Phase C（decision-dependent）からは完全に切り離した。

    Returns:
        {"corrections", "non_corrections", "assistant_only_skipped", "skipped_batches",
         "parse_failed_batches", "omitted_verdicts", "out_of_range_verdicts", "weak_written",
         "idioms_written", "idioms_filtered", "judged_written", "dry_run"}
    """
    from weak_signals.store import WeakSignal, append_signals, now_iso

    requests = emitted.get("requests", [])
    responses = responses or {}
    parsed = parse_responses(requests, responses, parser=passthrough)

    signals: List[WeakSignal] = []
    idioms: List[_store.CorrectionIdiom] = []
    judged_keys: List[str] = []
    corrections = 0
    non_corrections = 0
    assistant_only_skipped = 0  # #445: 全行 assistant 引用のため書込を見送った件数
    skipped_batches = 0
    parse_failed_batches = 0  # #273: 応答は届いたが JSON が解釈不能だったバッチ数
    idioms_filtered = 0  # #527: 過汎用 idiom（floor/stopword/context token）で弾いた件数
    omitted_verdicts = 0  # #273: verdict が返らず非修正として確定した発話数（observability）
    # #410 round3 [Should]⑤: バッチ対象外の index を無視した件数（黙って捨てない）。
    out_of_range_verdicts = 0
    # #400 A5（設計 §2.4）: fingerprint は utterances に依存しない固定テンプレート部分の
    # ハッシュなので、この ingest 呼び出し内で 1 回だけ計算すれば足りる（バッチ間で不変）。
    _fingerprint = _prompt.prompt_fingerprint()

    for req in requests:
        key = req.get("id")
        if not key:
            continue
        group: List[Dict[str, Any]] = (req.get("meta") or {}).get("utterances", [])
        raw = parsed.get(key)
        text = raw.strip() if isinstance(raw, str) else ""
        if not text:
            # 応答欠損: 判定済みにせず次 drain で再試行。
            # #410 round4 [Must]1+2: このバッチのコストは判定結果を問わず judge_runner の
            # Phase B が呼び出し直前に既に予約済み（reserve_batch_cost）。ここでの billed
            # 判別・追加記録は不要になった。
            skipped_batches += 1
            continue

        # 変数名は `parsed`（= parse_responses の応答マップ）と必ず分ける。
        # 同名にすると 2 バッチ目以降の `parsed.get(key)` が verdict dict を引いて
        # 全バッチ silent skip する（#273 レビューで検出した shadowing 回帰）。
        # #410 round3 [Should]⑤: expected_len=len(group) でバッチ対象外の index を検出する
        # （round2 は検出時にバッチ全体を失格にしていたが、余分な1件で無限再試行になる
        # 過剰さを避けるため round3 でパーサ側が個別に無視する方式へ変更。上の
        # out_of_range_verdicts で件数を surface する）。
        parsed_verdicts = _prompt.parse_verdicts_result(text, expected_len=len(group))
        if not parsed_verdicts["ok"]:
            # #273: JSON 解釈不能。応答欠損と同様、判定済みにせず次 drain で再試行する
            # （[] フォールバックを「該当なし」と誤読して judged_keys に積むと desync する）。
            parse_failed_batches += 1
            continue
        # #410 round3 [Should]⑤: バッチ対象外の index はパーサ側で個別に無視され
        # verdicts から除外済み（バッチ全体は失格にしない）。無視した件数だけ observability
        # として積む（黙って捨てない）。
        out_of_range_verdicts += parsed_verdicts.get("out_of_range", 0)

        by_index = {v["index"]: v for v in parsed_verdicts["verdicts"]}
        # #410 [Must]D: 「正当な全件非修正」（verdicts=[] を明示的に返した・#273）と
        # 「一部 index だけ欠落した部分応答」を区別する。前者はバッチ全体を確定させないと
        # 正当なケースが毎 drain 再判定され費用が積む（既存契約・
        # test_ingest_legitimate_empty_verdicts_still_marks_judged が固定）。後者は
        # バッチ全体を戻すと head-of-line blocking になるため、欠落した index の発話だけ
        # 未判定のまま残し、揃っている index は従来どおり確定する。
        #
        # #410 round4 [Must]3 是正: 「元々 verdicts=[]（モデルが明示的に該当なしと判定）」と
        # 「範囲外 index を除去した結果 verdicts=[] に収束した」は意味が違う。後者は
        # 「対象発話を判定していない」のであって「対象発話には修正が無い」のではない
        # （round3 はこの区別をせず全件を非修正として誤確定させていた＝データ損失）。
        # out_of_range が1件でも発生していれば legitimate_empty_batch にせず、対象発話は
        # 未判定のまま残す（下の omitted_verdicts 経路に自然に合流する）。
        legitimate_empty_batch = (
            len(parsed_verdicts["verdicts"]) == 0
            and parsed_verdicts.get("out_of_range", 0) == 0
        )

        for local_i, utt in enumerate(group):
            key = _store.utterance_key(utt)
            v = by_index.get(local_i)
            if v is None:
                if legitimate_empty_batch:
                    judged_keys.append(key)
                    non_corrections += 1
                    continue
                # 部分応答の欠落: judged_keys に積まない（未判定のまま次回 drain で再試行）。
                # 件数だけ surface して silence にはしない（#273）。
                omitted_verdicts += 1
                continue
            judged_keys.append(key)
            if not v.get("is_correction"):
                non_corrections += 1
                continue
            # #445: 発話が全行 assistant 引用（human 発言が1行も無い）なら、LLM が
            # is_correction=True と判定していても書き込まない。人間発話のみという既存方針
            # （user_only_text / #528-3）を書込境界でも徹底する。judged には積んで再判定
            # ループを防ぎつつ、黙って減らさず専用カウンタに計上する（silence != evaluated）。
            if _representative.is_assistant_only_text(utt.get("text") or ""):
                assistant_only_skipped += 1
                continue
            corrections += 1
            idiom_text = v.get("idiom")
            prov = {
                "source_path": utt.get("source_path", ""),
                "line_no": utt.get("line_no", ""),
                "session_id": utt.get("session_id", ""),
                # #528-3: representative の判読性のため user 発話のみ保存（assistant の
                # 過去レポート引用ブロックを除去）+ 直前 AI 行動を evidence に添える。
                "text": _representative.user_only_text(utt.get("text") or "")[:200],
                "prev_action": (utt.get("prev_action") or "")[:120],
                "reason": v.get("reason", ""),
                # #253: Haiku が抽出した idiom を保存し、signal_text の多トピック発言トリム
                # （trim_to_idiom_sentence）に使う。idiom_eligible（個人辞書用ゲート）とは
                # 独立に保存する（トリム目的では eligibility を問わない）。
                "idiom": idiom_text or "",
                "judge": "llm_haiku",
                # #400 A5（設計 §2.1/§2.4）: 対象軸カテゴリ（8値 enum・非修正時は None）。
                # producer 時点の測定条件（model / prompt fingerprint / schema version）を
                # 同時に保存する。事実でなく「その judge 実行時の測定値」として扱うため、
                # 集計時に現在値を付けず、この時点の値をそのまま固定する。
                "category": v.get("category"),
                "model": model,
                "prompt_fingerprint": _fingerprint,
                "category_schema_version": _prompt.CATEGORY_SCHEMA_VERSION,
            }
            detected_at = now_iso()
            signals.append(WeakSignal(
                channel=LLM_JUDGE_CHANNEL,
                provenance=prov,
                detected_at=detected_at,
                session_id=str(utt.get("session_id") or ""),
                pj_slug=str(utt.get("pj_slug") or ""),
            ))
            # #527: 過汎用 idiom（極短/相槌・推量/日付・数値断片）は個人辞書に入れない。
            # idiom 化を弾いても weak_signal は隔離記録済み（reflect で人間が拾える）。
            if idiom_text and _idiom_filter.idiom_eligible(idiom_text):
                idioms.append(_store.CorrectionIdiom(
                    idiom=idiom_text,
                    provenance=prov,
                    detected_at=detected_at,
                    pj_slug=str(utt.get("pj_slug") or ""),
                ))
            elif idiom_text:
                idioms_filtered += 1

    ws_res = append_signals(signals, path=weak_signals_path, dry_run=dry_run)
    idiom_res = _store.append_idioms(idioms, path=idioms_path, dry_run=dry_run)
    # #410 round4 [Must]1+2: est_tokens は渡さない（judge_runner の Phase B が呼び出し
    # 直前に reserve_batch_cost で既に予約済み。ここで再度付与すると二重計上になる）。
    judged_res = _store.record_judged(judged_keys, path=judged_path, dry_run=dry_run)

    return {
        "corrections": corrections,
        "non_corrections": non_corrections,
        "assistant_only_skipped": assistant_only_skipped,  # #445: 全行 assistant 引用で書込を見送った件数
        "skipped_batches": skipped_batches,
        "parse_failed_batches": parse_failed_batches,
        "weak_written": ws_res["written"],
        "idioms_written": idiom_res["written"],
        "idioms_filtered": idioms_filtered,  # #527: 過汎用で弾いた idiom 件数（observability）
        "omitted_verdicts": omitted_verdicts,  # #273: verdict 欠落で非修正確定した発話数
        "out_of_range_verdicts": out_of_range_verdicts,  # #410 round3 [Should]⑤: 無視した範囲外件数
        "judged_written": judged_res["written"],
        "dry_run": bool(dry_run),
    }


# ─────────────────────────────────────────────────────────────────
# トークン見積もり（llm-batch-guard: 実走前にユーザーへ提示）
# ─────────────────────────────────────────────────────────────────
def estimate_utterance_tokens(
    utterance: Dict[str, Any], *, index: int = 0, max_chars: int = MAX_CHARS_PER_UTTERANCE
) -> int:
    """1 発話あたりの概算トークン。

    ``estimate_tokens``（バッチ集計）と ``ingest_judgement_results``（#410 [Must]B:
    ``correction_judged.jsonl`` へ判定済みキーごとの推定コストを残し当日累積を導出する）が
    同じ単価を共有する単一ソース。プロンプト雛形分（``_PROMPT_OVERHEAD_TOKENS``）は
    バッチ単位のため含まない。

    #410 round2 [Must]C 是正: 旧実装は本文（``text``）長のみを測り、prev_action
    （実送信では最大300字）・ラベル文言（"[i] 直前のClaudeの操作: " 等）を固定係数
    ``_PER_UTTERANCE_OVERHEAD_TOKENS`` で丸めていたため、長い日本語 prev_action が多い
    バッチで大幅な過小評価になっていた。理想解として「実際に組み立てるプロンプト行
    （``prompt.format_utterance_line``）の長さをそのまま測る」方式にした — ロジックを
    2箇所（見積もりと実送信）に複製すると片方だけ切り詰め幅を変えたときに乖離が再発する
    ため、行の組み立てそのものを単一ソース化した（固定の per-utterance オーバーヘッド
    加算はもう不要 — ラベル文言も実測に含まれるため）。

    ``index``（#410 round3 [Must]2 是正）: バッチ内での実位置（0 始まり）。呼び出し側が
    常に ``index=0`` を渡していたため、"[10]" のように2桁以上になる発話ほど（"[0]" より
    1文字長い）過小評価していた。既定 0 は単発の発話単価見積もり（呼び出し側がバッチ内
    位置を持たない箇所）との後方互換のため維持する。
    """
    line = _prompt.format_utterance_line(index, utterance, max_chars=max_chars)
    return math.ceil(len(line) / _CHARS_PER_TOKEN)


def estimate_tokens(
    utterances: List[Dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    *,
    max_chars: int = MAX_CHARS_PER_UTTERANCE,
) -> Dict[str, Any]:
    """判定対象発話の概算トークン消費を返す（llm-batch-guard 用・決定論・#410 [Must]C）。

    各発話の本文実長（``max_chars`` で頭打ち・``build_batch_prompt`` と同じ切り詰め）に
    連動する。固定件数係数（旧実装）だと長文1件が短文1件と同じ見積もりになり、
    トークン上限が実効性を持たなかった。

    #410 round3 [Must]2 是正: 実際の emit（``_chunk`` で ``batch_size`` ごとに分割し、
    各バッチ内で 0 始まりの index を振る）と同じ分割・index 付けで見積もる。旧実装は
    件数から batches 数だけ算出し、各発話の index を意識せず一律 index=0 で見積もって
    いたため、1バッチが大きく2桁 index が生じる構成ほど過小評価になっていた。

    #400 A5（設計 §2.2）是正: 出力（verdict JSON）の token も加算する。旧実装は入力
    （プロンプト本文＝発話 + 固定雛形）しか見ておらず、判定結果として返ってくる出力
    token が見積もりから完全に欠落していた。出力は発話 1 件につき verdict 1 件が
    対応するため ``_OUTPUT_TOKENS_PER_VERDICT × 発話件数`` で加算する
    （バッチ数でなく発話数に連動する点が固定費 ``_PROMPT_OVERHEAD_TOKENS`` と異なる）。

    式は ``_batch_cost_tokens``（``reserve_batch_cost`` が実際の予算ガードに使う関数）を
    全バッチに適用した総和として定義する。**同じ量を2箇所で別々に足し上げない**
    （A5 初版は estimate 側だけ出力予算を足し、予算ガード側が入力のみのまま desync していた）。
    """
    items = utterances or []
    n = len(items)
    groups = _chunk(items, batch_size)
    batches = len(groups)
    est = sum(_batch_cost_tokens(group, max_chars=max_chars) for group in groups)
    return {
        "utterances": n,
        "batches": batches,
        "batch_size": batch_size,
        "est_total_tokens": est,
    }
