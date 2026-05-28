"""fleet PJ 列挙 / 導入状況判定ロジック。

副作用無しの読み取り専用関数群。fleet/__init__.py から re-export される（後方互換）。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import (
    STATUS_ENABLED,
    STATUS_NOT_ENABLED,
    STATUS_STALE,
    _DEFAULT_AUTO_MEMORY_ROOT,
    _DEFAULT_SETTINGS_PATH,
    _PLUGIN_KEY_PREFIX,
    _SETTINGS_RETRY_SLEEP_SEC,
)


def _pj_safe_name(pj_path: Path) -> str:
    """growth-state cache 命名に使う safe_name（growth_engine._cache_path と同じルール）。"""
    name = pj_path.resolve().name or "unknown"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def resolve_auto_memory_dir(pj_path: Path) -> Path:
    """PJ パスから Claude Code auto-memory ディレクトリを逆引きする。

    命名規則: `~/.claude/projects/-<絶対パスを `/` → `-` に置換>`

    例: `/Users/foo/bar` → `~/.claude/projects/-Users-foo-bar`

    相対パスや trailing slash は `Path.resolve()` で正規化してから変換する。
    特殊文字 (`-` を含むディレクトリ名等) は Phase 3 で扱う (本実装は非対応)。
    """
    absolute = pj_path.resolve()
    slug = str(absolute).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def enumerate_projects(root: Path) -> list[Path]:
    """PJ 候補を列挙する。

    `root` 直下の子ディレクトリで、以下いずれかを持つものを PJ とみなす:
    - `.claude/` ディレクトリ
    - `CLAUDE.md` ファイル

    除外ルール:
    - ドットで始まるディレクトリ (`.worktrees/` 等) は開発メタデータのため
    - シンボリックリンクは任意パスへの audit trampoline を防ぐため

    `root` 自体が存在しない場合は空リストを返す。
    返り値はディレクトリ名でソート。
    """
    if not root.is_dir():
        return []
    projects: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(".") or child.is_symlink():
            continue
        if (child / ".claude").is_dir() or (child / "CLAUDE.md").is_file():
            projects.append(child)
    return projects


class MemoryDir(NamedTuple):
    """PJ 横断 recall の列挙単位。"""

    memory_dir: Path  # ~/.claude/projects/<slug>/memory/
    pj_display: str    # slug から先頭 `-` を除いた人間可読名


def enumerate_memory_dirs(projects_root: Path | None = None) -> list[MemoryDir]:
    """`*/memory/` に `*.md` を持つ全 PJ を列挙する（plugin 有効性で絞らない）。

    `enumerate_projects()` は `_is_plugin_enabled` で rl-anything 有効 PJ に絞るため
    横断 recall には使えない（未導入 PJ の memory が静かに消える）。recall は
    auto-memory の `memory/` 存在だけを条件に列挙する別経路。

    除外: ドットで始まるディレクトリ・シンボリックリンク（任意パス trampoline 防止）。
    `projects_root` 未指定時は `~/.claude/projects`。返り値は pj_display でソート。
    """
    root = projects_root or _DEFAULT_AUTO_MEMORY_ROOT
    if not root.is_dir():
        return []
    found: list[MemoryDir] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith(".") or child.is_symlink():
            continue
        memory_dir = child / "memory"
        if not memory_dir.is_dir():
            continue
        if not any(memory_dir.glob("*.md")):
            continue
        found.append(MemoryDir(memory_dir=memory_dir, pj_display=child.name.lstrip("-")))
    return sorted(found, key=lambda m: m.pj_display)


def _load_settings_with_retry(settings_path: Path) -> dict | None:
    """settings.json を読んで dict を返す。parse 失敗時は 100ms 後に 1 回 retry。"""
    for attempt in range(2):
        if not settings_path.is_file():
            return None
        try:
            return json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            if attempt == 0:
                time.sleep(_SETTINGS_RETRY_SLEEP_SEC)
                continue
            return None


def _is_plugin_enabled(settings: dict) -> bool:
    """settings.enabledPlugins に rl-anything@* が truthy で含まれるか。"""
    enabled = settings.get("enabledPlugins") or {}
    if not isinstance(enabled, dict):
        return False
    for key, value in enabled.items():
        if key.startswith(_PLUGIN_KEY_PREFIX) and bool(value):
            return True
    return False


def _latest_activity(auto_memory_dir: Path) -> float | None:
    """auto-memory ディレクトリ内の `.jsonl` の最新 mtime を返す。無ければ None。"""
    if not auto_memory_dir.is_dir():
        return None
    latest: float | None = None
    for f in auto_memory_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest


def _safe_compute_level(env_score: object) -> int | None:
    if not isinstance(env_score, (int, float)):
        return None
    try:
        from growth_level import compute_level
    except ImportError:
        return None
    return compute_level(float(env_score)).level


def classify_project(
    pj_path: Path,
    settings_path: Path | None = None,
    auto_memory_root: Path | None = None,
    stale_days: int = 30,
    now: datetime | None = None,
) -> str:
    """PJ の rl-anything 導入状況を 3 値で判定する。

    判定表 (設計 Phase 1 ハイブリッド):
    - `rl-anything@*` 有効 + auto-memory の直近 `.jsonl` が `stale_days` 以内 → ENABLED
    - `rl-anything@*` 有効 + auto-memory 古い or 欠損 → STALE
    - `rl-anything@*` 無効 or settings 欠損 / 破損（retry も失敗） → NOT_ENABLED

    `settings_path` が破損していた場合は 100ms sleep 後に 1 回だけ retry する。
    """
    settings_path = settings_path or _DEFAULT_SETTINGS_PATH
    auto_memory_root = auto_memory_root or _DEFAULT_AUTO_MEMORY_ROOT
    now = now or datetime.now(timezone.utc)

    settings = _load_settings_with_retry(settings_path)
    if settings is None or not _is_plugin_enabled(settings):
        return STATUS_NOT_ENABLED

    slug = str(pj_path.resolve()).replace("/", "-")
    auto_memory_dir = auto_memory_root / slug

    latest = _latest_activity(auto_memory_dir)
    if latest is None:
        return STATUS_STALE
    age_sec = now.timestamp() - latest
    if age_sec > stale_days * 86400:
        return STATUS_STALE
    return STATUS_ENABLED
