"""Shell scope normalization for the deterministic skill vulnerability scanner.

This is deliberately a conservative lexical model, not a complete shell parser.  It
centralizes the places where physical Markdown/script lines become shell candidates.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple


_TRAILING_PIPE_RE = re.compile(r"\|\s*$")
_TRAILING_BACKSLASH_RE = re.compile(r"\\\s*$")
_MARKDOWN_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_HEREDOC_OPENER_RE = re.compile(
    r"(?<!<)<<(?P<strip_tabs>-?)(?!<)\s*"
    r"(?:'(?P<single>[^'\n]+)'|\"(?P<double>[^\"\n]+)\"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)
_HEREDOC_SHELL_COMMAND_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:sudo\s+)?(?:\S*/)?(?:sh|bash|zsh|ksh|dash)\b"
)

# Shell の「どこで論理行が続くか」は is_shell_continuation と
# join_logical_lines の2関数に集約する。新しい継続構文を検出 pattern ごとに
# 足してはならない。heredoc だけは物理行の本文ゾーンを先に確定する別 concern。


def effective_shell_text(stripped_line: str) -> str:
    """Return the executable prefix before an unquoted shell comment.

    This is the sole comment-removal entry point used by physical-line scanning,
    logical-line joining, and flow analysis.  It intentionally is not a full shell
    parser.  Uncertain constructs stay inspectable.  Heredocs are handled separately:
    bodies executed by sh/bash/zsh/ksh/dash stay inspectable, while bodies supplied to
    data commands such as ``cat`` are explicit data zones and bypass shell processing.

    ``#`` begins a comment only outside quotes, when unescaped, and at the beginning of
    a shell word (line start, whitespace, or a shell operator boundary).  Thus URL
    fragments and escaped hashes remain intact.
    """
    in_single = False
    in_double = False
    boundary_chars = ";&|()<>"
    prev_is_boundary = True
    i = 0
    while i < len(stripped_line):
        ch = stripped_line[i]
        if ch == "\\" and not in_single:
            i += 2
            prev_is_boundary = False
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            prev_is_boundary = False
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            prev_is_boundary = False
            i += 1
            continue
        if ch == "#" and not in_single and not in_double and prev_is_boundary:
            return stripped_line[:i]
        prev_is_boundary = ch.isspace() or ch in boundary_chars
        i += 1
    return stripped_line


def is_shell_continuation(stripped_line: str) -> bool:
    """Return whether a physical line continues under the conservative shell model.

    The centralized cases are trailing ``|`` / ``\\`` and unclosed ``$(``, backtick,
    single quote, or double quote.  Markdown table-shaped rows are rejected here,
    rather than by weakening the surrounding code-block scope.
    """
    effective = effective_shell_text(stripped_line)
    if _MARKDOWN_TABLE_ROW_RE.match(effective):
        return False
    if _TRAILING_PIPE_RE.search(effective) or _TRAILING_BACKSLASH_RE.search(effective):
        return True

    in_single = False
    in_double = False
    in_backtick = False
    command_substitution_depth = 0
    i = 0
    while i < len(effective):
        ch = effective[i]
        if ch == "\\" and not in_single:
            i += 2
            continue
        if ch == "'" and not in_double and not in_backtick:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single and not in_backtick:
            in_double = not in_double
            i += 1
            continue
        if ch == "`" and not in_single:
            in_backtick = not in_backtick
            i += 1
            continue
        if ch == "$" and i + 1 < len(effective) and effective[i + 1] == "(" and not in_single:
            command_substitution_depth += 1
            i += 2
            continue
        if ch == ")" and command_substitution_depth and not in_single:
            command_substitution_depth -= 1
        i += 1
    return bool(in_single or in_double or in_backtick or command_substitution_depth)


def compute_shell_scope_lines(
    lines: List[str],
    match_opener: Callable[[str], Optional[Tuple[str, int]]],
    match_closer: Callable[[str, str, int], bool],
) -> set:
    """Return Markdown code-block line numbers without trusting the info string.

    All closed backtick/tilde fenced blocks and 4-space/tab indented code lines are
    shell candidates.  Fence markers are excluded.  An unclosed fence extends to EOF
    under CommonMark, so its remainder is also a shell candidate.  This differs from
    literal-zone trust: adding shell scope strengthens inspection instead of hiding it.
    """
    scope: set = set()
    stripped_lines = [raw.rstrip("\r\n") for raw in lines]
    idx = 0
    while idx < len(lines):
        opener = match_opener(stripped_lines[idx])
        if opener is None:
            if stripped_lines[idx].startswith("    ") or stripped_lines[idx].startswith("\t"):
                scope.add(idx + 1)
            idx += 1
            continue
        ch, min_len = opener
        close_at = None
        for j in range(idx + 1, len(lines)):
            if match_closer(stripped_lines[j], ch, min_len):
                close_at = j
                break
        if close_at is None:
            scope.update(range(idx + 2, len(lines) + 1))
            break
        scope.update(range(idx + 2, close_at + 1))
        idx = close_at + 1
    return scope


def compute_heredoc_zones(lines: List[str], shell_scope: set) -> Tuple[set, set]:
    """Return ``(shell_body_lines, data_body_and_terminator_lines)`` for heredocs.

    Recognizes ``<<WORD``, ``<<-WORD``, and single/double-quoted delimiters.  The body
    stays executable only when the introducing command is sh/bash/zsh/ksh/dash (also
    absolute/relative command paths and an optional ``sudo``).  Unclosed heredocs do
    not create a suppressing zone.
    """
    shell_body: set = set()
    data_zone: set = set()
    idx = 0
    while idx < len(lines):
        if (idx + 1) not in shell_scope:
            idx += 1
            continue
        line = lines[idx].rstrip("\r\n")
        match = _HEREDOC_OPENER_RE.search(effective_shell_text(line))
        if match is None:
            idx += 1
            continue
        delimiter = match.group("single") or match.group("double") or match.group("bare")
        strip_tabs = match.group("strip_tabs") == "-"
        executes_shell = bool(_HEREDOC_SHELL_COMMAND_RE.search(line[: match.start()]))
        close_at = None
        for j in range(idx + 1, len(lines)):
            if (j + 1) not in shell_scope:
                break
            candidate = lines[j].rstrip("\r\n")
            if strip_tabs:
                candidate = candidate.lstrip("\t")
            if candidate.startswith("    "):
                candidate = candidate[4:]
            if candidate == delimiter:
                close_at = j
                break
        if close_at is None:
            idx += 1
            continue
        body_lines = set(range(idx + 2, close_at + 1))
        if executes_shell:
            shell_body.update(body_lines)
        else:
            data_zone.update(body_lines)
            data_zone.add(close_at + 1)
        idx = close_at + 1
    return shell_body, data_zone


def join_logical_lines(lines: List[str], scope_linenos: set) -> List[Tuple[int, str]]:
    """Join continued physical lines and return ``(first_lineno, logical_text)``.

    Comment-only and empty lines are transparent during a continuation.  A trailing
    backslash is removed; a trailing pipe and unclosed constructs remain in the joined
    text so the scanner can recognize the executed combination.
    """
    out: List[Tuple[int, str]] = []
    idx = 0
    while idx < len(lines):
        lineno = idx + 1
        stripped = lines[idx].rstrip("\r\n")
        if lineno not in scope_linenos or not is_shell_continuation(stripped):
            idx += 1
            continue
        start_lineno = lineno
        buf = effective_shell_text(stripped)
        j = idx + 1
        while is_shell_continuation(buf) and j < len(lines) and (j + 1) in scope_linenos:
            nxt_effective = effective_shell_text(lines[j].rstrip("\r\n"))
            if not nxt_effective.strip():
                j += 1
                continue
            if _TRAILING_BACKSLASH_RE.search(buf):
                buf = _TRAILING_BACKSLASH_RE.sub("", buf).rstrip() + " " + nxt_effective.strip()
            else:
                buf = buf.rstrip() + " " + nxt_effective.strip()
            j += 1
        out.append((start_lineno, buf))
        idx = j
    return out
