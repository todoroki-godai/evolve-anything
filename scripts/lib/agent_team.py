"""Agent Team — エージェント編成ギャップの決定論検出（Issue #326）。

`agent_quality.py` が各エージェント *単体* の品質（frontmatter / トリガー / 行数）を
見るのに対し、本モジュールはエージェント *間* の関係を見て「チーム編成の改善余地」を
検出する。検出は 2 軸:

- **役割重複（role overlap）**: description の役割語が Jaccard で近いペア。
  どのエージェントを呼ぶべきか曖昧になり、ルーティング事故の温床になる。
- **孤立（isolated）**: 他のどのエージェント定義本文からも名前で参照されない
  エージェント。ルーター/オーケストレーターの編成から外れている兆候。

「ドメイン→チーム自動生成」（Issue #326 のフル機能）の前段として、まず編成の
改善余地を *毎回* 可視化する。observability builder（`sections_agent.py`）経由で
audit に載り、evolve は audit を消費するため evolve のたびに surface される
（手動 CLI 止まりにしない＝version != enforcement 回避）。

LLM 非依存・決定論。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence, Set

from similarity import jaccard_coefficient

# 役割語 Jaccard がこれ以上なら役割重複とみなす。
# description 全体ではなく Examples を除いた役割記述部のみを語集合化するため、
# 0.5 でも「本当に役割が重なるペア」を拾える（定型句ノイズは _role_tokens で除去）。
ROLE_OVERLAP_THRESHOLD = 0.5

# 役割語から落とす汎用語（英日）。これらは全エージェントの description に頻出し、
# 残すと Jaccard を底上げして無関係なペアを誤検出する。
_STOPWORDS = {
    # 英語の機能語・スキル定義の定型語
    "use", "this", "the", "agent", "when", "to", "a", "an", "for", "of", "and",
    "or", "is", "are", "on", "in", "with", "your", "you", "that", "it", "as",
    "by", "be", "user", "assistant", "context", "task", "should", "used",
    "needs", "help", "wants", "asks", "via", "tool", "launch", "expert",
    # 日本語の汎用語
    "する", "して", "した", "こと", "ため", "もの", "など", "場合", "とき",
    "エージェント", "ユーザー", "使う", "使用", "対応",
}

_TOKEN_RE = re.compile(r"[a-z0-9ぁ-んァ-ヶ一-龠]+", re.IGNORECASE)
_EXAMPLES_RE = re.compile(r"examples?\s*[:：]", re.IGNORECASE)


@dataclass
class RoleOverlap:
    """役割が重複するエージェントペア。"""

    agent_a: str
    agent_b: str
    similarity: float


@dataclass
class AgentTeamResult:
    """編成ギャップ分析の結果。"""

    total_agents: int
    role_overlaps: List[RoleOverlap] = field(default_factory=list)
    isolated: List[str] = field(default_factory=list)

    @property
    def has_gap(self) -> bool:
        return bool(self.role_overlaps or self.isolated)


def _role_tokens(agent) -> Set[str]:
    """エージェントの description から役割語の集合を作る。

    Examples ブロック以降は定型句ノイズなので切り落とし、ストップワードを除いた
    2 文字以上のトークンだけを残す。
    """
    desc = ""
    if getattr(agent, "frontmatter", None):
        desc = agent.frontmatter.get("description", "") or ""
    head = _EXAMPLES_RE.split(desc, maxsplit=1)[0]
    toks = _TOKEN_RE.findall(head.lower())
    return {t for t in toks if len(t) > 1 and t not in _STOPWORDS}


def detect_role_overlaps(
    agents: Sequence, threshold: float = ROLE_OVERLAP_THRESHOLD
) -> List[RoleOverlap]:
    """全ペアの役割語 Jaccard を測り、threshold 以上のペアを返す。"""
    overlaps: List[RoleOverlap] = []
    items = [(a, _role_tokens(a)) for a in agents]
    for i in range(len(items)):
        agent_a, tokens_a = items[i]
        if not tokens_a:
            continue
        for j in range(i + 1, len(items)):
            agent_b, tokens_b = items[j]
            if not tokens_b:
                continue
            sim = jaccard_coefficient(tokens_a, tokens_b)
            if sim >= threshold:
                overlaps.append(
                    RoleOverlap(
                        agent_a=agent_a.name,
                        agent_b=agent_b.name,
                        similarity=sim,
                    )
                )
    overlaps.sort(key=lambda o: o.similarity, reverse=True)
    return overlaps


def detect_isolated(agents: Sequence) -> List[str]:
    """編成から宙に浮いたエージェント名を返す（入次数 0 かつ出次数 0）。

    エージェント名はハイフン区切りで具体的（例: senior-engineer, reviewer-san）な
    ため、他エージェントの content への部分文字列出現を「参照」とみなす決定論判定で
    十分な精度が出る。

    被参照ゼロだけを孤立とするとルーター/オーケストレーター（自分は他を呼ぶが他からは
    呼ばれない設計上の入口）まで誤検出する。そこで **入次数 0（誰からも参照されない）
    かつ 出次数 0（自分も誰も参照しない）** のエージェントだけを「孤立」とする。
    これにより、入口役（出次数>0）と被参照の専門家（入次数>0）を除外し、どの編成にも
    繋がっていない宙ぶらりんの定義だけを拾う。エージェントが 2 個未満なら参照関係は
    意味を持たないので空。
    """
    if len(agents) < 2:
        return []
    names = {a.name for a in agents}
    isolated: List[str] = []
    for target in agents:
        target_content = getattr(target, "content", "") or ""
        in_degree = any(
            other.name != target.name
            and target.name in (getattr(other, "content", "") or "")
            for other in agents
        )
        out_degree = any(
            name != target.name and name in target_content for name in names
        )
        if not in_degree and not out_degree:
            isolated.append(target.name)
    return isolated


def analyze_agent_team(
    agents: Sequence, *, overlap_threshold: float = ROLE_OVERLAP_THRESHOLD
) -> AgentTeamResult:
    """エージェント群の編成ギャップ（役割重複・孤立）を分析する。"""
    return AgentTeamResult(
        total_agents=len(agents),
        role_overlaps=detect_role_overlaps(agents, threshold=overlap_threshold),
        isolated=detect_isolated(agents),
    )
