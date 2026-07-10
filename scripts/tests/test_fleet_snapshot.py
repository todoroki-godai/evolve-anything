"""fleet のリファクタ防御スナップショットテスト。

Slice 0: 後続リファクタ (Phase 1 = fleet/ パッケージ分割) で
fleet の公開 API surface が変わらないことを byte レベルで保証する。

- API surface: 公開関数シグネチャ + module-level constants の dump を fixture 化
- 外部 importer (bin/evolve-fleet, scripts/lib/tests/test_fleet_tokens.py, prune.py 等)
  が依存する `from fleet import X` 形式の import 互換性を担保する SoT

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_fleet_snapshot.py` で。
"""
import inspect
import os
import re
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import fleet  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# fleet module constants"]
    consts = {}
    for name in dir(fleet):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(fleet, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# fleet public function / class signatures")
    members = []
    for name in dir(fleet):
        if name.startswith("_"):
            continue
        obj = getattr(fleet, name)
        # Phase 1 でパッケージ化後は submodule (fleet.formatters 等) も公開 API に含める
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "fleet" or mod.startswith("fleet.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(fleet, name)
        try:
            sig = inspect.signature(obj)
            # デフォルト値が関数オブジェクト（例: run=subprocess.run）だと repr に
            # メモリアドレスが混入しプロセスごとに変わる。アドレスを正規化して hermetic に保つ
            sig_text = re.sub(r" at 0x[0-9a-fA-F]+", " at 0x...", str(sig))
            lines.append(f"{name}{sig_text}")
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


def test_fleet_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 1 (fleet/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (bin/evolve-fleet, prune.py, evolve.py, test_fleet_tokens.py 等) の
    `from fleet import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "fleet_api_surface.txt")


def test_default_rl_audit_bin_exists():
    """fleet/__init__.py の _DEFAULT_RL_AUDIT_BIN が実在のパスを指していること。

    fleet/ パッケージの階層が変わると Path(__file__).parent の数がずれて
    bin/evolve-audit が見つからなくなり、全 PJ が AUDIT_ERROR になる（PR #65 での既発症）。
    """
    assert fleet._DEFAULT_RL_AUDIT_BIN.exists(), (
        f"bin/evolve-audit が見つかりません: {fleet._DEFAULT_RL_AUDIT_BIN}\n"
        "fleet/__init__.py の .parent 数と __file__ の階層が合っているか確認してください"
    )
