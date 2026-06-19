# Issues Archive (migrated reference)

_scrubbed archive — 225 threads_


---

## #1 analyze.py がプロジェクトフィルタなしで全データを分析する  `[closed]`

## 概要

`/rl-anything:backfill` 実行時、backfill スクリプトは `--project-dir` で指定されたプロジェクトのセッションのみを処理するが、後続の `analyze.py` は `sessions.jsonl` / `usage.jsonl` / `workflows.jsonl` を**フィルタなしで全件読み込む**ため、他プロジェクトのバックフィルデータも混在した分析結果になる。

## 再現手順

1. プロジェクトA で `/rl-anything:backfill` を実行（例: 339セッション蓄積済み）
2. プロジェクトB で `/rl-anything:backfill` を実行（例: 170セッション）
3. プロジェクトB の分析レポートに、プロジェクトA のデータも含まれる（合計581セッションとして表示）

## 期待する動作

`analyze.py` が `--project` オプション（またはデフォルトでカレントプロジェクト名）を受け取り、該当プロジェクトの `project_name` でフィルタした結果のみを分析・表示する。

## 現状の動作

- `backfill.py`: `--project-dir` でスコープされたセッションのみ処理 ✅
- `analyze.py`: JSONL を全件読み込み、プロジェクトフィルタなし ❌

## 提案

- `analyze.py` に `--project <name>` 引数を追加
- デフォルトはカレントディレクトリのプロジェクト名（`backfill.py` と同じ `project_name_from_dir()` ロジック）
- `sessions.jsonl` / `usage.jsonl` / `workflows.jsonl` の各レコードを `project_name` フィールドでフィルタ
- SKILL.md の Step 2 コマンドも `--project-dir` を渡すよう更新

> 💬 comment:
>
> v0.4.0 (commit d42b7a9) で対応済み。analyze.py にプロジェクトフィルタ機能を追加し、OpenSpec もアーカイブ済み (1ec5973)。

---

## #2 [Feedback] 機能提案: Merge suppression for prune phase  `[closed]`

## フィードバック

**カテゴリ**: 機能提案
**コンポーネント**: prune / evolve
**満足度**: 4/5

## 詳細

### 問題

evolve の Prune フェーズ（Merge サブステップ）で却下した統合候補ペアが、
次回 evolve 実行時に再度提案される。

現在の `discover-suppression.jsonl` は discover.py のパターン検出
（behavior/error/rejection）にのみ効果があり、
prune の `merge_proposals`（TF-IDF duplicate_candidates 由来）には適用されない。

### 期待する動作

- Merge 提案を却下した場合、当該ペアを suppression に登録し、
  次回以降の evolve で再提案しない
- suppression は discover と prune/merge で共有、
  または merge 専用の suppression を持つ

### ユースケース

意図的に分離しているスキル（例: プロジェクト固有 vs 汎用）が、
語彙の類似度で毎回統合候補に出てしまい、手動スキップが必要になる。

---
*Submitted via /rl-anything:feedback*

---

## #3 [Feedback] Bug: Merge duplicate_candidates produces 465 false-positive proposals  `[closed]`

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: prune (merge / duplicate_candidates)
**満足度**: 2/5

## 詳細

`evolve --dry-run` 実行時、`prune.merge_result.merge_proposals` が **465件** の統合提案を返した。
ほぼ全てが誤検知（false positive）。

### 再現状況
- プロジェクト内に約30スキル（project + global + plugin）
- `duplicate_candidates` の閾値 0.8 で、明らかに無関係なスキル同士が重複と判定される
  - 例: `refresh-aws-secrets` ↔ `mailpit-test`、`add-repo` ↔ `aws-cdk-deploy` 等
- 結果として N×N に近い組み合わせが全て proposed になる

### 期待動作
- 閾値 0.8 であれば、本当に内容が類似したスキルペアのみが候補に挙がるべき
- 465件の提案は実用上レビュー不可能

### 推定原因
- 類似度計算ロジック（TF-IDF / Jaccard 等）がスキルの短いメタデータだけを比較している可能性
- または閾値の意味が逆転している（0.8以上ではなく0.8以下を拾っている等）

---
*Submitted via /rl-anything:feedback*

> 💬 comment:
>
> fix(merge): similarity engine で誤検知 465 件を解消 + フォールバック安全側統一 (v0.15.1) にて修正済み。commit: 32e0dd2

---

## #4 [Feedback] 機能提案: evolve Merge フェーズの偽陽性フィルタリング改善  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: 機能提案
**コンポーネント**: evolve
**満足度**: 3/5

## 詳細

### 問題: Merge フェーズで偽陽性が大量発生

`evolve` パイプラインの Merge フェーズで、`reorganize.merge_groups` のクラスタ内スキル全ペアに対して
C(N,2) の統合提案を生成するため、同じ語彙を共有するが異なる責務のスキル群から大量の偽陽性が発生する。

### 再現手順

1. 7つのスキルが同じドメイン語彙（"docs", "handbook", "project" 等）を共有
2. reorganize が1クラスタにまとめる（類似度 0.4989）
3. merge_duplicates() が C(7,2)=21 ペアの統合提案を生成
4. 実際に妥当だったのは類似度 0.81 の1ペアのみ（偽陽性率 95%）

### 提案

- クラスタ類似度だけでなく、スキルの「責務の類似性」（description やトリガーの重複度）で追加フィルタリング
- 類似度閾値を引き上げるか、ペア単位の類似度チェックを追加
- 大きなクラスタ（N >= 5）では全ペアではなく duplicate_candidates のみ処理

---
*Submitted via /rl-anything:feedback*

> 💬 comment:
>
> 対応すみ

---

## #5 [Feedback] 改善要望: merge閾値が厳しすぎ — reorganize検出ペアの対話的統合提案  `[open]`  (feedback)

## フィードバック

**カテゴリ**: 改善要望
**コンポーネント**: evolve (prune/merge)
**満足度**: 3/5

## 詳細

### 問題

reorganize フェーズで類似度 0.536（8組中2位）と検出されたスキルペアが、
merge 判定では `skipped_low_similarity` になり、統合が提案されなかった。

ユーザーが手動で統合を判断・実行したところ、重複整理で大幅に行数削減でき、
実際に統合は妥当だった。

### 現状の挙動

1. reorganize が `merge_groups` として類似度の高いペアを検出
2. prune の `merge_duplicates()` が再評価し、閾値未満として `skipped_low_similarity` で除外
3. ユーザーには統合候補として情報表示されるが、対話的な統合提案は行われない

### 提案

**Prune/Merge フェーズで、reorganize の merge_groups に対して
「統合してよいか？」の確認ステップを自動提案する。**

具体的には:
- reorganize で類似度が一定以上（例: 0.4+）の merge_groups について、
  merge_duplicates の閾値に関わらず AskUserQuestion で統合を提案する
- ユーザーが承認した場合のみ、Claude がスキル内容を読み込んで統合版を生成
- ユーザーが却下した場合は merge suppression に登録

これにより、機械的な閾値判定だけでなく、
ユーザーのドメイン知識を活かした統合判断が可能になる。

---
*Submitted via /rl-anything:feedback*

---

## #12 feat(memory): APEX-MEM A++ temporal validity + provenance + query-time filter  `[open]`

APEX-MEM（arXiv:2604.14362）の概念を rl-anything メモリシステムに適用する。

## 背景

現在の memory ファイルには時間的追跡がなく corrections.jsonl との因果チェーンが切れている。

- 3ヶ月前のルールが今も有効かどうか分からない
- memory エントリがどの corrections から生まれたか辿れない
- decay_days は corrections.jsonl にあるが memory ファイルにはない
- superseded されたエントリも system prompt に全量注入される

## 実装スコープ (A++)

1. memory frontmatter に valid_from / superseded_at / decay_days / source_correction_ids 追加（前方互換）
2. instructions_loaded hook で superseded エントリを system prompt 注入から除外
3. reflect.py が memory 作成時に source_correction_ids を記録（単方向リンク）
4. audit.py で stale memory 検出（decay_days 超過 / sources が reflect 済み）
5. sessions.jsonl に correction_count フィールド追加

## opt-in

userConfig: temporal_memory=true で有効化（fleet 安全側）

## 実装しないもの（将来 issue）

Event-Centric Rewrite (Approach C) は fleet Phase 2/3 完成後に別 issue で設計。

## 参考

- APEX-MEM: https://arxiv.org/abs/2604.14362
- 設計ドック: ~/.gstack/projects/evolve-anything/todoroki-main-design-20260426-220536.md

---

## #13 feat(memory): APEX-MEM Approach C — Event-Centric fleet memory graph (future)  `[closed]`

## 概要

APEX-MEM A++ (#12) の次フェーズ。
6ノード型オントロジーによる Event-Centric メモリグラフとして rl-anything を再設計する。

## 前提条件（このIssueを着手する前に必要なもの）

- fleet Phase 2/3 完成（bin/rl-fleet evolve-all / audit-all）
- CC auto-memory の代替設計（MEMORY.md 廃止の許可）
- #12 (A++) の実装・運用実績

## 設計概要

### 6ノード型オントロジー

- Rule: CLAUDE.md / rules に存在するルール
- Skill: .claude/skills/ のスキル定義
- Correction: corrections.jsonl のエントリ
- Session: sessions.jsonl のセッション
- Pitfall: pitfalls.md のエントリ
- Memory: auto-memory の memory ファイル

### アーキテクチャ

- JSONL グラフとして管理（前方互換 JSONL）
- duckdb でクエリ（telemetry_query.py 基盤を流用）
- instructions_loaded の静的フィルタ → ReAct 型クエリ時解決エージェントに置き換え
- fleet memory graph: 全 PJ の memory を横断観測

### APEX-MEM との対応

- イベント中心パラダイム: Session がイベントの first-class citizen
- クエリ時解決: ReAct ループ（SchemaViewer / EntityLookup / GraphSQL / Search の簡略版）
- 35クラス → 6ノードに縮小

## 設計ドック

~/.gstack/projects/evolve-anything/todoroki-main-design-20260426-220536.md

## 参考

- APEX-MEM: https://arxiv.org/abs/2604.14362
- A++ 実装: #12

---

## #15 [Feedback] 機能提案: 技術・ツール評価スキル「tech-eval」の新規作成  `[closed]`

## フィードバック

**カテゴリ**: 機能提案
**コンポーネント**: その他（新規スキル）
**満足度**: 3/5

## 詳細

技術・ツールの「使えそう？」という問いに対して、
現状のLLMはツール導入可否（インフラ互換性等）の浅いレイヤーで回答しがち。

### 欲しいスキル: /tech-eval

入力（ツール名 / URL / スライド / 論文）を受け取り、以下を実行する:

1. 技術概念の分解 — ツール自体でなく、その背後にあるアルゴリズム・設計パターンを抽出
2. PJ現行実装との照合 — コードベースをgrepして実装済み/未実装を判定
3. ギャップ整理 — 「実装済み / 未実装だが有効 / 不適合」の3分類で出力
4. GitHub Issue 自動作成 — 確認事項として整理

### 期待するアウトプット形式

| 概念 | 既存実装 | ギャップ | 採用推奨度 |
|------|---------|---------|-----------|
| RRF  | ✅ 実装済み | なし | 不要 |
| AST分割 | ❌ 未実装 | あり | 中 |

### トリガーワード案
「この技術、使えそう？」「概念を評価して」「PJに取り入れる価値があるか」/tech-eval

---
*Submitted via /rl-anything:feedback*

> 💬 comment:
>
> ## 実装完了（グローバルスキルとして配置）
> 
> tech-eval の実装方針を検討した結果、**rl-anything プラグインではなくグローバルスキル（`~/.claude/skills/tech-eval/`）として配置**しました。
> 
> ### 理由
> - tech-eval は「任意の PJ のコードベースを評価する」汎用ツールであり、rl-anything の4つの柱（自律進化/フィードバック/直接パッチ最適化/fleet）と直交する機能
> - `/rl-anything:tech-eval` という namespace より `/tech-eval` の方が呼び出しがシンプルで、rl-anything 非導入プロジェクトでも使える
> 
> ### 実装内容
> `~/.claude/skills/tech-eval/SKILL.md` として配置済み。以下の6ステップで動作:
> 1. 入力種別判定（URL / ツール名 / テキスト）
> 2. 技術概念を 3〜10 個に分解（アルゴリズム・設計パターン）
> 3. コードベースを grep して ✅ / 🔶 / ❌ の3分類で照合
> 4. 採用推奨度（不要 / 低 / 中 / 高）を算定
> 5. Markdown テーブルレポートを出力
> 6. 推奨度「高/中」の概念について GitHub Issue 作成を提案（任意）
> 

---

## #20 feat: subagent 乱立によるトークン消費爆発の検知・抑制機能  `[closed]`

## 背景

2026-05-08 に `suggest_subagent_delegation.py` の test-suite パターンが原因で
「pytest → 提案 → subagent 生成 → subagent も pytest → ...」のカスケードループが発生し、
トークンを大量消費する事故が発生した（fix は #前コミット で対応済み）。

同様の事故を今後防ぐため、セッション内の subagent 乱立を検知・抑制する仕組みを追加したい。

## やりたいこと

### Layer 1: グローバル rule（Claude への指示）

`~/.claude/rules/` に subagent 生成を抑制する rule を追加する。

- subagent を連続生成する前にユーザー確認を求める
- 同一セッションで生成できる subagent 数に上限を設ける（例: 3個まで）

### Layer 2: hook（自動監視）

`SubagentStop` hook（`subagent_observe.py`）を拡張して、セッション内 subagent 数を計測する。

- セッション内 subagent 数が閾値（例: 5個）を超えたら `systemMessage` で警告
- 警告内容: 「このセッションで X 個の subagent が生成されています。意図しないループが発生していないか確認してください」

### 実装メモ

- `subagents.jsonl` には既に session_id + timestamp が記録されている
- `subagent_observe.py` の末尾に計測ロジックを追加するだけで実装可能
- 閾値は `userConfig` で設定可能にする

## 優先度

Medium（再発防止だが、今回の根本原因は既に修正済みのため緊急ではない）

---

## #22 fleet MVP-D: growth-state issues_summary + subagents.jsonl token-load 集計  `[closed]`  (enhancement)

## 背景

「PC環境を診断して」フローで現 PJ の audit に加えて他 PJ の問題（特に token 消費の重い処理）も surface したい。ADR-022 の Phase 2/3 フル実装は規模大なため、より軽量な MVP-D としてキャッシュ拡張で目的を達成する。

ADR-022 の Phase 2/3 (`audit-all` / `reflect-all` / `evolve-all`) はそのまま残し、Phase 1 と Phase 2 の間に MVP-D を入れる位置づけ。

## 確定アーキテクチャ

### データフロー

\`\`\`
[per-PJ audit run]                          [shared global]
  audit.py                                    subagents.jsonl
    └── compute_issues_summary()                  (1024+ entries, project field 付き)
         └── write to growth-state-<slug>.json         │
                  │                                    │
                  └──────────────┬─────────────────────┘
                                 ▼
                          rl-fleet status
                          ├ growth-state cache 読み (per PJ)
                          ├ subagents.jsonl 読み (1回, 30d filter, group-by project)
                          └ render: PJ表 + ISSUES列 + SUBAGENTS_30d列
\`\`\`

### 決定事項

| 観点 | 決定 |
|------|------|
| Scope | MVP-D: growth-state キャッシュ拡張 + token-load 集計 |
| issues_summary 内容 | counts only（line_violations / hardcoded_values / potential_duplicates / corrections_unprocessed / skill_quality_degraded_count） |
| 型定義 | \`@dataclass IssuesSummary\` 新設（FleetRow と同じ pattern） |
| token load 集計 | fleet status 実行時に \`subagents.jsonl\` を on-the-fly group-by project, 30d window |
| perf 対策 | 30d 窓フィルタのみ。Phase 4 で rolling pruning を再検討（subagents.jsonl が 100k 行超えたら） |

### 実装ステップ（sequential、並列化価値なし）

| Step | 変更内容 | 依存 |
|------|---------|------|
| A | \`scripts/lib/issues_summary.py\` 新設（\`@dataclass IssuesSummary\`）+ \`audit.py\` で compute して growth-state に書き込み | — |
| B | \`scripts/lib/fleet.py\` で subagents.jsonl on-the-fly 集計 + 列追加（ISSUES / SUBAGENTS_30d） | A |
| C | テスト追加（\`test_audit.py\` / \`test_fleet.py\` / 新規 \`test_issues_summary.py\`） | A, B |

### Test Coverage（完全対応必須）

\`\`\`
新規コードパス
  compute_issues_summary()
    ├── 各カウント計算（5種）
    └── empty corrections / 空配列での 0 返却
  aggregate_subagents_by_project()
    ├── 30d window フィルタ
    ├── group-by project（空 project → "(unknown)" フォールバック）
    └── 破損 JSON 行 skip（行単位 try/except）
  render_fleet_table() with new columns
    ├── 旧 cache (issues_summary 欠落) → "—" 表示
    └── 新 cache → counts 表示
\`\`\`

### Failure modes（critical gaps）

1. **古い growth-state.json (issues_summary 欠落)** — display "—" でフォールバック、テスト要
2. **subagents.jsonl 破損 1行で全件落ちる** — 行単位 try/except、テスト要
3. **空 \`project\` フィールド** — "(unknown)" にフォールバック、テスト要
4. **30d 境界条件** — UTC naive vs aware 混在に注意、テスト要

## NOT in scope（明示的に保留）

| 保留項目 | 理由 |
|---------|------|
| \`bin/rl-fleet audit-all --parallel\` | Phase 2 の核だが、growth-state 自然更新で当面代替可 |
| \`reflect-all\` / \`evolve-all\` / \`--apply\` / \`rollback\` | Phase 3 全体。dry-run + opt-in marker のガード設計コストが大きい |
| global rules × PJ CLAUDE.md 名前衝突検出 | SPEC.md Phase 2 の片翼。意味的検証は Phase 4+ 領域 |
| \`tool_durations.jsonl\` / \`usage.jsonl\` 集計 | データ sparse（1行 / 6行）、backfill 後に再評価 |
| DuckDB SoR への subagents 統合 | Phase 4 |

## What already exists（重複回避）

- \`fleet.py\` の \`FleetRow\` dataclass + \`_pj_safe_name\`、subprocess timeout、error isolation
- \`audit.py\` の line_violations / duplicates / hardcoded_values 検出ロジック
- \`growth_engine.py\` の growth-state cache 読み書き
- \`subagents.jsonl\` の \`project\` フィールド（既存スキーマで集計可能）
- \`subagent_warning_threshold\` userConfig（閾値判定の reuse 候補）

## 統合: 「診断して」memory チェーン

実装後、\`feedback_diagnose_uses_audit_directly.md\` を更新して以下を反映:
- fleet status 出力に ISSUES / SUBAGENTS_30d 列が増える
- 低スコア PJ のコマンド提示時に \"subagents 過多\" などの追加情報も含める

## 見積り

5 ファイル変更、~250 行追加（dataclass 50 / audit.py 拡張 60 / fleet.py 拡張 80 / テスト 60）。
CC 実装時間：30-45min。

## 関連

- ADR-022: rl-anything fleet 化（Phase 1/2/3 の高レベル設計）
- SPEC.md Next: fleet Phase 2/3 計画（本 issue で MVP-D を Phase 1.5 として挿入）
- feedback_diagnose_uses_audit_directly.md: 「診断して」チェーン memory

## 新セッションで再開する手順

\`\`\`
cd ~/tools/rl-anything
claude
# 中で
/rl-anything:implement
# このIssueを参照して実装開始
\`\`\`

または \`/rl-anything:implement <issue#>\` でこの issue を直接指定。

---

## #24 [Feedback] 機能提案: PJ/グローバル別の LLM トークン使用量計測と環境レビューへの統合  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: 機能提案
**コンポーネント**: audit / fleet
**満足度**: 4/5

## 詳細

### 背景
「PC環境を見直して」「全PJの状態をチェックして」のような環境レビュー要求時、現状 rl-anything は env_score / issues_summary / SUBAGENTS_30d は集計できるが、**LLM トークン使用量（input / output / cache_creation / cache_read）の PJ 別・グローバル集計機能がない**。コスト視点でのボトルネック PJ 特定や、肥大化スキル・暴走 subagent の発見ができない。

なお既存の `subagents.jsonl` の "token-load" 集計は subagent 起動回数であり、LLM トークン量ではない（命名が紛らわしい）。

### 提案
1. `~/.claude/projects/<pj>/*.jsonl` の transcript から PJ × 期間 × トークン種別を集計するユーティリティ追加（例: `bin/rl-fleet tokens` または `bin/rl-tokens`）。
2. `bin/rl-fleet status` の表に `TOKENS_30d` 列（または cache hit率）を追加。
3. 環境レビュー系スキル（audit / fleet status）の出力に「トークン消費 TOP 3 PJ」と「異常値検出（前週比 +N%）」を含める。
4. 異常検出時は具体的アクションを提案（例: 「PJ X は cache hit率 20% → prompt caching の見直し」「subagent から呼ばれる skill Y が肥大」など）。

### 期待効果
- 「環境を見直して」依頼時に、構造品質（既存）+ コスト視点（新規）の両輪で診断できる
- token 消費の異常を早期発見し、無駄な subagent 連鎖や巨大 skill を是正
- rtk gain（RTK 側）が CC の節約量を見るのに対し、こちらは「rl-anything 環境全体の LLM コスト健康度」を見る役割

### 関連
- commit 72f66af の "token-load" は subagent 起動数で、本提案とは別軸
- rtk gain は CC 全体の節約。本提案は PJ 単位の分解が目的

---
*Submitted via /rl-anything:feedback*

---

## #25 [Feedback] bug-report: skill 削除時に Python import 依存の検査漏れ  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: prune / skill-deletion workflow
**満足度**: 3/5

## 詳細

### 発生事例
内部専用スキルを削除した際、`scripts/` 配下のモジュールが他スキル/CLI から import されていることを検査せずに削除し、依存先が破壊された。当該破壊は気付かれず3バージョンに渡って main にリリースされた。

### 根本原因
「内部専用スキル」というラベルは "ユーザーが slash command で呼び出さない" を意味するだけで、"他のスキルから Python import されていない" を保証しない。削除前に依存検査を行う手順が prune / 削除ワークフローになかった。

### 再発防止案

**手順の組み込み:**
1. 削除候補ごとに `git grep -l "<skill-name>"` で全参照箇所を抽出
2. `skills/<skill>/scripts/*.py` の各モジュールについて `from <module>` / `import <module>` をリポジトリ全体から検索
3. `bin/` 配下のシェルラッパーが当該パスを参照していないか確認
4. 1件でも参照があれば「依存断ち切り」を別 PR として先行させる

**自動化案:**
- prune の検証フェーズに import dependency check ステップを追加
- CI に「削除された skills/ 配下のパスが他から参照されていないか」smoke test（`grep -r "skills/<deleted>" .` が空であること）

**メタデータ案:**
- SKILL frontmatter に `imported-by`（他スキル/CLI からの import 元一覧）を追加
- audit/prune が自動更新

### 影響範囲
- 直接パッチ最適化の柱が壊れた状態でリリース
- 同じパターンで他スキルでも発生し得る（特に `scripts/lib/` 共有モジュールを持つ複合スキル）

---
*Submitted via /rl-anything:feedback*

---

## #28 PR #27 設計やり直し: token usage ingest が大規模 PJ で実走不能 — connection 使い回し + session 単位差分検出に変更  `[closed]`

## 背景

PR #27 (#24 対応) で実装した token usage ingest を実機で動作確認したところ、現状の設計ではスケールに耐えないことが判明。PR は draft に戻し、本 issue で設計やり直し方針をまとめる。

PR #27: https://github.com/todoroki-godai/evolve-anything/pull/27 (draft)
ブランチ: \`feat/token-usage-tracking\` (worktree: /tmp/rl-anything-token-usage)
設計ドキュメント: \`~/.gstack/projects/evolve-anything/todoroki-main-design-20260509-082840.md\`

## 実測値 (M1 / rl-anything PJ 1 個 / --days 7)

| 指標 | 値 |
|---|---|
| jsonl ファイル数 | **9,925 ファイル** |
| jsonl 合計サイズ | 1.9 GB |
| ingest 実行時間 | 60秒+ で未完了 (kill) |
| 挿入された rows | 2,870 (極一部) |
| DuckDB ファイルサイズ | **575 MB** (= 200 KB/row 相当) |

設計時の想定「数分以内 / 50 PJ」とは桁違い。`--days 90` × 1280 PJ では完走不能。

## 原因 3 点

### 1. write amplification (致命的)

\`token_usage_store.py\` の \`append_batch\` がファイルごとに \`_connect()\` → \`con.close()\`。DuckDB は close 時に checkpoint = 全データ flush。9925 files で O(N) checkpoint → 同じデータが何度もディスクへ書かれる。

**Fix**: ingest 実行全体で connection を 1 つ使い回す (\`ingest_all_projects\` レベルで open/close)。これで checkpoint は最後の 1 回のみ。

### 2. mtime ベース差分判定が機能しない

Claude Code は session jsonl に append し続けるため、active session の大半が常に「最近更新」扱い。 \`mtime > N日前\` フィルタでは事実上 9925 files 全部対象になる。

**Fix**: 「session per jsonl + last_seen_uuid 差分」に切り替え。
- \`session_progress\` テーブル: \`(pj_id, session_id, last_uuid, last_ts)\`
- ingest 時、各 jsonl は session_id (= ファイル名 stem) ごとに last_uuid 以降のみパース
- ファイル全体パース不要、O(変化したメッセージ数) になる

### 3. executemany 適用済みだが connection オーバーヘッドに埋もれる

PR #27 の最新コミット \`0fc3203\` で executemany + transaction 化済み。25 件 pytest pass。ただし上記 1 の checkpoint 量に対し効果限定。

## 設計やり直し方針

### スキーマ追加

\`\`\`sql
CREATE TABLE session_progress (
    pj_id    VARCHAR NOT NULL,
    session_id VARCHAR NOT NULL,
    last_uuid VARCHAR,
    last_ts   TIMESTAMP,
    PRIMARY KEY (pj_id, session_id)
);
\`\`\`

### ingest フロー変更

1. \`ingest_all_projects\` で 1 connection を open
2. 各 PJ の各 jsonl について:
   - session_id (ファイル名 stem) で \`session_progress\` を引き、\`last_uuid\` を取得
   - jsonl を逆順で開き、\`last_uuid\` まで読む or \`last_uuid\` 未満をスキップ
   - 新規 uuid のみ batch に積む
3. PJ 完了時に session_progress を更新 (1 transaction)
4. 全 PJ 完了後に connection close (= 唯一の checkpoint)

### 性能 Success Criteria (今度こそ実測ベース)

- **rl-anything PJ 1 個 / --days 7 を 60 秒以内** ← 必須、CI bench 化
- 全 PJ (~1280) / --days 90 を 30 分以内 (進捗表示込み)
- DuckDB ファイルサイズが row 数の 1 KB/row 以下
- 2 回目以降の差分 ingest は同じスケールで 30 秒以内 (incremental)

### CI bench

\`scripts/tests/bench_ingest.py\`:
- 大量 jsonl fixture を tmp に生成 (9925 files × 30 messages 想定)
- ingest_pj_dir を実行し、time + DB size を assertion
- pytest -m bench で opt-in (通常 CI には載せない)

## 出発点 (作業継続用)

- worktree: \`/tmp/rl-anything-token-usage\` 残存
- ブランチ: \`feat/token-usage-tracking\` (10 コミット、最新 \`0fc3203\`)
- 既存 25 件のテストは全 pass、設計変更後も維持する
- やり直しコミットは上書き OK (force push 必要、todoroki-godai アカウントで実施)

## 手順案

1. \`/office-hours\` 起動 (本 issue を input にする) → 「session per jsonl + 差分」設計確定
2. \`/plan-eng-review\` で実装計画 (Test Plan に bench を含める)
3. \`/rl-anything:implement\` で worktree 内に実装
4. **必ず実機 1 PJ ingest を 60 秒以内で完走させる検証を経てから** PR を ready に戻す
5. CodeRabbit / `/review` 通過 → /ship

## 関連

- 親 issue: #24
- PR: #27 (draft)
- 関連 commit: 26ee6a6 (初期実装), 0fc3203 (executemany 適用、不十分)
- learning 候補: \`~/.claude/projects/\` 系を触るスキル全般に「実機 1 PJ ベンチを必ず回す」を rule 化

> 💬 comment:
>
> Closed via PR #27 (commit 1cfe8a7). Real-PJ bench: 41.2s / DB 5MB / 12,799 rows / parse/commit=0.20 = commit-bound.

> 💬 comment:
>
> ## リファクタ Phase 1〜7 完了報告 (2026-05-15)
> 
> HARD violator (>800 行) 全件解消。
> 
> | Phase | ファイル | before → after |
> |-------|---------|----------------|
> | 3 | remediation | 2364 → 198 |
> | 4 | prune | 1411 → 147 |
> | 5 | pitfall_manager | 1230 → 94 |
> | 6 | tool_usage_analyzer | 867 → 169 |
> | 7 | verification_catalog | 828 → 147 |
> 
> 残: warn (>500) 8 ファイル — skill_evolve 754 / trigger_engine 751 / coherence 737 / telemetry_query 652 / pipeline_reflector 595 / rl_common 548 / reflect_utils 534 / agent_quality 531。HARD は全 0。別 issue で追跡推奨。

---

## #31 /review skill が CodeRabbit を自動発火しない (docs-only skip 判定込みで実装)  `[closed]`

## Problem

`review-routing.md` (global rule): 「コードレビューはすべて /review スキル一本」「/review スキルが PR 有無を判定して CodeRabbit を自動実行する」

しかし現状 `/review` skill (`~/.claude/skills/review/SKILL.md`) は CR auto-trigger を実装していない。PR があっても CR は手動 (`@coderabbitai review` コメント or `/coderabbit:review` skill) で発火させる必要がある。

実例: PR #27 で Ready 化後 `reviews: 0`、`gh pr comment 27 --body "@coderabbitai review"` で初めて発火。

## Desired behavior

`/review` skill 内に以下のロジック:

1. **PR 存在判定**: `gh pr view --json number 2>/dev/null` で PR 有無
2. **diff 種別判定**:
   - `*.md` / `CHANGELOG.md` / `docs/*` のみ → CR skip (機械検証で十分、token 節約)
   - コード変更 (.py/.ts/.go/etc 含む) → CR auto-fire
3. **CR fire**: PR にコメント `@coderabbitai review` 投稿
4. **結果待ちは optional**: skill 内で 5-10 分 sleep は重い。CR 結果は次回 `/review` 起動時に拾うか、user 判断委ね

## Why this matters

- Senior engineer 相談 (PR #30 review 時): 「docs-only PR で CR を回すのは儀式化、ROI 低い」
- 同時に: 「コード変更で CR スキップは品質劣化リスク」
- 中間点: **diff 種別による自動判定**

## Out of scope

- CR 結果の auto-parse / auto-fix (別 issue)
- CR 以外のレビュー bot (Greptile 等) との統合 (既に SKILL.md に Greptile triage あり)

---

## #34 [Feedback] 機能提案: Claude API トークン消費の上限制御・他プロジェクトへの影響防止  `[closed]`

## フィードバック

**カテゴリ**: 機能提案
**コンポーネント**: optimize
**満足度**: 3/5

## 詳細

### 課題

バッチ処理（100件超のPDF OCR）を実行中に Claude Haiku / Sonnet への API 呼び出しが
集中し、他のプロジェクトでトークンが使えなくなる問題が発生している。

具体的には以下のパターンでほぼ全件がエスカレートしていた：
- EasyOCR 文字化け → Claude Haiku 呼び出し
- Haiku 結果が不確実 → Claude Sonnet へ再エスカレート

100件処理で Haiku + Sonnet が二重に走ると、セッション全体のトークン予算を
一気に消費し、同一アカウントの別プロジェクトが rate limit に当たる。

### 要望

Claude Code / rl-anything レベルで以下のような制御ができると嬉しい：

1. **バッチ処理中のトークン上限設定**（例: `--max-tokens 50000` フラグ）
2. **スロットリング**: API 呼び出し間隔を強制的に空ける（例: 1秒/件）
3. **ローカル LLM 優先フラグ**: Ollama 等が使える場合は Claude API を呼ばない
4. **トークン消費量のリアルタイム表示**: 処理中に「残り予算 X トークン」が分かる

現状の回避策として Ollama（qwen2.5vl:7b）を Haiku の代替に差し替え、
Sonnet エスカレートを完全削除することでトークン消費を大幅削減した。
ただしこれはアプリ側の個別対応であり、フレームワーク側で制御できると
他ユーザーへの汎用的な解決になる。

---
*Submitted via /rl-anything:feedback*

---

## #41 chore: テスト高速化 & トークン消費削減（A: pytest mock化 / B: skill 棚卸し / C: CLAUDE.md slim 化）  `[closed]`

## 背景

セッション内で観測された問題:
- `pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` が 5 分以上経っても完了しない（hooks/ 単体は 0.69s で 377 件と高速）
- 累積トークンが 1 セッションで 1.5M を超えるペースで増加。Skill 一覧と CLAUDE.md / rules が SessionStart のたびに再注入されるのが主因

## やること

### A. pytest 高速化（最優先）
- `pytest --collect-only -q` でテスト件数の偏りを把握
- `pytest scripts/rl/tests/ -q --durations=10` と `pytest scripts/tests/ -q --durations=10` で TOP10 の遅いテストを特定
- 疑い: LLM 統合系（fitness 評価・rl-loop 系）でモック漏れ／実 API call、DuckDB ingest / token_usage_store で実 walker、fixture が `~/.claude/projects/` を実走
- mock 化 or fixture 局所化で短縮（目標: 全体 30s 以内）

### B. 不要 skill の棚卸し
- rl-anything プラグインが提供している skill 一覧を再評価
- 使用テレメトリが薄い / 重複している skill を deprecate or 削除候補としてリストアップ
- 毎セッションの system reminder 注入トークンを削減

### C. CLAUDE.md / rules slim 化
- グローバル `~/.claude/CLAUDE.md` と project `CLAUDE.md` のうち、毎ターン再描画される領域を最小化
- rules は 10 行以内ルールに準拠しているか再確認

## 関連

- v1.50.2 で `token_guard` hook を削除し、context window 占有率を見る `ctx_guard` 一本化（累積トークンは公式 `/usage` でカバー）
- v1.50.0 で `ctx_guard` 追加

## 完了条件

- A: 全テスト 30s 以内（CI 含む）
- B: skill 棚卸しレポート + 削除/deprecate 候補
- C: CLAUDE.md / rules が再描画コストの観点で見直し済み

---

## #67 [tech-eval] r^comp 風 compression term を telemetry fitness に追加  `[closed]`

## 背景

SkillOS (arXiv:2605.06614, Ouyang et al. 2026) は skill curator の報酬関数に **圧縮度項 `r^comp = 1 - |𝒮|/|χ|`**（skill 数 / 経験数）を明示的に組み込み、skill バブル（増やすほど報酬になる罠）を構造的に防いでいる。

rl-anything は現状 `prune` スキルの LLM judge で都度判断しており、**fitness 関数に skill 肥大化への明示ペナルティが無い**。

## 提案

`fitness/telemetry.py` または `fitness/environment.py` に compression term を追加：

```
skill_compression_score = 1 - n_skills / max(n_corrections_resolved, 1)
```

または対数スケール：

```
skill_compression_score = 1 - log(1 + n_skills) / log(1 + n_corrections_resolved)
```

- `n_skills`: `skills/` 配下のアクティブ skill 数
- `n_corrections_resolved`: corrections.jsonl で「解決済み」となった件数（既に集計基盤あり）

environment fitness の `telemetry` 重み (0.45) の内訳に組み込む。重み比はハイパラ 1 つで調整可。

## 期待効果

- prune の LLM judge ヒューリスティックを補強する**定量的シグナル**が常時走る
- skill 増殖時に environment score が自動で下がり、`/rl-anything:audit` で早期検知できる
- evolve loop の variation 生成時に「skill 数を増やす方向」へのバイアスが抑制される

## 実装の出発点

- `scripts/rl/fitness/telemetry.py` の既存軸（3軸構成）に 4軸目として追加
- corrections 解決件数の取得は `scripts/lib/` に既存の集計ロジック
- 実装規模: 30〜50 行 + テスト

## 再評価条件

- skill 数 < 10 の cold start PJ では分母が小さく振動するため、最小サンプル数の閾値を要検討
- r^fc（function-call validity）と併せて導入すると signal 重複の可能性 → ablation で確認

## 参考

- 論文: https://arxiv.org/abs/2605.06614 (SkillOS, Section: Reward Design)
- tech-eval レポート（このセッション）

closes 該当なし（新規提案）

> 💬 comment:
>
> Umbrella: #71 / 詳細レポート: PR #70 (`docs/research/skillos-tech-eval.md`)

---

## #68 [tech-eval] r^fc 風 function-call validity を skill_quality fitness に追加  `[closed]`

## 背景

SkillOS (arXiv:2605.06614, Ouyang et al. 2026) は skill curator の報酬関数に **`r^fc`（有効な function call の割合）** を組み込み、「skill 指示通りに valid な tool 呼び出しがなされた率」を skill 品質の操作的定義として直接 reward 化している。

rl-anything は observe hooks (15個) で **tool 呼び出しを既に全量記録している**が、この signal を skill 品質の評価に back-prop していない（現在は telemetry の使用回数・成功率レベルにとどまる）。

## 提案

skill 別に **function-call validity rate** を集計し、`fitness/skill_quality.py` の軸に追加：

```
skill_fc_validity[s] = valid_tool_calls_during(s) / total_tool_calls_during(s)
```

- 「skill `s` が発火している間に行われた tool 呼び出し」を hook ログから抽出
- `valid` の定義案: 
  - (a) tool 呼び出しが成功（エラー終了でない）
  - (b) skill の YAML frontmatter / SKILL.md 本文で許可・推奨されている tool 群に含まれる
  - 最初は (a) のみで MVP、後に (b) を追加

## 期待効果

- 「使われているが正しく tool を導けていない skill」を可視化（telemetry の usage_count だけでは見えない）
- evolve loop の skill 改善判断に「指示の精度」シグナルが追加される
- pitfalls.md 自動学習との連携: validity 低下時に pitfall 検出を強化

## 実装の出発点

- observe hooks のログは既に `~/.claude/rl-anything/` に蓄積（`token_usage_store` と同じ系統）
- skill 発火 → tool call の紐付けは session_id + timestamp で可能
- `scripts/rl/fitness/skill_quality.py` に新軸として追加
- 実装規模: 80〜120 行（ログ集計 + fitness 統合 + テスト）

## 再評価条件

- 「valid」の定義 (b) を導入すると skill SKILL.md 内の tool 言及をパースする必要 → 別 Issue 化を検討
- r^comp（#67 で提案）と signal 重複の可能性 → 両方導入後に ablation

## 参考

- 論文: https://arxiv.org/abs/2605.06614 (SkillOS, Section: Reward Design — r^fc term)
- tech-eval レポート（このセッション）
- 関連 Issue: #67 (r^comp compression term)

closes 該当なし（新規提案）

> 💬 comment:
>
> Umbrella: #71 / 詳細レポート: PR #70 (`docs/research/skillos-tech-eval.md`)

---

## #69 [tech-eval] SkillOS 論文を ADR / SPEC.md に引用し frozen-executor + trainable-curator 分離を正当化  `[closed]`

## 背景

SkillOS (arXiv:2605.06614, Ouyang et al. 2026) は **frozen agent executor + trainable skill manager (curator) の分離設計** を採用し、curator policy のみを学習対象としている。Cross-backbone 実験（Qwen3-8B で学習した curator を Gemini-2.5-Pro executor に転用しても効く）で、curator がメタ層で汎化することを示した。

rl-anything は実質的に同じ設計（Claude Code 自体は frozen、plugin 側のスキル群と curation ロジックだけが進化）を採っているが、**設計判断としては明示化されていない**。SkillOS は同じ分離設計を独立に発見・正当化した先行研究として引用価値が高い。

## 提案

`docs/decisions/` に ADR を追加（または既存 ADR があれば追記）：

> **ADR-XXX: Frozen Executor + Trainable Curator の分離**
>
> - Claude Code 本体（executor）は frozen と扱い、rl-anything は curator 側の品質（skill 群と curation ロジック）のみを進化対象とする
> - 根拠: (a) executor を変更できる権限がない (b) SkillOS [Ouyang+ 2026] が同設計で cross-backbone 汎化を実証 (c) safety: executor を変えないことで影響範囲を skill ファイル群に限定

`SPEC.md` の設計原則セクションにも 1〜2 行で言及し、論文リンクを残す。

## 期待効果

- 「なぜ Claude Code 自体の挙動を変えに行かないのか」「なぜ skill curation に集中しているのか」が外部から見て自明になる
- 将来「curator も LLM 側で微調整したい」みたいな提案が来た時の判断基準として使える
- 既存研究との対応関係が明確になり、論文を読むユーザー（学術寄り）への訴求になる

## 実装の出発点

- `docs/decisions/` の既存 ADR フォーマットに従う
- 引用情報:
  - 論文タイトル: SkillOS: Learning Skill Curation for Self-Evolving Agents
  - 著者: Siru Ouyang et al. (Google DeepMind, UIUC)
  - arXiv: https://arxiv.org/abs/2605.06614
  - 提出: 2026-05-07
- 実装規模: ADR 1 件 + SPEC.md 1〜2 行

## 再評価条件

- 将来 Claude Code が plugin から hook 経由でモデル挙動を能動的に変えられるようになった場合は、この設計判断を見直す
- rl-anything の scope が agent executor 自体の改善に拡張された場合は ADR を superseded にする

## 参考

- 論文: https://arxiv.org/abs/2605.06614
- 関連 Issue: #67, #68 (SkillOS の reward 設計取り込み)

closes 該当なし（新規提案）

> 💬 comment:
>
> Umbrella: #71 / 詳細レポート: PR #70 (`docs/research/skillos-tech-eval.md`)

---

## #71 [tech-eval umbrella] SkillOS (arXiv:2605.06614) の取り込み  `[closed]`

SkillOS: Learning Skill Curation for Self-Evolving Agents (arXiv:2605.06614, Ouyang et al. 2026-05-07) の評価結果に基づく取り込みタスクの umbrella issue。

## 詳細レポート

[docs/research/skillos-tech-eval.md](../blob/main/docs/research/skillos-tech-eval.md) (PR #70)

論文の手法詳細・査読コメント・rl-anything との対応関係・取り入れる優先順位の全文はこちらを参照。

## 取り込みタスク

### 推奨度: 高

- [ ] #67 — r^comp 風 compression term を telemetry fitness に追加
- [ ] #68 — r^fc 風 function-call validity を skill_quality fitness に追加

### 推奨度: 中

- [ ] #69 — SkillOS 論文を ADR / SPEC.md に引用し frozen-executor + trainable-curator 分離を正当化

### 推奨度: 低 / 不要

- GRPO による curator policy 学習 — データ量・実装コスト過大。単一 PJ では rollout 不足
- BM25 retrieval — SkillOS 自身が limitation と認めている。CC の skill 自動 load 機構があるので不要
- skill 変更の遅延 attribution（前回提案） — r^comp と r^fc 導入で必要性が部分的に消化される

## 採用判断のサマリ

論文を読み込むと、**r^comp と r^fc の 2 つの reward 項のほうが ROI が圧倒的に高い**。両方とも

- rl-anything のデータ基盤（`token_usage_store`, observe hooks, skill 数カウント）で既に取れる
- fitness 関数への加算項として実装 30〜50 行
- ハイパラ 1 個追加で済む

ので、`fitness/environment.py` の重みを維持したまま `telemetry` の内訳に compression と function-call validity を追加するのが妥当。GRPO 化は不要。

## 関連

- 論文: https://arxiv.org/abs/2605.06614
- PR: #70 (tech-eval レポートのコミット)
- Sub-issues: #67, #68, #69

---

## #97 [tech-eval] reflect memory に update_count guard を追加 (arXiv:2605.12978 由来)  `[closed]`

## 背景

[arXiv:2605.12978 "Useful Memories Become Faulty When Continuously Updated by LLMs"](https://arxiv.org/abs/2605.12978) は、エージェントが自身のメモリを LLM 自身に更新・要約させ続けると、世代を重ねるごとに元情報からズレて誤りが指数的に蓄積することを実証している。

評価レポート: [`docs/research/faulty-updated-memories.md`](https://github.com/todoroki-godai/evolve-anything/blob/main/docs/research/faulty-updated-memories.md) (PR #96)

## 現状

✅ 既に効いている安全策:
- `corrections.jsonl` は append-only (要約せず raw event 保持)
- MEMORY.md は個別 MD ファイルへの追記運用で再要約による減損は起きにくい
- `reflect` skill は最終反映前に人間レビューを通す

🔶 未実装の guard:
- memory が LLM 経由で何回書き換えられたかを記録するメタデータがない
- 高頻度に書き換えられた memory に対する warning がない

## 提案

### 1. memory frontmatter に `update_count` を追加
`scripts/reflect_utils.py` の memory 書き換えパス (`split_memory_sections` 等経由) で `update_count` を `+1` する。

```yaml
---
name: foo-pitfall
description: ...
metadata:
  type: feedback
  update_count: 3  # NEW
  last_updated: 2026-05-15
---
```

### 2. audit に `memory_heavy_update` ルール追加
`scripts/lib/audit/issues.py` に `update_count >= 3` の memory を warning として検出するルールを追加。

### 3. reflect 実行時の warning
reflect で memory を更新する際、`update_count >= 3` のエントリには「過去 N 回 LLM 更新済み、元 corrections を再参照すべきかも」と inline warning を出す。

## 出発点

- `scripts/reflect_utils.py:302-454` (memory 読み書き)
- `scripts/lib/memory_temporal.py` (stale/superseded 検出ロジック、新メタデータ追加の参考)
- `scripts/lib/audit/issues.py` (新ルール追加先)
- 既存テスト: `scripts/tests/test_reflect_provenance.py`

## 再評価条件

- 不要 (採用方向で進める想定)
- 実装後、`update_count >= 3` の memory が実環境で何件出るかで guard 閾値を調整

## 関連

- 評価 PR: #96
- arXiv:2605.12978

---

## #100 Phase 5/6/7 並列リファクタ計画: pitfall_manager / tool_usage_analyzer / verification_catalog の HARD violator 解消  `[open]`

## 背景

`scripts/lib/` 配下の HARD violator（`MAX_PYTHON_SOURCE_HARD=800` 行超過、`scripts/lib/line_limit.py`）3 ファイルを Phase 5/6/7 として **3 並列 subagent + worktree 隔離** で分割する計画。

Phase 1〜4（fleet / discover / remediation / prune）と同じ slice パターンを踏襲: snapshot test 先行 → 機能群ごとに新モジュール抽出 → re-export で後方互換維持 → 各 slice が 1 PR。

## 対象ファイル

| Phase | ファイル | 現行行数 | 目標 | 担当 subagent |
|-------|----------|---------:|-----:|---------------|
| 5 | scripts/lib/pitfall_manager.py | 1230 | ≤200 | agent-A |
| 6 | scripts/lib/tool_usage_analyzer.py | 867 | ≤200 | agent-B |
| 7 | scripts/lib/verification_catalog.py | 828 | ≤200 | agent-C |

## 並列化戦略

**ファイル衝突分析**:
- 各 Phase は **異なる Python モジュール**を編集 → 新規ファイル + 該当 .py の package 化のみ → **コード衝突ゼロ**
- 共通衝突点: \`CHANGELOG.md\` の \`## [Unreleased] ### Changed\` 先頭追記。3 並列だと毎回コンフリクト → **各 agent は push 直前に \`git pull --rebase origin main\` 必須**
- snapshot test fixture は phase ごとに別ファイル（\`pitfall_manager_api_surface.txt\` 等）→ 衝突なし

**衝突しない並列タスクのみ並列化**:
- 同 phase 内の slice 順序は sequential（同 \`__init__.py\` を編集するため）
- phase 間は parallel（独立モジュール）

## Phase 5: pitfall_manager.py 分割計画

| Slice | 抽出先 | 主な対象 | 概算行数 |
|-------|--------|----------|---------:|
| 0 | scripts/tests/test_pitfall_manager_snapshot.py | API surface snapshot | (test) |
| 1 | pitfall_manager/parser.py | parse_pitfalls / render_pitfalls / get_hot/warm/cold_tier / _flush_item / _PITFALL_HEADER_RE / _FIELD_RE | ~150 |
| 2 | pitfall_manager/recording.py | find_matching_candidate / record_pitfall / promote_to_active / graduate_pitfall / _make_pitfall_entry / _safe_read / _write_empty_template | ~250 |
| 3 | pitfall_manager/detection.py | _STOP_WORDS / extract_root_cause_keywords / _split_sections_from_content / detect_integration / extract_pitfall_candidates / detect_archive_candidates / execute_archive | ~400 |
| 4 | pitfall_manager/preflight.py + rationalization.py | _compute_line_guard / _CATEGORY_TEMPLATE_MAP / suggest_preflight_script / detect_rationalization_patterns / generate_rationalization_table | ~250 |
| 5 | pitfall_manager/runner.py | pitfall_hygiene + Phase 5 完了 | ~150 |

## Phase 6: tool_usage_analyzer.py 分割計画

| Slice | 抽出先 | 主な対象 | 概算行数 |
|-------|--------|----------|---------:|
| 0 | scripts/tests/test_tool_usage_analyzer_snapshot.py | API surface snapshot | (test) |
| 1 | tool_usage_analyzer/session_io.py + stall.py | _resolve_session_dir / extract_tool_calls / extract_tool_calls_by_session / _classify_stall_step / _detect_stall_in_session / detect_stall_recovery_patterns / stall_pattern_to_pitfall_candidate | ~330 |
| 2 | tool_usage_analyzer/classify.py | _is_cat_replaceable / _get_command_head / classify_bash_commands / _get_command_key / detect_repeating_commands / _classify_subcategory | ~280 |
| 3 | tool_usage_analyzer/codegen.py + install_check.py | generate_rule_candidates / _HOOK_TEMPLATE / generate_hook_template / check_artifact_installed / check_hook_installed + Phase 6 完了 | ~250 |

## Phase 7: verification_catalog.py 分割計画

| Slice | 抽出先 | 主な対象 | 概算行数 |
|-------|--------|----------|---------:|
| 0 | scripts/tests/test_verification_catalog_snapshot.py | API surface snapshot | (test) |
| 1 | verification_catalog/helpers.py + templates.py | _safe_result / _detect_primary_language / _iter_source_files / _is_test_file / _has_cross_module_pattern + 全 _*_TEMPLATE 定数 + 副作用 regex | ~250 |
| 2 | verification_catalog/detectors_basic.py | detect_data_contract_verification / detect_side_effect_verification / detect_evidence_verification | ~300 |
| 3 | verification_catalog/detectors_advanced.py + 残 | detect_cross_layer / detect_happy_path + runner + Phase 7 完了 | ~250 |

## 各 Slice 共通手順（Phase 1〜4 で確立済み）

1. branch 作成（\`refactor/<phase>-sliceN-<name>\`）
2. 新モジュール作成 + 元ファイルを re-export ブロックに置換
3. \`grep -rn --include=\"*.py\" -E \"<module>\\.<name>\"\` で外部 mock.patch 確認 → 該当箇所は新モジュール内で \`from . import X\` lazy 参照
4. snapshot test + 関連テスト pass 確認

## 既知の制約・落とし穴

- pre-existing 失敗は無視（PR description に明記）:
  - \`skills/evolve/scripts/tests/test_remediation.py::test_fix_line_limit_rule_separation\`
  - \`hooks/tests/test_e2e_*.py\` の collection RecursionError（バッチ実行時のみ）
  - \`scripts/tests/test_remediation_*\` の FileNotFoundError（旧 shim）
  - \`skills/reflect/...::test_line_limit_warning_on_overflowed_rule\`
- rl-anything は public/todoroki-godai org → push / PR とも \`todoroki-godai\` で固定
- main への直接 commit 禁止 / hook 回避禁止
- subagent 暴走防止: cascade 禁止（subagent が更に subagent を生成しない）

## 推定所要時間

Phase 4 sequential 実績（slice 1 件あたり 5〜10 分）から推定:
- Phase 5（5 slice + snapshot）: 30〜60 min
- Phase 6（3 slice + snapshot）: 20〜40 min
- Phase 7（3 slice + snapshot）: 20〜40 min
- **3 並列で実施した場合の wall time: 30〜60 min**（最長 phase に律速）
- sequential なら合計 70〜140 min

CHANGELOG.md コンフリクト解決オーバーヘッド込みでも並列の方が確実に速い。

## 起動条件

Phase 4 sequential agent（aa9ff36bca0bf5913）の完了後に起動。Phase 4 が main を更新し続けるため、Phase 5/6/7 を被らせると CHANGELOG / 共通設定ファイルで衝突するため。

closes #28 への追加進捗として位置付け。

---

## #148 [tech-eval] stop_failure の error_type を AgentErrorTaxonomy 5 分類に拡張  `[closed]`

## 背景

AgentErrorTaxonomy (arXiv:2509.25370, Jeevan et al. 2025) は LLM エージェントの失敗を **memory / reflection / planning / action / system** の 5 レイヤーに分類する体系を提案し、AgentErrorBench（ALFWorld/GAIA/WebShop）で修正フィードバックループに適用することで **最大 +26% の相対改善** を実証している。

rl-anything の `hooks/stop_failure.py` は現在 `error_type` フィールドに `rate_limit` / `auth_failure` / `unknown` 等の**技術エラーのみ**を記録しており、エージェント行動レベルの失敗分類は存在しない。これにより `reflect` スキルが「何を学んだか」を分類できず、学習効率が頭打ちになっている。

## 提案

`stop_failure.py` の `error_type` を以下の 2 次元に拡張：

```python
# 既存（技術エラー）
"error_class": "tech"  # rate_limit / auth_failure / timeout

# 新規（行動エラー）
"error_class": "behavioral"
"error_layer": "memory"       # 記憶の参照ミス・古い情報使用
                | "reflection" # 自己評価の失敗
                | "planning"   # タスク分解・手順設計のミス
                | "action"     # ツール選択・パラメータ誤り
                | "system"     # 外部 API・環境起因
```

`reflect` スキルはこの `error_layer` でフィルタし、分類別の pitfall を生成できるようになる。

## 実装ヒント

- `stop_failure.py` への追記のみ（既存フィールドは互換維持）
- behavioral 分類は LLM で post-hoc に推定（hook 内で同期呼び出し不可なため、`reflect` 実行時に遅延分類）
- AgentErrorBench のアノテーション基準を分類プロンプトのベースとして使用

## 参考

- 論文: https://arxiv.org/abs/2509.25370
- MAST Taxonomy (14 モード × 3 カテゴリ): https://arxiv.org/abs/2503.13657
- 関連 open issue: #68（function-call validity — action レイヤーの一部と重なる）

---

## #149 [tech-eval] MemOS の L1→L4 結晶化アーキテクチャを参照し corrections→evolve 設計を ADR 化  `[closed]`

## 背景

MemOS (MemTensor/MemOS) は LLM エージェント向けの「自己進化メモリ OS」で、記憶を **L1トレース → L2ポリシー → L3ワールドモデル → L4結晶化スキル** の 4 段階で進化させる設計を採用している。ハイブリッド検索（FTS5 + ベクトル）による 35.24% のトークン削減も実証済み。

rl-anything の現行パイプラインは偶然にも同型の構造を持っている：

| MemOS 層 | rl-anything 対応 |
|---------|-----------------|
| L1 トレース | `corrections.jsonl` / `sessions.jsonl` |
| L2 ポリシー | `MEMORY.md` (auto-memory) |
| L3 ワールドモデル | `rules/` + `CLAUDE.md` |
| L4 結晶化スキル | `skills/` (skill files) |

しかしこの対応関係は**設計判断として明示化されていない**。また MemOS が解決している「層間の矛盾解消（HiMem の conflict-aware reconsolidation）」が rl-anything では手動になっている。

## 提案

1. **ADR を追加** (`docs/decisions/ADR-XXX-memory-crystallization.md`)
   - rl-anything の 4 層メモリ設計を明文化し、MemOS との対応関係を根拠として示す
   - 各層の「ライフサイクル・更新トリガー・廃棄条件」を定義

2. **ギャップマッピング**（SPEC.md に追記）
   - 現在未実装: 層間の矛盾検出・自動 reconsolidation
   - 現在未実装: ハイブリッド検索（MEMORY.md は線形スキャン）
   - 将来検討候補として記載

## 期待効果

- 「なぜ corrections → MEMORY.md → rules → skill という階層があるのか」が外部から自明になる
- MemOS / HiMem の改善手法を取り込む際の受け口ができる

## 参考

- MemOS GitHub: https://github.com/MemTensor/MemOS
- HiMem (conflict-aware reconsolidation): https://arxiv.org/abs/2601.06377
- AgeMem (tool-based memory ops): https://arxiv.org/abs/2601.01885
- Awesome-AI-Memory (調査起点): https://github.com/IAAR-Shanghai/Awesome-AI-Memory
- 関連 ADR: #69（frozen-executor + trainable-curator 分離の正当化）

---

## #150 [tech-eval] corrections.jsonl スキーマに preceding_tool_calls[] を追加し reflect 精度を向上  `[closed]`

## 背景

TraceElephant (arXiv:2604.22708) は、失敗帰属の精度が**完全トレース**（入力・コンテキスト・ツール呼び出し列を含む）を使うことで +76% 向上することを実証している。従来の手法はエージェント出力のみを記録していたが、「直前に何のツールをどの順で呼んだか」という文脈が失敗分類の精度に決定的な影響を与えるという知見。

rl-anything の `corrections.jsonl` は現在、修正の「内容」は記録しているが、**修正直前のツール呼び出し列（コンテキスト）を記録していない**。このため `reflect` スキルが「どのツール操作の後に誤りが起きたか」を分析できず、pitfall 生成の精度が低い。

## 提案

`corrections.jsonl` のスキーマに `preceding_tool_calls` フィールドを追加：

```jsonc
{
  "session_id": "...",
  "timestamp": "...",
  "correction_text": "...",
  // 新規追加
  "preceding_tool_calls": [
    { "tool": "Bash", "success": true },
    { "tool": "Edit", "success": true },
    { "tool": "Bash", "success": false }  // ← 直前の失敗
  ],
  "error_type": "..."  // #148 と連携
}
```

`hooks/correction_detect.py`（または相当箇所）でセッション内の直近 N 件のツール呼び出しを参照して埋める。

## 実装ヒント

- `sessions.jsonl` / `tool_usage` ログが既に存在するため、session_id で JOIN して post-hoc に付与することも可能（スキーマ変更なしの MVP アプローチ）
- `reflect` スキルの分類プロンプトに `preceding_tool_calls` を渡し、「どの操作パターンが失敗を招くか」を自動学習させる
- #148（error_layer 分類）と組み合わせると、「action レイヤーの Bash 失敗後に Edit を試みた」等のパターンが検出可能になる

## 参考

- TraceElephant: https://arxiv.org/abs/2604.22708
- ErrorProbe (failure attribution): https://arxiv.org/abs/2604.17658
- 関連 issue: #148（error_type 5 分類）、#68（function-call validity）

---

## #151 [feat] reflect memory の update_count を Python で自動インクリメント  `[closed]`

## 背景

PR #147 で `update_count` guard を実装した（closes #97）。現状、`update_count` のインクリメントは `skills/reflect/SKILL.md` Step 7.6 の LLM 指示に依存しており、LLM が Step 7.6 を見落とすと counter が更新されない。

## 課題

guard の enforcement が LLM の instruction following に 100% 依存している。
コンテキスト圧縮・SKILL.md の途中省略・モデルのスキル内容スキップ等で silently no-op になり得る。

## 提案

`post_tool_use` hook（Edit / Write）で対象ファイルが `.claude/memory/*.md` の場合に frontmatter の `update_count` を自動インクリメントする。

```python
# hooks/post_tool_use.py (新規 or 既存 hook に追加)
# tool_name in ("Edit", "Write") and path matches .claude/memory/*.md
# → parse frontmatter → update_count += 1 → write back
```

または `reflect_utils.py` に `increment_update_count(filepath)` helper を追加し、reflect.py / SKILL.md 両方から明示的に呼べるようにする。

## 出発点

- `hooks/` 既存 hook 構造
- `scripts/lib/memory_temporal.py` — `parse_memory_temporal` が既に frontmatter を読む
- `scripts/reflect_utils.py` — memory 書き込みヘルパー群

## 関連

- 実装 PR: #147
- arXiv:2605.12978

---

## #154 fix(frontmatter): yaml.dump sort_keys=False で frontmatter キー順を保持する  `[closed]`

## 背景

PR #153 レビュー中に発見 (adversarial review F3)。

## 問題

`scripts/lib/frontmatter.py::update_frontmatter` 内の `yaml.dump` が `sort_keys=True`（デフォルト）で動作しているため、`update_frontmatter` を呼ぶたびに frontmatter の全キーがアルファベット順に reorder される。

`post_tool_use_memory.py` hook が Edit/Write 後に毎回 `update_frontmatter` を呼ぶようになったため（PR #153）、memory ファイルを編集するたびに frontmatter キー順が変わり、git diff が読みにくくなる。

## 修正案

```python
# frontmatter.py の yaml.dump 呼び出しに sort_keys=False を追加
new_yaml = yaml.dump(parsed, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
```

## 影響範囲

- `scripts/lib/frontmatter.py::update_frontmatter`
- 既存 snapshot テストが frontmatter キー順に依存している場合は更新が必要

## 関連

- PR #153 (post_tool_use_memory hook)
- adversarial review finding F3

---

## #161 [Feedback] 機能提案: LSP導入提案機能の追加  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: 機能提案（feature request）
**コンポーネント**: audit / evolve
**満足度**: 4/5

## 詳細

rl-anything の audit や evolve フローの中で、プロジェクトの言語構成（Python/TypeScript 等）を検出し、Claude Code の LSP サポート（.lsp.json + 言語サーバー）の導入を提案する機能がほしい。

具体的には以下の点を期待する:

- 対象 PJ のファイル数・使用言語を分析する
- トークン削減・効率向上の観点から「LSP を導入すべきか」を判定する
- 「何をインストールすればよいか」（言語サーバー名・設定例）を提示する

audit レポートや evolve の推奨アクションとして自然に組み込まれると、ユーザーが LSP 設定の恩恵を受けやすくなる。

---
*Submitted via /rl-anything:feedback*

---

## #167 [tech-eval] エージェント自己進化強化：集団放送メモリ進化 + 事前リスク評価の実装  `[closed]`

## 背景

2026-05-19 の AI デイリーレポートに掲載された2論文を tech-eval で評価し、
rl-anything への採用推奨度「高」と判定した。

---

## 対象論文

### 1. FORGE: Self-Evolving Agent Memory With No Weight Updates via Population Broadcast
- arXiv: https://arxiv.org/abs/2605.16233
- 提出日: 2026-05-18

### 2. Look Before You Leap: Autonomous Exploration for LLM Agents
- arXiv: https://arxiv.org/abs/2605.16143
- 提出日: 2026-05-18

---

## ユーザー体験への影響

| 技術 | Before（現状） | After（採用後） |
|------|--------------|----------------|
| FORGE: 集団放送メモリ進化 | スキル改善はcorrections蓄積→reflect→人間確認が必須 | 経験から複数候補が競争的に進化し、人間介入なしで最良スキルが自動選択される |
| FORGE: 重み更新なし適応 | 毎セッション同じ品質から再出発 | 前セッションの成功パターンが継続して引き継がれる |
| Look Before You Leap: 事前リスク評価 | evolveで意図しないスキル破壊が起きることがある | 危険な変更を実行前に検出し「これはリスクが高い」と警告 |

---

## 現行実装とのギャップ

### FORGE → `genetic-prompt-optimizer` の強化

- **現状**: `genetic-prompt-optimizer` は1パス直接パッチ止まり（集団競争なし）
- **ギャップ**: Population Broadcast 方式の複数候補競争・勝者選択ロジックが未実装
- **対象ファイル**: `scripts/lib/` 以下の optimizer 実装

### Look Before You Leap → `regression_gate.py` の事前チェック拡張

- **現状**: `regression_gate.py` は事後チェックのみ（実行前評価なし）
- **ギャップ**: 不確実性定量化・リスクスコア算出・実行前ブロック判定が未実装
- **対象ファイル**: `scripts/lib/regression_gate.py`

---

## 推奨実装アクション

1. arXiv 2605.16233 を精読し、`genetic-prompt-optimizer` を Population Broadcast 方式に拡張する設計 doc を作成
2. arXiv 2605.16143 の不確実性定量化ロジックを `regression_gate.py` の事前チェックとして実装

---

## 再評価条件

- **FORGE**: コード公開後（GitHubリンク検討中との記載あり）— 公開確認後に実装着手
- **Look Before You Leap**: コード公開確認後

---

## 採用後の確認方法

- [ ] FORGE方式実装後: `python scripts/rl/tests/test_genetic_optimizer.py` で `diversity_score > 0.3` を確認
- [ ] 事前リスク評価実装後: `regression_gate.pre_check({'type': 'dangerous_change'})` が `{'risk': 'high', 'block': True}` を返すことを確認

---

## #180 fix(evolve): global スキルの stale_rule / hardcoded_value 誤検知を検出ロジックで除外  `[closed]`

## 問題

evolve 実行のたびに以下の false positive が毎回発生し、AskUserQuestion が無駄に発火する。

### stale_rule (layer_diagnose)
- `.claude/rules/infra-ship-gate.md` line 2: \"buildspec/CDK/Terraform\" がファイルパスと誤認識
- `.claude/rules/transcript-store-bench.md` line 2: \"walk/ingest\" がファイルパスと誤認識
- いずれもドメイン用語（スラッシュを含む技術語）がパス参照と誤検知されている

### hardcoded_value (remediation proposable)
- gstack などの global スキル内の `sk-only-for-one-way` が API key パターンとして誤検知
- 毎回 508件の proposable が計上されているが大半がこれ

## 影響

- 毎回 `auto_fixable 1件` の確認ダイアログ → スキップ が繰り返される（信頼毀損）
- proposable 508件という数字が実態（実質0件）と乖離し、factual-claims 違反

## 解決方針（senpai 設計レビュー済み）

1. **stale_rule**: `layer_diagnose.py` の `_PATH_PATTERN` マッチ後に「スラッシュを含むが実ファイルパスとして無効なトークン」を除外するフィルタを追加。具体的には `/` を含むが先頭が英大文字・ドット以外で始まるものを除外するか、既知のドメイン語リストで抑制
2. **hardcoded_value**: `global` スコープ（`~/.claude/skills/` 配下）のスキルは `detect_hardcoded_values` の対象外にする。remediation の scope 判定で `origin == "global"` を除外するロジックを追加

## 期待する結果

- evolve 実行時の auto_fixable が実態を反映した値になる
- proposable 件数が custom PJ 向けの実効件数のみを表示する

## 優先度

senpai レビューにて「最大コスト（毎回の信頼毀損）」として優先度1位と判断。

> 💬 comment:
>
> 統合 issue に移行

---

## #181 fix(evolve): remediation 件数を custom PJ のみ表示し global 誤検知を参考値に分離  `[closed]`

## 問題

evolve レポートの remediation 件数が実態と乖離している。

```
total_issues: 606
proposable: 508
manual_required: 95
```

508件の大半は `~/.claude/skills/`（gstack 等）の global スキルへの hardcoded_value 誤検知。  
カスタム PJ に関係する実効件数は実質 0件に近い。  
→ factual-claims.md 違反（数字が判断材料を汚染している）

## 解決方針（senpai 設計レビュー済み）

remediation レポートの件数表示を scope で分離する:

```
# Before
proposable: 508件

# After
proposable: custom 0件 / global 508件（参考値）
```

- サマリ判定（対応不要 / 要対応）は **custom スコープのみ** を使う
- global スコープの件数は「参考値」として折りたたみ表示または括弧書き
- `impact_scope == "global"` の issue を集計から分離するロジックを remediation.py または evolve SKILL.md の Step 5.5 に追加

## 期待する結果

- ユーザーが「508件も問題がある」と誤解しなくなる
- custom PJ に関係する実効件数だけで対応要否を判断できる

> 💬 comment:
>
> 統合 issue に移行

---

## #182 feat(evolve): 推奨アクションを「判定カード + 実行コマンド直書き」形式に改善  `[closed]`

## 問題

evolve の「推奨アクション」セクションが actionable でない。

現状:
```
- Bash割合: 66.1%（目標 ≤40%）— 未達
- Workflow Checkpoints: ギャップなし
- 自己進化: 対象スキルなし
- Remediation: 対応不要
```

→ ユーザーが「次に何をすればいいかわからない」と言った。  
→ 「未達」と書かれても実行すべきコマンドがない場合は行動に繋がらない。

## 解決方針（senpai 設計レビュー済み）

推奨アクションを3段階の判定カード形式に変更する:

```
## 推奨アクション

🔴 要対応 (1件)
  - /rl-anything:reflect — 未処理フィードバック 9件

🟡 情報 (コマンドなし)
  - Bash割合: 66.1%（rule 導入済み、習慣づけ継続）

✅ 問題なし
  - Prune / Reorganize / Workflow Checkpoints
```

- `要対応`: 実行すべきコマンドを直書き。コマンドがない項目はここに出さない
- `情報`: 状態の記録だが今すぐ action 不要なもの。件数・割合のみ
- `問題なし`: 一行でまとめる（フェーズ名を列挙するだけ）

これにより「次に叩くコマンド」がひと目でわかる状態にする。

## 影響範囲

- evolve SKILL.md の Step 10（推奨アクション）の出力フォーマット指示を改訂
- evolve.py 側の出力データ構造は変更不要（表示ロジックの変更のみ）

## 優先度

senpai レビューにて優先度3位（①②の後）。

> 💬 comment:
>
> 統合 issue に移行

---

## #183 fix(evolve): レポートのノイズ除去と推奨アクションの actionability 改善  `[closed]`

## 背景

evolve 実行後に「次に何をすればいいかわからない」という問題が発生。senpai（senior-engineer）レビューで優先順位を確定した。

## 問題と解決方針（優先度順）

### 1. false positive が毎回 AskUserQuestion を発火させる（最優先）

**現象**:
- `stale_rule`: `infra-ship-gate.md` の "buildspec/CDK/Terraform"、`transcript-store-bench.md` の "walk/ingest" がファイルパスと誤認識
- `hardcoded_value`: gstack 等 global スキル内の `sk-only-for-one-way` が API key として誤検知
- → 毎回 `auto_fixable 1件` の確認ダイアログ → スキップ が繰り返される

**方針**: 検出ロジック側で除外（suppression リストは負債化するので不採用）
- `_PATH_PATTERN` マッチ後に「スラッシュを含む技術語」の誤検知を除外
- `origin == "global"` のスキルを `detect_hardcoded_values` の対象外にする

### 2. proposable 508件が実態（実質0件）と乖離（factual-claims 違反）

**現象**: 508件の大半が global スキルへの誤検知で、custom PJ に関係するものはほぼない

**方針**: scope 分離表示
```
# Before
proposable: 508件

# After  
proposable: custom 0件 / global 508件（参考値）
```
サマリ判定は custom スコープのみで行う。

### 3. 推奨アクションが actionable でない

**現象**: 「Bash割合 66.1% 未達」など、コマンドがない項目も並列表示されて何をすべきか不明

**方針**: 判定カード形式に変更
```
🔴 要対応: /rl-anything:reflect — 未処理フィードバック 9件
🟡 情報: Bash割合 66.1%（rule 導入済み、継続）
✅ 問題なし: Prune / Reorganize / Checkpoints
```
コマンドがない項目は「要対応」に出さない。

### 4. （付随）「カスタムスキル 0件」が4フェーズで繰り返される

Reorganize・Optimize・Pitfall・Fitness でそれぞれ同じ理由でスキップと表示される → 1行にまとめる

## 影響範囲

- `scripts/lib/layer_diagnose.py` — stale_rule 検出ロジック改善
- `scripts/lib/audit/issues.py` — hardcoded_value の global 除外
- `skills/evolve/SKILL.md` — Step 5.5（件数表示）/ Step 10（推奨アクション形式）の指示改訂

---

## #185 feat(evolve): プラグイン自己診断ギャップ — rl-anything 本体スキルを skill_evolve / Pitfall剪定 / Fitness Evolution 対象にする  `[closed]`  (enhancement)

## 問題

rl-anything を **rl-anything PJ 内で実行**すると、以下のフェーズが常にスキップされる:

- **Pitfall 剪定**: 「カスタムスキル 0件のためスキップ」
- **自己進化適性判定 (skill_evolve)**: カスタムスキル 0件のため対象なし
- **Fitness Evolution**: データ不足 (0/30件)

3ヶ月以上セッションを継続していても改善されない。

## 根本原因

### 1. `find_artifacts()` のスキャン対象が `.claude/skills/` に限定されている

```python
# scripts/lib/audit/__init__.py
# .claude/skills/ (カスタム) と ~/.claude/skills/ (global) のみをスキャン
# rl-anything 本体の skills/ ディレクトリ（プラグインスキル）は対象外
```

rl-anything には `.claude/skills/` が存在しないため、カスタムスキル = 0件。  
`skills/evolve/`, `skills/reflect/` 等のプラグインスキルは `origin = "plugin"` として扱われ除外される。

### 2. Fitness Evolution のデータ蓄積条件が未達

Fitness Evolution は `~/.claude/rl-anything/optimize*.jsonl` の accept/reject 実績を使う。  
カスタムスキルがないため `optimize` を実行できず → データが溜まらない。

### 3. 構造的矛盾

rl-anything はスキルを `skills/<name>/SKILL.md` に配置するが、  
evolve が読む対象は `.claude/skills/<name>/SKILL.md`（ユーザー自作スキル）。  
プラグイン本体が自分自身のスキルを改善できない。

## 影響

- rl-anything PJ を長期間使い続けても Pitfall / Fitness のデータが蓄積されない
- 「なぜ 0件なのか」がユーザーに伝わらず混乱を招く
- rl-anything 自身のスキル品質を evolve で改善できない

## 修正案

### Option A: `plugin` origin を evolve 診断対象に追加（短期）

`skill_evolve_assessment()` と `pitfall_hygiene()` で `origin == "plugin"` も対象とする設定フラグを追加。  
rl-anything PJ 専用に `CLAUDE_PLUGIN_SELF_DIAGNOSE=true` 等の環境変数で有効化。

### Option B: evolve 出力に「なぜスキップされたか」を明示（即時・低コスト）

現在の「カスタムスキル 0件のためスキップ」メッセージに、  
「rl-anything プラグイン本体を diagnostics に含めるには Option A を参照」等の説明を追記。  
少なくともユーザーの混乱を防ぐ。

### Option C: `find_artifacts()` に `plugin_skills` スキャンモードを追加（中期）

プラグイン本体リポジトリ（`.claude-plugin/plugin.json` が存在する場合）は  
`skills/` を追加スキャン対象として `origin = "plugin_self"` で返す。  
evolve はこれを `custom` と同等に扱う。

## 優先度

- Option B（メッセージ改善）: 即実装推奨
- Option A/C（本格対応）: v1.60.x 以降

## 発見経緯

セッション中に `/rl-anything:evolve` を実行した際、  
「3ヶ月以上セッションしているのになぜ Fitness Evolution が 0/30件なのか？」という疑問から調査。  
`find_artifacts()` の動作確認で構造的原因を特定。

> 💬 comment:
>
> PR #453（commit e8257be0、measurement_bug 実装）のメッセージに `closes #445, #185` とあるが、内容は measurement_bug（#445）のみで本 issue の主訴とは無関係。GitHub のキーワード文法（closes は issue ごとに必要）により誤 close を免れたため、本 issue は引き続き open が正。
> 
> ## 2026-06-12 時点のコード調査結果
> 
> **部分解消:**
> `find_project_skill_dirs()` は `.claude/skills/` と repo 直下 `skills/` の両方を走査するよう修正済み（scripts/lib/audit/artifacts.py:29-31、#423）。telemetry._find_all_skills も同関数に統一済み。
> 
> **未解消（根本バグ残存）:**
> `skill_evolve_assessment` / `pitfall_hygiene` が使う `find_artifacts()` 本体（artifacts.py:61-65）は依然 `.claude/skills/` のみ参照 → rl-anything PJ で evolve を実行するとプラグインスキルが 0 件扱いのまま。
> 
> Option C（origin="plugin_self" での自己診断対象化）は未実装。`assessment.py:269-270` は origin == "plugin" を continue で除外。
> 
> Option B（スキップ理由の明示）も plugin スキルについては未対応（excluded_globals サマリはあるが plugin スキップ理由は出ない）。
> 
> ## 残課題
> 
> - `find_artifacts()` を `find_project_skill_dirs()` へ統一（または skills/ スキャン追加）
> - skill_evolve / pitfall 剪定側の origin 扱い決定

---

## #186 feat(lifecycle): スキルライフサイクル管理の強化 — 貢献スコア追跡・Retirement・キャップ・Pre-flight 能動化  `[closed]`  (enhancement)

## 背景

2026-05-21 の AI トレンドレポートで「スキルの形式化とライフサイクル管理」が最重要テーマとして浮上。
Library Drift (arXiv:2605.19576)・HASP (arXiv:2605.17734) の2論文が rl-anything の現行実装と直接対応する課題を指摘している。

**核心問題**: rl-anything は「スキルが増える仕組み（evolve/discover）」は持つが、「スキルが腐敗する仕組みを検知・修復する」定量基盤がない。Library Drift 論文では LLM 自動生成スキルが人間キュレーション比 -16.2pp の性能差を生む「Library Drift」を実証しており、現状の prune スキル（ゼロ invocation 検出のみ）では不十分。

---

## タスク一覧（優先度順）

### 🔴 高: 貢献スコア追跡

**概念**: スキル invocation ごとに成否を追記専用ログに記録し、スキル別の貢献スコアを算出する。

**Before**: スキルが増えても「全体に効いているか」が不明。劣化は主観でしか気づけない  
**After**: audit レポートに各スキルの貢献スコア推移が表示され「劣化スキル」が定量的に見える

**実装方針**:
- `usage.jsonl` に `outcome: success|skip|error` フィールドを追加（hook 側で記録）
- `scripts/lib/quality_monitor.py` の `needs_rescore` を拡張してスキル別スコアを集計
- `audit` サマリーに `contribution_score` 列を追加

**確認方法**: `python3 scripts/lib/quality_monitor.py --summary` の出力に `contribution_score` 列が表示される

---

### 🔴 高: Retirement 機構（自動アーカイブ候補提案）

**概念**: 低貢献スキルを自動で「アーカイブ候補」としてフラグ立てし、cleanup で一掃できるようにする。

**Before**: 使われなくなったスキルが蓄積し続け、evolve が全体に効きにくくなる  
**After**: 低貢献スキルが自動で「アーカイブ候補」としてフラグ立てされ cleanup で一掃できる

**実装方針**:
- `scripts/lib/prune/detection.py` の `detect_decay` に「貢献スコアが N 回連続ゼロ → アーカイブ候補」ロジックを追加
- `prune` スキル実行後のレポートに「Retirement 候補」セクションを追加
- `cleanup` スキルと連携してアーカイブを実行

**確認方法**: `prune` スキル実行後のレポートに「Retirement 候補」セクションが出現し、90日以上 zero-invocation かつ貢献スコア 0.0 のスキルが一覧される

**再評価条件**: Library Drift コードが公開された場合（現在 CC BY-NC-SA 4.0 でコード未公開）

---

### 🟡 中: スキル数キャップ

**概念**: スキル数の上限を設け、上限到達時に新規追加前に Retirement を強制する。

**Before**: スキル数が無制限に増加し続ける（現在の prune はサイズではなく品質で判定）  
**After**: スキル数が上限に達したら新規追加前に Retirement を強制

**実装方針**:
- `audit` の summary に「現在スキル数 / 推奨上限」を表示（まずこれだけでも効果あり）
- 上限定数は `scripts/lib/config.py` に集約（`MAX_SKILL_COUNT`）
- `discover` / `evolve-skill` 実行時に上限チェックを挿入

**確認方法**: `audit` レポートに「スキル数: X / 推奨上限: Y」が表示される

**再評価条件**: スキル数が 100 を超えた場合

---

### 🟡 中: Pre-flight ガードレール能動化

**概念**: 現状の pitfalls.md は「コンテキスト注入型（受動）」。同一スキルで N 回 correction が発火した場合に事前警告を挟む「能動型」に進化させる。

**Before**: pitfall は「コンテキストに読み込まれる」受動型。失敗頻度の高い呼び出しパターンを能動的に検知しない  
**After**: 失敗確率の高い状態（例: 特定オプション組み合わせ）でスキル起動時に警告が自動発火

**実装方針**:
- `hooks/correction_detect.py` に「同一スキルで N 回以上 correction → pitfall_manager へのフラグ追加」ロジックを追加
- スキル起動 hook でフラグ済みスキルを検知し、Pre-flight 警告を挿入
- 閾値 N は `userConfig` に追加（デフォルト: 3）

**確認方法**: 同一スキルで3回以上 correction_detect フックが発火した後に、そのスキルを再呼び出すと pitfall 警告が表示される

**再評価条件**: pitfalls.md のエントリが 20 を超えた場合

---

## 参考論文

- [Library Drift (arXiv:2605.19576)](https://arxiv.org/abs/2605.19576) — 貢献スコア・Retirement・キャップの原典
- [HASP (arXiv:2605.17734)](https://arxiv.org/abs/2605.17734) — Pre-flight ガードレールの理論的裏付け

---

## #188 [tech-eval] HASP-style 失敗状態検知フック: セッション内エラーパターンから pitfall を能動的に push  `[open]`  (enhancement)

## 概要

HASP 論文（arXiv 2605.17734）が示す「エージェントが失敗しやすい状態で自動発動するガードレール」の設計を rl-anything に取り込む。

## Before（現状の体験）

- SKILL.md の Pre-flight チェックリストは **静的**（スキル実行前に LLM が受動的に読む）
- `correction_preflight_threshold` は「セッション内修正数」のカウントのみ。ツール呼び出しパターン・エラー種別は未検知
- エラーが連続しても pitfall guidance が能動的に push されない

## After（採用後の体験）

- セッション内のエラーパターン（直近 N ツール呼び出しで error outcome が閾値超過）を observe hook が検知
- 関連スキルの `pitfalls.md` を自動的に inject → LLM が次のアクション前に pitfall を参照する
- 「失敗しやすい状態に入ったら自動でガードレールが張られる」✨🛡

## 実装上のギャップ

| 現行 | HASP が求めるもの |
|------|-----------------|
| `suggest_preflight_script` (静的提案) | runtime state detector (動的検知) |
| `correction_preflight_threshold` = セッション修正数 | エラーパターン × スキル種別 × 直近履歴 |
| pitfall は手動参照 | pitfall を能動的に push するフック |

## 設計案

1. `observe_stop` フックで直近 N ターンの `outcome=error` を集計
2. 閾値超過 + スキルマッチで `pitfall_inject_candidates` を生成
3. 次ターンの system prompt に pitfall テキストを prepend（`correction_preflight_threshold` の拡張版）

## 再評価条件

`correction_preflight_threshold` のヒット率が週 3 回以上になったら実装優先度を上げる。

## 参考

- 論文: [HASP arXiv 2605.17734](https://arxiv.org/abs/2605.17734)
- 関連実装: `scripts/lib/pitfall_manager/preflight.py:85`、`scripts/lib/remediation/fixers_quality.py:114`
- tech-eval 日: 2026-05-21

---

## #189 [tech-eval] 階層型クロスセッションメモリ: working / episodic / semantic の3層設計で reflect を強化  `[closed]`  (enhancement)

## 概要

agentmemory（GitHub: rohitg00/agentmemory）・Glia・rohanpaul_ai の論文解説が独立して同一問題（AIコーディングエージェントの記憶喪失）に収束している。
rl-anything の現行 flat memory を3層階層に進化させ、`reflect` スキルのセッション横断精度を上げる。

## Before（現状の体験）

- `corrections.jsonl` + `auto-memory/MEMORY.md` は **flat 構造**
- セッションをまたぐと関連文脈が薄れ、同じ修正が繰り返される
- MEMORY.md への関連度スコアリングがなく、古い記憶と新しい記憶が同列に並ぶ

## After（採用後の体験）

- **working**: 現セッション内の短期記憶（既存の corrections.jsonl セッション分）
- **episodic**: 直近数セッションの重要決定・エラーパターン（TTL 付き）
- **semantic**: 長期的なプロジェクト方針・設計判断（MEMORY.md の永続層）
- `reflect` が「今のタスクに関連する記憶」を階層順に retrieve → 精度向上 ⚡✨

## 実装上のギャップ

| 現行 | 3層メモリが求めるもの |
|------|---------------------|
| flat JSONL + flat Markdown | working / episodic / semantic の分離ストア |
| 時系列 append のみ | 関連度スコアリング + TTL 管理 |
| grep ベースの検索 | 意味的 retrieve（embedding or BM25） |

## 設計方針（要検討）

1. 現行 `corrections.jsonl` を working 層として据え置き
2. episodic 層: セッション終了時に重要決定を DuckDB の episodic テーブルに昇格（TTL 30日）
3. semantic 層: 現行 MEMORY.md をそのまま活用
4. retrieve: まず semantic → episodic → working の順で関連エントリを引く

## 先行実施事項

- `/rl-anything:second-opinion` で agentmemory のアーキテクチャを評価してから設計
- agentmemory の benchmark 手法を `rl-scorer` の評価軸に取り込めるか確認

## 再評価条件

flat memory の retrieve miss が体感できるようになったとき（同じ修正が 3 セッション以上繰り返されたら着手）。

## 参考

- agentmemory: [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory)
- Glia: [Product Hunt 2026-05-19](https://www.producthunt.com/products/glia-2)（ブラウザ↔IDE コンテキスト共有）
- 論文: @rohanpaul_ai 「AIコーディングエージェントの階層メモリシステム」(2026-05-21)
- 関連実装: `scripts/lib/audit/usage.py`、`auto-memory/MEMORY.md`
- tech-eval 日: 2026-05-21

---

## #194 feat(eval): エージェント評価・メモリ選別の次世代化 (AgentAtlas / Insights Generator / Mem-π / 12-factor-agents)  `[closed]`

## 背景

2026-05-22 AI daily report の tech-eval から抽出。現在の telemetry fitness は success_rate のバイナリ計算しか行っておらず、「どのステップで・なぜ失敗したか」の情報を捨てている。4つの未実装概念をまとめて実装することで、evolve/audit/reflect の精度を段階的に向上させる。

---

## 実装タスク（優先順）

### 🔴 高優先度

#### 1. 軌跡失敗分類 (AgentAtlas)
- **概要**: タスク成否バイナリから「9カテゴリ失敗ラベル」への拡張
- **Before**: audit の失敗率は「何%失敗した」しか分からない
- **After**: 「ツール呼び出し失敗」「コンテキスト切れ」が区別でき、原因別に evolve 戦略を変えられる
- **実装箇所**: `scripts/rl/fitness/telemetry.py:227` の success_rate 計算を `error_type` フィールド対応に拡張 → `generate-fitness` に失敗カテゴリ軸を追加
- **確認方法**: `python3 scripts/rl/workflow_analysis.py --for-fitness` 出力に `error_category` フィールドが現れ、複数カテゴリが区別されること
- **再評価条件**: corrections.jsonl に error_type フィールドが整備されたタイミング

#### 2. コーパスレベル診断 (Insights Generator)
- **概要**: 個別トレースから「corrections 繰り返しパターン TOP-N」の統計集約
- **Before**: rl-loop 結果を1件ずつ見ても繰り返しパターンに気づけない
- **After**: corrections.jsonl 全体から「この失敗パターンが N 回繰り返されている」が自動で浮かぶ
- **実装箇所**: `scripts/lib/pipeline_reflector/` を拡張して corpus 集計を追加 → `audit` の出力に「繰り返し失敗パターン TOP-N」セクションを追加
- **確認方法**: `/rl-anything:audit` 出力に「繰り返し失敗パターン」セクションが現れ、同一 skill で 3 回以上繰り返されたパターンが列挙されること
- **再評価条件**: corrections が 50 件以上蓄積したタイミング

---

### 🟡 中優先度

#### 3. RL ベース メモリ選別 (Mem-π)
- **概要**: reflect の pre-filter に「重要度スコア」を導入（初期は heuristic、後に RL 化）
- **Before**: corrections が蓄積すると reflect のレビュー量が増え続ける
- **After**: 重要度の高い修正だけが自動昇格し、レビュー件数が抑制される
- **実装箇所**: `skills/reflect/SKILL.md` の閾値フィルタを heuristic importance スコアに置き換え
- **確認方法**: `reflect` 実行後に `corrections.jsonl` の `importance_score` フィールドが付与され、低スコア件数が減っていること
- **再評価条件**: 軌跡診断基盤が整い、重要度の定義が確立したタイミング（タスク1/2 完了後）

#### 4. ツール冪等性設計 (12-factor-agents Factor 5-6)
- **概要**: `evolve-skill` の pre-flight チェックリストに冪等性検出項目を追加
- **Before**: 副作用のあるスキルが重複実行時にサイレントに壊れても検知できない
- **After**: spec-keeper + evolve-skill が「冪等性違反」を pre-flight で検出し警告する
- **実装箇所**: `skills/evolve-skill/SKILL.md` の pre-flight セクションに idempotency チェック追加
- **確認方法**: `evolve-skill <skill>` の pre-flight 出力に `idempotency_check: pass/fail` が含まれること
- **再評価条件**: スキル副作用インシデントが発生したタイミング

---

## 参考文献

- [AgentAtlas: Beyond Outcome Leaderboards for LLM Agents](https://arxiv.org/abs/2605.20530)
- [Insights Generator: Systematic Corpus-Level Trace Diagnostics](https://arxiv.org/abs/2605.21347)
- [Mem-π: Adaptive Memory through Learning When and What to Generate](https://arxiv.org/abs/2605.21463)
- [humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents)

---

## #196 [tech-eval] evolve-skill に bounded edit operations を実装（SkillOpt 2605.23904）  `[closed]`  (enhancement)

## 概要

SkillOpt（arXiv 2605.23904、Microsoft Research、2026-05-25）の技術評価から。

現在の `evolve-skill` は LLM にスキル全文を渡して全書き換え（full rewrite）を提案させる。SkillOpt は weight-space optimization の規律をテキスト空間に転用し、**bounded edit operations（add/delete/replace のみ、差分 ≤30行）** で小さな更新のみを許可することで +19.1pt の改善を達成した。

## Before / After

- **Before**: evolve のたびにスキル全文が変わり diff が大きく、regression gate が逃しやすい
- **After**: 差分パッチのみ → diff が小さく regression gate が精度高く効く。LLMトークン40〜60%削減

## 実装方針

1. `evolve-skill` の LLM プロンプトに「unified diff 形式で ≤30行の変更のみ提案すること」制約を追加
2. `apply_evolve_proposal` で diff 行数が閾値超過したらリジェクト
3. `regression_gate` でスコア厳密改善チェック（現状 LLM judge → 数値比較へ強化）

## 工数概算

S（20〜30行）

## 参照

- arXiv: https://arxiv.org/abs/2605.23904
- 関連: #67（r^comp）、#68（r^fc）
- tech-eval: docs/tech-eval/2026-05-25 レポート

---

## #197 [tech-eval] discover に constraint decay 検出を追加（arXiv 2605.06445）  `[closed]`  (enhancement)

## 概要

Constraint Decay（arXiv 2605.06445、HN 241pt）の技術評価から。

LLM エージェントが長いコンテキスト中に「制約を徐々に忘れる（constraint decay）」現象を実証した研究。構造制約が累積するほど性能が最大 -30pt 低下する。現在の `spec-keeper` はセッション間ドリフトを見るが、**同一セッション内の decay は計測していない**。

## Before / After

- **Before**: 長い作業セッションで後半に指示が守られなくなっていても気づけない
- **After**: `discover` レポートに「後半 N ターンでの制約遵守率」が表示され警告される

## 実装方針

1. `discover/patterns.py` に `detect_constraint_decay()` を追加
2. `sessions.jsonl` の turn_index × `corrections.jsonl` の発生位置を突き合わせ
3. 「後半30%の correction 密度」を `session_decay_rate` として算出
4. 閾値（0.3）超過時に `discover` レポートに WARNING 表示

## 工数概算

M（50〜80行）

## 参照

- arXiv: https://arxiv.org/abs/2605.06445
- 既存関連: `critical_instruction_extractor.py`、`spec-keeper`
- tech-eval: docs/tech-eval/2026-05-25 レポート

---

## #198 [tech-eval] Stop hook でセッション終了時の自動メモリ更新（OpenViking）  `[closed]`  (enhancement)

## 概要

volcengine/OpenViking（ByteDance）の技術評価から。

現在の Stop hook は `evolve` 提案をするが **auto-memory の自動更新はしない**。毎回手動で `/reflect` を実行しないとセッションの学びがメモリに残らない。OpenViking のセッション終了時自動メモリ更新パターンを導入し、「手動 /reflect 忘れ」による情報ロスをなくす。

## Before / After

- **Before**: セッション後にメモリ更新を忘れると次回会話で情報が古いまま
- **After**: 毎セッション末に auto-memory が自動更新される

## 実装方針

1. Stop hook（`hooks/observe.py` 等）にセッション末の軽量 reflect ロジックを追加
2. 直近セッションの correction / pitfall を `auto-memory/` に自動 append
3. MEMORY.md が 200行超えたら自動 prune（oldest / low-importance を archive）
4. 既存 `reflect` スキルの軽量サブセットとして実装（LLM コール 1回以内）

## 工数概算

S（30〜50行）

## 参照

- https://github.com/volcengine/OpenViking
- 既存関連: `hooks/observe.py`、`reflect` スキル、auto-memory システム
- tech-eval: docs/tech-eval/2026-05-25 レポート

---

## #199 [tech-eval] evolve-skill に textual learning-rate budget を追加（SkillOpt 2605.23904）  `[closed]`  (enhancement)

## 概要

SkillOpt（arXiv 2605.23904）の技術評価から。中優先度。

1イテレーションで変更できる行数に上限（textual learning-rate budget）を設ける。#196（bounded edit operations）の補強として、「1回の evolve でスキルの半分以上が変わることがある」問題を構造的に防ぐ。

## Before / After

- **Before**: 1回の evolve でスキルの半分以上が変わることがある
- **After**: 変更上限があることで意図しない巻き添え変更を防ぐ

## 実装方針

1. `evolve-skill` の設定に `max_lines_per_iter: int = 30` を追加
2. apply 時に変更行数カウント → 超過時は最も重要な変更のみに絞り込んで再提案
3. `userConfig` に `skill_lr_budget` として公開

## 工数概算

S（15〜20行）

## 依存

#196（bounded edit operations）が先行実装されることが望ましい

## 参照

- arXiv: https://arxiv.org/abs/2605.23904

---

## #200 [tech-eval] rejected-edit buffer の学習フィードバックを evolve-skill に組み込む（SkillOpt 2605.23904）  `[closed]`  (enhancement)

## 概要

SkillOpt（arXiv 2605.23904）の技術評価から。中優先度。

現在 `trigger_engine/self_evolution.py:66-79` に rejected カウントは記録されているが、この情報が次回の `evolve-skill` 提案に活用されていない。SkillOpt は rejected edit の履歴を「prior」として次回提案に注入し、同じ失敗パターンを繰り返さないようにする。

## Before / After

- **Before**: 同じパターンのrejectが繰り返される
- **After**: rejected 履歴が次回提案の prior になり同じ失敗をしない

## 実装方針

1. `trigger_engine/self_evolution.py` の rejected 統計を JSON で出力する関数を追加
2. `evolve-skill` の pre-flight プロンプトに「過去に rejected された変更パターン」を注入
3. rejected 率 > 30% のスキルは evolve をスキップして `medium` 未満にダウングレード

## 工数概算

S（20行）

## 参照

- arXiv: https://arxiv.org/abs/2605.23904
- 既存: `trigger_engine/self_evolution.py:66-79`

---

## #201 [tech-eval] スキル変更への evidence attribution を追加（EVE-Agent 2605.22905）  `[closed]`  (enhancement)

## 概要

EVE-Agent（arXiv 2605.22905）の技術評価から。中優先度。

`evolve-skill` が提案するスキル変更に「どの correction が動機か」を示す逐語的ソーススパン（evidence）を付与する。「なぜこのスキルが変わったか」を後から追跡可能にし、根拠不明の変更（ゴースト修正）を排除する。

## Before / After

- **Before**: 「なぜこのスキルが変わったか」を後から追跡できない
- **After**: スキル変更に `motivated_by: corrections.jsonl#L42-L51` 形式の citation が付く

## 実装方針

1. `apply_evolve_proposal` 実行時に根拠となった `corrections.jsonl` の entry id リストを収集
2. SKILL.md の frontmatter または ADR に `evidence_refs: [entry_id_list]` として記録
3. `audit` でリンク切れ（参照先 correction が存在しない）を検出

## 工数概算

S（frontmatter 追記 + audit 検出）

## 参照

- arXiv: https://arxiv.org/abs/2605.22905
- 既存: `corrections.jsonl`、`evolve-skill`、`audit`

---

## #202 [tech-eval] per-skill 負の転移測定を audit に追加（Raw Experience 2605.23899）  `[closed]`  (enhancement)

## 概要

From Raw Experience to Skill Consumption（arXiv 2605.23899、Microsoft Research）の技術評価から。中優先度。

モデル生成スキルは平均的に有益だが、非自明な負の転移（追加したスキルが逆に他スキルの性能を下げる現象）を引き起こすことがある。現在の `regression_gate` は全体スコアを見るが、**per-skill の貢献度変化は測定していない**。

## Before / After

- **Before**: あるスキルが他スキルに干渉していても気づけない
- **After**: スキルごとの「追加後スコア差分」が可視化される

## 実装方針

1. `audit/usage.py` の `aggregate_contribution_scores`（v1.59.0）を拡張
2. スキル追加前後の fitness スコアを記録し `delta_score` を算出
3. `delta_score < -0.05` のスキルを `negative_transfer` issue として `audit` レポートに表示
4. `prune` の候補スコアに `negative_transfer` フラグを反映

## 工数概算

M（audit 拡張 40〜60行）

## 参照

- arXiv: https://arxiv.org/abs/2605.23899
- 既存: `audit/usage.py`、`aggregate_contribution_scores`、`regression_gate.py`

---

## #203 [tech-eval] meta-skill による品質フィルタを skill_triage に追加（Raw Experience 2605.23899）  `[closed]`  (enhancement)

## 概要

From Raw Experience to Skill Consumption（arXiv 2605.23899、Microsoft Research）の技術評価から。中優先度。

「どの経験特徴を抽出すべきか」を学ぶ meta-skill の概念を `skill_triage` に取り込む。現在の `skill_triage.py` は CREATE/UPDATE/SPLIT/MERGE/OK を判定するが、「どの実行ログ特徴がスキル化に値するか」の判断軸がない。これにより無駄なスキル追加・負の転移を削減する。

## Before / After

- **Before**: スキル追加後に性能が下がるケース（負の転移）を事前に防げない
- **After**: 抽出すべき経験特徴を明示化 → 無駄なスキル追加・負の転移を削減

## 実装方針

1. `skill_triage.py` に `meta_quality_check()` を追加
2. 判定軸: 汎用性（PJ固有か一般的か）・再利用頻度推定・既存スキルとの意味的重複度
3. meta_quality_check がスコア < 閾値のとき CREATE を SKIP に格下げ
4. 判定根拠を `triage_reason` フィールドに記録

## 工数概算

M（skill_triage 拡張 40〜60行）

## 参照

- arXiv: https://arxiv.org/abs/2605.23899
- 既存: `scripts/lib/skill_triage.py`
- 関連: #202（負の転移測定）

---

## #204 [tech-eval] MEMORY に L2 オンデマンド層を追加（OpenViking 3層コンテキスト重み付け）  `[open]`  (enhancement)

## 概要

volcengine/OpenViking の技術評価から。中優先度。

現在の auto-memory は MEMORY.md（L0: 一行）＋ 個別 .md ファイル（L1: 詳細）の2層構造。OpenViking の L2（全文オンデマンド読み込み）を追加し、重要度の低いメモリは L0 だけ渡して context を節約する。

## Before / After

- **Before**: 常に全 md ファイルが読まれ、低重要度のメモリも context を消費する
- **After**: 重要度低いメモリは L0（一行）だけ渡し L2 はオンデマンド → context 節約

## 実装方針

1. MEMORY.md の各エントリに `detail_file:` フィールドを追加（既存の個別 md が L2 に対応）
2. `memory/` ディレクトリの個別ファイルに `importance: high/medium/low` frontmatter を追加
3. `audit` で `importance: low` かつ `detail_file` 参照ありのメモリは MEMORY.md から L0 のみに縮約
4. 高重要度メモリ（feedback/project系）は L1 全文をロード維持

## 工数概算

M（audit + memory ファイル frontmatter 整備）

## 参照

- https://github.com/volcengine/OpenViking
- 既存: auto-memory システム、MEMORY.md、`audit/memory.py`
- 関連: #198（Stop hook 自動更新）

---

## #205 [tech-eval] スキル廃棄ロジック — SkillOpt (arXiv 2605.23904) の品質スコア連動自動退役を実装  `[closed]`

## 概要

Microsoft Research の SkillOpt (arXiv 2605.23904) が提案する「エグゼクティブ層によるスキル廃棄」を rl-anything の `evolve-skill` / `prune` に組み込む。現在、`prune` は手動トリガー型で自動廃棄条件が定義されていない。低品質スキルが蓄積すると、AI が古いロジックを踏み続け学習ループの精度が劣化する。

## Before / After（ユーザー体験の変化）

| | 状態 |
|---|---|
| **Before** | 低スコア・obsolete スキルがスキルプールに蓄積し続け、AI が古い判断を繰り返す |
| **After** | evolve-skill の 5 軸スコアが閾値以下 + 最終使用から N 日超のスキルが自動廃棄候補にリストアップされ、`prune` フローへ直結 |

## 現行実装とのギャップ

- `evolve-skill/SKILL.md` に 5 軸スコアリング（実行頻度・失敗多様性・評価可能性・外部依存度・判断複雑さ）は実装済み
- `prune/SKILL.md` にスキル削除フローは存在するが、スコア連動の自動廃棄条件がない
- `pitfall-similarity.ts` に TF-IDF 重複検出あり（衝突検出の一部は実装済み）
- **不足**: 「スコア < 閾値 AND 最終使用 > N 日」を満たすスキルを廃棄候補フラグとして出力する Step

## 実装案

1. `evolve-skill` の Step 末尾に「廃棄候補評価」フェーズを追加
   - 5 軸合計 < 8 (満点 15) AND `last_used` > 30 日 → `retirement_candidate: true` を metadata に記録
2. `prune/SKILL.md` に「retirement_candidate フラグ付きスキルを自動リストアップ」するステップを追加
3. `audit --coherence-score` のレポートに廃棄候補数を表示

## 採用後の確認方法

- [ ] `rl-anything:evolve-skill <skill>` 実行後に `retirement_candidate` フィールドが SKILL.md metadata に出力されること
- [ ] `rl-anything:audit --coherence-score` で廃棄候補スキルがリストアップされること
- [ ] `prune` フロー実行時に retirement_candidate フラグ付きスキルが自動候補に上がること

## 再評価条件

pitfall 数が 250 を超えるか、audit で obsolete スキルが 5 件以上検出された時点で即着手。

## 参考

- arXiv: https://arxiv.org/abs/2605.23904
- tech-eval 日付: 2026-05-25
- 関連スキル: `evolve-skill`, `prune`, `audit`

> 💬 comment:
>
> figma-to-code の evaluate-human.ts 実装として todoroki-godai/figma-to-code に移管。スコープ誤りのためクローズ。

---

## #206 [tech-eval] 制約劣化（Constraint Decay）の定量化 — fold ごとの制約遵守スコアを GPS として記録  `[closed]`

## 概要

arXiv 2605.06445 (Constraint Decay) が実証した「長期タスクで LLM が制約を徐々に忘れる現象」に対し、rl-anything のスキル実行ループおよび figma-to-code の `evaluate:human` に制約持続性スコア (GPS 相当) を導入する。

## Before / After（ユーザー体験の変化）

| | 状態 |
|---|---|
| **Before** | evaluate:human が全 fold PASS を返しても「後段 fold で制約が崩れていたか」が分からない |
| **After** | `constraint_decay_fold` フィールドで何番目の fold で制約が失われたかが可視化され、修正箇所の特定が即座にできる |

## 現行実装とのギャップ

- `scripts/evaluate-human.ts` は各 fold に `viewportContextBlurb` / `foldCtx` でリマインダー注入済み（部分対応）
- fold ごとのスコアは `fold_scores` として JSON 出力済み
- **不足**: 「前 fold から後 fold にかけてスコアが > 10pt 低下した fold 番号」を `constraint_decay_fold` として自動フラグ化する処理

## 実装案

1. `evaluate-human.ts` の fold ループ後処理に以下を追加:
   ```ts
   const decayFolds = foldScores
     .map((s, i) => i > 0 && foldScores[i - 1] - s > 10 ? i : -1)
     .filter(i => i >= 0);
   output.constraint_decay_fold = decayFolds;
   ```
2. CLAUDE.md の「評価系 defect 見逃し」チェックリストに `constraint_decay_fold: []` 確認を追加
3. rl-anything の `rl-loop-orchestrator` に「constraint_decay_fold が空でない場合はスキル blurb を強化して再実行」トリガーを追加

## 採用後の確認方法

- [ ] `npm run evaluate:human -- --slug=minna-works` の出力 JSON に `constraint_decay_fold` フィールドが存在すること
- [ ] fold スコアの分散 > 15pt のケースで `constraint_decay_fold` が正しい fold 番号を返すこと

## 再評価条件

fold スコアの分散が > 15pt になるケースが 2 件以上観測された時点で即着手。

## 参考

- arXiv: https://arxiv.org/abs/2605.06445 (HN 241pt, 137 comments)
- tech-eval 日付: 2026-05-25
- 関連スクリプト: `scripts/evaluate-human.ts`, `scripts/lib/pitfall/`

> 💬 comment:
>
> figma-to-code の evaluate-human.ts 実装として todoroki-godai/figma-to-code に移管。スコープ誤りのためクローズ。

---

## #223 feat(fitness): 日次 evolve でスキル diff 提案の accept/reject を採点付きで蓄積する  `[closed]`

## 背景・問題
`fitness_evolution`（適応度関数の重みを人間フィードバックとの相関で進化させる機構）が、サンプル不足（0/30件）で実質デッドフィーチャー化している。母集団が `optimize`/`rl-loop` の accept/reject イベント（`history.jsonl` の `human_accepted is not None`）に限定されており、「1日1回 evolve」程度の運用では永遠に貯まらない。

## 方針（senior-engineer 2回の判断で確定）
ユーザーの直感「evolve 内の accept/reject 出来事を使う」は成立する。鍵は **accept/reject の対象を fitness 関数でその場採点して `best_fitness` を正規付与する**こと。これで optimize と同一の量になり、混合ではなく増量＝相関が壊れない。

### 正規サンプルになりうるイベント
- evolve の Compile/remediation での **スキル diff 提案**（SKILL.md の改善提案）の accept/reject。対象が SKILL.md content なので `evaluate_skill_quality(after_content, skill_dir)`（`scripts/rl/fitness/skill_quality.py` L160-175）で採点でき、意味論も「スキル品質スコア vs 人間判断」で一致。

### 混ぜてはいけないイベント
- remediation の構造修正（BLOCK/WARN、auto_fixable な機械修正）— fitness 採点対象オブジェクトでない。
- discover の rule/hook candidate、reorganize/prune — skill_quality の入力契約に乗らない。
- skill_evolve 提案 — 採点可能だが human_accepted の分散が小さく相関の情報量が乏しい。**当面は source ラベルで記録のみ、相関母集団からは除外**。

## 実装スコープ（最小 PR）
- (a) `fitness_evolution.py` の `insufficient_data` メッセージに「optimize/rl-loop/evolve diff 提案の accept/reject が母集団」と明記。`best_fitness=None` を相関母集団に入れないガード（現 L82 付近）をテストで固定。
- (b) **採点ブリッジ**: evolve の Compile/remediation でスキル diff を accept/reject した時点で、after content を `evaluate_skill_quality` に通し `(best_fitness, human_accepted, fitness_func="skill_quality", source="evolve_remediation")` を `history.jsonl` に正規記録する関数を1つ追加（書き込みは既存 `save_history_entry` を再利用）。
- (c) `analyze_correlations` を **`fitness_func` でグループ化**してから相関を取るよう変更（異種採点の混合防止の本丸。source ラベルは記録のみ、相関では fitness_func を使う）。

## 参照
- `skills/evolve-fitness/scripts/fitness_evolution.py` L74-100, L157-214
- `scripts/rl/fitness/skill_quality.py` L160-175
- `skills/evolve/SKILL.md` L102-104, L123-131
- `skills/genetic-prompt-optimizer/scripts/optimize.py` L301-342

## 補足
research-best-practices の知見: Online RLHF（policy生成→人間label→iterative蓄積）の正攻法に合致。30件閾値は相関分析の統計的下限として妥当（n=25→99 で type II error 改善）。noise対策として fitness_func グループ化が必須。

---

## #225 feat(evolve): skill_evolve LLM batch guard が all-or-nothing — グループ単位の選択スキップ + 永続スキップリストを追加  `[closed]`  (enhancement)

## Problem

evolve の `skill_evolve_assessment`（スキル自己進化適性判定、SKILL.md の Step 3.6）における LLM batch guard が **all-or-nothing** でブロックするため、対象スキルが多い PJ で実質的に評価不能になる。

### 事実（コードで確認済み）
- 実装: `scripts/lib/skill_evolve/assessment.py:70-88`
- 定数: `_TOKENS_PER_SKILL = 47_000`、`_MAX_AUTO_SKILLS = 10`
- 挙動: 対象スキル（custom + `evolve_global_allowlist` に載った global）が **10件を超えると `RuntimeError` で evolve 全体がブロック**される。
  - エラーメッセージ例:
    ```
    [llm-batch-guard] skill_evolve_assessment: 14件のスキルが対象です。
    推定トークン消費: 658,000 tokens (14 × 47,000)。
    evolve --skip-llm-evolve で LLM 評価をスキップできます。
    ```
- 現状の唯一の逃げ道は `--skip-llm-evolve`（**全スキップ = all-or-nothing**）。一部だけ評価する手段がない。
- global スキルはデフォルトで除外され、`evolve_global_allowlist`（manifest userConfig、カンマ区切り）に明示追加したものだけが対象に入る（= allowlist 方式）。

### ユーザーの実際の痛み
- docs-qa PJ で 14 スキル → 658,000 tokens でブロックされ、判断材料も乏しく対応に困った。
- 過去 gstack 関連スキルは膨大すぎて評価を丸ごとスルーした経験がある。
- 「スキルのグループ（種類）ごとに、進化させる価値があるか／スキップすべきかを判断したい」「スキップした global スキルは "スキップリスト" に貯めて次回以降も自動でスキップしたい」という要望。

## Proposed Solution

1. **グループ単位の選択スキップ**
   10件超でも全ブロックせず、スキル群を種類（custom / global / プラグイン由来 / 大規模 = 行数やトークン見積もりが大きいもの等）でグルーピングし、グループ単位で「評価する／スキップする」を選べるようにする。

2. **永続スキップリスト（denylist）**
   「進化させない」と判断したスキル（特に gstack のような巨大 global 群）を永続スキップリストに記録し、次回 evolve から自動的に LLM 評価対象外にする。現状の allowlist（含める側）に対する denylist（除外する側）の追加。manifest userConfig か専用ファイルでの永続化を検討。

3. **判断材料の提示**
   グループ／スキルごとに「対象件数・推定トークン・進化適性スコアの目安」を提示してから選ばせる（all-or-nothing をやめる）。

## 関連コード
- `scripts/lib/skill_evolve/assessment.py:70-88`（batch guard 本体）
- `evolve_global_allowlist`（manifest userConfig、現状の allowlist 方式）

---

## #231 [tech-eval] Co-ReAct: ステップレベル・ルーブリック評価を fitness/plugin.py に統合  `[closed]`

## 概要

Co-ReAct (arXiv 2605.23590) の知見を rl-anything の evolve フローに適用する。
ReAct エージェントの各推論ステップに評価ルーブリックを協調させることで、evolve の品質収束を速める。

## Before / After（ユーザー体験の変化）

- **Before**: evolve 品質が一括評価のため「どのステップで詰まっているか」が不透明。試行回数が多くなりがち
- **After**: 各 evolve ステップにルーブリックスコアが付き、ボトルネックが可視化される ⚡

## 既存実装との差分

- `scripts/bench/spike_rl_scorer_output_eval.py:299` にルーブリックの言及あり（spike のみ、production 未統合）
- `scripts/rl/fitness/plugin.py` の fitness 関数は現状ステップ単位のフィードバックを持たない
- 追加すべき: fitness 評価の各フェーズ（propose → apply → verify）にルーブリックチェックポイントを挟む

## 採用後の確認方法

```bash
python3 -m pytest scripts/rl/tests/ -k rubric -v
# → rubric スコアが各 evolve ステップのレポートに含まれること
```

## 再評価条件

evolve 成功率が 80% 超になったら不要

## 参考

- arXiv: https://arxiv.org/abs/2605.23590
- tech-eval 実施日: 2026-05-26

---

## #232 [tech-eval] CoSPlay: qa-only スキルを evolve フローから自動呼び出しに変更  `[open]`

## 概要

CoSPlay (arXiv 2605.23491) の「テスト時自己対戦」パターンを rl-anything の evolve フローに適用する。
現状は人間が qa-only をトリガーする必要があるが、evolve が自動でテスト生成 → 自己評価 → 修正の1ループを完結できるようにする。

## Before / After（ユーザー体験の変化）

- **Before**: qa-only スキルは人間トリガー依存。evolve の自己検証なし、品質担保が手動
- **After**: evolve が自動でテストを生成→自己評価→修正の1ループを完結 ⚡🛡

## 既存実装との差分

- `qa-only` スキルは独立スキルとして存在するが、`evolve` フローから自動呼び出されていない
- CoSPlay のポイント: **追加学習なし**にテスト時の自己対戦だけで精度改善可能
- 追加すべき: `evolve` の propose フェーズ後に、生成された diff を対象に qa-only を自動起動し、失敗時は再提案ループに戻す

## 採用後の確認方法

```bash
# dry-run で QA ループが自動起動することを確認
python3 -m rl_anything evolve --dry-run <skill-name>
# → QA ループが自動起動して結果が出力されること
```

## 再評価条件

手動 QA トリガーが週 0 回になったら完了

## 参考

- arXiv: https://arxiv.org/abs/2605.23491
- tech-eval 実施日: 2026-05-26

> 💬 comment:
>
> ## 設計ブロッカー
> 
> /plan-eng-review + outside voice レビューで設計上の問題が判明。
> 
> **問題**: `fitness/plugin.evaluate()` はキーワード存在チェッカー。before/after の evolve 品質比較に使うのは category error。delta がキーワード増減を測定し、evolve 品質を測定しない。±0.05 閾値はノイズ内。
> 
> **正しい実装案**: rl-scorer の pre-application 自動呼び出し（haiku/sonnet コストが発生）
> 
> **TODO before implementation**:
> - [ ] rl-scorer を pre-application で自動呼び出しするコスト見積もり
> - [ ] 代替: 補足的定量指標（行数増加 / セクション追加数 / pitfall 追加有無）の設計

---

## #233 [tech-eval] AlphaSignal記憶強化: memory_temporal.py に importance_score / access_count を追加  `[closed]`

## 概要

AlphaSignal が紹介した「Karpathy LLMウィキベース記憶強化リポジトリ」の設計思想を取り込む。
「すべての事実を等価に扱うことの問題点」を解消するため、記憶エントリに重要度スコア・アクセス頻度・強化/弱化を導入する。

## Before / After（ユーザー体験の変化）

- **Before**: 全メモリが等価扱い。古い・低頻度の情報がコンテキスト上位に残り続ける
- **After**: 重要度スコアで上位メモリが優先され、関連性の高い記憶が優先的に活用される ⚡✨

## 既存実装との差分

`scripts/lib/memory_temporal.py` に既存フィールド:
- `decay_days` (TTL ベースの失効) ✅
- `superseded_at` (明示的な無効化) ✅
- `valid_from` (有効期間) ✅

**未実装**（追加すべきフィールド）:
- `importance_score: float` — 0.0〜1.0 の重要度。手動設定 or auto_memory_runner が推定
- `access_count: int` — 参照された回数。参照のたびにインクリメント
- `last_reinforced_at: str` — 最後に強化された日時

## 実装方針

1. `parse_memory_temporal()` に上記3フィールドのパースを追加
2. `auto_memory_runner.py` で新規メモリ生成時に `importance_score` を LLM で推定して付与
3. audit レポートの memory セクションに「低スコアメモリ候補」を表示

## 採用後の確認方法

```bash
# 全メモリに importance_score フィールドが存在すること
grep -rn "importance_score" ~/.claude/projects/*/memory/*.md
python3 -m pytest scripts/tests/test_memory_temporal.py -v
```

## 再評価条件

関連度の低いメモリがコンテキスト上位に残るケースがなくなったら完了

## 参考

- AlphaSignal tweet: https://x.com/AlphaSignalAI/status/2043660604351660314
- tech-eval 実施日: 2026-05-26

---

## #238 [tech-eval] feat(evolve): CODESKILL — トラジェクトリからスキル自動生成 + 実行スパース報酬ループ  `[closed]`  (enhancement)

## 背景

CODESKILL (arXiv:2605.25430) の技術評価から。推奨度**高×2**。

コーディングエージェントが自分の実行トラジェクトリからスキルを自動生成し、rubric 評価 + 実行スパース報酬の二重フィードバックで品質管理、サイズ安定したスキルバンクを維持するフレームワーク。SWE-Bench で +9.69% の精度向上を実証。コード公開済み。

現在の `evolve-skill` は「人間が書いたスキルを LLM でプロンプト改善する」段階にある。CODESKILL の核心は「**エージェントが実行経験からスキルを自動生成し、実行結果で選別する**」という次の進化段階。

## Before / After

- **Before**: スキルは全て人間が書く。何が効いているか実行結果から自動判定できない
- **After**: sessions.jsonl のトラジェクトリから新スキル候補が自動生成 ✨。telemetry の success/failure が evolve-skill への直接フィードバックになる ⚡

## 実装スコープ

### 1. トラジェクトリ → スキル自動抽出（推奨度: 高）
- CODESKILL リポジトリの skill extraction モジュールを読み、sessions.jsonl との互換性を評価
- `trigger_eval_generator.py` の延長線上に skill extraction パイプラインを構築
- 出力: スキル候補 JSONL → skill-triage の CREATE 判定に接続

### 2. 実行結果スパース報酬ループ（推奨度: 高）
- `telemetry.py` の trigger success/failure 率を `evolve-skill` フィードバックに接続
- 現在のルールベース rubric (`skill_quality.py`) に「実際に使われて成功したか」の実績軸を追加
- 設計: dense feedback (skill_quality rubric) + sparse reward (telemetry success) の二重評価

## 採用後の確認方法

- [ ] CODESKILL リポジトリのコードを読み、sessions.jsonl トラジェクトリ形式との差分を評価
- [ ] `python3 scripts/lib/trigger_eval_generator.py` で evals.json 生成を確認 → CODESKILL の rubric 評価と統合テスト
- [ ] `audit --fitness telemetry` でスキル別 success_rate を確認 → スパース報酬の入力データとして使用

## 再評価条件

evolve-skill が月3回以上手動実行されるようになったとき（需要確認）

## 参照

- CODESKILL: https://arxiv.org/abs/2605.25430
- 既存: `scripts/lib/trigger_eval_generator.py`, `scripts/rl/fitness/skill_quality.py`, `scripts/rl/fitness/telemetry.py`
- 関連: #185（プラグイン自己診断ギャップ）

> 💬 comment:
>
> ## アーキテクチャ設計
> 
> ---
> 
> ### 全体データフロー
> 
> ```
> ┌─────────────────────────────────────────────────────────────────┐
> │                     CODESKILL 統合パイプライン                      │
> │                                                                   │
> │  sessions.jsonl                                                   │
> │  ~/.claude/projects/<pj>/*.jsonl                                  │
> │       │                                                           │
> │       ▼                                                           │
> │  [Phase 1] skill_extractor.py                                     │
> │  ┌──────────────────────────────────────────────────────────┐    │
> │  │  TrajectorySampler                                        │    │
> │  │  ・type=user/assistant ターンを抽出                         │    │
> │  │  ・skill 呼び出しターン (command-name タグ) を特定           │    │
> │  │  ・呼び出し前後の context window を取得                      │    │
> │  └──────────────────────────────────────────────────────────┘    │
> │       │                                                           │
> │       │  trajectory_records: List[TrajectoryRecord]               │
> │       ▼                                                           │
> │  ┌──────────────────────────────────────────────────────────┐    │
> │  │  SkillCandidateGenerator                                  │    │
> │  │  ・繰り返しパターン(≥2回)のクラスタリング                     │    │
> │  │  ・LLM 1パス: trigger + description ドラフト生成             │    │
> │  │  ・出力: skill_candidates.jsonl                             │    │
> │  └──────────────────────────────────────────────────────────┘    │
> │       │                                                           │
> │       │  skill_candidates.jsonl                                   │
> │       ▼                                                           │
> │  ┌──────────────────────────────────────────────────────────┐    │
> │  │  QualityFilter (LLM コストゼロ)                             │    │
> │  │  ・skill_quality.py の CSO rubric → generalizability score │    │
> │  │  ・meta_quality.py の重複/低頻度フィルタ                     │    │
> │  │  ・スコア < 0.5 → SKIP, 0.5-0.7 → REVIEW, ≥ 0.7 → PASS   │    │
> │  └──────────────────────────────────────────────────────────┘    │
> │       │                                                           │
> │       │  filtered_candidates.jsonl                                │
> │       ▼                                                           │
> │  [既存] skill_triage.py の CREATE パスに接続                       │
> │  ┌──────────────────────────────────────────────────────────┐    │
> │  │  triage_skill(..., source="codeskill_extraction")         │    │
> │  │  ・missed_skills 相当のエビデンスとして注入                   │    │
> │  │  ・meta_quality_check → CREATE/REVIEW/SKIP                │    │
> │  └──────────────────────────────────────────────────────────┘    │
> └─────────────────────────────────────────────────────────────────┘
> ```
> 
> ---
> 
> ### Phase 1: トラジェクトリ → スキル抽出パイプライン
> 
> #### 新規ファイル: `scripts/lib/skill_extractor.py`
> 
> **責務の分割（500行制限遵守）:**
> 
> ```
> scripts/lib/
> ├── skill_extractor.py          # エントリポイント + オーケストレーション (≤200行)
> ├── trajectory_sampler.py       # sessions.jsonl からのターン抽出 (≤200行)
> └── candidate_generator.py      # パターンクラスタリング + LLM 生成 (≤300行)
> ```
> 
> **TrajectoryRecord 型定義:**
> ```python
> @dataclass
> class TrajectoryRecord:
>     session_id: str
>     skill_name: str        # command-name タグから抽出
>     user_prompt: str       # スキル呼び出し直前の user ターン
>     outcome_hint: str      # 呼び出し直後の assistant ターン冒頭 (200字)
>     timestamp: str
>     project: str
> ```
> 
> **抽出ロジック (trajectory_sampler.py):**
> - `type=user` かつ content に `<command-name>/xxx</command-name>` を含むターンを検出
> - 直前の `type=user` ターンの `.message.content` を `user_prompt` として取得
> - 直後の `type=assistant` ターン冒頭 200 字を `outcome_hint` として取得
> - `outcome_hint` に "Error", "失敗", "できません" が含まれる → `outcome=failure`
> - それ以外 → `outcome=success`
> 
> **パターンクラスタリング (candidate_generator.py):**
> - 同一スキルの `user_prompt` を Jaccard クラスタリング（閾値 0.6）
> - クラスタサイズ ≥ 2 のみを候補に昇格
> - LLM 1パス: クラスタの代表プロンプト 3件 → `description` + `trigger` キーワードリスト生成
>   - モデル: Haiku（コスト最小化）
>   - 入力制限: クラスタあたり 3 件 × 300 字以内
> - 出力: `skill_candidates.jsonl`
> 
> **skill_candidates.jsonl スキーマ:**
> ```jsonl
> {
>   "skill_name": "xxx",
>   "source": "codeskill_extraction",
>   "cluster_size": 5,
>   "success_rate": 0.8,
>   "representative_prompts": ["...", "...", "..."],
>   "draft_description": "...",
>   "draft_triggers": ["keyword1", "keyword2"],
>   "generated_at": "2026-05-27T..."
> }
> ```
> 
> **generalizability_score 計算（LLM コストゼロ）:**
> ```
> generalizability_score =
>   (1 - is_specialized_ratio) * 0.4     # meta_quality._is_specialized
>   + (cso_score)               * 0.4     # skill_quality.check_cso_compliance
>   + min(cluster_size / 10, 1) * 0.2    # 証拠量
> ```
> 
> **skill_triage への接続:**
> ```python
> # triage_skill の missed_skills 引数に変換して注入
> missed_skill_entry = {
>     "skill": candidate["skill_name"],
>     "session_count": candidate["cluster_size"],
>     "triggers_matched": candidate["draft_triggers"],
>     "source": "codeskill_extraction",
> }
> ```
> 
> ---
> 
> ### Phase 2: 実行スパース報酬ループ
> 
> #### データフロー図
> 
> ```
> ┌──────────────────────────────────────────────────────────────────┐
> │                  スパース報酬フィードバックループ                      │
> │                                                                    │
> │  telemetry.py                                                      │
> │  score_implicit_reward() 内の per-skill success/failure カウント   │
> │       │                                                            │
> │       ▼                                                            │
> │  [新規] reward_aggregator.py                                       │
> │  ┌───────────────────────────────────────────────────────────┐    │
> │  │  aggregate_skill_rewards(project_dir, days=30)            │    │
> │  │  → {skill_name: {success: N, failure: M, rate: 0.xx}}     │    │
> │  └───────────────────────────────────────────────────────────┘    │
> │       │                                                            │
> │       ▼                                                            │
> │  [統合スコア] double_eval_score                                     │
> │  ┌───────────────────────────────────────────────────────────┐    │
> │  │  dense_score  = skill_quality CSO rubric score  (weight 0.6) │ │
> │  │  sparse_score = telemetry success_rate          (weight 0.4) │ │
> │  │  combined     = dense * 0.6 + sparse * 0.4                │    │
> │  │                                                           │    │
> │  │  閾値:                                                    │    │
> │  │  combined < 0.4 → evolve-skill トリガー (自動改善)         │    │
> │  │  0.4 ≤ combined < 0.6 → REVIEW フラグ                    │    │
> │  │  combined ≥ 0.6 → OK (改善不要)                           │    │
> │  └───────────────────────────────────────────────────────────┘    │
> │       │                                                            │
> │       ▼                                                            │
> │  evolve-skill (既存)                                               │
> │  ┌───────────────────────────────────────────────────────────┐    │
> │  │  --fitness double_eval フラグで起動                          │    │
> │  │  sparse_reward を pitfall.md 自動追記のエビデンスとして使用    │    │
> │  │  "このスキルは X% の確率で失敗後 correction が発生"           │    │
> │  └───────────────────────────────────────────────────────────┘    │
> └──────────────────────────────────────────────────────────────────┘
> ```
> 
> #### 新規ファイル: `scripts/lib/reward_aggregator.py`
> 
> **責務:** telemetry.py の `score_implicit_reward` 内部ロジックを per-skill 粒度で再集計
> 
> ```python
> def aggregate_skill_rewards(
>     project_dir: Path,
>     days: int = 30,
> ) -> Dict[str, SkillReward]:
>     """
>     Returns:
>         {
>           "commit": SkillReward(success=10, failure=2, rate=0.833),
>           "review":  SkillReward(success=5,  failure=3, rate=0.625),
>           ...
>         }
>     """
> ```
> 
> **telemetry.py の変更（最小限）:**
> - `score_implicit_reward` の内部ロジックをそのまま維持
> - `_per_skill_success_stats()` として per-skill カウント部分を private 関数として切り出し
> - `reward_aggregator.py` からその private 関数を `from telemetry import _per_skill_success_stats` で参照
> 
> #### `scripts/rl/fitness/double_eval.py`（新規 fitness 関数）
> 
> ```python
> # 既存の skill_quality.py + reward_aggregator.py を組み合わせ
> # --fitness double_eval で evolve コマンドから使用可能にする
> 
> def evaluate_double_eval(content: str, skill_dir: str) -> Optional[Dict[str, Any]]:
>     dense_result = evaluate_skill_quality(content, skill_dir)
>     sparse_result = _get_sparse_reward(skill_dir)
>     combined = dense_result["overall"] * 0.6 + sparse_result["rate"] * 0.4
>     return {
>         "overall": combined,
>         "dense": dense_result,
>         "sparse": sparse_result,
>     }
> ```
> 
> ---
> 
> ### テスト計画
> 
> #### Unit Tests
> 
> | テスト対象 | Mock 箇所 | テストシナリオ |
> |------------|-----------|--------------|
> | `trajectory_sampler.py` | `open()` (sessions.jsonl) | command-name タグ有無、outcome=success/failure 判定 |
> | `candidate_generator.py` | `subprocess.run(["claude", ...])` (LLM呼び出し) | クラスタサイズ < 2 → スキップ、Jaccard クラスタリング正常動作 |
> | `reward_aggregator.py` | `telemetry_query.query_usage`, `query_corrections` | corrections 60秒以内判定、per-skill 集計の正確性 |
> | `double_eval.py` | `evaluate_skill_quality()`, `_get_sparse_reward()` | dense 0.8 + sparse 0.2 → combined 0.56 (REVIEW) |
> | `skill_triage.py` の接続 | `generate_eval_set()` | source="codeskill_extraction" で missed_skills 相当に変換 |
> 
> **No-LLM Guard:** `candidate_generator.py` の LLM 呼び出しは単体テストで必ず mock。
> `conftest.py` の guard が検出するため `RL_ALLOW_LLM_IN_TESTS=1` なしでは通過しない。
> 
> #### Integration Tests
> 
> **シナリオ 1: 実機 1PJ E2E ベンチ**
> ```
> 1. ~/.claude/projects/<pj>/*.jsonl を walk (--max-files 50 でサンプリング)
> 2. trajectory_sampler で TrajectoryRecord を抽出
> 3. candidate_generator でドラフト生成 (LLM はモック)
> 4. quality_filter でスコアリング
> 5. skill_triage の CREATE パスを通過するか確認
> 成功基準: wall time < 10s, 候補 ≥ 1 件生成
> ```
> 
> **シナリオ 2: スパース報酬の入力確認**
> ```
> 1. audit --fitness telemetry で既存スキルの success_rate を取得
> 2. reward_aggregator.aggregate_skill_rewards() の出力と一致確認
> 成功基準: 全スキルで rate が [0.0, 1.0] に収まる
> ```
> 
> ---
> 
> ### 実装ファイル一覧
> 
> | ファイル | 区分 | 行数目安 | Phase |
> |---------|------|---------|-------|
> | `scripts/lib/trajectory_sampler.py` | 新規 | ~200 | 1 |
> | `scripts/lib/candidate_generator.py` | 新規 | ~280 | 1 |
> | `scripts/lib/skill_extractor.py` | 新規 | ~150 | 1 |
> | `scripts/lib/reward_aggregator.py` | 新規 | ~120 | 2 |
> | `scripts/rl/fitness/double_eval.py` | 新規 | ~100 | 2 |
> | `scripts/rl/fitness/telemetry.py` | 変更（最小限）| +20行 | 2 |
> | `scripts/lib/skill_triage.py` | 変更（接続点追加）| +30行 | 1 |
> | `scripts/tests/test_trajectory_sampler.py` | 新規テスト | ~150 | 1 |
> | `scripts/tests/test_candidate_generator.py` | 新規テスト | ~120 | 1 |
> | `scripts/tests/test_reward_aggregator.py` | 新規テスト | ~100 | 2 |
> | `scripts/tests/test_double_eval.py` | 新規テスト | ~80 | 2 |
> 
> ---
> 
> ### 未解決の判断ポイント（実装前に確認）
> 
> 1. **LLM 呼び出し件数の事前承認**: `candidate_generator.py` の LLM 呼び出しはクラスタ数に比例する。実装前に `find ~/.claude/projects -name "*.jsonl" | wc -l` でセッション数を計測し、見積もりトークン数をユーザーに提示して確認を取る（`llm-batch-guard` ルール）。
> 
> 2. **double_eval の weight 調整**: dense 0.6 / sparse 0.4 は仮値。初期 audit の結果を見て調整する。`config.py` に `DOUBLE_EVAL_WEIGHTS` として集約しハードコードを避ける。
> 
> 3. **transcript-store-bench 必須**: `trajectory_sampler.py` は `~/.claude/projects/` を walk する。実装前に `find ~/.claude/projects -name "*.jsonl" | wc -l` と `du -sh ~/.claude/projects/` で規模感を取り、`--max-files N` サンプリング + timeout を bench script に含める（`transcript-store-bench` ルール）。

---

## #239 [tech-eval] feat(memory): ストレージゲーティング + 意味検索レイヤー（Personalize-then-Store / honcho）  `[closed]`  (enhancement)

## 背景

Personalize-then-Store (arXiv:2605.25535) + plastic-labs/honcho の技術評価から。推奨度**高+中**。

- **Personalize-then-Store**: 「何を記憶するか（ゲーティング）→どう個人化するか→どう保存するか」の3段階分解と PerMemBench 評価フレームワーク。コード・データセット公開済み
- **honcho**: セッション間永続メモリ、ユーザーモデル更新、意味検索による記憶取得のPythonライブラリ

現在の `auto_memory_runner.py` は corrections 直近5件を全件処理（ゲーティングなし）。MEMORY.md は全エントリをコンテキストに全量ロードしており、肥大化するほど悪化する。

## Before / After

- **Before（ゲーティングなし）**: 些細な correction も記憶に追加される。MEMORY.md が肥大化すると全量がコンテキストを消費し続ける
- **After（ゲーティングあり）**: 重要な学習だけが残る 🛡。ノイズメモリが排除され長期的に AI 精度向上 ✨
- **Before（全量ロード）**: MEMORY.md が増えるほどセッション開始コスト増
- **After（意味検索）**: 関連記憶だけを取得してコンテキスト節約 ⚡

## 実装スコープ

### 1. ストレージゲーティング（推奨度: 高）
- `auto_memory_runner.py` に「この correction は記憶に値するか？」を判定するスコアリングレイヤーを追加
- 判定基準の候補: correction の再発頻度 / MEMORY.md に既出の内容との重複度 / Jaccard 類似度（`meta_quality.py` の再利用度チェックが参考）
- MEMORY.md エントリ数 × 参照回数を DuckDB で計測し、参照率 0 のメモリをゲーティングラベルとして活用

### 2. 意味検索レイヤー（推奨度: 中）
- MEMORY.md エントリの embedding 化と関連記憶のオンデマンド取得
- まず「重要度フラグ付きの全量ロード」から始め、embedding 化は段階的に導入

## 既存 issue との関係

- **#12（APEX-MEM）**: temporal validity + provenance — 記憶の有効期限管理。本 issue のゲーティング（保存可否）とは別軸、相補的
- **#204（L2 オンデマンド層）**: 重要度別アクセス層 — 本 issue の意味検索と部分的に類似。実装順として #204 を先行させると意味検索の効果が測定しやすくなる

## 採用後の確認方法

- [ ] `hooks/auto_memory_runner.py` を確認 → corrections の重要度スコアリングを追加できる箇所を特定
- [ ] DuckDB で MEMORY.md エントリ別参照回数を集計 → 参照率 0 のエントリを特定してゲーティングラベルとして使用
- [ ] PerMemBench のデータセット（arXiv:2605.25535）を参照 → benchmark テスト設計の参考

## 再評価条件

MEMORY.md が50行を超えたとき / コンテキスト消費が問題になったとき

## 参照

- Personalize-then-Store: https://arxiv.org/abs/2605.25535
- honcho: https://github.com/plastic-labs/honcho
- 既存: `hooks/auto_memory_runner.py`, `scripts/lib/meta_quality.py`, MEMORY.md
- 関連: #12（APEX-MEM）, #204（L2 オンデマンド層）

> 💬 comment:
>
> ## アーキテクチャ設計
> 
> ---
> 
> ### Phase 1: ストレージゲーティング
> 
> #### 概要
> 
> `auto_memory_runner.py` の `run()` 関数に、LLM 呼び出し前のゲーティング判定ステップを追加する。「記憶に値するか」を LLM なしで低コスト判定し、スコアが閾値未満なら LLM 呼び出しをスキップして return する。
> 
> #### データフロー
> 
> ```
> corrections.jsonl (直近5件)
>         |
>         v
> [read_recent_corrections()]
>         |
>         v
> [should_store_gate(corrections)]  ← Phase 1 追加箇所
>         |
>     スコア < 閾値?
>     /           \
>  Yes             No
>  |               |
> return          [_build_prompt()]
> (スキップ)           |
>                     v
>               [_call_llm()]  ← 1 LLM call
>                     |
>                     v
>              [_write_entry_file()]
>                     |
>                     v
>               [_apply_importance_score()]  ← 既存: memory_temporal.py
>                     |
>                     v
>              [_append_index_line()]
> ```
> 
> #### スコアリング基準（3軸、LLM 不使用）
> 
> **軸1: 再発頻度スコア（recurrence_score）**
> - corrections.jsonl 全件を走査し、同一 `correction_type` + メッセージの Jaccard 類似度 > 0.7 のパターン数をカウント
> - 直近5件の外側に同じパターンが存在 → score += 0.4
> - 1件のみ（初出） → score += 0.1
> - 根拠: 一度しか起きていないエラーは記憶不要。繰り返すパターンが記憶価値を持つ（Personalize-then-Store §3.1 の frequency gating 相当）
> 
> **軸2: 既存メモリとの非重複スコア（novelty_score）**
> - memory_dir の既存 `.md` ファイルの description / body を走査
> - 新 correction と Jaccard 類似度を計算（`meta_quality.py` の `_jaccard_similarity()` を再利用）
> - 全既存エントリとの最大類似度が 0.6 未満 → score += 0.4
> - 0.6 以上 → score += 0.05（重複は記憶価値低）
> - 根拠: `meta_quality.py` の `DUPLICATE_JACCARD_THRESHOLD = 0.6` をそのまま流用。新しい情報か否かがゲーティングの本質
> 
> **軸3: 修正影響度スコア（severity_score）**
> - correction の `confidence` フィールド（0.0–1.0）をそのまま使用
> - `confidence >= 0.8` → score += 0.2
> - `confidence >= 0.5` → score += 0.1
> - `guardrail: true` の場合 → score += 0.2（セキュリティ関連は加点）
> - 根拠: 既存フィールドのみ使用、LLM 不要
> 
> **総合スコアと閾値**
> ```
> gate_score = recurrence_score + novelty_score + severity_score
> # max = 0.4 + 0.4 + 0.2 = 1.0
> ```
> 
> | gate_score | 判定 | デフォルト閾値 |
> |------------|------|---------------|
> | >= 0.5 | STORE（LLM 呼び出し） | `gating_threshold = 0.5` |
> | < 0.5 | SKIP（LLM スキップ） | — |
> 
> 閾値は `userConfig` の `gating_threshold`（デフォルト 0.5）で調整可能。
> 
> #### 変更箇所（auto_memory_runner.py）
> 
> ```
> # 追加: 新関数 should_store_gate()
> def should_store_gate(
>     corrections: List[dict],
>     memory_dir: Path,
>     all_corrections_path: Path,
>     threshold: float = 0.5,
> ) -> tuple[bool, float]:
>     """ゲーティング判定。(store: bool, gate_score: float) を返す。"""
>     ...
> 
> # 変更: run() 関数の step 3 の前に挿入
> # Before:
> #   prompt = _build_prompt(corrections)
> #   llm_output = _call_llm(prompt)
> # After:
> #   store, gate_score = should_store_gate(corrections, _memory_dir, corrections_path)
> #   if not store:
> #       return  # graceful exit: gate スコア不足
> #   prompt = _build_prompt(corrections)
> #   llm_output = _call_llm(prompt)
> ```
> 
> 追加ファイル: `scripts/lib/memory_gating.py`（`should_store_gate` の実体、テスト対象）
> 
> #### 既存 `importance_score` との役割分担
> 
> | 既存 `importance_score` | 新 `gate_score` |
> |------------------------|-----------------|
> | **保存後**に frontmatter に記録 | **保存前**のフィルタ（記憶するか否か） |
> | 検索・ランキングに使用 | LLM call コスト削減に使用 |
> | `memory_temporal.compute_importance_score()` | `memory_gating.should_store_gate()` |
> 
> ---
> 
> ### Phase 2: 意味検索レイヤー
> 
> #### 選択技術と理由
> 
> **推奨: SQLite FTS5**
> 
> | 手法 | 外部依存 | 実装コスト | 検索品質 | 備考 |
> |------|---------|-----------|---------|------|
> | TF-IDF（自前） | なし | 高 | 中 | スパース。数式実装が必要 |
> | **SQLite FTS5** | なし（stdlib `sqlite3`） | 低 | 中〜高 | BM25 組み込み、日本語 unicode61 tokenizer で動作 |
> | DuckDB text_search | DuckDB（既存依存） | 中 | 中 | FTS は実験的機能。episodic.db と分離すべき |
> 
> SQLite FTS5 を選ぶ理由:
> 1. Python stdlib の `sqlite3` のみ（pip install 不要）
> 2. BM25 スコアリング組み込み（`rank` カラムで降順ソート可能）
> 3. `unicode61` tokenizer で日本語テキストも単語分割される
> 4. 既存 DuckDB（episodic.db）と干渉しない独立 DB ファイル
> 
> #### インデックス設計
> 
> ```
> # memory_search.db （memory_dir と同階層に配置）
> CREATE VIRTUAL TABLE memory_fts USING fts5(
>     filename,      -- auto_YYYYMMDD_HHMMSS_<hash>.md
>     description,   -- frontmatter description
>     importance,    -- frontmatter importance (high/medium/low)
>     importance_score,  -- 数値スコア
>     body,          -- markdown 本文
>     tokenize='unicode61'
> );
> ```
> 
> #### インデックス更新タイミング
> 
> - `auto_memory_runner.py` の `_write_entry_file()` 成功後に `memory_search.index_entry()` を呼ぶ
> - セッション開始時にインデックス欠損チェック（memory_dir のファイル数 != FTS 行数なら再インデックス）
> 
> #### データフロー（セッション開始〜関連記憶取得）
> 
> ```
> セッション開始
>     |
>     v
> [reflect/suggest_auto_memory_topic()]
>     |  クエリ文字列（現セッションのコンテキスト）
>     v
> [memory_search.query_relevant(q, top_k=5)]
>     |
>     v
> [SQLite FTS5: SELECT filename, description, body
>              FROM memory_fts
>              WHERE memory_fts MATCH ?
>              ORDER BY rank LIMIT 5]
>     |
>     v
> [重要度フィルタ: importance_score >= 0.3 のみ]
>     |
>     v
> [関連 .md ファイル読み込み]
>     |
>     v
> context に注入（MEMORY.md 全量の代替）
> ```
> 
> #### クエリ生成方法（LLM 不使用）
> 
> - クエリ = 現セッションの `CLAUDE_PROJECT_DIR` のプロジェクト名 + 直近 correction の `message` から stopword 除去したキーワード列
> - 例: `"git diff status correction hook"` → FTS5 MATCH で BM25 検索
> 
> #### 段階的移行戦略
> 
> ```
> Step 1（今すぐ）: Phase 1 ゲーティングで MEMORY.md 肥大化を抑制
> Step 2（MEMORY.md が 50 行超えたら）: FTS5 インデックス構築
> Step 3（安定後）: reflect スキルの read_auto_memory() を
>                    memory_search.query_relevant() で置き換え
> ```
> 
> Step 3 まで MEMORY.md の全量ロードは維持する（後方互換）。
> 
> ---
> 
> ### 既存 issue との統合方針
> 
> #### #12（APEX-MEM: temporal validity）との役割分担
> 
> ```
> #12 (temporal)           本 issue (gating/search)
> ─────────────────────────────────────────────────
> 保存後のライフサイクル管理    保存前の取捨選択
> decay_days で失効            gate_score でフィルタ
> superseded_at で無効化       重複度で重複排除
> ├─ 読み時に is_stale() で除外  ├─ 書き時に should_store_gate() で除外
> └─ 相補的: 両方あると記憶品質が最大化
> ```
> 
> **統合方針**: Phase 1 の gate_score が「保存可否」を決め、#12 の `decay_days` が「いつ失効するか」を決める。独立して実装可能で競合しない。
> 
> #### #204（L2 オンデマンド層）との関係
> 
> ```
> #204 の L2 層                本 issue の意味検索
> ────────────────────────────────────────────────
> importance_score で tier 分け  FTS5 クエリで関連度順取得
> 高スコア → L1（常時ロード）     top_k=5 の関連エントリのみ取得
> 低スコア → L2（オンデマンド）   スコア閾値で無関連エントリを除外
> └─ #204 が先行すると意味検索の「L2 からの取得」実装が容易になる
> ```
> 
> **統合方針**: #204 を先行実装し、L2 層のアクセスインターフェースを `memory_search.query_relevant()` で実現する形にすると自然に統合できる。
> 
> ---
> 
> ### テスト計画
> 
> #### unit tests（LLM mock 必須）
> 
> **`test_memory_gating.py`**
> ```
> - test_gate_score_recurrence: 再発パターンがある場合 recurrence_score が加点されること
> - test_gate_score_novelty: Jaccard 類似度 > 0.6 の既存エントリがある場合 novelty_score が低下すること
> - test_gate_score_severity: confidence=0.9 / guardrail=true の場合に severity_score が最大になること
> - test_gate_returns_false_below_threshold: 総合スコア < 0.5 の場合 should_store_gate が False を返すこと
> - test_gate_empty_corrections: corrections 空の場合 graceful に False を返すこと
> ```
> 
> **`test_auto_memory_runner_with_gate.py`**
> ```
> - test_run_skips_llm_when_gate_false:
>     should_store_gate を mock して False → _call_llm が呼ばれないこと
>     （subprocess.run は mock 対象。LLM 実呼び出し禁止）
> - test_run_calls_llm_when_gate_true:
>     should_store_gate を mock して True → _call_llm が呼ばれること
> ```
> 
> **`test_memory_search.py`**
> ```
> - test_index_and_query: エントリをインデックス後、関連クエリで取得できること（インメモリ DB）
> - test_query_returns_top_k: top_k=3 の場合 最大3件返すこと
> - test_importance_filter: importance_score < 0.3 のエントリが結果に含まれないこと
> - test_empty_index_returns_empty: インデックスが空の場合 [] を返すこと
> ```
> 
> #### integration tests（`RL_ALLOW_LLM_IN_TESTS=1` が必要な場合のみ）
> 
> ```
> シナリオ1: ゲーティング E2E
>   - 実際の corrections.jsonl に低スコアなエントリを準備
>   - auto_memory_runner.run() を実行
>   - memory_dir に新ファイルが増えていないことを確認
> 
> シナリオ2: 重複排除 E2E
>   - 既存 memory エントリと Jaccard 類似度 > 0.6 の correction を準備
>   - ゲーティングで SKIP されること、LLM が呼ばれないことを確認
> ```
> 
> ---
> 
> ### 実装ファイル一覧
> 
> | ファイル | Phase | 変更種別 |
> |---------|-------|---------|
> | `scripts/lib/memory_gating.py` | Phase 1 | 新規作成（`should_store_gate()`） |
> | `hooks/auto_memory_runner.py` | Phase 1 | 変更（`run()` に gate call 追加） |
> | `scripts/lib/memory_search.py` | Phase 2 | 新規作成（SQLite FTS5 ラッパー） |
> | `scripts/lib/reflect_memory.py` | Phase 2 | 変更（`read_auto_memory()` に検索オプション追加） |
> | `scripts/tests/test_memory_gating.py` | Phase 1 | 新規作成 |
> | `scripts/tests/test_memory_search.py` | Phase 2 | 新規作成 |
> | `scripts/tests/test_auto_memory_runner.py` | Phase 1 | 変更（gate mock テスト追加） |
> 
> **行数バジェット注意**: `auto_memory_runner.py` は現在 337 行。gate ロジックは `memory_gating.py` に分離して本体への追加は最小限（500 行バジェット遵守）。

---

## #240 [tech-eval] feat(fitness): DVAO 動的重みスケーリング + System 3 自己モデル追跡  `[closed]`  (enhancement)

## 背景

DVAO (arXiv:2605.25604) + DAIR.AI System 3 for AI Agents の技術評価から。推奨度**中×2**。

- **DVAO**: 複数報酬のグループ内分散を監視し、学習シグナルの信頼性に応じて重みを動的調整する RL 手法。現在の `environment.py` の固定重み（coherence 0.25 / telemetry 0.45 / constitutional 0.30）を動的化するアプローチとして応用
- **System 3**: System 1（反射）+ System 2（熟慮）に続く「動的自律」層。自己モデル追跡・内発的好奇心報酬でエージェントが自律的に改善対象を特定する

## Before / After

- **Before（固定重み）**: 学習フェーズが変わっても重みが変わらず、ノイズの多い軸も同じ重みで評価される
- **After（動的重み）**: 分散の高い軸（信頼性低）を自動ダウンウェイトして安定した評価 ⚡🛡
- **Before（自己モデルなし）**: rl-anything 自身が自分のどの軸が弱いか把握していない。evolve は人間の手動指示のみ
- **After（自己モデルあり）**: audit 結果を構造化保存し、auto-trigger が弱点軸を優先的に evolve 提案 ✨

## 実装スコープ

### 1. fitness 動的重みスケーリング（DVAO 部分応用、推奨度: 中）
- `evolve` / `audit` 実行結果の axis 別スコアを DuckDB に時系列保存
- 過去 N 回の各軸スコアの標準偏差を算出し、分散の逆数で `environment.py` の重みを動的スケール
- 完全な DVAO（RL training）は不要。「高分散軸はダウンウェイト」の rule-based 簡易版から開始

### 2. 自己モデル追跡（System 3 簡易版、推奨度: 中）
- `audit --fitness environment` の結果を構造化 JSONL に保存し、軸別の弱点プロファイルを生成
- `auto-trigger` の判断に「過去 audit で低スコアだった軸」を優先フラグとして追加
- アイドル時の Cron + audit で定期的な自己評価を実施（System 3 の「内発的好奇心」の簡易版）

## 採用後の確認方法

- [ ] `audit --fitness environment` を10回実行し axis 別スコア分散を DuckDB に記録 → 分散 0.1 超の軸を特定
- [ ] 動的重み版と固定重み版の overall スコアを比較 → 収束が早まるか検証
- [ ] `auto-trigger` の evolve 提案が低スコア軸に集中しているかログ確認

## 再評価条件

audit の coherence/telemetry スコア変動幅が 0.1 超になったとき / auto-trigger の提案精度を測定する手段ができたとき

## 参照

- DVAO: https://huggingface.co/papers/2605.25604
- System 3 (DAIR.AI): https://x.com/dair_ai/status/2004907378446381304
- 既存: `scripts/rl/fitness/environment.py`, `scripts/rl/fitness/config.py`, `scripts/lib/trigger_engine.py`
- 関連: #185（プラグイン自己診断ギャップ）

> 💬 comment:
>
> ## アーキテクチャ設計
> 
> ### 前提: 現状実装の整理
> 
> - `environment.py`: 4軸（coherence/telemetry/constitutional/skill_quality）を `BASE_WEIGHTS` で固定重みブレンド。`_normalize_weights()` が利用可能軸のみ動的正規化（重みの合計を1.0に調整）するが、**軸ごとの信頼性は考慮しない**
> - `config.py`: `BASE_WEIGHTS = {coherence: 0.23, telemetry: 0.43, constitutional: 0.29, skill_quality: 0.05}` がハードコード
> - `trigger_engine/`: evolve-state.json + remediation-outcomes.jsonl ベース。弱点軸の概念なし
> - DuckDB 慣習: `token_usage.db` に `connection()` context manager で1接続共有、`INSERT OR IGNORE` で冪等
> 
> ---
> 
> ### Phase 1: Fitness スコア時系列記録
> 
> #### DuckDB テーブル設計
> 
> `~/.claude/rl-anything/token_usage.db`（既存 DB に追加）に以下テーブルを追加する。
> **別 DB は作らない**。token_usage_store.py の `_SCHEMA_SQL` に CREATE TABLE IF NOT EXISTS を追記する形で共存させる。
> 
> ```sql
> CREATE TABLE IF NOT EXISTS fitness_history (
>     run_id      VARCHAR PRIMARY KEY,  -- uuid4 で生成
>     ts          TIMESTAMP NOT NULL,
>     pj_id       VARCHAR NOT NULL,     -- token_usage と同じ pj_id 形式
>     axis        VARCHAR NOT NULL,     -- 'coherence' | 'telemetry' | 'constitutional' | 'skill_quality'
>     score       DOUBLE NOT NULL,
>     weight_used DOUBLE NOT NULL,      -- 当該 run で実際に使われた正規化重み
>     days        INTEGER DEFAULT 30,   -- compute 時の days パラメータ
>     skip_llm    BOOLEAN DEFAULT FALSE
> );
> CREATE INDEX IF NOT EXISTS idx_fitness_history_pj_ts
>     ON fitness_history(pj_id, ts);
> CREATE INDEX IF NOT EXISTS idx_fitness_history_pj_axis_ts
>     ON fitness_history(pj_id, axis, ts);
> ```
> 
> 1 run で 4 行（軸ごとに1行）を同一 run_id で INSERT OR IGNORE する。
> run_id = `{pj_id}:{ts_iso}:{axis}` のような複合キーでもよいが、シンプルに uuid4 で生成し同一 run の軸を bundle する run_id を別途持つ方が JOIN しやすい（後述の `overall_run_id` を run_id として軸ごとに挿入）。
> 
> #### 保存タイミング
> 
> `compute_environment_fitness()` の末尾（`result` dict 構築後）で呼び出す。
> フラグ `record: bool = True` を追加し、テストや fleet 高速パスでは `record=False` で無効化可能にする。
> 
> ```
> compute_environment_fitness(project_dir, days, skip_llm, record=True)
>   └── 既存の軸計算 ...
>   └── _normalize_weights(sources)
>   └── overall 計算
>   └── [NEW] _record_fitness_history(project_dir, run_result)  ← Phase 1 追加
> ```
> 
> #### データフロー (Phase 1)
> 
> ```
> audit --fitness environment
>         │
>         ▼
> compute_environment_fitness()
>         │
>         ├─ coherence:      0.72  ──┐
>         ├─ telemetry:      0.55  ──┤  _record_fitness_history()
>         ├─ constitutional: 0.81  ──┤  ─────────────────────────────
>         └─ skill_quality:  0.60  ──┘       DuckDB fitness_history
>                                             pj_id | axis | score | ts
>                                             ──────┼──────┼───────┼────
>                                             proj  │ coh  │ 0.72  │ T1
>                                             proj  │ tel  │ 0.55  │ T1
>                                             proj  │ con  │ 0.81  │ T1
>                                             proj  │ sq   │ 0.60  │ T1
> ```
> 
> #### 実装ファイル (Phase 1)
> 
> - `scripts/lib/token_usage_store.py`: `_SCHEMA_SQL` に `fitness_history` テーブル定義を追加
> - `scripts/rl/fitness/environment.py`: `_record_fitness_history()` 関数を追加、`compute_environment_fitness()` 末尾で呼び出し
> - `scripts/lib/fitness_history_store.py` (新規): fitness_history への append/query の薄いラッパー（token_usage_store.connection() を再利用）
> 
> ---
> 
> ### Phase 2: DVAO 動的重みスケーリング
> 
> #### 計算式
> 
> 過去 N 回（デフォルト N=10）の各軸スコアの標準偏差を算出し、重みスケール係数を掛け合わせる。
> 
> ```
> std_dev(axis)        = 過去 N run の axis スコアの標本標準偏差
> stability(axis)      = 1 / (1 + std_dev(axis))   ← 分散0なら1.0、分散大なら0.5以下
> scaled_weight(axis)  = BASE_WEIGHT(axis) × stability(axis)
> final_weight(axis)   = scaled_weight(axis) / Σ scaled_weight  ← 再正規化
> ```
> 
> **フォールバック条件**: fitness_history に当該 pj_id のレコードが N 件未満の場合は現行の `BASE_WEIGHTS` をそのまま使用する（Phase 1 で記録が溜まるまでの移行期間）。
> 
> #### config.py への追加設定
> 
> ```python
> # DVAO 動的重みスケーリング
> DVAO_CONFIG = {
>     "enabled": True,
>     "history_window": 10,     # 標準偏差算出に使う過去 run 数
>     "min_history": 5,         # これ未満なら固定重みにフォールバック
>     "stability_floor": 0.3,   # stability の下限（特定軸を過剰ダウンウェイトしない）
> }
> ```
> 
> #### environment.py の変更箇所
> 
> `_normalize_weights()` を以下のように変更する。
> 
> ```
> [変更前]
> _normalize_weights(available_axes) → BASE_WEIGHTS を available_axes でフィルタ → 合計1.0に正規化
> 
> [変更後]
> _normalize_weights(available_axes, project_dir, dvao_config)
>   ├─ fitness_history から過去 N run のスコアを取得
>   ├─ N 件未満 → 従来と同じ BASE_WEIGHTS ベース正規化（フォールバック）
>   ├─ N 件以上 → axis 別 std_dev 計算 → stability スケール → 再正規化
>   └─ result dict に "weight_source": "dvao" | "base" を記録
> ```
> 
> #### データフロー (Phase 2)
> 
> ```
> audit --fitness environment (11回目以降)
>         │
>         ▼
> compute_environment_fitness()
>         │
>         ├─ 各軸スコア算出 (既存)
>         │
>         ├─ _normalize_weights() [DVAO モード]
>         │         │
>         │         ▼
>         │   fitness_history (過去10 run)
>         │   axis=coherence:      [0.71, 0.72, 0.69, ...] → std=0.015 → stability=0.985
>         │   axis=telemetry:      [0.40, 0.55, 0.70, ...] → std=0.124 → stability=0.890
>         │   axis=constitutional: [0.80, 0.81, 0.82, ...] → std=0.010 → stability=0.990
>         │   axis=skill_quality:  [0.55, 0.62, 0.45, ...] → std=0.085 → stability=0.922
>         │         │
>         │         ▼
>         │   BASE_WEIGHTS × stability → 再正規化
>         │   (telemetry の分散が高い → 相対的にダウンウェイト)
>         │
>         ├─ overall 計算 (DVAO 重みで)
>         └─ _record_fitness_history() (今回のスコアと実際の重みを保存)
> ```
> 
> ---
> 
> ### Phase 3: System 3 自己モデル追跡
> 
> #### 自己モデルデータ構造
> 
> `~/.claude/rl-anything/self_model.jsonl`（JSONL、append-only）
> 
> ```json
> {"ts": "2026-05-27T10:00:00Z", "pj_id": "...", "axis": "telemetry", "score": 0.45, "weak_flag": true, "consecutive_low": 3, "run_id": "uuid4", "trigger_priority": 0.8}
> {"ts": "2026-05-27T10:00:00Z", "pj_id": "...", "axis": "coherence", "score": 0.72, "weak_flag": false, "consecutive_low": 0, "run_id": "uuid4", "trigger_priority": 0.2}
> ```
> 
> フィールド説明:
> - `weak_flag`: score < WEAK_THRESHOLD (デフォルト 0.5) が `CONSECUTIVE_LOW_COUNT` (デフォルト 3) 回連続で true
> - `consecutive_low`: 現在の連続低スコア回数（0 にリセットされる）
> - `trigger_priority`: 0.0〜1.0。`consecutive_low / CONSECUTIVE_LOW_COUNT` で線形計算
> 
> **弱点フラグ判定条件**:
> ```
> weak_flag = True  when:
>   score < 0.5   (WEAK_THRESHOLD)
>   AND 過去3回連続（CONSECUTIVE_LOW_COUNT）で同条件
> ```
> 
> #### config.py への追加設定
> 
> ```python
> SELF_MODEL_CONFIG = {
>     "weak_threshold": 0.5,
>     "consecutive_low_count": 3,
>     "self_model_file": "self_model.jsonl",  # DATA_DIR 相対
> }
> ```
> 
> #### auto-trigger との接続
> 
> `trigger_engine/self_evolution.py` に `_evaluate_weak_axis_priority()` を追加する。
> 既存の `evaluate_session_end()` から呼び出し、`TriggerResult.details` に弱点軸リストを追加する。
> 
> ```python
> # TriggerResult.details の拡張
> {
>     "triggered_types": [...],
>     "weak_axes": ["telemetry", "skill_quality"],  # [NEW] 弱点軸リスト
>     "action_hint": "/rl-anything:audit --fitness telemetry"  # [NEW] 優先 audit コマンド
> }
> ```
> 
> trigger が発火した際のメッセージに弱点軸を明示する:
> ```
> 「自己モデル弱点検出: telemetry が3回連続 0.50 未満。
>  推奨: /rl-anything:audit --fitness telemetry を先に実行」
> ```
> 
> #### 定期 audit 設計
> 
> **CronCreate は使わない**（CLAUDE.md rules/safety.md: デプロイ/ビルド完了監視に CronCreate を使わない）。
> 代わりに CC Stop hook（セッション終了時）の `auto_memory_runner.py` 的パターンで非同期実行する。
> 
> ```
> Stop hook (セッション終了)
>     │
>     ├─ 既存: trigger_engine.evaluate_session_end()
>     │          └─ 弱点軸あり → TriggerResult に weak_axes 付与
>     │
>     └─ [NEW] self_model_updater.py
>                ├─ 直近 fitness_history から弱点軸を判定
>                └─ self_model.jsonl を append（LLM 呼び出しなし）
> ```
> 
> 手動での定期 audit は `evolve-state.json` の `audit_overdue` トリガー（既存 interval_days=30）を活用し、弱点軸を優先する形に拡張する。
> 
> #### データフロー (Phase 3)
> 
> ```
> [セッション終了]
>     │
>     ▼
> Stop hook
>     │
>     ├─ fitness_history (DuckDB) から直近スコアを読む
>     │       pj_id | axis        | score | ts
>     │       ──────┼─────────────┼───────┼──────
>     │       proj  │ telemetry   │ 0.45  │ T-1
>     │       proj  │ telemetry   │ 0.48  │ T-2
>     │       proj  │ telemetry   │ 0.43  │ T-3  ← 3回連続 < 0.5
>     │
>     ├─ self_model.jsonl に append
>     │       {axis: "telemetry", weak_flag: true, consecutive_low: 3, ...}
>     │
>     └─ trigger_engine: weak_axes = ["telemetry"]
>               │
>               ▼
>         TriggerResult(
>           triggered=True,
>           action="/rl-anything:audit --fitness telemetry",
>           message="自己モデル弱点検出: telemetry 3回連続低スコア"
>         )
> ```
> 
> ---
> 
> ### 両機能の統合点
> 
> `fitness_history` (DuckDB) が DVAO と自己モデルの共通 SoR となる。
> 
> ```
> fitness_history (DuckDB)
>     │
>     ├─ [DVAO] Phase 2
>     │   std_dev(axis, 過去10 run)
>     │   → scaled_weight → 次回 compute の重み
>     │
>     └─ [System 3] Phase 3
>         consecutive_low(axis, 過去3 run)
>         → weak_flag → self_model.jsonl → trigger_priority
> ```
> 
> self_model.jsonl は「人間が読める弱点プロファイル」として保存し、DuckDB は「機械が高速クエリする履歴 SoR」として使い分ける。
> 
> ---
> 
> ### テスト計画
> 
> #### unit tests (DuckDB は実 DB を使う、mock しない)
> 
> | テストファイル | テスト内容 |
> |---|---|
> | `scripts/tests/test_fitness_history_store.py` | INSERT OR IGNORE 冪等性、スキーマ自動作成、connection() 共有パス |
> | `scripts/tests/test_environment_dvao.py` | N 件未満 → BASE_WEIGHTS フォールバック、N 件以上 → std_dev 計算・再正規化、stability_floor 下限 |
> | `scripts/tests/test_self_model_updater.py` | consecutive_low カウント・リセット、weak_flag=True 条件、trigger_priority 線形計算 |
> | `scripts/tests/test_trigger_weak_axis.py` | `_evaluate_weak_axis_priority()`: weak_axes なし → triggered=False、あり → action_hint 付き TriggerResult |
> 
> **DuckDB テストのパターン**: `tmp_path` fixture で一時 DB を作成し、`CLAUDE_PLUGIN_DATA` 環境変数をオーバーライドして使う（token_usage_store のテストと同パターン）。
> 
> #### integration test シナリオ
> 
> 1. `compute_environment_fitness()` を5回呼び出し → fitness_history に20行（4軸×5回）が蓄積されること
> 2. 6回目以降: `weights.weight_source == "dvao"` になること
> 3. telemetry スコアを意図的に揺らした mock → telemetry の weight が coherence より下がること
> 4. consecutive_low=3 の状態で Stop hook 相当の処理 → self_model.jsonl に `weak_flag=true` が書かれること
> 
> ---
> 
> ### 実装ファイル一覧
> 
> #### Phase 1 (スコア記録)
> 
> | ファイル | 変更種別 | 内容 |
> |---|---|---|
> | `scripts/lib/token_usage_store.py` | 変更 | `_SCHEMA_SQL` に `fitness_history` テーブル追加 |
> | `scripts/lib/fitness_history_store.py` | 新規 | `append_fitness_run()`, `query_recent_scores()` の薄いラッパー |
> | `scripts/rl/fitness/environment.py` | 変更 | `_record_fitness_history()` 追加、`compute_environment_fitness()` に `record: bool = True` 引数追加 |
> | `scripts/tests/test_fitness_history_store.py` | 新規 | Phase 1 unit tests |
> 
> #### Phase 2 (DVAO 動的重み)
> 
> | ファイル | 変更種別 | 内容 |
> |---|---|---|
> | `scripts/rl/fitness/config.py` | 変更 | `DVAO_CONFIG` 追加 |
> | `scripts/rl/fitness/environment.py` | 変更 | `_normalize_weights()` に DVAO ロジック追加 |
> | `scripts/tests/test_environment_dvao.py` | 新規 | Phase 2 unit tests |
> 
> #### Phase 3 (自己モデル追跡)
> 
> | ファイル | 変更種別 | 内容 |
> |---|---|---|
> | `scripts/rl/fitness/config.py` | 変更 | `SELF_MODEL_CONFIG` 追加 |
> | `scripts/lib/self_model_updater.py` | 新規 | `update_self_model()`, `get_weak_axes()` |
> | `scripts/lib/trigger_engine/self_evolution.py` | 変更 | `_evaluate_weak_axis_priority()` 追加 |
> | `hooks/stop_hook.py` (or 既存 Stop hook) | 変更 | `self_model_updater.update_self_model()` を非同期呼び出し |
> | `scripts/tests/test_self_model_updater.py` | 新規 | Phase 3 unit tests |
> | `scripts/tests/test_trigger_weak_axis.py` | 新規 | trigger 統合 unit tests |
> 
> ---
> 
> ### 実装順序サマリー
> 
> ```
> Phase 1 (スコア記録のみ)
>   目標: fitness_history に蓄積されること
>   完了条件: 10 run 後に fitness_history に 40 行以上存在する
>   依存: なし
> 
>     ↓
> 
> Phase 2 (DVAO 動的重み)
>   目標: weight_source == "dvao" になること & 高分散軸がダウンウェイトされること
>   完了条件: telemetry 分散 > 0.1 のとき weight が BASE_WEIGHT × 0.9 未満になる
>   依存: Phase 1 (fitness_history が N 件以上蓄積済み)
> 
>     ↓
> 
> Phase 3 (自己モデル追跡)
>   目標: weak_axes が trigger_engine に伝わること
>   完了条件: consecutive_low=3 の軸を持つ状態で Stop hook 実行 → TriggerResult に weak_axes が含まれる
>   依存: Phase 1 (fitness_history クエリ)
> ```

---

## #241 [tech-eval] feat(breakthrough): VeriTrace — 仮説ツリーによる推論品質向上  `[closed]`  (enhancement)

## 背景

VeriTrace (arXiv:2605.26081) の技術評価から。推奨度**中**。

深層研究エージェントが調査中に「認知グラフ（知識グラフ + 仮説ツリー）」をリアルタイム更新する3ループ（解釈更新・逸脱フィードバック・スキーマ改訂）フレームワーク。従来比 +40% の精度向上を実証。コード公開済み。

現在の `breakthrough` / `investigate` スキルは診断→提案のシングルパス。調査が長くなると前半の仮説を忘れ、矛盾した方向に進みやすい。VeriTrace のアプローチを取り込むことで、長時間 audit/investigate 中の信念一貫性が保証される。

## Before / After

- **Before**: 長い audit/investigate で前半の仮説を忘れる。誤検知が後続ステップを汚染する
- **After**: 調査全体を通じた仮説の一貫性を保証 🛡。新情報が既存仮説と矛盾したら自動検知 ✨

## 実装スコープ

### 軽量版（先行実装）
- `breakthrough` スキルに「現在の仮説リスト」中間アーティファクト（JSONL）を追加
  - 各仮説: `{id, statement, confidence, evidence_for[], evidence_against[], status}`
  - 新情報追加時に既存仮説の `evidence_against` を更新して矛盾を可視化
- `investigate` スキルも同様の仮説ツリーを保持

### 3調整ループ（本実装、VeriTrace 準拠）
1. **解釈更新**: 新情報が入るたびに関連仮説の confidence を更新
2. **逸脱フィードバック**: 現状の調査方向が初期仮説から外れていたら警告
3. **スキーマ改訂**: 複数の仮説が矛盾した場合、上位スキーマを見直す

## 採用後の確認方法

- [ ] `breakthrough` スキルに仮説リスト出力を追加 → 同じ audit を仮説ツリーあり/なしで実行して結論の一貫性を比較
- [ ] 長時間 investigate（5ターン以上）で仮説の矛盾検知が発火するかテスト
- [ ] VeriTrace リポジトリの認知グラフ実装を参照 → 仮説データ構造の設計に活用

## 再評価条件

breakthrough が月2回以上使われるようになったとき / 長時間 investigate でスキップが多発したとき

## 参照

- VeriTrace: https://arxiv.org/abs/2605.26081
- 既存: `skills/breakthrough/SKILL.md`, `skills/investigate/` (if exists)
- 関連: #188（HASP-style 失敗状態検知フック）

> 💬 comment:
>
> ## アーキテクチャ設計
> 
> ---
> 
> ### 前提確認
> 
> `investigate` スキルは rl-anything には存在しない（gstack プラグイン側のスキル）。
> 本設計は **breakthrough スキルへの組み込みを優先**し、investigate への展開方針を後半に記載する。
> 
> ---
> 
> ### 仮説ツリー データ構造
> 
> **ファイル名規則**: `~/.claude/rl-anything/hypothesis_{session_id}.jsonl`
> 
> ```json
> {
>   "hypothesis_id": "h1",
>   "statement": "audit.py の stale_ref 誤検知は path 正規化の問題",
>   "confidence": 0.7,
>   "status": "active",
>   "evidence_for": [
>     "観察1: Windows パスと POSIX パスの混在を確認",
>     "観察2: normalize() 呼び出し前後でパスが変わる"
>   ],
>   "evidence_against": [],
>   "parent_hypothesis_id": null,
>   "created_at": "2026-05-27T10:00:00Z",
>   "updated_at": "2026-05-27T10:05:00Z"
> }
> ```
> 
> **フィールド選定の根拠:**
> 
> | フィールド | 用途 | 備考 |
> |---|---|---|
> | `hypothesis_id` | 仮説間の参照・逸脱検知での特定 | `h1`, `h2`... 単純な連番 |
> | `statement` | 仮説の主張（1文）| 具体的かつ反証可能な形で書く |
> | `confidence` | 現在の確信度 0.0〜1.0 | 0.3未満で suspended 候補 |
> | `status` | `active` / `confirmed` / `refuted` / `suspended` | スキーマ改訂時に suspended を多用 |
> | `evidence_for` / `evidence_against` | 証拠リスト（最大10件ずつ） | against が3件以上 → 逸脱警告トリガー |
> | `parent_hypothesis_id` | 上位スキーマとの階層関係 | スキーマ改訂で新しい親を生成する時に使用 |
> | `created_at` / `updated_at` | 調査の時系列トレーサビリティ | |
> 
> **保存場所の選択理由:**
> - セッション単位で分離できる（`session_id` をファイル名に含める）
> - breakthrough スキルは Claude Code スキル（プロンプト駆動）なので、永続化は Claude Code の Read/Write ツール経由
> - セッション終了後に cleanup スキルの対象になれるよう `rl-anything-` プレフィックスを付ける
>   → `~/.claude/rl-anything/hypothesis_rl-anything-{session_id}.jsonl`
> 
> ---
> 
> ### breakthrough スキルへの組み込み
> 
> #### Before（現在のフロー）
> 
> ```
> [Phase 1: Intake]
>     ↓
>     ユーザーから: 問題・到達点・試したこと・完成定義 を収集
>     ↓
> [Phase 2: Diagnosis]
>     ↓
>     タイプ判定: A/B/C/D/E の1つを選択
>     ↓
> [Phase 3: Strategy Selection]
>     ↓
>     タイプ対応の戦略を選ぶ（シングルパス）
>     ↓
> [Phase 4: Proposal]
>     ↓
>     診断結果・戦略・エージェント構成・終了条件を提示
>     ↓ ← ユーザー承認
> [Phase 5: Execution]
>     ↓
>     Agent/Task でエージェント起動（単発または数ターンのループ）
>     ↓
>     完了（仮説の整合性は保証されない）
> ```
> 
> ---
> 
> #### After（VeriTrace 組み込み後）
> 
> ```
> [Phase 1: Intake]
>     ↓
>     ユーザーから: 問題・到達点・試したこと・完成定義 を収集
>     ↓
> [Phase 1.5: 仮説ツリー初期化] ← ★ 追加
>     ↓
>     収集情報から初期仮説 h1〜hN を生成
>     Write: hypothesis_{session_id}.jsonl に保存
>     仮説リストをユーザーに提示して確認
>     ↓
> [Phase 2: Diagnosis]
>     ↓
>     タイプ判定: A/B/C/D/E（初期仮説を参照して診断）
>     ↓
> [Phase 3: Strategy Selection]
>     ↓
>     タイプ対応の戦略を選ぶ
>     ↓
> [Phase 4: Proposal]
>     ↓
>     診断結果・戦略・エージェント構成・終了条件を提示
>     仮説リストも併記（「この仮説を検証する戦略」として提示）
>     ↓ ← ユーザー承認
> [Phase 5: Execution with 3調整ループ] ← ★ 拡張
>     ↓
>     エージェント起動
>     ┌─────────────────────────────────────────────────┐
>     │         情報収集・仮説更新ループ（最大5回）         │
>     │                                                  │
>     │  [情報収集]                                      │
>     │      ↓                                          │
>     │  [ループ1: 解釈更新]                             │
>     │      新情報 → 関連仮説の confidence 更新         │
>     │      evidence_for / evidence_against に追記       │
>     │      ↓                                          │
>     │  [ループ2: 逸脱フィードバック] ← トリガー条件あり  │
>     │      調査方向 vs 初期仮説を照合                   │
>     │      → 警告（承認後に継続 or 仮説修正）           │
>     │      ↓                                          │
>     │  [ループ3: スキーマ改訂] ← トリガー条件あり        │
>     │      複数仮説が矛盾 → 上位スキーマを見直す         │
>     │      parent_hypothesis_id を設定して構造を更新     │
>     │      ↓                                          │
>     │  整合性チェック: 全仮説 active/confirmed で終了?   │
>     └─────────────────────────────────────────────────┘
>     ↓
> [Phase 6: 整合性レポート] ← ★ 追加
>     ↓
>     confirmed/refuted/suspended 仮説のサマリーを提示
>     「最終的に何が分かったか」を仮説ツリーから生成
>     ↓
>     完了
> ```
> 
> ---
> 
> ### 3調整ループの実装方針
> 
> #### ループ1: 解釈更新（Interpretation Update）
> 
> **発火タイミング**: 新しい情報・観察が得られるたびに毎回実行
> 
> **プロンプト組み込み箇所**: Phase 5 のエージェントプロンプトに追加する指示ブロック
> 
> ```
> 【仮説更新プロトコル】
> 新しい情報が得られたら:
> 1. Read: hypothesis_{session_id}.jsonl を読む
> 2. 関連する仮説を特定する（statement と照合）
> 3. 情報が evidence_for か evidence_against かを判定
> 4. confidence を更新する（for +0.1 / against -0.15、上限1.0・下限0.0）
> 5. Write: 更新を保存する
> ```
> 
> **confidence 変化の非対称設計（against が重い）**: 反証は証拠より信念を強く崩すべきため。
> 
> ---
> 
> #### ループ2: 逸脱フィードバック（Deviation Feedback）
> 
> **発火トリガー（いずれか）**:
> - 初期仮説 h1 の `evidence_against` が **3件以上** になった時
> - 現在調査している方向と h1〜h3 の statement が **意味的に無関係** になった時
> - confidence が 0.3 未満の仮説が過半数になった時
> 
> **発火時の処理**:
> ```
> ⚠️ 逸脱警告: 現在の調査方向が初期仮説から外れています
>   初期仮説 h1: [statement]
>   現在の調査: [直近の情報収集内容]
>   矛盾する証拠: [evidence_against の件数と内容]
> 
>   選択肢:
>   A) 初期仮説を修正して続行（statement を更新）
>   B) 初期仮説を suspended にして新しい仮説から再出発
>   C) 逸脱は意図的なので無視して続行
> ```
> 
> ユーザーへの確認が必要なので、`AskUserQuestion` ツール（または承認要求の形式）で提示する。
> 
> ---
> 
> #### ループ3: スキーマ改訂（Schema Revision）
> 
> **発火トリガー**:
> - `active` な仮説の中で **2つ以上が直接矛盾** している時（同じ事象について相反する statement）
> - 例: h1「問題はパス正規化」vs h3「問題はファイル権限」が同時に active で confirmed に向かっている
> 
> **発火時の処理**:
> ```
> 複数仮説の矛盾を検知:
>   h1: [statement] (confidence: 0.7)
>   h3: [statement] (confidence: 0.6)
> 
> 上位スキーマを生成:
>   h0（新規）: 「両者を包含する上位レベルの問題仮説」
>   h1, h3 の parent_hypothesis_id を h0 に設定
> 
> スキーマ改訂後:
>   h0 を active に
>   h1, h3 を suspended に（証拠は保持）
>   新しい調査方向: h0 の検証に集中
> ```
> 
> ---
> 
> ### 軽量版（Phase 1）の最小実装
> 
> breakthrough スキルの SKILL.md に追加するセクション:
> 
> **追加箇所**: Phase 1 (Intake) と Phase 2 (Diagnosis) の間
> 
> **追加セクション名**: `## Phase 1.5: 仮説ツリー初期化（VeriTrace）`
> 
> **最小実装内容**:
> ```
> Phase 1 で収集した情報から初期仮説を2〜5個生成し、以下の形式で列挙する。
> 
> ```
> ## 現在の仮説リスト
> 
> | ID | 仮説 | 確信度 | ステータス |
> |----|------|--------|----------|
> | h1 | [問題の主原因に関する仮説] | 0.5 | active |
> | h2 | [別の原因仮説] | 0.3 | active |
> | h3 | [前提条件に関する仮説] | 0.7 | active |
> ```
> 
> 調査が進むにつれて各仮説を更新する。確信度が 0.3 未満になった仮説は
> suspended に変更し、evidence_against の内容を記録する。
> ```
> 
> この「仮説リストの可視化」だけでも長時間調査中の一貫性が大幅に改善される。
> 完全な JSONL 保存はフルスタック実装（Phase 2〜3）で対応する。
> 
> ---
> 
> ### investigate スキルへの展開方針
> 
> `investigate`（gstack プラグイン）は rl-anything 外のスキルだが、設計方針は共通化できる。
> 
> **共通化できる部分:**
> - 仮説ツリーのデータ構造（同一 JSON スキーマ）
> - 3調整ループのロジック（同一トリガー条件）
> - references/ 配下の共有ドキュメント（例: `references/hypothesis-protocol.md`）
> 
> **breakthrough との差分:**
> - breakthrough は「行き詰まり問題」が入力 → 仮説は「なぜ詰まっているか」の原因仮説
> - investigate は「不具合/現象」が入力 → 仮説は「根本原因の候補」
> - investigate は調査フェーズが長い（breakthrough より逸脱フィードバックのトリガーを緩めに設定）
> 
> **展開方法:**
> 1. breakthrough に組み込んで動作確認（Phase 1〜3）
> 2. `references/hypothesis-protocol.md` を breakthrough から切り出して共有ドキュメント化
> 3. investigate スキルの該当フェーズで `hypothesis-protocol.md` を参照する形で展開
> 
> ---
> 
> ### 評価方法
> 
> breakthrough スキルは Claude Code スキル（プロンプト駆動）なので、単体テストではなく **evaluation ベース** で評価する。
> 
> #### 評価基準案
> 
> | 指標 | 測定方法 | 合格ライン |
> |---|---|---|
> | 仮説一貫性スコア | セッション開始時の仮説 h1 と最終結論が論理的に接続されているか（人手評価） | 3/5回以上で「接続されている」 |
> | 矛盾検知率 | 意図的に矛盾する情報を投入した時に逸脱警告が発火するか | 5/5回で発火 |
> | 仮説更新の適時性 | 新情報取得後1ターン以内に仮説が更新されているか | 4/5回で1ターン以内 |
> | スキーマ改訂の正確性 | 矛盾する仮説が上位スキーマに統合されるか | 3/5回で統合される |
> 
> #### 評価手順（eval ベース）
> ```
> 1. 評価シナリオを3種類用意:
>    - 短期調査（2〜3ターン）
>    - 中期調査（5〜7ターン、仮説の矛盾あり）
>    - 長期調査（10ターン以上、初期仮説が全て refuted になるケース）
> 
> 2. 仮説ツリーあり/なしで同じシナリオを実行し、最終結論の一貫性を比較
> 
> 3. 逸脱フィードバック評価: ターン5で意図的に「初期仮説と無関係な方向」に誘導し、
>    警告が発火するかを確認
> ```
> 
> ---
> 
> ### 実装フェーズ
> 
> #### Phase 1（先行実装可能・コスト小）
> - breakthrough SKILL.md に `Phase 1.5: 仮説ツリー初期化` セクションを追加
> - 仮説をマークダウンテーブルで可視化するプロンプト指示を追加
> - Phase 5 のエージェントプロンプトに「調査終了時に仮説ステータスを更新すること」の指示を追加
> - **完了目安**: 1 PR、SKILL.md + references/ 追記のみ
> 
> #### Phase 2（自動更新ループ）
> - Phase 5 に「解釈更新プロトコル」を追加
> - JSONL への Write/Read を使った仮説ファイルの永続化
> - 調査エージェントプロンプトに confidence 更新ルールを組み込む
> - `references/hypothesis-protocol.md` を新規作成して breakthrough/investigate で共有
> - **完了目安**: 1〜2 PR
> 
> #### Phase 3（逸脱検知・スキーマ改訂）
> - 逸脱フィードバックのトリガー条件をプロンプトに追加
> - AskUserQuestion 形式で逸脱警告を提示するフローを設計
> - スキーマ改訂ロジック（parent_hypothesis_id の自動設定）を追加
> - evaluate シナリオを用意して Phase 1〜3 の評価を実施
> - **完了目安**: 2〜3 PR
> 
> ---
> 
> > 設計メモ: VeriTrace のコア価値は「何を信じているかを常に可視化し続けること」。
> > breakthrough は既に診断タイプ（A/B/C/D/E）という形で「何が問題か」を明示する設計になっており、
> > 仮説ツリーはこの診断結果を時系列で追跡する拡張として自然に組み込める。
> > Phase 1 の最小実装（マークダウンテーブルによる可視化）だけでも
> > 「前半の仮説を忘れる」問題の大半に対応できると判断する。

---

## #253 [tech-eval] 後ろ向き分解による中間報酬（BES Backward Search）を fitness/optimize に導入  `[closed]`  (enhancement)

## 概念
BES (Bidirectional Evolutionary Search, arxiv:2605.28814) の **後ろ向き分解** — タスクを検証可能なサブゴールに再帰分解し、密な中間フィードバックを与える。理論上、必要サンプル数を指数的に削減する。

## Before / After（開発者体験）
- Before: fitness/optimize が最終スコアだけを見るため収束が粗く、スコア改善が頭打ちになっても原因が掴みにくい
- After: サブゴール単位でスコアが出て optimize の収束が安定する ✨

## 既存実装との差分
- `scripts/lib/regression_gate.py` は通過/不通過の**ゲート**であり中間報酬ではない
- fitness 関数（`scripts/rl/fitness/`）は最終スコアのみを返す
- ギャップ: タスク→検証可能サブゴールへの再帰分解と、サブゴール単位スコアリング機構が無い

## 提案アクション
`regression_gate.py` をサブゴール分解スコアリングに拡張。Embodied-Minds-Lab/BES の backward search 実装を参照。

## 採用後の確認方法
- [ ] optimize を同一 corrections で N 回実行 → サブゴール導入前後で最終スコアの分散が縮小し、収束 iteration が減ることを確認

## 再評価条件
optimize の収束が不安定／スコア改善が頭打ちと感じた時。

## 出典
ai-daily-report 2026-05-29 / arxiv:2605.28814（rl-anything: fitness 関数設計に直結と評価）

---

## #254 [tech-eval] メモリエラーの類型化と原因帰属（MemTrace）を auto-memory に導入  `[closed]`  (enhancement)

## 概念
MemTrace (arxiv:2605.28732) — LLM メモリシステムのエラーを「誤検索 / 記憶の腐敗 / 文脈ドリフト」など複数類型に分類し、各エラーを発生源に帰属させるフレームワーク。長期記憶を持つエージェントのデバッグを体系化する。

## Before / After（開発者体験）
- Before: auto-memory が誤った記憶を返しても、どこで何が起きたか原因を追えない
- After: 誤検索の発生源を特定しデバッグできる 🛡

## 既存実装との差分
- `scripts/lib/episodic_store.py`(Jaccard retrieve)・`memory_gating.py`・`memory_temporal.py` は存在する
- ギャップ: 「検索失敗 / 腐敗 / ドリフト」をエラー類型として分類し発生源に帰属する診断機構が無い（`layer_diagnose.py` は層診断でありメモリエラー帰属ではない）

## 提案アクション
`episodic_store` + `memory_temporal` 上にエラー類型診断を追加。

## 採用後の確認方法
- [ ] 意図的に誤った memory を仕込んで `episodic_store.query_relevant()` を叩く → 診断機構がエラー類型と発生源を正しく出力するか

## 再評価条件
auto-memory の誤検索で実害が出た時。

## 出典
ai-daily-report 2026-05-29 / arxiv:2605.28732（rl-anything のメモリ設計改善に直結と評価）

---

## #255 [tech-eval] AI-slop 具体パターン辞書を constitutional fitness に追加（taste-skill / LLM smells）  `[closed]`  (enhancement)

## 概念
taste-skill / 「Various LLM Smells」— AI が生成しがちな具体パターン（過度な肯定・不要な要約・意味のないヘッダー分割・過剰な謝罪・不要な免責）を辞書化して抑制する。

## Before / After（開発者体験）
- Before: 原則違反は検出するが「ありきたりな AI 文体（slop）」は素通りする
- After: slop 辞書で AI 文章の凡庸さを機械的にチェックできる ✨

## 既存実装との差分
- `scripts/rl/fitness/constitutional.py`(原則ベース LLM Judge) + `critical_instruction_extractor.py` は存在する
- ギャップ: 具体的な slop パターン辞書を持たない

## 提案アクション
`constitutional.py` の principles に slop パターン辞書を追加。

## 採用後の確認方法
- [ ] slop を含むサンプル文章を constitutional fitness に通す → slop 検出で減点されることを確認（現状は素通り）

## 再評価条件
生成文章の凡庸さがユーザー報告で問題化した時。

## 出典
ai-daily-report 2026-05-29（taste-skill / Various LLM Smells）

---

## #256 [tech-eval] 前向き進化探索（BES Forward Search）を genetic-prompt-optimizer に試験導入  `[closed]`  (enhancement)

## 概念
BES (arxiv:2605.28814) の **前向き進化探索** — 進化演算子を使って部分軌跡を組み合わせ、通常の自己回帰展開では到達できない候補を生成する。

## Before / After（開発者体験）
- Before: genetic-prompt-optimizer は LLM 1パス直接パッチのみで探索の多様性が低い
- After: 進化演算子（crossover / 部分軌跡結合）で局所最適を脱出できる ✨

## 既存実装との差分
- `genetic-prompt-optimizer` は corrections/context ベースの LLM 1パス直接パッチ（CLAUDE.md 明記）
- ギャップ: 進化演算子による部分軌跡の組み合わせ＝到達不能候補の生成が無い
- 実装コスト大のため優先度は中

## 提案アクション
Embodied-Minds-Lab/BES の forward search 実装を読み、進化演算子を `genetic-prompt-optimizer` / `rl-loop` に試験導入。

## 採用後の確認方法
- [ ] 同一ベースラインで 1パス版 vs 進化演算子版を rl-loop で比較 → best-case スコアが上回るか

## 再評価条件
1パスパッチが局所最適に陥っていると判明した時。

## 出典
ai-daily-report 2026-05-29 / arxiv:2605.28814

---

## #260 Handover: tech-eval 由来 BES/MemTrace/slop 実装 — Wave2(#256) 残  `[closed]`

## Decisions
- ai-daily-report (2026-05-29) の tech-eval から rl-anything 直結4概念を Issue 化 → agent team で実装。
- **Wave 分割採用**: #253/#254/#255 を並行 worktree、#256 は #253 マージ後（理由: #256 の進化探索が #253 の subgoal スコアを fitness-proportional 選択に consume するため。先行実装すると旧スコアリング契約に対して作られ手戻りする）。
- **#253 設計**: subgoal_scorer.py を新設し regression_gate(binary hard gate) は不変に保つ（責務分離）。サブゴール0件→0.0 fallback で NaN 回避。
- **#254 設計**: 新 oracle/LLM 不使用、既存信号のみ合成（misretrieval=低score / context_drift=staleness / corruption=検索直後 correction）。
- **#255 設計**: 決定論 regex 辞書（LLMコスト0）。constitutional に `overall*0.9 + slop_score*0.1` でブレンド。
- 統合経路は 3 PR 方式（PJ の PR ベースワークフローに合致）。

## Discarded Alternatives
- 全4並行 worktree: #256 の手戻りリスクで却下。
- #253 を regression_gate.py に直接拡張: binary gate と密スコアの責務混在 + file-size-budget リスクで却下。
- #254 の LLM 判定方式: 検索毎の LLM コストで却下（既存信号合成を採用）。
- #255 を principles の LLM-judge 原則追加: 非決定論・テスト困難で却下。
- AEPO（探索型ポリシー最適化）: RL訓練前提で rl-anything 設計と不適合のため scope 外。

## Deploy State
- dev/prod: N/A（プラグイン、デプロイ概念なし）
- main: cea09b89（未マージ）

## Next Actions
1. **PR #257/#258/#259 をレビュー・マージ**（CRITICAL なし、/review 済み・機械修正適用済み）。
2. **#257 マージ後に Wave2(#256) 起動** — evolution_operators.py（crossover/mutation）、#253 の `run_subgoal_scoring` 戻り値 `{total, subgoals[]}` を選択に consume、run_loop.py に世代ループ統合。feat/bes-forward-evolution。
3. **#257 ↔ #255 配線**: subgoal_scorer.py の `_score_slop_free` プレースホルダを slop_detector.detect_slop に接続（両 PR マージ後）。
4. 判断系の据え置き（各 PR コメント済み）: #257 correction の learning 一致を keyword 分解へ / #258 misretrieval の rank ガード + decay_days 未使用 / #259 `## まとめ` FP。
5. マージ後 `/rl-anything:cleanup` で locked worktree 3つ片付け。
6. spec が変わるため `/rl-anything:spec-keeper update` 検討。

## Context (auto)
- branch: main (cea09b89)
- PRs: #257 feat/bes-subgoal-scoring(6b70eb6a) / #258 feat/memtrace-attribution(4b62191e) / #259 feat/slop-dictionary(d0999884)
- 元 Issue: #253 #254 #255 #256

> 💬 comment:
>
> 全 Next Actions 完了。PR #257/#258/#259/#261 マージ済み、#257↔#255 slop 配線済み、spec-keeper update (5032dbf5) push 済み、locked worktree 4つ + マージ済みブランチ7つを cleanup 済み。BES subgoal/前向き進化探索・MemTrace・slop 辞書がすべて main に統合。

---

## #268 [tech-eval] 着手前レイヤー強化: 実装前グリル + CONTEXT.md 用語集  `[closed]`

mattpocock/skills の tech-eval から、rl-anything に欠けている「開発タスク着手直前」レイヤーの2概念を提案する。meta層（evolve/audit/prune/triage/handover/skill-creator）は上位互換で取り込み不要だが、この2つは mattpocock の看板価値と一致しギャップが大きい。

## 1. 実装前グリル（推奨度: 高）

対話的に曖昧点を branch ごとに詰めてから実装に入るパターン（mattpocock の grill-me / grill-with-docs 相当）。

- **Before**: `think-before-coding` rule（テキスト）+ ambiguous-intent-resolver agent はあるが、能動的な対話ループがなく、曖昧なまま `implement` に直行しがち
- **After**: 着手前に意図ズレ・前提の曖昧点を強制的に詰める対話ステップが入る
- **差分**: rule を「受動的チェック」から「能動的グリルループ」へ昇格。新規スキル新設 or 既存 ambiguous-intent-resolver agent のループ化
- **確認方法**: 数セッション運用後 `bin/rl-fleet status` の correction 傾向で「意図ズレ/手戻り」系 correction 発生率が低下しているか
- **再評価条件**: implement 後に意図ズレ起因の correction が corrections.jsonl に増えたら即着手

## 2. CONTEXT.md 用語集 / Ubiquitous Language（推奨度: 中）

PJ 固有 jargon を1語に圧縮する用語集ドキュメント（mattpocock の CONTEXT.md / shared language 相当）。

- **Before**: SPEC.md/ADR はあるが用語集は無（`grep ubiquitous/glossary` 0件）。同一概念を毎回再解釈し思考トークンを浪費
- **After**: 用語1語で表現でき、命名が一貫し思考トークンが減る。トークン経済を主戦場にする本 PJ（slop_detector・RTK）と思想的に噛み合う
- **差分**: 新規スキルでなく spec-keeper に「用語集セクション」追加で対応する案
- **確認方法**: 同一タスクを用語集あり/なしで実行し `bin/rl-fleet tokens` で input/thinking トークンを比較
- **再評価条件**: SPEC.md/ADR で同一概念に複数呼称が出始めたら

## 出典
tech-eval `mattpocock/skills` の評価結果より。diagnose/architecture改善/handoff/write-a-skill/git guardrails は実装済みのため対象外。

---

## #275 evolve の glossary seed 作成トリガー(Step 7.7)を決定論 phase に格上げ  `[closed]`

## 背景 — install ≠ enforcement の一段深い再発

#273 で「CONTEXT.md が無い PJ では evolve が用語集を LLM seed 提案生成する」Step 7.7 を evolve SKILL.md の Housekeeping に追加した。しかし docs-platform の実 evolve 実行（ev-v6 / session f4b9fac3, 2026-05-29）で**発火しなかった**ことを確認した。

### 観測された事実

| 観点 | 結果 |
|------|------|
| docs-platform に CONTEXT.md | なし（Step 7.7 の発火条件を満たす可能性大） |
| Step 7.7 の指示がコンテキストに | 入っていた（SKILL.md L7 で読み込み確認） |
| seed gate を実際に評価したか | していない（`find_undefined_terms`/glossary 評価の実行痕跡ゼロ） |
| 最終レポートでの言及 | 一切なし（他フェーズは全報告、用語集だけ欠落） |
| 実行モード | \`--dry-run\` |

## 根本原因

evolve のレポートは**オーケストレーション・スクリプトが emit する phase 出力**（observe/discover/.../pitfall_hygiene/fitness_evolution）を起点に書かれる。assistant はそのリストを順に報告する。

Step 7.7 は **SKILL.md の散文ステップにすぎず、phase 出力に裏打ちされていない**ため:
1. スクリプトが用語集 phase を出さない → assistant の注意リストに乗らない
2. \`--dry-run\` で「何も変更しない」モードに入り、サーフェスすべき advisory まで省略された

audit 側の glossary section は正しく配線済みだが、CONTEXT.md が無いと None を返す設計（正しい）。つまり**作成トリガーだけが決定論 phase を持たず、散文指示に依存 → 実運用で消える**。

これは learning_install_is_not_enforcement.md の構造的再発（一段深いレイヤー）。

## 打ち手

作成トリガーを evolve オーケストレーションの**決定論 phase に格上げ**する（audit が glossary drift を phase 化しているのと同じ手）:

- CONTEXT.md 不在 + undefined jargon ≥3（\`SEED_MIN_CANDIDATES\`）を deterministic に検出
- \`glossary_seed\` phase として**常時 emit**（dry-run でも「通常実行時に seed 提案します」と必ずレポートに出す）
- \`write_context_seed\` / SEED gate ロジック（#273）は既存 → phase 出力への配線が主作業

## 同時更新（workflow.md ルール）

- learning_install_is_not_enforcement.md に「散文ステップは決定論 phase に裏打ちしないと実運用で消える」を追記
- SPEC.md / CHANGELOG.md

## 検証

修正後、CONTEXT.md の無い実 PJ（docs-platform 等）で evolve --dry-run を回し、レポートに \`glossary_seed\` phase が出ることを確認する。

---

## #277 constitutional/chaos の _load_sibling がパッケージ化された coherence を silent skip  `[closed]`

## 背景

#129〜#143 で `scripts/rl/fitness/coherence.py` が `coherence/` パッケージ（`coherence/__init__.py`）へ分割された。その際 `_load_sibling()` の追従が `environment.py` のみに入り、`constitutional.py` と `chaos.py` は旧版（`{name}.py` 固定パス）のまま残った。

## 症状

`_load_sibling("coherence")` が `_fitness_dir / "coherence.py"` を探し、ファイルが存在しないため `FileNotFoundError`。`constitutional` fitness はこれを捕捉して **silent skip** する（`Constitutional Score スキップ: [Errno 2] No such file or directory: .../coherence.py`）。結果、`evolve`/`audit` の constitutional スコアから coherence 依存部分が欠落し続けていた（install≠enforcement の silent skip 型）。

docs-platform で `evolve --dry-run` を回した際に顕在化（#275 の動作確認中）。

## 影響範囲

- `scripts/rl/fitness/constitutional.py` `_load_sibling` ❌ 旧版
- `scripts/rl/fitness/chaos.py` `_load_sibling` ❌ 旧版
- `scripts/rl/fitness/environment.py` `_load_sibling` ✅ パッケージ対応済み（正解パターン）

## 打ち手

`environment.py` の正解 `_load_sibling`（`pkg_init.exists()` で分岐し `importlib.import_module`）を `constitutional.py` / `chaos.py` に移植。回帰テストで `_load_sibling("coherence")` がパッケージをロードできることを保証する。

---

## #285 [tech-eval] Belief Entropy: memory 書込時の品質ゲート  `[closed]`  (enhancement)

## 概念
長期タスクで再帰的要約がタスク関連情報を失う「belief deviation」を、要約の不確実性指標 **Belief Entropy** で検出し、認識的不確実性を高める要約を**書込前に**ペナルティ化する（出典: Meta-Cognitive Memory Policy Optimization, arXiv:2605.30159）。

## Before / After（開発者体験）
- **Before**: `auto_memory_runner` が生成した要約を無検査で MEMORY.md に貯め、後で recall が誤ヒット。memory_trace は事後帰属しかできない。
- **After**: 劣化しそうな要約を書込時に弾く。audit に block 件数が surface され recall 誤ヒットが減る。

## 確定アーキテクチャ（plan-eng-review 2026-06-02）

### 算出方式: 決定論 retention/drift プロキシ（D2=A）
- **LLM ゼロ追加**。auto_memory_runner は毎 Stop の hot hook で既に LLM 1 call を使うため、2回目の呼び出しは hot-hook latency pitfall と同型 → 回避。
- `score_belief(summary, corrections)`:
  - **retention** = 元 corrections のキー用語が要約にどれだけ保持されたか
  - **drift** = 要約がソースに無い主張をしていないか
  - 両者を `similarity.jaccard_coefficient`（既存 public 正準版）で算出、閾値下回りで block。
- 論文の厳密 entropy ではなく proxy だが hot-hook 原則と整合。

### 挿入点
`hooks/auto_memory_runner.py` の `_call_llm`(:389) と `_write_entry_file`(:395) の**間**（生成後ゲート）。
- 既存 `memory_gating.score_correction`（生成前: recurrence/novelty/severity）とは**別ポイント・補完関係**。memory_gating は再構築しない。
- block 時は **memory ファイル書込 AND MEMORY.md index 追記の両方をスキップ**（副作用テスト必須）。
- ゲートは **try/except でサイレント継続**（既存 `_apply_importance_score` と同じ graceful パターン。belief のバグで Stop hook を壊さない）。

### 配線先（enforcement surface）
1. `hooks/auto_memory_runner.py`（Stop hook 毎回）= 書込前ゲート。
2. `scripts/lib/audit/memory.py` の builder を `scripts/lib/audit/observability.py` の `_OBSERVABILITY_BUILDERS` に登録 → evolve/audit 両経路（markdown + 構造化）に「block した低信頼要約 N 件」を surface。

### 新規モジュール
`scripts/lib/belief_entropy.py`（決定論・optional import。memory_gating と同じ lean import パターン、duckdb 等の eager import なし）。

## 採用後の確認方法
- [ ] `/rl-anything:evolve`（または audit）を回す → memory セクションに「block した低信頼要約 N 件」が出る
- [ ] corrections を5件貯めて Stop → 劣化要約が MEMORY.md に append されない（書込+index 両方skip）
- [ ] belief OK 時は従来通り書込（回帰テスト緑）

## NOT in scope
- 別LLMでの厳密 Belief Entropy 算出（hot-hook 原則で却下）

## 再評価条件
memory_trace の誤ヒット率（misretrieval）が実測で問題化したら proxy → 厳密手法を再検討。

---
tech-eval 由来 / plan-eng-review でアーキ確定（推奨度: 高）

---

## #286 [tech-eval] Self-Trained Verification: 自己学習する fitness 検証器  `[closed]`  (enhancement)

## 概念（再フレーム済）
論文 Self-Trained Verification（arXiv:2605.30290）は「外部ラベル無しで検証器を自己学習」する手法。rl-anything は決定論 Python + LLM 呼び出しのみで **ML 学習基盤を持たない**ため、額面通りの検証器学習は innovation token の浪費・設計不整合。

→ **「accept/reject から検証閾値を自己導出して定期に再 calibrate」= recurring calibration に再フレーム**（plan-eng-review D1=A）。論文の狙い（自己検証の自動改善）の大半を既存資産で達成。

## Before / After（開発者体験）
- **Before**: fitness の accept/reject 閾値は `evolve-fitness` の**手動 calibration** 頼み。drift が長期間 surface されない。
- **After**: drift が recurring に可視化され、閾値の再 calibration が促される。手動起動頻度が減る。

## 確定アーキテクチャ（plan-eng-review 2026-06-02）

### スコープ: 新規学習基盤なし（D1=A）
- 既存 `skills/evolve-fitness/scripts/fitness_evolution.py`（accept/reject から score-acceptance 相関を分析し閾値を自己導出）を**再利用**。
- 検証器モデルの学習・重み更新は**作らない**。

### 配線先（enforcement surface）= 両方（D3=C）
1. **audit observability builder**: calibration drift を `_OBSERVABILITY_BUILDERS` に builder 1本登録。audit/evolve を回すと毎回評価し、drift 検出時に advisory 行を surface。clean 時は「評価したが drift なし ✓」、30件未満は「評価したがデータ不足」（silence≠evaluated を防止）。
2. **trigger_engine proactive**: accept/reject が **30件以上 かつ drift 検出**時に `/evolve-fitness` 起動をプロアクティブ提案。

### 不変条件
- **全 fitness 変更は人間承認が MUST**（evolve-fitness SKILL の既存ルール）。audit / trigger とも **advisory 止まり**、自動適用しない。

## 採用後の確認方法
- [ ] `audit`（または evolve）を回す → calibration drift が両経路に surface（drift / clean / データ不足の3分岐）
- [ ] accept/reject を 30件以上貯めて drift を作る → trigger_engine が /evolve-fitness を提案
- [ ] 30件未満では trigger 提案が出ない

## NOT in scope
- 論文通りの「学習する検証器モデル」（ML学習基盤は rl-anything に不適合 → 却下）
- fitness 閾値の自動適用（人間承認 MUST）

## 再評価条件
accept/reject が安定的に蓄積し proxy calibration の限界が見えたら、より厳密な自己検証手法を再検討。

---
tech-eval 由来 / plan-eng-review でアーキ確定（推奨度: 中、scope reduced）

---

## #288 [tech-eval] Harness Updating ablation: negative_transfer を更新コンポーネント別 delta に拡張  `[closed]`

## 背景

arXiv 2605.30621 **「Harness Updating Is Not Harness Benefit」**（2026-06-01）の tech-eval から。
自己進化ハーネスにおいて「更新したこと自体」と「実際に効いた更新」を切り分ける ablation 方法論を提案する論文。rl-anything の根幹（「更新したが効いていない自己進化」の検出）に直撃する。

daily report (2026-06-02) では rl-anything ⭐⭐⭐ 判定だが、tech-eval で実コード照合した結果、トレンド9概念中6つは既に実装済み。本件は数少ない「実装にギャップがある」概念。

## 概念の説明

ハーネス（スキル/プロンプト/設定）の更新が必ずしも性能向上に結び付かないことを示し、改善をもたらした要素と表面的変化を区別する。複数更新が同時に入ったとき「どの更新が効いたか」を分離する ablation 視点。

## Before / After（運用者体験）

- **Before**: `compute_negative_transfer` は skill 追加イベント前後の success-rate delta という **粗い単一指標**。「複数更新のうちどれが効いたか」までは分離できない。
- **After**: 更新コンポーネント単位で delta を分離 → audit で「効かない自己進化」をより正確に旗上げ。誤検出が減る。

## 既存実装との差分（根拠）

- 現状: \`scripts/lib/audit/usage.py:135 compute_negative_transfer\`（delta < -0.05 で負転移フラグ）
- 関連: evolve-fitness の calibration drift、observability contract (ADR-028)
- ギャップ: コンポーネント別 ablation 粒度がない

## 配線先（recurring ループ）

**audit（毎回発火）→ observability contract**。
\`scripts/lib/audit/observability.py\` の \`_OBSERVABILITY_BUILDERS\` に builder を1行登録すれば、markdown 経路と構造化経路（evolve が消費）の両方に自動伝播する（ADR-028）。手動 CLI 止まりにしない（install≠enforcement の learning に従う）。

## 採用後の確認方法

- [ ] \`/rl-anything:audit\` を回す → observability セクションに「更新コンポーネント別 delta」行が surface される（audit だけで出ること）
- [ ] \`python3 -m pytest scripts/lib/audit/\` が緑

## 再評価条件

論文に再現可能な ablation protocol があり、かつ現 \`negative_transfer\` の誤検出が実データで観測された場合に着手。protocol が抽象的すぎる/誤検出が観測されないなら見送り。

---
出典: tech-eval (ai-github-trending-2026-06-02.md) / arXiv 2605.30621

---

## #291 [tech-eval] SIRI: skill_extractor を discover/evolve に配線（成功軌跡からのスキル採掘を発火させる）  `[closed]`

## 背景

日報 (2026-06-03) の ⭐⭐⭐ 概念 **SIRI**（arXiv 2606.02355: 成功軌跡からスキルを採掘→検証→コアポリシーへ蒸留）を tech-eval で照合した結果、rl-anything には **採掘モジュール `skill_extractor` が実装済み（Issue #238 Phase 1）だが、どの recurring ループにも配線されていない**ことが判明した。

呼び出し元 grep の結果、参照は `SPEC.md` / `spec/architecture.md` / `scripts/tests/test_skill_extractor.py` のみ。discover / evolve / audit / hooks のいずれからも呼ばれず、実質休眠状態。「version ≠ enforcement」と同型の配線漏れ。

## SIRI の3段階と rl-anything の対応

| SIRI 段階 | rl-anything 既存 | 状態 |
|-----------|------------------|------|
| ①成功軌跡からスキル採掘 | `scripts/lib/skill_extractor/trajectory_sampler.py` + `skill_extractor.py` | **配線漏れ** |
| ②比較ロールアウトで検証 | `scripts/rl/fitness/chaos.py`（仮想除去ロバストネス） | 配線済 |
| ③コアポリシーへ蒸留 | evolve（SKILL.md / rules への反映） | 配線済 |

採掘（①）だけが宙に浮いている。

## Before / After（開発者体験）

- **Before**: 採掘モジュールは存在するが evolve/discover から呼ばれず、成功軌跡が候補化されない。`/discover` を回しても trajectory ベースのスキル候補は出ない。
- **After**: discover を回すたびに成功軌跡から `missed_skills` 候補が自動浮上し、既存の skill-triage CREATE パスに合流する。

## 配線先（enforcement surface）

- **`run_discover()`（scripts/lib/discover/runner.py）から `sample_trajectories` → `skill_extractor` を発火**させ、出力（skill-triage の missed_skills 形式）を既存の triage 合流ポイントに接続する。
- discover は evolve が消費する recurring ループなので、evolve のたびに自動発火する。手動 CLI 止まりにしない。

## 採用後の確認方法

- [ ] `/rl-anything:discover`（または `/rl-anything:evolve`）を回す → レポートに「成功軌跡から採掘したスキル候補（N 件）」が出る。出なければ配線が効いていない。

## 再評価条件

- 採掘候補の精度が低く noise になる場合は `generalizability_score` 閾値（skill_extractor のフィルタ）を引き上げる。

## 出典

tech-eval `reports/2026/06/ai-github-trending-2026-06-03.md`（SIRI: arXiv 2606.02355, 2026-06-01, コード公開 ✓）

🤖 Generated with [Claude Code](https://claude.com/claude-code)


---

## #292 [tech-eval] TASTE: eval 飽和度を audit observability に surface する  `[closed]`

## 背景

日報 (2026-06-03) の概念 **TASTE**（arXiv 2605.28556: ツール呼び出し列から難問を逆生成し、既存エージェントベンチの飽和を暴く）を tech-eval で照合した結果、rl-anything の eval 生成は**順生成のみ**で、**eval 自体の飽和（緑なのに頑健でない）を検出する経路が無い**ことが判明した。

- 既存: `scripts/lib/trigger_eval_generator.py` は sessions.jsonl → evals.json の*順生成*。
- 近接カバー: `negative_transfer` / fitness calibration drift（#285 / #286）はスキル追加の回帰を検出するが、eval 自体の飽和度は測らない。

## Before / After（開発者体験）

- **Before**: スキル追加の回帰は検出できるが、trigger eval が全部緑でも「それが頑健性を意味するのか飽和なのか」が分からない。fitness calibration の盲点が見えない。
- **After**: audit レポートに「eval 飽和度」が calibration drift と並んで surface され、緑の eval セットが信頼できる状態か判断できる。

## 配線先（enforcement surface）

- **audit の observability contract（`audit/observability.py` の `_OBSERVABILITY_BUILDERS`）に「eval 飽和度」builder を1行追加**。
- これにより markdown 経路（report.generate_report）と構造化経路（collect_observability → evolve Step 3.8）の両方に自動伝播し、evolve のたびに surface される。calibration drift と同セクションに同居させる。

## 採用後の確認方法

- [ ] `/rl-anything:audit` を回す → observability セクションに「eval 飽和度」行が calibration drift と並んで出る。

## 再評価条件

- trigger_eval_generator の eval が実運用で飽和兆候（高スコア継続なのに correction は減らない等）を示したら着手。優先度は SIRI 配線（高）より低い。

## 出典

tech-eval `reports/2026/06/ai-github-trending-2026-06-03.md`（TASTE: arXiv 2605.28556, 2026-05-27）

🤖 Generated with [Claude Code](https://claude.com/claude-code)


---

## #295 [Feedback] バグ報告: shadow環境でのdry-run実行時にproject CLAUDE.mdが読めず誤検知が多発  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告 / 改善要望
**コンポーネント**: evolve (audit / remediation / prune / discover)
**満足度**: 3/5

## 詳細

evolve の dry-run が shadow コピー環境で実行される際、対象プロジェクトの CLAUDE.md を解決できず（ログに "No CLAUDE.md found, skipping missed skill detection"）、CLAUDE.md 依存の除外ロジックが軒並み無効化されて誤検知が多発する。

観測した誤検知:
1. `detect_untagged_reference_candidates` — 「CLAUDE.md Skills セクション記載スキルは除外」ロジックが効かず、ユーザー呼び出し型の実行スキルを `type: reference` 付与候補として誤検出
2. `prune` zero-invocation — 日常的に使われるオンデマンドスキルがテレメトリ未記録のため大量に zero_invocation 判定される
3. `discover` skill_triage が "no_skills_found" を返す

いずれも「沈黙＝問題なし」ではなく「環境解決失敗による誤検出」であり、人間側で1件ずつ切り分ける必要があった。

提案:
- shadow 実行時もプロジェクトルートの CLAUDE.md / .claude/skills を実体パス基準で解決する
- CLAUDE.md が解決できなかった場合、CLAUDE.md 依存の除外ロジックを伴う検出（untagged_reference / missed_skill 等）は結果を出さず「環境解決失敗でスキップ」と明示する（誤検知を confident な提案として出さない）

---
*Submitted via /rl-anything:feedback*

> 💬 comment:
>
> ## 訂正: 真因は shadow ではなく CLAUDE.md パーサのバグでした
> 
> 実測で切り分けた結果、当初の前提（「shadow 環境で CLAUDE.md が読めない」）は**不正確**でした。訂正します。
> 
> ### 真因: `_parse_skills_section` が `- **ラベル**: `/skill-name`` 形式を読めない
> 
> `extract_skill_triggers(project_root=<実体PJ>)` は **CLAUDE.md が存在する（exists: True）のに trigger を 0 件**しか返しません。shadow / worktree / 一時コピーではなく、**実体 project_dir 上で**再現します。
> 
> 原因は `skill_triggers.py:_parse_skills_section` のリスト形式正規表現:
> 
> ```python
> re.match(r"^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]", stripped)
> ```
> 
> これは `- skill-name:` / `- /skill-name:` 形式しか拾えません。対象 PJ の Skills セクション実形式は:
> 
> ```markdown
> ## Skills
> - **AWSデプロイ**: `/aws-deploy` - `.claude/skills/aws-deploy/SKILL.md`
> - **RAGデータ投入**: `/rag-ingest` - `.claude/skills/rag-ingest/SKILL.md`
> ```
> 
> ハイフン直後が太字ラベル（`**...**` かつ非ASCII）で `[a-zA-Z0-9_:-]+` に不一致。肝心の skill 名 `/aws-deploy` は**コロンの後ろのバッククォート内**にあり、パーサが見ていません。
> 
> → trigger 0 件 → `claudemd_skills` 空集合 → `detect_untagged_reference_candidates` / `detect_missed_skills` の「CLAUDE.md 記載スキルは除外」ロジックが全滅 → ユーザー呼び出し型の実行スキルを誤検出。
> 
> ### 影響範囲
> - `untagged_reference_candidates`（`type: reference` 付与提案の誤検出）
> - `discover` の `detect_missed_skills` が "No CLAUDE.md found" を返す（実際は CLAUDE.md は在る）
> 
> ### 修正提案
> - `_parse_skills_section` のリスト行パーサで、行内のバッククォート内 `` `/skill-name` `` も skill 名候補として拾う（太字ラベル + コロン後ろにコマンドがある形式に対応）
> - "No CLAUDE.md found" メッセージは「CLAUDE.md は在るが Skills trigger 抽出 0」のケースと区別する（ミスリードを防ぐ）
> 
> ### 付随（別件・shadow の正体）
> ログに出ていた `/tmp/.../shadow/` は `chaos.py:_prepare_shadow_project()` が作る **Chaos Testing 専用の非git一時コピー**で、本誤検知とは無関係でした。これが `.claude/worktrees/` 配下の古い agent worktree 残骸を copytree しようとして `[Errno 2]` で落ちる別問題はありますが、fitness chaos 限定です。
> 
> ---
> *Correction submitted via /rl-anything:feedback*

---

## #298 feat: evolve 完了後に結果を解析しバグ/改善を半自動で GitHub issue 化する  `[closed]`  (enhancement,feedback)

## 背景 / モチベーション

evolve を回した後、その結果のなかに rl-anything 自身のバグや改善余地が見つかることがある。現状これを拾うフローは **完全に手動** で、ユーザーが気づいて `/rl-anything:feedback` を叩いて初めて issue 化される。

直近の実例が #295: evolve の dry-run が shadow コピー環境で実行された際、対象 PJ の CLAUDE.md を解決できず（`No CLAUDE.md found, skipping missed skill detection`）、CLAUDE.md 依存の除外ロジック（`detect_untagged_reference_candidates` の「Skills セクション記載スキルは除外」など）が軒並み無効化され、`untagged_reference` / `missed_skill` / prune zero-invocation の誤検知が大量発生した。これは「沈黙＝問題なし」ではなく「環境解決失敗による誤検出」で、ユーザーが手動で1件ずつ切り分けたうえで `/rl-anything:feedback` して #295 を起票した。

**この #295 のような手動フロー（evolve 結果を人間が読み解いて → 異常に気づき → 手で feedback して issue 化）を半自動化したい。** evolve 完了時に結果を決定論的に解析し、バグ/改善の兆候があれば issue ドラフトを提案 → ユーザー承認したものだけ起票する。

## スコープ（起動モデル: 半自動）

- evolve 完了時に結果を解析し、バグ/改善が見つかれば **issue ドラフトを提案** する。
- ユーザーが承認したドラフトのみ `gh issue create` で起票する。
- 既存の trigger 提案（`trigger_engine`）や `/rl-anything:feedback` と同型の安全設計（提案 → 承認 → 実行）にする。
- **スコープ外: 全自動の即起票はしない**（誤検知 issue の量産を防ぐ。#295 自体が「confident な誤検出を出さない」ことを求めた事例）。

## 解析対象（3軸）

evolve.py の result を解析入力にする。実際のキーは以下:

### 軸1: evolve の自己検出（observability contract）の異常・⚠
- `result["observability"]`（`scripts/lib/audit/observability.py` の `collect_observability` が格納、SKILL.md Step 3.8 で surface）を入力にする。key は `_OBSERVABILITY_BUILDERS` 単一ソース由来: `glossary_drift` / `unmanaged_pitfalls` / `belief_blocks` / `calibration_drift` / `eval_saturation` / `negative_transfer`。
- ✓ 行（clean）はスキップし、⚠/異常を示す行のみを「自己改善余地」候補として拾う。
- 設計上の接続点: この contract は ADR-028 で「必ず surface すべき行」を生成側でなく出力経路の契約として単一ソース化済み。新機能はこの **既存の構造化フィールドをそのまま解析入力として再利用** する（markdown blob を選択読みしない＝モグラ叩き回避）。将来 observability 項目が増えても `_OBSERVABILITY_BUILDERS` に1行足すだけで本機能の解析対象にも自動伝播する。

### 軸2: evolve 実行時のエラー / 誤検知
- 各フェーズの例外: `result["phases"][<phase>]` に `{"error": ...}` や `{"skipped": True}` が入っているもの（evolve.py は discover / audit / layer_diagnose / skill_evolve / remediation / reorganize / prune / pitfall_hygiene / fitness_evolution / self_evolution の各フェーズで例外を握って `error` キーに格納する）。
- `result["observability"]` がトップレベルで `{"error": ...}` を返したケース。
- **環境解決失敗による誤検知（#295 のパターン）**: CLAUDE.md / .claude/skills が解決できなかったのに confident な検出（untagged_reference / missed_skill 候補）を出している兆候。これは evolve が出力に「環境解決失敗でスキップ」と明示するようになれば検出が容易になる（#295 の提案と連動。本機能はその明示シグナルを消費する側）。

### 軸3: 改善余地の発見
- 低スコア skill: audit / fitness の skill quality スコア。
- 未配線モジュール（install ≠ enforcement パターン）・重複等の構造的改善余地: `result["phases"]["remediation"]` の `manual_required`、`reorganize` の split_candidates、prune の duplicate 等。

## 既存資産の再利用方針

- **observability contract**（`scripts/lib/audit/observability.py`, ADR-028）: 軸1の解析入力。再利用必須。
- **feedback スキル**（`skills/feedback/SKILL.md`）: 起票経路を再利用する。テンプレ（カテゴリ / コンポーネント / 詳細）、ラベル `feedback`、`gh issue create --repo todoroki-godai/rl-anything ... --label "feedback"`、認証チェック（`gh auth status`）、未認証時の `~/.claude/rl-anything/feedback-drafts/` ローカル保存フォールバック、プライバシー保護（SKILL.md 内容・ローカルパス・PJ固有情報を含めない MUST NOT）をそのまま踏襲する。
- **trigger_engine**（`scripts/lib/trigger_engine/`）: 半自動提案はここに乗せるのが自然。`write_pending_trigger` / `read_and_delete_pending_trigger` の pending-trigger 機構と snooze（`snooze_trigger` / `clear_snooze`）を流用して「evolve 完了時に提案を pending として書き、次の機会に surface → 承認 → 起票」を実装する。evolve.py は既に末尾で `result["trigger_summary"] = _build_trigger_summary()` を組み立てており、ここに self-feedback 提案を合流できる。

## dedup ゲート（重複 issue 化の防止）

同じ検出を毎 evolve で再起票しないことが必須。

- 検出ごとに **指紋（fingerprint）** を作る（例: 軸 + コンポーネント + 正規化した検出キー、observability key や phase error type のハッシュ）。
- 既起票指紋を DATA_DIR 配下（PJ スコープ、slug 付き）に台帳として保持し、一致したら再提案しない。
  - 注意: DATA_DIR は全 PJ 共通。台帳を単一ファイルで持つと別 PJ の状態を流用するため slug でスコープし read 側で照合する（既知の `pitfall_global_datadir_single_file` / ADR-031 の教訓）。
- 起票前に `gh issue list --search` で既存 issue（番号・タイトル）と突合し、類似があれば起票せずユーザーに既存番号を提示する。
- 既知の issue 番号（例: #295）を台帳に登録し、同型検出を抑止できるようにする。

## 決定論 / LLM 依存度の方針

- 軸1（observability 再利用）・軸2（phase error 抽出）・dedup（指紋照合）は **完全に決定論・LLM 非依存**（既存信号をそのまま消費する）。
- issue ドラフトの自然文整形に LLM を使う場合は、`/rl-anything:feedback` と同じく承認ゲートを通す。バッチで多数のドラフトを LLM 生成する設計にする場合は、`.claude/rules/llm-batch-guard.md` 準拠で件数・見積もりトークン数を事前にユーザーへ提示し確認を取る（無確認のバッチ LLM 処理は禁止）。

## 受け入れ条件（Acceptance Criteria）

- [ ] evolve 完了時、`result["observability"]` の ⚠/異常行・`result["phases"][*]` の `error`/`skipped`・低スコア skill 等から「バグ/改善候補」を **決定論で** 抽出できる。
- [ ] 抽出した候補は **issue ドラフトとして提案** され、ユーザーが承認したものだけ `gh issue create` で起票される（全自動起票はしない）。
- [ ] 起票経路は feedback スキルを再利用し、ラベル `feedback`、リポジトリ `todoroki-godai/rl-anything`、未認証時のローカルドラフト保存、プライバシー保護（SKILL.md 内容・ローカルパス・PJ固有情報を含めない）を満たす。
- [ ] dedup ゲートが効く: 同じ検出を 2 回連続 evolve しても再提案・再起票されない（指紋台帳 + `gh issue list --search` 突合、台帳は slug スコープ）。
- [ ] #295 のような「環境解決失敗による誤検知」を、confident な提案ではなく「環境解決失敗でスキップ」シグナルとして拾える（or evolve がそのシグナルを出すよう連動）。
- [ ] LLM を使う箇所がある場合、`.claude/rules/llm-batch-guard.md` 準拠（件数・トークン見積もりの事前提示）であり、決定論部分は LLM 非依存である。
- [ ] 新しい observability 項目が `_OBSERVABILITY_BUILDERS` に追加されたとき、本機能の解析対象にも追加配線なしで伝播する（モグラ叩き回避を回帰で確認）。
- [ ] スコープ外（全自動即起票をしない）が実装・ドキュメントで担保されている。

## 関連

- #295（手動 feedback フローの実例 — 本 issue のモチベーション）
- ADR-028（observability contract、`docs/decisions/028-observability-contract-audit-evolve.md`）
- `skills/feedback/SKILL.md` / `skills/evolve/SKILL.md` / `scripts/lib/trigger_engine/`


> 💬 comment:
>
> 完全実装済みのため close。決定論経路は `evolve_introspect`（evolve Step 11 = result.self_analysis を `analyze_evolve_result` で解析→flatten/filter_duplicates/render_issue_body で issue 候補化）、LLM メタレビュー経路は `report-feedback` スキル（旧 feedback 後継）として配線済み。提案→承認→`gh issue create` の半自動フロー（全自動即起票なし）という本 issue のスコープを満たしている。

---

## #299 feat: evolve 実行後の自己解析 → バグ/改善点を検出して GitHub issue を半自動起票  `[closed]`

## 背景 / モチベーション

現状、`/rl-anything:evolve` は Observe → Diagnose → Compile → Housekeeping → Report の3ステージを実行するが、**evolve 自身の実行結果（提案の質・誤検出・実行時エラー）を振り返る経路が無い**。
evolve が出した提案にバグや改善余地があっても、それは人間が気づいて手で issue を立てるまで構造に残らない。「install ≠ enforcement」（自動で回るループに載っていないものは育たない）と同型の配線漏れになっている。

そこで **evolve 実行後に evolve の出力を自己解析し、バグや改善点を検出したら rl-anything 自身にフィードバックして GitHub issue を半自動で起票する**機能を追加したい。自己進化パイプラインのメタ層（パイプライン自身を改善するループ）を閉じるのが狙い。

## 設計方針

### 起動モデル: 半自動（提案 → 承認 → 起票）

- evolve 完了後に解析を走らせ、検出結果を**候補として人間に提示**する
- 人間が承認した候補のみ `gh issue create` で起票する（誤検出を confident に起票しない＝`silence ≠ evaluated` 原則の裏返し）
- 全自動起票はノイズ issue 量産・誤検出の固定化リスクがあるため採らない

### 解析対象（3種）

1. **evolve の自己検出** — evolve が生成した提案・パッチ自体の質を検査する
   - 例: 矛盾する提案、適用不能なパッチ、regression gate を通らない変更、line budget 超過を誘発する提案
2. **evolve 実行時エラー / 誤検出** — 実行ログ・例外・observability セクションを解析する
   - 例: ステージ内で握り潰された例外、`claude_md_unparseable` のような環境解決失敗による誤検出、observability builder が surface し損ねた項目
3. **改善余地の発見** — テレメトリ・実行結果から構造的な改善機会を抽出する
   - 例: 繰り返し却下される提案パターン、低スコアで停滞しているスキル、未配線のモジュール（discover/audit から呼ばれていない機能）

## 受け入れ条件（Acceptance Criteria）

- [ ] evolve 完了後に自己解析フェーズが発火する（手動 CLI 止まりにしない＝evolve のたびに自動で回る）
- [ ] 3種の解析対象それぞれについて検出ロジックが存在し、検出ゼロのときも「評価したが該当なし ✓」を1行残す（沈黙＝配線漏れ誤認を防止）
- [ ] 検出候補を人間に提示し、承認された候補のみ issue 化する（半自動）
- [ ] 起票される issue は重複検出を持つ（同一 root cause で毎 evolve ごとに重複起票しない。既存 open issue とのタイトル/本文類似度で dedup）
- [ ] 解析は決定論優先。LLM を使う場合は単体テストで mock し、件数・トークン見積もりを事前提示する（`llm-batch-guard` 準拠）
- [ ] TDD（検出ロジックの単体テスト + 起票経路の mock テスト、no-LLM-in-tests 準拠）

## 検討事項 / オープンクエスチョン

- 解析の実体は新規スキルか、既存 `audit` の observability contract に builder を1本足す形か（後者なら `_OBSERVABILITY_BUILDERS` に登録するだけで markdown/構造化の両経路に自動伝播し、モグラ叩きを避けられる — ADR-028 のパターン）
- 重複起票防止の dedup キー（root cause 単位 vs 提案単位）
- 起票 issue のラベル付与（`enhancement` / `bug` の自動判定 or 人間選択）

## 参考

- 自己進化パイプライン: `CLAUDE.md` の「4つの柱」
- observability contract: ADR-028（`audit/observability.py`、surface 配線の単一ソース化）
- silence ≠ evaluated の運用例: negative_transfer / eval_saturation セクション


---

## #301 [evolve introspect] `onboard-project` に split と archive を同時提案（矛盾）  `[closed]`  (bug)

## 自己解析: 矛盾する提案

evolve が同一スキル `onboard-project` に対して **split（分割）** と **archive（淘汰）** を同時に提案しています。分割しようとする対象を同じ run で消そうとしており、提案ロジックが矛盾しています。

reorganize の split 検出と prune の archive 検出のどちらかが誤りか、両者の相互排他チェックが欠けています。

<!-- rl-evolve-introspect:self:split_archive_contradiction:onboard-project -->


---

## #302 [evolve introspect] `project-setup` に split と archive を同時提案（矛盾）  `[closed]`  (bug)

## 自己解析: 矛盾する提案

evolve が同一スキル `project-setup` に対して **split（分割）** と **archive（淘汰）** を同時に提案しています。分割しようとする対象を同じ run で消そうとしており、提案ロジックが矛盾しています。

reorganize の split 検出と prune の archive 検出のどちらかが誤りか、両者の相互排他チェックが欠けています。

<!-- rl-evolve-introspect:self:split_archive_contradiction:project-setup -->


---

## #303 [tech-eval] SkillPyramid: スキルの階層的統合（hierarchical consolidation）を reorganize/prune に追加  `[closed]`  (enhancement)

## 概念
スキルが獲得・蓄積されると数が増えるが、現状の reorganize（split検出）/ prune（merge提案）は **フラット**な統廃合しかできず、増えたら max_skill_count(30) で頭打ちになるだけ。SkillPyramid (arXiv:2606.03692) は低レベルスキルを上位スキルへ**階層的に束ねる**ことで肥大化を構造で抑える。

## Before / After（運用者体験）
- Before: スキルは split/merge で平面整理。増えると上限に張り付き、どれを消すかの判断に追われる
- After: 低レベルスキル群を上位スキルへ階層統合する提案が出て、肥大化が構造的に抑制される（✨新機能 / 🛡安定性）

## 既存実装との差分（根拠）
- `scripts/lib/reorganize.py` — split 検出のみ
- `scripts/lib/prune/` — merge 提案（フラット）
- max_skill_count=30（`scripts/tests/test_skill_lifecycle.py:119` ほか）
- → 「階層（低→上位）」軸が存在しない

## 配線先（enforcement surface）
**audit/evolve が消費する reorganize/prune に「階層統合提案」セクションを追加**する。手動 CLI 止まりにしない（version ≠ enforcement）。

## 採用後の確認方法
- [ ] `/rl-anything:evolve`（または audit）を回す → reorganize/prune セクションに「階層統合提案（低レベル→上位）」行が出る

## 再評価条件
skill 数が max_skill_count(30) に常時張り付く / prune の merge 提案が頻発したら着手

出典: arXiv:2606.03692 (daily report 2026-06-04)

---

## #304 [tech-eval] Skill-RM: スキル軸での異種評価基準統一（報酬モデル）を rl-scorer/fitness に検討  `[closed]`  (enhancement)

## 概念
Skill-RM (arXiv:2606.03980) は、タスクごとに異なる評価基準を「エージェントのスキル」を共通軸として単一報酬モデルで横断評価する。現状の rl-anything fitness は「軸別」重み統合（coherence/telemetry/constitutional/skill_quality）で、「スキル別」の異種基準統一とは**直交**する。

## Before / After（運用者体験）
- Before: fitness は軸別重み統合。スキルごとの異種成功条件は統一スコアで測れない
- After: スキルごとの異種基準を1つの報酬で横断評価でき、rl-scorer のキャリブレーションが安定（🛡安定性）

## 既存実装との差分（根拠）
- `scripts/rl/fitness/environment.py:71`（`_normalize_weights`）/ `:80`（`compute_environment_fitness`）— 軸別の動的重み統合
- rl-scorer は技術40%/ドメイン40%/構造20%
- → 「スキルを共通軸にした評価統一」は未実装

## 配線先
rl-scorer / environment fitness（evolve のたびに発火）

## 採用後の確認方法
- [ ] `/rl-anything:evolve` を回す → rl-scorer 出力にスキル別の統一スコアが現れ、calibration drift の乖離が縮小

## 再評価条件
calibration drift が継続的に surface されたら着手

出典: arXiv:2606.03980 (daily report 2026-06-04)

---

## #305 [tech-eval] SkillOpt: スキルを外部プログラムとして訓練・最適化する枠組みの調査  `[closed]`  (enhancement)

## 概念
Microsoft SkillOpt（Rohan Paul 解説, daily report 2026-06-04）は「agent skill は手書き・LLM一発生成・緩い修正で劣化しやすい」と問題提起し、スキルを**小さな外部プログラムとして訓練**すべきと主張。rl-anything の optimize は LLM 1パスパッチ＋regression gate で、勾配的な「訓練」とは異なる。

## Before / After（運用者体験）
- Before: corrections → LLM 1パスパッチ＋regression gate / BES 進化探索で改善
- After: 劣化しやすい手書きスキルを勾配的に訓練する枠組みで evolve の収束が改善（✨新機能）

## 既存実装との差分（根拠）
- `scripts/lib/evolution_operators.py:189`（`evolve_generation` — BES 前向き進化探索）
- `scripts/lib/subgoal_scorer.py`（BES 後ろ向き分解）
- optimize = LLM 1パスパッチ + regression_gate
- → 「スキルをプログラムとして訓練」の枠組みは部分実装

## 配線先
optimize / rl-loop `--evolve-search`（evolution_operators が消費）

## 採用後の確認方法
- [ ] `rl-loop --evolve-search` を回す → 世代ごとの subgoal fitness が単調改善し、収束世代数が減る

## 再評価条件
論文コード公開後 / evolve の収束が頭打ち（同じ却下 type 反復）になったら

出典: Microsoft SkillOpt (daily report 2026-06-04)

---

## #306 [tech-eval] Interaction Trajectories: 軌跡有効性の実証基準を skill_extractor の score 算定に反映  `[closed]`  (enhancement)

## 概念
"What Makes Interaction Trajectories Effective for Training Terminal Agents?" (arXiv:2606.03461) は、端末エージェント訓練に**有効な相互作用軌跡の条件**を実証的に調査。rl-anything の skill_extractor は generalizability_score の閾値（TRAJECTORY_SKILL_SCORE_THRESHOLD=0.25）で機械的にフィルタしており、score 算定根拠を補強できる。

## Before / After（運用者体験）
- Before: 閾値0.25で機械的にフィルタ
- After: 「何が有効な軌跡か」の実証基準で score 算定根拠を補強し、候補の質が上がる（🛡安定性）

## 既存実装との差分（根拠）
- `scripts/lib/skill_extractor/`（`sample_trajectories` / `extract_skill_candidates` / generalizability_score）
- run_discover 配線済
- → score 式の根拠が論文の実証基準で補強できる

## 配線先
skill_extractor の score 算定（run_discover → evolve で発火）

## 採用後の確認方法
- [ ] `/rl-anything:discover` を回す → trajectory_skill_candidates の triage 通過率（採用/却下比）が改善

## 再評価条件
trajectory 由来候補の triage 却下率が高止まりしたら

出典: arXiv:2606.03461 (daily report 2026-06-04)

---

## #308 [feat] Triage Decision Ledger: SKIP 判断に TTL・再発カウンタを持たせ「定期見直し」を evolve ループに内蔵する  `[closed]`  (enhancement)

## 課題

毎日 `evolve` を回すと、同じスキル候補に対して **「スキル SKIP（推奨）」が繰り返し surface される**。`meta_quality_check`（`scripts/lib/meta_quality.py:99-101`）が `low_reuse AND 重複候補あり → SKIP` を**ステートレスに毎回ゼロ判定**しており、過去に同じ判断を下したことを覚えていないのが根本原因。

これにより3つの問題が起きている:

1. **同じ SKIP が毎日出るノイズ** — 一度「今はいい」と判断しても毎回蒸し返される
2. **「繰り返し検出される」というシグナルの喪失** — 毎日 SKIP 候補に挙がる＝閾値が不適切 or 世界が変わったサインなのに、低頻度として毎回握りつぶす
3. **判断に賞味期限がない** — 数十日前の SKIP が今も妥当とは限らない（AI の進化が速く、当時 SKIP でも今は CREATE 相当のことがある）

欲しいのは「定期見直しコマンド」そのものではなく、**判断に状態（TTL・再発カウンタ）を持たせ、見直しを evolve ループに内蔵する**こと。

## 提案: Triage Decision Ledger

`optimize_history_store` と同じ **PJ スコープ**で判断を永続化する（`DATA_DIR/triage_decisions/<slug>.jsonl`、slug は worktree 安全に `git --git-common-dir` 親 basename で解決 — [[pitfall_worktree_slug_show_toplevel]] / ADR-031 と同様）。

### レコード schema（候補キー単位）
- `candidate_key`（正規化したスキル候補名/シグネチャ）
- `recommendation`（CREATE / REVIEW / SKIP）
- `reuse_rate`, `duplicate_of`
- `first_seen`, `last_seen`, `times_seen`, `times_skipped`
- `decided_at`, `ttl_days`, `suppressed_until`

### 3層の見直しトリガー（evolve/discover が台帳を参照して挙動を変える）

| 層 | トリガー | 挙動 |
|----|---------|------|
| **① 抑制（cooldown）** | SKIP 済み & クールダウン内 & 再発が閾値未満 | 個別表示せず「SKIP 抑制 N件 ✓」の **1行に畳む**（沈黙≠評価にしないため必ず1行残す＝ADR-028 の observability contract と同思想） |
| **② 再発エスカレーション** | `times_skipped >= ESCALATE_N`（窓内、既定3） | **SKIP→REVIEW に自動昇格**。「N回 SKIP: 繰り返し検出。閾値か採用を見直せ」 |
| **③ 賞味期限切れ（TTL）** | `now > decided_at + ttl_days`（既定45日） | 🔄 として **1回だけ**強制再評価。「この判断は N 日前。当時 SKIP だが再評価を」 |

### ④ 外部シグナル連動（follow-up・本 Issue のスコープ外でも可）
release-notes-review / pj-report が「過去 SKIP 候補に合致する新技術」を検出したら TTL をリセットし REVIEW に昇格。「定期見直し」を*時間*でなく*実際の変化*で駆動する。

## 配線先（enforcement surface）★重要
新スキルや手動コマンドにしない。**毎日回している `evolve` / `trigger_engine` の中**で発火させる（手動 CLI 止まりは version≠enforcement で実質効かない、[[learning_install_is_not_enforcement]]）。

設計の前例: **デイリーレポート（ai-daily-report）自身が持つ「8日以上ぶりの再登場 🔄 / dedup_days」と同じパターン**を triage 判断に適用するだけ。

## 既存実装との接続点（根拠）
- `scripts/lib/meta_quality.py:49`（`meta_quality_check`）— SKIP 判定。ここに台帳参照を差し込む
- `scripts/lib/skill_triage.py` — CREATE/UPDATE/SPLIT/MERGE/OK 判定パス
- `scripts/lib/optimize_history_store.py` — PJ スコープ JSONL ストアの実装パターン（流用）
- trigger_engine / evolve SKILL.md の surface 経路

## 採用後の確認方法（recurring ループで出る形）
- [ ] `/rl-anything:evolve` を**連続2回**回す → 2回目で同じ SKIP 候補が個別表示されず「SKIP 抑制 N件 ✓」に畳まれる（①）
- [ ] 同候補を3回以上検出 → `REVIEW`（再発エスカレーション）に昇格表示される（②）
- [ ] `decided_at` を TTL 超過させた fixture → 🔄 強制再評価が1回だけ出る（③）

## テスト方針（TDD First）
- `meta_quality` / 台帳 read-write の単体テストを先に書く（LLM 非依存・決定論なので mock 不要）
- 連続 evolve の冪等性（①の抑制が2回目で効く）を正常系 E2E で確認
- 副作用: 台帳ファイルの肥大化・別 PJ slug への混入が無いこと（[[pitfall_global_datadir_single_file]]）

出典: daily report 2026-06-04 の課題ヒアリングから

---

## #316 hook_drift: dead_ref 検出（参照先スキルの実在突合）  `[closed]`  (enhancement)

## 背景
ADR-036 / PR #315 で hook_drift の第一フェーズ stale_pin を実装。dead_ref（hook/flow-chain が参照する skill 名がどのツールにも実在しない）は **表記ゆれによる false positive 量産リスク**のため第一フェーズから除外した。

## やること
- live registry（gstack skills + rl-anything skills + ~/.claude/skills + 各プラグイン SKILL.md）の skill 名集合を構築
- **正規化レイヤーを先に固める**: `/rl-anything:spec-keeper update` → `spec-keeper` 等のプレフィックス/ツール名/空白除去。テストで変換を固定してから検出に繋ぐ
- 誤検知が observability contract 経由で毎 evolve 出続けると audit 信頼性を毀損するため、正規化の信頼性確保が前提（glossary_drift が undefined_terms を gate しない教訓と区別）

## 関連
ADR-036, PR #315, scripts/lib/hook_drift.py

---

## #317 hook_drift: internal_drift 検出（hook 内ハードコード vs 外部宣言）  `[open]`  (enhancement)

## 背景
ADR-036 / PR #315。internal_drift（hook 内 FALLBACK_CHAIN 等のハードコードが外部宣言 flow-chain.json と乖離）は **実害がほぼゼロ**（flow-chain.json が読めれば FALLBACK は使われない）ため第一フェーズから除外＝YAGNI 判定。

## やること（着手条件付き）
- 「外部宣言ファイルを持つ hook」が複数現れたら汎用突合を検討
- 現時点で対象は suggest-gstack-next-action 1件のみ。対象が増えるまで保留
- PR #315 では hook 本体の FALLBACK を SoT 整合に手修正済み（機構不要で解消済み）

## 関連
ADR-036, PR #315

---

## #318 hook 有用性評価（follow-through）第2フェーズ  `[open]`  (enhancement)

## 背景
ADR-036 / PR #315。hook 評価の本命は「提案が実際に従われたか（follow-through）」。第一フェーズでは観測データが無く先送り。PR #315 で hook 本体に fire-log（`~/.gstack/analytics/hook-fires.jsonl` に {ts, skill, suggested_next} を append）の種をまいた。

## やること
- `hook-fires.jsonl`（提案発火）× `skill-usage.jsonl`（実行スキル）を cross-ref して「提案 → 実行」追従率を算出
- rl-anything の observe → diagnose 構造に乗せ、follow-through が低い hook は delete 候補として surface
- 着手条件: fire-log が十分蓄積してから（数週間運用後）

## 関連
ADR-036, PR #315

---

## #319 hook_drift: ADR-036/docstring の「flow-chain.json は gstack setup で再生成」前提が誤り（実環境で確認）  `[closed]`

## 概要

PR #315 (hook_drift / stale_pin) を実環境でドッグフードした際、`hook_drift.py` の docstring と ADR-036 が置いている前提が**実態と食い違っている**ことが判明した。検出ロジック自体は正しく動くが、「どう直すか」の説明が誤っている。

## 前提崩れの内容

`scripts/lib/hook_drift.py` の docstring（および `docs/decisions/036-hook-drift-stale-pin-first.md`）はこう記述している:

> flow-chain.json は gstack の setup/upgrade 時に再生成される設計だが、再生成が漏れると hook が古いフロー構成を提案し続ける。

しかし実環境調査の結果:

1. `~/.claude/skills/gstack/`（setup / bin 含む全体）を grep しても `flow-chain.json` への参照が **ゼロ**。gstack は一切このファイルを書き込まない。
2. setup が触るのは `~/.gstack/.last-setup-version` のみ。
3. `~/.gstack/flow-chain.json` は `/rl-anything:implement` `/rl-anything:spec-keeper update` を参照する **手動メンテのファイル**（gstack 純正ではない）。`gstack_version` フィールドは手書きのピン。

→ つまり **gstack setup を回しても flow-chain.json の version ピンは更新されず、stale_pin は自動解消されない**。実際の解消は flow-chain.json の `gstack_version` を手で更新する必要があった（実環境で 1.47.0.0 → 1.55.0.0 に手修正して drift なしを確認済み）。

## 影響

- stale_pin **検出**は正しい（pin と実環境の乖離は事実）。誤検知ではない。
- ただし audit/evolve が出す**解消ガイダンス**（「gstack upgrade 後に flow-chain.json が再生成されたか見直しを推奨」）が、再生成されない前提のファイルに対して的外れになっている。

## 対応案

1. `hook_drift.py` docstring の「gstack の setup/upgrade 時に再生成される設計」を「flow-chain.json は手動メンテされる SoT で、gstack 本体は生成しない」へ訂正。
2. ADR-036 に Consequences 追記（実環境ドッグフードで前提崩れを発見した経緯）。
3. `build_hook_drift_section` の stale メッセージのガイダンス文言を「flow-chain.json の gstack_version ピンを手で更新」へ調整（再生成を促す表現を外す）。

## 備考

実環境で偶然 stale_pin（1.47.0.0 vs 1.55.0.0, MINOR 8 差）が起きていたため、合成 fixture でなく実データで検証できた。`learning_synthetic_fixture_false_confidence` の好例。

---

## #326 [tech-eval] エージェントチーム自動設計を agent-brushup に追加（audit 配線・subagent-guard 整合前提）  `[closed]`

## 概念
ドメイン（解きたいタスク領域）を与えると、必要な専門スキルを生成し、複数の専門エージェントから成るチーム編成を自動で設計する "メタスキル"。エージェント設計そのものを上位スキルとして抽象化する（参考: revfactory/harness, 2026-06-02 trending +957⭐）。

## Before / After（ユーザー体験）
- **Before**: `agent-brushup` は `create <role>` で単体エージェントの scaffold を出すのみ。「どのエージェント群を・どんな役割分担で組むか」は人手で設計（`skills/agent-brushup/SKILL.md:23`）。
- **After**: ドメインを与えると専門エージェント群の役割分担まで自動編成し、チーム定義を提案する。

## 既存実装との差分（根拠・ギャップ）
- 既存: `skills/agent-brushup/SKILL.md:23 create <role>` = 単体 scaffold。チーム編成・役割分担の自動設計は未実装。
- ギャップ: 単体生成 → 複数エージェントのチーム編成設計（上位メタレイヤー）。

## ⚠️ 設計上の前提（着手前に決めること）
本機能は `~/.claude/rules/subagent-guard.md`（subagent 乱立防止・カスケード生成禁止・3個以上は確認必須）と緊張関係にある。
**自動編成の出力を「提案 diff の提示」に留め、実際の subagent 起動はユーザー承認を介す**形なら整合する。自動起動まで踏み込むなら subagent-guard 方針の見直しが先。

## 配線先（enforcement surface）
- 現状: `agent-brushup` スキルを**手動起動したときのみ**発火 → version≠enforcement と同型で死蔵リスク。
- 提案: `audit` のエージェント診断 section に「チーム編成ギャップ」を出し、evolve/audit の recurring ループで毎回 surface する。手動 CLI 止まりにしない。

## 採用後の確認方法
- [ ] 配線後: `/rl-anything:audit` を回す → レポートに「エージェント編成ギャップ」section が出る。
- [ ] `/rl-anything:agent-brushup` でドメインを渡す → チーム定義 diff が提示され、承認するまで subagent は起動しない（subagent-guard 整合の検証）。

## 再評価条件
- subagent 乱立防止方針が緩和された / マルチエージェント運用が常態化したとき。
- agent-brushup の単体 scaffold では設計コストが回らなくなったとき。

---
出典: ai-daily-report 2026-06-02 / tech-eval 評価（推奨度: 中）
🤖 Generated with [Claude Code](https://claude.com/claude-code)


---

## #336 evolve 出力が JSON パース不可 + Path/str 型エラー（stdout汚染・型不整合）  `[closed]`  (bug)

## Problem

sys-bots で `/rl-anything:evolve` を1回フル実行した際、出力の再現性・パース可能性に2件の不具合があった（報告: todoroki-godai/sys-bots, 2026-06-05）。

### 1. stdout に警告ノイズと結果 JSON が混在（パース不可）
`rl-evolve --dry-run` の stdout 先頭に診断ノイズが出力され、その後ろに本体の結果 JSON が続く:
- `Chaos Testing スキップ: [...]`（worktree の存在しないファイルパス列挙、`[Errno 2] No such file or directory`）
- scipy `RuntimeWarning: invalid value encountered in scalar divide`

stdout が純粋 JSON でないため `json.loads` が失敗し、先頭から `JSONDecoder().raw_decode` でスキャンする防御コードが必要になった。

### 2. Path/str 型契約の不整合（TypeError）
`emit_customize_request(name, skill_dir)` に `skill_dir` を `str` で渡すと `proposal.py:248` で:
```
TypeError: unsupported operand type(s) for /: 'str' and 'str'
```
`skill_dir / "SKILL.md"` が str に対して `/` を使うため。一方 `assess_single_skill` は str を受け入れる。関数間で Path/str の期待が不統一。

## Impact
- evolve は self_analysis で自動 issue 化する下流パイプラインを持つため、出力品質不良が下流を汚染する
- 利用側が `raw_decode` スキャンや型正規化の防御を自前で書く不当な負担

## Expected
1. stdout = pure JSON（`--json` モード追加 or 警告を stderr へ分離）。stdout を `json.loads` で必ず validate するテストを追加
2. `skill_dir` を関数入口で `Path()` 正規化（`emit_*` / `assess_*` / `ingest_*` で契約統一）

## 環境
- rl-anything plugin / sys-bots PJ（env_tier: large, スキル14 custom + 5 archived）


---

## #337 evolve 検出が _archived/Slack ID/汎用略語を誤検知（remediation 約80%ノイズ）  `[closed]`  (bug)

## Problem

sys-bots で `/rl-anything:evolve` を実行したところ、remediation / discover の検出が前処理フィルタ欠落により誤検知まみれになった（約80%ノイズ）。共通根は「アーカイブ除外・doc文脈ID除外・ストップリスト」の欠落（報告: todoroki-godai/sys-bots, 2026-06-05）。

### 1. `_archived/` スキルが評価対象に混入
- skill_evolve の batch_guard グループ19件のうち5件が `.claude/skills/_archived/` 配下（bot-tool-configuration, just-commands, bedrock-slack-bot-troubleshoot, google-sheets-to-rag, web-auth-debug）
- remediation の `missing_effort` 5件が**すべて** `_archived/` 配下スキルへの effort frontmatter 付与提案 → 無意味

### 2. hardcoded_value が Slack ID を誤検知（41件）
slack-fetch(22) / alert-ops-e2e-test(12) / slack-app-create(5) 等の SKILL.md にある Slack チャンネルID（`C0ACWGEA5BR`）・App ID（`A0...`）を「設定に抽出すべきハードコード値」として41件フラグ。これらは運用手順を示す**意図的な参照値**で秘匿対象でない。

### 3. glossary jargon のストップリストが弱い（56件中45件ノイズ）
CONTEXT.md 不在で「未登録 jargon 56件」と surface されたが、中身に英大文字ストップワード（`ALWAYS, FIRST, INFO, CUSTOM, DIR, MB, MD`）や Slack ID（`C05KMHFDPB9`）が混入。真の固有語は11件程度。

### （関連）batch_guard のトークン見積もりが約50倍過大
「19スキル・推定 893,000 トークン」と警告されたが、実際に emit された判断採点プロンプトは各 ~2,000字 × 14件 = 合計 ~10k トークン程度（SKILL.md 先頭2000字に truncate）。893k は全文×全スキルの概算らしく実コストと桁違い。`_archived` 除外＋truncate 反映で改善する。

## Impact
- remediation 提案の約80%がノイズ → 本物の検出が埋もれる
- 利用者が手動で false positive 削除に時間を消費

## Expected
1. glob 除外: `**/_archived/**`（必要なら `**/disabled/**` も）を全フェーズで skip
2. hardcoded_value allowlist: doc 文脈の Slack ID（`C0[A-Z0-9]{8,}` / `A0[A-Z0-9]{8,}`）を除外
3. glossary ストップリスト拡張: 英大文字ストップワード集合 + Slack ID regex
4. batch_guard 見積もり: truncate 後のプロンプト長で算出（`_archived` 除外後）
5. test: 実PJ eval で hardcoded_value 41→5以下、jargon 56→15以下を確認

## 環境
- rl-anything plugin / sys-bots PJ


> 💬 comment:
>
> docs-platform ev-0605 run でも `_archived` 誤検知を再現したので具体例を追記します。
> 
> `detect_missing_effort_frontmatter` が **`.claude/skills/.archive/` 配下**を走査し、アーカイブ済みスキル3件に effort frontmatter 付与を proposable として提案:
> 
> | file | proposed_effort | reason |
> |------|-----------------|--------|
> | `.archive/add-repo/SKILL.md` | low | content_lines=39 < 80 |
> | `.archive/docs-context/SKILL.md` | low | content_lines=79 < 80 |
> | `.archive/refresh-aws-secrets/SKILL.md` | medium | content_lines=90, default |
> 
> アーカイブ済みスキルは active 対象外なので、remediation の走査から `**/.archive/**`（および `.archive` ディレクトリ全般）を一律除外すべき。これは本 issue の「`_archived` 誤検知」ケースの再現＋具体パターンです。
> 
> なお同 run で stale_ref が SSM/tmp パスを confidence 0.95 で `auto_fixable` に入れる件は、「ノイズ」を超えた safety リスク（フルオート時の自動改変）として #339 に切り出しました。
> 

> 💬 comment:
>
> 補足: `.archive/` 配下スキルを `missing_effort` で誤検知する件は本 issue のスコープに含めて対応しました（重複 issue は作成しません）。
> 
> PR #342 で `EXCLUDED_SKILL_DIRS` に `_archived` / `disabled` を追加し、`effort_detector.detect_missing_effort_frontmatter` にも `is_excluded_skill_path` フィルタを配線したため、`.archive` / `_archived` / `disabled` / `.gstack-backup` 配下はいずれも missing_effort 検出対象外になります。回帰テスト `test_skips_archived_skills` で `.archive` / `_archived` / `disabled` の3種を網羅しています。

---

## #339 evolve: stale_ref が SSM/tmp パスを confidence 0.95 で auto_fixable に分類（フルオート時の memory 誤改変リスク）  `[closed]`  (bug)

## 概要

`stale_ref` 検出器が **SSM パラメータパス**・**`/tmp` 一時ファイルパス**を confidence **0.95** で `auto_fixable` バケットに分類する。`--dry-run` では無害だが、**フルオート evolve（非 dry-run + 一括修正）では memory ファイルを誤って書き換える**データ汚染リスクがある。

これは #337（remediation 誤検知＝ノイズ）と同根だが、**「ノイズ」ではなく「高 confidence で auto-fix バケットに入る安全性バグ」**として切り分ける（優先度が違う）。

## 再現（docs-platform ev-0605 run）

`auto_fixable`（confidence 0.95, impact_scope=file）に以下2件が入った:

| file | 検出 path | 実体 | 判定 |
|------|-----------|------|------|
| `MEMORY.md:38` | `/docs-platform/strategy` | **SSM パラメータパス**（`SSM /docs-platform/strategy/*`） | FP |
| `project_cost-analysis-bedrock.md:53` | `/tmp/ab_test.py` | A/B テストの**歴史的引用**（一時ファイル） | FP |

どちらも「ファイル参照」ではない。前者は AWS SSM パス、後者は「何を実行したか」の記録。

## 問題点

1. `/` 始まりでも `/tmp/...`・SSM 風パス（拡張子なし・バッククォート内・`/docs-platform/...` のような論理パス）を **file ref として扱っている**
2. これらが **confidence 0.95 → `auto_fixable`** に入る。`auto_fixable` は仕様上「一括修正」で自動適用される対象であり、FP がここに入ると**自動でファイル改変**される
3. docs-platform の memory には「SSM パス stale_ref は既知 FP」と明記済みだが、検出器に反映されていない

## 提案

- `/tmp/` 配下・拡張子なしの論理パス・SSM 風パス（`/<service>/...` でファイルシステム上に存在せずバッククォート内）を file-ref 検出から除外
- 少なくとも上記パターンは confidence を下げ **`proposable` 止まり**にして auto-fix バケットに落とさない（人間確認を挟む）

## 関連
- #337（remediation 誤検知の総合 issue）

<!-- rl-evolve-introspect:stale-ref-ssm-tmp-autofix -->


---

## #340 evolve: reorganize の TF-IDF cosine でゼロノルムベクトル由来の NaN 警告（クラスタリング結果が歪む）  `[closed]`  (bug)

## 概要

evolve 実行の冒頭で scipy が NaN 警告を stderr に出す。クラッシュはしないが、reorganize（TF-IDF 階層クラスタリング）の距離計算に **NaN が混入**しており、hierarchy / split の結果が歪む可能性がある。

```
/opt/homebrew/lib/python3.14/site-packages/scipy/spatial/distance.py:670: RuntimeWarning: invalid value encountered in scalar divide
  dist = 1.0 - uv / math.sqrt(uu * vv)
```

## 原因（推定）

`dist = 1.0 - uv / sqrt(uu * vv)` の `uu` または `vv` が 0 = **ゼロノルムのベクトル**。空 or 退化したスキル内容（TF-IDF が全ゼロになるドキュメント）が cosine 距離計算に渡され、0除算 → NaN。

## 影響

- reorganize の cosine 距離行列に NaN が入ると、階層クラスタリング（linkage）の結果が非決定的・不正になりうる
- 今回の run では `hierarchy_candidates` が1件出た（`remove-project + manage-repo + handbook-lifecycle → pj-remove-suite`）が、NaN 混入下でのクラスタリング結果なので**信頼性が疑わしい**

## 提案

- TF-IDF ベクトル化後にゼロノルム行を検出して除外（またはクラスタリング対象から外す）
- cosine 距離計算前に `uu == 0 or vv == 0` をガードし、NaN を 1.0（最大距離）等にフォールバック
- `warnings.filterwarnings` で握り潰すのではなく**根本原因（ゼロベクトル）を除去**する

## 再現
- docs-platform ev-0605 run（custom 11スキルの reorganize フェーズ）

<!-- rl-evolve-introspect:reorganize-scipy-nan -->


---

## #341 evolve: self_analysis が stderr 警告・auto_fixable への FP landing を検出できない（メタ層の盲点）  `[closed]`  (bug)

## 概要

Step 11 の self_analysis（evolve 自身の歪みを振り返ってメタ層のループを閉じる仕組み）が、**今回の run で実際に起きた歪みを1つも検出できなかった**。「自分を省みる」役割の機構が自分の問題を見逃している。

## 観測（docs-platform ev-0605 run）

self_analysis の出力:
```
self_detection:          ✓ 矛盾する提案・budget 悪化提案なし
runtime_errors:          ✓ フェーズ例外・observability 取得失敗なし
improvement_opportunities: ✓ 系統的却下・calibration regression なし
total_candidates: 0
```

しかし同じ run で実際には:

1. **scipy の RuntimeWarning（NaN）が stderr に出ていた**（→ #340）のに `runtime_errors` は「例外なし ✅」と報告。**phase が throw した例外しか見ておらず、stderr 警告を見ていない**。
2. **`auto_fixable`（confidence 0.95）に既知 FP が2件入っていた**（SSM パス・/tmp パス、→ #339）のに `self_detection` は「矛盾提案なし ✅」。**「高 confidence バケットに FP が入る」パターンを検出条件に持っていない**。

## 問題点

- `runtime_errors` の検出が「phase exception（握り潰された例外）」に限定されており、**stderr に出る警告（NaN・deprecation 等）を拾わない**
- `self_detection` が「split↔archive 矛盾 / budget 悪化提案」など特定パターンに限定されており、**「FP が auto_fixable に landing する」safety パターンを見ていない**
- 結果として、フルオート運用で最も危険な「FP の自動適用」を self_analysis がガードできない

## 提案

- runtime_errors の検出に **stderr 警告のキャプチャ**を追加（`warnings` モジュールのフック or subprocess stderr 走査）
- self_detection に **「auto_fixable に confidence>=0.9 で入った項目のうち、既知 FP パターン（SSM/tmp/.archive/汎用略語）に一致するもの」**を矛盾候補として起票する検出軸を追加
- 既知 FP パターンは #337 と共通カタログ化して self_analysis と remediation の両方から参照

## 関連
- #337（remediation 誤検知カタログ）
- #298（self_analysis 起票機構の元 issue）

<!-- rl-evolve-introspect:self-analysis-blind-warnings -->


---

## #350 [P1][bug] evolve-skill: apply_evolve_proposal が既存 pitfalls.md を無条件上書き（データ損失）+ 手順に安全分岐なし  `[closed]`

## 概要（🔴 P1 / データ損失バグ）

`apply_evolve_proposal()` が既存 `references/pitfalls.md` を**無条件上書き**するため、蓄積した実エントリが消える。

### 該当
`scripts/lib/skill_evolve/proposal.py:347`
```python
pitfalls_path.parent.mkdir(parents=True, exist_ok=True)
pitfalls_path.write_text(proposal["pitfalls_template"], encoding="utf-8")  # 存在ガード無し
```
`pitfalls_template` は空テンプレなので、実エントリを持つスキルに標準 apply をかけると全消去される。

### 実害
docs-platform の `check-handbook-drift` で実3件（handbook-drift カーソル eat / 既存PRブロック / 直接invoke の実戦知見）を踏みかけた。手動 apply（SKILL.md 追記のみ）で回避した。

### 連動する設計の穴（②）
`skills/evolve-skill/SKILL.md` Step 5 の手順は「`apply_evolve_proposal` を呼べ」しか書いておらず、pitfalls 保全の分岐が無い。**手順に忠実な AI ほどデータを消す**。

### 提案
1. `proposal.py`: `if not pitfalls_path.exists():` ガード、または既存エントリを温存してテンプレ見出しのみ補完するマージ方式に変更
2. `evolve-skill/SKILL.md`: 「既存 pitfalls.md があれば SKILL.md 追記のみ・pitfalls は触らない」分岐を明記


---

## #351 [P1] prune: zero_invocation が本番オンデマンドスキルで構造的に常時誤発火（invocation_count に計測供給源なし）  `[closed]`

## 概要（🟡 P1 / レポート最大ノイズ源）

`prune` の `zero_invocation` が、オンデマンド本番スキルに対して**構造的に毎回誤発火**する。invocation_count を実起動から更新する経路が存在しないため。

### 根拠
- `scripts/lib/prune/skill_inspect.py`: `candidate.setdefault("invocation_count", 0)` のみ。**実起動を invocation_count に流し込むコードがリポ内に存在しない**（`grep -rn invocation_count` で供給源ゼロ）
- `_count_triggers()` は description の Trigger 宣言数を数えるだけで実使用と無関係

### 実害
docs-platform で `/generate-docs` `/onboard-project` 等 11本の本番スラッシュスキルが毎回「zero_invocation・要確認」に並ぶ。evolve レポートの最大ノイズ。`.pin` は対症療法にすぎない。

### 提案
- スラッシュ起動を usage log に記録 → invocation_count に反映する経路を追加、または
- それが困難なら zero_invocation 除外判定を keyword だけでなく「CLAUDE.md の Skills 表に登録済み」で行う（本番運用スキルの誤検知を抑止）


---

## #352 [P2] hardcoded_detector: 正規API URL を誤検知し裸の AWS account 番号は未検出（検出が価値と逆）  `[closed]`

## 概要（🟡 P2 / 検出精度）

`hardcoded_detector` が「正規API URL は拾うが廃止 account ID は拾わない」＝価値と逆の検出をする。

### 根拠
`scripts/lib/hardcoded_detector.py`
- `service_url` パターンが Slack 正規エンドポイント `https://slack.com/api/chat.postMessage` を検出（confidence 0.55）。`_is_safe_url` で除外しきれていない＝FP
- 一方 **bare 12桁 AWS account 番号のパターンが無い**（`arn:aws:...:\d{12}:` の ARN 内のみ）。`060361059038`（廃止アカウント）等は素のままだと検出対象外

### 提案
- `_is_safe_url` の許可リストに `*.slack.com/api/`・`*.amazonaws.com` の公式 API パスを追加
- 必要なら裸の12桁 account 番号パターンを追加（ただし誤検知に注意・low confidence）


---

## #353 [P2] evolve レポートのノイズ/UX 束（AskUserQuestion 4択矛盾・reason_refs ノイズ・memory_heavy 前提・proposable_custom 二重持ち・jargon）  `[closed]`

## 概要（🔵 P2 / レポートのノイズ削減・UX papercuts）

evolve レポート運用で実際に摩擦になった小さな問題の束。

### ⑥ AskUserQuestion 4択 ⇔ 提案詳細プロトコル「最大10件展開」の矛盾
proposable 5件を1問で聞こうとして `options <=4` でエラー → 分割を強いられた。
→ プロトコルに「5件以上は multiSelect で2問に分割」または「主要4件＋残りは誘導」を明記。

### ⑨ `reason_refs: ✘ missing` の常時ノイズ
`scripts/lib/skill_evolve/rubric.py:63` は correction_ids 起点でない evolve で常に ✘ を出す。対処不能なのに毎回赤表示。
→ correction 非由来時は項目自体を出さない。

### ⑩ memory_heavy_update の前提が逆
`update_count > 3` で「肥大」フラグ。活発に正しく更新したメモリ（コスト最適化で7回更新）を誤検知。
→ 行数・陳腐化と組み合わせ、更新回数単独では出さない。

### ⑪ proposable_custom の二重持ち
`classified.proposable_custom = null` だが `phases.remediation.proposable_custom = 5`。同名フィールドが場所で食い違い、jq 解析で混乱。
→ 片方に寄せる。

### ⑫ observability jargon 候補のノイズ
46件が AWS/技術略語（ARN, CDK, SNS...）ばかり。
→ 一般技術用語の denylist フィルタ。


---

## #354 [P3] fitness_evolution 永久 insufficient_data + judgment_complexity 手採点の再現性  `[closed]`

## 概要（🔵 P3 / 設計の限界）

### ⑦ fitness_evolution が構造的に永久 insufficient_data
「skill_evolve 提案は採点対象外」と明記されているため、skill 中心の PJ（docs-platform 等）では accept/reject 母集団がほぼ貯まらず 0/30 のまま固定。
→ 採点対象を広げる、または PJ 特性で「貯まりにくい」旨を表示して永久 0/30 の誤解を防ぐ。

### ⑧ judgment_complexity を AI が手採点（再現性が弱い）
ADR-037 で LLM-free 化したが、結局 assistant が 1-3 を主観決定する運用。同じスキルでも採点者次第で 2/3 が変わり、適性 medium/high が揺れる。
→ 静的指標（Step 数・条件分岐数・AskUserQuestion 数）で近似し再現性を上げる案を検討。


---

## #356 [tech-eval] 軌跡の自己選好報酬で fitness calibration を強化（Retrospective Harness Optimization）  `[open]`

## 概念

エージェントが**過去の実行軌跡（trajectory）を自己選好ランキング**して学習・改善する手法（arXiv 2606.05922 *Retrospective Harness Optimization*, 2026-06-05）。成功/失敗のトレースを振り返り、良い軌跡を優先する報酬信号を自前で作る。

## Before / After（ユーザー体験の変化）

- **Before**: fitness の calibration は accept/reject の **binary** シグナル（`optimize_history_store.py`）で較正されている。
- **After**: 成功軌跡 ⇄ 失敗軌跡の **pairwise 選好**から報酬を構成し、evolve の提案が「実際に accept されやすい方向」へ寄る（accept 予測精度の向上）。エンドユーザーには間接的に「採用したくなる提案が増える」体験として現れる。

## 既存実装との差分（根拠・ギャップ）

- 実装済み: accept/reject 履歴ストア `scripts/lib/optimize_history_store.py`、calibration drift 検出（audit）、`evolve-fitness`。
- 未実装: `grep -rn "retrospective\|self_preference\|trajectory.rank" scripts/` → **0 件**。軌跡同士の相対選好（pairwise preference）から報酬を作る経路は無い。現状は二値の accept/reject のみ。

## 配線先（recurring ループ）

手動止まりにしない。**evolve-fitness / `optimize_history_store` → audit の `calibration_drift` section** に配線し、`/rl-anything:evolve` のたびに自動発火させる（version ≠ enforcement）。

## 採用後の確認方法

- [ ] `/rl-anything:evolve` を回す → report の `calibration_drift` 帯に「軌跡選好で再較正した fitness の accept 予測精度」が section として出る。
- 手動で別コマンドを叩かないと出ない場合は、配線が手動止まりというシグナル。

## 再評価条件

- `evolve_introspect` が calibration regression を系統的に検出したら優先度↑。
- 逆に binary calibration で accept 予測が十分頑健なら本 issue は close 候補。

## 出典

- daily report: `ai-daily-report/reports/2026/06/ai-github-trending-2026-06-07.md`
- arXiv: https://arxiv.org/abs/2606.05922

---
🤖 tech-eval skill による自動起票

---

## ⛔ 着手ゲート（data-gated）— plan-eng-review D1 で決定

**この issue は証拠が出るまで実装しない。** 現状の `accept/reject` 二択較正（`pipeline_reflector/calibration.py` の `calibrate_confidence` / `check_calibration_regression` / `check_control_chart`）は稼働中で、二択方式が体系的にズレているという証拠はまだ無い（premise challenge / Brooks: essential vs accidental complexity）。

**ゲート条件**: `evolve_introspect` が calibration regression を **複数回（目安: 2週の evolve 運用で N≥2 回）** 検出したら着手。検出が出なければ close 候補。

> 動いている較正の上に pairwise 報酬の並行経路を speculative に足すと保守コストだけ増える（premature abstraction）。逆に証拠を待ちすぎると実際の regression を放置するので、`evolve_introspect` の検出を待つ data-gate で両リスクを抑える。

## 📐 Open design constraints（ゲートが開いたとき決定 — plan-eng-review D2）

ゲート着手時に再調査しないよう、設計上の前提を記録する。**今は決定不要。**

### 1. データモデル: pairwise のペアリング基盤が無い

pairwise preference は「同一タスクの代替候補ペア」を要するが、`optimize_history_store` の正準レコードは `{"fitness_func": ..., "best_fitness": <scalar>, "human_accepted": <bool>}` で、**決定単位のグルーピングキーが無い**（`fitness_func` 単位の単一候補 accept/reject のみ）。着手時に下記から選ぶ:

- **① `fitness_history_store` の `run_id`+`axis` を流用**（`fitness_history_store.py:85` は `run_id, ts, axis, score` を持つ＝より richな substrate。推奨候補）
- **② `optimize_history_store` にグルーピングキー追加**（既存レコード後方互換に注意）
- **③ global accept>reject preference**（最小実装だが**タスク難易度で交絡**＝易タスクの reject が難タスクの accept より高 fitness になりうる。非推奨）

### 2. DRY: 既存 calibration モジュールを拡張する（並行新設しない）

実体の較正ロジックは `optimize_history_store` ではなく `pipeline_reflector/calibration.py`。pairwise 報酬は**この既存モジュールを拡張**し、`check_calibration_regression` の regression 検出と同じ outcomes 母集団・同じ surface 経路（audit の calibration_drift 帯）に接続する。並行モジュール新設は DRY 違反。

### 確認方法（ゲート着手・実装後）

- [ ] `/rl-anything:evolve` を回す → calibration_drift 帯に pairwise 報酬で再較正した accept 予測精度が **既存 binary 較正と並んで**出る（A/B 比較可能な形）
- [ ] 単体テスト: ペアリング関数（決定論・LLM 非依存）+ calibration.py 拡張部 + audit surface の3層


> 💬 comment:
>
> ⚠️ **ゲート発火の上流ブロッカーを発見: #360**
> 
> このゲート（calibration regression 検出）は現状 **un-trippable**。実測（sys-bots ev-0607 evolve）で判明:
> - `optimize_history/`（accept/reject 母集団）が全PJで空
> - evolve は較正データを一切書かない（writer は手動 `/optimize` / `/rl-loop` だけ）
> 
> → 日次 evolve をいくら回しても証拠は出ない。本 issue を進める前に #360 でデータ源の配線を決める必要がある。#360 の対応方針 B/C 次第では、本 issue のゲート条件自体を張り替える。

---

## #357 [evolve introspect] auto_fixable に既知 FP（extensionless_logical_path）が landing: `data/bots/wheeling`  `[closed]`  (bug)

## 自己解析: auto_fixable への FP landing

confidence=0.95 で **auto_fixable**（無確認で自動適用され得る）に入った issue（type=`stale_ref`, file=`/Users/todoroki/.claude/projects/-Users-todoroki-work-sys-bots/memory/MEMORY.md`）が、既知 FP パターン `extensionless_logical_path` に一致しています（対象: `data/bots/wheeling`）。

remediation の FP_EXCLUSIONS を通り抜けて高 confidence バケットに landing しており、フルオート運用では誤った自動修正につながります。`known_fp_patterns` の照合をremediation 側の auto_fixable 判定にも組み込むか、当該 type の confidence を見直してください。

<!-- rl-evolve-introspect:self:fp_in_auto_fixable:extensionless_logical_path:data/bots/wheeling -->

---

## #358 [evolve] テレメトリ発火0で Prune が全スキルを淘汰候補に誤検出（telemetry 未記録）  `[closed]`  (bug)

## 症状

evolve の Prune フェーズが、テレメトリのスキル発火カウントが 0 のスキルを無条件で淘汰候補（`zero_invocations` / 要確認）に挙げる。しかし実環境ではスキル発火がテレメトリに全く記録されておらず、結果として**プロジェクトの全 custom スキルが毎回 archive 候補になる**。

## 証拠（sys-bots, 2026-06-07 evolve）

`zero_invocations` に custom 17 スキル全件が landing：

| skill | git commits | trigger_count |
|-------|-------------|---------------|
| aws-deploy | 56（中核・最頻メンテ） | 0 |
| rag-ingest | 18 | 0 |
| bot-create | 9 | 0 |
| evaluate-personality | 3 | 0 |
| … 残り13件も全て | — | 0 |

aws-deploy は CLAUDE.md 記載のプロジェクト中核スキルで 56 commits も入っているのに trigger_count=0。これは「使われていない」のではなく「Skill ツール発火がテレメトリに記録されていない」ことを示す。

## 影響

- Prune フェーズが丸ごとノイズ化。日次 evolve のたびに同じ全件を「個別精査」させられる
- 本来検出すべき「本当に使われていない一時スキル」が全件ノイズに埋もれる（signal/noise 崩壊）

## 提案

- `zero_invocation` 判定を、telemetry の総発火数が 0（=計測自体が機能していない）の場合に無効化する、もしくは
- git 活動（直近 N 日の commit / 最終変更日）を併用し、活発にメンテされているスキルを淘汰候補から除外する、もしくは
- Skill ツール発火がテレメトリに記録されない根本原因を調査する（こちらが本筋の可能性）

検出: sys-bots ev-0607 セッション

rl-evolve-introspect:prune_zero_invocation_telemetry_dead


---

## #359 [evolve] hardcoded_value がドキュメント本文の URL/ARN を過剰検出（doc 文脈未除外）  `[closed]`  (bug)

## 症状

evolve の `hardcoded_value` 検出が、SKILL.md など**ドキュメント本文/例示コマンド中の URL・ARN・SQS URL** を「抽出すべきハードコード設定値」として proposable に挙げる。散文や手順例の中の URL は設定値ではないため、ほぼ全件が false positive。

## 証拠（sys-bots, 2026-06-07 evolve / proposable 9件）

```
hardcoded_value | SKILL.md:93  | https://api.slack.com/apps           (pattern_type=service_url)
hardcoded_value | SKILL.md:270 | https://api.slack.com/apps?new_app=1 (service_url)
hardcoded_value | SKILL.md:292 | https://slack.com/api/auth.test      (service_url)
hardcoded_value | SKILL.md:293 | https://slack.com/api/bots.info?...  (service_url)
hardcoded_value | SKILL.md:177 | https://slack.com/oauth/v2/authorize (service_url)
hardcoded_value | SKILL.md:53  | https://sqs.../slack-events-dev.fifo (service_url, 例示curlコマンド)
hardcoded_value | SKILL.md:54  | arn:aws:secretsmanager:...           (aws_arn, 例示)
...
```

`https://api.slack.com/apps`（手順「1. https://api.slack.com/apps にアクセス」）を「ハードコード値」と判定するのは構造的に誤り。これらは設定ファイルではなくドキュメントの手順説明・例示コマンド。

## 影響

- proposable が毎回 doc URL の FP で埋まり、本来の設定値ハードコードが埋もれる
- numeric_id 側は FP_EXCLUSIONS で 10件正しく除外できているのに、service_url/aws_arn は通り抜ける（除外ルールの非対称）

## 提案

- `.md` ファイル（特に SKILL.md / rules / docs）では `service_url` 系の検出を抑制、もしくは
- 散文・手順番号・例示コードブロック内の URL を文脈で除外、もしくは
- 公式ドキュメントエンドポイント（api.slack.com, slack.com/api/* 等の汎用 API URL）を allowlist 化

検出: sys-bots ev-0607 セッション

rl-evolve-introspect:hardcoded_value_doc_url_overfire


---

## #360 [investigate] calibration 較正データ(optimize_history)が空 — accept/reject 収集が recurring loop に乗っておらず #356 ゲートが un-trippable  `[closed]`

## 症状

sys-bots の ev-0607 セッションで `/rl-anything:evolve` を回しても、#356 のゲート条件である **calibration regression の証拠がゼロ**。調査したところ:

- `optimize_history/`（accept/reject 母集団）ディレクトリが **全PJで存在しない**（`~/.claude/rl-anything`・`~/.gstack` 両方で空）
- 6/7 更新ファイル（`evolution_memory` / `corrections` / `usage` / `evolve-state` / `audit-history`）のどれにも calibration_regression / calibration_drift / self_analysis シグナルなし
- `evolve-state.json` の calibration 系シグナル 0 件

## Root cause（実測・file:line 付き）

**recurring loop（evolve/audit）が accept/reject 母集団を一切生まない。** 較正データの writer は手動最適化スキルだけに配線されている:

| 経路 | 場所 | 駆動条件 |
|---|---|---|
| writer① | `skills/genetic-prompt-optimizer/scripts/optimize.py:535` `record_human_decision(human_accepted=args.accept)` | `/rl-anything:optimize` を `--accept`/`--reject` で手動実行したときだけ |
| writer② | `skills/rl-loop-orchestrator/scripts/run_loop.py:749` `_history_store.append_entry(loop_result, ...)` | `/rl-anything:rl-loop-orchestrator` を手動実行したときだけ |
| reader | `skills/evolve-fitness/scripts/fitness_evolution.py` / `skills/audit/scripts/aggregate_runs.py:71` / `scripts/lib/discover/errors.py:98` | calibration / discover が読む |

確認: `skills/evolve/**` は `optimize_history` / `append_entry` / `human_accepted` を **一切参照しない**（grep 0 件）。`scripts/lib/optimize_history_store.append_entry` は本番では writer①② からしか呼ばれず、両方とも手動スキル。

→ 日次で回る evolve は較正データを生まないので、`check_calibration_regression`（`pipeline_reflector/calibration.py:149`）は永久に入力ゼロ。**#356 のゲート（calibration regression 検出）は現状 un-trippable**。

## Impact

- #356 を保留にして「証拠が出たら着手」としたが、**証拠が出る経路が日常運用に無い**。待っても永久に発火しない。
- calibration_drift 系の観測全般（audit の calibration_drift 帯、`evolve_introspect` の calibration regression カテゴリ）が input ゼロで空回りしている可能性。
- これは `install ≠ enforcement` / `version ≠ enforcement`（既存 learning）と同型 — **データ源が recurring loop でなく手動 CLI に配線されている**問題。

## 対応方針（後で決定 — 今は調査 issue）

- **A**: evolve/optimize が出す提案の accept/reject を **recurring loop 内でキャプチャ**して optimize_history に書く配線を足す（データ源を日次ループに乗せる）
- **B**: ゲート条件（#356）を、recurring loop が実際に生むデータ源に張り替える（例: `corrections.jsonl` の reject パターン、telemetry の暗黙成功率 = invoke 直後 60s に correction 無し）
- **C**: 「手動 optimize でのみ蓄積」が意図通りの仕様なら、#356 のゲートを「`/optimize` を N 回 accept/reject したら着手」に書き換え、calibration_drift 観測が手動依存であることを明記

私見: B か C。pairwise 報酬（#356）以前に、**calibration を測るデータが日次ループに流れていない**ことが本質。A は配線コストが高く、そもそも evolve 提案に人間 accept/reject の UX が無い。

## 確認方法

- [ ] `bin/rl-fleet` 等で全PJの `optimize_history/<slug>.jsonl` 行数を表示 → 全ゼロを確認
- [ ] `/rl-anything:evolve` を回す前後で `optimize_history` が増えないことを確認（writer 非配線の実証）
- [ ] `/rl-anything:optimize --accept` を1回実行 → optimize_history が増えることを確認（writer は生きているが手動限定の実証）

## 関連

- #356（軌跡の自己選好報酬 / pairwise calibration）— このゲートが発火しない上流原因
- learning: install ≠ enforcement / version ≠ enforcement

---
🤖 plan-eng-review → implement ゲート調査（B 選択）から派生


---

## #364 [Phase 2] DATA_DIR の hook/tool 分裂を一元化する（計画的 migration）  `[closed]`

## 背景
#358（PR #363, [ADR-042](docs/decisions/042-hook-store-dir-resolver-not-datadir-unification.md)）で、`rl_common.DATA_DIR` が実行コンテキストで分裂している根因を確認した:
- **hook 実行時**（env `CLAUDE_PLUGIN_DATA` 有）→ plugin-data dir `~/.claude/plugins/data/rl-anything-rl-anything/`
- **tool/skill 実行時**（env 無）→ fallback `~/.claude/rl-anything/`

ストアごとに正準 dir が割れている:
| 正準 | ストア |
|---|---|
| plugin-data（hook writer） | usage / skill_activations / sessions / subagents / tool_durations / workflows |
| fallback（tool/skill writer） | corrections / evolve-state / audit-history / eval-sets / episodic.db / evolution_memory |
| 両 split | errors.jsonl / sessions.db（10GB + 2.2GB） |

#358 は症状（usage/skill_activations の reader）だけを最小修正した。**本 issue は根本の一元化（Phase 2）。**

## なぜ #358 で一元化しなかったか
- DATA_DIR 一斉スイッチ → tool 系ストア（corrections/evolve-state 260K live/eval-sets/episodic）が一瞬空に見え evolve/audit が壊れる
- 両 dir に 10GB + 2.2GB の DuckDB が live で割れており実マージは遅い・壊れやすい
- plugin-data dir は reinstall で wipe される（正準先として脆い）

## やること（設計要件）
1. **正準 dir を決める**: reinstall 耐性のため **fallback `~/.claude/rl-anything/` を正準**にし、plugin-data → fallback の **逆 migration**（fallback は再 clone でも残るが plugin-data は uninstall で wipe される）
2. hook 側も fallback に書くよう統一（env 未設定時に自動 fallback になるため整合）
3. **冪等 migration script**: dry-run mode + `.datadir-migrated` marker + 複数回実行 safe。jsonl は concat+dedup、DB は move-if-absent、両 present の errors.jsonl は line dedup マージ
4. **10GB+2.2GB DuckDB sessions.db のマージ戦略**を別途検証（実機 E2E ベンチ必須、`transcript-store-bench` ルール準拠）
5. ADR-042 を Superseded にし新 ADR を起こす
6. #358 の `hook_store_path` resolver は一元化完了後に撤去（reader が単一 DATA_DIR を読めば足りる）

## 関連
- #358 / PR #363 / ADR-042
- #360（optimize_history 空）も同根の可能性。一元化で再評価する

## 着手条件
ユーザーが「両 dir 管理が煩雑」と判断したとき、または #360 等が同根で再発したとき。

---

## #369 discover.workflow_checkpoint_gaps が条件付きでキーごと消え Step 10.4 から評価有無が不明  `[closed]`

## 概要
evolve リファクタの実機 dry-run 検証中に発見した軽微な observability ズレ。

evolve SKILL.md **Step 10.4** は `discover.workflow_checkpoint_gaps` をテーブル表示し「なければ『ギャップなし』」とする。だが `discover/runner.py:380-399` の try ブロックは **workflow skill 該当なし等の条件でブロックごとスキップ**し、その場合 `workflow_checkpoint_gaps` も `workflow_checkpoint_gaps_error` も**キー自体が出力に現れない**。

## 問題
- Step 10.4 はキー欠落時「ギャップなし」に落ちるため**実害はない**
- しかし「評価した結果ギャップ無し」なのか「そもそも評価していない」のか区別できない（silence ≠ evaluated）
- 対照的に `stall_recovery_patterns`（Step 10.5）は常に出力される

## 期待
`discover/runner.py` で workflow_checkpoint 評価をスキップした場合も、空リスト `[]` か skipped マーカーを常に `workflow_checkpoint_gaps` に入れて、Step 10.4 が「評価したが該当なし ✓」を明示できるようにする。

## 補足
発見元: evolve SKILL.md progressive disclosure リファクタ後の実機検証（rl-anything 自身）。同時発見の Step 6 dead reference / glossary 汎用語 / agent_team 過剰警告は別 PR で修正済み。

決定論・LLM 非依存の小修正。

---

## #375 bug(evolve): references のキー名が result JSON の実構造と乖離（proposable は件数、split は skill_name/line_count）  `[closed]`

## 種別: bug

## 概要
evolve スキルの SKILL.md / references に記載された result JSON のキー名が、実装（evolve.py / 各フェーズ）の実際の構造と食い違っており、ドキュメント通りに jq で掘ると空が返る。

## 実際に遭遇したズレ（sys-bots evolve session 2026-06-08）
| ドキュメント記載 | 実際の構造 |
|---|---|
| `remediation.proposable[].target` / `.skill` を配列として参照 | `proposable` は **数値（件数）**。中身は `classified.proposable[]` |
| `reorganize.split_candidates[].skill` / `.content_lines` | 実際は `.skill_name` / `.line_count` |
| `proposable[].type` で iterate | `proposable` が number なので `Cannot iterate over number` エラー |

## 影響
- reference 通りに書いた jq が動かず、構造を当てるのに 4-5 回の試行錯誤が必要だった
- Step 5.5 / Step 4 の「出力に含まれる X を確認する」が額面通りに follow できない

## 提案
- references のキー名を実装に合わせて修正（特に remediation の `proposable`(件数) vs `classified.proposable`(実体) の区別を明記）
- もしくは result JSON のスキーマを1箇所に固定し、SKILL.md からはそれを参照する形にして乖離を防ぐ

---
Source: sys-bots evolve 実行セッション 2026-06-08 の手動フィードバック

---

## #376 bug(evolve): skill_evolve が usage_count=0 のスキルを medium（変換可能）と判定する（使用実績の重みが効いていない）  `[closed]`

## 種別: bug / 設計の弱点

## 概要
skill_evolve_assessment が、telemetry の使用回数ゼロ（`usage_count: 0` / `error_count: 0`）のスキルを軒並み `medium`（変換可能 — ユーザー判断）と判定する。自己進化（pitfalls.md 蓄積）は「実際のミスが溜まったスキル」に効く仕組みなのに、一度も使われていないスキルに自己進化を勧めるのは本末転倒。

## 実例（sys-bots evolve session 2026-06-08）
14スキル中11スキルが medium と判定。全スキルが下記の状態:
```
scores: {frequency:1, diversity:1, evaluability:1, external_dependency:2-3, judgment_complexity:2-3, error_count:0}
telemetry_detail: {usage_count:0, error_count:0}
total_score: 6-9 → medium
```
medium スコアの大半が `judgment_complexity` + `external_dependency` 由来で、**使用実績（frequency / usage_count）の重みがほぼ効いていない**。

## 影響
- 「11スキル変換可能」と出るが、実態は「全部使われていないので進化させる意味がない」
- ユーザーが空の pitfalls ひな型を量産する方向に誘導されかねない（セレモニー化）

## 提案
- `usage_count == 0`（または frequency が閾値未満）のスキルは medium に昇格させず low/保留にする
- もしくは suitability に「使用実績待ち（insufficient_usage）」の区分を追加し、エラーが出始めてから候補化する

---
Source: sys-bots evolve 実行セッション 2026-06-08 の手動フィードバック

---

## #377 enhancement(evolve): UX/構造の改善5点（token見積もり過大・hardcoded誤検知・per-item承認MUST・fitness母集団・自己解析の盲点）  `[closed]`

## 種別: enhancement（UX / 構造）

sys-bots evolve session 2026-06-08 で気づいた、バグではないが体験を損なう/構造的な弱点を5点まとめる。個別に切り出すべきものがあれば分割可。

---

### 1. batch_guard の token 見積もりが過大で誤解を生む
- skill_evolve batch_guard が「~11.6k tokens（コスト大）」と警告するが、ADR-037 で `compute_llm_scores` は cache-read 化されており、cache hit 時の実 LLM コールは **0**。`--confirmed-batch` 再実行も一瞬だった。
- 見積もりが「最悪ケース」のまま提示されるため「重い処理」と誤った印象を与える。
- 提案: cache hit 見込み時の実コスト（≈0）を併記、または cache 状態を見て見積もりを動的に下げる。

### 2. remediation `hardcoded_value` がドキュメント文字列を高 confidence で誤検知
- SKILL.md の**説明文中**の実 Bot ID（`B0AJRU27Z2Q`）や dev の Secret ARN を「ハードコード違反」として conf 0.65-0.75 で提案してきた。
- コード中のハードコードと、ドキュメントに意図的に書かれた実リソース記載を区別していない。
- evolve-ops 的に「FP多数」は既知だが、confidence が高いため紛らわしい。
- 提案: `.md` のテーブル/引用/コードフェンス内の ID/ARN は confidence を下げる、または除外する。

### 3. Step 5.5 の per-item 承認 MUST が低価値 FP 群で「質問攻め」になる
- proposable 11件が大半 FP/低価値（conf 0.5中心）なのに「1件ずつ個別承認 MUST」。素直に従うと AskUserQuestion を連発することになる。
- 提案: confidence や FP 推定でしきい値を切り、低 confidence 群は「まとめてスキップ（個別展開は任意）」をデフォルトにする。

### 4. fitness_evolution が構造的にデータが貯まらない自覚はあるが解決経路がない
- `status: insufficient_data` / `structural_reason: skill_evolve_not_scored` で「母集団が貯まりにくい」とスキル自身が説明してくるが、ユーザーが何をすれば貯まるのかの実効的な導線が弱い（関連: #356）。
- 提案: skill diff の accept/reject を貯めるための具体アクション（どのコマンド/フロー）をレポートで明示。

### 5. Step 11 自己解析が UX/設計の歪みを拾えない（盲点）
- self_analysis は「矛盾提案・実行時エラー・系統的却下」の決定論検出のみで「0件 ✓」。一方で本 issue の #1（キー名乖離）#2（usage0 を medium）のような設計の歪みは1つも検出されない（関連: #298）。
- 提案: 自己解析に「ドキュメント記載キーと実 result 構造の乖離」「usage 実績と suitability の矛盾」など軽量な整合性チェックを足す。

---
Source: sys-bots evolve 実行セッション 2026-06-08 の手動フィードバック

> 💬 comment:
>
> ### 項目4（fitness_evolution の母集団／導線）→ 対応済み（PR #384）
> 
> **結論: 母集団が貯まらない本体は ADR-041 / evolve_decisions (#360-A) で構造的に解決済み。残っていた古い案内文を実装に追従させた。**
> 
> - `/rl-anything:evolve` を回すたびに discover の matched_skills(skill diff) + skill_evolve(high/medium) の accept/reject が `optimize_history` へ自動記録され、fitness_evolution の母集団になる（`evolve_decisions.py:111-137`）。＝特別な操作は不要で、evolve を回し続けること自体が母集団を貯める。
> - insufficient_data の案内文が #360-A 以前の手動導線（rl-optimize で accept しろ）のままだったため、「evolve を回せば自動で貯まる」を明示。
> - レビューで、追記文と既存文「skill_evolve…採点対象外」の矛盾（doc↔impl drift）を検出。真因は #354⑦ 当時の stale 記述で、採点対象外なのは remediation の fix（rules/hook・構造）。SKILL.md ×2 + `fitness_evolution.py` message(SoT) + テスト docstring を一括是正。
> 
> この項目はクローズ。残りは #377-1（token見積もり過大）・#377-3（per-item承認MUST）。#377-2（hardcoded誤検知）は PR #382、#377-5（自己解析の盲点）は PR #380 で対応済み。

> 💬 comment:
>
> **#377-1 (token見積もり過大) 解決済み — PR #385 マージ**
> 
> batch_guard の `estimated_tokens` は worst-case（全スキル Phase B 想定）だったため「コスト大」と誤読される問題を是正:
> 
> - `is_fresh_llm_judgment` を **SoT 述語**として `llm_scoring.py` に抽出し、`emit_judgment_requests(refresh=False)` の実 skip 条件と batch_guard 見積もりが同一定義を共有（drift 防止）
> - batch_guard group に `estimated_tokens_cache_aware` / `cache_fresh_count` / `refresh_needed_count` を追加（worst-case `estimated_tokens` は後方互換で残置）
> - SKILL.md + references で worst-case と cache 反映後の実見込みを併記、`--confirmed-batch` 再実行自体が LLM-free（ADR-037）である点を明示
> - TDD 7 ケース新規 / 全体 2193 passed
> 
> ---
> 
> ### #377 進捗
> - [x] #377-1 token見積もり過大 (#385)
> - [x] #377-2 hardcoded 誤検知 (#382)
> - [ ] #377-3 per-item 承認 MUST が質問攻め ← **残**
> - [x] #377-4 fitness 母集団／導線 (#384)
> - [x] #377-5 自己解析の盲点 (#380)

---

## #379 P2 hardening: evolve result-schema 契約の堅牢化（逆方向テスト・references doc-drift・記法カバレッジ・飽和・機械可読化）  `[closed]`

P1 PR #378（#375 result-schema 契約 + #376 usage==0 ガード）の `/review` で 4 subagent（testing / maintainability / Claude adversarial / red-team）が検出した、P1 スコープ外の hardening 項目を集約。P2(#377-5 self-detect) 着手前に取りこぼさないための追跡 issue。

## 背景
#375 で `scripts/lib/evolve_result_schema.py` に result の正準スキーマ契約（`CANONICAL` + `check_conformance` + `extract_documented_paths`）を導入した。これは現状 **test-time ゲートのみ**で、runtime self-detect(#377-5) は P2 で本モジュールを consume する設計。その前提で契約自体の堅牢性を上げる項目が以下。

## P2 で対応する項目

### 1. 逆方向の契約テスト（契約の二次 drift 防止）★優先
`check_conformance` は `CANONICAL ⊆ result` 方向のみ検査。`evolve.py` は ~18 phase を result に書くが CANONICAL は 7 phase しかカバーしない。新 phase/キー追加時に CANONICAL 更新を強制する**失敗テストが無い**ため、契約自体が静かに陳腐化し P2 はカバー外 phase を「契約なし」と誤認する。これは #375 が解こうとした drift の構造的再発。
- 対応: 実 dry-run result の phase キー集合が CANONICAL に登録済みかを assert する逆方向テストを追加（未登録 phase を fail させ更新を強制）。最低限「契約は意図的に部分カバー」を docstring に明記。

### 2. doc-drift 検査を `references/` に拡張
`extract_documented_paths` の doc 側検査は `skills/evolve/SKILL.md` のみ走査。module docstring 自身が「references が result のキー名を手書きしていた」をドリフト起源として挙げているのに、`skills/evolve/references/*.md`（実際に dotted path を含む）は未カバー＝false negative。
- 対応: 走査対象に `skills/evolve/references/**/*.md` を含める（CLAUDE.md は jargon が多く FP リスクのため要判断）。

### 3. `extract_documented_paths` の記法カバレッジ
正規表現 `(?:result\.)?(phases\.[A-Za-z_]+(?:\.[A-Za-z_]+)+)` の穴:
- bracket 記法 `result["phases"]["skill_evolve"][...]` を取りこぼす（false negative）
- dict 型 canonical キーの sub-field（例 `phases.skill_evolve.batch_guard_trigger.reason`）を documented すると 4-segment path が canonical_paths に無く **false positive で build 破壊**
- 対応: exact membership でなく longest canonical prefix マッチに変更。bracket 記法も任意で対応。

### 4. 契約飽和（緩すぎる optional+nullable）
`batch_guard_trigger` を optional=True かつ nullable=True で登録（dict|None|欠落 すべて許容）し kind=dict が実質何も拘束しない。reorganize 系の一律 optional 化で skipped 経路の型ミスも検出不能。eval_saturation の low_negative_coverage 相当。
- 対応: phase active かつ非 None なら item_keys/型を要求する条件付き検査を検討。

### 5. `check_conformance` 返り値の機械可読化（P2 consume 用）
返り値が `List[str]`（人間可読メッセージ）で機械パース不可。P2 self-detect が violation を構造的に消費しにくい。
- 対応: P2 着手前に `(path, reason)` 構造で返す版を検討。CANONICAL / check_conformance を P2 の安定 API として design doc か docstring に明記。

## 関連
- P1: #378（closes #375 #376）
- 親: #377-5（self-detect 本体）
- dry-run dogfood の限界（本 repo では reorganize skipped のため split item_keys を未 exercise）も #377-5 のテスト設計で考慮する。


> 💬 comment:
>
> 確定3項目はすべて main に実装済みのため close します（独立検証済み）。
> 
> - 項目1（逆方向契約テスト）: `test_real_phases_are_all_registered`（#379-1）— 実 dry-run の phase 集合が CANONICAL∪UNCOVERED に収まるか assert、未登録 phase で fail。
> - 項目2（references doc-drift 拡張）: `test_references_documented_paths_are_known`（#379-2）— `skills/evolve/references/**/*.md` を rglob して dotted path を canonical 突合。
> - 項目3（記法カバレッジ）: `extract_documented_paths` の bracket 記法対応 + `compute_doc_path_drift` の longest-prefix マッチ（#379-3）。
> 
> 実装は #506（closes #493）系の作業に同梱。`scripts/tests/test_evolve_result_schema.py` 29 tests green。項目5（構造化返り値 .path/.reason）も `ConformanceViolation` で実装済み。項目4（契約飽和の条件付き検査）は split item_keys 強制（`test_split_candidate_item_keys_enforced`）で実用上カバー。

---

## #381 [tech-eval] skill_extractor に Workflow-to-Skill の4軸構造分解 (routing/workflow/semantics/attachments) を追加  `[closed]`

## 概要

軌跡からスキル候補を抽出する `skill_extractor` に、Workflow-to-Skill (arXiv [2606.06893](https://arxiv.org/abs/2606.06893)) が提案する **4軸構造分解** を追加する。論文はワークフローを `routing`（どこで使うか）/ `workflow`（手順）/ `semantics`（何をするか）/ `attachments`（必要リソース）の4要素に分解して再利用スキルを生成する。

## Before / After（ユーザー体験）

- **Before**: discover/triage が出すスキル候補は `generalizability_score`（スコア）しか持たず、「どこで発火させるか」「何のリソースが要るか」を採用時に人が後から調べる必要がある。
- **After**: 候補テーブルに `routing`（配線先）と `attachments`（必要リソース）の列が付き、`evolve`/`discover` を回すだけで候補ごとに発火先と前提が surface され、採用判断が速くなる。

## 既存実装との差分（根拠・ギャップ）

- ✅ コアの「軌跡→スキル抽出」は実装済み:
  - `scripts/lib/skill_extractor/trajectory_sampler.py` の `sample_trajectories` がセッション履歴の `<command-name>` ターンを採掘（#238 Phase1 → #291 配線）
  - `scripts/lib/skill_extractor/skill_extractor.py` の `extract_skill_candidates` が候補生成
- 🔶 ギャップ: `skill_extractor.py:76` は `skill_name` でグルーピングして `generalizability_score` を付けるだけで、**4軸の構造分解を持たない**。論文の routing/attachments に相当する構造がない。
- これは本PJの弱点（「候補は出るが配線先が人手判断」）と直接対応する。

## 配線先（recurring ループ）

- `run_discover` → `skill_extractor`（`evolve` が毎回消費する recurring ループ）上にある。
- 実装方針: `extract_skill_candidates` の返り値に `routing` / `attachments` フィールドを追加し、triage の `missed_skill_opportunities` 形式へ変換する際に保持 → 候補テーブルに surface。
- 手動 CLI 止まりにしない（`evolve` を回せば自動で効く）。

## 採用後の確認方法

- [ ] `/rl-anything:evolve`（または `discover`）を回す → スキル候補テーブルに `routing`（配線先）と `attachments`（必要リソース）の列が現れ、`generalizability_score` だけでなく「どこで発火するか」が候補ごとに表示される。

## 再評価条件

- 候補採用率が低いまま／配線先不明で却下が続くなら優先度を上げる。

---

出典: tech-eval (ai-github-trending-2026-06-09.md) / 推奨度: 中

---

## #387 skill_extractor: routing.trigger_keywords の stopword 拡充（実 PJ でノイズ語 if/not/md/claude が露見、#381 follow-up）  `[closed]`

## 背景

#381 (PR #383) で `skill_extractor` に Workflow-to-Skill の4軸構造分解（`decomposition.py`）を入れた。マージ後、実 PJ（rl-anything、169 transcript files → `max_files=50` サンプリング）で本流経路（`run_discover` → `extract_skill_candidates`）を E2E 実走したところ、4軸自体は正しく動作したが、`routing.trigger_keywords` に**ノイズ語**が混じることが判明した。

合成 fixture（TDD 14件）では露見せず、実コーパスで初めて見えた（`learning_synthetic_fixture_false_confidence` の再現）。

## 症状（実測）

実 PJ の上位候補の `trigger_keywords`:

| skill | trigger_keywords |
|---|---|
| `review` | `["if", "md", "gstack", "test", "review"]` |
| `rl-anything:spec-keeper` | `["if", "gstack", "claude", "not", "review"]` |
| `rl-anything:implement` | `["review", "gstack", "if", "plan", "not"]` |

`"if"` / `"not"` / `"md"` / `"claude"` は発火文脈を表さないノイズ。user_prompt に頻出するため上位に来てしまう。

## 根本原因

`scripts/lib/skill_extractor/decomposition.py` の `_STOPWORDS` が `agent_team._STOPWORDS` 由来の最小セットで、以下が未収録:

- 英語機能語の一部: `if`, `not`, `no`, `so`, `then`, `else`, `when`, `how`, `what`, `but`, `from`, `at`, `we`, `i`, `my` 等
- 拡張子/技術トークン: `md`, `py`, `js`, `txt` 等（ファイル名由来）
- 環境固有の頻出語: `claude`, `gstack`（どのプロンプトにも出るため弁別しない）

`#381` のスコープ（4軸を決定論で導く配線）は達成済み。これは `routing` 軸の**実用品質**の follow-up。

## 提案

1. `_STOPWORDS` に英語機能語の標準セットを拡充（NLTK 風の最小 function-word リストを inline 定数で）
2. よくある拡張子（`md`/`py`/`js`/`ts`/`txt`/`json`/`yaml` 等）を除外
3. 環境固有の頻出語（`claude`/`gstack` 等のツール名）は、全候補に共通して出る語を `document-frequency` で間引く案も検討（ただし決定論・LLM 非依存は維持）
4. 実 PJ コーパスでドッグフードして緑を確認（合成 fixture だけで判断しない）

## 受け入れ条件

- [ ] 上記の実 PJ 上位候補で `trigger_keywords` から `if`/`not`/`md`/`claude`/`gstack` が消え、発火文脈を表す語（`review`/`plan`/`spec` 等）が残る
- [ ] 既存14テスト緑 + stopword 拡充の回帰テスト追加
- [ ] 実 PJ E2E で再確認（wall time 計測つき）


---

## #393 [Feedback] evolve observability の誤検知2件: cross_skill category 未展開 / unmanaged_pitfalls が worktree を拾う  `[closed]`  (bug,feedback)

## 概要

`docs-platform` での evolve 実行（2026-06-09・dry-run 観測）で、observability に**誤検知が2件**出ました。性質が近い（検出ノイズ）のでまとめて報告します。

---

### 1. `cross_skill_analysis` のカテゴリ名が `[category]` のまま（テンプレ未展開）

`phases.pitfall_hygiene.cross_skill_analysis` の出力:

```json
{"[category]": ["docs-qa", "manage-webhook", "manage-handbook", "manage-repo"]}
```

キーが文字どおり `[category]` になっており、テンプレ placeholder が展開されていない疑いがあります。

- 「4スキルに根本原因が横断集中している」という**有用なシグナルが出ているのに、肝心の "何のカテゴリか" が分からない**ため、共通ルール化の判断ができない。
- pitfalls.md 側の category フィールドが空のまま集計されている可能性。空 category を `[category]` という文字列で埋めて出しているか、フォーマット文字列が未適用。

**期待**: 実際のカテゴリ名を表示する。category が空のエントリは集計から除外するか「未分類」と明示する。

---

### 2. `unmanaged_pitfalls` が worktree 内のコピーを拾う

`observability.unmanaged_pitfalls` の出力:

```
- .claude/worktrees/issue-for/.claude/skills/check-handbook-drift/references/pitfalls.md (3 entries)
- .claude/worktrees/issue-for/.claude/skills/manage-repo/references/pitfalls.md (3 entries)
```

本体スキル（`.claude/skills/check-handbook-drift/...` 等）は既に自動強制ルールに登録済みなのに、`.claude/worktrees/` 配下の**一時 worktree コピー**を「未登録」として報告している。

**期待**: pitfalls.md 探索時に `.claude/worktrees/` 配下（および `.git/`, `node_modules/` 等）を除外する。worktree は一時的な作業コピーなので恒久管理対象ではない。

---

## 環境

- 実行: `/rl-anything:evolve`（docs-platform, 2026-06-09）
- env_tier: medium


---

## #394 [Feedback] evolve の判断材料不足2件: hook_drift に検出元パス無し / estimated_tokens_cache_aware が worst-case 同値  `[closed]`  (enhancement,feedback)

## 概要

evolve がユーザー/assistant に「判断材料（evidence・根拠）」を出す箇所で、**根拠不足・表示と実挙動の乖離**が2件ありました。どちらも「提示された数字/主張だけでは正しく判断できず二度手間になる」点で共通するのでまとめて報告します。

---

### 1. `hook_drift` に検出元パス（evidence）が無く、検証で誤判断しかけた

`observability.hook_drift` の出力:

```
⚠ gstack flow 追従漏れ: flow-chain.json は gstack 1.55.0.0 想定だが実環境は 1.57.0.0
```

「実環境は 1.57.0.0」とだけ出るが**根拠（どこから 1.57.0.0 を読んだか）が無い**。

検証しようと `gstack --version` を叩いたところ、PATH に gstack バイナリが無く `||` フォールバックで **flow-chain.json 自身を読み戻して 1.55.0.0** が返り、危うく「evolve の誤報（flow-chain が正しい）」と逆の結論を出しかけた。実際は evolve が正しく、真のソースは `~/.claude/skills/gstack/package.json` (= 1.57.0.0) だった。

**期待**: hook_drift の evidence に検出元パス（例: `~/.claude/skills/gstack/package.json`）を併記する。これがあれば assistant/ユーザーが独自検証で迷わず確認できる。

---

### 2. `estimated_tokens_cache_aware` が worst-case と同値で「機能していない」

`skill_evolve.batch_guard_trigger.groups[0]`:

```json
{"estimated_tokens": 9107, "estimated_tokens_cache_aware": 9107,
 "cache_fresh_count": 0, "refresh_needed_count": 11}
```

SKILL は「worst-case と cache-aware を併記し、cache 反映後 ≈0 と判断せよ」と指示しているが、**cache fresh 0 件だと cache_aware は worst-case と同値**になり、「≈0」の根拠としては使えない。

実際に「≈0」なのは *`--confirmed-batch` 再実行が LLM-free（ADR-037 で評価ループが cache-read + 静的フォールバック）だから* であって、`estimated_tokens_cache_aware` フィールドからは読み取れない。**フィールドの意味（cache 反映後の見込み）と実挙動（LLM-free なので課金ゼロ）が乖離**しており、フィールドだけ見ると「9.1k tokens かかる」と誤読する。

**期待**: confirmed-batch 再実行が LLM-free な場合は `estimated_tokens_cache_aware` を 0（または「再実行 LLM-free」フラグ）にするか、フィールドの意味を「Phase B judgment refresh を回した場合のみの繰り延べコスト」と明示する。

---

## 環境

- 実行: `/rl-anything:evolve`（docs-platform, 2026-06-09）


---

## #395 [Feedback] evolve の doc/出力構造の乖離2件: evolve.py vs rl-evolve / skill_evolve 出力の二重構造  `[closed]`  (documentation,feedback)

## 概要

evolve の手順書（SKILL.md）と実環境・出力構造の間に乖離があり、assistant が空振りする箇所が2件ありました。DX/可読性の問題としてまとめて報告します。

関連: #379（result-schema 契約の機械可読化）。本 Issue の項目2はその一部として扱える可能性あり。

---

### 1. SKILL の `evolve.py` 直叩き前提が実環境（`rl-evolve` ラッパー）と乖離

`references/skill-evolve-assessment.md` Step 4 の手順:

```
python3 evolve.py --confirmed-batch [--skip-skills=...] --output /tmp/rl_evolve_out.json [既存の引数]
```

`evolve.py` の実パスがドキュメントから分からず、glob 探索（`~/.claude/plugins/*/rl-anything/scripts/evolve.py` 等）が空振りした。最終的に `rl-evolve --confirmed-batch ...` ラッパーで動作した。

**期待**: SKILL の再実行手順を `rl-evolve --confirmed-batch ...`（インストール時に PATH に入るラッパー）に統一するか、両方を明記する。「evolve.py を直接探せ」という前提を外す。

---

### 2. `skill_evolve` の出力が二重構造でアクセスしづらい

`phases.skill_evolve` に以下が併存:

- `assessments[]` — 各スキルの本体（`.skill_name` + `.suitability` + `.scores` + ...）
- `high_suitability` / `medium_suitability` / `already_evolved` / `insufficient_usage` / `rejected` — 別キーの集計配列

最初 `.skill_evolve.high_suitability[].skill` で抽出したら全部空で「評価が走っていない」と誤読しかけた。実体は `assessments[]` 側に `.skill_name`/`.suitability` で入っており、フィールド名も `skill` ではなく `skill_name`。

**期待**: 集計配列（high_suitability 等）と詳細配列（assessments）のどちらが正準かを SKILL/schema で明示する。あるいは集計配列に最低限 `skill_name` を入れて単独で使えるようにする（#379 の機械可読化の一環）。

---

## 環境

- 実行: `/rl-anything:evolve`（docs-platform, 2026-06-09）


---

## #396 [Feedback] evolve フロー最適化2件: 新規観測0でのフル評価 no-op / fitness 母集団の鶏卵問題  `[closed]`  (enhancement,feedback)

## 概要

evolve のフロー設計について改善提案が2件。どちらも「このフローだと労力/価値が見合わないケースがある」点で共通します。

---

### 1. 新規観測 0 でもフル評価が回り、結果 no-op になる

`phases.observe`:

```json
{"sessions": 0, "observations": 0, "total_observations": 172, "sufficient": true,
 "message": "0 セッション, 0 新規観測 (全172) — データ十分"}
```

前回 evolve 以降の**新規セッション/観測が 0** なのに「データ十分」でフルパイプライン（audit / discover / skill_evolve batch_guard / remediation / ...）が回り、結局すべて keep/評価のみの **no-op** に終わった。batch_guard の AskUserQuestion まで挟む割に成果が無い。

**期待**: 「前回 evolve から新規観測 0（かつ前回が直近）」のときは軽量モード（observability surface だけ出して重い LLM フェーズや batch_guard をスキップ提案）を提示する。べき等性は正しいが、ユーザー操作コストに見合わない。

---

### 2. fitness 母集団が構造的に貯まらない（鶏卵問題）

`phases.fitness_evolution`:

```json
{"status": "insufficient_data", "structural_reason": "skill_evolve_not_scored", "data_count": 0}
```

メッセージは「evolve を回せば discover の skill diff / skill_evolve high·medium の accept/reject が母集団に積み上がる（ADR-041）」と案内する。しかしこの PJ では：

- `matched_skills: 0`（skill diff 提案が出ない）
- `high_suitability: 0` / `medium_suitability: 0`（変換提案が出ない＝8件 already_evolved、3件 insufficient_usage）

つまり**提案自体が出ない限り accept/reject は発生せず、evolve を何回回しても 0/30 のまま**。「evolve を回せば貯まる」という案内が、この PJ 構造では空手形になる。

**期待**: 提案が構造的に出ない PJ（already_evolved 飽和 + remediation fix 中心）では、母集団が貯まらない理由をより正直に説明する。あるいは fitness の母集団ソースを remediation の accept/reject にも広げる検討（ADR-041 で採点対象外になっている fix 系を一部含める等）。

---

## 環境

- 実行: `/rl-anything:evolve`（docs-platform, 2026-06-09）


---

## #400 [Feedback] evolve: dry-run/evolve_decisions/batch_guard の構造不整合とUX問題（バグ3+UX3+改善2）  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告 + 改善要望
**コンポーネント**: evolve
**満足度**: 3/5

## 詳細

evolve をフル実行した際に気づいた構造的な不整合とUX上の問題。バグ3件・理解しづらさ3件・改善案2件。

---

## 🐞 バグ（構造的な不整合）

### 1. dry-run 運用下で fitness の母集団が永遠に貯まらない
evolve は `--dry-run` で分析し、実際の変更（ファイル編集・archive・コミット）は assistant が AskUserQuestion 経由で適用する運用。しかし Step 7.8 evolve_decisions は「dry-run のため未記録」でスキップするため、**実際に適用した変更が optimize_history に1件も積まれない**。結果 fitness_evolution が永続的に `0/30` から動かず、Step 8 の「提案が出れば貯まる」は dry-run 運用である限り構造的に成立しない。

### 2. skill_evolve ↔ archive の reconcile 欠如
prune の archive 候補に上がっているスキルが、同一 run の skill_evolve で「medium 適性 = 自己進化を組み込め」と評価された直後に archive される矛盾が発生。split ↔ archive には reconcile（split_archive_reconcile）があるのに、skill_evolve ↔ archive には無い。archive 予定のスキルは skill_evolve 評価対象から事前除外すべき。

### 3. 課金ゼロ確定の batch_guard が停止＋全フェーズ再実行
batch_guard が `cache_fresh_count` 全件 fresh / `estimated_tokens_cache_aware=0` / `rerun_llm_free=True` と**課金ゼロを機械的に確定**しているのに AskUserQuestion で停止し、`--confirmed-batch` で evolve 全体を再実行する。確定ゼロなら自動評価でよく、全フェーズのやり直しは無駄。

---

## 😵 理解しづらい（UX）

### 4. batch_guard のトークン表示が脅し
画面に worst-case トークン（実質0なのに数千〜万）が前面表示される。`rerun_llm_free=True` の時に worst-case を前面に出すと、ユーザーを不必要に身構えさせる。確定ゼロのケースは「課金なし」とだけ表示すべき。

### 5. fitness_evolution の説明が冗長で結論が無い
`insufficient_data` 時に「提案が出れば貯まる／でも出ないPJでは 0/30 のまま／それは正常」と3段で説明されるが、ユーザーが取るべき行動は結局「放置でOK」の1行。長文の割に次アクションが読み取れない。構造的に母集団が貯まらないPJでは「このPJでは fitness は使わない設計。対応不要」と1行で締めるべき。

### 6. proposable の batch_skip が完全に不可視
remediation の proposable が individual と batch_skip に分かれ、低 confidence の batch_skip 群は1行も表示されずスキップされる。「沈黙 != 評価」の原則に反し、何件・何が握り潰されたか（件数だけでも）が分からない。

---

## 💡 改善案

- **usage=0 のスキルを batch_guard の母集団から事前除外する**。実例では評価対象14件中10件が insufficient_usage 保留で、実質4件のために guard が発火していた。使用実績ゼロのスキルは自己進化（実ミス蓄積）の効果が無いので、最初から母集団に入れない。
- **dry-run 運用でも assistant が適用した変更を evolve_decisions に記録する経路を追加する**（バグ#1 の根治）。これが無いと fitness calibration が永久に機能しない。
- **batch_skip の件数を最低1行 surface する**（バグ#6 の緩和、「沈黙 != 評価」の徹底）。

---
*Submitted via /rl-anything:feedback*


---

## #402 evolve: ingest（Step 7.8 drain）が SKILL.md prose 依存で決定論コードから呼ばれない — #360 と同系統の enforcement gap  `[closed]`  (enhancement)

## 背景: #400 の積み残し（同系統の enforcement gap）

#400 で `evolve_decisions`（ADR-041）の dry-run 運用経路を根治し、PR #401 で **emit→apply→ingest→fitness reader** が apply 境界をまたいで動くことを E2E + 実 store パスで実証した（空の本物 store が 0→1、worktree-safe slug `rl-anything` で reader が独立に同一パスを解決して観測）。

しかし、**`ingest_decisions`（Step 7.8 drain）を呼び出すのは今も `skills/evolve/SKILL.md:489` の指示文だけ**で、決定論コード（`evolve.py`）からは一切呼ばれていない（`evolve.py:969` はコメントのみ、実呼び出しは prose）。

```
$ grep -rn "ingest_decisions" --include="*.py" scripts skills hooks | grep -v tests | grep -v "def "
skills/evolve/scripts/evolve.py:969:    # SKILL.md Step 7.8 の drain（ingest_decisions）が optimize_history に記録する。  ← コメント
# 実呼び出しは skills/evolve/SKILL.md:489 のみ
```

## なぜ問題か（root cause）

これは #360 を生んだ **「SKILL.md MUST ≠ enforcement」（`learning_skill_md_must_not_enforcement`）と同じ系統**。

- PR #401 のテストが証明したのは「**IF** ingest を pending 付きで呼べば store は +1」。
- 証明していないのは「**実 evolve run で ingest が実際に呼ばれる**」。
- emit 側は `evolve.py:973` で決定論的に発火するが、**drain（ingest）の発火トリガーは assistant が SKILL.md Step 7.8 を実行するか**に依存する。assistant が飛ばす／フローが変わると、また optimize_history は空のまま＝fitness が `0/30` から動かない元の症状に戻る。そして**この失敗はどのテストでも捕まらない**（テストは ingest を直接呼ぶため）。

## 影響

- 症状: 実 PJ で evolve を回しても母集団が貯まらず fitness が永久に insufficient_data に張り付く（#400 の主訴と同じ）。
- 発火頻度: drain が prose 依存である限り、いつでも再発しうる（silent）。

## 方針（second-opinion で YAGNI 判定してから着手）

emit が決定論なら drain も決定論にしたい。ただし drain は「assistant が apply した**後**」に走る必要があり、その apply は本質的に対話ステップ。案:

1. **Stop hook で「emit したが未 drain」を検出して auto-drain**（`result.evolve_decisions.pending` が出た session で ingest 未実行を検知）。emit→（apply）→drain の C相を hook 化し、`evolve_decisions` が accept/reject を決定論キャプチャしたのと同じ思想を **drain トリガー**にも適用する。
2. もしくは `rl-evolve --drain`（emit と対の CLI）を用意し、SKILL.md からは1コマンド呼ぶだけにして「呼ばれたか」を fire-log で計測（hook_drift の follow-through 計測と同型）。

## 回帰ガード（必須）

- 「ingest を呼べば +1」ではなく「**実 run で drain トリガーが発火する**」を assert する E2E を足す（trigger が prose でなく決定論経路に乗ったことを観測）。`scripts/tests/evolve_pj_harness.py` を拡張。
- 完了基準は「テスト緑」でなく「**実 PJ 非 dry-run で1回回したら正準 store が +1**」（`learning_dryrun_verification_blind_spot`）。

## 参照
- #360 / #400 / PR #401
- ADR-041（evolve_decisions の決定論キャプチャ）
- `learning_skill_md_must_not_enforcement` / `learning_install_is_not_enforcement` / `learning_dryrun_verification_blind_spot`


---

## #407 evolve: lightweight_recommended でも全フェーズを完走（observe 先行 early-return が無い）+ dry-run が無音で長い  `[closed]`

## 概要

`observe` フェーズが `lightweight_recommended`（前回 evolve 以降の新規観測なし）を返しても、CLI は重いフェーズ（`audit` / `skill_evolve` / `reorganize` / `prune` …）を含む **全フェーズを完走してから** 結果を返す。SKILL 側は Step 1 で「軽量モードにするか」をユーザーに尋ねるが、その分岐が提示される時点で既に重い処理は終わっている。

## 実測（figma-to-code, `rl-evolve --dry-run`）

- `phases.observe.action == "lightweight_recommended"`（message: 「前回 evolve 以降の新規観測なし（0 セッション / 0 新規観測, 全175）」）
- それでも出力 JSON に **18 フェーズ全部** が入っている（`audit.report` 生成済み・`skill_evolve.assessments` 生成済み）
- `skills/evolve/scripts/evolve.py` L381-387 は `action` をセットするだけで **return せず後続フェーズを継続**
- dry-run の完了まで実測 **約 20 分**

## 問題

1. **軽量モードの目的（重い LLM/分析フェーズを省く）が CLI 側で実現されていない。** 軽量と判定するために毎回フル分析のコストを払っており、SKILL の lightweight 分岐が事実上の事後通知になっている。
2. **dry-run 冒頭の長時間が無音。** 進捗ゼロ・推定所要時間の提示がなく、ハング/デッドロックと区別できない（ファイル存在ベースの待機と組み合わさると stale 誤読も誘発する → 関連 issue 参照）。

## 提案

- `observe`（新規観測の有無）は安価に計算できるので、**重いフェーズの前に observe だけ算出**し、`lightweight_recommended` / `skip_recommended` のときは `audit` 以降を skip して early-return する（例: `--observe-first` フラグ、または action 判定後に重いフェーズを条件分岐）。これで SKILL Step 1 の分岐が初めて意味を持つ。
- dry-run 冒頭で推定所要時間 or フェーズ進捗を stderr に出す。

## 環境

- rl-anything（plugin cache 1.92.x 系）
- 観測: figma-to-code PJ への evolve 実行中（2026-06-09）

<!-- rl-evolve-feedback:lightweight-not-early-return -->


---

## #408 evolve: 実行結果の同一性・観測可能性が弱く 別PJ/stale/失敗を取り違える（constitutional『LLM評価失敗』は実は stale cache）  `[closed]`

## 概要

evolve の実行結果（出力 JSON / レポート）が「**誰の（どの PJ）・いつの・正しい結果か**」を機械的に検証できず、別 PJ の結果や stale な結果、内部の失敗を取り違えやすい。実際に取り違えが発生した。

## 実測で踏んだ事象（figma-to-code への evolve 実行中, 2026-06-09）

### A. 共有 `/tmp` 固定パスで別 PJ の stale 出力を誤読

- SKILL Step 1 が `rl-evolve --dry-run --output /tmp/rl_evolve_out.json`（**PJ 非依存の固定パス**）を指示。
- 同日先に別 PJ（sys-bots）の evolve が同じパスに書いた **stale ファイルが残存**。新 run（約20分かかる）完了前にそれを読み、constitutional principles（cdk-deploy / lambda / bedrock …）と skill 一覧（aws-deploy / rag-ingest / slack-fetch …）が **全て sys-bots のもの** として現れた。
- dry-run だったため無傷だが、**本実行なら「別 PJ のデータを見て対象 PJ に変更を加える」事故**になりうる。

### B. 出力 JSON に PJ 識別子が無い

- 読み手（assistant）が「これは正しい PJ の結果か」を検証する手段が `phases.skill_evolve.assessments[].skill_name` からの推測しかない。`slug` / `project_dir` / `generated_at` 等のトップレベル識別子が無い。

### C. worktree から slug 誤解決

- SKILL Step 0.5 の `SLUG=$(basename $(git rev-parse --show-toplevel))` は git worktree だと **worktree 名**（例 `evolve`）を返し、本来の PJ slug（`figma-to-code`）にならない。`--project-dir` を渡しても world-context 用 SLUG は worktree 名のまま → DATA_DIR が PJ 共通なため world-context の cross-PJ 汚染リスク（SKILL 自身が警告している事象）。

### D. constitutional 失敗が silent、しかもメッセージが誤り

- レポート本文に「**LLM 評価に失敗しました**」と出るが、`warnings[]` にも `observability` にも乗らず、`env_score=None` で Report クライマックスのナレーションも無言スキップ（沈黙≠評価済みの原則違反）。
- **根本原因を追ったところ、そもそも失敗ではなかった**:
  - ADR-037 で constitutional は LLM を全廃し「cache 済みレイヤーだけ集約、cache 無/全 miss なら LLM を呼ばず `None`」という LLM-free 設計（`scripts/rl/fitness/constitutional.py::compute_constitutional_score`）。
  - 実測: cache（`figma-to-code/.claude/constitutional_cache.json`, 6/4 生成）は3レイヤー（`claude_md` / `skills` / `memory`）のうち **`claude_md` 1つしか採点していなかった**。skills/memory は未採点。
  - その `claude_md` も 6/4 以降 CLAUDE.md を多数更新（#287〜#291 等）したため **content hash 不一致で cache miss**。
  - → **0/3 レイヤー命中 → `None`**。
  - つまり正体は「**cache が stale で再採点が必要**」。なのに表示は「LLM 評価に失敗しました」（`scripts/lib/audit/sections.py:19`）＝ **LLM 全廃前の文言の残骸**で二重に誤解を招く。

### E. `env_tier` が run 間で揺れる

- 同じ PJ で `large`↔`small` と変動（stale 由来も混在）。決定根拠が不透明。

## 提案

1. **出力に metadata ヘッダを必須化**: `{slug, project_dir, generated_at, dry_run, env_tier}` をトップレベルに。SKILL Step 1 に「読んだら slug を検証してから Diagnose に進む」を明記。
2. **`--output` を PJ/セッション別パスに**（例 `/tmp/rl_evolve_<slug>.json`）。待機はファイル存在でなく PID 終了で判定するよう SKILL に明記。
3. **slug 算出を worktree 対応**: `git rev-parse --git-common-dir` 経由で親 repo 名に正規化。
4. **constitutional のメッセージ修正と surface**: `None` は「失敗」でなく「cache stale/全 miss → 2相 refresh（audit Step 3.5）が必要」と案内。`warnings[]` に積み、推奨アクションに昇格。「LLM 評価に失敗しました」の文言は ADR-037（LLM 全廃）と矛盾するので撤去。
5. **`env_tier` の決定根拠を出力に含める**。

## 環境

- rl-anything（plugin cache 1.92.x 系）
- 観測: figma-to-code PJ への evolve 実行中（2026-06-09）

<!-- rl-evolve-feedback:result-identity-and-observability -->


---

## #409 correction_detect が CC 実ペイロードの prompt フィールドを読まず、UserPromptSubmit 起点の修正検出が初期実装から一度も発火していない  `[closed]`

## 症状

corrections.jsonl の新規レコードが Stop hook feedback 由来（save_state 経由）のみで、ユーザー発話の修正パターン（「いや、そうじゃなくて」等）が一度も記録されていない。

## 根因

CC の UserPromptSubmit イベントは発話を top-level `prompt` フィールドで渡すが、`hooks/correction_detect.py` は `event["message"]` しか読まない。初期実装（328eddb6）から一貫してこの形だったため、**実環境では誕生以来一度も検出が発火していない**。

## 証拠

- 実ペイロード形 `{"session_id":..., "prompt":"違う、そうじゃなくて..."}` を流すと exit=0 で何も書かれない（実測）
- 同じ文を `{"message":...}` 形で流すと `chigau 0.85` を検出・記録（実測）
- 既存テスト 76 件は全て合成の `message` 形 → 緑のまま機能不全（learning_synthetic_fixture_false_confidence の実例）
- corrections.jsonl 全 9 レコードの correction_type は stop 系 + 由来不明 1 件のみ

## 影響

corrections を上流とする reflect / auto-memory / optimize / constraint_decay / trigger_engine が、ユーザー発話シグナルを一度も受け取れていなかった。

## 修正

`event.get("prompt")` を優先読み、旧 `message` 形はフォールバックで温存。実ペイロード形の回帰テスト 2 件追加。

---

## #415 sessions.db が per-fire connection 開閉で再肥大する（9.6GB/実データ14MB を実測）— batch 化 or 定期 compaction が必要  `[closed]`

## 実測

- hook 側 sessions.db: **9.6GB**、sessions テーブル 83,966 行、raw_json 平均 161 bytes ＝ 実データ約 14MB（**約 680 倍の bloat**）
- 原因は pitfall_duckdb_checkpoint の運用面: 毎 hook 発火で connect→INSERT 1行→close を繰り返すと DuckDB ファイルが追記のたびに成長し、縮小されない

## 現状

#364（PR #414）の `rl-fleet migrate-data` が rebuild 方式マージで一度 compaction するが、**migration 後も hook の書き込みパターンは per-fire のままなので数ヶ月で再肥大する**。

## 候補

1. hook は jsonl にのみ書き、db への ingest は evolve/audit 実行時に batch（既存 `session_store.ingest` 経路を SoR に）
2. 定期 compaction: audit/evolve 完走時にファイルサイズ vs 行数×平均行長の乖離が閾値超過なら rebuild
3. 両方（jsonl-first + 保険の compaction）

決定論・LLM 非依存で実装可能。

---

## #416 フルスイート実行時のみ fail する実行順依存テスト 3-4 件（prune TestMergeDuplicates / audit test_collect_issues）— pre-existing  `[closed]`

## 症状

canonical コマンド `pytest hooks/ scripts/tests/ skills/ scripts/rl/tests/` の全体実行でのみ以下が fail し、単独・skills/ スコープ実行では緑:

- `skills/prune/scripts/tests/test_prune.py::TestMergeDuplicates::test_primary_by_usage_count`
- `skills/prune/scripts/tests/test_prune.py::TestMergeDuplicates::test_primary_alphabetical_on_equal_count`
- `skills/audit/scripts/tests/test_collect_issues.py::test_plugin_skill_excluded_from_line_limit`

## 切り分け済み

- main の worktree（e3aeba00）でも同一 fail を確認 → **pre-existing**（feat/data-dir-unification 起因ではない）
- 単独実行は緑 → 実行順・収集経路依存（pitfall_test_hygiene_global_state_and_pkg_shadow / pitfall_test_sysmodules_manual_pop と同系統の可能性）

## 次の一手

`pytest -p no:randomly` 不使用環境なので、収集順での先行テストによる global 状態（sys.modules / mock 残留 / DATA_DIR）汚染を bisect で特定する。

> 💬 comment:
>
> 起票時に fail していた 3 テスト（skills/prune/scripts/tests/test_prune.py::TestMergeDuplicates::test_primary_by_usage_count / test_primary_alphabetical_on_equal_count、skills/audit/scripts/tests/test_collect_issues.py::test_plugin_skill_excluded_from_line_limit）は skills/ 配下のため現行フルスイートの収集対象に含まれる。
> 
> 2026-06-12 のフルスイート実行（`python3 -m pytest`、pytest.ini testpaths 経由で 4747 件収集、#468 以降の正準コマンド）で 4759 passed / 1 skipped / fail 0 を確認済み → 症状消滅。
> 
> テスト隔離強化（#457: run_evolve 系の HOME 隔離 / #459 / #471: defense-in-depth 3件）のいずれかで副次的に解消されたと推定。
> 
> 再発した場合は再 open する。

---

## #417 feat(data-dir): merge_db のスキーマ乖離・破損・並行書き込みに対するロバスト化（#414 follow-up）  `[closed]`

## 背景
PR #414（DATA_DIR 一元化 migration, #364 Phase 2）のレビューで検出した robustness 3点。migration 本体は安全（write-before-delete・冪等・dry-run書込ゼロ・marker全件成功時のみ）だが、以下の縁ケースが未対処。

## 1. スキーマ乖離で migration が永久失敗（INVESTIGATE, 優先）
`scripts/lib/data_dir_migration.py:167`:
```python
con.execute(f"CREATE TABLE {qt} AS SELECT * FROM src.{qt} UNION SELECT * FROM old.{qt}")
```
src/old で同名テーブルの列数・型が異なると DuckDB Binder Error → 当該 entry が failure → `failures>0` で marker が書かれず → SessionStart リマインド（`restore_state._deliver_data_dir_migration_reminder`）が鳴り続け、その db は永遠に未移行になる。バージョン跨ぎで列追加された db（token_usage / episodic）で起きうる。

**失敗は `format_summary` + stderr に出るため silent ではない**（maintainer が気付ける）が、自動回復はしない。

対処案: Binder Error を catch → 列共通部（intersection）でマージ、または src/old を別テーブル名で両保持してから手動統合を促す。20行超の設計変更のため #414 とは分離。

## 2. 並行書き込み窓（INFORMATIONAL）
`data_dir_migration.py:289` で merge 後に `_remove_entry(src)`。merge_jsonl は `:100` でスナップショット読みするため、migrate 実行中に他 CC セッションの hook が source へ追記した行は未マージで削除される。窓は merge 所要時間ぶん。運用（idle 実行）で回避可能だが enforce されていない。

対処案: 実行前ロック / source を rename してから merge / 実行ガイダンスに「他セッションを閉じてから」を明記。

## 3. UNION dedup の行折り畳み（INFORMATIONAL・仕様）
`UNION`（`UNION ALL` でない）はキー無しテーブルの正当に重複する行も折り畳む。jsonl 行 dedup と同じ意図的設計。token_usage は PK uuid で無害。ドキュメント済みなら可。

## テストギャップ
スキーマ不一致 / 破損・ロック中 db / 並行書き込み が未テスト（現状 happy union のみ）。上記対処と同時にケース追加。

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## #419 fix(audit): fleet ISSUES 599件は測定バグ — hardcoded_values 検出パイプライン3点修正  `[closed]`

## 発端

`rl-fleet status` の ISSUES 列が全 PJ で 600 前後に揃っており（bots/receipt/figma-to-code は**ぴったり 599 で一致**）、env_score の物差しとして死んでいる。再現調査で 599 件を完全再現し、根因3点を確定した。

## 証拠（再現済み）

- 除外なし経路（orchestrator 相当）で走査すると TOTAL=599、内訳: `api_key 552 / service_url 46 / numeric_id 1`
- api_key 552 件（**92%**）は全て gstack スキル散文 `a`**`sk-only-for-one-way`** が `sk-` パターンに単語途中で部分一致したもの
- リポジトリ HEAD の `issues.py` 経路（origin 除外あり）で同条件走査すると **TOTAL=0**

## 根因3点

1. **`sk-` regex に単語境界がない**: `hardcoded_detector.py:17` の `(xoxb-|xapp-|sk-|AKIA)` が `ask-only-...` 等の英単語内部にマッチ。`(?<![A-Za-z0-9])` 等の境界が必要
2. **検出ループの二重実装の片直し**: `scripts/lib/audit/issues.py:252` には global/plugin origin 除外があるが、`scripts/lib/audit/orchestrator.py:175` の同型ループには**ない**。growth-state→fleet に流れるのは除外なし側。検出ループを 1 箇所に共通化して divergence を根治する
3. **走査対象の汚染**: gstack が `~/.claude/skills/` に同梱する node_modules CHANGELOG・`.hermes`/`.gstack-backup` ミラーが走査対象（pitfall_audit_collection_noise の再発形）。収集除外に `node_modules` / dot-dir を追加

## 再発予防（メタ不変条件）

fleet で**全 PJ の issue 数が同値**なら測定バグの強いシグナル。fleet 集計に「複数 PJ の issues_summary が完全一致したら警報」の不変条件チェックを1本入れる。

## Acceptance Criteria

- [ ] `sk-only-for-one-way` を含む散文 fixture で api_key 検出 0 件（regex 境界の回帰テスト）
- [ ] orchestrator 経路と issues.py 経路が同一の検出関数を共有（divergence の構造的根治）
- [ ] node_modules / `.hermes` / `.gstack-backup` 配下が走査対象外（収集除外テスト）
- [ ] 実環境で audit 再実行 → rl-anything の hardcoded_values が 599 → 1桁台に減少することを実測
- [ ] fleet の同値カウント警報テスト

## 関連

#352, #359（ADR-043: 検出器FPは文脈で落とす）, #377-2, pitfall_audit_collection_noise

---

## #420 fix(test-hygiene): growth-journal の 87% がテスト汚染 — DATA_DIR 隔離を許可リスト方式から構造的隔離へ  `[closed]`

## 発端

growth-journal.jsonl が 977 件中 **852 件（87%）テスト実行による汚染**（project が `test_*`/`tmp*`/`unknown`）。実 PJ の crystallization は 13 件のみ。growth-state の「65,752 sessions で crystallizations_count=0」の正体は出口詰まりではなく、実信号がテストゴミに埋もれていることだった。汚染は現在も進行中（2026-06-10 01:17 のエントリを確認）。

## 根因

ルート conftest.py の隔離 fixture は存在するが、**per-test の `monkeypatch.setenv` は module import より後**に走る。`growth_journal.py:22` は `_DATA_DIR_VAL = _common.DATA_DIR` を **import 時に確定**するため env が効かず、conftest の手動 patch 許可リスト（session_store / token_usage_store / optimize_history_store の3つ）にも入っていない＝**4匹目のモグラ**。許可リスト方式は構造的に再発する。

## 設計（3層）

1. **入口で塞ぐ**: conftest の**トップレベル**（全 import より先に実行される）で `CLAUDE_PLUGIN_DATA` を session 一時 dir に設定。import 時キャプチャ組も含め構造的に隔離され、モジュール個別 patch が不要になる
2. **不変条件テスト**: 「pytest 下で全 store モジュールの DATA_DIR 解決先が実 home でない」ことを assert する契約テスト1本。新 store 追加時の漏れを機械検出
3. **既存汚染の purge**: `test_*`/`tmp*` 始まりの 852 件を一度だけ除去。`unknown`/空 project の 112 件は誤 purge リスクがあるため別バケツで保留判定

## Acceptance Criteria

- [ ] conftest トップレベル env 設定 + 既存の手動 patch 3 件を撤去しても全テスト緑
- [ ] 不変条件テスト: store モジュールを列挙し pytest 下で実 home 解決がないことを assert
- [ ] フルスイート実行後に実 growth-journal / corrections / sessions に新規エントリゼロ（実環境で実測）
- [ ] purge 後の growth-journal が実 PJ エントリのみ（件数・内訳を実測提示）

## 関連

#308（dry-run stateful store write）, #417（resolver marker が実 home 読み）, pitfall_dryrun_stateful_store_write, pitfall_test_hygiene_global_state_and_pkg_shadow, pitfall_global_datadir_single_file

---

## #421 feat(reward): 報酬入力の飢餓解消 — correction capture 率の監視 + SessionStart 自動 drain  `[closed]`

## 発端

RL ループの報酬データがほぼ空であることを実測で確認:

- corrections.jsonl: **9 件のみ・全件 reflect_status=skipped**（30日 3.3B tokens 消費の PJ で）
- evolve_decisions: rl-anything/docs-platform **0 件**、sys-bots 2 件のみ
- evolve_pending: 空

ADR-041 の決定論キャプチャ配線は正しいが、**上流に水が流れていない**。capture 率が正常か異常かを今は誰も監視していない。

## 設計（2点）

1. **correction capture 率を telemetry に追加**: 「20ターン以上のセッションのうち correction を1件以上検出した割合」を決定論算出し、audit/fleet で surface。9件/76日が検出器の仕様通りなのか capture 漏れなのかを判別可能にする（hook 有用性評価 #318 の follow-through と同じ思想）
2. **SessionStart 自動 drain**: `rl-evolve --drain`（apply 済み提案の accept 判定回収）を restore_state hook に同居させ、人手ゼロで決定論回収。learning_skill_md_must_not_enforcement の教訓（SKILL.md の MUST では実行され損ねる）をそのまま適用し、「実行され損ねない場所」に置く

## 前提

#420（テスト汚染除去）が先。汚染データの上に capture 率を作っても信号が濁る。

## Acceptance Criteria

- [ ] capture 率が audit レポート / fleet に表示される（決定論・LLM 非依存）
- [ ] SessionStart 後に pending marker が自動 drain され evolve_decisions に記録される E2E（apply 境界をまたぐ実テスト — learning_dryrun_verification_blind_spot 準拠で store 差分を assert）
- [ ] hot hook レイテンシ予算を維持（pitfall_hot_hook_eager_import: drain 追加で SessionStart が重くならないこと、実測値提示）

## 関連

ADR-041, #400, #402, #409, #411, #318, #360-A

---

## #422 chore(observe): 読者ゼロ観測の削減 — tool_durations.jsonl は毎 Bash で hook 起動して誰も読まない  `[closed]`

## 発端

主要ストアの producer→consumer 突合（テスト除外）で、書きっぱなし観測を特定:

| ストア | サイズ | writer(hooks) | reader(scripts/skills) |
|---|---|---|---|
| **tool_durations.jsonl** | **5.1MB** | 1（毎 Bash PostToolUse） | **0** |
| sessions.jsonl/db | 33MB+24MB | 2 | 14 |
| errors.jsonl | 8.1MB | 4 | 10 |
| usage.jsonl | 2.0MB | 3 | 26 |
| subagents.jsonl | 6.1MB | 1 | 3 |
| workflows.jsonl | 3.1MB | 1 | 4 |

`tool_duration.py` hook は**全 Bash 実行ごとに python3 を起動**して書き込むが、消費者がいない。純粋なレイテンシ＋ディスクコスト。

## 設計

1. tool_duration hook + ストアを削除（または「将来使う具体的な消費者設計」があるなら issue に明記して残す判断を記録）
2. この producer→consumer 表を #318（hook 有用性評価 第2フェーズ）の入力として整備し、「reader 0 のストアを audit が検出する」チェックを追加（今回の手動突合を決定論化）

## Acceptance Criteria

- [ ] tool_duration の hook 登録・実装・ストアが削除される（or 残す判断が ADR/issue に記録される）
- [ ] audit に orphan store 検出（writer あり reader なし）が入り、回帰テストあり
- [ ] hooks.json の Bash matcher が PreToolUse 1 個になり、Bash 実行あたりの hook 起動回数減を実測

## 関連

#318, #415（sessions.db 肥大）, pitfall_hot_hook_eager_import

---

## #423 feat(fitness): アウトカム指標 v1 — utilization 恒久0の修理 + 行動アウトカム3軸の advisory 導入  `[closed]`

## 発端

env_score が全 PJ で 0.6 前後・Lv.6-7・initial_nurturing に頭打ち。実測で構造要因を2つ確認:

1. **utilization=0.0 が構造的**: `telemetry.py` の `_find_all_skills` は `project_dir/.claude/skills/` のみ走査。plugin レイアウト（skills/ 直下）の本 PJ では恒久 0 → telemetry の重み 25% が死に枠
2. **スコアの大半が「構造の綺麗さ」**: coherence/constitutional は入力 proxy であり、「環境が良くなればユーザーの手戻りが減る」という目的変数を直接測る軸がない

## 設計（3段階）

### 1. 即修理
utilization のスキル探索を audit と同じ `find_artifacts` 収集系に統一（plugin レイアウト対応）。

### 2. アウトカム3軸を advisory（表示のみ）で導入
- **correction 再発率**: 同型 correction が reflect/evolve 適用後の窓で再発した率。「学習が定着したか」の最直接指標
- **一発成功率**: エラー→リトライ連鎖なしのワークフロー完走率（workflows.jsonl 拡張）
- **rework 率**: 同一ファイルの N ターン内再編集率（既存 file 変更記録から決定論算出）

### 3. 実測分布を見てから昇格（ADR 化）
2〜4 週 advisory 並走 → 分布実測 → 重みへ昇格 + coherence/constitutional をゲート（pass/fail）に降格。ADR-044 の学び（しきい値は実データ dry 適用前に確定しない）を適用。

## 前提

#419（測定バグ）・#420（テスト汚染）・#421（correction capture）が先。壊れた入力の上に新指標を作らない。

## Acceptance Criteria

- [ ] utilization が plugin レイアウト PJ で非ゼロになる（実測値提示）
- [ ] アウトカム3軸が audit レポートに advisory 表示される（決定論・LLM 非依存）
- [ ] 各軸の出力に evidence（根拠レコード参照）が付く（learning_observability_quality_evidence_and_meaning 準拠）
- [ ] 重み昇格の判断基準（分布条件）が ADR に記録される

## 関連

#185（自己診断ギャップ）, #356（fitness calibration）, ADR-044, learning_gate_design_needs_real_corpus_dryrun

---

## #427 chore(observe): message_display.jsonl も orphan store — writer 登録ありで reader 0  `[closed]`

## 発端

#422 で導入した orphan store 検出（`orphan_store.py`）が、実ツリーで真正の orphan をもう1件検出した:

- **message_display.jsonl**: writer `hooks/message_display.py`（MessageDisplay matcher で登録済み）、reader（scripts/skills）**0**

tool_durations と同型の「書きっぱなし観測」。#422 のスコープ外として未対応（検出のみ）。

## 対応案

tool_duration と同じ判断フロー:
1. 消費者設計が具体的にあるなら issue に明記して残す判断を記録
2. 無いなら hook 登録・実装・テスト・ストアを削除

## 関連

#422（orphan store 検出の導入元）, #318（hook 有用性評価）

---

## #430 feat(observe): utterance アーカイブ — 全PJ human 発話の永続ストア（transcript 14日消失への対策）  `[closed]`

## 背景（実測）

- `cleanupPeriodDays: 14` により `~/.claude/projects/` の transcript は **14日で削除**されている（35日超の生存ファイルは 9件のみ）。会話データは「貯まっている」のではなく毎日失われている
- 全PJ走査の実測: user行 47,349 のうち human 発話は 6,967（約15%、残りは tool_result 等の機構ターン）。テキスト量は数MB/月オーダーと小さい
- correction の個人辞書学習・outcome 帰属・遡及分析はすべてこのデータが基盤。消失したら再構築不能

## 提案

human 発話＋最小文脈のみを永続アーカイブに batch ingest する:

- 保存対象: human 発話テキスト / PJ slug / session_id / timestamp / 直前 assistant アクション要約（tool 名程度）
- 除外: tool_result・harness 注入ターン（compaction / system-reminder / command-name — learning_trajectory_mining_machinery_turns 準拠）・文字起こしコンテンツ等の非対話データ（PJ タグで除外可能に）
- 書き込み: hook per-fire でなく **batch ingest**（#415 の jsonl-first 再設計と同一経路に一本化。DuckDB checkpoint pitfall 準拠で最上位 1 connection）
- スキーマに temporal validity + provenance を最初から持たせる（#12 と接続）

## 設計参照

- Personalize-then-Store (arXiv 2605.25535): 選択→個人化→保存の3段階分解と各段の評価枠組み
- Heterogeneous Temporal Memory Governance (arXiv 2605.14802): 記憶種別ごとの temporal governance 層（#12 の設計参考）
- plastic-labs/honcho: 非同期バックグラウンドパイプライン構造の参照実装（エンジン導入はしない）
- supermemory の機能リスト（事実抽出・矛盾解消・期限切れ削除）は checklist としてのみ参照

## 関連

- #415（jsonl-first 化 — 同じ ingest 工事に一本化）
- #12（APEX-MEM temporal validity + provenance）
- 暫定対策として cleanupPeriodDays を 14→60 に延長済み（ディスク影響 概算+5GB）

## Success Criteria

- 実機 1 PJ E2E: 全PJ transcript から human 発話のみが ingest され、wall time / DB size / row 数を assertion（transcript-store-bench ルール準拠）
- 14日より古い発話がアーカイブから検索可能になる
- 機構ターン混入率が目視サンプルで 5% 未満

> 💬 comment:
>
> マージ後の実環境検証（HEAD=ae361d77、PR #437/#438）: 全4項目 OK
> 
> 1. **初回 backfill 完走**: 全PJ 1,501 files / 1.2GB → inserted 3,948件（dialogue 3,204 / long_paste 637 / excluded_pj 107）、16 PJ、wall 11.5秒、DB 18.1MB。冪等性確認（再実行 inserted=0 / 0.1秒）
> 2. **sessions.db ingest (#415)**: 79,723 jsonl 行 → 19,151 inserted（60,572 は dedup スキップ）、rotate 1世代保持、compaction は乖離2.42倍<10倍ゲートで非発火（設計通り）
> 3. **staleness marker / SessionStart advisory**: marker 書込・14日以内沈黙・marker 不在時 advisory の両条件確認
> 4. **audit store contract**: utterances.db の undeclared/stale 誤検知なし
> 
> 要注意（未修正・備忘）:
> - `utterance_archive/query.py` の keyword 検索が case-sensitive `LIKE`（`duckdb` が 0件、`ILIKE` なら 6件）。recall 用途や #431 で keyword を使うなら `ILIKE` 化を検討
> - ingest_state 1,536 vs find 1,545 の差9ファイルは空/フィルタ対象（異常なし）

---

## #431 feat(reward): correction capture の二層化 — 定期バッチ LLM 意味判定 + ユーザー固有修正イディオムの個人辞書  `[closed]`

## 背景（実測 2026-06-10）

- corrections.jsonl は累計 9件、うち本物の人間修正は **1件**（残り8件は Stop hook の機械生成）。2026-05-25 以降の新規ゼロ
- フェーズ昇格条件（corrections >= 10, growth_engine.py）がこの飢餓で永久未達 → **全7PJ が initial_nurturing に固定**。reflect / optimize も燃料切れ
- 既存パターン（CORRECTION_PATTERNS 23種）は修正系がほぼ行頭アンカー（`^いや` `^違う` `^no`）で文中の修正を構造的に取りこぼす
- **固定語彙の拡充は実コーパスで否定済み**: 全PJ human 発話 6,967 件を走査した結果、文中形候補（じゃなくて26 / 違う16 / ではなく8 / って言った4 / 戻して・やり直し・そうじゃなく **0**）はヒットの大半が文字起こしデータ・貼り戻した assistant 出力・「何が違う？」質問慣用句で、本物の修正は数件。precision/recall とも実用にならない
- 実際の修正スタイルはユーザー固有: 正しい値の後置型（「つむぎにしてほしい、四国めたんじゃなくて」）、ソフト指摘型（「P6のデザインが違うんだけど」）、観察型（「〜気がするんだよなぁ」）。**語彙でなく意味論でしか拾えない**

## 提案（二層化）

1. **hot hook（既存維持）**: 強パターンのみ・ゼロ LLM・低レイテンシ。拡充しない
2. **定期バッチ（新設）**: auto_memory の 2相方式（ADR-037）と同型で、直近セッション transcript を LLM が読み「ユーザーが Claude の方向を正したターンか」を意味判定
   - 検出結果は **weak_signals レーンに隔離**し、reflect 時の人間確認後に corrections へ昇格
   - フェーズ昇格カウントは human-source（確認済み）のみ
   - 検出した言い回しは provenance 付きで**個人辞書**として蓄積（実コーパスで precision 検証してから hot hook の補助パターンに昇格可能）
3. **provenance 重み付け**: 既存 source フィールドで human > hook を明確化し、機械ノイズで状態が動かないようにする

## 制約

- llm-batch-guard: 実装前に件数・見積もりトークン数を提示して確認を取る
- 入力データは #430（utterance アーカイブ）を基盤とする（transcript 14日消失のため）
- 文字起こしコンテンツ等の非対話 PJ データの除外必須

## 設計参照

- Mem-π (arXiv 2605.21463): 「いつ・何を記憶すべきか」の学習的取捨選択
- MemAudit (arXiv 2605.23723): 汚染メモリの事後監査・因果帰属（現状 8/9 が機械ノイズ＝汚染状態の理論的裏付け）

## 関連

- #430（基盤）、#421/#428（capture 率測定 — 本 issue は「測定」の次の「改善」）

## Success Criteria

- 実 PJ 直近 N セッションのバッチ dry-run で、目視確認した本物の修正の再現率と precision を実測値で報告
- weak_signals → 昇格フローが reflect で機能し、フェーズ昇格が human-source のみで駆動される

---

## #432 feat(reward): 暗黙修正シグナルの決定論検出（直後手編集 / permission deny / 言い直し / Esc 中断）→ weak_signals レーン  `[closed]`

## 背景

明示的な修正発話はユーザーの言い回しに依存し稀（#431 の実測参照）。一方、修正の**行動シグナル**は語彙非依存で決定論検出できる:

1. **直後手編集**: Claude の Edit/Write 直後にユーザーが同一ファイルを手で編集（file change observe hook は既設）
2. **permission deny**: ツール実行の拒否
3. **言い直し**: 連続する user prompt の高類似（編集距離 or トークン重複）
4. **Esc 中断**: 実行中の介入

## 提案

- 各チャネルを既存 observe 層の組み合わせで検出し、**weak_signals レーン**（#431 と共有）に provenance 付きで記録
- corrections 本流には直接入れない（deny は「今はやるな」、手編集は「続きの作業」の可能性があり本質的にノイジー）。reflect 確認後にのみ昇格
- ゼロ LLM・hot path はイベント記録のみ、判定はバッチ側

## 設計上の注意

- 偽陽性の文脈除去は learning_detector_fp_context_not_allowlist 準拠（個別 allowlist でなく除外理由の直交分離）
- しきい値（言い直し類似度等）は実コーパス dry-run 前に確定しない（ADR-044 の教訓）
- 新ストア追加時は writer/reader/retention を宣言（orphan store 再発防止、#434 参照）

## 関連

- #431（weak_signals レーン共有・昇格フロー）、#430（言い直し検出の発話データ基盤）

## Success Criteria

- 実 PJ 直近セッションの dry-run で各チャネルの検出件数と目視 precision を報告
- DATA_DIR への書き込みが dry-run でゼロ（pitfall_dryrun_stateful_store_write 準拠）


---

## #433 feat(fitness): outcome 3軸を evolve ターゲットランキングの自動入力に昇格（advisory→閉ループ）  `[open]`

## 背景（実測）

- outcome_metrics（#423/#429, ADR-046）の読者は audit 内部（observability.py / sections_outcome.py）のみ＝**画面表示で終端**。測定値が evolve のターゲット選定にも fitness 重みにも流れていない
- 測定層は充実（outcome 3軸・capture 率・orphan store）したが、ほぼすべて advisory 止まり。「測る環境」から「測定が行動を変える環境」への転換が必要

## 提案（自律度: ランキング自動入力まで。自動適用はしない）

1. **per-skill 帰属**: outcome 3軸（correction 再発率 / 一発成功率 / rework 率）を環境全体値からスキル単位に分解（telemetry の使用記録と突合）
2. **ランキング配線**: skill_triage / evolve の候補順位に outcome スコアを自動入力。**適用判断は従来通り人間**
3. **安全弁**: 既存 negative_transfer（#288）を regression gate に転用し、outcome 起点の進化が悪化を生んだら rollback 候補に挙げる

## 実装順序

- **一発成功率・rework 率の2軸は corrections 非依存なので先行配線可能**
- correction 再発率軸は #431/#432 で信号が溜まってから（空のまま配線しても空回り）
- ADR-046 で予定の「2-4週後の重み昇格判断」と同じレールに乗せる

## 関連

- #429（outcome 3軸 advisory 実装）、ADR-046、#431/#432（correction 軸の信号源）、#288（negative_transfer）

## Success Criteria

- evolve dry-run で outcome スコアが候補順位を実際に動かしたことを before/after で提示
- negative_transfer gate が悪化ケースで rollback 候補を surface する E2E（apply 境界をまたぐ — learning_dryrun_verification_blind_spot 準拠）

> 💬 comment:
>
> PR #436 で先行スコープ（2軸の per-skill 帰属 + ランキング配線）をマージ。実環境検証で得た構造的発見を記録する:
> 
> **changed=true（順位の実際の入れ替わり）は現データでは構造的に発生しない**
> - CREATE 候補は discover の missed_skill 由来＝未存在スキルなので telemetry ゼロ → 全件 degraded/neutral 0.0
> - telemetry を持つ既存スキルは triage で OK 行きになり、ランキング対象（CREATE/UPDATE/SPLIT/MERGE）に入らない
> - → ランキングが実際に順位を動かすのは (a) 既存スキルが UPDATE 候補に複数並ぶ、または (b) correction 再発率軸（#431/#432 の信号蓄積後）が配線されてから
> 
> 残スコープ: correction 再発率軸の配線、negative_transfer regression gate の転用（いずれも信号蓄積後）。

---

## #434 chore(observe): ストア新設の事前契約ゲート — writer/reader/retention 宣言を必須化（orphan store の事後検出→事前予防）  `[closed]`

## 背景

- orphan store 検出（#422/#426/#427）は**事後**検出。message_display.jsonl（#427）のように「writer 登録あり reader 0」が繰り返し発生しており、モグラ叩き状態
- sessions.db 肥大（#415）も「書き込みパターンを生成時に設計しなかった」ことが遠因。ストアのライフサイクル（retention/compaction）が新設時に決まっていない

## 提案

新ストア追加時に **writer / reader / retention** の3点宣言を必須化する事前ゲート:

1. spec/components.md（または専用 registry）への宣言エントリ必須
2. 既存 orphan_store 検出（hooks=writer / scripts+skills=reader 静的突合）を「宣言と実体の drift 検出」に拡張 — 宣言なしの新規 writer を audit が検出
3. retention 宣言（恒久 / TTL N日 / compaction 条件）を持たせ、audit が宣言なしストアを advisory 表示

## 関連

- #422/#426/#427（orphan store 事後検出）、#415（ライフサイクル未設計の実害）、#430（新設アーカイブが最初の適用例）

## Success Criteria

- 宣言なしで新ストアに書き込む hook を追加すると audit が検出する回帰テスト
- 既存全ストアの宣言バックフィル完了（orphan 3件の disposition 含む）

---

## #442 weak_signals に 45日 TTL を追加し expired を昇格候補から除外（evolve phase 化）  `[closed]`

## 背景
weak_signals.jsonl に TTL が存在せず、313 件が無期限に未昇格で滞留している。
corrections の constraint_decay 45日と整合する TTL を入れ、期限切れを昇格候補から外す。

## 変更
- `scripts/lib/weak_signals/ttl.py` 新規: `TTL_DAYS=45`, `mark_expired(dry_run=)`。
  `detected_at` から 45日超かつ未昇格・未expired を `expired=True` に原子的 rewrite
  （`promote._rewrite_promoted` と同型）。**削除しない**。
- `weak_signals/store.py`: `WeakSignal` に `expired` / `expired_at` 追加。
  `read_unpromoted` に `exclude_expired=True` 引数（promote.py / future readers が使う）。
- `evolve.py`: weak_signals run_batch 直後・daily_review の**前**に
  `result["weak_signals_ttl"] = ttl.mark_expired(dry_run=dry_run)` を常時 emit。
- `store_registry.py`: weak_signals.jsonl 宣言を `retention="ttl", ttl_days=45` に更新。

## Acceptance Criteria
- [ ] `mark_expired(dry_run=True)` が store の mtime を一切変えない（実 PJ E2E で assert）
- [ ] 45日超レコードが `expired=True` になり `read_unpromoted(exclude_expired=True)` から外れる
- [ ] 既存 promote/append の dry_run 挙動に回帰なし（snapshot）
- [ ] `claude plugin validate` パス

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: なし（先行ブロック）。**PR サイズ**: 1 PR・~250 行（store + ttl + evolve 配線 + tests）。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #443 初回 evolve で backlog の消化方式を選択（まとめて確認 / 日次5件 / TTL 失効に任せる）  `[closed]`

## 背景
既存 313 件（全件 llm_judge・未昇格）を初回 evolve でまとめて確認する入口がない。
**実測: 文字列類似では 313→267 (15%) しか圧縮できない**（idiom は生の発話断片）。
**決定（設計 §機能#3）: ハイブリッド方式** — アクティブ PJ（rl-anything 47件・figma-to-code
116件など上位）のみ初回 bootstrap でまとめて確認（per-PJ 15-30分）。残り PJ は日次5件 +
TTL 45日の自然失効に任せる。これは**「古い修正候補は腐る」を意図した間引き**であり、
45日間確認されなかった低活動 PJ のシグナルは現在の作業文脈との関連が失われている
（TTL がそのまま品質フィルタとして機能する）。

## 変更
- `scripts/lib/correction_semantic/bootstrap_backlog.py` 新規: `build(pj_slug, dry_run=)`。
  marker 未設定なら当該 PJ の未昇格 backlog を内容キーワード jaccard≥0.5 で group 化
  （31 group が 77件吸収）。`groups`（代表 idiom + signal_keys）を返す（一括昇格 UX 用）。
- `bootstrap_done-<slug>.marker` 新規ストア（store_registry 宣言・writer_locus=batch）。
- `evolve.py`: `result["correction_review"]["bootstrap"]` に相乗り emit（#C とキー共有）。
- `skills/evolve/SKILL.md`: is_bootstrap=True のとき **AskUserQuestion で 3 択を人間が選ぶ**
  （機械が「アクティブ PJ」を判定しない。backlog 件数は判断材料として表示するだけ）:
  - 「まとめて確認」→ groups を順に AskUserQuestion バッチで確認（代表確認で同 group 一括昇格）。完了時 marker
  - 「日次5件ずつ」→ marker を立てず #C の通常ページネーションに合流
  - 「TTL 失効に任せる」→ marker を立てる（以後再提示しない。TTL が間引く）

## Acceptance Criteria
- [ ] bootstrap は **cwd の PJ slug の backlog のみ**を対象にする（別 PJ の件数が混入しない）
- [ ] marker 立ち後は `is_bootstrap=False` で即返す（「TTL 失効に任せる」選択でも marker が立つ）
- [ ] 3 択いずれを選んでも evolve 全体は完走する
- [ ] dry_run でファイル不変（実 PJ E2E）
- [ ] rl-anything 実 PJ で `pj_total=47`（実測値）が出ることを確認

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: なし（#C と統合するが先行実装可）。**PR サイズ**: 2 PR
（PR1: bootstrap_backlog + marker + evolve emit、PR2: SKILL.md の 3 択分岐）。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #444 weak_signals observability に「evolve で昇格可能」誘導を追記  `[closed]`

## 背景
weak_signals builder は登録済み（ADR-028）。未昇格 N 件を surface しているが、
ユーザーを evolve の入口（今日の修正確認）へ誘導していない。**新 builder は作らない**（#278 教訓）。

## 変更
- `scripts/lib/audit/sections_weak_signals.py`: 戻り行に
  「未昇格 N 件 → /rl-anything:evolve の今日の修正確認で昇格可能」を追記。
  `correction_review.remaining` が取れる文脈なら「backlog 消化中（残 X group）」併記。

## Acceptance Criteria
- [ ] markdown 経路と構造化経路（collect_observability）の両方に同じ行が出る（ADR-028 単一ソース）
- [ ] store 空のときは None（沈黙）を維持

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: なし（#C と並行可・低リスク）。**PR サイズ**: 1 PR・~60 行。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #445 複数PJで集計値が bit-exact 一致したら測定バグ候補として audit に surface（#185）  `[closed]`

## 背景
learning_measurement_layer_diagnosis: 「全 PJ 同値カウント = 測定バグ強シグナル」。
#419-#423 はこれを手動診断した。自動化して audit に乗せる。advisory のみ・スコア非関与。

## 変更
- `scripts/lib/audit/measurement_bug.py` 新規: `detect_measurement_bug(metrics_by_pj)`。
  **決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出**。
  ≥3 PJ で bit-exact 一致したら候補。0 同値は未測定・データ不足で正当に起きる（#423 既出）ため
  除外し FP を構造的に避ける。precision 優先は ADR-043 の方針と整合。
- `scripts/lib/audit/sections_measurement.py` 新規 builder を `_OBSERVABILITY_BUILDERS` 登録（ADR-028）。
  データ源は growth-state-*.json walk（rl-fleet status と同経路）。

## Acceptance Criteria
- [ ] 3 PJ 以上で同一の **非ゼロ** env_score が出たら surface・1-2 PJ 一致は無視
- [ ] 0 / 0.0 / None の一致は候補にしない（テストで明示 assert）
- [ ] markdown / 構造化両経路に伝播

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: なし（完全独立）。**PR サイズ**: 1 PR・~180 行。Closes #185。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #446 evolve に「今日の修正確認」phase を追加（前回以降の新規 weak_signal を idiom 単位 group 化・最大5件）  `[closed]`

## 背景
昇格経路が reflect SKILL Step 7.7 の散文ステップのみ → 昇格 0 件。
毎日叩かれる evolve に決定論 phase として移植する（learning_skill_md_must_not_enforcement）。

## 変更
- `scripts/lib/correction_semantic/daily_review.py` 新規: `build_review(pj_slug, max_groups=5, dry_run=)`。
  **既読キー集合に含まれない** 未昇格(channel=llm_judge・非expired)を idiom_key で group 化・
  頻度降順・上位5件。
- `correction_review_seen.jsonl` 新規ストア（PJ slug スコープ・store_registry 宣言）。
  **決定（設計 §機能#1・論点2）: correction_judged.jsonl と同方式の物理キー集合**
  （append-only・`{"key": signal_key, "decision": "promoted"|"rejected", ...}`）。
  detected_at 時刻 cursor 案は却下（同時刻シグナルの取りこぼし境界バグ）。
  313件規模・TTL 45日減衰の母集団ではキー集合肥大化は無視できる（数十 KB オーダー）。
  既読追記は **apply 時のみ**（dry_run は読むだけ）。
- `evolve.py`: weak_signals run_batch / ttl の後に `result["correction_review"]` を常時 emit。
- `skills/evolve/SKILL.md`: 新 Step（reflect Step 7.7 を移植）。
  `$OUT` の groups を AskUserQuestion で y/n（最大5問1バッチ）→「はい」を `rl-reflect --promote-weak`。
  **エッジケース分岐を明記**: Skip/Other/中断でも evolve は完走（design §2.1）。dry_run は表示のみ。

## Acceptance Criteria
- [ ] 新規 0 件なら `eligible=False, groups=[]` を emit（AskUserQuestion を出さない）
- [ ] 「いいえ」で既読集合に decision="rejected" 追記・「Skip」は追記しない（次回再提示）
- [ ] promote 部分失敗時に該当 group を既読集合に追記しない（取りこぼし防止）
- [ ] 既読集合の重複追記が read 側 set 化で無害であること（冪等性テスト）
- [ ] dry_run でファイル不変（実 PJ E2E）
- [ ] reflect SKILL Step 7.7 は残す（後方互換）か evolve 移植に伴い deprecate 注記（頭判断）

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: #442（expired 除外）, #443（bootstrap キー共有）。**PR サイズ**: 2 PR
（PR1: daily_review + 既読集合 + evolve emit、PR2: SKILL.md ステップ + AskUserQuestion 分岐）。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #447 human-confirmed idiom に一致する新規 weak_signal を idiom_dict で自動昇格（daily cap + surface + 取り消し付き）  `[closed]`

## 背景
人間が一度承認したパターンを毎回確認させるのは非効率。confirmed idiom に一致する新規シグナルは
機械再適用する。**決定（ADR-047）: HUMAN_SOURCES に重み 1.0 で追加**（0.8 割引・advisory 並走は
却下 — フェーズ表示の整数性 / 体験ゴールの遅延。FP リスクは安全弁3点で吸収）。
**現 313 idiom は全件未確認なので confirmed=True が立つまで一切発動しない**（雪崩防止）。

## 変更（PR1: 自動昇格本体）
- `correction_idioms.jsonl`: `confirmed` / `confirmed_at` / `confirmed_by` / `revoked_at` 追加
  （store_registry 更新）。
- #446 の review で「はい」確定時に該当 idiom を `confirmed=True` 化（daily_review or promote 側）。
- `scripts/lib/correction_semantic/idiom_autopromote.py` 新規:
  `autopromote(pj_slug, daily_cap=, dry_run=)`。
  confirmed（かつ未 revoke）idiom 集合に `idiom_key` 一致する新規未昇格を
  `promote_signals(source="idiom_dict")` 昇格。**daily_cap 件で打ち切り**、超過分は
  `capped` として返し次回 run に持ち越す。
- `provenance_weight.HUMAN_SOURCES = frozenset({"reflect_confirmed", "idiom_dict"})`（重み 1.0）。
  昇格レコードに `promoted_by="idiom_dict"` + `idiom_key` を残す。
- `evolve.py`: daily_review の後に `result["idiom_autopromote"]` を常時 emit。

## 変更（PR2: 安全弁3点の配線）
- **安全弁①**: userConfig `idiom_autopromote_daily_cap`（number・デフォルト 10）を
  `.claude-plugin/plugin.json` に追加（既存項目と同じフラット number + description 粒度）。
- **安全弁②**: `sections_weak_signals.py` builder（ADR-028）に
  「本 run の idiom_dict 自動昇格 N 件（idiom 一覧）」行を追加。毎 evolve/audit で必ず surface。
- **安全弁③**: `rl-reflect --revoke-idiom <idiom_key>` 新規 CLI。
  confirmed=False + revoked_at に戻し、該当 idiom_key 由来の `promoted_by="idiom_dict"`
  corrections を `invalidated=True` に原子的 rewrite。`count_human_corrections` は
  invalidated を除外（フェーズ進捗が正しく巻き戻る）。weak_signals の promoted=True は
  維持（再提示しない）。

## Acceptance Criteria（最重要）
- [ ] **confirmed 未設定の現状で `autopromote` が promoted=0 を返す**（実 PJ dry-run E2E で assert）
- [ ] confirmed=True の idiom にだけ一致して昇格する
- [ ] daily_cap 超過分が昇格されず `capped` で surface される
- [ ] idiom_dict 昇格が `count_human_corrections` に 1.0 でカウントされる（フェーズ進捗が動く）
- [ ] `--revoke-idiom` 後、invalidated 分が `count_human_corrections` から除外される（進捗巻き戻り）
- [ ] revoke 済み idiom は autopromote の対象から外れる
- [ ] 自動昇格が observability 両経路（markdown / 構造化）に surface される
- [ ] dry_run でファイル不変

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: #446（confirmed 化）。**PR サイズ**: 2 PR
（PR1: autopromote 本体 + HUMAN_SOURCES + cap ロジック ~300 行、PR2: userConfig + surface + revoke ~250 行）。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #448 evolve レポート末尾に成長状態を決定論表示（あと N 件で次フェーズ / 今日の昇格成果）  `[closed]`

## 背景
成長レベル/フェーズ/進捗バーは出ているが「あと何件で次フェーズか」「今日の昇格成果」が無い。
ユーザーが「進化している実感」を毎日得られるようにする。

## 変更
- **閾値の単一ソース化（決定・論点4）**: `growth_engine.py` に
  `STRUCTURED_CORRECTIONS_TARGET = 10`（+ sessions/rules 閾値）をモジュール定数として切り出し、
  `detect_phase` / `compute_phase_progress` 内のリテラルを置換（挙動不変・snapshot で確認）。
- `scripts/lib/growth_report.py` 新規: `build_growth_report(...)`（決定論）。
  閾値は **ハードコードせず growth_engine の定数を import**（二重実装の片直し事故 = #419 の轍を
  構造的に防ぐ。閾値変更時に growth_engine だけ直せば判定とレポートが同時追従）。
- `evolve.py`: audit phase 後に `result["growth_report"]` を top-level emit。
- `skills/evolve/SKILL.md` Step 9: `growth_report.lines` を成長レベル表示直後に列挙。

## Acceptance Criteria
- [ ] `corrections 7/10 — あと3件で構造化育成へ` 形式の行が出る
- [ ] `今日の確認で idiom N 件が自動化対象に昇格` が #446/#447 の結果から決定論で出る
- [ ] corrections が閾値到達済みなら「達成・次フェーズ条件は sessions/coherence」を表示
- [ ] growth_report に閾値リテラル（10 等）が直書きされていない（growth_engine 定数の import のみ）
- [ ] growth_engine の定数切り出しで既存フェーズ判定の挙動が不変（snapshot 回帰）

## 共通チェックリスト（PR ごと）
- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）

**依存**: #446（daily_review 結果参照）, #447（idiom_autopromote 結果参照）。**PR サイズ**: 1 PR・~200 行。

---

設計: docs/evolve/daily-evolve-reward-loop-design.md / ADR: docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md（commit 6b1425df）

---

## #449 agent-brushup: agent frontmatter の exact model ID pin を決定論検出する  `[closed]`

## 背景

モデルティア委譲体制の正式化（2026-06-12、`~/.claude/rules/model-routing.md`）に伴い、agent 定義の `model:` frontmatter は **エイリアス（opus/sonnet/haiku/fable）必須** とした。

exact model ID（例: `claude-opus-4-8`）を pin すると、新モデルリリース後も古いモデルに固定されたまま気づけない **silent stale** が起きる。実際に global agents 4件（design-review / refactor-engineer / senior-engineer / reviewer-san）で発生していた（手動で修正済み）。これは `rl-fleet plugins` が検出する「version 無しプラグイン silent stale」と同型の問題。

## 提案

`agent-brushup`（`scripts/agent_quality.py`）に決定論チェックを1つ追加:

- agent 定義（global `~/.claude/agents/*.md` + PJ `.claude/agents/*.md`）の frontmatter `model:` を走査
- 値が exact model ID パターン（`claude-[a-z]+-[0-9]` 等）なら **stale リスクとして警告**（エイリアスへの置換を提案）
- 値がエイリアス（opus/sonnet/haiku/fable/inherit）または未指定なら OK

## Acceptance Criteria

- [ ] exact ID pin の agent が agent-brushup レポートに警告として surface される（ファイルパス + 現在値 + 推奨エイリアス）
- [ ] エイリアス指定・未指定の agent は警告されない（false positive なし）
- [ ] 検出パターンが将来のモデル名にも頑健（ハードコードされたモデル名リストでなくパターンマッチ）
- [ ] 単体テスト（LLM 呼び出しなし・fixture ベース）

## 参考

- 設計議論: モデルティア委譲体制（HEAD=fable / HARD=opus / NORMAL=sonnet / MECH=haiku）
- 関連 pitfall: version 無しプラグイン silent stale（MEMORY.md）

---

## #457 test: フルスイートが1時間超 — run_evolve 系テストの実環境ストア読みを隔離して数分に短縮する  `[closed]`

## 症状（実測 2026-06-12）

フルスイート（`pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/`）が **1時間超**かかる。

- `skills/evolve/scripts/tests/test_evolve_batch_guard.py` の `run_evolve()` を呼ぶテストが **1件 60〜300秒**（単独実行で 67.93s 実測、スイート内ではさらに遅化）
- 進捗 77% 時点で経過約 50 分・FAILED 0（遅いだけで壊れてはいない）

## 根因仮説

`run_evolve(project_dir=tmp_path)` でも evolve パイプラインが**実環境のグローバルストア**（`~/.claude/projects` ≈ 9925 jsonl / 1.9GB 規模、DATA_DIR 配下）を走査している疑い。既知 pitfall「テストがグローバル状態を読む」（test衛生 pitfall / transcript-store-bench ルール）の再発形。

**注意: 着手時にまず実測で根因を確定すること**（プロファイル or 走査パスのログ確認）。仮説のまま fixture を書かない。

## 打ち手（効果順）

1. **根治**: 該当テストの fixture で DATA_DIR / transcript 走査先を tmp に完全隔離（読み先の環境変数 or resolver を monkeypatch）。1件300秒 → ms 級を狙う
2. **実測**: `--durations=20` で TOP20 を取り、batch_guard 以外の伏兵も特定・同様に隔離
3. **slow マーカー**: 残った真に遅いテストへ `@pytest.mark.slow` + 日常/PR ゲートは deselect、リリース前のみフル実行（運用は CLAUDE.md のテスト節に追記）
4. pytest-xdist 並列化は順序依存 pitfall（sys.modules 汚染・importlib.reload 等の前科）誘爆リスクがあるため①〜③の後に別判断

## Acceptance Criteria

- [ ] 根因を実測で特定し issue にコメントで記録（どのストアを何件読んでいたか）
- [ ] `test_evolve_batch_guard.py` 全件が各 5 秒未満で pass（実環境ストア読みゼロ）
- [ ] フルスイートの wall time を before/after 実測で記録（目標: 10分未満）
- [ ] 隔離 fixture は他テストへ波及可能な共通 conftest 化を検討（dry-run で DATA_DIR 書き込みゼロ assert の既存パターンに揃える）
- [ ] 既存テストの意図（batch guard の検証内容）を変えない

## 関連

- 既知 pitfall: テストのグローバル状態読み（#407/#408, test衛生）、transcript ストア規模破綻（#28）
- 発見経緯: #449 (PR #454) のマージゲートでフルスイートを直列実行した際に顕在化

> 💬 comment:
>
> 根因の実測記録（acceptance criteria 1）:
> 
> **何を読んでいたか**: `run_evolve(project_dir=tmp_path)` の後段 post-processing フェーズ（utterance_archive ingest / prune global skill check / weak_signals 言い直し検出 / correction_semantic）が `Path.home()/.claude/projects` ≈ **9925 jsonl / 1.9GB** を default 走査。ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR) 隔離は `Path.home()` 由来パスに効かず素通り。
> 
> **何秒消費していたか**: cProfile 実測で HOME 非隔離 **8.69s/件** → 隔離 **0.32s/件**。フルスイート内では cold cache 等で 24〜38s/件に膨張し、`test_evolve_batch_guard.py` 6件だけで 182.92s。別根因として `test_compaction_rebuilds_bloated_db` の per-row INSERT 60000行が 43s。
> 
> **結果**: フルスイート 1956.84s (32:36) → **58.09s**（約34倍）。修正は PR #459。

---

## #460 [tech-eval] rl-fleet recall に [[link]] 1-hop 展開を追加（Organize then Retrieve 部分採用）  `[open]`

## 概要

arXiv 2606.11680 (Organize then Retrieve: 階層メモリ検索) の tech-eval（2026-06-12 daily report 照合）で特定したギャップ。
rl-anything の memory は**保存側は階層化済み**（MEMORY.md index → fact file → `[[link]]` 相互リンク）だが、**検索側 `scripts/lib/fleet/recall.py` はフラット TF スコアリングのみ**で階層を活用していない。hit した fact の `[[link]]` 先を 1-hop 展開して併記すれば、決定論のまま「芋づる想起」が手に入る。

## Before / After（ユーザー体験）

- **Before**: `bin/rl-fleet recall "duckdb checkpoint"` がキーワード一致 fact を単発で返す。関連 memory（リンク先 pitfall 等）は人が `[[link]]` を見て手で開く
- **After**: hit fact の `[[link]]` 1-hop 先が `↳ linked:` として併記され、文脈が一度で揃う ✨

## 既存実装との差分

- `scripts/lib/fleet/recall.py:114` `_score()` — TF + desc/name boost のフラットスコア。MEMORY.md index 行は penalty 0.5（dedup 意図）
- `recall.py:59` `parse_fact_file()` — frontmatter は読むが本文中の `[[name]]` リンクは抽出していない
- 設計制約: **ADR-025（vector/gbrain 非採用・決定論キーワード検索）と整合させること**。リンク辿りは決定論なので方針内。LLM rerank は引き続き入れない（recall.py:6 の設計コメント踏襲）

## 実装方針（案）

1. `parse_fact_file` で本文の `[[name]]` を抽出し `Fact.links` に保持
2. hit 確定後、同一 PJ 内で `name` slug → fact file を解決し 1-hop 先を `linked` として hit に添付（スコア対象にはしない／展開はトップ N hit のみ・深さ 1 固定で爆発防止）
3. `format_hits` で `↳ linked:` 行を出力（--json は `linked: [...]` フィールド）
4. dangling link（未作成 memory への `[[link]]`）は無視（memory 規約上 error ではない）

## 配線先（enforcement surface）

`bin/rl-fleet recall`（オンデマンド CLI）。recall は「思い出したい瞬間」に叩く性質のツールなので**手動発火が本来形**であり、recurring ループ（evolve/audit）への配線は不要と判断済み。

## 採用後の確認方法

- [ ] `bin/rl-fleet recall "duckdb checkpoint"` → hit した fact に `↳ linked:` で `[[link]]` 先（関連 pitfall 等）が併記される
- [ ] `bin/rl-fleet recall "duckdb checkpoint" --json` → 各 hit に `linked` フィールドが付く
- [ ] dangling link を含む fact で recall してもエラーにならない

## 再評価条件

- memory ファイル数の増加で単発 hit の文脈不足が体感される時（現時点では推奨度「中」＝急がない）
- recall の hit 精度への不満が corrections に現れた時

## 参照

- arXiv 2606.11680 — Organize then Retrieve: 効率的エージェントのための階層メモリ探索
- ADR-025（vector/gbrain 非採用、決定論 recall）
- tech-eval レポート: ai-daily-report 2026-06-12 照合（7概念中 5実装済み・1不適合・本件のみギャップ）

---

## #461 feat(audit): ADR-046 重み昇格レディネスの決定論判定（outcome_promotion_readiness）  `[closed]`

## 背景

ADR-046 は outcome 3軸（correction 再発率 / 一発成功率 / rework 率近似）の environment fitness 重み昇格について「2〜4週 advisory 並走 → 分布実測 → 昇格判断」と定め、昇格の分布条件を3つ定義した:

1. **分散が十分** — 軸の値が全 PJ で同値でない（全 PJ 同値 = 測定バグ強シグナル、learning_measurement_layer_diagnosis）
2. **データ件数下限** — 分母が下限（暫定 correction≥10 / sessions≥30）を満たす PJ が複数ある
3. **方向の妥当性** — env 改善イベント（reflect/evolve 適用）の前後で軸が期待方向へ動く相関が実測で見える

しかし **この3条件を測る機構が存在しない**。判断期日（2026-06-24〜07-08 頃）に人間が分布を目視して勘で判断することになり、ADR-044 の学び（しきい値は実コーパス適用前に確定できない）を運用面で取りこぼす。

## 提案

決定論チェッカー `outcome_promotion_readiness`（LLM 非依存）を追加し、audit の observability contract（ADR-028 `_OBSERVABILITY_BUILDERS`）に登録する:

- 条件1: fleet 横断で軸値の同値判定（既存 `measurement_bug`（#445）の bit-exact 同値検査と同じ思想・流用可否を検討）
- 条件2: PJ ごとの分母実測値 vs 下限のテーブル
- 条件3: `evolve_decisions` ストア（ADR-041）の apply イベントを anchor に、前後窓で軸値を比較（窓幅は実データ dry-run で決定 — ADR-044 準拠）
- 各条件を ✓/✗ + evidence（実測値・PJ 名）で surface。**3条件すべて ✓ になったら「重み昇格を提案」行を出す**（適用判断は従来通り人間の y/n）

## Success Criteria

- 実 PJ の現状データで条件2/3 が ✗（データ不足）として evidence 付きで正しく出る
- 合成 fixture で3条件 ✓ → 昇格提案行が markdown / 構造化の両経路に surface される（ADR-028 契約）
- dry-run でストア非書込（pitfall_dryrun_stateful_store_write）

## 関連

ADR-046 / #423 / #429（outcome 3軸 advisory 実装）、#433（ランキング配線・残スコープあり）、#445（measurement_bug）、ADR-041（evolve_decisions）、ADR-028（observability contract）

---

## #462 feat(correction_semantic): confirmed idiom の PJ 横断優先提示（cross-PJ 確認集約）  `[closed]`

## 背景（実測 2026-06-12）

- weak_signals 318 件の PJ 内訳: figma-to-code 116 / amamo 48 / rl-anything 47 / atlas-breeaders 24 / receipt 20 …
- idiom_autopromote（#447, ADR-047）の照合は **pj_slug × idiom テキスト単位**。「git status じゃなくて git diff」のような全 PJ 共通の修正癖でも、PJ ごとに別々の y/n 承認が必要
- 人間確認の帯域（daily_review 最大5件/日 × idiom_autopromote daily_cap 10）が律速のため、同義 idiom の PJ 数ぶんの重複確認はスループットを直接削る

## 提案（自動昇格はしない — ADR-047 不変条件1の維持）

ある PJ で `confirmed=True` になった idiom と **正規化テキスト一致**する他 PJ の未確認 idiom を:

1. daily_review / bootstrap_backlog の提示順で **先頭に優先表示**し、「他 PJ（<slug>）で承認済み」ラベルを付ける
2. 人間が y を返せば当該 PJ でも confirmed 化（通常フローと同じ）— **1 idiom × N PJ の確認が実質 N 回の軽い y で済む**
3. 自動 confirmed 化・自動昇格は **しない**。ADR-047 の不変条件「人間が承認していないパターンは絶対に自動昇格しない」と物理接地（idiom_key = source_path:line_no ハッシュ）はそのまま維持。本提案は提示順と判断材料の改善のみ

将来 cross-PJ の revoke 率実測が十分溜まったら「2 PJ 以上で confirmed → 残り PJ は自動 confirmed」への昇格を別 ADR で判断する（ADR-046 と同じ「実測 → 昇格判断」レール）。

## Success Criteria

- 正規化テキスト一致の検出が決定論（LLM 非依存）で、similarity 既存実装の流用方針を明記
- confirmed idiom と一致する他 PJ idiom が daily_review 提示の先頭にラベル付きで出る（実データ E2E）
- 承認時に当該 PJ の idiom が confirmed 化され、以後その PJ で idiom_autopromote が効く
- dry-run でストア非書込

## 関連

ADR-047 / #447（idiom_autopromote）、#446（daily_review）、#443（bootstrap_backlog）、#431/#432（correction capture 二層化）、ADR-031（slug スコープ）。fleet 横断転移（方向性4）の最小スライス

---

## #463 bug(correction_semantic): confirm_idioms が本流から一度も呼ばれず idiom_autopromote が永久に0件（ADR-047 配線漏れ）  `[closed]`

## 症状（実測 2026-06-12）

ADR-047 / #446 / #447 の設計では「daily_review で人間が『はい』と承認 → idiom に confirmed=True → 以後同テキストの再発 weak_signal を idiom_autopromote が機械昇格」が中核ループ。しかし:

```
$ grep -rn "confirm_idioms" scripts/ skills/ hooks/ | grep -v tests
scripts/lib/correction_semantic/store.py:205:def confirm_idioms(   ← 定義のみ
```

- `confirm_idioms`（store.py:205）の**本流呼び出し元がゼロ**（テストのみ）
- evolve SKILL.md Step 6.1/6.2 にも `confirm_idioms` への言及なし（「はい」→ `rl-reflect --promote-weak` → `record_reviewed` のみ）
- `rl-reflect --promote-weak`（reflect.py:747）も `promote_signals` のみで confirmed 化しない

結果: **正規手順で y/n に答えても confirmed=True が一切立たず、idiom_autopromote は雪崩防止の不変条件（confirmed 0 件 → promoted=0）により永久に 0 件**。v1.97.0 の目玉機能が構造的に dead code。

learning_skill_md_must_not_enforcement / learning_install_is_not_enforcement と同族の「実装はあるが配線が無い」ギャップ。ADR-047 の Test Plan は confirm_idioms を単体で検証しており、**呼び出し配線の欠如はテストで検出できていなかった**。

## 暫定処置（実施済み）

2026-06-12 の初回 bootstrap（rl-anything、30件承認）では、provenance（source_path+line_no）突合で signal_key→idiom_key を解決し `confirm_idioms(confirmed_by="bootstrap_review")` を手動呼び出しで補完済み（confirmed 30件）。**他 PJ の今後の daily_review では再発する。**

## 修正案

1. **配線**: 昇格と confirmed 化を1経路に束ねる。推奨は `rl-reflect --promote-weak` 内で promote 成功後に当該 signal の idiom を confirmed 化する（SKILL の散文に新ステップを足すのではなく CLI に寄せる — ADR-045 の enforcement 原則と同じ「tool 文脈に閉じる」）。signal→idiom の対応は provenance（source_path+line_no）突合（本 issue の暫定処置と同じロジック）をライブラリ化
2. **回帰テスト（E2E）**: daily_review「はい」相当のフロー実行後に (a) corrections +N、(b) 当該 idiom confirmed=True、(c) 同テキストの新規 weak_signal 投入で idiom_autopromote が promoted≥1 を返す、まで通しで assert（閉ループ E2E — learning_dryrun_verification_blind_spot 準拠）
3. **SKILL.md 追従**: Step 6.1/6.2 の記述を CLI 一本化後の手順に更新

## Success Criteria

- 正規フロー（CLI 経由）の承認だけで confirmed=True が立つ
- confirmed 後の再発シグナルで autopromote が実発火する閉ループ E2E が緑
- dry-run でストア非書込

## 関連

ADR-047 / #447（idiom_autopromote）、#446（daily_review）、#443（bootstrap_backlog）、ADR-045（enforcement は tool 文脈に閉じる）、#462（cross-PJ 優先提示 — 本修正が前提）

---

## #464 bug(tests): test_audit_snapshot が corrections_insights の import 時 Path.home() 固定で実 corrections.jsonl を読む（order-dependent・実機10件超で顕在化）  `[closed]`

## 症状（2026-06-12 実測）

フルスイート実行で `test_generate_report_empty_snapshot` / `test_generate_report_populated_snapshot` が snapshot mismatch で fail（単体実行では pass する order-dependent）。差分は「## 繰り返し失敗パターン TOP-5: `semantic_idiom` — 30 回」— **実 DATA_DIR の corrections.jsonl を読んでいる**。

## 根因

`scripts/lib/corrections_insights.py:27` が **import 時に `Path.home()` を解決して `CORRECTIONS_FILE` モジュール定数に固定**する。`_isolate_env` の `monkeypatch.setenv("HOME", ...)` は import 後では効かず、フルスイートでは先行テストが実 HOME で import 済みのため隔離が貫通しない（pitfall_resolver_marker_reads_real_home と同族）。

今日まで潜伏した理由: 実 corrections が 9 件で表示閾値 `MIN_DISPLAY_RECORDS=10` 未満 → セクション自体が出ず緑。2026-06-12 の bootstrap 初回転で 39 件になり閾値を初めて超えて顕在化（データ状態依存の潜伏 — pitfall_dict_get_none_and_nondryrun_latch と同パターン）。

## 修正

`_isolate_env` の既存パターン（outcome_metrics.DATA_DIR / measurement_bug.DATA_DIR 等の setattr 固定）に合わせ、`corrections_insights.CORRECTIONS_FILE` を tmp に差し替える（`load_corrections_for_insights` は call 時にモジュール属性を読むため import 順に依らず効く）。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## #468 bug(process): ドキュメント上のフルスイートコマンドが scripts/lib/tests/（1111件）を収集していない  `[closed]`

## 症状（実測 2026-06-12）

CLAUDE.md「テスト」節のフルスイートコマンド:

```
python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ -v
```

これは `scripts/lib/tests/` を**収集しない**（`--collect-only -q | grep -c 'scripts/lib/tests'` → 0）。`scripts/lib/tests/` には 1111 件のテストがあり（correction_semantic / weak_signals / audit 系の単体テストの大半）、明示的にパス指定したときしか走らない。

PR #465-#467 のマージゲートで頭がフルスイートを直列実行した際に発見。歴代のマージゲート・#457 の「フルスイート約1分」計測も、この 1111 件を含んでいなかった可能性が高い（learning_install_is_not_enforcement と同族の「コマンドはあるが網羅の保証が無い」ギャップ）。

## 修正案

1. `pytest.ini` に `testpaths = hooks skills scripts/tests scripts/rl/tests scripts/lib/tests` を追加し、bare `pytest` で全件が走るようにする（コマンド列挙への依存を根治）
2. CLAUDE.md テスト節のコマンドを `python3 -m pytest`（testpaths 依存）に簡約 or `scripts/lib/tests/` を追記
3. 取りこぼしの再発防止: 「tests/ 配下にあるのに canonical コマンドで収集されないディレクトリ」を audit の決定論チェックに追加することを検討（orphan_store #422 と同思想の静的突合）

## 参考実測

- canonical コマンド: 3605 passed（2026-06-12 main）
- `scripts/lib/tests/` 単独: 1111 passed / 16.67s — 追加コストは軽微

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## #469 feat(audit): outcome_promotion_readiness の session 系分母を session_store union read（DuckDB + 未 ingest jsonl）で実効化  `[closed]`

## 背景（#461 実装時の発見・PR #467 の concerns より）

sessions.jsonl は #415 で DuckDB（sessions.db）へ ingest 済みのため live jsonl が存在しない。一方 #461 の `outcome_promotion_readiness` は ADR-046 が canonical とする sessions.jsonl を読むため、**条件2（sessions≥30 の分母）/ 条件3（apply 前後窓の paired session）の session 系軸が現状ほぼ常に空**になる。

実データ dry-run（2026-06-12）でも条件3 は `no_paired_windows`（anchors=2 はあるが paired session 0）で、データ不足でなく**読み経路の構造問題**として ✗ になっている。

## 提案

- session 読みを session_store の union read（DuckDB sessions.db + 未 ingest の live jsonl）に切り替える
- outcome_metrics（#423）側も同じ読み経路なら同時に揃える（二重実装しない）
- ADR-046 の判断期日（2026-06-24〜07-08 頃）までに入れないと、レディネス判定が「永遠に ✗」のまま昇格判断の役に立たない

## 関連

#461 / PR #467（outcome_promotion_readiness）、#415（utterance/session DuckDB ingest）、ADR-046

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## #471 テスト隔離の defense-in-depth 強化 3 件（/review opus finding）  `[closed]`

2026-06-12 の /review（opus subagent 2体）で #461-#464 のマージ後 diff から出た P2 強化提案。いずれもバグ修正でなく #464 同型バグの再発防止。

## 1. 観測ビルダーの隔離漏れ構造ガード (confidence 8/10)
`scripts/tests/test_audit_snapshot.py:107-117` の #464 修正はモジュール個別の setattr（場当たり）。`_isolate_env` は手動列挙 7 モジュールで、import 時に `Path.home()`/DATA_DIR を固定する規約のまま新ビルダーを足すと snapshot が実データを読む事故が再発する（今回が4回目の同型）。
→ `_OBSERVABILITY_BUILDERS` 登録モジュールが module-level `DATA_DIR`/`CORRECTIONS_FILE` 等を持つのに `_isolate_env` で上書きされていない場合に fail する構造ガードテストを追加。

## 2. dry-run テストの byte 照合強化 (confidence 7/10)
`scripts/lib/tests/test_outcome_promotion_readiness.py:343-353` はファイル名集合の同一性しか assert しておらず、既存ファイルへの追記・書換を見逃す。
→ before/after で `read_bytes()` 照合に強化（`test_promote_weak_confirm_dry_run_writes_nothing` に正パターンあり）。

## 3. scripts/lib/tests/ conftest の autouse HOME/DATA_DIR 隔離 (confidence 6/10)
`scripts/lib/tests/conftest.py` の autouse は `evolve_decisions.MARKER_ROOT` のみ隔離。新規テストが setattr を忘れると実 `~/.claude` を読む（#464 を生んだのと同じ潜在ギャップ）。
→ autouse fixture で HOME setenv + DATA_DIR の tmp 隔離をデフォルト化（実パスが必要なテストは marker でオプトアウト）。

## 参考（休眠 P2・対応不要メモ）
- `outcome_promotion_readiness.py` の window 照合が ISO 文字列の辞書順比較（現状全行 +00:00 で無害）
- `_resolve_session_pj` の token-containment fallback が他PJ誤紐付けの可能性（advisory のみ）
- `scripts/lib/audit/gstack.py:13` の `_FLOW_CHAIN_FILE` も import 時 Path.home() 定数（snapshot では mock 済み）

---

## #476 [Feedback] バグ報告: correction系カウンタの不整合と bootstrap/daily の二重提示  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: evolve (correction_review / observability)
**満足度**: 3/5

## 詳細

同一の evolve レポート内で correction 系の件数が3系統で食い違い、レビューフローに二重提示がある。

1. **カウンタ不整合**: `correction_capture` は「直近30日 capture 率 0%（報酬入力が枯渇の可能性）」と警告する一方、`outcome_metrics` は「窓内 correction 39件 / distinct type 3」、`weak_signals` は「llm_judge channel で 313件捕捉」と報告。測定 channel/store が違うだけだとしても「枯渇」警告は llm_judge が大量捕捉している実態と矛盾する。
   - 改善案: correction_capture を channel 別表示（hook N件 / llm_judge M件）にして誤警告を解消する

2. **件数のスコープ混在**: weak_signals の 318/288 はグローバル集計、bootstrap の pj_total 13 は PJ 集計。ラベルなしで同じレポートに並ぶため桁の食い違いに見える。各行に (全PJ)/(当PJ) の明示を。

3. **bootstrap/daily の二重提示**: daily の5グループが bootstrap groups に signal_key 単位で全包含されていた。SKILL.md 手順通り Step 6.1（bootstrap まとめて確認）→ Step 6.2（daily）を実行すると同じシグナルを2回質問することになる。`daily.remaining: 8` も実体は bootstrap の残りグループで「前回以降の新規」ではない。
   - 改善案: bootstrap が is_bootstrap=true で発火する run では daily から bootstrap-pending のシグナルを除外（または daily 自体をスキップ）

4. **growth_report が対話前スナップショットのまま**: 分析時点で `promoted_today: 0` が確定し、その後の対話で複数件昇格してもレポート表示値は更新されない。「今日の確認で N件が自動化対象に昇格」の行が構造的に常に0になる。`corrections_human 0/10` と prune の `corrections kept 39` の関係も説明がなく、何を数えて0なのか読み取れない。
   - 改善案: drain/promote 後に growth_report を再計算する手順にするか、promote CLI が更新後カウントを返して assistant が表示する

---
*Submitted via /rl-anything:feedback*


---

## #477 [Feedback] バグ報告: remediation の scope分類不整合・却下の非永続化（重複提案）・既知FP拡充  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: remediation / audit
**満足度**: 3/5

## 詳細

evolve の remediation フェーズで分類・べき等性・既知FPの問題を確認した。

1. **scope 分類の不整合**: `impact_scope: "global"` の item（ユーザーグローバル rules 配下の line_limit_violation）が `proposable_custom_individual` に振り分けられ、同時に集計は `proposable_global: 0` だった。SKILL.md 上 global scope は「参考値・対応不要」のはずが、個別承認の AskUserQuestion としてユーザーに提示される。パス由来の custom/global 判定と impact_scope 判定と振り分けロジックが不整合。

2. **却下の非永続化 → 重複提案**: 個別承認フローでユーザーがスキップ/却下した提案を記録する仕組みがない（merge の add_merge_suppression や triage ledger に相当するものが不在）。「重複した提案を行ってはならない（MUST NOT）」のべき等性原則に反し、次回 evolve で同じ提案が再出する見込み。
   - 改善案: remediation 用 suppression ledger（issue の dedup_key 単位、TTL 付きでも可）を追加

3. **行カウント基準が不明 + confidence 過剰**: 実ファイル40行超の rule を `lines: 11 / limit: 10` と報告（コンテンツ行のみのカウント？）。何を数えているかレポートから読み取れない。また「1行超過」に confidence 0.95 が付くのは超過幅を考慮しておらず過剰。超過率で confidence をスケールすべき。

4. **既知FPパターンの拡充**: ドキュメント用スキルのコードブロック内に意図的に記載された AWS ARN を hardcoded_value（conf 0.75）として個別提案に上げてきた。doc 文脈の ID は既知の FP 系統。glossary の jargon 候補に PDF/QA 等の汎用略語が並ぶのも同系統。known_fp_patterns に「markdown コードブロック内の ARN/ID」「汎用略語 denylist」を追加してほしい。

---
*Submitted via /rl-anything:feedback*


---

## #478 [Feedback] バグ報告: Skill呼び出しが usage registry に乗らず prune zero_invocation が構造的FP  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: prune / telemetry / skill_triage
**満足度**: 3/5

## 詳細

1. **Skill 呼び出しが usage registry に乗っていない疑い**: evolve レポートの「Usage (last 30 days)」に Agent:* の発火のみが並び、PJ 固有スキルは全件 `trigger_count: 0` だった。直近1週間に内容更新され実際に参照・使用されているスキルも 0 のままで、Skill tool 経由（または rule 経由の暗黙参照）の発火が usage registry に記録されていない可能性が高い。

2. **帰結: prune zero_invocation が構造的 FP**: 上記により prune の zero_invocation 候補（今回 custom 3件）が構造的に false positive 化する。今回は SKILL.md 全文 + git log の個別調査で全件「オンデマンド型=keep」と判定できたが、候補抽出の根拠データ自体が壊れていると毎回の調査コストが無駄になる。skill_evolve の insufficient_usage（usage_count==0 で保留）判定にも同じデータが使われており影響が広い。
   - 確認観点: Skill tool 発火が usage-log に流れる経路の有無、`rl-usage-log` 手動呼び出しに依存していないか

3. **skill_triage の CREATE 候補が surface されない**: trajectory 由来の新スキル候補（CREATE）が remediation の低confidence batch_skip の1行に畳まれ、実質ユーザーに提示されない。evolve SKILL.md に triage の CREATE/UPDATE/SPLIT/MERGE を表示する Step が存在しない（skip_suppressed_summary の1行 surface のみ規定）。triage 結果のサマリ表示 Step を追加してほしい。

---
*Submitted via /rl-anything:feedback*


---

## #479 [Feedback] バグ報告: evolve SKILL.md 記載と実体の乖離（importパス・所要時間目安・fitness文言矛盾）  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: evolve (SKILL.md docs / fitness)
**満足度**: 3/5

## 詳細

evolve SKILL.md の記載と実装の実体が乖離している箇所が3つ。

1. **import パス誤り（実行時エラー）**: Step 6.1/6.2 は `bootstrap_backlog.mark_done(slug, dry_run=dry_run)` / `daily_review.record_reviewed(...)` と記載するが、実体は `correction_semantic` パッケージ配下（`from correction_semantic import bootstrap_backlog, daily_review`）。記載を素直に import すると ModuleNotFoundError になる（実際に発生）。Step 6.5 の auto_memory_broker のような sys.path 設定込みの完全なコード例を Step 6.1/6.2 にも載せてほしい。

2. **所要時間目安が stale**: Step 1 に「フル dry-run の所要時間目安をユーザーに伝える（MUST）: large ≈ 8〜20分」とあるが、実測は約34秒だった（観測161件・skills+rules 64件の large 環境）。ADR-037 で audit/skill_evolve が LLM-free（cache 読み）化されて以降、見積もりが一桁以上乖離しており、ユーザーに誤った待ち時間を案内させる。tier 別目安の再校正か、直近実測値の表示への置き換えを提案。

3. **fitness 文言の3箇所矛盾**: 同一 run で
   - Step 2: `has_fitness: true`（PJ 固有 fitness 関数生成済み）を表示
   - fitness_evolution の next_action: 「このPJでは fitness は使わない設計。対応不要（提案が構造的に出ないため母集団は貯まらない）」（structural_reason: skill_evolve_not_scored）
   - calibration_drift: 「accept/reject データ不足 2/30 — あと28件」と蓄積前提の文言
   が並ぶ。「使わない設計」が正なら Step 2 の fitness 生成提案フロー・calibration の「あと N件」表示と整合しない。structural_reason がある場合は3箇所の文言を統一（生成提案の抑制 / calibration 行を「構造的に対象外」へ切替）してほしい。

---
*Submitted via /rl-anything:feedback*


---

## #484 [seam] weak_signals 決定論3チャネル（manual_edit_after_ai / esc_interrupt / rephrase）が実PJで一度も永続化されていない  `[closed]`

## 事象
weak_signals.jsonl の channel 分布は `llm_judge` 313件 + `permission_deny` 5件（テスト用一時dir由来 `pj_slug=tmpdcm8avo8`）のみ。設計4チャネル（#432）のうち決定論3チャネルが**全PJ通算0件**で、reflect の `--show-weak-signals` 昇格フローに決定論シグナルが届いていない。

## 証拠（read-only 実測）
- 同一検出コードを実走: `weak_signals.batch.run_batch('rl-anything', dry_run=True)` → `{'detected': {'permission_deny':5, 'esc_interrupt':9, 'manual_edit_after_ai':8, 'rephrase':1}, 'written':18, 'skipped_dup':5}` — **18件が今この瞬間「未保存」**（既存 store に無い）
- 検出経路は健全: slug 解決一致（`pj_slug_from_cwd`='rl-anything'、evolve.py:1179 `_resolve_pj_slug` と同値）、transcript 60件取得成功

## 反証済みの仮説
dedup で消えた説（dry-run が written:18 を返す）/ slug 不一致説（一致を実測）/ rotated 退避説（rotation ファイル無し）/ 意図的設計説（store_registry・evolve.py:1172-1180 とも4チャネル書込を明記、ADR にスキップ記載なし）— すべて否定。残る説明は「非 dry-run evolve が実PJで run_batch の書込に到達していない」= #478 型のデータ不流通。

## 修正案
- 非 dry-run evolve 経路で `run_batch` の書込が landed するかをトレースし根因特定
- apply 境界をまたぐ store 差分 assert の E2E 回帰を追加（「dry-run検証の盲点」#400 と同型の予防）

出典: 繋ぎ目調査 A+B（2026-06-12）。severity: HIGH

---

## #485 [seam] usage-registry.jsonl の writer 条件が永久 False — bare スキル名 vs パス前置判定の schema 不一致で Scope Advisory が構造的に空  `[closed]`

## 事象
global スキル使用を per-PJ 集計する `usage-registry.jsonl` が一度も書かれていない（ファイル MISSING、旧 plugin-data dir / rotated にも無し）。audit の `scope_advisory`（global スキルの配置助言）が常に空を返す。

## 証拠
- writer: `hooks/observe.py:71` `is_global_skill()` = `tool_input.get("skill").startswith("~/.claude/skills/")`（パス前置判定）
- 実データ: usage.jsonl の `skill_name` は **bare 名**（`commit` 42回 / `research-best-practices` 28 / `ship` 23 …）→ 前置一致が常に False
- これらは実在の global skill（`~/.claude/skills/` 配下確認済み）なのに registry に乗らない
- reader: `audit/orchestrator.py:155-156` → graceful に `{}` を返すためクラッシュせず気づけない
- テスト `test_global_skill_registers`（test_hooks_observe.py:138）は `skill=f"{prefix}/my-global"`（パス形）の合成入力で緑 = 「合成 fixture の false confidence」pitfall の実例

## 補足
skill_activations 経由の prune/audit global 判定は別途生きている（全滅ではなく cross-PJ Scope Advisory のみ死）。

## 修正案
`is_global_skill` を「bare 名 → `~/.claude/skills/<name>/SKILL.md` 存在チェック」に変更。テストも実 CC が渡す bare 名形に直す。

出典: 繋ぎ目調査 A+B（2026-06-12）。severity: HIGH（データ欠損系）

---

## #486 [seam] backfill スキルが丸ごと壊れている — SKILL.md が #215 で削除済みの CLI 3本を実行指示、CLAUDE.md/SPEC.md は今も初回セットアップとして案内  `[closed]`

## 事象
`skills/backfill/SKILL.md` の Step1/2/3 が指示する `rl-backfill`（:30）/ `rl-backfill-reclassify`（:46, :78）/ `rl-backfill-analyze`（:86）が**3本とも command-not-found**。`disable-model-invocation: true` のユーザー明示起動スキルなので、新規PJ導入時の最初の体験が全コマンド失敗になる。

## 証拠
- `which` / `--help` 実行 → 3本とも NOT FOUND。`bin/` の executable 15本に含まれず（`bin/rl-backfill-turn-indices` は別物）
- commit `5c5c6ef9`（v1.65.1, #215）で「ソース .py 削除済みのデッドラッパーを削除」として3ファイル削除済み
- reclassify サブコマンド / `--include-reclassified` の実体もツリーに存在せず（grep 0件）
- install.sh / 代替エントリポイント無しを確認（反証失敗）
- CLAUDE.md:89 / SPEC.md:43 は今も backfill を「初回セットアップスキル」として案内

## 修正案
backfill の取り込みは v1.96.0 以降 session_store / utterance_archive の batch ingest（hooks 自動 + `bin/rl-fleet ingest`）に置き換わっている。
- (a) SKILL.md を現行の取り込み経路（`rl-fleet ingest` 等）に書き換える、または
- (b) スキル自体を deprecated 化して案内文を差し替える
- いずれの場合も SPEC.md / CLAUDE.md の backfill 記述を同期更新

出典: 繋ぎ目調査 C（2026-06-12）。severity: HIGH（#479 同種・CLI レベル）

---

## #487 [seam] agent-brushup Step1 が CLI・Python フォールバック両方とも動かない（main 関数不在 + sys.path 不足）  `[closed]`

## 事象
`skills/agent-brushup/SKILL.md` Step1 の指示が両経路とも実行不能:
- CLI: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lib/agent_quality.py scan "$(pwd)"`（:32）— `agent_quality.py` に `if __name__ == "__main__"` が **0個**（サブコマンド処理が存在しない幻の CLI）
- フォールバック: `from agent_quality import scan_agents, check_quality, check_upstream`（:38）— sys.path 設定なしで `ModuleNotFoundError: No module named 'lib'`

## 証拠（実行確認済み）
- `agent_quality.py:17-18` が `from frontmatter import` + `from lib.agent_quality_catalog import` を実行 → 解決には `scripts/` と `scripts/lib` の**両方**が path に必要
- `PYTHONPATH="scripts/lib:scripts"` を通して初めて import OK を実測
- bin にラッパー無し。conftest が `scripts/` を path に入れるため**テストは緑**（テスト緑・実運用赤の典型）

## 修正案
Step1 を cleanup/evolve-skill と同型の sys.path 付き Python ブロックに統一。ただし `sys.path.insert(0, .../scripts/lib)` だけでは `lib.X` が解決しないため、`scripts` も path に足すか、`agent_quality.py` 側の `from lib.X` を `from X` に直す（モジュール内部の絶対 import 整理が根治）。

出典: 繋ぎ目調査 C（2026-06-12）。severity: HIGH

---

## #488 [seam] prune SKILL.md の `from scripts.prune import` が ModuleNotFoundError — #479 と完全同型の修正漏れ  `[closed]`

## 事象
`skills/prune/SKILL.md` Step4（:126）と Step5（:159）が sys.path 設定なしで `from scripts.prune import archive_file, check_import_dependencies, SkillDependencyError` / `from scripts.prune import restore_file, list_archive` を実行指示。verbatim 実行で `ModuleNotFoundError: No module named 'scripts.prune'`（実行確認済み）。

## 証拠
- 実体は `scripts/lib/prune/`（パッケージ）。`scripts/__init__.py` は存在しないため `scripts.X` 形式は構造的に不可
- 関数/クラスは全て `scripts/lib/prune/__init__.py` で re-export 済み — 名前は実在、パスだけが誤り
- `sys.path.insert(0, .../scripts/lib)` + `from prune import ...` で OK を実測
- shim 不在を確認（`find scripts -name 'prune*'` は `scripts/lib/prune` のみ）

## 修正案
evolve（#479 修正）/cleanup と同じく `_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd(); sys.path.insert(0, os.path.join(_root, "scripts", "lib"))` を前置し `from prune import ...` に直す。

出典: 繋ぎ目調査 C（2026-06-12）。severity: HIGH（#479 同型クラスの残存個体）

---

## #489 [seam] outcome_metrics 3軸が全PJ集計をラベルなしで当PJレポートに表示 — 一発成功率 全PJ0.73 vs 当PJ0.88 の15pt乖離を実測  `[closed]`

## 事象
行動アウトカム3軸（`correction_recurrence` / `first_try_success` / `rework`）が corrections.jsonl / sessions(db+jsonl union) を **project フィルタなし**で読み、当PJの evolve/audit レポートに表示される。読み手は当PJの数値と誤認する。#476 と同型のスコープ取り違え。ADR-046 重み昇格判断の判断材料汚染リスクあり。

## 証拠（read-only 実測、30日窓）
- `outcome_metrics.py:132-137`（corrections 無フィルタ）、`:175-180` / `:230-235`（sessions 無フィルタ）
- first_try_success: **全PJ 3698/5059=0.73 vs 当PJ 1588/1813=0.88**（15pt 乖離）
- sessions 母集団: 全PJ 7740 / 当PJ 2827。correction_recurrence 母集団: 全PJ 45 / 当PJ 30

## 反証の試み
docstring は「project_dir に依存しない」と書くが「全PJ集計である」と読み手に開示していない。同作者の `outcome_promotion_readiness` は per-PJ 分解 + ADR-046 ラベルで全PJ性を明示 = 開示すべきという設計判断が既にある。無ラベルは漏れと判断。

## 修正案
(A) project フィルタを足し当PJスコープにする（growth_report と整合、RL報酬の目的変数として筋）、または (B) セクションに「（全PJ集計・advisory）」ラベル明記。最低限 (B) は必須。

出典: 繋ぎ目調査 D+H（2026-06-12）。severity: HIGH

> 💬 comment:
>
> ## スコープ追加: sections_capture.py の capture 率も同型（全PJ集計の無ラベル表示）
> 
> 事後の同型残存スキャン（2026-06-12）で **本 issue と完全同型の未発見インスタンス**を1件確認。本 issue のスコープに含めて一緒に修正する。
> 
> ### 発見内容
> 
> `scripts/lib/audit/sections_capture.py:90` の `build_capture_rate_section` が `compute_capture_rate(usage_file=, corrections_file=)` を **`project=` 引数なし**で呼んでいる。
> 
> - `compute_capture_rate` 自体は `project` フィルタ（`_project_match`、`capture_rate.py:80`）を**持っている**のに渡していない
> - 結果: active sessions / captured / capture% が**全PJ集計のまま当PJ audit レポートに無ラベル表示**される
> - ミスリード構造が悪質な点: 同じセクション内の `llm_judge` 行は `_llm_judge_count` が `resolve_slug()` で**当PJ限定**なので、**当PJ値と全PJ値がラベルなしで併置**される（#490 の16倍食い違いと同型）
> - docstring（L74-75）は「project_dir には依存せず環境グローバルを読む」と意図を明記しているが、これはまさに本 issue が指摘する設計問題そのもの
> 
> ### 修正方針（本体と同じ2択）
> 
> - (A) `project=` を渡して当PJスコープにする（推奨。フィルタは実装済みなので1行）
> - (B) 最低限「（全PJ集計）」ラベル明記
> 
> ### 受け入れ基準（追加）
> 
> - capture 率セクションの数値が当PJスコープであるか、全PJ集計であると明示されていること
> - llm_judge 行とのスコープ不一致が解消または明示されていること

---

## #490 [seam] weak_signals セクションの未昇格件数・昇格導線が全PJ集計（282件）なのに daily_review は当PJのみ（17件）— 16倍の食い違い  `[closed]`

## 事象
`sections_weak_signals.py` の `total` には #476 で「（全PJ集計）」ラベルが付いたが、同セクションの `by_channel` 内訳（:113-120）・`unpromoted` 件数（:108-109）・「未昇格 N 件は evolve の今日の修正確認 phase で昇格可能」の導線文（:122-123）は全PJ集計のまま。実際に昇格する `daily_review` は当PJ slug でフィルタ（daily_review.py:153）するため、「282件昇格できる」と読めるが当PJは17件しか出てこない。

## 証拠（実測）
- weak_signals.jsonl: total 318 / 当PJ 47。unpromoted 全PJ **282** vs 当PJ **17**
- `sections_weak_signals.py:108`（無フィルタ unpromoted）、`:122`（全PJ件数に当PJ昇格導線）
- `daily_review.py:153` `if r.get("pj_slug") != pj_slug: continue`

## 修正案
unpromoted と昇格導線文を daily_review と同じ pj_slug 突合でフィルタした当PJ件数に変える。total は「（全PJ集計）」のまま残し「うち当PJ未昇格 M 件が昇格可能」と併記。

出典: 繋ぎ目調査 D+H（2026-06-12）。severity: HIGH（#476 の部分修正の残り）

---

## #491 [seam] dry-run の evolve が3箇所で書き込む（pending marker / audit-history / episodic.db）+ 再発予防の SHA256 不変 E2E が欠落  `[closed]`

## 事象（3件とも隔離環境での実測 SHA 比較で確定）
`run_evolve(dry_run=True)` の「1バイトも書かない」契約が3箇所で破れている:

### 1. evolve_decisions の pending marker（最重・二方向違反）
`emit_decisions(dry_run=True)` が候補ありなら marker を**作成**、候補なしなら既存 marker を**削除**。
- `scripts/lib/evolve_decisions.py:262-275` — queue は gate 済みだが marker ブロックは dry_run 判定なし（コメント L267 が「dry-run でも書く」と意図的明記。ただし marker は apply→drain 待ちポインタなので dry-run で立てるのは機能的にも誤り）
- 実測: dry-run で `~/.claude/rl-anything/evolve_pending/<slug>.json` added / 事前 seed が削除。戻り値 `persisted: false` が誤誘導
- 既存テスト `test_emit_dry_run_does_not_write` は `read_queue()==[]` しか見ず MARKER_ROOT を patch しない = 構造的に検出不能。MARKER_ROOT は `Path.home()` 固定のため conftest の HOME 隔離が実環境汚染を偶然マスクしている

### 2. audit phase が audit-history.jsonl + evolve-state.json を無条件更新
- `skills/evolve/scripts/evolve.py:627-639`（guard なし）→ `audit/orchestrator.py:134 run_audit`（**dry_run 引数が存在しない**）→ `_record_audit_completion`
- dry-run 連打で audit-history が伸び last_audit_timestamp が進む = 劣化検出の基準が動く副作用

### 3. episodic.db が read 経路で物理生成
- `episodic_store.py:60-65 _connect` が read API（query_relevant）でも mkdir + duckdb.connect + CREATE TABLE を実行し空 DB を materialize

## 修正案
1. marker ブロックを `if not dry_run:` で gate + 戻り値に `marker_written`/`marker_cleared` + MARKER_ROOT を patch する回帰テスト
2. `run_audit(dry_run: bool=False)` を追加し `_record_audit_completion` を gate、evolve から `dry_run=dry_run` を渡す
3. read API は `db_path.exists()` でなければ connect せず空返し
4. **共通の根本予防**: `run_evolve(dry_run=True)` 前後で隔離 HOME+DATA_DIR の全ファイル SHA256 不変を assert する E2E を1本追加（これが3件すべてをすり抜けさせた共通原因）

出典: 繋ぎ目調査 F（2026-06-12）。severity: HIGH(1) + MEDIUM(2,3)。pitfall_dryrun_stateful_store_write（#308）の同系統

---

## #492 [seam] PJ slug 導出が2系統混在 — 同じ weak_signals ストアを worktree切り方式で書き git-common-dir方式で読む（時限式 silent mismatch）  `[closed]`

## 事象
slug 導出関数が2系統あり、同一ストアの read/write で別方式が使われている。調査 D+H と E+G が**独立に同じ根**を発見:
- (a) `optimize_history_store.resolve_slug`（git-common-dir 親 basename）= sections_capture / spec_trigger / triage_ledger / evolve_decisions / **SKILL.md の apply 側（mark_done / record_reviewed）**
- (b) `utterance_archive.pj_slug_from_cwd`（`/.claude/worktrees/` 切り詰め basename）= weak_signals batch / daily_review / bootstrap（via evolve.py `_resolve_pj_slug`）

## 具体的な危険箇所
1. **weak_signals ストア**: (b) で書き (a)（sections_capture._llm_judge_count、#476 fixup）で読む。現 worktree レイアウトでは偶然一致して無害だが #440 が「2方式は worktree で食い違う」と明記済み。さらに sections_capture は `resolve_slug()` を引数なし=cwd で呼ぶため、evolve が `project_dir != cwd` で起動されると別PJ slug を解決する
2. **bootstrap marker / daily seen-store**: phase 側 build は (b)、SKILL.md の apply（mark_done/record_reviewed、SKILL.md:381-382/412-413）は (a)。repo の **subdirectory から実行**すると割れ、`bootstrap_done-<A>.marker` と `<B>.marker` が別ファイル → bootstrap 永久再提示・daily 既読除外が不発

## 修正案
- 短期: SKILL.md の apply 呼び出しを「phase 出力の `result.correction_review.bootstrap.slug` / `.daily.slug`（= phase が実際に read に使った slug）」を渡す方式に変更（read/write 同一 slug を構造的に保証）
- sections_capture._llm_judge_count を書込側と同じ slug 関数に揃え `project_dir` を引数で受ける
- 中期: slug 導出を1関数に単一ソース化（pitfall_worktree_slug_show_toplevel / #440 の恒久解）

出典: 繋ぎ目調査 D+H（MEDIUM #3）+ E+G（LOW #3）（2026-06-12）。severity: MEDIUM（現状無害・時限式）

> 💬 comment:
>
> ## スコープ追記: 読み側正規化では復元不能な「書込時 basename 固定」の根治（#489 レビューからの送り込み）
> 
> #489 の修正（2026-06-12）で読み側フィルタは `pj_slug_from_cwd` ベースの worktree 安全正規化に統一したが、**書込側の情報欠落**が残っている:
> 
> - `sessions.jsonl` / `usage.jsonl` の `project` フィールドはフルパスでなく **basename のみ**で書かれる（hook 書込時に `feedback` / `bots` 等の worktree 名で固定）
> - 実測: usage.jsonl に `feedback` 46件 / `bots` 45件（rl-anything / sys-bots の worktree セッション由来）
> - フルパスが無いため読み側では本体 repo 名に復元不能 — これらのレコードは当PJフィルタから恒久的に漏れる
> 
> ### 本 issue での根治方針
> 
> slug 導出の1関数化と同時に、**hook 書込側の `project` を `pj_slug_from_cwd(cwd)` 由来の正規化済み slug に統一**する（worktree cwd でも本体 repo 名が書かれるように）。既存の basename 固定レコードは遡及不能なので、移行日をコード内定数（#478 の `USAGE_RECORDING_FIX_DATE` と同型）で記録し advisory 表示するのが望ましい。

---

## #493 [seam] evolve result の top-level キー群（correction_review / growth_report / idiom_autopromote 等）が schema 契約の対象外 — #375 の保護が新レーンに届いていない  `[closed]`

## 事象
`evolve_result_schema.py` の CANONICAL は `phases.*` のみ登録。#442-#448 で大量追加された top-level キー（`correction_review.bootstrap` / `correction_review.daily` / `growth_report` / `evolve_decisions.pending` / `weak_signals` / `idiom_autopromote` / `utterance_ingest`）は契約に1件も無い。SKILL.md は top-level path を7箇所（173/360/392/551/567/586/670行）で読んでいる。

## 証拠
- `evolve_result_schema.py:63-136` — 全 Key が `phases.` 始まり。COVERED∪UNCOVERED 完全性テストも `result["phases"]` のみ走査（test_evolve_result_schema.py:246-262）
- `extract_documented_paths` の regex が `phases\.` 固定 → documented_path_drift も top-level を拾えない
- check_conformance 実測 violations=0（top-level を見ていないだけ）

## 影響
キー名 rename / kind drift（例 `daily.eligible`→`enabled`、`idiom_autopromote.promoted` int→dict 化）が契約テスト0件で素通りし、SKILL.md reader が**静かに空表示**になる。#375 が phases で解いた drift の新レーンでの構造的再発。

## 修正案
- CANONICAL を top-level path も登録できるよう一般化（`correction_review.daily.eligible` 等）
- `extract_documented_paths` を「canonical 登録済み任意 top prefix」へ拡張
- 最低限 `correction_review` / `growth_report` / `idiom_autopromote` の reader 必須キーを登録し、phase 完全性テストと同型の top-level 完全性テストを追加

出典: 繋ぎ目調査 E+G（2026-06-12）。severity: MEDIUM

> 💬 comment:
>
> ## 範囲補足: 契約外の top-level キーは本文列挙分より多い（全列挙）
> 
> 事後の同型残存スキャン（2026-06-12）で evolve.py が書く top-level キーを全列挙した結果、本文列挙分（correction_review / growth_report / evolve_decisions / weak_signals / idiom_autopromote / utterance_ingest）に加えて以下も契約外:
> 
> `correction_semantic` / `env_tier` / `env_tier_reason` / `next_action` / `observability` / `observe_first` / `self_analysis` / `sessions_ingested` / `skipped_heavy_phases` / `trigger_summary` / `warnings` / `weak_signals_ttl`
> 
> 別 issue は不要（修正案の「top-level 完全性テスト追加」が入れば同一根で一括カバーされる）。実装時は **CANONICAL への登録対象を上記全件まで広げる**こと。特に `weak_signals_ttl` / `observability` は SKILL.md reader が読む実害キー。

---

## #494 [seam] SKILL.md 散文 MUST 依存の記録動作の決定論化 — record_rejection は fallback ゼロで永久消失、growth_report.promoted_today は構造的に常時0  `[closed]`

## 背景
SKILL.md の散文 MUST で「assistant が inline python を叩いて状態を記録する」動作6つを fallback 有無で棚卸しした（learning_skill_md_must_not_enforcement の体系適用）。

| 記録動作 | fallback |
|---|---|
| Step 7.8 accept/reject drain | ✅ restore_state.py の undrained 検出（#360-A/#402） |
| Step 6.5 auto-memory drain | ✅ キュー残留→次回 drain |
| Step 6.1 mark_done / Step 6.2 record_reviewed | △ 次回再提示で回収可（ただし「却下」の記録漏れは毎回再提示） |
| reflect promote-weak/episodic | △ 次回再提示 |
| **Step 5.5 record_rejection（remediation suppression）** | **❌ なし — 取りこぼすと却下が記録されず、#477 が解いた「同じ提案が毎回再出」が再発** |

## 発見1: record_rejection の fallback 欠如（MEDIUM）
dry-run 時は記録しない仕様のため、標準フロー（--dry-run 分析→対話）では特に取りこぼしやすい。**完全に安全網が無い唯一のレーン**。
- 修正案: Step 7.8 と同様の drain/marker 化、または run_evolve が却下 issue を CLI 引数で受けて決定論記録する

## 発見2: growth_report.promoted_today が構造的に常時0（MEDIUM）
`growth_report.py:60-64` は `_daily.get("promoted")` を読むが、`daily_review.build_review` の返り値（daily_review.py:328-335）に `promoted` キーは存在しない。実 promote は emit 後の Step 6.2 で起きるため**コード上必ず0**。補正は SKILL.md Step 9 の散文 MUST（#476-4）のみで、assistant が忘れると stale な「corrections N/10」と reflect 昇格分が落ちた表示になる（autopromoted_today は live で正しい = 半 stale）。
- 修正案: (a) 返り値に `promoted_today_is_snapshot: True` を明示し SKILL.md が必ず override 分岐に入る、(b) 根治は Step 6.2 完了後に growth_report を再生成する2相化

出典: 繋ぎ目調査 C + E+G（2026-06-12）。severity: MEDIUM

---

## #495 [seam] LOW 軽微一括 — MessageDisplay 不発 / TTL の cross-PJ write / audit・discover・reflect の doc stale  `[closed]`

繋ぎ目調査（2026-06-12）で見つかった LOW 4件の一括 issue。個別に急がないが棚卸しとして記録。

## 1. MessageDisplay hook が不発（store 未作成）
- `message_display.jsonl` = MISSING（rotated 含め）。writer は hooks.json:215 に登録済み。当環境の CC で MessageDisplay イベントが発火していない疑い
- disposition=keep_future なので reader 破綻なし。対応: 発火する CC version か確認し、不発なら登録を畳むか registry note に「未発火」追記

## 2. weak_signals TTL `mark_expired` が当PJ evolve 中に全PJの expired を書き換える
- `ttl.py:93-105` が slug フィルタなしで全件 read→全件 rewrite。detected_at ベースで冪等なため機能上は無害だが、「当PJ操作が他PJストアを書き換える」原則違反 + 並行 evolve 時の rewrite 競合リスク
- 対応: 当PJ slug のみ expired マークに限定、または append-only な expired レーン化

## 3. doc 軽微 stale 3点
- `skills/audit/SKILL.md:75-80` — `from skill_usage_stats import` 例に sys.path 前置なし（補助スニペット・主経路は rl-audit CLI で完結）
- `skills/discover/SKILL.md:64` — 「verification_catalog.py」表記が stale（現在はパッケージ、re-export で機能は正常）
- `skills/reflect/SKILL.md` — Usage に `--revoke-idiom` 未記載（flag 自体は実在・機能正常）

出典: 繋ぎ目調査 A+B / D+H / C（2026-06-12）。severity: LOW

> 💬 comment:
>
> 追加 LOW（後片付け中に発見）: テストが実 /tmp に `rl-anything-last-skill-sess-proj-002.json` を漏らしていた（2026-06-12 フルスイート実行時刻と一致）。writer は `scripts/lib/rl_common/workflow.py:89-92 last_skill_path` = `TMPDIR` env または /tmp。設計自体は意図的（cross-process の ephemeral marker）だが、last_skill 系テストの一部が TMPDIR を tmp_path に monkeypatch していない。該当テスト（hooks/tests/ の last_skill 利用箇所）に TMPDIR 隔離の autouse fixture を足す。

> 💬 comment:
>
> ## スコープ追加: evolve-skill/SKILL.md にも sys.path 不足ブロック3件（dogfood gate Layer3 が検出）
> 
> PR #503 でマージした通し評価ゲートの実機1周（Layer 3: SKILL.md コードブロック抽出実行）が、本 issue 記載の `audit/SKILL.md:75` と**同クラスの未発見インスタンス**を3件検出した:
> 
> - `skills/evolve-skill/SKILL.md:62` — `skill_evolve` を sys.path 設定なしで import
> - `skills/evolve-skill/SKILL.md:118` — 同上
> - `skills/evolve-skill/SKILL.md:129` — 同上
> 
> いずれもモジュールは `scripts/lib/` に実在するが、ブロックが `sys.path.insert` を持たないため素の起動で ModuleNotFoundError。修正パターンは #487/#488 と同じ（`CLAUDE_PLUGIN_ROOT` 解決 + `sys.path.insert(scripts/lib)` 前置）。
> 
> 本 issue の対応時に audit/SKILL.md:75 と合わせて4件まとめて修正すること。修正後は `bin/rl-dogfood-gate --layer 3` が緑になることが受け入れ基準。

---

## #496 [seam] 通し評価ゲートの新設 — 「テスト緑・evolve無エラー・でも成果物がバグだらけ」を構造的に防ぐ実環境 dogfood E2E  `[closed]`

## 背景（ユーザー所感がそのまま問題定義）
> 継ぎ接ぎだらけの追加機能をして、一旦その機能はテストが通って evolve やってもエラーが出ないけど、実際にユーザーが evolve やってレポートとか成果評価するとバグめっちゃある。通しで評価する何かがあれば防げそう

繋ぎ目調査（#484-#495 の18件）の逆算で、既存テスト 4826 件が構造的に検出できない4つの抜け道が確定:
1. **配線の死**: テストは関数を直接呼ぶため「本流から呼ばれるか」を検証しない（#478, #484）
2. **合成 fixture の自己暗示**: 自作テストデータは自分の誤解を検出できない（#485）
3. **テスト環境の下駄**: conftest sys.path / HOME 隔離が本番との差分を作りそこにバグが住む（#487, #488, #491）
4. **間違った数字はエラーを出さない**: レポートの数値乖離はプログラム的に正常（#489, #490, #476）

## 提案: 3層の通し評価ゲート（リリース前 dogfood gate）
### 層1: 実環境 dogfood E2E
- 実PJで evolve 非 dry-run 1周 → 「書かれるべきストアの差分」を assert（weak_signals 4チャネル / usage / usage-registry / corrections …）。捕捉: #484 #485 型
- dry-run 1周 → 隔離 HOME+DATA_DIR 全ファイル SHA256 不変を assert。捕捉: #491 型（同 issue の修正案4と統合可）
- 前例: learning_dryrun_verification_blind_spot（#400）「完了基準は store 差分」の体系化

### 層2: 成果物の不変条件（report invariants）
- レポート数値同士の機械検査: 「当PJ件数 ≤ 全PJ件数」「『昇格可能 N 件』= daily_review が実際に提示する件数」「% の分母分子整合」「全PJ集計には必ず（全PJ集計）ラベル」等
- observability contract（ADR-028）の拡張として実装可能。捕捉: #476 #489 #490 型

### 層3: SKILL.md コードブロックの抽出実行
- 全 SKILL.md の python/bash ブロックを verbatim 抽出し subprocess で実行（read-only のものは実行、書込系は構文+import 解決まで）。conftest の下駄なし = ユーザーと同じ起動経路。捕捉: #479 #486 #487 #488 型

## 受け入れ基準（案）
- #484-#495 の各バグを「修正を revert した状態」でゲートに通すと検出されること（検出能力の実証）
- リリースフロー（commit-version.md の bump 手順）にゲート実行を組み込む

出典: 繋ぎ目調査 2026-06-12 の横断根因分析。関連: #491（SHA256 E2E）, #493（schema 契約拡張）

> 💬 comment:
>
> ## 運用知見: Layer1 は ambient write 混入で flaky になりうる（改善提案）
> 
> #491 マージ後の Layer1 実機確認で一度 `modified:evolve-state.json` の赤が出たが、再現実測（dry-run 前後のキー差分ゼロ）で **dry-run 自体は無実**と判明。原因はゲート実行中にライブセッションの hook（trigger_engine の `_save_state`）が evolve-state.json を書いた ambient write の混入。現設計（実 DATA_DIR を直接 snapshot diff）は「dry-run の書込」と「同時進行の hook の書込」を区別できない。
> 
> ### 改善提案（Layer1 の隔離コピー化）
> 
> 1. DATA_DIR を一時ディレクトリにコピー
> 2. `CLAUDE_PLUGIN_DATA=<コピー>` を環境変数で渡して dry-run evolve を実行（common.py は CLAUDE_PLUGIN_DATA 優先）
> 3. コピー側の SHA256 を比較
> 
> これで (a) ambient write の混入ゼロ（flaky 解消）、(b) dry-run バグがあっても実環境を汚さない、(c) 検出力は同等、の3点が同時に得られる。あわせて `skill-evolve-cache.json` / `constitutional_cache.json` は LLM 再呼び出し回避キャッシュとして意図された dry-run 書込（evolve-ops の cache warm 設計）なので、**文書化された cache 除外リスト**（bypass フラグではなく原則ベースの恒久除外）を snapshot 比較に持たせること。
> 
> 現状の実績: #491 修正後、Layer1 全緑（1a 不変 ✓ / ingest E2E 581 rows ✓）。

> 💬 comment:
>
> ## クローズ判断（司令塔）
> 
> 通し評価ゲートは運用可能な状態で完成。受け入れ基準と実績:
> 
> **実装済み（PR #503 + #515）**
> - Layer 1a: dry-run SHA256 不変 — **隔離コピー方式**（DATA_DIR コピー + CLAUDE_PLUGIN_DATA 隔離）で ambient write 偽赤を構造的に排除。文書化された三層除外（ファイル名 / dir prefix `evolve_pending/` / JSON キー `skill_type_cache`）
> - Layer 1 ingest E2E: 実PJ 581 rows（旧 test_real_pj_e2e 35秒をゲートに移設しフルスイート短縮に寄与）
> - Layer 2: report invariants（required_keys / non_negative / pj≤global / observability contract）
> - Layer 3: SKILL.md コードブロック抽出実行（素の起動経路・conftest 下駄なし）pass=74 fail=0
> 
> **検出力の実証（受け入れ基準「revert した修正を検出できること」の実運用版）**
> - 実機1周目で新規6件を即検出: sys.path 4件（#495 で修正・赤→緑を確認）+ observability contract drift 2件（#504 で修正）
> - #513 回帰の検証にも利用（marker 復元後の Layer1 緑で除外リスト動作を確認）
> 
> **最終確認（2026-06-12）**
> - フルスイート 4972 passed / 31.7s
> - `bin/rl-dogfood-gate --layer all` 全緑（1a ✓ / ingest ✓ / Layer2 4項目 ✓ / Layer3 74 pass）
> 
> **切り出した残課題**
> - #518 Layer 1b（非 dry-run store 差分）— #484 解決により前提解除、実装は follow-up
> - #517 evolve.py DATA_DIR の CLAUDE_PLUGIN_DATA 非対応（隔離コピー検出の盲点）— Layer 1b の先行依存

---

## #504 [seam] observability contract drift — result["observability"] に contract 未登録キー2件（constitutional / remediation_batch_skip）  `[closed]`

## 発見経緯

PR #503 の通し評価ゲート（`bin/rl-dogfood-gate --layer 2`）実機1周で検出。

## 内容

evolve result の `observability` セクションに、observability contract（ADR-028 の `_OBSERVABILITY_BUILDERS`、`scripts/lib/audit/observability.py`）に**未登録のキーが2件**出力されている:

- `constitutional`
- `remediation_batch_skip`

contract は「必ず surface すべき行の単一ソース」（markdown / 構造化の両経路に自動伝播）なので、result に出るキーが contract 側に無い = 成果物と契約の drift。#489/#490 と同クラス（成果物⇔契約の乖離）。

## 修正方針（どちらか）

1. **contract 拡張**: `constitutional` / `remediation_batch_skip` の builder を `_OBSERVABILITY_BUILDERS` に登録（surface すべき行なら）
2. **result 整理**: contract 経由でない ad-hoc 書き込み箇所を特定し、contract 経由に寄せる

どちらが正かは各キーの書き込み元（grep で特定）が「契約として必ず出すべき行か」で判断。

## 受け入れ基準

- `bin/rl-dogfood-gate --layer 2` の observability_contract チェックが緑
- 今後の新キー追加時に同 drift が即検出される状態の維持（ゲートが既にカバー）

関連: #496（ゲート本体）、ADR-028

---

## #513 [regression] PR #505 が evolve_decisions pending marker の dry-run 書込（#402 設計）をゲートし emit→drain 捕捉が全死  `[closed]`

## 事象

PR #505（#491 dry-run 非書込修正）が `emit_decisions` の pending marker 書込/削除を `if not dry_run:` 内にゲートしたが、**marker の dry-run 書込は #402 / ADR-041 の意図された設計**だった:

- 削除されたコードのコメント原文: 「#402: drain 検出用の運用マーカー（**dry-run でも書く**。store/queue とは別状態）」
- `write_pending_marker` docstring: 「emit が **dry-run でも書く**」「SessionStart の drain リマインドと `rl-evolve --drain` の pending ソース」

標準 evolve フローは `rl-evolve --dry-run` 分析のみ（#484 と同じ前提）なので、このゲートにより **emit→drain の決定論 accept/reject 捕捉（ADR-041）が全死**する: marker が書かれない → `drain_pending` 空振り → SessionStart リマインドも沈黙。

## 証拠

- `scripts/lib/tests/test_evolve_drain.py` が main（8b2e5df9 以降）で **6件 FAIL**（#402 設計を encode した契約テスト。PR #505 は targeted テスト範囲外で未検出 → フルスイートが Wave 4 まで未実行だったため顕在化が遅れた）
- 発見経緯: #484 worker の baseline 検証（範囲外発見として報告）

## 根因の根因

#491 の seam 調査がこの marker を「dry-run 書込違反」と判定したのは false positive。marker は skill-evolve-cache.json / constitutional_cache.json と同類の「**文書化された意図的 dry-run 書込**」（運用ポインタ）であり、SHA256 不変契約の原則ベース除外リストに載せるべきものだった。

## 修正方針

1. `emit_decisions` の marker block を `if not dry_run:` の外に戻す（#402 セマンティクス復元。`marker_written`/`marker_cleared` 返り値は維持）
2. `test_dry_run_no_write_e2e.py` の SHA256 不変 assert から `evolve_pending/` marker を文書化された除外として外す
3. dogfood gate Layer1 の除外リスト（#496 隔離コピー化で導入中）にも `evolve_pending/` を追加
4. `test_evolve_drain.py` 6件が緑に戻ることを確認

## 受け入れ基準

- `pytest scripts/lib/tests/test_evolve_drain.py -n 0` → 11 passed
- `pytest skills/evolve/scripts/tests/test_dry_run_no_write_e2e.py -n 0` → 緑（marker 除外を文書化した上で）

関連: #491, #402, #484, #496, ADR-041


---

## #517 evolve.py の module-level DATA_DIR が CLAUDE_PLUGIN_DATA 非対応 — 隔離コピー検出の盲点  `[closed]`

## 事象

`skills/evolve/scripts/evolve.py` の module-level `DATA_DIR` は `Path.home() / ".claude" / "rl-anything"` ハードコードで、`CLAUDE_PLUGIN_DATA` 環境変数を読まない（`scripts/lib/` 配下のモジュールは env 優先で解決する — common.py 同型）。`MARKER_ROOT`（evolve_decisions.py:41）も意図的に home 固定（hook/tool 合意のため、こちらは設計）。

## 影響

- dogfood gate Layer1 の隔離コピー方式（PR #515）は `CLAUDE_PLUGIN_DATA=<コピー>` で dry-run evolve を起動するが、**evolve.py 直書きの書込はコピーでなく実 dir に向かう**ため検出不能（現状 evolve.py 直書きの dry-run 書込は無いので実害ゼロ・将来リスク）
- Layer 1b（非 dry-run store 差分、#517）の隔離実行にも同じ制約がかかる

## 修正方針

evolve.py の DATA_DIR を `scripts/lib/common.py` と同じ env 優先解決に揃える。`EVOLVE_STATE_FILE` 等の派生パスも追従。テストは既存の `monkeypatch.setattr("evolve.DATA_DIR", ...)` 群が緑のまま、`CLAUDE_PLUGIN_DATA` 設定時に env 側へ向くことを assert。

出典: PR #515 worker の範囲外発見。pitfall_datadir_hook_tool_split ファミリー


---

## #518 dogfood gate Layer 1b: 非 dry-run store 差分チェックの実装（#484 解決により前提解除）  `[closed]`

## 背景

dogfood gate Layer 1b（非 dry-run store 差分 — 「書かれるべきものが書かれる」方向）は #496 Wave 0 で NotImplemented 枠のみ予約し「#484 修正後に実装」とした。#484 は PR #512 でマージ済み・実環境検証済み（`rl-evolve --drain` で written=18、決定論3チャネル初出現）なので前提解除。

## 実装内容

隔離コピー（PR #515 の `copy_data_dir_to_tmp` + `CLAUDE_PLUGIN_DATA` 伝播）を流用し、コピー側に対して `rl-evolve --drain` を実行 → store 差分で以下を assert:

- `weak_signals_persisted` が drain サマリに存在し dry_run=False
- weak_signals.jsonl の決定論チャネル書込（初回コピーとの差分 or 冪等2回目 written=0）
- evolve_decisions の marker→optimize_history 反映（accept/skip 分類）

## 依存・注意

- **#517 が先行依存**: evolve.py module-level DATA_DIR が CLAUDE_PLUGIN_DATA 非対応のため、現状では drain の書込の一部（evolve.py 直書き経路があれば）とマーカー clear（MARKER_ROOT は意図的 home 固定）が実環境に向かう。隔離実行の完全性は #517 解決後に確保される
- MARKER_ROOT の home 固定は hook/tool パス合意のための設計（evolve_decisions.py:41 コメント）— Layer 1b 側で test 用 override（env or 引数）を足す方向で検討

出典: #496 クローズ時の残課題切り出し。関連 PR #503, #512, #515


---

## #521 [evolve introspect] `discover` フェーズで例外: 'NoneType' object is not subscriptable  `[closed]`  (bug)

## 自己解析: 実行時エラー

evolve の `discover` フェーズ（phase）で例外が握り潰されていました。フェーズは `{"error": ...}` を格納するだけなので result は緑に見えます。

```
'NoneType' object is not subscriptable
```

このフェーズの try/except で原因を握り潰さず、root cause を修正してください。

<!-- rl-evolve-introspect:runtime_error:discover:nonetype-object-is-not-subscriptable -->



---

## #522 [Feedback] バグ報告: 検出レーンのデータ品質3件（triage confidence降格 / zero_invocation欠損データ / tool_usage誤パース・誤分類）  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: evolve（skill_triage→remediation / prune / discover）
**満足度**: 4/5

## 詳細

evolve の日次実行で確認した、検出レーンに乗るデータの信頼性に関わるバグ3件。

### 1. skill_triage CREATE の confidence が remediation issue 化で降格し、承認レーンに構造的に乗らない

- skill_triage の CREATE 候補は confidence **0.7** で出力されるが、remediation issue（`skill_triage_create` type）に変換されると confidence_score が **0.5** に固定降格する
- 結果、`partition_proposable_by_confidence`（しきい値 0.7）で常に batch_skip（デフォルトスキップ）落ちし、個別承認レーンに乗れない
- #478 で「CREATE 埋没厳禁」として Step 3.8 の surface 表示は義務化されたが、**表示されるだけで承認フローには永久に乗らない**構造が残っている
- 提案: issue 化時に triage の confidence をそのまま引き継ぐ（0.7 なら proposable_custom_individual に乗る）

### 2. zero_invocation 候補が usage 計測修正前の欠損データから生成される

- prune の zero_invocations 候補すべてに「usage 記録経路は 2026-06-12 に修正済み (#478)。この日以前のデータは欠損のため zero と断定不可」という advisory が付いた状態で候補として surface された
- 欠損と自覚しているデータを根拠に候補を出すと、SKILL.md の MUST（per-item 調査 + AskUserQuestion 個別承認）に従う限り、信頼できない根拠でユーザーに質問することになる
- 提案: 計測経路の修正日から観測窓（30日）が満ちるまで zero_invocation 判定自体を保留（suppress）し、「計測待ち N件（YYYY-MM-DD から判定再開）」と1行表示する

### 3. tool_usage_patterns の誤パースと誤分類

- **誤パース**: `VAR=value cmd` 形式の環境変数代入プレフィックスが cli_summary にコマンド名としてカウントされる（実測で `WT=...` が29回カウント）
- **誤分類**: 既存ルールで禁止済みのコマンドパターン（例: cd 禁止ルールがあるのに cd が626回観測）が、repeating_patterns で「スキル候補」として提案される。これはスキル化対象ではなく **rule installed ≠ enforced の違反観測**であり、observability の思想と最も相性が良い検出なのにレーンが存在しない
- 提案: (a) 変数代入をコマンド頭として扱わない (b) repeating pattern を既存 rules と突合し、ルール違反観測は別レーン（例: `rule_violation_observed`）で「ルールは導入済みだが実行が止まっていない → hook enforce を検討」として report する

---
*Submitted via /rl-anything:feedback*

---

## #523 [Feedback] バグ報告: evolve 自身の観測漏れ2件（self_analysis が実 stderr を捕捉せず「警告なし」と報告 / env_score が null で成長レベル演出が未発火）  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: evolve（self_analysis / world_context・growth_level）
**満足度**: 4/5

## 詳細

evolve が「自分自身を観測する」経路（Step 11 自己解析 / Report クライマックス）の配線漏れ2件。

### 1. self_analysis が実 stderr を捕捉せず「stderr 警告なし」と報告する

- フル dry-run 実行中、Chaos Testing が shadow コピー時に `.claude/worktrees/` 配下の **stale な agent worktree** のファイル不在で「Chaos Testing スキップ: [(src, dst, '[Errno 2] No such file or directory: ...'), ...]」という生 Python タプルの長大な stderr を出力した
- ところが同じ run の self_analysis.runtime_errors は「✓ 実行時エラー: フェーズ例外・observability 取得失敗・**stderr 警告なし**」と報告 — 実際に stderr が出ているのに「なし」と言っており、stderr キャプチャが self_analysis に配線されていない
- これは #299 で塞いだはずの「install ≠ enforcement」型の配線漏れが、自己解析自身に残っている例
- 提案:
  - (a) Chaos Testing の shadow コピー対象から `.claude/worktrees/`（agent worktree 残骸）を除外する
  - (b) rl-evolve 実行中の stderr を self_analysis.runtime_errors の入力に実際に配線する
  - (c) Chaos Testing のスキップ通知は生タプル dump ではなく「スキップ N 件（worktree 残骸）」の1行要約にする

### 2. env_score が null のまま成長レベル演出（Report クライマックス）が一度も発火しない

- 世界観ナレーションの Report クライマックスは「result JSON に env_score があれば compute_level → save_world_context → レベル表示」だが、複数回の evolve（直近2回）で env_score が null / world-context.json の current_level が null のまま
- env_score が算出される条件が不明で、演出のクライマックスが構造的に死んでいる可能性がある（初回 evolve から一度もレベルが表示されていない）
- 提案: env_score の算出条件をドキュメント化するか常時算出にし、null の場合は理由（どの入力が不足か）を result に surface する（silence ≠ evaluated を env_score 自身にも適用）

---
*Submitted via /rl-anything:feedback*

---

## #524 [Feedback] バグ報告: fixers_llm separation のリファレンス乖離（signature 未記載で TypeError）と絶対パス参照リンク生成  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: バグ報告
**コンポーネント**: evolve（remediation / fixers_llm）
**満足度**: 4/5

## 詳細

Step 5.5.1 の2相品質回復（rule の line_limit_violation → 分離）を実際に実行して踏んだ2点。

### 1. emit_separation_request の signature がリファレンス未記載で TypeError を踏む

- `references/remediation.md` の Phase A サンプルコードは `emit_compression_request(issue, original, limit)`（3引数）の例のみ
- 実際の `emit_separation_request` は `(issue, path, original_content, limit)` の**4引数**で、サンプルを流用すると `TypeError: missing 1 required positional argument` になる
- assistant がインラインで書くコードの手本がリファレンスなので、separation / split それぞれの実 signature 付きサンプルを追記してほしい（#379 の references doc-drift 検査対象としても拾えるはず）

### 2. emit prompt が参照リンクをマシン固有の絶対パスで指示する

- separation の Phase A prompt が「参照リンク: /Users/<user>/.../.claude/references/<name>.md」と**ユーザーホーム配下の絶対パス**で書き換えを指示してくる
- 分離対象の rule はリポジトリにコミットされるファイルなので、マシン固有の絶対パスを埋め込むと他環境・他メンバーで壊れる（今回は手で PJ ルート相対パス `.claude/references/<name>.md` に直して回避）
- 提案: 対象ファイルが project 配下の場合、emit prompt と ingest 検証の両方で PJ ルート相対パスを生成・許容する

---
*Submitted via /rl-anything:feedback*

---

## #525 [Feedback] 改善要望: evolve レポートの整合性・可読性・実行冗長性（文言矛盾3点 / TL;DR 不在 / global候補持ち回り / SLUG ボイラープレート）  `[closed]`  (feedback)

## フィードバック

**カテゴリ**: 改善要望
**コンポーネント**: evolve（report / SKILL.md / prune）
**満足度**: 4/5

## 詳細

evolve 日次実行のレポートを読み手目線でレビューした際の改善要望。

### 1. レポート内の文言が相互に矛盾して見える3点

- **fitness**: Step 2 で「fitness 関数生成済み ✅」と表示した直後に、Step 8 fitness_evolution の next_action が「このPJでは fitness は使わない設計。対応不要」。正確には「calibration の母集団が構造的に貯まらない」の意味だが、文言が fitness 全体を否定して読め、生成済み表示と衝突する。「calibration は対象外（fitness 関数自体は評価に使用中）」等に言い換えるべき
- **weak_signals**: observability が「当PJ未昇格 N 件は今日の修正確認 phase で昇格可能」と言う一方、同 run の daily phase は「新規なし（既読済）」。既読・却下済みのシグナルを「昇格可能」に数えており、件数の意味が噛み合わない。「未昇格 N 件（うち未読 0 件）」のように既読を分離して表示すべき
- **growth_report**: 「今日の確認で N 件が自動化対象に昇格」が、当該 run ではなく**同日の別セッション**の昇格を指す。SKILL.md は #476-4 で上書き表示の補正手順を持つが、根本は phase スナップショットを「今日の確認で」と呼ぶ文言の問題。「本日累計 N 件昇格（このrunでは 0 件）」のように出所を明示すべき

### 2. レポートに TL;DR がなく成果が埋もれる

- 18フェーズ・observability 14項目を全 surface する設計（silence ≠ evaluated）のため、クリーンな環境では「実際の変更2件」が長文に沈む
- 提案: レポート冒頭に「変更 N 件 / 要対応 N 件 / 残りすべて評価済みクリーン」の TL;DR を必須化し、全✓の observability 項目は「✓ クリーン: glossary / orphan_store / ...」のように1ブロックに畳む（評価済みであることは件数と項目名で担保）

### 3. 実行の冗長性2点

- **global prune 候補の全件持ち回り**: cross-PJ の global スキル候補（実測76件）を PJ evolve の result JSON に毎回全件積んでいる。PJ 文脈では件数1行 +「グローバル文脈の audit へ」誘導で十分で、全件データは JSON サイズと読み手の負担になるだけ
- **SLUG/OUT 再導出ボイラープレート**: SKILL.md の各 Bash ブロックで python ワンライナーによる SLUG 再導出 + OUT パス組み立てを繰り返す。rl-evolve 側は slug 解決済みなので、`rl-evolve --print-out-path` のような1コマンドを用意すれば SKILL.md の手順が大幅に短縮でき、assistant の書き損ね面も縮む（#402 の drain 1コマンド化と同型の改善）

---
*Submitted via /rl-anything:feedback*

---

## #526 [evolve 実体験] バグ束: #476-4 スコープ混同(41/10誤表示)・env_score サイレント消滅・None伝播未定義・count=null 契約不整合  `[closed]`  (bug)

## 概要

docs-platform での evolve 実体験（2026-06-12 run）で確認したバグ 4 件の束。各件とも実データで裏取り済み。

## 1.【確定】#476-4 の上書き指示が per-PJ / 全PJ のスコープ混同 → 「41/10」という嘘表示

SKILL.md Step 9 の #476-4 補正は「最後に実行した `rl-reflect --promote-weak` 出力の `corrections_human`（昇格後の最新値）で `corrections（human-confirmed のみ）{最新値}/10` を上書き表示」と指示している。しかし実測:

- `growth_report` の `0/10` = **docs-platform スコープ**（promote 前は実際に 0 件で正しい）
- `rl-reflect --promote-weak` の `corrections_human: 41` = **全PJ合計**（correction_idioms.jsonl の confirmed 内訳: rl-anything 30 / sys-bots 6 / docs-platform 5）

指示に従うと per-PJ メトリクスに全PJ値が混入し、assistant は「41/10」と表示してしまう（実際にやった）。正しい docs-platform 値は 5/10。

**修正案**: CLI が per-PJ の `corrections_human` を返す（`pj_slug` フィールドは既にある）か、SKILL の指示を「growth_report 値 + 今回昇格数の加算」に変更する。

## 2.【濃厚】env_score がサイレント消滅し成長レベルが表示されない

world-context.json には `current_level: 6`（2026-05-27 算出）が保存済みなのに、今回 run の result は `env_score: null` でレベル表示が出なかった。前回は算出できていたので退行。

「env_score が取得できない場合: 表示なし」という契約（references/report-narration.md）のせいで、**レベル計算が壊れても観測不能** — evolve 自身が掲げる silence != evaluated 原則への自己違反。discover クラッシュ（#521）の連鎖か env_score 計算自体の退行かの切り分けが必要。少なくとも「env_score: 取得失敗（前回 Lv.6）」のような degraded 表示を出すべき。

## 3.【濃厚】フェーズ失敗時の None 伝播が下流 Step で未定義（#521 の派生・仕様面）

#521（discover クラッシュ）の root cause とは別に、`discover` が `{"error": ...}` に落ちると `reflect_data_count: None` が下流に流れる。SKILL.md Step 6 / Step 10.1 は `reflect_data_count >= 5` の比較前提で **None の分岐が未定義**。assistant がアドリブで「取得不能」と書くしかなかった。フェーズ失敗時の degraded-mode 表示（「discover 失敗のため reflect 件数 不明」等）を SKILL 契約に明記すべき。

## 4.【軽微】fitness_evolution.count が null で「N/30件」表示契約が満たせない

SKILL.md Step 8 は insufficient_data 時に「`Fitness Evolution: データ不足（N/30件）` の1行を添える」と要求するが、structural_reason=skill_evolve_not_scored の場合 `count` フィールドは `null` で N が埋められない。count を 0 で埋めるか、structural ケースは件数行を省く仕様にするか、どちらかに揃えてほしい。

---
環境: docs-platform / rl-evolve 2026-06-12 run / dry-run 分析 → 対話適用の標準フロー


> 💬 comment:
>
> 全小項目の実装完了を確認しクローズ（逐条照合済み）:
> - #526-1 (SKILL Step 9 の per-PJ/全PJ スコープ混同是正) → #535 (ef2c28ee)
> - #526-2 (構造化 env_score の surface + degraded 表示) → #530 (236df40d)
> - #526-3 (discover 失敗時の None 伝播 degraded 分岐) → #530 (236df40d)
> - #526-4 (fitness_evolution.count structural ケースの件数行省略) → #535 (ef2c28ee)
> フルスイート 5083 passed で回帰なし。

---

## #527 [evolve 実体験] idiom 過汎用 FP: 「気がする」等の短文字日常語が confirmed 化され idiom_autopromote の誤昇格トリガーになる  `[closed]`  (bug)

## 概要

過汎用な短文字 idiom が confirmed 化され、idiom_autopromote（#463: confirmed idiom と同テキスト再発は機械昇格）の FP 製造機になるリスク。docs-platform の 2026-06-12 bootstrap 消化で実際に発生。

## 実測

`correction_idioms.jsonl` の confirmed=True 41 件中、6 文字以下の極短 idiom:

```
いやいや / じゃなくて / ないよ / わかりずらい / 完結に / 比率だけ / 気がする / 照合し直して
```

今回の bootstrap 昇格でも `気がする` `比率だけ` `いや、2/24の` が confirmed 化された。

## 問題

1. **日常語の機械昇格**: `気がする` `ないよ` `いやいや` は修正文脈以外でも頻出する日常語。confirmed 化により今後の同テキスト再発が correction として自動昇格され、weak_signals → corrections の母集団を日常会話ノイズで汚染する
2. **文脈固有断片の汎化価値ゼロ**: `いや、2/24の` は日付固有の断片で、idiom として再発マッチさせる意味がない
3. ユーザーは bootstrap でシグナル（発話全文）の昇格可否を判断しているが、**そこから抽出される idiom の粒度は見せられていない**。承認したシグナルから危険な短文字 idiom が切り出されても気づけない

## 修正案

- idiom 抽出に最小長（例: 8 文字 or 形態素 3 語以上）の floor
- 日常語 stopword リスト（気がする/ないよ/いやいや 等の相槌・推量表現）
- 日付・数値などの文脈固有トークンを含む断片は idiom 化しない
- bootstrap/daily の AskUserQuestion で「昇格すると confirmed になる idiom」も合わせて提示し、ユーザーが idiom 単位で拒否できるようにする

---
環境: docs-platform / 2026-06-12 bootstrap（6 グループ中 5 昇格）


---

## #528 [evolve 実体験] UX束: fitness 自己矛盾(混乱が weak_signal で実測済)・weak_signals 件数体系・bootstrap representative 品質・skill_triage 指示文混入  `[closed]`  (enhancement)

## 概要

docs-platform での evolve 実体験（2026-06-12 run）で確認した「わかりづらい点」4 件の束。いずれも機能は正しく動くが、レポートの読み手が混乱する。

## 1. fitness メッセージの自己矛盾（ユーザー混乱の実測例つき）

Step 2 は「✅ has_fitness: true — `documentation` 関数が利用可能」と表示し、Step 8 の next_action は「**このPJでは fitness は使わない設計。対応不要（提案が構造的に出ないため母集団は貯まらない）**」と表示する。読み手には「では generate-fitness で作った documentation 関数は何に使われているのか」が分からない。

決定的なのは、今回 bootstrap で昇格した weak_signal の 1 つが「これって evolve していったら勝手にたまってくんじゃないの？」— **まさにこの表示への過去のユーザー混乱が weak_signal として実測されている**のに、表示は未改善のまま。#479 の整合は文言を揃えただけで「fitness 関数の現在の役割」を説明していない。

**修正案**: has_fitness=true かつ structural_reason あり のケースで「documentation 関数は rl-optimize / rl-loop-orchestrator 実行時に使われる。evolve の日次ループでは skill 提案が出ない限り母集団は貯まらない」のような 1 行を next_action に添える。

## 2. weak_signals の件数体系が読めない

observability の行: 「暗黙修正シグナルが 347 件（全PJ集計）（llm_judge 6）。うち当PJ未昇格 6 件」— 347 / 6 / 6 の関係が初見で読めない。さらに Step 7.8 の drain では `permission_deny 5 + esc_interrupt 5` が別途出てくる。

**修正案**: チャネル別 × スコープ（全PJ/当PJ）のマトリクス 1 行ずつ（例: `llm_judge: 全PJ 6 / 当PJ未昇格 6`、`permission_deny: ...`）。

## 3. bootstrap representative の品質

- representative に **assistant の過去レポート出力が引用ごと混入**（「ℹ️ データ蓄積待ち: fitness_evolution: insufficient_data（0/30件）…」がシグナル本文に含まれていた）し判読困難
- 「やっぱり、高だけにして」のような一行 representative は**何に対する修正か分からず**昇格判断ができない

**修正案**: user 発話のみ抽出（assistant 出力の引用ブロックを strip）+ 直前の AI 行動の 1 行要約を evidence に添える。

## 4. observability.skill_triage がデータでなく指示文

契約フィールド `observability.skill_triage` の中身が「…必ずサマリ表示すること（#478）…」という assistant への指示文。他の key は findings（観測結果）なのに、ここだけ instructions で構造が混在。リマインダ意図は分かるが、findings 側に実データ（CREATE/UPDATE/SPLIT/MERGE 件数）を入れ、指示は SKILL.md に置くのが筋。

---
環境: docs-platform / rl-evolve 2026-06-12 run


> 💬 comment:
>
> 全小項目の実装完了を確認しクローズ（逐条照合済み）:
> - #528-1 (fitness 役割の説明1行を next_action 前に必須化) → #535 (ef2c28ee)
> - #528-2 (weak_signals のチャネル別×スコープ matrix 化) → #535 (ef2c28ee)
> - #528-3 (representative の user 発話抽出 + prev_action 要約) → v1.100.0 / #535
> - #528-4 (observability.skill_triage に triage 実件数を注入) → #541 (53570910)
> フルスイート 5083 passed で回帰なし。

---

## #529 [evolve 実体験] 改善束: zero_invocation 計測欠損時の自動抑制・outcome_metrics 最小分母 floor・Step 11 モジュール名明記  `[closed]`  (enhancement)

## 概要

docs-platform での evolve 実体験（2026-06-12 run）からの改善提案 3 件の束。

## 1. zero_invocation 候補の自動抑制（advisory と SKILL MUST の矛盾）

今回 zero_invocations 3 件（project-setup / aws-architecture-diagram / handbook-lifecycle）すべてに advisory「usage 記録経路は 2026-06-12 に修正済み (#478)。この日以前のデータは欠損のため zero と断定不可」が付いていた。つまり**データ自身が「信頼できない」と自己申告している**。

しかし SKILL.md Step 7 は per-item 調査（SKILL.md 全文 Read + git log）+ AskUserQuestion 個別承認を MUST 指定。assistant は advisory に従って MUST を逸脱（調査・質問なしで keep 判断）するしかなかった — advisory と MUST が矛盾している。

**修正案**: 計測修正日が観測窓の途中にある場合、candidates から自動除外し「計測待ち N 件（修正日 YYYY-MM-DD 以降の実測蓄積後に再評価）」の 1 行 surface に置き換える。窓全体が修正後データで埋まったら通常フローに復帰。

## 2. outcome_metrics に最小分母 floor

「correction 再発率: 0.50」の evidence は「窓内 correction 9 件 / distinct type 2 / **再発 type 1**」— つまり n=2 で 50%。低サンプルで率を出すと誤シグナルになる（docs-platform の handbook-drift skip-rate アラートで同型の問題を floor 導入で修正した実績あり）。

promotion_readiness 側には「分母 ≥10」の条件があるのに、outcome_metrics の**表示側**には floor がなく、0.50 という強い数字が一人歩きする。

**修正案**: distinct type < 5 等の floor 未満では率を出さず「サンプル不足（distinct 2 type）」と表示する。

## 3. SKILL.md Step 11 に実モジュール名を明記

Step 11 の `render_issue_body` / `flatten_candidates` / `filter_duplicates` の import 元が SKILL 本文に書かれておらず、references/self-analysis.md を読まないと分からない。assistant は `from self_analysis import render_issue_body` と誤推測して ModuleNotFoundError で一度失敗した（実体は `scripts/lib/evolve_introspect.py`）。

**修正案**: SKILL.md Step 11 に「実コードは `evolve_introspect` モジュール（詳細: references/self-analysis.md）」の 1 行を追記。#479（remediation の sys.path 完全コード化）と同型の対処。

---
環境: docs-platform / rl-evolve 2026-06-12 run


> 💬 comment:
>
> 全小項目の実装完了を確認しクローズ（逐条照合済み）:
> - #529-1 (zero_invocation 計測窓 suppress + 計測待ち N 件 surface) → #534 (d4246149)
> - #529-2 (outcome_metrics の最小分母 floor) → v1.100.0
> - #529-3 (SKILL Step 11 に実モジュール名 evolve_introspect を明記) → #535 (ef2c28ee)

---

## #531 refactor(evolve): evolve.py が file-size-budget の 800 行ハード上限を超過（現 1509 行）— 段階分割  `[open]`

## 事象

`skills/evolve/scripts/evolve.py` が **1509 行**（実測値 2026-06-15）で、`.claude/rules/file-size-budget.md` の `MAX_PYTHON_SOURCE_HARD`（800行・分割必須）を大幅超過。

検出ロジック: `scripts/lib/line_limit.py` の定数 + `audit.check_python_source_budgets`

## 経緯

- 超過自体は継続中。PR #530（self-observation 配線3件の根治）等で段階的に増加。
- 単一ファイルに observe / fitness / discover / audit / skill_triage / remediation / prune / report / self_analysis など ~18 フェーズの orchestration が集中した状態が続いている。

## 影響

- レビュー負荷が高い（1500行超のモノリス）
- 並行 PR での変更衝突リスクが高い
- フェーズ単位のテスト分離が困難

## 推奨アプローチ

既存の `audit.py` 段階分割（2046行→178行・11 PR 連続 merge・PR #51-#61）の勝ちパターンに倣う。

1. **office-hours design doc** でフェーズ群の境界を設計
2. **snapshot テスト / `evolve_result_schema.py` 契約テスト**を安全網として先に整備
3. フェーズ群ごとに module 抽出（例: `observe.py` / `fitness.py` / `remediation.py` / `report.py`）
4. `evolve.py` 側は **re-export で後方互換維持**（`from .observe import ...`）
5. 各 PR を squash merge し段階的に縮小

`scripts/lib/evolve_result_schema.py` の CANONICAL 契約テストが result キーの drift を検出するので、分割の安全網として活用できる。

## 関連

- 同系統: #100（Phase 5/6/7 HARD violator 分割計画）
- ルール: `.claude/rules/file-size-budget.md`、`scripts/lib/line_limit.py`
- 勝ちパターン: PR #51-#61（audit.py 段階分割の記録 → `learning_audit_package_split.md`）

> 💬 comment:
>
> ## Wave 4 設計方針メモ（実装着手前のたたき台）
> 
> evolve.py(現 1738 行・file-size-budget hard limit 800 超過)の分割は、**フェーズごとにファイルを分け、デザインパターンを適用する**方針で進める。
> 
> ### パターン: Pipeline / Stage
> evolve は `Observe → Diagnose → Compile → Housekeeping → Report` の段階パイプライン。これを素直にモジュール分割する:
> - 各フェーズを 1 モジュールに切り出す（例: `evolve/phases/observe.py` 等）
> - `evolve.py` は薄い orchestrator（ステージを順に呼ぶだけ）に縮小
> - 共有状態は result dict（既に `evolve_result_schema.py` で契約化済み）を引き回す形を維持
> - フェーズ間の依存は暗黙のグローバル参照でなく明示的に引数で渡す
> 
> ### 実行プレイブック
> 機械的分割でなく設計判断を伴うため:
> 1. まず refactor-engineer に分割境界・PR 粒度・適用パターンを設計させる
> 2. 実績パターン「audit.py 段階リファクタの勝ちパターン」（2046→178 行を snapshot test + re-export + 小 PR 連発で完走）を踏襲
> 3. impl-worker が段階適用、各段で snapshot test 緑を確認
> 
> ### 順序
> #583〜#587 のロジック修正が landed した**後**に着手する（先に分割すると分割後コードへの再修正が二重発生するため、安定コードを分割する）。
> 

---

## #532 [tech-eval] import スキルの静的脆弱性スキャン（SkillSpector 型）を audit に配線  `[open]`

## 概念

外部スキル（プロンプト・ツール定義・スクリプト）を静的解析し、悪意あるパターン・prompt injection・危険なツール定義を検出する。NVIDIA/SkillSpector がスキルのサプライチェーン攻撃面に正面から取り組んだもの（2026-06-15 daily report）。

## Before / After（rl-anything 運用者の体験）

- **Before**: `import` skill で外部スキルを取り込む経路に品質ゲートが無く、信頼できないスキルを取り込んでも気づけない。
- **After 🛡**: 取り込み前に静的スキャンで danger を検出してブロック／警告。`audit` レポートに「Skill Vulnerability」section が出て、取り込み済みスキルの危険度が一覧化される。

## 既存実装との差分（ギャップ）

- `scripts/rl/fitness/constitutional.py:233` の cso 信号は **runtime の pass/fail** であって、スキル content の静的解析ではない。
- `import` skill 経路（`skills/import/`）に取り込みゲートが存在しない。
- → 静的脆弱性スキャンは未実装。

## 配線先（recurring ループ）

- `import` skill の取り込み時ゲート（PreToolUse 相当 or skill 内チェック）
- `audit` の observability section — 検出ロジックを `scripts/lib` の決定論 lint として実装し `audit/observability.py` の `_OBSERVABILITY_BUILDERS` に登録 → markdown / 構造化の両経路へ自動伝播し evolve のたびに surface（version ≠ enforcement を回避）

## 採用後の確認方法

- [ ] `/rl-anything:audit` を回す → レポートに「Skill Vulnerability」section が出て `import` 済みスキルの danger 件数が表示される

## 再評価条件

- 外部スキルの取り込み頻度が上がる / public marketplace 連携を始めるとき

---
出典: tech-eval (ai-github-trending-2026-06-15.md) / NVIDIA/SkillSpector

---

## #533 [tech-eval] マルチエージェント fan-out の費用対効果を telemetry で実証  `[open]`

## 概念

「複数エージェントを協調させると単一より本当に良くなるのか」を批判的に検証する（arXiv 2606.13003 "The Illusion of Multi-Agent Advantage"）。多くのケースで fan-out の優位は見かけ倒しでコストに見合わない、という主張。

## Before / After（rl-anything 運用者の体験）

- **Before**: rl-scorer の3軸並列 fan-out が単一採点に勝つかを計測する仕組みが無く、論文の主張を自 PJ で検証できない。`subagent-guard` は件数ゲートのみで「効くか」は見ていない。
- **After**: fan-out 効果（並列 vs 単一の delta）が telemetry で可視化され、勝率が閾値未満なら警告。無駄な並列を抑制できる。

## 既存実装との差分（ギャップ）

- `~/.claude/rules/subagent-guard.md` は乱立防止の **件数ゲート**のみ。
- CLAUDE.md「並列数で質を補う／ティアは上げない」は運用ルールで、実測の裏付けは無い。
- rl-scorer（オーケストレーター + 3 subagent 並列採点）の fan-out が単一比で勝つかの計測経路が無い。

## 配線先（recurring ループ）

- `telemetry` または `chaos` fitness に fan-out 効果軸を追加 → `audit` が消費し evolve のたびに surface
- rl-scorer の3軸並列結果 vs 単一採点の delta を telemetry に記録

## 採用後の確認方法

- [ ] `/rl-anything:evolve` を回す → telemetry section に「fan-out advantage delta」が出て、3軸並列が単一採点に勝った率が表示される

## 再評価条件

- fan-out のトークンコストが体感で気になり始めたとき

---
出典: tech-eval (ai-github-trending-2026-06-15.md) / arXiv 2606.13003

---

## #538 [tech-eval] paired trajectory auditing（観測版）を audit→evolve に配線  `[open]`

## 概念
**paired trajectory auditing**（SkillAudit, arXiv 2606.14239）— 同一タスクをスキル有/無で実行し、挙動トラジェクトリの差分を診断信号に変える。隠しテスト・報酬・検証スコアといった特権的フィードバック無しでスキル更新の良し悪しを測る。論文では89タスク8ドメインで無スキル40.9% / 静的エキスパート56.7% に対し **73.9%**。

## Before / After（rl-anything を回す開発者の体験）
- **Before**: スキル更新の良し悪しを `chaos.py`（構造 coherence の仮想除去）と `compute_component_transfer`（時系列前後の success 率デルタ）でしか測れない。どちらも「同一タスクを skill 有/無で走らせた**挙動**の対照」ではない。
- **After**: 同じタスクが skill 有/無で起きた事例を対比した挙動デルタを診断信号として surface できる。

## 既存実装との差分（根拠）
- `scripts/rl/fitness/chaos.py:138 compute_chaos_score` = 構造の仮想除去（coherence delta）。対照だが*構造*シグナル、*挙動*ではない。
- `scripts/lib/audit/usage.py:143 compute_component_transfer` = スキル追加*前後*（時系列）の success 率デルタ。準実験だが同一タスクの対照ではない。
- ギャップ = 「同一タスクの skill 有/無の**挙動**対照」。

## スコープ判断（重要）
SkillAudit は 89 タスクを**能動的に再実行**して軌跡を取る。rl-anything は受動観測（再実行しない）設計なので full 移植は実行ハーネス新設が必要でコスト大。
→ **本 issue のスコープは観測版**: 既存セッション履歴から「同タスクが skill 有/無で発生した事例」を準実験的に拾い、挙動デルタを算出する（`compute_component_transfer` の発展）。能動再実行版は別 issue（要ハーネス）。

## 配線先（enforcement surface）
**evolve（audit が消費）で毎回発火**。`scripts/lib/audit/usage.py` に section を追加し、observability contract（`_OBSERVABILITY_BUILDERS`）経由で markdown/構造化 両経路へ伝播。手動 CLI / 能動 batch 止まりにしない（version≠enforcement）。

## 採用後の確認方法
- [ ] `/rl-anything:audit` を回す → report に「paired diff / skill 有無の挙動デルタ」section が出る（別コマンド不要で出るのが正しい配線）
- [ ] 0 件でも「✓ 該当データなし」を1行残す（silence≠evaluated）

## 再評価条件
能動実行ハーネスを導入したら full SkillAudit（タスク再実行版）を再検討する。

---
出典: tech-eval `ai-github-trending-2026-06-16` / SkillAudit arXiv 2606.14239


---

## #539 [tech-eval] 矛盾記述の Repair 候補化（evolve_consistency の延長）  `[open]`

## 概念
**Refine / Repair 2経路編集**（SkillAudit, arXiv 2606.14239）— スキル文書の更新を2経路に分ける: Refine=不要・冗長な記述の除去、Repair=矛盾する記述の置換。

## Before / After
- **Before**: 矛盾は `evolve_consistency` が検出するが、Repair（置換）は人手。検出止まり。
- **After**: 軌跡/整合性検査で検出した矛盾記述を自動で Repair 候補化し、evolve の提案に乗せる。

## 既存実装との差分（根拠）
- `scripts/lib/evolve_consistency.py` = P1 invariant の runtime self-detect（矛盾検出はある）。
- prune = 除去（Refine 相当）は実装済み。
- ギャップ = 「検出した矛盾を **Repair（置換）候補**として提示」する経路が無い。

## 配線先（enforcement surface）
**evolve**（`evolve_consistency` の延長）。`improvement_opportunities` 合流の既存経路に Repair 候補を足す。

## 採用後の確認方法
- [ ] `/rl-anything:evolve` を回す → 矛盾検出に対し Repair 候補（置換案）が proposable に出る
- [ ] 0 件でも整合性 zero_line を残す（silence≠evaluated）

## 再評価条件
矛盾検出の FP 率が安定したら自動 Repair 候補化に着手。FP が高いうちは検出止まりで保留。

## 優先度
中（C2/C3/C5 の自己進化コアは実装済みのため、矛盾 Repair は増分改善）

---
出典: tech-eval `ai-github-trending-2026-06-16` / SkillAudit arXiv 2606.14239


---

## #540 [tech-eval] skill 脆弱性走査（SkillSpector）を audit observability に追加  `[open]`

## 概念
**skill 脆弱性走査**（NVIDIA SkillSpector）— スキル文書を悪性パターン（prompt-injection、権限逸脱、危険コマンド誘導など）の観点で走査する。スキルを「作る」でなく「防御する」段階の機構。

## Before / After
- **Before**: スキルの prompt-injection 等は無検査。constitutional に `/cso` security 軸はあるが*スキル単位の悪性パターン走査*ではない。
- **After**: audit に「skill 脆弱性: N 件 / ✓ 該当なし」セクションが出て、危険なスキル記述を可視化。

## 既存実装との差分（根拠）
- `scripts/rl/fitness/constitutional.py` の /cso security 軸 = 原則ベース LLM 評価。スキル単位の悪性パターン辞書走査ではない。
- prune/audit は品質軸（行数・重複・liveness）であり security 走査ではない。
- ギャップ = スキル本文に対する決定論的な悪性パターン走査。

## 配線先（enforcement surface）
**audit の observability contract**。`_OBSERVABILITY_BUILDERS` に builder を1行登録すれば markdown/構造化 両経路へ自動伝播し、evolve のたび発火する（モグラ叩き回避）。

## 採用後の確認方法
- [ ] `/rl-anything:evolve` を回す → observability セクションに「skill 脆弱性: N 件 / ✓ 該当なし」が surface（0 件でも1行残す, silence≠evaluated）

## 再評価条件
SkillSpector のパターン定義が公開されたら走査辞書を移植して精度を上げる。初版は決定論 regex で日英の代表パターンから。

## 優先度
中（安価・独立性が高く着手しやすい）

---
出典: tech-eval `ai-github-trending-2026-06-16` / NVIDIA SkillSpector


---

## #548 [tech-eval] アクセス頻度ベースの memory 強化（reinforce_memory の本番配線）  `[open]`

## 概念

memory は「よく参照・検証されるほど残り、放置されるほど薄れる」べき（Karpathy LLM Wiki 忘却曲線派生、48h で5,000⭐ / Continual Self-Improvement / OPD-Evolver の4階層メモリと同方向）。アクセス・検証のたびに忘却曲線をリセット（削除でなく減衰）する。

## Before / After（運用者体験）

- **Before**: memory は古さ(age)だけで減衰する。何度参照しても強化されない。`reinforce_memory()` 関数は実装済みだが**本番のどこからも呼ばれていない（caller ゼロ・grep 確定）**。`compute_importance_score` は `access_count` を明示除外（`memory_temporal.py:118`）。
- **After**: recall / session 注入で参照された memory の `last_reinforced_at` がリセットされ `importance_score` が上がる＝よく使う記憶ほど残る。

## 既存実装との差分（根拠・ギャップ）

- ✅ 指数減衰の形は実装済み: `scripts/lib/prune/skill_inspect.py:251` `compute_decay_score = base*exp(-age/decay_days)`（Ebbinghaus 型）
- 🔶 強化側が死蔵: `scripts/lib/memory_temporal.py:137` `reinforce_memory()` はテストからしか呼ばれない（本番 caller ゼロ）
- ❌ access_count 非追跡: `compute_importance_score`（L118）が「hook で取得不能」として access を除外

→ まさにこの PJ の持病「version/install ≠ enforcement」の memory 版。関数はあるが発火経路がない。

## 配線先（enforcement surface）

手動 CLI 止まりにしない。recurring に参照が走る2経路に乗せる:
1. `bin/rl-fleet recall` のヒット時に、ヒットした memory ファイルへ `reinforce_memory(path, reason="recall hit")` を呼ぶ（recall ヒットを access proxy にする → access_count 非取得問題を回避）
2. session-start の memory 注入 hook で、注入した memory に同様に reinforce
- 減衰側は evolve→prune が既に消費しているので追加配線不要

## 採用後の確認方法

- [ ] `bin/rl-fleet recall "<よく使う語>"` を実行 → 対象 memory の frontmatter `last_reinforced_at` が更新され `importance_score` が上がる（現状は変化しないはず）

## 再評価条件

- access_count が hook で取れないままなら recall/注入ヒットを唯一の access proxy として運用（reset の取りこぼしが問題化したら別経路追加）

出典: AI デイリーレポート 2026-06-17（Karpathy LLM Wiki 忘却派生）

---

## #549 [tech-eval] 経験操作の4能力評価軸（read/use/write/maintain）を fitness に追加  `[open]`

## 概念

OPD-Evolver（arXiv 2606.17628・HF 15 upvote）の「経験を保存することと、経験を通じて進化する術を learn することは別」という問題提起。memory を「読む・使う・書く・維持する」4能力として評価し、記憶を使い切れているかを測る。多ドメインで ReasoningBank 比 +11.5%。

## Before / After（運用者体験）

- **Before**: fitness は coherence/telemetry/constitutional/skill_quality の4軸。「記憶を読めているか/使えているか/維持できているか」という観点がない。
- **After**: evolve report に「記憶操作 read/use/write/maintain」section が出て、記憶の死蔵・未活用を可視化できる。

## 既存実装との差分（根拠・ギャップ）

- 現状 fitness: `environment`（coherence 0.25 / telemetry 0.45 / constitutional 0.30 + skill_quality 動的統合）
- memory の温度（importance_score / last_reinforced_at）は `memory_temporal.py` にあるが、「4能力」という統合評価軸にはなっていない
- #548（reinforce 配線）と相補: reinforce が "use/maintain" のデータを生む

## 配線先（enforcement surface）

evolve（audit が消費）の observability contract に builder を1本追加（`_OBSERVABILITY_BUILDERS` に登録すれば markdown/構造化の両経路へ自動伝播）。evolve のたびに surface。

## 採用後の確認方法

- [ ] `/rl-anything:evolve` を回す → report に「記憶操作 read/use/write/maintain」section が出る

## 再評価条件

- 既存4軸 fitness と重複しないか dry-run で確認後。重複するなら独立 section でなく既存軸の sub-metric に格納

出典: AI デイリーレポート 2026-06-17（OPD-Evolver, arXiv 2606.17628）

---

## #550 [tech-eval] skill_extractor 候補に store-time 再実行ゲートを追加  `[open]`

## 概念

PreAct（arXiv 2606.17929）の store-time ゲート: 成功軌跡をスキル化する前に、**独立評価器がクリーン状態から再実行して本当に解けたものだけ**を store に登録する（壊れた/再現しないプログラムの蓄積を防ぐ）。

## Before / After（運用者体験）

- **Before**: skill_extractor の軌跡候補は generalizability_score / 類似度・頻度ヒューリスティックで採否。`meta_quality` の CREATE/REVIEW/SKIP も静的判定で、再現性の再検証はしない。
- **After**: 候補に「再実行検証: pass/fail」が付与され、再現しない候補が triage に渡らない。

## 既存実装との差分（根拠・ギャップ）

- ✅ 軌跡→構造分解は実装済み: `scripts/lib/skill_extractor/decomposition.py`（routing/workflow/semantics/attachments の4軸）+ generalizability_score
- 🔶 store-time の再実行検証なし: `meta_quality`（CREATE/REVIEW/SKIP）・`triage_ledger` は静的ヒューリスティックで、PreAct の「クリーン状態から再実行して solvable か」をやっていない

## 配線先（enforcement surface）

skill_extractor 候補 → triage の手前。`evolve` 内（discover が skill_extractor を発火する経路）で検証を挟む。

## 採用後の確認方法

- [ ] `/rl-anything:evolve --dry-run` → skill_extractor 候補に「再実行検証: pass/fail」が付与され fail 候補が triage に渡らないことを確認

## 再評価条件・コスト注意

- 再実行は LLM/実行コストがかかる。全候補一律でなく generalizability_score 上位のみ等のサンプリングを検討
- 候補の false-positive 採用が実測で問題化してから本格配線（現状コストに見合うかを dry-run で先に観測）

出典: AI デイリーレポート 2026-06-17（PreAct, arXiv 2606.17929）

---

## #554 glossary: jargon 候補に universal-tech/AWSサービス名 stoplist を適用（GET/JS/JWT/CRUD 等のFP除去）  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
`glossary_drift` が「未登録 jargon 候補 33 件」と報告。内訳に汎用テック語/AWSサービス名が混入していた:

- **FP（読者が既知の一般語）**: `GET` `JS` `JWT` `CRUD` `SHA` `RPC` `IaC` `CDN` `SaaS` `TypeScript`
- **FP（AWSサービス名）**: `CloudFront` `DynamoDB` `EventBridge`
- **真の PJ jargon**: `AMAMO` `JCM` `MRV` `EOA` `AnchorRegistry` `PKCE` `UpEnergy` `TAMPERED/VERIFIED` 等

約13/33 が用語集に不要な一般語。件数が水増しされ、`SEED_MIN_CANDIDATES` 閾値による seed 生成提案の判断も歪む。

## 提案
jargon 候補抽出に **universal-tech-term + AWS サービス名の stoplist** を当ててから件数化する。`evolve-ops` 系の「汎用略語を誤検知」注意は既知だが glossary seed 経路に未適用の可能性。

<!-- rl-evolve-introspect:glossary-jargon-universal-stoplist -->


---

## #555 discover: rule_violation/tool_usage の examples を1行truncate + 別PJソース参照を弱シグナル化  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
`discover.rule_violation_observed` の `cd /tmp` 6回違反の examples が、中身は
`cd /tmp && grep ... /tools/rl-anything/scripts/evolve.py` や `/tmp/rl_evolve_out.json` を読むコマンド＝**rl-anything 開発作業**だった（amamo の作業ではない）。cwd ベース帰属では「技術的に正しい」が、アプリPJのテレメトリに開発ノイズが載る。

また examples フィールドが**巨大な多行スクリプト丸ごと**で、`tool_usage_patterns` / `rule_violation_observed` の表示が極端に重い。

## 提案
1. examples を1行に truncate（先頭120字等）して表示・保存
2. 違反 example のパスが「別PJのソースツリー」を指す場合は弱シグナル扱い or 帰属メタを付与

<!-- rl-evolve-introspect:discover-examples-truncate-and-crosspj-noise -->


---

## #556 auto-memory: 既存rule引用型 correction を生成キューから除外（毎run 生成→belief block の浪費）  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
auto-memory drain で `blocked=1`。中身は **「先送り表現検出 → no-defer-use-subagent ルールに従え」という Stop hook の指摘そのもの**＝既存グローバル rule の再掲。belief_entropy ゲートで棄却された。

`belief_blocks` observability には**前回も同じ no-defer memory が block 済み**と記録あり。つまり毎 run「Stop hook が enqueue → 生成 → belief block」を繰り返してサイクルを浪費している（循環: 既存 rule をソースに memory を作ろうとして毎回弾かれる）。

## 提案
Stop hook の「ルール引用型 correction」（既存 rule 名を本文に含む reminder）は auto-memory 生成キューに enqueue しない。または同一 dedup_key の belief block が N 回連続したら enqueue 抑制する。

<!-- rl-evolve-introspect:auto-memory-rule-citation-reblock-loop -->


---

## #557 cli: promote-weak 出力 corrections_human を scope明示リネーム（全PJ集計と当PJの混同・#526-1根治）  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
1画面に correction 件数カウンタが3系統出て、scope と分母がバラバラ:

- prune `corrections_cleanup.kept = 50`
- growth_report `corrections_human = 0`（当PJ・分析時点）
- `rl-reflect --promote-weak` 出力 `corrections_human = 81`（**全PJ集計**）

同じ `corrections_human` という名前で「当PJ 0」と「全PJ 81」が出るため取り違えやすい。`#526-1` で「CLI の corrections_human を growth 表示に使うな（41/10 事故）」という注意書きが既に入っているのが、根っこの命名曖昧さの証拠。

## 提案
CLI(`rl-reflect --promote-weak`) 出力を `corrections_human_allpj` のように **scope 明示リネーム**し、per-PJ 値と機械的に区別できるようにする。

<!-- rl-evolve-introspect:corrections-human-scope-rename -->


---

## #558 evolve(Step6.1): bootstrap まとめて確認に TF-IDF テーマクラスタ＋バケットmultiSelect を標準化  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
初回 bootstrap で当PJ未昇格シグナル **48件 / 45グループ**。SKILL Step 6.1「まとめて確認」は「各 group を AskUserQuestion で順に確認」と指示するが、45件を素直に出すと質問マラソンになり `explain-clearly`（質問を畳む）と衝突する。

実運用では assistant が**手動でテーマ別バケット化**（DESIGN/COST/ATTRIB/SCOPE + 一回限り）して4択1問に畳んで処理した。これはスキル側が標準提供すべき機能。

## 提案
bootstrap groups に **TF-IDF テーマクラスタリング**（reorganize で既に保有）を当て、「バケット単位の multiSelect」を Step 6.1 の標準フローにする。グループ数が閾値超のときだけクラスタ提示に切り替える。

<!-- rl-evolve-introspect:bootstrap-bulk-theme-cluster-ux -->


---

## #559 fitness_evolution: insufficient_data+structural 出力を {verdict, one_liner} に圧縮（過剰防衛注記の根治）  `[closed]`

## 観測（amamo PJ の evolve 実行・2026-06-18）
`fitness_evolution`（status=insufficient_data, structural_reason=skill_evolve_not_scored）の出力が
`has_fitness` / `structural_reason` / `next_action` / 3段落 `message` と重複情報過多。

SKILL 側にこの1フェーズの誤読防止注記が大量に積まれている（#400 バグ#5 / #525-1 / #526-4 / #528-1 / #479）。**注記の多さ＝出力契約が壊れて誤読され続けている兆候**。

## 提案
insufficient_data + structural のケースは `{verdict, one_liner}` の2フィールドに圧縮し、長文 `message` は `details` に隔離。SKILL 側の #番号注記群を1本化できる。

<!-- rl-evolve-introspect:fitness-evolution-output-contract-simplify -->


---

## #560 fix(evolve): self_analysis の usage↔suitability guard が verification_bypass を無視し検証系11件を毎回 false positive 検出  `[closed]`  (bug)

## 症状

evolve の self_analysis（Step 11）が `improvement_opportunities` として
「usage0 なのに suitability=medium」を **11 件**、毎 evolve run で量産する。
対象スキル: `evolve-skill` / `discover` / `feedback` / `backfill` / `audit` /
`pitfall-curate` / `agent-brushup` / `prune` / `release-notes-review` / `import` / `spec-keeper`。

これらは dedup_key が `improvement:consistency_usage_suitability:<skill>` で互いに別 root cause 扱いされるため、
起票すると 11 個の独立 issue になり、起票しなくても毎 run ノイズとして surface され続ける。

## 根本原因（false positive）

検出対象 11 件は **すべて `verification_bypass=True`**（実データで確認）。
これは #376 が意図的に設けた例外で、「検証系スキルは usage_count=0 でも medium 維持」という設計:

- `scripts/lib/skill_evolve/assessment.py:86-89` — `is_verification_skill` なら `suitability="medium"` + `verification_bypass=True`
- `scripts/lib/skill_evolve/assessment.py:93-99` — #376 の usage=0 降格は `not verification_bypass` のときのみ。検証系は降格しない（＝医図的に medium 維持）

ところが guard 側 `_detect_usage_suitability_contradiction`
（`scripts/lib/evolve_consistency.py:116-161`）は `usage_count==0 かつ suitability∈{high,medium}` を
**無条件で矛盾**と判定し、`verification_bypass` を見ていない。

結果、classifier（例外として medium 維持）と guard（無条件で矛盾）が**同じ #376 について不一致**で、
検証系の正当な medium-at-usage0 を毎回 11 件 false positive として吐く。

## 証拠

```
medium かつ usage0 のスキルの verification_bypass:
  evolve-skill, discover, feedback, backfill, audit, pitfall-curate,
  agent-brushup, prune, release-notes-review, import, spec-keeper
  → 11 件すべて verification_bypass=True / recommendation「検証系スキルのため自己進化を推奨」
guard が矛盾として検出する件数: 11 / うち verification_bypass=True: 11
```

## 修正方針

`_detect_usage_suitability_contradiction` のループ先頭で `verification_bypass` を除外する:

```python
for a in assessments:
    if not isinstance(a, dict):
        continue
    if a.get("verification_bypass"):   # ← #376 の検証系例外は矛盾ではない
        continue
    ...
```

assessment dict は既に `verification_bypass` フィールドを持つ（追加収集は不要）。
回帰テスト: verification_bypass=True の usage0/medium assessment を guard に渡し 0 件になることを assert。

## 補足（上流の設計の匂い・別 issue 候補）

`is_verification_skill`（`scripts/lib/skill_evolve/classification.py:80-88`）は SKILL.md **本文の部分一致**で
`test`/`check`/`audit`/`scan` 等を拾うため、`feedback`/`import`/`backfill`/`discover` まで「検証系」判定される。
そのため #376 の usage=0 降格がほぼ全スキルでバイパスされ形骸化している。
本 issue は guard 側の修正で false positive を止めるが、根を断つなら verification 判定をスキル名 or 明示メタデータに絞る案も検討余地あり。

<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:evolve-skill -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:discover -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:feedback -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:backfill -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:audit -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:pitfall-curate -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:agent-brushup -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:prune -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:release-notes-review -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:import -->
<!-- rl-evolve-introspect:improvement:consistency_usage_suitability:spec-keeper -->


---

## #561 fix(evolve): constitutional cache stale の「失敗ではない」advisory が self_analysis runtime_errors(bug) に二重 surface  `[closed]`  (bug)

## 症状

evolve の self_analysis（Step 11）の `runtime_errors` カテゴリに、
constitutional cache stale の advisory が `label: bug` の issue 候補として 1 件乗る。

```
⚠ 実行時エラー 1 件: constitutional_cache:constitutional-cache-stale-miss-audit-step-refresh-cache
title: [evolve introspect] stderr 警告: constitutional_cache:
       Constitutional: cache stale/全 miss で未算出（失敗ではない）。...
```

文面自体に「**失敗ではない**」「audit Step 3.5 の 2 相 refresh で cache 再生成を推奨」と明記された
**既知の良性 advisory** が、「直すべき bug」として surface される。

## 根本原因

`skills/evolve/scripts/evolve.py:225-231` が同一文面を **2 経路**に push している:

```python
line = "Constitutional: cache stale/全 miss で未算出（失敗ではない）。audit Step 3.5 の 2 相 refresh で cache 再生成を推奨"
warning_sink.append({"category": "constitutional_cache", "message": line})  # ← これが問題
if isinstance(observability, dict):
    observability["constitutional"] = [line]   # ← observability 行（これは正しい）
```

`warning_sink` は `result["warnings"]` に流れ、self_analysis の
`_detect_captured_warnings`（`scripts/lib/evolve_introspect.py:201-225`）に拾われる。
このパスは本来 **scipy RuntimeWarning(NaN) 等の真の警告**用（#341）であって、
「計算前提の崩れ」を bug 候補化する設計。良性 advisory を流す先ではない。

結果、constitutional の良性状態が observability 行（正）と runtime_error/bug 候補（誤）の**二重 surface**になる。

## 修正方針（いずれか）

A. 良性 advisory は `observability` のみに入れ、`warning_sink` には append しない（推奨・最小）。
B. `_detect_captured_warnings` で既知良性カテゴリ（`constitutional_cache` 等）または
   「失敗ではない」を含むメッセージを除外する。

A が最小かつ意図が明確（warning_sink = 真の警告のみ、observability = 状態 surface）。

回帰テスト: constitutional cache miss 時に `result["warnings"]` に constitutional_cache が**入らない**
（or self_analysis runtime_errors 候補に出ない）こと、observability には依然出ることを assert。

<!-- rl-evolve-introspect:runtime_warning:constitutional_cache:constitutional-cache-stale-miss-audit-step-refresh-cache -->


---

## #562 fix(evolve): weak_signals 昇格案内が未読をチャネル横断で数え「今日の修正確認 phase で昇格可能」と誤案内（決定論チャネルは reflect 経路）  `[closed]`  (bug)

## 症状

weak_signals の observability 昇格案内と、evolve の「今日の修正確認」phase の実挙動が食い違う。

- observability:「当PJ未昇格 35 件（うち**未読 18 件**）。未読分は `/rl-anything:evolve` の
  **今日の修正確認 phase で昇格可能**（既読済は再提示されない）。」
- 実際: `correction_review.daily.eligible=False` / `groups=[]` → phase は **0 件**しか提示しない。

ユーザーは「18 件昇格できる」と読んで evolve を回すが「新規なし」で何も出ず混乱する。

## 根本原因（チャネルの取り違え）

`scripts/lib/audit/sections_weak_signals.py:154-166` は未読を**全チャネル横断**で数える:

```python
for r in records:
    if is_current and not r.get("promoted"):
        unpromoted += 1
        if r.get("signal_key") not in reviewed_keys:
            unread += 1          # ← channel を区別していない
```

一方「今日の修正確認」phase（`daily_review` / `build_review`）は **channel=llm_judge のみ**を対象とする。
決定論チャネル（manual_edit_after_ai / esc_interrupt / rephrase）は daily phase の対象外で、
reflect の `--promote-weak` 経路で昇格する。

実データの未読 18 件の内訳（当PJ・実測）:

```
当PJ未読(未昇格&未既読) by channel:
  esc_interrupt: 9, manual_edit_after_ai: 8, rephrase: 1   ← 計 18（全部 決定論チャネル）
  llm_judge: 0                                              ← daily phase 対象は 0 件
```

つまり未読 18 件は**全て決定論チャネル**で、daily phase は構造的に 1 件も提示できない。
`daily.eligible=False` は**正しい**。誤っているのは observability の案内文（unread を全チャネルで数えて
「今日の修正確認 phase で昇格可能」と一括で言い切っている）。

## 修正方針

`hint_line` の「今日の修正確認 phase で昇格可能」は **llm_judge チャネルの未読数**にスコープする。
決定論チャネルの未読は別行で誘導先を分ける:

- llm_judge 未読 N 件 → 今日の修正確認 phase（evolve）で昇格
- 決定論チャネル 未読 M 件 → `/rl-anything:reflect --promote-weak` で昇格

これで daily.eligible=False（llm_judge 未読 0）と案内文が噛み合う。

回帰テスト: 決定論チャネルのみ未読の fixture で、hint の「今日の修正確認 phase」数が 0 になり、
別途 reflect 誘導行が出ることを assert。


---

## #563 fix(audit): rework_rate に最小分母 floor が無く分母1で1.0に張り付き measurement_bug/promotion_readiness を誤発火  `[closed]`  (bug)

## 症状

outcome_metrics の rework 率が **分母 1** で 1.00 を表示する:

```
・rework 率(近似): 1.00 — 低いほど良い（検証なし連続編集の少なさ）
    evidence: rework 1 / 編集あり 1 sessions (連続編集閾値 3)
```

分母（編集ありセッション）が 1 件のとき、1 セッションの当落で率が 0.0/1.0 に振れる無意味なシグナルになる。

## 根本原因（floor の非対称）

同じ outcome_metrics の `correction_recurrence_rate` は #529-2 で
`MIN_DISTINCT_TYPES_FLOOR = 5` を導入し、分母未満では率を `None`「サンプル不足」にする
（`scripts/lib/audit/outcome_metrics.py:66-74, 205-216`）。

一方 `rework_rate`（`scripts/lib/audit/outcome_metrics.py:283-327`）には**最小分母 floor が無い**:

```python
rate = round(len(rework_sessions) / len(edit_sessions), 4)   # 分母 1 でも率を出す
```

## 影響（下流の誤発火）

分母が小さい全 PJ で rework=1.0 に構造的に張り付くため:

- `measurement_bug` 検出器が「複数 PJ で bit-exact 1.0 一致 = 測定バグ強シグナル」（#445）として surface
- `promotion_readiness` 条件1「分散が十分」が「全 3 PJ が同値 1.0」で恒久 ✗
  → ADR-046 の outcome 重み昇格判断が構造的にブロックされる

実測（promotion_readiness より）:
```
✗ 条件1 分散が十分: 全 3 PJ が同値 1.0 = 測定バグ強シグナル（#445）
分母実測: rl-anything=30, docs-platform=5, sys-bots=6  ← session 分母は十分だが
                                                          rework の edit_sessions 分母が極小
```

## 修正方針

`rework_rate` にも `correction_recurrence_rate` と同様の最小分母 floor を入れ、
`len(edit_sessions) < FLOOR` のとき率を `None`「サンプル不足」にする（沈黙 != 評価不能の方針 #393-#396 を踏襲）。
floor 値は recurrence の floor と整合する形で決める（edit_sessions ベースなので別定数が妥当）。

これにより分母極小での 1.0 張り付きが消え、measurement_bug の false positive と
promotion_readiness 条件1 の恒久 ✗ が解消する。

回帰テスト: edit_sessions が floor 未満の fixture で rate=None / サンプル不足表示になることを assert。
floor 以上では従来通り率を出すことも assert。


---

## #564 [tech-eval] SEAGym 流の evolve 多視点評価（held-out / replay / 退行）を evolve/audit に配線  `[closed]`  (enhancement)

## 概念
自己進化エージェントの harness 更新を、単一スコアではなく **train / validation / test / replay / cost の5記録**で測る（SEAGym: An Evaluation Environment for Self-Evolving LLM Agents, arXiv 2606.17546）。「再利用可能な改善か / 直近タスクへ過学習か / コスト増か / 旧挙動を壊したか」を区別できるようにする。

## Before / After（開発者体験）
- Before: evolve 提案は accept/reject の単一信号。提案が「過学習か」「旧挙動を壊したか」を区別できない
- After: evolve レポートに「再利用可能 / 過学習 / 退行 / コスト増」の多視点ラベルが出て、誤提案を採用前に弾ける（🛡 提案の信頼性向上）

## 既存実装との差分（根拠・ギャップ）
部品は揃っているが「evolve 世代横断の多視点退行検査」として束ねられていない:
- `scripts/rl/fitness/chaos.py` — 仮想アブレーション（単一除去ロバストネス・SPOF 検出）。held-out の片鱗はある
- `scripts/lib/audit/outcome_attribution.py` — per-skill 一発成功率 / rework 率
- `scripts/lib/audit/usage.py` negative_transfer — スキル追加前後の success delta
- **未実装**: replay 診断 / OOD 転移ビュー / evolve 世代スナップショットの退行突合

## 配線先（recurring ループ）
**evolve / audit**（毎回回る）。chaos.py・outcome_attribution・negative_transfer を統合した held-out/replay セクションを evolve レポート末尾に追加する。手動 CLI 止まりにしない。

## 採用後の確認方法
- [ ] `/rl-anything:evolve` を回す → レポートに「held-out / replay / 退行」セクションが出て、提案ごとに多視点ラベルが付く

## 再評価条件
ADR-046（アウトカム重み昇格）の判断が始まる時（他 PJ のデータ蓄積後・PJ≥2）に再優先。退行検出が重み昇格ゲートの裏付けに使える。

---
出典: tech-eval（ai-github-trending-2026-06-18.md）。論文コード未公開のため概念のみ移植。

---

## #565 [tech-eval] FinAcumen 流の関連度ゲート付き経験検索＋無関係抑制を reflect/correction_semantic に配線  `[closed]`  (enhancement)

## 概念
過去軌跡から「成功戦略」と「失敗由来の戒めルール」を蒸留して永続メモリへ蓄積し、推論時は **意味的関連度が校正済み閾値を超えたときだけ**経験を条件付け、無関係メモリはフォールバックで**明示的に抑制**する（FinAcumen: Financial Multimodal Reasoning via Self-Evolving Experience Memory Harness, arXiv 2606.17642）。

## Before / After（開発者体験）
- Before: 過去修正・pitfall は Top-N 一律提示。無関係な記憶も提案根拠に混じる
- After: 関連度が閾値を超えた経験だけが提案根拠に出て、無関係メモリは抑制される（🛡 提案根拠の信頼性向上・FP 低減）

## 既存実装との差分（根拠・ギャップ）
- `scripts/lib/correction_semantic/`（daily_review.py / bootstrap_backlog.py）— jaccard≥0.5/0.8 で group 化・provenance_weight・idiom 照合。ただし目的は grouping/dedup
- pitfall-curate の配布版は Top-N **静的**で、関連度に応じた動的選択ではない
- **未実装**: 提案時の校正済み閾値ゲートによる選択的検索＋無関係メモリの明示抑制（フォールバック）

## 配線先（recurring ループ）
**reflect / correction_semantic**（既存の Haiku semantic judge バッチ）。判定パイプラインに「関連度ゲート＋抑制」を1段追加する増分実装。

## 採用後の確認方法
- [ ] `/rl-anything:reflect` を回す → 提案根拠に閾値未満の無関係 correction が出ない（関連度スコア付きで表示される）

## 再評価条件
correction 提案の FP（無関係な根拠の混入）がユーザー体感で増えたら優先度を上げる。

---
出典: tech-eval（ai-github-trending-2026-06-18.md）。論文コード未公開のため概念のみ移植。

---

## #567 glossary: jargon 候補の一般英単語 FP を辞書ベースで根治（stoplist 列挙の卒業）  `[closed]`

## 背景（#554 / #554-2 の follow-through）
#554/#554-2 で universal-tech 語・AWS 名・SQL/ログ/ステータス語を `DEFAULT_STOPLIST` に明示列挙して jargon FP を削減した（実 PJ E2E: docs-platform 22→10 / sys-bots 5→3）。だが stoplist 個別列挙は本質的にモグラ叩き（learning_detector_fp_context_not_allowlist）。

## 提案
一般英単語の判定を**辞書ベース**にする（語が一般英単語辞書に含まれる かつ PJ 固有 CamelCase/全大文字頭字語でない なら jargon 候補から除外）。stoplist は「辞書に載らないが除外したい語」だけに縮小する。

## 受け入れ基準
- BEGIN/END/FAILED 等の汎用語が辞書フィルタで自動除外される（stoplist 追記不要）
- FastAPI/NestJS/UPDATER/AMAMO 等の PJ・framework 固有語は保持
- 実 PJ dry-run で FP 削減を実測

refs #554

---

## #568 bootstrap(#558): TF-IDF テーマクラスタの圧縮が短い日本語断片で弱い（61→48）— 距離/閾値チューニング  `[closed]`

## 観測（実 PJ E2E・amamo）
#558 のクラスタリングを amamo 実 weak_signals で非破壊検証した結果、61 グループ → **48 バケット**にしかならなかった（決定論は OK）。`_CLUSTER_DISTANCE_THRESHOLD=0.85` でも短い日本語断片は TF-IDF で束ねにくく、大半が 1 グループのまま。

## 問題
#558 の狙いは「45 グループの質問マラソン回避」だが、48 バケットでは圧縮が不足し狙いを十分達成できていない。

## 提案
- 短文向けの距離閾値再調整（実コーパス dry-run で 48→10 前後を目標）
- もしくは文字 n-gram / 既存 reorganize の別パラメータ採用
- バケット数の上限ガード（上限超過時はさらに粗いクラスタへ再帰）

## 受け入れ基準
- amamo 実データで bucket 数が AskUserQuestion で畳める規模（目安 ≤ 10）になる
- 決定論を維持

refs #558

---

## #569 fix(outcome_promotion_readiness): per_pj_rework も最小分母 floor を欠く（evidence 専用・#563-2 の同類残）  `[closed]`

## 背景（#563-2 の残）
#563-2 で readiness 条件1（variance, correction_recurrence）に `MIN_DISTINCT_TYPES_FLOOR` を適用し測定バグ FP を解消した。だが同モジュールの `per_pj_rework`（`scripts/lib/audit/outcome_promotion_readiness.py:164`）は依然 `round(rework_sessions / edit_sessions, 4)` で **`MIN_EDIT_SESSIONS_FLOOR` 未適用**。

## 影響
現状この値は `axes` evidence 表示専用で gate（3条件）には非関与のため実害は小さいが、将来 rework を条件に組み込むと #563 と同じ分母1で1.0張り付き FP が再発する。同類の潜在バグとして floor を入れておく。

## 提案
`per_pj_rework` で `edit_sessions < _om.MIN_EDIT_SESSIONS_FLOOR` の PJ は value=None（or 除外）にする。evidence 表示も「サンプル不足」を出す。

refs #563

---

## #574 fix(subagent-guard): subagent_observe が distinct agent でなく SubagentStop 記録数を数え偽の暴走警告を出す  `[closed]`  (bug)

## 症状

subagent-guard（`hooks/subagent_observe.py`）が、実際には distinct な subagent が 2〜3 個しか動いていないセッションで「直近 5 分で subagent が 17 個生成され、閾値 5 に達しました」と**偽の暴走警告**を出す。`subagent-guard.md` ルールに従い頭が作業を一時停止→ユーザー報告するため、誤発火のたびに無駄な中断が発生する。

## 根因

`_count_recent_session_subagents()`（`hooks/subagent_observe.py:33`）が時間窓内の **`subagents.jsonl` の記録行数**を数えている。一方 `handle_subagent_stop()`（同 :66）は **SubagentStop イベントごとに 1 行 append** する。

長命の background worker（`impl-worker` 等）は idle になるたびに SubagentStop を**再発火**するため、**同一 `agent_id` が複数行**書かれる。結果、distinct な subagent 数ではなく「停止イベント発生回数」を数えることになり、長命ワーカーを抱えるセッションでカウントが構造的に水増しされる。

時間窓（5 分）で測る意図（コメント:38-41「短時間に集中生成された暴走ループ/カスケードだけを捕捉」）に対し、実装は「同じ 1 個のワーカーが窓内で何度 idle になったか」まで加算してしまっている。

## 証拠（実データ）

`~/.claude/rl-anything/subagents.jsonl`、セッション `fe3653bc`（#567/#568/#569 follow-up を処理したセッション。実体は worker567 / worker568 + α）:

```
records: 90
distinct (agent_name, agent_id): 23
  18x  name='impl-worker'  id='a230c61b485654d01'
  13x  name='worker567'    id='ab9e9ad5dadad6d3a'
   9x  name='impl-worker'  id='aa21fc710aa227b7b'
   9x  name='worker568'    id='a9f6863868935d9c6'
   ...
```

同一 `agent_id` が最大 **18 回**記録されている。総記録 90 に対し distinct agent は 23 — 約 4 倍の水増し。窓を 5 分に絞っても、idle で再発火する 2〜3 個のワーカーだけで容易に閾値 5 を超える。

## 修正案

`_count_recent_session_subagents()` を **窓内の distinct `agent_id` 数**で数えるよう変更する（`agent_id` 欠落時は `agent_name` 等へフォールバック）。

```python
seen = set()
for line in ...:
    ...
    if rec.get("session_id") != session_id:
        continue
    ts = _parse_ts(rec.get("timestamp"))
    if ts is not None and ts >= cutoff:
        seen.add(rec.get("agent_id") or rec.get("agent_name") or id(rec))
return len(seen)
```

これにより「短時間に**新規**生成された subagent 数」という本来の意味論に戻り、長命 idle ワーカーの再発火で水増ししない。

## Acceptance Criteria

- [ ] `_count_recent_session_subagents` が窓内の distinct `agent_id` 数を返す（同一 `agent_id` の複数 SubagentStop を 1 と数える）
- [ ] `agent_id` 欠落レコードでも誤って 0/過大カウントにならない（フォールバック）
- [ ] 単体テスト: 同一 `agent_id` × N 行 → count=1 / 異なる id × N → count=N / 窓外は除外、を追加
- [ ] 実データ（session fe3653bc）で再計算し、閾値 5 を不当に超えないことを確認
- [ ] CHANGELOG に追記（fix・bump なし）

## 備考

これは measurement_bug 系（`audit/measurement_bug.py` が扱う「非自明な集計値の取り違え」）の一種。`distinct agent` と `stop イベント数` の取り違えは observability 系で再発しやすいので、テストで契約を固定したい。


---

## #577 [dogfood] multiview_eval: join キー名前空間不一致で実データ常に「該当視点なし」(#564 follow-up)  `[closed]`

## 発見（実PJ dogfood）

`/tech-eval` で実装した multiview_eval（#564, v1.103.0）を実PJ2つ（rl-anything / docs-platform）で動作確認したところ、**実データでは必ず「✓ 評価したが該当視点なし」しか出ない**繋ぎ目バグを発見。

## 根因: join 両辺のキー名前空間の不一致

`classify_multiview` は evolve 対象スキルと outcome を skill 名で join するが、両辺の名前空間が食い違う:

- `target_skills`（`sections_multiview._custom_skill_names` = SKILL.md ディレクトリ名）: 素の `cleanup` / `spec-keeper` / `docs-refresh`
- `outcome_attribution` キー（`attribute_outcomes` = 起動時のスキル名）: プラグイン修飾形 `rl-anything:cleanup` / `rl-anything:spec-keeper` / `rl-anything:docs-refresh`

→ 同一スキルが居るのにプレフィックスの有無で **交差が空集合**。実測（rl-anything）で `target_skills ∩ attribution = 空`。chaos は設計上 None、negative_transfer も 0 件なので、outcome 由来3視点（過学習/コスト増/再利用可能）が**構造的に発火不能**。

pytest が緑なのは合成 fixture が両辺のキーを bare で一致させているため（合成 fixture の false confidence）。

## 修正

`classify_multiview` の join 前にキーを bare skill 名へ正規化する `_bare_skill_name`（`<plugin>:` プレフィックス剥がし・`Agent:*` は agent 帰属なので join 対象外）を導入。outcome_attribution / negative_transfer / chaos に適用。

## 配線先（recurring ループ）

audit observability builder → `collect_observability` → evolve が `result[observability]` に格納。`/rl-anything:evolve` / `/rl-anything:audit` を回せば surface。

## 採用後の確認

- [ ] 実PJ（rl-anything）で `build_multiview_eval_section` を回し、`rl-anything:cleanup` 等が bare `cleanup` に join され outcome 由来ラベルが付く（または中立判定が根拠付きで出る）
- [ ] 名前空間付きキーで join を assert する回帰テスト追加（実データ相当）
- [ ] `Agent:*` キーが同名スキルに誤 join しないことを assert

---

## #578 [dogfood] relevance_gate: dedup 用閾値 0.5 流用で実コーパス全件 suppressed (#565 follow-up)  `[closed]`

## 発見（実PJ dogfood）

`/tech-eval` で実装した relevance_gate（#565 FinAcumen, v1.103.0）を rl-anything の実 weak_signals 287件で動作確認。機構は正常（採点・kept/suppressed 分離・理由付与・降順ソート 全て機能）だが、**実文脈では kept=0 / suppressed=287 に倒れる**（自由文文脈の jaccard が max ~0.25・中央値 0.0 で閾値 0.5 に到達しない）。「関連経験を残し無関係を抑制」が目的なのに**全件抑制の no-op** になっている。

## 根因: dedup 用の閾値を relevance gating に流用

`RELEVANCE_THRESHOLD = JACCARD_THRESHOLD`（=0.5）は bootstrap_backlog の **near-duplicate クラスタリング用**閾値の流用。relevance（過去経験が現文脈に関係するか）は near-duplicate より緩い関係なので、dedup 用 0.5 では実コーパスで到達不能。

overlap 係数への metric 変更も検討したが、実データでは「確認」「コミット」等の汎用語1語一致で overlap=1.0 の偽陽性が増えるため不採用。jaccard は1語一致を 1/N に自然減衰するので metric は据え置きが妥当。

## 修正

`RELEVANCE_THRESHOLD` を `JACCARD_THRESHOLD` から decouple し、実コーパス分布（max~0.25）に合わせた relevance 専用の校正値（0.2）に下げる。閾値は `--relevance-threshold` で従来通り上書き可能（#565 スコープ: 学習機構は作らない・固定/設定可能な定数で十分）。

## 配線先（recurring ループ）

`bin/rl-reflect --show-weak-signals --context <文脈>`。reflect 運用で過去経験を提案根拠に出す経路。

## 採用後の確認

- [ ] `bin/rl-reflect --show-weak-signals --context <実文脈>` で部分一致（jaccard ~0.25）の関連経験が kept に出る（旧 0.5 では全 suppress）
- [ ] 空文脈は従来通り gate_applied=False で全件 kept（安全側フォールバック維持）
- [ ] decouple 後の閾値 < JACCARD_THRESHOLD を assert する回帰テスト

---

## #583 [report-feedback] weak_signals「今日の修正確認 phase で昇格可能」案内と実導線が食い違う（過去未読分に入口がない）  `[closed]`  (bug,feedback)

## 背景
observability の correction_capture は「未昇格の llm_judge シグナルは今日の修正確認 phase で
昇格可能」と案内する。しかし実際には daily_review の `eligible=False`（＝前回以降の新規分のみ対象）
かつ bootstrap marker 済みのとき、**過去に溜まった未読分は昇格導線から構造的に外れる**。
「昇格可能」と言われても入口が無い状態になる。

## 提案
次のいずれか:
- 過去未読分を拾う明示導線を surface する（`/rl-anything:reflect --promote-weak` への誘導を
  daily/bootstrap とは別レーンで案内文に出す）。
- または案内文を実態に合わせる（「新規分のみ。過去分は reflect --promote-weak で」と明記）。

## 根拠
- correction_capture の案内文（observability）と daily_review.eligible の条件が不一致
- bootstrap marker 済み + daily eligible=False で過去 backlog が宙に浮く

<!-- rl-evolve-introspect:evolve-weak-signals-promotable-guidance-has-no-entry-point-for-backlog -->


---

## #584 [report-feedback] calibration_drift advisory が skill_evolve 未採点 PJ でも「あと N件」を表示し蓄積を誤読させる  `[closed]`  (bug,feedback)

## 背景
fitness_evolution の insufficient_data 表示は #479（`audit/sections.py:575` 付近）で
`structural_reason=skill_evolve_not_scored`（＝skill 提案が構造的に出ない PJ）を検出したら
「あと N件」を畳むよう既に対処済み。

しかし同じ環境で **calibration_drift 側の advisory（`audit/sections.py:601` 付近の
「calibration drift 判定は保留（あと {min_count - valid_count} 件）」）は別経路で残り**、
件数を出し続ける可能性がある。skill 提案が構造的に出ない PJ では calibration 母集団は
永久に貯まらないのに「あと N件」が出ると、「いつか溜まる」と誤読させる（蓄積前提の表示）。

## 提案
fitness_evolution と同じく、`structural_reason=skill_evolve_not_scored` と判定済みなら
calibration_drift advisory の件数表示（あと N件 / N/30）も畳む。
（#479 の guard を calibration_drift 経路にも適用する。）

## 根拠
- fitness_evolution は対処済み: `audit/sections.py:575-586`
- calibration_drift は件数表示が残存: `audit/sections.py:601`
- 実観測: large 環境で毎回 insufficient_data + calibration_drift advisory が出続ける

<!-- rl-evolve-introspect:evolve-calibration-drift-shows-accumulation-count-when-structurally-unscorable -->


---

## #585 [report-feedback] 高頻度の rule_violation_observed に hook_candidate / remediation 昇格導線がない  `[closed]`  (enhancement,feedback)

## 背景
builtin_replaceable は `tool_usage_hook_candidate` に昇格して remediation proposable に乗るのに、
`rule_violation_lane.py` が分離する **rule_violation_observed（例: rule_installed_but_not_enforced・
同一コマンドの 400回超違反）は surface のみ**で、hook 候補にも remediation proposable にも乗らない。
最も enforce すべき高頻度違反（rule 導入済みだが実行が止まっていない）が放置される。

## 提案
高頻度の rule_violation_observed を `tool_usage_hook_candidate` 相当の hook_candidate /
remediation proposable に昇格させる（閾値で gate）。レーン分離（`rule_violation_lane.py`）は
済んでいるので、その出力を hook 昇格経路に配線する。

## 根拠
- `scripts/lib/rule_violation_lane.py`: レーン分離は実装済み・surface のみ
- `scripts/lib/issue_schema.py` の make_hook_candidate_issue は builtin_replaceable 系のみ対象

<!-- rl-evolve-introspect:evolve-rule-violation-observed-has-no-enforce-hook-promotion-path -->


---

## #586 [report-feedback] prune が PJスコープ evolve でも global 候補をフル配列で result に積む（非効率）  `[closed]`  (enhancement,feedback)

## 背景
PJスコープの evolve でも prune の global 淘汰候補（実測 ~75件）を毎回フル配列で result に格納する。
SKILL.md 自身が「PJ単独では判断不能・1行に畳め・producer 最適化は別 issue」と既知の非効率と認めている。
result に数十KB を毎回生成する。

## 提案
PJスコープ時は producer 側で global 配列を **件数のみ**（とポインタ `bin/rl-fleet status`）に削る。
consumer 側の 1行畳みだけでなく producer 側で配列生成を止める。

## 根拠
- 実観測: global 候補 75件がフル配列で result に入る
- SKILL.md が producer 最適化を別 issue として明示

<!-- rl-evolve-introspect:evolve-prune-global-candidates-stored-as-full-array-in-result -->


---

## #587 [report-feedback] usage 計測復旧後に insufficient_usage / zero_invocations_suppressed の自動再評価が保証されているか不明  `[closed]`  (enhancement,feedback)

## 背景
usage 計測経路の修正により insufficient_usage 件数が skill_evolve 判定を保留し、
zero_invocations_suppressed が「計測待ち」になる。計測窓が修正日をまたぐ間の suppress は妥当だが、
**窓が修正日以降に揃ったときに自動再評価される保証/通知があるか不明**。
このままだと suppress が解除されず判定が永久保留になり得る。

## 提案
- suppress 解除時の再評価が確実に走ることを保証する（または解除予定日をレポートに surface）。
- 「計測窓が ◯◯ に揃えば再評価」のような解除予定を advisory として出す。

## 根拠
- insufficient_usage による skill_evolve 判定保留 / zero_invocations_suppressed「計測待ち」の挙動

<!-- rl-evolve-introspect:evolve-usage-suppress-no-guaranteed-reevaluation-on-window-recovery -->


---

## #588 [report-feedback] evolve 手順が長大で dry-run 記録可否が Step ごとに分岐し取り違えやすい  `[closed]`  (enhancement,feedback)

## 背景
evolve SKILL.md は Step 0.5〜11 + 多数の MUST があり、かつ「dry-run では記録しない」vs
「drain は dry-run でも実行」のように **記録可否が Step ごとに分岐**する。実行者が判断を取り違えやすく、
実際に長い手順の終盤で実行ミスが起きた。

## 提案
- dry-run 記録可否の一元表（どの Step が書き込むか）を SKILL.md 冒頭に置く。
- または各 Step の書き込み有無を機械可読フラグ化する（実行者が Step ごとに迷わない）。

## 根拠
- Step 数の多さ + 記録可否の Step 別分岐（mark_done / record_reviewed は dry_run ゲート、drain は別挙動）

<!-- rl-evolve-introspect:evolve-skill-procedure-dryrun-write-policy-not-centralized -->


---

## #590 [report-feedback] bootstrap の theme_buckets ラベルが日本語で文字n-gram断片になり選択不能  `[open]`  (enhancement)

## 背景
weak_signals の初回 bootstrap（Step 6.1）で group 数が閾値超のとき、決定論 TF-IDF で theme_buckets を生成し、各バケットの `theme_label` を AskUserQuestion の選択肢ラベルに使う。ユーザーはこのラベルを手がかりにどのバケットを昇格するか選ぶ設計。

## 問題
日本語のシグナルでは TF-IDF が文字レベル n-gram を拾い、`theme_label` が「、、 / って / んだ」「れれな / れれ / ない」「ペー / １ペ / １ペー」のような意味をなさない断片列になる。ラベルが内容を全く表さないため、バケット選択の手がかりにならず multiSelect の利点が失われる。

## 提案
- 日本語（CJK）テキストでは文字 n-gram ではなく分かち書き/単語境界ベースの特徴量に切り替える、または
- theme_label に代表シグナルの冒頭抜粋（representative の先頭 N 文字）を併記し、n-gram ラベル単独で出さない。

## 根拠（レポートの該当箇所）
bootstrap 12 group・theme_buckets 3 件のケースで、3 バケットすべてのラベルが上記のような文字断片になり、ラベルからは中身が判別不能だった。

<!-- rl-evolve-introspect:bootstrap-theme-label-cjk-ngram-noise -->


---

## #591 [report-feedback] confirmable_idiom にメッセージ全文が入り再発しない idiom が昇格対象になる  `[open]`  (enhancement)

## 背景
weak_signals の昇格時、`confirmable_idiom` を confirmed 化すると以後その表現の再発を自動昇格する（idiom_autopromote）。過汎用 idiom は #527 FP guard で除外され None になる。

## 問題
冗長な（複数行・自然文の）暗黙修正シグナルでは `confirmable_idiom` にメッセージ全文がそのまま入る。全文一致は実運用で二度と再発しないため、confirmed 化しても自動昇格の将来価値はゼロで、idiom 辞書にノイズが溜まるだけになる。FP guard は「過汎用」側だけを見ており「過特化（全文）」側を素通しする。

## 提案
- idiom の長さ/トークン数に上限を設け、上限超（実質メッセージ全文）の場合は confirmable_idiom を None にして standing auto-promote rule にしない（今回限りの昇格は許可）。
- もしくは idiom 抽出時に自然文をキーフレーズへ正規化してから再発判定に使う。

## 根拠（レポートの該当箇所）
bootstrap group の confirmable_idiom が複数行のメッセージ全文になっている group が複数あり、None 化されていたのは過汎用ガードに当たった一部のみだった。

<!-- rl-evolve-introspect:confirmable-idiom-full-message-never-recurs -->


---

## #592 [report-feedback] 用語集 jargon 検出が汎用技術語・stdlib名・フォーマットプレースホルダを誤検知する  `[open]`  (bug)

## 背景
glossary_drift は CONTEXT.md 不在の PJ で未登録 jargon 候補を抽出し、用語集 seed を促す。

## 問題
候補に PJ 固有でない語が多数混入する。具体的には:
- フォーマットプレースホルダ（全大文字の日付トークン `YYYY` 等）
- 標準ライブラリのクラス名（例: `ThreadPoolExecutor`）
- 広く知られた技術略語（例: `MP4` / `OAuth2` / `TTS` / `HF` / `HN`）

これらは用語集に登録する意味がなく、本当に固有のドメイン語が候補の中で埋もれて signal が薄まる。

## 提案
- 汎用技術語の allowlist による除外
- フォーマットプレースホルダ（`YYYY`/`MM`/`DD` 等の全大文字日付トークン）の正規表現除外
- 標準ライブラリ/フレームワークの既知シンボル名の除外

## 根拠（レポートの該当箇所）
jargon 候補 14 件中、上記カテゴリが過半を占め、固有語は数語のみだった。

<!-- rl-evolve-introspect:glossary-jargon-fp-generic-terms -->


---

## #593 [report-feedback] outcome データに worktree ディレクトリ名がそのまま slug として混入する  `[open]`  (bug)

## 背景
outcome weight promotion readiness（advisory）は cross-PJ の分母を slug ごとに集計して表示する。slug 正規化は resolve_slug（git-common-dir 親で正規化）で行う方針。

## 問題
分母実測の中に、PJ 名ではなく **worktree ディレクトリ名と同じ slug**（例: `evolve`）が独立した PJ として数十件規模でカウントされている。worktree から実行したときに `git rev-parse --show-toplevel` の basename（=worktree 名）が slug 化される既知の罠（resolve_slug が解くはずのもの）が、**書き込み側のどこかで取りこぼされている**可能性がある。読み取り側を resolve_slug 済みにしても、記録側に正規化漏れがあると別 PJ としてデータが蓄積され、cross-PJ 集計を汚染する。

## 提案
- optimize_history / outcome 系ストアの**書き込み箇所すべて**で resolve_slug が適用されているか検証する
- worktree 名と一致する slug が混入していないかの健全性チェックを追加し、検出時は警告する
- 既存データに混入があれば移行/マージする

## 根拠（レポートの該当箇所）
promotion readiness の分母一覧に、worktree 名相当の slug が分母 40 で出現していた。

<!-- rl-evolve-introspect:outcome-slug-worktree-name-pollution -->


> 💬 comment:
>
> ## 追加調査: 現行バグと確定（履歴残渣ではない）
> 
> 実データで slug 混入の発生時期と経路を追跡したところ、**現行の書き込み経路**で発生していることを確認した。
> 
> ### 確認できた事実
> - 混入していた phantom slug の実体は **worktree フルパス**（`<repo>/.claude/worktrees/<worktree-name>` 形）で、表示が basename だけを出すため別 PJ 名に見えていた。
> - 該当 correction は **40 件すべて直近日付**・同一秒に一括書込・`source: reflect_confirmed`。＝ reflect での weak_signal 昇格時に生成された**最近のデータ**で、過去残渣ではない。
> - 影響は **correction_recurrence 軸のみ**。session 軸（first_try_success）は正規化済みで phantom slug は出ない。
> 
> ### 根本原因（正規化漏れが2箇所）
> 1. **集計側**: `audit/outcome_promotion_readiness.py` の `_pj_of()` が `project_path` を生文字列のまま返し、worktree→親リポジトリの slug 正規化を通していない。session 軸は `_resolve_session_pj` + session_store の `_normalize_pj` で slug 化されるため phantom が出ない。**同じ正規化が correction 軸に適用されていない**のが直接原因。
> 2. **書込側**: `reflect_confirmed` の correction が `project_path` に worktree フルパス（cwd）をそのまま刻む。reflect を worktree から実行するたびに再発する。
> 
> ### 補足
> evolve 本体は Step 0.5 で resolve_slug（git-common-dir 親で正規化）を使い worktree 名混入を回避しているが、**corrections の書込経路と promotion-readiness の correction 集計はその安全な resolver を通っていない**。slug 規律が一部経路にしか適用されていない。
> 
> ### 提案する修正
> - `_pj_of()` に session 軸と同じ worktree 安全な slug 正規化（`pj_slug_from_cwd` 相当）を通す → 集計側は既存データも含め即解消。
> - 併せて correction 書込時に `project_path` を正規化する → ストア自体をクリーンにできる。
> - 表示（`_denominator_line`）は basename ではなくフルパスか正規化後 slug を出すと、worktree 由来の混入が一目で分かる。
> 

> 💬 comment:
>
> ## 追加調査2: 混入は複数ストア横断・複数書込点（scope 拡大）
> 
> corrections 以外の永続ストアも洗ったところ、同じ worktree slug 混入が **計3ストア**に存在し、**書込点ごとに記録形が異なる**ことを確認した。本 issue の scope は「特定の集計軸の正規化漏れ」ではなく「**複数の書込点が cwd を親リポジトリへ正規化せず stamp している**」点にある。
> 
> ### クロスストア棚卸し（worktree 名 = 別概念と衝突する slug）
> 
> | ストア | 件数 | 記録形 | 書込経路 |
> |--------|------|--------|----------|
> | corrections（jsonl） | 40 | **フルパス**（`project_path`） | reflect 昇格（reflect_confirmed） |
> | sessions（db） | 7 | **basename** | session append hook |
> | subagents（jsonl） | 4 | **basename** | subagent テレメトリ hook |
> | weak_signals / growth-state / per-slug ディレクトリ（evolve_decisions・triage_decisions・optimize_history・auto_memory_queue 等） | 0 | — | クリーン |
> 
> ### エピソードは2回
> - **作業セッション時**: sessions.db + subagents.jsonl に **basename** で記録。
> - **reflect 昇格時**: corrections.jsonl に **フルパス** で記録。
> 
> → 同一 worktree 由来でも、書込点（session append / subagent telemetry / reflect correction）ごとに basename だったりフルパスだったりと**形がバラバラ**で、いずれも親リポジトリ slug へ正規化していない。
> 
> ### 示唆
> - 集計側（`_pj_of` 等）の正規化だけ直しても、**書込形が経路ごとに違う**ため取りこぼす。`project` を stamp する全書込点で worktree 安全な slug 正規化を共通関数に寄せるのが本筋。
> - slug でキー化された後のストア（per-slug ディレクトリ・growth-state）には未波及。汚染は**キー化前の生テレメトリ**（sessions / subagents / corrections）に限局しており、書込時点の正規化で source を断てる。
> - 既存の混入レコードはバックフィル正規化（basename / worktree フルパス → 親 slug）で回収可能。
> 
> ### 補足（誤検知だったもの）
> ファイル名に "evolve" を含む `eval-sets/evolve.json` は **skill のトリガー eval セット**（`{"query": "/rl-anything:evolve", "should_trigger": true}`）であり、PJ slug 混入ではない。同様に `evolve-state.json` / `skill-evolve-*.json` は feature ファイルで無関係。
> 

> 💬 comment:
>
> ## 既存汚染データのバックフィル正規化（実施記録 + 横断スイープ）
> 
> 本 issue の混入は単一 worktree に留まらず、**複数 repo の worktree 名が複数ストアに広く混入**していた。ローカルで決定論バックフィルを実施したので、メンテナがコード修正時に既存データ回収を同梱できるよう手順と結果を残す。
> 
> ### 横断スイープ結果（worktree 名 → 親 repo・全ストア）
> - 親解決は FS の `<repo>/.claude/worktrees/<name>` から `name → repo` を一意決定（**名前の repo 跨ぎ衝突は無し**を確認）。
> - 混入は **2 形態**:
>   - フルパス（`project_path` に `/.claude/worktrees/<name>`）
>   - basename（`project` が worktree 名そのもの）
> - 混入ストア: `subagents` / `sessions(db: project 列 + raw_json)` / `usage` / `workflows` / `skill_activations` / `errors` / `usage-registry` / `corrections`。**slug キー化後のストア（per-slug ディレクトリ・growth-state）には未波及**。
> 
> ### バックフィルの要点（再現可能な recipe）
> 1. **ストアごとに正規化先の形が違う**ので合わせる:
>    - `corrections`: `project_path` は **abspath 慣習** → 親 repo の abspath。
>    - `sessions/subagents/usage/...`: `project` は **slug 慣習** → 親 repo basename。
> 2. **sessions.db は `project` 列だけでなく `raw_json` 内の `$.project` も置換**（読取経路 `read_session_records` が raw_json を返すため。列だけ直すと無意味）。
> 3. **冪等**（対象値の完全一致のみ置換）・**事前バックアップ**必須。
> 4. **AMBIGUOUS 名は除外**: worktree 名が実 PJ slug と衝突しうるもの（例では 1 名）は basename だけで一意化できないため backfill 対象外にした。
> 
> ### 回収不能性（コード修正が本質である理由）
> basename だけで記録された worktree セッションは、**書込時の完全パスが失われている**ため後付け回収は「worktree 名 → 親 repo」が一意な場合に限り可能。名前が実 PJ slug と衝突する場合は事実上回収不能。よって **書込時点で正規化する（cwd の完全パス → 親 repo slug）コード修正が唯一の恒久対策**で、backfill は補助に過ぎない。`project` を stamp する全書込点（session append / subagent telemetry / usage / workflows / skill_activations / errors / reflect correction）を共通の worktree 安全 slug 正規化に寄せるべき。
> 

> 💬 comment:
>
> **部分対応 #601 をマージしました**（`refs` で本 issue は open 維持）。
> 
> - write 側正規化（observe usage-registry / correction_detect corrections / promote reflect 昇格 の `project_path`）+ 集計側 `_pj_of` 統一 + 3ストアのバックフィル CLI（`bin/rl-fleet migrate-pj-slug`）。
> - これは `.claude/worktrees/` 配下の worktree のみ write 時に直る。
> 
> **残課題は #602 に切り出しました**:
> 1. **sibling ディレクトリ worktree**（`rl-anything-wt/issue-593` 等）は `pj_slug_fast` の構造的限界で write 時に親repoへ畳めない（hot-path で subprocess 禁止 → `resolve_pj_slug` 不可）。SessionStart 1回 resolve→cache 案。
> 2. バックフィル CLI の7ストア化（実汚染は usage/workflows/skill_activations/errors/usage-registry にも及ぶ）。
> 
> 実データ1,110件は別途回収済み（バックアップ有・冪等）だが、上記 write 時の恒久対策が入るまでは sibling worktree から再混入し得る。

---

## #594 [report-feedback] promotion readiness の条件表示が同一表現で母数が異なり矛盾に見える  `[open]`  (enhancement)

## 背景
outcome weight promotion readiness は3条件の充足を ✓/✗ で表示する。

## 問題
条件1（分散）と条件2（データ件数下限）がどちらも「PJ が N 件（≥2 必要）」という同一の表現を使うが、N の母数の意味が異なる（分散を満たす PJ 数 vs 分母を満たす PJ 数）。その結果、条件1「PJ が 0 件のみ」と条件2「PJ 2 件」が並んで一見矛盾しているように読める。

## 提案
各条件のラベルに母数の意味を明示する（例: 「分散を満たす PJ 数: 0」「分母 ≥N を満たす PJ 数: 2」）。

## 根拠（レポートの該当箇所）
条件1「分散が十分: PJ が 0 件のみ（≥2 必要）」と条件2「データ件数下限: PJ 2 件（≥2 必要）」が同一表現で並列表示されていた。

<!-- rl-evolve-introspect:promotion-readiness-pj-count-ambiguous-label -->


---

## #595 [report-feedback] discover の recommended_artifacts が却下/未導入の継続を抑制せず毎回再提示する疑い  `[open]`  (enhancement)

## 背景
discover は未導入の推奨 artifact（global rule/hook 等）を recommended_artifacts として surface する。

## 問題
ユーザーが意図的に導入しない選択をしても、同じ artifact が以後の evolve でも再提示され続ける（抑制記憶が無い）疑いがある。関連して、同種の修正に由来する低信頼 memory 要約が belief_entropy gate で繰り返し破棄されており、同じ素材から毎回生成→破棄のサイクルが起きうる。

## 提案
- recommended_artifacts に「未導入を継続」判断のクールダウン/suppression ledger を導入し、一定期間は再提示しない
- 併せて auto-memory キューで、繰り返し belief gate に block される素材を再エンキューしないようにする

## 根拠（レポートの該当箇所）
未導入の推奨 artifact が多数 surface され、同種の no-defer 系要約が belief_blocks に繰り返し計上されていた。なお「毎回再提示」かは単一実行では断定できず、観察ベースの提案。

<!-- rl-evolve-introspect:recommended-artifacts-no-decline-suppression -->


---

## #599 [tech-eval] SGCD: 失敗軌跡マイニング + 失敗の罠軸を skill_extractor に追加  `[open]`  (enhancement)

## 概要
SGCD (Skill-Guided Continuation Distillation for GUI Agents, arXiv 2606.18890) の発想を `skill_extractor` に取り込み、**成功軌跡だけでなく失敗軌跡もスキル抽出の素材化**し、スキル候補に「失敗の罠 / 成功基準」軸を追加する。

出典: `/tech-eval` で 2026-06-19 AI デイリーレポートを評価した結果（推奨度: 中）。

## Before / After（開発者体験の変化）
- **Before**: `skill_extractor` は成功軌跡のみから Workflow-to-Skill の4軸（routing/workflow/semantics/attachments）を抽出。failure は `trajectory_sampler.py:288` で `unknown` 扱いになり学習素材にならない。「失敗の罠」「成功基準」という軸が候補に存在しない。
- **After**: 失敗 rollout も抽出対象になり、スキル候補に「failure 由来である」フラグと「失敗の罠 / 成功基準」軸フィールドが付く。同種ミスの再発抑止に効く方向。
- UI 変化なし（evolve/discover レポートに section/軸が増えるのみ）。⚡速度・🛡安定性は間接効果。

## 既存実装との差分（根拠・ギャップ）
- `scripts/lib/skill_extractor/decomposition.py:1` — Workflow-to-Skill (arXiv 2606.06893) の4軸分解。SGCD の「continuation plan / key targets / failure traps / success criteria」軸は持たない。
- `scripts/lib/skill_extractor/trajectory_sampler.py:288` — outcome 判定は success/unknown のみ。failure を tool_result/error から判定する経路が未実装（コード内コメントでも将来課題と明記）。
- SGCD のもう一つの新規性「off-trajectory 状態の合成（スキルなしで数ステップ走らせて現実的な軌道外状態に到達 → スキル誘導で完遂）」は rl-anything の transcript 採掘ベース設計とは機構が異なる。**まずは failure-rollout マイニング + 失敗の罠軸の追加までをスコープとし、off-trajectory 合成は対象外**とする。

## 配線先（どの recurring ループで発火させるか）
- **evolve**（`skill_extractor` → `discover` が消費する recurring ループ）に乗せる。手動 CLI 止まりにしない。
  1. `trajectory_sampler` の outcome 判定に failure（tool_result / error 検出）を実装
  2. `decomposition` に「失敗の罠 / 成功基準」軸を追加
  3. discover が failure 由来候補も surface するよう配線

## 採用後の確認方法
- [ ] `/rl-anything:evolve` を回す → discover の skill 候補に `outcome: failure` 由来の候補と「失敗の罠」軸フィールドが現れる

## 再評価条件
- 失敗判定（tool_result / error からの failure 判定）の精度が実コーパスで取れるか dry-run 検証してから本実装。FP が多い場合は閾値・判定ロジックを再設計。


---

## #600 [tech-eval] RODS: reward 分散ベースの進化ターゲット選定を audit/evolve に追加  `[open]`  (enhancement)

## 概要
RODS (Reward-Driven Online Data Synthesis for Multi-Turn Tool-Use Agents, arXiv 2606.19047) の「**reward 報酬分散を能力境界の検出器として再利用する**」発想を、evolve/audit のスキル進化ターゲット選定に取り込む。

出典: `/tech-eval` で 2026-06-19 AI デイリーレポートを評価した結果（推奨度: 中）。

## Before / After（開発者体験の変化）
- **Before**: 進化ターゲット（次にどのスキルを evolve するか）の優先付けに reward 分散の概念は使われていない。
- **After**: 「reward 分散が高い = 成功と失敗が拮抗する能力境界付近 = 学習余地が最も大きい」スキルが outcome_attribution のターゲットランキング上位に自動で来る。「次に何を進化させるべきか」が分散ベースで surface される ✨。
- UI 変化なし（audit レポートのランキングに分散スコア列が増えるのみ）。

## 既存実装との差分（根拠・ギャップ）
- `scripts/lib/audit/outcome_promotion_readiness.py:210` `check_variance` — 分散は計算しているが用途は「全 PJ 同値 = 測定バグ強シグナル」の**測定バグ検出**であり、能力境界検出ではない。
- `scripts/lib/eval_saturation.py` — TASTE (arXiv 2605.28556) で eval 飽和を診断するが、これは「eval set が緑なのに頑健か飽和か」の診断で、進化ターゲット選定とは別軸。
- RODS の「進捗報酬分散をゼロコスト境界検出器に再利用し、境界サンプルの構造に合う新変種をスキル整合リサンプリングで合成、方策と共進化する動的リプレイバッファ」のうち、**online data synthesis / co-evolving replay buffer は GRPO 勾配ベース RL の機構であり rl-anything（LLM 1パスパッチ + regression gate、勾配学習なし）には乗らない**。
- **転用するのは「分散 → 学習余地ランキング」という targeting 発想のみ**にスコープを絞る。

## 配線先（どの recurring ループで発火させるか）
- **audit / evolve** に乗せる。`outcome_attribution`（per-skill 帰属 → evolve ターゲットランキング）のスコア入力に、既存 `check_variance` の分散計算を targeting 用途で転用し「高分散 = 学習余地大」のスコア列を足す。

## 採用後の確認方法
- [ ] `/rl-anything:audit` を回す → outcome_attribution のターゲットランキングに分散スコア列が出て、高分散スキルが上位に来る

## 再評価条件
- GRPO 勾配機構は持ち込まない前提を厳守。分散ベースのランキングが既存の outcome_attribution 順位と矛盾しないか dry-run で before/after 順位差分を確認してから本適用。


---

## #602 sibling-dir worktree の write 時 PJ slug 解決 + バックフィル7ストア化（#593 残課題）  `[open]`  (enhancement)

#601（部分対応）の follow-up。#593 の残課題2点を追跡する。

## 背景
#601 で `project_path` の write 側正規化（`pj_slug_fast`/`project_name_from_dir` 経由）+ 3ストアのバックフィル CLI を入れたが、構造的な穴が2つ残る。

## 残課題1: sibling ディレクトリ worktree が write 時に親repoへ畳めない
- `pj_slug_fast`（hooks hot-path 用・subprocess なし）は **文字列に `/.claude/worktrees/` マーカーがある時しか**親 repo に正規化できない（`scripts/lib/pj_slug.py:50-52`）。
- `tools/rl-anything-wt/issue-593` や figma の `fable5` のような **sibling ディレクトリ worktree**（`.claude/worktrees/` 配下でない）はマーカーが無く、basename が幻PJ slug として記録され続ける。
- 畳むには `resolve_pj_slug`（git-common-dir を引く subprocess 版）が必要だが、hooks は #492 の設計で per-fire subprocess 禁止。
- **案**: SessionStart で1回だけ `resolve_pj_slug` を実行して slug を env/marker に cache し、hooks はそれを読む（per-fire subprocess を避けつつ sibling worktree も親repoへ畳める）。read/write 同一 slug の原則（#492）を sibling worktree にも拡張する。

## 残課題2: バックフィル CLI を7ストアへ拡張
- 現状 `bin/rl-fleet migrate-pj-slug` は corrections / subagents / sessions.db の3ストアのみ。
- 実環境の汚染は **7ストア**（+ usage / workflows / skill_activations / errors / usage-registry）に及ぶことを横断スイープで確認（実データ1,110件は ad-hoc スクリプトで回収済み・バックアップ有だが、製品化 CLI は未カバー）。
- `pj_slug_backfill.py` を全7ストアへ拡張し、再現可能な恒久ツールにする。

## 受け入れ基準
- [ ] sibling worktree から書いても `project`/`project_path` が親repo slug になる（write 時・hot-path 制約維持）
- [ ] `bin/rl-fleet migrate-pj-slug` が7ストアを dry-run 既定・冪等で正規化
- [ ] basename 喪失ケース（フルパス情報なし）は復元不能として原値維持を明示
- [ ] 決定論・LLM 非依存・TDD

refs #593

---

## #618 [evolve introspect] discover フェーズで 'NoneType' object is not subscriptable が再発（#521 regression・errors.py:36 None ガード欠落）  `[open]`

## 自己解析: 実行時エラー（#521 の regression）

evolve の `discover` フェーズで例外が握り潰されている。フェーズは `{"error": ...}` を格納するだけなので result はトップレベルでは緑に見えるが、**discover 全機能が死に matched_skills / unmatched_patterns / reflect_data_count が全滅**する（reflect 件数が取得不能 = degraded）。

`#521`（CLOSED・同一 dedup_key `runtime_error:discover:nonetype-object-is-not-subscriptable`）で修正済みとされたが **2026-06-19 の docs-platform evolve で再発**。今回トレースバックで root cause を特定した。

### Root cause（特定済み）

```
File "scripts/lib/discover/runner.py", line 161, in run_discover
    errors = detect_error_patterns(project_root=project_root, include_unknown=include_unknown)
File "scripts/lib/discover/errors.py", line 36, in detect_error_patterns
    error = rec.get("error", "")[:200]
            ~~~~~~~~~~~~~~~~~~~~^^^^^^
TypeError: 'NoneType' object is not subscriptable
```

`errors.py:36` の `rec.get("error", "")[:200]` は **`"error"` キーが存在し値が明示的に `None`** のとき `None[:200]` で TypeError になる。`.get(..., "")` のデフォルトは「キー欠落」しか守らず「値が None」を守れない。#521 の修正がこの code path（error キーありで値 None のレコード）をカバーしていなかった regression。

### 修正案

`error = (rec.get("error") or "")[:200]` のように **None 合体**で守る。同様の `rec.get(key, default)[:N]` パターンが errors.py 内に他にもあれば併せて点検（同型の latent crash 防止）。

<!-- rl-evolve-introspect:runtime_error:discover:nonetype-object-is-not-subscriptable -->


---

## #619 dogfood-gate Layer3: report-feedback/SKILL.md の bash内inline-python を existence_only が誤検知（3件赤）  `[open]`  (bug)

## 概要

`bin/rl-dogfood-gate` の **Layer 3（SKILL.md コードブロック抽出実行）** が `skills/report-feedback/SKILL.md` の3ブロックで `existence_only` 赤を出す。pre-push hook（light）が毎 push で非ブロッキング警告を出し続けている。

## 症状（pre-push gate light のログ）

```
=== Layer 3: SKILL.md code blocks ===
  summary: pass=74 fail=3 skip=9
  ✗ report-feedback: 3 件の赤
       report-feedback/SKILL.md:84  [existence_only] missing: ['analysis', 'from', 'result']
       report-feedback/SKILL.md:142 [existence_only] missing: ['cands', 'existing', 'from', 'res']
       report-feedback/SKILL.md:176 [existence_only] missing: ['cand', 'from']
```

## 根本原因

3ブロックはいずれも **`bash` フェンスの中に inline `python3 -c '...'` を埋め込んだ**形（例: L84）:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/scripts/lib" python3 -c '
import json, sys
from evolve_introspect import flatten_candidates, summary_lines
result = json.load(open(sys.argv[1]))
analysis = result.get("self_analysis", {})
...'
```

Layer 3 の existence_only チェックが、この埋め込み Python のトークン（`from` / `result` / `analysis` / `cands` / `existing` / `res` / `cand`）を「未定義の参照シンボル」として拾ってしまう。これらは bash ブロック内 python ヒアドキュメントのローカル変数・キーワードで、**実際には欠落でも未定義でもない false positive**。bump とは無関係で v1.104.0（report-feedback 新設）から存在。

## 期待

Layer 3 が `bash` ブロック内の inline `python3 -c` を python として誤って existence チェックしない（または report-feedback の3ブロックが緑になる）。

## 修正案（triage で選択）

- **A. extractor 側（推奨）**: Layer 3 の existence_only 判定が、bash ブロック内に埋め込まれた `python3 -c '...'` の中身をシンボル抽出対象から除外する（あるいは bash ブロックは bash としてのみ解析する）。同型の bash+inline-python が他スキルにもあれば横展開で効く。
- **B. SKILL.md 側**: 該当3スニペットを `python` フェンス＋self-contained に書き換えるか、illustrative マーカー（skip 指定）を付ける。

A は extractor のロバスト性向上で再発予防になるが、B より調査範囲が広い。実コーパス（他スキルの bash+python 同型ブロック有無）を確認してから方針決定するのが安全。

## 参考
- 検出: `bin/rl-dogfood-gate --layer light`（または `--layer all`）の Layer 3
- 対象: `skills/report-feedback/SKILL.md:84,142,176`
- ログ: `/tmp/rl-prepush-gate.log`


---

## #620 [report-feedback] discover 全クラッシュ時に reflect_data_count が degraded sentinel(-1)でなく欠落(None)になり、文書化された `< 0` ガードが破綻する  `[open]`  (enhancement)

## 背景
discover フェーズが例外で全クラッシュすると、phase 出力は `{"error": ...}` だけになり `reflect_data_count` キー自体が存在しなくなる。下流が `.get("reflect_data_count")` で読むと値は **None** になる。

一方、evolve スキルの手順は degraded を **sentinel `-1`** として扱い『数値比較の前に `< 0` を判定する』と規定している。だが実際に降ってくるのは `-1` ではなく **None / キー欠落**である。

## 問題
- 契約の不一致: degraded を表す値が文書(`-1`)と実体(`None`/欠落)で食い違う。
- Python では `None < 0` は **TypeError**。文書どおり `< 0` で degraded 判定する消費側コードは、最も degraded なケース（discover 全クラッシュ）でかえって二次クラッシュする。

## 提案
- discover が全クラッシュした経路でも `reflect_data_count = -1`（既定の degraded sentinel）を必ずセットして契約を一本化する。あるいは消費側ガードを `count is None or count < 0` に統一する。
- どちらにせよ『degraded の表現を1つに正準化』し、`< 0` という比較が None に対して安全に評価されることを保証する。

## 根拠（レポートの該当箇所）
レポート上 reflect 件数が『不明』と表示され、phase 出力に reflect_data_count が存在しなかった（None）。discover 例外（別途 bug 起票済み）に連鎖して degraded 経路が踏まれたケース。

<!-- rl-evolve-introspect:reflect-count-degraded-sentinel-vs-none -->


---

## #621 [report-feedback] introspect の dedup が open issue のみ照合するため、close 済み issue の regression が前歴に紐付かず新規起票される  `[open]`  (enhancement)

## 背景
evolve_introspect / report-feedback の重複排除（filter_duplicates）は **open issue のみ** を照合対象にする。そのため、過去に『修正済み』として **close された issue が再発** すると、同じ dedup_key マーカーを持っていても重複と判定されず、前歴へのリンクなしに新規 issue が起票される。

## 問題
- 再発（regression）を新規起票すること自体は妥当だが、**直前に close された同一 root cause の issue へのバックリンクが付かない**ため、『一度直したはずが再発した』という重要な文脈（=不完全な修正だった事実）がレビュアーに伝わらない。
- 結果として同じ修正ミスを繰り返しやすい。

## 提案
- filter_duplicates が dedup_key マーカーで **closed issue にもヒット**した場合は、unique として起票しつつ body 冒頭に自動で『#N の regression（前回 closed）』を差し込む、もしくは closed issue を reopen する選択肢を提示する。
- 少なくとも『closed に同一マーカーあり』を呼び出し側へ surface し、人間が regression 文脈を添えられるようにする。

## 根拠（レポートの該当箇所）
決定論 self-analysis が検出した runtime error を起票する際、同一 dedup_key を持つ issue が既に存在したが close 済みのため open-only 照合では拾えず、regression である事実は人手で補う必要があった。

<!-- rl-evolve-introspect:introspect-dedup-ignores-closed-regressions -->

