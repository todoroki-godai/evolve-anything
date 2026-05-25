---
date: 2026-05-25
status: accepted
---
# memory frontmatter v2 スキーマ

## Context

auto_memory_runner（Stop hook から非同期起動）が生成する memory ファイルと、
既存の手動 memory ファイルの間でフロントマタースキーマを統一する必要がある。
また、MEMORY.md から詳細ファイルへの参照（L2 オンデマンド層）をサポートするため、
`detail_file` フィールドを追加する（Issue #204）。

## Decision

memory frontmatter v2 スキーマを以下のように定義する:

```yaml
---
name: <kebab-case-slug>           # 必須: エントリの識別子
description: <one-line summary>   # 必須: 1行の要約
metadata:
  type: user | feedback | project | reference  # 必須: エントリの種別
importance: high | medium | low   # 推奨: auto_memory_runner は medium を付与
detail_file: <relative path>      # オプション: L2 オンデマンド層用の詳細ファイルパス
---
```

### フィールド詳細

- **name**: kebab-case のスラグ。MEMORY.md の index からの参照キーになる。
- **description**: 1行の要約。MEMORY.md の index 行の `— <summary>` 部分に使われる。
- **metadata.type**: エントリの種別。
  - `user`: ユーザー固有の設定・好み
  - `feedback`: ユーザーからの修正フィードバック（auto_memory_runner が使用）
  - `project`: プロジェクト固有の事実・設計判断
  - `reference`: 外部参照・ドキュメントリンク
- **importance**: エントリの重要度。未指定時は `medium` 扱い（audit がデフォルト補完）。
- **detail_file**: MEMORY.md の index には短い要約のみ記録し、詳細を別ファイルに分離する場合のパス。
  audit.memory が broken link を検出する。

## Consequences

- `scripts/lib/audit/memory.py` は `importance` 未指定エントリを `medium` 扱いにする
- `scripts/lib/audit/memory.py` は `detail_file` が指定されている場合、ファイルの実存チェックを行う（broken link 検出）
- auto_memory_runner は常に `importance: medium` と `metadata.type: feedback` を LLM 出力に付与することを期待する
- v1 フォーマット（frontmatter なし）との後方互換性は維持する（audit は frontmatter なしをエラーにしない）
