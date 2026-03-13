## Why

prune の人間承認フローが「一括 → 個別選択」の2段階になっており、最初の一括選択肢が冗長。ユーザーはスキルごとに「なぜ削除すべきか/すべきでないか」の判断根拠を見た上で意思決定したいが、現在の推薦ラベル（archive推奨/keep推奨/要確認）だけでは情報が不足している。

## What Changes

- Step 3 の2段階承認フロー（一括方針選択 → 個別選択）を廃止し、**最初から個別レビュー**に変更
- 各スキルについて SKILL.md を読んだ上での**削除判断の分析・理由**をテキスト出力してから AskUserQuestion で聞く形に
- 候補が多い場合の**ショートカット選択肢**（「残り全てアーカイブ」「残り全てスキップ」）を追加

## Capabilities

### New Capabilities
- `individual-review-flow`: prune Step 3 の個別レビューフロー。スキルごとの分析提示 + 判断確認 + ショートカット

### Modified Capabilities

## Impact

- `skills/prune/SKILL.md` — Step 3 の全面書き換え
- Python コード（prune.py）の変更は不要（候補検出ロジックはそのまま、UX はスキル定義側で制御）
