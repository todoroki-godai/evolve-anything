"""audit のリファクタ防御スナップショットテスト。

PR-1: 後続リファクタ (PR0 = constants 集約 / Phase 2 = audit/ パッケージ分割) で
audit の振る舞いが変わらないことを byte レベルで保証する。

- API surface: 公開関数シグネチャ + module-level constants の dump を fixture 化
- generate_report 出力: HOME / CLAUDE_PLUGIN_DATA を tmp に向けて完全決定論化し、
  empty / populated 2 種類の入力に対する出力を固定

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_audit_snapshot.py` で。
"""
import importlib
import inspect
import os
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audit  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# audit module constants"]
    consts = {}
    for name in dir(audit):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(audit, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# audit public function signatures")
    funcs = []
    for name in dir(audit):
        if name.startswith("_"):
            continue
        obj = getattr(audit, name)
        # audit パッケージ化後は submodule (audit.memory 等) も公開 API に含める
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "audit" or mod.startswith("audit.")):
            funcs.append(name)
    for name in sorted(funcs):
        sig = inspect.signature(getattr(audit, name))
        lines.append(f"{name}{sig}")
    return "\n".join(lines) + "\n"


def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """audit の出力をホーム環境から切り離す。"""
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    monkeypatch.setenv("HOME", str(home))
    # DATA_DIR は import 時に env var を読むので reload が必要
    if "rl_common" in sys.modules:
        importlib.reload(sys.modules["rl_common"])
    importlib.reload(sys.modules["audit"])
    return proj


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


def test_audit_api_surface_snapshot():
    """公開関数シグネチャ + 定数値の dump。

    PR0 (constants 集約) で値が変わったり関数シグネチャが変わったら検知する。
    Phase 2 でモジュールが分割されても公開 API はこのファイルが SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "audit_api_surface.txt")


def test_generate_report_empty_snapshot(tmp_path, monkeypatch):
    """全引数 empty 相当で generate_report を呼んだ出力 snapshot。

    Phase 2 で section 順序や区切りが変わったら検知する。
    """
    proj = _isolate_env(tmp_path, monkeypatch)
    import audit as _audit  # reload 後の最新を取得
    actual = _audit.generate_report(
        artifacts={"skills": [], "rules": [], "memory": [], "claude_md": []},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=proj,
    )
    _assert_snapshot(actual, "audit_generate_report_empty.txt")


def test_generate_report_populated_snapshot(tmp_path, monkeypatch):
    """各 section に最小の non-empty 入力を与えた出力 snapshot。

    PR0 で section 内のフォーマット文字列が変わったら検知する。
    """
    proj = _isolate_env(tmp_path, monkeypatch)
    import audit as _audit
    actual = _audit.generate_report(
        artifacts={
            "skills": [Path("/x/skill.md")],
            "rules": [Path("/x/rule.md")],
            "memory": [],
            "claude_md": [],
        },
        violations=[
            {"file": "/x/skill.md", "lines": 600, "limit": 500, "category": "skill"},
        ],
        usage={"foo": 3, "bar": 1},
        duplicates=[{"name": "dup", "paths": ["/x/a", "/y/a"]}],
        advisories=[],
        project_dir=proj,
        gstack_analytics=["## gstack analytics", "- mock entry"],
        coherence_report=["## Coherence", "- mock entry"],
        telemetry_report=["## Telemetry", "- mock entry"],
        constitutional_report=["## Constitutional", "- mock entry"],
        environment_report=["## Environment", "- mock entry"],
        pipeline_health_report=["## Pipeline Health", "- mock entry"],
        cross_project_report=["## Cross Project", "- mock entry"],
        growth_report=["## Growth", "- mock entry"],
    )
    _assert_snapshot(actual, "audit_generate_report_populated.txt")
