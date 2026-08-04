"""evolve_decisions.py — evolve 提案 accept/reject の決定論キャプチャ（#360-A, ADR-041）。

fitness calibration（check_calibration_regression）の母集団 optimize_history が空だった
根本原因は、accept/reject の記録が SKILL.md の MUST（assistant が手で python を叩く）止まりで
決定論コードから呼ばれなかったこと（install ≠ enforcement の SKILL.md 版）。

本モジュールは evolve SKILL.md 1 実行内で完結する emit→（インライン適用）→drain の2相で、
accept をディスク差分から、reject を明示シグナルから取る（ADR-041, C: ハイブリッド）:

  - emit_decisions  : run_evolve 末尾。候補スキルの before_sha をキューにスナップショット。
  - ingest_decisions: Step 7.8 drain。after_sha != before_sha なら accept、明示却下なら reject、
                      未変更かつ未却下（skip）は記録しない。

書き込みは既存 record_evolve_diff_decision を再利用（fitness_func=skill_quality で採点 →
optimize_history へ冪等記録）。母集団は「混合でなく増量」を保つ。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import optimize_history_store as _store  # noqa: E402
from rl_common.file_lock import atomic_write_text, file_lock  # noqa: E402

# identity 関数は別 module（#287）。名前を re-export し既存参照をそのまま動かす。
from evolve_decision_ids import (  # noqa: E402,F401
    _decision_event_id,
    _entry_generation,
    _is_superseded,
    _legacy_run_id,
    _new_run_id,
    _proposal_id,
    _sha256,
    _supersede_keys,
    _tracked_path,
)

DATA_DIR = _store.DATA_DIR
QUEUE_ROOT = DATA_DIR / "evolve_decisions"

# 「未 drain 提案」マーカーの root（#402）。QUEUE_ROOT は DATA_DIR(=CLAUDE_PLUGIN_DATA 派生)配下で
# hook(env 有)/tool(env 無)で割れる（pitfall_datadir_hook_tool_split, #358）。SessionStart hook
# (env 有) と emit/drain(tool 文脈, env 無) が**同一パスに合意する必要がある**ため、ここは env を
# 見ず home 基準で固定する。マーカーは評価状態(optimize_history/queue)ではなく「apply→drain 待ちの
# 提案ポインタ」という運用状態で、fitness 母集団には入らず drain で消える。
MARKER_ROOT = Path.home() / ".claude" / "evolve-anything" / "evolve_pending"

# 未 drain 提案の保持上限（日）。他ストア（weak_signals / triage_ledger）と同じ 45 日。
# 判定は read 時の age 導出で行う（forward write に依存しない・#279）。
PENDING_TTL_DAYS = 45

# MVP 対象は discover の matched_skills（#223/Step 3 と同じスキル diff クラス）。
# skill_evolve / remediation への拡張は均質性を崩さないため follow-up（ADR-041）。
FITNESS_FUNC = "skill_quality"


# ─── slug / queue path ─────────────────────────────────────────────────────


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """optimize_history_store と同じ worktree 安全 slug（書き込み先を一致させる）。"""
    return _store.resolve_slug(cwd)


def queue_path_for(slug: str) -> Path:
    return QUEUE_ROOT / f"{_store._sanitize_slug(slug)}.jsonl"


def read_queue(slug: str) -> List[Dict[str, Any]]:
    """slug の pending decisions を読む。未存在なら []。壊れた行はスキップ。"""
    path = queue_path_for(slug)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_queue(slug: str, records: List[Dict[str, Any]]) -> None:
    """slug のキューを records で**上書き**する（emit は毎 run 現在バッチで置換）。"""
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    atomic_write_text(queue_path_for(slug), body)


@contextmanager
def _queue_lock(slug: str) -> Iterator[None]:
    """キューの RMW を直列化する（#287-1）。非ロックだと交差時に最後の上書きが勝ち、別 run の
    追加や drain 済み除去が消える。marker とは別ファイル・別ロックなので入れ子にできる。"""
    with file_lock(QUEUE_ROOT / f"{_store._sanitize_slug(slug)}.lock"):
        yield


# ─── pending marker（未 drain 提案ポインタ, #402）─────────────────────────────


def marker_path(slug: str) -> Path:
    return MARKER_ROOT / f"{_store._sanitize_slug(slug)}.json"


@contextmanager
def _marker_lock(slug: str) -> Iterator[None]:
    """marker の read-modify-write を process 間で直列化する。

    ⚠️ flock は open file description 単位なので入れ子に取ると自己 deadlock する。
    このロック下から marker を触るときは `_locked` サフィックスの内部関数を使う。
    """
    with file_lock(MARKER_ROOT / f"{_store._sanitize_slug(slug)}.lock"):
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
    return (now - emitted) > timedelta(days=PENDING_TTL_DAYS)


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
) -> None:
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
    """
    run_id = run_id or _legacy_run_id(pending)
    superseded_ids = {entry.get("id") for entry in pending if entry.get("id")}
    # パス単位 supersede は #279 のパス単独 ID で書かれた移行期 entry も自然に片付ける
    # （旧 ID は新 ID と一致しないが対象パスは同じ）。判定は accept 判定と同じ
    # `_tracked_path` を使う（advisory は対象が pytest.ini 等で skill_path を持たない。
    # ここだけ skill_path 直読みにすると advisory の residue が素通りする）。
    superseded_paths = {
        path for path in (_tracked_path(entry) for entry in pending) if path
    }
    with _marker_lock(slug):
        current = _read_pending_marker_file(slug) or {}
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
            return
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


# ─── helpers ───────────────────────────────────────────────────────────────


def _collect_advisory_proposals(project_dir: Path) -> List[Any]:
    """advisory detector の提案を集める（遅延 import・失敗は呼び出し側で握る）。"""
    from advisory_proposals import collect_advisory_proposals

    return collect_advisory_proposals(project_dir)


def _advisory_pending(project_dir: Optional[str], run_id: str) -> List[Dict[str, Any]]:
    """advisory 提案を pending entry へ変換する（#284）。

    accept 判定は skill 提案と同じ「対象ファイルの sha が変わったか」。現行 adapter
    （invalid_frontmatter / testpaths_coverage）はいずれも修正がファイル変更を伴うので
    この判定で足りる。ファイル変更を伴わない advisory を足すときは判定方式から設計する
    （#267 Sprint 1 の未決事項）。

    ``fitness_func`` は付けない — advisory の判断は optimize_history でなく
    advisory_decisions.jsonl に入るため（母集団の均質性を保つ）。
    """
    base = Path(project_dir) if project_dir else Path.cwd()
    out: List[Dict[str, Any]] = []
    for proposal in _collect_advisory_proposals(base):
        target = proposal.target_paths[0] if proposal.target_paths else None
        if not target:
            continue
        path = Path(target)
        if not path.is_absolute():
            path = base / path
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            continue  # 読めない対象は accept 判定できないので載せない
        out.append(
            {
                "id": proposal.id,
                "run_id": run_id,
                "detector_id": proposal.detector_id,
                "title": proposal.title,
                "action": proposal.action,
                "target_path": str(path),
                "before_sha": _sha256(before),
                "pattern": f"advisory:{proposal.detector_id}",
                "proposal_type": "advisory",
            }
        )
    return out


# 提案対象とみなす suitability（high/medium のみ issue 化される — evolve.py Phase 3.5）。
_SKILL_EVOLVE_PROPOSED = ("high", "medium")


def _extract_candidates(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """accept/reject 記録対象のスキル内容提案を result から抽出する。

    対象（いずれも適用されれば SKILL.md content が変わる＝fitness_func=skill_quality で
    均質に採点でき、母集団が「混合でなく増量」になる）:
      - discover の matched_skills（skill diff, #223 と同クラス）
      - skill_evolve の high/medium 適性 assessment（自己進化パターン組み込み提案）

    remediation の fix は target が rules/hooks/構造と異種で skill_quality 母集団の均質性を
    壊すため対象外（ADR-041 follow-up の意図的スコープ）。

    同一 skill_path は1件に畳む（discover 優先）。
    """
    phases = result.get("phases") or {}
    seen: set = set()
    out: List[Dict[str, str]] = []

    # 1) discover matched_skills（skill diff）
    for m in (phases.get("discover") or {}).get("matched_skills") or []:
        sp = m.get("skill_path")
        name = m.get("matched_skill")
        if not sp or not name or sp in seen:
            continue
        seen.add(sp)
        out.append({
            "skill_name": name, "skill_path": sp,
            "pattern": m.get("pattern", ""), "proposal_type": "skill_diff",
        })

    # 2) skill_evolve 適性 high/medium（自己進化パターン組み込み提案）
    for a in (phases.get("skill_evolve") or {}).get("assessments") or []:
        if a.get("suitability") not in _SKILL_EVOLVE_PROPOSED:
            continue
        skill_dir = a.get("skill_dir")
        name = a.get("skill_name")
        if not skill_dir or not name:
            continue
        sp = str(Path(skill_dir) / "SKILL.md")
        if sp in seen:
            continue
        seen.add(sp)
        out.append({
            "skill_name": name, "skill_path": sp,
            "pattern": f"skill_evolve:{a.get('suitability')}", "proposal_type": "skill_evolve",
        })

    return out


def _record_advisory_event(
    slug: str, entry: Dict[str, Any], tracked: Optional[str], decision: str, *, reason: Optional[str] = None,
) -> None:
    """advisory pending 1件の terminal/fact を記録する（#267）。呼び側で not dry_run を確認済み前提。"""
    from advisory_decision_log import record_advisory_decision

    record_advisory_decision(
        slug=slug,
        proposal_id=entry["id"],
        detector_id=str(entry.get("detector_id") or "unknown"),
        target_path=str(tracked or ""),
        decision=decision,
        run_id=entry.get("run_id"),
        reason=reason,
    )


def _load_recorder():
    """fitness_evolution.record_evolve_diff_decision を遅延 import（lib 外モジュール）。"""
    fe_dir = _LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"
    if str(fe_dir) not in sys.path:
        sys.path.insert(0, str(fe_dir))
    from fitness_evolution import record_evolve_diff_decision  # noqa: E402

    return record_evolve_diff_decision


# ─── Phase A: emit ─────────────────────────────────────────────────────────


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
        slug = resolve_slug(Path(project_dir) if project_dir else None)
    run_id = run_id or _new_run_id()

    pending: List[Dict[str, Any]] = []
    for c in _extract_candidates(result):
        try:
            before = Path(c["skill_path"]).read_text(encoding="utf-8")
        except OSError:
            continue  # 読めないスキルは対象外
        before_sha = _sha256(before)
        pending.append(
            {
                "id": _proposal_id(c["skill_path"], before_sha),
                "run_id": run_id,
                "skill_name": c["skill_name"],
                "skill_path": c["skill_path"],
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


# ─── Phase C: ingest (drain) ───────────────────────────────────────────────


def ingest_decisions(
    slug: str,
    *,
    rejected: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
    history_file: Optional[Path] = None,
    pending: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Step 7.8 drain。各 pending を分類して optimize_history に記録する。

      after_sha != before_sha（適用された）→ accept（human_accepted=True）
      id in rejected（明示却下）          → reject（human_accepted=False, reason）
      未変更かつ未却下（skip）            → optimize_history には記録しない（deferred）

    accept/reject は record_evolve_diff_decision 経由で optimize_history へ冪等記録。

    advisory 提案は optimize_history でなく ``advisory_decision_log`` へ記録する（#284）。
    判断結果に関わらず drain 到達時に必ず ``surfaced`` を記録し、分類結果（accept/reject/
    deferred）も続けて記録する（#267: 採用率の分母を分子と同じレーンに残す）。

    pending のソース（#400 バグ#1 根治）:
      - `pending=None`（既定）: キュー `DATA_DIR/evolve_decisions/<slug>.jsonl` から読む。
        消化済みをキューから消す（非 dry_run 時）。
      - `pending=[...]` を明示渡し: `result.evolve_decisions.pending` を直接消費する。
        **dry-run 運用フロー専用の経路** — `evolve --dry-run` では emit がキューを
        書かないため、result 同梱の pending（before_sha 付き）を渡すことで apply 後の
        ディスク差分から accept を記録できる。この場合キューは SoT でないため触らない。
    """
    rejected = rejected or {}
    from_queue = pending is None
    if from_queue:
        pending = read_queue(slug)
    if history_file is None:
        history_file = _store.history_path(slug)
    else:
        history_file = Path(history_file)

    accepted: List[str] = []
    rejected_out: List[str] = []
    skipped: List[str] = []
    recorder = None

    for entry in pending:
        pid = entry["id"]
        tracked = _tracked_path(entry)
        is_advisory = entry.get("proposal_type") == "advisory"
        try:
            after = Path(tracked).read_text(encoding="utf-8") if tracked else None
        except OSError:
            after = None
        after_sha = _sha256(after) if after is not None else None
        applied = after_sha is not None and after_sha != entry.get("before_sha")

        # #267: 判断結果と独立に surfaced（分母）を記録する。
        if not dry_run and is_advisory:
            _record_advisory_event(slug, entry, tracked, "surfaced")

        if applied:
            kind, after_content, reason = "accept", after, None
        elif pid in rejected:
            kind, after_content, reason = "reject", (after if after is not None else ""), rejected[pid]
        else:
            skipped.append(pid)
            if not dry_run and is_advisory:
                _record_advisory_event(slug, entry, tracked, "deferred")
            continue

        if not dry_run and is_advisory:
            # advisory は異種対象なので skill_quality 母集団に入れず専用ストアへ記録（#284）。
            _record_advisory_event(slug, entry, tracked, kind, reason=reason)
        elif not dry_run:
            if recorder is None:
                recorder = _load_recorder()
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
            )
        (accepted if kind == "accept" else rejected_out).append(pid)

    if not dry_run and from_queue:
        # キューが SoT のときだけ消化済みを除去する。pending を直接渡された場合
        # （dry-run 運用経路）はキューを生成も変更もしない。
        # 未判断は deferred。後続 run で apply/reject できるようキューに残す。
        # 判断（ファイル読み・採点）は重いのでロック外で行い、**書く直前にロック下で
        # 読み直して**差分だけ適用する（その間に別 run が追加した entry を消さない・#287-1）。
        consumed = set(accepted) | set(rejected_out)
        with _queue_lock(slug):
            _write_queue(slug, [e for e in read_queue(slug) if e.get("id") not in consumed])

    return {"accepted": accepted, "rejected": rejected_out, "skipped": skipped}


# ─── drain（`evolve --drain` の実体, #402）────────────────────────────────


def drain_pending(
    *,
    slug: Optional[str] = None,
    project_dir: Optional[str] = None,
    result_json: Optional[str] = None,
    rejected: Optional[Dict[str, str]] = None,
    history_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """`evolve --drain` の実体（#402）。pending を marker か result-json から取り、
    apply 後のディスク差分から accept を ingest し、marker をクリアする。

    enforcement gap（ingest が SKILL.md prose 依存）を、SKILL.md が inline python でなく
    **単一コマンド `evolve --drain` を呼ぶだけ**にして縮める。drain は CLI＝**tool 文脈**で
    走るため optimize_history を reader と同一 DATA_DIR に書く＝#358（DATA_DIR split）を踏まない。

    冪等: ingest が `_decision_event_id`（提案 ID + 判断種別 + 判断時点の内容）で dedup するので、
    未 apply で空振り→後で apply→再 drain でも accept は一度だけ記録される（apply タイミング非依存）。

    Args:
        slug: 未指定なら project_dir/cwd から worktree 安全に解決。
        result_json: 指定時はこの result JSON の `evolve_decisions.pending` を使う（marker より優先）。
        rejected: {pending_id: reason} の明示却下。
        history_file: テスト用の store 上書き。
    """
    if slug is None:
        slug = resolve_slug(Path(project_dir) if project_dir else None)

    # #287-3: スナップショットと purge をそれぞれロック下で行い、**ingest はロック外**に置く
    # （ingest は skill_quality 採点で秒オーダーになりうるので、握ると同一 slug の emit と
    # SessionStart hook を飢餓させる）。TOCTOU は世代キー（`_entry_generation`）で防ぐ。
    # ロック下では公開版でなく `_locked` / `_read_pending_marker_file` を使う（自己 deadlock）。
    with _marker_lock(slug):
        if result_json:
            data = json.loads(Path(result_json).read_text(encoding="utf-8"))
            envelope = data.get("evolve_decisions") or {}
            pending = envelope.get("pending") or []
        else:
            marker = _read_pending_marker_file(slug)
            pending = (marker.get("pending") if marker else None) or []

    summary = ingest_decisions(
        slug, pending=pending, dry_run=False, rejected=rejected, history_file=history_file
    )
    consumed = set(summary["accepted"]) | set(summary["rejected"])
    remaining = [entry for entry in pending if entry.get("id") not in consumed]
    # 未判断は deferred として marker に残し、後続 run で apply/reject できるようにする。
    summary["deferred"] = [entry.get("id") for entry in remaining]
    generations = {
        _entry_generation(entry) for entry in pending if entry.get("id") in consumed
    }
    with _marker_lock(slug):
        _purge_marker_entries_locked(slug, consumed, generations=generations)
    return summary
