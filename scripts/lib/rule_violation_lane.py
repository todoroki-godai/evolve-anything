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
import ast
import re
from datetime import datetime, timezone
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

# 文の区切り。禁止キーワードが支配する範囲をこの手前までに限る。
_SENTENCE_DELIMITERS = ("。", "．", "!", "?", "！", "？")

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


def _tokenize_command(text: str) -> List[str]:
    """文字列を空白区切りのトークン列にする（先頭の `$ ` プロンプト等は除く）。"""
    stripped = text.strip().lstrip("$").strip()
    if not stripped:
        return []
    return stripped.split()


def _prohibited_spec(text: str) -> str:
    """backtick トークンから禁止コマンドの照合対象（spec）を取り出す（#222）。

    単一語トークン（例: `` `cd` ``）はそのまま head として扱う（従来挙動）。
    複数語トークン（例: `` `git checkout -b` ``）は先頭語 `git` への縮約をせず、
    トークン列全体を正規化（空白1つ区切り）して保持する。これにより下流の照合が
    「先頭語が一致すれば無関係な全呼び出しにマッチする」誤検出を起こさない。

    先頭語のみ有効なコマンド名形式（`_COMMAND_HEAD_RE`）かを検証し、不正な場合
    （記号のみの backtick トークン等）は空文字列を返す。
    """
    tokens = _tokenize_command(text)
    if not tokens or not _COMMAND_HEAD_RE.match(tokens[0]):
        return ""
    return " ".join(tokens)


def _match_prohibited_spec(pattern: str, prohibited_specs: Set[str]) -> str:
    """pattern のトークン列が prohibited_specs のいずれかと prefix 一致するか判定する（#222）。

    prohibited_specs の各要素は空白区切り1語以上のトークン列。単一語 spec
    （例: "cd"）は pattern の先頭語一致（従来の head 一致と同じ挙動）、複数語 spec
    （例: "git checkout -b"）は pattern の先頭 N トークンが完全一致する場合のみ
    マッチする。これにより「先頭語だけ一致する無関係なコマンド」（例: `git status`
    に対する禁止指定 `git checkout -b`）を誤マッチしない。

    複数の spec が該当する場合は最も具体的な（トークン数が多い）spec を返す。
    一致が無ければ空文字列を返す。
    """
    tokens = _tokenize_command(pattern)
    if not tokens:
        return ""
    matched = ""
    for spec in prohibited_specs:
        spec_tokens = spec.split()
        if not spec_tokens:
            continue
        if tokens[: len(spec_tokens)] == spec_tokens and len(spec_tokens) > len(matched.split()):
            matched = spec
    return matched


def extract_prohibited_command_heads(rule_dirs: Iterable[Path]) -> Set[str]:
    """rules ディレクトリ群から「禁止されたコマンドの照合 spec」の集合を抽出する。

    各 *.md を行単位で走査し、禁止キーワードを含む行の backtick トークンを
    spec（単一語なら head、複数語ならトークン列全体・#222）として収集する。
    決定論・LLM 非依存。

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


def _keyword_positions(line: str) -> List[int]:
    """行内の禁止キーワードの全出現位置を昇順で返す（無ければ空リスト）。

    1 行に複数の禁止表現が並ぶ rules 本文（1 項目 1 行の長文）に対応するため、
    最も早い 1 件ではなく全出現を返す。
    """
    positions: List[int] = []
    for kw in _PROHIBITION_KEYWORDS:
        start = 0
        while True:
            idx = line.find(kw, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + 1
    return sorted(positions)


def _prohibited_heads_in_line(line: str) -> Set[str]:
    """1 行から禁止されたコマンドの照合 spec を抽出する。

    禁止対象のコマンドは禁止キーワードの**前**の backtick トークンに現れる
    （日本語: `` `cd` 禁止 `` / 英語: `` `pkill` is MUST NOT ``）。キーワードより後ろの
    backtick トークンは代替手段（`` `git -C` を使う `` 等）の可能性が高いため除外する。
    これにより「禁止行に同居する推奨コマンド」の誤検出を防ぐ（#522-3 FP 対策）。

    複数語トークン（例: `` `git checkout -b` ``）は先頭語への縮約をしない（#222）。

    **判定に使う識別**: 禁止キーワードと backtick トークンの「文字位置の近さ」
    （キーワードと同じ文に属し、かつキーワードより前に閉じた backtick）。
    名前・意味ではない。**既知の種別のみ検出・迂回可能**であり、このレーンは
    advisory である。blocking 保証には使わない
    （`.claude/rules/no-denylist-checks.md`）。

    **作動しない条件（既知・`TestKnownLimitations` で現状挙動を固定）**:
    ①1 つの文で推奨と禁止を対比すると推奨側も拾う
    （「状態は `git status` で確認し、`rm` は禁止する。」）
    ②禁止対象をキーワードより後ろに書く文型を拾わない
    （「禁止コマンドは `rm` とする。」）
    ③キーワードが否定される文型を禁止として拾う（「`git status` は禁止対象外。」）
    ④英文の終止符を文境界として扱わない（ASCII `.` を境界に加えると
    `script.py` / `python3.12` のようなトークン内の点まで境界になり真陽性を落とす）。
    いずれも実 rules に該当 0 件（2026-09-04 実測）。

    行内でキーワードより前の backtick を**すべて**拾う旧実装は、rules が 1 項目 1 行の
    長文であるため、同じ行の無関係な推奨コマンドまで禁止扱いにしていた
    （実測 2026-09-03: 実 rules から抽出した 17 spec のうち 8 spec が誤り。
    `git log` / `git status` は「頭が作業先の実体（`git log`/`git status`）を見る」
    という**推奨**の記述から、646 文字離れた位置の「使わない」に引きずられていた）。
    """
    positions = _keyword_positions(line)
    if not positions:
        return set()
    spans = [(m.start(), m.end(), m.group(1)) for m in _BACKTICK_RE.finditer(line)]
    heads: Set[str] = set()
    for kw_pos in positions:
        # backtick の内側に現れたキーワードは、禁止の宣言ではなく引用（例:
        # 「`MUST NOT` という表現が残っていないか検索する」）。無効化する。
        if any(start <= kw_pos < end for start, end, _ in spans):
            continue
        # そのキーワードと同じ文に属する backtick を採用する
        # （「`rm` と `sudo` は禁止する」のように 1 つのキーワードが
        #  複数の対象を支配する並列文型を落とさないため）。
        sentence_start = max(
            [line.rfind(delim, 0, kw_pos) for delim in _SENTENCE_DELIMITERS] + [-1]
        )
        for start, _end, token in spans:
            if not (sentence_start < start and _end <= kw_pos):
                continue
            spec = _prohibited_spec(token)
            if spec:
                heads.add(spec)
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

    pattern のトークン列が prohibited_heads のいずれかと prefix 一致すれば
    rule_violation_observed レーンへ。そうでなければ skill_candidates として残す。
    入力リストは破壊しない。

    prohibited_heads の要素が複数語トークン（例: "git checkout -b"）の場合は
    トークン列の完全 prefix 一致でのみマッチし、先頭語だけの縮約一致はしない
    （#222）。単一語（例: "cd"）は従来通り先頭語一致で判定する。

    examples フィールドは truncate_example で 1 行・120 字に切り詰める（#555）。
    project_root が指定された場合、examples 内に別 PJ のパスが含まれる違反には
    cross_pj: true メタを付与する（#555）。

    Returns:
        {"skill_candidates": [...], "rule_violation_observed": [...]}
    """
    skill_candidates: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    for pat in repeating_patterns:
        head = _match_prohibited_spec(str(pat.get("pattern", "")), prohibited_heads)
        if head:
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

# 禁止コマンドの照合 spec 集合。単一語（例: "cd"）と複数語（例: "git checkout -b"）
# が混在しうる（#222）。複数語 spec は先頭語だけでなくトークン列の完全 prefix 一致
# でのみマッチさせ、無関係な同一コマンド名の呼び出し（例: "git status"）を
# 誤ってブロックしない。
PROHIBITED = {prohibited_set}


def _command_tokens(command):
    parts = command.strip().lstrip("$").strip().split()
    idx = 0
    while idx < len(parts) and parts[idx] in ("env", "sudo"):
        idx += 1
    return parts[idx:]


def check_command(command):
    tokens = _command_tokens(command)
    if not tokens:
        return None
    matched = ""
    for spec in PROHIBITED:
        spec_tokens = spec.split()
        if (
            spec_tokens
            and tokens[: len(spec_tokens)] == spec_tokens
            and len(spec_tokens) > len(matched.split())
        ):
            matched = spec
    if matched:
        return (
            f"`{{matched}}` は既存 rules で禁止されています。該当ルールの代替手段を使用してください。"
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


# ── hook 実在チェック・時間窓（#479） ──────────────────────────
#
# rule_violation_observed が「既に導入済みの enforcement hook をもう一度作れ」と
# 提案し続ける問題への対処。_enforcement_hook_script_path() が実在する場合、
# その hook の PROHIBITED 集合と mtime（導入日時の代理値）を使って violations を
# 3分岐する（issue 本文の分岐1〜3）。
#
# 時間窓の実装可否について（feasibility メモ）:
# tool_usage_analyzer.session_io.extract_tool_calls は Bash コマンド文字列のみを
# 集約し、JSONL レコードが持つ timestamp フィールド（例:
# "2026-08-16T00:09:33.280Z"）を破棄する（session_io.py の bash_commands.append(cmd)
# 一箇所のみで timestamp は読み捨て）。detect_repeating_commands（classify.py）は
# さらにコマンド文字列のみを集計するため、repeating_patterns には最初から timestamp
# が乗らない。generic pipeline（extract_tool_calls の返り値型・detect_repeating_commands
# の集計方式）を変更すると `test_tool_usage_analyzer_snapshot.py` が固定する API
# surface 契約に影響し、rule_violation 以外の全パターン種別（builtin_replaceable /
# sleep 等）にも波及するため、本 issue のスコープでは touch しない。
# 代わりに rule_violation_observed 専用の再スキャン（_iter_bash_commands_with_timestamps）
# を追加し、hook 実在が確認できた violated_command だけに限定して JSONL を
# 再読込し timestamp 付きで数え直す。既存の集計パイプラインには影響しない。


def _parse_hook_prohibited_set(hook_path: Path) -> Optional[Set[str]]:
    """hook スクリプトから `PROHIBITED = {...}` の集合を静的パースする。

    import して実行すると副作用（グローバル状態の変更）が起こりうるため、
    正規表現で `PROHIBITED = {...}` 行を抜き出し `ast.literal_eval` で評価する
    （import しない）。パースできない場合は None を返す（呼び出し側は fail-open）。
    """
    try:
        text = hook_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^PROHIBITED\s*=\s*(\{.*\})\s*$", text, re.MULTILINE)
    if not m:
        return None
    try:
        value = ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, set):
        return None
    return {str(v) for v in value}


def _parse_iso8601(ts: str) -> datetime:
    """ISO8601 文字列（`Z` 終端含む）を tz-aware datetime にパースする。

    `Z` 終端と `+00:00` 終端は同一 instant でも文字列としては不一致になる
    （辞書順比較の罠）ため、必ず datetime へパースしてから比較する。
    tz 情報が無い場合は UTC とみなす。
    """
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iter_bash_commands_with_timestamps(
    project_root: Path,
    *,
    projects_dir: Optional[Path] = None,
):
    """rule_violation_observed 専用の Bash コマンド再スキャン（timestamp 保持）。

    tool_usage_analyzer.session_io.extract_tool_calls と同じ session dir 解決を
    使うが、そちらは timestamp を破棄するため、この専用関数で `(command, timestamp)`
    を yield する。generic pipeline は変更しない（feasibility メモ参照）。
    """
    import json

    from tool_usage_analyzer.session_io import _resolve_session_dir

    session_dir = _resolve_session_dir(project_root, projects_dir=projects_dir)
    if session_dir is None:
        return
    for session_file in sorted(session_dir.glob("*.jsonl")):
        try:
            text = session_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            ts = rec.get("timestamp")
            content = rec.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                if item.get("name") != "Bash":
                    continue
                cmd = item.get("input", {}).get("command", "")
                if cmd:
                    yield cmd, ts


def _count_command_occurrences_since_bulk(
    heads: Set[str],
    since_dt: datetime,
    project_root: Path,
    *,
    projects_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """複数 head の since_dt 以降の観測回数を **1 パスのスキャンで** まとめて数える（#479 性能修正）。

    以前は violations のループ内で head ごとに `_iter_bash_commands_with_timestamps`
    をフル実行しており、同一 head の distinct pattern が複数存在すると同じ
    JSONL 群を何度も読み直す O(N_violations × セッションサイズ) になっていた
    （実測: 164 files / 269.2 MiB のセッション群で 1 スキャン 1.32s、"cd" の
    10 エントリで約13秒・2.7GB 相当の再読込）。呼び出し側は判定が必要な head
    集合を一度に渡し、ここで単一パスに集約する。

    timestamp を欠く/パース不能なレコードは安全側（除外）に倒す。
    """
    counts: Dict[str, int] = {h: 0 for h in heads}
    if not heads:
        return counts
    for cmd, ts in _iter_bash_commands_with_timestamps(project_root, projects_dir=projects_dir):
        if not ts:
            continue
        try:
            cmd_dt = _parse_iso8601(ts)
        except (ValueError, TypeError):
            continue
        if cmd_dt < since_dt:
            continue
        matched = _match_prohibited_spec(cmd, heads)
        if matched:
            counts[matched] += 1
    return counts


def _display_hook_path(hook_path: Path) -> str:
    """recommendation 表示用に hook_path をホームディレクトリ相対（`~/...`）へ畳む（#479 Must2）。

    絶対パスをそのまま recommendation に埋め込むと、この文字列が
    `phases_remediate.py` 経由で GitHub issue 本文に載る際に個人特定可能な
    ローカルパス（`/Users/<ユーザー名>/...`）が外部流出する
    （グローバル rule `no-personal-dir-in-external-artifacts`）。

    実体は ``rl_common.path_display.home_relative_display`` に集約（#467
    フォローアップで discover 側にも同じ需要が生じ、判定重複を避けるため単一ソース化）。
    """
    from rl_common.path_display import home_relative_display  # noqa: PLC0415

    return home_relative_display(hook_path)


def _merge_still_violated_entries(
    head: str,
    entries: List[Dict[str, Any]],
    post_count: int,
    hook_path: Path,
    since_dt: datetime,
) -> Dict[str, Any]:
    """同一 head の複数 violation エントリを 1 件に畳む（#479 Must3）。

    count を head 単位の値（post_count）に更新したことで、count の意味が
    pattern 単位 → head 単位に変わった。畳まずに複数エントリへ同じ count を
    複製すると、読み手には合計値に見えてしまう誤読を作り込む。同一 head の
    エントリは 1 件へマージし、examples は先着順・重複除去で最大 3 件まで残す
    （partition_rule_violations の既存方針 `key_examples[key]) < 3` に合わせる）。
    """
    merged_examples: List[str] = []
    cross_pj = False
    for entry in entries:
        for ex in entry.get("examples", []):
            if ex not in merged_examples and len(merged_examples) < 3:
                merged_examples.append(ex)
        if entry.get("cross_pj"):
            cross_pj = True

    display_path = _display_hook_path(hook_path)
    merged: Dict[str, Any] = {
        "pattern": head,
        "count": post_count,
        "violated_command": head,
        "reason": "enforced_but_still_violated",
        "examples": merged_examples,
        "recommendation": (
            f"`{head}` は enforcement hook（{display_path}）導入済みだが、導入後も"
            f" {post_count} 回観測。新しい hook を作るのではなく、既存 hook の"
            "判定範囲（分割ロジック・パターン漏れ等）を点検すること。"
        ),
        "hook_enforced_since": since_dt.isoformat(),
    }
    if cross_pj:
        merged["cross_pj"] = True
    return merged


def apply_hook_enforcement_status(
    violations: List[Dict[str, Any]],
    *,
    hook_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
    projects_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """既に導入済みの enforcement hook の状態に基づき violations を3分岐する（#479）。

    1. hook_path が実在しない → 変更なし（reason=rule_installed_but_not_enforced のまま）。
       ※ この exists() チェックは、hook_path が実在しない場合に
       `_parse_hook_prohibited_set` の `read_text()` が OSError で fail-open するのと
       振る舞い上は同値（equivalent）だが、意図を明示するため明示的に残す。
    2. hook_path が実在し、violated_command が hook の PROHIBITED に含まれ、かつ
       hook 導入後（mtime 以降）の観測が 0 → 提案から除外する（対処済み）。
    3. hook_path が実在し PROHIBITED に含まれるが、hook 導入後も観測がある →
       同一 head のエントリを 1 件に畳み、reason を "enforced_but_still_violated" に
       変更し、count を導入後の観測数（head 単位）に更新、recommendation を
       「hook を作れ」でなく「導入済み hook の判定範囲を点検せよ」に差し替える。

    時間窓の判定は head ごとに個別スキャンせず、対象 head 集合を一括して
    1 パスでスキャンする（`_count_command_occurrences_since_bulk`・#479 性能修正）。

    hook スクリプトのパース失敗・project_root 未指定（時間窓判定不能）の場合は
    fail-open（変更なし）で返す。入力リストは破壊しない。
    """
    if hook_path is None:
        hook_path = _enforcement_hook_script_path()
    if not violations:
        return list(violations)
    try:
        hook_exists = hook_path.exists()
    except OSError:
        hook_exists = False
    if not hook_exists:
        return list(violations)

    prohibited_set = _parse_hook_prohibited_set(hook_path)
    if prohibited_set is None:
        return list(violations)

    try:
        hook_mtime = hook_path.stat().st_mtime
    except OSError:
        return list(violations)
    since_dt = datetime.fromtimestamp(hook_mtime, tz=timezone.utc)

    # violated_command が prohibited_set に含まれるものだけを対象に集約する。
    candidates_by_head: Dict[str, List[Dict[str, Any]]] = {}
    for viol in violations:
        head = str(viol.get("violated_command", ""))
        if head in prohibited_set:
            candidates_by_head.setdefault(head, []).append(viol)

    counts: Optional[Dict[str, int]] = None
    if candidates_by_head and project_root is not None:
        counts = _count_command_occurrences_since_bulk(
            set(candidates_by_head.keys()), since_dt, project_root, projects_dir=projects_dir,
        )

    out: List[Dict[str, Any]] = []
    merged_heads: Set[str] = set()
    for viol in violations:
        head = str(viol.get("violated_command", ""))
        if head not in prohibited_set:
            out.append(viol)
            continue
        if counts is None:
            # 時間窓判定不能（project_root 未指定等）→ false negative を避け変更なし
            out.append(viol)
            continue
        if head in merged_heads:
            continue  # 同一 head は初出時に1件へ畳み済み
        merged_heads.add(head)
        post_count = counts.get(head, 0)
        if post_count == 0:
            continue  # 対処済み → 提案から除外
        out.append(
            _merge_still_violated_entries(
                head, candidates_by_head[head], post_count, hook_path, since_dt,
            )
        )
    return out


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

    reason が "enforced_but_still_violated"（#479・apply_hook_enforcement_status が
    付与）の違反は昇格対象から除外する。これは既に enforcement hook が導入済みで
    その PROHIBITED に含まれる違反であり、ここで再度 scaffold すると同じ hook を
    もう一度作れという stale 提案（#479 が直した症状そのもの）を、この昇格経路で
    再発させてしまうため。

    入力は破壊しない。決定論・LLM 非依存。

    Returns:
        tool_usage_hook_candidate issue のリスト（昇格対象が無ければ空リスト）。
    """
    # 遅延 import で循環依存を避ける（issue_schema は rule_violation_lane を import しない）。
    from issue_schema import make_hook_candidate_issue

    eligible: List[Dict[str, Any]] = []
    for viol in rule_violations or []:
        if viol.get("reason") == "enforced_but_still_violated":
            continue
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


def rule_violation_suppression_issue(violation: Dict[str, Any]) -> Dict[str, Any]:
    """rule_violation_observed 項目を suppression_ledger 用の安定 identity issue に変換する（#103）。

    rule_violation_observed は `{pattern, count, examples, violated_command, ...}` 形で、
    remediation の issue 形（type/file/detail）を持たない。そのまま suppression_ledger.dedup_key に
    渡すと type/file/detail が空になり全項目が同一キーへ collapse してしまう。

    `violated_command`（禁止コマンド head。例: "cd"）を identity の核にすることで、
    「同じ禁止コマンドの再観測は同じ dismiss で抑制する」PJ スコープの意図的運用フラグを実現する。
    決定論・LLM 非依存。
    """
    head = str(
        violation.get("violated_command")
        or _command_head(str(violation.get("pattern", "")))
    )
    return {
        "type": "rule_violation_observed",
        "file": "",
        "detail": {"target": head},
    }


def default_rule_dirs(project_root: Path) -> List[Path]:
    """突合対象の rules ディレクトリ（global ~/.claude/rules + PJ .claude/rules）を返す。"""
    return [
        Path.home() / ".claude" / "rules",
        Path(project_root) / ".claude" / "rules",
    ]
