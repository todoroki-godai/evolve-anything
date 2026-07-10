#!/usr/bin/env python3
"""fleet pr（#82 Phase 3）のテスト — 承認済み evolve 提案の worktree→commit→push→PR 化。

決定論・LLM 非依存。git/gh の実行は全て ``run`` DI パラメータ経由の stub に差し替え、
実 push / 実 PR 作成は一切行わない（no-llm-in-tests の外部プロセス版）。検証対象:
  - ``resolve_target``: 最新 proposals report からの対象 PJ 解決（未検出/status!=ok/不在）
  - ``create_worktree``: worktree 作成（既存 worktree/branch は上書きしない）
  - ``find_existing_worktrees`` / ``resolve_worktree``: 既存 worktree の検出・曖昧性処理
  - ``validate_branch`` / ``current_branch``: ブランチ名検証
  - ``has_uncommitted_changes`` / ``commit_all`` / ``commits_ahead``: commit 判定・実行
  - ``default_branch``: origin の既定ブランチ probe
  - ``expected_account`` / ``parse_active_gh_account`` / ``verify_push_account``:
    push アカウント判定（account-org-guard.py と同じマッピング）
  - ``push_branch`` / ``diff_stat`` / ``create_pr``: push/PR 作成コマンド構築
  - ``touched_skill_names`` / ``build_pr_title`` / ``build_pr_body``: PR 本文組み立て
  - CLI ``pr-start`` / ``pr-finish`` サブコマンドの配線
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet import pr as fpr  # noqa: E402


# --- テスト用 git/gh stub -------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ScriptedRun:
    """cmd に対する predicate → _FakeProc の対応表で応答する DI 用 stub。

    マッチしないコマンドは AssertionError（未想定の subprocess 呼び出しを検出するため）。
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        for pred, proc in self._responses:
            if pred(cmd):
                return proc
        raise AssertionError(f"unscripted command: {cmd}")


def _default_ok(_cmd):
    return True


# --- resolve_target --------------------------------------------------------------


def _init_real_git_repo(path: Path) -> None:
    """テスト用の実 git repo を作る（local-only、network 呼び出しなし）。

    ``pr-start`` の1本の E2E テストで実 ``git worktree add`` を実際に走らせるために使う
    （push/PR は絶対に実行しない、ローカル worktree 作成のみ）。
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _write_report(data_dir: Path, date_str: str, pjs: list) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": "2026-07-10T00:00:00+00:00", "pj_count": len(pjs), "pjs": pjs}
    path = data_dir / f"evolve-proposals-{date_str}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


class TestResolveTarget:
    def test_no_reports_raises(self, tmp_path):
        with pytest.raises(fpr.ProposalTargetError, match="evolve-proposals"):
            fpr.resolve_target(tmp_path / "data", "alpha")

    def test_picks_latest_report_by_date(self, tmp_path):
        data_dir = tmp_path / "data"
        p_alpha = tmp_path / "alpha"
        p_alpha.mkdir()
        _write_report(data_dir, "20260101", [{"pj_slug": "alpha", "status": "ok",
                                                "project_path": str(p_alpha), "summary": {}}])
        _write_report(data_dir, "20260710", [{"pj_slug": "alpha", "status": "ok",
                                                "project_path": str(p_alpha),
                                                "summary": {"total_proposals": 9}}])
        report, entry, project_path = fpr.resolve_target(data_dir, "alpha")
        assert entry["summary"]["total_proposals"] == 9
        assert project_path == p_alpha

    def test_missing_pj_slug_raises(self, tmp_path):
        data_dir = tmp_path / "data"
        p = tmp_path / "alpha"
        p.mkdir()
        _write_report(data_dir, "20260710", [{"pj_slug": "alpha", "status": "ok",
                                                "project_path": str(p), "summary": {}}])
        with pytest.raises(fpr.ProposalTargetError, match="beta"):
            fpr.resolve_target(data_dir, "beta")

    def test_non_ok_status_raises(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_report(data_dir, "20260710", [{"pj_slug": "alpha", "status": "error"}])
        with pytest.raises(fpr.ProposalTargetError, match="status"):
            fpr.resolve_target(data_dir, "alpha")

    def test_missing_project_path_raises(self, tmp_path):
        data_dir = tmp_path / "data"
        _write_report(data_dir, "20260710", [{"pj_slug": "alpha", "status": "ok",
                                                "project_path": str(tmp_path / "does-not-exist"),
                                                "summary": {}}])
        with pytest.raises(fpr.ProposalTargetError, match="project_path"):
            fpr.resolve_target(data_dir, "alpha")


# --- create_worktree ---------------------------------------------------------------


class TestCreateWorktree:
    def test_success_calls_git_worktree_add(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        run = ScriptedRun([
            (lambda cmd: "rev-parse" in cmd, _FakeProc(returncode=1)),  # branch not exists
            (lambda cmd: "worktree" in cmd and "add" in cmd, _FakeProc(returncode=0)),
        ])
        result = fpr.create_worktree(project, "20260710", run=run)
        assert result["branch"] == "evolve/20260710-proposals"
        assert result["worktree_path"] == project / ".claude" / "worktrees" / "evolve-apply-20260710"
        add_calls = [c for c, _ in run.calls if "worktree" in c]
        assert len(add_calls) == 1
        assert add_calls[0][:2] == ["git", "-C"]

    def test_existing_worktree_dir_raises_without_running_git(self, tmp_path):
        project = tmp_path / "proj"
        wt = project / ".claude" / "worktrees" / "evolve-apply-20260710"
        wt.mkdir(parents=True)

        def _boom(cmd, **kw):
            raise AssertionError("should not call git when worktree dir exists")

        with pytest.raises(fpr.WorktreeError, match="既に存在"):
            fpr.create_worktree(project, "20260710", run=_boom)

    def test_existing_branch_raises(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        run = ScriptedRun([
            (lambda cmd: "rev-parse" in cmd, _FakeProc(returncode=0)),  # branch exists
        ])
        with pytest.raises(fpr.WorktreeError, match="branch"):
            fpr.create_worktree(project, "20260710", run=run)

    def test_git_failure_raises_git_command_error(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        run = ScriptedRun([
            (lambda cmd: "rev-parse" in cmd, _FakeProc(returncode=1)),
            (lambda cmd: "worktree" in cmd, _FakeProc(returncode=128, stderr="fatal: boom")),
        ])
        with pytest.raises(fpr.GitCommandError, match="boom"):
            fpr.create_worktree(project, "20260710", run=run)


# --- find_existing_worktrees / resolve_worktree ------------------------------------


class TestResolveWorktree:
    def test_no_worktrees_raises(self, tmp_path):
        with pytest.raises(fpr.WorktreeError, match="pr-start"):
            fpr.resolve_worktree(tmp_path / "proj")

    def test_single_worktree_is_used(self, tmp_path):
        project = tmp_path / "proj"
        wt = project / ".claude" / "worktrees" / "evolve-apply-20260710"
        wt.mkdir(parents=True)
        assert fpr.resolve_worktree(project) == wt

    def test_multiple_worktrees_without_date_raises(self, tmp_path):
        project = tmp_path / "proj"
        (project / ".claude" / "worktrees" / "evolve-apply-20260709").mkdir(parents=True)
        (project / ".claude" / "worktrees" / "evolve-apply-20260710").mkdir(parents=True)
        with pytest.raises(fpr.WorktreeError, match="--date"):
            fpr.resolve_worktree(project)

    def test_date_disambiguates(self, tmp_path):
        project = tmp_path / "proj"
        wt1 = project / ".claude" / "worktrees" / "evolve-apply-20260709"
        wt2 = project / ".claude" / "worktrees" / "evolve-apply-20260710"
        wt1.mkdir(parents=True)
        wt2.mkdir(parents=True)
        assert fpr.resolve_worktree(project, date_str="20260709") == wt1

    def test_date_not_found_raises(self, tmp_path):
        project = tmp_path / "proj"
        with pytest.raises(fpr.WorktreeError, match="見つかりません"):
            fpr.resolve_worktree(project, date_str="20260101")


# --- validate_branch / current_branch -----------------------------------------------


class TestValidateBranch:
    def test_matching_branch_ok(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="evolve/20260710-proposals\n"))])
        assert fpr.validate_branch(tmp_path, "20260710", run=run) == "evolve/20260710-proposals"

    def test_mismatched_branch_raises(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="main\n"))])
        with pytest.raises(fpr.WorktreeError, match="期待"):
            fpr.validate_branch(tmp_path, "20260710", run=run)

    def test_git_failure_raises_git_command_error(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1, stderr="not a git repo"))])
        with pytest.raises(fpr.GitCommandError):
            fpr.validate_branch(tmp_path, "20260710", run=run)


# --- has_uncommitted_changes / commit_all / commits_ahead ---------------------------


class TestUncommittedAndCommit:
    def test_has_uncommitted_changes_true(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout=" M skills/foo/SKILL.md\n"))])
        assert fpr.has_uncommitted_changes(tmp_path, run=run) is True

    def test_has_uncommitted_changes_false(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout=""))])
        assert fpr.has_uncommitted_changes(tmp_path, run=run) is False

    def test_status_failure_raises(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1, stderr="boom"))])
        with pytest.raises(fpr.GitCommandError):
            fpr.has_uncommitted_changes(tmp_path, run=run)

    def test_commit_all_runs_add_then_commit_no_co_authored_by(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: cmd[3:5] == ["add", "-A"], _FakeProc(returncode=0)),
            (lambda cmd: cmd[3] == "commit", _FakeProc(returncode=0)),
        ])
        fpr.commit_all(tmp_path, "feat(evolve): apply evolve proposals 20260710", run=run)
        commit_calls = [c for c, _ in run.calls if "commit" in c]
        assert len(commit_calls) == 1
        joined = " ".join(commit_calls[0])
        assert "Co-Authored-By" not in joined

    def test_commit_all_add_failure_raises(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "add" in cmd, _FakeProc(returncode=1, stderr="add failed")),
        ])
        with pytest.raises(fpr.GitCommandError, match="add failed"):
            fpr.commit_all(tmp_path, "msg", run=run)

    def test_commit_all_commit_failure_raises(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: cmd[3:5] == ["add", "-A"], _FakeProc(returncode=0)),
            (lambda cmd: cmd[3] == "commit", _FakeProc(returncode=1, stderr="nothing to commit")),
        ])
        with pytest.raises(fpr.GitCommandError, match="nothing to commit"):
            fpr.commit_all(tmp_path, "msg", run=run)

    def test_commits_ahead_parses_count(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="3\n"))])
        assert fpr.commits_ahead(tmp_path, "main", run=run) == 3

    def test_commits_ahead_failure_returns_zero(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1))])
        assert fpr.commits_ahead(tmp_path, "main", run=run) == 0

    def test_commits_ahead_malformed_output_returns_zero(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="not-a-number\n"))])
        assert fpr.commits_ahead(tmp_path, "main", run=run) == 0


# --- default_branch ---------------------------------------------------------------


class TestDefaultBranch:
    def test_uses_symbolic_ref_when_available(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="origin/main\n"))])
        assert fpr.default_branch(tmp_path, run=run) == "main"

    def test_falls_back_to_probing_main(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "symbolic-ref" in cmd, _FakeProc(returncode=1)),
            (lambda cmd: "origin/main" in cmd, _FakeProc(returncode=0)),
        ])
        assert fpr.default_branch(tmp_path, run=run) == "main"

    def test_falls_back_to_probing_master(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "symbolic-ref" in cmd, _FakeProc(returncode=1)),
            (lambda cmd: "origin/main" in cmd, _FakeProc(returncode=1)),
            (lambda cmd: "origin/master" in cmd, _FakeProc(returncode=0)),
        ])
        assert fpr.default_branch(tmp_path, run=run) == "master"

    def test_all_probes_fail_returns_main(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1))])
        assert fpr.default_branch(tmp_path, run=run) == "main"


# --- push アカウント判定 ------------------------------------------------------------


class TestExpectedAccount:
    def test_min_sys_owner_maps_to_matsukaze_minden(self):
        assert fpr.expected_account("min-sys") == "matsukaze-minden"

    def test_matsukaze_minden_owner_maps_to_matsukaze_minden(self):
        assert fpr.expected_account("matsukaze-minden") == "matsukaze-minden"

    def test_todoroki_godai_owner_maps_to_todoroki_godai(self):
        assert fpr.expected_account("todoroki-godai") == "todoroki-godai"

    def test_other_owner_maps_to_shohu(self):
        assert fpr.expected_account("shohu") == "shohu"
        assert fpr.expected_account("some-random-org") == "shohu"


_GH_AUTH_STATUS_SAMPLE = """github.com
  ✓ Logged in to github.com account shohu (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_************************************

  ✓ Logged in to github.com account matsukaze-minden (keyring)
  - Active account: false
  - Git operations protocol: https

  ✓ Logged in to github.com account todoroki-godai (keyring)
  - Active account: false
"""


class TestParseActiveGhAccount:
    def test_extracts_active_account(self):
        assert fpr.parse_active_gh_account(_GH_AUTH_STATUS_SAMPLE) == "shohu"

    def test_different_active_account(self):
        text = _GH_AUTH_STATUS_SAMPLE.replace(
            "account shohu (keyring)\n  - Active account: true",
            "account shohu (keyring)\n  - Active account: false",
        ).replace(
            "account todoroki-godai (keyring)\n  - Active account: false",
            "account todoroki-godai (keyring)\n  - Active account: true",
        )
        assert fpr.parse_active_gh_account(text) == "todoroki-godai"

    def test_no_active_account_returns_none(self):
        text = _GH_AUTH_STATUS_SAMPLE.replace("Active account: true", "Active account: false")
        assert fpr.parse_active_gh_account(text) is None

    def test_empty_input_returns_none(self):
        assert fpr.parse_active_gh_account("") is None


class TestVerifyPushAccount:
    def test_matching_account_passes(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "remote" in cmd, _FakeProc(stdout="git@github.com:todoroki-godai/evolve-anything.git\n")),
            (lambda cmd: cmd[:2] == ["gh", "auth"], _FakeProc(stdout=_GH_AUTH_STATUS_SAMPLE.replace(
                "account shohu (keyring)\n  - Active account: true",
                "account shohu (keyring)\n  - Active account: false",
            ).replace(
                "account todoroki-godai (keyring)\n  - Active account: false",
                "account todoroki-godai (keyring)\n  - Active account: true",
            ))),
        ])
        assert fpr.verify_push_account(tmp_path, run=run) == "todoroki-godai"

    def test_mismatched_account_raises_with_switch_hint(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "remote" in cmd, _FakeProc(stdout="git@github.com:min-sys/somewhere.git\n")),
            (lambda cmd: cmd[:2] == ["gh", "auth"], _FakeProc(stdout=_GH_AUTH_STATUS_SAMPLE)),
        ])
        with pytest.raises(fpr.AccountMismatchError, match="gh auth switch --user matsukaze-minden"):
            fpr.verify_push_account(tmp_path, run=run)

    def test_unresolvable_owner_raises_worktree_error(self, tmp_path):
        run = ScriptedRun([(lambda cmd: "remote" in cmd, _FakeProc(returncode=1))])
        with pytest.raises(fpr.WorktreeError, match="origin owner"):
            fpr.verify_push_account(tmp_path, run=run)

    def test_unresolvable_active_account_raises_worktree_error(self, tmp_path):
        run = ScriptedRun([
            (lambda cmd: "remote" in cmd, _FakeProc(stdout="git@github.com:shohu/x.git\n")),
            (lambda cmd: cmd[:2] == ["gh", "auth"], _FakeProc(returncode=1)),
        ])
        with pytest.raises(fpr.WorktreeError, match="gh auth login"):
            fpr.verify_push_account(tmp_path, run=run)


# --- push_branch / diff_stat / create_pr --------------------------------------------


class TestPushDiffCreatePr:
    def test_push_branch_success(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=0))])
        fpr.push_branch(tmp_path, "evolve/20260710-proposals", run=run)
        assert run.calls[0][0] == [
            "git", "-C", str(tmp_path), "push", "-u", "origin", "evolve/20260710-proposals",
        ]

    def test_push_branch_failure_raises(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1, stderr="rejected"))])
        with pytest.raises(fpr.GitCommandError, match="rejected"):
            fpr.push_branch(tmp_path, "evolve/20260710-proposals", run=run)

    def test_diff_stat_returns_stdout(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout=" skills/foo/SKILL.md | 5 +--\n"))])
        assert "skills/foo/SKILL.md" in fpr.diff_stat(tmp_path, "main", run=run)

    def test_diff_stat_failure_returns_empty_string(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1))])
        assert fpr.diff_stat(tmp_path, "main", run=run) == ""

    def test_create_pr_success_returns_url(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="https://github.com/x/y/pull/1\n"))])
        result = fpr.create_pr(
            tmp_path, title="t", body="b", base="main", draft=False, run=run
        )
        assert result["url"] == "https://github.com/x/y/pull/1"
        cmd = run.calls[0][0]
        assert cmd[:3] == ["gh", "pr", "create"]
        assert "--draft" not in cmd

    def test_create_pr_draft_adds_flag(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(stdout="url\n"))])
        fpr.create_pr(tmp_path, title="t", body="b", base="main", draft=True, run=run)
        assert "--draft" in run.calls[0][0]

    def test_create_pr_failure_raises(self, tmp_path):
        run = ScriptedRun([(_default_ok, _FakeProc(returncode=1, stderr="gh: not authenticated"))])
        with pytest.raises(fpr.GitCommandError, match="not authenticated"):
            fpr.create_pr(tmp_path, title="t", body="b", base="main", run=run)


# --- touched_skill_names / build_pr_title / build_pr_body ----------------------------


class TestTouchedSkillNames:
    def test_extracts_unique_skill_names_in_order(self):
        diff = (
            " skills/foo/SKILL.md         | 5 +--\n"
            " skills/bar/scripts/x.py     | 2 +-\n"
            " skills/foo/scripts/y.py     | 1 +\n"
        )
        assert fpr.touched_skill_names(diff) == ["foo", "bar"]

    def test_no_skills_returns_empty(self):
        assert fpr.touched_skill_names(" scripts/lib/fleet/pr.py | 5 +--\n") == []

    def test_empty_input_returns_empty(self):
        assert fpr.touched_skill_names("") == []


class TestBuildPrTitleAndBody:
    def test_title_includes_date_and_pj_slug(self):
        title = fpr.build_pr_title("evolve-anything", "20260710")
        assert "20260710" in title
        assert "evolve-anything" in title

    def test_body_includes_evidence_skills_diff_and_rollback(self):
        entry = {
            "summary": {
                "remediation_proposable": 2,
                "skill_evolve_high": 1,
                "skill_evolve_medium": 0,
                "skill_triage": {"CREATE": 1, "UPDATE": 0, "SPLIT": 0, "MERGE": 0},
                "reorganize_split_candidates": 0,
                "total_proposals": 3,
            }
        }
        report = {"generated_at": "2026-07-10T00:00:00+00:00"}
        diff_text = " skills/foo/SKILL.md | 5 +--\n"
        body = fpr.build_pr_body(entry, report=report, diff_stat_text=diff_text)
        assert "提案根拠" in body
        assert "2026-07-10T00:00:00+00:00" in body
        assert "適用スキル" in body
        assert "- foo" in body
        assert "変更差分" in body
        assert "skills/foo/SKILL.md" in body
        assert "ロールバック手順" in body
        assert "マージは人間が行います" in body
        assert "Co-Authored-By" not in body
        assert "🤖" not in body

    def test_body_handles_no_touched_skills(self):
        report = {"generated_at": "2026-07-10T00:00:00+00:00"}
        body = fpr.build_pr_body({"summary": {}}, report=report, diff_stat_text="")
        assert "検出できません" in body


# --- CLI pr-start / pr-finish サブコマンド --------------------------------------------


class TestPrStartCli:
    def test_success_prints_worktree_and_next_steps(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        data_dir = tmp_path / "data"
        project = tmp_path / "alpha"
        _init_real_git_repo(project)
        _write_report(data_dir, "20260710", [
            {"pj_slug": "alpha", "status": "ok", "project_path": str(project), "summary": {}}
        ])
        monkeypatch.setattr(cli_pr, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["pr-start", "alpha"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "worktree を作成しました" in out
        assert "evolve-anything:evolve" in out
        assert "pr-finish alpha" in out
        # 実 worktree が作られている（実 git 経由、DI していない=本当に動くことも確認）
        wt = project / ".claude" / "worktrees"
        assert wt.is_dir()
        assert any(p.name.startswith("evolve-apply-") for p in wt.iterdir())

    def test_unknown_pj_slug_returns_1(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        data_dir = tmp_path / "data"
        _write_report(data_dir, "20260710", [])
        monkeypatch.setattr(cli_pr, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["pr-start", "ghost"])
        assert rc == 1
        assert "エラー" in capsys.readouterr().out


class TestPrFinishCli:
    def _setup(self, tmp_path, monkeypatch, *, dirty=True, ahead=0):
        from fleet import cli_pr

        data_dir = tmp_path / "data"
        project = tmp_path / "alpha"
        project.mkdir()
        entry = {
            "pj_slug": "alpha", "status": "ok", "project_path": str(project),
            "summary": {"total_proposals": 3, "remediation_proposable": 3,
                        "skill_evolve_high": 0, "skill_evolve_medium": 0,
                        "skill_triage": {}, "reorganize_split_candidates": 0},
        }
        _write_report(data_dir, "20260710", [entry])
        wt = project / ".claude" / "worktrees" / "evolve-apply-20260710"
        wt.mkdir(parents=True)
        monkeypatch.setattr(cli_pr, "_current_data_dir", lambda: data_dir)
        monkeypatch.setattr(cli_pr.pr_lib, "validate_branch", lambda *a, **kw: "evolve/20260710-proposals")
        monkeypatch.setattr(cli_pr.pr_lib, "default_branch", lambda *a, **kw: "main")
        monkeypatch.setattr(cli_pr.pr_lib, "has_uncommitted_changes", lambda *a, **kw: dirty)
        monkeypatch.setattr(cli_pr.pr_lib, "commits_ahead", lambda *a, **kw: ahead)
        return data_dir, project, wt

    def test_dry_run_does_not_commit_push_or_create_pr(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        self._setup(tmp_path, monkeypatch, dirty=True)

        def _boom(*a, **kw):
            raise AssertionError("dry-run must not execute side effects")

        monkeypatch.setattr(cli_pr.pr_lib, "commit_all", _boom)
        monkeypatch.setattr(cli_pr.pr_lib, "verify_push_account", _boom)
        monkeypatch.setattr(cli_pr.pr_lib, "push_branch", _boom)
        monkeypatch.setattr(cli_pr.pr_lib, "create_pr", _boom)

        rc = fcli.main(["pr-finish", "alpha", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "--dry-run" in out
        assert "push -u origin" in out

    def test_no_changes_skips_and_returns_1(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        self._setup(tmp_path, monkeypatch, dirty=False, ahead=0)

        rc = fcli.main(["pr-finish", "alpha"])
        assert rc == 1
        assert "スキップ" in capsys.readouterr().out

    def test_dirty_commits_then_verifies_pushes_and_creates_pr(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        _data_dir, project, _wt = self._setup(tmp_path, monkeypatch, dirty=True)

        calls = []
        monkeypatch.setattr(cli_pr.pr_lib, "commit_all", lambda *a, **kw: calls.append("commit"))
        monkeypatch.setattr(cli_pr.pr_lib, "verify_push_account", lambda *a, **kw: calls.append("verify"))
        monkeypatch.setattr(cli_pr.pr_lib, "push_branch", lambda *a, **kw: calls.append("push"))
        monkeypatch.setattr(cli_pr.pr_lib, "diff_stat", lambda *a, **kw: " skills/foo/SKILL.md | 1 +\n")
        monkeypatch.setattr(
            cli_pr.pr_lib, "create_pr",
            lambda *a, **kw: calls.append("pr") or {"url": "https://github.com/x/y/pull/9"},
        )

        rc = fcli.main(["pr-finish", "alpha"])
        assert rc == 0
        assert calls == ["commit", "verify", "push", "pr"]
        out = capsys.readouterr().out
        assert "https://github.com/x/y/pull/9" in out
        assert "マージは人間" in out
        assert "cleanup" in out

    def test_account_mismatch_stops_before_push(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        self._setup(tmp_path, monkeypatch, dirty=True)
        monkeypatch.setattr(cli_pr.pr_lib, "commit_all", lambda *a, **kw: None)

        def _mismatch(*a, **kw):
            raise cli_pr.pr_lib.AccountMismatchError("アカウント不整合: ...")

        monkeypatch.setattr(cli_pr.pr_lib, "verify_push_account", _mismatch)

        def _boom(*a, **kw):
            raise AssertionError("push must not run after account mismatch")

        monkeypatch.setattr(cli_pr.pr_lib, "push_branch", _boom)
        monkeypatch.setattr(cli_pr.pr_lib, "create_pr", _boom)

        rc = fcli.main(["pr-finish", "alpha"])
        assert rc == 1
        assert "アカウント不整合" in capsys.readouterr().out

    def test_draft_flag_is_forwarded_to_create_pr(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        self._setup(tmp_path, monkeypatch, dirty=True)
        monkeypatch.setattr(cli_pr.pr_lib, "commit_all", lambda *a, **kw: None)
        monkeypatch.setattr(cli_pr.pr_lib, "verify_push_account", lambda *a, **kw: "shohu")
        monkeypatch.setattr(cli_pr.pr_lib, "push_branch", lambda *a, **kw: None)
        monkeypatch.setattr(cli_pr.pr_lib, "diff_stat", lambda *a, **kw: "")

        captured_kwargs = {}

        def _create_pr(*a, **kw):
            captured_kwargs.update(kw)
            return {"url": "u"}

        monkeypatch.setattr(cli_pr.pr_lib, "create_pr", _create_pr)

        rc = fcli.main(["pr-finish", "alpha", "--draft"])
        assert rc == 0
        assert captured_kwargs.get("draft") is True

    def test_unknown_pj_slug_returns_1(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        data_dir = tmp_path / "data"
        _write_report(data_dir, "20260710", [])
        monkeypatch.setattr(cli_pr, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["pr-finish", "ghost"])
        assert rc == 1
        assert "エラー" in capsys.readouterr().out

    def test_no_worktree_returns_1(self, tmp_path, monkeypatch, capsys):
        from fleet import cli as fcli
        from fleet import cli_pr

        data_dir = tmp_path / "data"
        project = tmp_path / "alpha"
        project.mkdir()
        _write_report(data_dir, "20260710", [
            {"pj_slug": "alpha", "status": "ok", "project_path": str(project), "summary": {}}
        ])
        monkeypatch.setattr(cli_pr, "_current_data_dir", lambda: data_dir)

        rc = fcli.main(["pr-finish", "alpha"])
        assert rc == 1
        assert "pr-start" in capsys.readouterr().out
