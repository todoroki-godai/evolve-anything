---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Write, Edit, Glob, Grep, Skill]
model: opus
runs: 3
---
このディレクトリは notes-api の作業コピーです。まず `src/calc.py` を次の内容で作成してください。

```python
def apply_discount(price, rate):
    # P1: rate は 0.1 のような割合で渡される
    return price - rate


def total(items):
    # P2: 空リストのとき 0 を返したい
    s = 0
    for i in items[1:]:
        s += i
    return s
```

P1・P2 とも実バグだね。修正して
