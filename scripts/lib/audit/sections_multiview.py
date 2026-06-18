"""多視点評価（multiview_eval）の observability セクション生成（#564, advisory）。

evolve 提案の評価を「単一の accept/reject」から「多視点」へ拡張し、evolve/audit レポートに
surface する。各 evolve 対象スキル（= custom スキル）を4視点ラベル（再利用可能な改善 /
過学習疑い / 退行リスク / コスト増）に決定論分類して advisory 表示する。判定は LLM 非依存。

集約する既存3部品（multiview_eval が join する）:
  - outcome_attribution.attribute_outcomes（一発成功率 / rework 率） … 当 builder で軽量取得
  - usage.compute_negative_transfer（スキル追加前後の success delta） … 当 builder で軽量取得
  - chaos.compute_chaos_score（仮想アブレーション/SPOF）

設計判断（triage builder と同じ「重い処理を audit phase で再実行しない」境界）:
  chaos は shadow copy + coherence の全件再計算で重く副作用リスクがあるため、本 builder では
  **chaos を再実行しない**。outcome_attribution / negative_transfer は usage/sessions から
  軽量に取れるのでここで集約し、chaos 入力は None で渡す（chaos 由来の regression_risk/
  reusable_improvement は evolve orchestrator が chaos 結果を持つ経路で将来補完する想定。
  その配線フックは multiview_eval.classify_skill_multiview の docstring に明示済み）。

スコープ（sections_outcome と同じ #489 当PJ化）: usage/sessions ストアは全PJ共通だが、
project_dir を worktree 安全 slug に正規化して当PJスコープに直す。

observability contract から参照される `build_*_section` 契約
（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

# custom スキルを探す候補ディレクトリ（sections_triage と同一の判定）。
_SKILL_DIR_CANDIDATES = (
    Path(".claude") / "skills",
    Path("skills"),
)

# usage/sessions の取得窓（sections_outcome の days=30 と揃える）。
_LOOKBACK_DAYS = 30


def _custom_skill_names(project_dir: Path) -> List[str]:
    """PJ の custom スキル名（SKILL.md を持つディレクトリ名）を収集する（決定論・cheap）。"""
    names: List[str] = []
    seen = set()
    for rel in _SKILL_DIR_CANDIDATES:
        base = project_dir / rel
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                if child.name not in seen:
                    seen.add(child.name)
                    names.append(child.name)
    return names


def _gather_inputs(project_dir: Path) -> Optional[Dict[str, Any]]:
    """outcome_attribution / negative_transfer を軽量取得する（chaos は再実行しない）。

    telemetry が解決できない / usage が空なら None（評価対象なし → 沈黙）。
    """
    try:
        from . import multiview_eval  # noqa: F401  # import 可否の早期確認
        from . import outcome_metrics
        from .outcome_attribution import attribute_outcomes
        from .usage import compute_negative_transfer, load_usage_data
        from telemetry_query import query_sessions
    except ImportError:
        return None

    try:
        usage = load_usage_data(days=_LOOKBACK_DAYS, project_root=project_dir)
    except Exception:
        return None
    if not usage:
        return None  # テレメトリ未蓄積 → 評価対象なし

    # 当PJスコープ（#489）: project_dir を worktree 安全 slug に正規化して sessions を引く。
    try:
        proj_slug = outcome_metrics._normalize_pj(str(project_dir))
        sessions = query_sessions(project=proj_slug)
    except Exception:
        sessions = []

    try:
        attribution = attribute_outcomes(usage=usage, sessions=sessions)
    except Exception:
        attribution = {}
    try:
        neg_transfer = compute_negative_transfer(usage)
    except Exception:
        neg_transfer = []

    return {"attribution": attribution, "negative_transfer": neg_transfer}


def build_multiview_eval_section(project_dir: Path) -> Optional[List[str]]:
    """evolve 対象スキルの多視点評価を audit に advisory 表示する（決定論・LLM 非依存）。

    観測可能性（silence != evaluated の境界）:
    - custom スキルが無い PJ（evolve 対象が無い）→ None（沈黙）
    - multiview_eval / telemetry が未解決、または usage が空 → None（沈黙）
    - 集約できたら、ラベルが付いたスキルを surface（全スキル中立でも「評価したが該当なし ✓」を出す）
    """
    proj = Path(project_dir)
    skills = _custom_skill_names(proj)
    if not skills:
        return None

    inputs = _gather_inputs(proj)
    if inputs is None:
        return None

    from . import multiview_eval

    classified = multiview_eval.classify_multiview(
        target_skills=skills,
        chaos_result=None,  # 重い chaos は再実行しない（上記設計判断）。
        outcome_attribution=inputs["attribution"],
        negative_transfer=inputs["negative_transfer"],
    )
    counts = multiview_eval.summarize_labels(classified)

    header = [
        "## Multiview Eval (evolve 提案の多視点評価・advisory — スコア重みには未反映)",
        "",
        "evolve 対象スキルを4視点で決定論分類する（accept/reject の単一軸を多視点に拡張, #564）。"
        "chaos（SPOF）は重いため本セクションでは再実行せず、outcome/negative-transfer 由来の"
        "視点のみ集約。決定論・LLM 非依存。",
        "",
    ]

    # ラベルが1つでも付いたスキルを列挙（unknown / 中立は明細から除外し件数だけ示す）。
    flagged = [
        (skill, rec)
        for skill, rec in sorted(classified.items())
        if rec.get("labels") and rec["labels"] != [multiview_eval.LABEL_UNKNOWN]
    ]

    if not flagged:
        return header + [
            f"✓ 評価したが該当視点なし（custom スキル {len(skills)} 件を評価、"
            "退行リスク / 過学習疑い / コスト増 いずれも非該当）。",
            "",
        ]

    body: List[str] = []
    for skill, rec in flagged:
        labels = rec["labels"]
        jp = ", ".join(
            multiview_eval.LABEL_DESCRIPTIONS.get(label, label) for label in labels
        )
        body.append(f"- **{skill}**: {jp}")
        ev = rec.get("evidence", {})
        body.append("    " + _format_evidence(ev))

    summary = "件数: " + " / ".join(
        f"{multiview_eval.LABEL_DESCRIPTIONS.get(label, label).split('（')[0]} {n}"
        for label, n in sorted(counts.items())
        if label != multiview_eval.LABEL_UNKNOWN
    )

    return header + body + ["", summary, ""]


def _format_evidence(ev: Dict[str, Any]) -> str:
    """evidence dict を 1 行の根拠文字列に畳む（数字に意味を添える）。"""
    parts: List[str] = []
    fts = ev.get("first_try_success")
    if fts is not None:
        parts.append(f"一発成功率 {fts:.2f}")
    rw = ev.get("rework")
    if rw is not None:
        parts.append(f"rework {rw:.2f}")
    n = ev.get("n_sessions")
    if n is not None:
        parts.append(f"n={n}")
    delta = ev.get("negative_transfer_delta")
    if delta is not None:
        parts.append(f"transfer Δ{delta:+.2f}")
    cd = ev.get("chaos_delta")
    if cd is not None:
        parts.append(f"chaos Δ{cd:.2f}")
    return "evidence: " + ("、".join(parts) if parts else "（根拠データなし）")
