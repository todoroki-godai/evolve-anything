"""telemetry_query のリファクタ防御スナップショットテスト。

Phase 11 (telemetry_query.py 652 行 → telemetry_query/ パッケージ分割) で
telemetry_query の公開 API surface が変わらないことを byte レベルで保証する。

- API surface: 公開関数シグネチャ + module-level constants の dump を fixture 化
- 外部 importer (audit / discover / evolve / quality_engine / hooks / tests 等)
  が依存する `from telemetry_query import X` 形式の import 互換性を担保する SoT
- mock.patch("telemetry_query.HAS_DUCKDB", False) する既存テストの安全網

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_telemetry_query_snapshot.py` で。
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

import telemetry_query  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# telemetry_query module constants"]
    consts = {}
    for name in dir(telemetry_query):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(telemetry_query, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# telemetry_query public function / class signatures")
    members = []
    for name in dir(telemetry_query):
        if name.startswith("_"):
            continue
        obj = getattr(telemetry_query, name)
        mod = getattr(obj, "__module__", "")
        # Phase 11 でパッケージ化後は submodule (telemetry_query.helpers 等) も公開 API に含める
        if callable(obj) and (mod == "telemetry_query" or mod.startswith("telemetry_query.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(telemetry_query, name)
        try:
            sig = inspect.signature(obj)
            lines.append(f"{name}{sig}")
        except (TypeError, ValueError):
            lines.append(f"{name} (no signature)")
    return "\n".join(lines) + "\n"


def _collect_internal_surface() -> str:
    """_warn_no_duckdb / _load_jsonl 等の internal helper シグネチャも担保する。

    submodule 分割時に内部関数が消えると mock.patch がサイレントに壊れるため、
    `_` 始まりのモジュールレベル callable も snapshot に含める。
    """
    lines = ["# telemetry_query internal helpers (referenced by tests via mock.patch)"]
    members = []
    for name in dir(telemetry_query):
        if not name.startswith("_") or name.startswith("__"):
            continue
        obj = getattr(telemetry_query, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "telemetry_query" or mod.startswith("telemetry_query.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(telemetry_query, name)
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


def test_telemetry_query_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 11 (telemetry_query/ パッケージ分割) で公開 API が変わったら検知する。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "telemetry_query_api_surface.txt")


def test_telemetry_query_internal_surface_snapshot():
    """internal helper (mock.patch 対象) のシグネチャ dump。

    `mock.patch("telemetry_query.HAS_DUCKDB", False)` 等が package 化後も
    解決できることを保証する SoT。
    """
    actual = _collect_internal_surface()
    _assert_snapshot(actual, "telemetry_query_internal_surface.txt")
