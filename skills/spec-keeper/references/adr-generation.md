# spec-keeper ADR 自動生成（3層フィルタ）

SKILL.md「`/spec-keeper init` — Step 4」から参照される。

Claude Code の Glob→Grep→Read 階層戦略と同じ思想で、
コストの低い操作から段階的に絞り込む。Read は最後の最後だけ。

## Layer 1: grep スキャン（数秒、Read なし、LLM 判定なし）

Bash grep のみで「設計判断を含む design.md」を高速抽出する。

```bash
evolve-usage-log "spec-keeper"
# 設計判断セクションを含む design.md を抽出（数秒で完了）
find openspec/changes/archive -name "design.md" \
  -exec grep -l "^## Decision\|^# Decision\|^## Risks\|Trade-off\|Approach" {} \;
```

gstack の design doc も同様にスキャン:
```bash
grep -l "Approach\|Decision\|Trade-off" ~/.gstack/projects/*/\*-design-*.md 2>/dev/null
```

git log からも重要な設計変更コミットを抽出:
```bash
git log --oneline --all | grep -i "refactor\|migrate\|architect\|redesign\|breaking"
```

## Layer 2: ヘッダ構造分類（数秒、Read なし、grep 出力のみ）

Layer 1 の候補に対して、セクションヘッダだけを抽出してアーキテクチャ重要度を自動分類する。

```bash
# 各候補の design.md からセクションヘッダのみ抽出（本文は読まない）
grep "^#" path/to/design.md
```

ディレクトリ名 + セクションヘッダから 3カテゴリに自動分類:

| カテゴリ | 判定基準 | 例 |
|----------|----------|-----|
| **High** (アーキテクチャ) | `world`, `core`, `refactor`, `migrate`, `system`, `foundation`, `phase1` がディレクトリ名に含む。またはヘッダに `Architecture`, `Data Model`, `Store`, `State Management` がある | `add-world-core-shell-phase1` |
| **Medium** (機能設計) | `Decisions` セクションがあるが High に該当しない | `implement-lie-system-foundation` |
| **Low** (UI/修正) | `fix-`, `enhance-`, `polish-`, `cleanup-`, `improve-` で始まる。またはヘッダが `Visual`, `Layout`, `Style` のみ | `fix-toast-stack-management` |

分類結果をユーザーに提示:
```
ADR 候補スキャン完了（{N} 秒）:
  High（アーキテクチャ）: 8 件
  Medium（機能設計）: 15 件
  Low（UI/修正）: 40 件 ← 通常は除外

High の候補:
  1. [x] add-world-core-shell-phase1 — ## Decisions: ストア実装, データ責務分割, UI受け渡し
  2. [x] implement-lie-system-foundation — ## Decisions: 嘘生成アルゴリズム, 信頼度モデル
  ...

High + Medium は全選択します。除外したいものがあれば番号で指定してください。
Low から追加したいものがあれば番号で指定してください。
```

## Layer 3: 選択された候補のみ Read → 並行 ADR 生成

ユーザーが選んだ候補（5-15件）に対してのみ Read を実行する。

5件以上なら Agent ツールで並行処理:
- 各 Agent が design.md + proposal.md を Read
- ADR ドラフトを生成して `docs/decisions/{NNN}-{slug}.md` に Write

各 ADR は以下から構成:
- **Context**: proposal.md の Problem Statement（あれば）
- **Decision**: design.md の Decisions セクション
- **Alternatives**: design.md の Approaches Considered / Risks / Trade-offs
- **Consequences**: 後続の archive でその判断がどう影響したか（あれば）

gstack の design doc (`~/.gstack/projects/`) に Approaches Considered がある場合も同様に抽出。

## パフォーマンス目安

- Layer 1+2: 150件 → 数秒（grep のみ）
- Layer 3: 10件選択 → 並行 Agent で 30-60秒
- 合計: 1分以内（旧方式: 5分以上 or タイムアウト）
