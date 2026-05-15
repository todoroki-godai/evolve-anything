"""pipeline_reflector のリファクタ防御スナップショットテスト。

Phase 12 (pipeline_reflector.py 595 行 → pipeline_reflector/ パッケージ分割) で
pipeline_reflector の公開 API surface が変わらないことを保証する。

- API surface: 公開関数シグネチャ + module-level constants の dump を fixture 化
- 外部 importer (audit/orchestrator.py, evolve.py, scripts/lib/tests/test_pipeline_reflector.py 等)
  が依存する `from pipeline_reflector import X` 形式の import 互換性を担保する SoT

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_pipeline_reflector_snapshot.py` で。
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

import pipeline_reflector  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# pipeline_reflector module constants"]
    consts = {}
    for name in dir(pipeline_reflector):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(pipeline_reflector, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# pipeline_reflector public function / class signatures")
    members = []
    for name in dir(pipeline_reflector):
        if name.startswith("_"):
            continue
        obj = getattr(pipeline_reflector, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "pipeline_reflector" or mod.startswith("pipeline_reflector.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(pipeline_reflector, name)
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


def test_pipeline_reflector_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 12 (pipeline_reflector/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (audit/orchestrator.py, skills/evolve/scripts/evolve.py,
    scripts/lib/tests/test_pipeline_reflector.py 等) の
    `from pipeline_reflector import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "pipeline_reflector_api_surface.txt")
