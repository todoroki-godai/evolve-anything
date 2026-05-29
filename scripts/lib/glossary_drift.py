"""glossary_drift.py — CONTEXT.md（Ubiquitous Language 用語集）の drift 検出。

決定論・LLM 非依存。用語集が SoT（SPEC.md / CLAUDE.md 等）から腐る
（= jargon が増えたのに用語集に追記されない）ことを検出する。
spec-keeper の update フローが advisory として消費する。

検出する drift:
  - malformed_lines:    用語集テーブルのスキーマ不一致行（3列でない等）
  - duplicate_terms:    同一用語の重複定義
  - missing_first_seen: 初出（# 参照）が空のエントリ
  - undefined_terms:    SoT に出現する jargon 候補で用語集に未登録のもの

CLI:
  python3 scripts/lib/glossary_drift.py CONTEXT.md SPEC.md CLAUDE.md
      レポートを stdout に出力。drift があれば exit 1、なければ exit 0。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

# jargon 候補: ALLCAPS 頭字語(2-6文字) または 内部に大文字を持つ CamelCase。
# 例: BES, RRF, BM25, MemTrace, DuckDB。先頭小文字の通常語は拾わない。
_CANDIDATE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[A-Z]{2,6})\b")

# 用語集に載せる価値の薄い汎用テック頭字語。undefined 判定から除外する。
DEFAULT_STOPLIST: frozenset[str] = frozenset(
    {
        # 汎用テック頭字語
        "API", "CLI", "LLM", "JSON", "JSONL", "YAML", "HTML", "CSS", "HTTP",
        "HTTPS", "URL", "URI", "SQL", "DB", "ID", "UUID", "PK", "OK", "TODO",
        "README", "SPEC", "CLAUDE", "ADR", "PR", "CI", "CD", "PJ", "SoT",
        "MUST", "NOT", "AND", "OR", "TTL", "CPU", "OSS", "UX", "UI", "E2E",
        "TDD", "SDD", "MCP", "SDK", "CC", "WoW", "TOP", "N", "AI", "ASCII",
        "ROI", "CJK", "NFD", "NaN", "LR", "GitHub",
        # SQL / 制御キーワード（jargon でない）
        "INSERT", "INTO", "ON", "DO", "NOTHING", "IGNORE", "CONFLICT", "BLOCK",
        # skill-triage の状態・hook イベント名・ツール名（英語の制御語で decode 不要）
        "CREATE", "UPDATE", "MERGE", "SPLIT", "SKIP", "REVIEW",
        "PreToolUse", "PostToolUse", "UserPromptSubmit", "AskUserQuestion",
        "MEMORY", "CHANGELOG",
    }
)

_SEP_RE = re.compile(r"^[:\-\s|]+$")


@dataclass
class GlossaryEntry:
    term: str
    meaning: str
    first_seen: str


@dataclass
class GlossaryReport:
    entries: list[GlossaryEntry] = field(default_factory=list)
    malformed_lines: list[tuple[int, str]] = field(default_factory=list)
    duplicate_terms: list[str] = field(default_factory=list)
    missing_first_seen: list[str] = field(default_factory=list)
    undefined_terms: list[str] = field(default_factory=list)

    def has_drift(self) -> bool:
        """構造的 drift（用語集自体の整合性破れ）。CLI の gate 対象。

        undefined_terms はヒューリスティックで誤検出を含むため gate しない
        （has_undefined で別に取る）。オオカミ少年化を避ける。
        """
        return bool(
            self.malformed_lines
            or self.duplicate_terms
            or self.missing_first_seen
        )

    def has_undefined(self) -> bool:
        """SoT に出現する未登録 jargon 候補がある（advisory・非 gate）。"""
        return bool(self.undefined_terms)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_glossary(path: str) -> tuple[list[GlossaryEntry], list[tuple[int, str]]]:
    """CONTEXT.md の Markdown テーブルから用語エントリを抽出する。

    返り値: (entries, malformed_lines)。malformed_lines は (行番号, 生の行)。
    ヘッダ行（用語/意味を含む）と区切り行（---）はスキップする。
    """
    entries: list[GlossaryEntry] = []
    malformed: list[tuple[int, str]] = []
    raw = ""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return entries, malformed

    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _SEP_RE.match(stripped):
            continue
        cells = _split_row(stripped)
        # ヘッダ行
        if any("用語" in c for c in cells) and any("意味" in c for c in cells):
            continue
        if len(cells) != 3 or not cells[0]:
            malformed.append((lineno, line))
            continue
        entries.append(GlossaryEntry(term=cells[0], meaning=cells[1], first_seen=cells[2]))
    return entries, malformed


def find_undefined_terms(
    entries: list[GlossaryEntry],
    source_paths: list[str],
    *,
    stoplist: frozenset[str] = DEFAULT_STOPLIST,
) -> list[str]:
    """SoT に出現する jargon 候補で用語集に未登録のものを返す（ソート済み・一意）。"""
    defined = {e.term for e in entries}
    found: set[str] = set()
    for sp in source_paths:
        try:
            with open(sp, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            continue
        for m in _CANDIDATE_RE.finditer(text):
            tok = m.group(1)
            if tok in defined or tok in stoplist:
                continue
            found.add(tok)
    return sorted(found)


def check_glossary(
    context_path: str,
    source_paths: list[str],
    *,
    stoplist: frozenset[str] = DEFAULT_STOPLIST,
) -> GlossaryReport:
    entries, malformed = parse_glossary(context_path)

    seen: set[str] = set()
    dups: list[str] = []
    missing: list[str] = []
    for e in entries:
        if e.term in seen and e.term not in dups:
            dups.append(e.term)
        seen.add(e.term)
        if not e.first_seen.strip():
            missing.append(e.term)

    undefined = find_undefined_terms(entries, source_paths, stoplist=stoplist)
    return GlossaryReport(
        entries=entries,
        malformed_lines=malformed,
        duplicate_terms=dups,
        missing_first_seen=missing,
        undefined_terms=undefined,
    )


def _format_report(report: GlossaryReport) -> str:
    lines = [f"用語集エントリ: {len(report.entries)} 件"]
    if report.malformed_lines:
        lines.append(f"  ⚠ スキーマ不一致行: {len(report.malformed_lines)}")
        for ln, raw in report.malformed_lines:
            lines.append(f"      L{ln}: {raw.strip()}")
    if report.duplicate_terms:
        lines.append(f"  ⚠ 重複定義: {', '.join(report.duplicate_terms)}")
    if report.missing_first_seen:
        lines.append(f"  ⚠ 初出欠落: {', '.join(report.missing_first_seen)}")
    if not report.has_drift():
        lines.append("  ✓ 構造 drift なし")
    if report.undefined_terms:
        lines.append(
            f"  ℹ advisory: 用語集未登録の jargon 候補 ({len(report.undefined_terms)}) "
            f"— 追記を検討: {', '.join(report.undefined_terms)}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: glossary_drift.py CONTEXT.md [SOURCE.md ...]", file=sys.stderr)
        return 2
    context_path, source_paths = argv[0], argv[1:]
    report = check_glossary(context_path, source_paths)
    print(_format_report(report))
    return 1 if report.has_drift() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
