"""Shell scope normalization for the deterministic skill vulnerability scanner.

This is deliberately a conservative lexical model, not a complete shell parser.  It
centralizes the places where physical Markdown/script lines become shell candidates.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Pattern, Tuple


# A remote-exec detector must answer one question consistently: does this command
# position ultimately launch a shell?  Keep that grammar here and interpolate this
# exact fragment into every remote_exec pattern.  This is intentionally lexical and
# conservative; dynamic names ($SHELL, aliases, functions) require runtime knowledge.
_COMMAND_PATH = r"(?:\.{1,2}/|/(?:[A-Za-z0-9._+-]+/)*)?"


def _static_shell_word(word: str) -> str:
    r"""Return a regex for one statically spelled shell word.

    Shell quote removal and backslash processing make ``b'a'sh`` and ``ba\sh`` the
    same executable name as ``bash``.  Quotes and escapes are accepted only while
    spelling an allowlisted static word; variables and other dynamic expansion stay
    outside this issue's trust boundary.
    """
    quote = r"['\"]*"
    chars = [rf"(?:\\[{re.escape(char)}]|{re.escape(char)})" for char in word]
    return quote + quote.join(chars) + quote + r"(?![A-Za-z0-9_])"


def _static_command(names: Tuple[str, ...]) -> str:
    return _COMMAND_PATH + "(?:" + "|".join(_static_shell_word(n) for n in names) + ")"


_SHELL_COMMAND = _static_command(
    ("tcsh", "bash", "zsh", "ksh", "dash", "dsh", "ash", "csh", "sh")
)
_WRAPPER_COMMAND = _static_command(
    ("exec", "env", "command", "nohup", "setsid", "time", "builtin", "eval")
)
_OPTION = r"(?:--|--?[A-Za-z0-9][A-Za-z0-9_-]*(?:=[^\s|;&()<>]+)?)"
_ASSIGNMENT = r"(?:[A-Za-z_][A-Za-z0-9_]*=(?:[^\s|;&()<>]+|'[^']*'|\"[^\"]*\"))"
_WRAPPER_STEP = rf"(?:{_WRAPPER_COMMAND}(?:\s+(?:{_OPTION}|{_ASSIGNMENT}))*\s+)"

# Additional deterministic launchers found while exploring beyond the issue examples.
# nice consumes an optional priority operand; busybox selects `sh` as an applet; xargs
# launches its following command for pipeline input.  All still terminate in the same
# `_SHELL_COMMAND`, so they cannot turn a non-shell consumer into a finding.
_NICE_STEP = rf"(?:{_static_command(('nice',))}(?:\s+{_OPTION})*(?:\s+-?\d+)?\s+)"
_BUSYBOX_STEP = rf"(?:{_static_command(('busybox',))}(?:\s+{_OPTION})*\s+)"
_XARGS_STEP = rf"(?:{_static_command(('xargs',))}(?:\s+{_OPTION})*\s+)"
_TIMEOUT_STEP = (
    rf"(?:{_static_command(('timeout',))}(?:\s+{_OPTION})*"
    rf"\s+\d+(?:\.\d+)?[smhd]?\s+)"
)
_SUDO_OPTION_ARG = (
    r"(?:(?:-u|--user|-g|--group|-h|--host|-C|--close-from|-T|"
    r"--command-timeout|-R|--chroot|-D|--chdir)\s+[^\s|;&()<>]+)"
)
_SUDO_STEP = (
    rf"(?:{_static_command(('sudo',))}"
    rf"(?:\s+(?:{_SUDO_OPTION_ARG}|{_OPTION}))*\s+)"
)
SHELL_EXEC_SUBJECT = (
    rf"(?:(?:{_WRAPPER_STEP}|{_SUDO_STEP}|{_NICE_STEP}|{_BUSYBOX_STEP}|"
    rf"{_XARGS_STEP}|{_TIMEOUT_STEP})*"
    rf"{_SHELL_COMMAND})"
)

_REMOTE_LINE_GUARD = r"^(?!\s*echo\s+[\"'][^$`\n]*[\"']\s*$)"


def build_remote_exec_patterns() -> List[Tuple[str, Pattern[str]]]:
    """Build the remote-exec catalog from the shared shell execution subject.

    Every entry deliberately contains ``SHELL_EXEC_SUBJECT`` verbatim.  Fetching or
    decoding alone remains benign; a finding requires a fetch/decode-to-shell combo.
    A direct ``echo \"curl ... | sh\"`` literal is explanatory data, while command
    substitutions/backticks are not suppressed because they execute before ``echo``.
    """
    subject = SHELL_EXEC_SUBJECT
    specs = [
        (
            "remote_exec.curl_pipe_sh",
            rf"{_REMOTE_LINE_GUARD}.*\b(?:curl|wget|fetch)\b[^\n|]*\|\s*{subject}",
        ),
        (
            "remote_exec.base64_pipe_sh",
            rf"{_REMOTE_LINE_GUARD}.*\bbase64\s+(?:--decode|-d|-D)\b[^\n|]*\|\s*{subject}",
        ),
        (
            "remote_exec.download_and_run",
            rf"{_REMOTE_LINE_GUARD}.*\b(?:curl|wget)\b[^\n]*\s-o(?:\s+|=)"
            rf"[^\n]*&&\s*{subject}",
        ),
        (
            "remote_exec.shell_c_command_substitution",
            rf"{_REMOTE_LINE_GUARD}.*{subject}[^\n]*?\s-c\b[^\n]*?\$\([^)]*\b(?:curl|wget|fetch)\b",
        ),
        (
            "remote_exec.process_substitution",
            rf"{_REMOTE_LINE_GUARD}.*{subject}(?:\s+{_OPTION})*\s+<\(\s*(?:curl|wget|fetch)\b[^)]*\)",
        ),
    ]
    return [(pattern_id, re.compile(source, re.IGNORECASE)) for pattern_id, source in specs]


_TRAILING_PIPE_RE = re.compile(r"\|\s*$")
_TRAILING_BACKSLASH_RE = re.compile(r"\\\s*$")
_MARKDOWN_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_HEREDOC_OPENER_RE = re.compile(
    r"(?<!<)<<(?P<strip_tabs>-?)(?!<)\s*"
    r"(?:'(?P<single>[^'\n]+)'|\"(?P<double>[^\"\n]+)\"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)
_HEREDOC_DATA_COMMANDS = frozenset(
    {
        "cat",  # Copies stdin to output and never interprets it as shell source.
        "tee",  # Copies stdin to files/stdout and never interprets it as shell source.
    }
)
_HEREDOC_DATA_UNSAFE_CONTEXT_RE = re.compile(r"[;&|()`$]")

# Shell の「どこで論理行が続くか」は is_shell_continuation と
# join_logical_lines の2関数に集約する。新しい継続構文を検出 pattern ごとに
# 足してはならない。heredoc だけは物理行の本文ゾーンを先に確定する別 concern。


def effective_shell_text(stripped_line: str) -> str:
    """Return the executable prefix before an unquoted shell comment.

    This is the sole comment-removal entry point used by physical-line scanning,
    logical-line joining, and flow analysis.  It intentionally is not a full shell
    parser.  Uncertain constructs stay inspectable.  Heredocs are handled separately:
    all bodies stay inspectable unless the opener is a direct invocation of a known
    non-executing data command such as ``cat`` or ``tee``.

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
    Heredoc suppression is applied later only to direct, known data-command openers;
    unknown or compound opener contexts remain in this inspectable scope.
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


def _is_known_data_heredoc_command(prefix: str) -> bool:
    """Return whether an opener prefix is a direct known data-command invocation."""
    stripped = prefix.strip()
    if not stripped or _HEREDOC_DATA_UNSAFE_CONTEXT_RE.search(stripped):
        return False
    command = stripped.split(None, 1)[0].rsplit("/", 1)[-1]
    return command in _HEREDOC_DATA_COMMANDS


def compute_heredoc_zones(lines: List[str], shell_scope: set) -> Tuple[set, set]:
    """Return ``(shell_body_lines, data_body_and_terminator_lines)`` for heredocs.

    Recognizes ``<<WORD``, ``<<-WORD``, and single/double-quoted delimiters.  The body
    is suppressed only when the opener is a direct invocation of a known command that
    treats stdin as data.  Unknown, wrapped, or compound contexts stay inspectable so
    syntax variation cannot opt out of scanning.  Unclosed heredocs do not suppress.
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
        is_known_data = _is_known_data_heredoc_command(line[: match.start()])
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
        if is_known_data:
            data_zone.update(body_lines)
            data_zone.add(close_at + 1)
        else:
            shell_body.update(body_lines)
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
