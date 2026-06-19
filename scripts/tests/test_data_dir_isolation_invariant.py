#!/usr/bin/env python3
"""DATA_DIR テスト隔離の不変条件テスト（#420）。

実環境の growth-journal.jsonl が 87% テスト汚染した根因は、store モジュールが
import 時に DATA_DIR を確定する一方、conftest の隔離が per-test の
``monkeypatch.setenv`` で「import より後」に走り、import 時キャプチャ組には効か
なかったこと。さらに手動 patch 許可リスト（session_store / token_usage_store /
optimize_history_store）に growth_journal が入っておらず「4 匹目のモグラ」だった。

本テストは許可リスト方式の構造的再発を封じる契約:
  pytest 下で **scripts/lib 配下から DATA_DIR を参照する全 store モジュール** の
  解決先が実 home（~/.claude）配下でないことを assert する。store を列挙ベタ書き
  せず機械発見することで、新 store 追加時の隔離漏れを検出する。

前提: ルート conftest がトップレベルで ``CLAUDE_PLUGIN_DATA`` を session 一時 dir に
設定しているため、import 時キャプチャ組も含め実 home から構造的に隔離される。
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_LIB_DIR = _PLUGIN_ROOT / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

# 実 home（~/.claude）配下を「汚染先」とみなす基準。
_REAL_HOME_CLAUDE = (Path.home() / ".claude").resolve()

# DATA_DIR を module-level でキャプチャするモジュールの発見パターン。
# 例: ``DATA_DIR = Path(_PLUGIN_DATA_ENV) if ...`` / ``_DATA_DIR_VAL = _common.DATA_DIR``
_DATA_DIR_CAPTURE = re.compile(
    r"^\s*(DATA_DIR|_DATA_DIR_VAL)\s*=", re.MULTILINE
)

# import で副作用（重い処理・引数必須）が出るモジュールは対象外。
# DATA_DIR をキャプチャしない（= 隔離不要）ものも自然に除外される。
_SKIP_MODULES = {
    "rl_common",  # パッケージ（__init__）— 直下の DATA_DIR は別途検査
}


def _discover_store_modules():
    """scripts/lib 配下から DATA_DIR を module-level 参照するモジュール名を機械発見。"""
    found = []
    for py in sorted(_LIB_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        text = py.read_text(encoding="utf-8")
        if not _DATA_DIR_CAPTURE.search(text):
            continue
        mod = py.stem
        if mod in _SKIP_MODULES:
            continue
        found.append(mod)
    return found


_STORE_MODULES = _discover_store_modules()


def _resolved_data_dir(mod):
    """module の DATA_DIR / _DATA_DIR_VAL を取り出して resolve する。"""
    for attr in ("DATA_DIR", "_DATA_DIR_VAL"):
        val = getattr(mod, attr, None)
        if val is not None:
            return Path(val).resolve()
    return None


def test_store_modules_discovered():
    """機械発見が空でない（パターン崩れの早期検知）。"""
    assert _STORE_MODULES, "no store modules discovered — discovery pattern may be broken"
    # 既知の代表 store が含まれていることを確認（発見ロジックの sanity check）。
    for expected in ("session_store", "token_usage_store"):
        assert expected in _STORE_MODULES, f"{expected} not discovered"


@pytest.mark.parametrize("mod_name", _STORE_MODULES)
def test_store_data_dir_not_real_home(mod_name):
    """pytest 下で各 store モジュールの DATA_DIR が実 home 配下に解決されない。"""
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"{mod_name} not importable in isolation: {e}")
        return

    resolved = _resolved_data_dir(mod)
    if resolved is None:
        pytest.skip(f"{mod_name} exposes no DATA_DIR/_DATA_DIR_VAL attribute")
        return

    assert _REAL_HOME_CLAUDE not in resolved.parents and resolved != _REAL_HOME_CLAUDE, (
        f"{mod_name}.DATA_DIR resolved to real home: {resolved}. "
        "conftest top-level CLAUDE_PLUGIN_DATA isolation is not effective for this module "
        "(it would pollute ~/.claude/evolve-anything during tests)."
    )


def test_growth_journal_isolated():
    """growth_journal（#420 の 4 匹目のモグラ）が明示的に隔離されている。"""
    gj = importlib.import_module("growth_journal")
    resolved = gj._data_dir().resolve()
    assert _REAL_HOME_CLAUDE not in resolved.parents and resolved != _REAL_HOME_CLAUDE, (
        f"growth_journal._data_dir() resolved to real home: {resolved}"
    )
