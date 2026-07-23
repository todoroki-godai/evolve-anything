"""scripts/git-hooks/pre-push.local の終了コード分岐の回帰テスト（繋ぎ目バグ対策）。

繋ぎ目バグ: hook が gate の非0終了を一律「⚠赤検出」に潰すと、light 非対応の
古い gate（共有 hooks を未マージ worktree から踏むと argparse が exit 2）を誤警告し
狼少年になる。gate の終了コード契約（0=緑/1=赤/2=実行エラー）を hook が区別することを、
**実際の bash hook を subprocess で実走**して封じる（mock でなく実走 = このPJの
「テスト緑・実環境赤」対策）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOK = _REPO_ROOT / "scripts" / "git-hooks" / "pre-push.local"


def _make_stub_repo(tmp_path: Path) -> Path:
    """bin/evolve-dogfood-gate が環境変数 STUB_RC の終了コードを返す stub git repo を作る。"""
    repo = tmp_path / "stubrepo"
    (repo / "bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    gate = repo / "bin" / "evolve-dogfood-gate"
    gate.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "print('stub gate', sys.argv[1:])\n"
        "sys.exit(int(os.environ.get('STUB_RC', '0')))\n",
        encoding="utf-8",
    )
    gate.chmod(0o755)
    return repo


def _run_hook(repo: Path, stub_rc: int) -> subprocess.CompletedProcess:
    env = dict(os.environ, STUB_RC=str(stub_rc))
    return subprocess.run(
        ["bash", str(_HOOK), "origin", "git@example.com:x.git"],
        cwd=repo,
        input="",  # managed hook が渡す stdin（push ref 一覧）を空で模す
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_hook_exists_and_executable():
    assert _HOOK.exists(), f"hook source 不在: {_HOOK}"


def test_green_gate_reports_pass(tmp_path):
    repo = _make_stub_repo(tmp_path)
    res = _run_hook(repo, 0)
    assert res.returncode == 0
    assert "全緑" in res.stderr


def test_red_gate_warns_but_nonblocking(tmp_path):
    """gate が実際に赤（exit 1）→ 警告は出すが push は止めない（exit 0）。"""
    repo = _make_stub_repo(tmp_path)
    res = _run_hook(repo, 1)
    assert res.returncode == 0, "非ブロッキング契約: 赤でも hook は exit 0"
    assert "赤を検出" in res.stderr
    assert "スキップ" not in res.stderr


def test_unsupported_layer_skips_silently_not_false_red(tmp_path):
    """gate が exit 2（light 非対応の古い worktree 等）→ 「赤」と誤警告せず soft スキップ。"""
    repo = _make_stub_repo(tmp_path)
    res = _run_hook(repo, 2)
    assert res.returncode == 0
    assert "スキップ" in res.stderr
    assert "赤を検出" not in res.stderr, "繋ぎ目バグ: exit 2 を赤と誤警告してはならない"


def test_missing_gate_passes_through_silently(tmp_path):
    """gate が見つからない repo → 黙って通す（非ブロッキング）。"""
    bare = tmp_path / "nogate"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
    res = _run_hook(bare, 0)
    assert res.returncode == 0
    # gate 不在経路は何も警告しない（全緑・赤・スキップのいずれも出さない）
    assert "全緑" not in res.stderr
    assert "赤を検出" not in res.stderr


# --- sibling_copy_guard 配線（#210）の回帰テスト ------------------------------
#
# 実際の bash hook を subprocess で実走する既存パターンを踏襲。sibling_copy_guard.py
# 自体は stub（呼ばれたら marker ファイルを touch + 環境変数の終了コードで exit）に
# 差し替え、hook 側の「終了コード分岐」「大規模merge push のskipガード」だけを検証する
# （sibling_copy_guard.py 自体のロジックは test_sibling_copy_guard.py が担当）。


def _init_repo_with_commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_stub_repo_with_sibling_guard(tmp_path: Path, marker: Path) -> Path:
    """origin/main ref（fake）+ 実行可能な gate stub + sibling_copy_guard stub を持つ repo。

    sibling_copy_guard stub は呼ばれたら ``marker`` を touch し、環境変数 SIB_RC の
    終了コードで exit する（呼ばれたかどうか＝skip ガードの検証に使う）。
    """
    repo = tmp_path / "stubrepo_sibling"
    (repo / "bin").mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    base_sha = _init_repo_with_commit(repo, "base")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base_sha], cwd=repo, check=True
    )

    gate = repo / "bin" / "evolve-dogfood-gate"
    gate.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    gate.chmod(0o755)

    guard = repo / "scripts" / "lib" / "sibling_copy_guard.py"
    guard.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('called')\n"
        "print('stub sibling guard', sys.argv[1:])\n"
        "sys.exit(int(os.environ.get('SIB_RC', '0')))\n",
        encoding="utf-8",
    )
    guard.chmod(0o755)
    return repo


def _run_hook_env(repo: Path, extra_env: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ, STUB_RC="0", **extra_env)
    return subprocess.run(
        ["bash", str(_HOOK), "origin", "git@example.com:x.git"],
        cwd=repo,
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_sibling_guard_clean_is_silent(tmp_path):
    marker = tmp_path / "called.marker"
    repo = _make_stub_repo_with_sibling_guard(tmp_path, marker)
    res = _run_hook_env(repo, {"SIB_RC": "0"})
    assert res.returncode == 0
    assert marker.exists(), "対象コミット数が閾値以下なら guard は呼ばれるはず"
    assert "兄弟コピー" not in res.stderr, "該当なし（exit 0）は無音（dogfood-gate の全緑と違いノイズを出さない）"


def test_sibling_guard_detection_warns_but_nonblocking(tmp_path):
    marker = tmp_path / "called.marker"
    repo = _make_stub_repo_with_sibling_guard(tmp_path, marker)
    res = _run_hook_env(repo, {"SIB_RC": "1"})
    assert res.returncode == 0, "非ブロッキング契約: 検出ありでも hook は exit 0"
    assert "⚠ diff-scoped 兄弟コピー検出" in res.stderr
    assert "stub sibling guard" in res.stderr, "検出時は CLI 出力（ログ内容）を表示する"


def test_sibling_guard_crash_skips_silently(tmp_path):
    """CLI がクラッシュ等で 0/1 以外を返した場合は fail-open で無音スキップする。"""
    marker = tmp_path / "called.marker"
    repo = _make_stub_repo_with_sibling_guard(tmp_path, marker)
    res = _run_hook_env(repo, {"SIB_RC": "3"})
    assert res.returncode == 0
    assert "⚠ diff-scoped 兄弟コピー検出" not in res.stderr
    assert "兄弟コピー" not in res.stderr


def test_sibling_guard_skips_large_merge_push(tmp_path):
    """非merge commit 数が閾値超（merge push 由来の大規模差分）なら guard 自体を呼ばない。"""
    marker = tmp_path / "called.marker"
    repo = _make_stub_repo_with_sibling_guard(tmp_path, marker)
    # base 以降に非merge commit を 16 個積む（既定閾値 15 を超えさせる）。
    for i in range(16):
        (repo / "f.txt").write_text(f"change {i}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"c{i}"], cwd=repo, check=True)

    res = _run_hook_env(repo, {"SIB_RC": "0"})
    assert res.returncode == 0
    assert not marker.exists(), "閾値超過時は guard 自体を呼ばずスキップする"
    assert "のため skip" in res.stderr
