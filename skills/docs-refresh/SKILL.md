---
name: docs-refresh
effort: low
description: |
  docs/site/ 以下の HTML 説明サイトをリポジトリの現状に合わせて最新化する。
  バージョン番号・スキル一覧・4つの柱・アーキテクチャ表を自動更新し、手動管理の sources.html には触れない。
  Trigger: docs更新, docs refresh, サイト更新, HTMLを最新化, ドキュメントサイト更新, リリース後にdocs, docs/site を更新
  /rl-anything:version や版上げ作業の最後に呼ぶこと。
allowed-tools: Read, Edit, Write, Bash, Glob
---

# docs-refresh — docs/site/ 最新化

`docs/site/` の HTML 説明サイトをリポジトリの現状に合わせて更新する。
`sources.html`（arXiv 参照・issue 紐付け）は手動キュレーション対象なので **絶対に触らない**。

## 更新対象と情報源

| 更新項目 | 情報源 | 対象ファイル |
|---|---|---|
| バージョン番号 | `.claude-plugin/plugin.json` | 全 HTML（header の `class="header-version"` span） |
| スキル一覧 | `skills/` ディレクトリ + `CLAUDE.md` の4つの柱テーブル | `pipeline.html` の `#skills` セクション |
| 4つの柱 | `CLAUDE.md` の「## 4つの柱」テーブル | `index.html` の `#pillars` セクション |
| アーキテクチャ表 | `CLAUDE.md` の「## コンポーネント」テーブル | `reference.html` の `#arch` セクション |

## 手順

### Step 1: 現バージョンを取得する

```bash
python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])"
```

### Step 2: 全 HTML のバージョン badge を更新する

`docs/site/` の HTML ファイル（`sources.html` を除く）を Glob で列挙し、
`<span class="header-version">v?.??.?</span>` の部分を新バージョンに Edit で書き換える。

### Step 3: スキル一覧の差分を確認する

```bash
ls skills/ | sort
```

`pipeline.html` の `#skills` セクションを Read して現在記載されているスキルと突き合わせ、
追加・削除・名称変更があれば Edit で反映する。

スキルカードの HTML 構造（既存を踏襲）:
```html
<div class="skill-card">
  <div class="skill-name">スキル名</div>
  <div class="skill-badge">カテゴリ</div>
  <p>説明（CLAUDE.md または SKILL.md の description 冒頭から引く）</p>
</div>
```

説明は CLAUDE.md の「## 4つの柱」テーブルか、当該スキルの `SKILL.md` frontmatter の `description` 冒頭から取る。
LLM 的な言い換えは不要。原文を短縮するだけでよい。

### Step 4: 4つの柱を確認する

CLAUDE.md の `## 4つの柱` テーブルを Read して、`index.html` の `#pillars` セクションと比較する。
柱の追加・名称変更・説明変更があれば Edit で反映する。

### Step 5: アーキテクチャ表を確認する

CLAUDE.md の `## コンポーネント` テーブルを Read して、`reference.html` の `#arch` セクションと比較する。
コンポーネントの追加・削除があれば Edit で反映する。

### Step 6: 変更サマリーをユーザーに報告する

更新したファイル・項目を箇条書きで報告する。変更なしの項目は「変更なし」と明記する。
編集ゼロだった場合は「docs/site/ はすでに最新です」と伝える。

## 注意事項

- **sources.html は読まない・触らない**。この制約は理由がある：arXiv 参照や issue 紐付けは手動キュレーションで価値を持つため、自動更新すると情報が失われる。
- `reference.html` の `#quickstart` セクション（ストーリー仕立ての図やシナリオカード）はデザイン判断が入っており、コード解析では再現できないため更新対象外。
- スキル説明は既存 HTML の文体・長さを合わせる（1〜2 文）。長い説明を無断で追加しない。
- HTML の CSS クラス名・構造はいじらない。テキストコンテンツのみ更新する。
