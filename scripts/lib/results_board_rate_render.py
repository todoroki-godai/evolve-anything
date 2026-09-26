"""Pure correction-rate rendering for the results board."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from correction_rate import FREEZE_DELAY_DAYS, GATE_CONSECUTIVE_WEEKS
from correction_semantic.prompt import CATEGORY_ENUM, CATEGORY_LABELS_JA

def _category_breakdown_lines(category_breakdown: Optional[Dict[str, Any]]) -> List[str]:
    """1週分のカテゴリ内訳（#400 A5・設計 §2.6「表示の形」）を markdown 行にする。

    ``measured=False``（同一物理キーへの conflicting category 検出）・counts が空
    （legacy 週で category を持つ TP が1件も無い）のいずれでも空リストを返す
    （§3: 内訳は「あれば出す」optional・category を持たない週で壊れない）。
    週次 delta は雑音（設計 §2.6 実測）なので**比較は表示しない** — 今週の構成比 +
    最大カテゴリの実発話1件のみ。
    """
    cb = category_breakdown or {}
    counts: Dict[str, int] = cb.get("counts") or {}
    # 衝突（同一 physical key に複数 category）で内訳を落としたときは、**黙って消さない**。
    # 内訳行が痕跡なく消えると「カテゴリが無い週」と見分けが付かず、判定の重複記録という
    # 測定バグの手がかりを失う（P4: silence != evaluated）。内訳は出さないが理由は出す。
    conflict_keys = cb.get("conflict_keys") or 0
    if not cb.get("measured"):
        if conflict_keys:
            return [
                f"  カテゴリ内訳: 測定不能"
                f"（同一発話に複数カテゴリ {conflict_keys} 件 — 判定の重複記録が疑われます）"
            ]
        return []
    if not counts:
        return []

    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], CATEGORY_ENUM.index(kv[0])))
    parts = []
    for cat, cnt in ordered:
        pct = (cnt / total * 100) if total else 0.0
        label = CATEGORY_LABELS_JA.get(cat, cat)
        parts.append(f"{label}（{cat}）{cnt}件（{pct:.0f}%）")
    unclassified = cb.get("unclassified_count") or 0
    if unclassified:
        parts.append(f"unclassified {unclassified}件")

    lines = [f"  カテゴリ構成: {', '.join(parts)}"]
    example = cb.get("top_category_example")
    top_category = cb.get("top_category")
    if example and top_category:
        top_label = CATEGORY_LABELS_JA.get(top_category, top_category)
        text = (example.get("text") or "").strip()
        reason = (example.get("reason") or "").strip()
        suffix = f"（{reason}）" if reason else ""
        lines.append(f"  最大カテゴリ（{top_label}）の実発話: {text}{suffix}")
    return lines


def _render_exclusion_diagnostics(diagnostics: Dict[str, Any]) -> List[str]:
    """指摘率の分母から除外した件数を表示する（#466・0件でも必ず表示・silence != evaluated）。

    3種別（tracked外 / 90日超 / ホーム起動）は ``correction_rate.py`` が排他的に集計する
    （ホーム起動セッションを先に除外してから tracked 判定するため、tracked外の件数と
    二重計上しない）。
    """
    untracked = diagnostics.get("excluded_untracked_total", 0)
    cutoff = diagnostics.get("excluded_before_cutoff_total", 0)
    home = diagnostics.get("excluded_home_dir_total", 0)
    total = diagnostics.get("excluded_total", untracked + cutoff + home)
    return [
        f"分母から除外: {total} 件（tracked外 {untracked} 件・"
        f"90日超 {cutoff} 件・ホーム起動 {home} 件）",
        "",
    ]


def _render_coverage_gap_reasons(correction_rate: Dict[str, Any]) -> List[str]:
    """100%未満の各週について、カバレッジ不足の排他的な理由内訳を表示する。"""
    if "coverage_gaps" not in correction_rate or correction_rate["coverage_gaps"] is None:
        return ["カバレッジ不足理由: 評価不能", ""]
    gaps = correction_rate["coverage_gaps"]
    if not isinstance(gaps, list):
        return ["カバレッジ不足理由: 評価不能", ""]

    lines: List[str] = []
    sorted_gaps = sorted(
        gaps,
        key=lambda gap: str(gap.get("week_id") or "") if isinstance(gap, dict) else "",
        reverse=True,
    )
    for gap in sorted_gaps:
        week_id = gap.get("week_id") if isinstance(gap, dict) else None
        prefix = f"- {week_id} カバレッジ不足理由" if week_id else "- カバレッジ不足理由"
        reason = gap.get("reason") if isinstance(gap, dict) else None
        if not isinstance(reason, dict) or reason.get("measured") is not True:
            detail = reason.get("reason") if isinstance(reason, dict) else None
            suffix = f"（{detail}）" if detail else ""
            lines.append(f"{prefix}: 評価不能{suffix}")
            continue

        counts = [
            reason.get("deadline_exceeded_count"),
            reason.get("unjudged_count"),
            reason.get("unclassified_count"),
        ]
        judged = gap.get("judged")
        total = gap.get("total")
        valid_ints = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts)
        valid_totals = all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (judged, total)
        )
        if not valid_ints or not valid_totals or sum(counts) != total - judged:
            lines.append(f"{prefix}: 評価不能（内訳合計が母集団と一致しません）")
            continue

        deadline_exceeded, unjudged, unclassified = counts
        line = (
            f"{prefix}: 締切（+{FREEZE_DELAY_DAYS}日）超過で集計外: {deadline_exceeded} 件"
            f"・未判定: {unjudged} 件"
        )
        if unclassified:
            line += f"・判定日時なし（旧形式レコード）: {unclassified} 件"
        lines.append(line)
    if lines:
        lines.append("")
    return lines


def _render_point_pj_breakdown(pj_breakdown: Dict[str, Any]) -> List[str]:
    """点表示（状態(ii)）専用の PJ 別内訳（#508 I7・全 PJ 列挙・floor 込み）。

    状態(iii) の PJ 行構築ロジック（既存・不変）とは意図的に別関数にする（既存分岐を
    一字も変えないため）。judged が ``MIN_PJ_RATE_DENOM`` 未満の PJ は件数のみ出す。
    """
    parts: List[str] = []
    for pj_slug, stats in sorted(pj_breakdown.items()):
        if stats.get("rate") is not None:
            parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（{stats['rate'] * 100:.1f}%）")
        else:
            parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（件数のみ・分母不足）")
    return parts


def _render_correction_rate_point(gate: Dict[str, Any], correction_rate: Dict[str, Any]) -> List[str]:
    """指摘率セクションの状態(ii)（点表示）ブロックを生成する（#508 §2-3 必須要素 (a)〜(f)）。

    呼び出し側（``_render_correction_rate``）が ``point_week`` の存在と PJ 別内訳の
    非空（I7(d)）を確認してから呼ぶ契約。
    """
    point_week = gate["point_week"]
    required = gate.get("required", GATE_CONSECUTIVE_WEEKS)
    n = gate.get("current_run_length", 0)

    week_id = point_week["week_id"]
    judged = point_week["judged_count"]
    tp = point_week["tp_count"]
    rate = point_week.get("rate")
    rate_label = f"{rate * 100:.1f}%" if rate is not None else "?"

    lines: List[str] = [
        # (a)(b): 対象週の week_id と分子/分母の実数。(c): 1週分の但し書き。
        f"**指摘率（{week_id}）: {rate_label}**（1週分。推移は {required} 週連続で表示）",
        f"判定 {judged} 件中 TP {tp} 件・カバレッジ100%",
        # (d): 連続 run の進捗。n は表示専用（I8）。
        f"連続 run の進捗: {n}/{required} 週連続",
    ]

    # (e)/I6: 点の対象週より新しい確定候補週が未測定なら、欠測を隠さず明示する。
    latest = correction_rate.get("latest_coverage")
    if latest and latest.get("week_id") != week_id:
        reasons = latest.get("failure_reasons") or []
        reason_note = f"・理由: {'・'.join(reasons)}" if reasons else ""
        lines.append(
            f"最新候補週 {latest['week_id']}: 判定カバレッジ {latest['judged']}/{latest['total']}"
            f"・未測定{reason_note}"
        )

    # (f)/I7: PJ 別内訳を必須 evidence として全件列挙する。
    pj_lines = _render_point_pj_breakdown(point_week.get("pj_breakdown") or {})
    lines.append(f"PJ別: {', '.join(pj_lines)}")

    lines.append("")
    return lines


def _render_correction_rate(
    correction_rate: Dict[str, Any],
    gate_health: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """指摘率セクション（ADR-054 §7.2.1 柱3(a)）の markdown ブロックを生成する。

    表示開始ゲート（§2.9・k=``GATE_CONSECUTIVE_WEEKS`` 週連続で全量判定確定週が揃うまで
    非表示）が閉じている間は「未測定（判定カバレッジ X/Y）」1行のみ。開いていれば
    表示対象週を新しい順に列挙し、PJ別内訳（Simpson 防御・必須 evidence）と、
    悪化週のみ TP 実発話 TOP3（朝レビューへの導線）を添える。

    #400 A5（設計 §2.6「表示の形」）: 各週の TP をカテゴリ（対象軸）で内訳分解した行も
    添える。**週次 delta の比較は表示しない**（週次 TP ≈10〜20件を8分割すると各セル
    0〜5件で雑音・PJ 構成比の変化＝task-mix 交絡が支配的なため）。表示は今週の構成比 +
    最大カテゴリの実発話1件 + task-mix 交絡の注記の3点に絞る。category を持つ週が
    1つも無ければ注記自体を出さない（silence でなく、内訳が単に無いだけ）。
    """
    gate = correction_rate.get("gate") or {}
    # #568 T3: gate_open を鵜呑みにせず、検算に通った場合だけ系列表示を許す。
    # 検算は `measurement_result.validate_correction_gate` が行い、summary 自体は
    # verbatim のまま（board["correction_rate"] の pass-through 契約を壊さない）。
    # gate_health が None の呼び出し（既存テスト等）は従来どおり gate_open に従う。
    gate_open_effective = (
        gate.get("gate_open") is True
        if gate_health is None
        else gate_health.get("gate_open_effective") is True
    )
    required = gate.get("required", GATE_CONSECUTIVE_WEEKS)
    lines: List[str] = []

    # #466: 分母から除外した件数は gate の開閉に関わらず常に表示する（silence != evaluated）。
    lines.extend(_render_exclusion_diagnostics(correction_rate.get("diagnostics") or {}))
    coverage_gap_lines = _render_coverage_gap_reasons(correction_rate)

    # #508 状態(ii): 系列ゲートが閉じていても、点表示できる確定週があれば1週分の点を出す。
    # I7(d): PJ 別内訳が空なら点表示そのものを行わない（状態(i)へフォールバック）。
    # 既存の閉ゲート分岐・開ゲート分岐はこの下で一字も変えない。
    point_week = gate.get("point_week")
    if not gate_open_effective and point_week and (point_week.get("pj_breakdown") or {}):
        lines.extend(_render_correction_rate_point(gate, correction_rate))
        lines.extend(coverage_gap_lines)
        return lines

    if not gate_open_effective:
        latest = correction_rate.get("latest_coverage")
        if latest:
            headline = (
                f"指摘率: 未測定（判定カバレッジ {latest['judged']}/{latest['total']}・"
                f"{latest['week_id']}）"
            )
        else:
            headline = "指摘率: 未測定（確定週データなし）"
        lines.append(f"**{headline}**")
        lines.append(f"全量判定の確定週が {required} 週連続で揃うまで系列は表示しません。")
        lines.append("")
        lines.extend(coverage_gap_lines)
        return lines

    displayed = correction_rate.get("displayed_weeks") or []
    lines.append("**指摘率**（判定カバレッジ100%の確定週のみ・新しい順）")
    lines.append(
        "分子は LLM judge の意味判定です（実測 precision 80% ＝ 分子の2割は誤りを含む前提で読んでください）。"
    )
    lines.append("")
    lines.extend(coverage_gap_lines)
    category_lines_by_week = {
        w["week_id"]: _category_breakdown_lines(w.get("category_breakdown"))
        for w in displayed
    }
    # task-mix 交絡の注記は**構成比を実際に表示する週がある場合だけ**出す。
    # 「測定不能」行しか無い週で注記だけ出すと、読者が存在しない構成比を探すことになる。
    if any(
        (w.get("category_breakdown") or {}).get("measured")
        and (w.get("category_breakdown") or {}).get("counts")
        for w in displayed
    ):
        lines.append(
            "カテゴリ構成は**その週に何をやったか**に強く依存します（task-mix 交絡）。"
            "週次の増減比較には使わず、今週の内訳として読んでください。"
        )
        lines.append("")
    for w in reversed(displayed):
        rate = w.get("rate")
        rate_label = f"{rate * 100:.1f}%" if rate is not None else "?"
        lines.append(
            f"- {w['week_id']}: {rate_label}"
            f"（判定 {w['judged_count']} 件中 TP {w['tp_count']} 件・カバレッジ100%）"
        )
        pj_parts = []
        for pj_slug, stats in sorted((w.get("pj_breakdown") or {}).items()):
            if stats.get("rate") is not None:
                pj_parts.append(
                    f"{pj_slug} {stats['tp']}/{stats['judged']}（{stats['rate'] * 100:.1f}%）"
                )
            else:
                pj_parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（件数のみ・分母不足）")
        if pj_parts:
            lines.append(f"  PJ別: {', '.join(pj_parts)}")
        lines.extend(category_lines_by_week.get(w["week_id"], []))
        if w.get("is_worsening") and w.get("top3_examples"):
            lines.append("  悪化週です。気になった直近の指摘:")
            for ex in w["top3_examples"]:
                text = (ex.get("text") or "").strip()
                reason = (ex.get("reason") or "").strip()
                suffix = f"（{reason}）" if reason else ""
                lines.append(f"    - {text}{suffix}")
    lines.append("")
    return lines
