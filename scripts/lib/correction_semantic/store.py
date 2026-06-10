"""correction_semantic.store — 個人辞書 + 判定進捗の append/read（#431）。

2 つの jsonl ストアを扱う:
- ``correction_idioms.jsonl`` — 検出した修正言い回し（イディオム）の個人辞書。
  provenance（元発話の物理キー・判定理由）付き。dedup キー = idiom + 元発話の物理キー。
- ``correction_judged.jsonl`` — LLM 判定済み発話の物理キー進捗。再判定（無駄な LLM call）を
  防ぐために utterance の物理 PK（source_path:line_no）で突合する。

dry-run ゼロ書込（pitfall_dryrun_stateful_store_write）: append 系は ``dry_run`` を受け、
True なら **一切ファイルに触れない**（ディレクトリ作成も append も行わない）。

DATA_DIR は ADR-042 resolver（rl_common.resolve_data_dir）経由（hook/tool 統一）。
jsonl で十分（DuckDB checkpoint pitfall 回避）。両ストアとも writer は batch（evolve 同居）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

IDIOMS_STORE_NAME = "correction_idioms.jsonl"
JUDGED_STORE_NAME = "correction_judged.jsonl"


# ─────────────────────────────────────────────────────────────────
# 物理キー（判定進捗の突合）
# ─────────────────────────────────────────────────────────────────
def utterance_key(utterance: Dict[str, Any]) -> str:
    """utterance の物理 PK（source_path:line_no）を返す（utterances.db の PK と同型）。

    判定済み突合・provenance のどちらにも使う安定キー。
    """
    return f"{utterance.get('source_path', '')}:{utterance.get('line_no', '')}"


# ─────────────────────────────────────────────────────────────────
# 個人辞書（correction_idioms.jsonl）
# ─────────────────────────────────────────────────────────────────
@dataclass
class CorrectionIdiom:
    """1 件の修正言い回しレコード（correction_idioms.jsonl 1 行に対応）。

    idiom:       抽出された修正の言い回し（例: "四国めたんじゃなくて"）
    provenance:  検出根拠（source_path / line_no / session_id / reason 等の evidence dict）
    detected_at: 検出時刻（ISO8601 UTC）
    pj_slug:     ADR-031 準拠 slug（read 側照合の強制・全PJ共通 DATA_DIR 単一ファイル pitfall）
    idiom_key:   dedup キー（idiom + provenance の物理キーの安定ハッシュ）
    """

    idiom: str
    provenance: Dict[str, Any]
    detected_at: str
    pj_slug: str
    idiom_key: str = ""

    def __post_init__(self) -> None:
        if not self.idiom_key:
            self.idiom_key = compute_idiom_key(self.idiom, self.provenance)

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


def compute_idiom_key(idiom: str, provenance: Dict[str, Any]) -> str:
    """idiom + 元発話の物理キーの安定ハッシュ（再判定時の dedup キー）。

    同じ発話から同じ言い回しを抽出したら同じキーになるので、バッチ再実行で
    二重記録しない。physical key（source_path:line_no）を含めることで、同一言い回しを
    別発話から拾った場合は別レコードとして残す（provenance を潰さない）。
    """
    phys = f"{provenance.get('source_path', '')}:{provenance.get('line_no', '')}"
    payload = json.dumps(
        {"idiom": idiom, "phys": phys}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _resolve_store(name: str, base: Optional[Path]) -> Path:
    if base is not None:
        return Path(base) / name
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = rl_common.resolve_data_dir(env)
    return Path(data_dir) / name


def default_idioms_path(base: Optional[Path] = None) -> Path:
    return _resolve_store(IDIOMS_STORE_NAME, base)


def default_judged_path(base: Optional[Path] = None) -> Path:
    return _resolve_store(JUDGED_STORE_NAME, base)


def read_idioms(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """既存の個人辞書レコードを読む（ファイル無し → 空リスト）。"""
    store = path if path is not None else default_idioms_path()
    return _read_jsonl(store)


def existing_idiom_keys(path: Optional[Path] = None) -> Set[str]:
    return {r.get("idiom_key") for r in read_idioms(path) if r.get("idiom_key")}


def append_idioms(
    idioms: List[CorrectionIdiom],
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """新規イディオムを correction_idioms.jsonl に追記する（dedup + dry-run ゲート）。

    Returns:
        {"written": int, "skipped_dup": int, "dry_run": bool}
    """
    store = path if path is not None else default_idioms_path()
    seen = existing_idiom_keys(store)

    to_write: List[CorrectionIdiom] = []
    skipped = 0
    batch_keys = set(seen)
    for it in idioms:
        if it.idiom_key in batch_keys:
            skipped += 1
            continue
        batch_keys.add(it.idiom_key)
        to_write.append(it)

    if dry_run:
        return {"written": len(to_write), "skipped_dup": skipped, "dry_run": True}

    if to_write:
        from rl_common import append_jsonl

        store.parent.mkdir(parents=True, exist_ok=True)
        for it in to_write:
            append_jsonl(store, it.to_record())

    return {"written": len(to_write), "skipped_dup": skipped, "dry_run": False}


# ─────────────────────────────────────────────────────────────────
# 判定進捗（correction_judged.jsonl）
# ─────────────────────────────────────────────────────────────────
def read_judged_keys(path: Optional[Path] = None) -> Set[str]:
    """判定済み発話の物理キー集合を返す（ファイル無し → 空 set）。

    各行は {"key": "<source_path>:<line_no>", ...}。"""
    store = path if path is not None else default_judged_path()
    out: Set[str] = set()
    for rec in _read_jsonl(store):
        k = rec.get("key")
        if k:
            out.add(k)
    return out


def record_judged(
    keys: List[str],
    path: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """判定済み発話の物理キーを追記する（dedup + dry-run ゲート）。

    Returns:
        {"written": int, "dry_run": bool}
    """
    store = path if path is not None else default_judged_path()
    existing = read_judged_keys(store)

    to_write: List[str] = []
    seen = set(existing)
    for k in keys:
        if not k or k in seen:
            continue
        seen.add(k)
        to_write.append(k)

    if dry_run:
        return {"written": len(to_write), "dry_run": True}

    if to_write:
        from rl_common import append_jsonl

        store.parent.mkdir(parents=True, exist_ok=True)
        for k in to_write:
            append_jsonl(store, {"key": k})

    return {"written": len(to_write), "dry_run": False}


def filter_unjudged(
    utterances: List[Dict[str, Any]],
    judged_keys: Set[str],
) -> List[Dict[str, Any]]:
    """判定済みでない発話だけを返す（物理キーで突合）。"""
    return [u for u in utterances if utterance_key(u) not in judged_keys]


# ─────────────────────────────────────────────────────────────────
# 内部 helper
# ─────────────────────────────────────────────────────────────────
def _read_jsonl(store: Path) -> List[Dict[str, Any]]:
    if not store.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(store, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return out
