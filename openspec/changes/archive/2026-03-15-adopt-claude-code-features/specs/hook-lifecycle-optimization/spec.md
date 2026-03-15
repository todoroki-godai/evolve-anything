## ADDED Requirements

### Requirement: PostCompact hook registration
save_state.py を PostCompact フックとしても登録し、compaction 後の状態保存を補完する（MUST）。PostCompact 時は PreCompact チェックポイントを上書きしてはならない（MUST NOT）。

#### Scenario: PostCompact triggers save_state
- **WHEN** コンテキスト compaction が完了する
- **THEN** save_state.py が PostCompact フックとして実行される
- **AND** checkpoint.json の `post_compact_checkpoint` キーに保存される（`checkpoint` キーは上書きしない）
- **AND** `hook_type: "post_compact"` フィールドが含まれる

#### Scenario: PreCompact and PostCompact coexistence
- **WHEN** compaction が発生する
- **THEN** PreCompact で `checkpoint` キーに保存され、PostCompact で `post_compact_checkpoint` キーに保存される
- **AND** PreCompact の情報量の多いチェックポイントが保護される

#### Scenario: restore_state priority
- **WHEN** restore_state.py がチェックポイントを読み込む
- **THEN** `checkpoint` キー（PreCompact）を優先的に参照する（MUST）
- **AND** `checkpoint` キーが存在しない場合のみ `post_compact_checkpoint` にフォールバックする（SHOULD）

### Requirement: SessionStart hook idempotency
restore_state.py の SessionStart フックが複数回呼ばれても安全であることを保証する。`once: true` は settings hooks では利用不可のため、スクリプト内でガードを実装する。

#### Scenario: duplicate SessionStart invocation
- **WHEN** restore_state.py が同一セッション内で複数回呼ばれる
- **THEN** 2回目以降は早期リターンし、重複出力を防止する
