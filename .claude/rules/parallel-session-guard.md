# 並列セッション branch drift 対策

正典: `docs/agent-contract/policy.md` の「Lane と ownership」。

- `git commit` / `git add` 前に期待branchとowned pathsを確認する
- drift検知時は編集を停止する。checkout / stash / resetで自動復帰しない
- top-level executorの並行開発はrepo外worktreeを使う
