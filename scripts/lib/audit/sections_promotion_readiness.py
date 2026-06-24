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
            f"  {_mark(True)} 条件1 分散が十分（PJ間で値がばらつくか）: "
            f"判定できた PJ {v.get('pj_count', 0)} / 相異なる値 {v.get('distinct_values', 0)} 種"
        )
    reason = v.get("reason", "")
    if reason == "insufficient_pj":
        return (
            f"  {_mark(False)} 条件1 分散が十分（PJ間で値がばらつくか）: "
            f"判定できる PJ {v.get('pj_count', 0)}（2 以上必要・必要件数を満たす軸値のみ計上）"
        )
    if reason == "all_identical":
        return (
            f"  {_mark(False)} 条件1 分散が十分（PJ間で値がばらつくか）: "
            f"判定できた全 {v.get('pj_count', 0)} PJ が同値 {v.get('value')} = 測定バグの強い兆候（#445）"
        )
    return f"  {_mark(False)} 条件1 分散が十分（PJ間で値がばらつくか）: 判定不能（{reason}）"


def _denominator_line(d: Dict[str, Any]) -> List[str]:
    floor = d.get("floor", "?")
    meeting = d.get("meeting", [])
    # #25/#50: 条件1 の「判定できた PJ 数（分散）」と区別できるよう、こちらは「必要件数
    # ≥floor に達した PJ 数」と母数の意味を平易に明示する（同一表現による矛盾の見え方を解消）。
    head = (
        f"  {_mark(d.get('pass', False))} 条件2 データ件数下限（各PJに必要なデータ件数があるか）: "
        f"必要件数 {floor} 件以上の PJ {len(meeting)}（2 以上必要）"
    )
    lines = [head]
    denoms = d.get("denominators", {})
    if denoms:
        sample = ", ".join(f"{Path(pj).name or pj}={n}" for pj, n in list(denoms.items())[:5])
        lines.append(f"      各PJの件数: {sample}")
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


def _predictive_validity_line(pv: Dict[str, Any]) -> List[str]:
    """条件4 予測妥当性（in/out-of-sample 順位相関 #42）を ✓/✗ + 内容で1〜2行に出す。

    閾値・件数下限は ``predictive_validity`` モジュールの定数を単一ソースとして引く
    （ここでハードコード重複を作らない）。データ不足は「データ不足」と明示し、相関値を
    捏造しない（沈黙 != 評価不能 の慣例）。
    """
    from . import predictive_validity as pvm

    if pv.get("pass"):
        return [
            f"  {_mark(True)} 条件4 予測妥当性（順位相関）: "
            f"in/out-of-sample で skill 順位が一致"
            f"（rho={pv.get('rho')}, 対象 {pv.get('n_skills', 0)} skill）"
        ]
    if pv.get("reason") == "insufficient_data":
        return [
            f"  {_mark(False)} 条件4 予測妥当性（順位相関）: "
            f"データ不足（両 sample に {pvm.MIN_RANKED_SKILLS} skill 以上必要・"
            f"現在 {pv.get('n_skills', 0)}）"
        ]
    return [
        f"  {_mark(False)} 条件4 予測妥当性（順位相関）: "
        f"順位が分布外で崩れる"
        f"（rho={pv.get('rho')} < {pvm.PREDICTIVE_VALIDITY_RHO_FLOOR}・誤昇格リスク）"
    ]


def _compressed_gap_line(result: Dict[str, Any]) -> str:
    """#49 + #51 調停: 全✗ ケースを 1 行に圧縮しつつ per-条件の具体ギャップ数値 +
    必要アクションを載せる（「時期尚早」で終わらせない）。

    閾値（必要 PJ 数 / correction・sessions の floor）は ``outcome_promotion_readiness`` の
    定数を単一ソースとして引く（ここでハードコード重複を作らない）。
    """
    from . import outcome_promotion_readiness as opr

    from . import predictive_validity as pvm

    min_pj = opr._MIN_PJ
    variance = result["variance"]
    denominator = result["denominator"]
    direction = result["direction"]
    predictive = result.get("predictive_validity", {})

    # 条件1: 分散を判定できる PJ 数 / 条件2: 分母 floor を満たす PJ 数 / 条件3: apply 件数。
    var_now = variance.get("pj_count", 0)
    denom_now = len(denominator.get("meeting", []))
    apply_now = direction.get("anchors", 0)
    # 条件4: 予測妥当性。データ不足なら ranked skill 数、低rho なら rho を1語で添える。
    if predictive.get("reason") == "insufficient_data":
        pv_word = f"予測妥当性 skill {predictive.get('n_skills', 0)}/{pvm.MIN_RANKED_SKILLS}"
    elif predictive.get("pass"):
        pv_word = f"予測妥当性 rho={predictive.get('rho')}✓"
    else:
        pv_word = f"予測妥当性 rho={predictive.get('rho')}<{pvm.PREDICTIVE_VALIDITY_RHO_FLOOR}"

    return (
        f"ℹ Outcome Weight Promotion: まだ条件不足"
        f"（値がばらつくPJ {var_now}/{min_pj}・データが{opr.CORRECTION_FLOOR}件以上あるPJ "
        f"{denom_now}/{min_pj}・適用記録 apply {apply_now}件・{pv_word}）— 次回 audit で再測定。"
        f"貯めかた: corrections を{opr.CORRECTION_FLOOR}件/PJ・sessions を{opr.SESSION_FLOOR}件/PJ 以上"
        f"{min_pj} PJ で揃え、`evolve --drain` で適用（accept）を記録すると条件が埋まります"
        f"（スコアの重みには未反映・参考値として並走、ADR-046）。"
    )


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

    # slug 健全性（#24）は 3条件評価とは独立した健全性チェックなので、圧縮可否の判定より
    # 先に評価する。混入があれば（✗）重要警告なので圧縮せず全展開に倒す。
    slug_lines = _slug_hygiene_lines(opr)
    slug_has_issue = any("✗" in ln for ln in slug_lines)

    # #49 / #51 の調停: 3条件すべて ✗ かつ slug 健全性に問題なしの場合だけ、冗長な全展開を
    # せず 1行に圧縮する（#49 = 継続観察中で判断材料が無い状態の繰り返し全展開を避ける）。
    # ただし「時期尚早」で終わらせず、per-条件の具体ギャップ数値 + 必要アクションを同じ1行に
    # 載せる（#51 = 何があと幾つ足りないか・どうすれば貯まるかを示す）。✓ が1つでもある /
    # slug 混入あり なら従来の全展開（重要情報を埋もれさせない）。
    v_pass = bool(result["variance"].get("pass"))
    d_pass = bool(result["denominator"].get("pass"))
    dir_pass = bool(result["direction"].get("pass"))
    pv_pass = bool(result.get("predictive_validity", {}).get("pass"))
    if not (v_pass or d_pass or dir_pass or pv_pass) and not slug_has_issue:
        return [
            "## Outcome Weight Promotion Readiness (advisory — ADR-046)",
            "",
            _compressed_gap_line(result),
            *slug_lines,
            "",
        ]

    header = [
        "## Outcome Weight Promotion Readiness (advisory — ADR-046)",
        "",
        "outcome 3軸を environment fitness の重みへ繰り入れてよいかの決定論判定。"
        "4条件すべて ✓ で初めて「重み昇格を提案」する（重みには未反映）。決定論・LLM 非依存。",
        "",
    ]
    body: List[str] = [_variance_line(result["variance"])]
    body.extend(_denominator_line(result["denominator"]))
    body.extend(_direction_line(result["direction"]))
    body.extend(_predictive_validity_line(result.get("predictive_validity", {})))
    body.extend(slug_lines)

    if result.get("promote"):
        body.append("")
        body.append(
            "  → 4条件すべて充足。outcome 3軸を environment fitness の重みへ繰り入れる"
            "**重み昇格を提案**（coherence/constitutional をゲート降格する将来案を検討、ADR-046）"
        )
    else:
        body.append("")
        body.append(
            "  → 未充足の条件があるため重み昇格は時期尚早（advisory 並走を継続、ADR-046）"
        )
        # #52-5: 何が貯まれば昇格判断できるかの蓄積条件を1行添える。
        body.append(
            "      蓄積条件: 4条件すべて ✓（PJ 2 以上で値がばらつく・各PJに必要件数あり・"
            "適用前後で期待方向に改善・in/out-of-sample で skill 順位が一致）が揃うと"
            "「重み昇格を提案」に切り替わります。"
        )
    return header + body + [""]
