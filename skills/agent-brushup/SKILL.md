---
name: agent-brushup
effort: medium
description: |
  エージェント定義（~/.claude/agents/）の品質診断・改善提案・新規作成・削除候補提示。
  agency-agents のベストプラクティスカタログ参照 + upstream 更新チェック。
  Trigger: agent-brushup, エージェント改善, agent品質, agent診断, エージェント整理
---

# /rl-anything:agent-brushup — エージェント品質管理

エージェント定義を診断・改善・整理する。

## Usage

```
/rl-anything:agent-brushup [subcommand] [args]
```

サブコマンド:
- `diagnose` (デフォルト): 全エージェント走査 → 品質レポート + upstream チェック
- `improve <agent-name>`: 特定エージェントの改善提案
- `create <role>`: 新規エージェント scaffold
- `prune`: 未使用/低品質エージェントの削除候補

## 実行手順

### Step 1: エージェント走査

```bash
rl-usage-log "agent-brushup"
python3 <PLUGIN_DIR>/scripts/lib/agent_quality.py scan "$(pwd)" 2>&1
```

上記が CLI として使えない場合は、Python で直接:

```python
from agent_quality import scan_agents, check_quality, check_upstream
agents = scan_agents(project_root=Path("$(pwd)"))
```

### Step 2: サブコマンド分岐

#### diagnose（デフォルト）

1. `scan_agents()` で全エージェント走査（global + project）
2. 各エージェントに `check_quality()` 実行
3. レポート出力:
   - エージェント一覧（名前、スコープ、行数、スコア）
   - issue のあるエージェント（アンチパターン別）
   - ベストプラクティス提案
4. `check_upstream()` で agency-agents 更新チェック
   - 更新あり → 新着コミットのサマリを表示
   - 初回 → ハッシュ保存のみ
   - エラー → "upstream チェックをスキップしました" と表示

レポートフォーマット:

```
## Agent Brushup Report

### エージェント一覧
| Name | Scope | Lines | Score | Issues |
|------|-------|-------|-------|--------|
| ... | global | 150 | 0.85 | 1 |

### 検出された問題
- **[agent-name]** missing_frontmatter: YAML frontmatter が欠落
- **[agent-name]** knowledge_hardcoding: バージョン番号・具体パス・固有名詞のハードコード（陳腐化リスク）

### ベストプラクティス提案
- **[agent-name]** に success_metrics セクション追加を推奨
- **[agent-name]** に jit_file_references: JIT識別子戦略（回答前にファイル動的確認）の鉄則がない

### Upstream (agency-agents)
- ステータス: 更新なし / N件の更新あり / チェックスキップ
```

#### improve <agent-name>

1. 指定エージェントを走査・品質チェック
2. 検出された issue と suggestions をもとに、LLM で改善案を生成
3. 改善案を diff 形式で提示
4. ユーザー確認後に適用

**改善案生成のプロンプト要素:**
- 現在のエージェント定義全文
- 検出された issue リスト
- agency-agents のベストプラクティスカタログ（BEST_PRACTICES 定数）
- 「構造は変えずに、欠落セクションの追加と曖昧表現の具体化のみ行う」指示

#### create <role>

1. agency-agents のベストプラクティスに準拠した scaffold を生成
2. 必須セクション: Identity, Core Mission, Critical Rules, Deliverables, Communication Style
3. **Self-Evolution Protocol セクションを必ず含める**（後述テンプレート参照）
4. ユーザーに配置先を確認（global / project）
5. frontmatter (name, description, memory: user) を含めて生成
6. スコープが決まったら Self-Evolution Protocol の定義ファイルパスを実際のパスに置換:
   - global → `~/.claude/agents/<agent-name>.md`
   - project → `<project-root>/.claude/agents/<agent-name>.md`（絶対パス）

**Self-Evolution Protocol テンプレート:**

```markdown
## Self-Evolution Protocol

**トリガー条件（セッション末尾で確認）:**
以下のいずれかを満たしたらプロトコルを起動:
- 今セッションでユーザーから修正フィードバックを受けた
- MEMORY.md の完了タスクが3件以上になった
- 「効かなかった」知見が新たに記録された

**手順:**
1. `~/.claude/agent-memory/<agent-name>/MEMORY.md` を Read して今セッションの学びを整理
   （memory は常に global に保存される）
2. 自分の定義ファイルを Read:
   - global agent: `~/.claude/agents/<agent-name>.md`
   - project agent: `<project-root>/.claude/agents/<agent-name>.md`
3. knowledge_hardcoding / jit_file_references / description 具体性の3軸で自己採点
4. スコア改善が見込める箇所を特定してパッチ案を作成
5. **Regression gate**: パッチ後スコア ≥ 現在スコアであることを確認
6. ユーザーに diff を提示して承認を求める（自動適用しない）
7. 承認後 Edit で自分の定義ファイルを更新
```

#### prune

1. 全エージェント走査
2. テレメトリ（subagents.jsonl の agent_name フィールド）で使用状況確認
3. 未使用（テレメトリに記録なし）かつ低スコアのエージェントを候補リスト
4. テレメトリなし環境では mtime ベースのフォールバック（90日以上未更新）
5. 削除はユーザー確認必須

### Step 3: 状態保存

diagnose 実行時に `DATA_DIR/agent-brushup-state.json` に upstream ハッシュを保存。

## 注意事項

- エージェント定義の直接編集は **常にユーザー確認必須**
- improve / create の LLM 出力はそのまま適用しない。必ず diff を見せてから
- prune の削除提案は conservative（確信度が高いもののみ）
