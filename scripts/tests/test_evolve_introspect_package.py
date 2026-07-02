"""evolve_introspect パッケージ分割の回帰フェンス（#122 Phase 5）。

790 行の単一モジュールを機能クラスタでパッケージ分割する前に、後方互換を固定する:
  (a) 全 importer（grep 実在確認済み）が使う公開シンボルが
      `from evolve_introspect import X` / `evolve_introspect.X` で解決できる。
  (b) 分割前の top-level 名の keyset を snapshot し、分割で欠落しないことを保証する
      （欠落のみ検査＝追加は許容。audit.py / evolve.py 分割と同じ keyset 不変フェンス）。

このフェンスは分割着手前に green を確認してから分割し、分割後も green を保つ。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent  # scripts/
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

import evolve_introspect  # noqa: E402


# 全 importer が実際に import するシンボル（grep 実在確認済み）。
# split 後もこれらが package root から解決できねばならない。
_REEXPORT_CONTRACT = {
    "analyze_evolve_result",       # phases_capture / test_evolve_warning_capture
    "reconcile_split_archive",     # phases_remediate
    "_collect_archive_skills",     # evolve_reconcile
    "filter_duplicates",           # report_feedback contract / proposable dedup
    "render_issue_body",           # report_feedback contract / SKILL
    "flatten_candidates",          # report_feedback contract / SKILL
    "extract_marker",              # proposable dedup
    "summary_lines",               # report-feedback SKILL / test_skill_blocks
    "render_regression_body",      # report-feedback SKILL
    "_skill_name",                 # 既存 test_evolve_introspect が ei. でアクセス
}


# 分割前の module top-level 名（`_MARKER_RE` 等の全公開・準公開シンボル）。
# 分割で __init__ の re-export から落ちてはならない。
_KEYSET_SNAPSHOT = {
    # 定数
    "MARKER_PREFIX", "_MARKER_RE", "_TITLE_SIMILARITY_THRESHOLD",
    "_ADDITIVE_FIX_TYPES", "_LINE_LIMIT_TYPES", "_PRUNE_ARCHIVE_KEYS",
    "_AUTO_FIX_FP_CONFIDENCE", "_CATEGORY_KEYS",
    # orchestration
    "analyze_evolve_result", "reconcile_split_archive", "_issue_skill_name",
    "flatten_candidates", "summary_lines",
    # detectors: runtime
    "_detect_runtime_errors", "_detect_captured_warnings", "_parse_warning",
    "_make_warning_candidate", "_make_runtime_candidate", "_error_signature",
    # detectors: self
    "_detect_self_issues", "_detect_fp_in_auto_fixable",
    "_collect_consistency_candidates", "_load_known_fp_matcher",
    "_issue_fp_subject", "_collect_archive_skills",
    "_detect_split_archive_contradiction", "_detect_line_budget_conflict",
    # detectors: improvement
    "_detect_improvement_opportunities", "_detect_systematic_rejections",
    "_detect_calibration_regression",
    # render
    "render_issue_body", "render_regression_body",
    # dedup
    "extract_marker", "filter_duplicates", "_is_closed", "_match_existing",
    "_normalize_title",
    # helpers
    "_section", "_skill_name", "_issue_file",
}


def test_reexport_contract_importable():
    """全 importer が使う公開シンボルが package root から解決できる。"""
    missing = sorted(n for n in _REEXPORT_CONTRACT if not hasattr(evolve_introspect, n))
    assert not missing, f"re-export 欠落: {missing}"


def test_public_keyset_snapshot_no_dropped_symbols():
    """分割前の top-level 名が分割後も落ちていない（欠落のみ検査・追加は許容）。"""
    actual = {n for n in dir(evolve_introspect) if not n.startswith("__")}
    missing = sorted(_KEYSET_SNAPSHOT - actual)
    assert not missing, f"top-level シンボル欠落: {missing}"


def test_reexported_are_callable_or_value():
    """再エクスポートされた主要公開関数が実際に呼べる（属性の空 re-bind でない）。"""
    for name in ("analyze_evolve_result", "reconcile_split_archive",
                 "flatten_candidates", "summary_lines", "filter_duplicates",
                 "render_issue_body", "extract_marker"):
        assert callable(getattr(evolve_introspect, name)), f"{name} が callable でない"
