"""CC 組み込みスラッシュコマンド除外リストの単一ソース契約テスト（#333）。

trajectory_sampler.py / discover/runner.py に別々のリテラルで存在していた
「CC 組み込みコマンドか」の知識を rl_common.detection.CC_BUILTIN_COMMANDS に
一元化する。ここでは:

- 単一ソースが両 call site から import されていること（ローカル再定義が
  無いこと）を静的にも動的にも検査する（copied-parse-convention pitfall の
  再発防止・#40 と同型）
- `/effort` が両経路で組み込みとして除外されること
"""
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from rl_common.detection import CC_BUILTIN_COMMANDS  # noqa: E402
from skill_extractor.trajectory_sampler import _extract_skill_from_turn  # noqa: E402
from skill_extractor import trajectory_sampler  # noqa: E402
from discover.runner import _is_already_existing_skill  # noqa: E402
from discover import runner  # noqa: E402

_TRAJECTORY_SAMPLER_SRC = Path(trajectory_sampler.__file__).read_text()
_RUNNER_SRC = Path(runner.__file__).read_text()

# ローカルでビルトインコマンド集合を再定義していないか（`NAME = frozenset(` /
# `NAME = {` 形の代入）を検査する正規表現。import 文自体は除外する。
_LOCAL_LITERAL_RE = re.compile(
    r"^_?(?:BUILTIN_COMMANDS|CC_BUILTIN_COMMANDS)\s*=\s*(?:frozenset\(|\{)",
    re.MULTILINE,
)


def test_cc_builtin_commands_is_single_source_union():
    """rl_common.detection.CC_BUILTIN_COMMANDS が両旧リストの union を含む。"""
    old_trajectory_sampler_list = {
        "compact", "rename", "reload-plugins", "plugin", "clear", "help",
        "resume", "init", "config", "memory", "logout", "login", "status",
        "vim", "doctor",
    }
    old_discover_runner_list = {
        "loop", "model", "compact", "clear", "help", "cost", "init", "config",
        "doctor", "status", "resume", "memory", "permissions", "mcp", "agents",
        "fast", "vim", "login", "logout", "add-dir", "bug", "terminal-setup",
    }
    assert old_trajectory_sampler_list <= CC_BUILTIN_COMMANDS
    assert old_discover_runner_list <= CC_BUILTIN_COMMANDS


def test_cc_builtin_commands_includes_effort():
    """/effort は両リストに無かったため素通りしていた（issue 本体）。"""
    assert "effort" in CC_BUILTIN_COMMANDS


def test_trajectory_sampler_has_no_local_builtin_literal():
    """trajectory_sampler.py がローカルにビルトイン集合を再定義していない。"""
    assert not _LOCAL_LITERAL_RE.search(_TRAJECTORY_SAMPLER_SRC), (
        "trajectory_sampler.py should import CC_BUILTIN_COMMANDS from "
        "rl_common.detection instead of redefining it locally"
    )
    assert "rl_common.detection import" in _TRAJECTORY_SAMPLER_SRC
    assert "CC_BUILTIN_COMMANDS" in _TRAJECTORY_SAMPLER_SRC


def test_discover_runner_has_no_local_builtin_literal():
    """discover/runner.py がローカルにビルトイン集合を再定義していない。"""
    assert not _LOCAL_LITERAL_RE.search(_RUNNER_SRC), (
        "discover/runner.py should import CC_BUILTIN_COMMANDS from "
        "rl_common.detection instead of redefining it locally"
    )
    assert "rl_common.detection import" in _RUNNER_SRC
    assert "CC_BUILTIN_COMMANDS" in _RUNNER_SRC


def test_extract_skill_from_turn_ignores_effort():
    """trajectory_sampler 経由でも /effort が CREATE 候補として抽出されない。"""
    turn = {
        "type": "user",
        "message": {"role": "user", "content": "<command-name>/effort</command-name>"},
    }
    assert _extract_skill_from_turn(turn) is None


def test_is_already_existing_skill_treats_effort_as_builtin():
    """discover 経由でも /effort が CREATE 候補として抽出されない。"""
    assert _is_already_existing_skill("effort", set()) is True
