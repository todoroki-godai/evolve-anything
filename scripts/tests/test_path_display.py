"""rl_common.path_display のユニットテスト（#479 単一ソース抽出）。"""
import sys
from pathlib import Path
from unittest import mock

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from rl_common.path_display import home_relative_display  # noqa: E402


def test_home_subpath_is_tilde_folded(tmp_path):
    """Path.home() 配下は `~/...` 形式に畳む。"""
    with mock.patch("rl_common.path_display.Path.home", return_value=tmp_path):
        p = tmp_path / ".claude" / "plugins" / "cache" / "evolve-anything" / "SKILL.md"
        result = home_relative_display(p)
    assert result == "~/.claude/plugins/cache/evolve-anything/SKILL.md"
    assert str(tmp_path) not in result


def test_non_home_path_falls_back_to_filename_only(tmp_path):
    """Path.home() 配下でなければファイル名のみを返す（絶対パスを出さない）。"""
    home = tmp_path / "home"
    outside = tmp_path / "elsewhere" / "SKILL.md"
    with mock.patch("rl_common.path_display.Path.home", return_value=home):
        result = home_relative_display(outside)
    assert result == "SKILL.md"
    assert str(outside) not in result
    assert "/" not in result
