#!/usr/bin/env python3
"""fleet_config.py — fleet の tracked/ignored projects 設定管理。

ユーザーが承認した PJ のみを fleet status / audit-all の対象にする。
検出は Claude Code native の `~/.claude/projects/-<slug>/*.jsonl` に埋め込まれた
`cwd` フィールドを信頼する（slug デコードの曖昧性を回避）。

Config ファイル: `~/.claude/rl-anything/fleet-config.json`
{
  "tracked_projects": ["<abs path>", ...],
  "ignored_projects": ["<abs path>", ...],
  "last_discovery": "<ISO 8601>" | null
}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# rl_common と同じ DATA_DIR 解決を使い、CLAUDE_PLUGIN_DATA env を尊重
try:
    from rl_common import DATA_DIR as _DATA_DIR
except ImportError:
    _DATA_DIR = Path.home() / ".claude" / "rl-anything"

CONFIG_PATH = _DATA_DIR / "fleet-config.json"
CC_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def _default_config() -> dict[str, Any]:
    return {
        "tracked_projects": [],
        "ignored_projects": [],
        "last_discovery": None,
    }


def load_config() -> dict[str, Any]:
    """Config を読む。未存在 or 破損時はデフォルトを返す（クラッシュしない）。"""
    if not CONFIG_PATH.is_file():
        return _default_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_config()
    if not isinstance(data, dict):
        return _default_config()
    # 欠損キーを補完
    default = _default_config()
    for k, v in default.items():
        data.setdefault(k, v)
    return data


def save_config(config: dict[str, Any]) -> None:
    """Config を atomic に保存。親ディレクトリ未存在なら作る。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # atomic: write to .tmp then rename
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(CONFIG_PATH)


def discover_cc_projects() -> list[Path]:
    """Claude Code `~/.claude/projects/` から実際に開いた PJ の cwd を列挙する。

    各 `-<slug>/` 配下の `*.jsonl` の先頭 20 行で `cwd` フィールドを探す。
    見つかった abs path（重複除去）の sorted list を返す。

    利点:
    - slug → path の reverse decode の曖昧性（`-` が分離子 vs パス内文字）を回避
    - ユーザーが実際に CC で開いた PJ のみが対象になる（ノイズ少）
    """
    if not CC_PROJECTS_ROOT.is_dir():
        return []
    found: set[Path] = set()
    for slug_dir in CC_PROJECTS_ROOT.iterdir():
        if not slug_dir.is_dir():
            continue
        cwd = _read_cwd_from_slug_dir(slug_dir)
        if cwd is not None:
            found.add(cwd)
    return sorted(found)


def _read_cwd_from_slug_dir(slug_dir: Path) -> Path | None:
    """Slug dir 配下の jsonl から `cwd` を探す。見つからなければ None。

    CC のディレクトリ構造にはばらつきがあり、トップレベルに `*.jsonl` が直置き
    されている場合と、`<session-uuid>/subagents/*.jsonl` のように nested な
    場合がある。両方カバーするため rglob で再帰検索する。
    """
    for jsonl in slug_dir.rglob("*.jsonl"):
        try:
            with jsonl.open(encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 20:  # 先頭 20 行までに cwd がなければ諦める
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(d, dict) and isinstance(d.get("cwd"), str):
                        return Path(d["cwd"]).resolve()
        except OSError:
            continue
    return None


def filter_valid_projects(paths: list[Path]) -> list[Path]:
    """`CLAUDE.md` or `.claude/` を持つ実在ディレクトリのみ返す。

    ユーザーの HOME ディレクトリ自体は CC 本体の `.claude/` を持つため除外する
    （PJ としての実体はない）。
    """
    home = Path.home().resolve()
    result: list[Path] = []
    for p in paths:
        try:
            resolved = p.resolve()
            if not resolved.is_dir():
                continue
        except OSError:
            continue
        if resolved == home:
            continue
        if (resolved / "CLAUDE.md").is_file() or (resolved / ".claude").is_dir():
            result.append(resolved)
    return result


def diff_candidates(
    config: dict[str, Any], discovered: list[Path]
) -> list[Path]:
    """tracked/ignored どちらにも含まれない新候補を返す。"""
    tracked = {Path(p) for p in config.get("tracked_projects", [])}
    ignored = {Path(p) for p in config.get("ignored_projects", [])}
    return [p for p in discovered if p not in tracked and p not in ignored]


def track_project(config: dict[str, Any], path: Path) -> None:
    """PJ を tracked に追加（ignored にあれば除去、tracked 重複なし）。"""
    s = str(path)
    config.setdefault("tracked_projects", [])
    config.setdefault("ignored_projects", [])
    if s not in config["tracked_projects"]:
        config["tracked_projects"].append(s)
    if s in config["ignored_projects"]:
        config["ignored_projects"].remove(s)


def ignore_project(config: dict[str, Any], path: Path) -> None:
    """PJ を ignored に追加（tracked にあれば除去、ignored 重複なし）。"""
    s = str(path)
    config.setdefault("tracked_projects", [])
    config.setdefault("ignored_projects", [])
    if s not in config["ignored_projects"]:
        config["ignored_projects"].append(s)
    if s in config["tracked_projects"]:
        config["tracked_projects"].remove(s)
