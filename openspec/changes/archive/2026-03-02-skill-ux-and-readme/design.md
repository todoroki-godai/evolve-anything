## Context

rl-anything は Claude Code Plugin としてインストールされるが、現在のUXは `python3 <PLUGIN_DIR>/skills/.../optimize.py --target ...` という長いコマンドをユーザーが手動入力する形式。Plugin ディレクトリのパスを知っている必要があり、Claude Code Plugin の利点（スラッシュコマンドで呼び出し）が活かされていない。また README.md は技術仕様の羅列で、「なぜこの Plugin を導入すべきか」のストーリーがない。

## Goals / Non-Goals

**Goals:**
- SKILL.md の instructions を「Claude がスクリプトを自動実行する」形式に書き換え、`/optimize`, `/rl-loop` で即利用可能にする
- README.md を導入ストーリー（Before/After）中心に全面更新
- CLAUDE.md のクイックスタートをスラッシュコマンド形式に同期

**Non-Goals:**
- スクリプト本体（optimize.py, run-loop.py）のロジック変更
- 新しいスラッシュコマンドの追加（`/generate-fitness` は別 change で対応）
- CI/CD パイプラインの変更

## Decisions

### 1. スラッシュコマンドへの移行方針

**選択**: SKILL.md の `name` フィールドを短縮名に変更し、instructions 内で `<PLUGIN_DIR>` を使ったスクリプト実行コマンドを Claude に自動実行させる

**代替案**:
- A) ラッパースクリプト（`bin/optimize`）を作成してPATHに追加 → Plugin インストール時のPATH設定が不安定
- B) Claude Code の hook で自動実行 → hook はイベント駆動であり、ユーザー起点のコマンド実行には不向き

**理由**: SKILL.md の instructions に実行手順を書けば、Claude が `/optimize` 呼び出し時に自動でスクリプトを実行する。既存の Claude Code Plugin の仕組みに乗るため、追加の仕組みが不要。

### 2. SKILL.md instructions の書き換え方式

**選択**: instructions を「ユーザーへのドキュメント」から「Claude への実行指示」に転換。具体的には、引数パース → スクリプト実行 → 結果表示 の手順を命令形で記述

**代替案**: instructions をそのまま残し、別途 `run.md` を追加 → ファイルが分散し、メンテナンスコストが増加

**理由**: SKILL.md の instructions は Claude が読んで実行する前提の仕組み。ユーザー向けドキュメントは README.md に集約するのが自然。

### 3. README のストーリー構成

**選択**: Before（課題）→ What（Plugin の概要）→ After（導入効果）→ Quick Start → 詳細リファレンスの順で構成

**代替案**:
- A) 技術仕様中心（現状維持）→ 導入動機が伝わらない
- B) チュートリアル形式 → 長大になりメンテナンスが困難

**理由**: OSS の README として「なぜ使うべきか」が最初に来るのが標準的。Quick Start で即座に試せることを示し、詳細は後段に配置。

### 4. CLAUDE.md の同期範囲

**選択**: CLAUDE.md のクイックスタートセクションのみをスラッシュコマンド形式に更新。コンポーネント表や適応度関数の詳細は維持

**代替案**: CLAUDE.md を README.md から自動生成 → 過度な自動化で CLAUDE.md 固有の情報（テストコマンド等）が失われるリスク

**理由**: CLAUDE.md は Claude Code が読むプロジェクト説明であり、README.md とは目的が異なる。共通部分（クイックスタート）のみ同期し、それ以外は独立管理。

## Risks / Trade-offs

- **[既存ユーザーの混乱]** → SKILL.md のスキル名変更（`genetic-prompt-optimizer` → `optimize`）は breaking change。README に移行手順を記載
- **[instructions の書き方が変わる]** → Claude への実行指示形式は Claude Code のバージョンアップで挙動が変わる可能性。SKILL.md の instructions フォーマットは Claude Code の公式ドキュメントに準拠する
- **[README と CLAUDE.md の乖離]** → クイックスタート部分のみ同期対象とし、検証タスクで乖離チェックを実施
