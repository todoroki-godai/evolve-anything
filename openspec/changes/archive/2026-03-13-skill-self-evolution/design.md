## Context

evolve パイプラインはスキル/ルールを「外から」改善するが、スキル自身が実行中に知見を蓄積する仕組みがない。aws-deploy と figma-to-code で手動適用した「自己進化パターン」は実績がある（figma-to-code: 16→44 pitfalls）。これを evolve パイプラインの一機能として統合する。

既存の自己進化パターン（旧 skill-evolve グローバルスキル由来）:
1. Pre-flight Check（実行前に既知 pitfalls 確認）
2. pitfalls.md（知見の構造化蓄積）
3. Failure-triggered Learning（エラー/訂正時の即時記録）
4. Pitfall Lifecycle（New→Active→Graduated→Pruned）
5. Stale Knowledge Guard（6ヶ月超の pitfall は要検証）

研究知見による改善点:
- 根本原因カテゴリ付与（AgentDebug: 症状でなく原因を記録）
- 成功パターンも蓄積（自己生成カリキュラム: 73%→89%改善）
- 品質ゲート: 全部記録は記憶なしより悪い（experience-following）
- 3層コンテキスト管理（MemOS/Letta: Hot/Warm/Cold）
- 回避回数ベース卒業（時間ベースより堅牢）

## Goals / Non-Goals

**Goals:**
- evolve Diagnose ステージでスキルの自己進化適性を自動判定する
- 適性ありスキルに自己進化パターンを組み込む変換提案を生成する
- 品質ゲート（Candidate→New 2段階）で pitfall の質を担保する
- Housekeeping で自己進化済みスキルの pitfall を剪定する

**Non-Goals:**
- hooks やスクリプトによる pitfall の自動記録（スキル実行中の LLM 判断に委ねる）
- pitfall の自動マージや LLM による要約（手動記録の質を重視）
- プラグイン/symlink スキルへの適用
- 既存の aws-deploy/figma-to-code の自己進化パターンの変更（新規変換のみ）

## Decisions

### 1. 適性判定: テレメトリ 3軸 + LLMキャッシュ 2軸

**選択**: 5項目スコアリング（各1-3点、15点満点）。テレメトリで自動算出できる3軸と、スキル構造からLLMで判定する2軸のハイブリッド。

| 項目 | ソース | 再計算タイミング |
|------|--------|------------------|
| 実行頻度 | usage.jsonl | 毎回 |
| 失敗多様性 | errors.jsonl + LLM根本原因分類 | 毎回 |
| 外部依存度 | スキル内容の静的解析 | スキル変更時のみ |
| 判断複雑さ | LLMによるスキル構造評価 | スキル変更時のみ |
| 出力評価可能性 | 成功/失敗比率 (telemetry) | 毎回 |

**代替案**:
- 全項目LLM判定 → コスト高、再現性低。却下
- 全項目テレメトリのみ → 外部依存度・判断複雑さはテレメトリで判定困難。却下
- 旧 skill-evolve の手動5項目 → テレメトリで自動化できる部分は自動化すべき。却下

**分類閾値**:
- 12-15点: 適性高 → 変換を推奨
- 8-11点: 適性中 → ユーザー判断に委ねる
- 5-7点: 適性低 → 変換非推奨、理由提示

**キャッシュ**: LLM判定結果は `~/.claude/rl-anything/skill-evolve-cache.json` に保存。スキルファイルのハッシュと紐づけ、変更時のみ再計算。

### 2. 変換パターン: テンプレートベース挿入

**選択**: `skills/evolve/templates/` にテンプレートを用意し、LLM がスキルの文脈に合わせてカスタマイズして挿入。

挿入するセクション:
1. **Pre-flight Check**: `references/pitfalls.md` を読み Active+Pre-flight対応=Yes の項目を適用
2. **references/pitfalls.md**: 構造化テンプレート（Status/Last-seen/Root-cause/Pre-flight対応）
3. **Failure-triggered Learning テーブル**: エラー/リトライ/ユーザー訂正/再発の4トリガー
4. **Pitfall Lifecycle Management**: 4段階ライフサイクル + 剪定ルール
5. **成功パターン枠**: `## Success Patterns`（1-2件のベストプラクティス記録用）
6. **根本原因カテゴリ**: memory/planning/action/tool_use/context_loss の分類指示

**代替案**:
- 全文LLM生成 → 品質にばらつき。テンプレート+カスタマイズが安定。却下
- 固定テキスト貼り付け → スキルの文脈に合わない。却下

### 3. 品質ゲート: Candidate → New 2段階昇格

**選択**: 初回エラーは `Status: Candidate`（Pre-flight対象外）で仮記録。同一根本原因が2回目に出現で `Status: New` に昇格。ユーザー訂正は即 `Status: Active`（ゲートスキップ）。

```
初回エラー → Candidate（仮記録、Pre-flight 対象外）
2回目同一根本原因 → New（正式 pitfall、Pre-flight 対象外）
Active 化 → ユーザー訂正 or New が再発で昇格（Pre-flight 対象）
Graduated → ワークフローに統合済み（Pre-flight 対象外）
Pruned → N回連続回避で削除候補
```

**根本原因の同一性判定**: pitfall の `root_cause` フィールド（カテゴリ + 短い説明）で Jaccard 類似度 ≥ 0.5 を同一とみなす。`scripts/lib/similarity.py` の `jaccard_coefficient()` / `tokenize()` を再利用する。

**代替案**:
- 即時記録（現行） → 「全部記録は記憶なしより悪い」研究結果に反する。改善
- 3回以上で昇格 → 重要な問題の記録が遅れすぎる。却下

### 4. 3層コンテキスト管理

**選択**: pitfalls.md 内のセクション分離で3層を実現。ファイルを分割しない。

| 層 | 対象 | Pre-flight で読む | トークン予算 |
|----|------|-------------------|-------------|
| Hot | Active + Pre-flight対応=Yes（Top 5件） | Yes | ~500 tokens |
| Warm | New + 残りの Active | エラー時のみ | ~1000 tokens |
| Cold | Candidate + Graduated | 明示的参照時のみ | 制限なし |

**実装**: pitfalls.md のセクション構成で層を表現。Pre-flight Check セクションで「Hot 層のみ読め」と指示。

**代替案**:
- 別ファイル分割（hot.md/warm.md/cold.md） → ファイル数増加、管理コスト高。却下
- データベース管理 → オーバーエンジニアリング。却下

### 5. 卒業判定: 回避回数ベース

**選択**: pitfall が N 回連続でトリガーされずにスキルが実行された場合に卒業候補。N はスキルの実行頻度に応じて動的調整（高頻度: 10回、中: 5回、低: 3回）。

**代替案**:
- 時間ベース（6ヶ月） → 低頻度スキルでは永遠に卒業しない。改善
- 固定回数 → 高頻度スキルでは早すぎ、低頻度では遅すぎ。動的が適切

### 6. evolve パイプラインへの統合位置

**選択**: 既存ステージに自然に統合。独立コマンド不要。

| ステージ | 既存機能 | 追加機能 |
|---------|---------|---------|
| Diagnose (Step 3.7) | collect_issues | + skill_evolve_assessment() |
| Compile (Step 5.5) | remediation | + evolve_skill_proposal() via FIX_DISPATCH |
| Housekeeping (Step 7) | prune | + pitfall_hygiene() |
| Report (Step 10) | 推奨アクション | + 自己進化ステータスサマリ |

### 7. 対象フィルタ

**選択**: `classify_artifact_origin()`（`skills/audit/scripts/audit.py` に定義）で `"custom"` または `"global"` のスキルのみ対象。

除外:
- `"plugin"` → プラグイン管理下、変更すべきでない
- symlink → 外部リポジトリ管理、ローカル変更は消える
- 既に自己進化済み（pitfalls.md + Failure-triggered Learning が存在） → 変換スキップ

### 8. アンチパターン検出

旧 skill-evolve の5パターンを継承:

| パターン | 検出条件 | 対応 |
|---------|---------|------|
| Noise Collector | 失敗多様性スコア=1 | 変換非推奨 |
| Context Bloat | 頻度3 × 判断1 | 変換非推奨 |
| Band-Aid | 既存トラブルシュート10件超 | 設計見直しを推奨 |
| Stale Knowledge | 運用時検出 | 変換後の警告として組込 |
| Phantom Learning | 運用時検出 | 変換後の警告として組込 |

評価時検出（上3つ）が2件以上該当で変換非推奨。

### 9. 定数一覧

`scripts/lib/skill_evolve.py` 冒頭に以下の module constants を定義する:

| 定数名 | 値 | 用途 |
|--------|-----|------|
| `MEDIUM_SUITABILITY_THRESHOLD` | 8 | 適性中の下限スコア |
| `HIGH_SUITABILITY_THRESHOLD` | 12 | 適性高の下限スコア |
| `ROOT_CAUSE_JACCARD_THRESHOLD` | 0.5 | 根本原因の同一性判定閾値 |
| `HOT_TIER_MAX_ITEMS` | 5 | Hot 層の最大 pitfall 件数 |
| `ACTIVE_PITFALL_CAP` | 10 | Active pitfall の上限（超過で剪定レビュー） |
| `GRADUATION_THRESHOLDS` | `{3: 10, 2: 5, 1: 3}` | 実行頻度スコア → 卒業に必要な回避回数 |
| `STALE_KNOWLEDGE_MONTHS` | 6 | Stale Knowledge ガードの月数閾値 |
| `ANTI_PATTERN_REJECTION_COUNT` | 2 | 評価時アンチパターン該当数の拒否閾値 |
| `BAND_AID_THRESHOLD` | 10 | Band-Aid アンチパターンの項目数閾値 |
| `SUCCESS_PATTERN_LIMIT` | 2 | Success Patterns セクションの最大件数 |
| `WARM_TOKEN_BUDGET` | 1000 | Warm 層のトークン予算 |
| `HOT_TOKEN_BUDGET` | 500 | Hot 層のトークン予算 |
| `HIGH_CONFIDENCE` | 0.85 | 適性高の remediation confidence |
| `MEDIUM_CONFIDENCE` | 0.60 | 適性中の remediation confidence |
| `CANDIDATE_PROMOTION_COUNT` | 2 | Candidate → New 昇格に必要な出現回数 |

## Risks / Trade-offs

- **適性スコアの閾値不適切** → 初期閾値8/15で開始、evolve-fitness の accept/reject データで調整可能
- **pitfalls.md の肥大化** → 品質ゲート（2回出現で昇格）+ 3層管理 + Active 10件上限で制御
- **テンプレート挿入でスキルが肥大** → 挿入は ~30行 + references/pitfalls.md。SKILL.md の行数制限に注意
- **既存自己進化スキルとの不整合** → 既に自己進化済みのスキルは変換スキップで衝突回避
- **LLMキャッシュの陳腐化** → スキルファイルハッシュで変更検出、変更時のみ再計算
