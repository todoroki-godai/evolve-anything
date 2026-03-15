## Why

skills の `references/pitfalls.md` が Graduated 項目の蓄積・記述肥大化・手動卒業判定漏れにより肥大化し、トークン効率を悪化させている。現在の `pitfall_manager.py` は Candidate→New→Active の品質ゲートと回避回数ベースの卒業提案を持つが、**卒業後の自動削除**、**corrections/エラーログからの自動抽出**、**TTL ベースのアーカイブ**、**Pre-flight スクリプト化提案** は未実装。Related: #30

## What Changes

- corrections・エラーログから pitfall パターンを自動検出し Candidate として追加する機能（実装: pitfall-auto-detection）
- SKILL.md/references/ への統合済み pitfall を検出し Graduated → 削除を自動提案する卒業判定強化（実装: pitfall-graduation-enforcement）
- Pre-flight 対応 pitfall に対する検証スクリプト自動生成提案（実装: pitfall-preflight-codegen）
- Last-seen が N ヶ月前の項目を削除候補としてレポートする TTL ベースアーカイブ（実装: pitfall-ttl-archive）
- pitfalls.md を常に 100 行以下に維持する行数ガード（実装: pitfall-ttl-archive）

## Capabilities

### New Capabilities
- `pitfall-auto-detection`: corrections・エラーログから pitfall パターンを自動抽出し Candidate として追加
- `pitfall-graduation-enforcement`: SKILL.md/references/ 統合済み判定 + Graduated 項目の自動削除提案
- `pitfall-ttl-archive`: Last-seen ベースの TTL 自動アーカイブ + 行数ガード
- `pitfall-preflight-codegen`: Pre-flight 対応 pitfall から検証スクリプトの自動生成提案

### Modified Capabilities
- `pitfall-hygiene`: 卒業判定ロジックの強化（統合済み検出 + TTL + 行数ガード統合）
- `pitfall-quality-gate`: Candidate 追加ソースに corrections/エラーログを追加

## Impact

- `scripts/lib/pitfall_manager.py`: 卒業判定・TTL・行数ガードの追加
- `skills/evolve/`: Housekeeping ステージで自動検出・卒業・TTL を統合
- `hooks/`: corrections から pitfall パターン抽出のデータフロー追加
- discover: pitfall 自動検出の統合
