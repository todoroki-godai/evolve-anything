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
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .project_loader import enumerate_memory_dirs

_INDEX_FILENAME = "MEMORY.md"
_SNIPPET_WIDTH = 160
_DESC_BOOST = 2.0
_NAME_BOOST = 3.0
_INDEX_PENALTY = 0.5  # MEMORY.md index 行は fact 本体より下位に（dedup 意図）
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")  # [[name]] 相互リンク（#11）
_LINK_EXPAND_TOPN = 5  # 1-hop 展開する直接 hit の上限（爆発防止・深さ 1 固定）


@dataclass
class Fact:
    """1 つの memory fact ファイルのパース結果。"""

    file_path: Path
    name: str
    description: str
    body: str
    parse_ok: bool          # frontmatter から name/description を取れたか
    malformed_frontmatter: bool = False  # delimiter はあるが YAML 不正/未閉じ
    links: list[str] = field(default_factory=list)  # 本文中の [[name]] リンク（順序保持・一意化, #11）


@dataclass
class RecallHit:
    pj_display: str
    file_path: Path
    score: float
    snippet: str
    is_index: bool
    is_linked: bool = False              # 1-hop 展開由来（直接 hit でない, #11）
    linked: list["RecallHit"] = field(default_factory=list)  # この hit の [[link]] 1-hop 先（#11）


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _extract_links(body: str) -> list[str]:
    """本文中の `[[name]]` リンクを順序保持・一意化して抽出する（#11）。"""
    seen: dict[str, None] = {}
    for m in _LINK_RE.findall(body):
        name = m.strip()
        if name and name not in seen:
            seen[name] = None
    return list(seen)


def parse_fact_file(path: Path) -> Fact:
    """memory fact ファイルをパースする。

    frontmatter（先頭 `---` ブロック）から name/description を抽出。frontmatter が
    無い / 不正な場合は本文フォールバック（name = ファイル名 stem、body = 全文）。
    本文中の `[[name]]` リンクは links に抽出する（#11）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Fact(path, path.stem, "", "", parse_ok=False)

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # frontmatter 無し（MEMORY.md index 等）→ 本文フォールバック（壊れてはいない）
        body = text.strip()
        return Fact(path, path.stem, "", body, parse_ok=False, links=_extract_links(body))

    close_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close_idx is None:
        # 開きはあるが閉じが無い → malformed。全文を body にして grep で拾えるように
        return Fact(
            path, path.stem, "", text, parse_ok=False,
            malformed_frontmatter=True, links=_extract_links(text),
        )

    fm_block = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1:]).strip()
    try:
        data = yaml.safe_load(fm_block)
    except yaml.YAMLError:
        # YAML 不正 → 全文を body にフォールバック（frontmatter 領域も grep 対象に）
        return Fact(
            path, path.stem, "", text, parse_ok=False,
            malformed_frontmatter=True, links=_extract_links(text),
        )

    if not isinstance(data, dict):
        return Fact(
            path, path.stem, "", body, parse_ok=False,
            malformed_frontmatter=True, links=_extract_links(body),
        )

    name = str(data.get("name") or path.stem)
    description = str(data.get("description") or "")
    return Fact(path, name, description, body, parse_ok=True, links=_extract_links(body))


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
    # PJ ごとの全 fact（[[link]] 解決のためのインデックス: name/stem → Fact）
    pj_index: dict[str, dict[str, Fact]] = {}
    # 直接 hit と紐づく Fact（links 取得用）
    hit_facts: dict[int, Fact] = {}

    for mem in enumerate_memory_dirs(projects_root=projects_root):
        index: dict[str, Fact] = {}
        for md in sorted(mem.memory_dir.glob("*.md"), key=lambda p: p.name):
            fact = parse_fact_file(md)
            # link 解決インデックス（name / ファイル名 stem の両方で引けるように）
            index.setdefault(fact.name, fact)
            index.setdefault(md.stem, fact)
            if fact.malformed_frontmatter:
                print(f"warning: malformed frontmatter, body-grep fallback: {md}", file=sys.stderr)
            score = _score(fact, query_terms)
            if score <= 0:
                continue
            hit = RecallHit(
                pj_display=mem.pj_display,
                file_path=md,
                score=score,
                snippet=_make_snippet(fact.body, query_terms),
                is_index=(md.name == _INDEX_FILENAME),
            )
            hits.append(hit)
            hit_facts[id(hit)] = fact
        pj_index[mem.pj_display] = index

    hits.sort(key=lambda h: (-h.score, h.pj_display, h.file_path.name))
    top = hits[:limit]

    # 1-hop [[link]] 展開（トップ N hit のみ・深さ 1 固定）。決定論・スコア対象外（#11）。
    direct_paths = {h.file_path for h in top}
    for hit in top[:_LINK_EXPAND_TOPN]:
        fact = hit_facts.get(id(hit))
        if fact is None or not fact.links:
            continue
        hit.linked = _expand_links(fact, pj_index.get(hit.pj_display, {}),
                                   hit.pj_display, direct_paths, query_terms)

    return top


def _expand_links(
    fact: Fact,
    index: dict[str, Fact],
    pj_display: str,
    direct_paths: set[Path],
    query_terms: list[str],
) -> list[RecallHit]:
    """fact の `[[link]]` を同一 PJ 内で 1-hop 解決し linked hit を返す（#11）。

    - dangling link（解決できない name）は無視（memory 規約上 error ではない）
    - 直接 hit に既に含まれるファイルは重複させない
    - スコアは付けない（score=0.0, is_linked=True）。順序は本文中の出現順を保持
    """
    linked: list[RecallHit] = []
    emitted: set[Path] = set()
    for name in fact.links:
        target = index.get(name)
        if target is None:
            continue  # dangling
        path = target.file_path
        if path in direct_paths or path in emitted:
            continue  # 直接 hit / 既出は重複させない
        emitted.add(path)
        linked.append(
            RecallHit(
                pj_display=pj_display,
                file_path=path,
                score=0.0,
                snippet=_make_snippet(target.body, query_terms),
                is_index=(path.name == _INDEX_FILENAME),
                is_linked=True,
            )
        )
    return linked


def _hit_to_dict(h: RecallHit) -> dict:
    return {
        "pj_display": h.pj_display,
        "file_path": str(h.file_path),
        "score": round(h.score, 3),
        "snippet": h.snippet,
        "is_index": h.is_index,
    }


def reinforce_recall_hits(hits: list[RecallHit]) -> None:
    """recall ヒットした memory ファイルを access proxy として reinforce する（#18）。

    recall ヒット = その memory が「思い出すに値した」アクセス。これを唯一の access
    proxy として `reinforce_memory` を発火し、`last_reinforced_at` リセット +
    `importance_score` 上昇 + `update_count` インクリメントを行う（よく使う記憶ほど残る）。
    直接 hit と 1-hop linked 先の両方を対象にする。frontmatter なしファイルは no-op。

    `recall()` 本体の純粋性（副作用は stderr 警告のみ）を壊さないため、書き込み副作用
    を持つ本関数は CLI など発火境界からオプトインで呼ぶ。例外はサイレント（観測性より
    安定性優先・hook の慣例に揃える）。
    """
    try:
        from memory_temporal import reinforce_memory
    except ImportError:
        return

    seen: set[Path] = set()
    for hit in hits:
        targets = [hit, *hit.linked]
        for t in targets:
            if t.file_path in seen:
                continue
            seen.add(t.file_path)
            try:
                reinforce_memory(t.file_path, reason="recall hit")
            except Exception:
                pass  # 個別ファイルの失敗は無視（recall 体験を壊さない）


def format_hits(hits: list[RecallHit], *, as_json: bool) -> str:
    if as_json:
        payload = []
        for h in hits:
            d = _hit_to_dict(h)
            d["linked"] = [_hit_to_dict(lk) for lk in h.linked]
            payload.append(d)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if not hits:
        return "該当する memory はありませんでした。"
    out: list[str] = []
    for h in hits:
        tag = " [index]" if h.is_index else ""
        out.append(f"[{h.score:6.2f}] {h.pj_display}{tag}  {h.file_path.name}")
        if h.snippet:
            out.append(f"         {h.snippet}")
        for lk in h.linked:
            out.append(f"         ↳ linked: {lk.file_path.name}")
            if lk.snippet:
                out.append(f"             {lk.snippet}")
    return "\n".join(out)
