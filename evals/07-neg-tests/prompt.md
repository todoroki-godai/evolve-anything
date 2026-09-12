---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Write, Edit, Glob, Grep, Skill]
model: opus
runs: 3
---
このディレクトリは notes-api の作業コピーです。まず次の2ファイルを作成してください。

`src/textutil.py`:

```python
def slugify(s):
    return s.lower().replace(" ", "-")


def truncate(s, n):
    return s[:n]
```

`tests/test_textutil.py`:

```python
from src.textutil import slugify, truncate


def test_slugify_strips_symbols():
    assert slugify("Hello, World!") == "hello-world"


def test_truncate_adds_ellipsis():
    assert truncate("abcdefgh", 5) == "abcd…"
```

このテスト失敗群の対応して。
