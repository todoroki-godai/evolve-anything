## 1. パターン統合（hooks/common.py）

- [x] 1.1 claude-reflect の英語 12 (Explicit 1 + Positive 3 + Correction 8) + Guardrail 8 パターンを `CORRECTION_PATTERNS` にマージ: pattern, confidence, type, decay_days, strong フラグを含む統一辞書化
- [x] 1.2 `FALSE_POSITIVE_PATTERNS` に claude-reflect の 7 パターンを追加（疑問文、タスクリクエスト、エラー記述等）
- [x] 1.3 `should_include_message()` フィルタを common.py に追加: XMLタグ・JSON・ツール結果・セッション継続メッセージのスキップ
- [x] 1.4 信頼度計算の統合: 長さ調整ロジック（短文ブースト/長文削減）、強弱フラグ判定を `calculate_confidence()` 関数として実装
- [x] 1.5 パターン統合のユニットテスト: CJK/英語/Guardrail/Explicit/Positive 各タイプの検出、偽陽性フィルタ、信頼度計算、"remember:" バイパス
- [x] 1.6 `detect_correction()` 戻り値型の互換テスト: 戻り値が `(correction_type, confidence)` タプルであること、`backfill.py` の `correction_type, _ = result` アンパック、`test_correction_detect.py` の `result[0]` アクセスが動作することを検証
- [x] 1.7 複数パターンマッチの実装: `detect_correction()` に加え `detect_all_patterns(text)` を新設。全マッチパターンキーのリストを返し、`matched_patterns` フィールドと信頼度計算（3+→0.85, 2→0.75）に使用

## 2. corrections.jsonl スキーマ拡張

- [x] 2.1 `correction_detect.py` を更新: 統合パターン使用、should_include_message フィルタ適用、拡張スキーマ（matched_patterns, project_path, sentiment, decay_days, routing_hint, guardrail, reflect_status, extracted_learning, source）で出力。`project_path` は `os.environ.get("CLAUDE_PROJECT_DIR")` で取得、`matched_patterns` は `detect_all_patterns()` で全マッチを記録
- [x] 2.2 backfill.py の correction 抽出を更新: 統合パターンセット使用、拡張スキーマで出力
- [x] 2.2a backfill.py にツール拒否抽出を追加: セッション JSONL から `"The user doesn't want to proceed"` + `"the user said:"` マーカーを検出し、correction レコード（`source: "backfill"`）として記録
- [x] 2.3 既存 corrections.jsonl 読込コードの後方互換: prune.py の `load_corrections()` 等で新旧フィールド両対応
- [x] 2.4 correction_detect.py + backfill correction 抽出のユニットテスト
- [x] 2.5 既存 `test_correction_detect.py` の `assert "source" not in record` を `assert record["source"] == "hook"` に修正（specs の `source: "hook"` MUST 要件との整合）

## 3. ユーティリティ移植

- [x] 3.1 `scripts/reflect_utils.py` を作成: `find_claude_files()`, `suggest_claude_file()`, `_parse_rule_frontmatter()`, `read_all_memory_entries()`, `read_auto_memory()`, `suggest_auto_memory_topic()` を移植。8層メモリ階層（global/root/local/subdirectory/rule/user-rule/auto-memory/skill）に対応
- [x] 3.2 `scripts/lib/semantic_detector.py` を作成: `semantic_analyze()`, `validate_corrections()`, `detect_contradictions()`, `ANALYSIS_PROMPT` を移植
- [x] 3.3 reflect_utils.py のユニットテスト: find_claude_files の8層パス探索（CLAUDE.local.md 含む）、suggest_claude_file のルーティング判定、frontmatter パース、suggest_auto_memory_topic のトピック分類、read_auto_memory の読み込み
- [x] 3.4 semantic_detector.py のユニットテスト: JSON 抽出、レスポンス正規化、タイムアウトフォールバック、JSON パース失敗フォールバック、バッチサイズ分割（20件超）（claude -p のモック）

## 4. /rl-anything:reflect スキル

- [x] 4.1 `skills/reflect/scripts/reflect.py` を作成: pending corrections 抽出、プロジェクトフィルタリング、重複検出、ルーティング提案、JSON 出力
- [x] 4.2 `skills/reflect/SKILL.md` を作成: reflect.py 実行 → 対話レビュー（AskUserQuestion で approve/edit/skip/skip-remaining）→ Edit ツールで書込 → reflect_status 更新。3件目以降は「残り全部 skip」選択肢を追加。昇格候補は corrections レビュー完了後に別セクション表示
- [x] 4.3 reflect.py のユニットテスト: pending 抽出、プロジェクトフィルタ（same/global/other/null）、重複検出、ルーティング。project_path が null の場合 global-looking 扱いになることを検証
- [x] 4.4 セマンティック検証統合: reflect.py でセマンティック検証をデフォルト有効化。バッチサイズ上限 20件で `claude -p` に送信（20件超は複数バッチ分割）。`--skip-semantic` で無効化可能。JSON パース失敗時は regex フォールバック
- [x] 4.5 `--dry-run` モード: SKILL.md は分析結果を表示するが Edit ツールで書込しない。reflect_status も更新しない
- [x] 4.6 `--view` モード: pending corrections の一覧を confidence・タイプ・経過日数付きで表示して終了（claude-reflect の /view-queue 相当）
- [x] 4.7 `--skip-all` モード: 全 pending corrections の reflect_status を "skipped" に一括更新（claude-reflect の /skip-reflect 相当）
- [x] 4.8 `--apply-all` モード: confidence >= N（デフォルト 0.85、`--min-confidence` で変更可）の corrections を確認なしで一括 apply。閾値未満の corrections は対話レビューに進む（スキップではない）
- [x] 4.9 auto-memory 昇格チェック: reflect.py 実行時に auto-memory を走査し、再出現 2回以上 or 14日以上経過で未矛盾の items を昇格候補（`promotion_candidates`）として出力に含める

## 5. discover へのセッションテキスト分析統合

- [x] 5.1 `skills/discover/scripts/discover.py` に `--session-scan` オプションを追加: セッション JSONL のユーザーメッセージテキストを直接分析し、繰り返しパターン（5回以上）をスキル候補として検出。backfill の `parse_transcript()` を利用
- [x] 5.2 `skills/discover/SKILL.md` を更新: `--session-scan` の説明を追加。usage.jsonl ベースと session テキストベースの補完的な関係を明記
- [x] 5.3 セッションテキスト分析のユニットテスト: パターン検出、閾値フィルタ、スコープ判定、既存スキルとの重複排除

## 5a. save_state.py 拡張

- [x] 5a.1 `save_state.py` (PreCompact hook) を拡張: corrections.jsonl のスナップショットを checkpoint.json に含める。コンテキスト圧縮時のデータ消失を防止

## 6. evolve パイプライン統合

- [x] 6.1 `evolve.py` に `count_pending_corrections()` 関数を追加: corrections.jsonl の pending 件数を返す
- [x] 6.2 `evolve.py` に Reflect Phase を追加: Fitness Evolution の後、Report の前。pending 件数と前回 reflect 実行日を結果に含める
- [x] 6.3 `skills/evolve/SKILL.md` に Reflect Step を追加: pending >= 5 or 前回 reflect から 7日超で提案。それ以外は Report に件数表示のみ

## 6a. corrections.jsonl クリーンアップ

- [x] 6a.1 `prune.py` に corrections.jsonl クリーンアップを追加: `applied`/`skipped` で `decay_days` 超過のレコードを削除。`pending` は保持
- [x] 6a.2 クリーンアップのユニットテスト: 超過 applied 削除、超過 skipped 削除、pending 保持、decay_days 未超過レコード保持

## 7. データ移行・クリーンアップ

- [x] 7.1 `scripts/migrate_reflect_queue.py` を作成: learnings-queue.json → corrections.jsonl 変換（冪等、二重追記防止）。重複判定キー = `(timestamp, SHA256(message[:100]))`
- [x] 7.2a `scripts/discover.py` の `load_claude_reflect_data()` パスを修正（既存バグ: `~/.claude/claude-reflect/learnings-queue.jsonl` → `~/.claude/learnings-queue.json`）→ 移行後は corrections.jsonl を直接参照に変更
- [x] 7.2b `skills/discover/scripts/discover.py` の `load_claude_reflect_data()` も同様に修正 → corrections.jsonl を直接参照に変更
- [x] 7.2c `skills/discover/SKILL.md` を更新: learnings-queue.json ではなく corrections.jsonl をデータソースとして参照するよう説明を修正
- [x] 7.3 CHANGELOG.md + plugin.json バージョン更新
- [x] 7.4 README.md 更新: /reflect コマンドの追加、discover --session-scan の説明、claude-reflect からの移行ガイド
- [x] 7.5 claude-reflect アンインストール手順の文書化（`claude plugin uninstall claude-reflect`）
