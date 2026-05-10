# Transcript ストア取り扱い
`~/.claude/projects/`・`~/.claude/sessions/`・`~/.gstack/projects/` を walk/ingest/集計するコードは:
- 実装前に `find <DIR> -name '*.jsonl' | wc -l` と `du -sh <DIR>` で規模感を取り、Success Criteria を実測値ベースで書く
- Test Plan に **実機 1 PJ E2E ベンチ** を必須化。pytest fixture でなく実 PJ で 1 回完走させ、wall time/DB size/row 数を assertion してから完了報告（issue #28 で 9925 jsonl / 1.9 GB の規模破綻を経験）
- bench script 設計: 既知に O(N) で壊れる経路を全量データで実走しない。phase 単位で `--max-files N` サンプリング + 各phase timeout + 毎件 `print(..., flush=True)` で進捗を吐く。CPU 100%超 + output 0行が30秒続いたら暴走と判定して kill
