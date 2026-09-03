"""correction_semantic.promote — weak_signals → corrections 昇格フロー（#431 提案2/3）。

reflect 時に人間が weak_signals レーン（channel=llm_judge ほか）の未昇格レコードを確認し、
本物の修正だけを corrections 本流へ昇格する。昇格レコードは **source=reflect_confirmed**
（human-source）で書かれ、フェーズ昇格カウント（provenance_weight）を駆動する。

二重昇格防止: 昇格した weak_signal は ``promoted=True`` にマークする（read_unpromoted から外れる）。
weak_signals.jsonl は append-only だが、昇格マークだけは read-modify-write（原子的 rename）で
書き換える（dedup キーで該当行だけ更新、他行は不変）。

dry-run ゼロ書込: ``dry_run=True`` なら corrections にも weak_signals にも一切書かない。
DATA_DIR は ADR-042 resolver 経由（weak_signals.store / 各既定パスに委譲）。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from weak_signals.store import STORE_NAME as _WS_STORE_NAME, read_signals
from weak_signals.ttl import is_effectively_expired

# #46 read 層拡張: union read（昇格候補）+ union mark（再昇格防止）の候補 dir 解決を共有する。
from store_read_union import iter_read_store_paths as _iter_read_store_paths  # noqa: E402
from rl_common import append_correction_record, new_correction_id
from rl_common.correction_id import (
    assert_no_unexpected_content_loss,
    atomic_write_text_preserving_mode,
    corrections_write_lock,
    fcntl_unsupported_reason,
    snapshot_identities,
)


def _normalize_project_path(value: str) -> str:
    """project_path を worktree 安全な slug に正規化する（#593）。

    昇格レコードの project_path は呼び出し側が worktree フルパスを渡しうるが、
    consumer は PJ 識別子として扱う（パスとして open/stat しない）。書込境界で
    ``pj_slug.pj_slug_fast``（subprocess なしの軽量版）を通し、worktree フルパス
    （``.../.claude/worktrees/<name>``）を本体 repo slug に畳む。hook 書込側の
    project（#492 ``project_name_from_dir``）と同方式に揃える（新方式を発明しない）。
    空値はそのまま空で返す（None→"" を増幅しない）。
    """
    if not value:
        return value
    try:
        from pj_slug import pj_slug_fast
        slug = pj_slug_fast(value)
        if slug:
            return slug
    except Exception:
        pass
    return Path(value).name or value


def _filter_unpromoted(
    records: List[Dict[str, Any]],
    *,
    exclude_expired: bool = True,
    exclude_reviewed: bool = True,
    seen_path: Optional[Path] = None,
    seen_keys: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """promoted / TTL失効 / 既読・却下済みの3軸を適用する（``read_unpromoted`` と
    ``filter_actionable`` の共有 predicate。#405 round4 是正で分離・単一ソース化）。

    exclude_expired（既定 True）は TTL 失効レコードを候補から外す（#442。古い修正
    候補は腐る — TTL が品質フィルタとして機能する）。失効判定は ``expired`` フラグだけ
    でなく ``detected_at`` からの age 再計算（``is_effectively_expired`` / #89）で行う。
    標準フロー（dry-run → drain）は ``mark_expired`` を通らずフラグが書かれないため、
    read 時に age を導出しないと 45 日超の腐った signal が永久に落ちないからである
    （write 非依存）。

    exclude_reviewed（既定 True・#185 claim3）は既読ストア（correction_review_seen.jsonl）
    に記録済み（decision="promoted"/"rejected" どちらも）の signal_key を候補から外す。
    ``daily_review.record_reviewed(decision="rejected")`` は weak_signal 側の ``promoted``
    フラグを立てない（却下＝昇格しない、が正しい仕様）ため、これを見ないと reject 済みの
    signal が永遠に残り「reject しても件数が減らない」非対称が起きる。TTL 失効（#89）と
    同じ **read 時導出**方針（forward write に頼らない）。``seen_path`` はテスト isolation
    用の明示パス（未指定は既読ストアの production 既定＝union read）。``seen_keys`` を渡すと
    既読ストアの read をスキップしてそのまま使う（#405 round5 [Must]2: daily_review が
    ``build_review`` で既に読んだ既読集合を ``reviewed_keys_count`` 表示用に保持しており、
    ``filter_actionable`` へ委譲する際に二重 read を避けるため）。``seen_keys`` 指定時は
    ``seen_path`` は無視する。
    """
    out = [r for r in records if not r.get("promoted")]
    if exclude_expired:
        out = [r for r in out if not is_effectively_expired(r)]
    if exclude_reviewed:
        if seen_keys is not None:
            seen = seen_keys
        else:
            from correction_semantic.daily_review import read_reviewed_keys

            seen = read_reviewed_keys(seen_path)
        if seen:
            out = [r for r in out if r.get("signal_key") not in seen]
    return out


def read_unpromoted(
    weak_signals_path: Optional[Path] = None,
    channel: Optional[str] = None,
    exclude_expired: bool = True,
    exclude_reviewed: bool = True,
    seen_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """未昇格（promoted=False）の weak_signal レコードを返す（全 PJ・非スコープ）。

    channel を渡すとそのチャネルだけに絞る（例: "llm_judge" で #431 のバッチ判定のみ）。
    exclude_expired/exclude_reviewed の意味は ``_filter_unpromoted`` を参照。後方互換が
    必要な呼び出しはそれぞれ False で無視できる。

    PJ スコープが要る呼び出しは ``filter_actionable``（本モジュール）を使うこと
    （本関数は全 PJ 横断のまま維持する既存呼び出し元があるため PJ 引数を追加しない）。
    """
    recs = read_signals(weak_signals_path)
    out = _filter_unpromoted(
        recs,
        exclude_expired=exclude_expired,
        exclude_reviewed=exclude_reviewed,
        seen_path=seen_path,
    )
    if channel is not None:
        out = [r for r in out if r.get("channel") == channel]
    return out


def is_machinery_signal(rec: Dict[str, Any]) -> bool:
    """weak_signal の provenance.text が harness 注入の機構ターンか判定する（#443 PR2-a）。

    朝の提示に委譲メッセージ等の machinery（`<teammate-message` 等）が混入する問題
    （ADR-054 B-a・実測で朝の候補 300 件中 47 件が該当）を read 時に塞ぐ単一述語。判定は
    ``rl_common.detection.is_machinery_prompt`` を単一ソースとする（文字列 allowlist を
    新設しない）。text を持たない決定論チャネル（permission_deny 等）は機構ターンではない
    （False・空文字は判定対象にしない）。
    """
    from rl_common.detection import is_machinery_prompt

    prov = rec.get("provenance") or {}
    text = prov.get("text") or ""
    return bool(text) and is_machinery_prompt(text)


def _filter_actionable_without_machinery(
    records: List[Dict[str, Any]],
    pj_slug: Optional[str],
    *,
    exclude_reviewed: bool = True,
    seen_path: Optional[Path] = None,
    seen_keys: Optional[Set[str]] = None,
    marker_base: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """``filter_actionable`` から machinery 除外だけを抜いたパイプライン（内部共有 helper）。

    ``filter_actionable`` 本体と ``machinery_exclusion_stats``（除外件数の集計）が同じ
    「machinery を除く前の would-be-actionable 母集団」を必要とするため、read+scope の
    パスを1箇所に集約する（コピー慣習の partial fix を避ける）。
    """
    out = _filter_unpromoted(
        records,
        exclude_expired=True,
        exclude_reviewed=exclude_reviewed,
        seen_path=seen_path,
        seen_keys=seen_keys,
    )
    if pj_slug is None:
        return out
    from correction_semantic.bootstrap_backlog import _exclude_bootstrap_consumed

    return _exclude_bootstrap_consumed(out, pj_slug, marker_base=marker_base)


def machinery_exclusion_stats(
    records: List[Dict[str, Any]],
    pj_slug: Optional[str],
    *,
    exclude_reviewed: bool = True,
    seen_path: Optional[Path] = None,
    seen_keys: Optional[Set[str]] = None,
    marker_base: Optional[Path] = None,
) -> Dict[str, Any]:
    """``filter_actionable`` が machinery のみを理由に除外した件数を channel 別に集計する（#443）。

    対象は「machinery でなければ actionable だったはずのレコード」（promoted/TTL/reviewed/
    bootstrap 消化の軸は通過済みだが machinery である集合）。除外は黙って減らさない
    （silence != evaluated）ため、呼び出し側（digest/queue/observability 等）は本関数で
    件数を取り、既存の返り値 dict にキーとして載せる。新しい store は作らない（read-only・
    純関数）。

    Returns: ``{"total": int, "by_channel": {channel: count, ...}}``
    """
    would_be = _filter_actionable_without_machinery(
        records,
        pj_slug,
        exclude_reviewed=exclude_reviewed,
        seen_path=seen_path,
        seen_keys=seen_keys,
        marker_base=marker_base,
    )
    total = 0
    by_channel: Dict[str, int] = {}
    for r in would_be:
        if is_machinery_signal(r):
            total += 1
            ch = r.get("channel") or "(unknown)"
            by_channel[ch] = by_channel.get(ch, 0) + 1
    return {"total": total, "by_channel": by_channel}


def filter_actionable(
    records: List[Dict[str, Any]],
    pj_slug: Optional[str],
    *,
    exclude_reviewed: bool = True,
    seen_path: Optional[Path] = None,
    seen_keys: Optional[Set[str]] = None,
    marker_base: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """指定 PJ に**既にスコープ済み**の weak_signal レコード群から actionable 分だけ返す。

    PR #405 round4 是正: capture（``audit/sections_capture.py``）/ weak_signals section
    （``audit/sections_weak_signals.py``）/ icebox_reconcile
    （``icebox_reconcile._eval_weak_signals_unprocessed_count``）の3箇所が、それぞれ別々に
    「actionable（行動を促す・閾値判定に使う）」母集団を組み立てており、除外軸の一部が
    欠落する非対称が3回連続で見つかった（TTL失効・既読/却下済み・bootstrap消化済みの
    組み合わせが reader ごとに違った）。

    round5 [Must]2 是正: round4 では上記3箇所しか本関数を経由しておらず、
    ``fleet.queue_materials._scoped_kept_signals`` / ``correction_semantic.daily_review._read_new``
    が独自実装のまま残っていた（除外軸が増えるたび再分裂する構造）。全5 reader
    （queue_materials / daily_review / capture / weak_signals section / icebox_reconcile）が
    本関数を単一ソースとして経由する。

    read（store からのレコード取得）と pj_slug スコープは呼び出し側の責務のまま維持する
    （production union read / icebox の ``data_dir`` 起点 union read / legacy no-slug
    フォールバックなど reader ごとに read/scope の流儀が異なるため、ここで再度 pj_slug
    フィルタはしない — ``correction_semantic.bootstrap_backlog._exclude_bootstrap_consumed``
    と同じ契約: ``pj_slug`` は bootstrap marker 探索の基準としてのみ使う）。

    適用する除外 predicate（適用順は無関係・全て独立）:
      - promoted 済み
      - TTL 失効（#89 ``is_effectively_expired``・read 時導出）
      - （``exclude_reviewed=True`` のときのみ）既読・却下済み（#185）
      - bootstrap で判断済み（#94・marker 設置以前に detected した weak）
      - machinery（#443 PR2-a・``is_machinery_signal``。harness 注入の委譲メッセージ等。
        書込側修理（A2, #431）済みの既存在庫にも効く read 時除外）

    ``exclude_reviewed`` の既定は True（安全側＝厳密な actionable）。呼び出し側が既読を
    独立軸として別途集計する場合（例: 「未昇格 N 件（うち未読 M 件）」表示）は False を渡す。
    ``seen_keys`` は呼び出し側が既に読み終えた既読集合をそのまま使う（``_filter_unpromoted``
    を参照。二重 read 回避）。

    ``pj_slug=None`` 契約（#405 round6 [Must]1）: bootstrap marker 探索の基準となる PJ slug が
    解決できない呼び出し元（例: ``sections_weak_signals`` の current_slug 未解決フォールバック）
    向けに、promoted / TTL / reviewed の3軸は通常どおり適用しつつ **bootstrap 消化除外だけを
    スキップ**する。これにより、呼び出し側が「slug 解決可否で分岐し、片方だけ TTL を独自適用
    する」回避策（TTL predicate の仕様変更に追随しない独自実装の温床）を作らずに済み、常に
    本関数を単一の呼び出し口にできる。machinery 軸は pj_slug の有無に関わらず常時適用する。

    除外件数の可視化は ``machinery_exclusion_stats``（本モジュール）を別途呼ぶ（silence !=
    evaluated・#443）。
    """
    out = _filter_actionable_without_machinery(
        records,
        pj_slug,
        exclude_reviewed=exclude_reviewed,
        seen_path=seen_path,
        seen_keys=seen_keys,
        marker_base=marker_base,
    )
    return [r for r in out if not is_machinery_signal(r)]


def _match_key(pj_slug: Any, provenance: Optional[Dict[str, Any]]) -> tuple:
    """(pj_slug, source_path, line_no) — signal↔idiom を突合する物理キー。

    batch.py は同一発話から WeakSignal と CorrectionIdiom を同じ provenance で作るので、
    この3要素一致で signal→idiom を対応付けられる（#463）。
    """
    prov = provenance or {}
    return (pj_slug, prov.get("source_path", ""), prov.get("line_no", ""))


def resolve_idiom_keys_for_signals(
    signal_keys: List[str],
    *,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
) -> Dict[str, str]:
    """指定 signal_key → 対応する idiom_key の対応表を provenance 突合で解決する（#463）。

    `--promote-weak` 承認時に、昇格したシグナルに対応する idiom を confirmed 化するための
    配線。signal と idiom は (pj_slug, source_path, line_no) を共有する（batch.py が同一
    provenance で両方を作る）ため、その物理キーで突合する。promote 済み（promoted=True）の
    シグナルでも解決できる（read_signals は全件読む）。

    対応 idiom が無いシグナル（rephrase 等）・未知 signal_key は結果に含めない。

    Returns: {signal_key: idiom_key, ...}
    """
    from correction_semantic.store import read_idioms

    target = set(k for k in (signal_keys or []) if k)
    if not target:
        return {}

    # signal_key → (pj_slug, source_path, line_no)
    sig_match: Dict[str, tuple] = {}
    for r in read_signals(weak_signals_path):
        key = r.get("signal_key")
        if key in target:
            sig_match[key] = _match_key(r.get("pj_slug"), r.get("provenance"))

    # (pj_slug, source_path, line_no) → idiom_key
    idiom_by_match: Dict[tuple, str] = {}
    for r in read_idioms(idioms_path):
        mk = _match_key(r.get("pj_slug"), r.get("provenance"))
        idiom_key = r.get("idiom_key")
        if idiom_key:
            idiom_by_match.setdefault(mk, idiom_key)

    out: Dict[str, str] = {}
    for key, mk in sig_match.items():
        idiom_key = idiom_by_match.get(mk)
        if idiom_key:
            out[key] = idiom_key
    return out


def _correction_message(rec: Dict[str, Any]) -> str:
    """weak_signal の provenance から corrections の message 本文を組み立てる。

    #99: text/reason を持たない決定論チャネル（permission_deny 等）は channel 別の actionable
    テキスト（review_channels.signal_text）に fallback し、message=channel 名の空 correction を防ぐ。
    """
    prov = rec.get("provenance") or {}
    text = prov.get("text") or ""
    reason = prov.get("reason") or ""
    if text and reason:
        return f"{text}（{reason}）"
    if text or reason:
        return text or reason
    # 決定論チャネル: channel 別 actionable テキストへ fallback（最後の砦が channel 名）。
    from correction_semantic.review_channels import signal_text

    return signal_text(rec) or rec.get("channel", "weak_signal")


def _build_correction_record(
    rec: Dict[str, Any],
    project_path: str,
    *,
    source: str = "reflect_confirmed",
    idiom_key: Optional[str] = None,
) -> Dict[str, Any]:
    """weak_signal → corrections.jsonl の human-source レコードへ変換する。

    source: "reflect_confirmed"（人間確認・#431）/ "idiom_dict"（自動昇格・ADR-047）。
            いずれも provenance_weight.HUMAN_SOURCES のメンバーで重み 1.0。
    idiom_key: source="idiom_dict" のとき確認済み idiom_key を残す（安全弁③で巻き戻せる）。
    """
    prov = rec.get("provenance") or {}
    now = datetime.now(timezone.utc).isoformat()
    out = {
        "correction_id": new_correction_id(),
        "correction_type": "semantic_idiom",
        "matched_patterns": [],
        "message": _correction_message(rec),
        "last_skill": None,
        "preceding_tool_calls": None,
        "confidence": 0.9,
        "sentiment": "correction",
        "routing_hint": None,
        "guardrail": False,
        # #475 §6: "applied" は反映先ファイルに該当行が実在すると確認できたときだけ
        # reflect.py の update_reflect_status が付ける。ここは「昇格済み・反映先未定」の
        # "promoted" を書く（旧 "applied" 直書きは §6.1 が塞ぐ迂回口だった）。
        "reflect_status": "promoted",
        "extracted_learning": None,
        # #593: 書込境界で worktree 安全 slug に正規化（幻PJ slug 混入防止）。
        "project_path": _normalize_project_path(project_path),
        # human-source: フェーズ昇格カウント対象（provenance_weight.HUMAN_SOURCES）
        "source": source,
        "timestamp": now,
        "session_id": rec.get("session_id", ""),
        "weak_signal_key": rec.get("signal_key"),
        "weak_signal_channel": rec.get("channel"),
        "weak_signal_provenance": prov,
        # 安全弁③: revoke で巻き戻せるよう全レコードに invalidated を初期 False で持たせる。
        "invalidated": False,
    }
    if source == "idiom_dict":
        # provenance を潰さない: proxy 再適用だったことを後から監査・一括 invalidate できる。
        out["promoted_by"] = "idiom_dict"
        out["idiom_key"] = idiom_key
    return out


def _rewrite_promoted(
    weak_signals_path: Path,
    promoted_keys: Set[str],
) -> None:
    """weak_signals.jsonl の該当 signal_key 行を promoted=True にして原子的に書き直す。

    該当行が無ければ書き換えない（union mark で legacy/canonical を順に走査するとき、key を
    持たない dir のファイルを無駄に rewrite しない・#46）。明示の単一 path を読む（hermetic）。
    """
    if not weak_signals_path.exists() or not promoted_keys:
        return
    recs = read_signals(weak_signals_path)
    changed = False
    for r in recs:
        if r.get("signal_key") in promoted_keys and not r.get("promoted"):
            r["promoted"] = True
            changed = True
    if not changed:
        return
    new_content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    weak_signals_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(weak_signals_path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, weak_signals_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _mark_promoted(
    weak_signals_path: Optional[Path],
    promoted_keys: Set[str],
) -> None:
    """昇格した signal を promoted=True にマークする（再昇格防止）。

    #46 read 層拡張: ``weak_signals_path=None``（production）は legacy も含む union dir すべてで
    該当 signal_key を promoted=True に書き換える。read は union（legacy 可視）なのに mark を
    canonical だけに書くと legacy record の promoted=False が残り **毎 run 再昇格**してしまう
    （重複 corrections の avalanche）。これは新規 record の relocate / 物理 merge ではなく既存
    record の状態遷移（promotion セマンティクスが必須とする維持書込）なので read-layer-only
    方針と矛盾しない。明示 path 指定時はそのファイルのみ（hermetic）。
    """
    if weak_signals_path is not None:
        _rewrite_promoted(Path(weak_signals_path), promoted_keys)
        return
    for p in _iter_read_store_paths(_WS_STORE_NAME):
        _rewrite_promoted(p, promoted_keys)


def _skip_reason(rec: Optional[Dict[str, Any]], *, reviewed: bool) -> str:
    """requested だが昇格候補に入らなかった signal_key の理由を判定する（#326）。

    read_unpromoted の除外条件（promoted / expired / reviewed）と対称に判定する。
    rec が None（signal_key がそもそも weak_signals に存在しない）は "not_found"。
    """
    if rec is None:
        return "not_found"
    if rec.get("promoted"):
        return "already_promoted"
    if is_effectively_expired(rec):
        return "expired"
    if reviewed:
        return "already_reviewed"
    return "unknown"


def promote_signals(
    signal_keys: List[str],
    *,
    weak_signals_path: Optional[Path] = None,
    corrections_path: Optional[Path] = None,
    project_path: str = "",
    source: str = "reflect_confirmed",
    idiom_keys: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
    seen_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """指定 signal_key の未昇格 weak_signal を corrections へ昇格する。

    - corrections.jsonl に human-source レコードを追記
      （source="reflect_confirmed"=人間確認 / "idiom_dict"=自動昇格・ADR-047）
    - 昇格した weak_signal を promoted=True にマーク（二重昇格防止）
    - dry-run はどちらにも一切書かない（昇格するはずだった件数だけ返す）

    idiom_keys: source="idiom_dict" のとき signal_key → 確認済み idiom_key の対応表。
                昇格レコードに idiom_key を残し、安全弁③（revoke）で巻き戻せるようにする。

    #326: requested と promoted の件数差が silent failure だった（どの key がなぜ落ちたか
    分からない）。requested（重複除去した要求件数）/ promoted_keys（実際に昇格した signal_key
    一覧）/ skipped（昇格されなかった signal_key と理由 ``not_found`` / ``already_promoted`` /
    ``expired`` / ``already_reviewed`` の一覧）を追加する（既存キーは維持・純加算）。

    Returns:
        {"promoted": int, "dry_run": bool, "requested": int,
         "promoted_keys": [str, ...], "skipped": [{"signal_key": str, "reason": str}, ...]}
    """
    # dict.fromkeys で順序を保ったまま重複除去する（requested は一意な要求件数）。
    requested_keys = list(dict.fromkeys(k for k in (signal_keys or []) if k))
    target = set(requested_keys)
    # #46 read 層拡張: weak_signals_path=None（production）は read_unpromoted 経由で
    # canonical + legacy を union read し legacy 昇格候補を拾う。明示 path は単一（hermetic）。
    candidates = [
        r for r in read_unpromoted(weak_signals_path, seen_path=seen_path)
        if r.get("signal_key") in target
    ]
    candidate_keys = {r.get("signal_key") for r in candidates}

    def _build_skipped() -> List[Dict[str, str]]:
        # 理由判定は全件読み（promoted/expired 済みでも rec を引けるようにする）+ 既読集合。
        all_recs = read_signals(weak_signals_path)
        by_key: Dict[str, Dict[str, Any]] = {}
        for r in all_recs:
            k = r.get("signal_key")
            if k and k not in by_key:
                by_key[k] = r
        from correction_semantic.daily_review import read_reviewed_keys

        reviewed_keys = read_reviewed_keys(seen_path)
        out: List[Dict[str, str]] = []
        for k in requested_keys:
            if k in candidate_keys:
                continue
            out.append({
                "signal_key": k,
                "reason": _skip_reason(by_key.get(k), reviewed=k in reviewed_keys),
            })
        return out

    if dry_run:
        return {
            "promoted": len(candidates),
            "dry_run": True,
            "requested": len(requested_keys),
            "promoted_keys": sorted(candidate_keys),
            "skipped": _build_skipped(),
        }

    if not candidates:
        return {
            "promoted": 0,
            "dry_run": False,
            "requested": len(requested_keys),
            "promoted_keys": [],
            "skipped": _build_skipped(),
        }

    # corrections に human-source レコードを専用境界から追記する。
    if corrections_path is None:
        import rl_common as _rc

        corrections_path = Path(_rc.DATA_DIR) / "corrections.jsonl"
    corrections_path = Path(corrections_path)
    corrections_path.parent.mkdir(parents=True, exist_ok=True)

    idiom_keys = idiom_keys or {}
    promoted_keys: Set[str] = set()
    for rec in candidates:
        key = rec.get("signal_key")
        record = _build_correction_record(
            rec, project_path, source=source, idiom_key=idiom_keys.get(key),
        )
        append_result = append_correction_record(corrections_path, record)
        if append_result.status == "appended" and key:
            promoted_keys.add(key)
        elif append_result.status != "appended":
            print(
                f"[evolve-anything:correction] record not saved: {append_result.status}"
                + (f" ({append_result.reason})" if append_result.reason else ""),
                file=sys.stderr,
            )

    # weak_signal を promoted=True にマーク（再昇格防止・union dir 全て / hermetic）
    _mark_promoted(weak_signals_path, promoted_keys)

    return {
        "promoted": len(promoted_keys),
        "dry_run": False,
        "requested": len(requested_keys),
        "promoted_keys": sorted(promoted_keys),
        "skipped": _build_skipped(),
    }


def invalidate_idiom_corrections(
    idiom_keys: Set[str],
    *,
    corrections_path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """指定 idiom_key 由来の idiom_dict 昇格 corrections を invalidated=True に原子的 rewrite（安全弁③）。

    revoke（ADR-047）で confirmed を取り消したとき、その idiom_key で自動昇格された corrections を
    invalidated=True にして count_human_corrections から除外する（フェーズ進捗が正しく巻き戻る）。
    promoted_by="idiom_dict" かつ idiom_key が一致するレコードのみが対象（reflect_confirmed や
    他 idiom_key のレコードは不変）。weak_signals 側の promoted=True は維持（再提示しない）。

    dry-run ゼロ書込: dry_run=True なら一切ファイルに触れず「invalidate するはずだった件数」を返す。

    Returns: {"invalidated": int, "dry_run": bool}
    """
    target = set(k for k in (idiom_keys or set()) if k)
    if corrections_path is None:
        import rl_common as _rc

        corrections_path = Path(_rc.DATA_DIR) / "corrections.jsonl"
    corrections_path = Path(corrections_path)
    if not target or not corrections_path.exists():
        return {"invalidated": 0, "dry_run": dry_run}

    if dry_run:
        text = corrections_path.read_text(encoding="utf-8", errors="replace")
        _, matched, _ = _invalidate_idiom_text(text, target, mutate=False)
        return {"invalidated": matched, "dry_run": True}

    reason = fcntl_unsupported_reason()
    if reason is not None:
        return {"invalidated": 0, "dry_run": False, "error": reason}

    with corrections_write_lock(corrections_path):
        text = corrections_path.read_text(encoding="utf-8", errors="replace")
        new_content, matched, touched = _invalidate_idiom_text(text, target, mutate=True)
        if matched:
            assert_no_unexpected_content_loss(
                snapshot_identities(text),
                snapshot_identities(new_content),
                touched_before=snapshot_identities("\n".join(touched)),
            )
            atomic_write_text_preserving_mode(corrections_path, new_content)
    return {"invalidated": matched, "dry_run": False}


def _invalidate_idiom_text(text: str, target: Set[str], *, mutate: bool):
    recs: List[Any] = []
    touched: List[str] = []
    matched = 0
    for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                recs.append(raw_line)
                continue
            if (
                r.get("promoted_by") == "idiom_dict"
                and r.get("idiom_key") in target
                and not r.get("invalidated")
            ):
                matched += 1
                touched.append(raw_line)
                if mutate:
                    r["invalidated"] = True
                    recs.append(r)
                    continue
            recs.append(raw_line)
    return "".join(
        (json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n"
        for r in recs
    ), matched, touched
