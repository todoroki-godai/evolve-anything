"""rule_violation_observed レーン — 既存 rules で禁止済みのコマンドの違反観測を分離する。

repeating_patterns（tool_usage 分析）に、既存 rules で明示的に禁止されたコマンド
（例: `cd` 禁止なのに cd を 626 回観測）が「スキル候補」として混入する問題への対処。

これは「rule installed != enforced」の違反観測であり、新しいスキルを作るべき信号ではない。
専用レーン `rule_violation_observed`（「ルール導入済みだが実行が止まっていない →
hook enforce 検討」）に分離し、スキル候補レーンから除外する。

決定論・LLM 非依存。`learning_install_is_not_enforcement`（MEMORY）の思想を配線する。
"""
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


# 禁止を表すキーワード（日英）。これらを含む行の backtick トークンを禁止コマンドとみなす。
_PROHIBITION_KEYWORDS = (
    "禁止",
    "してはならない",
    "するな",
    "使わない",
    "MUST NOT",
    "DO NOT",
    "do not use",
    "避ける",
    "不可",
)

# backtick で囲まれたトークン（コマンド断片）を抽出する。
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# コマンド head として妥当なトークン（先頭語が英数/記号のコマンド名）。
_COMMAND_HEAD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")


def _command_head(text: str) -> str:
    """文字列から先頭のコマンド語を取り出す（先頭の `$ ` プロンプト等は除く）。"""
    stripped = text.strip().lstrip("$").strip()
    if not stripped:
        return ""
    return stripped.split()[0]


def extract_prohibited_command_heads(rule_dirs: Iterable[Path]) -> Set[str]:
    """rules ディレクトリ群から「禁止されたコマンド head」の集合を抽出する。

    各 *.md を行単位で走査し、禁止キーワードを含む行の backtick トークンの
    先頭語をコマンド head として収集する。決定論・LLM 非依存。

    存在しないディレクトリは無視する（安全側）。
    """
    heads: Set[str] = set()
    for rule_dir in rule_dirs:
        if not rule_dir or not Path(rule_dir).is_dir():
            continue
        for rule_file in sorted(Path(rule_dir).glob("*.md")):
            try:
                text = rule_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                heads |= _prohibited_heads_in_line(line)
    return heads


def _first_keyword_pos(line: str) -> int:
    """禁止キーワードのうち最も早い出現位置を返す（無ければ -1）。"""
    positions = [line.find(kw) for kw in _PROHIBITION_KEYWORDS]
    positions = [p for p in positions if p >= 0]
    return min(positions) if positions else -1


def _prohibited_heads_in_line(line: str) -> Set[str]:
    """1 行から禁止されたコマンド head を抽出する。

    禁止対象のコマンドは禁止キーワードの**前**の backtick トークンに現れる
    （日本語: `` `cd` 禁止 `` / 英語: `` `pkill` is MUST NOT ``）。キーワードより後ろの
    backtick トークンは代替手段（`` `git -C` を使う `` 等）の可能性が高いため除外する。
    これにより「禁止行に同居する推奨コマンド」の誤検出を防ぐ（#522-3 FP 対策）。
    """
    kw_pos = _first_keyword_pos(line)
    if kw_pos < 0:
        return set()
    heads: Set[str] = set()
    for m in _BACKTICK_RE.finditer(line):
        # 禁止キーワードより後ろに開始する backtick は代替手段とみなし除外
        if m.start() > kw_pos:
            continue
        head = _command_head(m.group(1))
        if head and _COMMAND_HEAD_RE.match(head):
            heads.add(head)
    return heads


def partition_rule_violations(
    repeating_patterns: List[Dict[str, Any]],
    prohibited_heads: Set[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """repeating_patterns を skill_candidates と rule_violation_observed に分割する。

    pattern の先頭語が prohibited_heads にあれば rule_violation_observed レーンへ。
    そうでなければ skill_candidates として残す。入力リストは破壊しない。

    Returns:
        {"skill_candidates": [...], "rule_violation_observed": [...]}
    """
    skill_candidates: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    for pat in repeating_patterns:
        head = _command_head(str(pat.get("pattern", "")))
        if head and head in prohibited_heads:
            violations.append({
                **pat,
                "violated_command": head,
                "reason": "rule_installed_but_not_enforced",
                "recommendation": (
                    f"既存 rules で `{head}` は禁止済みだが {pat.get('count', 0)} 回観測。"
                    "ルール導入済みだが実行が止まっていない → hook enforce を検討。"
                ),
            })
        else:
            skill_candidates.append(pat)
    return {
        "skill_candidates": skill_candidates,
        "rule_violation_observed": violations,
    }


def default_rule_dirs(project_root: Path) -> List[Path]:
    """突合対象の rules ディレクトリ（global ~/.claude/rules + PJ .claude/rules）を返す。"""
    return [
        Path.home() / ".claude" / "rules",
        Path(project_root) / ".claude" / "rules",
    ]
