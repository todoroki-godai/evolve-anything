"""correction_semantic.correction_backlog — 修正在庫（#514）。

`daily_review.build_review` は前回 evolve 以降の**新規** weak_signal のみを見るため、
それ以前に ``reflect_status=promoted`` まで昇格済みの corrections.jsonl レコード
（＝反映先が未定のまま溜まった在庫）には朝の確認導線が無かった（issue #514・実測
2026-08-18 時点で有効在庫174件・最大経過67日）。本モジュールは corrections.jsonl を
直接読み、当該 PJ の有効な在庫（``reflect_status == "promoted"`` かつ ``invalidated``
が真でない）を timestamp 昇順（古い順）で返す。**読み取り専用**（ファイル書込みは
一切行わない）。

slug 突合は自作しない（設計指定・#514）: corrections の ``project_path`` は実コーパスで
フルパス／bare slug が混在するため ``fleet.queue_materials._correction_slug``
（``project_name_from_dir`` 経由の正規化・新方式を発明しない）で bare slug に畳んでから、
旧名 PJ の alias 畳み込みは ``store_read_union.pj_slug_match``（``canonical_pj_slug``
経由）で行う（pitfall_pj_rename_legacy_slug_orphan と同型 — 畳まないと在庫の一部
＝旧名 PJ 分が永久に拾えなくなる）。

``source_correction_id`` は実データに直接のフィールドが無い（0/182件）ため
``memory_temporal.make_source_correction_id(session_id, timestamp)`` で組み立てる
（``reflect.py --apply`` が期待する形式と同一）。``routing_hint`` は実測で全件 None
のため出力に含めない。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from store_read_union import pj_slug_match as _pj_slug_match

CORRECTION_BACKLOG_MAX_ITEMS = 3


def _default_corrections_path() -> Path:
    """corrections.jsonl の正準パスを ADR-042 resolver 経由で解決する。"""
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従・daily_review.default_seen_path と同方針）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / "corrections.jsonl"


def _parse_ts(s: Any) -> Optional[datetime]:
    """ISO8601 timestamp を tz-aware datetime にする（`Z` / `+00:00` 終端を吸収）。

    pitfall_iso8601_lexical_compare_tz_suffix: 辞書順比較は同一 instant でも終端表記が
    違うと不一致になるため、必ずパースしてから比較する。パース不能 / 欠落 → None。
    """
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_backlog_item(rec: Dict[str, Any]) -> Dict[str, Any]:
    """corrections レコード → 朝の確認が消費する在庫アイテムへ整形する。"""
    from memory_temporal import make_source_correction_id

    ts_str = rec.get("timestamp") or ""
    session_id = rec.get("session_id") or ""
    ts = _parse_ts(ts_str)
    age_days = (datetime.now(timezone.utc) - ts).days if ts is not None else None
    return {
        "source_correction_id": make_source_correction_id(session_id, ts_str),
        "message": rec.get("message", ""),
        "age_days": age_days,
        "timestamp": ts_str,
        "session_id": session_id,
    }


def _read_eligible_backlog_records(
    corrections_path: Optional[Path],
    *,
    records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """全 PJ の有効な在庫を1回の store read で返す。

    raw read（ファイル不在→空・``OSError``→空・壊れた行→無言 skip）は
    ``fleet.queue_materials.read_corrections_records_with_health`` を単一ソースとして再利用する
    （新しい read 経路を発明しない・#533）。読取不能・壊れた行の可視化（read health）は
    ``fleet.queue_materials.corrections_read_health`` が ``build_queue_result`` 経由で担う。

    ``records``（``build_queue_result`` が既に1回 read 済みのレコード列）を渡すと再 read しない
    （#538 round2 [Must]1 — probe 時の read と本集計の read が別だと、両者の間にファイルが
    変化した場合に health と集計結果が食い違うスナップショット不一致が起きる）。
    """
    if records is not None:
        raw = records
    else:
        from fleet.queue_materials import read_corrections_records_with_health

        path = Path(corrections_path) if corrections_path is not None else _default_corrections_path()
        raw, _health = read_corrections_records_with_health(path)

    out: List[Dict[str, Any]] = []
    for rec in raw:
        if rec.get("reflect_status") != "promoted":
            continue
        if rec.get("invalidated"):
            continue
        out.append(rec)

    return out


def _eligible_backlog_records(
    pj_slug: str,
    corrections_path: Optional[Path],
    *,
    records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """当該 PJ の有効な在庫（promoted かつ invalidated でない）を timestamp 昇順で返す。

    ``build_correction_backlog`` / ``backlog_with_remaining`` 共通の読み取り本体
    （フォーマット前・件数計算用に未整形のまま渡す）。
    """
    from fleet.queue_materials import _correction_slug

    out = [
        rec
        for rec in _read_eligible_backlog_records(corrections_path, records=records)
        if _pj_slug_match(_correction_slug(rec.get("project_path")), pj_slug)
    ]

    # timestamp 欠落/パース不能は末尾（epoch 扱い）に送る。全欠落でも例外にしない。
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda r: _parse_ts(r.get("timestamp")) or epoch)
    return out


def correction_backlog_counts_by_pj(
    *,
    corrections_path: Optional[Path] = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """有効な修正在庫を canonical PJ slug 別に1回の read で集計する（#515）。

    ``records``（既に read 済みのレコード列）を渡すと再 read しない（#538 round2 [Must]1）。
    """
    from fleet.queue_materials import _correction_slug
    from pj_slug import canonical_pj_slug

    counts: Counter[str] = Counter()
    for rec in _read_eligible_backlog_records(corrections_path, records=records):
        slug = canonical_pj_slug(_correction_slug(rec.get("project_path")))
        if slug:
            counts[str(slug)] += 1
    return dict(sorted(counts.items()))


def build_correction_backlog(
    pj_slug: str,
    *,
    corrections_path: Optional[Path] = None,
    max_items: int = CORRECTION_BACKLOG_MAX_ITEMS,
) -> List[Dict[str, Any]]:
    """当該 PJ の修正在庫を古い順に最大 max_items 件返す（#514）。読み取り専用。

    母集団: ``reflect_status == "promoted"`` かつ ``invalidated`` が真でないもの。
    ``max_items=None`` なら全件返す。
    """
    records = _eligible_backlog_records(pj_slug, corrections_path)
    top = records if max_items is None else records[:max_items]
    return [_format_backlog_item(r) for r in top]


def backlog_with_remaining(
    pj_slug: str,
    *,
    corrections_path: Optional[Path] = None,
    max_items: int = CORRECTION_BACKLOG_MAX_ITEMS,
) -> Tuple[List[Dict[str, Any]], int]:
    """``build_review`` 統合用: 1 回の read で (backlog, remaining) を返す。

    ``build_correction_backlog`` を呼んでから件数計算のために再度ファイルを読むと、
    読みの間に store が更新された場合に返す remaining と実際の backlog が食い違う
    race を生む（codex [Should]1 是正・daily_review._read_new と同方針）。ここで
    ``_eligible_backlog_records`` を 1 回だけ呼び、スライス前後の長さ差で remaining
    を出す。
    """
    records = _eligible_backlog_records(pj_slug, corrections_path)
    top = records if max_items is None else records[:max_items]
    backlog = [_format_backlog_item(r) for r in top]
    remaining = max(0, len(records) - len(backlog))
    return backlog, remaining
