"""Auto-evolve trigger engine — セッション終了・corrections 蓄積時のトリガー評価。

トリガー条件の統合判定、クールダウン管理、ユーザー設定の読み込みを提供する。
LLM 呼び出しは行わない（MUST NOT）。

Phase 9 で `trigger_engine.py` 751 行 → `trigger_engine/` パッケージに分割。
公開 API (`from trigger_engine import X`) は本ファイルからの再エクスポートで維持される。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"
PENDING_TRIGGER_FILE = DATA_DIR / "pending-trigger.json"
SNOOZE_FILE = DATA_DIR / "trigger-snooze.json"

# duckdb の利用可否フラグ。実モジュールは ~100ms かかる重い C 拡張のため、
# eager import せず find_spec で存在確認のみ行う（correction_detect が毎
# UserPromptSubmit で本パッケージを import するため）。実際の duckdb 利用は
# session_store / state.py 側の関数内 lazy import に委ねる。
HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None

# FileChanged hook cooldown (seconds) — CC v2.1.83
FILE_CHANGED_COOLDOWN_SECONDS = 300  # 5 minutes

# Re-export state / config / cooldown helpers (Phase 9 / Slice 1)
from .state import (  # noqa: E402, F401
    DEFAULT_TRIGGER_CONFIG,
    TriggerResult,
    _FIRST_RUN_MIN_SESSIONS,
    _MAX_HISTORY_ENTRIES,
    _count_sessions_since,
    _deep_merge,
    _is_in_cooldown,
    _load_state,
    _load_user_config_with_explicit,
    _record_trigger,
    _save_state,
    load_trigger_config,
)


# Re-export bloat / file_change helpers (Phase 9 / Slice 2)
from .bloat import _build_bloat_message, _evaluate_bloat  # noqa: E402, F401
from .file_change import evaluate_file_changed, is_watched_file  # noqa: E402, F401


# Re-export session-end + corrections evaluators (Phase 9 / Slice 3)
from .session_corrections import evaluate_corrections, evaluate_session_end  # noqa: E402, F401



# Re-export self-evolution + pending + skill-change helpers (Phase 9 / Slice 4 — Phase 9 完了)
from .self_evolution import (  # noqa: E402, F401
    _evaluate_approval_rate_decline,
    _evaluate_self_evolution,
    get_rejected_stats,
)
from .pending import (  # noqa: E402, F401
    _is_snoozed,
    clear_snooze,
    detect_skill_changes,
    read_and_delete_pending_trigger,
    snooze_trigger,
    write_pending_trigger,
)
