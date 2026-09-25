"""Layer 3: SKILL.md / references/*.md コードブロック抽出 + 安全分類実行（#496）。

全 ``skills/*/SKILL.md`` と ``skills/*/references/*.md`` から fenced code block
（python/bash）を抽出し、ユーザーと同じ素の起動経路（conftest の sys.path 補完 /
HOME 隔離の下駄なし）で検証する。配線の死・sys.path 不足・削除済み CLI 参照を捕捉する。

**安全分類が最重要**: 書込・破壊系を実行しない。分類ルール:
  - python: import 文を抽出し import 検証に変換（import 以外の副作用行は実行しない）。
    import が 1 件も無ければ existence_only（実行せず構文/存在のみ）。
  - bash: ``--help`` / ``--dry-run`` 付き CLI はそのまま実行可。それ以外（引数なし実行・
    書込系 rm/git/mv・プレースホルダ）は「実行せず存在検証のみ」（コマンド/スクリプトの実在）。

``${CLAUDE_PLUGIN_ROOT}`` はリポジトリ root に展開して検証する。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# fence 言語の正規化。これ以外（json/text/md 等）は抽出対象外。
_LANG_NORMALIZE = {
    "python": "python", "py": "python", "python3": "python",
    "bash": "bash", "sh": "bash", "shell": "bash", "console": "bash", "zsh": "bash",
}

# プレースホルダ検出（置換不能なら存在検証のみ）。
_PLACEHOLDER_RE = re.compile(r"<[a-zA-Z0-9_\- ]+>|\{[a-zA-Z0-9_\-]+\}")

# bash で「実行して安全」と判断するフラグ。
_SAFE_BASH_FLAGS = ("--help", "-h", "--dry-run", "--version")

# bash で存在検証に落とす危険トークン（書込/破壊/状態変更）。
_DANGEROUS_BASH_TOKENS = (
    "rm", "mv", "cp", "git", "mkdir", "rmdir", "touch", "chmod", "chown",
    ">", ">>", "tee", "sed -i", "claude", "gh", "curl", "wget", "pip",
    "npm", "kill", "pkill", "ln",
)

# python import 行を拾う正規表現。
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import\s+.+|import\s+([\w\.,\s]+))\s*$")


def extract_code_blocks(skill_md: Path) -> List[Dict[str, Any]]:
    """SKILL.md から python/bash の fenced code block を抽出する。

    返り値: ``[{"lang": "python"|"bash", "code": str, "line": int, "source": str}]``
    （``line`` は fence 開始行の 1-origin 行番号、``source`` は SKILL.md パス）。
    """
    skill_md = Path(skill_md)
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks: List[Dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^(\s*)```([A-Za-z0-9_+-]*)\s*$", line)
        if not m:
            i += 1
            continue
        indent, lang_raw = m.group(1), m.group(2).lower()
        # fence 閉じを探す（同じインデント or 任意の ``` 行）。
        body: List[str] = []
        j = i + 1
        while j < n and not re.match(r"^\s*```\s*$", lines[j]):
            body.append(lines[j])
            j += 1
        lang = _LANG_NORMALIZE.get(lang_raw)
        if lang:
            blocks.append(
                {
                    "lang": lang,
                    "code": "\n".join(body),
                    "line": i + 1,
                    "source": str(skill_md),
                }
            )
        i = j + 1
    return blocks


def _has_placeholder(code: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(code))


def _import_entries_for_line(raw: str) -> List[Dict[str, str]]:
    """1行が import 文なら ``[{"module": str, "stmt": str}]`` を返す（複数行 import 対応）。

    ``from X import Y``: 同一行で閉じている（括弧が不均衡でない）かつ placeholder を
    含まなければ ``stmt`` を **verbatim**（元の行そのまま）にする＝``Y`` が実在しない
    typo も import 実行時に検出できる。複数行 bracket import の開き行（``from X import (``）
    と placeholder を含む行は、従来どおり ``import X`` に正規化してモジュール解決のみを
    検証する（#496 の割り切りを維持: verbatim 実行すると構文不完全 SyntaxError になる）。
    """
    m = _IMPORT_RE.match(raw)
    if not m:
        return []
    if m.group(1):  # from X import ...
        mod = m.group(1)
        stripped = raw.strip()
        unbalanced = stripped.count("(") > stripped.count(")")
        if unbalanced or _has_placeholder(stripped):
            return [{"module": mod, "stmt": f"import {mod}"}]
        return [{"module": mod, "stmt": stripped}]
    # import a, b
    entries: List[Dict[str, str]] = []
    for mod in m.group(2).split(","):
        mod = mod.strip().split(" as ")[0].strip()
        if mod:
            entries.append({"module": mod, "stmt": f"import {mod}"})
    return entries


def _extract_imports(code: str) -> List[Dict[str, str]]:
    """python コードから import 文を抽出し ``[{"module": str, "stmt": str}]`` を返す（表示/検証用）。"""
    imports: List[Dict[str, str]] = []
    for raw in code.splitlines():
        imports.extend(_import_entries_for_line(raw))
    return imports


_SETUP_LINE_RE_LIST = (
    re.compile(r"^import\s+(os|sys)(\s|,|$)"),
    re.compile(r"^(import os, sys|import sys, os)"),
    re.compile(r"^_?\w*root\w*\s*=", re.IGNORECASE),
)


def _is_setup_line(stripped: str) -> bool:
    """sys.path 設定行（``sys.path.insert`` / ``import os,sys`` / ``_root = ...``）か判定する。"""
    if "sys.path" in stripped:
        return True
    return any(p.match(stripped) for p in _SETUP_LINE_RE_LIST)


def _extract_exec_lines(code: str) -> List[str]:
    """import 文と sys.path 設定行を、元のソース内の相対順序を保ったまま抽出する。

    setup 行を先に・import 文を後に固定順で連結すると、setup 行が import 対象の
    モジュールを参照する書き方（例: ``import pathlib`` の後で
    ``plugin_root = pathlib.Path(...)`` を setup として拾ってしまうケース）で
    import 未実行のうちに参照して ``NameError`` になる偽陽性を生む
    （skills/implement/references/telemetry.md で実測）。1行ずつ走査し、import 文は
    ``_import_entries_for_line`` と同じ変換規則（#496 の複数行 bracket 正規化を含む）を、
    setup 行はそのまま採用し、元の出現順を維持したリストを返す。
    """
    lines: List[str] = []
    for raw in code.splitlines():
        entries = _import_entries_for_line(raw)
        if entries:
            lines.extend(e["stmt"] for e in entries)
            continue
        stripped = raw.strip()
        if stripped and _is_setup_line(stripped):
            lines.append(stripped)
    return lines


def classify_block(lang: str, code: str) -> Dict[str, Any]:
    """コードブロックを安全分類する。

    返り値: ``{"mode": "import_check"|"run"|"existence_only", ...}``
      - import_check: python の import 文（+ sys.path 設定行）を元の順序のまま検証
      - run: bash の --help/--dry-run 付き安全コマンドを実行
      - existence_only: 実行せず存在検証のみ
    """
    cls: Dict[str, Any] = {"mode": "existence_only"}
    if _has_placeholder(code):
        cls["has_placeholder"] = True

    if lang == "python":
        imports = _extract_imports(code)
        if imports:
            cls["mode"] = "import_check"
            cls["imports"] = imports
            cls["exec_lines"] = _extract_exec_lines(code)
        else:
            cls["mode"] = "existence_only"
        return cls

    # bash
    commands = _extract_bash_commands(code)
    cls["commands"] = commands
    # 埋め込み python（``python3 -c "..."``/``'...'``/heredoc 本文/セミコロン連結の
    # 単一行含む）の import 文も python fence と同じ import_check 経路に通す。
    py_source = _extract_embedded_python_source(code)
    embedded_imports = _extract_imports(py_source) if py_source else []
    if embedded_imports:
        cls["mode"] = "import_check"
        cls["imports"] = embedded_imports
        # ``PYTHONPATH=`` 前置は bash レベルの設定なので python 本体より前に置く。
        # プレースホルダは import 文自体に含まれない限り無視する（python fence と同じ
        # 割り切り: 実例は import 行と無関係な引数にだけ placeholder があった）。
        cls["exec_lines"] = _extract_env_pythonpath_setup(code) + _extract_exec_lines(py_source)
        return cls
    if cls.get("has_placeholder"):
        cls["mode"] = "existence_only"
        return cls
    if _is_safe_bash(code):
        cls["mode"] = "run"
    else:
        cls["mode"] = "existence_only"
    return cls


# 検証対象にしないシェルキーワード / builtin（コマンド実在チェックの FP を避ける）。
_SHELL_NONCOMMANDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "in", "function", "return", "break", "continue", "exit", "EOF",
    "cd", "echo", "export", "set", "setopt", "eval", "source", "read", "local",
    "true", "false", "test", "[", "[[", "]]", "}", "{",
}

# 「検証可能な裸の CLI 名」の形（bin/ や PATH に実在確認できるもの）。
_BARE_CMD_RE = re.compile(r"^[a-zA-Z][\w-]*$")


def _split_logical_lines(code: str) -> List[str]:
    """行末 ``\\`` 継続を結合し、論理コマンド行のリストを返す（コメント除外）。"""
    logical: List[str] = []
    buf = ""
    for raw in code.splitlines():
        stripped = raw.rstrip()
        line = raw.strip()
        if not line or line.startswith("#"):
            if buf:
                logical.append(buf)
                buf = ""
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
            continue
        buf += line
        logical.append(buf)
        buf = ""
    if buf:
        logical.append(buf)
    return logical


def _extract_bash_commands(code: str) -> List[str]:
    """論理コマンド行の先頭トークン（裸の CLI 名のみ）を拾う。

    継続行（``--requests ...``）・シェルキーワード・フラグ・代入・heredoc 本文は除外し、
    実在検証して意味のある裸のコマンド名だけを返す（FP を構造的に抑制）。
    """
    cmds: List[str] = []
    # 複数行クォート文字列（``python3 -c "..."`` / ``python3 -c '...'`` の本文）内かどうかを
    # ダブル・シングルそれぞれ独立に追跡する。bash の埋め込み python は single-quote 区切りも
    # 多用される（report-feedback/SKILL.md #31）。どちらの区切りで開いていても本文行
    # （from/import/代入行）を裸コマンドとして拾わない。
    in_dquote = False
    in_squote = False
    for line in _split_logical_lines(code):
        started_in_quote = in_dquote or in_squote
        # この論理行でクォートのパリティを更新する（行内の文字列開閉を追跡）。
        # 既に一方のクォート区間内なら、もう片方の引用符は文字列リテラルの一部なので
        # パリティ更新の対象にしない（例: single-quote 中の " は python の文字列リテラル）。
        if not in_squote and line.count('"') % 2 == 1:
            in_dquote = not in_dquote
        if not in_dquote and line.count("'") % 2 == 1:
            in_squote = not in_squote
        # クォート文字列の途中で始まる行（埋め込み python/heredoc 本文）は検証対象外。
        if started_in_quote:
            continue
        tokens = line.split()
        idx = 0
        skipped_subshell = False
        # 環境変数代入プレフィクス（FOO=bar cmd）を飛ばす。代入値が ``$(...)`` の
        # サブシェル（``BRANCH=$(git rev-parse ...)``）なら head の特定が不安定なので
        # 行ごと検証対象外（rev-parse 等を拾う FP を防ぐ）。引数中の ``"$(pwd)"`` は
        # head が clean なら問題ないので、ここでは代入プレフィクス内だけを見る。
        while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
            if "$(" in tokens[idx] or "`" in tokens[idx]:
                skipped_subshell = True
            idx += 1
        if skipped_subshell or idx >= len(tokens):
            continue
        head = tokens[idx]
        # head 自体が ``$(...)`` 起点ならコマンド名でない。
        if "$(" in head or head.startswith("`"):
            continue
        if head in _SHELL_NONCOMMANDS or head.startswith("-"):
            continue
        # 裸の CLI 名のみ実在検証（パス区切りや変数展開・引用符を含むものは
        # 別経路（.py スクリプトパス検証）か existence skip に委ねる）。
        if _BARE_CMD_RE.match(head):
            cmds.append(head)
    return cmds


# python3 -c "..." / python -c '...' の「単一行で開いて閉じる」形（セミコロン連結）を検出する。
_SINGLE_LINE_PY_C_RE = re.compile(r"python3?\s+-c\s+(?:\"([^\"]*)\"|'([^']*)')")

# ``python3 - <<'EOF' ... EOF`` / ``python3 <<EOF ... EOF`` 形の heredoc 開始行を検出する。
_HEREDOC_OPEN_RE = re.compile(r"^\s*.*\bpython3?\b.*<<-?\s*['\"]?(\w+)['\"]?\s*$")


def _extract_heredoc_python_lines(code: str) -> List[str]:
    """``python3 - <<'EOF' ... EOF`` 形の heredoc 本文を python ソース行として返す。"""
    lines_out: List[str] = []
    all_lines = code.splitlines()
    i, n = 0, len(all_lines)
    while i < n:
        m = _HEREDOC_OPEN_RE.match(all_lines[i])
        if not m:
            i += 1
            continue
        delim = m.group(1)
        i += 1
        while i < n and all_lines[i].strip() != delim:
            lines_out.append(all_lines[i])
            i += 1
        i += 1  # 終端行（delim 単独行）をスキップ
    return lines_out


def _extract_single_line_py_c_pseudo_lines(code: str) -> List[str]:
    """単一行で開閉する ``python3 -c "..."`` の中身を ``;`` で分割し疑似行として返す。

    ``import os, sys; sys.path.insert(0, X); from Y import z`` のようにセミコロン連結
    された一行は、複数行の埋め込み python とは異なり anchored な import 検出（1行=1文）
    に自然には乗らない。中身を ``;`` で分割して1文1行相当に正規化することで、既存の
    import/setup 検出をそのまま再利用する。**既知の限界**: 文字列リテラル内の ``;`` は
    区切りと誤認しうる（この分割は構文木でなく単純な文字列分割のため）。
    """
    pseudo: List[str] = []
    for line in code.splitlines():
        for m in _SINGLE_LINE_PY_C_RE.finditer(line):
            content = m.group(1) if m.group(1) is not None else m.group(2)
            if content is None:
                continue
            for stmt in content.split(";"):
                stmt = stmt.strip()
                if stmt:
                    pseudo.append(stmt)
    return pseudo


def _extract_embedded_python_source(code: str) -> str:
    """bash ブロック中に埋め込まれた python ソースを3経路から isolate して連結する。

    ①``python3 -c "..."``/``'...'`` の複数行本文（引用符が「開いたまま」の行。
    ``_extract_bash_commands`` と同じ per-line クォート parity 追跡を再利用）
    ②``python3 - <<'EOF' ... EOF`` 形の heredoc 本文
    ③単一行で開閉する ``python3 -c "..."`` の中身（``;`` 区切りで疑似行化）
    bash 構文の行（例: ``SLUG="$(... python3 -c "..." ...)"`` の一行完結形そのもの）は
    ①の「開始時点で引用符内」判定では対象にならないが、③で中身だけを別途拾う。
    """
    collected: List[str] = []
    in_dquote = False
    in_squote = False
    for line in _split_logical_lines(code):
        started_in_quote = in_dquote or in_squote
        if not in_squote and line.count('"') % 2 == 1:
            in_dquote = not in_dquote
        if not in_dquote and line.count("'") % 2 == 1:
            in_squote = not in_squote
        if started_in_quote:
            collected.append(line)
    collected.extend(_extract_heredoc_python_lines(code))
    collected.extend(_extract_single_line_py_c_pseudo_lines(code))
    return "\n".join(collected)


# ``PYTHONPATH=<path> python3 -c`` 前置（report-feedback/SKILL.md #31 の慣習）を検出する。
_ENV_PYTHONPATH_PREFIX_RE = re.compile(
    r"(?m)^\s*PYTHONPATH=(\"(?P<dq>[^\"]*)\"|'(?P<sq>[^']*)'|(?P<bare>\S+))\s+python3?\s+-c\b"
)


def _extract_env_pythonpath_setup(code: str) -> List[str]:
    """``PYTHONPATH=<path> python3 -c`` 前置を ``sys.path.insert`` 相当の setup 行に変換する。

    シェル環境変数で解決パスを渡す書き方は ``_extract_exec_lines`` の
    ``sys.path`` 行検出（引用符内の python 行が対象）では拾えないため、別経路で補う。
    """
    setup: List[str] = []
    for m in _ENV_PYTHONPATH_PREFIX_RE.finditer(code):
        value = m.group("dq") if m.group("dq") is not None else (m.group("sq") if m.group("sq") is not None else m.group("bare"))
        if not value:
            continue
        for entry in value.split(os.pathsep):
            entry = entry.strip()
            if entry:
                setup.append(f"sys.path.insert(0, {entry!r})")
    if setup:
        setup = ["import sys"] + setup
    return setup


def _is_safe_bash(code: str) -> bool:
    """bash ブロックが「実行して安全」か判定する。

    全行が単一の安全フラグ付きコマンドで、危険トークンを含まない場合のみ True。
    複数行や pipe/redirect が混じる場合は保守的に False（existence_only に落とす）。
    """
    nonempty = [l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("#")]
    if len(nonempty) != 1:
        return False
    line = nonempty[0]
    # 危険トークン（語境界）を含むなら不可
    for tok in _DANGEROUS_BASH_TOKENS:
        if re.search(r"(^|\s)" + re.escape(tok) + r"(\s|$)", line):
            return False
    if "|" in line or "$(" in line or "`" in line:
        return False
    # 安全フラグが含まれていれば実行可
    return any(flag in line.split() for flag in _SAFE_BASH_FLAGS)


# --- 実行 ----------------------------------------------------------------------

def _expand_plugin_root(code: str, repo_root: Path) -> str:
    return code.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root)).replace(
        "$CLAUDE_PLUGIN_ROOT", str(repo_root)
    )


def run_block(
    block: Dict[str, Any],
    repo_root: Path,
    sys_path_dirs: List[Path],
) -> Dict[str, Any]:
    """1 ブロックを分類し検証実行する。

    ``sys_path_dirs`` は import 検証時に PYTHONPATH に積むディレクトリ群（ユーザーと同じ
    素の起動経路を再現するため、conftest の下駄ではなく SKILL.md / bin が設定する分だけ）。
    返り値: ``{"status": "pass"|"fail"|"skip", "mode": ..., "detail": str, "line": int}``
    """
    repo_root = Path(repo_root)
    code = _expand_plugin_root(block["code"], repo_root)
    cls = classify_block(block["lang"], code)
    base = {"mode": cls["mode"], "line": block.get("line", 0), "source": block.get("source", "")}

    if cls["mode"] == "import_check":
        return {
            **base,
            **_run_import_check(cls.get("exec_lines", []), sys_path_dirs, repo_root),
        }
    if cls["mode"] == "run":
        return {**base, **_run_safe_bash(code, repo_root)}
    # existence_only
    return {**base, **_run_existence_check(cls, code, repo_root)}


def _run_import_check(
    exec_lines: List[str],
    sys_path_dirs: List[Path],
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """import 文（+ sys.path 設定行）を、元の相対順序のまま素の python subprocess で検証する
    （conftest 下駄なし）。

    ``exec_lines`` は ``classify_block`` が元のソース順のまま組み立てた実行行（``${CLAUDE_PLUGIN_ROOT}``
    は展開済み）。setup を import より先に固定する再配置はしない（setup 行が import 対象を
    参照する書き方で NameError の偽陽性を生むため）。``sys_path_dirs`` は明示的に渡された
    追加パスのみ（layer3 は空を渡す＝勝手に scripts/lib を足さずユーザーと同じ起動経路を
    再現し、sys.path 不足を捕捉する）。
    """
    lines = list(exec_lines)
    if repo_root is not None:
        lines = [_expand_plugin_root(s, repo_root) for s in lines]
    body = "\n".join(lines)
    pythonpath = os.pathsep.join(str(p) for p in sys_path_dirs)
    env = dict(os.environ)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    else:
        env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", body],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if proc.returncode == 0:
        return {"status": "pass", "detail": f"ok: {len(lines)} 行実行"}
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    return {"status": "fail", "detail": err[-1] if err else "import failed"}


def _run_safe_bash(code: str, repo_root: Path) -> Dict[str, Any]:
    """--help/--dry-run 付き安全コマンドを実行する（cwd=repo_root, bin を PATH 先頭に）。"""
    line = next(l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("#"))
    env = dict(os.environ)
    env["PATH"] = str(repo_root / "bin") + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            line,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": f"timeout: {line}"}
    if proc.returncode == 0:
        return {"status": "pass", "detail": f"ran: {line}"}
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    return {"status": "fail", "detail": f"exit {proc.returncode}: {err[-1] if err else line}"}


def _resolve_command(cmd: str, repo_root: Path) -> Optional[str]:
    """コマンドが repo_root/bin か PATH に実在するかを返す（パスは絶対）。"""
    bin_path = repo_root / "bin" / cmd
    if bin_path.exists():
        return str(bin_path)
    found = shutil.which(cmd)
    return found


def _run_existence_check(cls: Dict[str, Any], code: str, repo_root: Path) -> Dict[str, Any]:
    """実行せず、参照されるコマンド/スクリプトパスの実在のみ検証する。"""
    missing: List[str] = []
    checked: List[str] = []
    # bash: コマンド名の実在
    for cmd in cls.get("commands", []):
        if _PLACEHOLDER_RE.search(cmd):
            continue
        # 絶対/相対パス（スクリプト直叩き）はファイル存在で判定
        if "/" in cmd:
            p = (repo_root / cmd) if not cmd.startswith("/") else Path(cmd)
            checked.append(cmd)
            if not p.exists():
                missing.append(cmd)
            continue
        checked.append(cmd)
        if _resolve_command(cmd, repo_root) is None:
            missing.append(cmd)
    # プラグイン内スクリプト参照（${CLAUDE_PLUGIN_ROOT}/scripts/...py 等）のみ実在検証する。
    # /tmp/foo.py（生成物の出力先）や bare な example.py は参照でなく成果物なので除外（FP 対策）。
    for m in re.finditer(r"([\w./-]+\.py)\b", code):
        path = m.group(1)
        if _PLACEHOLDER_RE.search(path):
            continue
        # ディレクトリ成分が無い bare ファイル名（生成物/例示）は検証しない。
        if "/" not in path:
            continue
        # repo 内の plugin スクリプトを指す参照（scripts/ または skills/ 配下）のみ対象。
        if not re.search(r"(^|/)(scripts|skills)/", path):
            continue
        p = (repo_root / path) if not path.startswith("/") else Path(path)
        checked.append(path)
        if not p.exists():
            missing.append(path)

    if not checked:
        return {"status": "skip", "detail": "no verifiable command/path"}
    if missing:
        return {"status": "fail", "detail": f"missing: {sorted(set(missing))}"}
    return {"status": "pass", "detail": f"exists: {sorted(set(checked))}"}
