# クイックスタート

CLAUDE.md の `## クイックスタート` から移設（#415 圧縮）。コマンド一覧の実体。

```
# 初回セットアップ（新規PJ導入時）
# observe hooks が自動でセッションを記録する。数セッション利用後に下記を回せばよい。
# （旧 /evolve-anything:backfill は #215 で CLI 削除済みの幻なので廃止）
bin/evolve-fleet ingest             # 全 PJ の human 発話を utterances.db に取り込み（任意・ゼロ LLM）

# 日次運用（全フェーズ一括 = 取り込み + 改善提案）
/evolve-anything:evolve

# 修正フィードバックの反映
/evolve-anything:reflect

# 特定スキルの自己進化パターン組み込み
/evolve-anything:evolve-skill my-skill

# 環境の健康診断
/evolve-anything:audit

# 全 PJ 横断の fleet ステータス
bin/evolve-fleet status

# PJ 別 LLM トークン消費の初期取り込み（直近 90 日）
bin/evolve-fleet tokens --backfill

# PJ 別 LLM トークン消費サマリ (TOP 3 + 異常)
bin/evolve-fleet tokens

# 全 PJ の memory を keyword 横断検索（決定論・LLM 非依存）
bin/evolve-fleet recall "duckdb checkpoint"
bin/evolve-fleet recall "認証 ルーティング" --json --limit 5

# インストール済み CC プラグインの最新性診断（update/drift/unknown を決定論検出）
bin/evolve-fleet plugins
bin/evolve-fleet plugins --json

# 全 PJ の学習素材（決定論 weak_signals）を検出・蓄積（#304・ゼロ LLM・冪等）
bin/evolve-fleet detect                   # 直近セッションから検出（daily runner が毎朝自動実行）
bin/evolve-fleet detect --backfill        # 過去チャットを遡って取りこぼしを回収
bin/evolve-fleet detect --pj amamo --dry-run

# 学習素材ベースで「今 evolve すべき PJ」を列挙（決定論・ゼロ LLM）
bin/evolve-fleet queue                    # weak 未処理 + 新規 corr >= 閾値（既定5）の PJ をテーブル表示
bin/evolve-fleet queue --json --threshold 3
# 毎朝の evolve queue 自動実行を launchd に登録（#80・既定 09:00 / --time HH:MM / --uninstall）
bin/evolve-daily-install
bin/evolve-daily-install --uninstall

# advisory 3点セット追加の scaffold（module stub 生成 + 多点配線チェックリスト・#118）
bin/evolve-scaffold-advisory my_check                 # dry-run（stub + checklist 表示）
bin/evolve-scaffold-advisory my_check --with-store --write

# 採用した skill diff を戻す（#402・既定 dry-run。entry_id は戦果ボードか --list が印字する）
bin/evolve-revert --list                  # 戻せる採用の一覧（entry_id つき・read-only・#402 D2）
bin/evolve-revert <entry_id>              # 何が起きるか確認（書込ゼロ）
bin/evolve-revert <entry_id> --apply      # 実際に戻す
bin/evolve-revert <entry_id> --dump-before /tmp/before.md   # 戻さず変更前の本文だけ取り出す

# モデルティア正典の一元管理（#193）
bin/evolve-tier show                      # ティア表 + 正典ソース（file/defaults）を表示
bin/evolve-tier set HEAD --model sonnet --effort max   # 正典を更新（atomic write）
bin/evolve-tier sync                      # targets への反映を dry-run（diff 表示のみ）
bin/evolve-tier sync --apply              # drift のみ実書込（冪等）
bin/evolve-tier drift                     # 正典に無いモデルエイリアスの散文残存を検出

# モデルティアを対話的に変更（上記 CLI の対話 UX ラッパー・diff 提示+承認フロー付き）
/evolve-anything:tier

# エージェント品質診断
/evolve-anything:agent-brushup

# セカンドオピニオン（codex代替）
/evolve-anything:second-opinion

# SPEC.md の初期化・更新
/evolve-anything:spec-keeper init
/evolve-anything:spec-keeper update

# 孤立した依存プラグインのクリーンアップ
claude plugin prune
```
