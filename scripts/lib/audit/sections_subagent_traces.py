"""subagent 内部軌跡の observability セクション生成（#38, advisory）。

親セッションの error_count しか見ない既存 outcome 帰属の盲点 — subagent が内部で error
連発しても最終成功すれば「一発成功」と誤記録される — を、subagent transcript の
tool_use / tool_result / is_error 列から per-agent_type で advisory 表示する。
fitness の重み軸にはしない（outcome_metrics / fanout_cost と同じ advisory レーン）。

観測可能性契約（build_fanout_cost_section と同契約）:
- subagent_traces モジュール未解決 → None（沈黙）
- 当 PJ の軌跡レコードが 0 件（評価対象なし）→ None（沈黙）
- 1 件以上 → ヘッダ + agent_type 別行（floor 未満は zero_line でデータ不足明示）
  silence != evaluated: 評価対象があるのに floor 未満なら沈黙でなく不足を明示する。
"""
from pathlib import Path
from typing import List, Optional

# #76 Finding A: floor を満たす agent_type のうち、内部品質が悪い種別に ⚠ を付けて
# report.py の畳み込み（⚠/🔴 だけ full-text 展開）に乗せ、『✓ 評価済みクリーン』への
# 埋没を防ぐ。閾値は実 PJ dogfood（v1.111.0）で較正:
#   出すべき = 0.17(figma general-purpose) / 0.33(sys-bots general-purpose, tool error 8.33)
#   出さない = senpai 1.0 / senior-engineer 0.90 / Plan 1.0 / Explore 0.50（境界・strict <）
LOW_FIRST_TRY_SUCCESS = 0.5  # これ未満（strict）の内部一発成功率は ⚠。
HIGH_AVG_TOOL_ERROR = 5.0    # これ以上の平均 tool error は ⚠（rate 良好でも独立に発火）。


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
    try:
        from subagent_traces import query as _q
        from subagent_traces import store as _store
    except ImportError:
        return None

    slug = _slug_for(Path(project_dir))
    if not slug:
        return None

    traces = _store.read_traces(slug)
    if not traces:
        # 評価対象（当 PJ の軌跡）が 1 件も無い環境は沈黙する。
        return None

    summaries = _q.per_agent_type_summary(slug)

    header = [
        "## Subagent Internal Traces (当PJ・advisory — スコア重みには未反映)",
        "",
        "subagent が内部で何回エラーしてからやり直したかを transcript から測ります（#38）。"
        "親セッションの error_count だけ見ると、subagent が内部で何度も失敗して最終的に"
        "成功した場合に『一発成功』と誤記録されます。その盲点を agent 種別ごとに可視化します。"
        "LLM を使わず決定論で算出。",
        "",
    ]

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
        return header + body + [""]

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
    if flagged:
        body.append("")
        body.append(
            "  → ⚠ の agent 種別は親セッションでは『一発成功』に見えても内部で失敗を"
            "繰り返しています（#38）。当該 agent 定義のツール手順・前提・権限を見直してください。"
        )
    return header + body + [""]
