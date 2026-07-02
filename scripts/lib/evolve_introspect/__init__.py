"""evolve_introspect — evolve 実行後の自己解析（#299）。

evolve は Observe → Diagnose → Compile → Housekeeping → Report を回すが、
**evolve 自身の実行結果（提案の質・誤検出・実行時エラー）を振り返る経路が無い**。
本パッケージは evolve の result dict を入力に取り、決定論で 3 カテゴリの
GitHub issue 候補を生成する（「install != enforcement」と同型の配線漏れを塞ぐ）。

3 カテゴリ:
  1. self_detection         — evolve が出した提案・パッチ自体の質の問題
     （split↔archive 矛盾、line budget を悪化させる content 追加提案）
  2. runtime_errors         — 各フェーズで握り潰された例外 / observability の取得失敗
  3. improvement_opportunities — 構造的な改善機会
     （系統的に却下される提案 type、calibration regression）

設計原則:
  - 決定論・LLM 非依存。入力は evolve.run_evolve() の戻り値 dict のみ。
  - 各カテゴリは検出 0 件でも summary_line に「✓ 評価したが該当なし」を残す
    （silence != evaluated。沈黙＝配線漏れ誤認を防ぐ）。
  - 起票は半自動: 本パッケージは候補と dedup までを担い、gh issue create は
    SKILL 側が人間承認の後に行う。dedup_key は root cause 単位で安定させ、
    body に隠しマーカーを埋め込むことで毎 evolve の重複起票を防ぐ。

起票先は常に todoroki-godai/evolve-anything（検出対象はパイプライン自身のバグであり、
evolve がどの PJ 上で動いても起票先は固定）。SKILL 側で --repo を固定する。

構成（#122-P5 でパッケージ分割・re-export で後方互換維持）:
  - __init__:   orchestration（analyze_evolve_result / reconcile_split_archive /
                flatten_candidates / summary_lines）+ 全 re-export
  - detectors:  3 カテゴリの検出群 + 共有低レベルヘルパ
  - render:     issue body 生成
  - dedup:      重複仕分け + dedup マーカー
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── 後方互換 re-export（`from evolve_introspect import X` を全て動かす） ──
from .dedup import (  # noqa: F401
    MARKER_PREFIX,
    _MARKER_RE,
    _TITLE_SIMILARITY_THRESHOLD,
    extract_marker,
    filter_duplicates,
    _is_closed,
    _match_existing,
    _normalize_title,
)
from .render import (  # noqa: F401
    render_issue_body,
    render_regression_body,
)
from .helpers import (  # noqa: F401
    _section,
    _skill_name,
    _issue_file,
)
from .detectors import (  # noqa: F401
    _ADDITIVE_FIX_TYPES,
    _LINE_LIMIT_TYPES,
    _PRUNE_ARCHIVE_KEYS,
    _AUTO_FIX_FP_CONFIDENCE,
    _detect_runtime_errors,
    _detect_captured_warnings,
    _parse_warning,
    _make_warning_candidate,
    _make_runtime_candidate,
    _error_signature,
    _detect_self_issues,
    _detect_fp_in_auto_fixable,
    _collect_consistency_candidates,
    _load_known_fp_matcher,
    _issue_fp_subject,
    _collect_archive_skills,
    _detect_split_archive_contradiction,
    _detect_line_budget_conflict,
    _detect_improvement_opportunities,
    _detect_systematic_rejections,
    _detect_calibration_regression,
)


# ── 公開 API ─────────────────────────────────────────


def analyze_evolve_result(result: Dict[str, Any], project_dir: Optional[str] = None) -> Dict[str, Any]:
    """evolve の result dict を解析し 3 カテゴリの issue 候補を返す。

    Returns:
        {
          "self_detection":          {"candidates": [...], "summary_line": str},
          "runtime_errors":          {"candidates": [...], "summary_line": str},
          "improvement_opportunities": {"candidates": [...], "summary_line": str},
          "total_candidates": int,
        }
    各 candidate: {category, title, body, suggested_label, dedup_key, severity}
    """
    result = result or {}
    self_detection = _detect_self_issues(result)
    runtime_errors = _detect_runtime_errors(result)
    improvement = _detect_improvement_opportunities(result)

    total = sum(len(s["candidates"]) for s in (self_detection, runtime_errors, improvement))
    return {
        "self_detection": self_detection,
        "runtime_errors": runtime_errors,
        "improvement_opportunities": improvement,
        "total_candidates": total,
    }


# ── 相互排他 reconcile（split↔archive の root cause fix / #301 #302） ──


def reconcile_split_archive(result: Dict[str, Any]) -> Dict[str, Any]:
    """split（reorganize）と archive（prune）の相互排他を解決する。

    同一スキルが分割候補かつアーカイブ候補のとき **archive を優先** し、
    そのスキルを `reorganize.split_candidates`（および派生 `issues`）から除外する。
    消そうとしている対象を同じ run で分割提案するのは矛盾だからだ
    （#301 #302。`_detect_split_archive_contradiction` が検出していた root cause）。

    除外した内容は透明性のため `reorganize.split_suppressed_by_archive` に記録する
    （silent に消さない）。evolve.py が prune フェーズ直後・self-analysis の前に呼ぶ。

    Returns:
        {"suppressed": [skill, ...], "remaining_split": int}
    """
    phases = result.get("phases") if isinstance(result, dict) else None
    if not isinstance(phases, dict):
        return {"suppressed": [], "remaining_split": 0}
    reorganize = phases.get("reorganize")
    if not isinstance(reorganize, dict) or reorganize.get("skipped"):
        return {"suppressed": [], "remaining_split": 0}
    split_candidates = reorganize.get("split_candidates")
    if not isinstance(split_candidates, list) or not split_candidates:
        return {"suppressed": [], "remaining_split": 0}

    archive_skills = _collect_archive_skills(phases)
    if not archive_skills:
        return {"suppressed": [], "remaining_split": len(split_candidates)}

    kept: List[Any] = []
    suppressed: List[str] = []
    for sc in split_candidates:
        name = _skill_name(sc)
        if name and name in archive_skills:
            suppressed.append(name)
        else:
            kept.append(sc)

    if not suppressed:
        return {"suppressed": [], "remaining_split": len(split_candidates)}

    suppressed_sorted = sorted(set(suppressed))
    suppressed_set = set(suppressed_sorted)
    reorganize["split_candidates"] = kept
    reorganize["total_split_candidates"] = len(kept)
    reorganize["split_suppressed_by_archive"] = suppressed_sorted

    # split_candidate 由来の issue も除外する（skill 名は detail に入る）。
    issues = reorganize.get("issues")
    if isinstance(issues, list):
        reorganize["issues"] = [
            i for i in issues if _issue_skill_name(i) not in suppressed_set
        ]

    return {"suppressed": suppressed_sorted, "remaining_split": len(kept)}


# skill_evolve↔archive の reconcile（#400 バグ#2）と remediation batch_skip の observability 昇格
# （#400 バグ#6）は file-size budget のため evolve_reconcile.py へ分離した。reconcile_split_archive
# と対をなすので、利用側は evolve_reconcile.reconcile_skill_evolve_archive /
# build_remediation_batch_skip_observability を参照する。


def _issue_skill_name(issue: Any) -> str:
    """reorganize の split issue から skill 名を取り出す（top-level / detail 両対応）。"""
    if not isinstance(issue, dict):
        return ""
    top = _skill_name(issue)
    if top:
        return top
    detail = issue.get("detail")
    if isinstance(detail, dict):
        return _skill_name(detail)
    return ""


# ── surface 整形 ─────────────────────────────────────


_CATEGORY_KEYS = ("self_detection", "runtime_errors", "improvement_opportunities")


def flatten_candidates(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """analyze_evolve_result の 3 カテゴリの candidates を 1 リストに平坦化する。

    SKILL Step 11 が dedup・起票へ渡す前段。prose で 3 カテゴリを手で集める実装だと
    カテゴリを 1 つ取りこぼす事故が起きるため、決定論ヘルパーに一本化する。
    """
    out: List[Dict[str, Any]] = []
    for key in _CATEGORY_KEYS:
        section = analysis.get(key, {})
        if isinstance(section, dict):
            out.extend(section.get("candidates", []) or [])
    return out


def summary_lines(analysis: Dict[str, Any]) -> List[str]:
    """SKILL がそのまま列挙する surface 行を返す（0 件でも ✓ を残す）。"""
    return [
        f"- 自己検出: {analysis['self_detection']['summary_line']}",
        f"- 実行時エラー: {analysis['runtime_errors']['summary_line']}",
        f"- 改善余地: {analysis['improvement_opportunities']['summary_line']}",
    ]
