"""File-local producer/consumer flow analysis for ``skill_vuln_scan``."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Pattern, Tuple

from skill_vuln_shell import effective_shell_text


SECRET_SOURCE = re.compile(
    r"(?i)(~/\.ssh/id_|\.aws/credentials|id_rsa|\.env\b|printenv\b|\benv\b\s*\|)"
)
NET_SINK = re.compile(r"(?i)(\bcurl\b|\bwget\b|\bnc\b|https?://)")

_FLOW_FETCH_CMD = re.compile(r"(?i)(\b(?:curl|wget|fetch)\b|\bgh\s+api\b)")
_FLOW_CMD_SUBST = re.compile(r"\$\(|`")
_FLOW_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_PLACEHOLDER_TOKEN = re.compile(r"<[A-Za-z0-9_.-]+>")
FLOW_FETCH_TO_FILE = re.compile(
    r"(?i)\b(?:curl|wget|fetch)\b[^\n]*?"
    r"(?:-o|-O|--output|>>?)\s*['\"]?([^\s'\"|;&><`]+)"
)
_FLOW_FILE_IGNORE = {"-", "/dev/null", "/dev/stdout", "/dev/stderr"}
_EXEC_VAR_FORM_TEMPLATES = [
    r"(?i)\beval\b[^\n]*{ref}",
    r"(?i)\b(?:(?:ba|z|k|d|a)?sh|python3?|node|perl|ruby)\b[^\n]*?\s-(?:c|e)\b[^\n]*{ref}",
    r"(?i)\b(?:(?:ba|z|k|d|a)?sh|python3?)\b[^\n]*?<<<[^\n]*{ref}",
    r"(?i){ref}[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|k|d|a)?sh\b",
]


@dataclass(frozen=True)
class FlowFinding:
    """A file-local ordered producer/consumer vulnerability finding."""

    rel_path: str
    producer_line: int
    consumer_line: int
    category: str
    severity: str
    pattern_id: str
    var: str
    producer_snippet: str
    consumer_snippet: str


def mask_placeholder_tokens(text: str) -> str:
    """Mask ``<placeholder>`` tokens before redirect matching, preserving length."""
    return _PLACEHOLDER_TOKEN.sub(lambda match: "#" * len(match.group(0)), text)


def _var_ref_pattern(var: str) -> str:
    return r"\$\{?" + re.escape(var) + r"(?![A-Za-z0-9_])"


def _exec_var_regexes(var: str) -> List[Pattern[str]]:
    ref = _var_ref_pattern(var)
    return [re.compile(template.format(ref=ref)) for template in _EXEC_VAR_FORM_TEMPLATES]


def _exec_file_regexes(fpath: str) -> List[Pattern[str]]:
    ref = re.escape(fpath)
    base = re.escape(fpath.rsplit("/", 1)[-1])
    return [
        re.compile(
            r"(?i)\b(?:(?:ba|z|k|d|a)?sh|source|python3?|node|perl|ruby)\s+"
            r"(?:-\S+\s+)*['\"]?" + ref
        ),
        re.compile(r"(?i)(?:^|;|&&|\|\|)\s*\.\s+['\"]?" + ref),
        re.compile(r"(?i)(?:^|[;&|(`])\s*\./" + base + r"\b"),
        re.compile(r"(?i)\bchmod\s+\+x\b[^\n]*" + ref),
    ]


def detect_flows_in_scope(
    rel_path: str,
    scope_lines: List[Tuple[int, str]],
    literal_zone: set,
    shell_scope: set,
    normalize_for_matching: Callable[[str], str],
    strip_leading_decoration: Callable[[str], str],
    snippet: Callable[[str], str],
) -> List[FlowFinding]:
    """Detect ordered fetch→exec and read→exfil pairs within one file scope."""
    found: List[FlowFinding] = []
    fetch_vars: dict[str, Tuple[int, str]] = {}
    fetch_files: dict[str, Tuple[int, str]] = {}
    secret_vars: dict[str, Tuple[int, str]] = {}

    for lineno, text in scope_lines:
        effective_text = effective_shell_text(text) if lineno in shell_scope else text
        norm = (
            normalize_for_matching(effective_text)
            if lineno in literal_zone
            else strip_leading_decoration(effective_text)
        )
        for var, (producer_line, producer_snippet) in fetch_vars.items():
            if any(regex.search(norm) for regex in _exec_var_regexes(var)):
                found.append(FlowFinding(
                    rel_path, producer_line, lineno, "remote_exec_flow", "HIGH",
                    "remote_exec_flow.fetch_var_to_exec", var,
                    producer_snippet, snippet(text),
                ))
        for fpath, (producer_line, producer_snippet) in fetch_files.items():
            if any(regex.search(norm) for regex in _exec_file_regexes(fpath)):
                found.append(FlowFinding(
                    rel_path, producer_line, lineno, "remote_exec_flow", "HIGH",
                    "remote_exec_flow.fetch_file_to_exec", fpath,
                    producer_snippet, snippet(text),
                ))
        for var, (producer_line, producer_snippet) in secret_vars.items():
            if re.search(_var_ref_pattern(var), norm) and NET_SINK.search(norm):
                found.append(FlowFinding(
                    rel_path, producer_line, lineno, "secret_exfil_flow", "HIGH",
                    "secret_exfil_flow.read_var_to_net", var,
                    producer_snippet, snippet(text),
                ))

        assignment = _FLOW_ASSIGN.match(norm)
        if assignment:
            var, rhs = assignment.group(1), assignment.group(2)
            if _FLOW_CMD_SUBST.search(rhs):
                if _FLOW_FETCH_CMD.search(rhs):
                    fetch_vars.setdefault(var, (lineno, snippet(text)))
                if SECRET_SOURCE.search(rhs):
                    secret_vars.setdefault(var, (lineno, snippet(text)))
        fetch_match = FLOW_FETCH_TO_FILE.search(mask_placeholder_tokens(norm))
        if fetch_match:
            fpath = fetch_match.group(1)
            if fpath and fpath not in _FLOW_FILE_IGNORE:
                fetch_files.setdefault(fpath, (lineno, snippet(text)))
    return found


def iter_scopes(path: Path, text: str) -> List[List[Tuple[int, str]]]:
    """Return the single intentional file-local flow scope, preserving line numbers."""
    del path
    return [list(enumerate(text.splitlines(), start=1))]

