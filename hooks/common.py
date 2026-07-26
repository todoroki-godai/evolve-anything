#!/usr/bin/env python3
"""hooks 共通ユーティリティ — scripts/lib/rl_common.py の re-exporter。

hook scripts は 'import common' で従来通り使用可能。
ライブラリスクリプトは scripts/lib/rl_common から直接インポートすること。
"""
import os
import sys
from pathlib import Path

# hooks/ → plugin_root/ → scripts/lib/
_lib = Path(__file__).resolve().parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

from rl_common import *  # noqa: F401, F403


_VALID_RUNTIMES = frozenset({"claude", "codex"})


def resolve_runtime(payload: dict | None = None) -> str:
    """Return the calibrated hook runtime label.

    A valid payload value is authoritative.  Hooks without that field can be
    calibrated by ``EVOLVE_RUNTIME``; unknown or missing values retain the
    historical Claude default.
    """
    payload_runtime = payload.get("runtime") if isinstance(payload, dict) else None
    if isinstance(payload_runtime, str) and payload_runtime in _VALID_RUNTIMES:
        return payload_runtime
    env_runtime = os.environ.get("EVOLVE_RUNTIME")
    if isinstance(env_runtime, str) and env_runtime in _VALID_RUNTIMES:
        return env_runtime
    return "claude"
