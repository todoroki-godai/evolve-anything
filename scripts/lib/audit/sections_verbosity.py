"""回答冗長性の observability セクション生成（#75, advisory）。

Stop hook（record_verbosity）が記録した長応答候補と、judge が Haiku で付けた判定から、
当 PJ の「無駄に冗長率」「多発する冗長パターン Top-N」を advisory 表示する。
fitness の重み軸にはしない（subagent_traces / fanout_cost と同じ advisory レーン）。

観測可能性契約（build_subagent_traces_section と同契約）:
- verbosity モジュール未解決 → None（沈黙）
- 当 PJ の候補が 0 件（評価対象なし）→ None（沈黙）
- 候補ありで未判定のみ（judged < floor）→ 「N 件未判定（judge --run で判定）」を明示
  （silence != evaluated: 候補はあるのに沈黙して「評価した」と誤読させない）
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section


def _slug_for(project_dir: Path) -> Optional[str]:
    """project_dir を worktree 安全 slug に正規化する（本体/worktree どちらでも同一 slug）。"""
    try:
        from pj_slug import pj_slug_fast
        return pj_slug_fast(str(project_dir))
    except ImportError:  # pragma: no cover
        return Path(project_dir).name or None


def build_verbosity_section(project_dir: Path) -> Optional[List[str]]:
    """回答冗長性を audit に advisory 表示する（決定論・LLM 非依存）。

    - verbosity モジュール未解決 → None（沈黙）
    - 当 PJ の候補が 0 件（評価対象なし）→ None（沈黙）
    - 候補あり → 冗長率 + パターン Top-N。未判定のみなら judge --run 誘導を明示。
    """
    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        try:
            from verbosity import query as _q
        except ImportError:
            return None
        slug = _slug_for(proj)
        if not slug:
            return None
        summary = _q.verbosity_summary(slug)
        if summary["candidates"] == 0:
            # 評価対象（当 PJ の長応答候補）が 1 件も無い環境は沈黙する。
            return None
        return summary

    def render(summary: Dict[str, Any]) -> List[str]:
        from verbosity import query as _q  # DEFAULT_MIN_JUDGED 参照用

        body: List[str] = [
            f"  ・候補（足切り超の長応答）: {summary['candidates']} 件 / 判定済み {summary['judged']} 件 / "
            f"未判定 {summary['pending']} 件",
        ]

        if summary["verbose_rate"] is None:
            # 候補はあるが判定済みが floor 未満 → 沈黙でなくデータ不足 + 判定誘導を明示。
            body.append(
                f"  ・無駄に冗長率: データ不足 — 判定済みが最小サンプル数（{_q.DEFAULT_MIN_JUDGED} 件）に"
                "満たないため率は非表示。"
            )
            if summary["pending"] > 0:
                body.append(
                    f"      → 未判定 {summary['pending']} 件あり。"
                    "`python3 scripts/lib/verbosity/judge.py --run` で Haiku 判定すると率が出ます"
                    "（dry-run でコスト確認可）。"
                )
            return body

        body.append(
            f"  ・無駄に冗長率: {summary['verbose_rate'] * 100:.0f}%"
            f"（{summary['verbose']}/{summary['judged']} 件）— 高いほど応答に水増しが多い。"
        )
        if summary["patterns"]:
            top = "、".join(
                f"{p['pattern']}({p['count']})" for p in summary["patterns"]
            )
            body.append(f"  ・多発する冗長パターン: {top}")
            body.append(
                "      → 多発パターンは judge --run が rules/concise.md 追記案を提示します"
                "（自動適用しない・人間承認）。"
            )
        if summary["pending"] > 0:
            body.append(f"  ・未判定 {summary['pending']} 件は judge --run で追加判定できます。")

        return body

    return build_advisory_section(
        project_dir,
        title="Answer Verbosity (当PJ・advisory — スコア重みには未反映)",
        blurb=[
            "AI 応答が「無駄に冗長か」を Haiku がまとめて判定した結果です（#75）。長さ自体は減点せず、"
            "水増し・繰り返し・前置き等の無駄を測ります。Stop hook がゼロ LLM で長応答を記録し、"
            "judge が後段 Haiku でバッチ判定します。",
        ],
        compute=compute,
        applicable=lambda _summary: True,
        render=render,
    )
