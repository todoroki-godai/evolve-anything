#!/usr/bin/env python3
"""evolve-anything 共通ユーティリティ。

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

# DATA_DIR 一元化 marker（#364 Phase 2）。data_dir_migration.migrate() が
# 正準 dir に設置し、以後 hook 文脈（CC が CLAUDE_PLUGIN_DATA=plugin-data を設定）
# でも正準 dir に解決される。
DATA_DIR_UNIFIED_MARKER = ".data-dir-unified"
_DEFAULT_DATA_DIR = Path.home() / ".claude" / "evolve-anything"
_CC_PLUGIN_DATA_BASE = Path.home() / ".claude" / "plugins" / "data"


def resolve_data_dir(
    env_value: str = "",
    default_dir: "Path | None" = None,
    cc_plugin_data_base: "Path | None" = None,
) -> Path:
    """DATA_DIR を解決する（#364 Phase 2: marker ゲートの一元化 redirect）。

    - env 無し → 既定 fallback（従来通り）
    - env が CC install レイアウト（~/.claude/plugins/data/ 配下）を指し、かつ
      正準 dir に一元化 marker が存在 → 正準 dir へ redirect（hook/tool 統一）
    - それ以外の env（テスト isolation の tmp dir・custom 環境）→ 無条件で尊重
    """
    default_dir = default_dir if default_dir is not None else _DEFAULT_DATA_DIR
    cc_base = cc_plugin_data_base if cc_plugin_data_base is not None else _CC_PLUGIN_DATA_BASE
    if not env_value:
        return default_dir
    env_path = Path(env_value)
    try:
        if (
            env_path.resolve().is_relative_to(Path(cc_base).resolve())
            and (default_dir / DATA_DIR_UNIFIED_MARKER).exists()
        ):
            return default_dir
    except OSError:
        pass
    return env_path


# read 統一（#45）: DATA_DIR 断片化の移行期に reader が union read する候補 dir 名。
# canonical.parent（= ~/.claude）からの相対で導出する（実 home を直接参照しない）。
_LEGACY_DATA_DIR_NAME = "rl-anything"
_PLUGINS_DATA_REL = ("plugins", "data")
# plugin-data dir 名は ``<marketplace>-<plugin>``。marketplace prefix は環境で変わりうる
# （#358 probe は ``*evolve-anything*`` を一般マッチしていた）。固定名でなく plugin token を
# 含む dir を glob して superset カバーする（旧名 rl-anything era の dir も含む）。
_PLUGINS_DATA_NAME_TOKENS = ("evolve-anything", "rl-anything")


def iter_read_data_dirs(canonical: "Path | None" = None) -> "list[Path]":
    """read 用の候補 DATA_DIR を canonical 優先・存在するものだけ列挙する（#45 read 統一）。

    DATA_DIR 断片化（canonical / legacy rename / plugins-data hook split）の間、同一ストアが
    複数 dir に分裂しているため、reader が全 dir を union して読めるように候補を返す。
    候補は **canonical.parent からの相対**で導出するため、tmp canonical を渡すテストでは
    兄弟 dir が存在せず ``[canonical]`` のみを返す（実 home ~/.claude を読まない＝hermetic。
    store モジュールが実 home を読んで xdist 非hermetic になる #420/#457 を構造的に防ぐ）。

    write 統一（#55 write barrier）+ merge（#46）が済めば候補は canonical 1 つに収束する。
    それまでの移行期に既存データを orphan させないための read 側の安全網。

    Returns:
        存在する候補 dir の list（canonical 先頭・resolve 重複排除・順序安定）。
    """
    canonical = canonical if canonical is not None else DATA_DIR
    claude_root = canonical.parent
    candidates = [canonical, claude_root / _LEGACY_DATA_DIR_NAME]
    # plugins-data 候補は固定名でなく token glob（#358 の任意 marketplace prefix 一般性を
    # 引き継ぐ・superset）。claude_root 相対の glob なので hermetic（tmp canonical では
    # 兄弟が存在せず空）。順序は名前昇順で決定論化。
    plugins_data = claude_root.joinpath(*_PLUGINS_DATA_REL)
    try:
        if plugins_data.is_dir():
            matched = [
                child
                for child in plugins_data.iterdir()
                if child.is_dir()
                and any(tok in child.name for tok in _PLUGINS_DATA_NAME_TOKENS)
            ]
            candidates.extend(sorted(matched, key=lambda p: p.name))
    except OSError:
        pass

    out: "list[Path]" = []
    seen: set = set()
    for d in candidates:
        try:
            if not d.exists():
                continue
            key = d.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


DATA_DIR = resolve_data_dir(_PLUGIN_DATA_ENV)
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
        print(f"[evolve-anything] chmod data dir warning: {e}", file=sys.stderr)


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
    is_noise_agent_type,
    sanitize_message,
    should_include_message,
)

# project 識別子 / JSONL 追記 — Slice 4
from .persistence import (  # noqa: F401, E402
    PJ_SLUG_NORMALIZATION_DATE,
    append_jsonl,
    extract_worktree_info,
    get_preceding_tool_calls,
    project_name_from_dir,
)

# usage.jsonl レコードパース単一ソース — #139
from .usage_schema import (  # noqa: F401, E402
    usage_skill_name,
    usage_timestamp,
)

# write barrier 単一書込ゲート — ADR-049 / #55
from .store_write import (  # noqa: F401, E402
    StoreWriteError,
    store_write,
    store_write_raw,
)

# 偽陽性フィードバック管理 — Slice 4
from .false_positive import (  # noqa: F401, E402
    _FALSE_POSITIVE_EXPIRY_DAYS,
    add_false_positive,
    cleanup_false_positives,
    load_false_positives,
    message_hash,
)

# hook-writer 系ストア dir 解決 — #358
# PLUGIN_DATA_BASE は store_paths から再エクスポートし、テストが
# mock.patch.object(rl_common, "PLUGIN_DATA_BASE", ...) で差し替えられるよう
# __init__.py をモジュール属性の SoT として残す（DATA_DIR と同方針）。
from .store_paths import (  # noqa: F401, E402
    PLUGIN_DATA_BASE,
    hook_store_dir,
    hook_store_path,
)
