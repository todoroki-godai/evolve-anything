"""Trigger engine 状態管理 + 共通ユーティリティ。

`evolve-state.json` の load/save、trigger config のマージ、cooldown 判定、
セッション数カウント、`hooks/common.py` userConfig 取得を担う。

注: モジュール定数 (`DATA_DIR` / `EVOLVE_STATE_FILE` 等) は package 経由で
`from . import DATA_DIR` の遅延参照で取得する（テストの `mock.patch("trigger_engine.DATA_DIR", ...)` 追従のため）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# History pruning limit
_MAX_HISTORY_ENTRIES = 100

# Default trigger config
DEFAULT_TRIGGER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "triggers": {
        "session_end": {"enabled": True, "min_sessions": 3, "max_days": 7},
        "corrections": {"enabled": True, "threshold": 10},
        "audit_overdue": {"enabled": True, "interval_days": 30},
        "bloat": {"enabled": True},
    },
    "cooldown_hours": 24,
}

# First-run threshold (evolve-state.json missing)
_FIRST_RUN_MIN_SESSIONS = 3


def _load_user_config_with_explicit() -> tuple[dict, callable]:
    """hooks/common.py から userConfig をロードし、(config, is_explicit) を返す。

    hooks ディレクトリの sys.path 操作を一元化。
    ImportError 時は空 config + 常に False を返す callable を返す。
    """
    import sys as _sys
    _hooks_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "hooks")
    if _hooks_dir not in _sys.path:
        _sys.path.insert(0, _hooks_dir)
    from common import load_user_config, is_user_config_explicit
    return load_user_config(), is_user_config_explicit


@dataclass
class TriggerResult:
    """トリガー評価結果。"""

    triggered: bool = False
    reason: str = ""
    action: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _load_state() -> dict[str, Any]:
    """evolve-state.json を読み込む。存在しない場合は空 dict。"""
    from . import EVOLVE_STATE_FILE
    if not EVOLVE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(EVOLVE_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    """evolve-state.json に保存する。"""
    from . import DATA_DIR, EVOLVE_STATE_FILE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVOLVE_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_trigger_config(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """trigger_config を読み込み、デフォルト値とマージして返す。

    マージ優先順位: DEFAULT < evolve-state.json < userConfig (env vars)
    """
    if state is None:
        state = _load_state()
    user_config = state.get("trigger_config", {})
    # Deep merge: default <- evolve-state
    config = _deep_merge(DEFAULT_TRIGGER_CONFIG, user_config)

    # userConfig (CC v2.1.83 manifest.userConfig) からの上書き
    # 明示的にセットされたキーのみ上書き（デフォルト値で evolve-state を潰さない）
    try:
        uc, is_explicit = _load_user_config_with_explicit()
    except Exception:
        uc, is_explicit = {}, lambda _: False

    # 注意: config のネスト dict は _deep_merge の shallow copy で共有参照。変異前に deep copy する
    has_explicit = any(is_explicit(k) for k in ("auto_trigger", "cooldown_hours", "evolve_interval_days", "audit_interval_days", "min_sessions"))
    if has_explicit:
        import copy
        config = copy.deepcopy(config)
        if is_explicit("auto_trigger"):
            config["enabled"] = uc["auto_trigger"]
        if is_explicit("cooldown_hours"):
            config["cooldown_hours"] = uc["cooldown_hours"]
        if is_explicit("evolve_interval_days"):
            config["triggers"]["session_end"]["max_days"] = uc["evolve_interval_days"]
        if is_explicit("audit_interval_days"):
            config["triggers"]["audit_overdue"]["interval_days"] = uc["audit_interval_days"]
        if is_explicit("min_sessions"):
            config["triggers"]["session_end"]["min_sessions"] = uc["min_sessions"]

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (shallow copy)."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# --- Cooldown management ---


def _is_in_cooldown(
    state: dict[str, Any], reason: str, cooldown_hours: float
) -> bool:
    """同一 reason のトリガーがクールダウン期間内に発火済みか判定する。"""
    history = state.get("trigger_history", [])
    now = datetime.now(timezone.utc)
    for entry in reversed(history):
        if entry.get("reason") != reason:
            continue
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            elapsed_hours = (now - ts).total_seconds() / 3600
            if elapsed_hours < cooldown_hours:
                return True
        except (KeyError, ValueError):
            continue
    return False


def _record_trigger(state: dict[str, Any], result: TriggerResult) -> dict[str, Any]:
    """トリガー発火を trigger_history に記録し、pruning する。

    primary reason と details["all_reasons"] の両方を記録する。
    cooldown 判定は reason ごとに行うので、複数 reason 発火時に
    primary 以外も記録しないと次回 cooldown が機能しない。
    """
    history = state.get("trigger_history", [])
    timestamp = datetime.now(timezone.utc).isoformat()

    # 全 reasons を記録（primary 重複を避けて dedup）
    all_reasons = result.details.get("all_reasons") or [result.reason]
    seen: set[str] = set()
    for reason in all_reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        history.append(
            {
                "reason": reason,
                "action": result.action,
                "timestamp": timestamp,
            }
        )

    # Pruning: keep only the latest entries
    if len(history) > _MAX_HISTORY_ENTRIES:
        history = history[-_MAX_HISTORY_ENTRIES:]
    state["trigger_history"] = history
    return state


# --- Session count helper ---


def _count_sessions_since(last_run: str) -> int:
    """前回 evolve 以降のユニークセッション数を session_store 経由でカウント。

    session_store 内部で DuckDB / JSONL フォールバックを判断する。
    """
    try:
        import session_store
        return session_store.count_unique_since(last_run)
    except ImportError:
        return 0
