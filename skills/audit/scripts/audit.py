#!/usr/bin/env python3
"""CLI エントリポイント shim — 実装は scripts/lib/audit/ パッケージに移設。

importlib.util で scripts/lib/audit/__init__.py を直接ロードし、
sys.modules['audit'] を本物のパッケージで上書きする。
これにより skills/audit/scripts/ を sys.path に入れているテストからの
`from audit import X` が、本物のパッケージから X を取得できる。
"""
import importlib.util
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "lib"
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

_init_py = _lib / "audit" / "__init__.py"
_spec = importlib.util.spec_from_file_location(
    "audit",
    _init_py,
    submodule_search_locations=[str(_lib / "audit")],
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["audit"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    _mod.main()
