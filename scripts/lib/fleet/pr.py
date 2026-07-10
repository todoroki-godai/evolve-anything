"""fleet.pr — 承認済み evolve 提案を worktree→commit→push→PR 化する接続層（#82 Phase 3）。

evolve の適用そのものは本質的に対話（人間が対象 PJ の worktree 内で
`/evolve-anything:evolve` を回して承認・適用・commit する）。本モジュールが自動化するのは
**外殻のみ**:
  1. ``pr-start``: 適用前の worktree 準備（``git worktree add`` + branch 作成）
  2. ``pr-finish``: 適用後の commit（未コミット変更があれば）→ push → ``gh pr create``

マージは常に人間（自動マージはしない）。git/gh の実行はすべて ``run``（既定
``subprocess.run``）経由の DI にして、単体テストで実 git/gh を一切呼ばない
（``run_evolve_fn`` と同型の DI 規約・no-llm-in-tests の外部プロセス版）。

アカウント判定は ``~/.claude/hooks/account-org-guard.py`` と同じマッピング
（MINDEN_OWNERS/TODOROKI_OWNERS/default=shohu）を複製している。グローバル hook は
このプラグイン外（``~/.claude/hooks/``）にあり import できないため定数を複製した。
**account-org-guard.py のマッピングを変更したらここも同期すること。**
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .propose import PROPOSALS_FILE_PREFIX

WORKTREE_SUBDIR = Path(".claude") / "worktrees"
WORKTREE_PREFIX = "evolve-apply-"
BRANCH_PREFIX = "evolve/"
BRANCH_SUFFIX = "-proposals"

# アカウントマッピング（~/.claude/hooks/account-org-guard.py と同期を保つこと）
MINDEN_OWNERS = {"min-sys", "matsukaze-minden"}
ACCOUNT_FOR_MINDEN = "matsukaze-minden"
TODOROKI_OWNERS = {"todoroki-godai"}
ACCOUNT_FOR_TODOROKI = "todoroki-godai"
ACCOUNT_DEFAULT = "shohu"

_LOCAL_TIMEOUT_SEC = 15.0
_NETWORK_TIMEOUT_SEC = 60.0

RunFunc = Callable[..., "subprocess.CompletedProcess"]


class ProposalTargetError(Exception):
    """proposals report から対象 PJ を解決できない場合（レポート不在・未検出・status!=ok）。"""


class WorktreeError(Exception):
    """worktree 作成・検出・ブランチ検証に関するエラー。"""


class AccountMismatchError(Exception):
    """push アカウント不整合（gh auth switch を促す）。"""


class GitCommandError(Exception):
    """git/gh コマンドが非ゼロ終了した場合。"""

    def __init__(self, cmd: List[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{' '.join(cmd)} failed (exit {returncode}): {stderr.strip()}")


def _run(
    cmd: List[str],
    *,
    cwd: Optional[Path] = None,
    run: RunFunc = subprocess.run,
    timeout: float = _LOCAL_TIMEOUT_SEC,
):
    return run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
    )


def today_str() -> str:
    """UTC 基準の YYYYMMDD（``write_reports`` の既定 date_str と同じ形式）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def branch_name(date_str: str) -> str:
    return f"{BRANCH_PREFIX}{date_str}{BRANCH_SUFFIX}"


def worktree_path(project_path: Path, date_str: str) -> Path:
    return Path(project_path) / WORKTREE_SUBDIR / f"{WORKTREE_PREFIX}{date_str}"


# --- proposals report からの対象解決 ------------------------------------------


def find_latest_proposals_json(data_dir: Path) -> Optional[Path]:
    """``DATA_DIR/evolve-proposals-<date>.json`` のうち最新（日付降順）を返す。"""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return None
    candidates = sorted(data_dir.glob(f"{PROPOSALS_FILE_PREFIX}*.json"))
    return candidates[-1] if candidates else None


def load_proposals_report(path: Path) -> Dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def find_pj_entry(report: Dict[str, Any], pj_slug: str) -> Optional[Dict[str, Any]]:
    for e in report.get("pjs") or []:
        if isinstance(e, dict) and e.get("pj_slug") == pj_slug:
            return e
    return None


def resolve_target(data_dir: Path, pj_slug: str) -> "tuple[Dict[str, Any], Dict[str, Any], Path]":
    """最新 proposals report から対象 PJ entry を解決する。

    Returns: ``(report, entry, project_path)``
    Raises: ``ProposalTargetError``（レポート不在・pj_slug 未検出・status != ok・project_path 不在）
    """
    report_path = find_latest_proposals_json(data_dir)
    if report_path is None:
        raise ProposalTargetError(
            f"{data_dir} に evolve-proposals-*.json が見つかりません。"
            " 先に `evolve-fleet propose` を実行してください。"
        )
    report = load_proposals_report(report_path)
    entry = find_pj_entry(report, pj_slug)
    if entry is None:
        raise ProposalTargetError(
            f"{report_path.name} に pj_slug='{pj_slug}' の提案が見つかりません。"
        )
    if entry.get("status") != "ok":
        raise ProposalTargetError(
            f"pj_slug='{pj_slug}' の提案 status は '{entry.get('status')}' です"
            f"（'ok' である必要があります）。"
        )
    project_path = entry.get("project_path")
    if not project_path or not Path(project_path).is_dir():
        raise ProposalTargetError(
            f"pj_slug='{pj_slug}' の project_path が不明または不在: {project_path!r}"
        )
    return report, entry, Path(project_path)


# --- worktree 作成（pr-start）---------------------------------------------------


def branch_exists(project_path: Path, branch: str, *, run: RunFunc = subprocess.run) -> bool:
    proc = _run(
        ["git", "-C", str(project_path), "rev-parse", "--verify", "--quiet", branch], run=run
    )
    return proc.returncode == 0


def create_worktree(
    project_path: Path, date_str: str, *, run: RunFunc = subprocess.run
) -> Dict[str, Any]:
    """``git worktree add <path> -b <branch>`` を実行する。既存 worktree/branch は上書きしない。"""
    wt_path = worktree_path(project_path, date_str)
    branch = branch_name(date_str)
    if wt_path.exists():
        raise WorktreeError(f"worktree が既に存在します（上書きしません）: {wt_path}")
    if branch_exists(project_path, branch, run=run):
        raise WorktreeError(f"branch '{branch}' が既に存在します（上書きしません）。")

    proc = _run(
        ["git", "-C", str(project_path), "worktree", "add", str(wt_path), "-b", branch],
        run=run,
    )
    if proc.returncode != 0:
        raise GitCommandError(
            ["git", "worktree", "add", str(wt_path), "-b", branch], proc.returncode, proc.stderr or ""
        )
    return {"worktree_path": wt_path, "branch": branch}


# --- worktree 検出・検証（pr-finish）--------------------------------------------


def find_existing_worktrees(project_path: Path) -> List[Path]:
    """``.claude/worktrees/evolve-apply-*`` ディレクトリを列挙する（日付降順）。"""
    root = Path(project_path) / WORKTREE_SUBDIR
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith(WORKTREE_PREFIX)),
        key=lambda p: p.name,
        reverse=True,
    )


def resolve_worktree(project_path: Path, *, date_str: Optional[str] = None) -> Path:
    """pr-finish が操作対象とする worktree を1つ解決する。

    ``date_str`` 指定時はその日付の worktree のみ許容する。未指定時は既存 worktree が
    ちょうど1件ならそれを使う。0件は「先に pr-start を」、複数件は「--date で指定」を促す。
    """
    if date_str:
        wt = worktree_path(project_path, date_str)
        if not wt.is_dir():
            raise WorktreeError(f"worktree が見つかりません: {wt}")
        return wt

    candidates = find_existing_worktrees(project_path)
    if not candidates:
        raise WorktreeError(
            "evolve-apply-* worktree が見つかりません。先に `pr-start` を実行してください。"
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise WorktreeError(
            f"複数の worktree が見つかりました（{names}）。`--date YYYYMMDD` で指定してください。"
        )
    return candidates[0]


def current_branch(repo_path: Path, *, run: RunFunc = subprocess.run) -> str:
    proc = _run(["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"], run=run)
    if proc.returncode != 0:
        raise GitCommandError(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], proc.returncode, proc.stderr or ""
        )
    return proc.stdout.strip()


def validate_branch(worktree: Path, date_str: str, *, run: RunFunc = subprocess.run) -> str:
    """worktree の現在ブランチが ``date_str`` から期待される名前と一致するか検証する。"""
    expected = branch_name(date_str)
    actual = current_branch(worktree, run=run)
    if actual != expected:
        raise WorktreeError(
            f"worktree のブランチが想定と異なります（期待: {expected}, 実際: {actual}）。"
        )
    return expected


# --- commit（pr-finish）---------------------------------------------------------


def has_uncommitted_changes(worktree: Path, *, run: RunFunc = subprocess.run) -> bool:
    proc = _run(["git", "-C", str(worktree), "status", "--porcelain"], run=run)
    if proc.returncode != 0:
        raise GitCommandError(["git", "status", "--porcelain"], proc.returncode, proc.stderr or "")
    return bool(proc.stdout.strip())


def commit_all(worktree: Path, message: str, *, run: RunFunc = subprocess.run) -> None:
    """``git add -A`` + ``git commit -m <message>``。Co-Authored-By は付けない。"""
    proc = _run(["git", "-C", str(worktree), "add", "-A"], run=run)
    if proc.returncode != 0:
        raise GitCommandError(["git", "add", "-A"], proc.returncode, proc.stderr or "")
    proc = _run(["git", "-C", str(worktree), "commit", "-m", message], run=run)
    if proc.returncode != 0:
        raise GitCommandError(["git", "commit", "-m", message], proc.returncode, proc.stderr or "")


def commits_ahead(worktree: Path, base_branch: str, *, run: RunFunc = subprocess.run) -> int:
    """``origin/<base_branch>..HEAD`` のコミット数。判定不能時は保守的に 0 を返す（例外にしない）。"""
    proc = _run(
        ["git", "-C", str(worktree), "rev-list", "--count", f"origin/{base_branch}..HEAD"],
        run=run,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int((proc.stdout or "").strip() or "0")
    except ValueError:
        return 0


def default_branch(repo_path: Path, *, run: RunFunc = subprocess.run) -> str:
    """origin の既定ブランチ名を求める。``origin/HEAD`` 不明時は main/master を probe する。"""
    proc = _run(
        ["git", "-C", str(repo_path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        run=run,
    )
    if proc.returncode == 0 and (proc.stdout or "").strip():
        ref = proc.stdout.strip()
        return ref.split("/", 1)[1] if "/" in ref else ref
    for candidate in ("main", "master"):
        proc = _run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", f"origin/{candidate}"],
            run=run,
        )
        if proc.returncode == 0:
            return candidate
    return "main"


# --- push アカウント判定 ---------------------------------------------------------


_OWNER_RE = re.compile(r"github\.com[:/]+([^/]+)/")


def _origin_owner(repo_path: Path, *, run: RunFunc = subprocess.run) -> Optional[str]:
    proc = _run(["git", "-C", str(repo_path), "remote", "get-url", "origin"], run=run)
    if proc.returncode != 0:
        return None
    m = _OWNER_RE.search((proc.stdout or "").strip())
    return m.group(1) if m else None


def expected_account(owner: str) -> str:
    """origin owner から期待 gh アカウントを導出する（account-org-guard.py と同じマッピング）。"""
    if owner in MINDEN_OWNERS:
        return ACCOUNT_FOR_MINDEN
    if owner in TODOROKI_OWNERS:
        return ACCOUNT_FOR_TODOROKI
    return ACCOUNT_DEFAULT


_ACCOUNT_LINE_RE = re.compile(r"account\s+(\S+)\s*\(")
_ACTIVE_LINE_RE = re.compile(r"Active account:\s*(true|false)")


def parse_active_gh_account(gh_auth_status_output: str) -> Optional[str]:
    """``gh auth status`` の標準出力からアクティブアカウント名を抽出する。"""
    current: Optional[str] = None
    for line in gh_auth_status_output.splitlines():
        m = _ACCOUNT_LINE_RE.search(line)
        if m:
            current = m.group(1)
            continue
        m2 = _ACTIVE_LINE_RE.search(line)
        if m2 and current and m2.group(1) == "true":
            return current
    return None


def active_gh_account(*, run: RunFunc = subprocess.run) -> Optional[str]:
    proc = run(
        ["gh", "auth", "status"], capture_output=True, text=True, timeout=_NETWORK_TIMEOUT_SEC
    )
    if proc.returncode != 0:
        return None
    return parse_active_gh_account(proc.stdout or "")


def verify_push_account(project_path: Path, *, run: RunFunc = subprocess.run) -> str:
    """push 先アカウントを検証する。不一致なら ``gh auth switch`` を促して停止する。

    自動切替はしない（設計上の決定 — 誤 push を確実に止める）。
    """
    owner = _origin_owner(project_path, run=run)
    if owner is None:
        raise WorktreeError("origin owner を判定できません（`git remote get-url origin` 失敗）。")
    expected = expected_account(owner)
    active = active_gh_account(run=run)
    if active is None:
        raise WorktreeError(
            "`gh auth status` からアクティブアカウントを判定できません。`gh auth login` を確認してください。"
        )
    if active != expected:
        raise AccountMismatchError(
            f"アカウント不整合: origin owner='{owner}' は gh アカウント '{expected}' を"
            f" 期待しますが、現在アクティブなのは '{active}' です。"
            f" `gh auth switch --user {expected}` で切り替えてから再実行してください。"
        )
    return expected


# --- push / PR 作成 --------------------------------------------------------------


def push_branch(worktree: Path, branch: str, *, run: RunFunc = subprocess.run) -> None:
    proc = _run(
        ["git", "-C", str(worktree), "push", "-u", "origin", branch],
        run=run,
        timeout=_NETWORK_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise GitCommandError(
            ["git", "push", "-u", "origin", branch], proc.returncode, proc.stderr or ""
        )


def diff_stat(worktree: Path, base_branch: str, *, run: RunFunc = subprocess.run) -> str:
    """``git diff --stat origin/<base>..HEAD``。失敗時は空文字（advisory のため落とさない）。"""
    proc = _run(
        ["git", "-C", str(worktree), "diff", "--stat", f"origin/{base_branch}..HEAD"], run=run
    )
    return proc.stdout or "" if proc.returncode == 0 else ""


_SKILL_PATH_RE = re.compile(r"(?:^|[\s|])skills/([^/\s|]+)/")


def touched_skill_names(diff_stat_text: str) -> List[str]:
    """``git diff --stat`` の出力から touched skill 名を抽出する（重複排除・出現順）。"""
    seen: List[str] = []
    for m in _SKILL_PATH_RE.finditer(diff_stat_text or ""):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def create_pr(
    worktree: Path,
    *,
    title: str,
    body: str,
    base: str,
    draft: bool = False,
    run: RunFunc = subprocess.run,
) -> Dict[str, Any]:
    cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
    if draft:
        cmd.append("--draft")
    proc = _run(cmd, cwd=worktree, run=run, timeout=_NETWORK_TIMEOUT_SEC)
    if proc.returncode != 0:
        raise GitCommandError(cmd, proc.returncode, proc.stderr or "")
    return {"url": (proc.stdout or "").strip()}


# --- PR タイトル・本文テンプレート -----------------------------------------------


def build_pr_title(pj_slug: str, date_str: str) -> str:
    return f"feat(evolve): apply evolve proposals {date_str} ({pj_slug})"


def build_pr_body(
    entry: Dict[str, Any], *, report: Dict[str, Any], diff_stat_text: str
) -> str:
    """提案根拠・適用スキル一覧・差分・ロールバック手順を含む PR body を組み立てる。

    Claude の痕跡（Co-Authored-By / 🤖 フッター）は一切含めない。マージは人間である旨を明記する。
    """
    summary = entry.get("summary") or {}
    skills = touched_skill_names(diff_stat_text)
    skills_block = "\n".join(f"- {s}" for s in skills) if skills else "（diff --stat から skill 名を検出できませんでした）"
    diff_block = diff_stat_text.strip() or "(差分なし、または diff --stat の取得に失敗)"

    lines = [
        f"## 提案根拠（evolve-proposals report 生成日時: {report.get('generated_at', '不明')}）",
        "",
        f"- remediation.proposable: {summary.get('remediation_proposable', 0)}",
        f"- skill_evolve: high={summary.get('skill_evolve_high', 0)}, "
        f"medium={summary.get('skill_evolve_medium', 0)}",
        f"- skill_triage: {summary.get('skill_triage', {})}",
        f"- reorganize.split_candidates: {summary.get('reorganize_split_candidates', 0)}",
        f"- 提案合計: {summary.get('total_proposals', 0)} 件",
        "",
        "## 適用スキル（diff --stat から検出）",
        "",
        skills_block,
        "",
        "## 変更差分（dry-run diff --stat）",
        "",
        "```",
        diff_block,
        "```",
        "",
        "## ロールバック手順",
        "",
        "この PR に問題があれば、close してブランチを削除するだけで対象ブランチには影響しません"
        "（承認済み evolve 提案を worktree 隔離で適用したもので、apply 前の状態は保全されています）。",
        "",
        "---",
        "**マージは人間が行います。**（このツールは push → PR 作成までで、自動マージはしません）",
    ]
    return "\n".join(lines)
