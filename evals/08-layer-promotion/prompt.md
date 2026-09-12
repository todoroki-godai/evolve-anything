---
max_turns: 30
timeout_seconds: 900
allowed_tools: [Read, Write, Edit, Glob, Grep, Skill]
model: opus
runs: 3
---
このディレクトリは REST API プロジェクト notes-api の作業コピーです。すでに2層構成（SPEC.md + `spec/`）を採っています。まず `spec/architecture.md` を次の内容で作成してください。

```markdown
# Architecture (cold)

Last updated: 2026-08-01

モジュール間の呼び出し関係とデータフローの詳細をここに置く。
```

続けて `SPEC.md` を次の内容のまま作成してから、作業に取りかかってください。

```markdown
# notes-api SPEC

Last updated: 2026-08-01

## Current Capabilities
- ノートの作成・取得・削除（REST API）
- SQLite による永続化
- タグ検索（GET /notes?tag=）
- 全文検索（GET /notes?q=）
- ノートの共有リンク発行
- Markdown レンダリング
- 添付ファイルのアップロード
- Webhook 通知
- CSV エクスポート
- ゴミ箱と復元
- ノートのピン留め
- 並び替え（更新日時・作成日時・タイトル）
- ページネーション
- レート制限
- アクセスログ

## System Architecture
- `src/server.py` — HTTP ハンドラ
- `src/store.py` — SQLite アクセス層
- `src/search.py` — 検索
- `src/render.py` — Markdown
- `src/attach.py` — 添付
- `src/notify.py` — Webhook
- `src/export.py` — CSV
- `src/trash.py` — ゴミ箱
- `src/auth.py` — トークン検証
- `src/config.py` — 設定読み込み
- `src/ratelimit.py` — レート制限
- `src/log.py` — アクセスログ

## API Endpoints
- `POST /notes` — 作成
- `GET /notes/{id}` — 取得
- `PATCH /notes/{id}` — 更新
- `DELETE /notes/{id}` — 削除
- `GET /notes` — 一覧・検索
- `POST /notes/{id}/share` — 共有リンク発行
- `DELETE /notes/{id}/share` — 共有リンク失効
- `GET /share/{token}` — 共有閲覧
- `POST /notes/{id}/attachments` — 添付追加
- `GET /attachments/{id}` — 添付取得
- `DELETE /attachments/{id}` — 添付削除
- `POST /notes/{id}/pin` — ピン留め
- `POST /export` — CSV 出力
- `GET /trash` — ゴミ箱一覧
- `POST /trash/{id}/restore` — 復元

## Data Model
- notes テーブル: id, title, body, pinned, created_at, updated_at, deleted_at
- tags テーブル: note_id, tag
- attachments テーブル: id, note_id, path, size, content_text
- share_tokens テーブル: token, note_id, expires_at, revoked_at
- webhooks テーブル: id, url, secret, failure_count
- access_log テーブル: id, path, status, latency_ms, at
- インデックス: notes(deleted_at)
- インデックス: notes(pinned, updated_at)
- インデックス: tags(tag)
- インデックス: attachments(note_id)

## Configuration
- `DATABASE_PATH` — SQLite ファイルの位置
- `ATTACHMENT_DIR` — 添付の保存先
- `SHARE_TOKEN_TTL` — 共有リンクの有効期限
- `WEBHOOK_TIMEOUT` — Webhook のタイムアウト秒
- `WEBHOOK_MAX_RETRY` — Webhook の再送上限
- `EXPORT_MAX_ROWS` — CSV の上限行数
- `RATE_LIMIT_PER_MIN` — 1分あたりの上限リクエスト数
- `LOG_LEVEL` — ログ出力レベル
- `LOG_RETENTION_DAYS` — アクセスログの保持日数

## Operations
- バックアップは日次で SQLite ファイルをコピー
- 添付は月次で孤児ファイルを掃除
- 共有トークンは期限切れを日次で削除
- Webhook の失敗は3回まで指数バックオフで再送
- アクセスログは保持日数を過ぎたら日次で削除
- デプロイはローリング再起動で行う
- メンテナンス時は 503 を返す

## Key Design Decisions
- 永続化に SQLite を選択
- 検索はハンドラから分離して `src/search.py` に置く
- 添付はDBでなくファイルシステムに置く
- 共有リンクは不可逆トークンで発行する
- レート制限はプロセス内メモリで持つ
- アクセスログは同じ SQLite に書く

## Current Limitations
- 認証がない
- マルチユーザーに対応していない
- 添付の総容量に上限がない
- 検索は日本語の形態素解析をしていない
- レート制限がプロセスをまたがない
- 水平スケールできない

## Next
- 認証の追加
- マルチユーザー対応
- 検索基盤の外出し
```

今回、全文検索の対象に添付ファイルのテキストを含めるようにしました（`src/search.py` と `src/attach.py` を変更）。

spec-keeper 実行しておいて
