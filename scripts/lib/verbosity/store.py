"""verbosity.store — verbosity_candidates / verbosity_verdicts の書込・読取（#75）。

write は **必ず** store_write barrier（ADR-049）経由。read は read-only 純度
（ファイルを作らない・例外を投げない）で subagent_traces.store と同型。
pj_slug スコープ（全PJ共通 DATA_DIR ゆえ read 側で filter）、hash 単位 dedup。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

# ストアの basename（store_registry / store_write の単一キー）。
CANDIDATES_STORE = "verbosity_candidates.jsonl"
VERDICTS_STORE = "verbosity_verdicts.jsonl"

# テストは monkeypatch.setattr(store, "DATA_DIR", tmp_path) で差し替える
# （文字列ターゲット patch を避ける既知 pitfall 準拠・subagent_traces と同型）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"


def _base(data_dir: Optional[Path]) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def _read_jsonl(path: Path) -> List[dict]:
    """jsonl を 1 行ずつ安全に読む（ファイル不在 → []、壊れた行はスキップ）。

    ファイルを **作らない・書かない**（dry-run / read-only 純度）。
    """
    if not path.exists():
        return []
    out: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    return out


def read_candidates(slug: str, *, data_dir: Optional[Path] = None) -> List[dict]:
    """slug スコープの verbosity_candidates.jsonl を読み、hash 単位 dedup して返す。

    ``pj_slug == slug`` で filter。同一 hash は最初の 1 件のみ（候補ファイル内の重複除去）。
    ファイル不在 → []（ファイルを作らない・書かない = read-only 純度）。
    """
    base = _base(data_dir)
    out: List[dict] = []
    seen: set = set()
    for rec in _read_jsonl(base / CANDIDATES_STORE):
        if rec.get("pj_slug") != slug:
            continue
        h = rec.get("hash")
        if h is not None and h in seen:
            continue
        if h is not None:
            seen.add(h)
        out.append(rec)
    return out


def read_verdicts(slug: str, *, data_dir: Optional[Path] = None) -> Dict[str, dict]:
    """slug スコープの verbosity_verdicts.jsonl を hash → 最新 verdict の dict で返す。

    append 順 = 時系列なので hash 単位 last-append-wins（再判定の上書きを許容）。
    ファイル不在 → {}（read-only 純度）。
    """
    base = _base(data_dir)
    out: Dict[str, dict] = {}
    for rec in _read_jsonl(base / VERDICTS_STORE):
        if rec.get("pj_slug") != slug:
            continue
        h = rec.get("hash")
        if not h:
            continue
        out[h] = rec
    return out


def read_judged_hashes(slug: str, *, data_dir: Optional[Path] = None) -> set:
    """slug スコープで判定済み hash 集合を返す（judge の dedup 用）。"""
    return set(read_verdicts(slug, data_dir=data_dir).keys())


def write_verdict(record: dict) -> None:
    """1 件の判定レコードを store_write barrier 経由で追記する（ADR-049）。

    保存先は store_write が canonical DATA_DIR/<VERDICTS_STORE> へ内部解決する
    （呼び出し側は場所を決めない）。
    """
    from rl_common.store_write import store_write

    store_write(VERDICTS_STORE, record)
