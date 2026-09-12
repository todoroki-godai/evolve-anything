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

今回2つ入れました。(a) ノート要約に使うモデルを Sonnet 5 に切り替え、(b) 要約品質の評価に E 軸（根拠の妥当性）指標を追加。

3. /evolve-anything:spec-keeper 実行（Sonnet 5 切替・E軸指標追加は仕様変更に該当）
