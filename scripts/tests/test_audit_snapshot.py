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
# fitness_evolution は evolve-fitness スキル配下。calibration_drift builder のグローバル
# history を snapshot テストで隔離するため path を通す（_load_fitness_evolution と同経路）。
_FE_SCRIPTS = _PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"
for _p in (_LIB, _SCRIPTS, _FE_SCRIPTS):
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
    # calibration_drift / eval_saturation builder は環境グローバル状態（accept/reject
    # 履歴 / DATA_DIR の eval-sets）を読むため、実機データがあると snapshot がブレる。
    # 空 tmp に向けて出力を決定論化する（#286 / #292 / ADR-031 で store 隔離に移行）。
    import optimize_history_store as _ohs
    monkeypatch.setattr(_ohs, "HISTORY_ROOT", tmp_path / "no-history")
    monkeypatch.setattr(_ohs, "resolve_slug", lambda cwd=None: "no-history")
    import eval_saturation
    monkeypatch.setattr(
        eval_saturation, "_default_eval_sets_dir", lambda: tmp_path / "no-evalsets"
    )
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
        memory_trace_report=["## Memory Trace", "- mock entry"],
        cross_project_report=["## Cross Project", "- mock entry"],
        growth_report=["## Growth", "- mock entry"],
    )
    _assert_snapshot(actual, "audit_generate_report_populated.txt")


def test_run_audit_memory_trace_wiring(tmp_path, monkeypatch):
    """run_audit(memory_trace=True) が MemTrace セクションを出力に流すことを保証する回帰テスト。

    #258 で build_memory_trace_audit_section を実装したが orchestrator から
    呼ばれておらず audit 出力に現れなかった（配線漏れ）。本テストは
    「定義した関数が実際にユーザーの観測する出力に到達するか」を検証する。
    """
    proj = _isolate_env(tmp_path, monkeypatch)
    import audit as _audit

    sentinel = ["## MemTrace 帰属診断", "- SENTINEL_memory_trace_wired"]
    # orchestrator は遅延 import (from .memory import build_memory_trace_audit_section)
    # するため、解決先である submodule の属性を差し替える。_isolate_env が親 audit を
    # reload した後でも submodule を確実に取得するため import_module を使う。
    _mem_mod = importlib.import_module("audit.memory")
    monkeypatch.setattr(
        _mem_mod, "build_memory_trace_audit_section", lambda *a, **k: sentinel
    )

    out_on = _audit.run_audit(str(proj), skip_rescore=True, memory_trace=True)
    assert "SENTINEL_memory_trace_wired" in out_on, "memory_trace=True で MemTrace セクションが出力に現れない（配線漏れ）"

    out_off = _audit.run_audit(str(proj), skip_rescore=True, memory_trace=False)
    assert "SENTINEL_memory_trace_wired" not in out_off, "memory_trace=False なのに MemTrace セクションが出ている"
