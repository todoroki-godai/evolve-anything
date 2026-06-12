"""Layer 3: SKILL.md コードブロック抽出 + 安全分類実行（#496）。

全 ``skills/*/SKILL.md`` から fenced code block（python/bash）を抽出し、ユーザーと
同じ素の起動経路（conftest の sys.path 補完 / HOME 隔離の下駄なし）で検証する。
配線の死・sys.path 不足・削除済み CLI 参照（#479 #486 #487 #488 #495）を捕捉する。

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


def _extract_imports(code: str) -> List[Dict[str, str]]:
    """python コードから import 文を抽出し ``[{"module": str, "stmt": str}]`` を返す。

    割り切り（#496）: ``from X import Y`` は ``import X`` に正規化してモジュール解決のみを
    検証する。``Y`` がモジュール属性として実在しない（誤名・削除済みシンボル）ケースは
    見逃す。代わりに複数行括弧 import（``from X import (\\n a,\\n b,\\n)``）を verbatim 実行した
    ときの構文不完全 SyntaxError 偽陽性を構造的に防ぐ。sys.path 不足・モジュール不在
    （#487/#488 型）は ``import X`` で十分捕捉できる。
    """
    imports: List[Dict[str, str]] = []
    for raw in code.splitlines():
        m = _IMPORT_RE.match(raw)
        if not m:
            continue
        if m.group(1):  # from X import ...
            mod = m.group(1)
            imports.append({"module": mod, "stmt": f"import {mod}"})
        else:  # import a, b
            for mod in m.group(2).split(","):
                mod = mod.strip().split(" as ")[0].strip()
                if mod:
                    imports.append({"module": mod, "stmt": f"import {mod}"})
    return imports


def _extract_syspath_setup(code: str) -> List[str]:
    """ブロックが自前で行う sys.path 設定 + その前提行（import os/sys, _root 代入）を返す。

    ``sys.path.insert`` を含む行と、その行が依存する ``import os/sys`` / ``_root = ...`` /
    ``${CLAUDE_PLUGIN_ROOT}`` 解決行を verbatim で集める。これを import 検証の ``-c`` に
    前置することで「ブロックが宣言したパスだけ」で import を試す（ゲートは scripts/lib を
    勝手に足さない）。sys.path を一切足さないブロック（#487 agent-brushup 型）は import 失敗。
    """
    setup: List[str] = []
    for raw in code.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if (
            "sys.path" in stripped
            or re.match(r"^import\s+(os|sys)(\s|,|$)", stripped)
            or re.match(r"^(import os, sys|import sys, os)", stripped)
            or re.match(r"^_?\w*root\w*\s*=", stripped, re.IGNORECASE)
        ):
            setup.append(stripped)
    return setup


def classify_block(lang: str, code: str) -> Dict[str, Any]:
    """コードブロックを安全分類する。

    返り値: ``{"mode": "import_check"|"run"|"existence_only", ...}``
      - import_check: python の import 文を import 検証に変換
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
            # ブロックが自前で設定する sys.path 行を保存する。import 検証時は
            # この setup だけを前置し、ゲート側から scripts/lib を勝手に注入しない
            # ＝ユーザーと同じ素の起動経路を再現する（sys.path 不足 #487 を捕捉する核）。
            cls["setup"] = _extract_syspath_setup(code)
        else:
            cls["mode"] = "existence_only"
        return cls

    # bash
    commands = _extract_bash_commands(code)
    cls["commands"] = commands
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
    in_quote = False  # 複数行クォート文字列（python3 -c "..." の本文）内かどうか
    for line in _split_logical_lines(code):
        started_in_quote = in_quote
        # この論理行で " のパリティを更新する（行内の文字列開閉を追跡）。
        if line.count('"') % 2 == 1:
            in_quote = not in_quote
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
            **_run_import_check(cls["imports"], sys_path_dirs, cls.get("setup", []), repo_root),
        }
    if cls["mode"] == "run":
        return {**base, **_run_safe_bash(code, repo_root)}
    # existence_only
    return {**base, **_run_existence_check(cls, code, repo_root)}


def _run_import_check(
    imports: List[Dict[str, str]],
    sys_path_dirs: List[Path],
    setup: List[str] | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """import 文を素の python subprocess で import 検証する（conftest 下駄なし）。

    ``setup`` はブロックが自前で行う sys.path 設定行（``${CLAUDE_PLUGIN_ROOT}`` は展開済み）。
    これを import の前に実行することで「ブロックが宣言したパス」だけで import を試す
    （ユーザーと同じ起動経路の再現）。``sys_path_dirs`` は明示的に渡された追加パスのみ
    （layer3 は空を渡す＝勝手に scripts/lib を足さない → #487 の sys.path 不足を捕捉）。
    """
    setup_lines = list(setup or [])
    if repo_root is not None:
        setup_lines = [_expand_plugin_root(s, repo_root) for s in setup_lines]
    body = "\n".join(setup_lines + [imp["stmt"] for imp in imports])
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
        return {"status": "pass", "detail": f"imported: {[i['module'] for i in imports]}"}
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
