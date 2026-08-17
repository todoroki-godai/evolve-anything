# evolve-anything Plugin

> **Agent contract:** 作業開始前に
> [`docs/agent-contract/policy.md`](docs/agent-contract/policy.md) を全文読むこと。
> 通常のprimary executorはClaude Code、Codexはcold review・独立検証・ユーザー指定時に使う。
> runtime差は
> [`docs/agent-contract/capability-matrix.md`](docs/agent-contract/capability-matrix.md) が正典。

スキル/ルールの **自律進化パイプライン**、**修正フィードバックループ**、**直接パッチ最適化**、**fleet 観測・介入** を提供する Claude Code Plugin。

## 目指すユーザー体験（全機能の判断基準）

> **「記録は全自動・判断は朝の30秒・効果は週1の数字で実感」**（2026-08-04 ユーザー合意・#379）

**これは到達目標であって現状説明ではない**。この節を新機能の採否判定に使うときは、目標文でなく
**実測値の側**を基準にする（#376「数字が嘘をつかない」を自分の看板文言にも適用する）。

**到達状況の数値をこのファイルに書かない**。日付付きスナップショットを正典に置くと必ず腐り、
「古い数字が正典に居座る」という #376 そのものの病気になる。現在値は下記が実行時に出すライブ値だけを
根拠にする（2026-08-15 codex レビュー）。

```bash
bin/evolve-audit --growth  # 戦果ボード = 柱3（指摘率の測定可否・採用件数・除外内訳）
bin/evolve-revert --list   # 柱4（採用のうち戻せる件数）
```

1. **普段**: ユーザーは各PJで普通にチャットするだけ。プラグインの存在を忘れている（observe は全自動・無音）
2. **朝**: セッション開始の1行通知 → 改善案を**ユーザーの言葉**で1件ずつ提示 → y/n だけ。cd もコマンド暗記も不要
3. **週1**: 戦果ボードで事実を棚卸しする（採用件数・一発成功率・戻せる採用の一覧）。
   **効果の因果判定（「手直し回数の減少」＝(a) / 「採用した改善が効いたか」＝(b)）は分母が揃った
   項目だけ表示し、揃うまで `not_measured` と明示する**。効いていないものの自動取り下げも (b) が
   計測可能になって初めて発火する。**表示条件**: (a) は全量判定した週が4週連続（`correction_rate`）、
   (b) の分母は revert 済みを畳んだ有効 accept 件数（`results_board`）。**どちらも現在値は
   `bin/evolve-audit --growth` で確認する**（ADR-054 §5・§7.2）
4. **信頼**: 表示する数字が嘘をつかない（#376）/ 適用は必ず人間の y/n（無人適用しない）/
   skill 採用は1コマンドで戻せる（**適用範囲: evolve drain 経由の新規採用のみ**。optimize.py 経路と
   evolve-loop 経路は revert 対象外＝ADR-054 Phase D PR2/PR3 を凍結した。採用実績が乏しく
   投資に見合わないため。使われ始めたら解凍する）

**判断規則**: 新機能・変更・tech-eval 取り込みは、この体験の 1〜4 のどこかを直接強化するものだけ採用する。この流れに登場しないものは icebox（#379 縮小方針）。

**新設凍結（#379 Step 1）**: 縮小完了まで新 store / observability section / advisory proposal adapter / weak_signal channel の追加は停止する（削除は許容）。単一ソースは `scripts/lib/shrink_freeze.py`。契約テスト（`test_shrink_freeze.py`）が CI portable suite で blocking 強制、pre-push light は同内容を非ブロッキング advisory として早期警告。store / weak_signal channel の runtime 書込みも `store_write_raw` / `append_signals` の凍結ゲートで reject する。`scaffold_advisory --write` も凍結中は拒否する。

**表示淘汰（#379 Step 2）**: 人間の行動に繋がった実証のない observability section 33 件を audit の表示から外す（**コードは削除しない・builder は `_OBSERVABILITY_BUILDERS` に登録されたまま**）。単一ソースは `shrink_freeze.CULLED_OBSERVABILITY_SECTIONS`。淘汰した事実は `display_cull` の 1 行 meta として必ず surface する（silence != evaluated）。環境変数 `EVOLVE_SHOW_CULLED=1` で一時的に全表示へ戻せる。

## 4つの柱

| 柱 | スキル | 説明 |
|----|--------|------|
| 自律進化 | evolve, discover, reorganize, prune, audit | Observe → Diagnose → Compile → Housekeeping → Report の3ステージパイプライン |
| フィードバック | reflect, report-feedback | reflect=修正パターン検出 → corrections.jsonl → CLAUDE.md/rules に反映。report-feedback=evolve/audit レポートを LLM メタレビュー → evolve-anything 自身への改善 issue を todoroki-godai/evolve-anything に半自動起票（決定論 evolve_introspect が拾えない「読んで気づく」改善が対象。旧 feedback スキルの後継） |
| 直接パッチ最適化 | optimize, evolve-loop, generate-fitness, evolve-fitness | corrections/context → LLM 1パスパッチ → regression gate（`scripts/lib/regression_gate.py` に共通化）+ GEPA 数値ガードレール（入力/パッチ char 上限・実データ dry-run 較正・#120） |
| **fleet 観測・介入** | fleet (`bin/evolve-fleet`) | 全 PJ 横断で env_score / 導入状況を一覧表示。`status` / `tokens` / `test-guard status`（no-llm-in-tests / pytest-no-llm 導入状況）/ `discover` / `recall`（全 PJ memory を keyword 横断検索、決定論・LLM 非依存）/ `plugins`（インストール済み CC プラグインの最新性診断 — update/drift/unknown を決定論検出。version 無しプラグインの silent stale を cache↔marketplace source の差分で検出）/ `queue`（学習素材ベースで「今 evolve すべき PJ」を決定論・ゼロ LLM で列挙 — weak 未処理 + 新規 corr の合算が閾値以上の PJ・#79）/ `propose`（queue 待ち PJ に evolve --dry-run 提案をバッチ生成し集約レポート化・llm-batch-guard 承認ゲート付き・#81）/ `pr-start`/`pr-finish`（承認済み evolve 提案を worktree 隔離で commit→push→PR 化。適用そのものは対話 evolve のまま人間が行い、外殻の worktree 準備と push/PR だけを自動化。マージは常に人間・#82） |
| daily-evolve 入口 | queue | 全 PJ 横断の evolve 待ち一覧を表示し上から対話 evolve するガイド（pull 型・ADR-050 手動運用入口）。`evolve-fleet queue` の薄いラッパー（read-only・ゼロ LLM）+ 次アクション提示。`/cd <PJ>`→`/evolve-anything:evolve` の導線。CC 起動後タイミングの良い日に手で叩く想定（#80 launchd 自動登録の代替手段） |
| モデルティア変更 | tier | `bin/evolve-tier`（#193）の対話 UX ラッパー。現状表示（`show`）→ ユーザー発話から tier/model/effort を解釈（曖昧なら `AskUserQuestion`）→ `set` で正典更新 → `sync` の dry-run diff を全件提示 → **明示承認後にのみ** `sync --apply` → `drift` advisory 表示、の順でモデルティア正典を安全に変更。スキル自体はファイルを直接編集せず全変更は CLI 経由 |
| エージェント管理 | agent-brushup | エージェント定義の品質診断・改善提案・新規作成・削除候補 |
| セカンドオピニオン | second-opinion | codex CLI 検出時は外部 cold-read ルートB、それ以外は Claude Agent ルートA によるセカンドオピニオン |
| 行き詰まり突破 | breakthrough | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動で解決 |
| 構造化実装 | implement | plan artifact → タスク分解 → 実装（single/parallel）→ 検証 → テレメトリ記録 |
| pitfall 運用 | pitfall-curate | 任意PJの pitfalls.md を育てる PJ非依存ツール。類似 dedup / 普遍性分類（universal/project/instance + 汎用度1-5）/ 三段階開示の配布版(Top-N)生成 / 記録↔分類↔配布の同期ゲート。判断は agent、決定論処理は `scripts/pitfall_curate.py`。`pitfall_manager`（自己進化専用）とは別物 |
| 仕様管理 | spec-keeper | SPEC.md + ADR の管理、Progressive Disclosure L1/L2 自動昇格 |
| 後片付け | cleanup | PR マージ・デプロイ後の痕跡（branches / worktrees / tmp dirs / Issues / Test plan 残件）を候補提示→個別承認→実行。tmp dir default prefix は `evolve-anything-` のみに安全側限定 |
| ユーティリティ | update, version | 更新・バージョン確認（backfill は #215 で CLI 削除→evolve 自動 ingest に統合、スキルは廃止リダイレクトのみ。旧 feedback スキルは report-feedback に統合し削除） |

## コンポーネント

各コンポーネントの設計経緯・根拠・issue/ADR 参照を含む詳細は **[spec/components.md](spec/components.md)**（SoT）。
ここは 1 行サマリのみ。**新コンポーネント追加・変更時は spec/components.md に詳細を書き、この一覧には 1 行だけ追記する（サマリは「何をするか1文 + 契約フラグ」で構成し目安 ≤130 字。`凍結`/`reject`/`dry-run`/`fail-open`/`人間承認`/`単一ソース`等の動作を縛る語は要約時も必ず残す）。**
**契約フラグを省略してよいかの判断基準**（cold に書いてあるかは基準にしない。**コンポーネント単位でなく不変条件単位**で判定する）: **その不変条件を全 write/遷移入口が必ず経由し、例外モード（warn 降格・fail-open・例外口）を含め常に reject する場合のみ、その条件は省略可**（例: `shrink_freeze.assert_no_new_keys` の凍結中新設 reject。降格経路なし）。**抜け道が1つでもある不変条件・通常ロジックやテストのみで守られている契約は hot に必ず残す**（例: `store_write` barrier 自身の未登録ストア reject も env `EVOLVE_WRITE_GUARD=warn` で降格できるため対象外／関数の単一ソース・TTL の read 時導出・dry-run 純度）。

**横断契約リスト**（#415 圧縮。cold の全コンポーネントのうち、上の判断基準で hot に残す必要がある挙動契約を持つもののみ抜粋。契約フラグを持たない残りは spec/components.md の4ドメインファイルを参照）:

- `evolve_decisions`: run envelope で並行 run を分離し未判断は deferred 保持。supersede は対象パス単位、flat `result_path` は run 1件時のみ
- `file_lock`: ファイル単位排他ロックと atomic write の単一ソース。ロック下からは `_locked` 版を使い自己 deadlock を回避
- `evolve_revert`: 採用した skill diff を戻す apply engine。conflict は上書きせず中止、CLI は既定 dry-run・`--apply` のみ実書込（entry_id は戦果ボードか --list が印字する。#402・既定 dry-run）
- `optimize_history` の effective view: revert 済み accept を判断母集団から畳む `fold_effective` が単一ソース。業務 reader は `load_effective_history`、raw は allowlist 3件のみ
- `raw_history_gate`: raw history read を AST で閉じた allowlist に固定。許可の単一ソースは production 定数
- `auto_memory_runner/broker`: auto-memory の enqueue（ゼロ LLM）+ 2相生成・書込。project スコープ4層防御で他PJ混入を reject
- `triage_ledger`: SKIP 判断の状態管理（TTL 45日・再発昇格・dry-run 非書込）
- pitfall 自動強制: pitfalls.md の編集時 lint + commit ゲート（オプトイン）。danger 判定は commit をブロック
- observability contract: 必ず surface すべき observability 行の単一ソース
- `store_write` write barrier: 全ストア書込の単一ゲート。store_registry の active 登録外は既定 reject、registry 不在は fail-open（例外口 `store_write_raw`）
- `outcome_attribution`: 負の転移は末尾 rollback、dry-run に before/after 順位差分を surface
- `weak_signals`: 45日 TTL は read 時 age 導出で writer-death 非依存
- `correction_semantic`: フェーズ昇格は human-source のみ駆動
- `judge_runner` / `safe_llm_call`: 無人呼び出しは `safe_llm_call` に一点集約し4重防御、費用は呼び出し直前に事前予約
- `daily_review`: 新規 weak_signal を最大5件 y/n 確認し promote 成功後のみ既読追記（部分失敗は対象外）
- `review_channels`: y/n 確認に出す weak チャネルの単一ソース。content-rich チャネルのみ対象
- `idiom_autopromote`: confirmed idiom の再発 weak_signal を機械昇格。**#379 Step1 で凍結中、`autopromote()` は no-op**
- `growth_report`: あと N 件で次フェーズ。閾値は growth_engine が単一ソース
- `correction_rate`: 3ストア read 時 join・freeze cutoff・カバレッジ100%確定週のみ表示・k週連続ゲート
- `subagent_noise`: subagents.jsonl の agent_type ノイズ内訳を advisory 分解表示。判定は `noise_agent_type_kind` が単一ソース
- `verbosity`: Haiku バッチ判定が weak_signals へ emit、auto-apply しない
- `cross_pj_priority`: confirmed idiom の PJ 横断優先提示（提示のみ・自動承認しない）
- `plugin_self` origin: プラグイン本体 repo 直下 skills/ を診断対象化。auto-apply は人間承認必須に降格
- `scaffold_advisory`: advisory 3点セット追加の scaffold — builder stub 生成 + 配線チェックリスト。CLI は既定 dry-run
- `dogfood gate`: 通し評価ゲート — 3層検査（dry-run 不変/report invariants/コードブロック実行）。`--layer light` は pre-push で非ブロッキング自動実行
- `pj_slug`: PJ slug 導出の単一ソース。read/write 同一関数で worktree slug 食い違いを防止
- weak_signals drain 永続化: 決定論3チャネルの永続化を `evolve --drain` の apply 境界に配線。pending marker の dry-run 書込は意図された設計（消さない）
- reconcile_surfaced drain 永続化: remediation 連続提示の count marker 書込と閾値到達時の自動却下を `evolve --drain` の apply 境界へ移設。phases の dry-run は `persist=False` で非書込
- `idiom_filter`: 過汎用 idiom の FP guard。SKILL.md の AskUserQuestion で idiom 単位拒否も可能
- recall validity-aware ranking: stale/superseded memory を validity metadata で降格（ハード除外はしない）
- subagents/errors 測定バグ修正: subagents.jsonl の agent_type ノイズを writer/reader 二重防御（`is_noise_agent_type` 単一ソース）で遮断 + errors.jsonl の error_type unknown を決定論分類
- `memory_capability`: memory dir 解決は `resolve_cc_memory_dir` が単一ソース
- `skill_vuln_scan`: remote_exec/secret_exfil 等を combo 必須で検出
- `memory_guard`: auto-memory 書込境界の runtime 記憶汚染検出。prompt_injection/secret_exfil を reject（検査失敗は fail-open）。同名エントリの上書きは決定論遷移検証でゲート
- `daily`: 毎朝の evolve queue 自動実行。適用は対話で人間承認
- `icebox_notice`: fail-open で既存ファイル非破壊、閾値未満は無音
- `memory_hygiene`: 重複残骸は手順提案のみで auto-apply しない
- `invalid_frontmatter`: 壊れた frontmatter で発火不能なスキルを直接 surface（auto-fix せず人手修正提案）
- `evolve-tier`: モデルティア正典を一元化する CLI。sync は既定 dry-run、`--apply` のみ書込
- `evaluation_provenance`: 評価スコアに紐づく実行条件の記録契約。envelope が単一ソース。不明値は推測せず None
- `fleet_propose`: queue 待ち PJ に evolve --dry-run を順次実行し提案を集約レポート化。reject 済み提案は再提示しない
- `fleet_pr`: 承認済み evolve 提案を repo 外 worktree で commit→push→PR 化。path allowlist・push account guard で強制、マージは人間
- `runtime_telemetry`: usage/sessions/errors の hook record に runtime を較正追加。**Codex hook 配線は保留**
- `codex_usage`: codex CLI 利用状況を advisory 表示（fail-open）。CC 側 token_usage とは合算しない

## クイックスタート

日次運用は `/evolve-anything:evolve`（取り込み+改善提案）、健康診断は `/evolve-anything:audit`。
コマンド全量（fleet 各サブコマンド・evolve-tier・evolve-revert 等）は [spec/quickstart.md](spec/quickstart.md) に集約。
旧 `/evolve-anything:backfill` は #215 で CLI 削除済みの幻なので廃止。

## 適応度関数

組み込み8個: `default`（LLM汎用評価）、`skill_quality`（ルールベース構造品質）、`coherence`（構造的整合性4軸）、`telemetry`（テレメトリ3軸）、`constitutional`（原則ベースLLM Judge評価 + /cso security軸）、`chaos`（仮想除去ロバストネス）、`environment`（coherence+telemetry+constitutional+skill_quality 動的重み統合、`config.py` で閾値集約）、`plugin`（evolve-anything 用プラグイン統合 fitness）。
プロジェクト固有: `scripts/rl/fitness/{name}.py` に配置 → `--fitness {name}` で使用。
環境スコア: `audit --coherence-score --telemetry-score --constitutional-score` で構造品質+行動実績+原則遵守の統合スコアを表示。

詳細は [README.ja.md](README.ja.md#適応度関数) を参照。

## evolve-scorer のドメイン自動判定

CLAUDE.md からドメイン（ゲーム/API/Bot/ドキュメント）を推定し評価軸を自動切替。
詳細は [README.ja.md](README.ja.md#evolve-scorer-のドメイン自動判定) を参照。

## Superpowers 共存

Superpowers プラグインがインストールされている場合、メタ操作時（evolve/audit/reflect/optimize/discover）は Superpowers の TDD/SDD/debugging スキルを発火させない。開発タスク時はフル活用する。

## Compaction Instructions

コンテキスト圧縮時、以下の情報をサマリーに必ず含めること:

1. **完了済みタスクと未完了タスクの区別** — 完了タスクを再実行しないこと
2. **呼び出されたスキルの実行結果** — 完了/未完了/エラーの状態
3. **変更したファイルの一覧** — パスと変更内容の要約
4. **ユーザーの最後の指示** — 次に何をすべきかの文脈

## テスト

```bash
cd <PLUGIN_DIR>
# bare コマンドで全件走る（pytest.ini の `testpaths` が単一ソース。新しい tests/ を足したら追記する）
python3 -m pytest -v

# プラグイン定義の整合性チェック
claude plugin validate
```

経緯・所要時間・keyset snapshot の設計等の詳細は [spec/testing.md](spec/testing.md) 参照。
運用上ここだけは必ず押さえる:
- 実 `~/.claude` を読む必要があるテストは `@pytest.mark.real_home`（または `bench`/`bench_ingest`）で opt-out する。root conftest の HOME 隔離 autouse は未 opt-out のテストを静かに緑にしうる
- **並行 worker に回させるときは `-n 0` で直列**（targeted テストまで多プロセス化し CPU 飢餓するため）
- `UPDATE_SNAPSHOTS=1` は golden 上書きでなく既存キーとの union merge（条件付きキーを golden から消さない、宣言済み prefix の増減のみ許容する二層 golden 方式）

リリース前は `bin/evolve-dogfood-gate --layer all` も全緑を確認する。日常 push は軽量な `--layer light` が `pre-push` hook 経由で非ブロッキング警告として自動実行される。

## Specification
- 現在の仕様全体像: [SPEC.md](SPEC.md)
- コンポーネント詳細（設計経緯・issue/ADR 参照の SoT）: [spec/components.md](spec/components.md)
- 用語集（Ubiquitous Language）: [CONTEXT.md](CONTEXT.md) — PJ 固有 jargon を 1 語で decode。鮮度は `scripts/lib/glossary_drift.py` が検出し spec-keeper update が advisory 提示。新概念を入れたら CONTEXT.md に 1 行追記する
- 詳細仕様: [spec/](spec/)
- 設計判断の記録: [docs/decisions/](docs/decisions/)
