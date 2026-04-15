# principle-extraction Specification

## Purpose
CLAUDE.md と Rules から PJ 固有の原則を LLM で抽出し、Constitutional Evaluation の入力として提供する。シード原則による Cold Start 解決、品質スコアリングによる低品質原則の除外、キャッシュによるコスト最小化を含む。

## Requirements
### Requirement: Principle extraction from CLAUDE.md and Rules
`principles.py` の `extract_principles()` は CLAUDE.md と Rules ファイルを入力として、`claude -p` 経由で LLM に PJ 固有の原則リストを抽出させなければならない（MUST）。各原則は `id`（kebab-case）、`text`（原則の記述）、`source`（抽出元ファイルパス）、`category`（quality/safety/performance/convention/philosophy のいずれか）を含まなければならない（MUST）。`philosophy` カテゴリは静的コンテンツでは検証不可能な会話・行動レベルの原則を表し、`user_defined: true` でユーザーが手動登録する用途を主とする。

#### Scenario: CLAUDE.md から原則を抽出
- **WHEN** CLAUDE.md に「LLMコール最小化」「べき等性保証」「ユーザー承認なしに変更しない」が記述されている
- **THEN** 少なくとも 3 つの原則が抽出され、各原則に id, text, source, category が含まれる

#### Scenario: Rules からも原則を抽出
- **WHEN** `.claude/rules/commit-version.md` に「version bump 要否を確認」が記述されている
- **THEN** 該当 Rule から原則が抽出され、source にルールファイルのパスが含まれる

### Requirement: Seed principles
`extract_principles()` は LLM 抽出結果に加え、コード内の `SEED_PRINCIPLES` 配列を常にデフォルトで含めなければならない（MUST）。シード原則は `"seed": true` フラグを持ち、`--refresh` 時にも常に含まれなければならない（MUST）。

シード原則は2系統に分類される:
- **コア原則**（カテゴリ: `quality` / `safety` / `performance`）: `single-responsibility`, `graceful-degradation`, `user-consent`, `idempotency`, `minimal-llm-cost` 等。プラグイン全体の不変な動作原則
- **哲学原則**（カテゴリ: `philosophy`）: `think-before-coding`, `simplicity-first`, `surgical-changes`, `goal-driven-execution` 等。会話・行動レベルの普遍的コーディング哲学。`philosophy-review` スキルの評価対象として利用される

シード原則の追加・改訂は `SEED_PRINCIPLES` 配列の編集で行い、本 spec の更新を伴わない数の変更はテストと配列の二重管理を避けるためである。

#### Scenario: CLAUDE.md が空でもシード原則が利用可能
- **WHEN** CLAUDE.md が存在しない、または内容が空である
- **THEN** `SEED_PRINCIPLES` 配列の全要素が返され、各原則に `"seed": true` が含まれる

#### Scenario: LLM 抽出原則とシード原則のマージ
- **WHEN** LLM が N 件の原則を抽出する
- **THEN** シード原則全件 + LLM 抽出 N 件が返される（重複する場合は LLM 抽出が優先）

#### Scenario: 哲学原則カテゴリの存在
- **WHEN** `extract_principles()` の結果から `category == "philosophy"` でフィルタする
- **THEN** Karpathy 由来の哲学原則（`think-before-coding` 等）が含まれる

### Requirement: Principle quality scoring
`extract_principles()` は各抽出原則の品質を specificity（具体性: 0.0-1.0）と testability（検証可能性: 0.0-1.0）で評価しなければならない（MUST）。品質スコアが `THRESHOLDS["min_principle_quality"]`（デフォルト 0.3）未満の原則は Constitutional eval から除外しなければならない（MUST）。品質スコアは LLM 抽出と同一の呼び出し内で算出する（追加 LLM コストなし）。

#### Scenario: 高品質原則の通過
- **WHEN** 原則「Skill 内で claude -p を使用してはならない」が抽出される
- **THEN** specificity >= 0.7, testability >= 0.7 で品質スコアが閾値を超え、Constitutional eval に含まれる

#### Scenario: 低品質原則の除外
- **WHEN** 原則「コードはきれいに書く」が抽出される
- **THEN** specificity < 0.3 で品質スコアが閾値未満となり、`excluded_low_quality` リストに含まれる

#### Scenario: シード原則は品質チェックをバイパス
- **WHEN** シード原則が品質スコア評価される
- **THEN** シード原則は品質チェックを常にパスし、除外されない

### Requirement: Principle caching
`extract_principles()` の結果は `.claude/principles.json` にキャッシュしなければならない（MUST）。キャッシュが存在する場合は LLM を呼ばずにキャッシュを返さなければならない（MUST）。`--refresh` フラグでキャッシュを再生成できなければならない（MUST）。

#### Scenario: キャッシュ存在時は LLM を呼ばない
- **WHEN** `.claude/principles.json` が存在し、`--refresh` が指定されていない
- **THEN** `claude -p` を呼ばず、キャッシュされた原則リストを返す

#### Scenario: --refresh でキャッシュ再生成
- **WHEN** `--refresh` フラグが指定された
- **THEN** 既存キャッシュを無視して LLM で再抽出し、`.claude/principles.json` を上書きする

### Requirement: Cache staleness detection
`.claude/principles.json` に CLAUDE.md + Rules 全ファイルのコンテンツハッシュ（SHA-256）を `source_hash` として保存しなければならない（MUST）。キャッシュロード時にハッシュが不一致の場合、`stale_cache: true` を返却 dict に含めなければならない（MUST）。LLM は呼ばず、キャッシュされた原則をそのまま返す（コストゼロ）。

#### Scenario: ソースファイル変更によるキャッシュ陳腐化検出
- **WHEN** `.claude/principles.json` が存在し、キャッシュ後に CLAUDE.md が編集された
- **THEN** ハッシュ不一致により `stale_cache: true` が返却に含まれ、キャッシュされた原則がそのまま返される

#### Scenario: ソースファイル未変更時
- **WHEN** `.claude/principles.json` が存在し、CLAUDE.md と Rules が未変更
- **THEN** `stale_cache: false` が返却に含まれる

### Requirement: User-editable principles
`.claude/principles.json` はユーザーが手動編集可能な JSON フォーマットでなければならない（MUST）。ユーザーが追加した原則（`"user_defined": true`）は `--refresh` 時にも保持されなければならない（MUST）。

#### Scenario: ユーザー定義原則の保持
- **WHEN** `.claude/principles.json` に `"user_defined": true` の原則が含まれ、`--refresh` が実行される
- **THEN** LLM 再抽出後の結果にユーザー定義原則がマージされて保持される

### Requirement: Graceful fallback when LLM unavailable
LLM 呼び出しが失敗した場合、`extract_principles()` はシード原則のみを返さなければならない（MUST）。例外を発生させてはならない（MUST NOT）。警告メッセージを stderr に出力しなければならない（MUST）。

#### Scenario: LLM 呼び出し失敗時
- **WHEN** `claude -p` がタイムアウトまたはエラーで失敗する
- **THEN** シード原則 5 件が返され、stderr に警告が出力される
