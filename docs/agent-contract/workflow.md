# Workflow

1. `<issue>-<slug>`と`owned_paths`を決める。
2. `evolve-agent-task start`でownership取得とrepo外worktree作成を同時に行う。
3. ownerがTDDで実装し、明示pathだけをcommitする。
4. `evolve-agent-task handoff`でclean HEADの証拠を外部メタデータへ記録する。
5. reviewerがhead SHAを固定してread-only reviewする。
6. headが変わったらreviewをやり直す。
7. 人間がmergeを判断する。
8. `evolve-agent-task finish`でlaneを解放する。worktree/branchは自動削除しない。

`start`とownership取得を別操作にしてはいけない。間に競合窓が生じるためである。

lockはPID不在または1時間超過で自動回復する。誤ったlockを手動解除するときだけ
`evolve-agent-task force-unlock --yes`を使う。

runtime別の初期較正は
`evolve-agent-task runtime-summary --data-dir ~/.claude/evolve-anything`で表示する。
sessionsはrotateされるlive JSONL直読ではなく、`session_store`のsessions.db＋未ingest
JSONL union readerを使う。
