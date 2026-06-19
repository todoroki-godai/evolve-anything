#!/usr/bin/env python3
"""CLI エントリポイント shim — 実装は scripts/lib/discover/ パッケージに移設。
bin/evolve-discover が整備されたら SKILL.md から直接呼ばれなくなる。

shim 自身がファイル名 `discover.py` のため、ナイーブに `import discover` すると
sys.path 先頭に shim 自身のディレクトリが載った場合に自分自身を再帰 import して
RecursionError になる。これを避けるため、パッケージの __init__.py を file location
で明示ロードし "discover" 名で sys.modules に登録する。
"""
import importlib.util
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "lib"
_pkg_init = _lib / "discover" / "__init__.py"

if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

_spec = importlib.util.spec_from_file_location(
    "discover", _pkg_init, submodule_search_locations=[str(_lib / "discover")]
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["discover"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__" and hasattr(_mod, "main"):
    _mod.main()
