"""ADR-046 重み昇格レディネスの observability セクション生成（#461, advisory）。

outcome 3軸を environment fitness の重みへ繰り入れてよいかを、ADR-046 の3条件
（分散十分 / データ件数下限 / 方向の妥当性）で決定論判定し ✓/✗ + evidence で surface する。
3条件すべて ✓ なら「重み昇格を提案」行を出す（markdown / 構造化 両経路、ADR-028 契約準拠）。

検査対象は環境グローバルなストア（DATA_DIR の corrections.jsonl / sessions.jsonl /
optimize_history）であり project_dir には依存しないため、observability contract 互換で
引数は受け取るだけ（outcome_metrics / measurement_bug と同型）。スコア重みには入れない。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


def _mark(passed: bool) -> str:
    return "✓" if passed else "✗"


def _variance_line(v: Dict[str, Any]) -> str:
    # #25: 条件1 と条件2 がどちらも「PJ が N 件」という同一表現を使い、N の母数の意味
    # （分散を判定できる PJ 数 vs 分母 floor を満たす PJ 数）が違うのに見分けられず、
    # 「条件1 0 件 / 条件2 2 件」が一見矛盾して読めた。各ラベルに母数の意味を明示する。
    if v.get("pass"):
        return (
            f"  {_mark(True)} 条件1 分散が十分: "
            f"分散を判定できる PJ 数 {v.get('pj_count', 0)} / 相異なる値 {v.get('distinct_values', 0)} 種"
        )
    reason = v.get("reason", "")
    if reason == "insufficient_pj":
        return (
            f"  {_mark(False)} 条件1 分散が十分: "
            f"分散を判定できる PJ 数 {v.get('pj_count', 0)}（≥2 必要・件数下限を満たす軸値のみ計上）"
        )
    if reason == "all_identical":
        return (
            f"  {_mark(False)} 条件1 分散が十分: 分散を判定できる全 {v.get('pj_count', 0)} PJ が同値 "
            f"{v.get('value')} = 測定バグ強シグナル（#445）"
        )
    return f"  {_mark(False)} 条件1 分散が十分: 判定不能（{reason}）"


def _denominator_line(d: Dict[str, Any]) -> List[str]:
    floor = d.get("floor", "?")
    meeting = d.get("meeting", [])
    # #25: 条件1 の「分散を判定できる PJ 数」と区別できるよう、こちらは「分母 ≥floor を
    # 満たす PJ 数」と母数の意味を明示する（同一表現による矛盾の見え方を解消）。
    head = (
        f"  {_mark(d.get('pass', False))} 条件2 データ件数下限: "
        f"分母 ≥{floor} を満たす PJ 数 {len(meeting)}（≥2 必要）"
    )
    lines = [head]
    denoms = d.get("denominators", {})
    if denoms:
        sample = ", ".join(f"{Path(pj).name or pj}={n}" for pj, n in list(denoms.items())[:5])
        lines.append(f"      分母実測: {sample}")
    return lines


def _direction_line(dr: Dict[str, Any]) -> List[str]:
    passed = dr.get("pass", False)
    reason = dr.get("reason")
    if reason == "no_apply_events":
        return [
            f"  {_mark(False)} 条件3 方向の妥当性: apply イベント（reflect/evolve 適用）が"
            "未蓄積で判定不能"
        ]
    if reason == "no_paired_windows":
        return [
            f"  {_mark(False)} 条件3 方向の妥当性: apply イベント "
            f"{dr.get('anchors', 0)} 件あるが前後窓に十分な session が無い"
        ]
    head = (
        f"  {_mark(passed)} 条件3 方向の妥当性: apply 前後で期待方向へ動いた "
        f"{dr.get('expected_direction', 0)}/{dr.get('compared', 0)} "
        f"(窓幅 {dr.get('window_days', '?')}日)"
    )
    lines = [head]
    for ev in (dr.get("evidence") or [])[:3]:
        lines.append(
            f"      {Path(str(ev.get('pj', ''))).name} {ev.get('axis')}: "
            f"{ev.get('before')} → {ev.get('after')} "
            f"({'期待方向' if ev.get('improved') else '逆方向'})"
        )
    return lines


def _slug_hygiene_lines(opr) -> List[str]:
    """#24: optimize_history に worktree ディレクトリ名 slug が混入していないか健全性チェック
    の結果を1行 surface する。silence≠evaluated（この PJ の慣例）に従い、混入 0 件でも
    『✓ worktree名slug混入なし』を残し、検出時は該当 slug を警告行で出す。

    ``detect_worktree_name_slugs`` 未提供（旧 module 解決）でも例外を投げず沈黙しない —
    呼び出し側が import 済みの opr を渡すため、getattr で防御的に確認する。
    """
    detect = getattr(opr, "detect_worktree_name_slugs", None)
    if detect is None:
        return []
    suspects = detect()
    if not suspects:
        return [f"  {_mark(True)} slug 健全性: worktree 名 slug の混入なし（#24）"]
    sample = ", ".join(suspects[:5])
    extra = f" 他{len(suspects) - 5}件" if len(suspects) > 5 else ""
    return [
        f"  {_mark(False)} slug 健全性: optimize_history に worktree 名 slug が "
        f"{len(suspects)} 件混入（#24・本体 repo 名へ未正規化の汚染）",
        f"      該当: {sample}{extra}（pj_slug_backfill で回収 or 該当ファイルをマージ）",
    ]


def build_promotion_readiness_section(project_dir: Path) -> Optional[List[str]]:
    """ADR-046 重み昇格レディネスを 3 条件 ✓/✗ で surface する（決定論・LLM 非依存）。

    観測可能性:
    - モジュール未解決 → None（沈黙）
    - 3軸とも per-PJ データが 1 件も無い（評価対象が無い環境）→ None（沈黙）
      outcome_metrics / measurement_bug と同じ「評価対象が無ければ沈黙」の境界。
    - データがあれば 3 条件を ✓/✗ + evidence で出し、3 条件すべて ✓ なら昇格提案行を足す。
    """
    try:
        from . import outcome_promotion_readiness as opr
    except ImportError:
        return None

    result = opr.compute_promotion_readiness(days=30, window_days=opr.DEFAULT_WINDOW_DAYS)

    axes = result.get("axes", {})
    if not any(axes.get(k) for k in ("correction_recurrence", "first_try_success", "rework")):
        return None  # 評価対象（per-PJ データ）が無い環境は沈黙

    header = [
        "## Outcome Weight Promotion Readiness (advisory — ADR-046)",
        "",
        "outcome 3軸を environment fitness の重みへ繰り入れてよいかの決定論判定。"
        "3条件すべて ✓ で初めて「重み昇格を提案」する（重みには未反映）。決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = [_variance_line(result["variance"])]
    body.extend(_denominator_line(result["denominator"]))
    body.extend(_direction_line(result["direction"]))
    body.extend(_slug_hygiene_lines(opr))

    if result.get("promote"):
        body.append("")
        body.append(
            "  → 3条件すべて充足。outcome 3軸を environment fitness の重みへ繰り入れる"
            "**重み昇格を提案**（coherence/constitutional をゲート降格する将来案を検討、ADR-046）"
        )
    else:
        body.append("")
        body.append(
            "  → 未充足の条件があるため重み昇格は時期尚早（advisory 並走を継続、ADR-046）"
        )
    return header + body + [""]
