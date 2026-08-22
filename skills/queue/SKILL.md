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

`material_count = weak_unprocessed`（未昇格・未 expired・content-rich channel の weak_signals）`+
new_corrections`（前回 evolve 以降の新規 corrections）。これが threshold（既定 5）以上の PJ が「待ち」。
加えて `correction_backlog`（昇格済み・反映先未定）が1件以上ある PJ は、material が閾値未満でも
休眠在庫を永久滞留させないため「待ち」に含める（#515）。在庫は `material_count` に足さず、表の
`BACKLOG` 列と `REASON` で別表示するため、既存の material 順位は変わらない。
bootstrap phase で破棄/TTL 任せと判断済み（`bootstrap_done-<slug>.marker` 設置以前に検出）の weak は
material から除外される（#94）。content-poor channel（esc/手編集＝昇格手段なし）も material に数えず
footer + `--json` の `weak_content_poor` で透明化される（#113）。`WEAK` 列は content-rich 未処理のみ＝
promoted 昇格済み・TTL 失効・bootstrap 消化・content-poor を除いた実残数で、検出された weak の生総数
とは一致しない（待ち時に footer で明示・②）。

### Step 2: 結果を読み解いて提示

先頭行に `queue_status`（`READY` / `SETUP_REQUIRED` / `EMPTY`）+ `queue_status_reason` が必ず1行出る
（#267）。queue が空のとき「本当に素材が無い」(`EMPTY`) のか「素材はあるのに処理できていない」
(`SETUP_REQUIRED` — untracked material / skipped_dead / skipped_phantom / 未帰属 corrections のいずれか
が原因) のかをこの行で見分ける。`SETUP_REQUIRED` のときは `queue_status_reason` が示す内訳（例:
`untracked material 2 件` / `skipped_dead 1 件`）に沿って footer の該当セクションを確認する。

- **待ち 0 件（`queue_status=EMPTY`）** → 「今日は evolve 待ちなし」で終了（無理に evolve しない）。
  ただし**素材の検出自体が長期間走っていない**可能性は別途疑う。素材は daily runner の
  `fleet detect`（#304・step 1c）が毎朝生成するので、launchd 未登録・runner 停止中は素材が
  増えず永久に `EMPTY` になる。疑わしければ `evolve-fleet detect --dry-run` で「本当に素材が無い」
  のか「検出が走っていないだけ」かを切り分ける（過去分の回収は `--backfill`）。
- **待ち 0 件（`queue_status=SETUP_REQUIRED`）** → 「待ちは無いが処理できない学習素材がある」ことを
  `queue_status_reason` の内訳とともに提示する（放置しない方がよい）。
- **待ちあり** → テーブルの上から、各 PJ の `REASON`（weak=… + new corr=…）をそのまま添えて提示する。
  `MATERIAL` が大きい PJ ほど溜まった学習素材が多い＝改善余地が大きい、という読み方を 1 行添える。

`REASON` に `/ verify 待ち N 件（前回 accept・検証可能）` または `（…・露出セッションなし）` が付いて
いたら、直近 evolve run で accept 済みだがまだ効果検証していない提案が N 件あることを示す（#267）。
「検証可能」は accept 後に実タスクセッションが実行済み＝効果を確かめられる状態、「露出セッションなし」
は accept 後まだ何もタスクをこなしていない＝評価材料が無い状態。この verify 待ちは **material が
threshold 未満でも queue に残る**（`REASON` が `verify 待ち N 件（…） / material=M < threshold` の形なら
material 閾値未満での昇格 — evolve 直後で material がリセットされた直後こそ、直前の accept を検証すべき
タイミングだと示している）。verify 待ちは記録から14日経つと自動的に表示から外れる（read 時失効・
新ストア不要）。

### Step 3: 上から処理する（適用は人間承認）

evolve はカレント PJ 対象（project-dir 引数なし）。待ち PJ を処理するには、その PJ へ移動してから evolve:

1. **`evolve-fleet queue --json` の各 entry が持つ `project_path` を直接使う**。先頭の待ち PJ の
   `project_path` へ `/cd <project_path>` で移動する（CC v2.1.169+・prompt cache を壊さない）。
   親 dir のハードコード ls（`~/matsukaze-utils` / `~/updater` 等）は不要かつ `~/games` 配下等を
   取りこぼすので使わない — queue が返す実パスが SoT。
2. そこで `/evolve-anything:evolve` を実行する（まず下見したいなら `--dry-run`）。
3. 1 件処理したら本スキルを再度叩いて残りを確認する。

注記:
- `LAST_EVOLVE` が `never` なのは `evolve --drain` を 1 度回すまでの**仕様**（per-PJ の前回 evolve 時刻を
  記録するストアがまだ空なだけ・初見で壊れて見えるが正常）。
- 実ディレクトリが不在の tracked PJ（rename 済の dead パス等）は queue が自動 skip し、footer に
  `（skipped N dead: …）` として透明表示する（待ちには出さない）。
- bootstrap phase で「全件破棄」「TTL 失効に任せる」を選んだ PJ は、marker 設置**以前**に検出した
  weak を material から自動除外する（#94）。除外件数は footer に `（bootstrap 消化済みを待ちから除外:
  …）` で透明表示。「破棄したのに queue に出続ける」齟齬の根治（marker 設置後の新規 weak は残る）。

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
