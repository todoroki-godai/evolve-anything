## ADDED Requirements

### Requirement: Two-stage promotion gate
pitfall 記録時に Candidate → New の2段階昇格ゲートを適用する（SHALL）。初回エラーは `Status: Candidate` で仮記録し、同一根本原因が2回目に出現した場合にのみ `Status: New` に昇格する。

#### Scenario: First occurrence creates Candidate
- **WHEN** 新しいエラーが初めて発生した
- **THEN** pitfalls.md に `Status: Candidate` で記録される。Pre-flight Check の対象にはならない

#### Scenario: Second occurrence promotes to New
- **WHEN** Candidate の根本原因と同一（Jaccard 類似度 ≥ 0.5）のエラーが再発した
- **THEN** `Status: New` に昇格し、`Last-seen` が更新される

#### Scenario: User correction bypasses gate
- **WHEN** ユーザーが直接訂正した（ユーザー訂正トリガー）
- **THEN** 品質ゲートをスキップし、即座に `Status: Active` で記録される

### Requirement: Root cause similarity matching
Candidate の根本原因と新エラーの同一性を Jaccard 類似度で判定する（SHALL）。閾値は 0.5。

#### Scenario: Similar root cause matched
- **WHEN** Candidate に `Root-cause: action — CDK deploy パラメータ不足` があり、新エラーの根本原因が `action — CDK deploy オプション漏れ` である
- **THEN** Jaccard 類似度 ≥ 0.5 と判定され、Candidate が New に昇格する

#### Scenario: Different root cause creates new Candidate
- **WHEN** 既存 Candidate の根本原因が `action — CDK deploy パラメータ不足` で、新エラーが `tool_use — S3 バケット名の誤り` である
- **THEN** Jaccard 類似度 < 0.5 のため、新しい Candidate が別途作成される

### Requirement: Three-tier context management
pitfalls.md を3層構造で管理する（SHALL）。Pre-flight Check は Hot 層のみ読み込む。

| 層 | 対象 | Pre-flight 読込 | トークン予算 |
|----|------|-----------------|-------------|
| Hot | Active + Pre-flight対応=Yes（Top 5件、Last-seen 降順） | Yes | ~500 tokens |
| Warm | New + 残りの Active | エラー発生時のみ | ~1000 tokens |
| Cold | Candidate + Graduated | 明示的参照時のみ | 制限なし |

#### Scenario: Pre-flight reads only Hot tier
- **WHEN** スキルの Pre-flight Check が実行される
- **THEN** Active かつ Pre-flight対応=Yes の上位5件のみが読み込まれる

#### Scenario: Error triggers Warm tier loading
- **WHEN** スキル実行中にエラーが発生した
- **THEN** New ステータスと残りの Active pitfalls が追加で参照される

#### Scenario: Hot tier budget enforcement
- **WHEN** Active + Pre-flight対応=Yes の pitfall が6件以上ある
- **THEN** Last-seen が最も古い項目が Warm 層に移動し、Hot 層は5件を維持する

### Requirement: Pitfall lifecycle state machine
pitfall のステータス遷移は以下の状態機械に従う（SHALL）:

```
Candidate → New → Active → Graduated → Pruned
    ↑                ↑
    └─ 初回エラー     └─ ユーザー訂正（ゲートスキップ）
```

#### Scenario: Candidate to New promotion
- **WHEN** Candidate の根本原因と同一のエラーが再発した
- **THEN** Status が New に変わり、Last-seen が更新される

#### Scenario: New to Active promotion
- **WHEN** New の pitfall が再度トリガーされた、またはユーザーが Active 化を承認した
- **THEN** Status が Active に変わり、Pre-flight対応 フィールドの設定を求められる

#### Scenario: Active to Graduated transition
- **WHEN** Active の pitfall がワークフローに統合された（SKILL.md の手順に組込み済み）
- **THEN** Status が Graduated に変わり、Graduated セクションに移動する

#### Scenario: Direct to Active via user correction
- **WHEN** ユーザーが訂正を行った
- **THEN** Candidate/New をスキップし、即座に Active で記録される

### Requirement: Corrupted pitfalls.md handling
pitfalls.md が破損またはパース不能な場合、安全にフォールバックする（SHALL）。

#### Scenario: Malformed pitfalls.md
- **WHEN** pitfalls.md のマークダウン構造が壊れており、セクション（Active/Candidate/Graduated）をパースできない
- **THEN** 既存ファイルをバックアップ（`pitfalls.md.bak`）し、空テンプレートで再作成する。ユーザーに「pitfalls.md が破損していたため再作成しました。バックアップ: pitfalls.md.bak」と通知する

#### Scenario: Empty pitfalls.md
- **WHEN** pitfalls.md が存在するが内容が空である
- **THEN** 空テンプレートで再初期化する
