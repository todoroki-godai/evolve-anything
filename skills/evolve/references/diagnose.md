# Diagnose ステージ詳細（Step 1 / 3 / 3.5 / 3.7 / 3.8 / 4）

SKILL.md 側には各 Step の見出し・要点・MUST の1行要約のみを残してある。ここは表示テンプレ・分岐条件・コードの正準。

## Step 1: データ十分性チェック（observe 先行 pre-flight）

まず **`--observe-first`** で安価な observe + fitness ゲートだけを算出する（数秒で返る）。重いフェーズ（discover/audit/skill_evolve/remediation/prune…）はここでは回さない。これにより lightweight/skip の分岐が「フル分析コスト（dry-run で数十秒〜1 分、[ADR-037] LLM-free 化以降の実測・#479）を払う前」に効く（#407）。

```bash
evolve-usage-log "evolve"
PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス（bash は呼び出しごとに独立プロセスのため各ブロック冒頭で束縛する。
                     # env の PJ があれば優先・無ければ cwd。バッチ経路 #400 本体では呼び出し側が
                     # PJ を env で渡すだけで対応できる）
# 出力は PJ 別パスに書く（共有固定パスだと別 PJ の stale 出力を誤読する, #408-A）。
# #525-3: evolve が slug 解決済みの OUT パスを返すので SLUG/OUT 再導出を1コマンドに短縮。
OUT="$(evolve --project-dir "$PJ" --print-out-path)"
evolve --project-dir "$PJ" --dry-run --observe-first --output "$OUT"
```

⚠️ **`--output` は必須（MUST）**: result JSON はフェーズ全部入りで数十〜数百 KB になる。`--output` を付けると full JSON は `$OUT`（`/tmp/rl_evolve_<slug>.json`）に書かれ、stdout には `{"output": "...", "slug": ..., "generated_at": ..., "phases": [...], "env_tier": ...}` の **1行サマリ**だけが出る。

⚠️ **slug 照合は MUST（#408-B）**: `$OUT` を Read したら、まずトップレベルの `slug` / `project_dir` / `generated_at` を確認し、**対象 PJ と一致するか検証してから** Diagnose に進む。一致しなければ stale/別 PJ の出力なので使わず再実行する。以降このスキルで「evolve.py の出力に含まれる `X` フェーズを確認する」と書かれた箇所は、すべて **`$OUT` を Read（必要なら offset/limit で該当フェーズだけ）して参照する**。`evolve` の stdout を `| head` / `| tail` で削ったり Bash の出力をそのまま読もうとしてはならない（MUST NOT）。`indent=2` の巨大 JSON が途中で切れて invalid になり「JSON が不完全 → 全量を保存し直し」のやり直しが多発する（これが本フローを設計した理由）。

- 出力（`$OUT` の）`observe` フェーズの `action` で分岐する:
  - `action: "backfill_recommended"`（テレメトリ未取得＝初回導入直後、`telemetry_empty: true`）の場合:
    - 「テレメトリが空。先に /evolve-anything:backfill で既存セッション履歴を取り込んでください」と案内する（MUST）
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
    PJ="${PJ:-$(pwd)}"  # 対象 PJ の絶対パス（各ブロック冒頭で束縛。env の PJ があれば優先・無ければ cwd）
    OUT="$(evolve --project-dir "$PJ" --print-out-path)"
    evolve --project-dir "$PJ" --dry-run --output "$OUT"
    ```
  - 完了後、`$OUT`（=`/tmp/rl_evolve_<slug>.json`）を Read して再度 slug を照合し、Step 2 以降へ進む。

## Step 3: Discover フェーズ（enrich 統合済み）

パターン検出結果を表示。候補があれば生成を提案。

`tool_usage_patterns` が結果に含まれる場合、以下を追加表示:
- **Built-in 代替可能**: 件数と上位パターン（例: `cat → Read: 12回`）をルール候補として提案
- **繰り返しパターン**: 上位パターンとサブカテゴリをスキル候補として提案
- **Bash 割合**: 全ツール呼び出し数と Bash の割合（例: `Bash: 31.8% (127/400)`）

**`phases.discover.rule_violation_observed`（list、#522-3）が存在する場合は別レーンとして surface する（MUST）**: 既存 rules で禁止済みのコマンド（例: `cd` 禁止なのに 626 回観測）は「スキル候補」ではなく**ルール導入済みだが実行が止まっていない違反観測**（rule installed != enforced）として、`violated_command` / `count` / `recommendation`（hook enforce 検討）を1行ずつ表示する。これらは repeating_patterns から除外済みのためスキル候補としては提案しない。違反ゼロ時はキーが欠落するので省略してよい。この list は既に dismiss 済み（意図的運用として `record_rejection` 記録・TTL 内）の違反を除外した surface 対象で、畳んだ件数は `remediation.rule_violation_suppressed` に出る（#103）。「この PJ では意図的運用」と判断したら Step 5.5 の情報レーン dismiss で以後抑制できる。

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

> **一言メモ — Discover / Diagnose 完了後**: 発見パターン数（`unmatched_patterns` + `matched_skills`）に応じた1文を出力する（文言は [report-narration.md](report-narration.md)）。

## Step 3.5: レイヤー別診断

evolve.py の出力に含まれる `layer_diagnose` フェーズ結果を確認する。
`diagnose_all_layers()` は Rules / Memory / Hooks / CLAUDE.md の4レイヤーを診断し、issue リストを返す。

各レイヤーの結果を表示:
- `rules`: `orphan_rule`（孤立ルール）、`stale_rule`（参照先不在）
- `memory`: `stale_memory`（陳腐化エントリ）、`memory_duplicate`（重複セクション）
- `hooks`: `hooks_unconfigured`（hooks 設定なし）
- `claudemd`: `claudemd_phantom_ref`（幻影参照）、`claudemd_missing_section`（セクション欠落）

issue があれば Compile ステージの remediation で対処する。

## Step 3.7: Audit 問題検出

evolve.py の出力に含まれる audit の `collect_issues()` 結果を確認し、問題リストを Compile ステージに渡す。
（collect_issues() 内で layer_diagnose も統合されている）

evolve の audit は **`memory_trace=True` / `constitutional_score=True` 既定**で実行される。これにより MemTrace 帰属診断（決定論・LLM ゼロ）と slop_detector を 10% ブレンドした constitutional スコアが「evolve するだけ」で出力に乗る。[ADR-037] により audit 本体は claude -p を呼ばず cache（`constitutional_cache.json` / `principles.json`）を読むだけ。CLAUDE.md/Rules を変えた直後など constitutional cache を最新化したいときは、audit SKILL の **Step 3.5（principles round → constitutional round の2相）**を先に回してから evolve する（インライン採点＝subscription 課金）。cache が新しければ 0 コールで済む。
discover の `tool_usage_rule_candidate` / `tool_usage_hook_candidate`、skill_evolve の `skill_evolve_candidate`、および `verification_rule_candidate`（検証知見カタログ）も issue リストに統合される。

## Step 3.8: Observability（必ず surface する — MUST）

evolve.py 出力の **トップレベル `observability` フィールド**（`unmanaged_pitfalls` / `glossary_drift` 等の key → 行リスト）を、各 key の行を**そのまま必ずサマリに列挙する**。clean（「✓ 評価したが該当なし」）でも省略しない。

理由: これらは `phases.audit.report` の 217KB markdown 中盤にも出ているが、選択読みでは埋もれて surface されない（silence != evaluated の配線漏れが #272 後に再発した実例）。`observability` フィールドは audit↔evolve の契約として構造化済みなので、**markdown 側の該当行を探さず、この構造化フィールドを正準ソースとして出す**。`{"error": ...}` のときはエラーをそのまま表示する。

**Triage SKIP 抑制サマリ（#308、必ず1行 surface する — MUST）**: `phases.skill_triage.skip_suppressed_summary`（例: `SKIP 抑制 2件 ✓`）を**そのまま1行表示する**。0件でも省略しない（silence != evaluated）。これは過去に SKIP と判断したスキル候補のうち、クールダウン内で再発したため個別表示を畳んだ件数。なお `phases.skill_triage.REVIEW`（再発エスカレーション昇格）や `ledger_status == "ttl_expired"`（🔄 強制再評価）の候補は通常どおり個別 surface される — 抑制対象は「前回判断を維持中の SKIP」のみ。

**Triage アクションサマリ（#478 / #528-4、必ず surface する — MUST）**: `phases.skill_triage` の `CREATE` / `UPDATE` / `SPLIT` / `MERGE` 各リストの**件数と上位候補（skill 名 + confidence）をサマリ表示する**。特に **CREATE（trajectory 由来の新スキル候補）は埋没厳禁** — 過去は remediation の低 confidence batch_skip の1行に畳まれてユーザーに提示されなかった（#478）。各アクション0件でも「CREATE: 0件 ✓」のように省略せず1行残す（silence != evaluated）。**この表示指示（MUST）の置き場はこの SKILL.md である（#528-4）** — `observability.skill_triage` は findings レーンの行で「実データは `phases.skill_triage` にある」と案内するだけで、指示文（必ず〜せよ）は持たない（observability は実データの観測レーンであって指示の置き場ではない、という分離）。実データ件数は上記のとおり `phases.skill_triage` から読む。

**Weak Signals matrix の読み方（#528-2）**: `observability.weak_signals` の行は「暗黙修正シグナルが N 件（全PJ集計）」の総数行に続けて、**チャネル別×スコープの matrix**（`<ラベル>（<channel>）: 全PJ N / 当PJ未昇格 M` を1行ずつ）を出す。`347 件（全PJ集計）（llm_judge 6）。うち当PJ未昇格 6 件` のような桁混在の散文ではなく、チャネルごとに「全PJ母数」と「当PJ未昇格」を縦に並べた行をそのまま列挙する。昇格導線文は「当PJ未昇格 N 件（うち未読 M 件）」と既読を分離して出る（#525-1） — 未読分だけが今日の修正確認 phase の対象。

## Step 4: Reorganize フェーズ（split 検出 + 階層統合提案）

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
