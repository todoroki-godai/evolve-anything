## Why

Prune フェーズの `detect_zero_invocations()` は呼び出し回数ゼロのスキルをアーカイブ候補として検出するが、「参照型スキル」（デザインシステムガイド、評価仕様、設定ガイド等）は `/skill-name` で直接呼び出されることが稀であり、誤検出される。参照型スキルはコードベースとの整合性（ドリフト）で陳腐化を評価すべき。(ref: todoroki-godai/evolve-anything#1)

## What Changes

- SKILL.md の frontmatter に `type: reference` タグを追加可能にする
- `detect_zero_invocations()` で `type: reference` スキルを除外する
- 参照型スキル専用の陳腐化検出関数 `detect_reference_drift()` を新設し、スキル内容と現在のコードベース（CLAUDE.md、rules、実装）との乖離度でアーカイブ候補を判定する
- `run_prune()` の結果に `reference_drift_candidates` カテゴリを追加
- `suggest_recommendation()` に参照型スキル向けのロジックを追加

## Capabilities

### New Capabilities
- `reference-skill-classification`: SKILL.md frontmatter の `type: reference` タグによるスキル分類と、zero invocation 検出からの除外
- `reference-drift-detection`: 参照型スキルの内容とコードベースの乖離度を評価し、陳腐化候補を検出する

### Modified Capabilities

## Impact

- `skills/prune/scripts/prune.py`: `detect_zero_invocations()`, `suggest_recommendation()`, `run_prune()` の変更
- `scripts/lib/frontmatter.py`: frontmatter パーサーが `type` フィールドを返すよう対応（既存の `parse_frontmatter()` で対応済みの可能性あり）
- `docs/evolve/prune.md`: 参照型スキルの判断基準を追記
