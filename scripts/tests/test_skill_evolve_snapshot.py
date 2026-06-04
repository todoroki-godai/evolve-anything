"""skill_evolve のリファクタ防御スナップショットテスト。

Phase 8 (skill_evolve.py 754 行 → skill_evolve/ パッケージ分割) で
skill_evolve の公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_skill_evolve_snapshot.py` で。
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

import skill_evolve  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# skill_evolve module constants"]
    consts = {}
    for name in dir(skill_evolve):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(skill_evolve, name)
        if isinstance(val, (int, float, str, bool, tuple, frozenset)) and not callable(val):
            if isinstance(val, frozenset):
                consts[name] = f"frozenset({sorted(val)!r})"
            else:
                consts[name] = repr(val)
        elif isinstance(val, list) and all(isinstance(x, (str, int, float, bool)) for x in val):
            consts[name] = repr(val)
        elif isinstance(val, dict) and all(
            isinstance(k, (str, int)) and isinstance(v, (str, int, float, bool))
            for k, v in val.items()
        ):
            consts[name] = repr(sorted(val.items()))
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]}")
    lines.append("")
    lines.append("# skill_evolve public function / class signatures")
    members = []
    for name in dir(skill_evolve):
        if name.startswith("_"):
            continue
        obj = getattr(skill_evolve, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "skill_evolve" or mod.startswith("skill_evolve.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(skill_evolve, name)
        try:
            sig = inspect.signature(obj)
            lines.append(f"{name}{sig}")
        except (TypeError, ValueError):
            lines.append(f"{name} (no signature)")
    lines.append("")
    lines.append("# skill_evolve private helpers expected by tests / mock.patch")
    expected_private = [
        "_file_hash", "_load_cache", "_save_cache",
        "_score_execution_frequency", "_score_failure_diversity",
        "_score_output_evaluability", "_count_external_keywords",
        "_score_external_dependency", "_score_judgment_complexity_static",
        "_parse_judgment_response", "_customize_template",
        "_parse_customization_response", "_find_project_dir",
        "_plugin_root", "CACHE_FILE", "DATA_DIR",
    ]
    for name in expected_private:
        present = hasattr(skill_evolve, name)
        lines.append(f"{name}: {'present' if present else 'MISSING'}")
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


def test_skill_evolve_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値 + 期待される private 名の dump。

    Phase 8 (skill_evolve/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (`from skill_evolve import X`) と
    `mock.patch("skill_evolve.X")` の互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "skill_evolve_api_surface.txt")
