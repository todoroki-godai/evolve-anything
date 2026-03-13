## ADDED Requirements

### Requirement: 個別レビューフロー

prune Step 3 は一括方針選択を廃止し、各候補スキルを順番に個別レビューする形式とする（MUST）。

#### Scenario: 候補が1-2件の場合
- **WHEN** prune 候補が1件または2件検出される
- **THEN** 各スキルの分析テキストを出力し、AskUserQuestion で「アーカイブ / 維持 / 後で判断」の3択を表示する

#### Scenario: 候補が3件以上の場合
- **WHEN** prune 候補が3件以上検出される
- **THEN** 1-2件目は「アーカイブ / 維持 / 後で判断」の3択、3件目以降は「アーカイブ / 維持 / 残り全てスキップ」の3択を表示する

#### Scenario: 候補が0件の場合
- **WHEN** prune 候補が0件
- **THEN** 「未使用スキルはありません」と報告し、AskUserQuestion は表示しない

### Requirement: スキルごとの分析テキスト出力

各候補スキルについて AskUserQuestion の前に、SKILL.md を読んだ上での判断分析をテキスト出力する（MUST）。以下のテンプレートに従う（MUST）。

```
---
**N/M: {スキル名}** [{推薦ラベル}]
説明: {description}

判断理由:
- 未使用の背景: {なぜ使われていないかの分析}
- 今後の使用可能性: {汎用性・トリガー数・季節性等}
- 重複/統合: {他スキルとの重複・統合状況}
- 参照価値: {リファレンス・テンプレートとしての価値}
---
```

#### Scenario: 分析テキストの内容
- **WHEN** 候補スキルのレビューを行う
- **THEN** 上記テンプレートに従い、スキル名、description、推薦ラベル、4観点の判断理由を出力する

#### Scenario: description が空の場合
- **WHEN** 候補スキルの description が空文字
- **THEN** SKILL.md 全文を Read で読み取り、1行要約を生成して description に使用する

#### Scenario: SKILL.md の Read に失敗した場合
- **WHEN** 候補スキルの SKILL.md が Read できない（ファイル不在等）
- **THEN** prune.py の JSON 出力に含まれる description と recommendation のみで判断分析を提示する（MUST）。判断理由の各観点は「情報不足」と記載する

### Requirement: ショートカット選択肢

候補が多い場合に全件確認の負担を軽減するショートカットを提供する（MUST）。

#### Scenario: 3件目以降の選択肢
- **WHEN** 3件目以降の候補をレビューする
- **THEN** AskUserQuestion の選択肢は「アーカイブ / 維持 / 残り全てスキップ」の3択とする

#### Scenario: 残り全てスキップ選択時
- **WHEN** ユーザーが「残り全てスキップ」を選択する
- **THEN** 残りの候補を全て維持し、個別レビューを終了する

## REMOVED Requirements

### Requirement: 2段階承認フロー（Stage 2 一括方針選択）
**Reason**: 個別レビューをデフォルトにすることで、一括方針選択ステップが不要になった
**Migration**: Step 3 は直接個別レビューに入る。一括アーカイブが必要な場合はテキスト入力で依頼
