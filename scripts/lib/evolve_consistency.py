"""evolve result の整合性 self-detect（#377-5・決定論・LLM 非依存）。

P1（#375/#376）で導入した invariant を **runtime** で consume し、evolve が出した result から
設計の歪みを 2 種類検出する:

  1. CANONICAL とのキー/型乖離（impl drift）
     — `evolve_result_schema.check_conformance_structured` を runtime result に当てる。
       契約テスト（test-time）が緑でも、実運用 result が将来ドリフトしたら evolve 自身が
       issue 候補化する。健全時は 0 件（test_real_dry_run_result_conforms と同根の guard）。

  2. usage_count==0 なのに suitability∈{high,medium}（usage↔suitability 矛盾）
     — P1(#376) で usage0 は insufficient_usage へ降格済なので**修正後は 0 件**。
       split↔archive:88 と同じ「fix 済みでも regression guard として残す」パターン。

`evolve_introspect._detect_improvement_opportunities` が本モジュールの candidate を合流させ、
evolve のたびに self_analysis 経由で surface する（手動 CLI 止まりにしない）。issue 候補形は
evolve_introspect の他検出器と同一（category/title/body/suggested_label/dedup_key/severity）。
"""
from __future__ import annotations

from typing import Any, Dict, List

# usage 実績ゼロと矛盾する suitability（提案対象に昇格する適性）。
_PROPOSED_SUITABILITY = ("high", "medium")


def collect_consistency_candidates(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """result から整合性 drift candidate の raw リストを返す（improvement へ合流する生データ）。"""
    candidates: List[Dict[str, Any]] = []
    candidates.extend(_detect_conformance_drift(result))
    candidates.extend(_detect_usage_suitability_contradiction(result))
    return candidates


def detect_consistency_drift(result: Dict[str, Any]) -> Dict[str, Any]:
    """整合性 drift を section 形（candidates + summary_line）で返す（単体 surface / テスト用）。"""
    candidates = collect_consistency_candidates(result)
    if not candidates:
        return {
            "candidates": [],
            "summary_line": "✓ 整合性: result↔契約のキー/型乖離・usage↔suitability 矛盾なし",
        }
    names = ", ".join(c.get("subject", c["dedup_key"]) for c in candidates[:5])
    if len(candidates) > 5:
        names += f", 他 {len(candidates) - 5} 件"
    return {
        "candidates": candidates,
        "summary_line": f"⚠ 整合性 {len(candidates)} 件: {names}",
    }


# ── ① CANONICAL kind/キー乖離（impl drift） ──────────────────


# runtime で candidate 化する違反種別。"missing" は除外する:
#   - #375 の元バグは型/形の drift（proposable=list vs int、.skill vs .skill_name）＝
#     wrong_kind / item_key_missing であり、これが runtime で拾うべき高価値シグナル。
#   - "missing" は部分実行・phase gating で正常に出るため runtime では FP ノイズになりやすい。
#     必須キーの完全性は test-time の test_real_dry_run_result_conforms が enforce 済み。
_RUNTIME_DRIFT_REASONS = ("wrong_kind", "item_key_missing", "null_not_allowed")


def _detect_conformance_drift(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """runtime result が CANONICAL から**型レベルで**乖離していたら improvement 候補化する。"""
    check = _load_conformance_checker()
    if check is None:
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for v in check(result):
        if v.reason not in _RUNTIME_DRIFT_REASONS:
            continue
        dedup_key = f"improvement:consistency_conformance:{v.reason}:{v.path}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        body = (
            f"## 自己解析: result が契約から乖離\n\n"
            f"evolve の result 上の `{v.path}` が CANONICAL 契約に違反しています"
            f"（種別: `{v.reason}`{f' — {v.detail}' if v.detail else ''}）。\n\n"
            f"ドキュメント記載のキー名で result を掘ると空/型不一致になり、"
            f"#375 が封じた doc↔impl drift の再発です。`evolve_result_schema.CANONICAL` か"
            f"該当 phase の result 生成箇所を修正してください。"
        )
        out.append({
            "category": "improvement",
            "subject": v.path,
            "title": f"[evolve introspect] result 契約乖離（{v.reason}）: `{v.path}`",
            "body": body,
            "suggested_label": "bug",
            "dedup_key": dedup_key,
            "severity": "medium",
        })
    return out


def _load_conformance_checker():
    """evolve_result_schema.check_conformance_structured を遅延 import で取得する。

    import 失敗時は None（検出スキップ・self_analysis 全体を壊さない）。
    """
    try:
        from evolve_result_schema import check_conformance_structured
        return check_conformance_structured
    except Exception:
        try:
            from lib.evolve_result_schema import check_conformance_structured  # type: ignore
            return check_conformance_structured
        except Exception:
            return None


# ── ② usage_count==0 × suitability∈{high,medium}（矛盾・regression guard） ──


def _detect_usage_suitability_contradiction(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """使用実績ゼロのスキルが提案対象適性（high/medium）を持つ矛盾を検出する（#376 の guard）。"""
    phases = result.get("phases", {})
    if not isinstance(phases, dict):
        return []
    se = phases.get("skill_evolve", {})
    if not isinstance(se, dict):
        return []
    assessments = se.get("assessments", [])
    if not isinstance(assessments, list):
        return []

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for a in assessments:
        if not isinstance(a, dict):
            continue
        if a.get("verification_bypass"):   # #376 の検証系例外は矛盾ではない (#560)
            continue
        suitability = a.get("suitability")
        if suitability not in _PROPOSED_SUITABILITY:
            continue
        detail = a.get("telemetry_detail", {})
        usage_count = detail.get("usage_count") if isinstance(detail, dict) else None
        if usage_count != 0:
            continue
        skill_name = a.get("skill_name", "?")
        dedup_key = f"improvement:consistency_usage_suitability:{skill_name}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        body = (
            f"## 自己解析: usage 実績と suitability の矛盾\n\n"
            f"スキル `{skill_name}` は usage_count=0（一度も使われていない）にもかかわらず "
            f"suitability=`{suitability}`（提案対象に昇格）と判定されています。\n\n"
            f"使用実績ゼロのスキルは insufficient_usage へ降格すべきです（#376）。"
            f"`skill_evolve` の suitability 確定ロジック（`_finalize_suitability`）を確認してください。"
        )
        out.append({
            "category": "improvement",
            "subject": skill_name,
            "title": f"[evolve introspect] usage0 なのに suitability={suitability}: `{skill_name}`",
            "body": body,
            "suggested_label": "bug",
            "dedup_key": dedup_key,
            "severity": "medium",
        })
    return out
