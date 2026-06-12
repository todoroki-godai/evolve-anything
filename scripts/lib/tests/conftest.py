"""scripts/lib/tests の sys.path 設定 + autouse 隔離。"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_marker_root: MARKER_ROOT を隔離せず実定義(home 基準)を検証するテスト用"
    )
    config.addinivalue_line(
        "markers",
        "real_home: 実 HOME / 実 ~/.claude を意図的に読むテスト（autouse の HOME 隔離をオプトアウト）",
    )


@pytest.fixture(autouse=True)
def _isolate_home_default(request, tmp_path_factory, monkeypatch):
    """HOME を空 tmp dir へデフォルト隔離する（#471 defense-in-depth）。

    新規テストが DATA_DIR の手動 setattr を忘れても実 ``~/.claude`` を読まないようにする
    （#464 を生んだのと同じ潜在ギャップを構造的に塞ぐ）。``isolate_home``（#457 導入）は
    ``Path.home()`` が call-time に HOME を読む性質を使い、空の ``~/.claude/projects`` を指す。

    隔離 home は **テストの ``tmp_path`` とは別の専用 dir**（``tmp_path_factory``）に作る。
    ``tmp_path`` を共有すると ``isolate_home`` が作る ``.claude/projects`` が
    ``list(tmp_path.iterdir()) == []`` 系の I/O-free アサートや、``tmp_path`` を走査対象に
    渡す enumerate 系テストを汚染するため（実測で 7 件衝突）。

    既存テスト（約1111件）との共存:
      - 手動 ``monkeypatch.setattr(mod, "DATA_DIR", tmp_path)`` は後勝ちで上書きするため不変。
      - ``Path.home()`` を production / assertion 双方で使うテストは両側が同じ tmp home に
        揃うため整合する。
      - 実 HOME を意図的に読むテスト（実機 E2E / home 固定定数の検証）は
        ``@pytest.mark.real_home`` でオプトアウトする。

    ``_isolate_evolve_marker`` と同じ monkeypatch スコープ。HOME 変更は
    ``MARKER_ROOT``（import 時に実 home で凍結済み）には影響しないため両 fixture は独立に働く。
    """
    if request.node.get_closest_marker("real_home"):
        return
    home_root = tmp_path_factory.mktemp("isolated_home")
    isolate_home(monkeypatch, home_root)


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
