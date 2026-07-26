# ADR-052: Claude Code primary / Codex opt-in executor lanes

- Status: Accepted
- Date: 2026-07-26
- Related: #268, #266, ADR-040, ADR-049

## Context

Claude CodeとCodexが同じcheckoutを編集すると、未追跡adapterの誤stage、branch drift、
review対象SHAの曖昧化が起きる。一方、常時2agentを動かす費用は不要で、通常実装は
トークン余力の大きいClaude Codeを中心にしたい。

## Decision

- Claude Codeをprimary executor、Codexをopt-inのcold reviewer/独立executorとする。
- top-level laneは`<issue>-<slug>`、1 owner/1 writer、owned_paths非重複を契約化する。
- ownership取得とrepo外worktree作成は`start`の1排他区間で行う。
- codeのSoTはcommit、handoff証拠はgit-common-dir外部metadataとする。
- tracked artifactへのHEAD SHA埋込は自己参照になるため採用しない。
- stageは明示path＋cached diff全体のallowlist検証を必須とする。
- runtime telemetryはusage/sessions/errorsの3storeから較正し、未実証hookを一括移植しない。
- runtime集計でsessionsはlive JSONLを直読せず、sessions.db＋未ingest JSONLの正準union
  readerを使う。
- cleanupは`<repo>/.Codex/→<repo>/.claude/`と`~/.Codex/→~/.codex/`を別指紋にする。
  文脈不明な裸の`.Codex/`は復元先が一意でないためaudit-onlyとする。
- merge/release/Issue closeは人間authorityとする。

## Consequences

- 同一Issueの複数laneは可能だが、owned_paths重複は拒否される。
- reviewerはSHA変更後に再reviewが必要。
- 既存のrepo内nested worktreeは自動移動・削除しない。
- schema/CI enforcementはPR本文＋外部metadataのdogfood後に再評価する。
