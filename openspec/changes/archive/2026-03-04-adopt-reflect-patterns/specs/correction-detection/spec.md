## ADDED Requirements

### Requirement: corrections.jsonl の正式スキーマに準拠しなければならない（MUST）
corrections.jsonl の各レコードは以下のスキーマに準拠しなければならない（MUST）:

```json
{
  "correction_type": "iya | chigau | souja-nakute | no | dont | stop",
  "message": "ユーザーの発話テキスト（string）",
  "last_skill": "直前に実行されたスキル名（string | null）",
  "confidence": 0.85,
  "timestamp": "ISO 8601 形式（string）",
  "session_id": "セッション ID（string）"
}
```

- `correction_type`: 検出パターンの識別子（MUST）
- `message`: ユーザーの元の発話テキスト（MUST）
- `last_skill`: 直前に実行されたスキル名。不明な場合は `null`（MUST）
- `confidence`: パターンマッチの信頼度 0.0〜1.0（MUST）
- `timestamp`: 検出時刻。ISO 8601 形式（MUST）
- `session_id`: 検出元のセッション ID（MUST）

backfill 由来のレコードには追加で `"source": "backfill"` フィールドを含める（MUST）。リアルタイム検出のレコードには `source` フィールドは付与しない。

#### Scenario: 全必須フィールドが存在する
- **WHEN** correction_detect.py が修正パターンを検出する
- **THEN** corrections.jsonl に追記されるレコードには `correction_type`, `message`, `last_skill`, `confidence`, `timestamp`, `session_id` の全フィールドが含まれる

#### Scenario: backfill 由来のレコード
- **WHEN** `backfill --corrections` で修正パターンが検出される
- **THEN** レコードに `"source": "backfill"` フィールドが追加される

### Requirement: UserPromptSubmit hook で修正パターンを検出
hooks/correction_detect.py は UserPromptSubmit イベントの human message テキストに対して CJK/英語の修正パターンをマッチし、検出時に corrections.jsonl にレコードを追記する。LLM 呼び出しは行わない（MUST NOT）。レコードは上記の正式スキーマに準拠すること（MUST）。

#### Scenario: 日本語修正パターン検出
- **WHEN** ユーザーが「いや、そうじゃなくて skill-evolve を使って」と入力する
- **THEN** corrections.jsonl に `{"correction_type": "iya", "message": "いや、そうじゃなくて skill-evolve を使って", "last_skill": null, "confidence": 0.85, "timestamp": "...", "session_id": "..."}` が追記される

#### Scenario: 英語修正パターン検出
- **WHEN** ユーザーが「No, don't use that approach」と入力する
- **THEN** corrections.jsonl に `{"correction_type": "no", "message": "No, don't use that approach", "last_skill": null, "confidence": 0.75, "timestamp": "...", "session_id": "..."}` が追記される

#### Scenario: 疑問文は除外
- **WHEN** ユーザーが「いや、それでいいの？」と末尾が「？」で終わる文を入力する
- **THEN** corrections.jsonl にはレコードが追記されない

### Requirement: 直前スキルとの紐付けを行わなければならない（MUST）
correction レコードには直前に実行された Skill のスキル名を `last_skill` フィールドとして含めなければならない（MUST）。直前スキルが不明な場合は null とする。

#### Scenario: Skill 実行直後の修正
- **WHEN** observe.py が `commit` スキルの使用を記録した直後に修正パターンが検出される
- **THEN** correction レコードの `last_skill` が `"commit"` となる

#### Scenario: 直前スキルなし
- **WHEN** セッション開始直後など、まだ Skill が実行されていない状態で修正パターンが検出される
- **THEN** correction レコードの `last_skill` が `null` となる

### Requirement: hook 失敗時のサイレント処理
correction_detect.py は例外発生時に stderr に警告を出力し、exit 0 で終了する（MUST）。セッションをブロックしてはならない。

#### Scenario: JSON パースエラー
- **WHEN** stdin から不正な JSON が入力される
- **THEN** stderr に `[rl-anything:correction] parse error: ...` を出力し、exit 0 で終了する
