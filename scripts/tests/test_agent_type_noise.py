"""is_noise_agent_type のユニットテスト（agent_type ノイズ判定・単一ソース）。

#36（空 agent_type 除外）を ID 形（pure hex / UUID / agent_id 形）にも拡張した
writer/reader 共有判定。本物の agent 種別名（カスタム名含む）を誤って除外しないことを
truth table で固定する。決定論・LLM 非依存。
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_LIB = _PLUGIN_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import rl_common  # noqa: E402


# --- ノイズ（除外すべき = True） -------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "",                                          # #36: 空
        "   ",                                       # #36: 空白のみ
        None,                                        # 欠損
        "aab2173eb119c5b91",                         # 実観測: pure hex 17 桁
        "AAB2173EB119C5B91",                         # 大文字 hex
        "aaab2173eb119c5b91-ad6ac9c45992b7e0",       # agent_id 形（hex-hyphen-hex）
        "77037416-f452-4241-a414-4eb497336e71",      # UUID（session_id が漏れた形）
        "0123456789ab",                              # ちょうど 12 hex 桁
    ],
)
def test_ノイズ判定はTrue(value):
    assert rl_common.is_noise_agent_type(value) is True


# --- 本物（保持すべき = False） --------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "general-purpose",
        "senior-engineer",
        "impl-worker",
        "refactor-engineer",
        "doc-writer",
        "Explore",
        "senpai",
        "claude",
        "evolve-anything-advisor",
        "evolve-anything:second-opinion",
        "build-a1",        # カスタム名（'u','i','l' が非 hex）
        "gamer-mvp29",     # カスタム名（'g','m','r','v','p' が非 hex）
        "fapo-impl",       # カスタム名（'p','o','i','m','l' が非 hex）
        "deadbeef",        # pure hex だが 8 桁 < floor 12 → 保持
        "cafe-babe",       # hex+hyphen だが hex 桁 8 < floor 12 → 保持
        "------------",    # ハイフンのみ（hex 桁 0）→ 保持
    ],
)
def test_本物のagent名はFalse(value):
    assert rl_common.is_noise_agent_type(value) is False


def test_前後空白はstripして判定():
    assert rl_common.is_noise_agent_type("  general-purpose  ") is False
    assert rl_common.is_noise_agent_type("  aab2173eb119c5b91  ") is True
