"""root conftest の global HOME 隔離（#119）不変テスト。

root conftest の autouse fixture が **全テスト**で ``HOME`` を tmp へ隔離することを
検証する。実 ~/.claude/projects（≈9925 jsonl / 1.9GB）走査による膨張の再発を
構造的に防ぐ（#457 の autouse を skills/evolve のみ → root 全体へ昇格）。

opt-out は ``real_home`` / ``bench`` / ``bench_ingest`` マーカー。
"""
import os
from pathlib import Path

import pytest


def test_home_is_isolated_by_default():
    """マーカー無しのテストでは HOME が tmp へ隔離される（実 home でない）。"""
    home = Path(os.environ["HOME"]).resolve()
    # isolate_home は ``tmp_path / "isolated-home"`` を HOME に固定する。
    assert "isolated-home" in home.parts, f"HOME が隔離されていない: {home}"
    # 隔離先の ~/.claude/projects は空（実 9925 jsonl を走査しない）。
    projects = home / ".claude" / "projects"
    assert projects.exists(), "隔離 HOME 配下に .claude/projects が用意されていない"
    assert list(projects.iterdir()) == [], "隔離 HOME の projects が空でない"


@pytest.mark.real_home
def test_real_home_marker_opts_out():
    """real_home マーカー付きテストは実 HOME を保持する（bench/integration 用の逃げ道）。"""
    home = Path(os.environ["HOME"]).resolve()
    assert "isolated-home" not in home.parts, (
        f"real_home マーカーが HOME 隔離を opt-out できていない: {home}"
    )
