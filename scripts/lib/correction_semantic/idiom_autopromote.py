"""correction_semantic.idiom_autopromote — human-confirmed idiom の自動昇格（ADR-047・#447）。

人間が #446 の「今日の修正確認」で一度「はい」と承認した idiom テキスト（confirmed=True）に
**一致する新規未昇格 weak_signal** を、人間再確認なしで corrections へ自動昇格する
（source="idiom_dict"・重み 1.0 の human-source）。

> 不変条件（雪崩防止・ADR-047）: confirmed=True が 1 件も無ければ promoted=0。
> 現環境の 313 idiom は全件未確認（provenance.judge="llm_haiku"）なので、起動時点で
> 自動昇格は一切発動しない。confirmed は #446 の人間 y/n でしか立たない。

照合の単位（テキスト一致まで一般化）: confirmed の単位は「pj_slug × idiom テキスト」。
idiom_key（idiom + 物理キーの安定ハッシュ）は出現ごとに別値（dedup 用）なので、これを
照合キーにすると **同じ言い回しの新規発話**が別 idiom_key の新 record（unconfirmed）になり
永遠にマッチしない（同 phys のシグナルは #446 の「はい」時点で promoted 済み）→ 構造的 no-op。
承認済みパターンの**新規再発を機械再適用**するのが本機能の目的なので、テキスト一致まで
一般化する。新規 weak_signal の phys（source_path:line_no）→ その idiom record（judge が
何であれ）→ その idiom テキストが confirmed テキスト集合にあれば昇格対象。FP は安全弁3点
（daily_cap / surface / revoke）で吸収する（ADR-047 採用案 C）。corrections には昇格元の
idiom_key を残し provenance を保つ（安全弁③の一括 invalidate に使える）。

安全弁①（daily_cap）: 1 回の confirmed 化で大量昇格する暴走を量で抑える。daily_cap 件で
打ち切り、超過分は ``capped`` として返し次回 run に持ち越す（未昇格のまま残る）。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: dry_run=True なら promote_signals が
corrections / weak_signals に一切触れない（最下層 write ゲート）。
PJ slug スコープ（全PJ共通 DATA_DIR pitfall）: 当該 cwd の PJ slug の weak_signal のみ対象。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from correction_semantic.idiom_filter import idiom_eligible
from correction_semantic.promote import read_unpromoted
from correction_semantic.store import (
    normalize_idiom_text,
    read_confirmed_idiom_texts,
    read_idioms,
)


def _phys(prov: Dict[str, Any]) -> str:
    return f"{prov.get('source_path', '')}:{prov.get('line_no', '')}"


def _phys_to_idiom(pj_slug: str, idioms_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """当該 PJ slug の idiom record の phys_key → {idiom テキスト, idiom_key} 対応表。

    新規 weak_signal はその phys（source_path:line_no）を共有する idiom record を持つ
    （batch.py が同じ prov で WeakSignal と CorrectionIdiom を作るため）。この表で
    シグナルから idiom テキスト（confirmed 判定用）と idiom_key（provenance 記録用）を引く。
    confirmed/unconfirmed を問わず全 record を含める（confirmed 判定はテキスト集合で行う）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for r in read_idioms(idioms_path):
        if r.get("pj_slug") != pj_slug:
            continue
        phys = _phys(r.get("provenance") or {})
        idiom = r.get("idiom")
        key = r.get("idiom_key")
        if phys and idiom:
            out.setdefault(phys, {"idiom": idiom, "idiom_key": key})
    return out


def autopromote(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    corrections_path: Optional[Path] = None,
    project_path: str = "",
    daily_cap: int = 10,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """confirmed idiom テキストに一致する新規 weak_signal を idiom_dict で自動昇格する。

    Returns（常時 emit。対象 0 でも全キーを置く）:
      {
        "promoted": int,                 # 今 run で昇格した件数（#448 が int で読む契約）
        "capped": int,                   # daily_cap 超過で持ち越した件数（安全弁①）
        "promoted_idioms": [str, ...],   # 昇格した idiom 本文の一覧（安全弁② surface 用）
        "slug": str,
        "dry_run": bool,
      }
    """
    confirmed_texts = read_confirmed_idiom_texts(pj_slug, idioms_path)

    # 不変条件: confirmed が 1 件も無ければ即 promoted=0（雪崩防止）。
    if not confirmed_texts:
        return {
            "promoted": 0, "capped": 0, "promoted_idioms": [],
            "slug": pj_slug, "dry_run": dry_run,
        }

    phys_to_idiom = _phys_to_idiom(pj_slug, idioms_path)
    unpromoted = read_unpromoted(weak_signals_path, channel="llm_judge")

    matched: List[Dict[str, Any]] = []
    for r in unpromoted:
        if r.get("pj_slug") != pj_slug:
            continue
        info = phys_to_idiom.get(_phys(r.get("provenance") or {}))
        if info is None:
            continue
        idiom_text = info["idiom"]
        # #527 防御ゲート: ガード導入前に confirmed 化された過汎用 idiom（極短/相槌/断片）が
        # 既存ストアに残っていても自動昇格しない。ingest 側の guard と二重で FP を止める。
        if not idiom_eligible(idiom_text):
            continue
        # confirmed_texts は read_confirmed_idiom_texts が正規化済みで返す。候補側も
        # 同じ normalize_idiom_text を通して照合し、正規化ロジックを共有する（#462）。
        if normalize_idiom_text(idiom_text) not in confirmed_texts:
            continue
        matched.append({
            "signal_key": r.get("signal_key"),
            "idiom_key": info["idiom_key"],
            "idiom": idiom_text,
        })

    # 安全弁①: daily_cap 件で打ち切り、超過は capped で持ち越し。
    cap = max(0, int(daily_cap))
    selected = matched[:cap]
    capped = max(0, len(matched) - len(selected))

    signal_keys = [m["signal_key"] for m in selected if m["signal_key"]]
    idiom_keys_map = {m["signal_key"]: m["idiom_key"] for m in selected if m["signal_key"]}
    promoted_idioms = [m["idiom"] for m in selected if m.get("idiom")]

    if not signal_keys:
        return {
            "promoted": 0, "capped": capped, "promoted_idioms": [],
            "slug": pj_slug, "dry_run": dry_run,
        }

    from correction_semantic.promote import promote_signals

    res = promote_signals(
        signal_keys,
        weak_signals_path=weak_signals_path,
        corrections_path=corrections_path,
        project_path=project_path,
        source="idiom_dict",
        idiom_keys=idiom_keys_map,
        dry_run=dry_run,
    )

    return {
        "promoted": int(res.get("promoted", 0)),
        "capped": capped,
        "promoted_idioms": promoted_idioms,
        "slug": pj_slug,
        "dry_run": bool(res.get("dry_run", dry_run)),
    }
