"""Phase A: `emit_decisions`（`evolve_decisions` パッケージ分割・#383）。

run_evolve 末尾で呼ばれる。スキル diff 候補の before_sha をキューにスナップショットする。
振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が re-export し後方互換を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `resolve_slug`（`_queue.py`）
は test の `monkeypatch.setattr(evolve_decisions, "resolve_slug", ...)` 対象なので
`import evolve_decisions as _ed` 経由で呼ぶ。`_extract_candidates` / `_advisory_pending`
（`_candidates.py`）と `read_queue` / `_write_queue` / `_queue_lock`（`_queue.py`）、
`write_pending_marker` / `read_pending_marker` / `clear_pending_marker`（`_marker.py`）は
monkeypatch 対象でないため sub-module から直接 import してよい。

#402 PR-1（revert 用「記録拡張」）: skill diff 候補（advisory は対象外・確定した前提）に
before 全文の圧縮本文 + path 契約 + emit 時 generation スナップショットを付ける。詳細は
design_402_v6.md の決定1/2/4/5/8。
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

import optimize_history_store as _store
from evolve_decision_ids import (
    _compress_before_for_revert,
    _filter_monotonic_pending,
    _is_superseded,
    _new_run_id,
    _path_scope_identity,
    _proposal_id_from_identity,
    _repo_identity,
    _revert_generation_for_target,
    _sha256,
    _supersede_keys,
    REVERT_ENCODING,
    REVERT_SCHEMA_VERSION,
)
from rl_common.file_lock import file_lock

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

    # #402 決定8: emit は同じ history lock 内で「対象の disk 内容読み」と「generation
    # 読み」の両方を行う（revert の [復元→revert イベント追記] が1区間に閉じている
    # 限り、この lock 内で読んだ内容は revert の中間状態を観測しない）。queue/marker
    # への書込は lock 解放後に行う — history と queue/marker を同時保持しない
    # （emit/drain/revert のどの2つを同時に走らせても deadlock しない・決定8）。
    # dry_run 時はロックを取らない（`file_lock` は open(path, "a") で sidecar を必ず
    # 作るため、無条件に取ると dry-run 純度契約「1バイトも書かない」を破る。queue lock
    # と同じく実書込が起きる時だけ取る）。
    # ⚠️ dry-run の generation 読みは非ロックのスナップショットであり、その結果（marker /
    # result 同梱 pending）は**同一プロセス内で完結する保証が無い** — marker は後から
    # 別プロセス・別セッションの `evolve --drain` で読まれうる（#402）。つまり round4 の
    # monotonic supersede ガードが守るのは「queue/marker への公開順序」のみで、この
    # dry-run 読み取り自体が古い generation を later-drain まで持ち越すレースは PR-1 の
    # 対象外（PR-1 時点は revert writer 不在で generation は常に 0 のため実害なし）。
    # PR-2 で revert writer を入れる前に、(a) 書込なしで既存 sidecar のロックだけ取得する
    # 仕組み、または (b) apply 直前に history lock を取り直す locked re-snapshot の
    # いずれかを設計し、契約テストで固定する必要がある。
    history_file = _store.history_path(slug)
    pending: List[Dict[str, Any]] = []
    lock_cm = (
        nullcontext()
        if dry_run
        else file_lock(history_file.with_name(history_file.name + ".lock"))
    )
    with lock_cm:
        history = _store.load_history(slug)
        for c in _extract_candidates(result):
            try:
                before = Path(c["skill_path"]).read_text(encoding="utf-8")
            except OSError:
                continue  # 読めないスキルは対象外
            before_sha = _sha256(before)
            # identity は1回だけ解決し、id 計算（repo 相対パス基準・#376 AC4）と
            # worktree_root（orphan 判定用・#376 AC5）の両方に使い回す。
            identity = _repo_identity(c["skill_path"])
            # #402 決定5: path 契約（project/global scope・提案 identity とは独立）。
            path_identity = _path_scope_identity(c["skill_path"])
            # #402 決定2 Should3: 圧縮後サイズが上限を超えたら本文を落とす。
            before_b64, unavailable_reason = _compress_before_for_revert(before)
            # #402 決定4: 対象パス単位の revert 累積回数を emit 時にスナップショット。
            revert_generation = _revert_generation_for_target(
                history,
                path_identity["scope"],
                path_identity["repo_id"],
                path_identity["relative_path"],
            )
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
                    # #402 決定1/2: revert 復旧用の本文（圧縮・emit→queue→drain で運搬）。
                    "revert_before_b64": before_b64,
                    "revert_unavailable_reason": unavailable_reason,
                    "revert_schema_version": REVERT_SCHEMA_VERSION,
                    "revert_encoding": REVERT_ENCODING,
                    "revert_generation": revert_generation,
                    # #402 決定5: path 契約。
                    "repo_id": path_identity["repo_id"],
                    "relative_path": path_identity["relative_path"],
                    "scope": path_identity["scope"],
                    "resolved_path": path_identity["resolved_path"],
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
    revert_generation_discarded = 0
    if not dry_run:
        # run_id 無しは旧 schema の「前 run を上書き」キュー。新 envelope へ移る際に
        # stale として除去し、run_id 付きの未判断だけを保持する。
        # read→編集→write はロック下で行う（並行 emit が互いの追加を落とすのを防ぐ・#287-1）。
        # #402 決定8 round4: monotonic supersede ガード — 既存より小さい generation の
        # pending は公開せず捨てる（先に filter してから supersede 対象を決める。捨てた
        # entry の path を supersede 対象に含めると、まだ生きている高 generation の
        # 既存 entry まで巻き込んで消してしまう）。
        with _queue_lock(slug):
            existing = [entry for entry in read_queue(slug) if entry.get("run_id")]
            kept_pending, queue_discarded = _filter_monotonic_pending(existing, pending)
            revert_generation_discarded += queue_discarded
            # supersede は marker と同じ対象パス単位（ID 一致だけだと世代が queue に residue
            # し 1回の apply が全世代 accept 判定になる＝#290 の queue 経路・#287-1）。
            ids, paths = _supersede_keys(kept_pending)
            _write_queue(
                slug,
                [e for e in existing if not _is_superseded(e, ids, paths)] + kept_pending,
            )
        persisted = True

    # #402: drain 検出用の運用マーカー（dry-run でも書く。store/queue とは別状態）。
    # 候補ゼロなら古いマーカーを消す（drain 待ちが無いので沈黙させる）。
    # #513: 標準フローは dry-run 分析のみなので、ここをゲートすると emit→drain 捕捉
    # （ADR-041）が全死する（#505 の誤ゲートを revert）。marker は「文書化された
    # 意図的 dry-run 書込」であり、SHA256 不変契約側が evolve_pending/ を原則除外する。
    try:
        if pending:
            # write_pending_marker 自身が monotonic supersede ガード（決定8 round4）を
            # 適用し、捨てた件数を返す。
            revert_generation_discarded += write_pending_marker(slug, pending, run_id=run_id)
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
        # #402 決定8 round4: monotonic supersede ガードで queue/marker から捨てた件数
        # （新しい observability section は作らない・#379 Step2 凍結に非抵触の meta 返却）。
        "revert_generation_discarded": revert_generation_discarded,
    }
