"""evolve_revert._apply — apply engine 本体（#402 段階3 §2 手順3-5）。

entry 検索 → 対象解決（``_target``）→ 3分岐（正常系/冪等/conflict）→ 復元 → 再検証 →
revert イベント追記、を1つの関数にまとめる。**手順3〜5 は同一 history lock 内**
（決定8: revert は ``[ファイル復元 → revert イベント追記]`` を1つの history lock
区間に閉じる）。

耐障害範囲（S8）: 本モジュールが保証するのは通常のプロセスクラッシュまで。電源断は
対象外（temp/directory/history append の fsync 順序固定は行わない）。この範囲では
中断状態は「復元済み・イベント欠落」の1種類に保たれ、idempotent 分岐（S7）が復旧する。

自己 deadlock について（C26）: 本モジュールが呼ぶ ``optimize_history_store`` の関数
（``load_raw_history_with_aliases`` / ``append_entry``）はいずれも自前ではロックを
取らない（呼び出し側がロックを保持している前提の関数）ため、``_locked`` 版を分ける
必要が構造的に無い（history lock を二重に取得する経路が無い）。
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import optimize_history_store as _store
from evolve_decision_ids import (
    decompress_before_content,
    generation_of,
    revert_event_id,
    sha256,
)
from rl_common.file_lock import file_lock, seqlock_read

from ._entry import find_entry
from ._metadata import (
    LossReport,
    classify_losses,
    detect_drift,
    preview_losses,
    snapshot_from_fd,
    snapshot_from_path,
)
from ._render import (
    build_diff_summary,
    render_apply_success,
    render_conflict_message,
    render_dry_run_preview,
    render_hardlink_rejection,
    render_metadata_loss_rejection,
)
from ._target import REASON_HARDLINK, resolve_target

BRANCH_NORMAL = "normal"
BRANCH_IDEMPOTENT = "idempotent"
BRANCH_CONFLICT = "conflict"

REASON_ENTRY_NOT_FOUND = "entry_not_found"
REASON_BEFORE_UNAVAILABLE = "before_unavailable"
REASON_AFTER_SHA_MISSING = "after_sha_missing"
REASON_METADATA_LOSS = "metadata_loss"
REASON_DRIFT = "drift"


@dataclass(frozen=True)
class ApplyResult:
    """``apply_revert`` の結果。段階4 の CLI はこれを引数なしで薄くレンダリングする。"""

    ok: bool
    dry_run: bool
    entry_id: str
    slug: Optional[str] = None
    branch: Optional[str] = None
    reason: Optional[str] = None
    message: str = ""
    target_path: Optional[str] = None
    losses: Optional[LossReport] = None
    revert_event_id: Optional[str] = None
    revert_generation: Optional[int] = None
    nlink: Optional[int] = None
    diff: Optional[Dict[str, Any]] = None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_current(path: Path) -> Tuple[Optional[str], str, bytes]:
    """現ディスク内容を読む（path 経由。§2 手順4 の fd 保持契約は identity/owner/mode/
    xattr/flags のスナップショットに適用し、内容読みは既存の ``sha256`` 慣習に揃える）。

    decode 不能（binary 等）なら ``text=None`` を返す。この場合 ``sha`` は
    ``before_sha``/``after_sha``（いずれも有効な UTF-8 由来の sha256 hex）と構造的に
    一致し得ないため、3分岐判定は自然に conflict へ落ちる（C9 の「binary または decode
    不能」表示に対応）。対象が消えている等の OSError も同様に扱う。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "", b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, _sha256_bytes(raw), raw
    # Path.read_text() 相当の universal newline 変換（既存の before_sha/after_sha は
    # read_text 経由で計算されているため、比較対象を同じ変換規約に揃える）。
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized, sha256(normalized), raw


def _append_revert_event(
    entry: Dict[str, Any], entry_id: str, event_id: str, generation: int, slug: str
) -> None:
    """revert イベントを追記する（M-E: 既存の ``append_entry`` を経由。新ストアを作らない）。

    ``timestamp`` は明示せず ``append_entry`` の ``normalize_entry_timestamp`` に
    正規化させる（既存 writer との規約に揃える）。
    """
    event = {
        "event_type": _store.REVERT_EVENT_TYPE,
        "reverted_entry_id": entry_id,
        "revert_event_id": event_id,
        "revert_generation": generation,
        "scope": entry.get("scope"),
        "repo_id": entry.get("repo_id"),
        "relative_path": entry.get("relative_path"),
        "skill_name": entry.get("skill_name"),
    }
    _store.append_entry(event, slug)


def _restore_normal(
    *,
    entry_id: str,
    entry: Dict[str, Any],
    slug: str,
    target_path: Path,
    before_content: str,
    before_sha: str,
    after_sha: str,
    source_fd: int,
    initial,
    event_id: str,
    next_generation: int,
    allow_metadata_loss: bool,
) -> ApplyResult:
    """正常系（== after_sha）の実書込パス。同一ディレクトリに temp 生成 → 復元後 sha
    再検証 → mode 引き継ぎ → **replace 直前の再検証（C22）** → atomic replace →
    revert イベント追記、の順に行う。
    """
    tmp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(before_content, encoding="utf-8")
        # 復元後 sha が before_sha と一致することを再検証（自分の書込の自己検証）。
        restored_sha = sha256(tmp_path.read_text(encoding="utf-8"))
        if restored_sha != before_sha:
            return ApplyResult(
                ok=False, dry_run=False, entry_id=entry_id, slug=slug,
                branch=BRANCH_NORMAL, reason="restore_verification_failed",
                message="復元後の内容が before_sha と一致しませんでした（内部エラー）。",
                target_path=str(target_path),
            )
        os.chmod(tmp_path, initial.mode)

        # ── replace 直前の再検証（C22・6項目）─────────────────────────
        current_text2, current_sha2, current_raw2 = _read_current(target_path)
        if current_sha2 != after_sha:
            diff = build_diff_summary(
                before_text=before_content, current_text=current_text2,
                current_bytes=current_raw2, before_sha=before_sha, current_sha=current_sha2,
            )
            return ApplyResult(
                ok=False, dry_run=False, entry_id=entry_id, slug=slug,
                branch=BRANCH_CONFLICT, reason=REASON_DRIFT,
                message=render_conflict_message(entry_id, diff),
                target_path=str(target_path),
            )

        current_meta = snapshot_from_fd(source_fd)  # C23: source_fd を保持し続けて使う
        drift = detect_drift(initial, current_meta)
        if drift:
            return ApplyResult(
                ok=False, dry_run=False, entry_id=entry_id, slug=slug,
                branch=BRANCH_CONFLICT, reason=REASON_DRIFT,
                message=(
                    f"対象ファイルが検査中に変化しました（{drift}）。安全のため中止し"
                    "ました。内容をご確認のうえ再実行してください。"
                ),
                target_path=str(target_path),
            )

        temp_meta = snapshot_from_path(tmp_path)
        if temp_meta.mode != current_meta.mode:
            # v2 round4 codex [Should]: source が手順2 から変わっていないだけでは
            # temp 側への mode 引き継ぎが実際に効いたことを保証しない（内部整合性）。
            return ApplyResult(
                ok=False, dry_run=False, entry_id=entry_id, slug=slug,
                branch=BRANCH_CONFLICT, reason=REASON_DRIFT,
                message="temp への mode 引き継ぎを確認できませんでした（内部エラー）。",
                target_path=str(target_path),
            )

        losses = classify_losses(current_meta, temp_meta)
        if losses.blocking and not allow_metadata_loss:
            return ApplyResult(
                ok=False, dry_run=False, entry_id=entry_id, slug=slug,
                branch=BRANCH_NORMAL, reason=REASON_METADATA_LOSS,
                message=render_metadata_loss_rejection(entry_id, losses),
                target_path=str(target_path), losses=losses,
            )

        os.replace(tmp_path, target_path)
        _append_revert_event(entry, entry_id, event_id, next_generation, slug)
        return ApplyResult(
            ok=True, dry_run=False, entry_id=entry_id, slug=slug,
            branch=BRANCH_NORMAL, message=render_apply_success(),
            target_path=str(target_path), losses=losses,
            revert_event_id=event_id, revert_generation=next_generation,
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def apply_revert(
    entry_id: str,
    *,
    slug: Optional[str] = None,
    dry_run: bool = True,
    allow_metadata_loss: bool = False,
) -> ApplyResult:
    """entry_id を revert する（設計正典 §2 手順1-5）。段階4 の CLI が薄く呼ぶ主エントリ。

    - ``dry_run``（既定 True）: 対象ファイル・history lock sidecar・temp・history へ
      **ゼロ書込**（C28）。branch 判定 + losses の近似 preview のみ返す
    - ``allow_metadata_loss``: 手順2 の初回検査で既に存在していたメタデータ損失
      （所有者・xattr・flags）のみ override できる。観測後の drift・検査失敗・
      hardlink は override 不可（C24）
    """
    lookup = find_entry(entry_id, slug)
    if lookup.entry is None:
        return ApplyResult(
            ok=False, dry_run=dry_run, entry_id=entry_id, slug=lookup.slug,
            reason=REASON_ENTRY_NOT_FOUND, message=f"entry_id が見つかりません: {entry_id}",
        )
    entry = lookup.entry
    result_slug = lookup.slug

    before_b64 = entry.get("revert_before_b64")
    if not before_b64:
        return ApplyResult(
            ok=False, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
            reason=REASON_BEFORE_UNAVAILABLE,
            message=(
                "この entry は before 本文が保存されていないため revert できません"
                f"（理由: {entry.get('revert_unavailable_reason')}）。"
            ),
        )
    after_sha = entry.get("after_sha")
    if not after_sha:
        return ApplyResult(
            ok=False, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
            reason=REASON_AFTER_SHA_MISSING,
            message=(
                "この entry には after_sha が記録されていないため revert できません"
                "（schema が古い可能性があります）。"
            ),
        )

    resolution = resolve_target(entry)
    if not resolution.ok:
        message = (
            render_hardlink_rejection(resolution.nlink)
            if resolution.reason == REASON_HARDLINK
            else f"対象パスを解決できません（理由: {resolution.reason}）。"
        )
        return ApplyResult(
            ok=False, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
            reason=resolution.reason, message=message,
            target_path=str(resolution.path) if resolution.path else None,
            nlink=resolution.nlink,
        )

    target_path = resolution.path
    before_content = decompress_before_content(before_b64)
    before_sha = sha256(before_content)
    history_file = _store.history_path(result_slug)
    lock_path = history_file.with_name(history_file.name + ".lock")
    event_id = revert_event_id(entry_id)
    next_generation = generation_of(entry) + 1

    def _do() -> ApplyResult:
        """§2 手順3-5 本体。history lock（real or seqlock）の内側から呼ばれる。"""
        current_text, current_sha, current_raw = _read_current(target_path)

        if current_sha == after_sha:
            branch = BRANCH_NORMAL
        elif current_sha == before_sha:
            branch = BRANCH_IDEMPOTENT
        else:
            branch = BRANCH_CONFLICT

        if branch == BRANCH_CONFLICT:
            diff = build_diff_summary(
                before_text=before_content, current_text=current_text,
                current_bytes=current_raw, before_sha=before_sha, current_sha=current_sha,
            )
            return ApplyResult(
                ok=False, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
                branch=branch, reason=BRANCH_CONFLICT,
                message=render_conflict_message(entry_id, diff),
                target_path=str(target_path),
            )

        if branch == BRANCH_IDEMPOTENT:
            # S7: 前回の中断（復元済み・イベント欠落）でも手動で before に戻した場合
            # でも、状態からは中断原因を識別できないため、どちらも正式な revert と
            # みなす。deterministic な revert_event_id が既に履歴にあれば完全冪等。
            raw_history = _store.load_raw_history_with_aliases(result_slug)
            already = any(
                _store.is_revert_event(rec) and rec.get("revert_event_id") == event_id
                for rec in raw_history
            )
            if already:
                return ApplyResult(
                    ok=True, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
                    branch=branch, message="既に revert 済みです（完全冪等・書込なし）。",
                    target_path=str(target_path), revert_event_id=event_id,
                )
            if dry_run:
                return ApplyResult(
                    ok=True, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
                    branch=branch,
                    message=(
                        "対象は既に before 内容です。revert イベントのみ追記します"
                        "（--apply で実行）。"
                    ),
                    target_path=str(target_path), revert_event_id=event_id,
                    revert_generation=next_generation,
                )
            _append_revert_event(entry, entry_id, event_id, next_generation, result_slug)
            return ApplyResult(
                ok=True, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
                branch=branch, message=render_apply_success(),
                target_path=str(target_path), revert_event_id=event_id,
                revert_generation=next_generation,
            )

        # branch == BRANCH_NORMAL
        source_fd = os.open(str(target_path), os.O_RDONLY)
        try:
            initial = snapshot_from_fd(source_fd)  # 手順2 の観測（C22 の比較基準）
            if dry_run:
                losses = preview_losses(initial)
                # #469: revert すると何行変わるかを既存の diff 要約ロジック
                # （conflict と同じ build_diff_summary）で計算し、dry-run 出力に含める。
                diff = build_diff_summary(
                    before_text=before_content, current_text=current_text,
                    current_bytes=current_raw, before_sha=before_sha,
                    current_sha=current_sha,
                )
                return ApplyResult(
                    ok=True, dry_run=dry_run, entry_id=entry_id, slug=result_slug,
                    branch=branch, message=render_dry_run_preview(losses, diff=diff),
                    target_path=str(target_path), losses=losses, diff=diff,
                    revert_event_id=event_id, revert_generation=next_generation,
                )
            return _restore_normal(
                entry_id=entry_id, entry=entry, slug=result_slug,
                target_path=target_path, before_content=before_content,
                before_sha=before_sha, after_sha=after_sha, source_fd=source_fd,
                initial=initial, event_id=event_id, next_generation=next_generation,
                allow_metadata_loss=allow_metadata_loss,
            )
        finally:
            os.close(source_fd)

    if dry_run:
        # C28: dry-run はゼロ書込。read_only_file_lock + seqlock check-after で読む。
        result, _acquired = seqlock_read(lock_path, _do)
        return result
    with file_lock(lock_path):
        return _do()
