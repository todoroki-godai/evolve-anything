## 1. BUILTIN_AGENT_NAMES 共通モジュール

- [x] 1.1 `scripts/lib/agent_classifier.py` を新設し `BUILTIN_AGENT_NAMES = {"Explore", "Plan", "general-purpose"}` と `classify_agent_type(agent_name)` を定義
- [x] 1.2 `audit.py` の `_BUILTIN_TOOLS` を `BUILTIN_AGENT_NAMES` から派生する形にリファクタ: `_BUILTIN_TOOLS = {f"Agent:{n}" for n in BUILTIN_AGENT_NAMES} | {"commit"}`
- [x] 1.3 `classify_agent_type` のユニットテスト追加（組み込み/カスタムglobal/カスタムproject/両方存在/未知のフォールバック/ディレクトリ不在/I/Oエラー）

## 2. detect_behavior_patterns 修正

- [x] 2.1 `detect_behavior_patterns()` 内で Agent:XX パターンの処理順序を実装: (1) `_is_plugin()` でプラグイン判定 → (2) `classify_agent_type()` で組み込み/カスタム分類
- [x] 2.2 組み込み Agent は `builtin_agent_counter` に分離、カスタム Agent はメインランキングに残し `suggestion: "skill_candidate"` を維持
- [x] 2.3 組み込み Agent を `agent_usage_summary`（`type: "agent_usage_summary"`, `suggestion: "info_only"`, `agent_breakdown: {}`）として patterns 末尾に追加。サブカテゴリ情報を `agent_breakdown` に含める
- [x] 2.4 カスタム Agent の pattern dict に `agent_type` フィールドを付与（`determine_scope()` 連携用）

## 3. スコープ判定拡張

- [x] 3.1 `determine_scope()` で pattern の `agent_type` フィールドを参照し、カスタム Agent のスコープを判定（`custom_global` → `global`, `custom_project` → `project`）

## 4. テスト

- [x] 4.1 `detect_behavior_patterns()` の統合テスト: 組み込み Agent が `agent_usage_summary` に、カスタム Agent がメインランキングに含まれることを検証
- [x] 4.2 `determine_scope()` のカスタム Agent スコープ判定テスト
- [x] 4.3 `audit.py` の `_BUILTIN_TOOLS` リファクタ後の既存テストパス確認
- [x] 4.4 全テスト通過確認（`python3 -m pytest skills/discover/scripts/tests/ skills/audit/scripts/tests/ -v`）
