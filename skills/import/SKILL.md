---
name: rl-anything:import
description: |
  コミュニティスキルを GitHub からワンコマンドで import する。
  Trigger: import skill, スキルをインポート, コミュニティスキル, rl-fleet import
effort: low
allowed-tools: Bash
---

# rl-anything:import — コミュニティスキル import

`bin/rl-fleet import <source>` を呼び出して、GitHub またはローカルパスからスキルを安全にインポートする。

## 使い方

ユーザーに source の入力を促し、以下のコマンドを Bash で実行する:

```bash
bin/rl-fleet import <source> [--force] [--yes]
```

## source の形式

| 形式 | 例 |
|------|---|
| `owner/repo` | `todoroki-godai/my-skill` |
| `owner/repo/subpath` | `todoroki-godai/community-skills/my-skill` |
| GitHub URL | `https://github.com/todoroki-godai/my-skill` |
| ローカルパス | `/path/to/my-skill` |

## オプション

- `--force`: 同名スキルが存在する場合に上書きする
- `--yes` / `-y`: 確認プロンプトをスキップする（CI 環境向け）

## 実行手順

1. ユーザーに import したいスキルのソース（GitHub の `owner/repo` 等）を確認する
2. 以下のコマンドを実行する:

```bash
bin/rl-fleet import {source}
```

3. プレビューが表示されたら内容を確認してユーザーに伝える
4. ユーザーが承認した場合はコマンドの `[y/N]` プロンプトに `y` を入力する（`--yes` フラグで自動化も可能）

## セキュリティ注意事項

- スクリプト（scripts/ 以下）は自動実行されない
- fetch → validate → preview → confirm → copy のみ実施
- インポート前に必ずプレビューを確認すること
