## Context

discover の `detect_behavior_patterns()` は usage.jsonl から行動パターンを検出し、スキル候補を提案する。現在 `Agent:XX` パターンは以下の扱い:

- プラグイン Agent（`Agent:openspec-uiux-reviewer` 等）→ `plugin_summary` に分離済み
- 組み込み Agent（`Agent:Explore` 等）→ ad-hoc としてメインランキングに混在（問題）
- カスタム Agent（`.claude/agents/` 定義）→ 同上（本来はスキル候補として有効）

## Goals / Non-Goals

**Goals:**
- 組み込み Agent をメインランキングから除外し `agent_usage_summary` に分離
- カスタム Agent を正しく識別し、メインランキングに残す
- カスタム Agent の global/project スコープを正確に判定

**Non-Goals:**
- プラグイン Agent の判定ロジック変更（現状の `classify_usage_skill()` を維持）
- Agent の利用データ構造変更
- SKILL.md の表示フォーマット大幅変更

## Decisions

### 1. BUILTIN_AGENT_NAMES の共通定義

**選択**: `scripts/lib/agent_classifier.py` に `BUILTIN_AGENT_NAMES = {"Explore", "Plan", "general-purpose"}` を定義する。

**概念分離**:
- `BUILTIN_AGENT_NAMES`: Agent 名のみのセット（`scripts/lib/agent_classifier.py`）
- `_BUILTIN_TOOLS`（`audit.py` 既存）: `Agent:` プレフィックス付き + `commit` 等の非 Agent ツールを含むセット

`audit.py` の `_BUILTIN_TOOLS` は `BUILTIN_AGENT_NAMES` から派生して生成する:
```python
from scripts.lib.agent_classifier import BUILTIN_AGENT_NAMES
_BUILTIN_TOOLS = {f"Agent:{name}" for name in BUILTIN_AGENT_NAMES} | {"commit"}
```

**理由**: DRY 原則。組み込み Agent リストの変更が一箇所で完結する。

### 2. 組み込み Agent の判定: 既知名リスト + カスタム Agent ディレクトリ走査の併用

**選択**: `BUILTIN_AGENT_NAMES` セットによるハードコード + `~/.claude/agents/` と `.claude/agents/` の走査で「カスタムではない Agent:XX = 組み込み」と判定。

**理由**: 既知名リストだけでは Claude Code の新 Agent タイプ追加に追従できない。カスタム Agent ディレクトリ走査を併用することで、リストに無い未知の Agent も「カスタムに存在しなければ組み込み扱い」にできる。

**代替案**:
- 既知名リストのみ → シンプルだが新 Agent タイプへの追従が必要
- ディレクトリ走査のみ → カスタム Agent が定義されていない環境で判定不能

### 3. classify_agent_type の配置場所

**選択**: `scripts/lib/agent_classifier.py` に配置。

**理由**: `hooks/common.py` は hooks 固有のユーティリティ（`PROMPT_CATEGORIES` 等）が集中しており、Agent 分類は hooks に限定されない横断関心事。`scripts/lib/` に配置することで discover, audit, 将来の他スキルから再利用可能。

### 4. 処理順序: プラグインフィルタが先行

**前提条件**: `detect_behavior_patterns()` 内で Agent:XX パターンの処理順序は以下とする:
1. `_is_plugin()` / `classify_usage_skill()` でプラグイン Agent を判定 → `plugin_summary` に分離
2. プラグインでない Agent:XX に対して `classify_agent_type()` を適用
3. `"builtin"` → `builtin_agent_counter` に分離、`"custom_*"` → メインランキングに残留

**理由**: プラグイン判定は既存ロジックであり、先行させることで classify_agent_type の対象を絞り込める。

### 5. determine_scope() への classify_agent_type 結果の受け渡し

**選択**: `detect_behavior_patterns()` 内で Agent:XX パターンを処理する際、`classify_agent_type()` の結果を pattern dict に `agent_type` フィールドとして付与する。`determine_scope()` はこのフィールドを参照してスコープを判定する。

```python
# detect_behavior_patterns() 内
agent_type = classify_agent_type(agent_name)
if agent_type == "builtin":
    builtin_agent_counter[agent_key] += count
else:
    pattern["agent_type"] = agent_type  # "custom_global" or "custom_project"
    # ... メインランキングに追加

# determine_scope() 内
def determine_scope(pattern: dict) -> str:
    if "agent_type" in pattern:
        return "global" if pattern["agent_type"] == "custom_global" else "project"
    # ... 既存のファイルパスベース判定
```

**理由**: pattern dict を経由することで関数シグネチャの変更を最小化し、既存の `determine_scope()` との互換性を維持。

### 6. カスタム Agent のスコープ判定

**選択**: `~/.claude/agents/<name>.md` に存在 → global、`.claude/agents/<name>.md`（プロジェクトルート）に存在 → project。両方に存在する場合は project 優先。

**理由**: スキルの global/project 判定と同じ原則（プロジェクト固有のものが優先）。

### 7. `agent_usage_summary` の出力位置と構造

**選択**: `plugin_summary` と同様に patterns リストの末尾に `type: "agent_usage_summary"` として追加。

**agent_breakdown の内部スキーマ**:
```json
{
  "Agent:Explore": {
    "count": 76,
    "subcategories": [
      {"category": "spec-review", "count": 27},
      {"category": "debug", "count": 24}
    ]
  },
  "Agent:Plan": {
    "count": 32,
    "subcategories": [
      {"category": "architecture", "count": 15}
    ]
  }
}
```

**理由**: 既存の `plugin_summary` パターンと統一した構造にすることで、SKILL.md の表示ロジックへの影響を最小化。

## Risks / Trade-offs

- [リスク] `BUILTIN_AGENT_NAMES` リストのメンテナンス → Claude Code のリリースノートで新 Agent 追加時に更新。ただしカスタム Agent 走査があるため、未更新でも致命的ではない（未知の Agent はカスタムに無ければ組み込み扱い）
- [リスク] `.claude/agents/` ディレクトリが存在しない環境 → 空リストとして扱い、全 Agent:XX を組み込みリスト or 組み込み扱いにフォールバック。I/O エラー時は WARNING ログ出力しスキップ
- [トレードオフ] ディレクトリ走査のI/Oコスト → discover は低頻度実行のためパフォーマンス影響は無視可能
