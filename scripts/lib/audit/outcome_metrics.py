"""アウトカム指標 v1 — 行動アウトカム3軸の決定論算出（#423, advisory）。

env_score の大半は coherence / constitutional（入力 proxy = 構造の綺麗さ）であり、
「環境が良くなればユーザーの手戻りが減る」という目的変数を直接測る軸が無かった。
本モジュールは既存ストアのみから LLM 非依存・決定論で 3 軸を算出する:

1. correction 再発率: 同型 correction（correction_type）が窓内で複数セッションに跨って
   再発した率。corrections.jsonl から算出。
2. 一発成功率: エラーが 1 件も発生しなかったセッションの割合（error→retry 連鎖なし）。
   sessions.jsonl の error_count から算出。
3. rework 率: 検証ツールを介さず連続する Edit/Write が閾値以上現れたセッションの割合。
   sessions.jsonl の tool_sequence から算出。
   注意: ストアに編集対象ファイル ID が無いため「同一ファイル N ターン内再編集」は厳密には
   算出不能。tool_sequence 上の編集バーストを近似 proxy として用いる（ADR-046 に明記）。

各関数は `(value: float|None, evidence: dict)` を返す。データ不足時は value=None +
evidence["reason"]="no_data" で「沈黙でなくデータ不足を明示」する（#393-#396 準拠）。
重みには入れない（advisory のみ）。2〜4 週並走 → 分布実測 → 重み昇格判断（ADR-046）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# テストは ``monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path)`` で
# 直接この module 属性を差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "rl-anything"

# #469: session 系軸は sessions.jsonl 直読でなく session_store の union read
# （DuckDB sessions.db + 未 ingest live jsonl）を使う。sessions.jsonl は #415 で db へ
# ingest 後 rotate されるため live jsonl はほぼ空であり、jsonl 直読だと session 系の分母が
# 構造的に常に空になっていた。読み取り経路は session_store に 1 つだけ実装し、
# outcome_metrics と outcome_promotion_readiness の両方がそれを共有する（二重実装回避）。
try:
    import session_store as _session_store
except ImportError:  # pragma: no cover - パス未解決時は jsonl 直読へ fallback
    _session_store = None


def read_sessions(base: Path, *, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """session レコードを union read で取得する（db + 未 ingest jsonl, dedup 済み）。

    base 配下の sessions.db / sessions.jsonl を session_store 経由で読む。
    duckdb 無 / db 不在時は jsonl のみへ graceful fallback（session_store 側で処理）。
    session_store を import できない環境では sessions.jsonl 直読へ最終 fallback する。
    """
    if _session_store is not None:
        return _session_store.read_session_records(base, since=since)
    # session_store 不在時の最終フォールバック（旧挙動: live jsonl のみ）。
    recs = _read_jsonl(base / "sessions.jsonl")
    if since:
        return [r for r in recs if _ts_of(r, "timestamp", "first_timestamp") > since]
    return recs

# 検証/観測とみなすツール（編集バーストの「介在」判定に使う）。
_VERIFICATION_TOOLS = frozenset({"Bash", "Read", "Grep", "Glob", "Skill", "Agent", "Task"})
_EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})

_MAX_EXAMPLES = 5


def _dedup(seq: List[str], limit: int = _MAX_EXAMPLES) -> List[str]:
    """順序を保ったまま重複・空を除いて先頭 limit 件を返す（sessions.jsonl の重複行対策）。"""
    seen: set = set()
    out: List[str] = []
    for s in seq:
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """jsonl を 1 行ずつ安全に読む（壊れた行はスキップ）。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    return out


def _ts_of(rec: Dict[str, Any], *fields: str) -> str:
    """複数候補フィールドから最初に値のあるタイムスタンプ文字列を返す。"""
    for f in fields:
        v = rec.get(f)
        if v:
            return str(v)
    return ""


def _in_window(ts: str, since: str) -> bool:
    """ts（ISO）が since 以降か。空 ts は窓外として扱う（決定論）。"""
    if not ts:
        return False
    return ts.replace("Z", "+00:00") >= since


def _normalize_pj(value: Optional[str]) -> Optional[str]:
    """PJ 識別子（フルパス or basename）を worktree 安全な slug に正規化する（#489）。

    既存の共有関数 ``utterance_archive.extractor.pj_slug_from_cwd`` に寄せる
    （新しい比較方式を発明しない。slug 1関数化 #492 にそのまま乗る）。これにより
    ``/x/rl-anything/.claude/worktrees/feedback`` のような worktree セッションの
    フルパスが本体 repo 名 ``rl-anything`` に正規化され、本体⇔worktree 間の取りこぼし
    （undercount）を防ぐ。フルパスを持たない basename だけのレコード（worktree slug の
    痕跡が ``project=feedback`` 等で固定済み）はフルパスが無いため pj_slug_from_cwd の
    素通し（basename）と同じく原値のまま残る — 復元不能な情報欠落であり本関数の責務外。
    import 不能環境では basename フォールバック。
    """
    if not value:
        return None
    try:
        from utterance_archive.extractor import pj_slug_from_cwd
        return pj_slug_from_cwd(value)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        return Path(str(value)).name or None


def _project_match(rec: Dict[str, Any], project: Optional[str]) -> bool:
    """レコードが当PJ slug に属するか（#489, worktree 安全）。

    project は呼び出し側で ``_normalize_pj`` 済みの当PJ slug（None なら全PJ対象 =
    後方互換 cross-PJ 集計）。PJ 識別フィールドはストアごとに異なる:
    corrections.jsonl は ``project_path``（フルパス）、sessions.jsonl は ``project``
    （basename）。いずれも ``_normalize_pj`` で worktree 安全 slug に正規化してから
    突合する。どの識別フィールドも持たない（未帰属）レコードは寛容に include する
    （他PJの誤混入でなく属性欠落なので当PJ集計から除外しない）。
    """
    if project is None:
        return True
    raw = rec.get("project_path") or rec.get("project") or rec.get("project_name")
    slug = _normalize_pj(raw)
    return slug == project or slug is None


def correction_recurrence_rate(
    days: int = 30, *, data_dir: Optional[Path] = None, project: Optional[str] = None
) -> Tuple[Optional[float], Dict[str, Any]]:
    """同型 correction（correction_type）が複数セッションに跨って再発した率。

    分母 = 窓内に出現した distinct correction_type 数
    分子 = そのうち 2 つ以上の distinct session_id で発生した correction_type 数

    値が高いほど「同じ手戻りを繰り返している」= 悪い。

    project（PJ basename）指定時は当PJ分のみを対象にする（#489）。None なら全PJ集計
    （cross-PJ 用途の後方互換）。
    """
    base = data_dir if data_dir is not None else DATA_DIR
    since = _iso_days_ago(days)
    records = [
        r for r in _read_jsonl(base / "corrections.jsonl")
        if _in_window(_ts_of(r, "timestamp"), since) and _project_match(r, project)
    ]
    if not records:
        return None, {"reason": "no_data", "store": "corrections.jsonl", "window_days": days}

    sessions_by_type: Dict[str, set] = {}
    for r in records:
        ctype = r.get("correction_type")
        sid = r.get("session_id") or ""
        if not ctype:
            continue
        sessions_by_type.setdefault(ctype, set()).add(sid)

    if not sessions_by_type:
        return None, {"reason": "no_data", "store": "corrections.jsonl", "window_days": days}

    distinct_types = len(sessions_by_type)
    recurring = {t: len(s) for t, s in sessions_by_type.items() if len(s) >= 2}
    rate = round(len(recurring) / distinct_types, 4)
    examples = dict(sorted(recurring.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_EXAMPLES])
    return rate, {
        "records": len(records),
        "distinct_types": distinct_types,
        "recurring_types": len(recurring),
        "examples": examples,  # {correction_type: distinct_session_count}
        "window_days": days,
    }


def first_try_success_rate(
    days: int = 30, *, data_dir: Optional[Path] = None, project: Optional[str] = None
) -> Tuple[Optional[float], Dict[str, Any]]:
    """エラーが 1 件も発生しなかったセッションの割合（error→retry 連鎖なし）。

    分母 = 窓内のセッション数
    分子 = error_count == 0 のセッション数

    値が高いほど「一発で通った」= 良い。

    project（PJ basename）指定時は当PJ分のみを対象にする（#489）。None なら全PJ集計。
    """
    base = data_dir if data_dir is not None else DATA_DIR
    since = _iso_days_ago(days)
    records = [
        r for r in read_sessions(base)
        if _in_window(_ts_of(r, "timestamp", "first_timestamp"), since)
        and _project_match(r, project)
    ]
    if not records:
        return None, {"reason": "no_data", "store": "sessions.jsonl", "window_days": days}

    clean: List[str] = []
    for r in records:
        ec = r.get("error_count")
        if ec is None:
            continue
        if ec == 0:
            clean.append(r.get("session_id") or "")
    total = len(records)
    rate = round(len(clean) / total, 4) if total else None
    if rate is None:
        return None, {"reason": "no_data", "store": "sessions.jsonl", "window_days": days}
    return rate, {
        "total_sessions": total,
        "clean_sessions": len(clean),
        "examples": _dedup(clean),
        "window_days": days,
    }


def _has_edit_burst(tool_sequence: List[str], min_consecutive: int) -> bool:
    """tool_sequence に検証ツールを介さない連続 Edit/Write が min_consecutive 以上あるか。"""
    run = 0
    for tool in tool_sequence:
        if tool in _EDIT_TOOLS:
            run += 1
            if run >= min_consecutive:
                return True
        elif tool in _VERIFICATION_TOOLS:
            run = 0
        # その他のツール（AskUserQuestion 等）は run を維持も増加もしない（中立）
    return False


def rework_rate(
    days: int = 30, *, min_consecutive: int = 3, data_dir: Optional[Path] = None,
    project: Optional[str] = None,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """編集ありセッションのうち、編集バースト（検証なし連続編集）を含む割合。

    分母 = 窓内で 1 度でも Edit/Write を行ったセッション数
    分子 = そのうち検証ツールを介さない連続編集が min_consecutive 以上あったセッション数

    値が高いほど「検証せず編集を繰り返した」= rework が多い = 悪い。

    近似の限界（ADR-046）: ストアに編集対象ファイル ID が無いため厳密な「同一ファイル
    N ターン内再編集」は算出不能。tool_sequence 上の編集バーストを proxy とする。

    project（PJ basename）指定時は当PJ分のみを対象にする（#489）。None なら全PJ集計。
    """
    base = data_dir if data_dir is not None else DATA_DIR
    since = _iso_days_ago(days)
    records = [
        r for r in read_sessions(base)
        if _in_window(_ts_of(r, "timestamp", "first_timestamp"), since)
        and _project_match(r, project)
    ]
    if not records:
        return None, {"reason": "no_data", "store": "sessions.jsonl", "window_days": days}

    edit_sessions: List[str] = []
    rework_sessions: List[str] = []
    for r in records:
        seq = r.get("tool_sequence")
        if not isinstance(seq, list) or not seq:
            continue
        if not any(t in _EDIT_TOOLS for t in seq):
            continue
        sid = r.get("session_id") or ""
        edit_sessions.append(sid)
        if _has_edit_burst(seq, min_consecutive):
            rework_sessions.append(sid)

    if not edit_sessions:
        return None, {"reason": "no_data", "store": "sessions.jsonl", "window_days": days}

    rate = round(len(rework_sessions) / len(edit_sessions), 4)
    return rate, {
        "total_sessions": len(edit_sessions),  # 編集ありセッションのみが母集団
        "rework_sessions": len(rework_sessions),
        "min_consecutive": min_consecutive,
        "examples": _dedup(rework_sessions),
        "window_days": days,
    }


def compute_outcome_metrics(
    days: int = 30, *, data_dir: Optional[Path] = None, project: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """3 軸をまとめて算出する。各軸 {"value": float|None, "evidence": dict}。

    project（PJ basename）指定時は当PJ分のみを対象にする（#489）。None なら全PJ集計
    （後方互換）。当PJレポートの表示には project を渡して当PJスコープに直す。
    昇格判断（promotion_readiness）は per-PJ 分解を別途持つため本関数は経由しない。
    """
    cr_v, cr_e = correction_recurrence_rate(days, data_dir=data_dir, project=project)
    fs_v, fs_e = first_try_success_rate(days, data_dir=data_dir, project=project)
    rw_v, rw_e = rework_rate(days, data_dir=data_dir, project=project)
    return {
        "correction_recurrence": {"value": cr_v, "evidence": cr_e},
        "first_try_success": {"value": fs_v, "evidence": fs_e},
        "rework": {"value": rw_v, "evidence": rw_e},
    }
