[🇬🇧 English](README.md) | [🇯🇵 日本語](README.ja.md)

# evolve-anything

Claude Code のスキル/ルールを **自律的に観測・発見・淘汰・進化** させ、**LLM 直接パッチで最適化** する Claude Code Plugin。

> リリース情報: **v1.124.1** ・ **23個の userConfig**

## クイックスタート

```bash
# マーケットプレイスを登録（初回のみ）
claude plugin marketplace add todoroki-godai/evolve-anything

# インストール
claude plugin install evolve-anything@evolve-anything --scope user

# Claude Code を再起動
```

再起動後、Observe hooks が自動で動き始め、スキル使用・エラー・修正フィードバックを記録する。

`bin/` に bareコマンド（`evolve-audit`, `evolve` 等）も提供。PATH に追加すると CLI から直接実行できる:
```bash
export PATH="$(claude plugin path evolve-anything)/bin:$PATH"
evolve-audit
```

```bash
# 環境の健康診断
/evolve-anything:audit

# 過去セッションの human 発話を一括収集（任意・ゼロ LLM）
# ※ Skill/Agent 観測は observe hooks が進行形で自動記録する。専用 backfill CLI は廃止（#215/#486）
bin/evolve-fleet ingest

# 日次運用（まず dry-run でプレビュー → 確認後に本実行。取り込みも内包）
/evolve-anything:evolve --dry-run
/evolve-anything:evolve
```

普段は **`evolve` を日に1回叩くだけ**。データが足りなければ自動でスキップを提案してくれる。

## Python 依存

プラグインの source-tree 起動は維持しつつ、Python 依存グループの単一ソースを `pyproject.toml` に置く。使う機能に応じて導入する:

```bash
# 必須の parser のみ
python3 -m pip install -e ".[core]"

# DuckDB を使う telemetry / storage 機能
python3 -m pip install -e ".[storage]"

# TF-IDF・数値類似度・クラスタリング機能
python3 -m pip install -e ".[analysis]"

# contributor 向け: core/storage 機能 + pytest / pytest-xdist
python3 -m pip install -e ".[dev]"

# 数値 analysis 機能も扱う場合だけ追加
python3 -m pip install -e ".[dev,analysis]"
```

旧 `scripts/requirements.txt` は storage 専用の互換入口として残す。`scripts/` から `pip -r` で導入すると `../pyproject.toml` に委譲する。

## 全体像 — 4つの柱

evolve-anything は **4つの独立した柱** で構成される。

```
┌─────────────────────────────────────────────────────────┐
│  柱1: 自律進化パイプライン                                │
│  Observe(hooks) → Diagnose → Compile → Housekeeping     │
│  → evolve で一括実行                                      │
├─────────────────────────────────────────────────────────┤
│  柱2: 修正フィードバックループ                             │
│  correction_detect(hook) → corrections.jsonl → Reflect   │
├─────────────────────────────────────────────────────────┤
│  柱3: 直接パッチ最適化                                     │
│  Generate-Fitness → Optimize → RL-Loop → Evolve-Fitness  │
├─────────────────────────────────────────────────────────┤
│  柱4: fleet 観測・介入                                     │
│  evolve-fleet status → 全 PJ 横断で env_score / 導入状況可視化│
└─────────────────────────────────────────────────────────┘
```

| 柱 | 何をするか | メインコマンド |
|----|-----------|--------------|
| 自律進化 | 使用データからパターン検出→スキル生成→淘汰→進化 | `/evolve-anything:evolve` |
| フィードバック | ユーザーの修正（「いや、違う」等）を検出→ルールに反映 | `/evolve-anything:reflect` |
| 直接パッチ最適化 | corrections/context → LLM 1パスパッチ → regression gate | `/evolve-anything:evolve-loop` |
| **fleet 観測・介入** | 全 PJ 横断で env_score / 導入状況を一覧表示。全 PJ memory の keyword 横断検索 `recall` も提供 | `bin/evolve-fleet status` / `bin/evolve-fleet recall "<query>"` |
| エージェント管理 | エージェント定義の品質診断・改善提案 | `/evolve-anything:agent-brushup` |
| セカンドオピニオン | 独立した cold-read セカンドオピニオン | `/evolve-anything:second-opinion` |
| 仕様管理 | SPEC.md + ADR の管理、L1/L2 自動昇格 | `/evolve-anything:spec-keeper` |
| 行き詰まり突破 | 「惜しいがブレイクスルーしない」問題を診断→戦略提案→Agent起動 | `/evolve-anything:breakthrough` |
| 成長可視化 (NFD) | Lv.1-10 レベルシステム + フェーズ自動判定 + 🏆 戦果ボード（手直し回数の増減・accepted/rejected/pending/excluded・取り下げ候補） | `/evolve-anything:audit --growth` |

## やりたいこと別ガイド

| やりたいこと | コマンド |
|-------------|---------|
| 日次メンテナンス（プレビュー→本実行） | `evolve --dry-run` → `evolve` |
| 特定スキルをピンポイント改善 | `evolve-loop my-skill` |
| 修正フィードバックをルールに反映 | `reflect` |
| 蓄積されたフィードバックを確認 | `reflect --view` |
| 全 skills/rules の棚卸し | `audit` |
| プロジェクト固有の評価関数を作成 | `generate-fitness --ask` |
| テレメトリの収集を始める | 通常どおり Claude Code を使う（observe hooks が新規セッションを自動記録）→ `evolve` |
| 評価関数自体を改善 | `evolve-fitness` |
| エージェント定義を診断・改善 | `agent-brushup` |
| 独立したセカンドオピニオンを取得 | `second-opinion` |
| SPEC.md を初期化・更新 | `spec-keeper init` / `spec-keeper update` |
| 行き詰まり問題の突破 | `breakthrough` |
| 環境の成長レポート | `audit --growth` |
| マージ・デプロイ後の後片付け | `cleanup` |
| 全 PJ 横断の fleet ステータス | `bin/evolve-fleet status` |
| 全 PJ の memory を keyword 横断検索 | `bin/evolve-fleet recall "<query>"` |
| インストール済み CC プラグインの最新性診断 | `bin/evolve-fleet plugins` |
| 全 PJ human 発話の恒久アーカイブを更新 | `bin/evolve-fleet ingest` |
| 暗黙修正シグナルの確認と corrections への昇格 | `reflect --show-weak-signals` / `reflect --promote-weak` |

> すべてのコマンドは `/evolve-anything:` プレフィックス付きで呼び出す（例: `/evolve-anything:evolve`）

## スキル一覧（23個の公開スキル）

> **方針**: `skills/*/SKILL.md` の各 `name:` が公開コマンドの単一ソース。slash command は `/evolve-anything:<skill>` で、実装ディレクトリ名が異なる場合がある。たとえば `/evolve-anything:evolve-loop` の実装は `skills/evolve-loop-orchestrator/` にある。

| スキル | 柱 | 説明 |
|--------|-----|------|
| `agent-brushup` | エージェント管理 | エージェント定義を診断・改善 |
| `audit` | 自律進化 | skills/rules/memory の棚卸しと健全性確認 |
| `backfill` | 廃止済みリダイレクト | 現行の observe → evolve 取り込み経路を案内するだけで、backfill は実行しない |
| `breakthrough` | 行き詰まり突破 | 停滞を診断し、戦略を提案 |
| `cleanup` | ユーティリティ | マージ・デプロイ後の痕跡を安全に後片付け |
| `discover` | 自律進化 | パターンを検出してスキル/ルール候補を生成 |
| `docs-refresh` | ドキュメント | リリース後に HTML ドキュメントサイトを更新 |
| `evolve` | 自律進化 | 日次進化パイプラインを実行 |
| `evolve-fitness` | 直接パッチ最適化 | accept/reject データから評価関数を改善 |
| `evolve-loop` | 直接パッチ最適化 | ベースライン→パッチ→評価→人間確認 |
| `evolve-skill` | 直接パッチ最適化 | 1つのスキルに自己進化パターンを適用 |
| `generate-fitness` | 直接パッチ最適化 | PJ 固有の評価関数を生成 |
| `implement` | 構造化実装 | 承認済み計画を分解・実装・検証しテレメトリを記録 |
| `import` | fleet | 確認ゲート付きでコミュニティスキルを import |
| `pitfall-curate` | pitfall 運用 | pitfalls を分類・重複排除・配布 |
| `prune` | 自律進化 | 未使用・重複アーティファクトを統合候補として検出 |
| `queue` | fleet | 手動 evolve に十分な学習素材がある PJ を表示 |
| `reflect` | フィードバック | 修正フィードバックを確認・昇格 |
| `release-notes-review` | ユーティリティ | Claude Code リリースノートと環境健全性を確認 |
| `report-feedback` | フィードバック | evolve-anything 自身への改善フィードバックを issue 候補化 |
| `second-opinion` | セカンドオピニオン | 独立した cold-read レビューを取得 |
| `spec-keeper` | 仕様管理 | SPEC.md と ADR を維持 |
| `tier` | モデルティア管理 | モデルティア方針を安全に表示・更新 |

## bare CLI 一覧（26コマンド）

`bin/` が executable 名の単一ソース。bare CLI を使うときだけ `bin/` を `PATH` に追加し、通常のプラグイン操作には slash skill を使う。

| コマンド | コマンド | コマンド |
|----------|----------|----------|
| `evolve` | `evolve-audit` | `evolve-audit-aggregate` |
| `evolve-agent-task` | `evolve-backfill-turn-indices` | `evolve-codex-config-cleanup` |
| `evolve-daily-install` | `evolve-daily-run` | `evolve-discover` |
| `evolve-dogfood-gate` | `evolve-fleet` | `evolve-gain` |
| `evolve-loop` | `evolve-loop-ablation` | `evolve-migrate-legacy-accept` |
| `evolve-optimize` | `evolve-prompt-compare` | `evolve-prune` |
| `evolve-reflect` | `evolve-release-sync` | `evolve-reorganize` |
| `evolve-revert` | `evolve-scaffold-advisory` | `evolve-score-noise` |
| `evolve-tier` | `evolve-usage-log` | |

## Hooks（24件の登録、12イベント）

`hooks/hooks.json` が単一ソース。24件の登録には Edit / Write / MultiEdit の重複した `PostToolUse` 登録が含まれ、LLM コストゼロで19個の異なる hook script を実行する。

| Hook script | イベント / matcher | 主な効果 |
|-------------|-------------------|----------|
| `correction_detect` | UserPromptSubmit | 修正フィードバックを記録 |
| `ctx_guard` | UserPromptSubmit | context 占有率が設定閾値を超えたら警告 |
| `pitfall_injector` | UserPromptSubmit | 登録済みの関連 pitfall を注入 |
| `workflow_context` | PreToolUse / Skill | workflow context を記録 |
| `pitfall_commit_gate` | PreToolUse / Bash | 登録済み pitfall の危険な変更をブロック |
| `skill_activation_log` | PostToolUse / Skill | スキル発火を記録 |
| `observe` | PostToolUse / Skill, Agent | 使用状況とエラーを記録 |
| `post_tool_use_memory` | PostToolUse / Edit, Write, MultiEdit | memory 候補を記録 |
| `pitfall_lint` | PostToolUse / Edit, Write, MultiEdit | pitfall 形式 drift を警告 |
| `subagent_observe` | SubagentStop | subagent 完遂情報を記録 |
| `session_summary` | Stop | session / workflow summary を記録 |
| `record_verbosity` | Stop | 回答長テレメトリを記録 |
| `stop_failure` | StopFailure | API 失敗を記録 |
| `instructions_loaded` | InstructionsLoaded | 状態を復元しガイダンスを出力 |
| `save_state` | PreCompact | checkpoint を保存 |
| `post_compact` | PostCompact | compact 後のガイダンスを出力 |
| `file_changed` | FileChanged / CLAUDE.md\|SKILL.md | 関連編集後に audit を提案 |
| `permission_denied` | PermissionDenied | denied permission を記録 |
| `restore_state` | SessionStart | session state を復元 |

### Auto Trigger

セッション終了時・corrections 蓄積時に、evolve/audit の実行を自動提案する（実行はしない）。

| 条件 | デフォルト閾値 | 評価タイミング |
|------|---------------|---------------|
| 前回 evolve からのセッション数 | ≥ 10 | セッション終了時 |
| 前回 evolve からの経過日数 | ≥ 7 | セッション終了時 |
| corrections 蓄積件数 | ≥ 10 | correction 検出時 |
| 前回 audit からの経過日数 | ≥ 30 | セッション終了時 |

設定は `~/.claude/evolve-anything/evolve-state.json` の `trigger_config` で上書き可能:

```json
{
  "trigger_config": {
    "enabled": true,
    "triggers": {
      "session_end": { "min_sessions": 10, "max_days": 7 },
      "corrections": { "threshold": 10 },
      "audit_overdue": { "interval_days": 30 }
    },
    "cooldown_hours": 24
  }
}
```

無効化: `"trigger_config": { "enabled": false }`

---

以下は必要に応じて参照する詳細セクション。

<details>
<summary><strong>各スキルの詳細オプション</strong></summary>

### evolve

```
/evolve-anything:evolve --dry-run    # プレビュー（推奨）
/evolve-anything:evolve              # 本実行
```

実行フェーズ: Diagnose(Discover+Audit+Reorganize) → Compile(Optimize+Remediation+Reflect) → Housekeeping(Prune+Fitness Evolution) → Report

前回以降のセッション数が3未満 or 10観測未満の場合はスキップを推奨。

### discover

```
/evolve-anything:discover                    # パターン検出＋候補生成（enrich 統合済み）
/evolve-anything:discover --scope global     # グローバルスコープで検出
```

検出基準: 行動パターン（5+回）→スキル候補、エラーパターン（3+回）→ルール候補、却下理由（3+回）→ルール候補。組み込み Agent は `agent_usage_summary` に分離。推奨ルール/hook 未導入も検出。Jaccard 係数で既存スキルとの照合も実行（enrich 統合）。

### prune

```
/evolve-anything:prune                 # 淘汰候補を検出
/evolve-anything:prune --restore       # アーカイブから復元
/evolve-anything:prune --list-archive  # アーカイブ一覧
```

各候補に推薦ラベル（archive推奨 / keep推奨 / 要確認）と description を付与。TF-IDF 類似度フィルタで偽陽性を除外。参照型スキルは淘汰対象から除外。

### reflect

```
/evolve-anything:reflect                          # 対話レビュー
/evolve-anything:reflect --view                   # pending 一覧
/evolve-anything:reflect --dry-run                # プレビューのみ
/evolve-anything:reflect --apply-all              # 高信頼度を一括適用（>= 0.85）
/evolve-anything:reflect --apply-all --min-confidence 0.70  # 閾値変更
/evolve-anything:reflect --skip-semantic          # セマンティック検証を無効化
```

### evolve-loop

```
/evolve-anything:evolve-loop my-skill              # 1ループ
/evolve-anything:evolve-loop my-skill --loops 3    # 3ループ
/evolve-anything:evolve-loop my-skill --auto       # 人間確認スキップ
```

### generate-fitness

```
/evolve-anything:generate-fitness                # 基本
/evolve-anything:generate-fitness --ask          # 品質基準を質問してから生成
/evolve-anything:generate-fitness --name bot     # 関数名を指定
```

### audit

```
/evolve-anything:audit [project-dir]
/evolve-anything:audit --skip-rescore    # 品質計測をスキップ
/evolve-anything:audit --memory-context  # MEMORY セマンティック検証用 JSON 出力
```

レポート内容: Skill Quality Trends / MEMORY Health / Plugin Usage / OpenSpec Workflow Analytics / ハードコード値検出

### backfill（廃止 — #215/#486）

専用 CLI（`rl-backfill` 等）は #215 で削除済み。観測は observe hooks が進行形で自動記録し、
取り込み・分析は `evolve` / `audit` に統合済み。human 発話だけ先に取り込みたい場合のみ:

```
bin/evolve-fleet ingest                # 全 PJ の human 発話を utterances.db に取り込み（ゼロ LLM）
/evolve-anything:evolve --dry-run      # 取り込み + 改善提案（dry-run プレビュー）
```

</details>

<details>
<summary><strong>データフロー</strong></summary>

すべてのデータは `~/.claude/evolve-anything/` に保存される。

```
~/.claude/evolve-anything/
├── usage.jsonl           # スキル/エージェント使用記録
├── errors.jsonl          # エラー記録
├── sessions.jsonl        # セッションサマリ
├── workflows.jsonl       # ワークフローシーケンス
├── subagents.jsonl       # サブエージェント完了データ
├── usage-registry.jsonl  # グローバルスキル使用レジストリ
├── corrections.jsonl     # 修正フィードバック
├── false_positives.jsonl # 偽陽性 corrections（SHA-256 管理）
├── workflow_stats.json   # ワークフロー統計（workflow_analysis.py が出力）
├── checkpoint.json       # 進化状態チェックポイント
├── archive/              # prune でアーカイブされたファイル
└── feedback-drafts/      # ローカル保存フィードバック
```

| ファイル | 書き込み元 | 読み取り先 |
|---------|-----------|-----------|
| `usage.jsonl` | observe hook | discover, prune, audit |
| `errors.jsonl` | observe hook | discover, audit |
| `sessions.jsonl` | session_summary hook | audit, evolve, discover |
| `workflows.jsonl` | session_summary hook | audit, discover |
| `corrections.jsonl` | correction_detect hook | reflect, discover, evolve, prune |
| `false_positives.jsonl` | reflect | correction_detect |
| `workflow_stats.json` | workflow_analysis.py | optimize, evolve-scorer, generate-fitness |
| `checkpoint.json` | save_state hook | restore_state hook |

</details>

<details>
<summary><strong>適応度関数</strong></summary>

### 組み込み

| 関数 | 説明 |
|------|------|
| `default` | LLM による汎用評価（明確性・完全性・構造・実用性） |
| `skill_quality` | ルールベースの構造品質チェック（+ CSO security 軸） |
| `coherence` | 環境の構造的整合性（Coverage/Consistency/Completeness/Efficiency の4軸） |
| `telemetry` | テレメトリ駆動の環境実効性（Utilization/Effectiveness/Implicit Reward の3軸） |
| `constitutional` | 原則ベース LLM Judge 評価（PJ固有原則 × 4レイヤー） |
| `chaos` | 仮想除去ロバストネス（Rules/Skills を仮想削除し Coherence ΔScore で SPOF 検出） |
| `environment` | coherence + telemetry + constitutional の動的重み統合 |
| `plugin` | プラグイン統合 fitness |

`telemetry` / `environment` / `constitutional` は `--fitness` フラグでは使用しない（プロジェクトパスが必要なため）。`audit --coherence-score --telemetry-score --constitutional-score` で利用する。

### プロジェクト固有（カスタム）

`scripts/rl/fitness/{name}.py` に配置 → `--fitness {name}` で使用。

インターフェース: stdin でスキル内容を受け取り、0.0〜1.0 を stdout に出力。

```python
#!/usr/bin/env python3
import sys

def evaluate(content: str) -> float:
    score = 0.0
    if "必須キーワード" in content:
        score += 0.5
    return score

def main():
    content = sys.stdin.read()
    print(f"{evaluate(content)}")

if __name__ == "__main__":
    main()
```

### 評価関数の育成

accept/reject データが30件以上溜まると `/evolve-anything:evolve-fitness` で改善を提案:
- score-acceptance 相関 < 0.50 → 再キャリブレーション推奨
- 同じ rejection_reason が3回以上 → 新軸追加を提案

</details>

<details>
<summary><strong>evolve-scorer のドメイン自動判定</strong></summary>

CLAUDE.md からドメインを推定し、評価軸を自動切替。

| ドメイン | 評価軸 |
|----------|--------|
| ゲーム | 没入感・面白さ・バランス・具体性 |
| API/バックエンド | 正確性・堅牢性・保守性・セキュリティ |
| Bot/対話 | パーソナリティ適合・有用性・トーン一貫性 |
| ドキュメント | 正確性・可読性・実行可能性・完全性 |

スコア構成: 技術品質 (40%) + ドメイン品質 (40%) + 構造品質 (20%)

</details>

<details>
<summary><strong>導入ストーリー（Slack Bot プロジェクトの例）</strong></summary>

### 第1幕: Observe — データが貯まる

インストール後、hooks が自動でスキル使用・エラー・修正フィードバックを記録。あるプロジェクトでは、`/bot-create` で personality 設定が抜け落ちる事故が起きていた。

### 第2幕: Discover → Optimize — パターンから改善へ

`/evolve-anything:discover` で「`/bot-create` 後に手動で personality を追加している」パターンを検出。ルール候補を自動生成。さらに直接パッチ最適化でスキル自体を改善し、スコアが 0.62 → 0.84 に上昇。

### 第3幕: Reflect — フィードバックが活きる

「いや、personality を先に設定して」という修正フィードバックが `/evolve-anything:reflect` で CLAUDE.md に自動反映され、同じミスが発生しなくなった。

### 第4幕: 日次運用

| タイミング | やること |
|-----------|---------|
| 新スキル追加時 | `optimize` で1回最適化 → diff レビュー |
| 日次/週次 | `evolve --dry-run` → 確認 → `evolve` |
| 修正が溜まったとき | `reflect` でフィードバック反映 |

</details>

<details>
<summary><strong>claude-reflect からの移行</strong></summary>

```bash
# データ移行（冪等・二重追記防止）
python3 <PLUGIN_DIR>/scripts/migrate_reflect_queue.py

# 確認
/evolve-anything:reflect --view

# アンインストール
claude plugin uninstall claude-reflect
```

</details>

## テスト

```bash
# bare コマンドで全件収集（収集パスは pytest.ini の testpaths が単一ソース）
python3 -m pytest -v

# プラグイン定義の整合性チェック
claude plugin validate
```

## Acknowledgements

correction detection・confidence decay・multi-target routing のアーキテクチャは [claude-reflect](https://github.com/bayramnnakov/claude-reflect)（MIT License, Bayram Annakov）を参考にしています。

## ライセンス

MIT
