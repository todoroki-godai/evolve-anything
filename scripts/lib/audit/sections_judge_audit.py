"""judge false-pass 欠陥注入監査の observability セクション生成（#188, advisory）。

The Blind Curator（arXiv 2607.07436）: 自己進化のスキル退役（outcome_attribution の
negative_transfer rollback）は LLM judge が失敗を正しく見抜くことに依存する。judge が
false-pass（失敗を合格と誤判定）を出すと退役は無音で無効化され、どの集計指標にも表れない
サイレント故障になる。本 section は欠陥注入ハーネス（``judge_audit.harness``、opt-in CLI）が
記録した verdicts から false-pass 率を集計し、危険域なら ⚠ を出す。fitness の重み軸には
しない（verbosity / subagent_traces と同じ advisory レーン）。

観測可能性契約（build_verbosity_section と同契約）:
- judge_audit モジュール未解決 → None（沈黙）
- 判定 0 件（ハーネス未実行）→ None（沈黙。まだ計測していないだけだが、他の advisory
  section と同じ「評価対象なし」慣習に揃える。opt-in CLI の結果が無ければ何も主張しない）
- 判定ありで floor 未満（min_judged 未満）→ insufficient_data を明示
  （silence != evaluated: 判定はあるのに沈黙して「評価した」と誤読させない）
- floor 以上 → false-pass 率 + ⚠/✓ 判定
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


def build_judge_audit_section(project_dir: Path) -> Optional[List[str]]:
    """judge false-pass 率を audit に advisory 表示する（決定論・LLM 非依存）。

    - judge_audit モジュール未解決 → None（沈黙）
    - 判定 0 件 → None（沈黙。ハーネス未実行）
    - 判定あり → false-pass 率 + ⚠/✓。floor 未満ならデータ不足 + 実行誘導を明示。
    """

    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        try:
            from judge_audit import query as _q
        except ImportError:
            return None
        slug = _slug_for(proj)
        if not slug:
            return None
        summary = _q.false_pass_summary(slug)
        if summary["judged"] == 0:
            # ハーネス未実行（評価対象が無いのでなく「まだ計測していない」）は沈黙する。
            return None
        return summary

    def render(summary: Dict[str, Any]) -> List[str]:
        from judge_audit import query as _q  # FALSE_PASS_WARN_THRESHOLD 参照用

        body: List[str] = [
            f"  ・欠陥注入 fixture 判定済み: {summary['judged']}/{summary['total_fixtures']} 件",
        ]

        if summary["false_pass_rate"] is None:
            # 判定はあるが floor 未満 → 沈黙でなくデータ不足 + 実行誘導を明示。
            # effective_min_judged は fixture 総数でキャップ済みの実効 floor（#188 レビュー修正:
            # 固定 DEFAULT_MIN_JUDGED を出すと fixture 総数 < floor のとき全件判定後も
            # 「不足」表示のまま抜けられない矛盾になるため、実際に適用された floor を表示する）。
            body.append(
                f"  ・false-pass 率: データ不足 — 判定済みが最小サンプル数"
                f"（{summary['effective_min_judged']} 件）に満たないため率は非表示。"
            )
            # この分岐に来るのは judged < effective_min_judged <= total_fixtures のときのみ
            # （effective_min_judged は total_fixtures でキャップされるため）＝ pending は必ず
            # 正なので「追加実行できます」は常に真（全件判定済みで rate=None のまま留まることはない）。
            body.append(
                "      → `python3 scripts/lib/judge_audit/harness.py --run` で追加実行できます"
                "（dry-run でコスト確認可）。"
            )
            return body

        rate = summary["false_pass_rate"]
        mark = "⚠" if rate > _q.FALSE_PASS_WARN_THRESHOLD else "✓"
        body.append(
            f"  ・false-pass 率: {mark} {rate * 100:.0f}%"
            f"（{summary['false_pass']}/{summary['judged']} 件）"
            "— judge が既知の欠陥 fixture を合格と誤判定した割合。"
        )
        if rate > _q.FALSE_PASS_WARN_THRESHOLD:
            body.append(
                "      → 高い false-pass 率はスキル退役（outcome_attribution の "
                "negative_transfer rollback）を無音で無効化するリスクがあります"
                "（The Blind Curator arXiv 2607.07436）。"
            )
        if summary["pending"] > 0:
            body.append(f"  ・未判定 {summary['pending']} 件は harness.py --run で追加判定できます。")

        return body

    return build_advisory_section(
        project_dir,
        title="Judge False-Pass Audit (当PJ・advisory — The Blind Curator, #188)",
        blurb=[
            "既知の欠陥タスク（正解=失敗と分かっている fixture）を judge の実プロンプトに流し、"
            "合格(false-pass)と誤判定する割合を計測します（fault-injection 監査）。judge の"
            "偽陽性はスキル退役をサイレント無効化するため、事前に計測します。",
        ],
        compute=compute,
        applicable=lambda _summary: True,
        render=render,
    )
