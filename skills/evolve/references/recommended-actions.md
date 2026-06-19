# 推奨アクションのサブ項目判定（Step 10.1〜10.6）

Step 10 本体の MUST（必ず出力・判定カード3段階・カスタムスキル0件のまとめ方）は SKILL.md 側に残してある。
ここは各サブ項目（10.1〜10.6）の判定ロジックと表示テンプレ。**いずれも「必ず表示」が原則**（沈黙禁止）。

## 10.1: Reflect 推奨

discover 結果の `reflect_data_count` の値を確認し、**必ず**以下のいずれかを表示する。
**数値比較の前に「欠落（None）または `< 0`」を先に判定する**（discover 全クラッシュ時はキー自体が欠落しうるため `None < 0` 二次クラッシュを避ける・#32）:
- `reflect_data_count is None or reflect_data_count < 0` → 「Reflect: discover 失敗のため reflect 件数 不明」（degraded sentinel `-1` or 欠落・#526-3 / #32）
- `reflect_data_count >= 1` → 「⚠ 未処理の修正フィードバックが {N} 件あります。`/evolve-anything:reflect` で反映すると evolve-skill の精度が向上します」
- `reflect_data_count == 0` → 「Reflect: 未処理なし」

## 10.2: ツール使用改善

discover 結果の `installed_artifacts` と `tool_usage_patterns` を参照し、対策済み/未対策に応じて表示を切り替える。
閾値は `tool_usage_analyzer.py` のモジュール定数（`BUILTIN_THRESHOLD`, `SLEEP_THRESHOLD`, `BASH_RATIO_THRESHOLD`）を参照。

**全対策済みかつ検出ゼロ**: `installed_artifacts` の全 `recommendation_id` 付きエントリが `mitigation_metrics.mitigated=True` かつ `recent_count=0` → 「ツール使用: 全対策済み — 検出なし」と1行表示

**対策済み（検出あり）**: `mitigation_metrics.mitigated=True` かつ `recent_count > 0` → 各項目で「対策済み (hook: {name}, rule: {name}) — 直近 {N} 件検出」形式で表示。件数ベースの提案は表示しない

**未対策**: 対応する推奨の対策が未導入 → 従来通り件数と改善提案を表示:
- **Built-in 代替**: `builtin_replaceable` の合計件数 ≥ `BUILTIN_THRESHOLD` (10件) → 上位パターンと件数を表示し「プロジェクトルールまたは hook で Bash の grep/cat/find を検出・警告する仕組みの導入」を提案
- **sleep パターン**: `repeating_patterns` に `sleep` を含むエントリの合計 ≥ `SLEEP_THRESHOLD` (20件) → 「`run_in_background` + 完了通知待ちへの移行」を提案
- **Bash 割合**: `bash_calls / total_tool_calls` ≥ `BASH_RATIO_THRESHOLD` (40%) → 「Bash割合: {X}% (目標: ≤40%) — 未達」と表示。閾値未満の場合は「Bash割合: {X}% (目標: ≤40%) — 達成」と表示

全て閾値以下かつ未対策なら「ツール使用: 問題なし」と表示

**トレンド表示**: evolve-state.json に前回の `tool_usage_snapshot` がある場合、各指標に前回比トレンドを併記する:
- 件数指標: 「Built-in 代替: 15件 ↓ 5件減少 (-25%)」
- ratio 指標: 「Bash 割合: 45.4% → 38.2% (↓7.2pp)」
- 前回データなし（初回実行時）: トレンド表示なし（実績値のみ表示）

`evolve.py` の `compute_trend()` を使用してトレンドデータを算出する。

## 10.3: 自己進化ステータス

`skill_evolve` と `pitfall_hygiene` の結果から**必ず**以下を表示する:
- 自己進化済みスキル数
- 各スキルの pitfall 統計（Active/New/Candidate/Graduated 件数）
- 卒業候補/剪定推奨があればフラグ
- 根本原因カテゴリの横断分析結果

自己進化済みスキルが0の場合は「自己進化: 対象スキルなし」と表示。

## 10.4: Workflow Checkpoint Gaps

discover 結果の `workflow_checkpoint_gaps` を確認し、以下のいずれかを表示する:
- ギャップあり → テーブル形式で表示:
  ```
  | Skill | Category | Evidence | Confidence |
  |-------|----------|----------|------------|
  | verify | infra_deploy | 3 | 0.75 |
  ```
- ギャップなし → 「Workflow Checkpoints: ギャップなし」

## 10.5: Process Stall Patterns

discover 結果の `stall_recovery_patterns` を確認し、以下のいずれかを表示する:
- パターンあり → テーブル形式で表示:
  ```
  | Command | Sessions | Recovery | Confidence |
  |---------|----------|----------|------------|
  | cdk deploy | 3 | kill | 0.80 |
  ```
- パターンなし → 「Process Stall Patterns: 検出なし」

## 10.6: Remediation サマリ

remediation 結果から**必ず**以下を判定カードに反映する:
- `auto_fixable` ≥ 1 → 🔴 要対応「/evolve-anything:evolve（非 dry-run）— 自動修正可能 {N}件」
- `manual_required` ≥ 1 → 🔴 要対応「手動対応 {N}件」（issue type の概要リスト付き）
- `proposable_custom` ≥ 1 → 🔴 要対応「提案あり {N}件（次回 evolve で確認）」
- 上記すべて 0 → 「✅ 問題なし」に含める
- `proposable_global` のみ ≥ 1 → 🟡 情報「global スキル proposable {M}件（参考値）」
