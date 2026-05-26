#!/usr/bin/env python3
"""CLI エントリポイント shim — 実装は scripts/lib/prune/ パッケージに移設。

パッケージ化（旧 `scripts/lib/prune.py` → `scripts/lib/prune/`）後も旧ファイル
パスを spec ロードしていたため FileNotFoundError になっていた。パッケージの
__init__.py を file location で明示ロードし "prune" 名で sys.modules に登録する。
"""
import importlib.util
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "lib"
_pkg_init = _lib / "prune" / "__init__.py"

if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

_spec = importlib.util.spec_from_file_location(
    "prune", _pkg_init, submodule_search_locations=[str(_lib / "prune")]
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["prune"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__" and hasattr(_mod, "main"):
    _mod.main()
