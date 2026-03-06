"""Tests for parallel optimization module."""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from parallel import (
    DEFAULT_PARALLEL,
    OptimizeResult,
    ParallelPlan,
    build_plan,
    dedup_consolidate,
    detect_references,
    run_parallel,
    _content_hash,
)


# --- detect_references ---

def test_detect_references_with_refs(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "a.md").write_text("# a", encoding="utf-8")
    (refs_dir / "b.md").write_text("# b", encoding="utf-8")
    (refs_dir / "ignore.txt").write_text("not md", encoding="utf-8")

    result = detect_references(skill_path)
    assert len(result) == 2
    assert result[0].name == "a.md"
    assert result[1].name == "b.md"


def test_detect_references_no_refs_dir(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")

    result = detect_references(skill_path)
    assert result == []


def test_detect_references_empty_refs_dir(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    (tmp_path / "references").mkdir()

    result = detect_references(skill_path)
    assert result == []


# --- build_plan ---

def test_build_plan_with_refs(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "ref.md").write_text("# ref", encoding="utf-8")

    plan = build_plan(skill_path, parallel=2)
    assert len(plan.references) == 1
    assert plan.main_skill == skill_path
    assert plan.parallel == 2


def test_build_plan_no_refs(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")

    plan = build_plan(skill_path)
    assert plan.references == []
    assert plan.main_skill == skill_path
    assert plan.parallel == DEFAULT_PARALLEL


def test_build_plan_parallel_min_1(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")

    plan = build_plan(skill_path, parallel=0)
    assert plan.parallel == 1

    plan = build_plan(skill_path, parallel=-5)
    assert plan.parallel == 1


# --- run_parallel ---

def _mock_optimize(path: Path) -> OptimizeResult:
    return OptimizeResult(
        path=str(path),
        best_fitness=0.85,
        best_content=f"optimized: {path.name}",
    )


def _mock_optimize_fail(path: Path) -> OptimizeResult:
    raise RuntimeError(f"fail: {path.name}")


def test_run_parallel_refs_then_main(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "a.md").write_text("# a", encoding="utf-8")
    (refs_dir / "b.md").write_text("# b", encoding="utf-8")

    plan = build_plan(skill_path, parallel=2)
    results = run_parallel(plan, _mock_optimize)

    # 2 refs + 1 main = 3 results
    assert len(results) == 3
    ref_results = [r for r in results if r.is_reference]
    main_results = [r for r in results if not r.is_reference]
    assert len(ref_results) == 2
    assert len(main_results) == 1
    assert all(r.best_fitness == 0.85 for r in results)


def test_run_parallel_no_refs(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")

    plan = build_plan(skill_path)
    results = run_parallel(plan, _mock_optimize)

    assert len(results) == 1
    assert not results[0].is_reference


def test_run_parallel_error_handling(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "a.md").write_text("# a", encoding="utf-8")

    plan = build_plan(skill_path, parallel=1)
    results = run_parallel(plan, _mock_optimize_fail)

    # Both should have errors, not raise
    assert len(results) == 2
    assert all(r.error is not None for r in results)


def test_run_parallel_main_error(tmp_path: Path) -> None:
    """Main skill failure is caught."""
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")

    plan = ParallelPlan(references=[], main_skill=skill_path, parallel=1)
    results = run_parallel(plan, _mock_optimize_fail)

    assert len(results) == 1
    assert results[0].error is not None
    assert not results[0].is_reference


# --- dedup_consolidate ---

def test_dedup_identical_content() -> None:
    results = [
        OptimizeResult(path="a.md", best_fitness=0.7, best_content="same content"),
        OptimizeResult(path="b.md", best_fitness=0.9, best_content="same content"),
    ]
    deduped = dedup_consolidate(results)

    # Only the higher fitness one survives
    content_results = [r for r in deduped if not r.error and r.best_content]
    assert len(content_results) == 1
    assert content_results[0].best_fitness == 0.9
    assert content_results[0].path == "b.md"


def test_dedup_different_content() -> None:
    results = [
        OptimizeResult(path="a.md", best_fitness=0.7, best_content="content A"),
        OptimizeResult(path="b.md", best_fitness=0.9, best_content="content B"),
    ]
    deduped = dedup_consolidate(results)

    content_results = [r for r in deduped if not r.error and r.best_content]
    assert len(content_results) == 2


def test_dedup_preserves_errors() -> None:
    results = [
        OptimizeResult(path="a.md", error="failed"),
        OptimizeResult(path="b.md", best_fitness=0.9, best_content="ok"),
    ]
    deduped = dedup_consolidate(results)
    assert len(deduped) == 2
    error_results = [r for r in deduped if r.error]
    assert len(error_results) == 1


def test_dedup_empty() -> None:
    assert dedup_consolidate([]) == []


def test_dedup_whitespace_normalization() -> None:
    """Content differing only in whitespace is treated as duplicate."""
    results = [
        OptimizeResult(path="a.md", best_fitness=0.7, best_content="same  content\n\n"),
        OptimizeResult(path="b.md", best_fitness=0.9, best_content="same content\n"),
    ]
    deduped = dedup_consolidate(results)

    content_results = [r for r in deduped if not r.error and r.best_content]
    assert len(content_results) == 1


# --- _content_hash ---

def test_content_hash_deterministic() -> None:
    h1 = _content_hash("hello world")
    h2 = _content_hash("hello world")
    assert h1 == h2


def test_content_hash_whitespace_invariant() -> None:
    h1 = _content_hash("hello  world\n")
    h2 = _content_hash("hello world")
    assert h1 == h2


def test_content_hash_different() -> None:
    h1 = _content_hash("hello")
    h2 = _content_hash("world")
    assert h1 != h2


# --- ordering guarantee ---

def test_refs_complete_before_main(tmp_path: Path) -> None:
    """Verify that references are processed before main skill."""
    execution_order: list[str] = []

    def tracking_optimize(path: Path) -> OptimizeResult:
        execution_order.append(path.name)
        return OptimizeResult(path=str(path), best_fitness=0.8, best_content="ok")

    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# skill", encoding="utf-8")
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "ref.md").write_text("# ref", encoding="utf-8")

    plan = build_plan(skill_path, parallel=1)
    run_parallel(plan, tracking_optimize)

    # ref.md must appear before SKILL.md
    assert execution_order.index("ref.md") < execution_order.index("SKILL.md")
