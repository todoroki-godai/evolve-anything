"""daily review で rephrase の similarity-only 重複を吸収する（#543）。

共有 predicate は変更せず、呼び出し前の既読 signal_key 集合だけを拡張する。
identity は denylist 方式で similarity だけを除外し、将来のフィールドは保持する。
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

REPHRASE_CHANNEL = "rephrase"
_EXCLUDED_FIELDS = frozenset({"similarity"})
_REQUIRED_FIELDS = (
    "source_path", "line_no", "prev_line_no", "prev_text", "text", "detector",
)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return unicodedata.normalize("NFC", value).strip()


def _dedup_identity(record: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """similarity を除いた rephrase identity。欠損時は安全側へ倒す。"""
    if record.get("channel") != REPHRASE_CHANNEL:
        return None
    provenance = record.get("provenance") or {}
    session_id = record.get("session_id")
    if not session_id:
        return None
    for field in _REQUIRED_FIELDS:
        value = provenance.get(field)
        if value is None or value == "":
            return None
    identity_provenance = {
        key: value for key, value in provenance.items() if key not in _EXCLUDED_FIELDS
    }
    for field in ("text", "prev_text"):
        if isinstance(identity_provenance.get(field), str):
            identity_provenance[field] = _normalize_text(identity_provenance[field])
    return (
        session_id,
        json.dumps(identity_provenance, sort_keys=True, ensure_ascii=False),
    )


def expand_seen_keys_for_rephrase_dupes(
    scoped_records: List[Dict[str, Any]],
    seen_keys: Set[str],
) -> Set[str]:
    """同じ rephrase identity に既読 key があれば、その同値類を既読へ拡張する。"""
    groups: Dict[Any, List[str]] = {}
    for record in scoped_records:
        identity = _dedup_identity(record)
        if identity is None:
            continue
        signal_key = record.get("signal_key")
        if not signal_key:
            continue
        groups.setdefault(identity, []).append(signal_key)

    expanded = set(seen_keys)
    for signal_keys in groups.values():
        if len(signal_keys) >= 2 and any(key in seen_keys for key in signal_keys):
            expanded.update(signal_keys)
    return expanded
