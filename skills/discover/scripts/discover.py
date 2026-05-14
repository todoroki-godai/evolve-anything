#!/usr/bin/env python3
"""CLI エントリポイント shim — 実装は scripts/lib/discover/ パッケージに移設。
bin/rl-discover が整備されたら SKILL.md から直接呼ばれなくなる。

shim 自身がファイル名 `discover.py` のため、ナイーブに `import discover` すると
自分自身を再帰 import してしまう。`scripts/lib/` を sys.path 先頭に置きつつ
カレント sys.modules に shim 自身が既に登録されている場合は一旦削除して、
パッケージ実体を再ロードする。
"""
import importlib
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "lib"
if str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

# shim 自身が `discover` として sys.modules に居座っているとパッケージ実体が
# 読まれない。一旦取り除いてから importlib で package を読み込み、その上で
# 自分の sys.modules エントリも実体で置き換える。
_self = sys.modules.pop("discover", None)
_mod = importlib.import_module("discover")
sys.modules["discover"] = _mod

if __name__ == "__main__":
    _mod.main()
