"""correction capture 率の決定論算出（#421）。

RL ループの報酬入力（corrections）がほぼ空（実測: 9 件 / 76 日, 3.3B tokens 消費 PJ）だった
ことを受け、capture が「検出器の仕様通りの少なさ」なのか「capture 漏れ」なのかを判別可能に
するための observability メトリクス。**スコア重みには入れない**（advisory 表示のみ。壊れた入力
の上に重みを作らない）。

定義（決定論・LLM 非依存）:
  - ターン proxy = usage.jsonl の同一 session_id レコード数（tool/skill 1 呼び出し = 1 ターン）。
    UserPromptSubmit に紐づく専用の per-turn ストアが無いため、最も信頼できる per-session の
    活動量である usage 行数を proxy とする。
  - active_sessions（分母）   = days 窓内で min_turns 以上のターンを持つセッション数。
  - captured_sessions（分子） = そのうち corrections.jsonl に同一 session_id の correction を
    1 件以上持つセッション数。
  - capture_rate = captured_sessions / active_sessions（分母 0 なら applicable=False）。

usage.jsonl / corrections.jsonl はいずれも hook が書く plugin-data dir 系ストア（#358）。
本モジュールは duckdb を import しない（hot hook では使わないが、audit からも軽量に呼べるよう
JSONL を直接読む）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MIN_TURNS = 20
DEFAULT_DAYS = 30


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """JSONL を読み、壊れた行はスキップする。未存在なら []。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def compute_capture_rate(
    *,
    usage_file: Optional[Path] = None,
    corrections_file: Optional[Path] = None,
    days: int = DEFAULT_DAYS,
    min_turns: int = DEFAULT_MIN_TURNS,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """correction capture 率を算出する。

    Args:
        usage_file: usage.jsonl パス。None なら hook_store_path で正準解決。
        corrections_file: corrections.jsonl パス。None なら同上。
        days: 集計窓（日）。usage の ``ts`` がこの窓内のものだけを数える。
        min_turns: active とみなすセッションの最小ターン数（usage 行数 proxy）。
        project: 指定時はこの当PJ slug のレコードのみ対象（#489）。レコードの project /
            project_name / project_path は worktree 安全 slug に正規化して突合する
            （呼び出し側も同じ正規化で project を渡すこと）。未帰属レコードは寛容に include。

    Returns:
        {
          "applicable": bool,          # 分母 > 0 のときだけ True
          "active_sessions": int,      # 分母
          "captured_sessions": int,    # 分子
          "capture_rate": float,       # 分子 / 分母（分母 0 なら 0.0）
          "min_turns": int,
          "days": int,
        }
    """
    if usage_file is None or corrections_file is None:
        from rl_common import hook_store_path

        if usage_file is None:
            usage_file = hook_store_path("usage.jsonl")
        if corrections_file is None:
            corrections_file = hook_store_path("corrections.jsonl")

    cutoff = _iso_days_ago(days)

    # usage.jsonl → session_id ごとのターン数（窓内のみ）。
    turns_by_session: Dict[str, int] = {}
    for rec in _load_jsonl(Path(usage_file)):
        if project is not None and not _project_match(rec, project):
            continue
        ts = rec.get("ts") or rec.get("timestamp") or ""
        if ts and ts < cutoff:
            continue
        sid = rec.get("session_id") or ""
        if not sid:
            continue
        turns_by_session[sid] = turns_by_session.get(sid, 0) + 1

    active = {sid for sid, n in turns_by_session.items() if n >= min_turns}
    active_count = len(active)

    # corrections.jsonl → correction を持つ session_id 集合（窓内のみ）。
    corrected_sessions: set = set()
    for rec in _load_jsonl(Path(corrections_file)):
        if project is not None and not _project_match(rec, project):
            continue
        ts = rec.get("timestamp") or rec.get("ts") or ""
        if ts and ts < cutoff:
            continue
        sid = rec.get("session_id") or ""
        if sid:
            corrected_sessions.add(sid)

    captured = len(active & corrected_sessions)
    rate = round(captured / active_count, 4) if active_count > 0 else 0.0

    return {
        "applicable": active_count > 0,
        "active_sessions": active_count,
        "captured_sessions": captured,
        "capture_rate": rate,
        "min_turns": min_turns,
        "days": days,
    }


def _normalize_pj(value: Optional[str]) -> Optional[str]:
    """PJ 識別子（フルパス or basename）を worktree 安全な slug に正規化する（#489）。

    既存の共有関数 ``utterance_archive.extractor.pj_slug_from_cwd`` に寄せる
    （新しい比較方式を発明しない。slug 1関数化 #492 にそのまま乗る）。
    ``/x/evolve-anything/.claude/worktrees/feedback`` を本体 repo 名 ``evolve-anything`` へ
    正規化し、本体⇔worktree 間の取りこぼし（undercount）を防ぐ。
    import 不能環境では basename フォールバック。
    """
    if not value:
        return None
    try:
        from utterance_archive.extractor import pj_slug_from_cwd
        return pj_slug_from_cwd(value)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        return Path(str(value)).name or None


def _project_match(rec: Dict[str, Any], project: str) -> bool:
    """レコードが当PJ slug（``project``）に一致するか（#489, worktree 安全）。

    PJ 識別フィールドはストアごとに異なる: usage.jsonl は ``project``（basename）、
    corrections.jsonl は ``project_path``（フルパス）。いずれも ``_normalize_pj`` で
    worktree 安全 slug に正規化してから突合する。``project`` 引数は呼び出し側で正規化済み
    の当PJ slug。どの識別フィールドも持たない（未帰属）レコードは寛容に include する
    （unattributed 救済 = 他PJの誤混入でなく属性欠落なので、当PJ集計から除外しない）。
    """
    raw = rec.get("project_path") or rec.get("project") or rec.get("project_name")
    slug = _normalize_pj(raw)
    return slug == project or slug is None
