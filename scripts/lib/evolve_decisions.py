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

import hashlib
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

import fcntl

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import optimize_history_store as _store  # noqa: E402

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
    path = queue_path_for(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ─── pending marker（未 drain 提案ポインタ, #402）─────────────────────────────


def marker_path(slug: str) -> Path:
    return MARKER_ROOT / f"{_store._sanitize_slug(slug)}.json"


@contextmanager
def _marker_lock(slug: str) -> Iterator[None]:
    """marker の read-modify-write を process 間で直列化する。"""
    lock_path = MARKER_ROOT / f"{_store._sanitize_slug(slug)}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _run_is_expired(run: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """TTL 超過判定。``emitted_at`` を持たない旧 schema は age 不明ゆえ落とさない。"""
    raw = run.get("emitted_at")
    if not raw:
        return False
    try:
        emitted = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if emitted.tzinfo is None:
        emitted = emitted.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - emitted) > timedelta(days=PENDING_TTL_DAYS)


def _read_pending_marker_file(slug: str) -> Optional[Dict[str, Any]]:
    """marker を読む。TTL 超過 run は read 時に落とす（書き戻さない）。

    TTL は forward write でなく read 時の age 導出で効かせる（writer が止まっても
    滞留しない＝weak_signals の ``is_effectively_expired`` と同方針）。
    """
    path = marker_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if "runs" not in data:
        pending = data.get("pending") or []
        data["runs"] = [
            {
                "run_id": _legacy_run_id(pending),
                "pending": pending,
                "result_path": data.get("result_path"),
            }
        ]
    runs = [
        run
        for run in data.get("runs", [])
        if (run.get("pending") or []) and not _run_is_expired(run)
    ]
    if not runs:
        return None
    data["runs"] = runs
    # pending は常に runs から再構成する（supersede / TTL を旧 reader にも反映）。
    data["pending"] = [entry for run in runs for entry in (run.get("pending") or [])]
    return data


def _write_marker_file(slug: str, data: Dict[str, Any]) -> None:
    """reader が部分 JSON を見ないよう sibling tmp から atomic replace する。"""
    path = marker_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


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
    # （旧 ID は新 ID と一致しないが skill_path は同じ）。
    superseded_paths = {
        entry["skill_path"] for entry in pending if entry.get("skill_path")
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
                and entry.get("skill_path") not in superseded_paths
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
                "result_path": result_path,
            },
        )


def read_pending_marker(slug: str) -> Optional[Dict[str, Any]]:
    return _read_pending_marker_file(slug)


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
    if not consumed:
        return False
    with _marker_lock(slug):
        marker = _read_pending_marker_file(slug)
        if not marker:
            return False
        runs: List[Dict[str, Any]] = []
        for run in marker.get("runs", []):
            kept = [
                entry for entry in (run.get("pending") or []) if entry.get("id") not in consumed
            ]
            if kept:
                runs.append({**run, "pending": kept})
        if not runs:
            path = marker_path(slug)
            if path.exists():
                path.unlink()
            return True
        flattened = [entry for run in runs for entry in (run.get("pending") or [])]
        marker.update({"runs": runs, "pending": flattened, "schema_version": 2})
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
        sp = p.get("skill_path")
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_run_id() -> str:
    return "evrun_" + uuid.uuid4().hex


def _legacy_run_id(pending: List[Dict[str, Any]]) -> str:
    """旧 marker を安定した synthetic run として扱う。"""
    identity = "\n".join(sorted(str(entry.get("id", "")) for entry in pending))
    return "legacy_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]


def _proposal_id(skill_path: str, before_sha: str) -> str:
    """**提案**の content identity = (対象パス, 適用前の内容)。

    「同じ提案か」だけを表す。「同じ判断イベントか」は別キー（``_decision_event_id``）で
    表す — 1つの ID に両方を兼ねさせると必ずどちらかが壊れる（#279→#286→#290 で
    3回踏んだ）:

    - **run_id を混ぜてはいけない**（#279）: ID が run ごとに変わると判断イベントも
      run 跨ぎで別物になり、1回の apply が optimize_history に N 重記録される。
    - **パス単独にしてもいけない**（#286）: 判断イベントキーが恒久キーになり、同じ
      スキルの2回目以降の accept が冪等 dedup で捨てられる（生涯1件しか母集団に入らない）。
    - **before_sha を混ぜても、これ単独では足りない**（#290）: 対象の内容が過去の状態へ
      循環すると過去の ID が再利用されるため、判断イベントキーが再び衝突する。
    """
    return "evdiff_" + hashlib.sha1(
        f"{skill_path}\n{before_sha}".encode("utf-8")
    ).hexdigest()[:12]


def _decision_event_id(proposal_id: str, kind: str, after_content: str) -> str:
    """**判断イベント**の identity = (提案, 判断種別, 判断時点の内容)（#290）。

    ``record_evolve_diff_decision`` の冪等 dedup キー。提案 ID と分離することで、

    - 同じ apply を二重 drain しても after が同じ＝同キー（冪等は保つ）
    - 内容が循環して提案 ID が再利用されても after が違う＝別キー（欠落しない）

    の両方が成り立つ。提案 ID 側の identity 設計を変えても、この分離がある限り
    判断イベントの冪等性は巻き添えにならない。
    """
    return f"{proposal_id}_{kind}_{_sha256(after_content)[:12]}"


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

    persisted = False
    marker_written = False
    marker_cleared = False
    if not dry_run:
        # run_id 無しは旧 schema の「前 run を上書き」キュー。新 envelope へ移る際に
        # stale として除去し、run_id 付きの未判断だけを保持する。
        existing = [entry for entry in read_queue(slug) if entry.get("run_id")]
        current_ids = {entry.get("id") for entry in pending}
        _write_queue(
            slug,
            [entry for entry in existing if entry.get("id") not in current_ids] + pending,
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
    except OSError:
        pass

    return {
        "pending": pending,
        "count": len(pending),
        "persisted": persisted,
        "slug": slug,
        "run_id": run_id,
        "marker_written": marker_written,
        "marker_cleared": marker_cleared,
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
      未変更かつ未却下（skip）            → 記録しない

    accept/reject は record_evolve_diff_decision 経由で optimize_history へ冪等記録。

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
        try:
            after = Path(entry["skill_path"]).read_text(encoding="utf-8")
        except OSError:
            after = None
        after_sha = _sha256(after) if after is not None else None
        applied = after_sha is not None and after_sha != entry.get("before_sha")

        if applied:
            kind, after_content, reason = "accept", after, None
        elif pid in rejected:
            kind, after_content, reason = "reject", (after if after is not None else ""), rejected[pid]
        else:
            skipped.append(pid)
            continue

        if not dry_run:
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
            )
        (accepted if kind == "accept" else rejected_out).append(pid)

    if not dry_run and from_queue:
        # キューが SoT のときだけ消化済みを除去する。pending を直接渡された場合
        # （dry-run 運用経路）はキューを生成も変更もしない。
        # 未判断は deferred。後続 run で apply/reject できるようキューに残す。
        consumed = set(accepted) | set(rejected_out)
        remaining = [e for e in pending if e["id"] not in consumed]
        _write_queue(slug, remaining)

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

    if result_json:
        data = json.loads(Path(result_json).read_text(encoding="utf-8"))
        envelope = data.get("evolve_decisions") or {}
        pending = envelope.get("pending") or []
    else:
        marker = read_pending_marker(slug)
        pending = (marker.get("pending") if marker else None) or []

    summary = ingest_decisions(
        slug, pending=pending, dry_run=False, rejected=rejected, history_file=history_file
    )
    consumed = set(summary["accepted"]) | set(summary["rejected"])
    remaining = [entry for entry in pending if entry.get("id") not in consumed]
    # 未判断は deferred として marker に残し、後続 run で apply/reject できるようにする。
    summary["deferred"] = [entry.get("id") for entry in remaining]
    purge_marker_entries(slug, consumed)
    return summary
