"""Parallel Optimization: references/ 並行最適化 + De-dup consolidation"""
from __future__ import annotations
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# デフォルト並行数
DEFAULT_PARALLEL: int = 4


@dataclass
class OptimizeResult:
    """1ファイルの最適化結果"""
    path: str
    best_fitness: float | None = None
    best_content: str = ""
    error: str | None = None
    is_reference: bool = False


@dataclass
class ParallelPlan:
    """並行最適化の実行計画"""
    references: list[Path] = field(default_factory=list)
    main_skill: Path | None = None
    parallel: int = DEFAULT_PARALLEL


def detect_references(skill_path: Path) -> list[Path]:
    """スキルディレクトリ内の references/ ファイルを検出。

    Args:
        skill_path: SKILL.md 等のパス

    Returns:
        references/ 配下の .md ファイル一覧（ソート済み）
    """
    refs_dir = skill_path.parent / "references"
    if not refs_dir.is_dir():
        return []
    return sorted(refs_dir.glob("*.md"))


def build_plan(skill_path: Path, parallel: int = DEFAULT_PARALLEL) -> ParallelPlan:
    """最適化実行計画を構築。

    references/ があれば先に最適化し、その後 SKILL.md を最適化する。
    references/ がなければ SKILL.md のみ。
    """
    refs = detect_references(skill_path)
    return ParallelPlan(
        references=refs,
        main_skill=skill_path,
        parallel=max(1, parallel),
    )


def run_parallel(
    plan: ParallelPlan,
    optimize_fn: Callable[[Path], OptimizeResult],
) -> list[OptimizeResult]:
    """並行最適化を実行。

    Phase 1: references/ ファイルを並行最適化
    Phase 2: SKILL.md を最適化（references/ 完了後）

    Args:
        plan: 実行計画
        optimize_fn: 1ファイルを最適化する関数

    Returns:
        全ファイルの最適化結果リスト
    """
    results: list[OptimizeResult] = []

    # Phase 1: references/ の並行最適化
    if plan.references:
        logger.info("Phase 1: references/ %d files (parallel=%d)", len(plan.references), plan.parallel)
        ref_results = _run_batch(plan.references, optimize_fn, plan.parallel, is_reference=True)
        results.extend(ref_results)

    # Phase 2: SKILL.md
    if plan.main_skill:
        logger.info("Phase 2: main skill %s", plan.main_skill)
        try:
            result = optimize_fn(plan.main_skill)
            result.is_reference = False
            results.append(result)
        except Exception as e:
            logger.error("Main skill optimization failed: %s", e)
            results.append(OptimizeResult(
                path=str(plan.main_skill),
                error=str(e),
                is_reference=False,
            ))

    return results


def _run_batch(
    files: list[Path],
    optimize_fn: Callable[[Path], OptimizeResult],
    parallel: int,
    is_reference: bool = False,
) -> list[OptimizeResult]:
    """ファイルバッチを並行実行。"""
    results: list[OptimizeResult] = []

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        future_to_path = {
            executor.submit(_safe_optimize, optimize_fn, f): f
            for f in files
        }
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            result = future.result()
            result.is_reference = is_reference
            results.append(result)

    return results


def _safe_optimize(
    optimize_fn: Callable[[Path], OptimizeResult],
    path: Path,
) -> OptimizeResult:
    """例外を捕捉して OptimizeResult に変換。"""
    try:
        return optimize_fn(path)
    except Exception as e:
        logger.error("Optimization failed for %s: %s", path, e)
        return OptimizeResult(path=str(path), error=str(e))


def dedup_consolidate(results: list[OptimizeResult], similarity_threshold: float = 0.95) -> list[OptimizeResult]:
    """重複コンテンツを除去して結果を統合。

    同一または類似のコンテンツを持つ結果を統合する。
    fitness が高い方を残す。

    Args:
        results: 最適化結果リスト
        similarity_threshold: 類似度閾値（内容ハッシュベース、0.95 = ほぼ同一のみ）

    Returns:
        重複除去後の結果リスト
    """
    if not results:
        return results

    seen: dict[str, OptimizeResult] = {}  # content_hash -> best result
    deduped: list[OptimizeResult] = []

    for r in results:
        if r.error or not r.best_content:
            deduped.append(r)
            continue

        content_hash = _content_hash(r.best_content)

        if content_hash in seen:
            existing = seen[content_hash]
            # fitness が高い方を残す
            if (r.best_fitness or 0) > (existing.best_fitness or 0):
                logger.info("Dedup: replacing %s (%.3f) with %s (%.3f)",
                            existing.path, existing.best_fitness or 0,
                            r.path, r.best_fitness or 0)
                seen[content_hash] = r
        else:
            seen[content_hash] = r

    # エラー結果 + dedup 済み結果
    deduped.extend(seen.values())
    return deduped


def _content_hash(content: str) -> str:
    """コンテンツの正規化ハッシュを返す。

    空白・改行を正規化してからハッシュ化。
    """
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
