"""``evolve-agent-task`` CLI。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import CoordinationError, finish_lane, handoff_lane, start_lane


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evolve-agent-task")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--task-id", required=True)
    start.add_argument("--runtime", choices=("claude", "codex"), required=True)
    start.add_argument("--owned-path", action="append", required=True)
    start.add_argument("--base", default="main")
    start.add_argument("--worktree-root", type=Path, default=Path("/private/tmp"))

    handoff = sub.add_parser("handoff")
    handoff.add_argument("--task-id", required=True)
    handoff.add_argument("--verification", action="append", required=True)
    handoff.add_argument("--decision", action="append", default=[])
    handoff.add_argument("--open-risk", action="append", default=[])

    finish = sub.add_parser("finish")
    finish.add_argument("--task-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            result = start_lane(
                args.repo,
                task_id=args.task_id,
                runtime=args.runtime,
                owned_paths=args.owned_path,
                base=args.base,
                worktree_root=args.worktree_root,
            )
        elif args.command == "handoff":
            result = handoff_lane(
                args.repo,
                task_id=args.task_id,
                verification=args.verification,
                decisions=args.decision,
                open_risks=args.open_risk,
            )
        else:
            result = finish_lane(args.repo, task_id=args.task_id)
    except CoordinationError as exc:
        print(f"[agent-task] error: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
