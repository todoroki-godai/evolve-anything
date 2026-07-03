"""subagent_traces.store — subagent_traces.jsonl の書込・読取（#38）。

write は **必ず** store_write barrier（ADR-049）経由。read は read-only 純度
（ファイルを作らない・例外を投げない）で reward_ema.read_reward_ema と同型。
pj_slug スコープ（全PJ共通 DATA_DIR ゆえ read 側で filter）、agent_id 単位
last-append-wins（append 順 = 時系列で再 ingest の上書きを許容）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

# #112 read 層 alias fold: legacy 旧 slug タグを canonical へ畳んで拾う共有ヘルパー
# （daily_review._read_new と単一ソース・alias は read 専用・write は現 slug 固定）。
from store_read_union import pj_slug_match as _pj_slug_match

# ストアの basename（store_registry / store_write の単一キー）。
STORE_NAME = "subagent_traces.jsonl"

# テストは monkeypatch.setattr(store, "DATA_DIR", tmp_path) で差し替える
# （文字列ターゲット patch を避ける既知 pitfall 準拠・reward_ema と同型）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"


def _base(data_dir: Optional[Path]) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def _read_jsonl(path: Path) -> List[dict]:
    """jsonl を 1 行ずつ安全に読む（ファイル不在 → []、壊れた行はスキップ）。

    ファイルを **作らない・書かない**（dry-run 純度）。
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


def write_trace(record: dict, *, data_dir: Optional[Path] = None) -> None:
    """1 件の軌跡レコードを追記する（#140: read と write の隔離先を対称化する）。

    - ``data_dir=None``（本番）: store_write barrier（ADR-049）経由で canonical
      ``DATA_DIR/<STORE_NAME>`` へ。保存先は barrier が内部解決する（呼び出し側は場所を決めない）。
    - ``data_dir`` 指定（隔離・テスト・scratch 実行）: ``store_write_raw`` で
      ``data_dir/<STORE_NAME>`` へ直接書込む。barrier は本番書込の守り神ゆえ隔離書込に
      緩和は入れず、例外口 ``store_write_raw`` を通す（ADR-049 決定5）。これで
      ``read_traces`` / ``read_all_agent_ids`` の ``data_dir`` と write の隔離先が一致し、
      「read は隔離を尊重するが write だけ常に本番」という非対称契約（#140 実害）を解消する。
    """
    if data_dir is None:
        from rl_common.store_write import store_write

        store_write(STORE_NAME, record)
        return
    from rl_common.store_write import store_write_raw

    base = Path(data_dir)
    base.mkdir(parents=True, exist_ok=True)  # append_jsonl は parent を掘らない
    store_write_raw(base / STORE_NAME, record)


def read_traces(slug: str, *, data_dir: Optional[Path] = None) -> Dict[str, dict]:
    """slug スコープの subagent_traces.jsonl を読み、agent_id 単位の最新レコードを返す。

    ``pj_slug == slug`` で filter、append 順 = 時系列なので agent_id 単位
    **last-append-wins**。ファイル不在 → {}（ファイルを作らない・書かない = dry-run 純度）。
    """
    base = _base(data_dir)
    out: Dict[str, dict] = {}
    for rec in _read_jsonl(base / STORE_NAME):
        # #112 read 層 alias fold: legacy 旧 slug タグも canonical へ畳んで当 PJ として拾う。
        if not _pj_slug_match(rec.get("pj_slug"), slug):
            continue
        agent_id = rec.get("agent_id")
        if not agent_id:
            continue
        # append 順に上書き = 最後に見たもの（時系列で新しい）を採用。
        out[agent_id] = rec
    return out


def read_all_agent_ids(*, data_dir: Optional[Path] = None) -> set:
    """全 slug 横断で既 ingest 済 agent_id 集合を返す（ingest の dedup 用）。

    dedup は agent_id 単位（全PJ共通 DATA_DIR ゆえ slug を跨いでも agent_id は一意）。
    ファイル不在 → 空集合。
    """
    base = _base(data_dir)
    ids: set = set()
    for rec in _read_jsonl(base / STORE_NAME):
        aid = rec.get("agent_id")
        if aid:
            ids.add(aid)
    return ids
