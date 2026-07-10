"""evolve-fleet pr-start / pr-finish サブコマンド本体（#82 Phase 3）。

`cli.py` から subcommand 本体を分離する（`propose`/`tokens` と同型・800行対策）。
承認済み evolve 提案（`evolve-proposals-<date>.json`、#81）を入力に、対象 PJ で
worktree 隔離 →（対話 evolve 適用は人間が行う）→ commit → push → PR 作成までを自動化する。
マージは常に人間（自動マージしない）。
"""
from __future__ import annotations

import argparse

from . import _current_data_dir
from . import pr as pr_lib


def run_pr_start_command(args: argparse.Namespace) -> int:
    """pr-start: 最新 proposals report から対象 PJ を解決し、worktree + branch を準備する。"""
    data_dir = _current_data_dir()
    try:
        _report, _entry, project_path = pr_lib.resolve_target(data_dir, args.pj_slug)
    except pr_lib.ProposalTargetError as e:
        print(f"[fleet:pr-start] エラー: {e}")
        return 1

    date_str = pr_lib.today_str()
    try:
        result = pr_lib.create_worktree(project_path, date_str)
    except (pr_lib.WorktreeError, pr_lib.GitCommandError) as e:
        print(f"[fleet:pr-start] エラー: {e}")
        return 1

    print(f"[fleet:pr-start] worktree を作成しました: {result['worktree_path']}")
    print(f"[fleet:pr-start] branch: {result['branch']}")
    print()
    print("次のステップ:")
    print(f"  1. `/cd {result['worktree_path']}` で worktree に移動")
    print("  2. `/evolve-anything:evolve` を対話実行し、提案を承認・適用")
    print(f"  3. `bin/evolve-fleet pr-finish {args.pj_slug}` で commit→push→PR 作成")
    return 0


def run_pr_finish_command(args: argparse.Namespace) -> int:
    """pr-finish: worktree の変更を commit（未コミットのみ）→push→gh pr create する。"""
    data_dir = _current_data_dir()
    try:
        report, entry, project_path = pr_lib.resolve_target(data_dir, args.pj_slug)
    except pr_lib.ProposalTargetError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    try:
        worktree = pr_lib.resolve_worktree(project_path, date_str=args.date)
    except pr_lib.WorktreeError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    date_str = worktree.name[len(pr_lib.WORKTREE_PREFIX):]
    try:
        branch = pr_lib.validate_branch(worktree, date_str)
    except (pr_lib.WorktreeError, pr_lib.GitCommandError) as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    try:
        base_branch = pr_lib.default_branch(project_path)
    except pr_lib.GitCommandError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    try:
        dirty = pr_lib.has_uncommitted_changes(worktree)
    except pr_lib.GitCommandError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    commit_message = f"feat(evolve): apply evolve proposals {date_str}"

    if args.dry_run:
        print("[fleet:pr-finish] --dry-run: 以下を実行予定です（未実行）")
        print(f"  worktree: {worktree}")
        print(f"  branch: {branch} -> base: {base_branch}")
        if dirty:
            print(f'  commit: "{commit_message}"')
        print(f"  push: git push -u origin {branch}")
        print(f"  gh pr create --base {base_branch} --title ... --body ...{' --draft' if args.draft else ''}")
        return 0

    if dirty:
        try:
            pr_lib.commit_all(worktree, commit_message)
        except pr_lib.GitCommandError as e:
            print(f"[fleet:pr-finish] エラー: {e}")
            return 1
        print(f"[fleet:pr-finish] commit しました: {commit_message}")
    else:
        ahead = pr_lib.commits_ahead(worktree, base_branch)
        if ahead == 0:
            print(
                "[fleet:pr-finish] 未コミットの変更も base との差分もありません"
                "（対話 evolve で何も適用されていない可能性）。PR 作成をスキップします。"
            )
            return 1

    try:
        pr_lib.verify_push_account(project_path)
    except (pr_lib.AccountMismatchError, pr_lib.WorktreeError) as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    try:
        pr_lib.push_branch(worktree, branch)
    except pr_lib.GitCommandError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1
    print(f"[fleet:pr-finish] push しました: origin/{branch}")

    diff_text = pr_lib.diff_stat(worktree, base_branch)
    title = pr_lib.build_pr_title(args.pj_slug, date_str)
    body = pr_lib.build_pr_body(entry, report=report, diff_stat_text=diff_text)

    try:
        result = pr_lib.create_pr(
            worktree, title=title, body=body, base=base_branch, draft=args.draft
        )
    except pr_lib.GitCommandError as e:
        print(f"[fleet:pr-finish] エラー: {e}")
        return 1

    print(f"[fleet:pr-finish] PR を作成しました: {result.get('url', '')}")
    print(
        "[fleet:pr-finish] マージは人間が行ってください。"
        " マージ後の worktree/branch 掃除は `/evolve-anything:cleanup` で。"
    )
    return 0
