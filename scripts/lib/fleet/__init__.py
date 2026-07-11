"""evolve-anything fleet — 全 PJ 横断のメンテナンス拠点（Phase 1: status のみ）。

設計: `todoroki-main-design-20260422-140954.md` Phase 1 節。
"""
from __future__ import annotations

import os
from pathlib import Path

STATUS_ENABLED = "ENABLED"
STATUS_STALE = "STALE"
STATUS_NOT_ENABLED = "NOT_ENABLED"

AUDIT_OK = "OK"
AUDIT_TIMEOUT = "TIMEOUT"
AUDIT_ERROR = "ERROR"
AUDIT_CACHED = "CACHED"  # timeout したが前回 growth-state cache の score を表示（#66）

from rl_common import DATA_DIR as _DEFAULT_DATA_DIR  # honors CLAUDE_PLUGIN_DATA

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DEFAULT_AUTO_MEMORY_ROOT = Path.home() / ".claude" / "projects"
_DEFAULT_PROJECTS_ROOT = Path.home() / "tools"
_DEFAULT_RL_AUDIT_BIN = Path(__file__).resolve().parent.parent.parent.parent / "bin" / "evolve-audit"
_PLUGIN_KEY_PREFIX = "evolve-anything@"
_SETTINGS_RETRY_SLEEP_SEC = 0.1
# 実 PJ の audit は単体でも 10-13s かかる（2026-06-23 実測: docs 10.1s / sys-bots 13.0s）。
# 旧既定 10s では全 active PJ が TIMEOUT し SCORE/LV/PHASE 列が恒久的に空になっていた（#66）。
# 初回 warm-up が medium PJ を完走できる 30s に引き上げ、超過時は cache fallback（AUDIT_CACHED）で
# 前回スコアを表示する。
_DEFAULT_TIMEOUT_SEC = 30.0
_DEFAULT_MAX_WORKERS = 2
_KILL_GRACE_SEC = 2.0


def _current_data_dir() -> Path:
    """CLAUDE_PLUGIN_DATA を呼び出し時に再参照して DATA_DIR を返す。

    `rl_common._DEFAULT_DATA_DIR` は import-time capture のため env 後追い変更を
    反映できない。fleet-runs 書き出しなど呼び出しタイミングが重要な処理では
    こちらを使う。
    """
    env_val = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(env_val) if env_val else Path.home() / ".claude" / "evolve-anything"


# project_loader (PJ 列挙 / 導入状況判定) は fleet/project_loader.py に集約済み（後方互換のため再エクスポート）
from .project_loader import (  # noqa: E402, F401
    _pj_safe_name,
    resolve_auto_memory_dir,
    enumerate_projects,
    enumerate_memory_dirs,
    MemoryDir,
    _load_settings_with_retry,
    _is_plugin_enabled,
    _latest_activity,
    _safe_compute_level,
    classify_project,
)


# PJ 横断 memory recall engine は fleet/recall.py に集約（後方互換のため再エクスポート）
from .recall import (  # noqa: E402, F401
    Fact,
    RecallHit,
    parse_fact_file,
    recall,
    format_hits,
)


# audit subprocess 実行 / 結果 dataclass は fleet/audit_runner.py に集約済み（後方互換のため再エクスポート）
from .audit_runner import (  # noqa: E402, F401
    AuditResult,
    IssuesSummary,
    _parse_iso,
    _parse_issues_summary,
    _terminate_process_group,
    run_audit_subprocess,
)


# Format helpers / status table は fleet/formatters.py に集約済み（後方互換のため再エクスポート）
from .formatters import (  # noqa: E402, F401
    _TABLE_HEADERS,
    _format_short_int,
    _format_cell_tokens,
    _format_cell_cache_hit,
    _format_relative,
    _format_cell_score,
    _format_cell_level,
    _format_cell_phase,
    _format_cell_last_audit,
    _format_cell_audit,
    _format_cell_issues,
    _format_cell_subagents,
    format_status_table,
    format_status_json,
)


# status 収集 / 永続化は fleet/collectors.py に集約済み（後方互換のため再エクスポート）
from .collectors import (  # noqa: E402, F401
    FleetRow,
    _collect_single,
    _find_duplicate_basenames,
    _serialize_row,
    aggregate_sessions_by_project,
    aggregate_subagents_by_project,
    collect_fleet_status,
    detect_equal_issue_counts,
    write_fleet_run,
)




# tokens サブコマンド + 注入ロジックは fleet/cli_tokens.py に集約済み（後方互換のため再エクスポート）
from .cli_tokens import (  # noqa: E402, F401
    _inject_token_metrics,
    _resolve_pj_id,
    _run_tokens,
)


# 学習素材ベースの evolve 待ち列挙（#79）は fleet/queue.py + queue_state.py に集約
from .queue import (  # noqa: E402, F401
    build_queue_result,
    collect_phantom_materials,
    collect_untracked_materials,
    fold_activity_counts,
    new_corrections_by_pj,
    select_evolve_queue,
    weak_unprocessed_by_pj,
)
from .queue_state import (  # noqa: E402, F401
    persist_last_evolve,
    read_last_evolve,
)


# queue の待ち PJ への evolve --dry-run 提案バッチ生成（#81 Phase 2）は fleet/propose.py に集約
from .propose import (  # noqa: E402, F401
    build_batch_report,
    confirm_batch,
    estimate_cost,
    filter_previously_rejected_candidates,
    format_cost_confirmation,
    render_cli_summary,
    render_markdown_report,
    run_propose_batch,
    select_targets,
    summarize_pj_result,
    write_reports,
)


# 承認済み evolve 提案の worktree→commit→push→PR 化（#82 Phase 3）は fleet/pr.py に集約
from .pr import (  # noqa: E402, F401
    AccountMismatchError,
    GitCommandError,
    ProposalTargetError,
    WorktreeError,
    branch_name,
    build_pr_body,
    build_pr_title,
    commit_all,
    commits_ahead,
    create_pr,
    create_worktree,
    default_branch,
    diff_stat,
    expected_account,
    find_existing_worktrees,
    find_latest_proposals_json,
    find_pj_entry,
    has_uncommitted_changes,
    load_proposals_report,
    parse_active_gh_account,
    push_branch,
    resolve_target,
    resolve_worktree,
    today_str,
    touched_skill_names,
    validate_branch,
    verify_push_account,
    worktree_path,
)


# CLI エントリポイント (main / _run_status / _run_test_guard / _run_discover) は
# fleet/cli.py に集約済み（後方互換のため再エクスポート、bin/evolve-fleet は fleet.main を呼ぶ）
from .cli import (  # noqa: E402, F401
    _run_discover,
    _run_pr_finish,
    _run_pr_start,
    _run_propose,
    _run_queue,
    _run_recall,
    _run_status,
    _run_test_guard,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
