## ADDED Requirements

### Requirement: Auto-memory duplicate detection
reflect が correction を memory にルーティングする前に、Claude 組み込み auto-memory ディレクトリの既存ファイルを走査し、重複を検出する（MUST）。比較は correction テキスト全体 vs auto-memory 各ファイルのセクション単位（`split_memory_sections()` を再利用）で行い、セクション単位の最大 Jaccard スコアを採用する（MUST）。

#### Scenario: duplicate detected in auto-memory
- **WHEN** reflect が correction を memory に書き込もうとする
- **AND** auto-memory ディレクトリ内のファイルをセクション分割し、いずれかのセクションと correction の Jaccard 類似度が 0.6 以上
- **THEN** reflect は書き込みをスキップし、「auto-memory でカバー済み: <ファイル名>」とログ出力する

#### Scenario: no duplicate found
- **WHEN** reflect が correction を memory に書き込もうとする
- **AND** auto-memory ディレクトリの全セクションとの最大 Jaccard が 0.6 未満
- **THEN** reflect は通常通り memory に書き込む

#### Scenario: auto-memory directory not found
- **WHEN** auto-memory ディレクトリが存在しない場合
- **THEN** 重複チェックをスキップし、通常通り処理を続行する（SHALL）

### Requirement: CLAUDE.md coordination guide
CLAUDE.md に auto-memory との棲み分けガイドを記載しなければならない（MUST）。

#### Scenario: guide content
- **WHEN** ユーザーが CLAUDE.md を参照する
- **THEN** auto-memory と rl-anything memory の役割分担が明記されている（auto-memory: 一般的なユーザー学習、rl-anything reflect: correction ベースの構造化ルーティング）
