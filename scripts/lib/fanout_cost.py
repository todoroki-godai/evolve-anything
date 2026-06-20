"""fan-out 費用対効果の決定論算出（#14, advisory）。

「複数 agent を fan-out すると本当に得か」を既存テレメトリから決定論・LLM 非依存で
可視化する。fitness の重み軸にはしない（outcome_metrics / memory_capability と同じ
advisory レーン）。arXiv 2606.13003（multi-agent fan-out の費用対効果）に対応。

設計（cost 先行・advantage はデータゲート付き）:

1. fan-out COST（常に算出可能・非スパース）— 当 PJ の fan-out 実態:
   - fan-out session 率 = 「同一 parent session_id が subagent ≥2 体を spawn した session 数」
     / 「subagent を1体以上 spawn した session 数」
   - fan-out session あたり平均 subagent 数、agent_type 内訳
   - token cost: session 単位 join が綺麗にできないため subagent 体数を cost proxy とし
     「token 直接 join は未対応」と注記する（捏造しない）。

2. fan-out ADVANTAGE（データ充足ゲート付き）— fan-out session 群 vs 単一 agent session 群の
   一発成功率（first_try_success）差:
   - 各群の session 数が floor 未満なら値を出さず「データ不足（サンプル不足）」を明示
     （outcome_metrics の insufficient_sample / floor と同方針。#15/#10 が踏んだ
     構造的スパース性への対処）。

データ源:
- subagents.jsonl（DATA_DIR・全PJ共通）: 1 レコード = subagent 1 spawn。
  agent_type 空のレコードは本物の Task subagent でないので除外する（#36）。
- sessions（一発成功率の母集団）: outcome_metrics.read_sessions（session_store union read,
  #469）を流用し error_count==0 を一発成功とみなす（outcome_metrics と同判定）。
- PJ スコープ: outcome_metrics._normalize_pj で当 PJ slug に正規化（worktree 安全・#489）。

テストは ``monkeypatch.setattr(fanout_cost, "DATA_DIR", tmp_path)`` で直接この module 属性を
差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

# fan-out / single 各群の最小 session 数 floor。outcome_metrics の各群 ≥5 方針に倣う
# （edit_sessions≥5 / distinct_types≥5）。floor 未満では advantage delta を出さず
# 「データ不足（サンプル不足）」を明示する（沈黙 != 評価不能, #393-#396 / #15）。
MIN_GROUP_SESSIONS_FLOOR = 5

_FANOUT_THRESHOLD = 2  # parent が ≥ この体数を spawn したら fan-out session とみなす


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _in_window(ts: str, since: str) -> bool:
    if not ts:
        return False
    return ts.replace("Z", "+00:00") >= since


def _normalize_pj(value: Optional[str]) -> Optional[str]:
    """PJ 識別子を worktree 安全 slug に正規化する（outcome_metrics と同関数を共有, #489）。"""
    try:
        from audit import outcome_metrics
        return outcome_metrics._normalize_pj(value)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        if not value:
            return None
        return Path(str(value)).name or None


def _read_subagents(base: Path, since: str, project: Optional[str]) -> List[Dict[str, Any]]:
    """当 PJ・窓内・agent_type 非空の subagent レコードを返す（#36, #489）。

    collectors.aggregate_subagents_by_project と同じ除外を適用する（agent_type 空除外）。
    """
    path = base / "subagents.jsonl"
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        # #36: agent_type 空は本物の Task subagent でない。reader 契約として除外する。
        if not str(rec.get("agent_type", "")).strip():
            continue
        if not _in_window(str(rec.get("timestamp", "")), since):
            continue
        # #489: 当 PJ スコープに絞る。識別フィールドが無いレコードは寛容に include。
        if project is not None:
            slug = _normalize_pj(rec.get("project") or rec.get("project_path"))
            if slug is not None and slug != project:
                continue
        out.append(rec)
    return out


def _success_by_session(base: Path, since: str, project: Optional[str]) -> Dict[str, bool]:
    """当 PJ・窓内の session_id → 一発成功（error_count==0）の写像を返す。

    outcome_metrics.read_sessions（session_store union read, #469）を流用し、
    first_try_success と同じ error_count==0 判定を使う（#469 / outcome_metrics 準拠）。
    error_count が無い session は判定不能として除外する。
    """
    try:
        from audit import outcome_metrics
        records = outcome_metrics.read_sessions(base)
    except ImportError:  # pragma: no cover
        return {}
    result: Dict[str, bool] = {}
    for r in records:
        ts = r.get("timestamp") or r.get("first_timestamp") or ""
        if not _in_window(str(ts), since):
            continue
        if project is not None:
            slug = _normalize_pj(r.get("project") or r.get("project_path") or r.get("project_name"))
            if slug is not None and slug != project:
                continue
        ec = r.get("error_count")
        if ec is None:
            continue
        sid = r.get("session_id") or ""
        if not sid:
            continue
        # 同一 session の重複行は「いずれかにエラー」を成功失敗の保守側に倒す
        # （error がついた行があれば失敗扱い）。
        prev = result.get(sid)
        cur = (ec == 0)
        result[sid] = cur if prev is None else (prev and cur)
    return result


def _compute_cost(subagents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """fan-out cost（fan-out 率 / 平均 subagent / agent_type 内訳 / count proxy）。"""
    by_session: Dict[str, int] = {}
    breakdown: Dict[str, int] = {}
    for rec in subagents:
        sid = rec.get("session_id") or ""
        by_session[sid] = by_session.get(sid, 0) + 1
        at = str(rec.get("agent_type", "")).strip()
        breakdown[at] = breakdown.get(at, 0) + 1

    spawning = len(by_session)
    fanout_sids = [s for s, c in by_session.items() if c >= _FANOUT_THRESHOLD]
    fanout_sessions = len(fanout_sids)
    fanout_subagents = sum(by_session[s] for s in fanout_sids)
    rate = round(fanout_sessions / spawning, 4) if spawning else 0.0
    avg = round(fanout_subagents / fanout_sessions, 4) if fanout_sessions else 0.0
    # agent_type 内訳は件数降順で安定化（同数は名前順）。
    breakdown_sorted = dict(
        sorted(breakdown.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return {
        "value": {
            "fanout_session_rate": rate,
            "avg_subagents_per_fanout_session": avg,
        },
        "evidence": {
            "spawning_sessions": spawning,
            "fanout_sessions": fanout_sessions,
            "total_subagents": len(subagents),
            "fanout_subagents": fanout_subagents,
            "agent_type_breakdown": breakdown_sorted,
            # token 単位の session join は subagents↔token_usage に綺麗な対応が無いため
            # 未対応。subagent 体数を cost proxy とする（捏造しない, 設計どおり）。
            "token_join": "unsupported_proxy_count",
        },
    }


def _compute_advantage(
    subagents: List[Dict[str, Any]], success_by_session: Dict[str, bool]
) -> Dict[str, Any]:
    """fan-out 群 vs single 群の一発成功率 delta（floor ゲート付き）。"""
    by_session: Dict[str, int] = {}
    for rec in subagents:
        sid = rec.get("session_id") or ""
        by_session[sid] = by_session.get(sid, 0) + 1

    fanout_sids = [s for s, c in by_session.items() if c >= _FANOUT_THRESHOLD]
    single_sids = [s for s, c in by_session.items() if c == 1]

    fanout_eval = [s for s in fanout_sids if s in success_by_session]
    single_eval = [s for s in single_sids if s in success_by_session]

    if len(fanout_eval) < MIN_GROUP_SESSIONS_FLOOR or len(single_eval) < MIN_GROUP_SESSIONS_FLOOR:
        return {
            "value": None,
            "evidence": {
                "reason": "insufficient_sample",
                "fanout_group_sessions": len(fanout_eval),
                "single_group_sessions": len(single_eval),
                "floor": MIN_GROUP_SESSIONS_FLOOR,
            },
        }

    fanout_rate = round(sum(1 for s in fanout_eval if success_by_session[s]) / len(fanout_eval), 4)
    single_rate = round(sum(1 for s in single_eval if success_by_session[s]) / len(single_eval), 4)
    delta = round(fanout_rate - single_rate, 4)
    return {
        "value": delta,
        "evidence": {
            "fanout_success_rate": fanout_rate,
            "single_success_rate": single_rate,
            "fanout_group_sessions": len(fanout_eval),
            "single_group_sessions": len(single_eval),
        },
    }


def compute_fanout_metrics(project_dir, *, days: int = 30) -> Dict[str, Any]:
    """fan-out cost + advantage を当 PJ スコープで算出する（決定論・LLM 非依存）。

    当 PJ の subagents が窓内に 1 件も無ければ評価対象が無いので
    ``{"applicable": False}`` を返す（builder はこれを見て沈黙 = None を返す）。
    1 件以上あれば cost（常時算出）+ advantage（floor ゲート付き）を返す。

    返り値（applicable=True 時）:
        {
          "applicable": True,
          "cost": {"value": {...}, "evidence": {...}},
          "advantage": {"value": float|None, "evidence": {...}},
          "window_days": days,
        }
    """
    base = DATA_DIR
    since = _iso_days_ago(days)
    project = _normalize_pj(str(project_dir)) if project_dir is not None else None

    subagents = _read_subagents(base, since, project)
    if not subagents:
        return {"applicable": False}

    success_by_session = _success_by_session(base, since, project)
    return {
        "applicable": True,
        "cost": _compute_cost(subagents),
        "advantage": _compute_advantage(subagents, success_by_session),
        "window_days": days,
    }
