## MODIFIED Requirements

### Requirement: 8-tier memory hierarchy routing
corrections は8層メモリ階層の適切な書込先にルーティングされる（MUST）。CLAUDE.local.md（個人用）と auto-memory（低信頼度ステージング）を含む。ルーティング判定時にプロジェクト固有シグナル検出を実施し、`always/never/prefer` キーワードによる global ルーティングよりも優先する（MUST）。

**追加**: memory 書き込み前に Claude 組み込み auto-memory ディレクトリ（`~/.claude/projects/<encoded>/memory/`）を走査し、各ファイルを `split_memory_sections()` でセクション分割した上で correction テキストとの Jaccard 類似度を算出する（MUST）。いずれかのセクションとの最大 Jaccard が 0.6 以上の場合は書き込みをスキップする（MUST）。auto-memory ディレクトリが存在しない場合はチェックをスキップする（SHALL）。

#### Scenario: Guardrail routed to rules
- **WHEN** guardrail タイプの correction をルーティングする
- **THEN** `.claude/rules/guardrails.md` が提案される

#### Scenario: Auto-memory duplicate skip
- **WHEN** reflect が "テスト前にデータ確認すべき" という correction を memory にルーティングしようとする
- **AND** auto-memory に "テストデータの事前確認" というセクションを含むファイルが存在し、最大 Jaccard 0.65 である
- **THEN** 書き込みをスキップし、「auto-memory でカバー済み: <ファイル名>」とログ出力する

#### Scenario: Auto-memory directory missing
- **WHEN** auto-memory ディレクトリ（`~/.claude/projects/<encoded>/memory/`）が存在しない
- **THEN** 重複チェックをスキップし、通常通り memory にルーティングする

#### Scenario: No similar auto-memory entry
- **WHEN** auto-memory の全セクションとの最大 Jaccard が 0.6 未満
- **THEN** 通常通り memory に書き込む
