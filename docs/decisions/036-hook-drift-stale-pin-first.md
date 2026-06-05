# ADR-036: 他ツール追従 hook の陳腐化検出は stale_pin から始める

Date: 2026-06-04
Status: Accepted
Related: second-opinion レビュー, [ADR-028]（observability contract）, [ADR-033]（evolve_introspect）

## Context

`~/.claude/hooks/suggest-gstack-next-action.py` は gstack の Stop hook で、`~/.gstack/flow-chain.json`（フローチェーン定義）を参照して「次のアクション」を提案する。gstack 本体が進化（スキルの追加・rename・フロー変更）すると、この種の **他ツール追従 hook** は静的参照が腐り、古い／存在しないスキルを提案し続ける。

ユーザーの要望は「hook が役に立っているか・陳腐化していないかを rl-anything の evolve で評価したい」。最初の設計案は汎用 `hook_drift` モジュールで 3 種の drift（`dead_ref`＝参照先スキルの実在突合 / `internal_drift`＝hook 内ハードコード vs 外部宣言 / `stale_pin`＝参照ツールの version 乖離）を一括検出し、Tier 2 として hook ソースに `# rl-refs:` 宣言行を置く拡張も含んでいた。

`second-opinion` エージェントの独立レビューで、この初期設計は過剰（YAGNI・false positive リスク）と判定された:

- **`dead_ref` は表記ゆれで false positive を量産する**。flow-chain.json は `/rl-anything:implement` / `/review`、hook の FALLBACK は `/document-release` / `/spec-keeper update` と表記が混在する。正規化を固めずに実在突合すると「存在するスキルを死んでいると報告」するノイズが observability 経由で毎 evolve 出続け、audit 全体の信頼性を毀損する。これは `glossary_drift` が `undefined_terms` を gate しない（候補提示に留める）設計と整合しない — あちらは曖昧でも候補提示で済むが、dead_ref の誤検知は致命的。
- **`internal_drift` は実害ほぼゼロ**。flow-chain.json が読めれば hook は FALLBACK を使わない。1 ケースのために汎用突合機構を作るのは YAGNI。Tier 2 の `# rl-refs:` に至っては対象ファイルが現時点でゼロ。
- **有用性（follow-through）評価の前提が誤っていた**。「hook は発火痕跡を残さない」と判断して第2フェーズに送ったが、hook は STATE_FILE を書き、`skill-usage.jsonl` は実行スキルを記録している。発火を 1 行ログするだけで follow-through を後段で測れる。

## Decision

1. **第一フェーズは `stale_pin` のみを実装する**。`~/.gstack/flow-chain.json` の `gstack_version` と `~/.gstack/.last-setup-version`（実環境 version）を突合する。version 同士の単純比較なので **表記ゆれによる false positive が無い** ことが選定理由。`scripts/lib/hook_drift.py`（決定論・LLM 非依存、責務を version 突合に限定）。

2. **observability contract（[ADR-028]）に 1 行で配線する**。builder `build_hook_drift_section` を `scripts/lib/audit/sections_hook.py` に置き（sections.py が行数バジェットに迫るため `sections_eval.py` と同様に独立ファイル）、`_OBSERVABILITY_BUILDERS` に `("hook_drift", ...)` を登録。markdown / 構造化の両経路に自動伝播し、evolve のたびに surface される。gstack はグローバル（~/.gstack）のため builder は project_dir 非依存（eval-sets と同じ環境グローバル系）。

3. **silence ≠ evaluated を守る**。gstack 未導入環境（.gstack / flow-chain.json 不在）は None で沈黙。version 一致時は「評価したが drift なし ✓」、実 version 不明時は「判定保留 ℹ」を残す。

4. **hook 側は機構に頼らず即修正し、有用性の種をまく**。FALLBACK_CHAIN を SoT（flow-chain.json）と整合させ（`ship → /land-and-deploy → /rl-anything:spec-keeper update`）、提案 block を出す瞬間に `~/.gstack/analytics/hook-fires.jsonl` へ `{ts, skill, suggested_next}` を append する。これは follow-through 計測（第2フェーズ）のデータの種であり、`skill-usage.jsonl` と cross-ref して「提案 → 実行」率を後段で算出する。

5. **`dead_ref` / `internal_drift` / follow-through 評価は別 issue に切り出す**。dead_ref は live registry のスキル名正規化を固めてから、follow-through は fire-log が貯まってから着手する。

## Alternatives Considered

### 代替案A: 汎用 hook_drift（dead_ref + internal_drift + stale_pin）を一括実装
スコープが広く、dead_ref の false positive が observability 全体の信頼性を毀損する。第一例（suggest-gstack-next-action）の実害は stale_pin に集中しており（実測で flow-chain 1.47.0.0 vs 実環境 1.55.0.0）、一括実装は YAGNI。却下。

### 代替案B: hook の有用性（follow-through）評価を先に作る
評価軸としては本命だが、データ（fire-log）が無いと測れない。まず種をまき（Decision 4）、データが貯まってから診断を作るのが rl-anything の observe → diagnose 構造に整合する。先に診断機構だけ作っても空回りするため、順序として後。

### 代替案C: gstack 側に hook 健全性チェックを持たせる
責務論としてはあり得るが、gstack は skill-usage を記録するだけで hook の健全性に関知しない。「誰が事実情報（hook ファイル・flow-chain.json）にアクセスできるか」で判断すると、両方を読める rl-anything 側が監視を担うのが現実的。却下。

## Consequences

- flow-chain.json の `gstack_version` ピンが実環境（.last-setup-version）から取り残されると、evolve のたびに「flow-chain.json は gstack X 想定だが実環境は Y（MINOR N 差）」が surface され、ピンの手更新とフローチェーン見直しが促される。
- `hook_drift` の責務は version 突合に限定されており、スキル名の表記ゆれ問題に触れないため誤検知しない。dead_ref を足す際は正規化レイヤーをテストで固めてから contract に乗せる（本 ADR の教訓）。
- hook-fires.jsonl が蓄積を開始する。第2フェーズ（follow-through 評価）はこのログと skill-usage.jsonl の cross-ref で実装する。
- builder は `~/.gstack/.last-setup-version` と flow-chain.json の `gstack_version` キーの出力契約に依存する。gstack 側がこれらの場所/キーを変えるとテスト（`test_hook_drift.py`）が回帰検出する。

## Update（#319 — 実環境ドッグフードで前提崩れを発見）

初版 Context/Consequences は「flow-chain.json は gstack の setup/upgrade で再生成される」を暗黙の前提にしていたが、**これは誤り**だった。マージ後に実環境で stale_pin を解消しようと `gstack setup` の挙動を調べたところ:

- `~/.claude/skills/gstack/`（setup / bin 含む全体）を grep しても `flow-chain.json` への参照が **ゼロ** — gstack は一切このファイルを書き込まない。
- setup が触るのは `~/.gstack/.last-setup-version` のみ。
- `~/.gstack/flow-chain.json` は `/rl-anything:implement` 等を参照する **手動メンテのファイル**で、`gstack_version` は手書きのピン。

→ stale_pin の **検出ロジックは正しい**（ピンと実環境の乖離は事実、誤検知ではない）が、**解消ガイダンスが的外れ**だった。実際の解消は `gstack_version` ピンを手で実環境 version に更新する必要があり、`gstack setup` では消えない。これを受け docstring（`hook_drift.py` / `sections_hook.py`）と stale メッセージのガイダンス文言を「手動メンテ SoT・ピンを手更新」へ訂正した。教訓: 合成 fixture は「ピンが違えば検出する」までしかテストできず、「直し方の前提が正しいか」は本番データでしか炙り出せない（`learning_synthetic_fixture_false_confidence`）。
