## Context

Prune の `detect_zero_invocations()` は usage.jsonl の呼び出し回数でスキルの陳腐化を判定する。しかし「参照型スキル」（デザインシステムガイド、評価仕様等）は `/skill-name` で直接呼び出されず、system prompt への注入やコンテキストとして参照されるため、常にゼロ呼び出しとなり誤検出される。

現在の保護メカニズム:
- `.pin` ファイル: 手動で淘汰保護（ユーザーが個別に設置する必要あり）
- `_KEEP_KEYWORDS`: キーワードベースの推薦ラベル（"daily", "pipeline", "utility"）

## Goals / Non-Goals

**Goals:**
- SKILL.md の frontmatter `type: reference` で参照型スキルを宣言可能にする
- 参照型スキルを `detect_zero_invocations()` から除外する
- 参照型スキル専用のドリフト検出で陳腐化を評価する

**Non-Goals:**
- rules への `type` タグ適用（rules は既に淘汰対象外）

## Decisions

### D1: frontmatter の `type` フィールドで分類

**選択**: SKILL.md の YAML frontmatter に `type: reference` を追加

**代替案**:
- `.reference` マーカーファイル（`.pin` と同様） → frontmatter に統合した方がメタデータとして自然
- description 内のキーワード検出 → 誤検出リスクが高い

**根拠**: `parse_frontmatter()` が既に任意フィールドを辞書で返すため、コード変更なしで読み取り可能。

### D1b: frontmatter 未設定時は LLM でスキルタイプを自動推定

**選択**: `type` フィールドが frontmatter にない場合、スキル内容をサブエージェントに渡して `reference` / `action` を推定する。推定結果はキャッシュ（`evolve-state.json` の `skill_type_cache`）に保存し、毎回の LLM 呼び出しを避ける。

**代替案**:
- 手動タグ付けのみ → 既存スキルに全部手動で付けるのは非現実的。付け忘れで誤検出が続く
- キーワードヒューリスティック → 「ガイド」「仕様」等で分類可能だが精度が低い

**根拠**: ドリフト評価で既に LLM サブエージェントを使うため、タイプ推定も同じ仕組みで対応可能。キャッシュにより2回目以降はコストゼロ。frontmatter に明示的な `type` があればそちらを優先（手動オーバーライド）。

### D2: ドリフト閾値は evolve-state.json で管理

**選択**: `evolve-state.json` の `reference_drift_threshold` キーでドリフト閾値を管理。既存の `load_decay_threshold()` / `load_merge_similarity_threshold()` と同一パターンで `load_drift_threshold()` を実装。

**根拠**: prune.py 内に同パターンの閾値読み込み関数が3つ（`load_merge_similarity_threshold`, `load_interactive_merge_threshold`, `load_decay_threshold`）既に存在。同一パターンを踏襲することで一貫性を保つ。

### D3: ドリフト検出は LLM サブエージェントによるセマンティック評価

**選択**: Claude Code のサブエージェント（Agent tool）でスキル内容と現在のコードベース（CLAUDE.md、rules、関連ファイル）を突合し、内容の乖離度を 0.0〜1.0 で評価。閾値以下をドリフト候補とする。

**代替案**:
- ファイルパス存在チェックのみ → パスが存在しても内容が乖離しているケースを検出できない
- git diff ベースの変更量比較 → 変更量と乖離度は必ずしも相関しない

**根拠**: Claude Code サブスクリプション内で動作するため API コスト追加なし。参照型スキルは通常少数（〜10個程度）であり、各スキルの評価に必要なトークンも軽微。セマンティック評価により「ファイルは存在するが内容が古い」ケースも検出可能。

### D4: `detect_zero_invocations()` 内で除外

**選択**: `detect_zero_invocations()` の既存ループ内で `type: reference` チェックを追加し、該当スキルをスキップ

**根拠**: 既存の `.pin` チェック（`is_pinned()`）と同じ位置に追加するのが自然。

### D5: skill_type_cache の無効化戦略

**選択**: キャッシュエントリにスキルファイルの mtime を記録。`is_reference_skill()` 呼び出し時に現在の mtime と比較し、ファイルが更新されていればキャッシュを無効化して再推定する。frontmatter に `type` が明示されている場合はキャッシュを参照しない（D1 が最優先）。

**根拠**: mtime ベースは実装が単純で、ファイル内容変更時に確実にキャッシュが無効化される。

### D6: サブエージェント失敗時の安全側倒し

**選択**: LLM 推定失敗時は `False`（action 扱い）を返す。ドリフト評価失敗時はそのスキルを候補に含めない。

**根拠**: 偽陰性より誤アーカイブの方がコストが高い（design.md Risks に記載の通り）。失敗時は従来通りの挙動（zero invocation 検出対象）にフォールバックすることで安全性を確保。

## Risks / Trade-offs

- **[Risk] `type: reference` の付け忘れ** → audit レポートに「ゼロ呼び出しだが reference 未設定」の警告を追加して検知を促す
- **[Risk] ドリフト検出の精度** → LLM 評価のため高精度が期待できるが、偽陽性より偽陰性を許容する設計（閾値を保守的に設定）。見逃しは次回で検出可能、誤アーカイブは復元コストが高い
- **[Risk] サブエージェント実行時間** → 参照型スキルが多数ある場合に prune 全体が遅くなる → 並列実行 + スキル数が少ない前提（通常〜10個）で許容範囲
- **[Risk] 自動推定の誤判定** → frontmatter の明示 `type` を最優先にすることで手動オーバーライド可能。キャッシュに推定結果を保存するため、ユーザーが確認・修正する機会もある
