"""CLAUDE.md からスキル名とトリガーワードを抽出するユーティリティ。"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# トリガーワード行のパターン
TRIGGER_PATTERN = re.compile(
    r"(?i)トリガー(?:ワード)?:\s*|triggers?:\s*"
)


def extract_skill_triggers(
    claude_md_path: Optional[Path] = None,
    *,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """CLAUDE.md の Skills セクションからスキル名とトリガーワードを抽出する。

    Args:
        claude_md_path: CLAUDE.md のパス。None の場合は project_root/CLAUDE.md。
        project_root: プロジェクトルート。

    Returns:
        [{"skill": str, "triggers": [str, ...]}, ...]
        CLAUDE.md が見つからない場合は空リスト。
    """
    if claude_md_path is None:
        root = project_root or Path.cwd()
        claude_md_path = root / "CLAUDE.md"

    if not claude_md_path.exists():
        return []

    try:
        content = claude_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    return _parse_skills_section(content)


def _parse_skills_section(content: str) -> List[Dict[str, Any]]:
    """CLAUDE.md のコンテンツからスキルセクションをパースする。"""
    lines = content.splitlines()
    in_skills_section = False
    table_body_started = False
    results: List[Dict[str, Any]] = []
    current_skill: Optional[str] = None
    current_triggers: List[str] = []

    for line in lines:
        stripped = line.strip()

        # Skills セクション開始を検出（## Skills / ## Key Skills 等、見出しに skills を含む）
        if re.match(r"^#{1,3}\s+.*\bskills?\b", stripped, re.IGNORECASE):
            in_skills_section = True
            table_body_started = False
            continue

        # 別のセクション開始で Skills セクション終了
        if in_skills_section and re.match(r"^#{1,3}\s+", stripped) and not re.match(r"^#{1,3}\s+.*\bskills?\b", stripped, re.IGNORECASE):
            # 最後のスキルを保存
            if current_skill:
                results.append(_make_skill_entry(current_skill, current_triggers))
            in_skills_section = False
            table_body_started = False
            current_skill = None
            current_triggers = []
            continue

        if not in_skills_section:
            continue

        # テーブル区切り行（|---|---|）。直前の行はヘッダなので、以降を body 扱いにする
        if re.match(r"^\|[-| ]+\|", stripped):
            table_body_started = True
            continue

        # テーブル形式: | `/skill-name` | ... | or | /skill-name | ... |
        # 区切り行を見た後の body 行のみ対象（ヘッダ行 `| Skill | ... |` の誤抽出を防ぐ）
        if table_body_started and stripped.startswith("|"):
            table_match = re.match(r"^\|\s*`?/?([a-zA-Z0-9_:-]+)`?\s*\|", stripped)
            if table_match and re.match(r"^[a-zA-Z]", table_match.group(1)):
                if current_skill:
                    results.append(_make_skill_entry(current_skill, current_triggers))
                current_skill = normalize_skill_name(table_match.group(1))
                current_triggers = []
                trigger_match = TRIGGER_PATTERN.search(stripped)
                if trigger_match:
                    current_triggers = _parse_trigger_list(stripped[trigger_match.end():])
                continue

        # テーブルブロックを抜けたら body フラグを解除
        if table_body_started and not stripped.startswith("|"):
            table_body_started = False

        # スキル行の検出: `- /skill-name: ...` or `- skill-name: ...`
        skill_match = re.match(r"^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]", stripped)
        if skill_match:
            # 前のスキルを保存
            if current_skill:
                results.append(_make_skill_entry(current_skill, current_triggers))
            current_skill = normalize_skill_name(skill_match.group(1))
            current_triggers = []

            # 同じ行にトリガーワードがある場合
            trigger_match = TRIGGER_PATTERN.search(stripped)
            if trigger_match:
                trigger_text = stripped[trigger_match.end():]
                current_triggers = _parse_trigger_list(trigger_text)
            continue

        # トリガーワード行（スキル定義の続き行）
        if current_skill:
            trigger_match = TRIGGER_PATTERN.search(stripped)
            if trigger_match:
                trigger_text = stripped[trigger_match.end():]
                current_triggers = _parse_trigger_list(trigger_text)

    # 最後のスキルを保存
    if current_skill:
        results.append(_make_skill_entry(current_skill, current_triggers))

    return results


def _make_skill_entry(skill: str, triggers: List[str]) -> Dict[str, Any]:
    """スキルエントリを作成する。トリガーがない場合はスキル名をフォールバック。"""
    if not triggers:
        return {"skill": skill, "triggers": [skill]}
    return {"skill": skill, "triggers": triggers}


def _parse_trigger_list(text: str) -> List[str]:
    """カンマ区切りのトリガーワードリストをパースする。"""
    triggers = []
    for item in re.split(r"[,、]", text):
        item = item.strip()
        if item:
            triggers.append(item)
    return triggers


def normalize_skill_name(name: str) -> str:
    """スキル名を正規化する。先頭 / 除去、plugin-name: prefix 除去。"""
    name = name.lstrip("/")
    # plugin-name:skill-name → skill-name
    if ":" in name:
        name = name.split(":", 1)[1]
    return name
