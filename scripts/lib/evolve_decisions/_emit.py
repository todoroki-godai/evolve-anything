"""Phase A: `emit_decisions`（`evolve_decisions` パッケージ分割・#383）。

run_evolve 末尾で呼ばれる。スキル diff 候補の before_sha をキューにスナップショットする。
振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が re-export し後方互換を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `resolve_slug`（`_queue.py`）
は test の `monkeypatch.setattr(evolve_decisions, "resolve_slug", ...)` 対象なので
`import evolve_decisions as _ed` 経由で呼ぶ。`_extract_candidates` / `_advisory_pending`
（`_candidates.py`）と `read_queue` / `_write_queue` / `_queue_lock`（`_queue.py`）、
`write_pending_marker` / `read_pending_marker` / `clear_pending_marker`（`_marker.py`）は
monkeypatch 対象でないため sub-module から直接 import してよい。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from evolve_decision_ids import (
    _is_superseded,
    _new_run_id,
    _proposal_id_from_identity,
    _repo_identity,
    _sha256,
    _supersede_keys,
)

from ._candidates import _advisory_pending, _extract_candidates
from ._marker import clear_pending_marker, read_pending_marker, write_pending_marker
from ._queue import _queue_lock, _write_queue, read_queue

# MVP 対象は discover の matched_skills（#223/Step 3 と同じスキル diff クラス）。
# skill_evolve / remediation への拡張は均質性を崩さないため follow-up（ADR-041）。
FITNESS_FUNC = "skill_quality"


def emit_decisions(
    result: Dict[str, Any],
    project_dir: Optional[str] = None,
    *,
    dry_run: bool = False,
    slug: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """run_evolve 末尾。スキル diff 候補の before_sha をキューにスナップショットする。

    dry_run 時は pending を計算するが**書き込まない**（pitfall_dryrun_stateful_store_write）。
    返り値の pending は report 用（dry_run でも見せる）。
    """
    if slug is None:
        import evolve_decisions as _ed

        slug = _ed.resolve_slug(Path(project_dir) if project_dir else None)
    run_id = run_id or _new_run_id()

    pending: List[Dict[str, Any]] = []
    for c in _extract_candidates(result):
        try:
            before = Path(c["skill_path"]).read_text(encoding="utf-8")
        except OSError:
            continue  # 読めないスキルは対象外
        before_sha = _sha256(before)
        # identity は1回だけ解決し、id 計算（repo 相対パス基準・#376 AC4）と
        # worktree_root（orphan 判定用・#376 AC5）の両方に使い回す。
        identity = _repo_identity(c["skill_path"])
        pending.append(
            {
                "id": _proposal_id_from_identity(identity, before_sha),
                "run_id": run_id,
                "skill_name": c["skill_name"],
                "skill_path": c["skill_path"],
                "worktree_root": identity.get("worktree_root"),
                "before_sha": before_sha,
                "fitness_func": FITNESS_FUNC,
                "pattern": c["pattern"],
                "proposal_type": c.get("proposal_type", "skill_diff"),
            }
        )

    # #284: advisory detector を同じ lane に載せる。detector が壊れてもスキル提案の
    # emit は落とさない（advisory は付加価値レーン）。
    seen_ids = {entry["id"] for entry in pending}
    try:
        for entry in _advisory_pending(project_dir, run_id):
            if entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                pending.append(entry)
    except Exception:
        pass

    persisted = False
    marker_written = False
    marker_cleared = False
    marker_error: Optional[str] = None
    if not dry_run:
        # run_id 無しは旧 schema の「前 run を上書き」キュー。新 envelope へ移る際に
        # stale として除去し、run_id 付きの未判断だけを保持する。
        # read→編集→write はロック下で行う（並行 emit が互いの追加を落とすのを防ぐ・#287-1）。
        # supersede は marker と同じ対象パス単位（ID 一致だけだと世代が queue に residue し
        # 1回の apply が全世代 accept 判定になる＝#290 の queue 経路・#287-1）。
        ids, paths = _supersede_keys(pending)
        with _queue_lock(slug):
            existing = [entry for entry in read_queue(slug) if entry.get("run_id")]
            _write_queue(
                slug,
                [e for e in existing if not _is_superseded(e, ids, paths)] + pending,
            )
        persisted = True

    # #402: drain 検出用の運用マーカー（dry-run でも書く。store/queue とは別状態）。
    # 候補ゼロなら古いマーカーを消す（drain 待ちが無いので沈黙させる）。
    # #513: 標準フローは dry-run 分析のみなので、ここをゲートすると emit→drain 捕捉
    # （ADR-041）が全死する（#505 の誤ゲートを revert）。marker は「文書化された
    # 意図的 dry-run 書込」であり、SHA256 不変契約側が evolve_pending/ を原則除外する。
    try:
        if pending:
            write_pending_marker(slug, pending, run_id=run_id)
            marker_written = True
        else:
            marker = read_pending_marker(slug)
            runs = (marker or {}).get("runs", [])
            # 旧 schema の stale marker だけは従来どおり候補ゼロ run で掃除する。
            # run envelope を持つ marker は他 session の drain 待ちかもしれないので触らない。
            if runs and all(str(run.get("run_id", "")).startswith("legacy_") for run in runs):
                marker_cleared = clear_pending_marker(slug)
    except OSError as e:
        # #287-5: 握り潰すと権限不足・ディスクフルでも emit が成功扱いになる。標準フロー
        # （dry-run → 適用 → drain）では marker が pending の唯一の情報源なので、書けて
        # いなければ判断がまるごと失われる。emit 自体は落とさず（他の phase 結果は返す）
        # 構造化 warning として surface し、CLI 1 行サマリにも出す。
        marker_error = f"{type(e).__name__}: {e}"

    return {
        "pending": pending,
        "count": len(pending),
        "persisted": persisted,
        "slug": slug,
        "run_id": run_id,
        "marker_written": marker_written,
        "marker_cleared": marker_cleared,
        "marker_error": marker_error,
    }
