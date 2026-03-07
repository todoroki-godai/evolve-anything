## ADDED Requirements

### Requirement: corrections からエラー分類して直接パッチを生成する

corrections.jsonl に対象スキルに関連する未適用レコード（`reflect_status` が `"applied"` でないもの）が存在する場合、`error_guided` モードで動作する（MUST）。corrections の `message`（修正メッセージ）/ `correction_type`（修正パターン分類）/ `extracted_learning`（抽出された学習）を分析し、エラーパターンを分類したうえで、スキルを直接パッチする LLM プロンプトを構築する（MUST）。LLM コール（`claude -p`）は 1 回で完了する（MUST）。

#### Scenario: corrections が存在する場合に error_guided モードで動作する

- **WHEN** `~/.claude/rl-anything/corrections.jsonl` に `last_skill` が対象スキル名と一致し、`reflect_status` が `"applied"` でないレコードが 1 件以上存在する
- **THEN** `error_guided` モードで動作し、corrections の `message` / `correction_type` / `extracted_learning` を LLM プロンプトに含めてスキルの改善版を 1 回の `claude -p` コールで生成する

#### Scenario: corrections が大量にある場合は直近 N 件に制限する

- **WHEN** 対象スキルに関連する corrections が `MAX_CORRECTIONS_PER_PATCH`（デフォルト 10）件を超える
- **THEN** 直近 `MAX_CORRECTIONS_PER_PATCH` 件に制限してプロンプトに含める

### Requirement: corrections なしでも LLM 1パス改善を実行する

corrections がない場合は `llm_improve` モードで動作する（MUST）。usage 統計（workflow_stats.json）、audit の構造的問題（collect_issues）、pitfalls.md の過去の失敗パターンをコンテキストとして LLM に渡し、1 回のコールでスキルを改善する（MUST）。

#### Scenario: corrections なしで llm_improve モードが動作する

- **WHEN** 対象スキルに関連する corrections が 0 件
- **THEN** `llm_improve` モードで動作し、usage 統計・audit issues・pitfalls をコンテキストに含めた LLM 1 回コールでスキル改善版を生成する

#### Scenario: コンテキストソースが存在しない場合でもフォールバックする

- **WHEN** workflow_stats.json も pitfalls.md も存在しない
- **THEN** スキル内容のみを入力として LLM 1パス改善を実行する（エラーにならない）

### Requirement: regression gate で品質ガードする

LLM が生成したパッチ結果に対して `_regression_gate()` を適用する（MUST）。ゲート不合格の場合は元のスキルを維持し、パッチを破棄する（MUST）。

#### Scenario: regression gate を通過する場合

- **WHEN** LLM が生成したパッチが行数制限内かつ禁止パターンを含まない
- **THEN** パッチ結果を候補として accept/reject 確認に進む

#### Scenario: regression gate に不合格の場合（行数超過）

- **WHEN** LLM が生成したパッチが行数制限を超過する
- **THEN** 「品質ゲート不合格: 行数制限超過（{actual}行 > {limit}行）」を表示し、元のスキルを維持する。パッチは破棄される

#### Scenario: regression gate に不合格の場合（禁止パターン）

- **WHEN** LLM が生成したパッチが禁止パターンを含む
- **THEN** 「品質ゲート不合格: 禁止パターン検出（{pattern}）」を表示し、元のスキルを維持する。パッチは破棄される

#### Scenario: regression gate に不合格の場合（空コンテンツ）

- **WHEN** LLM が生成したパッチが空文字列
- **THEN** 「品質ゲート不合格: パッチ内容が空です」を表示し、元のスキルを維持する。パッチは破棄される

### Requirement: mode オプションで手動指定できる

`--mode error_guided|llm_improve|auto` オプションを提供する（MUST）。デフォルトは `auto`（corrections 有無で自動判定）。`auto` モードでは corrections 有無で自動判定する（MUST）。手動指定時は指定モードを優先するが、`error_guided` で corrections 0 件の場合は警告付きで `llm_improve` にフォールバックする（MUST）。

#### Scenario: auto モード（デフォルト）

- **WHEN** `--mode` を指定しない、または `--mode auto` を指定する
- **THEN** corrections 有無を判定して `error_guided` または `llm_improve` を自動選択する

#### Scenario: error_guided を手動指定して corrections がない場合

- **WHEN** `--mode error_guided` を指定したが corrections が 0 件
- **THEN** 「対象スキルの corrections が見つかりません。llm_improve モードにフォールバックします。」と警告を表示し `llm_improve` で実行する

### Requirement: LLM コール失敗時に元スキルを維持する

`claude -p` コールがタイムアウト・エラー・空レスポンスを返した場合、元のスキルを維持しエラーメッセージを表示する（MUST）。パッチは生成されない。

#### Scenario: LLM コールがタイムアウトする場合

- **WHEN** `claude -p` コールがタイムアウトまたはプロセスエラーで失敗する
- **THEN** 「LLM コール失敗: {エラー詳細}。元のスキルを維持します。」と表示し、元のスキルを維持する

#### Scenario: LLM コールが空レスポンスを返す場合

- **WHEN** `claude -p` コールが成功するが出力が空文字列
- **THEN** regression gate の空コンテンツチェックで不合格となり、元のスキルを維持する

### Requirement: コンテキスト収集失敗時に graceful degradation する

`_collect_context()` 内の各ソース（`collect_issues()`、workflow_stats.json 読み込み等）が例外を発生させた場合、該当ソースをスキップして残りのコンテキストで続行する（MUST）。

#### Scenario: collect_issues() が例外を発生させる場合

- **WHEN** `collect_issues()` 呼び出しが例外をスローする
- **THEN** audit issues を空として扱い、他のコンテキスト（corrections, workflow_stats, pitfalls）で LLM コールを続行する。警告をログに出力する

### Requirement: 遺伝的アルゴリズム関連モジュールを削除する

世代ループ専用の以下のモジュールを削除する（MUST）: `strategy_router.py`, `granularity.py`, `bandit_selector.py`, `early_stopping.py`, `model_cascade.py`, `parallel.py` 及びそれぞれのテストファイル。

#### Scenario: 削除後にテストが通る

- **WHEN** 上記モジュールを削除し、optimize.py から参照を除去する
- **THEN** 残存するテスト（新パイプラインのテスト）が全て pass する

### Requirement: 廃止オプションを使用した場合にエラーメッセージを表示する

`--generations`, `--population`, `--budget`, `--cascade`, `--parallel` は廃止とする（MUST）。これらを指定した場合はエラーメッセージで廃止を通知して終了する（MUST）。

#### Scenario: 廃止オプションを使用する

- **WHEN** `--generations 3` を指定して optimize を実行する
- **THEN** 「--generations は廃止されました。直接パッチモードでは世代ループは不要です。」と表示して終了する
