"""skill_vuln_scan.py — 取り込みスキルの静的脆弱性スキャン（SkillSpector 型・#13）。

LLM 非依存・決定論・読み取りのみ（ファイル/store 書込なし）。背景:

外部由来のスキル（SKILL.md プロンプト + 同梱シェルスクリプト）を取り込むと、悪意ある
パターン（リモート取得 → shell 実行 / 秘密ファイルのネットワーク exfil / 破壊的コマンド /
SKILL.md に埋め込まれた prompt injection / frontmatter の全ツール付与）を見落とす穴がある。
本モジュールは `root/skills/` 配下を行単位で静的スキャンし、危険パターンを Finding として
列挙する。SkillSpector（取り込みスキルの脆弱性静的検査）の発想に倣う。

FP 較正の方針（このリポジトリの鉄則 = 偽陽性に極めて厳格）:
- **combo 必須・bare 単体は検出しない**。例: `curl https://...`（単独）は正当な取得なので非検出、
  `curl http://... | sh` のように shell へ流す combo のみ remote_exec とする。
- `gh api repos/x/contents/... -q .content | base64 -d`（GitHub content デコード）は実在の
  正当 FP。`base64 -d` 単体は検出せず、`base64 -d ... | sh` の combo のみ検出する。
- `rm -rf ./build` のような相対パス削除は非検出。`/`・`~`・`$HOME`・`*` を消す場合のみ destructive。
- secret_exfil は「秘密ソース」と「ネット sink」が**同一行に共起**したときのみ。片方だけは非検出。

対象拡張子は `.md` / `.sh` / `.bash` のみ（`.py` は FP 抑制のため本 PR 対象外。follow-up）。
配線先は audit observability の "Skill Vulnerability" section（`audit/sections_skill_vuln.py`）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Pattern, Tuple

# 走査対象拡張子（.py は本 PR 対象外＝FP 抑制。follow-up で別途）。
_SCAN_EXTENSIONS = {".md", ".sh", ".bash"}

# 走査から除外するディレクトリ名（testpaths_coverage の _EXCLUDE_DIRS と同等の慣習）。
_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".claude",
    "tests",
}

# snippet の最大長（マッチ行を strip して truncate）。
_SNIPPET_MAX = 120

# --- pattern catalog（FP 較正済み・combo 必須） --------------------------------
# 各エントリ: (pattern_id, category, severity, compiled regex)
# 同一行に対し regex.search でマッチ判定する（行単位スキャン）。

_PATTERNS: List[Tuple[str, str, str, Pattern[str]]] = [
    # remote_exec / HIGH — リモート取得を shell にパイプ／ダウンロードして即実行。
    (
        "remote_exec.curl_pipe_sh",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\b(curl|wget|fetch)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|k|d|a)?sh\b"),
    ),
    (
        # base64 -d 単体は非検出。shell へパイプする combo のみ。
        "remote_exec.base64_pipe_sh",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\bbase64\s+(--decode|-d|-D)\b[^\n|]*\|\s*(ba)?sh\b"),
    ),
    (
        "remote_exec.download_and_run",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\b(curl|wget)\b[^\n]*\b-o\b[^\n]*&&[^\n]*\b(ba)?sh\b"),
    ),
    # destructive / MEDIUM
    (
        "destructive.rm_rf_root",
        "destructive",
        "MEDIUM",
        re.compile(r"(?i)\brm\s+-[rf]*\s+(/|~|\$HOME|\*)(\s|$)"),
    ),
    (
        "destructive.chmod_777",
        "destructive",
        "MEDIUM",
        re.compile(r"chmod\s+-?R?\s*777\b"),
    ),
    (
        "destructive.fork_bomb",
        "destructive",
        "MEDIUM",
        re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"),
    ),
    # prompt_injection / MEDIUM — SKILL.md 等に埋め込まれた注入ペイロード。
    (
        "prompt_injection.ignore_previous",
        "prompt_injection",
        "MEDIUM",
        re.compile(
            r"(?i)ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above)\s+"
            r"(instructions?|prompts?|rules?)"
        ),
    ),
    (
        "prompt_injection.disregard",
        "prompt_injection",
        "MEDIUM",
        re.compile(r"(?i)disregard\s+(the\s+|all\s+|previous\s+|above\s+)"),
    ),
    (
        "prompt_injection.do_not_tell_user",
        "prompt_injection",
        "MEDIUM",
        re.compile(r"(?i)do\s+not\s+(tell|inform|reveal\s+to|notify)\s+the\s+user"),
    ),
    (
        "prompt_injection.ja_ignore",
        "prompt_injection",
        "MEDIUM",
        re.compile(
            r"これまでの指示を無視|以前の指示を無視|システムプロンプトを無視|"
            r"ユーザーには(言わない|伝えない|秘密)"
        ),
    ),
    # overbroad_tools / LOW — frontmatter の全ツール付与。
    (
        "overbroad_tools.wildcard",
        "overbroad_tools",
        "LOW",
        re.compile(r"(?im)^\s*(tools|allowed-tools)\s*:\s*[\"']?\*"),
    ),
]

# secret_exfil は「秘密ソース」+「ネット sink」の同一行共起でのみ検出する（特殊判定）。
_SECRET_SOURCE = re.compile(
    r"(?i)(~/\.ssh/id_|\.aws/credentials|id_rsa|\.env\b|printenv\b|\benv\b\s*\|)"
)
_NET_SINK = re.compile(r"(?i)(\bcurl\b|\bwget\b|\bnc\b|https?://)")


@dataclass(frozen=True)
class Finding:
    """1 件の脆弱性ヒット。

    rel_path:   root からの POSIX 相対パス
    line:       1 始まりの行番号
    category:   remote_exec / secret_exfil / destructive / prompt_injection / overbroad_tools
    severity:   HIGH / MEDIUM / LOW
    pattern_id: マッチした pattern の識別子
    snippet:    マッチ行を strip し最大 120 字に truncate したもの
    """

    rel_path: str
    line: int
    category: str
    severity: str
    pattern_id: str
    snippet: str


@dataclass
class SkillVulnReport:
    """スキャン結果。

    applicable:    root/skills/ が存在したか（無ければ False＝非該当・沈黙）
    scanned_files: 走査した対象拡張子ファイル数
    findings:      検出した Finding（(rel_path, line, pattern_id) で安定ソート済み）
    """

    applicable: bool = False
    scanned_files: int = 0
    findings: List[Finding] = field(default_factory=list)


def _snippet(line_text: str) -> str:
    s = line_text.strip()
    if len(s) > _SNIPPET_MAX:
        return s[:_SNIPPET_MAX]
    return s


def _iter_target_files(skills_dir: Path) -> List[Path]:
    """skills_dir 配下の対象拡張子ファイルを除外ディレクトリを除いて列挙する。"""
    out: List[Path] = []
    for p in skills_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _SCAN_EXTENSIONS:
            continue
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _scan_line(rel_path: str, lineno: int, text: str) -> List[Finding]:
    found: List[Finding] = []
    for pattern_id, category, severity, regex in _PATTERNS:
        if regex.search(text):
            found.append(
                Finding(
                    rel_path=rel_path,
                    line=lineno,
                    category=category,
                    severity=severity,
                    pattern_id=pattern_id,
                    snippet=_snippet(text),
                )
            )
    # secret_exfil: 秘密ソース + ネット sink の同一行共起。
    if _SECRET_SOURCE.search(text) and _NET_SINK.search(text):
        found.append(
            Finding(
                rel_path=rel_path,
                line=lineno,
                category="secret_exfil",
                severity="HIGH",
                pattern_id="secret_exfil.source_and_sink",
                snippet=_snippet(text),
            )
        )
    return found


def scan_skills(root: Path) -> SkillVulnReport:
    """root/skills/ 配下の取り込みスキルを静的スキャンして脆弱性 Finding を返す（決定論）。"""
    root = Path(root)
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return SkillVulnReport(applicable=False, scanned_files=0, findings=[])

    findings: List[Finding] = []
    scanned = 0
    for path in _iter_target_files(skills_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        rel = path.relative_to(root).as_posix()
        for idx, line in enumerate(text.splitlines(), start=1):
            findings.extend(_scan_line(rel, idx, line))

    findings.sort(key=lambda f: (f.rel_path, f.line, f.pattern_id))
    return SkillVulnReport(applicable=True, scanned_files=scanned, findings=findings)
