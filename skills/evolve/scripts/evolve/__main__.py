"""`python3 -m evolve` のエントリポイント（#531 パッケージ化）。

旧 `python3 evolve.py` 直叩きの代替。`bin/evolve`（PATH ラッパー）が主経路で、
これは PATH に evolve が無い特殊環境向けのフォールバック。sys.path は呼び出し側で
`scripts/lib` と `skills/evolve/scripts` を通すこと（__init__.py 先頭でも PLUGIN_ROOT 経由で補う）。
"""
from evolve import main

if __name__ == "__main__":
    main()
