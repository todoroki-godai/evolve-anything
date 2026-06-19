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
    assert pj_slug.pj_slug_fast("/Users/x/tools/evolve-anything") == "evolve-anything"


def test_fast_worktree_normalizes_to_main():
    cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-many"
    assert pj_slug.pj_slug_fast(cwd) == "evolve-anything"


def test_fast_hyphenated_name():
    assert pj_slug.pj_slug_fast("/Users/x/work/ai-daily-report") == "ai-daily-report"


def test_fast_trailing_slash():
    assert pj_slug.pj_slug_fast("/Users/x/tools/evolve-anything/") == "evolve-anything"


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
    cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-z"
    assert pj_slug.resolve_pj_slug(cwd) == "evolve-anything"


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


# ── SessionStart cache（sibling-dir worktree の write 時解決・#29/#593）────────
def test_fast_sibling_worktree_resolves_via_cache(tmp_path):
    """sibling-dir worktree（/.claude/worktrees/ マーカー外）は cache 経由で本体 slug へ。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sibling = "/Users/x/tools/rl-anything-wt/issue-593"
    # SessionStart が authoritative slug を cache に書いたと想定
    pj_slug.write_pj_slug_cache(sibling, "rl-anything", data_dir=data_dir)
    # hot-path: マーカーで畳めないが cache で本体 slug に解決される
    assert pj_slug.pj_slug_fast(sibling, data_dir=data_dir) == "rl-anything"


def test_fast_worktree_marker_unchanged_even_with_cache(tmp_path):
    """/.claude/worktrees/ マーカー worktree はマーカー fast-path 優先（cache 不要・従来どおり）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-many"
    # cache が無くてもマーカーだけで本体 slug に畳める
    assert pj_slug.pj_slug_fast(cwd, data_dir=data_dir) == "evolve-anything"


def test_fast_normal_repo_unchanged_with_cache(tmp_path):
    """通常 repo（worktree でない）は cache の有無で挙動が変わらない（basename）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert pj_slug.pj_slug_fast("/Users/x/tools/evolve-anything", data_dir=data_dir) == "evolve-anything"


def test_fast_cache_miss_falls_back_to_basename(tmp_path):
    """cache 未生成 / cwd miss は従来の basename フォールバック（後方互換）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # cache ファイルが存在しないケース
    sibling = "/Users/x/tools/rl-anything-wt/issue-593"
    assert pj_slug.pj_slug_fast(sibling, data_dir=data_dir) == "issue-593"
    # cache はあるが別 cwd のエントリしかないケース（miss）
    pj_slug.write_pj_slug_cache("/Users/x/tools/other-wt/foo", "other", data_dir=data_dir)
    assert pj_slug.pj_slug_fast(sibling, data_dir=data_dir) == "issue-593"


def test_fast_cache_lookup_does_not_invoke_subprocess(tmp_path, monkeypatch):
    """cache 参照経路も hot-path 安全（subprocess を呼ばない）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sibling = "/Users/x/tools/rl-anything-wt/issue-593"
    pj_slug.write_pj_slug_cache(sibling, "rl-anything", data_dir=data_dir)

    def _boom(*a, **k):
        raise AssertionError("cache lookup must not invoke subprocess")

    monkeypatch.setattr(pj_slug.subprocess, "run", _boom)
    assert pj_slug.pj_slug_fast(sibling, data_dir=data_dir) == "rl-anything"


def test_fast_no_data_dir_arg_keeps_legacy_behavior():
    """data_dir 未指定（既存呼び出し元）はキャッシュを引かず従来 basename 挙動。"""
    sibling = "/Users/x/tools/rl-anything-wt/issue-593"
    assert pj_slug.pj_slug_fast(sibling) == "issue-593"


def test_fast_corrupt_cache_falls_back(tmp_path):
    """cache が壊れた JSON でも例外を投げず basename にフォールバックする。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / pj_slug.PJ_SLUG_CACHE_FILENAME).write_text("{ not json")
    sibling = "/Users/x/tools/rl-anything-wt/issue-593"
    assert pj_slug.pj_slug_fast(sibling, data_dir=data_dir) == "issue-593"


def test_write_cache_normalizes_path_keys(tmp_path):
    """cache lookup は末尾スラッシュ差を吸収して一致させる（write/read 同形正規化）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pj_slug.write_pj_slug_cache("/Users/x/tools/rl-anything-wt/issue-593/", "rl-anything", data_dir=data_dir)
    # 末尾スラッシュ無しで引いても一致
    assert pj_slug.pj_slug_fast("/Users/x/tools/rl-anything-wt/issue-593", data_dir=data_dir) == "rl-anything"


def test_write_cache_merges_existing_entries(tmp_path):
    """write は既存エントリを保持してマージする（複数 PJ の cwd を共存）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pj_slug.write_pj_slug_cache("/Users/x/tools/aaa-wt/x", "aaa", data_dir=data_dir)
    pj_slug.write_pj_slug_cache("/Users/x/tools/bbb-wt/y", "bbb", data_dir=data_dir)
    assert pj_slug.pj_slug_fast("/Users/x/tools/aaa-wt/x", data_dir=data_dir) == "aaa"
    assert pj_slug.pj_slug_fast("/Users/x/tools/bbb-wt/y", data_dir=data_dir) == "bbb"


def test_write_cache_creates_data_dir_if_missing(tmp_path):
    """data_dir が未作成でも write_pj_slug_cache が作成する。"""
    data_dir = tmp_path / "nonexistent" / "data"
    pj_slug.write_pj_slug_cache("/Users/x/tools/zzz-wt/q", "zzz", data_dir=data_dir)
    assert pj_slug.pj_slug_fast("/Users/x/tools/zzz-wt/q", data_dir=data_dir) == "zzz"


# ── 後方互換 wrapper ──────────────────────────────────────────────────────
def test_resolve_slug_wrapper_delegates(tmp_path):
    import optimize_history_store as store
    repo = tmp_path / "wrap-repo"
    _init_repo(repo)
    assert store.resolve_slug(repo) == "wrap-repo"


def test_pj_slug_from_cwd_wrapper_delegates():
    from utterance_archive.extractor import pj_slug_from_cwd
    cwd = "/Users/x/tools/evolve-anything/.claude/worktrees/agent-many"
    assert pj_slug_from_cwd(cwd) == "evolve-anything"
    assert pj_slug_from_cwd(None) is None
    assert pj_slug_from_cwd("") is None
