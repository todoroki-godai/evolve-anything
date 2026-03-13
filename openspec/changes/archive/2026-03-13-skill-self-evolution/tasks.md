## 1. 適性判定エンジン（skill_evolve.py）

- [x] 1.1 `scripts/lib/skill_evolve.py` を新規作成: 冒頭に全閾値を module constants として定義（`MEDIUM_SUITABILITY_THRESHOLD`, `HIGH_SUITABILITY_THRESHOLD`, `ROOT_CAUSE_JACCARD_THRESHOLD`, `HOT_TIER_MAX_ITEMS`, `ACTIVE_PITFALL_CAP`, `GRADUATION_THRESHOLDS` 等 — design Decision 9 参照）。`skill_evolve_assessment()` の骨格（スキル走査、対象フィルタ、スコアリング結果返却）。`is_self_evolved_skill()` の実装（`references/pitfalls.md` 存在 + SKILL.md に `Failure-triggered Learning` セクション検出で判定）
- [x] 1.2 テレメトリ3軸の実装: `telemetry_query.py` の `query_usage()`, `query_errors()` を使用して実行頻度・失敗多様性を算出。出力評価可能性は `query_usage()` の件数 - `query_errors()` の件数で成功率を推定
- [x] 1.3 LLM 2軸の実装: スキル内容から外部依存度（静的解析）と判断複雑さ（LLM評価）を算出。`skill-evolve-cache.json` にハッシュ付きキャッシュ
- [x] 1.4 閾値分類と アンチパターン検出: 3段階分類（高/中/低）+ 評価時3パターン（Noise Collector/Context Bloat/Band-Aid）の検出
- [x] 1.5 テスト: `scripts/tests/test_skill_evolve.py` — スコアリング、分類、キャッシュ、アンチパターン検出

## 2. 変換提案エンジン

- [x] 2.1 `skills/evolve/templates/self-evolve-sections.md` を新規作成: SKILL.md に挿入する6セクションのテンプレート（Pre-flight/自己更新ルール/Failure-triggered Learning/Lifecycle/成功パターン/根本原因カテゴリ）
- [x] 2.2 `skills/evolve/templates/pitfalls.md` を新規作成: references/pitfalls.md の空テンプレート（Active/Candidate/Graduated セクション、項目テンプレート）
- [x] 2.3 `evolve_skill_proposal()` を `scripts/lib/skill_evolve.py` に実装: テンプレート読込 → LLM でスキル文脈にカスタマイズ → 差分提案生成。テンプレートファイル不在時はエラーで中止。LLM カスタマイズ失敗時はテンプレートをそのまま挿入するフォールバック
- [x] 2.4 テスト: 変換提案の生成、テンプレート挿入の検証、テンプレート不在/LLM失敗のエラーハンドリング

## 3. 品質ゲート（pitfall_manager.py）

- [x] 3.1 `scripts/lib/pitfall_manager.py` を新規作成: Candidate→New 2段階昇格ロジック、`scripts/lib/similarity.py` の `jaccard_coefficient()` / `tokenize()` を再利用した根本原因同一性判定
- [x] 3.2 3層コンテキスト管理: pitfalls.md のセクション構造による Hot/Warm/Cold 層の分離、Hot 層5件上限
- [x] 3.3 状態機械: Candidate→New→Active→Graduated→Pruned の遷移ロジック、ユーザー訂正の即 Active。pitfalls.md 破損時はバックアップ+再作成のフォールバック
- [x] 3.4 テスト: 昇格ロジック、Jaccard 判定、層管理、状態遷移、破損ファイルハンドリング

## 4. Pitfall 剪定（pitfall_hygiene）

> **前提**: Task 1 の完了が必要（`skill_evolve_assessment()` の実行頻度スコアを卒業閾値の動的調整に使用するため）

- [x] 4.1 `pitfall_hygiene()` を `scripts/lib/pitfall_manager.py` に実装: 回避回数ベース卒業判定（頻度別動的閾値）。テレメトリデータ不足時は最小閾値（3回）でフォールバック
- [x] 4.2 Active 上限管理: 10件超で剪定レビュー提案、Stale Knowledge ガード（6ヶ月超警告）
- [x] 4.3 横断分析: 全自己進化済みスキルの pitfalls を走査し、根本原因カテゴリの集中を検出
- [x] 4.4 テスト: 卒業判定、上限管理、横断分析、テレメトリ不足フォールバック

## 5. evolve パイプライン統合

- [x] 5.1 `evolve.py` に Diagnose ステージ統合: Step 3.7 後に `skill_evolve_assessment()` を呼び出し、結果を remediation に渡す
- [x] 5.2a `remediation.py` の `compute_confidence_score()` に `skill_evolve_candidate` branch を追加: 適性高→confidence 0.85, 適性中→confidence 0.60
- [x] 5.2b `remediation.py` の `FIX_DISPATCH` に `skill_evolve_candidate` → `fix_skill_evolve()` を追加。`VERIFY_DISPATCH` に `skill_evolve_candidate` → `verify_skill_evolve()` を追加（検証: references/pitfalls.md 存在 + SKILL.md 自己更新セクション存在）
- [x] 5.3 `evolve.py` に Housekeeping ステージ統合: Step 7 後に `pitfall_hygiene()` を呼び出し
- [x] 5.4 `SKILL.md` の更新:
  - [x] 5.4a Diagnose セクション: 適性判定ステップの追加（`skill_evolve_assessment()` の呼び出しと結果表示）
  - [x] 5.4b Compile セクション: 変換提案ステップの追加（`evolve_skill_proposal()` の実行フローと承認手順）
  - [x] 5.4c Housekeeping セクション: pitfall 剪定ステップの追加（`pitfall_hygiene()` の呼び出しと剪定レビュー手順）
  - [x] 5.4d Report セクション: 自己進化ステータスサマリの表示内容定義
- [x] 5.5 Report に自己進化ステータスサマリを追加: 自己進化済みスキル数、pitfall 統計、横断分析結果

## 6. 検証

- [x] 6.1 統合テスト: evolve.py --dry-run で適性判定→変換提案→剪定の全フローが動作することを確認
- [x] 6.2 既存テストの非破壊確認: `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v` が全パス
