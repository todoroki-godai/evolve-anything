"""drain（`evolve --drain` の実体, #402）（`evolve_decisions` パッケージ分割・#383）。

`_partition_orphaned` / `drain_pending` を束ねる。振る舞いはゼロ変更で、
`evolve_decisions/__init__.py` が re-export し後方互換を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `resolve_slug`
（`_queue.py`）は test の `monkeypatch.setattr(evolve_decisions, "resolve_slug", ...)` 対象
なので `import evolve_decisions as _ed` 経由で呼ぶ。`ingest_decisions`（`_ingest.py`）と
`_marker_lock` / `_read_pending_marker_file` / `_purge_marker_entries_locked`（`_marker.py`）は
monkeypatch 対象でないため直接 import してよい。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from evolve_decision_ids import entry_generation, is_orphaned_worktree

from ._ingest import ingest_decisions
from ._marker import _marker_lock, _purge_marker_entries_locked, _read_pending_marker_file


def _partition_orphaned(
    entries: List[Dict[str, Any]],
) -> "tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """entries を (生きている worktree の分, orphan の分) に分ける（#376 AC5）。

    削除済み worktree（`worktree_root` がディスク上に存在しない）に属する pending は
    永久に apply されようがない残骸なので、判定不能を保守的に残しつつ orphan だけを
    分離する。呼び出し側が orphan を marker から取り除く。
    """
    kept: List[Dict[str, Any]] = []
    orphaned: List[Dict[str, Any]] = []
    for entry in entries:
        (orphaned if is_orphaned_worktree(entry) else kept).append(entry)
    return kept, orphaned


def drain_pending(
    *,
    slug: Optional[str] = None,
    project_dir: Optional[str] = None,
    result_json: Optional[str] = None,
    accepted: Optional[Any] = None,
    rejected: Optional[Dict[str, str]] = None,
    history_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """`evolve --drain` の実体（#402）。pending を marker か result-json から取り、
    明示 accept/reject を ingest し、marker をクリアする。

    enforcement gap（ingest が SKILL.md prose 依存）を、SKILL.md が inline python でなく
    **単一コマンド `evolve --drain` を呼ぶだけ**にして縮める。drain は CLI＝**tool 文脈**で
    走るため optimize_history を reader と同一 DATA_DIR に書く＝#358（DATA_DIR split）を踏まない。

    冪等: ingest が `decision_event_id`（提案 ID + 判断種別 + 判断時点の内容）で dedup するので、
    未 apply で空振り→後で apply→再 drain でも accept は一度だけ記録される（apply タイミング非依存）。

    Args:
        slug: 未指定なら project_dir/cwd から worktree 安全に解決。
        result_json: 指定時はこの result JSON の `evolve_decisions.pending` を使う（marker より優先）。
            marker 由来でないため orphan 判定（下記）の対象外。
        accepted: 明示 accept された id の集合（#376 AC1）。証跡が無い提案は
            ディスク差分があっても accept にならず pending のまま。
        rejected: {pending_id: reason} の明示却下。
        history_file: テスト用の store 上書き。
    """
    if slug is None:
        import evolve_decisions as _ed

        slug = _ed.resolve_slug(Path(project_dir) if project_dir else None)

    # #287-3: スナップショットと purge をそれぞれロック下で行い、**ingest はロック外**に置く
    # （ingest は skill_quality 採点で秒オーダーになりうるので、握ると同一 slug の emit と
    # SessionStart hook を飢餓させる）。TOCTOU は世代キー（`entry_generation`）で防ぐ。
    # ロック下では公開版でなく `_locked` / `_read_pending_marker_file` を使う（自己 deadlock）。
    # #576: result_json 分岐は marker を読まないのでロックを取らない（lock 取得自体が
    # MARKER_ROOT への書込＝read-only home で落ちる）。ロックが要るのは marker 分岐と、
    # 実際に purge するときだけ。
    orphaned_entries: List[Dict[str, Any]] = []
    if result_json:
        data = json.loads(Path(result_json).read_text(encoding="utf-8"))
        envelope = data.get("evolve_decisions") or {}
        pending = envelope.get("pending") or []
    else:
        with _marker_lock(slug):
            marker = _read_pending_marker_file(slug)
            all_pending = (marker.get("pending") if marker else None) or []
            # #376 AC5: 削除済み worktree の pending は orphan として先に切り離す
            # （ingest に渡さない＝永遠に skip され続ける残骸を作らない）。
            pending, orphaned_entries = _partition_orphaned(all_pending)

    summary = ingest_decisions(
        slug, pending=pending, dry_run=False,
        accepted=accepted, rejected=rejected, history_file=history_file,
    )
    consumed = set(summary["accepted"]) | set(summary["rejected"])
    remaining = [entry for entry in pending if entry.get("id") not in consumed]
    # 未判断は deferred として marker に残し、後続 run で apply/reject できるようにする。
    summary["deferred"] = [entry.get("id") for entry in remaining]
    summary["orphaned"] = [entry.get("id") for entry in orphaned_entries]
    generations = {
        entry_generation(entry) for entry in pending if entry.get("id") in consumed
    } | {entry_generation(entry) for entry in orphaned_entries}
    purge_ids = consumed | set(summary["orphaned"])
    if purge_ids:
        with _marker_lock(slug):
            _purge_marker_entries_locked(slug, purge_ids, generations=generations)
    return summary
