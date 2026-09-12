---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Write, Edit, Glob, Grep, Skill]
model: opus
runs: 3
---
このディレクトリは小さな REST API プロジェクト notes-api の作業コピーです。まず次の5ファイルをこの内容のまま作成してから、作業に取りかかってください。

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

`src/store.py`:

```python
import sqlite3

DB = "notes.db"


def connect():
    return sqlite3.connect(DB)


def insert(title, body):
    with connect() as c:
        c.execute("INSERT INTO notes (title, body) VALUES (?, ?)", (title, body))


def get(note_id):
    with connect() as c:
        return c.execute("SELECT id, title, body FROM notes WHERE id = ?", (note_id,)).fetchone()


def delete(note_id):
    with connect() as c:
        c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
```

`src/search.py`:

```python
from src.store import connect


def by_tag(tag):
    with connect() as c:
        return c.execute(
            "SELECT n.id, n.title FROM notes n JOIN tags t ON t.note_id = n.id WHERE t.tag = ?",
            (tag,),
        ).fetchall()
```

`src/server.py`:

```python
from src import store
from src.search import by_tag


def handle(method, path, query, body):
    if method == "POST" and path == "/notes":
        return store.insert(body["title"], body["body"])
    if method == "GET" and path.startswith("/notes/"):
        return store.get(path.rsplit("/", 1)[1])
    if method == "DELETE" and path.startswith("/notes/"):
        return store.delete(path.rsplit("/", 1)[1])
    if method == "GET" and path == "/notes" and "tag" in query:
        return by_tag(query["tag"])
    return None
```

今回、タグ検索エンドポイント `GET /notes?tag=<name>` を `src/server.py` に追加し、`src/search.py` を新設しました。これは issue #379 の対応です。

#379 close して spec-keeper も実行して
