# correction_id backfill runbook

`scripts/migrate_correction_id_backfill.py` は既存 `corrections.jsonl` に不変 ID を
一度だけ付与する。移行中の他 writer との競合を完全には検出できないため、次の停止契約を守る。

1. 全 Claude Code セッションを閉じ、hook の自動起動を止める。
2. `daily`、`backfill`、`prune`、`promotion` 系スクリプトを実行しない。
3. `python3 scripts/migrate_correction_id_backfill.py <path>` で dry-run を確認する。
4. `python3 scripts/migrate_correction_id_backfill.py --apply <path>` を実行する。
   `--apply` は `shutil.copy2` で `corrections.jsonl.bak-<timestamp>` を必ず作成し、
   バックアップを確認できなければ書込みを開始しない。回避フラグはない。
5. 結果が `conflict` または `incomplete` の場合は自動再試行しない。バックアップと
   出力された `initial_identity` / `final_identity` を調査し、dry-run からやり直す。

identity/hash の再照合より前の変更は検出できる。一方、再照合後から `os.replace` 前の
短い窓に生じた追記は検出できず、置換で失われる可能性がある。post-write 比較は移行が
意図した内容を書けたことだけを確認し、失われた他 writer の追記を必ず検出するものではない。
