"""subagent_traces.ingest — subagents.jsonl → subagent_traces.jsonl の増分取り込み（#38）。

増分 ingest:
1. subagents.jsonl を DATA_DIR（断片化対応の union read）から読む
2. 既 ingest 済 agent_id 集合（read_all_agent_ids）で skip（dedup by agent_id）
3. 未 ingest 行のうち agent_transcript_path が現存するものだけ、最大 max_new 件を
   extract_trace → pj_slug 付与 → write_trace
4. max_new で打ち切ったら capped: True と remaining を返す（沈黙切り捨てしない）

runaway 防止: transcript は agent_transcript_path に名指しされた本だけ読む
（projects 全 walk しない）。各 transcript パースは 1 本で完結。
決定論・ゼロ LLM。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import store as _store
from .extractor import extract_trace

# extractor のバージョン。軌跡パースを変えたら +1（再 ingest 判断の手掛かり）。
# 2: #200 で delegation_prompt / delegation_prompt_truncated の抽出を追加。
TRACE_VERSION = 2


def _default_data_dir() -> Path:
    """DATA_DIR の正準パスを解決する。

    store_write barrier（write 側）が ``rl_common.DATA_DIR`` を canonical 解決先に使うため、
    read 側もこれに揃える（read/write の dir 食い違い防止）。テストの
    ``monkeypatch.setattr(rl_common, "DATA_DIR", tmp)`` にもこれで追従する。
    """
    import rl_common

    return Path(rl_common.DATA_DIR)


def _read_subagents(base: Path) -> List[Dict[str, Any]]:
    """DATA_DIR 断片化を union read で吸収して subagents.jsonl を全件返す（append-only）。

    fanout_cost._read_subagents と同方針（cross-dir union・dedup なし concat）。
    agent_type ノイズ除外はここではせず ingest 側で行う（軌跡は agent_type 非依存に取れる）。
    """
    try:
        from rl_common import iter_read_data_dirs
        dirs = iter_read_data_dirs(base)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        dirs = [base]
    out: List[Dict[str, Any]] = []
    for d in dirs:
        path = d / "subagents.jsonl"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _slug_for(rec: Dict[str, Any], base: Path) -> Optional[str]:
    """subagent レコードの pj_slug を導出する（project フィールド → pj_slug_fast）。"""
    project = rec.get("project")
    if project:
        try:
            from pj_slug import pj_slug_fast
            slug = pj_slug_fast(project, data_dir=base)
            if slug:
                return slug
        except ImportError:  # pragma: no cover
            pass
        # pj_slug_fast 不可なら basename フォールバック。
        return Path(str(project)).name or None
    return None


def ingest_all_projects(
    *,
    max_new: int = 200,
    data_dir: Optional[Path] = None,
    progress: bool = False,
) -> Dict[str, Any]:
    """subagents.jsonl の未 ingest 行を増分取り込みする（決定論・ゼロ LLM）。

    Args:
        max_new:  1 回の ingest で書き込む最大件数（runaway 防止の打ち切り）。
        data_dir: DATA_DIR（未指定なら ADR-042 resolver で解決＝本番 canonical へ barrier 書込）。
                  **指定時は read / dedup / write すべて data_dir 配下に閉じる**（#140:
                  read だけ隔離され write が常に本番へ漏れる非対称を解消）。
        progress: True なら 1 件ごとに進捗を stderr に flush 出力。

    Returns:
        {"ingested": N, "skipped": M, "capped": bool, "remaining": K}
        - skipped: 既 ingest 済 dedup + transcript 不在でスキップした件数の合計。
        - capped:  max_new で打ち切ったか。
        - remaining: 打ち切り時に残った未 ingest（transcript 現存）件数。
    """
    base = data_dir if data_dir is not None else _default_data_dir()
    base = Path(base)

    subagents = _read_subagents(base)
    seen_ids = _store.read_all_agent_ids(data_dir=base)

    ingested = 0
    skipped = 0
    remaining = 0
    capped = False

    for rec in subagents:
        agent_id = rec.get("agent_id")
        if not agent_id:
            skipped += 1
            continue
        if agent_id in seen_ids:
            skipped += 1
            continue

        tpath = rec.get("agent_transcript_path")
        if not tpath or not Path(str(tpath)).exists():
            # 掃除済み transcript は本数に数えず skip（再 ingest で復活しない＝永続 skip）。
            skipped += 1
            continue

        if ingested >= max_new:
            # 打ち切り後の「未 ingest かつ transcript 現存」を残数として数える。
            capped = True
            remaining += 1
            continue

        trace = extract_trace(tpath)
        if trace is None:
            skipped += 1
            continue

        slug = _slug_for(rec, base)
        record = {
            "agent_id": agent_id,
            "pj_slug": slug,
            "agent_type": rec.get("agent_type", ""),
            "agent_name": rec.get("agent_name"),
            "parent_skill": rec.get("parent_skill"),
            "session_id": rec.get("session_id"),
            "timestamp": rec.get("timestamp"),
            "trace_version": TRACE_VERSION,
            **trace,
        }
        # #140: 元の data_dir（None=本番 barrier / 明示=隔離 raw）をそのまま貫通させ、
        # read/dedup と write の隔離先を一致させる（base は解決済みなので使わない）。
        _store.write_trace(record, data_dir=data_dir)
        seen_ids.add(agent_id)
        ingested += 1

        if progress:
            sys.stderr.write(
                f"  [subagent_traces] {agent_id}: "
                f"first_try_success={trace['first_try_success']} "
                f"tool_errors={trace['tool_error_count']}\n"
            )
            sys.stderr.flush()

    return {
        "ingested": ingested,
        "skipped": skipped,
        "capped": capped,
        "remaining": remaining,
    }
