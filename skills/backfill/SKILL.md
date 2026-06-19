---
name: backfill
effort: low
description: |
  DEPRECATED setup command. The dedicated backfill CLIs were removed in #215 (v1.65.1).
  Telemetry is now collected automatically by observe hooks (going forward) and batch-ingested by evolve.
  This skill remains only as a redirect for callers; it performs no backfill itself.
  Trigger: backfill, バックフィル, session history, セッション履歴, 分析
disable-model-invocation: true
---

# /evolve-anything:backfill — 【廃止】セッション履歴のバックフィル

このスキルは **廃止済み** です。専用 CLI（`rl-backfill` / `rl-backfill-reclassify` /
`rl-backfill-analyze`）は #215（v1.65.1）でソースごと削除されており、実行すると
command-not-found になります。**このスキルは何もバックフィルしません。**

## 現行の取り込み経路（こちらを使う）

セッション履歴の観測・取り込みは日次運用の `evolve` パイプラインに統合されました。
手動でバックフィル CLI を叩く必要はありません。

1. **観測（自動・進行形）**
   observe hooks が利用中のセッションを自動的に記録します（Skill/Agent ツール呼び出し・
   ワークフロー構造・エラー・修正など、LLM コストゼロ）。導入直後はしばらく通常運用すれば
   テレメトリが溜まります。

2. **取り込み（evolve が batch でまとめて実行）**
   `/evolve-anything:evolve` が `sessions.jsonl` → DuckDB の batch ingest と、
   全 PJ human 発話の `utterances.db` 増分 ingest（#430）を内包します。
   全 PJ の human 発話だけを先に取り込みたい場合は次を使えます（読み取りのみ・ゼロ LLM）:

   ```bash
   # 全 PJ の human 発話を utterances.db に増分 ingest（初回は --days 無指定で全期間）
   bin/evolve-fleet ingest
   ```

3. **分析レポート**
   旧 backfill の分析レポート（ワークフロー一貫性・ステップバリエーション・介入分析など）は
   `audit` / `evolve` の observability セクションに統合済みです。

   ```bash
   # 環境スコアと観測サマリ（dry-run・変更なし）
   /evolve-anything:audit
   ```

## 初回セットアップの手順

```
# 1. 数セッション通常運用して observe hooks にテレメトリを溜める
# 2. 取り込み + 改善提案を一括実行（dry-run で安全に下見）
/evolve-anything:evolve --dry-run
```

## allowed-tools

Read, Bash, Glob, Grep

## Tags

backfill, deprecated, observe, evolve, history
