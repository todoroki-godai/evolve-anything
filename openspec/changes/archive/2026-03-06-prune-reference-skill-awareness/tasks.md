## 1. Reference Skill Classification

- [x] 1.1 `prune.py` に `is_reference_skill(skill_path: Path) -> bool` を追加（frontmatter → キャッシュ → LLM 推定の優先順位で判定）
- [x] 1.2 `evolve-state.json` の `skill_type_cache` 読み書きユーティリティを追加（mtime ベースの無効化対応。既存の `load_*_threshold()` パターンに準拠）
- [x] 1.3 `detect_zero_invocations()` 内で `is_reference_skill()` チェックを追加し、参照型スキルをスキップ
- [x] 1.4 `suggest_recommendation()` に参照型スキル向けロジックを追加（`keep推奨` / ドリフト時は `要確認`）

## 2. Reference Drift Detection

- [x] 2.1 `load_drift_threshold()` を追加（`evolve-state.json` の `reference_drift_threshold`、デフォルト 0.5。既存 `load_decay_threshold()` と同パターン）
- [x] 2.2 `prune.py` に `detect_reference_drift(artifacts, project_dir)` を新設（参照型スキルの内容と現在のコードベースをサブエージェントで突合し、乖離度を評価。サブエージェント失敗時はそのスキルを候補に含めずエラーをログ出力）
- [x] 2.3 `run_prune()` に `reference_drift_candidates` を追加

## 3. Tests

- [x] 3.1 `is_reference_skill()` のユニットテスト（reference / action / 未設定 / LLM 推定失敗時 / キャッシュ無効化）
- [x] 3.2 `detect_zero_invocations()` の参照型スキル除外テスト
- [x] 3.3 `detect_reference_drift()` のユニットテスト（整合 / 乖離 / コンテキスト収集 / サブエージェント失敗時）
- [x] 3.4 `suggest_recommendation()` の参照型スキル向けテスト
- [x] 3.5 `load_drift_threshold()` のユニットテスト（設定あり / なし / 不正値）

## 4. Documentation & Cleanup

- [x] 4.1 `docs/evolve/prune.md` に参照型スキルの判断基準を追記
- [x] 4.2 audit レポートに「ゼロ呼び出しだが reference 未設定」の警告を追加
- [ ] 4.3 GitHub Issue todoroki-godai/evolve-anything#1 を close する（コミット後に実行）
