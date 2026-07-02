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
