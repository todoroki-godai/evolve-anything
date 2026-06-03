"""CLAUDE.md からスキル名とトリガーワードを抽出するユーティリティ。"""
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# トリガーワード行のパターン
TRIGGER_PATTERN = re.compile(
    r"(?i)トリガー(?:ワード)?:\s*|triggers?:\s*"
)


def resolve_claude_md_path(
    project_root: Optional[Path] = None,
    *,
    claude_md_path: Optional[Path] = None,
) -> Optional[Path]:
    """対象プロジェクトの CLAUDE.md を「実体パス基準」で解決する (#295)。

    evolve の dry-run が shadow コピー（worktree / サブディレクトリ / 一時コピー）で
    実行されると cwd 直下に CLAUDE.md が無く、CLAUDE.md 依存の除外ロジックが
    軒並み無効化されて誤検知が多発する。これを防ぐため、以下の順で解決する:

    1. claude_md_path を明示指定していればそれ（存在すれば）
    2. project_root（未指定なら cwd）直下の CLAUDE.md
    3. git fallback: project_root から見た git repo ルートの CLAUDE.md
       （サブディレクトリ実行でも本体 repo の CLAUDE.md に到達する）

    Returns:
        解決できた CLAUDE.md の Path。どれも見つからなければ None
        （= 非git shadow コピー等、環境解決に失敗した状態）。
    """
    if claude_md_path is not None:
        return claude_md_path if claude_md_path.exists() else None

    root = project_root or Path.cwd()
    direct = root / "CLAUDE.md"
    if direct.exists():
        return direct

    # git fallback: repo ルートの CLAUDE.md を実体パス基準で解決する。
    git_root = _git_toplevel(root)
    if git_root is not None:
        candidate = git_root / "CLAUDE.md"
        if candidate.exists():
            return candidate

    return None


def _git_toplevel(cwd: Path) -> Optional[Path]:
    """cwd が属する git repo の working tree ルートを返す（git 外なら None）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return Path(out) if out else None


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

    Note:
        shadow 実行（worktree / サブディレクトリ）でも実体パス基準で CLAUDE.md を
        解決する（resolve_claude_md_path）。解決できなければ空リスト。
    """
    resolved = resolve_claude_md_path(
        project_root=project_root, claude_md_path=claude_md_path
    )
    if resolved is None:
        return []

    try:
        content = resolved.read_text(encoding="utf-8")
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

        # スキル行の検出。以下の3形式に対応:
        #   1. `- /skill-name: ...` / `- skill-name: ...`（プレーン）
        #   2. `- **太字ラベル**: `/skill-name` - path`（ラベル + バッククォートコマンド, #295）
        skill_name = _extract_list_item_skill(stripped)
        if skill_name:
            # 前のスキルを保存
            if current_skill:
                results.append(_make_skill_entry(current_skill, current_triggers))
            current_skill = skill_name
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


# プレーンなリスト行の skill 名: `- /skill-name:` / `- skill-name:`
_PLAIN_LIST_SKILL = re.compile(r"^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]")
# バッククォート内のスラッシュコマンド: `` `/skill-name` ``
_BACKTICK_COMMAND = re.compile(r"`/([a-zA-Z0-9_:-]+)`")


def _extract_list_item_skill(stripped: str) -> Optional[str]:
    """Skills セクションのリスト行から skill 名を抽出する（無ければ None）。

    対応形式:
      1. `- /skill-name: ...` / `- skill-name: ...` （プレーン、コロン前が skill 名）
      2. `- **太字ラベル**: `/skill-name` - ...` （#295 真因。コロン後ろの
         バッククォートコマンドが skill 名。太字ラベルが非ASCIIでも拾える）

    形式 2 の補足: バッククォートコマンドの過剰捕捉は exclusion 集合を広げる
    方向（= 誤検知を減らす安全側）にしか効かないため、Skills セクション内の
    `` `/cmd` `` は積極的に skill 名候補として拾う。
    """
    if not stripped.startswith("-"):
        return None

    # 形式 1: コロン前がプレーンな skill 名（バッククォート無し）
    plain = _PLAIN_LIST_SKILL.match(stripped)
    if plain:
        return normalize_skill_name(plain.group(1))

    # 形式 2: 行内の最初のバッククォートコマンドを skill 名とする
    backtick = _BACKTICK_COMMAND.search(stripped)
    if backtick:
        return normalize_skill_name(backtick.group(1))

    return None


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
