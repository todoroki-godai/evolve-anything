# Issue 連携
commit(コミット)時、MEMORY.md → change アーティファクト(proposal/tasks/design) → git log の順で関連 issue を確認し、素の `#<issue番号>` を含める。
close キーワード（`Closes/Fixes/Resolves #N`）は書かない（auto-close 事故防止・グローバル commit.md と同基準）。
issue の close は merge 後にユーザー確認の上で明示操作する。受け皿は `/evolve-anything:cleanup` の「関連 Issues の close 候補提案」フロー。
