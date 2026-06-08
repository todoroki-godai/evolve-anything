# Prune フェーズ詳細（Step 7：Custom/Plugin/Global + Merge）

淘汰候補をスキルの出自別に3セクションで表示する（MUST）。
**「各候補を個別調査・分類してから1件ずつ承認（MUST）」「全候補を一括判断してはならない（MUST）」は SKILL.md 側に残してある。**
ここは調査手順・分類表・出力テンプレ・merge のコード。

## Custom Skills（淘汰候補）

カスタムスキルのうち、ゼロ呼び出しのものをアーカイブ候補として処理する。
「ゼロ呼び出し」はアーカイブの必要条件ではない。セットアップ・オンボーディング・デプロイ等のスキルは設計上低頻度が正常であり、SKILL.md を読まずに「オンデマンドスキル」と決めつけてはならない。

**各候補について順番に以下を実施する（MUST）:**

### 1. 調査
- 候補スキルの SKILL.md を Read で全文読み取る
- `git log --oneline --all -- <skill_dir>/` でそのスキルの最終変更日・変更傾向を確認する

### 2. 分類（4種別）

| 種別 | 判定基準 | 推奨 |
|------|----------|------|
| **オンデマンド型** | セットアップ・デプロイ・削除など特定イベント時のみ使う設計 | keep |
| **一時目的完了型** | hotfix・移行・バックフィル等、目的が完了済みで今後不要 | archive |
| **統合済み型** | 他スキルに機能が吸収されており独立して不要 | archive |
| **日常用途・未発火型** | 本来頻繁に使うはずだが使われていない（改善または削除候補） | 要確認 |

### 3. Q&A前にテキスト出力（MUST）

```
---
**N/M: {スキル名}** [作成: {日付} / {経過}日]
説明: {SKILL.md の description}
種別: {4種別のいずれか}
根拠: {SKILL.md・git log から読み取った判断理由を具体的に}
推奨: {keep / archive / 要確認}
---
```

### 4. AskUserQuestion で個別承認（テキスト出力の後に呼ぶ — MUST）
- 候補 1-2件目: `アーカイブ` / `維持` / `後で判断`
- 候補 3件目以降: `アーカイブ` / `維持` / `残り全てスキップ`

承認されたもののみアーカイブ。

**アーカイブを断った候補への対応（再表示抑制）**: ユーザーが「今は保持する」を選択した場合、次のように案内する（MUST）:
> 再度 evolve で表示したくない場合は、スキルディレクトリに `.pin` ファイルを作成してください:
> ```bash
> touch <skills_dir>/<skill_name>/.pin
> ```
> `.pin` があるスキルは以降の淘汰候補から自動除外されます。

## Plugin Skills（レポートのみ）
プラグイン由来で未使用のスキルを表示。アーカイブはせず情報提供のみ。
「未使用。`claude plugin uninstall` を検討？」と案内する。

## Global Skills（既存ロジック維持）
Usage Registry の cross-PJ 使用状況を確認し、既存の `safe_global_check` で処理。

## Merge サブステップ

evolve.py の出力に含まれる `prune.merge_result` を確認する。
prune.py の `merge_duplicates()` は `duplicate_candidates` から統合候補を JSON で出力する（型A パターン: LLM 呼び出しなし）。マージ候補検出は prune に一元化済み。

- `merge_proposals` の各エントリについて:
  - `status: "skipped_pinned"` / `"skipped_plugin"` / `"skipped_suppressed"` / `"skipped_low_similarity"` → スキップ理由を表示
  - `status: "proposed"` → Claude が primary と secondary の SKILL.md を読み込み、統合版を生成してユーザーに提示する（MUST）
    - AskUserQuestion で「承認（統合を適用）」「却下（変更なし）」を選択させる（MUST）
    - 承認された場合: 統合版を primary の SKILL.md に上書きし、secondary を `archive_file()` でアーカイブ
    - 却下された場合: 当該ペアを merge suppression に登録して次回以降の提案を抑制する。以下のコマンドを実行する（MUST）:
      ```bash
      python3 -c "
      from discover import add_merge_suppression
      add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')
      "
      ```
  - `status: "interactive_candidate"` → 対話的統合提案（MUST）:
    - `similarity_score` 降順で最大3件を処理する（1回の evolve あたりの上限）
    - 各ペアについて、Claude が primary と secondary の SKILL.md を読み込み、統合案の概要を提示する
    - AskUserQuestion で「承認（統合を適用）」「却下（次回以降も提案しない）」を選択させる（MUST）
    - 承認された場合: proposed と同じフロー（統合版生成 → primary の SKILL.md に上書き → secondary を `archive_file()` でアーカイブ）を適用する
    - 却下された場合: `add_merge_suppression()` で suppression 登録し、次回以降の再提案を抑制する:
      ```bash
      python3 -c "
      from discover import add_merge_suppression
      add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')
      "
      ```
