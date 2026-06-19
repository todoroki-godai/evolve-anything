"""rule_violation_observed レーン — 既存 rules で禁止済みのコマンドの違反観測を分離する。

repeating_patterns（tool_usage 分析）に、既存 rules で明示的に禁止されたコマンド
（例: `cd` 禁止なのに cd を 626 回観測）が「スキル候補」として混入する問題への対処。

これは「rule installed != enforced」の違反観測であり、新しいスキルを作るべき信号ではない。
専用レーン `rule_violation_observed`（「ルール導入済みだが実行が止まっていない →
hook enforce 検討」）に分離し、スキル候補レーンから除外する。

また、examples フィールドの巨大な多行スクリプトを 1 行 truncate し、
別 PJ のソースツリーを参照する例には cross_pj: true メタを付与する（#555）。

決定論・LLM 非依存。`learning_install_is_not_enforcement`（MEMORY）の思想を配線する。
"""
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

# examples フィールドの truncate 上限（字数）
_TRUNCATE_MAX_CHARS = 120
_ELLIPSIS = "…"

# rule_violation_observed を hook_candidate へ昇格する頻度しきい値（#585）。
# builtin_replaceable の検出しきい値（REPEATING_THRESHOLD=5）と同水準だと
# 低頻度の偶発違反まで remediation proposable に乗って質問攻めになるため、
# 「enforce すべき高頻度違反」に絞る独自しきい値を定義する。違反は既に rules で
# 明文禁止済みであり「hook で機械強制する」価値があるのは反復が定着した違反に限る。
RULE_VIOLATION_HOOK_THRESHOLD = 20


def truncate_example(text: str) -> str:
    """コマンド example を 1 行・最大 120 字に truncate する。

    多行の場合は最初の 1 行のみ取り出し「…」を末尾に付加する。
    1 行でも 120 字を超える場合は 120 字で切り「…」を付加する。
    空文字列はそのまま返す。
    """
    if not text:
        return text
    is_multiline = "\n" in text
    first_line = text.split("\n", 1)[0]
    if len(first_line) > _TRUNCATE_MAX_CHARS:
        return first_line[:_TRUNCATE_MAX_CHARS] + _ELLIPSIS
    if is_multiline:
        return first_line + _ELLIPSIS
    return first_line


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


def _is_cross_pj_example(example: str, project_root: Optional[Path]) -> bool:
    """example コマンドが project_root 外の絶対パスを含むかどうかを判定する。

    project_root が None の場合は判定不能として False を返す。
    example 内に絶対パス（/ 始まり）が含まれ、かつ project_root のパスプレフィックスを
    持たない場合に True を返す。比較は文字列レベルで行い、resolve() は使わない
    （symlink・マウントポイント差異による FP を防ぐ）。
    """
    if project_root is None:
        return False
    # 文字列比較：trailing slash を正規化してプレフィックス一致チェックに備える
    proj_str = str(project_root).rstrip("/")
    tokens = example.split()
    for token in tokens:
        # オプション・フラグは除外
        if token.startswith("-"):
            continue
        # 絶対パスを含むトークン（/ で始まるか / を含む）を探す
        if "/" not in token:
            continue
        # token 内で最初の / を探し、絶対パス部分を取り出す
        slash_idx = token.find("/")
        path_part = token[slash_idx:]
        if not path_part.startswith("/"):
            continue
        # project_root のプレフィックスを持つ場合は同一 PJ → スキップ
        if path_part == proj_str or path_part.startswith(proj_str + "/"):
            continue
        # 別 PJ の絶対パスを発見
        return True
    return False


def partition_rule_violations(
    repeating_patterns: List[Dict[str, Any]],
    prohibited_heads: Set[str],
    project_root: Optional[Path] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """repeating_patterns を skill_candidates と rule_violation_observed に分割する。

    pattern の先頭語が prohibited_heads にあれば rule_violation_observed レーンへ。
    そうでなければ skill_candidates として残す。入力リストは破壊しない。

    examples フィールドは truncate_example で 1 行・120 字に切り詰める（#555）。
    project_root が指定された場合、examples 内に別 PJ のパスが含まれる違反には
    cross_pj: true メタを付与する（#555）。

    Returns:
        {"skill_candidates": [...], "rule_violation_observed": [...]}
    """
    skill_candidates: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    for pat in repeating_patterns:
        head = _command_head(str(pat.get("pattern", "")))
        if head and head in prohibited_heads:
            # examples を truncate
            raw_examples: List[str] = pat.get("examples", [])
            truncated_examples = [truncate_example(ex) for ex in raw_examples]
            # cross_pj 判定：いずれかの example が別 PJ を参照している場合
            has_cross_pj = any(
                _is_cross_pj_example(ex, project_root) for ex in raw_examples
            )
            entry: Dict[str, Any] = {
                **pat,
                "examples": truncated_examples,
                "violated_command": head,
                "reason": "rule_installed_but_not_enforced",
                "recommendation": (
                    f"既存 rules で `{head}` は禁止済みだが {pat.get('count', 0)} 回観測。"
                    "ルール導入済みだが実行が止まっていない → hook enforce を検討。"
                ),
            }
            if has_cross_pj:
                entry["cross_pj"] = True
            violations.append(entry)
        else:
            skill_candidates.append(pat)
    return {
        "skill_candidates": skill_candidates,
        "rule_violation_observed": violations,
    }


# 違反コマンドを block する enforcement PreToolUse hook テンプレート（#585）。
# builtin_replaceable の hook（代替ツールへ誘導）と違い、これは「既存 rules で禁止済み
# のコマンドを機械的に block する」enforcement 型。代替は rules 本文に記載済みのため
# ここでは block + ルール参照誘導に徹する。
_ENFORCEMENT_HOOK_TEMPLATE = '''\
#!/usr/bin/env python3
"""PreToolUse hook: 既存 rules で禁止済みのコマンドを block する（evolve-anything #585 生成）。

rule_installed_but_not_enforced（ルール導入済みだが実行が止まっていない）違反を
高頻度観測したため、機械的に enforce する。代替手段は該当 rule 本文を参照すること。
"""
import json
import sys

# 禁止コマンド head の集合。
PROHIBITED = {prohibited_set}


def _get_command_head(command):
    parts = command.strip().lstrip("$").strip().split()
    idx = 0
    while idx < len(parts) and parts[idx] in ("env", "sudo"):
        idx += 1
    return parts[idx] if idx < len(parts) else ""


def check_command(command):
    head = _get_command_head(command)
    if head in PROHIBITED:
        return (
            f"`{{head}}` は既存 rules で禁止されています。該当ルールの代替手段を使用してください。"
        )
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    command = data.get("tool_input", {{}}).get("command", "")
    if not command:
        sys.exit(0)
    reason = check_command(command)
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
'''


def _enforcement_hook_script_path() -> Path:
    """enforcement hook の出力先（global ~/.claude/hooks）。"""
    return Path.home() / ".claude" / "hooks" / "enforce-prohibited-commands.py"


def make_hook_candidate_issues_from_rule_violations(
    rule_violations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """高頻度 rule_violation_observed を tool_usage_hook_candidate issue に昇格する（#585）。

    builtin_replaceable が make_hook_candidate_issue で remediation proposable に
    乗るのと同じ経路に、rule_installed_but_not_enforced 違反のうち
    RULE_VIOLATION_HOOK_THRESHOLD 以上の高頻度なものを乗せる。

    違反コマンド head を block する enforcement PreToolUse hook を 1 つの scaffold に
    まとめて生成し、既存の make_hook_candidate_issue（type=tool_usage_hook_candidate）で
    issue 化する。これにより remediation の fix_hook_scaffold / rationale / confidence
    がそのまま再利用される。source のみ "rule_violation_observed" に上書きし、由来を
    トレース可能にする。

    入力は破壊しない。決定論・LLM 非依存。

    Returns:
        tool_usage_hook_candidate issue のリスト（昇格対象が無ければ空リスト）。
    """
    # 遅延 import で循環依存を避ける（issue_schema は rule_violation_lane を import しない）。
    from issue_schema import make_hook_candidate_issue

    eligible: List[Dict[str, Any]] = []
    for viol in rule_violations or []:
        head = str(viol.get("violated_command", "")).strip()
        if not head:
            continue
        count = viol.get("count", 0) or 0
        if count < RULE_VIOLATION_HOOK_THRESHOLD:
            continue
        eligible.append({"head": head, "count": count})

    if not eligible:
        return []

    # 違反 head をまとめて 1 つの enforcement hook scaffold にする。
    commands = sorted({e["head"] for e in eligible})
    total_count = sum(e["count"] for e in eligible)

    script_path = _enforcement_hook_script_path()
    script_content = _ENFORCEMENT_HOOK_TEMPLATE.format(
        prohibited_set=repr(set(commands)),
    )
    import json

    settings_diff = json.dumps({
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": f"python3 {script_path}",
                }],
            }],
        },
    }, ensure_ascii=False, indent=2)

    hook_candidate = {
        "script_path": str(script_path),
        "script_content": script_content,
        "settings_diff": settings_diff,
        "target_commands": commands,
    }
    issue = make_hook_candidate_issue(hook_candidate, total_count)
    # 由来を rule_violation レーンに上書き（builtin_replaceable と区別する）。
    issue["source"] = "rule_violation_observed"
    return [issue]


def default_rule_dirs(project_root: Path) -> List[Path]:
    """突合対象の rules ディレクトリ（global ~/.claude/rules + PJ .claude/rules）を返す。"""
    return [
        Path.home() / ".claude" / "rules",
        Path(project_root) / ".claude" / "rules",
    ]
