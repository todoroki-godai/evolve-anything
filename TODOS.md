# TODOS

## Testing

### P0

**pytest モジュール名衝突の修正**

**Priority:** P0
**Status:** Open
**Noticed:** feat/checkpoint-session-isolation (2026-04-02)

`skills/implement/scripts/tests/test_backfill.py` と `skills/implement/scripts/tests/test_telemetry.py` が、
`skills/backfill/scripts/tests/test_backfill.py` および `scripts/rl/tests/test_telemetry.py` と
モジュール名が衝突している。`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` を
実行するとコレクションエラーが発生し、全テストが中断する。

**修正方法**: 各テストディレクトリに `__init__.py` を追加してパッケージ化するか、重複するテストファイルのベース名を変更する。

**Error:**
```
ERROR skills/implement/scripts/tests/test_backfill.py
ERROR scripts/rl/tests/test_telemetry.py
HINT: remove __pycache__ / .pyc files and/or use a unique basename for your test file modules
```

---

## Completed

(なし)
