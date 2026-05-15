"""Bloat 警告の評価 + メッセージ生成。

`scripts/bloat_control.bloat_check` の薄いラッパーと、警告辞書を日本語に整形する
`_build_bloat_message` を提供する。
"""
from __future__ import annotations

from typing import Any


def _evaluate_bloat(project_dir: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """bloat_check() を呼び出し、警告を返す。ImportError/例外時は None。"""
    triggers_cfg = config.get("triggers", {})
    bloat_cfg = triggers_cfg.get("bloat", {})
    if not bloat_cfg.get("enabled", True):
        return None
    try:
        from scripts.bloat_control import bloat_check
    except ImportError:
        return None
    try:
        result = bloat_check(project_dir)
        if result and result.get("warning_count", 0) > 0:
            return result
        return None
    except Exception:
        return None


def _build_bloat_message(bloat_result: dict[str, Any]) -> str:
    """bloat 警告から日本語メッセージを生成する。"""
    parts: list[str] = []
    for w in bloat_result.get("warnings", []):
        t = w.get("type", "")
        if t == "memory":
            parts.append(f"MEMORY.md が {w['lines']}/{w['threshold']} 行で超過")
        elif t == "claude_md":
            parts.append(f"CLAUDE.md が {w['lines']}/{w['threshold']} 行で超過")
        elif t == "rules_count":
            parts.append(f"rules が {w['count']}/{w['threshold']} 件で超過")
        elif t == "skills_count":
            parts.append(f"skills が {w['count']}/{w['threshold']} 件で超過")
        elif t == "memory_bytes":
            parts.append(f"MEMORY.md が {w['bytes']}/{w['threshold']} bytes で 25KB 上限に近接")
    return "、".join(parts)
