"""Git common dir を共有バスにする最小 agent coordination（#268）。

tracked artifact は作らない。lane state / handoff evidence は各 worktree が共有する
git common dir 配下に保持し、コードの SoT は commit のままにする。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

RunFunc = Callable[..., subprocess.CompletedProcess[str]]

_TASK_ID_RE = re.compile(r"^[0-9]+-[a-z0-9][a-z0-9-]*$")
_RUNTIMES = {"claude", "codex"}
_STATE_DIR = "evolve-agents"


class CoordinationError(RuntimeError):
    """lane の取得・handoff・解放契約に違反した。"""


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    run: RunFunc = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    result = run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise CoordinationError(
            f"{' '.join(cmd)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_task_id(task_id: str) -> str:
    if not _TASK_ID_RE.fullmatch(task_id):
        raise CoordinationError("task_id は '<issue>-<slug>' 形式で指定してください")
    return task_id


def _validate_runtime(runtime: str) -> str:
    if runtime not in _RUNTIMES:
        raise CoordinationError("runtime は claude または codex です")
    return runtime


def normalize_owned_paths(paths: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in paths:
        value = str(raw).strip().replace("\\", "/")
        candidate = PurePosixPath(value)
        if (
            not value
            or value == "."
            or candidate.is_absolute()
            or ".." in candidate.parts
        ):
            raise CoordinationError(f"owned path は repo 相対pathで指定してください: {raw!r}")
        clean = candidate.as_posix().rstrip("/")
        if clean not in normalized:
            normalized.append(clean)
    if not normalized:
        raise CoordinationError("owned_paths は1件以上必要です")
    return tuple(normalized)


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(right + "/")
        or right.startswith(left + "/")
    )


def _git_common_dir(repo: Path, *, run: RunFunc = subprocess.run) -> Path:
    raw = _run(["git", "rev-parse", "--git-common-dir"], cwd=repo, run=run).stdout.strip()
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _state_root(repo: Path, *, run: RunFunc = subprocess.run) -> Path:
    return _git_common_dir(repo, run=run) / _STATE_DIR


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoordinationError(f"lane state を読めません: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoordinationError(f"lane state がobjectではありません: {path}")
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _acquire_lock(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "coordination.lock"
    try:
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise CoordinationError(
            f"coordination lock が存在します。実行中laneを確認してください: {lock}"
        ) from exc


def _release_lock(root: Path, descriptor: int) -> None:
    os.close(descriptor)
    (root / "coordination.lock").unlink(missing_ok=True)


def _active_manifests(root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in sorted((root / "lanes").glob("*.json")):
        value = _read_json(path)
        if value.get("status") == "active":
            manifests.append(value)
    return manifests


def _worktree_candidate(root: Path, repo: Path, runtime: str, task_id: str) -> Path:
    for _ in range(20):
        suffix = secrets.token_hex(4)
        candidate = root / f"{repo.name}-{runtime}-{task_id}-{suffix}"
        if not candidate.exists():
            return candidate
    raise CoordinationError("一意なworktree pathを生成できませんでした")


def start_lane(
    repo: Path,
    *,
    task_id: str,
    runtime: str,
    owned_paths: Sequence[str],
    base: str = "main",
    worktree_root: Path = Path("/private/tmp"),
    run: RunFunc = subprocess.run,
) -> dict[str, Any]:
    """ownership 取得と repo 外 worktree 作成を1つの排他区間で行う。"""
    repo = Path(repo).resolve()
    task_id = _validate_task_id(task_id)
    runtime = _validate_runtime(runtime)
    owned = normalize_owned_paths(owned_paths)
    external_root = Path(worktree_root).resolve()
    try:
        external_root.relative_to(repo)
    except ValueError:
        pass
    else:
        raise CoordinationError("top-level executor worktree はrepo外へ作成してください")

    root = _state_root(repo, run=run)
    descriptor = _acquire_lock(root)
    branch = f"{runtime}/{task_id}"
    worktree = _worktree_candidate(external_root, repo, runtime, task_id)
    try:
        lane_path = root / "lanes" / f"{task_id}.json"
        if lane_path.exists() and _read_json(lane_path).get("status") == "active":
            raise CoordinationError(f"task_id は取得済みです: {task_id}")
        for existing in _active_manifests(root):
            for new_path in owned:
                for existing_path in existing.get("owned_paths") or []:
                    if _paths_overlap(new_path, str(existing_path)):
                        raise CoordinationError(
                            f"owned_paths が {existing.get('task_id')} と重複します: "
                            f"{new_path} ↔ {existing_path}"
                        )

        _run(
            ["git", "worktree", "add", str(worktree), "-b", branch, base],
            cwd=repo,
            run=run,
        )
        base_sha = _run(["git", "rev-parse", "HEAD"], cwd=worktree, run=run).stdout.strip()
        manifest = {
            "schema_version": 1,
            "task_id": task_id,
            "runtime": runtime,
            "branch": branch,
            "worktree": str(worktree),
            "base": base,
            "base_sha": base_sha,
            "owned_paths": list(owned),
            "status": "active",
            "started_at": _now(),
        }
        _atomic_write(lane_path, manifest)
        return manifest
    finally:
        _release_lock(root, descriptor)


def _active_lane(repo: Path, task_id: str, *, run: RunFunc) -> tuple[Path, dict[str, Any]]:
    root = _state_root(repo, run=run)
    path = root / "lanes" / f"{_validate_task_id(task_id)}.json"
    if not path.exists():
        raise CoordinationError(f"lane が見つかりません: {task_id}")
    lane = _read_json(path)
    if lane.get("status") != "active":
        raise CoordinationError(f"lane はactiveではありません: {task_id}")
    return path, lane


def handoff_lane(
    repo: Path,
    *,
    task_id: str,
    verification: Sequence[str],
    open_risks: Sequence[str] = (),
    decisions: Sequence[str] = (),
    run: RunFunc = subprocess.run,
) -> dict[str, Any]:
    """clean commit の証拠を git common dir へimmutableに保存する。"""
    repo = Path(repo).resolve()
    _path, lane = _active_lane(repo, task_id, run=run)
    worktree = Path(str(lane["worktree"]))
    dirty = _run(["git", "status", "--porcelain"], cwd=worktree, run=run).stdout
    if dirty.strip():
        raise CoordinationError("dirty worktree からhandoffは作成できません")
    branch = _run(
        ["git", "branch", "--show-current"], cwd=worktree, run=run
    ).stdout.strip()
    if branch != lane["branch"]:
        raise CoordinationError(
            f"branch drift: expected={lane['branch']} actual={branch or '(detached)'}"
        )
    head_sha = _run(["git", "rev-parse", "HEAD"], cwd=worktree, run=run).stdout.strip()
    changed_raw = _run(
        ["git", "diff", "--name-only", f"{lane['base_sha']}..{head_sha}"],
        cwd=worktree,
        run=run,
    ).stdout
    changed_files = [line for line in changed_raw.splitlines() if line]
    for changed in changed_files:
        if not any(_paths_overlap(changed, allowed) for allowed in lane["owned_paths"]):
            raise CoordinationError(f"変更がowned_paths外です: {changed}")
    evidence = {
        "schema_version": 1,
        "task_id": task_id,
        "runtime": lane["runtime"],
        "branch": branch,
        "base_sha": lane["base_sha"],
        "head_sha": head_sha,
        "owned_paths": lane["owned_paths"],
        "changed_files": changed_files,
        "verification": list(verification),
        "decisions": list(decisions),
        "open_risks": list(open_risks),
        "next_action": "review",
        "created_at": _now(),
    }
    destination = (
        _state_root(repo, run=run)
        / "handoffs"
        / task_id
        / f"{head_sha}.json"
    )
    if destination.exists():
        existing = _read_json(destination)
        if existing != evidence:
            raise CoordinationError(f"同一HEADのhandoffが既に存在します: {destination}")
        return existing
    _atomic_write(destination, evidence)
    return evidence


def finish_lane(
    repo: Path,
    *,
    task_id: str,
    run: RunFunc = subprocess.run,
) -> dict[str, Any]:
    """lane を解放する。worktree・branch・commitは削除しない。"""
    repo = Path(repo).resolve()
    root = _state_root(repo, run=run)
    descriptor = _acquire_lock(root)
    try:
        path, lane = _active_lane(repo, task_id, run=run)
        lane["status"] = "finished"
        lane["finished_at"] = _now()
        _atomic_write(path, lane)
        return lane
    finally:
        _release_lock(root, descriptor)
