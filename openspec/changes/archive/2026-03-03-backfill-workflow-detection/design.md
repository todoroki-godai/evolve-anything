## Context

backfill の `parse_transcript()` は現在 `Skill→Agent` パターンのみをワークフローとして検出する。5プロジェクト 915 セッションの実データ調査で以下が判明:

| プロジェクト | Agent 呼び出し | TeamCreate セッション | 検出 workflow | 捕捉率 |
|------------|----------:|----------:|----------:|------:|
| rl-anything | 90 | 2 | 4 | 4.4% |
| docs-platform | 41 | 12 | 3 | 7.3% |
| atlas-breeaders | 49 | 7 | 1 | 2.0% |
| ooishi-kun | 7 | 0 | 0 | 0% |
| figma-to-code | 1 | 0 | 0 | 0% |

Agent team パターン（TeamCreate→Agent×N→TeamDelete）が主要なワークフロー形態だが、全く検出されていない。

## Goals / Non-Goals

**Goals:**
- TeamCreate→Agent パターンをワークフローとして検出し workflows.jsonl に記録する
- Skill なしの連続 Agent 起動（agent-burst）をワークフローとしてグルーピングする
- 既存の Skill→Agent ワークフロー検出との互換性を維持する
- workflow_type フィールドでワークフロータイプを識別可能にする

**Non-Goals:**
- リアルタイム hooks のワークフロー検出変更（hooks は tool_use ベースで別の仕組み）
- Agent の実行結果の品質評価（Phase C の scope）
- SendMessage の内容分析（メッセージ内容はプライバシー上記録しない）

## Decisions

### Decision 1: ワークフロータイプの分類

| タイプ | 検出パターン | 境界 |
|--------|------------|------|
| `skill-driven` | Skill → Agent(s) | 次の Skill またはトランスクリプト終了 |
| `team-driven` | TeamCreate → Agent(s) | TeamDelete またはトランスクリプト終了 |
| `agent-burst` | Agent → Agent（Skill/Team なし、間隔 5 分以内） | Skill/TeamCreate による別ワークフローへの取込み、または 5 分以上の gap |

**代替案: TeamCreate のみ追加し agent-burst は対象外**
→ 不採用。rl-anything では Team なしの連続 Agent が 90 回あり、重要なデータソース。

### Decision 2: team-driven ワークフローの境界判定

TeamCreate の `team_name` を追跡し、対応する TeamDelete（または トランスクリプト終了）までを1ワークフローとする。

Team 内の Agent は `team_name` パラメータを持つが、持たない Agent（ad-hoc）が混在する場合がある。TeamCreate〜TeamDelete の区間内にある全 Agent を team workflow に含める。

**workflow レコードのフィールド:**
```json
{
  "workflow_id": "wf-xxx",
  "workflow_type": "team-driven",
  "skill_name": null,
  "team_name": "my-team",
  "session_id": "...",
  "steps": [...],
  "step_count": N
}
```

**agent-burst ワークフローレコードのフィールド:**
```json
{
  "workflow_id": "wf-xxx",
  "workflow_type": "agent-burst",
  "skill_name": null,
  "team_name": null,
  "session_id": "...",
  "steps": [...],
  "step_count": N
}
```

### Decision 3: agent-burst の閾値

連続する Agent 呼び出しの timestamp 間隔が **5 分以内** なら同一ワークフローとみなす。5 分以上空いた場合は別ワークフローとして分割する。

burst の最小 Agent 数は **2**（1 つの Agent は ad-hoc 扱い）。

**代替案: 10 分間隔**
→ 不採用。10 分だと別タスクの Agent まで含まれるリスクが高い。5 分はセッション内の連続操作としての妥当な上限。

### Decision 4: 既存 skill-driven との共存

parse_transcript() の1パスループ内で3タイプを同時追跡する。優先順位:

1. TeamCreate 検出 → team-driven モード開始
2. Skill 検出 → skill-driven モード開始（team-driven 中は team 内ステップとして記録）
3. Team/Skill 外の Agent → agent-burst 候補に蓄積
4. TeamDelete → team-driven モード終了
5. 次の Skill → 前の skill-driven を確定

既存の `workflow_type` 未設定レコードは `skill-driven` として後方互換。

### Decision 5: workflow_type フィールドの追加

`workflows.jsonl` のレコードに `workflow_type` フィールドを追加する。既存レコードには `workflow_type` がないため、読み取り時に未設定なら `"skill-driven"` として扱う。

## Risks / Trade-offs

- **[Risk] agent-burst の false positive** — 無関係な Agent が偶然 5 分以内に起動した場合、同一ワークフローに含まれる → burst の最小 Agent 数を 2 にして単発を除外。Phase C で精度を評価し閾値調整可能
- **[Risk] TeamCreate なしの Team パターン** — Claude Code のバージョンによって Team 関連ツールの名前が異なる可能性 → ToolSearch で既知のツール名を列挙し、未知のパターンは無視
- **[Trade-off] 5 分閾値のハードコード** — 設定可能にすると複雑化する。定数としてハードコードし、必要なら後で変更
- **[Trade-off] 過去データの再処理** — `--force` で再バックフィルすれば新パターンで再検出可能。自動マイグレーションは不要
