"""dogfood.layer3 オーケストレーションのユニットテスト（#496）。

合成の skills/ ツリーを作り、抽出→分類→実行の集約を検証する。実 SKILL.md は読まない。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


from dogfood import layer3  # noqa: E402


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "skills" / "good").mkdir(parents=True)
    (repo / "skills" / "bad").mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    (repo / "bin").mkdir()
    # good: stdlib import + bin コマンド存在 + --help
    good_bin = repo / "bin" / "rl-good"
    good_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(good_bin, 0o755)
    (repo / "skills" / "good" / "SKILL.md").write_text(
        "```python\nimport os\n```\n```bash\nrl-good --help\n```\n", encoding="utf-8"
    )
    # bad: 存在しない module import + 存在しない bin コマンド
    (repo / "skills" / "bad" / "SKILL.md").write_text(
        "```python\nfrom this_xyz_missing import foo\n```\n"
        "```bash\nrl-missing-cmd --project-dir x\n```\n",
        encoding="utf-8",
    )
    return repo


def test_run_layer3_aggregates_pass_and_fail(tmp_path):
    repo = _make_repo(tmp_path)
    res = layer3.run_layer3(repo)
    assert res["summary"]["pass"] >= 2  # good の python + bash
    assert res["summary"]["fail"] >= 2  # bad の python + bash
    skills = {s["skill"] for s in res["skills"]}
    assert skills == {"good", "bad"}


def test_find_skill_mds_empty_when_no_skills(tmp_path):
    assert layer3.find_skill_mds(tmp_path) == []


# --- #674: references/*.md の走査漏れ回帰 --------------------------------------------


def _make_repo_with_references(tmp_path: Path) -> Path:
    """SKILL.md + references/*.md を持つ合成 repo（#674: 見逃した prune-merge.md の型）。"""
    repo = tmp_path / "repo"
    (repo / "skills" / "foo" / "references").mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    (repo / "skills" / "foo" / "SKILL.md").write_text(
        "```python\nimport os\n```\n", encoding="utf-8"
    )
    # references/bar.md: #674 と同型のバグ（sys.path 設定が欠落した python3 -c "..."）
    (repo / "skills" / "foo" / "references" / "bar.md").write_text(
        '```bash\npython3 -c "\n'
        "from this_module_does_not_exist_xyz import foo\n"
        "foo('<a>', '<b>')\n"
        '"\n```\n',
        encoding="utf-8",
    )
    return repo


def test_find_skill_mds_includes_references(tmp_path):
    repo = _make_repo_with_references(tmp_path)
    found = layer3.find_skill_mds(repo)
    names = {p.name for p in found}
    assert "SKILL.md" in names
    assert "bar.md" in names
    assert len(found) == 2


def test_skill_name_for_references_resolves_to_parent_skill():
    from pathlib import Path as P

    skill_md = P("/repo/skills/foo/SKILL.md")
    ref_md = P("/repo/skills/foo/references/bar.md")
    assert layer3._skill_name_for(skill_md) == "foo"
    assert layer3._skill_name_for(ref_md) == "foo"


def test_run_layer3_scans_references_and_catches_missing_syspath(tmp_path):
    """#674 の回帰確認: references/*.md 内の埋め込み python import 欠落を layer3 が拾う。

    走査対象に含める（find_skill_mds）＋ import_check への昇格（skill_blocks の修正）の
    両方が揃って初めて赤くなる。skill 名は ``references`` でなく親スキル名 ``foo`` に
    正しく集約されることも確認する。
    """
    repo = _make_repo_with_references(tmp_path)
    res = layer3.run_layer3(repo)
    assert res["summary"]["fail"] >= 1
    skills = {s["skill"] for s in res["skills"]}
    assert skills == {"foo"}  # "references" という偽スキル名が出ない
    all_blocks = [b for s in res["skills"] for b in s["blocks"]]
    fail_blocks = [b for b in all_blocks if b["status"] == "fail"]
    assert any("references/bar.md" in b["source"] for b in fail_blocks)
