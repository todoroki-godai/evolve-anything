"""icebox_verdict_seen — icebox 3レーン判定の既読ストア（#352）。

一度提示した (issue番号, lane+closed_at ハッシュ) は lane または closed_at（再凍結）が
変わるまで再提示しない（correction_review_seen.jsonl と同型の物理キー集合パターン）。

#352 B5: 当初は lane/value/reason からハッシュを導出していたが、value（token_usage の
累積値等）は単調増加・reason には value が埋め込まれるため、成立を一度表示した後も評価値が
動くたびに毎日 fingerprint が変わり続け、**同じ成立を永久に再通知**する事故になっていた
（`store_registry.py` の「評価値が変わるまで再提示しない」宣言にも反する）。fingerprint から
value/reason を外し lane のみを軸にする。ただし issue が再オープン→再クローズされた
（再凍結）場合は新しい成立として再提示したいので closed_at は軸に残す。

追記は SessionStart hook が「今回名指しで表示した」verdict に対してのみ行う（自動・確認不要。
icebox lane1 通知は accept/reject を問う対話でなく受動的な気づき通知のため、daily_review の
「確定後にのみ記録」とは異なり表示＝既読でよい）。

dry-run ゼロ書込方針を踏襲: dry_run=True は一切ファイルに触れない。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SEEN_STORE_NAME = "icebox_verdict_seen.jsonl"


def verdict_fingerprint(verdict: Dict[str, Any]) -> str:
    """verdict の判定根拠（lane/closed_at）から短い決定論ハッシュを作る（#352 B5）。

    number は含めない（key 側で別途組み合わせる）。value/reason は**含めない**
    （value は単調増加しうる指標のため、含めると評価値が動くたびに毎日再通知される
    事故になる）。同じ lane・同じ closed_at なら同じ fingerprint になるので、
    lane が変わる（例: met→再度別条件で met）か issue が再凍結される（closed_at 変化）
    までは再提示しない。
    """
    payload = json.dumps(
        {
            "lane": verdict.get("lane"),
            "closed_at": verdict.get("closed_at"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def verdict_key(verdict: Dict[str, Any]) -> str:
    """既読集合のキー（issue番号 + 評価値ハッシュ）。"""
    return f"{verdict.get('number')}:{verdict_fingerprint(verdict)}"


def default_seen_path(base: Optional[Path] = None) -> Path:
    """icebox_verdict_seen.jsonl の正準パス（base 指定はテスト isolation 用）。"""
    if base is not None:
        return Path(base) / SEEN_STORE_NAME
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(rl_common.resolve_data_dir(env)) / SEEN_STORE_NAME


def _read_one(store: Path) -> Set[str]:
    out: Set[str] = set()
    if not store.exists():
        return out
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                k = rec.get("key")
                if k:
                    out.add(k)
    except OSError:
        return out
    return out


def read_seen_keys(path: Optional[Path] = None) -> Set[str]:
    """既読集合のキー（`verdict_key` 形式）を返す（ファイル無し → 空集合）。"""
    store = path if path is not None else default_seen_path()
    return _read_one(Path(store))


def filter_unseen(
    verdicts: List[Dict[str, Any]], seen_keys: Set[str]
) -> List[Dict[str, Any]]:
    """seen_keys に含まれない verdict のみを返す（順序保持）。"""
    return [v for v in verdicts if verdict_key(v) not in seen_keys]


def record_seen(
    verdicts: List[Dict[str, Any]],
    *,
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """verdict 群を既読集合に追記する（dedup + dry-run ゲート貫通）。

    Returns: {"written": int, "dry_run": bool}. `written` は**書込を試みた件数**でなく
    **実際に反映されたことを再読込で確認できた件数**（#352 P2: `store_write`/`store_write_raw`
    の内部実装 `append_jsonl` は OSError を stderr に出すだけで例外化しないサイレント失敗の
    ため、呼び出し元は例外を握り潰さずとも失敗を知りようがなかった。書込直後に再読込して
    確認する verify-after-write で補う）。確認できなかった分は stderr に 1 行警告する。
    """
    store = path if path is not None else default_seen_path()
    existing = read_seen_keys(store)

    to_write: List[Dict[str, Any]] = []
    seen_set = set(existing)
    for v in verdicts:
        k = verdict_key(v)
        if k in seen_set:
            continue
        seen_set.add(k)
        to_write.append(v)

    if dry_run:
        return {"written": len(to_write), "dry_run": True}

    if not to_write:
        return {"written": 0, "dry_run": False}

    from rl_common import store_write, store_write_raw

    seen_at = datetime.now(timezone.utc).isoformat()
    store.parent.mkdir(parents=True, exist_ok=True)
    for v in to_write:
        rec = {"key": verdict_key(v), "number": v.get("number"), "seen_at": seen_at}
        if path is None:
            store_write(SEEN_STORE_NAME, rec)
        else:
            store_write_raw(store, rec)

    confirmed = read_seen_keys(store)
    attempted_keys = [verdict_key(v) for v in to_write]
    written = sum(1 for k in attempted_keys if k in confirmed)
    if written < len(to_write):
        print(
            f"[evolve-anything:icebox_verdict_seen] record_seen: "
            f"{len(to_write) - written}/{len(to_write)} 件の書込を確認できませんでした"
            f"（{store}）",
            file=sys.stderr,
        )
    return {"written": written, "dry_run": False}
