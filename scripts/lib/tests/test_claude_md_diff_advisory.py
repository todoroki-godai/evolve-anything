"""claude_md_diff_advisory（CLAUDE.md 変更時に契約語を含む差分行を CI ログへ出す advisory）
のテスト（#415）。

判定・fail は一切しないコンポーネントなので、テストも「必ず exit 0 で終わること」と
「契約語を含む行を正しく拾えること」だけを見る。決定論・LLM 非依存・実 git を subprocess で
呼ぶ（実データストアへは書き込まない・tmp_path 内のみで完結）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import claude_md_diff_advisory  # noqa: E402


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def test_git_diff_returns_none_for_bad_ref(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    _commit(root, "init")
    assert claude_md_diff_advisory._git_diff(root, "nonexistent-ref-xyz") is None


def test_main_exits_zero_on_bad_ref(tmp_path: Path, capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    _commit(root, "init")
    rc = claude_md_diff_advisory.main(["nonexistent-ref-xyz"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "スキップ" in out


def test_main_reports_contract_token_lines(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "CLAUDE.md").write_text("# CLAUDE.md\n\n本文\n", encoding="utf-8")
    _commit(root, "init")
    subprocess.run(["git", "branch", "base"], cwd=root, check=True)

    (root / "CLAUDE.md").write_text("# CLAUDE.md\n\n本文 store_write_raw を削除しました\n", encoding="utf-8")
    _commit(root, "edit")

    monkeypatch.setattr(claude_md_diff_advisory, "_repo_root", lambda: root)
    rc = claude_md_diff_advisory.main(["base"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "契約語を含む行" in out
    assert "store_write_raw" in out


def test_main_no_diff_stays_quiet(tmp_path: Path, monkeypatch, capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "CLAUDE.md").write_text("# CLAUDE.md\n\n本文\n", encoding="utf-8")
    _commit(root, "init")
    subprocess.run(["git", "branch", "base"], cwd=root, check=True)

    monkeypatch.setattr(claude_md_diff_advisory, "_repo_root", lambda: root)
    rc = claude_md_diff_advisory.main(["base"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "差分なし" in out
