"""subagent の「completed 報告」↔内部完遂の意味的乖離 advisory section（worker-takeoff, #161）。

背景（MEMORY 頻出の worker-takeoff）: impl-worker が中間ナレーションで止まったまま harness に
completed 扱いされ、報告は「done」に見えても `git log`/`git status` は未完遂——という乖離を、
これまでは頭が毎回手で git 突合して回収していた（arXiv 2607.02507 "What LLM Agents Say When
No One Is Watching" の off-record/public 乖離検知を自己運用に転写）。

**設計判断（read-time 導出・新ストアなし）**: subagent_traces.jsonl（#38）は transcript の
tool_use/tool_result カウントのみを持ち、報告テキスト自体は保持しない。一方
subagents.jsonl（SubagentStop hook の生ログ）は `last_assistant_message`（最終 assistant
テキスト、hooks 側で 500 字に先頭から切り詰め済み）を既に持っている。本 section はこの
既存フィールドを直接読み、`rl_common.detect_takeoff_divergence`（決定論2シグナル AND・
pure function）で read 時に判定する。新ストア・新フィールド・ingest 変更は不要
（`learning_derive_state_from_logs_not_forward_write` 方針）。契約は
`sections_subagent_noise.py` と同型（DATA_DIR 直接 union read・pj_slug scope・
`is_noise_agent_type` でノイズ除外・agent_id 単位 last-append-wins）。

沈黙境界: 当 PJ レコード 0 件 → None（評価対象なし）。suspected 0 件 → None
（subagent_noise と同じ「無ければ非表示」慣習）。スコア重みには反映しない（advisory のみ）。
LLM 判定（Haiku 格上げ）は今回スコープ外（issue #161 の再評価条件に残す）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

try:
    from rl_common import DATA_DIR, detect_takeoff_divergence, is_noise_agent_type
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

    def is_noise_agent_type(agent_type):  # type: ignore
        return not str(agent_type or "").strip()

    def detect_takeoff_divergence(_last_assistant_message):  # type: ignore
        return None


def _normalize_pj(value):
    """PJ 識別子を worktree 安全 slug に正規化する（fanout_cost / subagent_noise と共有）。"""
    try:
        from audit import outcome_metrics
        return outcome_metrics._normalize_pj(value)
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        if not value:
            return None
        return Path(str(value)).name or None


def _iter_data_dirs(base: Path) -> List[Path]:
    """DATA_DIR 断片化（canonical / legacy rename / plugins-data split）を cross-dir union read する。"""
    try:
        from rl_common import iter_read_data_dirs
        return list(iter_read_data_dirs(base))
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        return [base]


def compute_worker_takeoff(project_dir) -> Optional[Dict[str, Any]]:
    """当 PJ の subagents.jsonl から worker-takeoff 疑いを agent_type 別に集計する。

    agent_id 単位で last-append-wins（同一 agent_id の再発火は最新の
    ``last_assistant_message`` を採用・subagent_traces.store.read_traces と同方針）。
    noise agent_type（空 / ID 形）は評価対象から除外する。

    Returns:
        None（当 PJ レコード 0 件 = 評価対象なし）または dict:
          {"total": int, "unjudged": int, "suspected_total": int,
           "by_agent_type": {agent_type: {"n": int, "suspected": int}}}
    """
    base = DATA_DIR
    project = _normalize_pj(str(project_dir)) if project_dir is not None else None
    latest: Dict[str, Dict[str, Any]] = {}

    for d in _iter_data_dirs(base):
        path = d / "subagents.jsonl"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
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
            if project is not None:
                slug = _normalize_pj(rec.get("project") or rec.get("project_path"))
                if slug is not None and slug != project:
                    continue
            agent_id = rec.get("agent_id")
            if not agent_id:
                continue
            if is_noise_agent_type(rec.get("agent_type", "")):
                continue
            latest[agent_id] = rec  # last-append-wins（append 順 = 時系列）

    if not latest:
        return None

    by_type: Dict[str, Dict[str, int]] = {}
    unjudged = 0
    suspected_total = 0
    for rec in latest.values():
        at = rec.get("agent_type") or ""
        verdict = detect_takeoff_divergence(rec.get("last_assistant_message"))
        b = by_type.setdefault(at, {"n": 0, "suspected": 0})
        b["n"] += 1
        if verdict is None:
            unjudged += 1
        elif verdict:
            b["suspected"] += 1
            suspected_total += 1

    return {
        "total": len(latest),
        "unjudged": unjudged,
        "suspected_total": suspected_total,
        "by_agent_type": by_type,
    }


def build_worker_takeoff_section(project_dir: Path) -> Optional[List[str]]:
    """worker-takeoff（completed だが完了報告なし）疑いを audit に advisory 表示する。

    - subagents レコードが当 PJ に 0 件 → None（沈黙・評価対象なし）
    - 疑い 0 件 → None（無ければ非表示・subagent_noise と同慣習）
    - 疑いあり → agent_type 別に ⚠ 件数 + 手動確認への誘導文を surface。
    """

    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        return compute_worker_takeoff(proj)

    def render(data: Dict[str, Any]) -> List[str]:
        total = data["total"]
        unjudged = data["unjudged"]
        suspected_total = data["suspected_total"]
        lines = [
            f"⚠ worker-takeoff 疑い（completed だが完了報告なし）{suspected_total} 件 / "
            f"評価対象 {total} 件（未評価 {unjudged} 件 — メッセージ空 or 500字打ち切りで判定不能）。",
        ]
        for at in sorted(data["by_agent_type"]):
            b = data["by_agent_type"][at]
            if b["suspected"] > 0:
                lines.append(f"  ・⚠ {at}: {b['suspected']} / {b['n']} 件")
        lines.append("")
        lines.append(
            "  → harness に completed 扱いされたのに、最終 assistant メッセージが完了署名"
            "（`=== ... ===` マーカー / 報告見出し）を含まず前向きナレーション"
            "（行末 `:` / Now・Next・Let's 系）で終わっています（#161）。該当 agent_id の"
            " transcript・git log/git status を手動確認し、未完遂なら再開してください。"
        )
        return lines

    return build_advisory_section(
        project_dir,
        title="Worker Takeoff Divergence (当PJ・advisory — completed≠完遂疑い・スコア重み非関与)",
        blurb=[
            "subagent が harness に completed 扱いされたのに、最終出力が完了報告でなく中間"
            "ナレーションのまま終わっている疑いを、subagents.jsonl の last_assistant_message"
            " から決定論2シグナル AND（完了署名の欠如×前向きナレーション終端）で検出します"
            "（worker-takeoff, #161）。LLM を使わず決定論で算出。",
        ],
        compute=compute,
        applicable=lambda data: data["suspected_total"] > 0,
        render=render,
    )
