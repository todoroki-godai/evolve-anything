"""fleet recall — PJ 横断 memory recall の決定論 engine。

設計: `todoroki-main-design-20260528-133406.md`（D1: 1段・決定論 / D2: 別経路列挙）。

方針:
- LLM rerank は入れない。recall の消費者は呼び出し側 assistant（＝最強の reranker）。
  CLI 側は keyword prefilter → TF + frontmatter boost の決定論ランクに徹し、
  semantic 判断は呼び出し側に委ねる。
- frontmatter パース失敗は本文 grep フォールバック。delimiter があるのに壊れている
  ファイルだけ stderr に警告（静かな検索漏れを防ぐ）。

副作用: stderr への警告出力のみ（戻り値は純粋）。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .project_loader import enumerate_memory_dirs

_INDEX_FILENAME = "MEMORY.md"
_SNIPPET_WIDTH = 160
_DESC_BOOST = 2.0
_NAME_BOOST = 3.0
_INDEX_PENALTY = 0.5  # MEMORY.md index 行は fact 本体より下位に（dedup 意図）
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class Fact:
    """1 つの memory fact ファイルのパース結果。"""

    file_path: Path
    name: str
    description: str
    body: str
    parse_ok: bool          # frontmatter から name/description を取れたか
    malformed_frontmatter: bool = False  # delimiter はあるが YAML 不正/未閉じ


@dataclass
class RecallHit:
    pj_display: str
    file_path: Path
    score: float
    snippet: str
    is_index: bool


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def parse_fact_file(path: Path) -> Fact:
    """memory fact ファイルをパースする。

    frontmatter（先頭 `---` ブロック）から name/description を抽出。frontmatter が
    無い / 不正な場合は本文フォールバック（name = ファイル名 stem、body = 全文）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Fact(path, path.stem, "", "", parse_ok=False)

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # frontmatter 無し（MEMORY.md index 等）→ 本文フォールバック（壊れてはいない）
        return Fact(path, path.stem, "", text.strip(), parse_ok=False)

    close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close_idx is None:
        # 開きはあるが閉じが無い → malformed。全文を body にして grep で拾えるように
        return Fact(path, path.stem, "", text, parse_ok=False, malformed_frontmatter=True)

    fm_block = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1:]).strip()
    try:
        data = yaml.safe_load(fm_block)
    except yaml.YAMLError:
        # YAML 不正 → 全文を body にフォールバック（frontmatter 領域も grep 対象に）
        return Fact(path, path.stem, "", text, parse_ok=False, malformed_frontmatter=True)

    if not isinstance(data, dict):
        return Fact(path, path.stem, "", body, parse_ok=False, malformed_frontmatter=True)

    name = str(data.get("name") or path.stem)
    description = str(data.get("description") or "")
    return Fact(path, name, description, body, parse_ok=True)


def _make_snippet(body: str, query_terms: list[str]) -> str:
    """本文優先で query 周辺の snippet を抜く（frontmatter だけ出る事故を防ぐ）。"""
    flat = " ".join(body.split())
    if not flat:
        return ""
    low = flat.lower()
    pos = -1
    for term in query_terms:
        pos = low.find(term)
        if pos != -1:
            break
    if pos == -1:
        return flat[:_SNIPPET_WIDTH]
    start = max(0, pos - _SNIPPET_WIDTH // 3)
    snippet = flat[start:start + _SNIPPET_WIDTH]
    return ("…" if start > 0 else "") + snippet


def _score(fact: Fact, query_terms: list[str]) -> float:
    body_low = fact.body.lower()
    name_low = fact.name.lower()
    desc_low = fact.description.lower()
    tf = sum(body_low.count(t) for t in query_terms)
    name_hit = sum(1 for t in query_terms if t in name_low)
    desc_hit = sum(1 for t in query_terms if t in desc_low)
    if tf == 0 and name_hit == 0 and desc_hit == 0:
        return 0.0
    score = tf + _DESC_BOOST * desc_hit + _NAME_BOOST * name_hit
    if fact.file_path.name == _INDEX_FILENAME:
        score *= _INDEX_PENALTY
    return score


def recall(
    query: str,
    *,
    limit: int = 10,
    projects_root: Path | None = None,
) -> list[RecallHit]:
    """全 PJ の memory を横断し、query にマッチする fact を決定論ランクで返す。

    keyword prefilter（query token がどこかに出現）→ TF + description/filename ブースト。
    同点は (pj_display, ファイル名) で tie-break するため同じ query で順位不変。
    malformed frontmatter のファイルは stderr に警告して本文 grep で拾う。
    """
    query_terms = _tokenize(query)
    if not query_terms:
        return []

    hits: list[RecallHit] = []
    for mem in enumerate_memory_dirs(projects_root=projects_root):
        for md in sorted(mem.memory_dir.glob("*.md"), key=lambda p: p.name):
            fact = parse_fact_file(md)
            if fact.malformed_frontmatter:
                print(f"warning: malformed frontmatter, body-grep fallback: {md}", file=sys.stderr)
            score = _score(fact, query_terms)
            if score <= 0:
                continue
            hits.append(
                RecallHit(
                    pj_display=mem.pj_display,
                    file_path=md,
                    score=score,
                    snippet=_make_snippet(fact.body, query_terms),
                    is_index=(md.name == _INDEX_FILENAME),
                )
            )

    hits.sort(key=lambda h: (-h.score, h.pj_display, h.file_path.name))
    return hits[:limit]


def format_hits(hits: list[RecallHit], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            [
                {
                    "pj_display": h.pj_display,
                    "file_path": str(h.file_path),
                    "score": round(h.score, 3),
                    "snippet": h.snippet,
                    "is_index": h.is_index,
                }
                for h in hits
            ],
            ensure_ascii=False,
            indent=2,
        )
    if not hits:
        return "該当する memory はありませんでした。"
    out: list[str] = []
    for h in hits:
        tag = " [index]" if h.is_index else ""
        out.append(f"[{h.score:6.2f}] {h.pj_display}{tag}  {h.file_path.name}")
        if h.snippet:
            out.append(f"         {h.snippet}")
    return "\n".join(out)
