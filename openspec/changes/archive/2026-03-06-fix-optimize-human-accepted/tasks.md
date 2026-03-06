## 1. SKILL.md の修正

- [x] 1.1 `skills/genetic-prompt-optimizer/SKILL.md` の frontmatter `allowed-tools` に `AskUserQuestion` を追加
- [x] 1.2 Step 3 を拡張: 結果報告後に AskUserQuestion で accept/reject を確認するワークフローを追加
- [x] 1.3 reject 時に AskUserQuestion（open-ended）で却下理由を確認し `--reject --reason "..."` で記録するフローを追加

## 2. 検証

- [x] 2.1 SKILL.md の手順が既存の `optimize.py --accept` / `--reject --reason` CLI と整合していることを確認
- [x] 2.2 `python3 -m pytest skills/genetic-prompt-optimizer/ scripts/rl/tests/ -v` でテストが通ることを確認
- [x] 2.3 `--accept` 実行後に `history.jsonl` の最終エントリが `human_accepted: true` になることを手動確認
- [x] 2.4 `--reject --reason "test"` 実行後に `rejection_reason: "test"` が記録されることを手動確認
