---
name: release-notes-review
description: |
  Claude Code のリリースノートを分析し、現在のプロジェクト環境（CLAUDE.md, skills, hooks, agents）と
  比較して適用可能な新機能・改善点を優先度別に報告する。高優先度の項目は OpenSpec change 提案まで一貫して行う。
  Trigger: release-notes, リリースノート確認, CC更新確認, バージョンアップ対応, release notes review,
  新機能チェック, Claude Code アップデート確認
  Use this skill whenever the user mentions Claude Code updates, version upgrades, release notes,
  or wants to check if their project can benefit from new Claude Code features. Also trigger when
  the user asks "何か新しい機能ある？" or "CC更新で使えるものは？" in the context of Claude Code.
---

# /rl-anything:release-notes-review — リリースノート分析 & 適用提案

Claude Code のリリースノートを取得し、現在のプロジェクト環境と突合して、
適用可能な改善点を優先度付きレポートとして出力する。

## Why this matters

Claude Code は頻繁にアップデートされ、plugin/skill/hook/agent 向けの新機能が次々と追加される。
しかし、リリースノートは膨大で、自分のプロジェクトに関係する項目を手動で拾うのは非現実的。
このスキルはその突合作業を自動化し、「何を適用すべきか」を構造化して提示する。

## Usage

```
/rl-anything:release-notes-review              # フル分析
/rl-anything:release-notes-review --report-only # レポートのみ（OpenSpec 提案なし）
```

## 実行手順

### Step 1: リリースノートの取得

`/release-notes` コマンドの出力を取得する。出力が既に会話コンテキストにある場合はそれを使用する。
ない場合は Skill tool で `/release-notes` を呼び出してテキストを取得する。

### Step 2: プロジェクト環境のスナップショット

現在の環境を把握するために以下を読み取る:

1. **CLAUDE.md** — プロジェクト構成、スキル一覧、コンポーネント情報
2. **スキル frontmatter** — 全 `skills/*/SKILL.md` の frontmatter（name, description, allowed-tools, context, hooks 等）
3. **フック定義** — `hooks/hooks.json`（イベント種別、matcher、スクリプトパス）
4. **エージェント定義** — 以下の両方を確認する:
   - `.claude/agents/*.md`（PJ 標準の配置先）
   - `agents/*.md`（プラグインルート直下、rl-anything の rl-scorer 等はここにある）
5. **plugin.json** — `.claude-plugin/plugin.json` のプラグインメタデータ

各ファイルは先頭 20-30 行の frontmatter/設定部分のみ読めば十分。全文を読む必要はない。

### Step 3: 突合分析

リリースノートの各エントリについて、以下の観点で現在の環境との関連性を判定する:

**関連性の判定基準**:
- **plugin/skill 関連**: frontmatter の新フィールド、skill hooks、context:fork、${CLAUDE_SKILL_DIR} 等
- **hook 関連**: 新しいフックイベント（PostCompact, WorktreeCreate 等）、フック改善
- **agent 関連**: model フィールド、memory スコープ、isolation:worktree、background:true
- **API/SDK 関連**: Agent SDK 変更、構造化出力、新ツール
- **UX 改善**: コマンド改善、パフォーマンス改善、バグ修正で影響を受けるもの

**機能の適用範囲に関する重要な制約**（誤提案を防ぐために必ず確認すること）:
- `once: true` は **skill hooks 専用**。settings hooks（hooks.json）には使えない。スクリプト内ガードで代替する。
- `context: fork` はステップバイステップの実行手順を持つスキルのみに有効。会話コンテキストに依存するスキル（対話的レビュー、corrections 参照等）には不適。
- `${CLAUDE_SKILL_DIR}` は SKILL.md 内の参照のみ。Python コード内のパス解決は `Path(__file__)` ベースが適切。

**除外基準**（レポートに含めない）:
- IDE 固有の修正（VSCode extension のみ等）
- OS 固有の修正（Windows のみ等）で該当しないもの
- 既に環境で適用済みの機能
- プロジェクトのドメインと無関係な機能

### Step 4: 優先度分類

検出した項目を3カテゴリに分類する:

#### 即適用可能（High Priority）
- frontmatter に1行追加するだけで適用できる
- hooks.json にエントリを追加するだけで適用できる
- 既存コードの変更が不要 or 極めて軽微
- 明確な効果がある（コンテキスト節約、コスト削減、安全性向上）

#### 中期検討（Medium Priority）
- コード変更を伴うが、影響範囲が限定的
- 新しいスクリプトやモジュールの追加が必要
- テストの追加・修正が必要
- 効果はあるが、既存動作に影響しうる

#### 長期（Low Priority）
- アーキテクチャ変更を伴う
- 調査・PoC が必要
- 将来の Claude Code バージョンで安定してから適用すべき
- 効果が不確実

### Step 5: レポート出力

以下のフォーマットでレポートを出力する:

```markdown
# Release Notes Review Report

**分析日**: YYYY-MM-DD
**対象バージョン範囲**: vX.Y.Z 〜 vA.B.C
**検出項目数**: N件（即適用: X / 中期: Y / 長期: Z）

## 即適用可能 🟢

### [項目名]
- **バージョン**: vX.Y.Z
- **内容**: 1-2行の説明
- **適用方法**: 具体的な変更手順
- **影響ファイル**: 変更が必要なファイル一覧
- **期待効果**: 何が改善されるか

## 中期検討 🟡

### [項目名]
- **バージョン**: vX.Y.Z
- **内容**: 1-2行の説明
- **必要な作業**: 実装に必要なステップ概要
- **影響範囲**: 変更が及ぶモジュール/スキル

## 長期 🔵

### [項目名]
- **バージョン**: vX.Y.Z
- **内容**: 1-2行の説明
- **検討理由**: なぜ今すぐ適用しないか
```

### Step 6: OpenSpec change 提案（`--report-only` でない場合）

レポート出力後、ユーザーに確認する:

> 「レポートを出力しました。OpenSpec change を作成する項目を選んでください。」

AskUserQuestion tool で項目を選択してもらい（multiSelect: true）、選択された項目を1つの change にまとめて
`/openspec-propose` を呼び出す。change 名は `adopt-cc-features-<簡潔な説明>` 形式。

選択がなければ「レポートのみで完了です」と終了する。

## 注意事項

- リリースノートは膨大になりうる。全エントリを1つずつ分析するのではなく、
  まず plugin/skill/hook/agent に関連するキーワードでフィルタし、関連エントリのみを詳細分析する。
- 前回の分析結果が `openspec/changes/` に存在する場合、重複提案を避ける。
  既存 change の proposal.md を読んで、既に提案済みの項目をスキップする。
- プロジェクトが rl-anything 以外の場合でも動作するが、
  rl-anything 固有の概念（evolve, reflect, remediation 等）への言及は省略する。
- 「適用済み」の判定は実際のファイル内容に基づくこと。CLAUDE.md の記述だけで判断しない。
  例: 「context: fork」が適用済みかどうかは、SKILL.md の frontmatter を読んで確認する。
- エージェント定義は `.claude/agents/` だけでなくプラグインルートの `agents/` も確認する。
  rl-anything では `agents/rl-scorer.md` がプラグインルート直下にある。
