"""bin/rl-release-sync の dry-run 契約テスト。

実際の git fetch / claude plugin 操作は副作用が大きく claude CLI に依存するため、
``--dry-run`` でコマンドシーケンスが正しい順序で出力されることを検証する
（単体テストで claude を実行しない — no-llm-in-tests）。
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "rl-release-sync"


def _init_main_repo(tmp_path):
    """plugin.json を持つ main ブランチの git repo を作る。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    pj = repo / ".claude-plugin"
    pj.mkdir()
    (pj / "plugin.json").write_text('{\n  "version": "1.102.0"\n}\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_dry_run_emits_sync_sequence(tmp_path):
    """ff → marketplace update → plugin update の順でコマンドを出す。"""
    repo = _init_main_repo(tmp_path)
    res = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=repo, capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "merge --ff-only origin/main" in out
    assert "claude plugin marketplace update rl-anything" in out
    assert "claude plugin update rl-anything@rl-anything" in out
    i_ff = out.index("merge --ff-only origin/main")
    i_mp = out.index("marketplace update rl-anything")
    i_pl = out.index("plugin update rl-anything@rl-anything")
    assert i_ff < i_mp < i_pl, f"順序違反: {out}"


def test_aborts_when_not_on_main(tmp_path):
    """本体が main 以外をチェックアウト中なら exit 2 で止める（誤同期防止）。"""
    repo = _init_main_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    res = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=repo, capture_output=True, text=True,
    )
    out = res.stdout + res.stderr
    assert res.returncode == 2, out
    assert "main 以外" in out


def test_aborts_outside_git_repo(tmp_path):
    """git repo 外で呼ぶと exit 2。"""
    res = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert res.returncode == 2, res.stdout + res.stderr
