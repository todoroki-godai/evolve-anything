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
    evolve_search(candidates, fitness_fn, generations, offspring_count, ...) -> dict

SkillOpt 近似 (#305):
    evolve_generation は単一世代の crossover/mutate のみで「訓練」の反復が無い。
    evolve_search は fitness_fn を勾配代理として **多世代** 進化させ、
    エリート保存で best fitness の単調非減少（=勾配上昇の近似）を保証する。
    fitness_fn は呼び出し側が注入する純粋関数（LLM 非依存・決定論を維持）。
    論文準拠の SkillOpt 実装が公開されたら fitness_fn を差し替える前提（[ADR-035]）。
"""

import random
import re
from typing import Callable, Dict, List, Optional

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


# ── evolve_search（多世代 / SkillOpt 近似 #305）───────────────────────────


def evolve_search(
    candidates: List[Dict],
    fitness_fn: Callable[[str], float],
    generations: int,
    offspring_count: int,
    corrections: Optional[List[str]] = None,
    *,
    patience: int = 3,
    epsilon: float = 1e-4,
    rng: Optional[random.Random] = None,
) -> Dict:
    """fitness_fn を勾配代理として多世代の進化探索を回す（SkillOpt 近似）。

    「スキルを訓練可能な対象として勾配的に最適化する」発想を、既存の
    evolution_operators（crossover/mutate/select）の枠内でエリート保存付き
    多世代探索として近似する。各世代で:

      1. 現集団を fitness_fn で再評価する（注入 fitness が真の信号）。
      2. evolve_generation で子集団を生成する。
      3. 親 + 子の中から best を含む top を次世代に引き継ぐ（エリート保存）。

    エリート保存により best fitness は世代をまたいで単調非減少になる
    （= 勾配上昇の近似）。patience 世代連続で改善幅が epsilon 未満なら
    収束とみなして早期停止する。

    完全決定論: fitness_fn が純粋関数なら、同一 rng seed で出力は再現する。
    LLM/subprocess は一切呼ばない（fitness_fn の中身は呼び出し側の責務）。

    Args:
        candidates:      初期集団 [{"content": str, ...}, ...]。
        fitness_fn:      content -> float (0.0–1.0 推奨)。勾配代理の信号。
        generations:     最大世代数。0 なら初期 best のみ返す。
        offspring_count: 各世代で生成する子の数。
        corrections:     mutate に渡す corrections（任意）。
        patience:        改善なし世代数の許容上限（早期停止）。
        epsilon:         「改善あり」とみなす最小差分。
        rng:             random.Random。未指定時は内部生成。

    Returns:
        {
            "best": {"content": str, "fitness": float} | None,
            "best_fitness_history": list[float],  # 世代ごとの best fitness
            "generations_run": int,               # 実際に回した世代数
            "converged": bool,                    # 早期停止したか
        }
    """
    empty_result = {
        "best": None,
        "best_fitness_history": [],
        "generations_run": 0,
        "converged": False,
    }
    if not candidates:
        return empty_result
    if rng is None:
        rng = random.Random()

    def _evaluate(pool: List[Dict]) -> List[Dict]:
        """各候補に fitness_fn を適用した新 dict のリストを返す。"""
        scored: List[Dict] = []
        for c in pool:
            content = c.get("content", "")
            scored.append({"content": content, "fitness": float(fitness_fn(content))})
        return scored

    # 初期集団を真の fitness で評価
    population = _evaluate(candidates)
    best = max(population, key=lambda c: c["fitness"])
    history: List[float] = []
    converged = False
    no_improve = 0
    gens_run = 0

    if generations <= 0:
        return {
            "best": {"content": best["content"], "fitness": best["fitness"]},
            "best_fitness_history": [],
            "generations_run": 0,
            "converged": False,
        }

    for _ in range(generations):
        gens_run += 1

        # 子集団を生成し fitness 評価
        offspring = evolve_generation(
            population, offspring_count, corrections=corrections, rng=rng
        )
        offspring_scored = _evaluate(offspring)

        # エリート保存: 親 + 子を fitness 降順に並べ、上位を次世代へ。
        # 集団サイズは初期サイズを維持し、best は必ず残る（単調性保証）。
        combined = population + offspring_scored
        combined.sort(key=lambda c: c["fitness"], reverse=True)
        pop_size = max(len(candidates), 1)
        population = combined[:pop_size]

        gen_best = population[0]
        improvement = gen_best["fitness"] - best["fitness"]
        # best は単調非減少（エリート保存で gen_best >= best が保証される）
        if gen_best["fitness"] > best["fitness"]:
            best = gen_best
        history.append(best["fitness"])

        if improvement < epsilon:
            no_improve += 1
        else:
            no_improve = 0
        if no_improve >= patience:
            converged = True
            break

    return {
        "best": {"content": best["content"], "fitness": best["fitness"]},
        "best_fitness_history": history,
        "generations_run": gens_run,
        "converged": converged,
    }
