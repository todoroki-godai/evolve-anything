## Context

現行の genetic-prompt-optimizer は `GeneticOptimizer` クラスに世代ループ（`run()` / `run_sectioned()`）、Individual クラス、mutate/crossover/evaluate パイプライン、補助モジュール6つ（strategy_router, granularity, bandit_selector, early_stopping, model_cascade, parallel）を持つ。LLM コール数は 6〜15+ / 1最適化。

corrections.jsonl は `~/.claude/rl-anything/corrections.jsonl` に hook が自動記録し、reflect スキルが読み込む。各レコードのフィールド: `message`（修正メッセージ全文）、`last_skill`（直近使用スキル）、`correction_type`（修正パターン分類）、`extracted_learning`（抽出された学習、reflect 後に付与）、`confidence`（検出信頼度 0.0-1.0）、`reflect_status`（`"pending"` / `"applied"` / `"skipped"`）。

## Goals / Non-Goals

**Goals:**
- optimize の LLM コールを 1〜2 回に削減（現行 6〜15+ → 1〜2）
- corrections がある場合はエラー情報を直接活用して精度の高いパッチを生成
- corrections がない場合も usage 統計・audit 結果をコンテキストに含めた 1パス改善
- 既存の accept/reject フロー・history.jsonl 記録・backup/restore を維持
- `/optimize` コマンドの基本 UX を維持（ユーザーから見た変更を最小化）

**Non-Goals:**
- fitness 関数の完全廃止（品質ゲート用に `_regression_gate()` は残す）
- reflect スキルの変更（corrections.jsonl のフォーマットは変えない）
- rl-loop-orchestrator のスクリプト変更（optimize を呼ぶ側はそのまま）。ただし SKILL.md 説明文は更新する

## Decisions

### D1: 2モード統一パイプライン

corrections 有無で分岐する 2 モードを `DirectPatchOptimizer` として統合。

```
DirectPatchOptimizer.run()
  ├─ _collect_context()        # corrections, usage stats, audit issues
  ├─ _build_patch_prompt()     # モードに応じたプロンプト構築
  │   ├─ corrections あり → error_guided モード
  │   └─ corrections なし → llm_improve モード
  ├─ _call_llm()               # claude -p 1回
  │   └─ timeout/error → 元スキル維持 + エラー表示
  ├─ _regression_gate()        # 既存の構造チェック（行数、禁止パターン）
  └─ result                    # accept/reject は SKILL.md 側で処理
```

**代替案**: corrections モードのみ実装して、なしの場合は既存 GA を残す → corrections なしのケースでも GA のコスト問題は解消されないため却下。

### D2: コンテキスト収集

LLM に渡すコンテキストを最大化して 1パスの質を上げる。

| ソース | パス | 用途 |
|--------|------|------|
| corrections.jsonl | `~/.claude/rl-anything/corrections.jsonl` | エラー分類の主入力 |
| workflow_stats.json | `~/.claude/rl-anything/workflow_stats.json` | 使用パターンのヒント（既存の `_load_workflow_hints()` 流用） |
| audit collect_issues() | スクリプト呼び出し | ハードコード値、行数超過等の構造的問題 |
| pitfalls.md | `references/pitfalls.md` | 過去の失敗パターン（既存） |

corrections の取得件数上限: `MAX_CORRECTIONS_PER_PATCH = 10`（名前付き定数）。

### D3: 削除対象モジュール

以下は世代ループ専用のため削除:

- `strategy_router.py` → 新ルーティングは `_build_patch_prompt()` 内で判定
- `granularity.py` → セクション分割は不要（1パスで全体を改善）
- `bandit_selector.py` → Thompson Sampling は世代ループ前提
- `early_stopping.py` → 世代ループがないため不要
- `model_cascade.py` → 1回の LLM コールでカスケード不要
- `parallel.py` → references/ 並行最適化は当面スコープ外

`Individual` クラスも簡素化し、`content` + `strategy` のみ保持する。

### D4: history.jsonl フォーマット拡張

既存フィールドに `strategy` を追加:

```json
{
  "run_id": "20260307_120000",
  "target": ".claude/skills/my-skill/SKILL.md",
  "strategy": "error_guided",
  "corrections_used": 3,
  "result": { "content": "...", "content_length": 150 },
  "human_accepted": null
}
```

`corrections_used` は `llm_improve` モード時は `0` を記録する。

### D5: SKILL.md オプション整理

廃止: `--generations`, `--population`, `--budget`, `--cascade`, `--parallel`
維持: `--dry-run`, `--restore`, `--fitness`（パッチ後の品質スコアを参考表示。accept/reject 判断の補助。regression_gate とは独立）
新規: `--mode error_guided|llm_improve|auto`（デフォルト auto = corrections 有無で自動判定）

## Risks / Trade-offs

- [LLM 1パスの質が低い場合] → `_regression_gate()` で構造的品質ガード + 人間の accept/reject で最終判断。GA 時代も改善率は低かった（bench 結果）ため実質リスク増なし
- [corrections が大量の場合のプロンプト肥大] → 直近 N 件（例: 10件）に制限し、関連度でソート
- [削除モジュールのテストが無駄になる] → sunk cost。bench で効果が証明されなかったコードを維持するコストのほうが高い
- [rl-loop-orchestrator との互換性] → optimize.py の CLI インターフェース（`--target`, `--accept`, `--reject`）は維持するため影響なし
