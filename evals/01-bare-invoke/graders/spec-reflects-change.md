---
type: llm
focus: {source: file, path: SPEC.md}
weight: 1
---
最終状態の SPEC.md が次の3条件を満たすなら合格、1つでも満たさないなら不合格。ここに書かれていない理由で不合格にしないこと。

条件1: Current Capabilities または System Architecture に、今回追加した機能（タグ検索、`GET /notes?tag=`、`src/search.py` のいずれか）が書かれている。

条件2: 次の4点が内容として残っている。文言の細部が変わっていても、補足が加わっていてもよい。
- ノートの作成・取得・削除
- SQLite による永続化
- 認証がないという制限
- 永続化に SQLite を選択したという設計判断

条件3: バージョン番号と日付つきの変更点リスト（CHANGELOG に書くべき履歴そのもの）が SPEC.md 本文に書き写されていない。CHANGELOG.md へのリンクやポインタが1行あるだけなら条件3を満たす。そのための見出しが新設されていても、Last updated の日付が更新されていても、条件3を満たす。
