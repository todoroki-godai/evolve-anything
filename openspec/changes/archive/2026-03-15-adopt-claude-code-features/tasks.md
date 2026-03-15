## 1. Skill Frontmatter Modernization

- [ ] 1.1 evolve/SKILL.md に `context: fork` を追加。AskUserQuestion 呼び出しをファイル出力+最終メッセージ提案に置換。詳細結果は `<DATA_DIR>/evolve-report.json` に出力する指示を追加
- [ ] 1.2 audit/SKILL.md に `context: fork` を追加。レポートは `<DATA_DIR>/audit-report.json` に出力する指示を追加。AskUserQuestion 不使用を確認
- [ ] 1.3 discover/SKILL.md に `context: fork` を追加。候補リストは `<DATA_DIR>/discover-report.json` に出力する指示を追加。AskUserQuestion 不使用を確認
- [ ] 1.4 evolve/SKILL.md のテンプレート参照を `${CLAUDE_SKILL_DIR}/templates/` に置換
- [ ] 1.5 全 SKILL.md を走査し、プラグインルートへの参照が `${CLAUDE_PLUGIN_ROOT}` を使用していることを確認
- [ ] 1.6 discover/SKILL.md にサブエージェント起動時の `model: haiku` 指定ガイダンスを追記
- [ ] 1.7 evolve/SKILL.md に PostToolUse skill hook（Bash matcher → regression_gate.py --quick-check）を追加

## 2. Hook Lifecycle Optimization

- [ ] 2.1 hooks.json に PostCompact エントリを追加（save_state.py を共有）
- [ ] 2.2 save_state.py に `hook_type` フィールド追加。PostCompact 時は `post_compact_checkpoint` キーに保存し、PreCompact の `checkpoint` キーを上書きしないよう実装
- [ ] 2.3 restore_state.py のチェックポイント読み込みで `checkpoint` キーを優先、`post_compact_checkpoint` にフォールバックするよう実装
- [ ] 2.4 restore_state.py にセッション内重複実行ガード（環境変数 or ファイルフラグ）を追加

## 3. Regression Gate Quick-Check Mode

- [ ] 3.1 scripts/lib/regression_gate.py に `quick_check()` 関数を追加（`check_gates()` とは独立）
- [ ] 3.2 `quick_check()` の入力: stdin から PostToolUse イベント JSON を受け取り、`tool_name` が "Bash" の場合のみ `tool_input.command` から対象 `.py` ファイルを正規表現で推定
- [ ] 3.3 `quick_check()` の処理: 対象 `.py` ファイルに `py_compile.compile()` で構文チェック。"Bash" 以外のツールはスキップ（exit 0）
- [ ] 3.4 `quick_check()` の出力: exit code 0/1 + stderr に `{"passed": bool, "errors": [{"file": str, "error": str}]}`
- [ ] 3.5 `--quick-check` CLI エントリポイントを追加（`if __name__ == "__main__"` ブロック）
- [ ] 3.6 regression_gate.py のテストに quick_check のテストケースを追加（正常, 構文エラー, 非Bashツール）

## 4. Auto-Memory Coordination

- [ ] 4.1 reflect_utils.py に `find_auto_memory_dir()` 関数を追加（`~/.claude/projects/<encoded>/memory/` を探索）
- [ ] 4.2 reflect_utils.py に `check_auto_memory_duplicate(text, auto_memory_dir, threshold=0.6)` を追加。各ファイルを `split_memory_sections()` でセクション分割し、セクション単位の最大 Jaccard スコアで判定
- [ ] 4.3 reflect の memory ルーティングフロー内に auto-memory 重複チェックを組み込み
- [ ] 4.4 auto-memory ディレクトリ不在時のフォールバック（チェックスキップ）テストを追加
- [ ] 4.5 CLAUDE.md に auto-memory との棲み分けガイドを追記

## 5. Worktree Safe Optimization

- [ ] 5.1 genetic-prompt-optimizer/SKILL.md の patch-apply-test サイクルを Agent tool（`isolation: "worktree"`）経由に変更。サブエージェント内で `optimize.py --apply-patch` → `pytest` → 結果ファイル出力の指示を追記
- [ ] 5.2 optimize.py に `--apply-patch` モードを追加（worktree 内でのパッチ適用専用）
- [ ] 5.3 rl-loop-orchestrator/SKILL.md にも同様の worktree isolation 経由の指示を追記

## 6. Effort Level Routing

- [ ] 6.1 evolve/SKILL.md の Diagnose フェーズ指示に「簡潔に集計」ガイダンスを追加
- [ ] 6.2 evolve/SKILL.md の Self-Evolution フェーズ指示に「慎重に分析」ガイダンスを追加
- [ ] 6.3 導入 2 週間後に telemetry_query.py でフェーズ別トークン使用量を比較する測定タスクを実施。有意な差がなければ effort 指示を削除

## 7. Memory Staleness Enhancement

- [ ] 7.1 scripts/lib/layer_diagnose.py に `MEMORY_STALE_DAYS = 90` 定数と `check_memory_staleness(path)` 関数を追加（frontmatter `last_modified` 優先、mtime フォールバック）
- [ ] 7.2 `check_memory_staleness()` に git 操作直後検出ロジックを追加（ディレクトリ内全ファイルの mtime 標準偏差 < 60秒ならスキップ）
- [ ] 7.3 layer_diagnose.py の stale_memory 検出に `check_memory_staleness()` を統合
- [ ] 7.4 layer_diagnose のテストに staleness テストケースを追加（frontmatter 優先, mtime フォールバック, git 操作直後スキップ）

## 8. Testing & Verification

- [ ] 8.1 context:fork 対応した evolve/audit/discover の手動テスト（正常実行・結果ファイル出力確認・AskUserQuestion 不使用確認）
- [ ] 8.2 `python3 -m pytest scripts/tests/ -v` で既存テストの pass を確認
- [ ] 8.3 `python3 -m pytest hooks/ -v` でフック関連テストの pass を確認
- [ ] 8.4 plugin.json の version bump 不要を確認（feat → minor、リリース時に bump）
