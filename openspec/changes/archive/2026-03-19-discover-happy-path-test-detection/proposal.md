Related: #33

## Why

オーケストレーション・パイプラインコード（複数ステップを順次実行する関数）において、異常系・早期リターンのテストのみで正常系E2Eテストが欠けている場合、ステップ間のデータ受け渡しバグが検出できない。実際に Lambda 4層パイプラインで Layer 2→3 間が空実装のままテスト全PASSし本番デプロイされたインシデントが発生した（#33）。verification_catalog に「ハッピーパステスト欠落検出」を追加し、discover/evolve 経由で自動提案する。

## What Changes

- verification_catalog に `happy-path-test-verification` エントリを追加
- パイプライン/オーケストレーションコードの検出関数 `detect_happy_path_test_gap()` を実装
  - ソースコードから「複数ステップを順次呼び出すパターン」を検出
  - 対応するテストファイルで「全ステップを通る正常系テスト」の有無を判定
- RECOMMENDED_ARTIFACTS に `test-happy-path-first` エントリを追加（ルール未導入PJへの提案）
- ルールテンプレート `_HAPPY_PATH_RULE_TEMPLATE` を追加

## Capabilities

### New Capabilities

- `happy-path-test-detection`: パイプライン/オーケストレーションコードに対するハッピーパステスト欠落を検出し、ルール提案する機能

### Modified Capabilities

- `verification-catalog`: 新しい検出パターン（happy-path-test-verification）をカタログに追加

## Impact

- `scripts/lib/verification_catalog.py`: カタログエントリ + 検出関数追加
- `skills/discover/scripts/discover.py`: RECOMMENDED_ARTIFACTS にエントリ追加
- 既存の discover/evolve/remediation パイプラインはそのまま利用（新エントリが自動統合される）
