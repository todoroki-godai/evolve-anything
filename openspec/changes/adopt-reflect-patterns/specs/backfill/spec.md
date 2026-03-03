## ADDED Requirements

### Requirement: --corrections フラグで修正パターンを遡及抽出しなければならない（MUST）
`backfill --corrections` を指定した場合、`type: "human"` レコードのテキストに対して CJK/英語修正パターンマッチを実行し、corrections.jsonl に追記しなければならない（MUST）。レコードは `correction-detection/spec.md` で定義された正式スキーマに準拠し、追加で `"source": "backfill"` フィールドを含めること（MUST）。

#### Scenario: 過去セッションから修正パターンを抽出
- **WHEN** トランスクリプトの human message に「いや、そうじゃなくて」が含まれ、直前の assistant ターンで `name: "Skill"`, `input.skill: "evolve"` が呼び出されている
- **THEN** corrections.jsonl に `{"correction_type": "souja-nakute", "message": "いや、そうじゃなくて...", "last_skill": "evolve", "confidence": 0.60, "timestamp": "...", "session_id": "...", "source": "backfill"}` が追記される

#### Scenario: 直前の Skill が特定できない場合
- **WHEN** human message の修正パターンが検出されるが、直前の assistant ターンに Skill 呼び出しがない
- **THEN** corrections.jsonl に `last_skill: null` として追記される（他フィールドはスキーマ準拠）

#### Scenario: backfill 由来の confidence 減点
- **WHEN** backfill で修正パターンが検出される
- **THEN** confidence はリアルタイム検出より低い値（0.60）を設定する（MUST）
