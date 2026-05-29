"""pitfall_commit_gate hook（commit 時ゲート）のテスト。

LLM も git も呼ばない決定論テスト。run_git を注入し、staged 内容に応じて
deny（danger）/ warn（drift）/ allow（ok・対象なし）を返すことを検証する。
"""
import sys
from pathlib import Path

_hooks = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks))
sys.path.insert(0, str(_hooks.parent / "scripts" / "lib"))
sys.path.insert(0, str(_hooks.parent / "skills" / "pitfall-curate" / "scripts"))

import pitfall_commit_gate as gate
import pitfall_registry
from parse import normalize

REL = ".claude/skills/x/references/pitfalls.md"
CANONICAL = normalize(
    "# Pitfalls\n\n## Active Pitfalls\n\n### サンプル\n- **Status**: Active\n"
)
DRIFT = "# Pitfalls\n\n## Active\n\n### サンプル\n**Status**: Active | **Last-seen**: 2026-05-29\n"
INDEX = (
    "# index\n\n> TOC\n\n| # | 問題 |\n|---|------|\n| 1 | a |\n\n"
    "- [pitfalls-x.md](pitfalls-x.md) — x\n- [pitfalls-y.md](pitfalls-y.md) — y\n"
)


def _make_runner(staged_names, contents):
    """run_git のフェイク。diff --cached と show :path に応答する。"""
    def run_git(args):
        if args[:2] == ["diff", "--cached"]:
            return "\n".join(staged_names) + "\n"
        if args[0] == "show":
            rel = args[1][1:]  # ":path" → "path"
            return contents[rel]
        return ""
    return run_git


def _commit_event(cmd="git commit -m 'x'"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def _enable(tmp_path):
    pf = tmp_path / REL
    pf.parent.mkdir(parents=True)
    pf.write_text(CANONICAL, encoding="utf-8")
    pitfall_registry.add_managed(tmp_path, pf)


def test_is_git_commit_detection():
    assert gate.is_git_commit("git commit -m 'x'")
    assert gate.is_git_commit("git -c gpg.sign=false commit")
    assert not gate.is_git_commit("git status")
    assert not gate.is_git_commit("git push")
    # commit- で始まる別サブコマンドを誤検知しない（commit\b の過検知バグ回帰）
    assert not gate.is_git_commit("git commit-graph write")
    assert gate.is_git_commit("git -C /repo commit -m x")


def test_non_bash_allows(tmp_path):
    _enable(tmp_path)
    ev = {"tool_name": "Edit", "tool_input": {}}
    assert gate.evaluate(ev, str(tmp_path), _make_runner([], {}))["decision"] == "allow"


def test_non_commit_bash_allows(tmp_path):
    _enable(tmp_path)
    ev = _commit_event("git status")
    assert gate.evaluate(ev, str(tmp_path), _make_runner([], {}))["decision"] == "allow"


def test_no_managed_files_allows(tmp_path):
    # enable していない PJ では何もしない
    runner = _make_runner([REL], {REL: INDEX})
    assert gate.evaluate(_commit_event(), str(tmp_path), runner)["decision"] == "allow"


def test_danger_staged_denies(tmp_path):
    _enable(tmp_path)
    runner = _make_runner([REL], {REL: INDEX})
    res = gate.evaluate(_commit_event(), str(tmp_path), runner)
    assert res["decision"] == "deny"
    assert REL in res["message"]


def test_drift_staged_warns_not_blocks(tmp_path):
    _enable(tmp_path)
    runner = _make_runner([REL], {REL: DRIFT})
    res = gate.evaluate(_commit_event(), str(tmp_path), runner)
    assert res["decision"] == "warn"


def test_canonical_staged_allows(tmp_path):
    _enable(tmp_path)
    runner = _make_runner([REL], {REL: CANONICAL})
    assert gate.evaluate(_commit_event(), str(tmp_path), runner)["decision"] == "allow"


def test_project_dir_is_repo_subdir(tmp_path):
    """project_dir が repo ルートのサブディレクトリでも、絶対パス突合で正しく検査する。"""
    import pitfall_registry
    repo_root = tmp_path
    project_dir = tmp_path / "subpkg"
    pf = project_dir / "pitfalls.md"
    pf.parent.mkdir(parents=True)
    pf.write_text(CANONICAL, encoding="utf-8")
    pitfall_registry.add_managed(project_dir, pf)  # キーは project 相対 "pitfalls.md"

    def run_git(args):
        if args[:2] == ["diff", "--cached"]:
            return "subpkg/pitfalls.md\n"  # repo ルート相対
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return str(repo_root) + "\n"
        if args[0] == "show":
            return INDEX  # danger
        return ""

    res = gate.evaluate(_commit_event(), str(project_dir), run_git)
    assert res["decision"] == "deny"
    assert "subpkg/pitfalls.md" in res["message"]


def test_managed_but_not_staged_allows(tmp_path):
    _enable(tmp_path)
    # 別ファイルだけ staged → 管理対象は staged されていないので通す
    runner = _make_runner(["README.md"], {})
    assert gate.evaluate(_commit_event(), str(tmp_path), runner)["decision"] == "allow"
