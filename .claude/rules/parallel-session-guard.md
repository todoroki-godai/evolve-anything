# 並列セッション branch drift 対策
- `git commit` / `git add` 前に `git branch --show-current` で期待 branch を確認する
- drift 検知時は `git checkout <想定 branch>` で working tree ごと戻す
- 複数ファイルを連続編集する長時間作業では数分おきに branch を確認する
- 並行開発は worktree を使う。stash+checkout は同一ディレクトリ競合の原因になる
