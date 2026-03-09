Related: #21

## 1. trigger_engine.py に bloat トリガーを追加

- [x] 1.1 `DEFAULT_TRIGGER_CONFIG` に `bloat` トリガー設定を追加（`enabled` のみ。閾値は `bloat_control.BLOAT_THRESHOLDS` が single source of truth）
- [x] 1.2 `evaluate_session_end()` に bloat 条件を追加: keyword-only パラメータ `evaluate_session_end(state=None, *, project_dir=None)` で project_dir を受け取り、`bloat_check()` を呼び出す。`project_dir=None` の場合は bloat 評価をスキップ
- [x] 1.3 bloat トリガーのメッセージ生成: 種別と具体的な数値を含む日本語メッセージ
- [x] 1.4 bloat_check() の import と例外ハンドリング: lazy import（`evaluate_bloat()` 内で `try: from scripts.bloat_control import bloat_check / except ImportError: return None`）。呼び出し時の例外もキャッチしてサイレント失敗、他トリガー評価を続行

## 2. session_summary.py の統合

- [x] 2.1 `session_summary.py` の trigger_engine 呼び出し箇所に project_dir を渡す

## 3. テスト

- [x] 3.1 `scripts/lib/tests/test_trigger_engine.py` に bloat トリガーのテストを追加: 各 bloat 種別の閾値超過/以内、複数種別同時検出、エラーハンドリング
- [x] 3.2 bloat トリガーのクールダウンテスト
- [x] 3.3 bloat + session_count 複合トリガーのテスト
- [x] 3.4 bloat trigger disabled テスト
