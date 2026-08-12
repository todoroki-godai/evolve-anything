"""evolve_revert._target のユニットテスト（#402 段階3 §2 手順2 / C2 / C3）。

対象パス解決 + 安全検査: 最終要素の lstat regular-file 判定と、解決後実体が root 配下
であることを**別々の検査**として実施する。``st_nlink != 1`` は conflict として拒否
（hardlink・M5）。決定論・LLM 非依存。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

from evolve_revert._target import (  # noqa: E402
    REASON_ESCAPES_ROOT,
    REASON_HARDLINK,
    REASON_MISSING_REPO_ID,
    REASON_NOT_FOUND,
    REASON_NOT_REGULAR_FILE,
    REASON_UNSUPPORTED_SCOPE,
    resolve_target,
)


def _entry(**overrides):
    base = {"scope": "project", "repo_id": None, "relative_path": None}
    base.update(overrides)
    return base


def test_resolves_project_scope_target(tmp_path):
    root = tmp_path / "repo"
    (root / "skills" / "my-skill").mkdir(parents=True)
    target = root / "skills" / "my-skill" / "SKILL.md"
    target.write_text("x", encoding="utf-8")

    result = resolve_target(_entry(repo_id=str(root), relative_path="skills/my-skill/SKILL.md"))

    assert result.ok is True
    assert result.path == target
    assert result.nlink == 1


def test_resolves_global_scope_target_under_home_claude_skills(tmp_path):
    home_skills = Path.home() / ".claude" / "skills"
    (home_skills / "my-skill").mkdir(parents=True)
    target = home_skills / "my-skill" / "SKILL.md"
    target.write_text("x", encoding="utf-8")

    result = resolve_target(_entry(scope="global", relative_path="my-skill/SKILL.md"))

    assert result.ok is True
    assert result.path == target


def test_unsupported_scope_is_rejected():
    result = resolve_target(_entry(scope=None, relative_path="x"))
    assert result.ok is False
    assert result.reason == REASON_UNSUPPORTED_SCOPE


def test_missing_repo_id_for_project_scope_is_rejected():
    result = resolve_target(_entry(repo_id=None, relative_path="x"))
    assert result.ok is False
    assert result.reason == REASON_MISSING_REPO_ID


def test_missing_target_file_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    result = resolve_target(_entry(repo_id=str(root), relative_path="nope.md"))
    assert result.ok is False
    assert result.reason == REASON_NOT_FOUND


def test_symlink_at_final_component_is_rejected_not_regular_file(tmp_path):
    """最終要素の lstat regular-file 判定: symlink 自体を replace するのを防ぐ。"""
    root = tmp_path / "repo"
    root.mkdir()
    real = tmp_path / "elsewhere.md"
    real.write_text("x", encoding="utf-8")
    link = root / "SKILL.md"
    link.symlink_to(real)

    result = resolve_target(_entry(repo_id=str(root), relative_path="SKILL.md"))

    assert result.ok is False
    assert result.reason == REASON_NOT_REGULAR_FILE


def test_parent_symlink_escape_is_rejected_by_containment_check_not_lstat(tmp_path):
    """C2: 最終要素 lstat と containment は別検査。中間ディレクトリの symlink 経由の脱出は
    lstat 単体（最終要素のみ非追従）では検出できず、containment 検査が要る。"""
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("x", encoding="utf-8")
    (root / "link_dir").symlink_to(outside)

    # 最終要素（secret.md）自体は symlink でなく regular file なので lstat 単体は通る
    # ——だからこそ containment を別検査にする必要がある（回帰防止）。
    import stat

    st = (root / "link_dir" / "secret.md").lstat()
    assert stat.S_ISREG(st.st_mode)

    result = resolve_target(_entry(repo_id=str(root), relative_path="link_dir/secret.md"))

    assert result.ok is False
    assert result.reason == REASON_ESCAPES_ROOT


def test_hardlink_is_rejected_as_conflict(tmp_path):
    """M5: st_nlink != 1 は conflict として拒否（他リンク先との内容分岐を防ぐ）。"""
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "SKILL.md"
    target.write_text("x", encoding="utf-8")
    other_link = tmp_path / "other-link.md"
    os.link(target, other_link)

    result = resolve_target(_entry(repo_id=str(root), relative_path="SKILL.md"))

    assert result.ok is False
    assert result.reason == REASON_HARDLINK
    assert result.nlink == 2
