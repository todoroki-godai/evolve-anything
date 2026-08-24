"""fleet のリファクタ防御スナップショットテスト。

Slice 0: 後続リファクタ (Phase 1 = fleet/ パッケージ分割) で
fleet の公開 API surface が変わらないことを byte レベルで保証する。

- API surface: 公開関数シグネチャ + module-level constants の dump を fixture 化
- 外部 importer (bin/evolve-fleet, scripts/lib/tests/test_fleet_tokens.py, prune.py 等)
  が依存する `from fleet import X` 形式の import 互換性を担保する SoT

#538 round6 [Must]Should2: 従来は ``dir(fleet)`` （top-level 再export のみ）しか走査せず、
``fleet.queue``/``fleet.queue_materials`` のような公開サブモジュール直下の関数・クラスや、
公開クラスの method（``CorrectionsSnapshot.records``/``.health`` 等）が fixture に一切
現れなかった。トップレベルへ再export されていない公開面の変更（例: 型の unpack 互換が
消える・method が削られる）を検知できるよう、公開サブモジュールと公開クラスの method まで
走査範囲を広げた。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_fleet_snapshot.py` で。
"""
import inspect
import os
import pkgutil
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


def _normalize_sig(obj) -> str:
    """シグネチャを hermetic な文字列にする（メモリアドレス正規化・失敗時フォールバック）。"""
    try:
        sig = inspect.signature(obj)
        # デフォルト値が関数オブジェクト（例: run=subprocess.run）だと repr に
        # メモリアドレスが混入しプロセスごとに変わる。アドレスを正規化して hermetic に保つ
        return re.sub(r" at 0x[0-9a-fA-F]+", " at 0x...", str(sig))
    except (TypeError, ValueError):
        return " (no signature)"


def _public_submodules():
    """``fleet`` パッケージ直下の公開（非 ``_`` 始まり）サブモジュールを import して返す。"""
    mods = []
    for _finder, name, _ispkg in pkgutil.iter_modules(fleet.__path__):
        if name.startswith("_"):
            continue
        import importlib

        mods.append(importlib.import_module(f"fleet.{name}"))
    return sorted(mods, key=lambda m: m.__name__)


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
    classes = {}  # name -> class object（top-level 再export された公開クラス）
    for name in dir(fleet):
        if name.startswith("_"):
            continue
        obj = getattr(fleet, name)
        # Phase 1 でパッケージ化後は submodule (fleet.formatters 等) も公開 API に含める
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "fleet" or mod.startswith("fleet.")):
            members.append(name)
            if isinstance(obj, type):
                classes[name] = obj
    for name in sorted(members):
        obj = getattr(fleet, name)
        lines.append(f"{name}{_normalize_sig(obj)}")

    # #538 round6 [Must]Should2: 公開サブモジュール直下の関数・クラスも走査する。
    # トップレベルへ再export されていない公開面（例: ``fleet.queue_materials`` の内部
    # ヘルパーは対象外だが、公開名の関数・クラスは対象）の変更を検知するため。
    lines.append("")
    lines.append("# fleet public submodule function / class signatures")
    submodule_members = []
    for mod in _public_submodules():
        for name in dir(mod):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name)
            if not callable(obj):
                continue
            if getattr(obj, "__module__", "") != mod.__name__:
                continue  # このサブモジュールへの re-export は他モジュールの走査で拾う
            submodule_members.append((mod.__name__, name, obj))
            if isinstance(obj, type):
                classes.setdefault(name, obj)
    for modname, name, obj in sorted(submodule_members, key=lambda t: (t[0], t[1])):
        lines.append(f"{modname}.{name}{_normalize_sig(obj)}")

    # #538 round6-7 [Must]Should2: 公開クラスの public method/property（``__init__`` は上の
    # コンストラクタシグネチャで既に捕捉済みなので除く）を走査する。``CorrectionsSnapshot``
    # の ``records``/``health`` field や、tuple 互換 ``__iter__`` の有無を検知する。
    #
    # round7 レビュー実測: ``vars(cls)`` ベースの列挙は2つの穴があった。
    #   ①``records``/``health`` は ``NamedTuple`` 生成の ``_tuplegetter`` descriptor で
    #     ``isinstance(attr, property)`` に一致せず・``callable(attr)`` も False のため、
    #     ``vars(cls)`` に実在するのに判定条件から漏れて未捕捉だった。
    #   ②``__iter__`` は ``tuple`` から継承されており ``vars(cls)``（cls 自身の
    #     ``__dict__``）には現れない。継承メンバーは ``dir(cls)`` でなければ拾えない。
    # ``records`` を削除する変異を適用すると、①のみを直しても ``vars(cls)`` からその名前
    # ごと消えるため fixture は変化するが、②のクラスで継承メンバーが漏れたままだと
    # 「public な tuple-compat 契約が消えたのに fixture が無反応」という別の穴が残る。
    # ``dir(cls)`` ベースに切り替え、名前解決は ``getattr(cls, name)`` で行う（descriptor は
    # class 経由アクセスで descriptor オブジェクト自身が返る＝instance 生成不要）。
    # tuple/object 由来の大量の builtin dunder（``__add__``/``count``/``index`` 等）で
    # fixture が埋もれないよう、対象は「public 名」+「明示的に追跡する少数の inherited
    # dunder」（tuple-compat の要である ``__iter__``）に限定する。
    _TRACKED_INHERITED_DUNDERS = ("__iter__",)
    lines.append("")
    lines.append("# fleet public class method / property signatures")
    method_lines = []
    for cls_name in sorted(classes):
        cls = classes[cls_name]
        candidate_names = {
            n for n in dir(cls) if not n.startswith("_")
        } | {d for d in _TRACKED_INHERITED_DUNDERS if hasattr(cls, d)}
        for attr_name in sorted(candidate_names):
            if attr_name == "__init__":
                continue
            attr = getattr(cls, attr_name)
            if callable(attr):
                method_lines.append(f"{cls_name}.{attr_name}{_normalize_sig(attr)}")
            elif hasattr(attr, "__get__"):
                # property / NamedTuple の _tuplegetter 等の data descriptor。
                # 型名を明示して drift（descriptor 種別の変化）も検知できるようにする。
                method_lines.append(f"{cls_name}.{attr_name} ({type(attr).__name__})")
            else:
                method_lines.append(f"{cls_name}.{attr_name} = {attr!r}")
    for line in sorted(method_lines):
        lines.append(line)

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
