## Context

スキルの自己進化パターン組み込み（Pre-flight / Failure-triggered Learning / pitfalls.md）は `evolve` パイプラインの一部（`skill_evolve.py` → remediation `fix_skill_evolve_candidate`）でのみ利用可能。自己進化パターン組み込みは1回限りの操作であり、パイプライン全体を回すのはオーバーヘッドが大きい。

既存モジュール:
- `scripts/lib/skill_evolve.py` — 適性判定（5軸スコアリング）+ 変換提案（テンプレートカスタマイズ）
- `skills/evolve/templates/self-evolve-sections.md` — SKILL.md に追加するセクションテンプレート
- `skills/evolve/templates/pitfalls.md` — `references/pitfalls.md` のテンプレート

## Goals / Non-Goals

**Goals:**
- 独立コマンド `/rl-anything:evolve-skill <name>` で特定スキルを自己進化対応にする
- rl-loop の `--evolve` フラグで最適化と自己進化対応を一体実行する
- 既存の `skill_evolve.py` API を再利用し、新コード量を最小化する
- 既に自己進化済みのスキルは自動スキップ

**Non-Goals:**
- `skill_evolve.py` 自体の機能変更
- evolve パイプラインからの自己進化判定の削除（両経路を維持）
- pitfall の自動生成（テンプレート配置のみ）

## Decisions

### D1: 独立コマンドを主、rl-loop 統合を副

自己進化パターン組み込みは1回限りの操作。rl-loop のような繰り返しループの中に置くのは操作粒度が合わない。独立コマンドが発見しやすく、ユースケースに直結する。

rl-loop の `--evolve` は「最適化のついでに未対応なら提案する」便利フラグとして維持。

代替案: rl-loop のみに統合 → 1回限りの操作にループを回す必要がありオーバーヘッド大。却下。

### D2: evolve-skill コマンドは SKILL.md ベースのスキル定義

`skills/evolve-skill/SKILL.md` に配置。`skill_evolve.py` の単一スキル向け軽量ラッパー `_assess_single_skill()` を共通ヘルパーとして `skill_evolve.py` に追加し、独立コマンドと rl-loop の両方から呼び出す。

代替案: Python スクリプトとして実装 → SKILL.md の方がプラグインのスキル一覧に自然に載り、発見しやすい。却下。

### D3: 判定 + 適用の2共通関数を skill_evolve.py に追加

`skill_evolve_assessment()` は全カスタムスキルスキャン。以下の2関数を `skill_evolve.py` に新設し、独立コマンド・rl-loop・remediation の3箇所から呼び出す:

1. **`assess_single_skill(skill_name, skill_dir)`** — 1スキルの適性判定結果を返す
2. **`apply_evolve_proposal(proposal)`** — `evolve_skill_proposal()` の返り値を受け取り、SKILL.md セクション追記 + `references/pitfalls.md` 作成を実行する

`apply_evolve_proposal()` は `remediation.py:fix_skill_evolve()` (L1069-1083) の SKILL.md 書き込み + references/pitfalls.md 作成ロジックを抽出・共通化したもの。バックアップ作成（D6）もここに含む。

代替案: 適用ロジックを各呼び出し元に個別実装 → 3箇所で同一ロジックが重複し DRY 違反。却下。

### D4: 自己進化ステップは rl-loop の Step 5.5（最適化後）

テキスト最適化（Step 1-5）完了後に自己進化パターンを組み込む。最適化パッチがテンプレート部分を壊すリスクを回避。

代替案: Step 1 前に組み込み → 最適化パッチが追加セクションを壊す可能性。却下。

### D5: 適性 medium 以上で提案、人間確認必須

適性 low/rejected はスキップ。`--auto` 時のみ自動承認。`--dry-run` 時は判定結果表示のみ。

代替案: 全適性で提案 → low/rejected まで表示するとノイズ。却下。

### D6: SKILL.md 変更前にバックアップ作成

`apply_evolve_proposal()` は SKILL.md 変更前に `.md.pre-evolve-backup` を作成する。`run-loop.py` の `.md.pre-rl-backup` パターンに倣う。

代替案: バックアップなし → 自己進化セクション追記はファイル末尾追記だが、テンプレートカスタマイズ結果が不適切だった場合に復元手段がない。却下。

## 共通化分析

### `fix_skill_evolve()` との関係

`remediation.py:fix_skill_evolve()` (L1069-1095) は evolve パイプライン経由で自己進化パターンを適用する既存関数。本変更で `apply_evolve_proposal()` を `skill_evolve.py` に抽出し、`fix_skill_evolve()` はそのラッパーにリファクタする:

- **Before**: `fix_skill_evolve()` が直接 SKILL.md 書き込み + pitfalls.md 作成
- **After**: `fix_skill_evolve()` → `apply_evolve_proposal(proposal)` を呼び出すのみ

呼び出し元3箇所:
1. `remediation.py:fix_skill_evolve()` — evolve パイプライン経由
2. `skills/evolve-skill/SKILL.md` — 独立コマンド経由
3. `run-loop.py:_try_evolve_skill()` — rl-loop `--evolve` 経由

## Risks / Trade-offs

- [テンプレートカスタマイズの LLM コスト] → `_customize_template()` で claude -p 1回。`--dry-run` 時はスキップ
- [最適化パッチが自己進化セクションを壊す可能性] → D4 で最適化後に追加する設計により回避
- [evolve-skill と evolve パイプラインの重複] → evolve パイプラインは全スキル一括、evolve-skill は単一スキルピンポイント。用途が異なるため共存
- [バックアップファイルの蓄積] → 1スキル1ファイルのため実質無視できるサイズ。必要なら手動削除
