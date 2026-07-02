"""paired trajectory auditing（観測版）の observability セクション生成（#15, advisory）。

SkillAudit (arXiv 2606.14239) の「同一タスクをスキル有/無で実行し挙動軌跡を対照する」を、
能動再実行せず既存テレメトリ（usage + sessions）から準実験的に観測した結果を evolve/audit
レポートに surface する。判定は決定論・LLM 非依存で advisory のみ（evolve の意思決定は変えない）。

相補する既存シグナルとの位置づけ（重複しない）:
  - compute_component_transfer（usage.py） = スキル追加の**時系列前後**デルタ（時間軸の準実験）。
  - outcome_attribution = スキル単位の絶対アウトカム（with/without の対照は取らない）。
  - multiview_eval = 既存シグナルの join による多視点ラベル。
  - chaos = 構造（coherence）の仮想除去。
  → 本 section だけが「同一 task-type 内での skill **有/無**の挙動対照」を提供する（SkillAudit の核）。

集約は usage.compute_paired_trajectory（純関数）に委譲し、本 builder は telemetry の軽量取得と
描画に専念する（重い chaos / coherence の再計算はしない。sections_multiview と同じ境界）。

スコープ（sections_outcome / sections_multiview と同じ当PJ化）: usage/sessions ストアは
全PJ共通だが、project_dir を worktree 安全 slug に正規化して当PJスコープに直す。

observability contract から参照される `build_*_section` 契約
（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

# usage/sessions の取得窓（sections_multiview / sections_outcome の days=30 と揃える）。
_LOOKBACK_DAYS = 30


def _gather_inputs(project_dir: Path) -> Optional[Dict[str, Any]]:
    """usage / sessions を軽量取得する。telemetry が解決できない / usage が空なら None。"""
    try:
        from . import outcome_metrics
        from .usage import compute_paired_trajectory, load_usage_data  # noqa: F401
        from telemetry_query import query_sessions
    except ImportError:
        return None

    try:
        usage = load_usage_data(days=_LOOKBACK_DAYS, project_root=project_dir)
    except Exception:
        return None
    if not usage:
        return None  # テレメトリ未蓄積 → 評価対象なし（沈黙）。

    # 当PJスコープ（#489）: project_dir を worktree 安全 slug に正規化して sessions を引く。
    try:
        proj_slug = outcome_metrics._normalize_pj(str(project_dir))
        sessions = query_sessions(project=proj_slug)
    except Exception:
        sessions = []

    return {"usage": usage, "sessions": sessions}


def build_paired_trajectory_section(project_dir: Path) -> Optional[List[str]]:
    """同一タスク種別での skill 有/無の挙動デルタを audit に advisory 表示する。

    観測可能性（silence != evaluated の境界）:
    - telemetry が未解決、または usage が空 → None（沈黙）
    - usage はあるが paired バケットが 1 つも組めない → 「評価したが対照対象なし」ℹ 行
    - paired デルタが算出できたら surface（regression が無くても「評価したが回帰なし ✓」を出す）
    """
    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        return _gather_inputs(proj)

    def render(inputs: Dict[str, Any]) -> List[str]:
        from .usage import compute_paired_trajectory

        paired = compute_paired_trajectory(
            usage=inputs["usage"], sessions=inputs["sessions"]
        )

        if not paired:
            return [
                "ℹ 評価したが paired 対照対象なし"
                "（同一 task-type で skill 有/無の両群が揃うセッションが不足）。",
            ]

        regressions = [r for r in paired if r.get("regression")]
        if not regressions:
            return [
                f"✓ 評価したが挙動回帰なし（{len(paired)} スキルを paired 対照、"
                "skill 有のほうが一発成功率を下げた事例は検出されず）。",
            ]

        lines = [
            "⚠ 同一タスクで skill 有のほうが一発成功率が低い（挙動を悪化させた疑い）。"
            "`/evolve-anything:evolve-skill` で該当スキルの見直しを検討:",
        ]
        for r in regressions:
            lines.append(
                f"- **{r['skill']}** (Δ{r['behavior_delta']:+.0%}): "
                f"有={r['with_success']:.0%} (n={r['n_with']}) / "
                f"無={r['without_success']:.0%} (n={r['n_without']}) "
                f"— {r['paired_task_types']} task-type で対照"
            )
        return lines

    return build_advisory_section(
        project_dir,
        title="Paired Trajectory (同一タスク種別での skill 有/無の挙動対照・advisory — スコア重みには未反映)",
        blurb=[
            "スキルを使ったセッションと使わなかったセッションで、一発成功率"
            "（修正なしで完了できた割合）に差が出ているかを既存ログから比べます。"
            "新たに実行し直すのではなく、すでに記録済みのテレメトリだけで観測します"
            "（参考値・スコアには反映しません）。LLM を使わず決定論で算出。",
        ],
        compute=compute,
        applicable=lambda inputs: True,
        render=render,
    )
