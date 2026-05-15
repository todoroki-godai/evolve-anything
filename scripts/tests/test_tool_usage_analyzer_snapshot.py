"""tool_usage_analyzer のリファクタ防御スナップショットテスト。

Phase 6 (tool_usage_analyzer.py 867 行 → tool_usage_analyzer/ パッケージ分割) で
公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_tool_usage_analyzer_snapshot.py` で。
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

import tool_usage_analyzer  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# tool_usage_analyzer module constants"]
    consts = {}
    for name in dir(tool_usage_analyzer):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(tool_usage_analyzer, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# tool_usage_analyzer public function / class signatures")
    members = []
    for name in dir(tool_usage_analyzer):
        if name.startswith("_"):
            continue
        obj = getattr(tool_usage_analyzer, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (
            mod == "tool_usage_analyzer" or mod.startswith("tool_usage_analyzer.")
        ):
            members.append(name)
    for name in sorted(members):
        obj = getattr(tool_usage_analyzer, name)
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


def test_tool_usage_analyzer_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 6 (tool_usage_analyzer/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (scripts/tests / skills/evolve / scripts/lib/discover 等) の
    `from tool_usage_analyzer import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "tool_usage_analyzer_api_surface.txt")
