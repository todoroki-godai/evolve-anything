---
name: evolve
effort: high
description: |
  Run the full autonomous evolution pipeline: Observe → Diagnose → Compile → Housekeeping → Report.
  Designed for daily execution to continuously improve skills and rules.
  Trigger: evolve, 自律進化, evolution pipeline, 日次実行, daily run, パイプライン実行
disable-model-invocation: true
---

# /rl-anything:evolve — 全フェーズ統合実行

Observe データ確認 → Diagnose → Compile → Housekeeping → Report の全フェーズをワンコマンドで実行する。日次実行を想定（MUST）。

## Usage

```
/rl-anything:evolve              # 通常実行
/rl-anything:evolve --dry-run    # レポートのみ、変更なし
```

## エフォートレベル対応

現在のエフォートレベル: **${CLAUDE_EFFORT}**

| レベル | 挙動 |
|--------|------|
| low | Step 1 でデータ不足時は即スキップ（確認なし）。LLM 分析はスキルのみ（rules/memory/hooks レイヤーをスキップ） |
| medium | 通常実行（全 Step を実行） |
| high / max | 通常実行 + Discover バリエーション生成数を最大化 |

## 前提

セクション 1-6 のコンポーネント（Observe hooks, テレメトリ, Feedback, Audit, Prune, Discover）が全て利用可能であること。

## 提案詳細プロトコル（全 AskUserQuestion 共通）

evolve が「やりますか？」と尋ねる前に、ユーザーが Yes/No を判断できる材料を提示する共通ルール。
**AskUserQuestion を出す前に per-item で次の3点を必ず提示する（MUST）:**

- **対象**: 具体名（`skill-name` / `path/to/file.py:42` / ルール名）。「N件」だけに丸めない
- **根拠**: 閾値・metric・evidence の**実値**（例: `content_lines=62 < 80`, `confidence=0.90`）
- **変更内容**: before → after か diff の1行要約（例: `effort: (なし) → low`）

per-item 展開は最大 10 件、超過は「他 M 件（全件: <コマンド>）」と誘導する。
**`options` は最大 4 件（MUST NOT）**: 5 件以上は1問にまとめず、方式 A（1件ずつ3択）か方式 B（4件グループ分割）で進む。

→ 背景・方式 A/B の手順・`detail` 活用の詳細は **[references/proposal-protocol.md](references/proposal-protocol.md)**。
このプロトコルは Step 2 / 5.5 / 7 / 7.5 など全提案ポイントに適用する（各 Step で再掲しない）。

## 実行手順

### Step 0.5: 世界観ロード

まず既存の世界観をロードする（LLM 不要）:

```bash
# cd せず対象 PJ の cwd のまま実行する（--claude-md / --slug は cwd の PJ を指す）。
# スクリプト本体はプラグイン同梱なので ${CLAUDE_PLUGIN_ROOT} で絶対参照する（相対 scripts/lib は cwd=対象PJ では存在しない）。
# --slug は --load にも必須。DATA_DIR は全 PJ 共通なので slug でスコープしないと
# 先に evolve した別 PJ の世界観を流用してしまう（cross-project 汚染）。
SLUG="$(basename $(git rev-parse --show-toplevel 2>/dev/null || echo unknown))"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/world_context.py" --load --slug "$SLUG"
```

`--load` が exit 0 で JSON を出した場合はそれを使う（既存世界観・継続）。Claude はこの JSON を読んで
各変数（`environment_name` / `protagonist_title` / `issue_name` / `improvement_name`）を以降のナレーション指示に展開する。

**exit 1（初回＝未生成）の場合のみ**、claude -p を使わずファイルベース2相で生成する（[ADR-037]）。
手順・JSON フォーマット・変数展開の詳細は **[references/world-context.md](references/world-context.md)**（初回のみ読めばよい）。
スクリプトが利用できない場合はナレーション指示をスキップする（evolve の主機能に影響しない）。

---

### Step 1: データ十分性チェック

```bash
rl-usage-log "evolve"
rl-evolve --project-dir "$(pwd)" --dry-run --output /tmp/rl_evolve_out.json
```

⚠️ **`--output` は必須（MUST）**: result JSON はフェーズ全部入りで数十〜数百 KB になる。`--output` を付けると full JSON は `/tmp/rl_evolve_out.json` に書かれ、stdout には `{"output": "...", "phases": [...], "env_tier": ...}` の **1行サマリ**だけが出る（`phases` は実フェーズ名）。

以降このスキルで「evolve.py の出力に含まれる `X` フェーズを確認する」と書かれている箇所は、すべて **`/tmp/rl_evolve_out.json` を Read（必要なら offset/limit で該当フェーズだけ）して参照する**。`rl-evolve` の stdout を `| head` / `| tail` で削ったり Bash の出力をそのまま読もうとしてはならない（MUST NOT）。`indent=2` の巨大 JSON が途中で切れて invalid になり「JSON が不完全 → 全量を保存し直し」のやり直しが多発する（これが本フローを設計した理由）。

- 出力（`/tmp/rl_evolve_out.json` の）`observe` フェーズの `action` で分岐する:
  - `action: "backfill_recommended"`（テレメトリ未取得＝初回導入直後、`telemetry_empty: true`）の場合:
    - 「テレメトリが空。先に /rl-anything:backfill で既存セッション履歴を取り込んでください」と案内する（MUST）
    - evolve を続行せず、backfill を先に実行するよう促す（自動実行はしない）
  - `action: "skip_recommended"`（少量だが観測ありのデータ不足）の場合:
    - 「データ不足のためスキップ推奨」メッセージを表示（MUST）
    - AskUserQuestion で実行/スキップを選択させる

### Step 2: Fitness 関数チェック

evolve.py の出力に含まれる `fitness` フェーズを確認する。

- `has_fitness: false` の場合:
  - **提案詳細プロトコルに従う**: 質問前に判断材料を提示する。CLAUDE.md / rules / skills からドメイン（ゲーム/API/Bot/ドキュメント等）を1行で推定し、「生成すると何が変わるか」（組み込み default 汎用評価 → ドメイン特化の評価軸）と、生成をスキップした場合に使われる組み込み関数名（default）を明示する
  - AskUserQuestion ツールで以下を質問する（MUST — テキスト表示だけで済ませてはならない）:
    - question: 「プロジェクト固有の評価関数が未生成です（推定ドメイン: {domain}）。生成しますか？」
    - options: 「生成する（generate-fitness --ask）」「スキップ（組み込み default で続行）」
  - ユーザーが「生成する」を選んだ場合: `/rl-anything:generate-fitness --ask` を実行してから Step 3 に進む（MUST）
  - ユーザーが「スキップ」を選んだ場合: 組み込み評価関数（default）で続行
- `has_fitness: true` の場合: 利用可能な fitness 関数名を表示して次へ

---

### Step 2.5: 意図確認チェック（Intention Check）

各スキルのパッチ候補に対して `intention_check(candidate, original)` を実行し、意図逸脱を検出する。

- **BLOCK** 検出条件（パッチを適用せず次のスキルへスキップ）:
  - Trigger 行削除率 ≥ 30%
  - `description:` キー消失
  - `disable-model-invocation: true` → `false` への変化
  - `## Usage` セクション完全消失
- **WARN** 検出条件（適用はするが注意喚起）:
  - `effort:` 値の昇降（`low` ↔ `high`）
  - Jaccard 係数 < 0.5（テキスト類似度が低い）

パイプライン完了後サマリに出力する:
- BLOCK: `BLOCKED: {skill} ({reason})`
- WARN: `WARNED: {skill} ({reason})`

---

## Stage 1: Diagnose（パターン検出 + 問題診断）

### Step 3: Discover フェーズ（enrich 統合済み）

パターン検出結果を表示。候補があれば生成を提案。

`tool_usage_patterns` が結果に含まれる場合、以下を追加表示:
- **Built-in 代替可能**: 件数と上位パターン（例: `cat → Read: 12回`）をルール候補として提案
- **繰り返しパターン**: 上位パターンとサブカテゴリをスキル候補として提案
- **Bash 割合**: 全ツール呼び出し数と Bash の割合（例: `Bash: 31.8% (127/400)`）

discover の出力に含まれる enrich 結果（Jaccard 照合）を確認する。
discover.py は Discover のパターン（error/rejection/behavior）を既存スキルと Jaccard 係数で照合し、`matched_skills` と `unmatched_patterns` を出力する（型A パターン: LLM 呼び出しなし）。

- `matched_skills` が存在する場合（最大3件）:
  - 各マッチについて、パターンとスキルの組を表示
  - 各ペアに対して、Claude が改善提案（diff 形式）を生成し、ユーザーに対話的に提示する（MUST）
  - AskUserQuestion で「適用する」「スキップ」を選択させる（MUST）
  - ユーザーが承認した場合のみ、スキルファイルに変更を適用する
  - **採点記録（決定論化済み, #360-A [ADR-041]）**: accept/reject の optimize_history 記録は
    **Step 7.8 の drain が自動で行う**（手で `record_evolve_diff_decision` を叩かない）。run_evolve が
    候補スキルの before_sha を emit 済みで、Step 7.8 が「適用された diff = accept」を決定論で記録する。
    - ここで assistant がやるのは1つだけ: ユーザーが**明示的に却下した**提案があれば、その
      `proposal_id`（`result.evolve_decisions.pending[].id`）と理由を控えておき、Step 7.8 の
      `rejected={id: 理由}` に渡す。適用したものは何もしなくて良い（差分から自動 accept）。
    - 対象は skill diff（`matched_skills`）と skill_evolve の high/medium 適性提案（どちらも SKILL.md
      content を変えるので skill_quality で均質に採点）。構造修正・rule/hook candidate・reorganize/prune・
      remediation fix は target 異種で均質性を壊すため対象外（ADR-041）。
- `unmatched_patterns` がある場合:
  - 「既存スキルに関連なし → Discover の新規候補として処理」と表示

> **一言メモ — Discover / Diagnose 完了後**: 発見パターン数（`unmatched_patterns` + `matched_skills`）に応じた1文を出力する（文言は [references/report-narration.md](references/report-narration.md)）。

### Step 3.5: レイヤー別診断

evolve.py の出力に含まれる `layer_diagnose` フェーズ結果を確認する。
`diagnose_all_layers()` は Rules / Memory / Hooks / CLAUDE.md の4レイヤーを診断し、issue リストを返す。

各レイヤーの結果を表示:
- `rules`: `orphan_rule`（孤立ルール）、`stale_rule`（参照先不在）
- `memory`: `stale_memory`（陳腐化エントリ）、`memory_duplicate`（重複セクション）
- `hooks`: `hooks_unconfigured`（hooks 設定なし）
- `claudemd`: `claudemd_phantom_ref`（幻影参照）、`claudemd_missing_section`（セクション欠落）

issue があれば Compile ステージの remediation で対処する。

### Step 3.6: スキル自己進化適性判定

evolve.py の出力に含まれる `skill_evolve` フェーズ結果を確認する。
`skill_evolve_assessment()` は全カスタムスキルの自己進化適性を5項目（各1-3点、15点満点）でスコアリングする。

[ADR-037] により判断複雑さ（judgment_complexity）軸も含め `compute_llm_scores` は LLM-free
（cache-read + 静的フォールバック）になり、evolve バッチはキャッシュ値で完走する。

- **`result.phases.skill_evolve.batch_guard_trigger` が `null` でない場合**: LLM 評価対象スキルが多すぎる。
  グループ提示 → AskUserQuestion（評価/今回スキップ/永続スキップ）→ `--confirmed-batch` 付き再実行の
  インタラクティブフローを実行してから evolve を再実行する（MUST）。手順・denylist/再実行コードは
  **[references/skill-evolve-assessment.md](references/skill-evolve-assessment.md)**。
  推定トークンは worst-case（`estimated_tokens`）と cache 反映後の実見込み（`estimated_tokens_cache_aware`、
  fresh `cache_fresh_count` 件は ≈0）を**併記**する。`--confirmed-batch` 再実行自体は LLM-free（#377-1）。
- **`null` の場合（通常）**: 以下のサマリを確認する:
  - **already_evolved**: 既に自己進化パターンが組み込まれたスキル数
  - **high_suitability**: 適性高（12-15点）→ Compile で変換を推奨
  - **medium_suitability**: 適性中（8-11点）→ ユーザー判断に委ねる
  - **insufficient_usage**: 使用実績ゼロ（`usage_count==0`）で**保留**（#376）→ 「保留（使用実績待ち）N件」と1行表示し、**変換可能（high/medium）の件数には含めない**。自己進化（pitfalls 蓄積）は実ミスが溜まったスキルに効くので、未使用スキルに空ひな型を量産しない。検証系スキルは usage=0 でも medium 維持（例外）
  - **rejected**: アンチパターン2件以上該当で変換非推奨

判断複雑さ cache を LLM 品質で最新化したい場合のみ、assessment 前にファイルベース2相を回す（任意・cache が新しければ 0 コール。コードは上記 reference）。
適性高/中のスキルは `skill_evolve_candidate` issue として Remediation に注入され、Step 5.5 で変換提案が生成される。

### Step 3.7: Audit 問題検出

evolve.py の出力に含まれる audit の `collect_issues()` 結果を確認し、問題リストを Compile ステージに渡す。
（collect_issues() 内で layer_diagnose も統合されている）

evolve の audit は **`memory_trace=True` / `constitutional_score=True` 既定**で実行される。これにより MemTrace 帰属診断（決定論・LLM ゼロ）と slop_detector を 10% ブレンドした constitutional スコアが「evolve するだけ」で出力に乗る。[ADR-037] により audit 本体は claude -p を呼ばず cache（`constitutional_cache.json` / `principles.json`）を読むだけ。CLAUDE.md/Rules を変えた直後など constitutional cache を最新化したいときは、audit SKILL の **Step 3.5（principles round → constitutional round の2相）**を先に回してから evolve する（インライン採点＝subscription 課金）。cache が新しければ 0 コールで済む。
discover の `tool_usage_rule_candidate` / `tool_usage_hook_candidate`、skill_evolve の `skill_evolve_candidate`、および `verification_rule_candidate`（検証知見カタログ）も issue リストに統合される。

### Step 3.8: Observability（必ず surface する — MUST）

evolve.py 出力の **トップレベル `observability` フィールド**（`unmanaged_pitfalls` / `glossary_drift` 等の key → 行リスト）を、各 key の行を**そのまま必ずサマリに列挙する**。clean（「✓ 評価したが該当なし」）でも省略しない。

理由: これらは `phases.audit.report` の 217KB markdown 中盤にも出ているが、選択読みでは埋もれて surface されない（silence != evaluated の配線漏れが #272 後に再発した実例）。`observability` フィールドは audit↔evolve の契約として構造化済みなので、**markdown 側の該当行を探さず、この構造化フィールドを正準ソースとして出す**。`{"error": ...}` のときはエラーをそのまま表示する。

**Triage SKIP 抑制サマリ（#308、必ず1行 surface する — MUST）**: `phases.skill_triage.skip_suppressed_summary`（例: `SKIP 抑制 2件 ✓`）を**そのまま1行表示する**。0件でも省略しない（silence != evaluated）。これは過去に SKIP と判断したスキル候補のうち、クールダウン内で再発したため個別表示を畳んだ件数。なお `phases.skill_triage.REVIEW`（再発エスカレーション昇格）や `ledger_status == "ttl_expired"`（🔄 強制再評価）の候補は通常どおり個別 surface される — 抑制対象は「前回判断を維持中の SKIP」のみ。

### Step 4: Reorganize フェーズ（split 検出 + 階層統合提案）

evolve.py の出力に含まれる `reorganize` フェーズ結果を確認する。
reorganize.py は TF-IDF + 階層クラスタリングでスキル群を分析し、JSON を出力する。

- `skipped: true` の場合:
  - 理由（`insufficient_skills` / `scipy_not_available`）を表示
  - `scipy_not_available` の場合: 「`pip install scipy scikit-learn` でインストールしてください」と案内
- `skipped: false` の場合:
  - クラスタ一覧を表示（各クラスタのスキル名とキーワード）
  - `split_candidates` があれば「分割候補」として表示し、分割を提案
  - **`hierarchy_candidates` があれば「階層統合提案（低レベル→上位）」として表示する（SkillPyramid, #303）**:
    - 各候補は同一クラスタの低レベル（小型）スキル群を上位スキルへ束ねる提案。
      `member_skills`（束ねる対象）/ `parent_skill_suggestion`（提案する上位スキル名）/
      `member_count` / `centroid_keywords` を 1 件ずつ提示する
    - これは split（肥大化の分割）/ merge（重複の統合）と違い「階層（低→上位）」軸で
      スキル数の肥大化を構造的に抑える。max_skill_count（既定30）に張り付いている時に特に有効
    - 統合は破壊的なので、提案表示に留める（実適用はユーザー判断）。`total_hierarchy_candidates: 0`
      なら「階層統合提案: 該当なし ✓」と1行残す（silence != evaluated）

**split↔archive 相互排他（自動・#301 #302）**: prune フェーズ直後に `reconcile_split_archive()` が走り、prune の archive 候補（zero_invocations / retirement / decay）に一致するスキルを `split_candidates` から除外する（消す対象を分割提案する矛盾を本流で解消、archive 優先）。除外結果は `phases.split_archive_reconcile.suppressed` と `reorganize.split_suppressed_by_archive` に記録される。`suppressed` が非空なら「分割候補から除外（archive 優先）: <skills>」を1行 surface する。

---

## Stage 2: Compile（パッチ生成 + メモリルーティング）

### Step 5: Optimize フェーズ

カスタムスキルの改善は `/rl-anything:evolve-skill <skill>` で実行。
`/rl-anything:optimize` スキルは削除済み（`bin/rl-optimize` は内部 CLI として存続）。

**外部インストールスキルは除外（MUST）。** `classify_artifact_origin()` が `"plugin"` を返すスキル
（プラグイン由来スキル等）は最適化対象外。
ユーザーが自作したスキル（custom / global）のみが対象。

### Step 5.5: Remediation フェーズ

evolve.py の出力に含まれる `remediation` フェーズ結果を確認する。
remediation.py は audit の検出結果を confidence_score / impact_scope ベースで3カテゴリに動的分類する。

- `total_issues == 0` の場合: 「問題なし — Remediation スキップ」と表示
- `dry_run` の場合: 分類サマリのみ表示（auto_fixable: N / proposable: custom N / global M（参考値） / manual_required: N）。判定は `proposable_custom` のみで行う

confidence/scope で3カテゴリに動的分類される。各カテゴリの MUST（出力テンプレ・対応 type 一覧は **[references/remediation.md](references/remediation.md)**）:

- **auto_fixable** (confidence ≥ 0.9, scope in file/project): `generate_auto_fix_summaries` でテキスト出力してから AskUserQuestion「一括修正/個別承認/スキップ」（MUST）。**補足説明は Q&A の前に出す（MUST）**。承認分を `FIX_DISPATCH` で実行 → `verify_fix()`+`check_regression()` の2段検証、regression は `rollback_fix()` で manual 格上げ → `record_outcome()` で記録。
- **proposable** (confidence ≥ 0.5, scope != global): `proposable_custom > 0` のときのみ個別承認フロー（MUST）。提案詳細プロトコルに従い `generate_proposals` を1件ずつ提示してから AskUserQuestion（MUST）。**補足説明は Q&A の前 / options は最大4択（MUST）**。同 type 複数でも件数に丸めない。`proposable_custom == 0 かつ proposable_global > 0` は「global のみ {M}件（参考値）— 対応不要」と1行でスキップ。

#### Step 5.5.1: proposable の line_limit_violation / split_candidate に対する2相品質回復（[ADR-037] Phase 1d-ii）

`fix_line_limit_violation` / `fix_split_candidate` は [ADR-037] で claude -p を全廃し決定論フォールバックで完走する。
承認後に assistant がファイルベース2相（emit→インライン→ingest）で実際の圧縮/分離/分割を行う（MUST）。
対象 issue（line_limit_violation 非rule=圧縮 / rule=分離、split_candidate=分割）の emit/ingest コードは
**[references/remediation.md](references/remediation.md) の Step 5.5.1 節**。`fixed=True` で書込完了、`fixed=False` は手動対応を案内。

**manual_required** (confidence < 0.5, or impact_scope = global):
- 問題の概要、推奨アクション、分類理由を表示のみ

**サマリ**: 「Remediation 完了: N件修正 / M件スキップ / K件ロールバック（要手動対応）」

> **一言メモ — Remediation 完了後**: 修正件数に応じた1文を出力する（文言は [references/report-narration.md](references/report-narration.md)）。

### Step 5.6: /simplify ゲート

Remediation でファイルが変更された場合、Python コードの品質チェックを行う。

**判定条件**:
1. Remediation の `record_outcome()` 結果から `fix_detail.changed_files` を集約する
2. 以下の条件で分岐:
   - `.py` ファイルが1つ以上含まれる → `/simplify` を実行
   - `.md` ファイルのみ → スキップ（「/simplify: Markdown のみ — スキップ」と表示）
   - 変更なし（0件 or dry-run）→ スキップ

**実行手順** (`.py` ファイルあり):
1. `/simplify` を実行する
2. `/simplify` の結果（git diff）をユーザーに提示する
3. AskUserQuestion で「適用」「元に戻す」を選択させる（MUST）
4. 結果をレポートに記録:
   - 適用: 「/simplify: N件の改善を適用」
   - 元に戻す: 「/simplify: 実行済み・変更なし」

**後方互換**: `/simplify` スキルが利用不可の場合（古い Claude Code）はスキップし、「/simplify: スキップ（未対応バージョン）」と表示する

### Step 6: Reflect フェーズ

reflect は独立フェーズではなく discover に統合済み。**`phases.discover.reflect_data_count`**（未処理の修正フィードバック件数）を確認する。前回 reflect 日付は出力に含まれないため、日付ではなく件数で判定する（Step 10.1 も同じ `reflect_data_count` を参照する）。

- `reflect_data_count >= 5` → AskUserQuestion で `/rl-anything:reflect` の実行を提案する（MUST）
  - question: 「未処理の修正フィードバックが {N} 件あります。/reflect を実行しますか？」
  - options: 「実行する」「スキップ」
- `0 < reflect_data_count < 5` → Report に「未処理修正 {N} 件あり」と表示のみ（Step 10.1 のサマリ掲載と整合）
- `reflect_data_count == 0` → スキップ

### Step 6.5: auto-memory キュー drain（2相, [ADR-037] Phase 2）

Stop hook（auto_memory_runner）は corrections を生成前ゲートして PJ スコープキュー
`DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue するだけのゼロ LLM 化済み。
LLM 生成・生成後ゲート（belief_entropy）・memory 書き込みはここで assistant が
ファイルベース2相（emit→インライン→ingest）で消化する。reflect Step 5.5 と同じ書式。

**Phase A（リクエスト生成 — claude -p なし）:** キューを読んで各 prompt を出力する。
空なら「auto-memory キュー: 0 件 ✓」（沈黙≠評価）で本ステップを終了する。

```python
import os, sys
from pathlib import Path
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
import rl_common, auto_memory_broker

slug = rl_common.project_name_from_dir(os.environ.get("CLAUDE_PROJECT_DIR", ""))
records = auto_memory_broker.read_queue(slug, rl_common.DATA_DIR)
if not records:
    print("auto-memory キュー: 0 件 ✓")  # 沈黙≠評価。ここで終了
else:
    emit = auto_memory_broker.emit_memory_requests(records)
    for r in emit["requests"]:
        print(r["id"], "\n", r["prompt"], "\n---")  # Phase B でこの prompt にインライン回答（subscription 課金）
```

**Phase B→C（インライン生成 → 回収 → 反映）:** `requests` が非空なら各 prompt を読み、
memory frontmatter v2 形式のエントリをインラインで生成し（claude -p を呼ばない）、
`responses = {request_id: 生テキスト}` を組んで再 emit（決定論・冪等）して ingest する:

```python
emit = auto_memory_broker.emit_memory_requests(records)  # 同一結果（決定論）
memory_dir = Path.home() / ".claude" / "projects" / slug / "memory"
memory_md_path = memory_dir / "MEMORY.md"  # index は entry .md と同じ memory/ 内（相対リンク成立のため）
summary = auto_memory_broker.ingest_memory_results(
    records, emit["requests"], responses,
    memory_dir, memory_md_path, rl_common.DATA_DIR,
)
print(f"auto-memory: stored={summary['stored']} blocked={summary['blocked']} skipped={summary['skipped']}")
```

- ingest が生成後ゲート（belief_entropy）を内蔵: ソースを落とした要約は書込なしで `belief_blocks.jsonl` に記録（blocked にカウント）
- 空応答（skipped）はキューに残り次回 drain で再試行される。stored/blocked は消化される
- 結果（stored/blocked/skipped）を Report に報告する

---

## Stage 3: Housekeeping（淘汰 + 評価関数改善）

### Step 7: Prune フェーズ（+Merge）

淘汰候補をスキルの出自別に3セクションで表示する（MUST）:

#### Custom Skills（淘汰候補）

カスタムスキルのうちゼロ呼び出しのものをアーカイブ候補として処理する。
**全候補を一括判断してはならない。各スキルを個別に調査・分類してから、1件ずつ承認を求める（MUST）。**
「ゼロ呼び出し」はアーカイブの必要条件ではない。セットアップ・デプロイ等は設計上低頻度が正常であり、SKILL.md を読まずに「オンデマンドスキル」と決めつけてはならない。

各候補について順番に（MUST）: ① SKILL.md を Read で全文 + `git log -- <skill_dir>/` で最終変更を調査 → ② 4種別
（オンデマンド型=keep / 一時目的完了型=archive / 統合済み型=archive / 日常用途未発火型=要確認）に分類 →
③ **Q&A の前に**テキスト出力（スキル名・種別・根拠・推奨）→ ④ AskUserQuestion で個別承認（テキスト出力の後に呼ぶ — MUST）。
承認されたもののみアーカイブ。アーカイブを断った候補には `.pin` 作成による再表示抑制を案内する（MUST）。

→ 調査手順・4種別の判定基準・出力テンプレ・`.pin` 案内文は **[references/prune-merge.md](references/prune-merge.md)**。

#### Plugin Skills（レポートのみ）
プラグイン由来で未使用のスキルを表示。アーカイブはせず「未使用。`claude plugin uninstall` を検討？」と案内のみ。

#### Global Skills（既存ロジック維持）
Usage Registry の cross-PJ 使用状況を確認し、既存の `safe_global_check` で処理。

#### Merge サブステップ

evolve.py の `prune.merge_result` を確認する（`merge_duplicates()` が `duplicate_candidates` から統合候補を JSON 出力。型A・LLM 呼び出しなし。マージ候補検出は prune に一元化済み）。
- `status: "skipped_*"`（pinned / plugin / suppressed / low_similarity）→ スキップ理由を表示
- `status: "proposed"` / `"interactive_candidate"`（後者は `similarity_score` 降順で最大3件）→ Claude が primary/secondary の SKILL.md を読んで統合版を生成・提示し、AskUserQuestion で承認/却下（MUST）。承認は統合版を primary に上書き + secondary を `archive_file()`、却下は `add_merge_suppression()` で次回以降抑制（MUST）。

→ 各 status の詳細手順と `add_merge_suppression` のコードは **[references/prune-merge.md](references/prune-merge.md)**。

> **一言メモ — Prune / Housekeeping 完了後**: 「整理完了。少し軽くなった。」を出力する。

### Step 7.5: Pitfall 剪定

evolve.py の出力に含まれる `pitfall_hygiene` フェーズ結果を確認する。
`pitfall_hygiene()` は自己進化済みスキルの pitfalls を回避回数ベースで卒業判定する。

- **graduation_candidates**: 卒業候補（Avoidance-count が閾値以上）→ AskUserQuestion で卒業確認（MUST）。**提案詳細プロトコルに従う**: どの pitfall が卒業対象か per-item で「スキル名・pitfall タイトル・avoidance count（実値）・最終回避日」を提示する。「N件卒業しますか」だけにしない
- **cap_exceeded**: Active pitfall が10件超のスキル → 剪定レビューを推奨
- **stale_warnings**: 6ヶ月以上更新のない Active pitfall → 検証を推奨
- **cross_skill_analysis**: 根本原因カテゴリの横断集中検出 → 共通ルール化を提案

### Step 7.6: 合理化防止テーブル

evolve.py の出力に含まれる `rationalization_table` フェーズ結果を確認する。
`pitfall_hygiene()` 内で `generate_rationalization_table()` が呼ばれ、corrections のスキップパターンをテレメトリと突合した結果が格納される。

- `rationalization_table` フェーズが存在しない場合: データ不足のためスキップ。「合理化防止テーブル: データ不足 — スキップ」と表示
- `rationalization_table` フェーズが存在する場合:
  - `table` の各エントリをテーブル形式で表示（MUST）:
    ```
    ### 合理化防止テーブル
    | 言い訳 | スキップ後エラー率 | サンプル数 |
    |--------|-------------------|-----------|
    | {excuse} | {outcome_error_rate}% | {sample_count} |
    ```
  - `outcome_error_rate` が `None` の場合は「N/A」と表示
  - `enriched_pitfalls` があれば「既存 pitfall にテレメトリデータをエンリッチ済み: {N}件」と表示

### Step 7.7: 用語集ブートストラップ（CONTEXT.md が無い場合）

**Step 3.8 で surface した `result.observability.glossary_drift`（list[str]）を確認する**（判定は済んでいる — 再実行しない）。
**`用語集未作成（CONTEXT.md 不在）`** で始まる行があれば、用語集を作る trigger がどこにも無い PJ で未登録 jargon 候補が
`SEED_MIN_CANDIDATES` 以上ある状態（候補件数・リストは同じ行に含まれる）。creation が手動依存だと drift 検出が永遠に発火しないため evolve で作成を提案する。

- 行が無い / `✓ 構造 drift なし` 等 → CONTEXT.md は既にある or 候補が薄い → 黙ってスキップ
- **`--dry-run`**: 書き込まず Step 3.8 で surface した行をレポートに残す（MUST、観測可能性）
- **通常実行 + seed 適格**: 件数とトークン見積もりを事前提示（llm-batch-guard 準拠・MUST）してから AskUserQuestion
  「生成する（各行 ⚠UNVERIFIED マーク）/ Skip」。A のときのみ SoT（SPEC.md/CLAUDE.md）から確認できる語のみ1行で意味生成（確信が持てない語は除外・捏造しない）し
  `gd.write_context_seed(context_path, rows)` で決定論書き出し → ユーザーに「LLM 推定なので確認を」と報告。

→ contract 統合の経緯（#275→#278）・AskUserQuestion テンプレ・UNVERIFIED の意味・コード詳細は
**[references/glossary-seed.md](references/glossary-seed.md)**。

### Step 7.8: evolve 提案 accept/reject drain（決定論キャプチャ, #360-A [ADR-041]）

fitness calibration の母集団 `optimize_history` を**日次 evolve ループで育てる**ステップ。
run_evolve 末尾の `emit_decisions` が、スキル内容提案（discover の `matched_skills` +
skill_evolve の high/medium 適性提案）の `before_sha` をキュー `DATA_DIR/evolve_decisions/<slug>.jsonl`
にスナップショット済み（`result.evolve_decisions`）。ここで適用実績と明示却下を記録する。

> **なぜ drain が要るか**: 従来は Step 3 の inline python で assistant が手で
> `record_evolve_diff_decision` を呼ぶ MUST だったが、実行されず optimize_history が空のままだった
> （SKILL.md MUST ≠ 決定論強制 = `install ≠ enforcement` の細粒度版）。accept をディスク差分から
> 決定論で取り、この drain を Step として固定することで「記録ステップ未実行」を構造的に塞ぐ。

**accept = 適用実績 / reject = 明示却下 / skip = 記録しない**（ADR-041, C: ハイブリッド）。
Step 3 でスキルファイルを実際に変更したもの（適用済み）が accept、ユーザーが「不要」と却下した
提案 id が reject、未変更かつ未却下（保留）は母集団に入れない。

- **`--dry-run` の場合**: 書き込まない（ingest が内部でガード）。`result.evolve_decisions.count` を
  「accept/reject 記録対象: N 件（dry-run のため未記録）✓」と1行 surface する（silence != evaluated）。
- **通常実行の場合**: Step 3 の承認フロー完了後に以下を実行する。`rejected` は Step 3 で
  ユーザーが明示的に却下した提案の `{id: 理由}`（却下が無ければ空 dict）:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
import evolve_decisions as ed

slug = ed.resolve_slug()
# rejected: Step 3 で明示却下した提案の {proposal_id: rejection_reason}。
# proposal_id は result.evolve_decisions.pending[].id（無ければ空 dict）。
summary = ed.ingest_decisions(slug, rejected={})
print(f"evolve-decisions: accepted={len(summary['accepted'])} "
      f"rejected={len(summary['rejected'])} skipped={len(summary['skipped'])}")
```

- accept/reject は `record_evolve_diff_decision` 経由で optimize_history（ADR-031）へ冪等記録され、
  fitness_evolution の相関母集団 / `check_calibration_regression` の入力になる
- skip（未変更・未却下）は記録されない。消化済みはキューから消える（次 run の emit で上書き）
- 結果（accepted/rejected/skipped 件数）を Report に報告する

### Step 8: Fitness Evolution — 評価関数の改善チェック

evolve.py の出力に含まれる `fitness_evolution` フェーズを確認する。

- `status: "insufficient_data"` の場合:
  - 以下を表示（**ユーザーが文脈を理解できるよう必ず理由を添えること**）:
    ```
    Fitness Evolution: データ不足（N/30件）
    理由: accept/reject の蓄積がまだ30件に達していません。
    このプロジェクトで fitness を有効化するには、accept/reject の実績を積む必要があります:
      - /rl-anything:evolve を回す（discover の matched_skills / skill_evolve high·medium 提案の
        accept/reject が ADR-041 の evolve_decisions により optimize_history へ自動記録される。
        ＝特別な操作は不要で、evolve を継続的に回すこと自体が母集団を貯める）
      - bin/rl-optimize で提案を accept/reject する
      - /rl-anything:rl-loop-orchestrator を実行してバリエーション評価を蓄積する
    ```
  - 0件の場合は `status: "insufficient_data" — データ 0/30件` だけでなく、「このプロジェクトではまだ一度も評価が蓄積されていません」と明示する（MUST）
  - `structural_reason == "skill_evolve_not_scored"` の場合は追加で以下を表示（MUST）:
    ```
    ℹ このプロジェクトでは remediation の fix 提案（rules/hook・構造修正等）が中心で、
      これらは採点対象外のため母集団が構造的に貯まりにくい状態です。
      ただし /rl-anything:evolve を継続的に回せば、discover の skill diff 提案と
      skill_evolve の high·medium 提案の accept/reject は自動で母集団に積み上がります（ADR-041）。
      → 手動で貯める導線を探す必要はなく、evolve を回し続けることが解決策です。
    ```
    (`message` フィールドにも同趣旨の説明が含まれているので、それを表示しても構わない)
- `status: "bootstrap"` の場合:
  - 「簡易分析モード (N/30件)」と表示
  - 基本統計（承認率、平均スコア、スコア分布）を表示
  - 相関分析は行わない旨を注記
- `status: "ready"` の場合:
  - score-acceptance 相関を表示（相関 < 0.50 なら警告）
  - 頻出 rejection_reason があれば新軸追加を提案
  - 提案がある場合、AskUserQuestion で承認を求める（MUST）
  - 承認されたもののみ fitness 関数に反映

---

## Report

### Step 9: Report フェーズ

evolve の結果を**人間が読みやすい形式**で出力する。raw な audit テキストをコードブロックにそのまま貼り付けてはならない（MUST NOT）。

**フォーマット規則（MUST）**:
- 各セクションは `###` 見出しで区切る
- 数値は「問題あり」「問題なし」の判定を添えて表示（数値だけでなく意味を伝える）
- 重大な問題がなければ「✅ 問題なし」と明示する（沈黙は禁止）
- 誤検知（スキップした理由）は「⚠ 誤検知 — スキップ: {理由}」と1行で示す

**出力例（このフォーマットに従う）**:
```
### 今回の evolve まとめ

#### アーティファクト概況
- スキル: N件（custom: X / global: Y）
- rules: Z件 / memory: W件

#### 検出された問題
- ⚠ 誤検知スキップ: stale_ref 6件（AWS SSM パス — バッククォート内のため対象外）
- ✅ rules/memory/hooks/claudemd: 問題なし

#### スキル品質
- implement: 0.88 ✅
- evolve: 0.76（要観察）
```

レポートには以下のセクションが含まれる:
- **Usage (last 30 days)**: PJ 固有スキルのみのメインランキング（プラグインスキルは除外）
- **Plugin usage**: プラグイン別の総使用回数サマリ（例: `gstack(340) / rl-anything(30)`）
- **gstack Workflow Analytics**: gstack スキルが検出された場合、ファネル（plan→refine→ship→document→spec→retro の完走率）、フェーズ別効率、品質トレンド、最適化候補を表示
- **/simplify ゲート結果**: Step 5.6 で /simplify を実行した場合、「/simplify: N件の改善を適用」または「/simplify: 実行済み・変更なし」「/simplify: スキップ（対象なし or 未対応バージョン）」を Compile セクションに表示

> **ナレーション指示 — Report クライマックス（成長レベル）:**
> evolve.py 出力 JSON に `env_score` があれば、`growth_level.compute_level` でレベルを取得し
> `save_world_context` で world-context.json に保存して（SLUG は Step 0.5 と同じ PJ 別スコープ値）、
> レベルアップ / 変化なし / 初回のいずれかでナレーションする。ワンライナー・分岐文言の詳細は
> **[references/report-narration.md](references/report-narration.md)**。env_score が取得できない場合は表示なし。

### Step 10: 推奨アクション（MUST — スキップ厳禁）

**このセクションは必ず出力すること。条件判定の結果によらず、セクション見出し「推奨アクション」を必ずレポート末尾に表示する。**
該当項目がゼロの場合は「推奨アクション: なし」と1行表示する。1件でもあれば全件列挙する。

**出力形式: 判定カード（MUST）**
各項目を以下の3段階で分類して出力する。コマンドなし・参考情報のみの項目は「🔴 要対応」に含めない。

```
### 推奨アクション

🔴 要対応（実行コマンドあり）:
  - /rl-anything:reflect — 未処理フィードバック {N}件

🟡 情報（対策済み・参考値・観察継続）:
  - Bash割合 {X}%（rule 導入済み、継続観察）
  - proposable: global スキルのみ {M}件（参考値）

✅ 問題なし:
  - Prune / Reorganize / Checkpoints / 自己進化
```

カスタムスキルが0件の場合、Reorganize・Optimize・Pitfall剪定・Fitness の4フェーズを個別に「スキップ」と書かず、推奨アクションの「✅ 問題なし」に1行でまとめて列挙する（繰り返し防止）。

各サブ項目は**必ず**判定カードに反映する（沈黙禁止）。判定ロジック・表示テンプレ・閾値定数は
**[references/recommended-actions.md](references/recommended-actions.md)**:

- **10.1 Reflect**: `reflect_data_count >= 1` → 🔴 `/rl-anything:reflect`（未処理 {N}件）/ 0 → 「Reflect: 未処理なし」
- **10.2 ツール使用**: `installed_artifacts` + `tool_usage_patterns` を対策済み/未対策で切替（Built-in代替/sleep/Bash割合の閾値判定 + 前回比トレンド）
- **10.3 自己進化**: 自己進化済みスキル数・pitfall 統計・卒業/剪定フラグ・根本原因横断分析を表示（0 件なら「対象スキルなし」）
- **10.4 Workflow Checkpoint Gaps**: `workflow_checkpoint_gaps` をテーブル表示 / なければ「ギャップなし」
- **10.5 Process Stall Patterns**: `stall_recovery_patterns` をテーブル表示 / なければ「検出なし」
- **10.6 Remediation サマリ**: auto_fixable / manual_required / proposable_custom ≥1 を 🔴 要対応に、proposable_global のみは 🟡 情報に反映

### Step 11: 自己解析 → issue 半自動起票（MUST — #299）

evolve は他フェーズで対象 PJ を改善するが、**evolve 自身の実行結果**（提案の質・実行時エラー・改善余地）を
振り返る経路がこれまで無かった（「install ≠ enforcement」と同型の配線漏れ）。このステップで evolve の `result` を
自己解析し、検出した候補を**人間承認のうえ GitHub issue 化**してメタ層のループを閉じる。

evolve.py 出力トップレベル `self_analysis`（`analyze_evolve_result` が決定論生成・LLM 非依存）を読む。3カテゴリ
（`self_detection` / `runtime_errors` / `improvement_opportunities`、各 `{candidates, summary_line}`）+ `total_candidates`。
各 candidate: `{category, title, body, suggested_label, dedup_key, severity}`。

**必ず以下を順に行う（MUST）**:
1. **surface（3カテゴリとも）**: 各 `summary_line` をそのまま列挙。0 件でも `✓ 評価したが該当なし` を省略しない（silence ≠ evaluated）。`{"error": ...}` はそのまま表示
2. **候補ゼロなら終了**: `total_candidates == 0` ならここで終了
3. **dedup**: `gh issue list --repo todoroki-godai/rl-anything --state open` と突合し `flatten_candidates`+`filter_duplicates` で root cause 単位の重複を除く。duplicates は「既存 #N と重複 — スキップ」と1行ずつ表示
4. **承認（unique のみ・提案詳細プロトコル）**: 1件ずつ title・根拠（severity）・起票先・ラベル（`suggested_label` は提案値で変更/スキップ可）を提示 → AskUserQuestion で個別承認。10 件超は per-item 10 件まで
5. **起票（承認分のみ）**: `render_issue_body` でマーカー付き body 生成 → `gh issue create --repo todoroki-godai/rl-anything`

→ self_analysis の構造詳細・各カテゴリの検出内容・dedup/render の実コードは **[references/self-analysis.md](references/self-analysis.md)**。

> **一言メモ — 自己解析完了後**: 起票件数に応じた1文を出力する（文言は [references/report-narration.md](references/report-narration.md)）。

### べき等性

連続実行時、前回以降の新規データのみを対象に処理する（MUST）。
重複した提案を行ってはならない（MUST NOT）。
自己解析の起票は body 埋め込みマーカー（`rl-evolve-introspect:<dedup_key>`）で root cause 単位の重複を防ぐ（MUST NOT — 同一 root cause で毎 evolve 重複起票しない）。

## allowed-tools

Read, Bash, AskUserQuestion, Write, Glob, Grep

## Tags

evolve, orchestrator, pipeline
