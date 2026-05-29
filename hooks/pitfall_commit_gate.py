#!/usr/bin/env python3
"""PreToolUse hook — `git commit` 時に staged な管理対象 pitfalls.md を lint する commit ゲート。

二段検査の commit 時ステージ。確定状態（staged 内容）だけを見るので編集途中の誤検知が無い。

方針（ユーザー確認済み）:
- **danger（index/TOC 等 wipe 危険）→ commit をブロック**（exit 2）。silent wipe が
  履歴に入る最悪形を最後に止める。書き換えはしない（ブロックするだけ）。
- **drift（正準形と差分）→ 警告のみで通す**（自動書き換えはしない方針）。
- ok / 対象なし → 何もせず通す。

enable で登録された pitfalls.md にのみ反応する（pitfall_registry が空なら無反応）。
LLM は呼ばない（MUST NOT）。git 実行は run_git で注入可能（テストは subprocess 不要）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List

_HOOK_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _HOOK_DIR.parent
for _p in (
    _PLUGIN_ROOT / "scripts" / "lib",
    _PLUGIN_ROOT / "skills" / "pitfall-curate" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pitfall_registry
from parse import check_normalized

# `git commit` を検出（`git -c x.y=z commit`, `git commit -m ...` 等も許容）。
# `echo "git commit"` のような文字列内一致は完全には防げないが、誤ブロックは
# 「コミット内容に管理対象 pitfalls が staged されている」場合のみなので実害は小さい。
_GIT_COMMIT = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+commit(?=\s|$)")

GitRunner = Callable[[List[str]], str]


def is_git_commit(command: str) -> bool:
    return bool(command) and bool(_GIT_COMMIT.search(command))


def _staged_managed_paths(
    project_dir: str, managed: List[str], run_git: GitRunner
) -> List[str]:
    """staged ファイルのうち管理対象 pitfalls のものを **repo ルート相対パス**で返す。

    registry キーは project_dir 相対だが、`git diff --cached` / `git show :path` は
    repo ルート相対で動く。両者を絶対パスへ解決して突合することで、project_dir が
    repo ルートのサブディレクトリでも正しく一致させる。返すのは `git show :path` に
    渡せる repo ルート相対パス（managed キーではなく staged 側の表記）。
    """
    try:
        out = run_git(["diff", "--cached", "--name-only"])
    except Exception:
        return []
    staged = [line.strip() for line in out.splitlines() if line.strip()]
    if not staged:
        return []
    try:
        repo_root = run_git(["rev-parse", "--show-toplevel"]).strip() or str(project_dir)
    except Exception:
        repo_root = str(project_dir)
    pd = Path(project_dir)
    managed_abs = {
        str((p if (p := Path(k)).is_absolute() else pd / p).resolve())
        for k in managed
    }
    result: List[str] = []
    for s in staged:
        sp = Path(s)
        s_abs = str((sp if sp.is_absolute() else Path(repo_root) / sp).resolve())
        if s_abs in managed_abs:
            result.append(s)
    return result


def evaluate(event: dict, project_dir: str, run_git: GitRunner) -> Dict[str, str]:
    """commit ゲートの判定（純粋ロジック）。

    返り値: {"decision": "allow"|"warn"|"deny", "message": str}
    """
    allow = {"decision": "allow", "message": ""}
    if event.get("tool_name") != "Bash":
        return allow
    command = (event.get("tool_input") or {}).get("command", "")
    if not is_git_commit(command):
        return allow
    if not project_dir:
        return allow
    managed = pitfall_registry.load_managed(project_dir)
    if not managed:
        return allow

    targets = _staged_managed_paths(project_dir, managed, run_git)
    danger: List[str] = []
    drift: List[str] = []
    for rel in targets:
        try:
            content = run_git(["show", f":{rel}"])
        except Exception:
            continue
        state = check_normalized(content)["state"]
        if state == "danger":
            danger.append(rel)
        elif state == "drift":
            drift.append(rel)

    if danger:
        return {
            "decision": "deny",
            "message": (
                "[rl-anything:pitfall_commit_gate] ✗ commit をブロックしました。\n"
                f"  次の pitfalls はエントリ0件で実質コンテンツがあり、wipe の恐れがあります: "
                f"{', '.join(danger)}\n"
                "  `### タイトル` 形式へ再構成するか、index/TOC なら管理対象から外して"
                "（pitfall_curate.py で disable）から commit してください。"
            ),
        }
    if drift:
        return {
            "decision": "warn",
            "message": (
                "[rl-anything:pitfall_commit_gate] ⚠ 正準フォーマットと差分のある "
                f"pitfalls を commit します（ブロックはしません）: {', '.join(drift)}\n"
                "  揃えるには `pitfall_curate.py normalize --pitfalls <path> --out <path>`。"
            ),
        }
    return allow


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "") or os.getcwd()

        def run_git(arglist: List[str]) -> str:
            return subprocess.run(
                ["git", *arglist],
                cwd=project_dir,
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        res = evaluate(event, project_dir, run_git)
        if res["decision"] == "deny":
            print(res["message"], file=sys.stderr, flush=True)
            sys.exit(2)
        if res["decision"] == "warn":
            print(res["message"], flush=True)
    except SystemExit:
        raise
    except Exception:
        pass  # サイレント失敗（ゲートの不具合で commit を巻き込まない）


if __name__ == "__main__":
    main()
