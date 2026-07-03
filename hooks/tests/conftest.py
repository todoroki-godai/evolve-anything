"""hooks/tests/ 共通の sys.path 設定と fixture。

旧 test_hooks.py から PR-A で分離。テーマ別ファイル (test_hooks_*.py) はここを参照する。
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

# hooks/ をインポートパスに追加
_hooks = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks))
# scripts/lib/ も追加（rl_common を直接パッチするため）
sys.path.insert(0, str(_hooks.parent / "scripts" / "lib"))

import common  # noqa: E402
import rl_common  # noqa: E402
import session_store  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_tmpdir(tmp_path_factory, monkeypatch):
    """TMPDIR を隔離する（#495）。

    rl_common.workflow.last_skill_path は ``os.environ.get("TMPDIR", "/tmp")`` を
    直接読むため、write_last_skill/read_last_skill を踏むテスト（observe 経由含む）が
    実 /tmp に ``evolve-anything-last-skill-<session>.json`` を漏らしていた。autouse で
    TMPDIR を専用一時ディレクトリに向け、テスト由来の ephemeral marker を実環境から
    隔離する。``tmp_path`` 直下を汚すと「副作用ゼロ」を assert するテスト
    （iterdir() == []）を壊すため、独立した tmp_path_factory ディレクトリを使う。
    workflow.py 本体（意図的な cross-process marker）は変更しない。
    """
    isolated = tmp_path_factory.mktemp("tmpdir_isolated")
    monkeypatch.setenv("TMPDIR", str(isolated))


@pytest.fixture
def tmp_data_dir(tmp_path):
    """テスト用の一時データディレクトリ。"""
    data_dir = tmp_path / "evolve-anything"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def patch_data_dir(tmp_data_dir):
    """common.DATA_DIR / CHECKPOINTS_DIR と rl_common の同名変数を一時ディレクトリに差し替える。

    common.py が rl_common の re-exporter になったため、関数内部で参照される
    rl_common.DATA_DIR / CHECKPOINTS_DIR / FALSE_POSITIVES_FILE も同時にパッチする。
    """
    checkpoints = tmp_data_dir / "checkpoints"
    fp_file = tmp_data_dir / "false_positives.jsonl"
    # session_store は call-time 解決（#137）。_DATA_DIR_OVERRIDE 1 本で DATA_DIR /
    # SESSIONS_DB / SESSIONS_JSONL がすべて tmp_data_dir 配下に追従する。
    with mock.patch.object(common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(common, "CHECKPOINTS_DIR", checkpoints), \
         mock.patch.object(common, "FALSE_POSITIVES_FILE", fp_file), \
         mock.patch.object(rl_common, "DATA_DIR", tmp_data_dir), \
         mock.patch.object(rl_common, "CHECKPOINTS_DIR", checkpoints), \
         mock.patch.object(rl_common, "FALSE_POSITIVES_FILE", fp_file), \
         mock.patch.object(session_store, "_DATA_DIR_OVERRIDE", tmp_data_dir):
        yield tmp_data_dir
