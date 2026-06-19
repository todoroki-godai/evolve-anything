"""restore_state の sibling-dir worktree slug cache（#29/#593）。

SessionStart で1回だけ ``resolve_pj_slug(cwd)``（authoritative・subprocess 可）を解決し、
``{cwd: slug}`` を DATA_DIR の pj_slug_cache.json に書く。これで hooks hot path の
``pj_slug_fast`` がマーカー外 sibling worktree でも本体 slug を引ける（subprocess なし）。

書き込み先は tmp_path のみ（CLAUDE_PLUGIN_DATA で隔離）。実環境は読まない。
決定論・LLM 非依存。
"""
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import pj_slug  # noqa: E402
import restore_state  # noqa: E402


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@t"], path)
    _git(["config", "user.name", "t"], path)
    (path / "f.txt").write_text("x")
    _git(["add", "."], path)
    _git(["commit", "-qm", "init"], path)


def test_persist_writes_authoritative_slug_for_sibling_worktree(tmp_path, monkeypatch):
    """sibling-dir worktree から SessionStart が本体 slug を cache に書く。"""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    # sibling-dir worktree（/.claude/worktrees/ マーカー外）
    sibling = tmp_path / "myrepo-wt" / "issue-1"
    _git(["worktree", "add", "-q", str(sibling)], repo)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sibling))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))

    restore_state._persist_pj_slug_cache()

    # hot path から cache 経由で本体 slug を引ける
    assert pj_slug.pj_slug_fast(str(sibling), data_dir=data_dir) == "myrepo"


def test_persist_silent_without_project_dir(tmp_path, monkeypatch, capsys):
    """CLAUDE_PROJECT_DIR 未設定なら cache に書かず沈黙する。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    restore_state._persist_pj_slug_cache()
    assert not (data_dir / pj_slug.PJ_SLUG_CACHE_FILENAME).exists()
    assert capsys.readouterr().out == ""


def test_persist_skips_unattributed_non_git_dir(tmp_path, monkeypatch):
    """git 外の素 dir（_unattributed）は cache に書かない。"""
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    restore_state._persist_pj_slug_cache()
    assert not (data_dir / pj_slug.PJ_SLUG_CACHE_FILENAME).exists()


def test_persist_called_from_handle_session_start(tmp_path, monkeypatch):
    """handle_session_start が _persist_pj_slug_cache を呼ぶ（配線確認）。"""
    repo = tmp_path / "wiredrepo"
    _init_repo(repo)
    sibling = tmp_path / "wiredrepo-wt" / "x"
    _git(["worktree", "add", "-q", str(sibling)], repo)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sibling))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))

    restore_state.handle_session_start({})

    assert pj_slug.pj_slug_fast(str(sibling), data_dir=data_dir) == "wiredrepo"


def test_persist_failsafe_on_error(tmp_path, monkeypatch, capsys):
    """slug 解決で例外が出ても hook を落とさない（stderr に 1 行・degrade）。"""
    repo = tmp_path / "errrepo"
    _init_repo(repo)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(restore_state._pj_slug, "resolve_pj_slug", _boom)
    # 例外を伝播させない
    restore_state._persist_pj_slug_cache()
    err = capsys.readouterr().err
    assert "pj_slug cache error" in err
