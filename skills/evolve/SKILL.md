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
# slug は resolve_slug（git-common-dir 親で正規化, ADR-031）で算出する。`git rev-parse
# --show-toplevel` の basename は worktree だと worktree 名（例 evolve）を返し本体 slug と
# 食い違うため使わない（#408-C）。worktree からの evolve でも本体 PJ slug に正規化される。
SLUG="$(python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts/lib'); from optimize_history_store import resolve_slug; print(resolve_slug())" 2>/dev/null || echo unknown)"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/world_context.py" --load --slug "$SLUG"
```

`--load` が exit 0 で JSON を出した場合はそれを使う（既存世界観・継続）。Claude はこの JSON を読んで
各変数（`environment_name` / `protagonist_title` / `issue_name` / `improvement_name`）を以降のナレーション指示に展開する。

**exit 1（初回＝未生成）の場合のみ**、claude -p を使わずファイルベース2相で生成する（[ADR-037]）。
手順・JSON フォーマット・変数展開の詳細は **[references/world-context.md](references/world-context.md)**（初回のみ読めばよい）。
スクリプトが利用できない場合はナレーション指示をスキップする（evolve の主機能に影響しない）。

---

### Step 1: データ十分性チェック（observe 先行 pre-flight）

まず **`--observe-first`** で安価な observe + fitness ゲートだけを算出する（数秒で返る）。重いフェーズ（discover/audit/skill_evolve/remediation/prune…）はここでは回さない。これにより lightweight/skip の分岐が「フル分析コスト（dry-run で数十秒〜1 分、[ADR-037] LLM-free 化以降の実測・#479）を払う前」に効く（#407）。

```bash
rl-usage-log "evolve"
# 出力は PJ 別パスに書く（共有固定パスだと別 PJ の stale 出力を誤読する, #408-A）。
# #525-3: rl-evolve が slug 解決済みの OUT パスを返すので SLUG/OUT 再導出を1コマンドに短縮。
OUT="$(rl-evolve --project-dir "$(pwd)" --print-out-path)"
rl-evolve --project-dir "$(pwd)" --dry-run --observe-first --output "$OUT"
```

⚠️ **`--output` は必須（MUST）**: result JSON はフェーズ全部入りで数十〜数百 KB になる。`--output` を付けると full JSON は `$OUT`（`/tmp/rl_evolve_<slug>.json`）に書かれ、stdout には `{"output": "...", "slug": ..., "generated_at": ..., "phases": [...], "env_tier": ...}` の **1行サマリ**だけが出る。

⚠️ **slug 照合は MUST（#408-B）**: `$OUT` を Read したら、まずトップレベルの `slug` / `project_dir` / `generated_at` を確認し、**対象 PJ と一致するか検証してから** Diagnose に進む。一致しなければ stale/別 PJ の出力なので使わず再実行する。以降このスキルで「evolve.py の出力に含まれる `X` フェーズを確認する」と書かれた箇所は、すべて **`$OUT` を Read（必要なら offset/limit で該当フェーズだけ）して参照する**。`rl-evolve` の stdout を `| head` / `| tail` で削ったり Bash の出力をそのまま読もうとしてはならない（MUST NOT）。`indent=2` の巨大 JSON が途中で切れて invalid になり「JSON が不完全 → 全量を保存し直し」のやり直しが多発する（これが本フローを設計した理由）。

- 出力（`$OUT` の）`observe` フェーズの `action` で分岐する:
  - `action: "backfill_recommended"`（テレメトリ未取得＝初回導入直後、`telemetry_empty: true`）の場合:
    - 「テレメトリが空。先に /rl-anything:backfill で既存セッション履歴を取り込んでください」と案内する（MUST）
    - evolve を続行せず、backfill を先に実行するよう促す（自動実行はしない）
  - `action: "skip_recommended"`（少量だが観測ありのデータ不足）の場合:
    - 「データ不足のためスキップ推奨」メッセージを表示（MUST）
    - AskUserQuestion で実行/スキップを選択させる
  - `action: "lightweight_recommended"`（過去データは十分だが**前回 evolve 以降の新規観測が 0**、`no_new_observations: true`、#396）の場合:
    - フル実行は audit/discover/skill_evolve batch_guard/remediation を回しても結局すべて keep/評価のみの **no-op** になりやすい（batch_guard の AskUserQuestion を挟む割に成果が無い）。べき等性は正しいが操作コストに見合わない
    - AskUserQuestion で「軽量モード（重い LLM フェーズ/batch_guard をスキップ）」か「フル実行」かを選ばせる（MUST）
    - 軽量モードを選んだ場合: 重いフェーズは回さず、observe の結果のみ報告して **ここで完了**してよい（pre-flight で既に重いフェーズはスキップ済み）

- フル実行が必要な場合（`action` が無い＝データ十分かつ新規観測あり、または上記分岐でユーザーが「実行/フル実行」を選んだ場合）:
  - **MUST: フル dry-run の所要時間目安をユーザーに伝えてから実行する**（無音で長時間ハングと誤解されるのを防ぐ, #407）。目安は `env_tier` で示す（[ADR-037] で audit/skill_evolve が LLM-free 化されて以降の実測ベース・#479）: `small` ≈ 〜15 秒 / `medium` ≈ 15〜30 秒 / `large` ≈ 30〜60 秒（観測 161 件・skills+rules 64 件の large 環境で実測約 34 秒）。auto-memory drain（Step 6.5）など assistant インライン LLM 生成を伴う対話フェーズは別途時間がかかる。
  - 重いフェーズ込みの dry-run を **`--observe-first` 無し**で同じ PJ 別パスに書き直す（Bash の各呼び出しは別シェルで `$OUT` が引き継がれないため、このブロック内で再導出する。#525-3 で `--print-out-path` に短縮済み）:
    ```bash
    OUT="$(rl-evolve --project-dir "$(pwd)" --print-out-path)"
    rl-evolve --project-dir "$(pwd)" --dry-run --output "$OUT"
    ```
  - 完了後、`$OUT`（=`/tmp/rl_evolve_<slug>.json`）を Read して再度 slug を照合し、Step 2 以降へ進む。

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

**`phases.discover.rule_violation_observed`（list、#522-3）が存在する場合は別レーンとして surface する（MUST）**: 既存 rules で禁止済みのコマンド（例: `cd` 禁止なのに 626 回観測）は「スキル候補」ではなく**ルール導入済みだが実行が止まっていない違反観測**（rule installed != enforced）として、`violated_command` / `count` / `recommendation`（hook enforce 検討）を1行ずつ表示する。これらは repeating_patterns から除外済みのためスキル候補としては提案しない。違反ゼロ時はキーが欠落するので省略してよい。

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

- **`result.phases.skill_evolve.batch_guard_trigger` が `null` でない場合**: refresh が必要なスキルが
  多い（Phase B judgment refresh の繰り延べ LLM コストが発生しうる）。グループ提示 → AskUserQuestion
  （評価/今回スキップ/永続スキップ）→ `--confirmed-batch` 付き再実行のインタラクティブフローを実行してから
  evolve を再実行する（MUST）。手順・denylist/再実行コードは
  **[references/skill-evolve-assessment.md](references/skill-evolve-assessment.md)**。
  - **表示は実見込みを先頭に**（#400 バグ#4）: cache 反映後の実見込み `estimated_tokens_cache_aware`
    （fresh `cache_fresh_count` 件は ≈0）を**主**に出す。worst-case の `estimated_tokens` は括弧内の
    参考値に留める（worst-case を前面に出してユーザーを不必要に身構えさせない）。
  - `--confirmed-batch` 再実行自体は LLM-free（#377-1）で、sentinel の **`rerun_llm_free: true`** フラグで
    機械可読に示される（#394）。再実行は `rl-evolve --confirmed-batch ...`（PATH ラッパー、#395）で行う。
  - **`null` で来る = 課金ゼロ確定の自動進行**（#400 バグ#3）: 全スキルが cache-fresh（refresh_needed 合計0）の
    ときは guard sentinel を返さず自動で評価へ進む。確定ゼロのケースで AskUserQuestion を出して全フェーズを
    やり直す無駄を構造的に排した。よって sentinel が出る = 実コストが発生しうる場合に限られる。
- **`null` の場合（通常）**: 以下のサマリ（**いずれも件数=int**。スキル名は `assessments[]` の `.skill_name` を見る。
  `high_suitability[].skill` のような配列展開はできない＝#395）を確認する:
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

**Triage アクションサマリ（#478 / #528-4、必ず surface する — MUST）**: `phases.skill_triage` の `CREATE` / `UPDATE` / `SPLIT` / `MERGE` 各リストの**件数と上位候補（skill 名 + confidence）をサマリ表示する**。特に **CREATE（trajectory 由来の新スキル候補）は埋没厳禁** — 過去は remediation の低 confidence batch_skip の1行に畳まれてユーザーに提示されなかった（#478）。各アクション0件でも「CREATE: 0件 ✓」のように省略せず1行残す（silence != evaluated）。**この表示指示（MUST）の置き場はこの SKILL.md である（#528-4）** — `observability.skill_triage` は findings レーンの行で「実データは `phases.skill_triage` にある」と案内するだけで、指示文（必ず〜せよ）は持たない（observability は実データの観測レーンであって指示の置き場ではない、という分離）。実データ件数は上記のとおり `phases.skill_triage` から読む。

**Weak Signals matrix の読み方（#528-2）**: `observability.weak_signals` の行は「暗黙修正シグナルが N 件（全PJ集計）」の総数行に続けて、**チャネル別×スコープの matrix**（`<ラベル>（<channel>）: 全PJ N / 当PJ未昇格 M` を1行ずつ）を出す。`347 件（全PJ集計）（llm_judge 6）。うち当PJ未昇格 6 件` のような桁混在の散文ではなく、チャネルごとに「全PJ母数」と「当PJ未昇格」を縦に並べた行をそのまま列挙する。昇格導線文は「当PJ未昇格 N 件（うち未読 M 件）」と既読を分離して出る（#525-1） — 未読分だけが今日の修正確認 phase の対象。

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
- `dry_run` の場合: 分類サマリのみ表示（auto_fixable: N / proposable: custom N（個別 {individual} / まとめスキップ {batch_skip}）/ global M（参考値） / manual_required: N）。判定は `proposable_custom` のみで行う

confidence/scope で3カテゴリに動的分類される。各カテゴリの MUST（出力テンプレ・対応 type 一覧は **[references/remediation.md](references/remediation.md)**）:

- **auto_fixable** (confidence ≥ 0.9, scope in file/project): `generate_auto_fix_summaries` でテキスト出力してから AskUserQuestion「一括修正/個別承認/スキップ」（MUST）。**補足説明は Q&A の前に出す（MUST）**。承認分を `FIX_DISPATCH` で実行 → `verify_fix()`+`check_regression()` の2段検証、regression は `rollback_fix()` で manual 格上げ → `record_outcome()` で記録。
- **proposable** (confidence ≥ 0.5, scope != global): `proposable_custom > 0` のときのみ個別承認フロー（MUST）。**ただし confidence で2分割して質問攻めを防ぐ（#377-3）**:
  - **個別承認対象 = `proposable_custom_individual`（conf ≥ 0.7、実体 `classified.proposable_custom_individual[]`）**: 提案詳細プロトコルに従い `generate_proposals` を1件ずつ提示してから AskUserQuestion（MUST）。**補足説明は Q&A の前 / options は最大4択（MUST）**。同 type 複数でも件数に丸めない。
  - **まとめてスキップ対象 = `proposable_custom_batch_skip`（conf < 0.7、実体 `classified.proposable_custom_batch_skip[]`）**: FP 集中帯（hardcoded/duplicate/skill_evolve medium 等）。**デフォルトはスキップ**で「低 confidence の proposable {batch_skip}件をまとめてスキップしました（個別に見る場合は展開可）」と1行表示する。1件ずつ AskUserQuestion を出さない（MUST NOT）。ユーザーが希望した場合のみ提案詳細プロトコルで個別展開する。
  - `proposable_custom_individual == 0`（＝個別対象なし）の場合は AskUserQuestion を出さず、batch_skip の1行表示のみで Step を終える（沈黙≠評価のため、batch_skip が0件でも個別対象0件なら「proposable: 個別対象なし ✓」を残す）。
  - `proposable_custom == 0 かつ proposable_global > 0` は「global のみ {M}件（参考値）— 対応不要」と1行でスキップ。
  - **却下/スキップの記録（べき等性 — 重複提案 MUST NOT、#477）**: 個別承認 AskUserQuestion でユーザーが**却下／スキップ**を選んだ提案は、`record_rejection` で suppression ledger に記録する（dedup_key 単位・TTL45日）。これにより次回 evolve で同じ提案が再出しない（run_evolve が `_apply_remediation_suppression` で却下済みを既に除外し、`remediation.suppressed_by_ledger` 件数を surface する）。**dry-run（`--dry-run`）のときは記録しない（MUST NOT）**。記録対象は「採用しなかった issue dict」（`classified.proposable_custom_individual[]` の要素そのもの）。下記コードで一括記録する（**#479: 直 import は ModuleNotFoundError になるため sys.path 設定込みの完全コードで実行する**）:

    ```python
    import os, sys
    _root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
    sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
    from remediation.suppression_ledger import record_rejection, resolve_slug

    # rejected_issues = ユーザーが却下/スキップした issue dict のリスト（個別承認で不採用にしたもの）
    # dry_run = True のときは下のループを実行しない（MUST NOT — suppression ledger に書かない）
    slug = resolve_slug()  # worktree 安全 slug（git-common-dir の親 basename）
    for issue in rejected_issues:
        record_rejection(issue, slug=slug)  # dedup_key 単位・TTL45日で記録（last-write-wins）
    print(f"suppression ledger: {len(rejected_issues)} 件を却下記録（次回 evolve で再提示しない）")
    ```

    - **決定論 fallback（#494）**: 上の inline 記録を取りこぼしても、run_evolve が remediation phase で `reconcile_surfaced` を毎 run 呼び、解決されないまま連続で個別承認に出続けた提案を閾値回数（既定2）で**自動却下**する安全網がある（`remediation.auto_rejected_by_reconcile` に件数 surface・dry-run 非書込）。これは Step 5.5 の散文 MUST が唯一の却下入口だった構造（却下が永久消失するレーン）を塞ぐためのもの。**それでもユーザーが明示却下した提案は上の record_rejection で即記録するのが正**（fallback は次回以降に効くため、即時抑制は inline 記録が担う）。

#### Step 5.5.1: proposable の line_limit_violation / split_candidate に対する2相品質回復（[ADR-037] Phase 1d-ii）

`fix_line_limit_violation` / `fix_split_candidate` は [ADR-037] で claude -p を全廃し決定論フォールバックで完走する。
承認後に assistant がファイルベース2相（emit→インライン→ingest）で実際の圧縮/分離/分割を行う（MUST）。
対象 issue（line_limit_violation 非rule=圧縮 / rule=分離、split_candidate=分割）の emit/ingest コードは
**[references/remediation.md](references/remediation.md) の Step 5.5.1 節**。`fixed=True` で書込完了、`fixed=False` は手動対応を案内。

**manual_required** (confidence < 0.5, or impact_scope = global):
- 問題の概要、推奨アクション、分類理由を表示のみ

**サマリ**: 「Remediation 完了: N件修正 / M件スキップ / K件ロールバック（要手動対応）」。`remediation.suppressed_by_ledger > 0` のときは「suppression ledger により {S}件を再提示抑制（前回却下・TTL45日内）」を1行追記する（silence != evaluated、#477）。

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

- `reflect_data_count < 0`（degraded sentinel `-1`・#526-3）→ discover が失敗して件数を取得できなかった場合。**数値比較する前に `< 0`（degraded）を先に判定する**（`>= 5` は degraded 値に対して評価しない）。Report には「discover 失敗のため reflect 件数 不明」と表示し、`phases.discover.error` / `phases.discover.traceback`（#521）を root cause として併記する。AskUserQuestion は出さない（件数不明では判断できないため）
- `reflect_data_count >= 5` → AskUserQuestion で `/rl-anything:reflect` の実行を提案する（MUST）
  - question: 「未処理の修正フィードバックが {N} 件あります。/reflect を実行しますか？」
  - options: 「実行する」「スキップ」
- `0 < reflect_data_count < 5` → Report に「未処理修正 {N} 件あり」と表示のみ（Step 10.1 のサマリ掲載と整合）
- `reflect_data_count == 0` → スキップ

### Step 6.1: 初回バックログ bootstrap（#443）

既存の weak_signals バックログ（channel=llm_judge・未昇格）を初回 evolve でまとめて確認する入口。**判定は phase 出力 `result.correction_review.bootstrap` を読むだけで行う（散文ステップで判定しない）。** 機械は「アクティブ PJ」を判定しない — 件数は人間の判断材料として表示するだけ。

- `bootstrap.is_bootstrap != True`（marker 立ち済み or backlog 0 / error）→ **スキップ**（沈黙≠評価のため `bootstrap.is_bootstrap=False` のときのみ「bootstrap: 消化済み ✓」を1行表示）
- `bootstrap.is_bootstrap == True` → **AskUserQuestion で 3 択を人間に選ばせる（MUST — テキスト表示だけで済ませない）**。question に `bootstrap.pj_total` 件・`bootstrap.groups_total` グループを判断材料として提示する:
  - question: 「この PJ の未昇格バックログ {pj_total} 件（{groups_total} グループ）を初回 bootstrap で消化しますか？」
  - options:
    1. **まとめて確認** → `bootstrap.groups` を順に AskUserQuestion バッチで提示（各 group の `representative` を確認 → 承認なら同 group の `signal_keys` を `rl-reflect --promote-weak <signal_keys カンマ区切り>` で一括昇格）。group の `confirmable_idiom` が非 None なら「確定すると idiom『{confirmable_idiom}』も confirmed 化（以後この表現の再発を自動昇格）」を question に添える（None＝過汎用 FP guard #527 で除外済み・standing auto-promote rule にしない・#527-4）。group の `cross_pj_confirmed` が非空なら「他 PJ（{slug一覧}）で承認済み」を question に添える（先頭に並んでいるのはこのため — #462。判断材料の提示のみで自動承認はしない）。CLI が promote と同時に対応 idiom を confirmed=True 化する（#463 — `promote_signals` ライブラリ直接呼びは confirmed 化をバイパスするため使わない）。全 group 確認後に `bootstrap_backlog.mark_done(slug, dry_run=dry_run)` で marker を立てる。
    2. **日次5件ずつ** → marker を立てず、Step 6 の通常 reflect ページネーションに合流（以後の evolve で `is_bootstrap=True` が再提示される）。
    3. **TTL 失効に任せる** → `bootstrap_backlog.mark_done(slug, dry_run=dry_run)` で marker を立てる（以後 bootstrap を再提示しない。weak_signals TTL #5 が残りを間引く）。

`mark_done` は `dry_run=True`（ドライラン実行時）なら marker を書かない（最下層まで dry-run ゲートを貫通）。3 択いずれを選んでも、Skip しても evolve 全体は完走する。

`bootstrap_backlog` は `correction_semantic` パッケージ配下なので、Step 6.5 と同じく sys.path を通してパッケージから import する（`import bootstrap_backlog` 直 import は ModuleNotFoundError になる）:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from correction_semantic import bootstrap_backlog

# #492: slug は phase 出力（build が実際に read に使った slug）をそのまま渡す。
# ここで resolve_slug() を再導出すると、評価が project_dir != cwd や repo subdir / worktree
# から起動された場合に build と別 slug を解決し、marker が別ファイルになって bootstrap が
# 永久再提示される（read/write split-brain）。read=write の slug を構造的に保証する。
slug = result["correction_review"]["bootstrap"]["slug"]

# 「まとめて確認」完了時・「TTL 失効に任せる」選択時のどちらでも呼ぶ。
# dry_run=True（ドライラン実行時）なら marker を書かない。
res = bootstrap_backlog.mark_done(slug, dry_run=dry_run)
# res == {"written": bool, "dry_run": bool, "path": str}
```

### Step 6.2: 今日の修正確認（daily_review・#446）

前回 evolve 以降の**新規** weak_signal（channel=llm_judge・未昇格・非expired・既読集合に無いもの）を idiom 単位で確認する日次入口。reflect SKILL Step 7.7 の散文ステップからの移植（learning_skill_md_must_not_enforcement — 毎日叩かれる evolve の決定論 phase 出力を消費する）。**判定は phase 出力 `result.correction_review.daily` を読むだけで行う。**

**二重提示の解消（#476-3）**: Step 6.1 で `bootstrap.is_bootstrap == True` の run では、daily phase は bootstrap groups が保持する signal_key を自動的に除外して emit する（evolve.py が `exclude_signal_keys` で配線済み）。そのため Step 6.1（まとめて確認）→ Step 6.2 を順に実行しても同じシグナルを 2 回質問しない。`daily.remaining` も bootstrap-pending を除いた「前回以降の新規」だけを数える。

- `daily.eligible != True`（新規 0 件 / error）→ **スキップ**（AskUserQuestion を出さない。`daily.eligible == False` のときのみ「今日の修正確認: 新規なし ✓」を1行表示）
- `daily.eligible == True` → `daily.groups`（最大5件・cross-PJ 承認済み一致が先頭、続いて頻度降順 — #462）を **AskUserQuestion で y/n 確認（MUST — 最大5問を1バッチで）**。各 question に group の `idiom`（無ければ `representative`）と `evidence.count`（再発回数）を提示し、`confirmable_idiom` が非 None なら「『はい』で確定すると以後この表現の再発を自動昇格する idiom『{confirmable_idiom}』も confirmed 化される」を添える（None＝過汎用 FP guard #527 で除外済み・この group の昇格は今回限りで standing auto-promote rule にならない・#527-4）。`cross_pj_confirmed` が非空なら「他 PJ（{slug一覧}）で承認済み」も添える（判断材料の提示のみで自動承認はしない）:
  - **はい（昇格）** → 同 group の `signal_keys` を `rl-reflect --promote-weak <signal_keys カンマ区切り>` で昇格（CLI が promote と同時に対応 idiom を confirmed=True 化し、以後の同テキスト再発は idiom_autopromote が機械昇格する — #463。出力の `confirmed_idioms` 件数で確認可。出力の `corrections_human` は**昇格後**の human-confirmed correction 件数で、Step 9 の成長状態レポート表示はこの最新値を使う — #476-4 対話前スナップショット問題の解消）→ **promote 成功を確認後に** `daily_review.record_reviewed(signal_keys, slug, decision="promoted", dry_run=dry_run)` で既読追記。promote が部分失敗した group は既読追記しない（取りこぼし防止 — 次回再提示される）
  - **いいえ（却下）** → `daily_review.record_reviewed(signal_keys, slug, decision="rejected", dry_run=dry_run)` で既読追記（次回から再提示しない）
  - **Skip / Other / 中断** → 既読追記しない（次回再提示）。evolve 全体は完走する
- `daily.remaining > 0` なら「ほか {remaining} グループは次回以降に提示」を1行表示する

`record_reviewed` は `dry_run=True`（ドライラン実行時）なら既読集合に書かない（最下層まで dry-run ゲートを貫通）。dry-run では確認の表示のみ行い、promote / 既読追記は行わない。

`daily_review` も `correction_semantic` パッケージ配下なので、パッケージから import する（`import daily_review` 直 import は ModuleNotFoundError になる）。`decision` はキーワード専用引数:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from correction_semantic import daily_review
# #492: slug は phase 出力（build_review が実際に read に使った slug）をそのまま渡す。
# resolve_slug() の再導出は read/write split-brain（既読除外不発）の原因になる。
slug = result["correction_review"]["daily"]["slug"]

# promote 成功を確認後に既読追記（はい＝昇格）。decision はキーワード専用。
res = daily_review.record_reviewed(signal_keys, slug, decision="promoted", dry_run=dry_run)
# 却下時は decision="rejected"。
# res == {"written": int, "dry_run": bool}
```

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

#### Global Skills（件数1行 + グローバル文脈の audit へ誘導 — #525-3）
Usage Registry の cross-PJ 使用状況を確認し、既存の `safe_global_check` で処理する。
**表示は冗長にしない（MUST・#525-3）**: global 候補は PJ 単独 evolve では判断材料が不足する（他 PJ での使用状況を見ないと淘汰可否を決められない）ため、**全件を1件ずつ持ち回らない**。

- **件数1行に畳む**: 「Global Skills: 淘汰候補 {N} 件（cross-PJ 使用状況の確認が必要）」と1行だけ surface する（実測で 76 件規模になり、PJ レポートに全件展開すると本来の PJ スコープの提案を埋もれさせる）。
- **グローバル文脈の audit へ誘導**: 個別判断は PJ 横断で見られる `bin/rl-fleet status` / グローバル audit に委ねる旨を1行添える（「全件と判断材料は `bin/rl-fleet status` で確認」）。
- 0 件なら「Global Skills: 淘汰候補なし ✓」を1行残す（silence != evaluated）。

> 補足: prune phase が result に global 候補の全件配列を載せている場合、PJ レポートではその件数だけを使う（配列を1件ずつ展開しない）。配列自体を result から落とす producer 側の最適化は別 issue（prune producer は本 SKILL の所有外）。

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

> **#400 バグ#1 根治**: 旧版は「`--dry-run` の場合は未記録でスキップ」していたが、evolve の
> 標準フローは `rl-evolve --dry-run` で分析 → assistant が Step 3 で対話適用、である。この運用だと
> emit がキューを書かない（dry-run 契約）ため、accept が**永久に記録されず optimize_history が
> 空のまま**だった（fitness_evolution が `0/30` から動かない真因）。修正後は `--dry-run` 分析だった
> 場合でも、**Step 3 の適用が済んだら必ず** ingest を実行する。ingest には result 同梱の
> `result.evolve_decisions.pending`（before_sha 付き）を直接渡し、apply 後のディスク差分から
> accept を決定論で取る（キュー不要）。`--dry-run` はあくまで「分析パスが書き込まない」意味であり、
> その後の対話適用は実変更なので ingest は `dry_run=False` で記録する。

**実行タイミング**: Step 3 の承認・適用フロー完了後に、分析が `--dry-run` だったか否かに関わらず
必ず以下の**単一コマンド**を実行する（#402: inline python をやめ、drain は1コマンドに集約。
これにより「assistant が inline スクリプトを書き損ねる」失敗面を縮める）:

```bash
rl-evolve --drain
```

- `rl-evolve --drain` は marker（`emit_decisions` が `--dry-run` でも記録した `before_sha` 付き
  pending）を読み、Step 3 で**適用済み**（ディスク sha が変わった）提案を accept として
  optimize_history へ記録し、marker をクリアする。**tool 文脈（CLI）で走る**ため reader と同一
  DATA_DIR に書く＝hook/tool の DATA_DIR split（#358）を踏まない。
- result JSON から明示的に渡したい場合は `rl-evolve --drain --result-json <path>`。
- **明示却下がある場合**のみ inline で `ed.drain_pending(rejected={id: 理由})` を使う
  （`--drain` CLI は accept/skip のみ扱い、却下理由は引数で渡す）。`id` は
  `result.evolve_decisions.pending[].id`。
- **enforcement の保険（#402）**: drain を忘れても、適用済みで未 drain な提案があれば
  **次回 SessionStart で `restore_state` が `rl-evolve --drain` を促すリマインド**を出す
  （`undrained_applied` が marker の before_sha と現ディスク sha を突合、store 非依存で #358 回避）。
  drain 実行で marker が消えてリマインドは自然終息する。これで `SKILL.md MUST ≠ enforcement`
  の穴を「単一コマンド化 + 決定論リマインド」で塞ぐ。

- 何も適用せず（純粋プレビュー）何も却下しなければ、全件が skip に落ち記録されない（self-correcting）
- accept/reject は `record_evolve_diff_decision` 経由で optimize_history（ADR-031）へ冪等記録され、
  fitness_evolution の相関母集団 / `check_calibration_regression` の入力になる
- **決定論 weak_signals の永続化も同居（#484）**: `rl-evolve --drain` は同じ apply 境界で
  決定論3チャネル（manual_edit_after_ai / esc_interrupt / rephrase）+ permission_deny を
  `persist_weak_signals_drain` で weak_signals.jsonl へ永続化する。理由は #400 と同型の盲点:
  標準フローは `rl-evolve --dry-run` 分析なので run_evolve 内の `run_batch(dry_run=True)` は
  #491 契約で常にゼロ書き込みになり、決定論チャネルが**実 PJ で一度も永続化されない**
  （llm_judge だけが Phase B/C の apply 側で書かれて存在していた）。検出は冪等（signal_key
  dedup）なので tool 文脈・非 dry-run の drain で書くのが正。結果は drain サマリの
  `weak_signals_persisted`（detected/written/skipped_dup）で surface される。
- 結果（accepted/rejected/skipped 件数）を Report に報告する。`accepted >= 1` なら
  「fitness 母集団に +N 件記録 ✓」と1行 surface する（silence != evaluated）

### Step 8: Fitness Evolution — 評価関数の改善チェック

evolve.py の出力に含まれる `fitness_evolution` フェーズを確認する。

- `status: "insufficient_data"` の場合（**結論を1行で締める** — #400 バグ#5）:
  - **役割1行を先に添える（#528-1・誤読防止 MUST）**: `has_fitness: true` かつ
    `structural_reason` がある（提案が構造的に出ない PJ）のときは、next_action の前に
    **「fitness 関数自体は rl-optimize / rl-loop-orchestrator 実行時に評価に使用中。evolve 日次
    ループでは skill 提案が出ない限り calibration 母集団は貯まらない」** を1行出す。これを省くと
    次の next_action「fitness は使わない設計」が**fitness 関数全体の否定**に読めてしまう（#525-1）。
    対象外なのは **calibration（accept/reject 蓄積による再調整）だけ**であって、fitness 関数の
    評価利用ではない、という区別を明示する。
  - **`result.phases.fitness_evolution.next_action` を最終行にそのまま出す**（MUST）。これが結論。
    evolve.py が現 run の提案有無で確定済みの1行で、長文説明より優先する:
    - 提案あり → 「放置でOK — evolve を継続すれば skill 提案の accept/reject が自動で母集団に貯まる（ADR-041）」
    - 提案なし → 「このPJでは fitness は使わない設計。対応不要（提案が構造的に出ないため母集団は貯まらない）」
      （↑上の役割1行とセットで読ませること。単独で出すと全体否定に誤読される — #525-1）
  - **件数行（#526-4・MUST）**: `structural_reason == "skill_evolve_not_scored"` がある
    insufficient_data では **件数行（`データ不足（N/30件）`）を省く**。理由: structural ケースでは
    `data_count` が構造的に 0 固定になりやすく（提案が出て初めて母集団が積み上がる）、`0/30` を
    出すと「あと 30 件貯めれば判定できる」という蓄積前提の誤読を生む（分子が意味を持たない）。
    `structural_reason` が無い通常の insufficient_data のときだけ `Fitness Evolution: データ不足
    （{data_count}/30件）` の1行を添える（`data_count` は producer が常に int で返す。`null` 穴あき
    表示は出さない）。
  - **3段の長文説明は出さない**（`message` フィールドの詳細を出すのはユーザーが理由を尋ねた時のみ）。
    冗長な説明で次アクションを埋もれさせない＝issue #400 バグ#5 の指摘。
  - 0件の場合（`data_count == 0`・structural でない）は「このプロジェクトではまだ一度も評価が
    蓄積されていません」を件数行に添えてよい
  - **整合（#479）**: `structural_reason`（`skill_evolve_not_scored`）がある insufficient_data では、
    `next_action`・上の役割1行・observability の `calibration_drift` 行（「構造的に対象外」に切替）・
    Step 2 の `has_fitness` 表示の4箇所が「提案が出て初めて母集団が貯まる＝calibration は構造的に
    対象外になり得る（fitness 関数の評価利用は継続）」で揃う。calibration_drift を
    「あと N 件で判定可能」と蓄積前提で言い直さない（矛盾するため）。
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

**TL;DR を冒頭に必ず出す（MUST・#525-2）**: レポートの一番上に、3 つの数字を1行で出す。詳細セクションを全部読まなくても「今回の evolve で何が起きたか」が即わかるようにするため。

```
TL;DR: 変更 {N} 件 / 要対応 {M} 件 / 残りすべて評価済みクリーン
```

- **変更 N 件**: 今回 evolve で実際にファイルへ適用した件数（skill diff / remediation fix / memory 書込 / 昇格など、apply 実績の合算）。dry-run 分析のみで何も適用していなければ 0。
- **要対応 M 件**: Step 10 の「🔴 要対応（実行コマンドあり）」の件数。
- **残りすべて評価済みクリーン**: 上記以外の observability 項目（全 ✓ のもの）。

**全 ✓ の observability 項目は1ブロックに畳む（MUST・#525-2）**: Step 3.8 で surface する observability の各 key のうち、「✓ クリーン（該当なし / drift なし）」だけのものは個別に1行ずつ展開せず、まとめて1行に畳む:

```
✓ クリーン: glossary / orphan_store / store_contract / hook_drift / agent_team / measurement_bug / promotion_readiness / testpaths_coverage
```

⚠ や ℹ（要注意・データ不足・要対応）を含む項目だけ個別に1行 surface する。これにより「全部 ✓ なのに項目数だけ多くて読みづらい」を防ぐ。**畳んでよいのは clean のものだけ**で、silence != evaluated は「✓ クリーン: ...」のブロックに名前を残すことで担保する（評価したことは見える）。

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
> evolve.py 出力 JSON のトップレベル `result["env_score"]` は**構造化 dict**（#523-2/#526-2 で配線）。
> `degraded: false` なら `level` / `title_ja` / `title_en` をそのまま使い（compute_level は解決済み）、
> 成功時のみ `save_world_context` で world-context.json に保存して（SLUG は Step 0.5 と同じ PJ 別スコープ値）、
> レベルアップ / 変化なし / 初回のいずれかでナレーションする。`degraded: true`（算出失敗）の場合は
> **黙らず**「env_score: 取得失敗（前回 Lv.N・world-context.json から）」を 1 行で surface する
> （silence != evaluated の自己適用）。ワンライナー・分岐文言の詳細は
> **[references/report-narration.md](references/report-narration.md)**。

> **成長状態レポート（#448）:**
> 成長レベル表示の直後に `result["growth_report"]` の `lines` を列挙する（MUST）。
> `growth_report` キーが存在しない / `error` キーが含まれる場合は表示をスキップ。
> `lines` が空リストの場合は「成長状態: データ不足」を 1 行表示する。
> 表示例:
> - `corrections（human-confirmed のみ）7/10 — あと3件で構造化育成へ`
> - `本日累計 reflect 確認 2件 / idiom 1件 が自動化対象に昇格（このrunでは 1 件）`
>
> **対話前スナップショット問題の補正（#476-4・MUST。全PJ値の混入を断つ — #526-1）:** `growth_report` は analysis 時点で生成されるため `corrections_human` / `promoted_today` は **Step 6.2 の対話で昇格する前の値**で固定される。Step 6.2 で実際に昇格した場合の上書きは、必ず **per-PJ の値に今回昇格数を加算する** 方式で行う（**`rl-reflect --promote-weak` 出力の `corrections_human` をそのまま使ってはならない — MUST NOT**）:
>
> - **`corrections（human-confirmed のみ）` 行**: `result["growth_report"]["corrections_human"]`（= 当PJ analysis 時点の値）に、Step 6.2 で「はい」と答えて昇格に成功した件数を**足した値**を分子にする（分母は `corrections_target`）。
>   - ⚠ **`rl-reflect --promote-weak` の出力 `corrections_human` は全PJ合計（例 41）を返す**ため、これで当PJ値（例 0/10）を上書きすると `41/10` という不整合表示になる（#526-1 の事故）。CLI 出力の `corrections_human` は当PJ分母 `/10` と意味が合わないので分子に使わない。
> - **`本日累計 ...（このrunでは M 件）` 行**: growth_report の `promoted_today` / `autopromoted_today`（本日累計・store 由来）と、`promoted_this_run` / `autopromoted_this_run`（このrun・明示渡し）をそのまま使う。Step 6.2 で承認した直後で store がまだ反映前なら、このrun件数を本日累計に足して表示してよい。
> - 昇格が 0 件だった場合は growth_report の値をそのまま表示する。
>
> `corrections（human-confirmed のみ）` は reflect 承認 / idiom_dict 自動昇格のみを数えた **当PJ** の数で、prune の `corrections kept`（全 correction を数える）とも、CLI の全PJ集計とも別物（行内の `（human-confirmed のみ）` ラベルと「当PJ」スコープで区別する）。

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

- **10.1 Reflect**: `reflect_data_count < 0`（degraded sentinel `-1`・#526-3）→ 🟡「Reflect: discover 失敗のため reflect 件数 不明」（数値比較の前に `< 0` を判定する）/ `>= 1` → 🔴 `/rl-anything:reflect`（未処理 {N}件）/ 0 → 「Reflect: 未処理なし」
- **10.2 ツール使用**: `installed_artifacts` + `tool_usage_patterns` を対策済み/未対策で切替（Built-in代替/sleep/Bash割合の閾値判定 + 前回比トレンド）
- **10.3 自己進化**: 自己進化済みスキル数・pitfall 統計・卒業/剪定フラグ・根本原因横断分析を表示（0 件なら「対象スキルなし」）
- **10.4 Workflow Checkpoint Gaps**: `workflow_checkpoint_gaps` をテーブル表示 / なければ「ギャップなし」
- **10.5 Process Stall Patterns**: `stall_recovery_patterns` をテーブル表示 / なければ「検出なし」
- **10.6 Remediation サマリ**: auto_fixable / manual_required / `proposable_custom_individual` ≥1 を 🔴 要対応に、`proposable_custom_batch_skip`（低 confidence・まとめスキップ済み）と proposable_global のみは 🟡 情報に反映（#377-3）

### Step 11: 自己解析 → issue 半自動起票（MUST — #299）

evolve は他フェーズで対象 PJ を改善するが、**evolve 自身の実行結果**（提案の質・実行時エラー・改善余地）を
振り返る経路がこれまで無かった（「install ≠ enforcement」と同型の配線漏れ）。このステップで evolve の `result` を
自己解析し、検出した候補を**人間承認のうえ GitHub issue 化**してメタ層のループを閉じる。

evolve.py 出力トップレベル `self_analysis`（`analyze_evolve_result` が決定論生成・LLM 非依存）を読む。3カテゴリ
（`self_detection` / `runtime_errors` / `improvement_opportunities`、各 `{candidates, summary_line}`）+ `total_candidates`。
各 candidate: `{category, title, body, suggested_label, dedup_key, severity}`。

**実コードは `evolve_introspect` モジュール（#529-3）**: dedup / body 生成の関数は
`from evolve_introspect import flatten_candidates, filter_duplicates, render_issue_body` 等で読む
（詳細: [references/self-analysis.md](references/self-analysis.md)）。**`self_analysis` という名前のモジュールは
存在しない** — トップレベル result キー名（`self_analysis`）からモジュール名を `self_analysis` と誤推測すると
`ModuleNotFoundError` になる（実モジュールは `evolve_introspect.py`）。

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
