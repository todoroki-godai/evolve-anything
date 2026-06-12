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

from weak_signals.store import default_store_path, read_signals


def read_unpromoted(
    weak_signals_path: Optional[Path] = None,
    channel: Optional[str] = None,
    exclude_expired: bool = True,
) -> List[Dict[str, Any]]:
    """未昇格（promoted=False）の weak_signal レコードを返す。

    channel を渡すとそのチャネルだけに絞る（例: "llm_judge" で #431 のバッチ判定のみ）。
    exclude_expired（既定 True）は TTL 失効（expired=True）レコードを昇格候補から外す
    （#442。古い修正候補は腐る — TTL が品質フィルタとして機能する）。後方互換が必要な
    呼び出しは exclude_expired=False で全件取得できる。
    """
    recs = read_signals(weak_signals_path)
    out = [r for r in recs if not r.get("promoted")]
    if exclude_expired:
        out = [r for r in out if not r.get("expired")]
    if channel is not None:
        out = [r for r in out if r.get("channel") == channel]
    return out


def _correction_message(rec: Dict[str, Any]) -> str:
    """weak_signal の provenance から corrections の message 本文を組み立てる。"""
    prov = rec.get("provenance") or {}
    text = prov.get("text") or ""
    reason = prov.get("reason") or ""
    if text and reason:
        return f"{text}（{reason}）"
    return text or reason or rec.get("channel", "weak_signal")


def _build_correction_record(rec: Dict[str, Any], project_path: str) -> Dict[str, Any]:
    """weak_signal → corrections.jsonl の human-source レコードへ変換する。"""
    prov = rec.get("provenance") or {}
    now = datetime.now(timezone.utc).isoformat()
    return {
        "correction_type": "semantic_idiom",
        "matched_patterns": [],
        "message": _correction_message(rec),
        "last_skill": None,
        "preceding_tool_calls": None,
        "confidence": 0.9,
        "sentiment": "correction",
        "routing_hint": None,
        "guardrail": False,
        "reflect_status": "applied",
        "extracted_learning": None,
        "project_path": project_path,
        # human-source: フェーズ昇格カウント対象（provenance_weight.HUMAN_SOURCES）
        "source": "reflect_confirmed",
        "timestamp": now,
        "session_id": rec.get("session_id", ""),
        "weak_signal_key": rec.get("signal_key"),
        "weak_signal_channel": rec.get("channel"),
        "weak_signal_provenance": prov,
    }


def _rewrite_promoted(
    weak_signals_path: Path,
    promoted_keys: Set[str],
) -> None:
    """weak_signals.jsonl の該当 signal_key 行を promoted=True にして原子的に書き直す。"""
    if not weak_signals_path.exists() or not promoted_keys:
        return
    recs = read_signals(weak_signals_path)
    for r in recs:
        if r.get("signal_key") in promoted_keys:
            r["promoted"] = True
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


def promote_signals(
    signal_keys: List[str],
    *,
    weak_signals_path: Optional[Path] = None,
    corrections_path: Optional[Path] = None,
    project_path: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """指定 signal_key の未昇格 weak_signal を corrections へ昇格する。

    - corrections.jsonl に source=reflect_confirmed の human-source レコードを追記
    - 昇格した weak_signal を promoted=True にマーク（二重昇格防止）
    - dry-run はどちらにも一切書かない（昇格するはずだった件数だけ返す）

    Returns:
        {"promoted": int, "dry_run": bool}
    """
    ws_path = weak_signals_path if weak_signals_path is not None else default_store_path()
    target = set(signal_keys or [])
    candidates = [
        r for r in read_unpromoted(ws_path)
        if r.get("signal_key") in target
    ]

    if dry_run:
        return {"promoted": len(candidates), "dry_run": True}

    if not candidates:
        return {"promoted": 0, "dry_run": False}

    # corrections に human-source レコードを追記
    from rl_common import append_jsonl

    if corrections_path is None:
        import rl_common as _rc

        corrections_path = Path(_rc.DATA_DIR) / "corrections.jsonl"
    corrections_path = Path(corrections_path)
    corrections_path.parent.mkdir(parents=True, exist_ok=True)

    promoted_keys: Set[str] = set()
    for rec in candidates:
        append_jsonl(corrections_path, _build_correction_record(rec, project_path))
        key = rec.get("signal_key")
        if key:
            promoted_keys.add(key)

    # weak_signal を promoted=True にマーク
    _rewrite_promoted(ws_path, promoted_keys)

    return {"promoted": len(promoted_keys), "dry_run": False}
