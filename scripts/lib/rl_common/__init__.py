#!/usr/bin/env python3
"""rl-anything 共通ユーティリティ。

hooks/common.py から移動。スキルスクリプト・フックスクリプトの両方から
参照される共有コード。hooks/ への直接 sys.path 操作を不要にする。

DATA_DIR, ensure_data_dir, append_jsonl, read_workflow_context,
classify_prompt, CORRECTION_PATTERNS 等を提供する。
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
CHECKPOINT_TTL_HOURS = 48.0

# ワークフロー文脈ファイルの有効期限（秒）
_WORKFLOW_CONTEXT_EXPIRE_SECONDS = 24 * 60 * 60  # 24時間

# InstructionsLoaded hook の定数
INSTRUCTIONS_LOADED_FLAG_PREFIX = "instructions_loaded_"
STALE_FLAG_TTL_HOURS = 24

# userConfig (CC v2.1.83 manifest.userConfig) は rl_common/config.py に集約済み。
# 後方互換のため `_parse_bool` / `load_user_config` / `is_user_config_explicit` /
# `USER_CONFIG_DEFAULTS` / `_USER_CONFIG_PREFIX` を re-export する (Phase 13 / Slice 1)。
from .config import (  # noqa: F401
    USER_CONFIG_DEFAULTS,
    _USER_CONFIG_PREFIX,
    _parse_bool,
    is_user_config_explicit,
    load_user_config,
)


def ensure_data_dir() -> None:
    """ディレクトリが存在しない場合 MUST 自動作成する。パーミッション 700。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError as e:
        print(f"[rl-anything] chmod data dir warning: {e}", file=sys.stderr)


# checkpoint 系は rl_common/checkpoint.py に集約済み (Phase 13 / Slice 2)
# workflow_context / skill_stack / last_skill 系は rl_common/workflow.py に集約済み
from .checkpoint import (  # noqa: F401, E402
    _load_legacy_checkpoint,
    cleanup_old_checkpoints,
    find_latest_checkpoint,
)
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


# PROMPT_CATEGORIES / CORRECTION_PATTERNS / FALSE_POSITIVE_FILTERS および
# classify_prompt / sanitize_message / should_include_message /
# calculate_confidence / detect_correction / detect_all_patterns は
# rl_common/detection.py に集約済み (Phase 13 / Slice 3)
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


# last_skill_path / write_last_skill / read_last_skill は rl_common/workflow.py に集約済み
# （上部の re-export に集約）


def project_name_from_dir(project_dir: str) -> str:
    """プロジェクトディレクトリパスから末尾のディレクトリ名を返す。"""
    return Path(project_dir).name


def extract_worktree_info(event: dict) -> dict | None:
    """hook event payload から worktree 情報を抽出する。"""
    wt = event.get("worktree")
    if not isinstance(wt, dict):
        return None
    name = wt.get("name")
    branch = wt.get("branch")
    if not name and not branch:
        return None
    return {"name": name or "", "branch": branch or ""}


def append_jsonl(filepath: Path, record: dict) -> None:
    """JSONL ファイルに1行追記する。新規作成時はパーミッション 600 を設定。失敗時はサイレント。"""
    try:
        is_new = not filepath.exists()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if is_new:
            try:
                filepath.chmod(0o600)
            except OSError as e:
                print(f"[rl-anything] chmod file warning: {e}", file=sys.stderr)
    except OSError as e:
        print(f"[rl-anything] write failed: {e}", file=sys.stderr)


# --- 偽陽性フィードバック ---

FALSE_POSITIVES_FILE = DATA_DIR / "false_positives.jsonl"
_FALSE_POSITIVE_EXPIRY_DAYS = 180


def message_hash(text: str) -> str:
    """メッセージの SHA-256 ハッシュを返す。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def load_false_positives() -> set[str]:
    """false_positives.jsonl から message_hash のセットを読み込む。"""
    if not FALSE_POSITIVES_FILE.exists():
        return set()
    try:
        hashes = set()
        for line in FALSE_POSITIVES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                h = record.get("message_hash")
                if h:
                    hashes.add(h)
            except json.JSONDecodeError:
                continue
        return hashes
    except OSError as e:
        print(f"[rl-anything] load_false_positives warning: {e}", file=sys.stderr)
        return set()


def add_false_positive(msg: str, correction_type: str) -> None:
    """偽陽性をファイルに追記する。"""
    ensure_data_dir()
    record = {
        "message_hash": message_hash(msg),
        "original_type": correction_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    append_jsonl(FALSE_POSITIVES_FILE, record)


def cleanup_false_positives() -> int:
    """180日超のエントリを false_positives.jsonl から削除する。削除件数を返す。"""
    if not FALSE_POSITIVES_FILE.exists():
        return 0
    try:
        lines = FALSE_POSITIVES_FILE.read_text(encoding="utf-8").splitlines()
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FALSE_POSITIVE_EXPIRY_DAYS)
        kept = []
        removed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                ts_str = record.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        removed += 1
                        continue
                kept.append(json.dumps(record, ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                kept.append(line)
        if removed > 0:
            FALSE_POSITIVES_FILE.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
        return removed
    except OSError as e:
        print(f"[rl-anything] cleanup_false_positives warning: {e}", file=sys.stderr)
        return 0
