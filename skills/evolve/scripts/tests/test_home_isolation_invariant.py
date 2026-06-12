#!/usr/bin/env python3
"""run_evolve 系テストの HOME 隔離不変条件テスト（#457）。

フルスイートが 1 時間超かかった根因: ``run_evolve(project_dir=tmp_path)`` でも
post-processing フェーズ（utterance_archive ingest / prune の global skill check /
weak_signals 言い直し検出 / correction_semantic）が ``Path.home() / ".claude" /
"projects"`` を default 走査先に取り、実環境の ~/.claude/projects（≈9925 jsonl /
1.9GB）を読んでいた。``conftest`` の DATA_DIR(=CLAUDE_PLUGIN_DATA) 隔離は
``Path.home()`` 由来パスには効かないため、本フェーズだけ実 home を読んでいた。

本テストは ``skills/evolve/scripts/tests/conftest.py`` の autouse fixture が
``HOME`` を tmp に隔離していることを契約として固定する。fixture が外れたら即赤に
なるので、再発（フルスイートの再激遅化）を検出できる。
"""
from pathlib import Path


_REAL_HOME = Path("~").expanduser().resolve()


def test_home_is_isolated_to_tmp():
    """autouse fixture により HOME が実 home でなく tmp を指す。"""
    current = Path.home().resolve()
    assert current != _REAL_HOME, (
        f"HOME is not isolated: Path.home()={current} == real home. "
        "skills/evolve/scripts/tests/conftest.py の isolate_home fixture が "
        "効いていない（run_evolve 系テストが実 ~/.claude/projects を走査して激遅化する）。"
    )


def test_real_projects_store_not_reachable_via_home():
    """実 ~/.claude/projects が Path.home() 経由で到達不能（空 tmp を指す）。"""
    projects = Path.home() / ".claude" / "projects"
    # 隔離された tmp home 配下なので存在しないか空。実 store（数千 jsonl）ではない。
    if projects.exists():
        jsonls = list(projects.rglob("*.jsonl"))
        assert len(jsonls) == 0, (
            f"~/.claude/projects under isolated HOME unexpectedly has "
            f"{len(jsonls)} jsonl files — HOME isolation leaked to real store."
        )
