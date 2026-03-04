## Why

rl-anything と claude-reflect の2つのプラグインが同じ UserPromptSubmit hook で修正パターンを検出しており、データが分断されている。CJK 修正（rl-anything）は /reflect に届かず、英語/Guardrail 修正（claude-reflect）は discover に届かない。毎プロンプトで Python が2本走るパフォーマンス問題もある。claude-reflect の機能を rl-anything に吸収し、修正検出から CLAUDE.md 反映までを1つのパイプラインに統合する。

## What Changes

- **パターン統合**: claude-reflect の英語17パターン + Guardrail 8パターンを `hooks/common.py` の CORRECTION_PATTERNS にマージ。CJK + 英語 + Guardrail の統一パターンセット化
- **corrections.jsonl 拡張**: confidence/decay_days/routing_hint/guardrail フィールドを追加し、learnings-queue.json の役割を吸収
- **`/rl-anything:reflect` スキル新設**: corrections.jsonl を入力に、6層メモリ階層ルーティング + セマンティック検証 + 対話レビュー → CLAUDE.md/rules/skills 書込
- **`/rl-anything:reflect-skills` スキル新設**: セッション履歴からスキル候補を発見（discover と統合）
- **evolve パイプラインに Reflect Step 追加**: 未処理 corrections が N件以上あれば reflect 実行を提案
- **backfill 拡張**: 過去セッションからの correction 遡及抽出時に英語/Guardrail パターンも使用
- **hooks 統合**: correction_detect.py で全パターンを処理。claude-reflect の UserPromptSubmit hook が不要に
- **claude-reflect アンインストール**: 全機能移行完了後に `claude plugin uninstall claude-reflect` で卒業

## Capabilities

### New Capabilities
- `reflect`: corrections.jsonl から学習を抽出し、セマンティック検証 + 6層メモリ階層ルーティングで CLAUDE.md/rules/skills/auto-memory に反映する対話的レビュースキル
- `reflect-skills`: セッション履歴パターンから新規スキル候補を発見し提案するスキル（discover との統合版）
- `unified-correction-patterns`: CJK + 英語 + Guardrail の統一修正パターンセットと corrections.jsonl の拡張スキーマ

### Modified Capabilities
- (なし — evolve-enrich-reorganize による Phase 拡張後のパイプラインに Reflect Step を追加するのみ)

## Impact

- **変更対象ファイル**: `hooks/common.py`, `hooks/correction_detect.py`, `skills/evolve/scripts/evolve.py`, `skills/evolve/SKILL.md`, `skills/backfill/scripts/backfill.py`
- **新規ファイル**: `skills/reflect/SKILL.md`, `skills/reflect/scripts/reflect.py`, `skills/reflect-skills/SKILL.md`, `skills/reflect-skills/scripts/reflect_skills.py`
- **依存**: evolve-enrich-reorganize change の完了後に実装（パイプライン Phase 順序に依存）
- **外部依存の削除**: claude-reflect プラグイン（完了後にアンインストール）
- **データ移行**: `~/.claude/learnings-queue.json` → `corrections.jsonl` への既存データ変換（ワンタイム）
