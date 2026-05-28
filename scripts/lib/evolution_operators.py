"""BES 前向き進化探索の進化演算子（決定論・LLM 非依存）。

genetic-prompt-optimizer の 1 パス直接パッチでは探索の多様性が低い。
crossover / mutation = 部分軌跡結合の進化演算子で局所最適を脱出する。

設計判断:
  - 完全決定論。LLM/subprocess は一切呼ばない（no-llm-in-tests と再現性のため）。
  - rng は注入可能。テストは random.Random(seed) で固定、未指定時は内部生成。

公開 API:
    crossover(parent_a, parent_b) -> str
    mutate(content, corrections=None) -> str
    select_parents(candidates, k, rng=None) -> list[dict]
    evolve_generation(candidates, offspring_count, corrections=None, rng=None) -> list[dict]
"""

import random
import re
from typing import Dict, List, Optional

# Markdown セクション見出し（`## ` 始まり）
_SECTION_RE = re.compile(r"^##\s", re.MULTILINE)


# ── frontmatter / セクション分解ヘルパー ──────────────────────────────


def _split_frontmatter(content: str) -> tuple:
    """先頭の `---...---` frontmatter と body を分離して返す。

    frontmatter がなければ ("", content) を返す。
    """
    if not content.startswith("---"):
        return "", content
    # 2 つ目の `---` 行までを frontmatter とみなす
    m = re.match(r"^(---\n.*?\n---\n)", content, re.DOTALL)
    if not m:
        return "", content
    fm = m.group(1)
    return fm, content[len(fm):]


def _split_sections(body: str) -> tuple:
    """body を「先頭(プリアンブル)」と「## セクション群」に分割する。

    Returns:
        (preamble: str, sections: list[str])
        各 section は `## ...` から次の `## ` 直前まで。
    """
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return body, []
    preamble = body[: matches[0].start()]
    sections: List[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append(body[start:end])
    return preamble, sections


def _section_title(section: str) -> str:
    """セクションの見出し行（正規化）を返す（重複判定キー）。"""
    first_line = section.splitlines()[0] if section.splitlines() else ""
    return first_line.strip().lower()


# ── crossover ─────────────────────────────────────────────────────────


def crossover(parent_a: str, parent_b: str) -> str:
    """2 つのテキストを Markdown セクション単位で決定論的に結合する。

    - frontmatter は parent_a のものを保持する。
    - preamble は parent_a のものを使う。
    - セクションは parent_a → parent_b の順に結合し、見出しが重複する
      セクションは parent_a 側を優先して 1 度だけ採用する（決定論）。
    """
    fm_a, body_a = _split_frontmatter(parent_a)
    _fm_b, body_b = _split_frontmatter(parent_b)

    preamble_a, sections_a = _split_sections(body_a)
    _preamble_b, sections_b = _split_sections(body_b)

    seen = set()
    merged: List[str] = []
    for section in list(sections_a) + list(sections_b):
        key = _section_title(section)
        if key in seen:
            continue
        seen.add(key)
        merged.append(section)

    child_body = preamble_a + "".join(merged)
    return fm_a + child_body


# ── mutate ────────────────────────────────────────────────────────────


def mutate(content: str, corrections: Optional[List[str]] = None) -> str:
    """セクションの並べ替え・連続重複行除去など決定論的な小変異。

    - frontmatter と preamble は保持する。
    - セクションを見出しのアルファベット順に安定ソートして並べ替える。
    - 連続する完全重複行を 1 行に畳む。
    - corrections があれば、対応する行を強調（行頭に `> ` 注記）する軽い変異。
    """
    fm, body = _split_frontmatter(content)
    preamble, sections = _split_sections(body)

    # セクションを見出しで安定ソート（決定論）
    sections_sorted = sorted(sections, key=_section_title)

    new_body = preamble + "".join(sections_sorted)

    # 連続重複行を除去
    deduped: List[str] = []
    prev: Optional[str] = None
    for line in new_body.splitlines(keepends=True):
        if line == prev and line.strip():
            continue
        deduped.append(line)
        prev = line
    new_body = "".join(deduped)

    # corrections 強調（軽い変異・決定論）
    if corrections:
        emphasized: List[str] = []
        seen_corr = set()
        for line in new_body.splitlines(keepends=True):
            stripped = line.rstrip("\n")
            matched = None
            for corr in corrections:
                c = (corr or "").strip()
                if c and c.lower() in stripped.lower() and c not in seen_corr:
                    matched = c
                    break
            emphasized.append(line)
            if matched is not None:
                seen_corr.add(matched)
                nl = "\n" if line.endswith("\n") else ""
                emphasized.append(f"> 重要: {matched}{nl}")
        new_body = "".join(emphasized)

    return fm + new_body


# ── select_parents ────────────────────────────────────────────────────


def select_parents(
    candidates: List[Dict],
    k: int,
    rng: Optional[random.Random] = None,
) -> List[Dict]:
    """fitness-proportional（ルーレット）選択で k 個の親を返す。

    各 candidate は {"content": str, "fitness": float} を持つ。
    fitness を重みにルーレット選択する。fitness が全て 0 以下の場合は
    一様選択にフォールバック（ゼロ除算回避）。

    Args:
        candidates: 候補リスト。
        k:          選択する親の数（重複あり = with replacement）。
        rng:        random.Random。未指定時は内部生成（非決定論）。

    Returns:
        選択された candidate dict のリスト（長さ k）。candidates が空なら [].
    """
    if not candidates:
        return []
    if rng is None:
        rng = random.Random()

    weights = [max(0.0, float(c.get("fitness", 0.0))) for c in candidates]
    total = sum(weights)
    if total <= 0.0:
        # 全 fitness が 0 / 負 → 一様選択にフォールバック
        weights = [1.0] * len(candidates)

    # rng.choices は with replacement のルーレット選択
    return rng.choices(candidates, weights=weights, k=k)


# ── evolve_generation ─────────────────────────────────────────────────


def evolve_generation(
    candidates: List[Dict],
    offspring_count: int,
    corrections: Optional[List[str]] = None,
    rng: Optional[random.Random] = None,
) -> List[Dict]:
    """select_parents で親ペアを選び crossover→mutate で子を生成する。

    Args:
        candidates:      {"content": str, "fitness": float} のリスト。
        offspring_count: 生成する子の数。
        corrections:     mutate に渡す corrections（任意）。
        rng:             random.Random。未指定時は内部生成。

    Returns:
        子候補 {"content": str} のリスト（長さ offspring_count）。
        candidates が空なら []。
    """
    if not candidates or offspring_count <= 0:
        return []
    if rng is None:
        rng = random.Random()

    # 親ペアを 2 * offspring_count 個選び 2 個ずつ消費
    parents = select_parents(candidates, k=offspring_count * 2, rng=rng)

    offspring: List[Dict] = []
    for i in range(offspring_count):
        parent_a = parents[2 * i]
        parent_b = parents[2 * i + 1]
        child_content = crossover(parent_a["content"], parent_b["content"])
        child_content = mutate(child_content, corrections=corrections)
        offspring.append({"content": child_content})

    return offspring
