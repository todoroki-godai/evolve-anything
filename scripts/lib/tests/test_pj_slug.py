"""pj_slug 単一ソースのテスト（#492）。

- pj_slug_fast: 文字列処理のみ（subprocess なし）。本体 / worktree / None / 末尾スラッシュ。
- resolve_pj_slug: git-common-dir authoritative + git 外 fallback。
- read/write 整合: worktree cwd で書いた slug を本体 cwd の read が見つける（往復）。
- 後方互換: resolve_slug / pj_slug_from_cwd が新関数に委譲。

決定論・LLM 非依存。HOME 隔離は conftest の autouse で済む。
"""
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import pj_slug  # noqa: E402


# ── pj_slug_fast（文字列のみ）─────────────────────────────────────────────
def test_fast_main_repo():
    assert pj_slug.pj_slug_fast("/Users/x/tools/rl-anything") == "rl-anything"


def test_fast_worktree_normalizes_to_main():
    cwd = "/Users/x/tools/rl-anything/.claude/worktrees/agent-many"
    assert pj_slug.pj_slug_fast(cwd) == "rl-anything"


def test_fast_hyphenated_name():
    assert pj_slug.pj_slug_fast("/Users/x/work/ai-daily-report") == "ai-daily-report"


def test_fast_trailing_slash():
    assert pj_slug.pj_slug_fast("/Users/x/tools/rl-anything/") == "rl-anything"


def test_fast_none_and_empty_return_none():
    assert pj_slug.pj_slug_fast(None) is None
    assert pj_slug.pj_slug_fast("") is None


def test_fast_accepts_path_object():
    assert pj_slug.pj_slug_fast(Path("/Users/x/proj/myrepo")) == "myrepo"


def test_fast_does_not_invoke_subprocess(monkeypatch):
    """hot path 保証: pj_slug_fast は subprocess を一切呼ばない。"""
    def _boom(*a, **k):
        raise AssertionError("pj_slug_fast must not invoke subprocess")

    monkeypatch.setattr(pj_slug.subprocess, "run", _boom)
    assert pj_slug.pj_slug_fast("/a/b/repo") == "repo"


# ── resolve_pj_slug（authoritative）───────────────────────────────────────
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


def test_resolve_uses_git_common_dir_parent(tmp_path):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    assert pj_slug.resolve_pj_slug(repo) == "myrepo"


def test_resolve_from_worktree_normalizes_to_main(tmp_path):
    """git worktree から呼んでも本体 repo 名に正規化される（authoritative）。"""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    wt = tmp_path / "wt-area" / "feature-x"
    _git(["worktree", "add", "-q", str(wt)], repo)
    assert pj_slug.resolve_pj_slug(wt) == "myrepo"


def test_resolve_outside_git_falls_back_to_fast(tmp_path, monkeypatch):
    """git 外: 文字列フォールバック。worktree マーカーがあれば本体名へ正規化。"""
    # git が成功しないよう subprocess.run を強制失敗させる
    def _fail(*a, **k):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(pj_slug.subprocess, "run", _fail)
    cwd = "/Users/x/tools/rl-anything/.claude/worktrees/agent-z"
    assert pj_slug.resolve_pj_slug(cwd) == "rl-anything"


def test_resolve_unattributed_when_nothing_resolves(monkeypatch):
    def _fail(*a, **k):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(pj_slug.subprocess, "run", _fail)
    assert pj_slug.resolve_pj_slug("") == pj_slug.UNATTRIBUTED_SLUG


def test_resolve_plain_nongit_dir_returns_unattributed(monkeypatch):
    """git 不可 かつ worktree マーカー無しの素の dir は _unattributed（旧挙動温存）。

    worktree マーカーが無ければ basename フォールバックしない — calibration 除外の
    セマンティクスを壊さないための限定フォールバック契約。
    """
    def _fail(*a, **k):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(pj_slug.subprocess, "run", _fail)
    assert pj_slug.resolve_pj_slug("/Users/x/some/plain-dir") == pj_slug.UNATTRIBUTED_SLUG


# ── read/write 整合の往復（往復テスト）─────────────────────────────────────
def test_roundtrip_worktree_write_main_read(tmp_path):
    """worktree cwd で導出した slug を本体 cwd の read が一致して見つける。"""
    repo = tmp_path / "proj-rt"
    _init_repo(repo)
    wt = tmp_path / "wt" / "agent-rt"
    _git(["worktree", "add", "-q", str(wt)], repo)

    write_slug = pj_slug.resolve_pj_slug(wt)   # worktree から書く
    read_slug = pj_slug.resolve_pj_slug(repo)  # 本体から読む
    assert write_slug == read_slug == "proj-rt"


# ── 後方互換 wrapper ──────────────────────────────────────────────────────
def test_resolve_slug_wrapper_delegates(tmp_path):
    import optimize_history_store as store
    repo = tmp_path / "wrap-repo"
    _init_repo(repo)
    assert store.resolve_slug(repo) == "wrap-repo"


def test_pj_slug_from_cwd_wrapper_delegates():
    from utterance_archive.extractor import pj_slug_from_cwd
    cwd = "/Users/x/tools/rl-anything/.claude/worktrees/agent-many"
    assert pj_slug_from_cwd(cwd) == "rl-anything"
    assert pj_slug_from_cwd(None) is None
    assert pj_slug_from_cwd("") is None
