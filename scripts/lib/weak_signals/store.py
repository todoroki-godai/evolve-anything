"""weak_signals.store — weak_signals.jsonl の append/read（#432）。

レコードスキーマ（#431 のバッチ LLM 判定も将来このレーンを共有するので汎用にする）:
- ``channel``      — 検出チャネル名（CHANNELS のいずれか / 将来は llm_judge 等も）
- ``provenance``   — 検出根拠（source_path / line_no / file_path / detector 等の evidence dict）
- ``detected_at``  — 検出時刻（ISO8601 UTC）
- ``session_id``   — 由来セッション
- ``pj_slug``      — ADR-031 準拠 slug（read 側照合の強制。全PJ共通 DATA_DIR 単一ファイル pitfall）
- ``promoted``     — 昇格状態（初期 False。reflect 確認後に True へ）
- ``signal_key``   — 同一シグナルの dedup キー（channel + provenance の安定ハッシュ）
- ``expired``      — TTL 失効状態（初期 False。detected_at から TTL_DAYS 超で True / #442）
- ``expired_at``   — 失効マーク時刻（ISO8601 UTC / null。weak_signals.ttl.mark_expired が設定）

dry-run 書き込みゼロ（pitfall_dryrun_stateful_store_write）: append_signals は ``dry_run``
を受け、True なら **一切ファイルに触れない**（最下層 write までゲートを貫通させる）。

DATA_DIR は ADR-042 resolver（rl_common.resolve_data_dir）経由で解決する（hook/tool 統一）。
jsonl で十分（DuckDB 不要 — checkpoint pitfall 回避）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from measurement_result import MeasuredList, measurement_failure_reason
# #46 read 層拡張: union read は共有モジュール（correction_semantic.store と単一ソース）。
from store_read_union import iter_read_store_paths as _iter_read_store_paths  # noqa: E402

STORE_NAME = "weak_signals.jsonl"


@dataclass
class WeakSignal:
    """1 件の弱シグナルレコード（weak_signals.jsonl 1 行に対応）。"""

    channel: str
    provenance: Dict[str, Any]
    detected_at: str
    session_id: str
    pj_slug: str
    promoted: bool = False
    signal_key: str = ""
    expired: bool = False
    expired_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.signal_key:
            self.signal_key = compute_signal_key(self.channel, self.provenance)

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


def compute_signal_key(channel: str, provenance: Dict[str, Any]) -> str:
    """channel + provenance の安定ハッシュ（再検出時の dedup キー）。

    provenance を sort_keys で正規化してハッシュするので、同じ証拠なら同じキーになる。
    バッチ再実行で同一シグナルを二重記録しないために read 側で既存キーと突合する。
    """
    payload = json.dumps(
        {"channel": channel, "provenance": provenance},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store_path(base: Optional[Path] = None) -> Path:
    """weak_signals.jsonl の正準パスを ADR-042 resolver 経由で解決する。

    base を渡せばそれを優先（テスト isolation 用）。未指定なら resolve_data_dir。
    """
    if base is not None:
        return Path(base) / STORE_NAME
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / STORE_NAME


def _read_one(store: Path) -> List[Dict[str, Any]]:
    """単一 weak_signals.jsonl を読む（ファイル無し → 空リスト）。"""
    if not store.exists():
        return MeasuredList()
    out: List[Dict[str, Any]] = []
    dropped_lines = 0
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        dropped_lines += 1
                        continue
                    out.append(record)
                except (json.JSONDecodeError, ValueError):
                    dropped_lines += 1
    except OSError as exc:
        return MeasuredList(
            measured=False,
            reason=measurement_failure_reason("weak_signals.store._read_one", store, exc),
        )
    reason = f"破損 JSONL を {dropped_lines} 行スキップ" if dropped_lines else None
    return MeasuredList(
        out,
        measured=not (dropped_lines and not out),
        reason=reason,
        dropped_lines=dropped_lines,
    )


def read_signals(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """既存の weak_signals レコードを読む（ファイル無し → 空リスト）。

    path 未指定（production 既定）は #46 read 層拡張で canonical + legacy を union read し、
    ``signal_key`` で dedup する（canonical 先頭勝ち）。signal_key 欠落レコードは dedup できない
    ので全件残す（取りこぼし防止）。明示 path 指定時はそのファイルのみ（hermetic）。
    """
    if path is not None:
        return _read_one(Path(path))
    out: List[Dict[str, Any]] = []
    seen: set = set()
    measured = True
    dropped_lines = 0
    reasons: List[str] = []
    for p in _iter_read_store_paths(STORE_NAME):
        batch = _read_one(p)
        measured = measured and bool(batch.measured)
        dropped_lines += batch.dropped_lines
        if batch.reason:
            reasons.append(batch.reason)
        for r in batch:
            k = r.get("signal_key")
            if k and k in seen:
                continue
            if k:
                seen.add(k)
            out.append(r)
    return MeasuredList(
        out,
        measured=measured,
        reason="; ".join(dict.fromkeys(reasons)) or None,
        dropped_lines=dropped_lines,
    )


def existing_signal_keys(path: Optional[Path] = None) -> set:
    """既存レコードの signal_key 集合（dedup 用）。"""
    return {
        r.get("signal_key")
        for r in read_signals(path)
        if r.get("signal_key")
    }


def _reject_unknown_channels(signals: List[WeakSignal]) -> None:
    """#379 Step 1 凍結ゲート: 凍結中は正準集合に無い channel の signal を書込み拒否する。

    正準集合は producer 側（``weak_signals.channels.WEAK_SIGNAL_CHANNELS``）。
    ``shrink_freeze.is_frozen()`` が False（凍結解除後）は no-op。read 側（``read_signals``
    等）は一切変更しない — 既存ストアに残る未知/旧 channel のレコードは読めなくしない。
    ファイルに触れないため dry-run 純度（pitfall_dryrun_stateful_store_write）を破らず、
    dry-run でも実書込と同じタイミング（append_signals 冒頭）で検証する。
    """
    import shrink_freeze
    from weak_signals.channels import WEAK_SIGNAL_CHANNELS

    if not shrink_freeze.is_frozen():
        return
    unknown = sorted({sig.channel for sig in signals} - WEAK_SIGNAL_CHANNELS)
    if unknown:
        raise shrink_freeze.FreezeViolationError(
            f"weak_signal_channel: 新規追加を検出しました {unknown}。"
            "#379 Step 1 新設凍結中。本当に必要なら SHRINK_FREEZE_ACTIVE の解除判断を"
            "ユーザーに仰ぐこと"
        )


def append_signals(
    signals: List[WeakSignal],
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """新規シグナルを weak_signals.jsonl に追記する（dedup + dry-run ゲート）。

    pitfall_dryrun_stateful_store_write 準拠: ``dry_run=True`` なら **ファイルに一切
    触れない**（ディレクトリ作成も append も行わない）。書き込み件数は dry-run でも
    「書くはずだった件数」を返すので観測はできる。

    Returns:
        {"written": 新規書き込み件数, "skipped_dup": 重複でスキップした件数,
         "dry_run": bool}
    """
    _reject_unknown_channels(signals)
    store = path if path is not None else default_store_path()
    seen = existing_signal_keys(store)

    to_write: List[WeakSignal] = []
    skipped = 0
    batch_keys = set(seen)
    for sig in signals:
        if sig.signal_key in batch_keys:
            skipped += 1
            continue
        batch_keys.add(sig.signal_key)
        to_write.append(sig)

    if dry_run:
        # 最下層: dry-run は store に一切書かない。件数だけ返す。
        return {"written": len(to_write), "skipped_dup": skipped, "dry_run": True}

    if to_write:
        # ADR-049 / #55: production（path 無し）は単一書込ゲート store_write 経由。
        # 明示 path（テスト/isolation）は store_write_raw（別名例外口）でそのパスを尊重する。
        from rl_common import store_write, store_write_raw

        store.parent.mkdir(parents=True, exist_ok=True)
        for sig in to_write:
            if path is None:
                store_write(STORE_NAME, sig.to_record())
            else:
                store_write_raw(store, sig.to_record())

    return {"written": len(to_write), "skipped_dup": skipped, "dry_run": False}
