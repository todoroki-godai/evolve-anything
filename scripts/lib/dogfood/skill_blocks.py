"""Layer 3: SKILL.md / references/*.md コードブロック抽出 + 安全分類実行（#496）。

全 ``skills/*/SKILL.md`` と ``skills/*/references/*.md`` から fenced code block
（python/bash）を抽出し、ユーザーと同じ素の起動経路（conftest の sys.path 補完 /
HOME 隔離の下駄なし）で検証する。配線の死・sys.path 不足・削除済み CLI 参照を捕捉する。

**安全分類が最重要**: 書込・破壊系を実行しない。分類ルール:
  - python: import 文を抽出し import 検証に変換（import 以外の副作用行は実行しない）。
    import が 1 件も無ければ existence_only（実行せず構文/存在のみ）。
  - bash: ``python3 -c <arg>`` / ``python3 - <<DELIM ... DELIM`` の呼び出しは、呼び出し
    ごとに独立して python の import 検証へ回す（他の呼び出しの setup・失敗を混ぜない）。
    ``--help`` / ``--dry-run`` 付き CLI はそのまま実行可。それ以外（引数なし実行・
    書込系 rm/git/mv・プレースホルダ）は「実行せず存在検証のみ」。**コマンド/スクリプト
    の存在検証は import 検証の成否と独立に常に行い、いずれかが fail なら全体を fail にする。**

``${CLAUDE_PLUGIN_ROOT}`` はリポジトリ root に展開して検証する。
"""
from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# --- AST ベースの import/setup 文選別 -------------------------------------------
#
# 旧実装は「行が import 文の形か」を正規表現で判定していたため、①import が引用符の
# 開始行にある（``python3 -c "from X import Y\n..."``）②セミコロン連結や複数行 bracket
# import など「1行=1文」を前提にできない書き方、の両方で見逃しが起きた（レビュー指摘
# M1）。``ast.parse`` で python ソースとして正規に構文解析し、``tree.body`` が返す
# トップレベル文を**元の出現順のまま** import/setup 判定するため、行の切り方に依存しない。


def _is_setup_stmt(node: ast.stmt) -> bool:
    """sys.path 操作、または ``_root``/``plugin_root`` 等への代入を setup 文と判定する。"""
    try:
        src = ast.unparse(node)
    except Exception:
        return False
    if "sys.path" in src:
        return True
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and re.search(r"root", target.id, re.IGNORECASE):
                return True
    return False


def _ast_parse_salvage(source: str, max_attempts: int = 20) -> Tuple[Optional[ast.Module], Optional[str]]:
    """``ast.parse`` を試み、失敗したら SyntaxError の行を ``pass`` に置換して再試行する。

    実例（skills/evolve/references/report-narration.md）: 本文の1行だけが
    ``save_world_context(..., env_score=<ENV_SCORE>, ...)`` のように引用符外の
    placeholder を含み、その行単体は構文エラーになる。この行は import/setup 文ではない
    ため元々実行対象にしないが、``ast.parse`` は本文全体を一度に解析するため、この1行の
    エラーだけで本文中の他の import 文まで丸ごと「判定不能」になってしまう。エラー行を
    ``pass`` に差し替えて再試行することで、無関係な1行の構文エラーが他の import 検証を
    道連れにしないようにする（最大 ``max_attempts`` 回。複数行にまたがる構文エラー
    ― 例えば fence の言語指定を間違えて python 以外の内容が丸ごと入っている場合 ― は
    salvage できず、最終的に判定不能のまま返す。これは意図的: 本文の言語指定そのものが
    誤っている可能性を隠さない）。
    """
    lines = source.splitlines(keepends=True)
    last_err: Optional[SyntaxError] = None
    for _ in range(max_attempts):
        try:
            return ast.parse("".join(lines)), None
        except SyntaxError as e:
            last_err = e
            lineno = e.lineno
            if lineno is None or not (1 <= lineno <= len(lines)):
                break
            original = lines[lineno - 1]
            indent = original[: len(original) - len(original.lstrip(" \t"))]
            newline = "\n" if original.endswith("\n") else ""
            replacement = f"{indent}pass{newline}"
            if lines[lineno - 1] == replacement:
                break  # 同じ行を無限に置換し続ける事態を避ける
            lines[lineno - 1] = replacement
    if last_err is not None:
        return None, f"{type(last_err).__name__}: {last_err}"
    return None, "unknown parse error"


def _ast_select_exec_lines(source: str) -> Tuple[List[str], List[Dict[str, str]], Optional[str]]:
    """python ソースを ``ast.parse`` し、import 文と setup 文を元の順序のまま選び出す。

    返り値: ``(exec_lines, imports, parse_error)``。

    - ``exec_lines``: 実際に subprocess で実行する行（元のソース順。import 文は
      ``ast.unparse`` で再構成した verbatim 文なので、単一行・複数行 bracket・
      セミコロン連結のいずれでも常に構文的に正しい単独文になる＝#496 が懸念した
      「複数行 bracket import を verbatim 実行すると SyntaxError」は、テキストを
      そのまま切り出すのではなく AST から再構成するため発生しない）。
    - ``imports``: ``[{"module": str, "stmt": str}]``（表示・テスト用）。
    - ``parse_error``: ``ast.parse`` が失敗した場合のメッセージ（成功時は ``None``）。
      失敗時は ``exec_lines``/``imports`` は空リストになる。呼び出し側は「判定不能」
      として扱い、pass や skip にはしない（`no-denylist-checks.md` に従い解析対象外を
      緑化しない）。import 以外の副作用行（関数呼び出し等）は一切実行しない。
    """
    # markdown の箇条書き下に置かれた fenced code block は本文全体が一律にインデント
    # されることがある（例: skills/evolve/references/prune-merge.md）。トップレベルの
    # python スクリプトとして解析するため、共通の先頭空白を取り除いてから parse する
    # （相対的なネスト構造＝実際の if/for 本体等は dedent で保たれる）。
    tree, parse_error = _ast_parse_salvage(textwrap.dedent(source))
    if tree is None:
        return [], [], parse_error
    exec_lines: List[str] = []
    imports: List[Dict[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                stmt = f"import {alias.name}"
                imports.append({"module": alias.name, "stmt": stmt})
                exec_lines.append(stmt)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level:
                continue  # 相対 import は対象外（plugin 内の想定書式に含まれない）
            stmt = ast.unparse(node)
            imports.append({"module": node.module, "stmt": stmt})
            exec_lines.append(stmt)
        elif _is_setup_stmt(node):
            exec_lines.append(ast.unparse(node))
    return exec_lines, imports, None


# --- bash 埋め込み python の「呼び出し単位」抽出 ---------------------------------
#
# 旧実装は bash ブロック全体から引用符内の行を isolate して1本の python ソースに
# 連結していたため、同一ブロック内に複数の python3 呼び出しがあると setup/失敗が
# 混ざった（レビュー指摘 M2）。呼び出しごとに独立した ``source`` を取り出し、
# 呼び出しごとに別々の subprocess で検証する。

_DASH_C_OPEN_RE = re.compile(
    r"(?:PYTHONPATH=(?:\"(?P<pp_dq>[^\"]*)\"|'(?P<pp_sq>[^']*)'|(?P<pp_bare>\S+))\s+)?"
    r"python3?\s+-c\s+"
)

_HEREDOC_OPEN_RE = re.compile(
    r"^\s*(?:PYTHONPATH=(?:\"(?P<pp_dq>[^\"]*)\"|'(?P<pp_sq>[^']*)'|(?P<pp_bare>\S+))\s+)?"
    r".*\bpython3?\b.*<<-?\s*['\"]?(?P<delim>\w+)['\"]?\s*$"
)


def _pp_value(m: "re.Match[str]") -> Optional[str]:
    return m.group("pp_dq") if m.group("pp_dq") is not None else (
        m.group("pp_sq") if m.group("pp_sq") is not None else m.group("pp_bare")
    )


def _find_dash_c_invocations(code: str) -> List[Dict[str, Any]]:
    """``python3 -c <arg>`` の呼び出しを、呼び出しごとに独立して列挙する（元の出現順）。

    引用符の解決には ``shlex``（標準ライブラリ）を使う。``python3 -c `` の直後から
    shlex を開始することで、外側の bash 構文（``SLUG="$(... python3 -c "..." ...)"`` の
    ような nested quote）を理解する必要が無くなる＝shlex にとっては「次の1トークン」を
    取るだけの単純な問題になる（単一行完結・複数行にまたがる引用符のどちらも、shlex が
    正しく1トークンとして返す＝import が引用符の開始行にあっても正しく取れる。M1 の直接的な解決）。
    ``whitespace_split`` は使わない（既定の quoted-token 単位で止める）: ``whitespace_split=True``
    にすると shlex は「引用符の直後に空白を挟まず続く文字」を同じ語として食い続けるため、
    ``")`` のように閉じ引用符の直後に ``$(...)`` の閉じ括弧が空白無しで続く実例
    （``BODY=$(python3 -c "..." )`` 相当）で引数へ余計な ``)`` が混入する。
    """
    invocations: List[Dict[str, Any]] = []
    pos = 0
    while True:
        m = _DASH_C_OPEN_RE.search(code, pos)
        if not m:
            break
        remaining = code[m.end():]
        lex = shlex.shlex(remaining, posix=True, punctuation_chars=False)
        try:
            tok = lex.get_token()
        except ValueError:
            # 引用符が閉じていない等。この呼び出しは isolate できない＝スキップし、
            # 続きから次の出現を探す（無限ループ防止で最低 1 文字は進める）。
            pos = m.end() + 1
            continue
        if tok is None:
            pos = m.end() + 1
            continue
        try:
            consumed = lex.instream.tell()  # type: ignore[attr-defined]
        except Exception:
            consumed = len(tok)
        start = m.end()
        end = start + consumed
        invocations.append({"source": tok, "pythonpath": _pp_value(m), "start": start, "end": end})
        pos = max(end, m.end() + 1)
    return invocations


def _find_heredoc_invocations(code: str) -> List[Dict[str, Any]]:
    """``python3 - <<'EOF' ... EOF`` 形の heredoc 呼び出しを列挙する（元の出現順）。"""
    invocations: List[Dict[str, Any]] = []
    lines = code.splitlines(keepends=True)
    offsets = []
    acc = 0
    for line in lines:
        offsets.append(acc)
        acc += len(line)
    offsets.append(acc)
    i, n = 0, len(lines)
    while i < n:
        m = _HEREDOC_OPEN_RE.match(lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        delim = m.group("delim")
        pythonpath = _pp_value(m)
        body_start = i + 1
        j = body_start
        while j < n and lines[j].rstrip("\n").strip() != delim:
            j += 1
        body = "".join(lines[body_start:j])
        invocations.append({
            "source": body,
            "pythonpath": pythonpath,
            "start": offsets[body_start] if body_start < len(offsets) else offsets[-1],
            "end": offsets[j] if j < len(offsets) else offsets[-1],
        })
        i = j + 1
    return invocations


def _find_python_invocations(code: str) -> List[Dict[str, Any]]:
    """bash ブロック中の python 呼び出し（``-c`` / heredoc）を元の出現順で列挙する。

    **既知の限界（迂回可能・`no-denylist-checks.md`）**: ①``-c`` の引数を shlex で
    取り出すため、shlex の POSIX クォート規則から外れる書き方（例: バッククォート内の
    ネストした引用符）は isolate できないことがある ②heredoc の delimiter 行に
    余分な空白・引用符の揺れがあると開始行を検出できないことがある。
    """
    found = _find_dash_c_invocations(code) + _find_heredoc_invocations(code)
    found.sort(key=lambda e: e["start"])
    return found


def classify_block(lang: str, code: str) -> Dict[str, Any]:
    """コードブロックを安全分類する。

    返り値: ``{"mode": "import_check"|"run"|"existence_only", ...}``
      - import_check: 1件以上の python 呼び出しの import 文（+ setup 文）を検証対象にする
        （``invocations`` に呼び出しごとの実行行を保持。呼び出し間で混ぜない＝M2 の解決）
      - run: bash の --help/--dry-run 付き安全コマンドを実行
      - existence_only: 実行せず存在検証のみ
    """
    cls: Dict[str, Any] = {"mode": "existence_only"}
    if _has_placeholder(code):
        cls["has_placeholder"] = True

    if lang == "python":
        exec_lines, imports, parse_error = _ast_select_exec_lines(code)
        if parse_error is not None:
            cls["mode"] = "import_check"
            cls["imports"] = []
            cls["invocations"] = [{"exec_lines": [], "parse_error": parse_error}]
            return cls
        if imports:
            cls["mode"] = "import_check"
            cls["imports"] = imports
            cls["invocations"] = [{"exec_lines": exec_lines, "parse_error": None}]
        else:
            cls["mode"] = "existence_only"
        return cls

    # bash
    commands = _extract_bash_commands(code)
    cls["commands"] = commands

    invocations: List[Dict[str, Any]] = []
    all_imports: List[Dict[str, str]] = []
    for found in _find_python_invocations(code):
        exec_lines, imports, parse_error = _ast_select_exec_lines(found["source"])
        pp_setup: List[str] = []
        if found.get("pythonpath"):
            entries = [e for e in found["pythonpath"].split(os.pathsep) if e.strip()]
            if entries:
                pp_setup = ["import sys"] + [f"sys.path.insert(0, {e!r})" for e in entries]
        if parse_error is not None:
            invocations.append({"exec_lines": [], "parse_error": parse_error})
            continue
        if not imports:
            continue
        invocations.append({"exec_lines": pp_setup + exec_lines, "parse_error": None})
        all_imports.extend(imports)

    if invocations:
        cls["mode"] = "import_check"
        cls["imports"] = all_imports
        cls["invocations"] = invocations
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


def _mask_heredoc_bodies(code: str) -> str:
    """heredoc 本文をコマンド抽出対象から除外するため、該当範囲を空白へ置き換える。

    ``_extract_bash_commands`` の引用符 parity 追跡は heredoc（``<<DELIM``）の区切りを
    理解しないため、heredoc 本文の先頭行（例: ``from discover import ...``）が裸コマンド
    ``from`` として誤検出される（行数・オフセットは変えず文字だけ空白化して維持する）。
    """
    spans = [(inv["start"], inv["end"]) for inv in _find_heredoc_invocations(code)]
    if not spans:
        return code
    chars = list(code)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _extract_bash_commands(code: str) -> List[str]:
    """論理コマンド行の先頭トークン（裸の CLI 名のみ）を拾う。

    継続行（``--requests ...``）・シェルキーワード・フラグ・代入・heredoc 本文は除外し、
    実在検証して意味のある裸のコマンド名だけを返す（FP を構造的に抑制）。
    """
    code = _mask_heredoc_bodies(code)
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
    bash ブロックでは、python 呼び出しの import 検証（invocations 単位で独立実行）と
    コマンド/スクリプトの存在検証を**両方**実行し、いずれかが fail なら全体を fail にする
    （import_check への昇格を理由に存在検証を省略しない＝レビュー指摘 M3 の解決）。
    返り値: ``{"status": "pass"|"fail"|"skip", "mode": ..., "detail": str, "line": int}``
    """
    repo_root = Path(repo_root)
    code = _expand_plugin_root(block["code"], repo_root)
    cls = classify_block(block["lang"], code)
    base = {"mode": cls["mode"], "line": block.get("line", 0), "source": block.get("source", "")}

    sub_results: List[Tuple[str, Dict[str, Any]]] = []

    if cls["mode"] == "import_check":
        for idx, inv in enumerate(cls.get("invocations", [])):
            if inv.get("parse_error") is not None:
                sub_results.append((
                    f"import#{idx}",
                    {"status": "fail", "detail": f"判定不能（ast.parse 失敗）: {inv['parse_error']}"},
                ))
            else:
                sub_results.append((
                    f"import#{idx}",
                    _run_import_check(inv["exec_lines"], sys_path_dirs, repo_root),
                ))
    elif cls["mode"] == "run":
        sub_results.append(("run", _run_safe_bash(code, repo_root)))

    if block["lang"] == "bash":
        # M3: import_check への昇格と独立に、コマンド/スクリプトの存在検証を常に行う。
        existence_res = _run_existence_check(cls, code, repo_root)
        if existence_res["status"] != "skip":
            sub_results.append(("existence", existence_res))
    elif not sub_results:
        # python fence で existence_only（import なし）のときの従来フォールバック。
        sub_results.append(("existence", _run_existence_check(cls, code, repo_root)))

    if not sub_results:
        return {**base, "status": "skip", "detail": "no verifiable command/path"}

    statuses = [r["status"] for _, r in sub_results]
    if "fail" in statuses:
        overall = "fail"
    elif all(s == "pass" for s in statuses):
        overall = "pass"
    else:
        overall = "skip"
    parts = [f"{label}: {r['detail']}" for label, r in sub_results if r["status"] == "fail"]
    if not parts:
        parts = [f"{label}: {r['detail']}" for label, r in sub_results]
    return {**base, "status": overall, "detail": "; ".join(parts)}


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
