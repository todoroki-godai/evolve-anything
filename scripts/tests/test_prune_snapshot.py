"""prune のリファクタ防御スナップショットテスト。

Phase 4 (prune.py 1411 行 → prune/ パッケージ分割) で
prune の公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_prune_snapshot.py` で。
"""
import inspect
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import prune  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# prune module constants"]
    consts = {}
    for name in dir(prune):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(prune, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# prune public function / class signatures")
    members = []
    for name in dir(prune):
        if name.startswith("_"):
            continue
        obj = getattr(prune, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "prune" or mod.startswith("prune.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(prune, name)
        try:
            sig = inspect.signature(obj)
            lines.append(f"{name}{sig}")
        except (TypeError, ValueError):
            lines.append(f"{name} (no signature)")
    return "\n".join(lines) + "\n"


def _assert_snapshot(actual: str, fixture_name: str) -> None:
    fixture = _FIXTURES / fixture_name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        _FIXTURES.mkdir(exist_ok=True)
        fixture.write_text(actual)
        return
    assert fixture.exists(), (
        f"fixture missing: {fixture}. "
        f"Initial run requires UPDATE_SNAPSHOTS=1 pytest."
    )
    expected = fixture.read_text()
    assert actual == expected, (
        f"Snapshot mismatch ({fixture.name}). "
        f"If intentional, regenerate with UPDATE_SNAPSHOTS=1 pytest."
    )


def test_prune_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 4 (prune/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (hooks/tests / scripts/tests / skills/* 等) の
    `from prune import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "prune_api_surface.txt")
