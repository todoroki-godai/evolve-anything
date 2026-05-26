"""永続スキップリスト (denylist) の読み書き。

グローバルスコープ（全 PJ 共通）で ~/.claude/rl-anything/skill-evolve-denylist.json に保存。
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_DENYLIST_FILENAME = "skill-evolve-denylist.json"

DATA_DIR: Path = (
    Path(os.environ["CLAUDE_PLUGIN_DATA"])
    if os.environ.get("CLAUDE_PLUGIN_DATA")
    else Path.home() / ".claude" / "rl-anything"
)


def _denylist_path() -> Path:
    return DATA_DIR / _DENYLIST_FILENAME


def load_denylist() -> dict:
    path = _denylist_path()
    if not path.exists():
        return {"skills": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "skills" not in data:
            data["skills"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"skills": {}}


def _save_denylist(data: dict) -> None:
    path = _denylist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_to_denylist(skill_names: List[str], reason: str = "user_skip") -> None:
    data = load_denylist()
    now = datetime.now(timezone.utc).isoformat()
    for name in skill_names:
        if name:
            data["skills"][name] = {"reason": reason, "denied_at": now}
    _save_denylist(data)


def get_denied_skill_names() -> set:
    data = load_denylist()
    return set(data.get("skills", {}).keys())


def remove_from_denylist(skill_names: List[str]) -> None:
    data = load_denylist()
    for name in skill_names:
        data["skills"].pop(name, None)
    _save_denylist(data)
