## Pre-flight Check

**空のうちは `references/pitfalls.md` を読み込まない。** 読むかどうかは次の決定論的判定で決める
（空テンプレートを毎回読むのは Progressive Disclosure（Context rot 回避）方針に反する）。

```bash
grep -c '^### ' "{{PITFALLS_GATE_PATH}}" 2>/dev/null || echo 0
```

- **0**: 読み込みも Pre-flight もスキップする（記録が1件も無い間はこちら）
- **1以上**: pitfalls.md を読み、Status が Active かつ Pre-flight対応=Yes の項目の回避策を適用してから本タスクを実行する

## Self-Update Rules

| 更新対象 | 判断基準 | 更新方法 |
|----------|---------|---------|
| `references/pitfalls.md` | エラー発生・リトライ・ユーザー訂正・再発時 | **`/evolve-anything:pitfall-curate` 経由**（手で markdown を編集しない） |
| `## Success Patterns` | 特に効果的だったアプローチの発見時（最大2件） | このファイルを直接 Edit |
| Pitfall ステータス | ワークフローへの統合完了時に Graduated へ | pitfall-curate 経由 |

## Failure-triggered Learning

**`references/pitfalls.md` を手で編集しない。** 記録は `/evolve-anything:pitfall-curate` に委譲する
（`skills/pitfall-curate/SKILL.md` が正典。parse / 類似度による dedup / フィールド書込みは
`scripts/pitfall_curate.py` が決定論的に担う）。

**記録対象の範囲（MUST）**: この pitfalls.md は本スキル（{{SKILL_NAME}}）専用に配置される。
記録してよいのは **このスキルの手順そのものに起因し、PJ を変えても再発する失敗**だけ。
次は記録しない — 対象 PJ 固有の事情 / 一時的な環境障害 / 単発の打ち間違い。

**書込みは人間承認を経る（MUST）**: 記録候補が出たらユーザーに1行で提示し、承認を得てから
pitfall-curate を起動する。ユーザー本来の依頼に無断で割り込まない。

| トリガー | アクション | ステータス |
|----------|-----------|-----------|
| エラー発生 | 根本原因カテゴリ付きで記録 | Candidate（初回）/ New（2回目同一原因） |
| リトライ発生 | 何が不足していたか記録 | Candidate |
| ユーザー訂正 | 訂正内容と正しいアプローチを記録 | Active（ゲートスキップ） |
| 既知 pitfall 再発 | Avoidance-count をリセット、Last-seen 更新 | 既存ステータス維持 |

**根本原因カテゴリ**: 記録時に以下のいずれかを付与してください。
- `memory`: コンテキスト消失、前の情報の忘却
- `planning`: 手順の誤り、依存関係の見落とし
- `action`: コマンドミス、パラメータ誤り
- `tool_use`: ツール選択ミス、API仕様の誤解
- `context_loss`: 圧縮による情報消失
- `instruction`: スキル指示への違反（MUST/禁止行の読み飛ばし）

## Pitfall Lifecycle Management

```
Candidate → New → Active → Graduated → Pruned
    ↑                ↑
    └─ 初回エラー     └─ ユーザー訂正（ゲートスキップ）
```

- **Candidate**: 初回エラー。Pre-flight 対象外。同一根本原因が2回目で New に昇格。保存先は `## Candidate Pitfalls` 節
- **New**: 正式 pitfall。Pre-flight 対象外。再発 or ユーザー承認で Active に昇格。保存先は `## Active Pitfalls` 節（Warm 層）
- **Active**: Pre-flight 対象（Pre-flight対応=Yes の場合）。Hot 層は上位5件。保存先は `## Active Pitfalls` 節
- **Graduated**: ワークフローに統合済み。Pre-flight 対象外。保存先は `## Graduated Pitfalls` 節
- **Pruned**: Avoidance-count が **5** に達したら削除候補としてユーザーに提示する（自動削除しない）

**遷移を実行する主体は pitfall-curate**（昇格・Avoidance-count 更新・Pruned 判定を含む）。
このスキル単独でステータスを書き換えない。

## Success Patterns

<!-- 特に効果的だったアプローチを1-2件記録 -->

_まだ記録がありません。成功パターンを発見したら追記してください。_
