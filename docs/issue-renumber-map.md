# Issue 番号対応表（再作成による付け替え）

作成者アカウントの取り違えで起票された issue を正しいアカウントで再作成し、旧 issue を削除した。
GitHub は issue の作成者を後から変更できないため、番号が変わっている。

コード・CHANGELOG 中の `#<旧番号>` は下表で読み替えること（`#345` のみ参照側も更新済み）。

| 旧番号 | 新番号 | タイトル | 参照側の更新 |
|---|---|---|---|
| #103 | [#348](https://github.com/todoroki-godai/evolve-anything/issues/348) | advisory レーンの dismiss / 自動畳み込み入口 | 未更新（本表で読み替え） |
| #206 | [#349](https://github.com/todoroki-godai/evolve-anything/issues/349) | auto-memory Stop hook の project_path フィルタ欠落 | 未更新（本表で読み替え） |
| #216 | [#350](https://github.com/todoroki-godai/evolve-anything/issues/350) | spec-keeper: Progressive Disclosure 閾値を chars ベースへ | 未更新（本表で読み替え） |
| #257 | [#346](https://github.com/todoroki-godai/evolve-anything/issues/346) | [tech-eval] セッションの入力待ち・stall 検知通知（icebox） | 参照なし |
| #314 | [#345](https://github.com/todoroki-godai/evolve-anything/issues/345) | fleet: max_transcripts が dir 単位で適用される | 更新済み（コード・CHANGELOG とも `#345`） |

内容（本文・コメント・ラベル・close 理由）は再作成時にすべて引き継いでいる。

再発防止は `~/.claude/hooks/account-org-guard.py`（write 直前にアカウント整合を検査）と
`~/.claude/rules/auth.md`（switch と write を同一コマンドで連結する）で担保する。
