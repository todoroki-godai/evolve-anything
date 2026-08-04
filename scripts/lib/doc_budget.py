"""doc_budget.py — hot ドキュメント（SPEC.md / CLAUDE.md / spec/**.md）の byte/セクション/
ポインタ予算を決定論・LLM 非依存で検査する（#319）。

背景（#318 の再発防止）: spec-keeper SKILL.md には既に「MUST: 更新後の bytes を確認」と
書いてあるが、`install ≠ enforcement` / `SKILL.md の MUST ≠ enforcement` と同型で
spec-keeper を起動しなければ一度も走らない。SPEC.md が 41,142 bytes まで肥大してから
初めて気づいた（#318）。本モジュールは検査を audit advisory + pre-push light に常設し、
人手の `wc -c` 依存を断つ。

3 種の検査（(a)(b)(c)、issue #319）:
  - check_file_budgets:    ファイル単位の byte 予算（SPEC.md / CLAUDE.md / spec/**.md）
  - check_section_budgets: `## ` セクション単位の byte 予算（同じファイル群）
  - check_pointer_refs:    SPEC.md / CLAUDE.md 内マークダウンリンクの実在突合（hook_drift の
                           dead_ref と同型）

閾値の単一ソース: SPEC.md 35KB(MUST)/20KB(healthy)・単一 md ファイル 100KB(MUST)/50KB(healthy)
は spec-keeper SKILL.md（既存の運用値、根拠は issue #216）をそのまま採用。CLAUDE.md の
60KB(MUST)/40KB(healthy) は本 issue で新規に決めた暫定値（コメント参照）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# --- (a) ファイル単位の byte 予算 --------------------------------------------


@dataclass(frozen=True)
class FileBudget:
    must_bytes: int
    healthy_bytes: int


# SPEC.md: spec-keeper SKILL.md line 36 が出典（根拠: issue #216、Read ツールの実質上限
# ≈25K tokens 超で丸読み truncate される実害）。値は変えない。
SPEC_MD_BUDGET = FileBudget(must_bytes=35 * 1024, healthy_bytes=20 * 1024)

# CLAUDE.md: #319 で新規に決めた暫定値。CLAUDE.md は毎セッション自動注入されるため実コストは
# per-session token。実測 2026-08-04 時点で 56,223 bytes（healthy 超過・MUST 未満）— いきなり
# ⚠ にして恒久ノイズにせず、まず ℹ で気づける値を選んだ。実測を重ねて見直してよい。
CLAUDE_MD_BUDGET = FileBudget(must_bytes=60 * 1024, healthy_bytes=40 * 1024)

# spec/**.md: 単一 md ファイルの一般則（spec-keeper SKILL.md line 36 が出典、同じく変えない）。
SINGLE_MD_BUDGET = FileBudget(must_bytes=100 * 1024, healthy_bytes=50 * 1024)


@dataclass(frozen=True)
class FileBudgetFinding:
    """ファイル単位の byte 予算超過（healthy 超 or MUST 超）。

    path:     repo_root からの相対 POSIX パス。
    severity: "must"（MUST 閾値超過）| "healthy"（healthy 閾値超過・MUST 未満）。
    """

    path: str
    byte_size: int
    must_bytes: int
    healthy_bytes: int
    severity: str


def _classify(byte_size: int, budget: FileBudget) -> Optional[str]:
    if byte_size > budget.must_bytes:
        return "must"
    if byte_size > budget.healthy_bytes:
        return "healthy"
    return None


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def check_file_budgets(repo_root: Path) -> List[FileBudgetFinding]:
    """SPEC.md / CLAUDE.md / spec/**.md の byte 予算超過を検出する（決定論）。

    healthy 閾値未満のファイルは含めない（clean 時は空リスト）。不在ファイルはスキップする。
    """
    repo_root = Path(repo_root)
    findings: List[FileBudgetFinding] = []

    targets = [
        (repo_root / "SPEC.md", SPEC_MD_BUDGET),
        (repo_root / "CLAUDE.md", CLAUDE_MD_BUDGET),
    ]
    spec_dir = repo_root / "spec"
    if spec_dir.is_dir():
        targets.extend((p, SINGLE_MD_BUDGET) for p in sorted(spec_dir.rglob("*.md")))

    for path, budget in targets:
        if not path.is_file():
            continue
        try:
            byte_size = path.stat().st_size
        except OSError:
            continue
        severity = _classify(byte_size, budget)
        if severity is None:
            continue
        findings.append(
            FileBudgetFinding(
                path=_rel_posix(path, repo_root),
                byte_size=byte_size,
                must_bytes=budget.must_bytes,
                healthy_bytes=budget.healthy_bytes,
                severity=severity,
            )
        )
    return findings


# --- (b) セクション単位の byte 予算 -------------------------------------------

# 「セクション単体が (ファイル合計の 40% 超 かつ 4KB 超) または 8KB 超」。
# 合成 fixture でなく実データで較正した値（#262/#166 の教訓）。実測（2026-08-04・#319）:
# 全ファイルを対象にすると 6 件（うち 4 件は healthy 内のファイル内の大セクション＝無害）。
# `_section_budget_scope` で入口を healthy 超過ファイルに絞り 3 件へ。#318 が問題にした
# 「SPEC.md の Recent Changes だけが 10KB(45.2%)」は絞った後も捕捉される。
SECTION_PCT_THRESHOLD = 40.0
SECTION_PCT_MIN_BYTES = 4 * 1024
SECTION_ABS_BYTES = 8 * 1024

_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class SectionBudgetFinding:
    file: str
    heading: str
    byte_size: int
    file_total_bytes: int
    pct: float


def _iter_sections(text: str) -> List[tuple[str, int]]:
    """`## ` 見出し単位で本文を分割し (見出し, byte 数) を返す。

    見出し行〜次の `## ` 見出し直前までを 1 セクションとする。最初の `## ` より前の
    プリアンブル（タイトル・導入文）はセクションとして計上しない（file 合計には含まれる）。
    """
    matches = list(_H2_RE.finditer(text))
    sections: List[tuple[str, int]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        heading = m.group(1).strip()
        body = text[start:end]
        sections.append((heading, len(body.encode("utf-8"))))
    return sections


def _section_budget_scope(repo_root: Path) -> List[Path]:
    """セクション予算の検査対象ファイル一覧（#319 (b)）。

    **file 予算の healthy を超えているファイルに限る**。セクション粒度が要るのは
    「ファイルが予算に触れた時に、どこが太っているのかを指す」ためであって、健全な
    サイズのファイル内で 1 セクションの比率が高いこと自体は無害（例: 本文が実質 1 つの
    表である CLAUDE.md のコンポーネント表）。全ファイルを対象にすると実データで 6 件が
    恒久表示になり、この repo が既に抱えている「advisory 書きっぱなし」を増やすだけになる
    （観測→作用の変換率が落ちる）。healthy 超過を入口にすると #318 の検出力は保たれる
    （SPEC.md 41KB は MUST 超過なので Recent Changes が surface される）。
    """
    repo_root = Path(repo_root)
    targets = [
        (repo_root / "SPEC.md", SPEC_MD_BUDGET),
        (repo_root / "CLAUDE.md", CLAUDE_MD_BUDGET),
    ]
    spec_dir = repo_root / "spec"
    if spec_dir.is_dir():
        targets.extend((p, SINGLE_MD_BUDGET) for p in sorted(spec_dir.rglob("*.md")))

    scope: List[Path] = []
    for path, budget in targets:
        if not path.is_file():
            continue
        try:
            byte_size = path.stat().st_size
        except OSError:
            continue
        if _classify(byte_size, budget) is not None:
            scope.append(path)
    return scope


def check_section_budgets(repo_root: Path) -> List[SectionBudgetFinding]:
    """`## ` セクション単位の byte 予算超過を検出する（決定論）。"""
    repo_root = Path(repo_root)
    findings: List[SectionBudgetFinding] = []
    for path in _section_budget_scope(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total = len(text.encode("utf-8"))
        if total == 0:
            continue
        for heading, byte_size in _iter_sections(text):
            pct = byte_size / total * 100
            flagged = (pct > SECTION_PCT_THRESHOLD and byte_size > SECTION_PCT_MIN_BYTES) or (
                byte_size > SECTION_ABS_BYTES
            )
            if not flagged:
                continue
            findings.append(
                SectionBudgetFinding(
                    file=_rel_posix(path, repo_root),
                    heading=heading,
                    byte_size=byte_size,
                    file_total_bytes=total,
                    pct=pct,
                )
            )
    return findings


# --- (c) ポインタ実在の突合 ----------------------------------------------------

# SPEC.md / CLAUDE.md 内のマークダウンリンクのみが対象（issue #319 (c)）。hook_drift の
# dead_ref と同型: 移動時のリンク切れを恒久検出する予防的配線。
_POINTER_SCOPE_FILES = ("SPEC.md", "CLAUDE.md")

_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
# `<details><summary>...</summary>` を疑似見出しとして扱う（この repo の README.ja.md
# 慣習で `##`/`###` 見出しでなく summary にセクション名を持たせるパターンがあるため。
# 実データ較正で対応しないと CLAUDE.md → README.ja.md#適応度関数 等が偽陽性になる）。
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# GitHub 風スラッグ化: 英数字・アンダースコア・Unicode 文字（\w）・ハイフン・空白のみ残し、
# 小文字化して空白をハイフンに変換する（重複スラッグの `-1` サフィックスは非対応・#319 既知の限界）。
_SLUG_STRIP_RE = re.compile(r"[^\w\- ]", re.UNICODE)


@dataclass(frozen=True)
class PointerRefFinding:
    source_file: str
    link_text: str
    raw_target: str
    kind: str  # "missing_file" | "missing_anchor"


def _strip_fenced_code_blocks(text: str) -> str:
    """フェンスドコードブロック（```...```）の中身を空行に置換する（行数は保持）。

    コードブロック内に例示の `[text](url)` があってもリンクとして誤検出しないため。
    """
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            out.append("\n" if line.endswith("\n") else "")
            continue
        out.append("\n" if in_fence and line.endswith("\n") else ("" if in_fence else line))
    return "".join(out)


def _slugify(text: str) -> str:
    lowered = text.lower()
    cleaned = _SLUG_STRIP_RE.sub("", lowered)
    return cleaned.replace(" ", "-")


def _heading_slugs(md_text: str) -> set:
    texts = [m.group(1) for m in _HEADING_RE.finditer(md_text)]
    for m in _SUMMARY_RE.finditer(md_text):
        inner = _HTML_TAG_RE.sub("", m.group(1)).strip()
        if inner:
            texts.append(inner)
    return {_slugify(t) for t in texts}


def _extract_link_targets(text: str) -> List[tuple[str, str]]:
    """本文から (link_text, target) を抽出する（フェンスドコードブロックは除外）。"""
    stripped = _strip_fenced_code_blocks(text)
    results = []
    for m in _LINK_RE.finditer(stripped):
        link_text, raw_target = m.group(1), m.group(2)
        # タイトル付きリンク `(url "title")` はスペース以降を落とす。
        parts = raw_target.split(None, 1)
        target = parts[0] if parts else raw_target
        results.append((link_text, target))
    return results


def check_pointer_refs(repo_root: Path) -> List[PointerRefFinding]:
    """SPEC.md / CLAUDE.md 内リンクのリンク先ファイル・アンカーの実在を突合する（決定論）。

    FP 厳禁（hook_drift dead_ref と同方針）:
    - 外部 URL（http/https）・mailto は対象外
    - リンク先ファイルが存在しなければ missing_file（アンカーは判定しない）
    - アンカーは対象が実在する `.md` ファイルの場合のみ判定する（非 md / ディレクトリはスキップ）
    - 見出しスラッグは `##`〜`######` の ATX 見出しに加え `<summary>` タグの中身も対象にする
      （この repo の慣習的な疑似見出しパターンによる偽陽性を避けるため）
    """
    repo_root = Path(repo_root)
    findings: List[PointerRefFinding] = []
    heading_cache: dict = {}

    for name in _POINTER_SCOPE_FILES:
        src = repo_root / name
        if not src.is_file():
            continue
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for link_text, target in _extract_link_targets(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            path_part, _, anchor = target.partition("#")
            if path_part == "":
                resolved = src
            elif path_part.startswith("/"):
                # GitHub 慣習のルート相対リンク `/docs/foo.md`。`src.parent / "/docs/foo.md"` は
                # Path の仕様で左辺が捨てられ**ホストの絶対パス** `/docs/foo.md` を見に行くため、
                # 実在するリンクを missing_file と誤検出する。repo_root 起点で解決する。
                resolved = (repo_root / path_part.lstrip("/")).resolve()
            else:
                resolved = (src.parent / path_part).resolve()

            exists = resolved.is_dir() if path_part.endswith("/") else resolved.is_file()
            if not exists:
                findings.append(
                    PointerRefFinding(
                        source_file=name, link_text=link_text, raw_target=target, kind="missing_file"
                    )
                )
                continue

            if not anchor or resolved.suffix != ".md" or not resolved.is_file():
                continue

            if resolved not in heading_cache:
                try:
                    heading_cache[resolved] = _heading_slugs(resolved.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    heading_cache[resolved] = None

            slugs = heading_cache[resolved]
            if slugs is None:
                continue  # 対象ファイルを読めない → 判定不能（FP を避けて除外）
            if _slugify(anchor) not in slugs:
                findings.append(
                    PointerRefFinding(
                        source_file=name, link_text=link_text, raw_target=target, kind="missing_anchor"
                    )
                )

    return findings


# --- 集約レポート ---------------------------------------------------------------


@dataclass
class DocBudgetReport:
    applicable: bool
    file_findings: List[FileBudgetFinding]
    section_findings: List[SectionBudgetFinding]
    pointer_findings: List[PointerRefFinding]

    def has_findings(self) -> bool:
        return bool(self.file_findings or self.section_findings or self.pointer_findings)


def check_doc_budget(repo_root: Path) -> DocBudgetReport:
    """3 種の検査をまとめて実行する（audit advisory / dogfood light の共通入口）。

    applicable=False（SPEC.md も CLAUDE.md も無い PJ）のときは全リスト空。
    """
    repo_root = Path(repo_root)
    applicable = (repo_root / "SPEC.md").is_file() or (repo_root / "CLAUDE.md").is_file()
    if not applicable:
        return DocBudgetReport(
            applicable=False, file_findings=[], section_findings=[], pointer_findings=[]
        )
    return DocBudgetReport(
        applicable=True,
        file_findings=check_file_budgets(repo_root),
        section_findings=check_section_budgets(repo_root),
        pointer_findings=check_pointer_refs(repo_root),
    )
