---
name: evolve
effort: high
description: |
  Run the full autonomous evolution pipeline: Observe → Diagnose → Compile → Housekeeping → Report.
  Designed for daily execution to continuously improve skills and rules.
  Trigger: evolve, 自律進化, evolution pipeline, 日次実行, daily run, パイプライン実行
disable-model-invocation: true
---

# /evolve-anything:evolve — 全フェーズ統合実行

Observe データ確認 → Diagnose → Compile → Housekeeping → Report の全フェーズをワンコマンドで実行する。日次実行を想定。

## Usage

```
/evolve-anything:evolve              # 通常実行
/evolve-anything:evolve --dry-run    # レポートのみ、変更なし
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

## dry-run 記録可否の一元表（MUST — 手順本体に入る前に必ず確認する）

⚠️ **この表の「dry-run」＝スキル引数 `/evolve-anything:evolve --dry-run` のことだけを指す（MUST）**。
Step 1 以降の分析コマンドが `evolve --project-dir ... --dry-run` で走ることとは**無関係**（分析エンジン側の
`--dry-run` は標準フローで**常に**付く。それを見て「今回は dry-run」と判定してはならない — MUST NOT）。
ユーザーが引数なしで `/evolve-anything:evolve` を起動したら、コマンド行に `--dry-run` が並んでいても
**この表の「非 dry-run 時」列を適用する**（引数なし起動なのに dry-run 判定して Step 6.5 / 7.7 を誤スキップした
実害あり・#320）。以降この文書で `dry_run` 変数と書いたら、値はスキル引数の有無だけで決まる。

evolve の手順は Step 0.5〜11 と長く、**書き込み操作ごとに dry-run（スキル引数 `--dry-run`）で記録するか否かが分岐する**。
長い手順の終盤で取り違えやすい（過去に実行ミスが起きた）ので、各書き込み操作の dry-run 記録可否をここに集約する。
各 Step の本文に書かれた実際の挙動（`mark_done(dry_run=...)` / `record_reviewed(dry_run=...)` /
`evolve --drain` の設計）を転記したもの。個々の Step の記述が正準で、この表は早見表として使う。

| Step | 操作 | 関数 / コマンド | dry-run 時 | 非 dry-run 時 |
|------|------|----------------|-----------|--------------|
| 5.5 | remediation 却下を suppression ledger に記録 | `record_rejection`（SKILL では dry_run 時ループを実行しない・ライブラリは `persist=False`） | **書かない**（MUST NOT） | 書く |
| 6.1 | 初回 bootstrap 完了 marker | `bootstrap_backlog.mark_done(slug, dry_run=dry_run)` | **書かない** | 書く |
| 6.2 | 今日の修正確認 既読追記 | `daily_review.record_reviewed(..., dry_run=dry_run)` | **書かない** | 書く |
| 6.5 | auto-memory drain（memory 書込 / belief_blocks） | `auto_memory_broker.ingest_memory_results(...)` | **書かない**（分析パスはゼロ書込） | 書く |
| 6.6 | correction_semantic Phase A emit（読み取りのみ） | `correction_semantic.batch.emit_judgement_requests` | **読み取りのみ**（両列とも書込なし・dry-run 無関係に常時実行可） | 同左 |
| 7.8 | correction_semantic Phase C（weak_signals 隔離記録 + 個人辞書 + 判定進捗） | `evolve --drain --correction-responses <path>`（`ingest_judgement_results`） | **書く**（drain の apply 境界・Step 6.6 で responses ファイルが作られた場合のみ実行される。無ければ `{"skipped": ...}` で graceful skip・#339） | 書く |
| run 末尾 | evolve_decisions queue 書込（before_sha スナップショット） | `emit_decisions(...)` の `_write_queue` | **書かない** | 書く |
| run 末尾 | drain 検出用 **pending marker** | `emit_decisions(...)` の `write_pending_marker` | **書く**（#402/ADR-041・文書化された意図的 dry-run 書込・#513） | 書く |
| 7.8 | optimize_history へ accept/reject 記録 | `evolve --drain --accepted <id...> --rejected <id> <理由>`（`drain_pending`） | **書く**（`--accepted`/`--rejected` を渡した場合のみ。drain 自体は dry-run 分析後でも必ず実行するが、ID 無しでは記録しない・#444） | 書く |
| 7.8 | 決定論 weak_signals の永続化（manual_edit / esc / rephrase / permission_deny） | `evolve --drain`（`persist_weak_signals_drain`）／`evolve-fleet detect`（全 PJ・daily runner step 1c・#304） | **書く**（drain の apply 境界・#484/#513。detect 側は evolve 非依存で毎朝書く。両者とも `signal_key` dedup で冪等） | 書く |
| 7.8 | calibration state + tool_usage_snapshot 確定（result 依存） | `evolve --drain --result-json "$OUT"`（`persist_result_dependent_state`） | **書く**（drain の apply 境界・result 由来値を運搬・#146/ADR-051） | 書く |
| 3.5 | remediation 連続提示 count marker 更新＋閾値到達で自動却下 | phases_remediate の `reconcile_surfaced(persist=not dry_run)` | **書かない**（persist=False は marker を読むだけの表示用判定） | 書く |
| 7.8 | remediation 連続提示 count marker の実書込＋閾値到達 record_rejection（result 依存） | `evolve --drain --result-json "$OUT"`（`reconcile_surfaced(persist=True)`） | **書く**（drain の apply 境界・result 由来の tracked を運搬・#186） | 書く |

**2 つの設計の違いを取り違えない（MUST）**:

- **`mark_done` / `record_reviewed` / `record_rejection` / auto-memory ingest / queue 書込**は、dry-run で
  **一切書かない**（`pitfall_dryrun_stateful_store_write` を踏まない最下層ゲート）。`--dry-run` は「分析だけで
  ファイルを変えない」契約なので、これらは非 dry-run のときだけ書く。
- **`evolve --drain`（Step 7.8）と pending marker（run 末尾）は、dry-run でも書く**。理由は #402/ADR-041/#513:
  evolve の標準フローは `evolve --dry-run` で分析 → assistant が Step 3 で対話適用、という運用なので、
  drain を dry-run でゲートすると accept/reject の記録と決定論 weak_signals の永続化が **実 PJ で永久に死ぬ**
  （#505 の誤ゲートを revert した経緯）。drain は tool 文脈（CLI）で apply 境界に走り、検出は冪等（dedup）
  なので dry-run 分析後に走らせて書くのが正。pending marker も drain 検出に必要なので dry-run でも書く
  （store/queue とは別状態の運用マーカー）。
- **remediation 連続提示 count marker（#186）も drain の apply 境界が唯一の書き手**。phases_remediate の
  `reconcile_surfaced` は dry-run では `persist=False`（marker を読むだけの表示用判定）で、count は進めない。
  count marker の実書込と閾値（`DEFAULT_AUTO_REJECT_AFTER_RUNS=2`）到達時の自動却下は
  `evolve --drain --result-json "$OUT"` が result 由来の tracked を再構築して確定する
  （`build_reconcile_tracked` が phases 側と同一構成）。標準フローが dry-run のみで marker が永久未書込
  → 閾値未達で自動却下が全 PJ 死蔵していた #494 の穴の根治（weak_signals #484 と同型）。dry-run 連打で
  誤って count が進む事故を防ぐため、書込は drain の1点に集約する（pitfall_dryrun_stateful_store_write）。
- **correction_semantic Phase C（#339）も drain の apply 境界が唯一の書き手**。Phase B（Haiku 判定）は
  本質的に対話的で非対話 CLI（`--drain`）内では実行できないため、Step 6.6 が Phase A→B を行い responses を
  ファイルへ書き、Step 7.8 の `evolve --drain --correction-responses <path>` が Phase C（weak_signals 隔離
  記録 + 個人辞書 + 判定進捗）を確定する。Step 6.6 で responses ファイルが作られなかった（`unjudged == 0`
  またはユーザーがスキップを選んだ）場合は `--correction-responses` を付けず、drain 側は graceful skip する。

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

## 手順ナビ — 3 層に分けて読む（#49）

手順は Step 0.5〜11 と長く **27 ステップ・MUST 多数**で、毎回全部に同じ注意を払うと取りこぼす。
そこで全ステップを「**毎回通る骨格 (A)**／**該当した時だけ (B)**／**特定状況の参照 (C)**」の3層に分類する。
**まず (A) だけを「今すぐ実行する骨格」として通し、(B)(C) は各 Step の入口に書かれた条件に当てはまった時だけ実行する。**
分類は読みやすさのためのナビで、各 Step 本文が正準（本文の MUST はそのまま有効）。

### (A) 必須骨格 — 毎回このメインパスを通す（5 ステップ）

これだけは dry-run でも本実行でも**常に**通る。迷ったら (A) を順に実行すれば evolve は成立する。

1. **[Step 0.5](#step-05-世界観ロード)** 世界観ロード（LLM 不要）
2. **[Step 1](#step-1-データ十分性チェックobserve-先行-pre-flight)** データ十分性チェック（observe 先行 pre-flight）→ ここで lightweight/skip が分岐
3. **[Step 3.8](#step-38-observability必ず-surface-する--must)** Observability を必ず surface（silence ≠ evaluated の単一ソース）
4. **[Step 9](#step-9-report-フェーズ)** Report（TL;DR + 成長レベル + 成長状態）
5. **[Step 10](#step-10-推奨アクションmust--スキップ厳禁)** 推奨アクション（スキップ厳禁・該当ゼロでも「なし」を出す）

### (B) 条件付き — フェーズ出力にデータ／発見があった時だけ（10 ステップ）

各 Step の入口に「`result.phases.X` が〜の場合」「候補があれば」等の発火条件がある。条件に当てはまらなければ
1 行 surface（✓ クリーン）して**次へ進む**。当てはまった時だけ本文の AskUserQuestion / 適用フローを実行する。

- **[Step 2](#step-2-fitness-関数チェック)** Fitness 関数チェック（`has_fitness: false` のとき生成提案）
- **[Step 3.6](#step-36-スキル自己進化適性判定)** スキル自己進化適性判定（`batch_guard_trigger` 非 null のときインタラクティブ）
- **[Step 5.5](#step-55-remediation-フェーズ)** Remediation（`total_issues > 0` のとき分類・承認）
- **[Step 6.1](#step-61-初回バックログ-bootstrap443)** 初回バックログ bootstrap（`bootstrap.is_bootstrap == True` のとき 3 択）
- **[Step 6.2](#step-62-今日の修正確認daily_review446)** 今日の修正確認（`daily.eligible == True` のとき y/n 確認）
- **[Step 6.6](#step-66-correction_semantic-意味判定2相-phase-ab-431339)** correction_semantic 意味判定（`correction_semantic.unjudged > 0` のとき llm-batch-guard 確認）
- **[Step 7](#step-7-prune-フェーズmerge)** Prune（+Merge・淘汰候補があるとき個別承認）
- **[Step 7.5](#step-75-pitfall-剪定)** Pitfall 剪定（卒業/剪定候補があるとき）
- **[Step 7.8](#step-78-evolve-提案-acceptreject-drain決定論キャプチャ-360-a-adr-041)** accept/reject drain（Step 3 で適用 or 却下したとき。`evolve --drain` 1 コマンド）
- **[Step 11](#step-11-自己解析--issue-半自動起票must--299)** 自己解析 → issue 半自動起票（`total_candidates > 0` のとき承認起票）

### (C) 参照専用 — 特定状況でのみ開く（4 ステップ）

通常は 1 行 surface して通り過ぎてよい。本文を熟読するのは下記の特定状況だけ。

- **[Step 5.5.1](#step-551-proposable-の-line_limit_violation--split_candidate-に対する2相品質回復adr-037-phase-1d-ii)** proposable の line_limit/split に対する2相品質回復（Step 5.5 で該当 issue を承認した時のみ）
- **[Step 7.6](#step-76-合理化防止テーブル)** 合理化防止テーブル（`rationalization_table` フェーズが存在する時のみ）
- **[Step 7.7](#step-77-用語集ブートストラップcontextmd-が無い場合)** 用語集ブートストラップ（CONTEXT.md 不在 + seed 適格の時のみ）
- **[Step 8](#step-8-fitness-evolution--評価関数の改善チェック)** Fitness Evolution（`status: ready` で提案がある時のみ承認）

> 上記に挙げていない Step（3 / 3.5 / 3.7 / 4 / 5 / 5.6 / 6 / 6.5）は Diagnose/Compile の中間フェーズで、
> (A) のメインパスを通る過程で出力を読むもの。各 Step 本文の指示に従う。

## 実行手順

### Step 0.5: 世界観ロード
まず既存の世界観をロードする（LLM 不要）:

```bash
# 対象 PJ の cwd で実行。slug は resolve_slug（git-common-dir 親, ADR-031）— worktree でも本体 PJ slug に正規化（#408-C）。
PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス。bash は呼び出しごとに独立プロセスのため、$PJ を使う
                     # 全ブロックの冒頭でこの行を置く（env の PJ があれば優先・無ければ cwd。
                     # バッチ経路 #400 本体では呼び出し側が PJ を env で渡すだけで対応できる）
SLUG="$(PJ="$PJ" python3 -c "import os, sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib'); from optimize_history_store import resolve_slug; print(resolve_slug(cwd=os.environ['PJ']))" 2>/dev/null || echo unknown)"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/world_context.py" --load --slug "$SLUG"
```

`--load` が exit 0 なら既存世界観をそのまま使う。**exit 1（初回）のみ** claude -p を使わずファイルベース2相で生成する（[ADR-037]）。手順・JSON フォーマットは **[references/world-context.md](references/world-context.md)**（初回のみ読めばよい）。スクリプト不可時はナレーションをスキップ（主機能に影響しない）。

### Step 1: データ十分性チェック（observe 先行 pre-flight）
安価な `--observe-first` で observe + fitness ゲートだけを算出する（数秒で返る）。重いフェーズ（discover/audit/skill_evolve/remediation/prune…）はここでは回さない（#407）。

```bash
evolve-usage-log "evolve"
PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス。bash は呼び出しごとに独立プロセスのため、$PJ を使う
                     # 全ブロックの冒頭でこの行を置く（env の PJ があれば優先・無ければ cwd。
                     # バッチ経路 #400 本体では呼び出し側が PJ を env で渡すだけで対応できる）
OUT="$(evolve --project-dir "$PJ" --print-out-path)"
evolve --project-dir "$PJ" --dry-run --observe-first --output "$OUT"
```

⚠️ **ここの `--dry-run` は分析エンジンのフラグ（MUST NOT 誤読）**: 標準フローでは**常に**付き、「分析だけ回して
result JSON を作る」という意味しか持たない。**スキルレベルの dry-run 判定（記録可否の一元表）には一切使わない**。
スキル引数に `--dry-run` が無ければ、このコマンドを実行しても `dry_run=False` のまま進む（#320）。
⚠️ **`--output` は必須（MUST）**: full JSON は `$OUT`（`/tmp/rl_evolve_<slug>.json`）に書かれ、stdout は1行サマリのみ。
⚠️ **slug 照合は MUST（#408-B）**: `$OUT` を Read したら `slug`/`project_dir`/`generated_at` が対象 PJ と一致するか検証してから進む。以降「evolve.py の出力に含まれる X フェーズを確認する」は**すべて `$OUT` を Read して参照する**（stdout を `head`/`tail` で読んではならない — MUST NOT。巨大 JSON が途中で切れて invalid になるため）。

`$OUT` の `observe.action` で分岐する（`backfill_recommended`=先に backfill を案内・continue しない / `skip_recommended`=AskUserQuestion で実行可否 / `lightweight_recommended`=軽量モードかフル実行かを AskUserQuestion / 無し=フル実行）。フル実行時は所要時間目安（`env_tier` 基準）を伝えてから `--observe-first` 無しで dry-run を再実行し、`$OUT` を書き直す。
→ 各分岐の詳細条件・フル dry-run コマンドは **[references/diagnose.md](references/diagnose.md)**。

### Step 2: Fitness 関数チェック
evolve.py の出力に含まれる `fitness` フェーズを確認する。

- `has_fitness: false` かつ `generation_advised != false`: **提案詳細プロトコルに従い** AskUserQuestion で「生成する（`/evolve-anything:generate-fitness --ask`）」/「スキップ（default で続行）」を確認する（MUST — テキスト表示だけで済ませない）。ドメイン推定と生成される評価軸を判断材料として示す。
- `has_fitness: false` かつ `generation_advised: false`（#105）: `generation_note` を1行 surface し**デフォルトはスキップ**（fitness_evolution が「使わない設計」と判定済み・AskUserQuestion は出さない）。
- `has_fitness: true`: 利用可能な fitness 関数名を表示して次へ。

## Stage 1: Diagnose（パターン検出 + 問題診断）

### Step 3: Discover フェーズ（enrich 統合済み）
discover のパターン検出（`repeating_patterns` / `tool_usage_patterns` / `rule_violation_observed`）と enrich 結果（Jaccard 照合による `matched_skills` / `unmatched_patterns`）を確認する。`matched_skills` は **`skill_path` 単位にグループ化してから**（1 SKILL.md = 1 提案 = 1 判断・#444）改善提案を diff で提示し AskUserQuestion で承認/スキップ（MUST）— **承認して実際に適用した提案の `id`、却下した提案の `id` と理由を控えておく（MUST・#444）**。`id` は `result.evolve_decisions.pending[].id`（グループ化済みなので `skill_path` で一意に対応づく）。この記録は Step 7.8 の drain へ `--accepted`/`--rejected` として渡し、optimize_history への accept/reject 記録に使う（drain は自動では記録しない — 明示 ID が必須）。`rule_violation_observed`（rule installed だが実行が止まっていない違反観測）は別レーンで surface する（MUST）。
→ 表示テンプレ・分岐の詳細は **[references/diagnose.md](references/diagnose.md)**。
> 一言メモ: [references/report-narration.md](references/report-narration.md)

### Step 3.5: レイヤー別診断
`layer_diagnose` フェーズ（Rules / Memory / Hooks / CLAUDE.md の4レイヤー診断）の issue リストを確認する。issue があれば Compile ステージの remediation で対処する。
→ 各レイヤーの issue 種別は **[references/diagnose.md](references/diagnose.md)**。

### Step 3.6: スキル自己進化適性判定
`skill_evolve`（自己進化適性を15点満点でスコアリング）を確認する。`batch_guard_trigger` が非 null なら AskUserQuestion（評価/今回スキップ/永続スキップ）→ `--confirmed-batch` 再実行のフローを実行（MUST）。`null`（通常）なら `already_evolved` / `high_suitability` / `medium_suitability` / `insufficient_usage` / `rejected` の各件数（int。個別名は `assessments[]`）を確認する。
→ **[references/skill-evolve-assessment.md](references/skill-evolve-assessment.md)**。

### Step 3.7: Audit 問題検出
audit の `collect_issues()`（layer_diagnose 統合済み・`memory_trace` / `constitutional_score` 既定 true）の問題リストを Compile ステージに渡す。discover の rule/hook candidate、skill_evolve の候補、`verification_rule_candidate` も統合される。
→ 詳細: **[references/diagnose.md](references/diagnose.md)**。

### Step 3.8: Observability（必ず surface する — MUST）
トップレベル `observability` フィールド（`unmanaged_pitfalls` / `glossary_drift` 等）の各 key を**そのまま必ず列挙する**（clean でも `✓ 評価したが該当なし` を省略しない・silence != evaluated の単一ソース。`{"error": ...}` はそのまま表示）。加えて **Triage SKIP 抑制サマリ**（#308）・**Triage アクションサマリ**（CREATE/UPDATE/SPLIT/MERGE。CREATE の埋没厳禁・#478/#528-4）・**Weak Signals matrix**（チャネル別×スコープ、#528-2）も必ず1行以上 surface する（MUST）。
→ 各サマリの表示テンプレ・根拠は **[references/diagnose.md](references/diagnose.md)**。

### Step 4: Reorganize フェーズ（split 検出 + 階層統合提案）
`reorganize` フェーズ（TF-IDF + 階層クラスタリング）を確認する。`skipped` ならその理由を、そうでなければクラスタ一覧・`split_candidates`・`hierarchy_candidates`（SkillPyramid・低レベルスキル群を上位へ束ねる提案、#303）を表示する。**split↔archive 相互排他**（`reconcile_split_archive` が prune の archive 候補と重複する split 提案を自動除外・#301/#302）。0件でも「該当なし ✓」を残す（silence != evaluated）。
→ 詳細: **[references/diagnose.md](references/diagnose.md)**。

## Stage 2: Compile（パッチ生成 + メモリルーティング）

### Step 5: Optimize フェーズ
カスタムスキルの改善は `/evolve-anything:evolve-skill <skill>` で実行。
`/evolve-anything:optimize` スキルは削除済み（`bin/evolve-optimize` は内部 CLI として存続）。

**外部インストールスキルは除外（MUST）。** `classify_artifact_origin()` が `"plugin"` を返すスキル
（プラグイン由来スキル等）は最適化対象外。
ユーザーが自作したスキル（custom / global）のみが対象。

### Step 5.5: Remediation フェーズ
remediation.py が audit 結果を confidence_score / impact_scope で **auto_fixable**（conf≥0.9）/ **proposable**（conf≥0.5・custom は `partition_proposable_by_confidence` で conf≥0.7=個別承認・<0.7=まとめてスキップ #377-3）/ **manual_required**（conf<0.5 or global）の3カテゴリに動的分類する。`total_issues == 0` なら「問題なし」。各カテゴリとも AskUserQuestion の**前に**補足説明を出す。`proposable_global` / `rule_violation_observed` の情報レーンは dismiss 記録（TTL45日）で以後抑制でき、個別承認で却下された提案は `record_rejection` で suppression ledger に記録する（**dry-run 時は記録しない — MUST NOT**）。決定論 fallback（`reconcile_surfaced`、既定2回で自動却下・#494）が inline 記録の取りこぼしを補完する。hook インストール系（`*_hook_candidate`）は影響半径が最大なので折り畳み対象外（#225）。
サマリ: 「Remediation 完了: N件修正 / M件スキップ / K件ロールバック（要手動対応）」。`suppressed_by_ledger > 0` なら1行追記（#477）。
→ カテゴリ別出力テンプレ・dismiss/却下記録コード・対応 type 一覧は **[references/remediation.md](references/remediation.md)**。
> 一言メモ: [references/report-narration.md](references/report-narration.md)

#### Step 5.5.1: proposable の line_limit_violation / split_candidate に対する2相品質回復（[ADR-037] Phase 1d-ii）
`fix_line_limit_violation` / `fix_split_candidate` は [ADR-037] で claude -p を全廃し決定論フォールバックで完走する。
承認後に assistant がファイルベース2相（emit→インライン→ingest）で実際の圧縮/分離/分割を行う。
対象 issue（line_limit_violation 非rule=圧縮 / rule=分離、split_candidate=分割）ごとに emit/ingest 関数の signature が異なる（#524-1）。
→ signature 表・実行コードは **[references/remediation.md](references/remediation.md) の Step 5.5.1 節**。`fixed=True` で書込完了、`fixed=False` は手動対応を案内。

### Step 5.6: /simplify ゲート
Remediation でファイル変更があった場合、`.py` が1つ以上含まれるときのみ `/simplify` を実行し、diff を AskUserQuestion で「適用」/「元に戻す」確認する（MUST）。`.md` のみ・変更なし・古い CC はスキップ。
→ 判定条件・実行手順は **[references/remediation.md](references/remediation.md)**。

### Step 6: Reflect フェーズ
reflect は独立フェーズではなく discover に統合済み。discover の `reflect_data_count`（未処理の修正フィードバック件数）で分岐する: 欠落/degraded（`None` or `< 0`）→「discover 失敗のため不明」と表示・AskUserQuestion なし / `>= 5` → AskUserQuestion で `/evolve-anything:reflect` を提案（MUST）/ `0 < N < 5` → 表示のみ / `0` → スキップ。
→ 判定条件の全文は **[references/correction-review.md](references/correction-review.md)**。

### Step 6.1: 初回バックログ bootstrap（#443）
`result.correction_review.bootstrap.is_bootstrap == True` のとき、AskUserQuestion で3択（まとめて確認 / 日次5件ずつ / TTL失効に任せる）を人間に選ばせる（MUST — テキスト表示だけで済ませない）。**各 option の `detail` に副作用（marker を立てるか否か＝以後の再表示挙動）を必ず添える（#51）**。group 数が閾値（12）超のときは theme_buckets 単位の multiSelect 1問に畳む（#558）。
→ 3択の副作用詳細・multiSelect/per-group フロー・`mark_done` コードは **[references/correction-review.md](references/correction-review.md)**。

### Step 6.2: 今日の修正確認（daily_review・#446）
`result.correction_review.daily.eligible == True` のとき、前回以降の新規 weak_signal（最大5件）を AskUserQuestion で y/n 確認する（MUST — 最大5問を1バッチで）。「はい」→ `PJ="${PJ:-$(pwd)}" && evolve-reflect --project-dir "$PJ" --promote-weak` で昇格 + `record_reviewed(decision="promoted")`、「いいえ」→ `record_reviewed(decision="rejected")`。Step 6.1 の bootstrap 対象は自動的に除外されるため二重提示しない（#476-3）。
→ 判定条件・AskUserQuestion テンプレ・コードは **[references/correction-review.md](references/correction-review.md)**。

### Step 6.5: auto-memory キュー drain（2相, [ADR-037] Phase 2）
`DATA_DIR/auto_memory_queue/<slug>.jsonl`（Stop hook がゼロ LLM で enqueue 済み）を Phase A（emit・LLM ゼロ）→ Phase B/C（インライン生成→ingest）の2相で消化する。ingest は生成後ゲート（belief_entropy）を内蔵し、ソースを落とした要約は書込なしで `belief_blocks.jsonl` に記録（blocked カウント）。空応答（skipped）はキューに残り次回再試行。空キューなら「0件 ✓」で終了。結果（stored/blocked/skipped）を Report に報告する。
→ 実行コードは **[references/auto-memory-drain.md](references/auto-memory-drain.md)**。

### Step 6.6: correction_semantic 意味判定（2相 Phase A→B, #431/#339）
> **実行経路の SoT（#408/#410）**: Phase B（LLM 判定）の主経路は `correction_semantic.judge_runner.run_daily_judge` による daily runner の**無人日次実行**（毎朝・1日の件数/トークン上限あり・`bin/evolve-daily-run` から直接呼ばれる）。以前は本 Step 6.6（対話 y/n 承認時のインライン判定）が唯一の実体で、対話フローの奥にあるため2ヶ月供給が止まっていた（#408 根因）。本 Step は**フォールバック**（daily runner を待たずに今すぐ判定したい・バックログを手動で消化したい場合の対話経路）として残す。

`result.correction_semantic.unjudged` が未判定発話数（Phase A は `phases_capture` が既に emit 済みだが、requests 本体は result に載らないため本ステップで `emit_judgement_requests` を再実行して取得する）。`unjudged == 0` なら「correction_semantic 意味判定: 0件 ✓」で終了。`unjudged > 0` のときは **llm-batch-guard**（MUST）: 件数・概算トークン（`estimate_tokens`）を提示して AskUserQuestion で y/n 確認 → 承認時のみ Phase B（各 prompt をインラインで Haiku 相当の判定に回答・`claude -p` は呼ばない）を行い、`responses` を `/tmp/rl_correction_responses_<slug>.json` に保存する。**Phase C（ingest・weak_signals 記録）はここでは呼ばない** — Step 7.8 の `evolve --drain --correction-responses <path>` が apply 境界で実行する（#339）。
→ 実行コードは **[references/correction-semantic-drain.md](references/correction-semantic-drain.md)**。

## Stage 3: Housekeeping（淘汰 + 評価関数改善）
### Step 7: Prune フェーズ（+Merge）
淘汰候補をスキルの出自別に3セクションで表示する:
- **Custom Skills**: 「ゼロ呼び出し」だけでアーカイブと決めつけない。各候補を①SKILL.md+git log 調査 →②4種別分類 →③テキスト出力 →④個別 AskUserQuestion で承認（**全候補一括判断は禁止 — MUST**）。断った候補には `.pin` 案内を添える。→ [references/prune-merge.md](references/prune-merge.md)
- **Plugin Skills**: レポートのみ（アーカイブせず案内のみ）。
- **Global Skills**: 件数1行に畳み `bin/evolve-fleet status` へ誘導する（PJ単独では判断材料不足・#525-3）。→ [references/housekeeping.md](references/housekeeping.md)
- **Merge**: `prune.merge_result` の `status` に応じて統合版を生成し AskUserQuestion で承認/却下、却下は `add_merge_suppression()` で抑制（MUST）。→ [references/prune-merge.md](references/prune-merge.md)
> 一言メモ — Prune / Housekeeping 完了後: 「整理完了。少し軽くなった。」を出力する。

### Step 7.5: Pitfall 剪定
`pitfall_hygiene` フェーズを確認する: **graduation_candidates**（卒業候補・提案詳細プロトコルに従い AskUserQuestion で確認）/ **cap_exceeded**（Active pitfall が10件超のスキルは剪定レビュー推奨）/ **stale_warnings**（6ヶ月以上未更新は検証推奨）/ **cross_skill_analysis**（根本原因カテゴリの横断集中→共通ルール化提案）。

### Step 7.6: 合理化防止テーブル
`rationalization_table` フェーズがあれば言い訳×スキップ後エラー率×サンプル数のテーブルを表示する。フェーズ自体が無ければ「データ不足 — スキップ」。
→ テーブル形式・`enriched_pitfalls` 表示は **[references/housekeeping.md](references/housekeeping.md)**。

### Step 7.7: 用語集ブートストラップ（CONTEXT.md が無い場合）
Step 3.8 で surface した `observability.glossary_drift` の `用語集未作成（CONTEXT.md 不在）` 行を確認する（判定は済んでいる — 再実行しない）。seed 適格なら件数・トークン見積もりを事前提示（llm-batch-guard 準拠・MUST）してから AskUserQuestion「生成する（各行 ⚠UNVERIFIED マーク）/ Skip」。
→ 詳細: **[references/glossary-seed.md](references/glossary-seed.md)**。

### Step 7.8: evolve 提案 accept/reject drain（決定論キャプチャ, #360-A [ADR-041]）
fitness calibration の母集団 `optimize_history` を日次 evolve ループで育てるステップ。Step 3 の承認・適用フロー完了後、分析が `--dry-run` だったか否かに関わらず**必ず**以下の単一コマンドを実行する（MUST）。**Step 6.6 で responses ファイルを保存した場合は `--correction-responses <path>` も、Step 3 で承認して適用した提案・却下した提案がある場合は `--accepted`/`--rejected` も同じコマンドに足す（MUST）**:

```bash
PJ="${PJ:-$(pwd)}"  # Step 1 と同一の束縛（bash は呼び出しごとに独立プロセスのため各ブロックで再束縛する）
OUT="$(evolve --project-dir "$PJ" --print-out-path)"
evolve --project-dir "$PJ" --drain --result-json "$OUT"
```

**ID の受け渡し方（#444）**: Step 3 で `matched_skills` を **`skill_path` 単位にグループ化して**（1 SKILL.md = 1 提案 = 1 判断。提案 identity がファイル単位なので、マッチ単位に割ると複数の提示が同じ `id` を共有して判断が成立しない）AskUserQuestion にかけたとき、承認して実際にファイルを適用した提案の `id`（`result.evolve_decisions.pending[].id`。グループ化済みなので `skill_path` で一意に対応づく）を貯めておき、却下した提案は `id` と却下理由をペアで貯めておく（Step 3 の MUST）。承認/却下があれば、上のコマンドの末尾に `--accepted`/`--rejected` を足す（`--accepted` は承認 ID をスペース区切りで並べる。`--rejected ID REASON` は却下1件につき1回繰り返す — 理由は必須で、空文字は CLI が拒否する）。responses ファイルもあるならさらに `--correction-responses <path>` を足す。例:

```bash
PJ="${PJ:-$(pwd)}"  # 別ブロックなので再束縛する（bash は呼び出しごとに独立プロセス）
OUT="$(evolve --project-dir "$PJ" --print-out-path)"
evolve --project-dir "$PJ" --drain --result-json "$OUT" \
  --accepted evdiff_abc123 evdiff_def456 \
  --rejected evdiff_ghi789 "ドメイン不一致"
```

（承認も却下も無ければ `--accepted`/`--rejected` は省略する — pending のまま次回 evolve に持ち越される。1回の Step 7.8 で複数コマンドを実行して二重 drain する必要はない — 該当する引数だけを足した**単一コマンド**にまとめる。）

上のコマンドは、決定論 weak_signals（manual_edit/esc/rephrase/permission_deny）・calibration state・tool_usage_snapshot・remediation 連続提示 marker・correction_semantic Phase C（#339・responses ファイルがあるときのみ）を同じ apply 境界で確定する（`--result-json "$OUT"` が result 依存2項目（calibration state / tool_usage_snapshot）を運搬・#146/ADR-051。growth crystallization emit は #379 Step 4 で growth-journal harness ごと削除済み）。**`--accepted`/`--rejected` を渡さなければ evolve 提案の accept/reject は記録しない**（#376 是正）。

**accept = 明示 accept イベント AND 適用実績 / reject = 明示却下 / skip = 記録しない**（ADR-041 是正版, #376）。ディスク sha が変わっただけ（＝提案とは無関係な通常 commit の可能性がある）では accept にならない。`--accepted`/`--rejected` を省略すると diff が実際に適用されていても fitness 母集団に記録されない（承認も却下も無ければ省略でよい — pending のまま次回 evolve に持ち越される）。`--accepted`/`--rejected` に**存在しない ID・重複 ID・理由なし reject を渡すと CLI がエラーを返し何も記録しない**（#444。既存の `genetic-prompt-optimizer --accept`/`--reject`——直近結果を丸ごと受理/却下する単数フラグ・別コマンド——とは別物）。drain を忘れても次回 SessionStart のリマインドが保険になる（#402、ただし SessionStart hook は対話チャネルを持たず `--accepted`/`--rejected` を渡せないため記録はされない — リマインド表示のみ）。`accepted >= 1` なら「fitness 母集団に +N 件記録 ✓」と1行 surface する。
→ 設計根拠（#400/#484/#494/#444）・drain サマリの読み方・result-json 運搬の詳細は **[references/housekeeping.md](references/housekeeping.md)**。

### Step 8: Fitness Evolution — 評価関数の改善チェック
evolve.py の出力に含まれる `fitness_evolution` フェーズを確認する。`status: "insufficient_data"` は **`one_liner` を1行そのまま出すのが結論**。件数（`N/30`）や長文 `details.message` は既定で出さない（開示はユーザーが尋ねたときのみ・#559 で契約圧縮済み）。`status: "bootstrap"` は簡易統計（承認率・平均スコア・分布）のみ表示し相関分析は行わない。`status: "ready"` は score-acceptance 相関（<0.50 で警告）と頻出 rejection_reason を表示し、提案があれば AskUserQuestion で承認を求める（MUST）。
→ 誤読防止の詳細（#400/#525-1/#479 統合）は **[references/housekeeping.md](references/housekeeping.md)**。

## Report
### Step 9: Report フェーズ
evolve の結果を**人間が読みやすい形式**で出力する。raw な audit テキストをコードブロックにそのまま貼り付けてはならない。**冒頭に TL;DR を必ず出す（MUST・#525-2）**: 「TL;DR: 変更 {N}件 / 要対応 {M}件 / 残りすべて評価済みクリーン」。**全 ✓ の observability 項目は1ブロックに畳む（#525-2）**: ⚠/ℹ のみ個別表示し、✓ クリーンな key はまとめて1行に畳む。各セクションは `###` 見出し・数値には判定を添える・「✅ 問題なし」を沈黙させない（MUST）。
レポートには Usage（PJ固有スキルのみ） / Plugin usage / gstack Workflow Analytics（検出時） / `/simplify` ゲート結果のセクションを含める。成長レベル表示の直後に `growth_report.lines` を列挙する（MUST）。**Step 6.2 で対話昇格した場合は per-PJ の値に今回昇格数を加算する方式で上書きする**（`evolve-reflect --project-dir "$PJ" --promote-weak` 出力の `corrections_human_allpj` は全PJ合計であり、そのまま分子に使ってはならない — MUST NOT・#526-1）。
→ TL;DR/畳み込みブロック/フォーマット規則の全文と成長状態レポートの補正ロジックは **[references/report-narration.md](references/report-narration.md)**。

### Step 10: 推奨アクション（MUST — スキップ厳禁）
**このセクションはスキップせず出力すること。条件判定の結果によらず、セクション見出し「推奨アクション」をレポート末尾に表示する。** 該当項目がゼロの場合は「推奨アクション: なし」と1行表示する。各項目を 🔴要対応（実行コマンドあり）/ 🟡情報（対策済み・参考値・観察継続）/ ✅問題なし の3段階の判定カードで出力する（MUST）。カスタムスキルが0件の場合、Reorganize・Optimize・Pitfall剪定・Fitness を個別にスキップと書かず「✅ 問題なし」に1行でまとめる（繰り返し防止）。
各サブ項目（10.1 Reflect / 10.2 ツール使用 / 10.3 自己進化 / 10.4 Workflow Checkpoint Gaps / 10.5 Process Stall Patterns / 10.6 Remediation サマリ）は**必ず**判定カードに反映する（沈黙禁止）。
→ 判定カード出力例・各サブ項目の判定ロジックと閾値定数は **[references/recommended-actions.md](references/recommended-actions.md)**。

### Step 11: 自己解析 → issue 半自動起票（#299）
evolve は他フェーズで対象 PJ を改善するが、**evolve 自身の実行結果**（提案の質・実行時エラー・改善余地）を振り返る経路がこれまで無かった。このステップで evolve.py 出力トップレベル `self_analysis`（`analyze_evolve_result` が決定論生成・実モジュールは `evolve_introspect`、`self_analysis` という名前のモジュールは存在しない）の3カテゴリ（`self_detection` / `runtime_errors` / `improvement_opportunities`）を読む。
**必ず以下を順に行う（MUST）**: ①3カテゴリとも `summary_line` を surface（0件でも省略しない） ②`total_candidates == 0` なら終了 ③`gh issue list --repo todoroki-godai/evolve-anything --state all` と突合し dedup（`regressions` は前回 closed への backlink 付き） ④unique のみ提案詳細プロトコルで個別承認 ⑤承認分のみ `gh issue create` で起票（`render_regression_body` で backlink 付与）。
→ 構造詳細・dedup/render の実コードは **[references/self-analysis.md](references/self-analysis.md)**。
> 一言メモ: [references/report-narration.md](references/report-narration.md)

### べき等性
連続実行時、前回以降の新規データのみを対象に処理する（MUST）。
重複した提案を行ってはならない（MUST NOT）。
自己解析の起票は body 埋め込みマーカー（`evolve-introspect:<dedup_key>`）で root cause 単位の重複を防ぐ（MUST NOT — 同一 root cause で毎 evolve 重複起票しない）。

## allowed-tools
Read, Bash, AskUserQuestion, Write, Glob, Grep

## Tags
evolve, orchestrator, pipeline
