"""decomposition — 軌跡を Workflow-to-Skill の4軸へ分解する。

Workflow-to-Skill (arXiv 2606.06893) は、エージェントのワークフローを
``routing`` / ``workflow`` / ``semantics`` / ``attachments`` の4要素へ分解して
再利用可能なスキルを生成する。本モジュールは TrajectoryRecord 群から、その4軸を
決定論的に導く（LLM 非依存）。

各軸の意味と、軌跡から取れる近似:

- ``routing``     : いつ/どんな文脈で発火するか
                    → user_prompt の頻出キーワード + 代表プロンプト
- ``workflow``    : どう実行されるか（手順そのものは軌跡に残らないため実行プロファイルで近似）
                    → 呼び出し回数 + outcome 分布
- ``semantics``   : 何をするか
                    → スキル名（namespace / base_name）
- ``attachments`` : どの文脈に anchor されているか（≒ 必要リソースの広がり）
                    → distinct session 数。単一セッション由来なら session_bound=True
                      （= 一過性バーストで reuse 証拠が弱い）。projects は cross-project
                      な直接 API 利用のために残置（wired discover は単一 PJ scope なので
                      projects 自体は弁別しない）

Issue #381
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from skill_extractor.trajectory_sampler import TrajectoryRecord

# ── 定数 ──────────────────────────────────────────────────

ROUTING_KEYWORD_LIMIT = 5
"""routing.trigger_keywords に残す頻出語の最大数。"""

SAMPLE_TRIGGER_LIMIT = 3
"""routing.sample_triggers に残す代表プロンプトの最大数。"""

# agent_team.py と同じトークン規則（英数字 + ひらがな/カタカナ/漢字の連続）
_TOKEN_RE = re.compile(r"[a-z0-9ぁ-んァ-ヶ一-龠]+", re.IGNORECASE)

# 英語の機能語 + 日本語の汎用語（agent_team._STOPWORDS を本モジュール用に流用）
_STOPWORDS = {
    "use", "this", "the", "to", "a", "an", "for", "of", "and", "or", "is",
    "are", "on", "in", "with", "your", "you", "that", "it", "as", "by", "be",
    "please", "can", "do", "make", "want", "need",
    "する", "して", "した", "こと", "ため", "もの", "など", "場合", "とき",
    "ください", "ほしい", "たい", "やって", "お願い",
}


# ── 公開関数 ──────────────────────────────────────────────


def decompose_candidate(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """TrajectoryRecord 群を Workflow-to-Skill の4軸へ分解する。

    Args:
        records: 同一スキルの TrajectoryRecord リスト。空でも4軸の骨格は返す。

    Returns:
        ``{"routing": {...}, "workflow": {...}, "semantics": {...},
        "attachments": {...}}`` の dict。
    """
    return {
        "routing": _routing(records),
        "workflow": _workflow(records),
        "semantics": _semantics(records),
        "attachments": _attachments(records),
    }


# ── 各軸 ──────────────────────────────────────────────────


def _routing(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """いつ/どんな文脈で発火するか（trigger）。"""
    prompts = [r.user_prompt.strip() for r in records if r.user_prompt.strip()]

    counter: Counter = Counter()
    for p in prompts:
        for tok in _TOKEN_RE.findall(p.lower()):
            if len(tok) > 1 and tok not in _STOPWORDS:
                counter[tok] += 1

    trigger_keywords = [w for w, _ in counter.most_common(ROUTING_KEYWORD_LIMIT)]

    sample_triggers: List[str] = []
    seen: set = set()
    for p in prompts:
        if p not in seen:
            sample_triggers.append(p)
            seen.add(p)
        if len(sample_triggers) >= SAMPLE_TRIGGER_LIMIT:
            break

    return {
        "trigger_keywords": trigger_keywords,
        "sample_triggers": sample_triggers,
    }


def _workflow(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """どう実行されるか。手順は軌跡に残らないため実行プロファイルで近似する。"""
    outcomes = {"success": 0, "failure": 0, "unknown": 0}
    for r in records:
        key = r.outcome if r.outcome in outcomes else "unknown"
        outcomes[key] += 1
    return {
        "invocations": len(records),
        "outcomes": outcomes,
    }


def _semantics(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """何をするか（スキル identity）。"""
    skill_name = records[0].skill_name if records else ""
    if ":" in skill_name:
        namespace, base_name = skill_name.split(":", 1)
    else:
        namespace, base_name = None, skill_name
    return {
        "base_name": base_name,
        "namespace": namespace,
    }


def _attachments(records: List[TrajectoryRecord]) -> Dict[str, Any]:
    """どの文脈に anchor されているか（≒ 必要リソースの広がり）。

    Workflow-to-Skill の attachments（必要リソース）は軌跡にファイル単位では残らない。
    代わりに「スキルが何件の distinct セッションにまたがって発火したか」を anchor の
    広がりとして測る。実 discover の採掘は単一 PJ scope（`_project_transcript_dir`、
    cross-PJ noise 防止）のため ``projects`` は弁別しないが、``session_count`` は
    wired path でも弁別する: 単一セッション由来（``session_bound=True``）は一過性の
    バーストで skill 化の根拠が弱く、複数セッションにまたがるほど定着パターンとして
    CREATE の根拠が強い。``projects`` は cross-project な直接 API 利用のために残置する。
    """
    projects: List[str] = []
    seen_proj: set = set()
    sessions: set = set()
    for r in records:
        src = r.extra.get("source_file", "") if isinstance(r.extra, dict) else ""
        if src:
            proj = Path(src).parent.name
            if proj and proj not in seen_proj:
                projects.append(proj)
                seen_proj.add(proj)
        sid = (r.session_id or "").strip()
        if sid:
            sessions.add(sid)
    session_count = len(sessions)
    return {
        "projects": projects,
        "session_count": session_count,
        # 単一（または 0）セッション由来 = 一過性で reuse 証拠が弱い
        "session_bound": session_count <= 1,
    }
