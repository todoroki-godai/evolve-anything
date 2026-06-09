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
from pathlib import Path
from typing import Any, Dict, List, Optional

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
MARKER_ROOT = Path.home() / ".claude" / "rl-anything" / "evolve_pending"

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


def write_pending_marker(
    slug: str, pending: List[Dict[str, Any]], *, result_path: Optional[str] = None
) -> None:
    """slug の「未 drain 提案」マーカーを上書きする（emit が dry-run でも書く）。

    マーカーは store/queue とは別の運用状態。SessionStart の drain リマインドと
    `rl-evolve --drain` の pending ソースとして使う。
    """
    path = marker_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"slug": slug, "pending": pending, "result_path": result_path}, ensure_ascii=False),
        encoding="utf-8",
    )


def read_pending_marker(slug: str) -> Optional[Dict[str, Any]]:
    path = marker_path(slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def clear_pending_marker(slug: str) -> bool:
    path = marker_path(slug)
    if path.exists():
        path.unlink()
        return True
    return False


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


def _proposal_id(skill_path: str) -> str:
    return "evdiff_" + hashlib.sha1(skill_path.encode("utf-8")).hexdigest()[:12]


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
) -> Dict[str, Any]:
    """run_evolve 末尾。スキル diff 候補の before_sha をキューにスナップショットする。

    dry_run 時は pending を計算するが**書き込まない**（pitfall_dryrun_stateful_store_write）。
    返り値の pending は report 用（dry_run でも見せる）。
    """
    if slug is None:
        slug = resolve_slug(Path(project_dir) if project_dir else None)

    pending: List[Dict[str, Any]] = []
    for c in _extract_candidates(result):
        try:
            before = Path(c["skill_path"]).read_text(encoding="utf-8")
        except OSError:
            continue  # 読めないスキルは対象外
        pending.append(
            {
                "id": _proposal_id(c["skill_path"]),
                "skill_name": c["skill_name"],
                "skill_path": c["skill_path"],
                "before_sha": _sha256(before),
                "fitness_func": FITNESS_FUNC,
                "pattern": c["pattern"],
                "proposal_type": c.get("proposal_type", "skill_diff"),
            }
        )

    persisted = False
    if not dry_run:
        _write_queue(slug, pending)  # 現在 run の pending で上書き
        persisted = True

    # #402: drain 検出用の運用マーカー（dry-run でも書く。store/queue とは別状態）。
    # 候補ゼロなら古いマーカーを消す（drain 待ちが無いので沈黙させる）。
    try:
        if pending:
            write_pending_marker(slug, pending)
        else:
            clear_pending_marker(slug)
    except OSError:
        pass

    return {"pending": pending, "count": len(pending), "persisted": persisted, "slug": slug}


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
        **dry-run 運用フロー専用の経路** — `rl-evolve --dry-run` では emit がキューを
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
                entry_id=f"{pid}_{kind}",
            )
        (accepted if kind == "accept" else rejected_out).append(pid)

    if not dry_run and from_queue:
        # キューが SoT のときだけ消化済みを除去する。pending を直接渡された場合
        # （dry-run 運用経路）はキューを生成も変更もしない。
        consumed = set(accepted) | set(rejected_out) | set(skipped)
        remaining = [e for e in pending if e["id"] not in consumed]
        _write_queue(slug, remaining)

    return {"accepted": accepted, "rejected": rejected_out, "skipped": skipped}


# ─── drain（`rl-evolve --drain` の実体, #402）────────────────────────────────


def drain_pending(
    *,
    slug: Optional[str] = None,
    project_dir: Optional[str] = None,
    result_json: Optional[str] = None,
    rejected: Optional[Dict[str, str]] = None,
    history_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """`rl-evolve --drain` の実体（#402）。pending を marker か result-json から取り、
    apply 後のディスク差分から accept を ingest し、marker をクリアする。

    enforcement gap（ingest が SKILL.md prose 依存）を、SKILL.md が inline python でなく
    **単一コマンド `rl-evolve --drain` を呼ぶだけ**にして縮める。drain は CLI＝**tool 文脈**で
    走るため optimize_history を reader と同一 DATA_DIR に書く＝#358（DATA_DIR split）を踏まない。

    冪等: ingest が `{pid}_{kind}` entry_id で dedup するので、未 apply で空振り→後で apply→再 drain
    でも accept は一度だけ記録される（apply タイミング非依存）。

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
        pending = (data.get("evolve_decisions") or {}).get("pending") or []
    else:
        marker = read_pending_marker(slug)
        pending = (marker.get("pending") if marker else None) or []

    summary = ingest_decisions(
        slug, pending=pending, dry_run=False, rejected=rejected, history_file=history_file
    )
    clear_pending_marker(slug)
    return summary
