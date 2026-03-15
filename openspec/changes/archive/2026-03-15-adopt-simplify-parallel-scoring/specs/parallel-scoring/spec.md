## ADDED Requirements

### Requirement: rl-scorer SHALL use 3 parallel sub-agents for scoring
rl-scorer（`agents/rl-scorer.md`）はオーケストレーターとして動作し、3つのサブエージェントを Agent ツールで並列起動して採点する。各サブエージェントは1つの評価軸に特化する。

#### Scenario: Normal scoring with 3 parallel sub-agents
- **WHEN** rl-scorer が対象ファイルの採点を開始する
- **THEN** 以下の3サブエージェントを1つのメッセージで並列起動する:
  - technical-scorer（技術品質: 明確性・完全性・一貫性・エッジケース・テスト可能性）
  - domain-scorer（ドメイン品質: ドメイン推定結果に基づく4項目）
  - structural-scorer（構造品質: フォーマット・長さ・例示・参照・規約準拠）

#### Scenario: Sub-agent prompt includes pre-resolved context
- **WHEN** オーケストレーターがサブエージェントを起動する
- **THEN** CLAUDE.md ドメイン推定と対象ファイルの読み込みはオーケストレーターが1回行い、結果をサブエージェントの prompt に含める

### Requirement: Sub-agents SHALL use tiered model strategy
サブエージェントは評価軸の性質に応じた model を使用する。

#### Scenario: Technical and structural scorers use haiku
- **WHEN** technical-scorer または structural-scorer を Agent ツールで起動する
- **THEN** model パラメータに `haiku` を指定する

#### Scenario: Domain scorer uses sonnet
- **WHEN** domain-scorer を Agent ツールで起動する
- **THEN** model パラメータに `sonnet` を指定する
- **RATIONALE** ドメイン品質評価は主観的判断（没入感・面白さ等）を含み、haiku では精度不足のリスクがある

#### Scenario: Model fallback
- **WHEN** 精度テストで sonnet が不要と判明した場合
- **THEN** domain-scorer の model を haiku に変更するだけで切り替え可能な設計とする

### Requirement: Orchestrator SHALL use haiku model
`agents/rl-scorer.md` の frontmatter `model` は `haiku` とする。

#### Scenario: Orchestrator model
- **WHEN** rl-scorer エージェントが起動する
- **THEN** frontmatter の model は `haiku` で動作する
- **RATIONALE** オーケストレーターはドメイン推定+結果統合のみ。sonnet は不要

### Requirement: Score integration SHALL maintain existing interface
オーケストレーターは3サブエージェントの結果を統合し、既存と同一の JSON フォーマット（0.0-1.0 統合スコア）を出力する。

#### Scenario: Output format unchanged
- **WHEN** 3サブエージェントの採点が完了する
- **THEN** オーケストレーターが重み付き平均（技術40%・ドメイン40%・構造20%）で統合スコアを算出し、従来と同じ JSON 構造で出力する

#### Scenario: Sub-agent failure fallback
- **WHEN** サブエージェントの1つが失敗またはタイムアウトする
- **THEN** 失敗した軸は 0.0 として扱い、残りの軸で統合スコアを算出する。summary に失敗した軸を記載する

### Requirement: Each sub-agent SHALL return axis-specific JSON
各サブエージェントは担当軸のスコアのみを JSON で返す。

#### Scenario: Technical scorer output
- **WHEN** technical-scorer が採点を完了する
- **THEN** `{ "clarity": 0.8, "completeness": 0.7, "consistency": 0.9, "edge_cases": 0.6, "testability": 0.7, "total": 0.76 }` の形式で結果を返す

#### Scenario: Domain scorer output with workflow signals
- **WHEN** domain-scorer が採点を完了し、workflow_stats.json が存在する
- **THEN** ドメイン固有4項目のスコアに加え、ワークフロー効率性の補助シグナル（最大+0.1）を加算した結果を返す
