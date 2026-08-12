"""pending marker（未 drain 提案ポインタ, #402）helper（`evolve_decisions` パッケージ分割・#383）。

`marker_path` / `write_pending_marker` / `read_pending_marker` / purge 系 / `undrained_applied`
を束ねる。振る舞いはゼロ変更で、`evolve_decisions/__init__.py` が全名前を re-export し後方互換
と `setattr(evolve_decisions, ...)` 束縛を保つ。

⚠️ 束縛フェンス（`evolve_decisions/__init__.py` docstring 参照）: `MARKER_ROOT` /
`PENDING_TTL_DAYS` は `__init__.py`（パッケージ namespace）が正典。test の
`monkeypatch.setattr(evolve_decisions, "MARKER_ROOT", ...)`（conftest.py の autouse fixture
`_isolate_evolve_marker` を含む）を確実に効かせるため、本 module の関数は呼び出し時に
`import evolve_decisions as _ed; _ed.MARKER_ROOT` で遅延参照する。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import optimize_history_store as _store
from rl_common.file_lock import atomic_write_text, file_lock

from evolve_decision_ids import (
    _entry_generation,
    _filter_monotonic_pending,
    _legacy_run_id,
    _sha256,
    _tracked_path,
)


def marker_path(slug: str) -> Path:
    import evolve_decisions as _ed

    return _ed.MARKER_ROOT / f"{_store._sanitize_slug(slug)}.json"


@contextmanager
def _marker_lock(slug: str) -> Iterator[None]:
    """marker の read-modify-write を process 間で直列化する。

    ⚠️ flock は open file description 単位なので入れ子に取ると自己 deadlock する。
    このロック下から marker を触るときは `_locked` サフィックスの内部関数を使う。
    """
    import evolve_decisions as _ed

    with file_lock(_ed.MARKER_ROOT / f"{_store._sanitize_slug(slug)}.lock"):
        yield


def _run_is_expired(
    run: Dict[str, Any],
    now: Optional[datetime] = None,
    fallback_emitted: Optional[datetime] = None,
) -> bool:
    """TTL 超過判定。

    `emitted_at` 欠落・不正の run は age 不明だが、そのままだと永久に失効せず marker が
    残骸化する（#287-4）。`fallback_emitted`（marker の mtime）を保守的な上限に使う —
    実 emit は必ず mtime 以前なので、mtime 基準で超過なら本当の age も超過している。
    """
    import evolve_decisions as _ed

    raw = run.get("emitted_at")
    emitted: Optional[datetime] = None
    if raw:
        try:
            emitted = datetime.fromisoformat(str(raw))
        except ValueError:
            emitted = None
    if emitted is None:
        emitted = fallback_emitted
    if emitted is None:
        return False
    if emitted.tzinfo is None:
        emitted = emitted.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - emitted) > timedelta(days=_ed.PENDING_TTL_DAYS)


def _read_pending_marker_file(slug: str) -> Optional[Dict[str, Any]]:
    """marker を読む。TTL 超過 run は read 時に落とす（書き戻さない）。

    TTL は forward write でなく read 時の age 導出で効かせる（writer が止まっても
    滞留しない＝weak_signals の ``is_effectively_expired`` と同方針）。

    構文は妥当でも構造が壊れた JSON（`[]` / `{"runs": [null]}` 等）は `None` に畳む。型を
    信じて `.get()` すると `AttributeError` が hook まで伝播し GC にも到達しない（#287-4）。
    """
    path = marker_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if "runs" not in data:
        pending = data.get("pending")
        pending = pending if isinstance(pending, list) else []
        data["runs"] = [
            {
                "run_id": _legacy_run_id([e for e in pending if isinstance(e, dict)]),
                "pending": pending,
                "result_path": data.get("result_path"),
            }
        ]
    runs = []
    for run in data.get("runs") or []:
        if not isinstance(run, dict):
            continue
        entries = [e for e in (run.get("pending") or []) if isinstance(e, dict)]
        if not entries or _run_is_expired(run, fallback_emitted=mtime):
            continue
        runs.append({**run, "pending": entries})
    if not runs:
        return None
    data["runs"] = runs
    # pending は常に runs から再構成する（supersede / TTL を旧 reader にも反映）。
    data["pending"] = [entry for run in runs for entry in (run.get("pending") or [])]
    data["result_path"] = _flat_result_path(runs)
    return data


def _flat_result_path(runs: List[Dict[str, Any]]) -> Optional[str]:
    """後方互換の flat `result_path`（#283）。

    flat `pending` は全 run の合成なので、最後の writer 勝ちにすると「run A の提案」に
    「run B の result JSON」が付く。**run が1つのときだけ**値を出す（正典は
    `runs[].result_path`）。判定はパスの種類数でなく run 本数 — `--output` の既定は
    slug 由来の固定パスなので同一 PJ の2 run は同じパス文字列を持つのが普通で、
    後の run が上書きしている以上 flat pending とは対応しない。
    """
    return runs[0].get("result_path") if len(runs) == 1 else None


def _write_marker_file(slug: str, data: Dict[str, Any]) -> None:
    """reader が部分 JSON を見ないよう sibling tmp から atomic replace する。"""
    atomic_write_text(marker_path(slug), json.dumps(data, ensure_ascii=False))


def write_pending_marker(
    slug: str,
    pending: List[Dict[str, Any]],
    *,
    run_id: Optional[str] = None,
    result_path: Optional[str] = None,
) -> int:
    """slug の「未 drain 提案」マーカーへ run 単位で追記する（emit が dry-run でも書く）。

    マーカーは store/queue とは別の運用状態。SessionStart の drain リマインドと
    `evolve --drain` の pending ソースとして使う。同じ run_id は置換し、別 run は
    保持するため concurrent session / worktree の pending を上書きしない（#267）。

    supersede は **ID でなく対象パス単位**で行う（#290）。emit は毎回新しい run_id を
    採るため、supersede が無いと「並行 run」と「自分の前回 run」を区別できず runs[] が
    単調増加する（#279）。かといって ID 一致だけで消すと、`before_sha` を含む ID は
    対象の内容が変わるたびに変わるので **同じファイルの pending が複数世代 residue** し、
    ingest が「今のファイル ≠ その entry の before_sha」で全部 accept 判定する
    ＝1回の apply が N 件記録される（#279 が潰した N 重記録の別経路再導入）。
    1ファイルの未 drain 提案は最新1件だけが有効なので、パスで置換するのが正しい。
    別 worktree は skill_path が絶対パスで異なるので並行 run の提案は潰さない。

    **monotonic supersede ガード**（#402 決定8 round4）: 同一対象パスについて、
    ``pending`` の ``revert_generation`` が既存 entry より小さければ公開せず捨てる
    （emit の queue 更新と marker 更新が独立 lock 区間なので、公開順序が入れ替わると
    古い世代の pending が新しい世代を消しうる。ここでその逆転を遮断する）。

    Returns:
        monotonic ガードで捨てた件数（#402 決定8 round4。emit の返り値 meta に使う）。
    """
    run_id = run_id or _legacy_run_id(pending)
    with _marker_lock(slug):
        current = _read_pending_marker_file(slug) or {}
        existing_entries = [
            entry
            for run in current.get("runs", [])
            for entry in (run.get("pending") or [])
        ]
        pending, discarded = _filter_monotonic_pending(existing_entries, pending)

        superseded_ids = {entry.get("id") for entry in pending if entry.get("id")}
        # パス単位 supersede は #279 のパス単独 ID で書かれた移行期 entry も自然に片付ける
        # （旧 ID は新 ID と一致しないが対象パスは同じ）。判定は accept 判定と同じ
        # `_tracked_path` を使う（advisory は対象が pytest.ini 等で skill_path を持たない。
        # ここだけ skill_path 直読みにすると advisory の residue が素通りする）。
        superseded_paths = {
            path for path in (_tracked_path(entry) for entry in pending) if path
        }
        runs: List[Dict[str, Any]] = []
        for run in current.get("runs", []):
            if run.get("run_id") == run_id:
                continue
            kept = [
                entry
                for entry in (run.get("pending") or [])
                if entry.get("id") not in superseded_ids
                and _tracked_path(entry) not in superseded_paths
            ]
            if kept:
                runs.append({**run, "pending": kept})
        if pending:
            runs.append(
                {
                    "run_id": run_id,
                    "pending": pending,
                    "result_path": result_path,
                    "emitted_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if not runs:
            path = marker_path(slug)
            if path.exists():
                path.unlink()
            return discarded
        runs.sort(key=lambda run: str(run.get("run_id", "")))
        flattened = [entry for run in runs for entry in (run.get("pending") or [])]
        _write_marker_file(
            slug,
            {
                "schema_version": 2,
                "slug": slug,
                "runs": runs,
                # 旧 reader と SessionStart hook の後方互換。
                "pending": flattened,
                "result_path": _flat_result_path(runs),
            },
        )
    return discarded


def read_pending_marker(slug: str) -> Optional[Dict[str, Any]]:
    """marker を読む。読めない/全 run が TTL 超過なら物理削除もする（#287-4）。"""
    marker = _read_pending_marker_file(slug)
    if marker is None and marker_path(slug).exists():
        _gc_marker_file(slug)
    return marker


def _gc_marker_file(slug: str) -> bool:
    """失効・破損した marker を物理削除する（#287-4）。read が None を返しても誤通知は
    起きないがファイルは永久に残る。判定と unlink の間に別 run が書いた marker を消さない
    よう、ロック下で読み直して None のままのときだけ消す。"""
    with _marker_lock(slug):
        if _read_pending_marker_file(slug) is not None:
            return False
        path = marker_path(slug)
        if not path.exists():
            return False
        path.unlink()
        return True


def clear_pending_marker(slug: str) -> bool:
    with _marker_lock(slug):
        path = marker_path(slug)
        if path.exists():
            path.unlink()
            return True
        return False


def purge_marker_entries(slug: str, consumed: Set[str]) -> bool:
    """drain 済み entry を全 run 横断で marker から除去する（空になった run は落とす）。

    consumed は proposal ID の集合。ID は skill_path 由来の content identity なので、
    どの run の envelope から drain しても「同じ提案」は一度で消える。
    """
    with _marker_lock(slug):
        return _purge_marker_entries_locked(slug, consumed)


def _purge_marker_entries_locked(
    slug: str,
    consumed: Set[str],
    generations: Optional[Set[tuple]] = None,
) -> bool:
    """`purge_marker_entries` のロック無し版（marker ロック下から呼ぶ・#287-3）。

    `generations` を渡すと世代一致も条件に加える（drain 用）。flock は入れ子取得で自己
    deadlock するため、ロック取得と本体を分けてある。"""
    if not consumed:
        return False
    marker = _read_pending_marker_file(slug)
    if not marker:
        return False

    def _drop(entry: Dict[str, Any]) -> bool:
        if entry.get("id") not in consumed:
            return False
        return generations is None or _entry_generation(entry) in generations

    runs: List[Dict[str, Any]] = []
    for run in marker.get("runs", []):
        kept = [entry for entry in (run.get("pending") or []) if not _drop(entry)]
        if kept:
            runs.append({**run, "pending": kept})
    if not runs:
        path = marker_path(slug)
        if path.exists():
            path.unlink()
        return True
    flattened = [entry for run in runs for entry in (run.get("pending") or [])]
    marker.update(
        {
            "runs": runs,
            "pending": flattened,
            "schema_version": 2,
            "result_path": _flat_result_path(runs),
        }
    )
    _write_marker_file(slug, marker)
    return True


def undrained_applied(slug: str) -> List[Dict[str, Any]]:
    """marker の pending のうち、現在のディスク sha が before_sha と異なる（=apply 済）entry を返す。

    SessionStart リマインドの signal。**optimize_history を読まない**ので hook 文脈でも
    DATA_DIR split（#358）を踏まない。マーカー無し / 未 apply なら []（沈黙＝silence!=evaluated を
    満たしつつ、適用済みのものだけ surface する）。
    """
    marker = read_pending_marker(slug)
    if not marker:
        return []
    out: List[Dict[str, Any]] = []
    for p in marker.get("pending", []) or []:
        sp = _tracked_path(p)
        before = p.get("before_sha")
        if not sp or not before:
            continue
        try:
            current = _sha256(Path(sp).read_text(encoding="utf-8"))
        except OSError:
            continue
        if current != before:
            out.append(p)
    return out
