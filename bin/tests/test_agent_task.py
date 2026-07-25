"""evolve-agent-task のlocal git契約テスト（#268）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from agent_coordination.core import (  # noqa: E402
    CoordinationError,
    finish_lane,
    handoff_lane,
    normalize_owned_paths,
    start_lane,
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "scripts").mkdir()
    (repo / "docs").mkdir()
    (repo / "scripts" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "docs" / "README.md").write_text("docs\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_owned_paths_reject_empty_absolute_parent_and_dot() -> None:
    for value in ([], ["/tmp/x"], ["../x"], ["."]):
        with pytest.raises(CoordinationError):
            normalize_owned_paths(value)


def test_start_is_atomic_and_rejects_overlapping_lane(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    first = start_lane(
        repo,
        task_id="268-core",
        runtime="codex",
        owned_paths=["scripts"],
        worktree_root=worktrees,
    )
    assert Path(first["worktree"]).parent == worktrees
    assert not Path(first["worktree"]).is_relative_to(repo)
    assert first["branch"] == "codex/268-core"
    with pytest.raises(CoordinationError, match="重複"):
        start_lane(
            repo,
            task_id="268-cli",
            runtime="claude",
            owned_paths=["scripts/app.py"],
            worktree_root=worktrees,
        )
    assert not subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/claude/268-cli"],
        cwd=repo,
    ).returncode == 0


def test_handoff_requires_clean_tree_and_owned_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    lane = start_lane(
        repo,
        task_id="268-core",
        runtime="codex",
        owned_paths=["scripts"],
        worktree_root=worktrees,
    )
    worktree = Path(lane["worktree"])
    (worktree / "scripts" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(CoordinationError, match="dirty"):
        handoff_lane(repo, task_id="268-core", verification=["pytest: pass"])
    subprocess.run(["git", "add", "scripts/app.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=worktree, check=True)
    evidence = handoff_lane(
        repo,
        task_id="268-core",
        verification=["pytest: pass"],
        open_risks=["full suite pending"],
    )
    assert evidence["head_sha"] != evidence["base_sha"]
    assert evidence["changed_files"] == ["scripts/app.py"]
    assert evidence["next_action"] == "review"
    state = (
        repo
        / subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    ).resolve()
    assert (state / "evolve-agents" / "handoffs" / "268-core" / f"{evidence['head_sha']}.json").exists()


def test_handoff_rejects_outside_owned_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    lane = start_lane(
        repo,
        task_id="268-core",
        runtime="codex",
        owned_paths=["scripts"],
        worktree_root=worktrees,
    )
    worktree = Path(lane["worktree"])
    (worktree / "docs" / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/README.md"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "outside"], cwd=worktree, check=True)
    with pytest.raises(CoordinationError, match="owned_paths外"):
        handoff_lane(repo, task_id="268-core", verification=["pytest: pass"])


def test_finish_releases_lane_without_deleting_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    lane = start_lane(
        repo,
        task_id="268-core",
        runtime="codex",
        owned_paths=["scripts"],
        worktree_root=worktrees,
    )
    finished = finish_lane(repo, task_id="268-core")
    assert finished["status"] == "finished"
    assert Path(lane["worktree"]).exists()
    with pytest.raises(CoordinationError, match="active"):
        finish_lane(repo, task_id="268-core")


def test_cli_prints_json(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    script = Path(__file__).resolve().parents[1] / "evolve-agent-task"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
            "start",
            "--task-id",
            "268-cli",
            "--runtime",
            "codex",
            "--owned-path",
            "scripts",
            "--worktree-root",
            str(worktrees),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["task_id"] == "268-cli"
