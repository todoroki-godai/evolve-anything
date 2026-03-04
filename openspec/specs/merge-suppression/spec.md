### Requirement: Merge suppression records rejected pairs
merge 統合候補をユーザーが却下した場合、当該ペアを suppression に記録し次回以降の `merge_duplicates()` で再提案を抑制する（MUST）。suppression エントリは `discover-suppression.jsonl` に `type: "merge"` 付きで保存する（MUST）。

#### Scenario: Rejected merge pair is suppressed
- **WHEN** ユーザーが skill-a と skill-b の統合を却下した
- **THEN** `discover-suppression.jsonl` に `{"pattern": "skill-a::skill-b", "type": "merge"}` が追加される

#### Scenario: Suppressed pair is not re-proposed
- **WHEN** `discover-suppression.jsonl` に `{"pattern": "alpha::beta", "type": "merge"}` が存在する
- **THEN** `merge_duplicates()` は alpha と beta のペアを `status: "skipped_suppressed"` として出力し、`proposed` としない

#### Scenario: Pair key is normalized
- **WHEN** skill-b と skill-a の順でペアが検出された（逆順）
- **THEN** suppression チェックはスキル名をソートして `"skill-a::skill-b"` として照合するため、順序に依存しない
- **AND** `add_merge_suppression("skill-b", "skill-a")` は `{"pattern": "skill-a::skill-b", "type": "merge"}` を記録する（ソート後の正規化形式）

### Requirement: Merge suppression coexists with discover suppression
merge suppression エントリは既存の discover suppression と同一ファイル（`discover-suppression.jsonl`）に共存する（MUST）。`type` フィールドが未指定の既存エントリは discover 用として扱い、merge suppression チェックでは無視する（MUST）。

#### Scenario: Existing discover suppression entries are unaffected
- **WHEN** `discover-suppression.jsonl` に `{"pattern": "some-error-pattern"}` が存在する（type なし）
- **THEN** discover.py の `load_suppression_list()` は従来通りこのエントリを読み込み、merge suppression チェックはこのエントリを無視する

#### Scenario: load_suppression_list() excludes merge entries
- **WHEN** `discover-suppression.jsonl` に `{"pattern": "alpha::beta", "type": "merge"}` が存在する
- **THEN** `load_suppression_list()` はこのエントリを返さない（`type` が未指定または `"discover"` のエントリのみを返す）

#### Scenario: Mixed entries in suppression file
- **WHEN** `discover-suppression.jsonl` に discover 用エントリと `type: "merge"` エントリが混在する
- **THEN** 各システムは自分の `type` に該当するエントリのみを参照する

### Requirement: Suppression file write failure is non-fatal
suppression ファイルへの書き込みが失敗した場合、エラーを stderr に出力し、evolve フロー全体は継続する（MUST）。suppression 登録の失敗は次回の再提案を許容するだけであり、致命的エラーではない。

#### Scenario: Write failure does not halt evolve
- **WHEN** `add_merge_suppression()` の実行時にファイル書き込みが失敗した（権限エラー等）
- **THEN** エラーメッセージを stderr に出力し、呼び出し元の evolve フローは中断せず継続する

### Requirement: Suppressed merge pairs appear in output with skipped status
suppressed なペアは `merge_proposals` の出力に `status: "skipped_suppressed"` として含める（MUST）。これにより audit やレポートで抑制状況が可視化される。

#### Scenario: Skipped suppressed in output
- **WHEN** ペア alpha::beta が suppression に登録されており、かつ duplicate_candidates に含まれる
- **THEN** `merge_proposals` に `{"primary": {...}, "secondary": {...}, "status": "skipped_suppressed"}` が含まれる
