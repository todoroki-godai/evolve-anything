"""HASP-style pitfall inject ロジック。

セッション内エラーパターンを検知し、関連スキルの pitfall テキストを返す。
UserPromptSubmit フック (hooks/pitfall_injector.py) から呼ばれる。

inject タイミング: UserPromptSubmit フックを使うため、エラー発生から
1ターン遅延がある。CC の PostToolUse 直接 inject API が将来提供された
場合はそちらへ移行可能（TODOS.md P3 参照）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent

try:
    import sys as _sys
    _sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
    from rl_common import DATA_DIR
except ImportError:
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"  # type: ignore[assignment]


def _injected_path(session_id: str) -> Path:
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"evolve-anything-injected-{session_id}.json"


def count_recent_errors(session_id: str, tail_lines: int = 200) -> int:
    """直近 tail_lines 行のうち session_id に一致するエラー数を返す。"""
    errors_file = DATA_DIR / "errors.jsonl"
    if not errors_file.exists():
        return 0
    try:
        all_lines = errors_file.read_text(encoding="utf-8").splitlines()
        recent = all_lines[-tail_lines:]
        count = 0
        for line in recent:
            try:
                record = json.loads(line)
                if record.get("session_id") == session_id:
                    count += 1
            except json.JSONDecodeError:
                pass
        return count
    except Exception:
        return 0


def get_pitfall_for_skill(skill_name: str) -> Optional[str]:
    """スキル名に対応する pitfalls.md の Active セクションテキストを返す。

    skill_name が "path/to/skill" 形式のときは末尾ディレクトリ名を使う。
    """
    name = Path(skill_name).name
    pitfall_path = _plugin_root / "skills" / name / "references" / "pitfalls.md"
    if not pitfall_path.exists():
        return None
    try:
        content = pitfall_path.read_text(encoding="utf-8")
        active_text = _extract_active_section(content)
        return active_text if active_text.strip() else None
    except Exception:
        return None


def _extract_active_section(content: str) -> str:
    """pitfalls.md から Active Pitfalls セクションのみ抽出する。"""
    lines = content.splitlines()
    in_active = False
    result: list[str] = []
    for line in lines:
        if re.match(r"^##\s+Active\s+Pitfalls", line, re.IGNORECASE):
            in_active = True
            continue
        if in_active and re.match(r"^##\s+", line):
            break
        if in_active:
            result.append(line)
    return "\n".join(result).strip()


def is_already_injected(session_id: str, skill_name: str) -> bool:
    """session_id + skill_name の組み合わせで inject 済みかを確認する。"""
    path = _injected_path(session_id)
    if not path.exists():
        return False
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
        injected: list = data.get("injected_skills", [])
        return Path(skill_name).name in injected
    except Exception:
        return False


def mark_injected(session_id: str, skill_name: str) -> None:
    """session_id + skill_name を inject 済みとしてマークする。"""
    path = _injected_path(session_id)
    try:
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        injected: list = data.get("injected_skills", [])
        name = Path(skill_name).name
        if name not in injected:
            injected.append(name)
        data["injected_skills"] = injected
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
