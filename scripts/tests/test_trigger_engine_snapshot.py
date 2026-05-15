"""trigger_engine のリファクタ防御スナップショットテスト。

Phase 9 (trigger_engine.py 751 行 → trigger_engine/ パッケージ分割) で
trigger_engine の公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_trigger_engine_snapshot.py` で。
"""
import inspect
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
_HOOKS = _PLUGIN_ROOT / "hooks"
for _p in (_LIB, _SCRIPTS, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import trigger_engine  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# trigger_engine module constants"]
    consts = {}
    for name in dir(trigger_engine):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(trigger_engine, name)
        if isinstance(val, (int, float, str, bool, tuple, frozenset)) and not callable(val):
            if isinstance(val, frozenset):
                consts[name] = f"frozenset({sorted(val)!r})"
            else:
                consts[name] = repr(val)
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]}")
    lines.append("")
    lines.append("# trigger_engine public function / class signatures")
    members = []
    for name in dir(trigger_engine):
        if name.startswith("_"):
            continue
        obj = getattr(trigger_engine, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "trigger_engine" or mod.startswith("trigger_engine.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(trigger_engine, name)
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


def test_trigger_engine_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 9 (trigger_engine/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (`from trigger_engine import X`) の互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "trigger_engine_api_surface.txt")
