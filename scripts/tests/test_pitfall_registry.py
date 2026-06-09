"""pitfall_registry のテスト（決定論・LLM 非依存）。

レジストリは「hook がどの pitfalls.md を監視するか」のオプトイン台帳。
install で hook は配られるが、enable で登録するまで hook は無反応、という設計を支える。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib import pitfall_registry as reg


def _pf(tmp_path: Path) -> Path:
    d = tmp_path / ".claude" / "skills" / "x" / "references"
    d.mkdir(parents=True)
    p = d / "pitfalls.md"
    p.write_text("# Pitfalls\n", encoding="utf-8")
    return p


def test_load_empty_when_no_registry(tmp_path):
    assert reg.load_managed(tmp_path) == []


def test_add_then_is_managed(tmp_path):
    pf = _pf(tmp_path)
    assert reg.add_managed(tmp_path, pf) is True
    assert reg.is_managed(tmp_path, pf) is True
    assert reg.load_managed(tmp_path) == [
        ".claude/skills/x/references/pitfalls.md"
    ]


def test_add_duplicate_is_noop(tmp_path):
    pf = _pf(tmp_path)
    reg.add_managed(tmp_path, pf)
    assert reg.add_managed(tmp_path, pf) is False
    assert reg.load_managed(tmp_path) == [
        ".claude/skills/x/references/pitfalls.md"
    ]


def test_unmanaged_file_is_not_managed(tmp_path):
    pf = _pf(tmp_path)
    other = tmp_path / ".claude" / "skills" / "y" / "pitfalls.md"
    assert reg.is_managed(tmp_path, pf) is False
    assert reg.is_managed(tmp_path, other) is False


def test_remove_managed(tmp_path):
    pf = _pf(tmp_path)
    reg.add_managed(tmp_path, pf)
    assert reg.remove_managed(tmp_path, pf) is True
    assert reg.is_managed(tmp_path, pf) is False
    # 二度目は False（既に無い）
    assert reg.remove_managed(tmp_path, pf) is False


def test_corrupt_registry_returns_empty(tmp_path):
    p = tmp_path / ".claude" / "rl-anything" / "pitfall-managed.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not json", encoding="utf-8")
    assert reg.load_managed(tmp_path) == []  # raise しない


def test_path_outside_project_kept_absolute(tmp_path):
    outside = tmp_path.parent / "external_pitfalls.md"
    outside.write_text("# Pitfalls\n", encoding="utf-8")
    reg.add_managed(tmp_path, outside)
    # プロジェクト外は絶対パスのまま記録され、is_managed で一致する
    assert reg.is_managed(tmp_path, outside) is True
    assert str(outside.resolve()) in reg.load_managed(tmp_path)


def test_discover_finds_pitfalls_project_relative(tmp_path):
    # 複数階層に散らばった pitfalls.md を project 相対パスで発見する
    a = tmp_path / ".claude" / "skills" / "x" / "references"
    a.mkdir(parents=True)
    (a / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    b = tmp_path / "skills" / "y"
    b.mkdir(parents=True)
    (b / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    found = reg.discover_pitfalls(tmp_path)
    assert found == [
        ".claude/skills/x/references/pitfalls.md",
        "skills/y/pitfalls.md",
    ]  # ソート済み・決定論


def test_discover_skips_noise_dirs(tmp_path):
    # node_modules / .git 等の中の pitfalls.md は拾わない
    noise = tmp_path / "node_modules" / "pkg"
    noise.mkdir(parents=True)
    (noise / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    real = tmp_path / "docs"
    real.mkdir()
    (real / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    found = reg.discover_pitfalls(tmp_path)
    assert found == ["docs/pitfalls.md"]


def test_discover_skips_worktree_copies(tmp_path):
    # .claude/worktrees/<name>/... は git worktree の一時コピー。本体スキルの
    # pitfalls.md と同一内容のコピーを未登録候補として拾わない（#393）。
    wt = tmp_path / ".claude" / "worktrees" / "issue-x" / ".claude" / "skills" / "foo" / "references"
    wt.mkdir(parents=True)
    (wt / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    real = tmp_path / ".claude" / "skills" / "foo" / "references"
    real.mkdir(parents=True)
    (real / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    found = reg.discover_pitfalls(tmp_path)
    assert found == [".claude/skills/foo/references/pitfalls.md"]
    # unmanaged_candidates も worktree コピーを未登録扱いしない
    assert reg.unmanaged_candidates(tmp_path) == [".claude/skills/foo/references/pitfalls.md"]


def test_discover_empty_when_none(tmp_path):
    assert reg.discover_pitfalls(tmp_path) == []


def test_unmanaged_candidates_excludes_registered(tmp_path):
    a = tmp_path / "docs"
    a.mkdir()
    (a / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    b = tmp_path / "skills" / "y"
    b.mkdir(parents=True)
    (b / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    reg.add_managed(tmp_path, a / "pitfalls.md")
    # docs は登録済み → skills/y のみ未登録として残る
    assert reg.unmanaged_candidates(tmp_path) == ["skills/y/pitfalls.md"]


def test_unmanaged_candidates_all_when_none_registered(tmp_path):
    a = tmp_path / "docs"
    a.mkdir()
    (a / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    assert reg.unmanaged_candidates(tmp_path) == ["docs/pitfalls.md"]


def test_unmanaged_candidates_empty_when_all_registered(tmp_path):
    a = tmp_path / "docs"
    a.mkdir()
    (a / "pitfalls.md").write_text("# Pitfalls\n", encoding="utf-8")
    reg.add_managed(tmp_path, a / "pitfalls.md")
    assert reg.unmanaged_candidates(tmp_path) == []
