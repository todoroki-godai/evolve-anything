# TODOS

## Testing

### P0

(なし)

---

## philosophy-review

### P2

**corrections.jsonl concurrent write に file lock を追加**

**Priority:** P2

philosophy-review / reflect / discover が同一 corrections.jsonl に同時 append する可能性あり。Python buffered text-mode の `f.write()` は > PIPE_BUF で atomic 保証なし、partial-line 混入で JSONL 破損リスク。

**対応**: `fcntl.flock(f, LOCK_EX)` を追加、または専用 lockfile を介した atomic append。

**背景**: /review の adversarial subagent で検出 (2026-04-15, feat/philosophy-review PR #64)。現状は手動 trigger で単独実行前提なので deferred。自動 trigger を導入する段階で必須化。

---

## Completed

**pytest モジュール名衝突の修正** — 解決済み (2026-04-06, chore/release-v1.25.0)

`--import-mode=importlib` (pytest.ini) + `skills/implement/scripts/tests/conftest.py` で
`telemetry` / `test_backfill` の衝突を解消。1563 tests pass。
