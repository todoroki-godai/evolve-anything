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

evolve が「やりますか？」と尋ねる前に、ユーザーが Yes/No を判断できる材料を提示するための共通ルール。
件数や閾値だけ出して承認を求めると、ユーザーは「何が・なぜ・どう変わるか」が分からず判断できない。
実際このプロトコルが無かったため、effort frontmatter 提案が「active スキル 10件」とだけ表示され、
どのスキルにどの effort が付くのか分からない、という問題が起きた。

**AskUserQuestion を出す前に、対象を per-item で展開して以下の3点を必ず提示する:**

- **対象**: 具体名（`skill-name` / `path/to/file.py:42` / ルール名）。「N件」だけに丸めない
- **根拠**: なぜ検出したか。閾値・metric・evidence の**実値**を出す（例: `content_lines=62 < 80`, `confidence=0.90`, `ゼロ呼び出し・最終使用 45日前`）
- **変更内容**: 適用すると何がどう変わるか。可能なら before → after か diff の1行要約（例: `effort: (なし) → low`）

**多件数の扱い**: per-item 展開は**最大 10 件**まで。超過分は「他 M 件」と件数で示し、
「全件確認するには <コマンド or ファイルパス>」と誘導する。10 件以内なら全件展開する。

**Python が detail を持っている前提**: 多くの提案は issue の `detail`（`skill_name` / `confidence` / `reason` 等）や
`generate_proposals(issues)` の `{proposal, rationale}` に per-item の判断材料が既に入っている。
件数に丸めるのは表示側の問題なので、`detail` を読んで展開すれば追加の集計は不要。

このプロトコルは Step 2 / Step 5.5 / Step 7 / Step 7.5 など全提案ポイントに適用する（後述の各 Step で再掲しない）。

## 実行手順

### Step 0.5: 世界観ロード

```bash
python3 scripts/lib/world_context.py --load 2>/dev/null || \
  python3 scripts/lib/world_context.py --generate --claude-md CLAUDE.md \
    --slug "$(basename $(git rev-parse --show-toplevel 2>/dev/null || echo unknown))"
```

stdout フォーマット（`--load` も `--generate` も同じ）: JSON 1行。
例: `{"setting":"...","protagonist_title":"知識の番人","environment_name":"知識の塔","issue_name":"歪みの影","improvement_name":"輝く刻印","total_evolve_count":42,...}`

Claude はこの JSON を読んで各変数（`environment_name` / `protagonist_title` / `issue_name` / `improvement_name`）を
以降のナレーション指示に展開すること。スクリプトが利用できない場合はナレーション指示をスキップする（evolve の主機能に影響しない）。

---

### Step 1: データ十分性チェック

```bash
rl-usage-log "evolve"
rl-evolve --project-dir "$(pwd)" --dry-run
```

- 出力 `observe` フェーズの `action` で分岐する:
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
  - **採点記録（MUST, issue #223）**: accept/reject いずれの場合も、提案後の SKILL.md 全文（after content）を採点して履歴に正規記録する。対象が SKILL.md content なので fitness_func=`skill_quality` で採点でき、fitness_evolution の相関母集団に増量として加わる:
    ```bash
    python3 -c "import sys; sys.path.insert(0, '<PLUGIN_DIR>/skills/evolve-fitness/scripts'); \
    from fitness_evolution import record_evolve_diff_decision; \
    record_evolve_diff_decision(skill_name='<skill>', after_content=open('<after.md>').read(), \
    diff_summary='<1行要約>', human_accepted=<True|False>, rejection_reason=<reason or None>)"
    ```
    （構造修正・rule/hook candidate・reorganize/prune・skill_evolve 提案は採点対象外。skill diff のみ記録する）
- `unmatched_patterns` がある場合:
  - 「既存スキルに関連なし → Discover の新規候補として処理」と表示

> **一言メモ — Discover / Diagnose 完了後:**
> 発見パターン数（`unmatched_patterns` + `matched_skills` の合計）に応じて以下の 1 文を出力すること。
>
> - 3件以上: 「{N}件の兆候を確認。一つずつ見ていく。」
> - 1〜2件: 「{N}件、気になる点あり。見落とさないようにする。」
> - 0件: 「問題なし。今日は静かな日だ。」

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

**batch_guard_trigger 検出（優先処理）**:  
`result.phases.skill_evolve.batch_guard_trigger` が `null` でない場合、LLM 評価対象スキルが多すぎるため  
以下のインタラクティブフローを実行してから evolve を再実行する:

1. グループ表を表示する（origin / スキル数 / 推定トークン / スキル名一覧）  
   `already_denied` に含まれるスキルは「今回自動スキップ済み」と明示する
2. AskUserQuestion でグループごとに選択させる:
   - 「評価する（このまま続行）」
   - 「今回のみスキップ」
   - 「永続スキップ（denylist に追加）」
3. 永続スキップを選んだスキルがある場合（`_plugin_root` は `~/.claude/rl-anything` または `plugin_root.py` で解決できる実際のパス）:
   ```python
   python3 -c "
   import sys; sys.path.insert(0, str(__import__('plugin_root').PLUGIN_ROOT / 'scripts' / 'lib'))
   from skill_evolve.denylist import add_to_denylist
   add_to_denylist(['skill-a', 'skill-b'])
   print('denylist に追加しました')
   "
   ```
4. 「今回のみスキップ」と「永続スキップ」の両方のスキル名を `--skip-skills` に渡し、**必ず `--confirmed-batch` を付けて** evolve.py を再実行する（`--confirmed-batch` がないと guard が再発火する）:
   ```
   python3 evolve.py --confirmed-batch [--skip-skills=skill-a,skill-b] [既存の引数]
   ```
5. 新しい result で以降のステップを継続する

`batch_guard_trigger` が `null` の場合は従来通り以下のサマリを確認する:

- **already_evolved**: 既に自己進化パターンが組み込まれたスキル数
- **high_suitability**: 適性高（12-15点）のスキル数 → Compile で変換を推奨
- **medium_suitability**: 適性中（8-11点）のスキル数 → ユーザー判断に委ねる
- **rejected**: アンチパターン2件以上該当で変換非推奨

適性高/中のスキルがあれば `skill_evolve_candidate` issue として Remediation パイプラインに注入され、Step 5.5 で変換提案が生成される。

### Step 3.7: Audit 問題検出

evolve.py の出力に含まれる audit の `collect_issues()` 結果を確認し、問題リストを Compile ステージに渡す。
（collect_issues() 内で layer_diagnose も統合されている）

evolve の audit は **`memory_trace=True` / `constitutional_score=True` 既定**で実行される。これにより MemTrace 帰属診断（決定論・LLM ゼロ）と slop_detector を 10% ブレンドした constitutional スコアが「evolve するだけ」で出力に乗る。constitutional は haiku×最大4 だがレイヤ単位コンテンツハッシュキャッシュ（`constitutional_cache.json`）で通常 0〜1 コール、constitutional 単独 ON のため environment fitness（score_count≥2 で発火）は呼ばれない。
discover の `tool_usage_rule_candidate` / `tool_usage_hook_candidate`、skill_evolve の `skill_evolve_candidate`、および `verification_rule_candidate`（検証知見カタログ）も issue リストに統合される。

### Step 3.8: Observability（必ず surface する — MUST）

evolve.py 出力の **トップレベル `observability` フィールド**（`unmanaged_pitfalls` / `glossary_drift` 等の key → 行リスト）を、各 key の行を**そのまま必ずサマリに列挙する**。clean（「✓ 評価したが該当なし」）でも省略しない。

理由: これらは `phases.audit.report` の 217KB markdown 中盤にも出ているが、選択読みでは埋もれて surface されない（silence != evaluated の配線漏れが #272 後に再発した実例）。`observability` フィールドは audit↔evolve の契約として構造化済みなので、**markdown 側の該当行を探さず、この構造化フィールドを正準ソースとして出す**。`{"error": ...}` のときはエラーをそのまま表示する。

### Step 4: Reorganize フェーズ（split 検出のみ）

evolve.py の出力に含まれる `reorganize` フェーズ結果を確認する。
reorganize.py は TF-IDF + 階層クラスタリングでスキル群を分析し、JSON を出力する。

- `skipped: true` の場合:
  - 理由（`insufficient_skills` / `scipy_not_available`）を表示
  - `scipy_not_available` の場合: 「`pip install scipy scikit-learn` でインストールしてください」と案内
- `skipped: false` の場合:
  - クラスタ一覧を表示（各クラスタのスキル名とキーワード）
  - `split_candidates` があれば「分割候補」として表示し、分割を提案

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
- `dry_run` の場合: 分類結果サマリのみ表示（auto_fixable: N件, proposable: custom N件 / global M件（参考値）, manual_required: N件）
  - `proposable_custom` / `proposable_global` フィールドを使用。サマリ判定は `proposable_custom` のみで行う

**auto_fixable** (confidence ≥ 0.9, impact_scope in (file, project)):
- `generate_auto_fix_summaries(issues)` を呼び出し、**AskUserQuestion の前に**以下のフォーマットでテキスト出力する（MUST）:
  ```
  **修正候補 N件:**
  1. `<ファイルパス>` — <proposal>（理由: <rationale>）
  2. ...
  「一括修正」を選ぶとこれらが順に適用されます。
  ```
- ⚠ **pitfall — 補足説明は Q&A の前に出す（MUST）**: proposal/rationale をテキストとして先に出力してから AskUserQuestion を呼ぶ。選択肢の description に rationale を詰め込まない。ユーザーが Yes/No を判断できる状態を作ってから質問する
- その後、AskUserQuestion で「一括修正」「個別承認」「スキップ」を選択（MUST）
  - 一括修正: 全 auto_fixable を順に実行
  - 個別承認: 各 issue の proposal/rationale を提示しながら1件ずつ承認を取り、承認分のみ実行
  - スキップ: 何もしない
- 承認後: `FIX_DISPATCH[issue_type]` で対応する fix 関数を実行 → `verify_fix()` + `check_regression()` で2段階検証
- 対応 type: stale_ref, stale_rule, claudemd_phantom_ref, claudemd_missing_section, skill_evolve_candidate, verification_rule_candidate
- regression 検出時: `rollback_fix()` で復元し manual_required に格上げ
- 結果を `record_outcome()` で記録
- `collect_issues()` は内部で `diagnose_all_layers()` を統合済みのため、別途マージ不要

**proposable** (confidence ≥ 0.5, scope != global, confidence < 0.9 for non-file/project):
- `proposable_custom > 0` の場合のみ個別承認フローを実行（MUST）
- **提案詳細プロトコルに従う**: `generate_proposals(issues)` で各 issue の `{proposal, rationale}` を取得し、**1件ずつ**「対象・根拠（detail の実値）・変更内容」を提示してから AskUserQuestion で個別承認（MUST）
- **⚠ pitfall — 補足説明は Q&A の前に出す（MUST）**: 「なぜ必要か」「どんな効果があるか」を AskUserQuestion と同じターン内の Q&A より前のテキストとして先に出力すること。ユーザーに聞かれてから説明するのは遅い。ユーザーが Yes/No を判断できる状態を作ってから質問する。
- 同じ type の issue が複数あっても件数に丸めない（例: `missing_effort` が 10 スキル分あるなら各スキル名 + 推定 effort + reason を per-item で展開する。10 件超は他 M 件と誘導）
- 対応 type: line_limit_violation, near_limit, orphan_rule, stale_memory, memory_duplicate, missing_effort
- 承認された修正のみ実行 → 検証 → 記録
- `proposable_custom == 0` かつ `proposable_global > 0` の場合: 「proposable: global スキルのみ {M}件（参考値） — 対応不要」と1行表示してスキップ

**manual_required** (confidence < 0.5, or impact_scope = global):
- 問題の概要、推奨アクション、分類理由を表示のみ

**サマリ**: 「Remediation 完了: N件修正 / M件スキップ / K件ロールバック（要手動対応）」

> **一言メモ — Remediation 完了後:**
> 修正を適用したファイル/スキルの数（N件修正 の N）に応じて以下の 1 文を出力すること。
>
> - 3件以上: 「{N}件修正。地道な仕事だ。」
> - 1〜2件: 「{N}件、小さな修正。でも確かな改善だ。」
> - 0件: 「今回は何も変えなかった。それでいい。」

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

evolve.py の出力に含まれる `reflect` フェーズ結果を確認する。

- `pending_count >= 5` または前回 reflect から 7日超経過 → AskUserQuestion で `/rl-anything:reflect` の実行を提案する（MUST）
  - question: 「未処理の修正フィードバックが {N} 件あります（最終 reflect: {date}）。/reflect を実行しますか？」
  - options: 「実行する」「スキップ」
- `0 < pending_count < 5` かつ前回 reflect から 7日以内 → Report に「未処理修正 {N} 件あり」と表示のみ
- `pending_count == 0` → スキップ

---

## Stage 3: Housekeeping（淘汰 + 評価関数改善）

### Step 7: Prune フェーズ（+Merge）

淘汰候補をスキルの出自別に3セクションで表示する（MUST）:

#### Custom Skills（淘汰候補）

カスタムスキルのうち、ゼロ呼び出しのものをアーカイブ候補として処理する。
**全候補を一括判断してはならない。各スキルを個別に調査・分類してから、1件ずつ承認を求める（MUST）。**

「ゼロ呼び出し」はアーカイブの必要条件ではない。セットアップ・オンボーディング・デプロイ等のスキルは設計上低頻度が正常であり、SKILL.md を読まずに「オンデマンドスキル」と決めつけてはならない。

**各候補について順番に以下を実施する（MUST）:**

**1. 調査**
- 候補スキルの SKILL.md を Read で全文読み取る
- `git log --oneline --all -- <skill_dir>/` でそのスキルの最終変更日・変更傾向を確認する

**2. 分類（4種別）**

| 種別 | 判定基準 | 推奨 |
|------|----------|------|
| **オンデマンド型** | セットアップ・デプロイ・削除など特定イベント時のみ使う設計 | keep |
| **一時目的完了型** | hotfix・移行・バックフィル等、目的が完了済みで今後不要 | archive |
| **統合済み型** | 他スキルに機能が吸収されており独立して不要 | archive |
| **日常用途・未発火型** | 本来頻繁に使うはずだが使われていない（改善または削除候補） | 要確認 |

**3. Q&A前にテキスト出力（MUST）:**

```
---
**N/M: {スキル名}** [作成: {日付} / {経過}日]
説明: {SKILL.md の description}
種別: {4種別のいずれか}
根拠: {SKILL.md・git log から読み取った判断理由を具体的に}
推奨: {keep / archive / 要確認}
---
```

**4. AskUserQuestion で個別承認**（テキスト出力の後に呼ぶ — MUST）:
- 候補 1-2件目: `アーカイブ` / `維持` / `後で判断`
- 候補 3件目以降: `アーカイブ` / `維持` / `残り全てスキップ`

承認されたもののみアーカイブ。

**アーカイブを断った候補への対応（再表示抑制）**: ユーザーが「今は保持する」を選択した場合、次のように案内する（MUST）:
> 再度 evolve で表示したくない場合は、スキルディレクトリに `.pin` ファイルを作成してください:
> ```bash
> touch <skills_dir>/<skill_name>/.pin
> ```
> `.pin` があるスキルは以降の淘汰候補から自動除外されます。

#### Plugin Skills（レポートのみ）
プラグイン由来で未使用のスキルを表示。アーカイブはせず情報提供のみ。
「未使用。`claude plugin uninstall` を検討？」と案内する。

#### Global Skills（既存ロジック維持）
Usage Registry の cross-PJ 使用状況を確認し、既存の `safe_global_check` で処理。

#### Merge サブステップ

evolve.py の出力に含まれる `prune.merge_result` を確認する。
prune.py の `merge_duplicates()` は `duplicate_candidates` から統合候補を JSON で出力する（型A パターン: LLM 呼び出しなし）。マージ候補検出は prune に一元化済み。

- `merge_proposals` の各エントリについて:
  - `status: "skipped_pinned"` / `"skipped_plugin"` / `"skipped_suppressed"` / `"skipped_low_similarity"` → スキップ理由を表示
  - `status: "proposed"` → Claude が primary と secondary の SKILL.md を読み込み、統合版を生成してユーザーに提示する（MUST）
    - AskUserQuestion で「承認（統合を適用）」「却下（変更なし）」を選択させる（MUST）
    - 承認された場合: 統合版を primary の SKILL.md に上書きし、secondary を `archive_file()` でアーカイブ
    - 却下された場合: 当該ペアを merge suppression に登録して次回以降の提案を抑制する。以下のコマンドを実行する（MUST）:
      ```bash
      python3 -c "
      from discover import add_merge_suppression
      add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')
      "
      ```
  - `status: "interactive_candidate"` → 対話的統合提案（MUST）:
    - `similarity_score` 降順で最大3件を処理する（1回の evolve あたりの上限）
    - 各ペアについて、Claude が primary と secondary の SKILL.md を読み込み、統合案の概要を提示する
    - AskUserQuestion で「承認（統合を適用）」「却下（次回以降も提案しない）」を選択させる（MUST）
    - 承認された場合: proposed と同じフロー（統合版生成 → primary の SKILL.md に上書き → secondary を `archive_file()` でアーカイブ）を適用する
    - 却下された場合: `add_merge_suppression()` で suppression 登録し、次回以降の再提案を抑制する:
      ```bash
      python3 -c "
      from discover import add_merge_suppression
      add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')
      "
      ```

> **一言メモ — Prune / Housekeeping 完了後:**
> 以下の 1 文を出力すること。
>
> 「整理完了。少し軽くなった。」

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

audit の Glossary Drift section が **None**（= CONTEXT.md が存在しない）で、かつ SoT に
未登録 jargon 候補が一定数ある PJ では、用語集（Ubiquitous Language）を最初に作る trigger が
どこにも無いという穴がある。creation が手動依存だと detection（drift 検出）が永遠に発火しない。
evolve はユーザーが明示的に回す per-project ループなので、ここで作成を提案する。

**判定（決定論）**:

```python
import sys, os
sys.path.insert(0, os.path.join(os.environ.get("CLAUDE_PLUGIN_ROOT", "."), "scripts", "lib"))
import glossary_drift as gd
context_path = os.path.join(PROJECT_DIR, "CONTEXT.md")
candidates = []
if not os.path.exists(context_path):
    sources = [os.path.join(PROJECT_DIR, n) for n in ("SPEC.md", "CLAUDE.md") if os.path.exists(os.path.join(PROJECT_DIR, n))]
    candidates = gd.find_undefined_terms([], sources)
seed_eligible = (not os.path.exists(context_path)) and len(candidates) >= gd.SEED_MIN_CANDIDATES
```

`seed_eligible` が False（CONTEXT.md が既にある or 候補が `SEED_MIN_CANDIDATES` 未満）なら
このステップは黙ってスキップする。jargon の薄い PJ に空の用語集を作らない。

**True の場合のみ AskUserQuestion**（提案詳細プロトコルに従う）。LLM で意味を埋めるため、
**件数とトークン見積もりを事前提示する**（プロジェクトの llm-batch-guard 準拠・MUST）:

```
CONTEXT.md が無く、未登録 jargon 候補が {N} 件あります（{候補リスト}）。
LLM で意味を埋めた用語集ドラフトを生成しますか？
（SPEC.md + CLAUDE.md を読み {N} 語の意味を生成。入力 ~{Xk} tokens 見積もり）

A) 生成する — 各行を ⚠UNVERIFIED でマークし、後で確認
B) Skip — 今は作らない
```

**A を選んだ場合のみ**:
1. SPEC.md / CLAUDE.md を読み、各候補語の意味を **1 行で** 生成する。決め打ちで埋めず、
   SoT から意味が確認できる語のみ対象にする（確信が持てない語は除外し B 扱い）。捏造しない
2. `rows = [(term, meaning), ...]` を作り、決定論 writer で書き出す（**LLM は整形に関与しない**）:
   ```python
   gd.write_context_seed(context_path, rows)  # 既存があれば FileExistsError（非破壊）
   ```
3. 全行の初出列に `⚠UNVERIFIED` が入る。これは「人間が意味を確認し初出を `#NNN`/`ADR-NNN` に
   書き換えてマーカーを外す」までの未検証マーク。**drift gate には載らず**、以後の evolve/audit が
   `unverified_terms` advisory で確認を促し続ける（誤り毒・検出器自滅の回避）
4. ユーザーに「CONTEXT.md を {N} 語の seed で生成しました。意味は LLM 推定なので確認してください」と報告

> **なぜ silent でなく確認 + UNVERIFIED か**: 用語集は jargon の権威ある decode。LLM が黙って
> 埋めると誤った意味が静かに混入し「腐った用語集は無いより悪い」状態になる。また SoT から全自動で
> 埋めると drift 検出器の検出対象が消え自滅する。確認 1 回と未検証マーカーでこの両方を防ぐ。

### Step 8: Fitness Evolution — 評価関数の改善チェック

evolve.py の出力に含まれる `fitness_evolution` フェーズを確認する。

- `status: "insufficient_data"` の場合:
  - 以下を表示（**ユーザーが文脈を理解できるよう必ず理由を添えること**）:
    ```
    Fitness Evolution: データ不足（N/30件）
    理由: rl-loop / rl-optimize による accept/reject の蓄積がまだ30件に達していません。
    このプロジェクトで fitness を有効化するには、以下のいずれかで実績を積む必要があります:
      - bin/rl-optimize で提案を accept/reject する
      - /rl-anything:rl-loop-orchestrator を実行してバリエーション評価を蓄積する
    ```
  - 0件の場合は `status: "insufficient_data" — データ 0/30件` だけでなく、「このプロジェクトではまだ一度も評価が蓄積されていません」と明示する（MUST）
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

> **ナレーション指示 — Report クライマックス:**
> evolve.py の出力 JSON に `env_score` フィールドが含まれる場合（前のステップで表示した JSON を参照）、
> その値を使って以下のワンライナーでレベルを取得する（`<ENV_SCORE>` を実際の数値で置換）:
> ```bash
> python3 -c "import sys; sys.path.insert(0,'scripts/lib'); from growth_level import compute_level; import json; info = compute_level(<ENV_SCORE>); print(json.dumps({'level':info.level,'title_ja':info.title_ja,'title_en':info.title_en}))"
> ```
> stdout: `{"level": 7, "title_ja": "熟達", "title_en": "Experienced"}` 形式。
>
> 次に `save_world_context` で world-context.json に保存する:
> ```bash
> python3 -c "
> import sys; sys.path.insert(0,'scripts/lib')
> from world_context import load_world_context, save_world_context
> from pathlib import Path
> import os
> data_dir = Path(os.environ.get('CLAUDE_PLUGIN_DATA', Path.home() / '.claude' / 'rl-anything'))
> ctx = load_world_context(data_dir) or {}
> save_world_context(data_dir, ctx, env_score=<ENV_SCORE>)
> "
> ```
> `previous_level` / `current_level` は `save_world_context` が自動更新する。更新後の値でナレーションを出力する。
>
> - レベルアップ（`previous_level` < `current_level`、かつ両方あり）:
>   「✨ {旧称号} → **[Lv.{current_level}] {新称号}**」
> - 変化なし（`previous_level` == `current_level`、かつ値あり）:
>   「**[Lv.{current_level}] {称号}**」
> - 前回レベル不明（`previous_level` == null / 初回）:
>   「**[Lv.{current_level}] {称号}**」
> - env_score が取得できない場合: 表示なし。

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

#### 10.1: Reflect 推奨

discover 結果の `reflect_data_count` の値を確認し、**必ず**以下のいずれかを表示する:
- `reflect_data_count >= 1` → 「⚠ 未処理の修正フィードバックが {N} 件あります。`/rl-anything:reflect` で反映すると evolve-skill の精度が向上します」
- `reflect_data_count == 0` → 「Reflect: 未処理なし」

#### 10.2: ツール使用改善

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

#### 10.3: 自己進化ステータス

`skill_evolve` と `pitfall_hygiene` の結果から**必ず**以下を表示する:
- 自己進化済みスキル数
- 各スキルの pitfall 統計（Active/New/Candidate/Graduated 件数）
- 卒業候補/剪定推奨があればフラグ
- 根本原因カテゴリの横断分析結果

自己進化済みスキルが0の場合は「自己進化: 対象スキルなし」と表示。

#### 10.4: Workflow Checkpoint Gaps

discover 結果の `workflow_checkpoint_gaps` を確認し、以下のいずれかを表示する:
- ギャップあり → テーブル形式で表示:
  ```
  | Skill | Category | Evidence | Confidence |
  |-------|----------|----------|------------|
  | verify | infra_deploy | 3 | 0.75 |
  ```
- ギャップなし → 「Workflow Checkpoints: ギャップなし」

#### 10.5: Process Stall Patterns

discover 結果の `stall_recovery_patterns` を確認し、以下のいずれかを表示する:
- パターンあり → テーブル形式で表示:
  ```
  | Command | Sessions | Recovery | Confidence |
  |---------|----------|----------|------------|
  | cdk deploy | 3 | kill | 0.80 |
  ```
- パターンなし → 「Process Stall Patterns: 検出なし」

#### 10.6: Remediation サマリ

remediation 結果から**必ず**以下を判定カードに反映する:
- `auto_fixable` ≥ 1 → 🔴 要対応「/rl-anything:evolve（非 dry-run）— 自動修正可能 {N}件」
- `manual_required` ≥ 1 → 🔴 要対応「手動対応 {N}件」（issue type の概要リスト付き）
- `proposable_custom` ≥ 1 → 🔴 要対応「提案あり {N}件（次回 evolve で確認）」
- 上記すべて 0 → 「✅ 問題なし」に含める
- `proposable_global` のみ ≥ 1 → 🟡 情報「global スキル proposable {M}件（参考値）」

### べき等性

連続実行時、前回以降の新規データのみを対象に処理する（MUST）。
重複した提案を行ってはならない（MUST NOT）。

## allowed-tools

Read, Bash, AskUserQuestion, Write, Glob, Grep

## Tags

evolve, orchestrator, pipeline
