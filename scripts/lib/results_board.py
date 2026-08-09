#!/usr/bin/env python3
"""results_board — 戦果ボード（#379 Step 4）。

optimize_history（accept/reject 決定ログ）と corrections（手直し）を直読みし、
「手直し回数の減少」「採用した改善」「取り下げ候補」を決定論で1画面表示する。

growth-journal harness（crystallization イベント記録・growth_narrative の成長ストーリー）
削除の置換成果物。「記録は全自動・判断は朝の30秒・効果は週1の数字で実感」の3本目
（週1の戦果を数字で見せる）を、新規 write-only ストアを作らず既存ストア
（optimize_history / corrections）の直読みだけで実現する（#379 の新設凍結方針にも整合）。

決定論・LLM 非依存・read-only（ファイル書き込みなし）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from optimize_history_store import load_history
from telemetry_query import query_corrections
from correction_semantic.provenance_weight import count_human_corrections

_WINDOW_DAYS = 30

# #379 Step 4 実データ較正（~/.claude/evolve-anything/optimize_history/evolve-anything.jsonl・
# 38件・2026-08-10 読み取り時点）: pytest 実行由来の一時パス汚染は "pytest-of-" だけでなく
# macOS tmpfile 規約（/T/tmp<random>/ 等）にも及ぶ。狭い "pytest-of-" 限定では 30 件中 13 件
# （tmp<random> パターン）を取り逃し、真の accepted 件数が 1 件のところ複数件を誤って
# accepted と数えてしまう。よって temp dir パターンも広く拾う。
_PYTEST_OF_MARKER = "pytest-of-"
_TMP_DIR_RE = re.compile(r"/tmp[^/]*/")


def _is_test_polluted(entry: Dict[str, Any]) -> bool:
    """entry の target/skill_name がテスト実行由来の一時パスかを判定する。"""
    for key in ("target", "skill_name"):
        value = str(entry.get(key) or "")
        if not value:
            continue
        if _PYTEST_OF_MARKER in value or _TMP_DIR_RE.search(value):
            return True
    return False


def classify_decision(entry: Dict[str, Any]) -> str:
    """optimize_history の1エントリを accepted/rejected/pending/excluded に正規化する。

    実データのフィールド埋まり方は source によって3系統に分裂している
    （#279/#286/#290 の提案ID/判断イベントID分離と同根の split）:
      - source="evolve_remediation": human_accepted（bool）で判定
      - source=None（optimize / evolve-loop 由来）: approved（bool）で判定
      - それ以外の未知 source: pending（安全側フォールバック・将来 source 追加への耐性）

    優先順位: fitness_eligible=False（#376 の誤帰属 accept 無効化フラグ）を最優先で
    excluded。次点でテスト汚染パス（#420 growth-journal 汚染と同系統）を excluded。
    """
    if entry.get("fitness_eligible") is False:
        return "excluded"
    if _is_test_polluted(entry):
        return "excluded"

    source = entry.get("source")
    decided = entry.get("human_accepted") if source == "evolve_remediation" else entry.get("approved")

    if decided is True:
        return "accepted"
    if decided is False:
        return "rejected"
    return "pending"


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    """timestamp を aware UTC datetime にパースする（growth_report._is_today と同型）。

    naive 文字列（tz 情報なし）は UTC として解釈する。パース不能・非文字列は None。
    """
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _in_window(
    record: Dict[str, Any], start: datetime, end: datetime, *, inclusive_end: bool = False
) -> bool:
    """[start, end) 判定。``inclusive_end=True`` なら [start, end]（``now`` ちょうどを含める）。

    直近 window の上限は呼び出し時点の ``now`` 自身であることが多く、排他的にすると
    「たった今」記録されたエントリが直近集計から漏れる。前 window との境界（window_start）は
    二重計上を避けるため排他のまま据え置く。
    """
    ts = _parse_timestamp(record.get("timestamp"))
    if ts is None:
        return False
    if inclusive_end:
        return start <= ts <= end
    return start <= ts < end


def build_results_board(slug: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """戦果ボードを決定論生成する（read-only・LLM 非依存）。

    Args:
        slug: PJ slug。optimize_history_store.load_history と telemetry_query の
            project フィルタの両方に共通で使う。リポジトリ直下（worktree でない）呼び出しでは
            pj_slug.resolve_pj_slug の basename と telemetry_query の project-name（ディレクトリ
            basename）が一致する前提（既存コードの growth_report.py 等と同じ簡略化）。
        now: 基準時刻（省略時は現在の UTC）。テストの決定論性のため注入可能にする。

    Returns:
        rework（手直し件数の直近30日/その前30日/増減）・decisions（accepted/rejected/
        pending/excluded の直近30日件数・excluded も常時 key を出す=silence≠evaluated）・
        accepted_list（直近30日 accepted の skill_name+日付、最大10件・新しい順）・
        withdrawal_candidates（accepted のうち verdict==REGRESSED のもの）。
    """
    _now = now or datetime.now(timezone.utc)
    window_start = _now - timedelta(days=_WINDOW_DAYS)
    prev_window_start = _now - timedelta(days=_WINDOW_DAYS * 2)

    # ── 手直し回数（human corrections）: 直近30日 vs その前30日 ──────────
    try:
        corrections = query_corrections(project=slug) or []
    except Exception:
        corrections = []

    recent_corrections = [
        c for c in corrections if _in_window(c, window_start, _now, inclusive_end=True)
    ]
    prev_corrections = [
        c for c in corrections if _in_window(c, prev_window_start, window_start)
    ]
    recent_rework = count_human_corrections(recent_corrections)
    prev_rework = count_human_corrections(prev_corrections)

    # ── 採用した改善: 直近30日の optimize_history ─────────────────
    try:
        history = load_history(slug) or []
    except Exception:
        history = []

    recent_history = [
        h for h in history if _in_window(h, window_start, _now, inclusive_end=True)
    ]

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "accepted": [], "rejected": [], "pending": [], "excluded": [],
    }
    for entry in recent_history:
        buckets[classify_decision(entry)].append(entry)

    def _label(entry: Dict[str, Any]) -> str:
        return entry.get("skill_name") or entry.get("target") or "(unknown)"

    accepted_list = sorted(
        (
            {"skill_name": _label(e), "timestamp": e.get("timestamp")}
            for e in buckets["accepted"]
        ),
        key=lambda x: x["timestamp"] or "",
        reverse=True,
    )[:10]

    # ── 取り下げ候補: accepted のうち verdict == REGRESSED ──────────
    withdrawal_candidates = [
        {"skill_name": _label(e), "timestamp": e.get("timestamp"), "verdict": e.get("verdict")}
        for e in buckets["accepted"]
        if e.get("verdict") == "REGRESSED"
    ]

    return {
        "slug": slug,
        "generated_at": _now.isoformat(),
        "rework": {
            "recent_30d": recent_rework,
            "previous_30d": prev_rework,
            "delta": recent_rework - prev_rework,
        },
        "decisions": {
            "accepted": len(buckets["accepted"]),
            "rejected": len(buckets["rejected"]),
            "pending": len(buckets["pending"]),
            "excluded": len(buckets["excluded"]),
        },
        "accepted_list": accepted_list,
        "withdrawal_candidates": withdrawal_candidates,
    }


def render_results_board(board: Dict[str, Any]) -> List[str]:
    """戦果ボードの markdown ブロックを生成する（判定+行動を最上部・証拠は直下）。"""
    rework = board["rework"]
    decisions = board["decisions"]

    lines = ["## 🏆 戦果ボード", ""]

    delta = rework["delta"]
    if delta < 0:
        headline = f"手直しが {rework['previous_30d']}→{rework['recent_30d']} 件に減少（直近30日）"
    elif delta > 0:
        headline = f"手直しが {rework['previous_30d']}→{rework['recent_30d']} 件に増加（直近30日）"
    else:
        headline = f"手直しは {rework['recent_30d']} 件で横ばい（直近30日）"
    lines.append(f"**{headline}**")
    lines.append("")

    lines.append(
        f"採用した改善（直近30日）: accepted {decisions['accepted']} 件 / "
        f"rejected {decisions['rejected']} 件 / pending {decisions['pending']} 件 / "
        f"excluded {decisions['excluded']} 件"
    )
    lines.append("")

    if board["accepted_list"]:
        lines.append("### 直近の採用")
        for item in board["accepted_list"]:
            ts = (item.get("timestamp") or "")[:10]
            lines.append(f"- {ts}: {item['skill_name']}")
        lines.append("")

    if board["withdrawal_candidates"]:
        lines.append("### 取り下げ候補（採用後に REGRESSED 判定）")
        for item in board["withdrawal_candidates"]:
            ts = (item.get("timestamp") or "")[:10]
            lines.append(f"- {ts}: {item['skill_name']} — verdict={item['verdict']}")
        lines.append("")

    return lines
