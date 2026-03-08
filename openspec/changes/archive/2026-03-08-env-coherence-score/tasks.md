Related: #21

## 1. coherence.py 骨格 + Coverage

- [x] 1.1 `scripts/rl/fitness/coherence.py` を新規作成。先頭に `THRESHOLDS` 定数 dict を定義。`compute_coherence_score(project_dir)` の骨格と `score_coverage()` を実装（CLAUDE.md / Rules / Skills / Memory / Hooks / Skills セクション の6チェック。`.claude/` 不在時は coverage=0.0）
- [x] 1.2 `score_coverage()` の単体テスト（全レイヤーあり / Hooks 未設定 / CLAUDE.md のみ の3シナリオ）

## 2. Consistency

- [x] 2.1 `score_consistency()` を実装（CLAUDE.md 言及 Skill の実在チェック、Memory パス存在チェック、トリガーワード重複チェック）
- [x] 2.2 `score_consistency()` の単体テスト（Skill 実在 / 不在 / トリガー重複 の3シナリオ）

## 3. Completeness

- [x] 3.1 `score_completeness()` を実装（Skill 行数・必須セクション、Rule 行数制約、CLAUDE.md 行数、ハードコード値検出）。既存 audit / skill_quality / hardcoded_detector を再利用
- [x] 3.2 `score_completeness()` の単体テスト

## 4. Efficiency

- [x] 4.1 `score_efficiency()` を実装（意味的重複 Skill、near-limit、未使用 Skill、孤立 Rule の4チェック）。既存 audit の duplicate/prune チェックを再利用。usage.jsonl 不在時は未使用 Skill チェックを skip し残りで按分
- [x] 4.2 `score_efficiency()` の単体テスト

## 5. 統合 + audit 連携

- [x] 5.1 `compute_coherence_score()` の統合実装（4軸の重み付き平均 + details dict）
- [x] 5.2 audit SKILL.md に `--coherence-score` オプションを追加し、レポートに "## Environment Coherence Score" セクションを表示
- [x] 5.3 統合テスト（compute_coherence_score の戻り値検証 + 重み計算の数値テスト）
