"""verification_catalog のリファクタ防御スナップショットテスト。

Phase 7 (verification_catalog.py 828 行 → verification_catalog/ パッケージ分割) で
verification_catalog の公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_verification_catalog_snapshot.py` で。
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

from lib import verification_catalog  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# verification_catalog module constants"]
    consts = {}
    for name in dir(verification_catalog):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(verification_catalog, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# verification_catalog public function / class signatures")
    members = []
    for name in dir(verification_catalog):
        if name.startswith("_"):
            continue
        obj = getattr(verification_catalog, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (
            mod == "verification_catalog"
            or mod == "lib.verification_catalog"
            or mod.startswith("verification_catalog.")
            or mod.startswith("lib.verification_catalog.")
        ):
            members.append(name)
    for name in sorted(members):
        obj = getattr(verification_catalog, name)
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


def test_verification_catalog_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 7 (verification_catalog/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (discover/ runner / workflow_checkpoint / tests 等) の
    `from lib.verification_catalog import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "verification_catalog_api_surface.txt")
