## Context

rl-anything は 7 つの observe hooks でテレメトリを収集し、`/evolve` で Diagnose → Compile → Housekeeping の3ステージパイプラインを実行する。しかし `/evolve` は手動実行が前提で、ユーザーが忘れると進化ループが止まる。

現在の hooks アーキテクチャ:
- `observe.py` (PostToolUse async): usage/errors 記録
- `correction_detect.py` (PostToolUse async): corrections 記録
- `session_summary.py` (Stop async): セッション要約 + workflows 記録
- `restore_state.py` (SessionStart): チェックポイント復元 + stdout 出力
- `evolve-state.json`: 前回 evolve 実行状態を保持

## Goals / Non-Goals

**Goals:**
- セッション終了時に evolve 実行条件を自動評価し、条件達成時にユーザーへ提案する
- corrections 蓄積が閾値に達した時に関連スキルの再最適化を提案する
- 前回 audit から一定期間経過した場合にセッション終了時に audit 実行を提案する
- トリガー条件・閾値をユーザーがカスタマイズできる

**Non-Goals:**
- ユーザー承認なしの自動実行（Graduated Autonomy = Gap 2 Ph5 で対応）
- hooks の実行モデル変更（既存の async hook パターンを維持）
- evolve パイプライン自体の変更（トリガーのみ追加）

## Decisions

### D1: トリガー評価を共通エンジンに集約

**決定**: `scripts/lib/trigger_engine.py` に条件評価ロジックを集約し、各 hook から呼び出す。

**理由**: session_summary と correction_detect の両方がトリガー判定を行うため、条件評価・クールダウン・設定読み込みを重複させない。evolve.py の `load_evolve_state()` / `save_evolve_state()` と連携する設計。

**代替案**: 各 hook に直接ロジックを埋め込む → 閾値変更時に2箇所修正が必要になるため却下。

### D2: hook タイプ別出力方式

**決定**: hooks のタイプに応じて出力方式を使い分ける。

| Hook タイプ | 出力方式 | 理由 |
|-------------|---------|------|
| Stop (async) | `pending-trigger.json` にファイル書出し | Stop hook の stdout は Claude コンテキストに入らない |
| UserPromptSubmit | stdout 出力 | Claude コンテキストに入る |
| SessionStart | ファイル読取 + stdout 出力 | `restore_state.py` が `pending-trigger.json` を読み取り stdout に出力。配信後にファイル削除 |

**理由**: Claude Code の Stop hook は async で実行され、stdout がユーザーの Claude コンテキストに入らない。そのため Stop hook でのトリガー結果は `pending-trigger.json` に書き出し、次回 SessionStart 時に `restore_state.py` がファイルを読み取って stdout に出力する遅延配信方式を採用する。

**代替案**: Stop hook で直接 stdout 出力 → Claude コンテキストに入らないため却下。

### D3: トリガー条件の設計

**決定**: 以下の4条件を OR 評価（いずれか1つでもトリガー）:

| 条件 | デフォルト閾値 | 評価タイミング |
|------|---------------|---------------|
| 前回 evolve からのセッション数 | ≥ 10 | セッション終了時 |
| 前回 evolve からの経過日数 | ≥ 7 | セッション終了時 |
| corrections 蓄積件数 | ≥ 10 | correction 検出時 |
| 前回 audit からの経過日数 | ≥ 30 | セッション終了時 |

**クールダウン**: トリガーメッセージは 1 セッションにつき最大1回。同一条件の再提案は24時間以上の間隔。

**理由**: evolve.py の既存データ十分性チェック（3セッション/10観測）より少し余裕を持たせた閾値。corrections は `/reflect` も含め再最適化の契機になるため独立トリガー化。audit overdue は cron 廃止に伴い session-end 評価に統合。

### D4: ユーザー設定は evolve-state.json に統合

**決定**: `evolve-state.json` の `trigger_config` キーにユーザー設定を保存する。

```json
{
  "last_run_timestamp": "...",
  "last_audit_timestamp": "...",
  "trigger_config": {
    "enabled": true,
    "triggers": {
      "session_end": { "enabled": true, "min_sessions": 10, "max_days": 7 },
      "corrections": { "enabled": true, "threshold": 10 },
      "audit_overdue": { "enabled": true, "interval_days": 30 }
    },
    "cooldown_hours": 24
  },
  "trigger_history": []
}
```

**理由**: 既存の `evolve-state.json` と同一ファイルに統合することで、新規設定ファイルの追加を避け、既存の `load_evolve_state()` / `save_evolve_state()` パターンをそのまま活用できる。`trigger_config` キーが未設定の場合はデフォルト値で動作（zero-config）。

**代替案**: `trigger-config.json` を新規作成 → 既存の設定ファイルパターン（evolve-state.json に集約）から逸脱するため却下。

### D5: 定期 audit は session-end 評価に統合

**決定**: cron ベースの月次 audit を廃止し、セッション終了時に `last_audit_timestamp` からの経過日数を評価する `audit_overdue` 条件に統合する。

**理由**: Claude Code の CronCreate はセッションローカルで、セッション終了後は cron が消失する。月次スケジュールの永続的な cron としては使えない。session-end トリガーの一条件として統合すれば、ユーザーがセッションを開始するたびに audit 必要性を評価できる。

**代替案**: CronCreate で月次 cron を登録 → セッション終了で消失するため実用的でなく却下。

### D6: correction トリガーでの関連スキル特定

**決定**: correction レコードの `last_skill` フィールドと `context`（ファイルパス）から関連スキルを特定し、`/rl-anything:optimize <skill>` を提案する。

**理由**: corrections.jsonl には `last_skill`（修正が発生したスキル）が記録されているため、再最適化対象を特定できる。`last_skill` が空の場合は汎用の `/evolve` を提案。

## Risks / Trade-offs

- **[通知疲れ]** → クールダウン（24h）+ セッション単位の重複排除で緩和。設定で `enabled: false` にできる
- **[hooks パフォーマンス]** → trigger_engine は JSON 読み込み + 比較のみ（LLM 呼び出しなし）。既存の session_summary.py と同程度の負荷
- **[evolve-state.json の競合]** → 読み取り専用（hooks はトリガー履歴のみ書き込み、evolve 実行状態は evolve.py が管理）で書き込み競合を回避
- **[pending-trigger.json ライフサイクル]** → Stop hook で書き出し、SessionStart hook で読取+削除。SessionStart が実行されない場合は次回 SessionStart まで残留するが、影響は軽微（古い提案が表示されるのみ）
