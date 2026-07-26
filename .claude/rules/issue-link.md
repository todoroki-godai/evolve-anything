# Issue 連携

正典: `docs/agent-contract/policy.md` の「Git / Issue」。

commit(コミット)時、MEMORY.md → change アーティファクト(proposal/tasks/design) → git log の順で関連 issue を確認し、素の `#<issue番号>` を含める。
close キーワード（`Closes/Fixes/Resolves #N`）は書かない。**commit message だけでなく PR 本文も同様**（GitHub は PR 本文の close キーワードでも merge 時に auto-close する。auto-close 事故防止・グローバル commit.md と同基準）。
issue の close は merge 後にユーザー確認の上で明示操作する。受け皿は `/evolve-anything:cleanup` の「関連 Issues の close 候補提案」フロー。
