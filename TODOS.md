# TODOS

## Testing

### P0

(なし)

---

## rl-fleet

### P2

**`resolve_auto_memory_dir` の特殊文字ケーステスト追加（Phase 3 直前）**

**Priority:** P2

fleet.py の `resolve_auto_memory_dir(pj_path) -> Path` ヘルパは、PJ 絶対パスを `~/.claude/projects/<slug>/memory/` に逆引きする。`~/.claude/projects/` の命名規則は `-` 区切り絶対パスだが、PJ パスに日本語・space・記号が含まれる場合の slug 化ロジックは gstack / CC 本体依存。特殊文字ケースが未検証のまま Phase 3 rollback で snapshot / restore に失敗すると、auto-memory の書き戻し事故につながる。

**対応**: Phase 3 実装着手時に以下のテストを追加:
- `/Users/foo bar/project` (space)
- `/Users/foo/日本語パス` (multibyte)
- `/Users/foo/.hidden-dir/sub`
- シンボリックリンク経由の PJ パス

**背景**: plan-eng-review（2026-04-22, main branch, design 20260422-140954）で senior-engineer agent 相談時に検出。Phase 1/2 では `resolve_auto_memory_dir` 自体が導入されないため、Phase 3 ブランチ起票時に着手で十分。

**依存**: Phase 3 実装開始、`feat/rl-fleet-phase3` ブランチで fleet.py に `resolve_auto_memory_dir` が具体化してから

---

## philosophy-review

### P2

**全 JSONL 書き込みに fcntl.flock を追加**

**Priority:** P2

`append_jsonl` (rl_common/persistence.py) の全 JSONL 書き込みに `fcntl.flock` が未適用。
現在の concurrent writer:
- `errors.jsonl`: 3本（observe.py / stop_failure.py / permission_denied.py）
- `sessions.jsonl`: 2本（session_summary.py / instructions_loaded.py）
- `corrections.jsonl`: 1本（correction_detect.py）—元の TODOS 記載

O_APPEND で短い記録は実用上問題ないが、バッファが溢れると行混入で JSONL 破損リスク。
auto_trigger でフック同時発火が増えると顕在化する可能性あり。

**対応**: `fcntl.flock(f, LOCK_EX)` を `append_jsonl` に追加（`fix/append-jsonl-flock` ブランチで対応中）。

---

## Python source warn 超 5件の分割計画

**Priority:** P3

以下のファイルが warn 閾値 (500行) を超えており、800行 hard 到達で fleet パターン分割必須:

| ファイル | 行数 | 優先度 |
|---------|-----|-------|
| scripts/lib/agent_quality.py | 531 | 高（既に warn 超） |
| scripts/reflect_utils.py | 534 | 高（scripts/ ルート配置の不整合も解消予定） |
| scripts/lib/workflow_checkpoint.py | 462 | 中 |
| scripts/lib/skill_triage.py | 458 | 中 |
| scripts/lib/layer_diagnose.py | 433 | 低 |
| scripts/lib/audit/orchestrator.py | 420 | 低 |

分割手順: fleet/audit Phase 2 パターン（snapshot test → 機能塊切り出し → re-export → squash PR）

---

## Completed

**pytest モジュール名衝突の修正** — 解決済み (2026-04-06, chore/release-v1.25.0)

`--import-mode=importlib` (pytest.ini) + `skills/implement/scripts/tests/conftest.py` で
`telemetry` / `test_backfill` の衝突を解消。1563 tests pass。
