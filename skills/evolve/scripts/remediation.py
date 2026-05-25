#!/usr/bin/env python3
"""CLI エントリポイント shim — 実装は scripts/lib/remediation/ パッケージに移設。

sys.path の先頭に skills/evolve/scripts が来ても本ファイル（shim）自身を
再帰ロードしないよう、パッケージの __init__.py を file location で明示ロードし
"remediation" 名で sys.modules に登録する。
"""
import importlib.util
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "lib"
_pkg_init = _lib / "remediation" / "__init__.py"

if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

_spec = importlib.util.spec_from_file_location(
    "remediation", _pkg_init, submodule_search_locations=[str(_lib / "remediation")]
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["remediation"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__" and hasattr(_mod, "main"):
    _mod.main()
