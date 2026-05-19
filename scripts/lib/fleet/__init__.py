"""rl-anything fleet — 全 PJ 横断のメンテナンス拠点（Phase 1: status のみ）。

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

from rl_common import DATA_DIR as _DEFAULT_DATA_DIR  # honors CLAUDE_PLUGIN_DATA

_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_DEFAULT_AUTO_MEMORY_ROOT = Path.home() / ".claude" / "projects"
_DEFAULT_PROJECTS_ROOT = Path.home() / "tools"
_DEFAULT_RL_AUDIT_BIN = Path(__file__).resolve().parent.parent.parent.parent / "bin" / "rl-audit"
_PLUGIN_KEY_PREFIX = "rl-anything@"
_SETTINGS_RETRY_SLEEP_SEC = 0.1
_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_MAX_WORKERS = 2
_KILL_GRACE_SEC = 2.0


def _current_data_dir() -> Path:
    """CLAUDE_PLUGIN_DATA を呼び出し時に再参照して DATA_DIR を返す。

    `rl_common._DEFAULT_DATA_DIR` は import-time capture のため env 後追い変更を
    反映できない。fleet-runs 書き出しなど呼び出しタイミングが重要な処理では
    こちらを使う。
    """
    env_val = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(env_val) if env_val else Path.home() / ".claude" / "rl-anything"


# project_loader (PJ 列挙 / 導入状況判定) は fleet/project_loader.py に集約済み（後方互換のため再エクスポート）
from .project_loader import (  # noqa: E402, F401
    _pj_safe_name,
    resolve_auto_memory_dir,
    enumerate_projects,
    _load_settings_with_retry,
    _is_plugin_enabled,
    _latest_activity,
    _safe_compute_level,
    classify_project,
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
)


# status 収集 / 永続化は fleet/collectors.py に集約済み（後方互換のため再エクスポート）
from .collectors import (  # noqa: E402, F401
    FleetRow,
    _collect_single,
    _find_duplicate_basenames,
    _serialize_row,
    aggregate_subagents_by_project,
    collect_fleet_status,
    write_fleet_run,
)




# tokens サブコマンド + 注入ロジックは fleet/cli_tokens.py に集約済み（後方互換のため再エクスポート）
from .cli_tokens import (  # noqa: E402, F401
    _inject_token_metrics,
    _resolve_pj_id,
    _run_tokens,
)


# CLI エントリポイント (main / _run_status / _run_test_guard / _run_discover) は
# fleet/cli.py に集約済み（後方互換のため再エクスポート、bin/rl-fleet は fleet.main を呼ぶ）
from .cli import (  # noqa: E402, F401
    _run_discover,
    _run_status,
    _run_test_guard,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
