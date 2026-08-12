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

#402 PR-2 段階1（lock protocol）: dry-run 経路は書込ゼロの `read_only_file_lock` +
seqlock 型 check-after（design_402_pr2_v2.md §0.2）で disk 内容 + generation を読む。
非 dry-run 経路（実書込あり）は従来どおり `file_lock`（blocking・sidecar を自動作成）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import optimize_history_store as _store
from evolve_decision_ids import (
    compress_before_for_revert,
    filter_monotonic_pending,
    is_superseded,
    new_run_id,
    path_scope_identity,
    proposal_id_from_identity,
    repo_identity,
    revert_generation_for_target,
    sha256,
    supersede_keys,
    REVERT_ENCODING,
    REVERT_SCHEMA_VERSION,
)
from rl_common.file_lock import file_lock, read_only_file_lock

from ._candidates import _advisory_pending, _extract_candidates
from ._marker import clear_pending_marker, read_pending_marker, write_pending_marker
from ._queue import _queue_lock, _write_queue, read_queue

# MVP 対象は discover の matched_skills（#223/Step 3 と同じスキル diff クラス）。
# skill_evolve / remediation への拡張は均質性を崩さないため follow-up（ADR-041）。
FITNESS_FUNC = "skill_quality"

# #402 PR-2 §0.2: dry-run の sidecar 「不在→出現」チェック再試行の上限。単調性が守られる
# 正常系ではこの遷移は一度しか起きないため、通常は1回の再試行で locked 経路へ移る。何度も
# 再試行が必要な状態は単調性違反・path の不安定化・観測エラーのいずれかなので、「最大 N
# 回」という値そのものより「超過時に何を公開しないか」の契約の方が重要（設計 §0.2）。
_DRY_RUN_SNAPSHOT_MAX_RETRIES = 5


class EmitSnapshotRetriesExhausted(RuntimeError):
    """#402 PR-2 §0.2: dry-run の check-after が retries 上限を超えても安定しなかった。

    emit 全体を失敗させる契約（呼び出し元が握り潰して成功扱いにしてはならない）:
    新しい pending を queue / marker / result のいずれにも公開しない。既存 pending は
    変更も削除もしない。この例外は queue/marker への書込より前、snapshot 確定の時点で
    送出されるため「marker を先に公開してから失敗する」順序にはならない。
    """


def _read_disk_and_history(
    slug: str, candidates: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Optional[str]]]]:
    """history と各 candidate の disk 内容を読む（#402 決定8: history lock 区間内 or
    seqlock の暫定 snapshot 区間内で呼ばれる前提）。読めなかった候補は content=None。
    """
    history = _store.load_history(slug)
    reads: List[Tuple[Dict[str, Any], Optional[str]]] = []
    for c in candidates:
        try:
            content = Path(c["skill_path"]).read_text(encoding="utf-8")
        except OSError:
            content = None
        reads.append((c, content))
    return history, reads


def _sidecar_monotonicity_warning(
    history: List[Dict[str, Any]], lock_path: Path
) -> Optional[str]:
    """#402 PR-2 §0.3: history に revert イベントがあるのに sidecar が不在なら異常の痕跡。

    fail させず warn のみ（良性シナリオ: data dir 移送・バックアップ復元で jsonl は運ばれ
    ても sidecar が付いてこないことがある。dry-run は daily runner の無人経路でもあるため、
    fail に倒すと該当 PJ を無人経路で毎朝黙って殺す）。現在の読み取りの正しさには影響しない
    （読区間中に revert が動けば sidecar は必ず存在するため、過去の外部削除は無関係）。
    """
    if not any(rec.get("event_type") == "revert" for rec in history):
        return None
    return (
        f"#402: history に revert イベントが存在するのに lock sidecar が不在です "
        f"({lock_path})。sidecar 単調性契約（一度作られたら削除されない）違反の痕跡ですが、"
        "現在の読み取りの正しさには影響しないため続行します。回復方法: 次に何らかの決定"
        "（accept/reject/revert）が記録されると、通常の file_lock 経由で sidecar が"
        "自動的に再作成されます。"
    )


def _dry_run_snapshot(
    history_file: Path, slug: str, candidates: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Optional[str]]], Optional[str]]:
    """#402 PR-2 §0.2/§0.3: dry-run の書込ゼロ check-after（seqlock 型）。

    Returns: (history, [(candidate, disk_content_or_None), ...], warning_or_None)
    """
    lock_path = history_file.with_name(history_file.name + ".lock")
    for _ in range(_DRY_RUN_SNAPSHOT_MAX_RETRIES):
        with read_only_file_lock(lock_path) as acquired:
            if acquired:
                history, reads = _read_disk_and_history(slug, candidates)
                return history, reads, None
            # 不在（§0.2 手順2）: lock 無しで読む＝暫定 snapshot。
            history, reads = _read_disk_and_history(slug, candidates)
        # §0.2 手順3: 読了後に sidecar の不在を再確認する。
        if not lock_path.exists():
            return history, reads, _sidecar_monotonicity_warning(history, lock_path)
        # 出現していた → 暫定 snapshot を破棄し、1（read_only_file_lock 再試行）へ戻る。
    raise EmitSnapshotRetriesExhausted(
        f"dry-run history snapshot: sidecar keeps appearing/disappearing for {lock_path} "
        f"after {_DRY_RUN_SNAPSHOT_MAX_RETRIES} attempts"
    )


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
    run_id = run_id or new_run_id()

    # #402 決定8: emit は同じ history lock 内で「対象の disk 内容読み」と「generation
    # 読み」の両方を行う（revert の [復元→revert イベント追記] が1区間に閉じている
    # 限り、この lock 内で読んだ内容は revert の中間状態を観測しない）。queue/marker
    # への書込は lock 解放後に行う — history と queue/marker を同時保持しない
    # （emit/drain/revert のどの2つを同時に走らせても deadlock しない・決定8）。
    #
    # #402 PR-2 §0.2: dry_run 時は書込を伴う `file_lock` を無条件には取らない（open(path,
    # "a") で sidecar を必ず作るため、dry-run 純度契約「1バイトも書かない」を破る）。
    # 代わりに書込ゼロの `read_only_file_lock` + seqlock 型 check-after（`_dry_run_snapshot`）
    # で disk 内容と generation を読む。sidecar 不在時の「不在→出現」レースは check-after
    # が検出して読み直す。読区間全体で不在のままなら、その暫定 snapshot が revert の
    # 中間状態を観測していないことが単調性契約（§0.3）から保証される。
    history_file = _store.history_path(slug)
    candidates = _extract_candidates(result)
    snapshot_warning: Optional[str] = None
    if dry_run:
        history, reads, snapshot_warning = _dry_run_snapshot(history_file, slug, candidates)
    else:
        with file_lock(history_file.with_name(history_file.name + ".lock")):
            history, reads = _read_disk_and_history(slug, candidates)

    pending: List[Dict[str, Any]] = []
    for c, before in reads:
        if before is None:
            continue  # 読めないスキルは対象外
        before_sha = sha256(before)
        # identity は1回だけ解決し、id 計算（repo 相対パス基準・#376 AC4）と
        # worktree_root（orphan 判定用・#376 AC5）の両方に使い回す。
        identity = repo_identity(c["skill_path"])
        # #402 決定5: path 契約（project/global scope・提案 identity とは独立）。
        path_identity = path_scope_identity(c["skill_path"])
        # #402 決定2 Should3: 圧縮後サイズが上限を超えたら本文を落とす。
        before_b64, unavailable_reason = compress_before_for_revert(before)
        # #402 決定4: 対象パス単位の revert 累積回数を emit 時にスナップショット。
        revert_generation = revert_generation_for_target(
            history,
            path_identity["scope"],
            path_identity["repo_id"],
            path_identity["relative_path"],
        )
        pending.append(
            {
                "id": proposal_id_from_identity(identity, before_sha),
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
            kept_pending, queue_discarded = filter_monotonic_pending(existing, pending)
            revert_generation_discarded += queue_discarded
            # supersede は marker と同じ対象パス単位（ID 一致だけだと世代が queue に residue
            # し 1回の apply が全世代 accept 判定になる＝#290 の queue 経路・#287-1）。
            ids, paths = supersede_keys(kept_pending)
            _write_queue(
                slug,
                [e for e in existing if not is_superseded(e, ids, paths)] + kept_pending,
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
        # #402 PR-2 §0.3: sidecar 単調性契約違反の痕跡（warn + 続行）。異常なしなら None。
        # dry_run のみ検出対象（非 dry_run は file_lock が sidecar を必ず作るため不在なし）。
        "dry_run_snapshot_warning": snapshot_warning,
    }
