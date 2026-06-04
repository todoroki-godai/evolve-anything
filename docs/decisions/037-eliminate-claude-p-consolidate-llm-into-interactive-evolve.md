# ADR-037: claude -p 全廃と LLM の interactive evolve 集約

Date: 2026-06-04
Status: Accepted
Related: (issue 未起票)

## Context

Anthropic は 2026-06-15 から、サブスクプラン上の **Agent SDK / `claude -p`（non-interactive mode）の利用**を、対話型の利用枠とは別の「月次 Agent SDK クレジット」へ分離する。Max 20x のクレジット額は **$200/月**で、ロールオーバー無し。公式 support 記事がクレジット対象を明示列挙している:

- 対象（programmatic）: Agent SDK（Python/TS）、**`claude -p`（non-interactive mode）**、Agent SDK 経由の third-party app
- 非対象（従来サブスク）: **Interactive Claude Code in the terminal or IDE**

→ **課金境界は「起動方式」で引かれている**（`claude -p` バイナリ非対話起動 vs 対話ターミナル）。概念的な「自動化かどうか」ではない。

rl-anything は内部で `claude -p` / `claude --print` を **9 経路**呼んでおり、全て 6/15 以降クレジット枠を消費する:

- skill 起動（同期・interactive ターン内）: `skill_evolve/llm_scoring`・`proposal`・`world_context`（evolve）、`quality_monitor`・`constitutional`・`principles`・`score_noise`（audit/fitness）、`semantic_detector`（reflect）、`fixers_rules`・`fixers_quality`（optimize）、`critical_instruction_extractor`（evolve-skill）
- hook 起動（非同期・ターン外）: `auto_memory_runner`（Stop hook）— **唯一の hook 由来**

2026-05 の実測（API list price 換算）では、one-shot セッション（`claude -p` の proxy）が **≈$1,522/月**、うち rl-anything 自身が $1,080。主コストは sonnet/opus の大プロンプト採点（cache_create 起因）で、auto_memory（Opus default）は $34 と小さい。**5月ペースのままだと $200 クレジットを約 7.6 倍超過する**。

加えて確認できた構造的事実:
- skill 起動分は全て「ユーザーが skill を叩く＝assistant が制御を握っている」タイミングで走る
- **cron/headless で evolve/audit を自律実行する配線は存在しない**（auto-trigger は「提案」のみ）。skill 起動分を interactive 側へ移しても壊れる自律パスが無い
- `auto_memory_runner` だけが Stop hook（ターン終了後・非同期）で、Task ツールを呼べない唯一の難所

## Decision

**`claude -p` を全廃し、LLM 消費を interactive な `/evolve` に集約する。**

1. **hook = 決定論のデータ収集のみ（LLM ゼロ）**
   corrections / usage / errors / file-changes は今まで通り hook が JSONL に蓄積する。`auto_memory_runner` の Stop hook 内 `claude -p` は削除する。

2. **`/evolve`（interactive = subscription）を唯一の LLM 消費口にする**
   既存 9 経路の LLM 作業（scoring / proposal / fix / context / reflect / memory 生成）を evolve のフェーズに集約する。reflect と memory 生成は現状「提案のみ／Stop hook 担当」だが、evolve のフェーズとして実行する形へ取り込む。

3. **Python = LLM-free の「前処理＋ゲート」に縮める**
   per-item で `claude -p` を N 回呼ぶのをやめ、Python は候補を JSON で吐き、決定論のゲートで受ける。副次効果として Python が完全に LLM 非依存になり、`no-llm-in-tests` ルールと完全整合する（mock 不要）。

4. **LLM 実行方式: インライン優先 / Task subagent は最適化**
   evolve の LLM 作業は、第一にメインの対話 assistant が**インライン**で実行する（"Interactive Claude Code in the terminal" に明確に該当＝零リスク）。並列化が要る採点等は Task subagent（既存 `rl-scorer` agent 等）に寄せる。Task subagent の課金分類は公式未明記だが、起動方式が `claude -p` でない以上 interactive 扱いと推定。6/15 後に請求ダッシュボードで実測し確定する。

## Alternatives Considered

### A. そのまま縮小（`--model haiku` 固定 + cache_create 削減）

`claude -p` を残し、モデル軽量化と差分採点で額だけ下げる。コード変更は小さい。
**不採用理由**: 課金枠は programmatic のまま。$200 に収めるには採点頻度を大幅に削る必要があり、品質との直接トレードオフ。境界が起動方式である以上、`claude -p` を残す限り根本解決にならない。

### B. defer-and-pickup（hook は marker だけ、次セッションで LLM）

Stop hook が pending marker を書き、次セッションの assistant が起動時に Task で処理する。auto_memory の「セッション終了時自動生成」感を維持できる。
**不採用理由**: ユーザーの狙いが「evolve に全部集約」なので、別経路の pickup 機構は配線が増えるだけ。memory 生成も evolve のフェーズへ吸収する方針と重複する。将来 auto_memory 相当の即時性が必要になれば再検討の余地はある。

### C. Python パイプラインの自己完結性を維持（`claude -p` 内蔵のまま）

Python が単体で LLM を呼べる現状設計を残す。headless/サーバでの自律 evolve が将来可能。
**不採用理由**: その自律配線は現状存在せず（実害ゼロの設計意図）、6/15 の課金分離で `claude -p` 内蔵がコスト直撃する。LLM-free 化は no-llm-in-tests とも整合するため、自己完結性より優先する。

## Consequences

- **良い影響**:
  - `claude -p` ゼロ化で LLM 消費が subscription 枠へ移り、$200 クレジットの超過リスクを根本回避
  - Python が完全に LLM 非依存になり `no-llm-in-tests` と完全整合（mock 撤廃でテストが堅牢化）
  - LLM 消費口が `/evolve` 一点に集約され、コスト・挙動の観測と制御が容易
  - hook が決定論のみになり高速・低リスク化（毎 Stop の Opus 呼び出し消滅）

- **悪い影響 / トレードオフ**:
  - Python パイプラインの自己完結性を失う（headless/cron での自律 LLM 実行は不可に。現状その配線は無いため実害ゼロ、設計意図のみ後退）
  - LLM 作業が interactive ターンに乗るため、evolve のターンが肥大化しうる（インライン vs Task の使い分け基準が要設計）
  - **残存曖昧**: Task subagent の課金分類が公式未明記。零リスクの fallback（インライン実行）があるため目的達成は保証されるが、subagent 並列化の課金は 6/15 後に実測確定が必要
  - 移行は ~9 モジュール + 複数 SKILL.md + 1 hook + agent 層に及ぶため phased 実装が必須（Phase1: skill 起動分の Task/インライン化、Phase2: auto_memory を evolve へ吸収＋Stop hook LLM 削除、Phase3: bench 整理）

## Phase 1a 実装メモ（共通基盤 + SKILL→CLI 経路の実証）

Phase1 は対象が ~10 モジュール + 4 SKILL.md に及び単一の実装単位として過大なため、サブフェーズ
（1a〜1d）に分割した。**Phase 1a** で共通基盤を抽出し、SKILL→CLI 直の2経路で「SKILL がオーケスト
レーションする」パターンを実証した（PoC の score_noise は CLI 単体止まりで未検証だった部分）。

### 共通基盤: `llm_broker.py`

3相分離の単一ソース。Python から claude -p を完全に追い出す:
- `build_requests(items, prompt_fn) -> [{"id","prompt","meta"}]`（Phase A）: id 以外を meta に保持し
  Phase C の集約に再利用（run/axis/skill_path 等）
- `parse_responses(requests, responses, parser) -> {id: parsed}`（Phase C）: **requests を単一ソース**に
  全 id を走査し、欠損 id は `parser(None)=fallback` で穴埋め（assistant の応答漏れで壊れない）
- パーサ: 採点系 `parse_score`（bool を数値扱いしない）/ 生成系 `passthrough`
- 完全 IO-free・LLM-free（no-llm-in-tests と整合、mock 不要）

### パイプライン埋め込み経路の機構決定（M1: ファイルベース2相）

Bash 境界で Python が Task を呼び返せないため、パイプライン内 LLM 点は次の2択だった:
- **M1（採用）**: LLM 点で emit→[SKILL が Phase B 採点]→ingest に分割。SKILL.md が
  `--emit-requests`（or `--emit-request`）→ assistant がインライン生成/採点 → `--ingest`（or
  `--save-from-response`）の3ステップを駆動する。各 Python CLI は純粋な前処理/ゲート。
- **M2（不採用）**: SKILL.md 自体をループにし Python を純関数化。evolve.py の制御フロー大改修で
  リスク大。M1 で十分に claude -p を追い出せ、パイプラインの構造を温存できる。

**audit パイプラインの decouple**: `run_audit`（Python）から `run_quality_monitor()` のインライン
LLM 呼び出しを削除し、`run_audit` は既存 baselines を読むだけの決定論パイプラインにした。再スコアは
audit SKILL.md Step 3 の2相でのみ走る。同型の decouple を 1b〜1d（evolve.py / reflect 等）にも適用する。

### Phase 1a で変換済み / 残存

- 変換済み（claude -p ゼロ、回帰ゲート `test_no_claude_p_phase1a.py` で固定）:
  `llm_broker`（新規）・`world_context`（`--emit-request`/`--save-from-response`）・
  `quality_monitor`（`--emit-requests`/`--ingest`）・`score_noise`（PoC, dogfood リファクタ）
- 残存（1b 以降、ゲートの `KNOWN_REMAINING` に明示＝silent 取りこぼし防止）:
  `score_noise._run_claude_prompt`（bin/rl-prompt-compare 後方互換）・
  scoring 系（`constitutional`/`principles`）・evolve 系（`skill_evolve/llm_scoring`/`proposal`）・
  reflect/remediation 系（`fixers_rules`/`fixers_quality`/`critical_instruction`/`semantic_detector`）

### 注入契約（1b 以降の指針）

パイプライン埋め込み経路を変換する際は M1 を踏襲する:
1. LLM 点を `emit_*_requests() -> {"requests": [...], "skipped": [...]}`（Phase A・決定論）へ
2. パース/集約を `ingest_*(requests, responses) -> {...}`（Phase C・決定論、`llm_broker.parse_responses` を使う）へ
3. その経路を呼ぶ Python パイプライン（run_audit 等）からはインライン LLM 呼び出しを削除し、
   既存成果物（baselines/履歴）を読むだけにする
4. 該当 SKILL.md に「emit → インライン Phase B → ingest」の3ステップを記述する
5. 変換完了後、`KNOWN_REMAINING` から `CONVERTED_MODULES` へ移す

## Phase 1b 実装メモ（scoring 系: constitutional / principles）

scoring 軸 `constitutional` / `principles` の claude -p を全廃した。両者ともすでにキャッシュを持ち、
**cache 命中時は LLM を呼ばない**設計だったため、1a の audit decouple と同型で「パイプラインは cache を
読むだけ／refresh は SKILL 2相」へ寄せた。

### 依存関係（2-round）

constitutional のレイヤー評価プロンプトには principles リストが埋め込まれる。よって LLM 点は
**principles 抽出（1 call）→ constitutional レイヤー評価（最大4 call）** の順依存になる。SKILL は必ず
**principles round を先に**回す（`emit_layer_requests` は `principles_missing` を返して順序違反を検知可能にした）。

### 変換内容

- `principles.py`: `_extract_via_llm`（claude -p）を削除。`build_extraction_request`（Phase A）/
  `ingest_principles`（Phase C）/ `_parse_principles_response`（パーサ）を追加。`extract_principles` は
  LLM-free 化（cache hit→cache / cache miss・refresh→seed-only **非永続**。SKILL の refresh で正式抽出させる）。
- `constitutional.py`: `_evaluate_layer`（claude -p）を `_parse_layer_response`（パーサ）へ。集約ロジックを
  `_aggregate_constitutional` へ抽出し `compute_constitutional_score`（cache-only）と `ingest_layer_responses`
  （Phase C）で共有。`emit_layer_requests`（Phase A）を追加。`compute_constitutional_score` は cache 命中
  レイヤーのみ集約し、全 miss なら None（LLM は呼ばない）。死蔵した `_estimate_cost`/Haiku 価格定数を削除。
- `environment.py`: `compute_environment_fitness` は cache-only read になった（`skip_llm` は据置。cache 未生成時の
  0.0 寄与で overall が歪むのを避けるため fleet 高速パスでは引き続き constitutional をスキップ）。
- SKILL.md: audit に **Step 3.5（Constitutional 再評価・2相）** を追加。evolve Step 3.7 から参照。

### Phase 1b で変換済み / 残存

- 変換済み（`CONVERTED_MODULES` に追加）: `constitutional`・`principles`
- 残存（1c 以降、`KNOWN_REMAINING`）: `score_noise._run_claude_prompt`（後方互換）・
  evolve 系（`skill_evolve/llm_scoring`・`proposal`）・reflect/remediation 系
  （`fixers_rules`・`fixers_quality`・`critical_instruction`・`semantic_detector`）

## Phase 1c 実装メモ（evolve 系: skill_evolve の judgment 採点 / テンプレカスタマイズ）

evolve 系の claude -p 2 箇所を全廃した。両者とも **既に決定論フォールバックを持っていた**ため、
1a/1b と同型の cache-only decouple に素直に寄せられた:

- `llm_scoring._score_judgment_complexity_llm`（判断複雑さ 1-3 採点。フォールバック=分岐語カウント）
- `proposal._customize_template`（テンプレをスキル文脈に整形。フォールバック=テンプレそのまま）

### 設計上の判断

- **`compute_llm_scores` / `evolve_skill_proposal` は LLM-free 化**。evolve バッチ（evolve.py Phase 3.4）
  と run_loop は実行を中断して Task を呼べないため、必ず決定論フォールバックで完走する。LLM 品質の採点／
  整形は SKILL の2相（emit → assistant inline → ingest）が後追いで cache を更新し、次回以降が使う。
- **judgment は `judgment_source: "static"|"llm"` フラグで cache に保存**。external_dependency は元々静的
  解析なので常に確定保存し、judgment は source で refresh 対象（static / 欠落 / hash 不一致）を区別する。
  旧 cache（フラグ無し）は "static" 扱い → 次の refresh で1回だけ LLM 値へ昇格＝収束する。
- **テンプレカスタマイズは非キャッシュ**。`evolve_skill_proposal` はテンプレそのまま（決定論）、LLM 整形は
  `emit_customize_request`→`ingest_customized_proposal` の2相のみ。fence 除去 + diff budget gate（#196,#199）
  は `_parse_customization_response`（Phase C）に集約。共通の proposal 組み立ては `_assemble_proposal` へ抽出。
- **SKILL は inline Python で2相を駆動**（constitutional/principles の CLI 経路ではなく、skill_evolve の既存
  inline スタイルに合わせた）。emit が決定論・冪等なので「emit→prompt 提示→再 emit + ingest」の2ブロックで
  状態を持たずに2相を表現できる。
- batch_guard は LLM-free 化後もバッチ規模の承認ゲートとして残置（LLM コストは Phase B refresh / apply へ移動）。

### Phase B 信頼境界

Phase B の書き手は assistant（非決定論プロデューサ）。`_parse_judgment_response` は int/str/dict を、
`_parse_customization_response` は str + fence を寛容に受ける（1a の `world_context._extract_world_dict`、
1b の各パーサと同じ方針）。抽出不能時は judgment=据え置き（static のまま）、customize=テンプレ・フォールバック。

### Phase 1c で変換済み / 残存

- 変換済み（`CONVERTED_MODULES` に追加）: `skill_evolve/llm_scoring`・`skill_evolve/proposal`
- 残存（1d 以降、`KNOWN_REMAINING`）: `score_noise._run_claude_prompt`（後方互換）・reflect/remediation 系
  （`fixers_rules`・`fixers_quality`・`critical_instruction`・`semantic_detector`）・Stop hook（`auto_memory_runner`、Phase 2）
