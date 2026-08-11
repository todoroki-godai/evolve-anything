"""Phase C: `ingest_decisions`（`evolve_decisions` パッケージ分割・#383）。

Step 7.8 drain の実体。振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が re-export し
後方互換を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `_load_recorder`
（`_candidates.py`）は test の `monkeypatch.setattr(evolve_decisions, "_load_recorder", ...)`
対象なので `import evolve_decisions as _ed` 経由で呼ぶ。`read_queue` / `_write_queue` /
`_queue_lock`（`_queue.py`）は monkeypatch 対象でないため直接 import してよい。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import optimize_history_store as _store
from evolve_decision_ids import _decision_event_id, _sha256, _tracked_path

from ._queue import _queue_lock, _write_queue, read_queue


def ingest_decisions(
    slug: str,
    *,
    accepted: Optional[Any] = None,
    rejected: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
    history_file: Optional[Path] = None,
    pending: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Step 7.8 drain。各 pending を分類して optimize_history に記録する。

      id in accepted AND after_sha != before_sha（明示 accept + 適用実績）→ accept
      id in rejected（明示却下）                                        → reject（human_accepted=False, reason）
      証跡なし（未決定 or diff だけ）                                    → skip（記録しない）

    #376（AC1/AC2）: accept は「ディスク差分があった」だけでは成立しない。かつて
    ``after_sha != before_sha`` のみを accept 条件にしていたため、evolve 提案とは
    無関係な通常 commit で対象ファイルがたまたま変わっただけのケースまで accept と
    誤記録していた（optimize_history の母集団が汚染される）。``before_sha`` は捨てず
    整合性ガードとして残す（明示 accept はあっても実際に適用されていなければ pending
    のまま＝skip）。``accepted`` は id の集合（Set[str] / Iterable[str]）で、
    「この提案をユーザーが承認した」という明示的な decision イベントを表す
    （評価詳細プロトコルの AskUserQuestion 承認から Step 7.8 へ inline で渡す）。

    accept/reject は record_evolve_diff_decision 経由で optimize_history へ冪等記録。

    pending のソース（#400 バグ#1 根治）:
      - `pending=None`（既定）: キュー `DATA_DIR/evolve_decisions/<slug>.jsonl` から読む。
        消化済みをキューから消す（非 dry_run 時）。
      - `pending=[...]` を明示渡し: `result.evolve_decisions.pending` を直接消費する。
        **dry-run 運用フロー専用の経路** — `evolve --dry-run` では emit がキューを
        書かないため、result 同梱の pending（before_sha 付き）を渡すことで apply 後の
        ディスク差分から accept を記録できる。この場合キューは SoT でないため触らない。
    """
    accepted_ids: Set[str] = set(accepted) if accepted else set()
    rejected = rejected or {}
    from_queue = pending is None
    if from_queue:
        pending = read_queue(slug)
    if history_file is None:
        history_file = _store.history_path(slug)
    else:
        history_file = Path(history_file)

    accepted_out: List[str] = []
    rejected_out: List[str] = []
    skipped: List[str] = []
    recorder = None

    for entry in pending:
        pid = entry["id"]
        tracked = _tracked_path(entry)
        try:
            after = Path(tracked).read_text(encoding="utf-8") if tracked else None
        except OSError:
            after = None
        after_sha = _sha256(after) if after is not None else None
        applied = after_sha is not None and after_sha != entry.get("before_sha")

        # #376 AC1: accept は「明示的な decision イベント（accepted_ids）」と「実際に
        # 適用された（applied）」の AND。applied 単独では accept にしない（無関係な通常
        # commit の誤帰属防止）。accepted のみで applied が無い（未適用のまま承認だけ
        # された）場合も、後続 run で apply されるまで pending のまま（skip）。
        if pid in accepted_ids and applied:
            kind, after_content, reason = "accept", after, None
        elif pid in rejected:
            kind, after_content, reason = "reject", (after if after is not None else ""), rejected[pid]
        else:
            skipped.append(pid)
            continue

        if not dry_run and entry.get("proposal_type") == "advisory":
            # advisory は対象が pytest.ini / rules など異種なので skill_quality の
            # 母集団（optimize_history）に入れず専用ストアへ記録する（#284）。
            from advisory_decision_log import record_advisory_decision

            record_advisory_decision(
                slug=slug,
                proposal_id=pid,
                detector_id=str(entry.get("detector_id") or "unknown"),
                target_path=str(tracked or ""),
                decision=kind,
                run_id=entry.get("run_id"),
                reason=reason,
            )
        elif not dry_run:
            if recorder is None:
                import evolve_decisions as _ed

                recorder = _ed._load_recorder()
            recorder(
                skill_name=entry["skill_name"],
                after_content=after_content,
                diff_summary=f"evolve diff {kind}ed: {entry.get('pattern', '')[:60]}",
                human_accepted=(kind == "accept"),
                rejection_reason=reason,
                history_file=history_file,
                entry_id=_decision_event_id(pid, kind, after_content),
                # #267 Sprint 1: pending entry の run_id（emit 時の run envelope）を
                # optimize_history へ純加算する。queue の verify_pending が読む。
                run_id=entry.get("run_id"),
                # #376: この記録が明示的な decision イベント由来であることの出所ラベル。
                # legacy_accept_migration.py がこのフィールドの有無で旧 hash-proxy 単独
                # 判定の記録（値なし）と新契約の記録（値あり）を判別する。
                decision_source=f"explicit_{kind}",
            )
        (accepted_out if kind == "accept" else rejected_out).append(pid)

    if not dry_run and from_queue:
        # キューが SoT のときだけ消化済みを除去する。pending を直接渡された場合
        # （dry-run 運用経路）はキューを生成も変更もしない。
        # 未判断は deferred。後続 run で apply/reject できるようキューに残す。
        # 判断（ファイル読み・採点）は重いのでロック外で行い、**書く直前にロック下で
        # 読み直して**差分だけ適用する（その間に別 run が追加した entry を消さない・#287-1）。
        consumed = set(accepted_out) | set(rejected_out)
        with _queue_lock(slug):
            _write_queue(slug, [e for e in read_queue(slug) if e.get("id") not in consumed])

    return {"accepted": accepted_out, "rejected": rejected_out, "skipped": skipped}
