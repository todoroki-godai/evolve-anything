"""judge_audit.store — judge_audit_verdicts の書込・読取（#188）。

write は **必ず** store_write barrier（ADR-049）経由。read は read-only 純度
（ファイルを作らない・例外を投げない）で verbosity.store / subagent_traces.store と同型。
pj_slug スコープ（全PJ共通 DATA_DIR ゆえ read 側で filter）、fixture id 単位 dedup
（last-append-wins・再実行時の再判定を許容）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

# #112 read 層 alias fold: legacy 旧 slug タグを canonical へ畳んで拾う共有ヘルパー
# （verbosity.store と単一ソース）。
from store_read_union import pj_slug_match as _pj_slug_match

# ストアの basename（store_registry / store_write の単一キー）。
VERDICTS_STORE = "judge_audit_verdicts.jsonl"

# テストは monkeypatch.setattr(store, "DATA_DIR", tmp_path) で差し替える
# （文字列ターゲット patch を避ける既知 pitfall 準拠・verbosity.store と同型）。
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


def read_verdicts(slug: str, *, data_dir: Optional[Path] = None) -> Dict[str, dict]:
    """slug スコープの judge_audit_verdicts.jsonl を fixture id → 最新 verdict の dict で返す。

    append 順 = 時系列なので id 単位 last-append-wins（再実行の上書きを許容）。
    ファイル不在 → {}（read-only 純度）。
    """
    base = _base(data_dir)
    out: Dict[str, dict] = {}
    for rec in _read_jsonl(base / VERDICTS_STORE):
        # #112 read 層 alias fold: legacy 旧 slug タグも canonical へ畳んで当 PJ として拾う。
        if not _pj_slug_match(rec.get("pj_slug"), slug):
            continue
        fid = rec.get("id")
        if not fid:
            continue
        out[fid] = rec
    return out


def write_verdict(record: dict) -> None:
    """1 件の判定レコードを store_write barrier 経由で追記する（ADR-049）。

    保存先は store_write が canonical DATA_DIR/<VERDICTS_STORE> へ内部解決する
    （呼び出し側は場所を決めない）。
    """
    from rl_common.store_write import store_write

    store_write(VERDICTS_STORE, record)
