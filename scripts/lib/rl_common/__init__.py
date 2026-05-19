#!/usr/bin/env python3
"""rl-anything 共通ユーティリティ。

hooks/common.py から移動。スキルスクリプト・フックスクリプトの両方から
参照される共有コード。hooks/ への直接 sys.path 操作を不要にする。

DATA_DIR, ensure_data_dir, append_jsonl, read_workflow_context,
classify_prompt, CORRECTION_PATTERNS 等を提供する。

Phase 13 (#28) でパッケージ化。サブモジュールごとに以下のテーマで分割:

- ``config``      — userConfig (CC v2.1.83) / USER_CONFIG_DEFAULTS
- ``checkpoint``  — checkpoint 管理 (find_latest_checkpoint 等)
- ``workflow``    — workflow 文脈 / skill stack / last skill (TMPDIR 配下)
- ``detection``   — correction / prompt 分類 (CORRECTION_PATTERNS 等)
- ``persistence`` — project 識別子 / JSONL 追記
- ``false_positive`` — 偽陽性フィードバック管理

DATA_DIR / CHECKPOINTS_DIR / FALSE_POSITIVES_FILE はテストの
``mock.patch.object(rl_common, "DATA_DIR", ...)`` 経路維持のため
``__init__.py`` を SoT として残置し、サブモジュール側は関数本体内で
``import rl_common`` 経由の動的 lookup でパッチに追従する。
"""
import os
import sys
from pathlib import Path

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
CHECKPOINT_TTL_HOURS = 48.0

# InstructionsLoaded hook の定数
INSTRUCTIONS_LOADED_FLAG_PREFIX = "instructions_loaded_"
STALE_FLAG_TTL_HOURS = 24

# 偽陽性フィードバックファイル (false_positive.py で動的 lookup)
FALSE_POSITIVES_FILE = DATA_DIR / "false_positives.jsonl"


def ensure_data_dir() -> None:
    """ディレクトリが存在しない場合 MUST 自動作成する。パーミッション 700。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError as e:
        print(f"[rl-anything] chmod data dir warning: {e}", file=sys.stderr)


# --- サブモジュール re-export (Phase 13) ---

# userConfig (CC v2.1.83 manifest.userConfig) — Slice 1
from .config import (  # noqa: F401, E402
    USER_CONFIG_DEFAULTS,
    _USER_CONFIG_PREFIX,
    _parse_bool,
    is_user_config_explicit,
    load_user_config,
)

# checkpoint 管理 — Slice 2
from .checkpoint import (  # noqa: F401, E402
    _load_legacy_checkpoint,
    cleanup_old_checkpoints,
    find_latest_checkpoint,
)

# workflow 文脈 / skill stack / last skill — Slice 2
from .workflow import (  # noqa: F401, E402
    _WORKFLOW_CONTEXT_EXPIRE_SECONDS,
    last_skill_path,
    read_last_skill,
    read_skill_stack,
    read_workflow_context,
    skill_stack_path,
    workflow_context_path,
    write_last_skill,
    write_skill_stack,
)

# correction / prompt detection — Slice 3
from .detection import (  # noqa: F401, E402
    CORRECTION_PATTERNS,
    FALSE_POSITIVE_FILTERS,
    PROMPT_CATEGORIES,
    calculate_confidence,
    classify_prompt,
    detect_all_patterns,
    detect_correction,
    sanitize_message,
    should_include_message,
)

# project 識別子 / JSONL 追記 — Slice 4
from .persistence import (  # noqa: F401, E402
    append_jsonl,
    extract_worktree_info,
    get_preceding_tool_calls,
    project_name_from_dir,
)

# 偽陽性フィードバック管理 — Slice 4
from .false_positive import (  # noqa: F401, E402
    _FALSE_POSITIVE_EXPIRY_DAYS,
    add_false_positive,
    cleanup_false_positives,
    load_false_positives,
    message_hash,
)
