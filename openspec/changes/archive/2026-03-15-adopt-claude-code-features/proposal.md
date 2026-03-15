## Why

Claude Code v2.1.x で追加された plugin/skill 向け新機能（`context: fork`、`${CLAUDE_SKILL_DIR}`、agent model 指定、skill hooks、`once: true` hook、auto-memory、worktree isolation 等）が rl-anything の各スキル・フックに未適用のまま残っている。これらを採用することで、コンテキスト効率・コスト最適化・安全性・auto-memory との共存が改善される。

## What Changes

### 即適用（スキル frontmatter 更新）
- evolve / audit / discover スキルに `context: fork` を追加し、大量出力がメインコンテキストを汚染するのを防止
- 全スキルの templates/ 等パス参照を `${CLAUDE_SKILL_DIR}` 変数に置換し、ハードコードパスを排除
- rl-scorer エージェント定義は `model: sonnet`（現状維持）、evolve LLM 評価はモデル指定なし（inherit）、discover パターン検出は `model: haiku` でコスト最適化
- evolve スキルに skill-level hooks（PostToolUse）を追加し、remediation 後のリグレッション検出を自動化

### フック改善
- restore_state（SessionStart hook）にスクリプト内重複実行ガード（環境変数 or ファイルフラグ）を追加して重複実行を防止
- save_state に `PostCompact` フック対応を追加し、compaction 後の状態保存を補完

### auto-memory 協調
- reflect の memory ルーティングロジックに auto-memory 重複検出を追加（Claude 組み込み auto-memory との衝突回避）
- `autoMemoryDirectory` 設定との棲み分けガイドを CLAUDE.md に記載

### 安全性・効率
- optimize スキルで worktree isolation（`isolation: "worktree"`）を活用し、パッチ適用を隔離環境で試行
- evolve の各フェーズで effort level を使い分け（Diagnose=low, Compile=medium, Self-Evolution=high）
- memory ファイルの last-modified timestamp を layer_diagnose の stale_memory 検出に活用

### 長期基盤
- audit を `background: true` エージェントとして定義可能にする検討
- trigger_engine と `/loop` ネイティブコマンドの統合可能性の検討

## Capabilities

### New Capabilities
- `skill-frontmatter-modernization`: context:fork、${CLAUDE_SKILL_DIR}、skill hooks、agent model 指定の一括適用
- `auto-memory-coordination`: reflect と Claude 組み込み auto-memory の協調ロジック
- `hook-lifecycle-optimization`: once:true、PostCompact 対応、SessionStart 重複防止
- `worktree-safe-optimization`: optimize での worktree isolation 活用
- `effort-level-routing`: evolve フェーズ別 effort level 最適化

### Modified Capabilities
- `remediation-engine`: skill hooks 経由のリグレッション検出追加
- `reflect`: auto-memory 重複検出ロジック追加
- `line-limit`: memory last-modified timestamp を stale 検出に活用

## Impact

- **スキルファイル**: 全13スキルの SKILL.md frontmatter 更新
- **フック**: save_state.py、restore_state.py の更新
- **スクリプト**: reflect_utils.py、layer_diagnose.py、remediation.py の更新
- **エージェント定義**: .claude/agents/ 配下の rl-scorer 定義追加/更新
- **設定**: plugin.json、CLAUDE.md の更新
- **依存**: Claude Code v2.1.0+ 必須（context:fork、${CLAUDE_SKILL_DIR} サポート）
