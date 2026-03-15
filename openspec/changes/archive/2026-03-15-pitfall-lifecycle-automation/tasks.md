## 1. 定数・設定の追加

- [x] 1.1 `skill_evolve.py` に新定数を追加: `INTEGRATION_JACCARD_THRESHOLD=0.3`, `GRADUATED_TTL_DAYS=30`, `STALE_ESCALATION_MONTHS=3`, `PITFALL_MAX_LINES=100`, `ERROR_FREQUENCY_THRESHOLD=3`
- [x] 1.2 `pitfall_manager.py` の import に新定数を追加

## 2. Corrections/エラーログからの自動検出

- [x] 2.1 `pitfall_manager.py` に `extract_pitfall_candidates()` スケルトン + corrections parse
- [x] 2.2 root-cause キーワード抽出ロジック（「—」分割 → 後半単語分割 → ストップワード除外）
- [x] 2.3 既存 Candidate との Jaccard 重複排除（≥ ROOT_CAUSE_JACCARD_THRESHOLD で Occurrence-count += 1）
- [x] 2.4 errors.jsonl 頻出パターン検出（ERROR_FREQUENCY_THRESHOLD 回以上）
- [x] 2.5 unit test: corrections parse, 重複排除, スキルなしレコードのスキップ, errors
- [x] 2.6 失敗系テスト: malformed correction（スキップ継続）, missing errors.jsonl（corrections のみ実行）, empty last_skill

## 3. 統合済み判定（卒業強化）

- [x] 3.1 `pitfall_manager.py` に `detect_integration(pitfall, skill_dir)` を追加（SKILL.md frontmatter 除外 + セクション単位 Jaccard）
- [x] 3.2 References 突合: pitfalls.md 除外、最初の閾値超マッチを `integration_target` に記録
- [x] 3.3 `pitfall_hygiene()` に `detect_integration` を統合し、`graduation_proposals` フィールドを返却値に追加
- [x] 3.4 テスト: SKILL.md 統合済み判定、References マッチ、未統合 pitfall の除外

## 4. TTL ベースアーカイブ

- [x] 4.1 `pitfall_manager.py` に `detect_archive_candidates(sections)` を追加。Graduated TTL（GRADUATED_TTL_DAYS）+ Active stale エスカレーション（9ヶ月）を判定
- [x] 4.2 `pitfall_manager.py` に `execute_archive(pitfalls_path, titles)` を追加。指定タイトルの pitfall を削除
- [x] 4.3 `pitfall_hygiene()` に `archive_candidates` フィールドを返却値に追加
- [x] 4.4 テスト: TTL 超過/未超過の判定、stale エスカレーション、削除実行

## 5. 行数ガード

- [x] 5.1 `pitfall_hygiene()` に行数チェックを追加。PITFALL_MAX_LINES 超過時に Cold 層の古い順で削除候補を生成
- [x] 5.2 Cold 層不足時の警告メッセージ出力
- [x] 5.3 返却値に `line_count` フィールドを追加
- [x] 5.4 テスト: 100行超過時の削除候補生成、100行以下で発火しない、Cold層不足の警告

## 6. Pre-flight スクリプトテンプレート

- [x] 6.1 `skills/evolve/templates/preflight/` に `action.sh`, `tool_use.sh`, `output.sh`, `generic.sh` テンプレートを作成（TODO プレースホルダ + if/exit 構造）
- [x] 6.2 `pitfall_manager.py` に `suggest_preflight_script(pitfall, templates_dir)` を追加。Root-cause カテゴリからテンプレートパスを解決
- [x] 6.3 `pitfall_hygiene()` に `codegen_proposals` フィールドを返却値に追加
- [x] 6.4 テスト: カテゴリ別テンプレート解決、不明カテゴリの generic フォールバック

## 7. discover/evolve 統合

- [x] 7.1 discover の `run_discover()` に `extract_pitfall_candidates` を統合し、結果に `pitfall_candidates` フィールドを追加
- [x] 7.2 evolve の Housekeeping ステージで拡張された `pitfall_hygiene()` 結果（graduation_proposals, archive_candidates, codegen_proposals）をレポートに反映
- [x] 7.3 統合テスト: discover → pitfall_candidates 出力、evolve → 拡張 hygiene レポート

## 8. 既存テスト更新・最終検証

- [x] 8.1 `scripts/tests/test_pitfall_manager.py` の既存テストが新フィールド追加後も pass することを確認
- [x] 8.2 全テスト実行: `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`

## 9. End-to-end regression test

- [x] 9.1 corrections → Candidate → hygiene graduation_proposals フロー検証
- [x] 9.2 graduation 実行後 archive_candidates から除外の確認
- [x] 9.3 line count guard で Active pitfall が誤削除されないことの確認
- [x] 9.4 preflight codegen_proposals 全カテゴリ解決の確認
- [x] 9.5 全テスト pass（`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v`）
