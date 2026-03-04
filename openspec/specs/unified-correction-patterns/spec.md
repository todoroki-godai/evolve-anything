## ADDED Requirements

### Requirement: Unified correction pattern set
`hooks/common.py` の CORRECTION_PATTERNS は CJK + 英語 + Guardrail + Explicit + Positive の全パターンを統一辞書で管理する（MUST）。各パターンは `pattern`, `confidence`, `type`, `decay_days` フィールドを持つ。

#### Scenario: CJK correction detected
- **WHEN** ユーザーが「いや、違うよ」と入力する
- **THEN** `correction_type: "iya"`, `confidence: 0.85`, `type: "correction"` で corrections.jsonl に記録される

#### Scenario: English guardrail detected
- **WHEN** ユーザーが "don't add docstrings unless I ask" と入力する
- **THEN** `correction_type: "dont-unless-asked"`, `confidence: 0.90`, `type: "guardrail"`, `guardrail: true` で記録される

#### Scenario: Explicit remember detected
- **WHEN** ユーザーが "remember: always use bun" と入力する
- **THEN** `correction_type: "remember"`, `confidence: 0.90`, `type: "explicit"`, `decay_days: 120` で記録される

#### Scenario: Remember bypasses length filter
- **WHEN** ユーザーが 500文字超のメッセージで "remember:" を含む入力をする
- **THEN** 長さフィルタをバイパスして corrections.jsonl に記録される（MUST）

### Requirement: False positive filtering
`FALSE_POSITIVE_PATTERNS` により疑問文、タスクリクエスト、システムコンテンツを除外する（MUST）。

#### Scenario: Question filtered out
- **WHEN** ユーザーが "Can you help me with this?" と入力する
- **THEN** 修正パターンとして検出されない

#### Scenario: System content filtered
- **WHEN** メッセージに XML タグや JSON が含まれる
- **THEN** `should_include_message()` が False を返し処理をスキップする

### Requirement: Confidence calculation with length adjustment
信頼度は基本値に対して文長による調整を適用する（MUST）。短いメッセージ（80文字未満）はブースト、長いメッセージ（150文字超）は削減。

#### Scenario: Short message confidence boost
- **WHEN** 40文字の "no, use bun not npm" が検出される
- **THEN** confidence が基本値 + 0.10（最大 0.90）にブーストされる

#### Scenario: Long message confidence reduction
- **WHEN** 300文字超のメッセージが検出される
- **THEN** confidence が基本値 - 0.15（最小 0.50）に削減される

### Requirement: Extended corrections.jsonl schema
corrections.jsonl の各レコードは以下の拡張フィールドを含む（MUST）: `matched_patterns`, `project_path`, `sentiment`, `decay_days`, `routing_hint`, `guardrail`, `reflect_status`, `extracted_learning`, `source`。`source` の値域は初回リリースでは `"hook" | "backfill"` の2値（`"history-scan"` は将来拡張）。`matched_patterns` は全マッチパターンキーのリストで、信頼度計算（3+→0.85, 2→0.75）に使用する。

#### Scenario: Hook-captured correction
- **WHEN** hook が修正を検出する
- **THEN** `source: "hook"`, `project_path: CLAUDE_PROJECT_DIR`, `reflect_status: "pending"`, `extracted_learning: null` で記録される

#### Scenario: Backfill-captured correction
- **WHEN** backfill が過去セッションから修正を抽出する
- **THEN** `source: "backfill"`, `reflect_status: "pending"` で記録される

#### Scenario: Multiple patterns matched
- **WHEN** ユーザーが "no, use bun not npm" と入力し、"no" と "use-X-not-Y" の2パターンがマッチする
- **THEN** `correction_type: "no"`, `matched_patterns: ["no", "use-X-not-Y"]` で記録され、confidence は2パターンマッチ (0.75) で計算される

### Requirement: Tool rejection extraction
backfill が過去セッションからツール拒否（ユーザーがツール実行を拒否した際のメッセージ）を correction として抽出する（SHALL）。

#### Scenario: Tool rejection captured
- **WHEN** セッション JSONL にユーザーのツール拒否メッセージ "no, always run tests first" がある
- **THEN** backfill が correction レコード（`source: "backfill"`, `correction_type` はパターンマッチ結果）として記録する
