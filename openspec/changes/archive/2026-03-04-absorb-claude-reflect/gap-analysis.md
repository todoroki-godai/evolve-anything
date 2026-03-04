# absorb-claude-reflect: Gap Analysis

claude-reflect v3.0.1 の全機能と本 change 設計の突き合わせ結果。

## カバレッジ一覧

| claude-reflect 機能 | 設計でカバー | 対応箇所 | 備考 |
|---------------------|:----------:|----------|------|
| **Hooks** | | | |
| UserPromptSubmit (capture_learning.py) | OK | D8 correction_detect.py | 統合 |
| PreCompact (check_learnings.py) | OK | D8 save_state.py 拡張 | corrections.jsonl バックアップ追加 |
| PostToolUse (post_commit_reminder.py) | Skip | Non-Goals | 通知のみ、低価値 |
| SessionStart (session_start_reminder.py) | Skip | Non-Goals | 通知のみ、低価値 |
| **パターン検出** | | | |
| EXPLICIT_PATTERNS (1: remember:) | OK | D1 マージ | |
| POSITIVE_PATTERNS (3: perfect, great-approach, keep-doing) | OK | D1 マージ | |
| GUARDRAIL_PATTERNS (8) | OK | D1 マージ | |
| CORRECTION_PATTERNS (8: no, don't, stop, that's-wrong, actually, I-meant, I-told-you, use-X-not-Y) | OK | D1 マージ | |
| FALSE_POSITIVE_PATTERNS (7) | OK | Tasks 1.2 | |
| 信頼度計算（長さ調整、強弱フラグ） | OK | Tasks 1.4 calculate_confidence() | |
| should_include_message() | OK | Tasks 1.3 | |
| 複数パターンマッチ (patterns フィールド) | OK | D2 matched_patterns | 信頼度計算に使用 |
| **データスキーマ** | | | |
| learnings-queue.json → corrections.jsonl | OK | D2, D9 | マイグレーション対応 |
| project フィールド | OK | D2 project_path | |
| patterns フィールド | OK | D2 matched_patterns | |
| type フィールド | OK | D2 sentiment に統合 | |
| source フィールド | OK | D2 source ("hook" \| "backfill") | |
| **コマンド** | | | |
| /reflect (メイン) | OK | D3, Tasks 4.x | |
| /reflect --dry-run | OK | D3, Tasks 4.5 | |
| /reflect --scan-history | OK | D7 backfill --corrections で代替 | |
| /reflect --targets | Skip | — | 低優先度 |
| /reflect --review | Skip | — | --view で代替 |
| /reflect --dedupe | Skip | Non-Goals (将来 audit) | |
| /reflect --organize | Skip | Non-Goals (将来 audit) | |
| /reflect --include-tool-errors | Deferred | D11 (将来拡張) | 別スキルまたは evolve フェーズに分離 |
| /reflect --model MODEL | OK | D5 | --semantic 必須 |
| /view-queue | OK | D3 --view オプション | |
| /skip-reflect | OK | D3 --skip-all オプション | |
| /reflect-skills | Merged | discover --session-scan | 独立スキル廃止、discover に統合 |
| /reflect-skills --days N | Skip | — | discover 側の設計で対応 |
| /reflect-skills --project PATH | Skip | — | evolve はプロジェクト内で動作 |
| /reflect-skills --all-projects | Skip | — | 初回不要 |
| /reflect-skills --dry-run | Skip | — | discover --dry-run で代替 |
| **ルーティング** | | | |
| Guardrail → rules/guardrails.md | OK | D4 層1 | |
| Model → global CLAUDE.md | OK | D4 層2 | |
| always/never/prefer → global | OK | D4 層3 | |
| Path-scoped rule match | OK | D4 層4 | |
| Subdirectory match | OK | D4 層5 | |
| Low confidence → auto-memory | OK | D4 層6 | トピック分類 + 昇格ロジック |
| CLAUDE.local.md (個人用) | OK | D4 層7 | ユーザー選択時のみ |
| Skill files | Skip | — | Minor, 初回不要 |
| AGENTS.md | Skip | — | Minor, 初回不要 |
| Auto-memory トピック分類 | OK | D4 _AUTO_MEMORY_TOPICS | 6トピック + general |
| **セマンティック検証** | | | |
| semantic_analyze() | OK | D5 (デフォルト有効、バッチ送信) | --skip-semantic で無効化 |
| validate_queue_items() | OK | D5 validate_corrections() | |
| detect_contradictions() | Skip | Non-Goals (--dedupe) | |
| validate_tool_errors() | Deferred | D11 (将来拡張) | |
| **抽出スクリプト** | | | |
| extract_session_learnings.py | OK | backfill parse_transcript | |
| extract_tool_rejections.py | OK | D7, Tasks 2.2a | backfill に統合 |
| extract_tool_errors.py | Deferred | D11 (将来拡張) | |
| compare_detection.py | Skip | — | 診断ツール、省略 |

## 判断結果サマリ

| 判断 | 件数 | 内容 |
|------|------|------|
| OK (カバー済み) | 31 | 設計に含まれている |
| Merged (統合) | 1 | reflect-skills → discover --session-scan |
| Deferred (将来拡張) | 3 | ツールエラー関連 → 別スキル/evolve フェーズ |
| Skip (意図的省略) | 12 | Non-Goals or 低優先度 |

## Refine で追加・修正した全項目

### Round 1 (P0/P1)
1. **P0-1 修正**: `detect_correction()` 戻り値型を `(correction_type, confidence)` に修正（matched_text は誤り）
2. **P0-2**: `project_path` フィールド追加
3. **P0-3**: `source: "hook"` テストアサーション修正タスク追加
4. **P0-4**: discover.py 2箇所の明示
5. **P1-1〜P1-8**: パイプライン順序、出力スキーマ、decay_days 明確化等

### Round 2 (ファクトチェック)
6. **PreCompact backup**: save_state.py 拡張（「既にカバー」は誤り）
7. **matched_patterns**: 複数パターンマッチフィールド追加
8. **--view / --skip-all**: /view-queue, /skip-reflect を /reflect オプションに統合
9. **ツール拒否抽出**: backfill にツール拒否からの correction 抽出追加
10. **--dry-run**: /reflect に追加
11. **8層メモリ階層**: CLAUDE.local.md + auto-memory (トピック分類) 追加
12. **パターン数修正**: 「英語 17パターン」→「英語 12パターン」

### Round 3 (エージェント評価反映)
13. **--apply-all**: 高信頼度 corrections の一括適用オプション追加
14. **セマンティック検証デフォルト無効**: --semantic で明示有効化に変更（課金の透明性）
15. **reflect-skills 廃止**: discover --session-scan に統合（機能重複の解消）
16. **auto-memory 昇格ロジック**: 再出現ブースト (2回+) / 経年昇格 (14日+) / 手動昇格
17. **ツールエラー分離**: D11 を将来拡張に変更（/reflect の責務から分離）

### Round 4 (セマンティック検証再設計)
18. **セマンティック検証デフォルト有効**: `--semantic` → `--skip-semantic` に反転（デフォルト有効化）
19. **バッチ送信**: 1件ずつ → 全 pending corrections を1回の `claude -p` でまとめて検証（レイテンシ N回→1回）

### Round 5 (エージェント再評価反映)
20. **corrections.jsonl クリーンアップ**: D12 新設。prune.py で `applied`/`skipped` の `decay_days` 超過レコードを定期削除
21. **バッチサイズ上限**: 1回あたり最大 20件。超過時は複数バッチ分割
22. **JSON パース失敗フォールバック**: `claude -p` レスポンスのパース失敗時は regex フォールバック
23. **--apply-all 低 confidence 挙動明確化**: 閾値未満は「対話レビューに進む」（スキップではない）
24. **evolve Reflect Step 閾値**: pending >= 5 or 前回から 7日超で提案。それ以外は Report に件数表示のみ
25. **対話レビュー skip-remaining**: 3件目以降に「残り全部 skip」選択肢を追加
26. **project_path null ハンドリング**: D13 新設。null は global-looking 扱い
27. **gap-analysis L58 矛盾修正**: semantic_analyze の記述をデフォルト有効に更新
