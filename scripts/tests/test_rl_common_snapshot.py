"""rl_common のリファクタ防御スナップショットテスト。

Phase 13 (rl_common.py 548 行 → rl_common/ パッケージ分割) で
rl_common の公開 API surface が変わらないことを byte レベルで保証する。

rl_common はプラグイン全体（hooks/, scripts/lib/, skills/）から
`from rl_common import X` / `import rl_common` で大量に参照されるため、
公開関数・定数の互換性が崩れると広範囲に影響する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_rl_common_snapshot.py` で。
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

import rl_common  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _format_const(val: object) -> str:
    """定数値を deterministic に str 化する。"""
    if isinstance(val, frozenset):
        return f"frozenset({sorted(val)!r})"
    if isinstance(val, set):
        return f"set({sorted(val)!r})"
    if isinstance(val, dict):
        # dict は key sort して deterministic に
        items = sorted(val.items())
        return "{" + ", ".join(f"{k!r}: {v!r}" for k, v in items) + "}"
    if isinstance(val, list):
        return repr(val)
    return repr(val)


def _collect_api_surface() -> str:
    lines = ["# rl_common module constants"]
    consts = {}
    for name in dir(rl_common):
        if name.startswith("_"):
            continue
        val = getattr(rl_common, name)
        if callable(val):
            continue
        # Path や Path-like は str 化（DATA_DIR はテスト環境で変動するため除外）
        if isinstance(val, Path):
            continue
        if isinstance(val, (int, float, str, bool, tuple, frozenset, set, list, dict)):
            consts[name] = _format_const(val)
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]}")
    lines.append("")
    lines.append("# rl_common public function / class signatures")
    members = []
    for name in dir(rl_common):
        if name.startswith("_"):
            continue
        obj = getattr(rl_common, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "rl_common" or mod.startswith("rl_common.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(rl_common, name)
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


def test_rl_common_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 13 (rl_common/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (`from rl_common import X` / `import rl_common`) の
    互換性を保証する SoT。Path 型は環境依存のため除外。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "rl_common_api_surface.txt")
