"""scripts/lib/tests の sys.path 設定。"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_marker_root: MARKER_ROOT を隔離せず実定義(home 基準)を検証するテスト用"
    )


@pytest.fixture(autouse=True)
def _isolate_evolve_marker(request, tmp_path, monkeypatch):
    """emit_decisions(#402) は dry-run でも MARKER_ROOT にマーカーを書く。MARKER_ROOT は
    env 非依存の実 home 固定（~/.claude/rl-anything/evolve_pending）なので、隔離しないと
    全テストが実 home を汚す（verify-side-effects）。temp へ向けて構造的に封じる。
    個別テストが MARKER_ROOT を明示 setattr する場合はそちらが後勝ちで上書きする。
    `@pytest.mark.real_marker_root` を付けたテストは隔離せず実定義を検証する。"""
    if request.node.get_closest_marker("real_marker_root"):
        return
    try:
        import evolve_decisions as _ed

        monkeypatch.setattr(_ed, "MARKER_ROOT", tmp_path / "_evolve_pending_isolated", raising=False)
    except Exception:
        pass
