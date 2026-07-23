"""subagent 内部軌跡の observability セクション生成（#38, advisory / #200 委任プロンプト）。

親セッションの error_count しか見ない既存 outcome 帰属の盲点 — subagent が内部で error
連発しても最終成功すれば「一発成功」と誤記録される — を、subagent transcript の
tool_use / tool_result / is_error 列から per-agent_type で advisory 表示する。
fitness の重み軸にはしない（outcome_metrics / fanout_cost と同じ advisory レーン）。

観測可能性契約（build_fanout_cost_section と同契約）:
- subagent_traces モジュール未解決 → None（沈黙）
- 当 PJ の軌跡レコードが 0 件（評価対象なし）→ None（沈黙）
- 1 件以上 → ヘッダ + agent_type 別行（floor 未満は zero_line でデータ不足明示）
  silence != evaluated: 評価対象があるのに floor 未満なら沈黙でなく不足を明示する。

#200: 全 agent_type（⚠ の有無に関わらず）の集計行の下に、直近1件の委任プロンプト
（何を頼んだか）先頭150字を「└ 直近の委任: "..."」として添える。事後監査で
「何を委任したか」まで audit から辿れるようにする（record に delegation_prompt が
無い/空の既存レコードは省略・後方互換）。

#219: 実測 effort（CC v2.1.212 以降 transcript のトップレベル "effort" フィールド。
extractor.effort_counts / query.dominant_effort が SoT）を agent_type ごとに表示し、
agent frontmatter の ``tier:`` 宣言から model-tiers.json（tier_policy.py）で期待 effort を
引いて乖離があれば ⚠ を追加する。tier 未宣言・一致する agent 定義が無い・未計測のいずれか
なら drift 判定はスキップ（判定不能を drift と誤認しない）。新ストアは作らず read-time 導出のみ。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_quality import scan_agents

import tier_policy

from .advisory import build_advisory_section

# audit 表示側の委任プロンプト抜粋の truncate 上限（extractor の 300 字とは別の上限。
# audit 本文を長くしすぎないための表示制約）。
DELEGATION_EXCERPT_MAX_CHARS = 150


def _delegation_excerpt_for_agent_type(traces: Dict[str, dict], agent_type: str) -> str:
    """指定 agent_type の record 1 件から委任プロンプト先頭150字の抜粋を選ぶ。

    ``traces`` は agent_id → record の dict。同一 agent_type の record が複数あれば
    timestamp を持つものの中で最大（＝直近）を優先、無ければ最初に見つかったものでよい。
    delegation_prompt が空/欠如の record しか無ければ空文字を返す（呼び出し側で省略）。
    """
    candidates = [r for r in traces.values() if r.get("agent_type") == agent_type]
    if not candidates:
        return ""
    with_ts = [r for r in candidates if r.get("timestamp")]
    chosen = max(with_ts, key=lambda r: r["timestamp"]) if with_ts else candidates[0]
    prompt = chosen.get("delegation_prompt") or ""
    if not prompt:
        return ""
    if len(prompt) > DELEGATION_EXCERPT_MAX_CHARS:
        return prompt[:DELEGATION_EXCERPT_MAX_CHARS] + "…"
    return prompt

# #76 Finding A: floor を満たす agent_type のうち、内部品質が悪い種別に ⚠ を付けて
# report.py の畳み込み（⚠/🔴 だけ full-text 展開）に乗せ、『✓ 評価済みクリーン』への
# 埋没を防ぐ。閾値は実 PJ dogfood（v1.111.0）で較正:
#   出すべき = 0.17(figma general-purpose) / 0.33(sys-bots general-purpose, tool error 8.33)
#   出さない = senpai 1.0 / senior-engineer 0.90 / Plan 1.0 / Explore 0.50（境界・strict <）
LOW_FIRST_TRY_SUCCESS = 0.5  # これ未満（strict）の内部一発成功率は ⚠。
HIGH_AVG_TOOL_ERROR = 5.0    # これ以上の平均 tool error は ⚠（rate 良好でも独立に発火）。


def _expected_effort_for_agent_type(agents: List[Any], agent_type: str) -> Optional[str]:
    """agent_type に一致する agent 定義の ``tier:`` 宣言から期待 effort を引く（#219）。

    - 一致する agent 定義が無い → None（判定不能。drift 非表示）。
    - frontmatter に ``tier:`` が無い / 未知の tier 値 → None（同上）。
    - tier_policy（model-tiers.json、無ければ DEFAULT_TIER_POLICY）の該当 tier の
      ``effort`` をそのまま返す（MECH 等 effort 非対応 tier は None を返しうる）。
    """
    for agent in agents:
        fm = getattr(agent, "frontmatter", None) or {}
        name = fm.get("name") or getattr(agent, "name", None)
        if str(name) != str(agent_type):
            continue
        tier_raw = fm.get("tier")
        if not tier_raw:
            return None
        tier_key = str(tier_raw).strip().upper()
        policy_map = tier_policy.load_tier_policy(strict=False)
        policy = policy_map.get(tier_key)
        if not policy:
            return None
        return policy.get("effort")
    return None


def _slug_for(project_dir: Path) -> Optional[str]:
    """project_dir を worktree 安全 slug に正規化する（本体/worktree どちらでも同一 slug）。"""
    try:
        from pj_slug import pj_slug_fast
        return pj_slug_fast(str(project_dir))
    except ImportError:  # pragma: no cover
        return Path(project_dir).name or None


def build_subagent_traces_section(project_dir: Path) -> Optional[List[str]]:
    """subagent 内部軌跡を audit に advisory 表示する（決定論・LLM 非依存）。

    - subagent_traces モジュール未解決 → None（沈黙）
    - 当 PJ の軌跡が 0 件（評価対象なし）→ None（沈黙）
      （outcome_metrics / fanout_cost と同じ「評価対象が無ければ沈黙」境界）
    - 1 件以上 → agent_type 別の内部一発成功率 + 平均 tool error。floor 未満の agent_type
      しか無ければデータ不足を明示する。
    """
    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        try:
            from subagent_traces import query as _q  # noqa: F401
            from subagent_traces import store as _store
        except ImportError:
            return None
        slug = _slug_for(proj)
        if not slug:
            return None
        traces = _store.read_traces(slug)
        if not traces:
            # 評価対象（当 PJ の軌跡）が 1 件も無い環境は沈黙する。
            return None
        try:
            agents = scan_agents(project_root=proj)
        except Exception:
            agents = []
        return {"traces": traces, "summaries": _q.per_agent_type_summary(slug), "agents": agents}

    def render(data: Dict[str, Any]) -> List[str]:
        from subagent_traces import query as _q  # DEFAULT_MIN_TRACES 参照用

        traces = data["traces"]
        summaries = data["summaries"]
        agents = data.get("agents") or []

        body: List[str] = []
        if not summaries:
            # 軌跡はあるが各 agent_type が floor 未満 → 沈黙でなくデータ不足を明示。
            body.append(
                f"  ・agent 種別別の内部一発成功率: データ不足 — 集計対象 {len(traces)} 件はあるが、"
                f"各 agent 種別が最小サンプル数（{_q.DEFAULT_MIN_TRACES} 件）に満たないため率は非表示。"
            )
            body.append(
                f"      蓄積条件: 同一 agent 種別の subagent 軌跡が {_q.DEFAULT_MIN_TRACES} 件以上"
                "貯まると種別ごとに算出されます。"
            )
            return body

        flagged = False
        for s in summaries:
            rate = s["first_try_success_rate"]
            ate = s["avg_tool_error"]
            low_rate = rate < LOW_FIRST_TRY_SUCCESS
            high_err = ate >= HIGH_AVG_TOOL_ERROR
            if low_rate or high_err:
                flagged = True
                reasons: List[str] = []
                if low_rate:
                    reasons.append(f"内部リトライ多（一発成功率 < {LOW_FIRST_TRY_SUCCESS:.2f}）")
                if high_err:
                    reasons.append(f"tool error 過多（平均 ≥ {HIGH_AVG_TOOL_ERROR:.1f}）")
                body.append(
                    f"  ・⚠ {s['agent_type']}: 内部一発成功率 {rate:.2f}"
                    f"（{s['n']} 件）・平均 tool error {ate:.2f} — {' / '.join(reasons)}"
                )
            else:
                body.append(
                    f"  ・{s['agent_type']}: 内部一発成功率 {rate:.2f}"
                    f"（{s['n']} 件）— 高いほど内部リトライ少。平均 tool error {ate:.2f}"
                )
            # #200: ⚠ の有無に関わらず、集計行の下に直近1件の委任プロンプト抜粋を添える。
            excerpt = _delegation_excerpt_for_agent_type(traces, s["agent_type"])
            if excerpt:
                body.append(f'      └ 直近の委任: "{excerpt}"')

            # #219: 実測 effort 分布（未計測=effort_counts 空なら省略）。
            dominant_effort = s.get("dominant_effort")
            effort_counts = s.get("effort_counts") or {}
            if dominant_effort:
                dist = " / ".join(f"{k}:{v}" for k, v in effort_counts.items())
                body.append(f"      └ 実測 effort: {dominant_effort}（{dist}）")
                expected_effort = _expected_effort_for_agent_type(agents, s["agent_type"])
                if expected_effort and expected_effort != dominant_effort:
                    body.append(
                        f"      └ ⚠ effort drift: tier宣言は effort={expected_effort} を"
                        f"期待するが実測は {dominant_effort}（model-tiers.json 乖離）"
                    )
        if flagged:
            body.append("")
            body.append(
                "  → ⚠ の agent 種別は親セッションでは『一発成功』に見えても内部で失敗を"
                "繰り返しています（#38）。当該 agent 定義のツール手順・前提・権限を見直してください。"
            )
        return body

    return build_advisory_section(
        project_dir,
        title="Subagent Internal Traces (当PJ・advisory — スコア重みには未反映)",
        blurb=[
            "subagent が内部で何回エラーしてからやり直したかを transcript から測ります（#38）。"
            "親セッションの error_count だけ見ると、subagent が内部で何度も失敗して最終的に"
            "成功した場合に『一発成功』と誤記録されます。その盲点を agent 種別ごとに可視化します。"
            "LLM を使わず決定論で算出。",
        ],
        compute=compute,
        applicable=lambda _data: True,
        render=render,
    )
