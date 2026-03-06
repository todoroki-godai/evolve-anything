## Why

`skills/audit/scripts/audit.py` の `_extract_paths_outside_codeblocks()` が、MEMORY ファイル内のスラッシュ区切りの説明的表現（例: `usage/errors`, `discover/audit`）をファイルパスとして誤検出する。これらは「usage と errors のレコード」や「discover と audit の機能」を表す自然言語の略記であり、実際のファイルパスではない。誤検出は Remediation フェーズに伝播し、`auto_fixable` な stale reference として分類され、有用な MEMORY 行が削除される可能性がある。

## What Changes

- `_extract_paths_outside_codeblocks()` に既知プレフィックスフィルタを追加し、拡張子なしの2セグメント相対パス（例: `usage/errors`, `discover/audit`）を既知プロジェクトディレクトリプレフィックス（`skills`, `scripts`, `hooks`, `.claude`, `openspec`, `docs`）で検証する。プレフィックスにマッチしないものは説明的略記として除外する
- `_extract_paths_outside_codeblocks()` の専用ユニットテストを追加（真陽性・偽陽性の両方をカバー）
- 既存の正しい検出（例: `skills/nonexistent/SKILL.md`, `scripts/lib/agent_classifier.py`）に影響がないことを保証

## Capabilities

### New Capabilities
- `path-extraction-filtering`: パス抽出関数のヒューリスティック偽陽性フィルタ（専用ユニットテスト付き）

### Modified Capabilities

## Impact

- **コード**: `skills/audit/scripts/audit.py`（`_extract_paths_outside_codeblocks()` 関数、366-407行目付近）
- **下流の利用者**: `skills/evolve/scripts/remediation.py`（`_extract_paths_outside_codeblocks` をインポート・呼び出し）— 精度向上の恩恵を自動的に受ける
- **テスト**: 新規テストファイル `skills/audit/scripts/tests/test_path_extraction.py`
- **リスク**: 低 — 既存の正規表現マッチの後に適用される追加フィルタであり、コアの抽出ロジックを変更しない
