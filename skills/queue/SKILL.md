---
name: queue
effort: low
disallowed-tools: [Edit, Write, MultiEdit]
description: |
  全 PJ 横断の「今 evolve すべき PJ（待ち一覧）」を決定論ゼロ LLM で表示し、上から対話 evolve
  するためのガイドを出す。pull 型 daily-evolve（ADR-050）の手動運用入口。CC 起動後、タイミングの
  良い日に叩いて「今日どの PJ を改善するか」を決めるのに使う。bin/evolve-fleet queue の薄いラッパー
  （read-only・変更なし）+ 次アクション提示。
  Trigger: queue, evolve 待ち, evolve queue, 待ち一覧, 今日 evolve, どの PJ を evolve,
  朝の evolve, daily evolve, 全PJ evolve, 今 evolve すべき, evolve するPJ
---

# /evolve-anything:queue — 全 PJ の evolve 待ち一覧

pull 型 daily-evolve（ADR-050）の手動運用入口。決定論・ゼロ LLM で「今 evolve すべき PJ」を
一覧表示し、上から対話 evolve するためのガイドを出す。**このスキル自体は何も変更しない**
（読み取りのみ）。evolve の適用は対象 PJ の対話セッションで人間が承認する。

## Usage

```
/evolve-anything:queue                 # 待ち一覧を表示（既定 threshold 5）
/evolve-anything:queue --threshold 3   # 閾値を下げてより多くの PJ を拾う
```

## 実行手順

### Step 1: 待ち一覧を取得（決定論・read-only・ゼロ LLM）

`evolve-fleet` はプラグイン bin が PATH 上にあるのでベタ呼び出しでよい（`evolve-audit` 等と同規約）。
どの PJ の cwd から叩いてもグローバル DATA_DIR を読む。

```bash
evolve-usage-log "queue"
evolve-fleet queue          # ユーザーが --threshold N を渡したら付与する
```

`material_count = weak_unprocessed`（未昇格・未 expired の weak_signals）`+ new_corrections`（前回
evolve 以降の新規 corrections）。これが threshold（既定 5）以上の PJ が「待ち」。

### Step 2: 結果を読み解いて提示

- **待ち 0 件** → 「今日は evolve 待ちなし」で終了（無理に evolve しない）。
- **待ちあり** → テーブルの上から、各 PJ の `REASON`（weak=… + new corr=…）をそのまま添えて提示する。
  `MATERIAL` が大きい PJ ほど溜まった学習素材が多い＝改善余地が大きい、という読み方を 1 行添える。

### Step 3: 上から処理する（適用は人間承認）

evolve はカレント PJ 対象（project-dir 引数なし）。待ち PJ を処理するには、その PJ へ移動してから evolve:

1. 先頭の待ち PJ のディレクトリへ `/cd <path>` で移動する（CC v2.1.169+・prompt cache を壊さない）。
   PJ ディレクトリは通常 `~/matsukaze-utils/<slug>`（個人）か `~/updater/<slug>`（業務）配下。
   slug からパスが不明なら `ls -d ~/matsukaze-utils/<slug> ~/updater/<slug> 2>/dev/null` で解決してよい。
2. そこで `/evolve-anything:evolve` を実行する（まず下見したいなら `--dry-run`）。
3. 1 件処理したら本スキルを再度叩いて残りを確認する。

### Step 4（任意）: 翌朝の SessionStart 通知を更新

次にセッションを開いたとき冒頭に出る「evolve 待ち: …」通知を今すぐ更新したいときだけ実行する:

```bash
evolve-daily-run
```

launchd 自動登録（`bin/evolve-daily-install`）は手動運用では不要。

## 設計の前提（なぜ pull 型か）

evolve の適用判定は `AskUserQuestion`（人間承認）前提で、無人 cron で回すと承認待ちでハングする。
だから無人で回せるのは「待ち一覧の生成」まで（決定論ゼロ LLM）、適用は対話セッションで人間が承認する。
詳細は ADR-050（`docs/decisions/050-daily-evolve-pull-learning-material.md`）。

## allowed-tools

Read, Bash, Glob, Grep

## Tags

queue, daily-evolve, fleet, pull, ADR-050, waiting-list
