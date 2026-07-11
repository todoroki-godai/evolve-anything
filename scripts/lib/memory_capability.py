"""記憶操作 capability の決定論算出（#19, advisory）。

OPD-Evolver（arXiv 2606.17628）由来の「記憶操作能力」を read/use/write/maintain の
4 観点フレームで捉え、記憶の死蔵・未活用を可視化する。fitness の重み軸には**しない**
（advisory 表示のみ・outcome_metrics と同じレーン, ADR-046 と同方針）。

【3軸算出にした理由（reason 非永続化）】
``memory_temporal.reinforce_memory(filepath, reason)`` の ``reason`` はファイルに書かれない
（ログ用のみ）。さらに reinforce は SessionStart で有効な全 memory に毎回発火する
（``hooks/instructions_loaded.py`` の ``_reinforce_loaded_memory``）。このため frontmatter から
「recall 由来か SessionStart 注入由来か」を事後復元できず、read を独立軸にすると静的存在指標
（near-constant）にしかならない。よって **read/use を統合して3軸** で算出する:

1. write（記憶量）: temporal frontmatter を持つ件数 / 総 memory 件数。
2. maintain（維持・健全度）: (非 stale かつ 非 superseded) / 総件数。evidence には
   #93 記憶遷移検証（memory_guard.transition_check_counts）の reject件数/検査件数も含む。
3. use_read（活性）: last_reinforced_at を持つ件数 / 総件数 + update_count 中央値。

スコープ（DATA_DIR 単一ファイル pitfall の再来防止）: 当 PJ の slug が解決した
``~/.claude/projects/<slug>/memory/`` の **1 ディレクトリのみ** を対象とし、絶対に全 PJ を
走査しない。slug は worktree 安全な ``pj_slug.resolve_pj_slug(project_dir)`` で解決する。
集計対象は ``*.md`` のうち ``MEMORY.md``（索引であって memory 実体でない）を除いたファイル。

決定論・LLM 非依存。
"""
from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any, Dict, List

from frontmatter import parse_frontmatter
from memory_temporal import is_stale, is_superseded, parse_memory_temporal
from pj_slug import resolve_cc_memory_dir

# 索引ファイル（memory 実体ではないので集計対象外）。
_INDEX_FILENAME = "MEMORY.md"


def _resolve_memory_dir(project_dir: Path) -> Path:
    """当 PJ の memory ディレクトリを返す（CC パスエンコード単一ソース・1 ディレクトリのみ）。

    CC の memory dir は ``~/.claude/projects/<path-encoded>/memory``。``resolve_pj_slug`` の
    repo-basename slug とは名前空間が別物なので ``resolve_cc_memory_dir`` を使う（#19 で
    ``resolve_pj_slug`` 由来の別 dir を指し section が常に沈黙したバグの根治・#18 と単一ソース）。
    """
    return resolve_cc_memory_dir(project_dir)


def _memory_files(memory_dir: Path) -> List[Path]:
    """集計対象の memory ファイル（MEMORY.md を除く *.md）を返す。"""
    if not memory_dir.is_dir():
        return []
    return sorted(
        p for p in memory_dir.glob("*.md") if p.name != _INDEX_FILENAME
    )


def compute_memory_capability(project_dir: Path) -> Dict[str, Any]:
    """当 PJ の記憶操作 capability を3軸で算出する（決定論・LLM 非依存）。

    Returns:
        対象 memory が 0 件（dir 不在 / MEMORY.md のみ）→ ``{"applicable": False}``。
        1 件以上 → ``{"applicable": True, "total": int, "write": axis, "maintain": axis,
        "use_read": axis}``。各 axis は ``{"value": float, "evidence": dict}``
        （outcome_metrics と同系統の形）。
    """
    project_dir = Path(project_dir)
    memory_dir = _resolve_memory_dir(project_dir)
    files = _memory_files(memory_dir)
    if not files:
        return {"applicable": False}

    total = len(files)
    with_frontmatter = 0
    stale = 0
    superseded = 0
    healthy = 0
    reinforced = 0
    update_counts: List[int] = []

    for path in files:
        fm = parse_frontmatter(path)
        temporal = parse_memory_temporal(path)
        if fm:
            with_frontmatter += 1

        is_sup = is_superseded(temporal)
        is_st = is_stale(temporal)
        if is_sup:
            superseded += 1
        if is_st:
            stale += 1
        # 健全 = 非 stale かつ 非 superseded（両方該当でも 1 件としてしか引かない）。
        if not is_sup and not is_st:
            healthy += 1

        # use_read 軸: last_reinforced_at は parse_memory_temporal が読まないため
        # frontmatter から直接判定する（reinforce_memory が書き込むフィールド）。
        if fm.get("last_reinforced_at"):
            reinforced += 1

        update_counts.append(int(temporal.get("update_count", 0)))

    write_axis = {
        "value": round(with_frontmatter / total, 4),
        "evidence": {"with_frontmatter": with_frontmatter, "total": total},
    }
    # #93: 記憶遷移検証（coverage/preservation/fidelity）の reject 件数 / 検査件数を
    # maintain 軸 evidence に追加する。matched_name（同名 frontmatter）が無い書込は
    # そもそも検証対象外なので checked=0 は「健全」ではなく「該当なし」を意味する。
    try:
        import memory_guard
        from pj_slug import pj_slug_fast
        transition_slug = pj_slug_fast(str(project_dir))
        transition_counts = (
            memory_guard.transition_check_counts(transition_slug)
            if transition_slug else {"checked": 0, "rejected": 0}
        )
    except ImportError:
        transition_counts = {"checked": 0, "rejected": 0}

    maintain_axis = {
        "value": round(healthy / total, 4),
        "evidence": {
            "stale": stale,
            "superseded": superseded,
            "total": total,
            "transition_checked": transition_counts["checked"],
            "transition_rejected": transition_counts["rejected"],
        },
    }
    use_read_axis = {
        "value": round(reinforced / total, 4),
        "evidence": {
            "reinforced": reinforced,
            "total": total,
            "update_count_median": float(median(update_counts)) if update_counts else 0.0,
        },
    }

    return {
        "applicable": True,
        "total": total,
        "write": write_axis,
        "maintain": maintain_axis,
        "use_read": use_read_axis,
    }
