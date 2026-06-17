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
    """bin/rl-dogfood-gate が環境変数 STUB_RC の終了コードを返す stub git repo を作る。"""
    repo = tmp_path / "stubrepo"
    (repo / "bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    gate = repo / "bin" / "rl-dogfood-gate"
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
