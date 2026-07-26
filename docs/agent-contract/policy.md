# Agent Collaboration Policy

この文書は Claude Code と Codex が共有する開発契約の単一ソース（SoT）である。
`CLAUDE.md` と `AGENTS.md` は各 runtime の自動読込入口であり、作業開始時にこの文書を
読むことを必須とする。製品仕様の SoT は従来どおり `SPEC.md`、`spec/`、ADR に置く。

## 既定の運用

- primary executor は Claude Code とする。
- Codex は architecture cold review、独立検証、行き詰まり時、またはユーザーが明示した
  場合に使う。
- merge、release、Issue close は人間だけが決定する。
- agent は別 agent を常時・無制限に自動起動しない。

## Lane と ownership

- task ID は `<issue>-<slug>` 形式とする。
- 1 lane = 1 owner = 1 writer。
- 同一 Issue の複数 lane は `owned_paths` が重複しない場合だけ並行できる。
- トップレベル executor lane は repo 外の worktree を使う。
- reviewer は指定された commit SHA を read-only で確認する。
- reviewer が修正する場合は別 lane で行い、owner が明示的に取り込む。
- branch drift を検出したら停止する。自動 checkout、stash、reset で「戻さない」。

標準入口:

```bash
bin/evolve-agent-task start \
  --task-id 268-core \
  --runtime codex \
  --owned-path scripts/lib/example.py
```

## 実装原則

### TDD / root cause

- 実装前に失敗するテストを書く。
- エラー修正は表層的な回避でなく根本原因を特定してから行う。
- 単体テストから Claude/Codex CLI、Anthropic/OpenAI SDKなどの実LLMを呼ばない。
  LLM境界はテスト対象の1層下でmockする。

### 証拠とデータ契約

- 「完了」「成功」「pass」と報告する前に、対応する検証コマンドを実行する。
- 未実行の検証を成功として記録しない。
- モジュール間の変換コードは、実装前にsourceの返り値構造と既存fixtureを確認する。
- 正パスだけでなく、意図しない書込・状態残留・再帰triggerを確認する。

### Git / Issue

- commit前にbranchとowned pathsを確認する。
- commitには関連Issueを素の `#<number>` で含める。
- `Closes/Fixes/Resolves`は使わない。commit messageだけでなく**PR本文も同様**
  （GitHubはPR本文のclose keywordでもmerge時にauto-closeするため）。
  Issue closeはmerge後に人間が明示実行する。
- `Co-Authored-By`やAI生成フッターを付けない。
- `git add -A`でworktree全体をstageしない。明示pathspecとcached diffを検証する。

## Handoff

初期運用ではtracked artifactを作らず、PR本文またはgit-common-dir外部メタデータに記録する。

- task ID
- owner runtime
- base SHA / head SHA
- owned paths / changed files
- 実行した検証コマンドと結果
- decisions / open risks / next action

PR HEADがreview済みSHAから変わった場合、そのreviewはstaleである。

## Runtime 固有情報

コマンド、パス、hook payloadなどの識別子はruntime adapterにだけ置く。名前を機械置換して
存在しない機能を作らない。対応状況は [capability-matrix.md](capability-matrix.md) を参照する。
