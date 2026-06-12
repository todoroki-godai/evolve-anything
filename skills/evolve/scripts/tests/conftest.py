"""skills/evolve/scripts/tests/ 共通 conftest — HOME 隔離（#457）。

run_evolve を呼ぶテストが ``Path.home() / ".claude" / "projects"`` 経由で実環境の
~/.claude/projects（≈9925 jsonl / 1.9GB）を走査して激遅化していた（フルスイート
1 時間超）。隔離ロジックの本体は ``scripts/lib/test_home_isolation.py``（conftest 名
だと別ディレクトリの同名 conftest を sys.path で shadow するため専用モジュール化）。

注意: ここで ``sys.path`` にこのディレクトリ自身を挿入しないこと。挿入すると
``scripts/tests/`` の ``conftest`` を shadow し、向こうの
``from conftest import _PY_CROSS_MODULE`` 等が ImportError になる（pkg 名衝突 pitfall）。
pytest は conftest を自動探索するので path 挿入は不要。

ルート ``conftest.py`` は ``CLAUDE_PLUGIN_DATA``(=DATA_DIR) を tmp に隔離するが、
``Path.home()`` 由来パスには効かない。本 autouse fixture が ``HOME`` を空 tmp dir へ
隔離して実 store 走査を断つ。検証意図（batch_guard sentinel 伝播など）には触れない
（変えるのは I/O 先のみ、Phase 3.4 の mock 結果 assert はそのまま）。
"""
import sys
from pathlib import Path

import pytest

# helper（scripts/lib/test_home_isolation.py）を import するための path だけ通す。
_LIB_DIR = Path(__file__).resolve().parents[4] / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from test_home_isolation import isolate_home  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_home_for_evolve_tests(monkeypatch, tmp_path):
    """このディレクトリの全テストで HOME を tmp に隔離する（#457）。

    run_evolve を呼ぶテストが実 ~/.claude/projects を走査して激遅化する再発を
    構造的に防ぐ。検証意図には触れない（I/O 先のみ隔離）。
    """
    isolate_home(monkeypatch, tmp_path)
