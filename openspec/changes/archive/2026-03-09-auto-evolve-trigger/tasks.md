## 1. Trigger Engine（共通モジュール）

- [x] 1.1 `scripts/lib/trigger_engine.py` を作成。`TriggerResult` dataclass、`load_trigger_config()`、`evaluate_session_end()` 関数を実装。`evaluate_session_end()` に `audit_overdue` 条件を追加し、`evolve-state.json` の `last_audit_timestamp` を読み取って `interval_days`（デフォルト: 30日）超過を判定
- [x] 1.2 クールダウン管理を実装。`evolve-state.json` の `trigger_history` 読み書き + 期限チェック
- [x] 1.3 `evolve-state.json` の `trigger_config` キー読み込みとデフォルト値マージを実装（zero-config 対応）
- [x] 1.4 trigger_engine のユニットテスト（`scripts/lib/tests/test_trigger_engine.py`）。条件判定、クールダウン、設定読み込み、履歴 pruning、audit_overdue 判定をカバー

## 2. Session End Trigger

- [x] 2.1 `hooks/session_summary.py` に trigger_engine 呼び出しを追加。`triggered=True` 時に `pending-trigger.json` にトリガー結果を書き出し（Stop hook の stdout は Claude コンテキストに入らないため）
- [x] 2.2 `hooks/restore_state.py` に `pending-trigger.json` 読取 + stdout 出力 + ファイル削除ロジックを追加。既存の checkpoint 復元パターンを踏襲
- [x] 2.3 スキル変更検出ロジックを追加。`git diff` で `.claude/skills/*/SKILL.md` の変更を検出し、変更スキル名を提案メッセージに含める
- [x] 2.4 session_summary のエラーハンドリング。trigger_engine の例外をキャッチしセッション終了処理を続行
- [x] 2.5 session_summary trigger のテスト（`hooks/tests/test_session_trigger.py`）。トリガー発火/非発火/エラー時のケースをカバー

## 3. Corrections Threshold Trigger

- [x] 3.1 `trigger_engine.py` に `evaluate_corrections()` 関数を追加。前回 evolve/reflect 以降の corrections 件数を集計し閾値判定
- [x] 3.2 関連スキル特定ロジック。corrections レコードの `last_skill` から上位3件を抽出
- [x] 3.3 `hooks/correction_detect.py` に trigger_engine 呼び出しを追加。閾値到達時に提案メッセージを出力
- [x] 3.4 corrections trigger のテスト。閾値到達/未到達/スキル特定/クールダウンのケースをカバー

## 4. Audit History & Overdue Detection

- [x] 4.1 audit スキルに `last_audit_timestamp` 更新ロジックを追加。audit 実行完了時に `evolve-state.json` の `last_audit_timestamp` を現在時刻に更新
- [x] 4.2 audit 結果の `audit-history.jsonl` 記録ロジックを追加。`{timestamp, coherence_score, telemetry_score, environment_score}` を追記
- [x] 4.3 劣化検出ロジック。前回スコアとの比較で 10% 以上低下時に警告メッセージを出力
- [x] 4.4 audit_history のテスト。履歴記録/劣化検出/pruning をカバー

## 5. Integration & Documentation

- [x] 5.1 evolve.py にトリガー履歴サマリを Report ステージに追加（直近のトリガー発火回数・最終発火日時）
- [x] 5.2 README.md に Auto Trigger セクションを追加（設定方法、閾値カスタマイズ）
- [x] 5.3 CLAUDE.md を更新（auto-evolve-trigger の概要を追記）
- [x] 5.4 E2E テスト。session_summary → trigger_engine → pending-trigger.json → restore_state → メッセージ出力の一連フローを検証
