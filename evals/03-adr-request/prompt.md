---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Write, Edit, Glob, Grep, Skill]
model: opus
runs: 3
---
このディレクトリは小さな REST API プロジェクト notes-api の作業コピーです。まず次の2ファイルをこの内容のまま作成してから、作業に取りかかってください。

`SPEC.md`:

```markdown
# notes-api SPEC

Last updated: 2026-08-01

## Current Capabilities
- ノートの作成・取得・削除（REST API）
- SQLite による永続化

## System Architecture
- `src/server.py` — HTTP ハンドラ（3 エンドポイント）
- `src/store.py` — SQLite アクセス層

## Key Design Decisions
- 永続化に SQLite を選択

## Current Limitations
- 認証がない
- 全文検索がない

## Next
- 認証の追加
```

`CHANGELOG.md`:

```markdown
# Changelog

## [0.2.0] - 2026-08-01
- ノートの削除エンドポイントを追加
```

W1 として永続化層の検討をしていました。最終的に、SQLite をやめて PostgreSQL へ移行すると決めました。検討した代替案は「SQLite のまま WAL モードで運用を続ける」と「DynamoDB へ移す」の2つで、前者は同時書込みの上限に既に当たっており、後者はローカル開発の再現性が落ちるため見送りました。

1. W1: 移行方針の確定
2. 移行スクリプトの設計
3. spec-keeper: 最終状態を ADR 化

これお願い
