## Why

evolve/prune がスキルの「出自」を区別せずにゼロ呼び出し判定しているため、淘汰判断が不正確。
プラグイン由来スキル（`plugin install` で入ったもの）をアーカイブしても次の `plugin update` で復活し、
カスタムスキル（ユーザーが手書きしたもの）は「description 不足で呼ばれていないだけ」の可能性がある。
さらに、rl-anything プラグイン自身のスキルに frontmatter がなく、Claude が自動起動判定できない。

## What Changes

- `find_artifacts()` にスキルの出自分類（custom / plugin / global）を追加
- `detect_zero_invocations()` でプラグイン由来スキルを淘汰対象外にし、レポートのみ出力
- rl-anything プラグインの全 SKILL.md に YAML frontmatter を追加（description / disable-model-invocation）
- `.claude/commands/opsx/` を削除し SKILL.md に一本化。公式が「commands は skills に統合済み」と明言しており、重複はコンテキストウィンドウの浪費

## Capabilities

### New Capabilities
- `skill-origin-classification`: スキルの出自を custom / plugin / global に分類し、prune の判断基準として使用する機能
- `frontmatter-standardization`: rl-anything プラグインの全 SKILL.md に YAML frontmatter を追加し、発見性を向上させる

## Impact

- `scripts/audit.py` — `find_artifacts()` の戻り値構造に `origin` フィールド追加
- `skills/prune/scripts/prune.py` — `detect_zero_invocations()` にプラグイン除外ロジック追加
- `skills/*/SKILL.md` — 全12スキルに frontmatter 追加
- `.claude/commands/opsx/` — 全5ファイル削除（apply, archive, explore, propose, verify）
- 既存テストへの影響: `find_artifacts()` の戻り値変更によりテスト修正が必要
