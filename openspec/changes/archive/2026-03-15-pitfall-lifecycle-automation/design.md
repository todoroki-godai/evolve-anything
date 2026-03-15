## Context

`pitfall_manager.py` は既に Candidate→New→Active→Graduated の状態機械、品質ゲート、3層コンテキスト管理、回避回数ベース卒業判定を実装している。しかし issue #30 が指摘する以下の自動化が欠けている:

1. corrections/エラーログからの pitfall パターン自動抽出（現在は手動記録のみ）
2. SKILL.md/references/ への統合済み判定による自動卒業（現在は回避回数のみ）
3. Graduated 項目の自動削除（現在は蓄積される一方）
4. Pre-flight 対応 pitfall のスクリプト化提案

## Goals / Non-Goals

**Goals:**
- corrections.jsonl・エラーログから pitfall パターンを自動検出し Candidate として追加
- SKILL.md 本文との突合による統合済み自動判定で Graduated→削除を提案
- TTL（Last-seen から N ヶ月経過）ベースの Graduated 項目自動削除
- pitfalls.md の行数を 100 行以下に維持するガード
- Pre-flight 対応 pitfall に対する検証スクリプトのテンプレート提案

**Non-Goals:**
- Pre-flight スクリプトの完全自動生成（テンプレート提案まで）
- 既存の品質ゲート（Candidate→New 2段階昇格）ロジックの変更
- pitfall_manager.py 以外のモジュールへの大規模リファクタ

## Decisions

### D1: 自動検出のデータソース
corrections.jsonl の `correction_type` + `message` を分析して pitfall パターンを抽出する。エラーログ（errors.jsonl）も補助ソースとして使用。

**理由**: corrections は「ユーザーが修正した」＝再発防止すべきパターンを最も的確に捉えている。errors は頻出パターンの補強に使う。
**代替案**: agent-browser のエラーログも候補だが、ドメイン固有すぎるため初期スコープ外とする。

### D2: 統合済み判定のアルゴリズム
pitfall の Root-cause キーワードを SKILL.md 本文と Jaccard 突合し、閾値（0.3）以上で「統合済み候補」としてフラグする。最終的な卒業→削除はユーザー確認必須。

**理由**: SKILL.md に直接記述されていればその pitfall は role を果たし終えている。閾値を低めに設定し false negative を減らす。
**代替案**: LLM 判定も可能だが、コスト対効果を考えると Jaccard で十分。

#### D2.1: Root-cause キーワード抽出
Root-cause 文字列を「—」（em dash）で分割し、後半部分を単語分割する。ストップワード（助詞・冠詞等）を除外したトークン集合を突合用キーワードとする。

#### D2.2: SKILL.md 突合方式
SKILL.md の YAML frontmatter を除外し、セクション（`##` 見出し）単位でテキストを分割する。各セクションのトークン集合と Root-cause キーワードの Jaccard 係数を計算し、いずれかのセクションが ≥ INTEGRATION_JACCARD_THRESHOLD（0.3）であれば統合済みと判定する。

#### D2.3: References 複数ファイル対応
`references/` 配下のファイルを走査する際、`pitfalls.md` 自体は除外する。各ファイルについてセクション単位で Jaccard 計算を行い、最初に閾値超マッチしたファイルを `integration_target` に記録する。

#### D2.4: TF-IDF cosine による精査（代替案）
`similarity.py` の `compute_pairwise_similarity()`（TF-IDF cosine）を精査ステップに活用可能。初期は Jaccard のみで運用し、false positive が多い場合に Jaccard → TF-IDF cosine の2段階判定に移行する。移行判断基準: graduation_proposals の reject 率が 30% を超えた場合。

### D3: TTL と削除ポリシー
Graduated 項目は `Graduated-date` から 30 日後に自動削除候補としてレポートする。Active/New 項目は `Last-seen` から 6 ヶ月（既存 STALE_KNOWLEDGE_MONTHS）で stale 警告を出し、さらに 3 ヶ月（計 9 ヶ月）未更新で削除候補とする。

**理由**: Graduated は統合済みのため短い TTL で十分。Active の stale は既存ロジックを拡張するだけ。

### D4: 行数ガードの実装場所
`pitfall_hygiene()` の既存フローに行数チェックを追加。100 行超過時は Cold 層（Graduated + Candidate の古い順）を自動削除候補として提案する。

**理由**: 既存の `pitfall_hygiene` が定期実行されるため、そこに統合するのが自然。

### D5: Pre-flight スクリプトテンプレート
pitfall の Root-cause カテゴリ（action/tool_use/output 等）に応じたスクリプトテンプレートを `skills/evolve/templates/` に配置。`pitfall_hygiene()` が Pre-flight 対応=Yes の pitfall に対してテンプレートパスを提案する。

**理由**: 完全自動生成は品質保証が難しいため、テンプレート提案にとどめる。

### D6: 品質ゲートの二重適用を回避
auto-detection で corrections → Candidate 作成時に既存 Candidate と Jaccard 突合（≥ ROOT_CAUSE_JACCARD_THRESHOLD）。重複時は新規 Candidate を作成せず Occurrence-count += 1。count >= CANDIDATE_PROMOTION_COUNT（2）で New に自動昇格。`record_pitfall()` の既存ゲートを通るため、別途の品質ゲートは不要。

**理由**: auto-detection と既存の品質ゲートが同じ pitfall に対して独立に動作すると、重複 Candidate が発生する。既存の `record_pitfall()` フローに統合することで一元管理する。

## Constants

| 定数名 | 値 | 配置先 | 理由 |
|--------|-----|--------|------|
| INTEGRATION_JACCARD_THRESHOLD | 0.3 | skill_evolve.py | false negative 低減のため低め設定 |
| GRADUATED_TTL_DAYS | 30 | skill_evolve.py | 統合済みのため短 TTL |
| STALE_ESCALATION_MONTHS | 3 | skill_evolve.py | 計9ヶ月で削除候補 |
| PITFALL_MAX_LINES | 100 | skill_evolve.py | トークン効率 |
| ERROR_FREQUENCY_THRESHOLD | 3 | skill_evolve.py | errors 最低出現回数 |

## Data Contracts

### corrections.jsonl
```json
{correction_type: "stop"|"iya"|..., last_skill: string|null, message: string, timestamp: ISO8601, ...}
```

### errors.jsonl
```json
{skill_name: string|null, error_message: string, timestamp: ISO8601, ...}
```

## Risks / Trade-offs

- [corrections からの自動抽出精度] → Jaccard 閾値と CANDIDATE_PROMOTION_COUNT による2段階ゲートで低品質エントリを排除
- [SKILL.md 突合の false positive] → 統合済み判定はフラグのみで自動削除しない。ユーザー確認必須
- [TTL 削除で必要な pitfall を失う] → 削除は提案のみ。dry-run レポートで確認後に実行
