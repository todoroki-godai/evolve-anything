---
name: release-notes-review
effort: medium
description: |
  CC リリースノートをPJ環境+グローバル環境と突合し適用可能な新機能を優先度別に報告。
  新コマンド・新スキル・大型機能は 🌟 ハイライトとして冒頭で詳報。環境健康診断も実施。
  Trigger: release-notes, リリースノート確認, CC更新確認, 新機能チェック, 環境見直し, 環境の健康診断
---

# /evolve-anything:release-notes-review — リリースノート分析 & グローバル環境健康診断

Claude Code のリリースノートから**未チェック分のみ**を抽出し、**プロジェクト環境**と
**グローバル環境**の両方と突合して適用可能な改善点を優先度付きレポートとして出力する。
加えて、グローバル環境（rules/skills/agents/settings hooks）の健康診断を行い、
CC 新機能で代替可能なカスタム設定や改善機会を検出する。

## Usage

```
/evolve-anything:release-notes-review              # フル分析（リリースノート + 環境健康診断）
/evolve-anything:release-notes-review --report-only # レポートのみ（実装提案なし）
/evolve-anything:release-notes-review --env-only    # 環境健康診断のみ（リリースノートスキップ）
```

## 実行手順

### Step 0: バージョン範囲の決定

`--env-only` の場合はこのステップをスキップして Step 2 へ。

1. **現在の CC バージョン取得**: `claude --version` を Bash で実行
2. **チェック済みバージョン確認**: auto-memory から `release_notes_last_checked.md` を Read で探す
   - 場所: `~/.claude/projects/<encoded-project-path>/memory/release_notes_last_checked.md`
   - なければ初回実行として扱う（全バージョンが対象）
   - この file の `pending`（前回までに積んだ 🟡 保留リスト）も同時に読み込む（Step 0.5 で使う）
3. **差分判定**: 現在バージョン ≤ チェック済みバージョン → リリースノート分析はスキップし Step 0.5 → Step 2.5 へ

### Step 0.5: 前回 🟡 保留の決着（死蔵防止）

`pending` が空ならこのステップをスキップする。

🟡 保留は「次回観測」と書いたまま照合されず死蔵しがち（観測→作用の変換が切れる）。
**保留は積むだけでなく毎回決着させる**のがこのステップの目的。各 `pending` 項目について、
今回の差分・現環境と突き合わせて次の3択で決着する:

- **resolved（決着）**: CC 側で恒久解消された / 実際に適用した / 観測して不要と確定 → `pending` から除去し、レポート冒頭の「前回保留の決着」に1行で記録
- **dropped（棄却）**: 3 レビュー以上（=約3バージョン）持ち越しても動かない / 前提が消えた → `pending` から除去し「棄却」として記録。**惰性で持ち越さない**
- **carry（継続）**: まだ観測待ちで生きている → `pending` に残す。ただし `carried_count` を +1 し、根拠（何を待っているか）を1行で更新

決着結果は Step 4 レポート冒頭の「前回保留の決着」セクションに出し、Step 5 で `pending` を書き戻す。

### Step 1: リリースノートの取得（未チェック分のみ）

`/release-notes` はビルトインスラッシュコマンド（Skill tool では呼び出せない）。

**取得方法**（優先順位順）:
1. `/release-notes` の出力が既に会話コンテキストにある → そのまま使用
2. ない場合 → 以下のいずれかで取得:
   - `gh api repos/anthropics/claude-code/contents/CHANGELOG.md -q .content | base64 -d` (GitHub API)
   - WebFetch で `https://code.claude.com/docs/en/changelog` (公式ドキュメント)

取得後、**チェック済みバージョン以前のエントリはすべて除外**し、未チェック分のみを分析対象にする。
これによりコンテキスト消費を大幅に削減する。

### Step 1.5: 差分の性質判定（健康診断モードの決定）

`--env-only` の場合は常にフルモード（この判定をスキップ）。それ以外は、未チェック差分に
**環境に影響しうる新機能**が含まれるかで健康診断の深さを切り替える。

**なぜ**: CC のパッチリリースは大半が bug fix で、rules/skills/agents/hooks の構成は
数バージョン変わらないことが多い。新機能ゼロのバージョンで毎回フル走査（全 rules/skills/agents を
Read）しても「count 微増・全 OK・要修正なし」で終わり、コストに対してリターンが薄い。
**健康診断は新機能が出たときにこそ価値がある**（CC 代替・新機能活用の突合ができる）。

**判定**: 差分の CHANGELOG に、以下のような**環境の役割に影響しうる新機能**があるか:
- 新 hook イベント（PostCompact / WorktreeCreate 等）・hook の新フィールド/構文
- skill/agent の新 frontmatter フィールド・skill hooks・context:fork・memory スコープ 等
- 新 permission 構文（`Tool(param:value)` 等）・新設定キー（settings.json）
- model 挙動の変更（デフォルトモデル交代・effort 挙動 等、model-routing に影響）
- 新スラッシュコマンド・新ビルトインスキル・新ツール（例: `/doctor`）— 既存 rule/skill を
  代替しうるか否かに関わらず 🌟 ハイライト対象（Step 3.0.1）なので、これがあれば必ずフルモード

`Added` / `Changed` 行でも、純粋な表示・UX 微調整（badge 追加 / hint 文言 / 配色 / キー操作 /
パフォーマンス改善のみ）は**新機能に含めない**。`Fixed` / `Removed` が中心なら新機能ゼロ扱い。

- **新機能あり → フルモード**: Step 2 / 2.5 を全走査で実施（全 rules/skills/agents を Read）
- **新機能ゼロ（bug fix のみ）→ 軽量モード**: フル走査をスキップし、以下の**構造チェックのみ**行う:
  1. rules / 非gstack skills / agents の**件数**を count（全ファイル Read はしない）
  2. agent の **exact-ID pin** を1発 grep（`grep '^model:' ~/.claude/agents/*.md`）
  3. settings.json hooks を parse し `matcher`/`if`/`command` を列挙 → 参照スクリプトの実在チェック
  4. MEMORY.md の行数（上限接近チェック）
  5. 前回レビューの count と比較し、増減があった箇所だけ軽く確認

  この場合レポート Part 2 は各カテゴリ1行の要約（`rules N・agents N（全OK）` 等）に畳む。
  「PJ 必須対応ゼロ・バグ修正のみ」を結論として明示し、フル診断は次に新機能が出たとき or
  `--env-only` 明示時に回す旨を添える。

### Step 2: プロジェクト環境のスナップショット（フルモードのみ）

現在の環境を把握するために以下を読み取る（先頭 20-30 行の frontmatter/設定部分のみ）:

#### 2.1 プラグイン/プロジェクト環境

1. **CLAUDE.md** — プロジェクト構成、スキル一覧、コンポーネント情報
2. **スキル frontmatter** — 全 `skills/*/SKILL.md` の frontmatter
3. **フック定義** — `hooks/hooks.json`
4. **エージェント定義** — `.claude/agents/*.md` と `agents/*.md` の両方
5. **plugin.json** — `.claude-plugin/plugin.json`

#### 2.2 グローバル環境

6. **グローバル rules** — Glob で `~/.claude/rules/*.md` を列挙し全ファイルを Read
7. **グローバル skills** — Glob で `~/.claude/skills/*/SKILL.md` を列挙し frontmatter を Read
8. **グローバル agents** — Glob で `~/.claude/agents/*.md` を列挙し Read
9. **settings.json** — Read で `~/.claude/settings.json` の hooks セクション

### Step 2.5: グローバル環境健康診断

プロジェクト環境に加え、グローバル環境の品質を診断する。
CC のリリースノートと突合し、新機能で代替可能になった設定も検出する。

**モード注意**: 以下 2.5.1〜2.5.5 の全走査（各ファイルを Read する詳細診断）は**フルモード時のみ**。
Step 1.5 で軽量モードと判定した場合は、この節の全 Read は行わず Step 1.5 の構造チェック（件数 +
pin + hook 実在 + MEMORY 行数）で代替する。`--env-only` は常にフルモード。

#### 2.5.1 Global Rules (`~/.claude/rules/*.md`)

以下を検査:

- **行数チェック**: `rules-style.md` ルールに従い、frontmatter 除外で 3 行以内か
- **重複検出**: 複数ルールが同じことを異なる表現で指示していないか
- **矛盾検出**: ルール間で相反する指示がないか
- **陳腐化チェック**: 参照先ツール/ワークフロー/スキルが現在も存在するか
- **CC 代替チェック**: CC 新機能がルールの役割を吸収していないか
  （例: CC がビルトインで提供するようになった機能を手動ルールで指示している場合）

#### 2.5.2 Global Skills (`~/.claude/skills/*/SKILL.md`)

gstack 内蔵スキル（`~/.claude/skills/gstack/` 配下および `~/.claude/skills/gstack-*/`）を除外し、自作/サードパーティを対象:

- **CC 機能重複チェック**: CC 新機能が自作スキルの役割を吸収していないか
- **frontmatter 品質**: name, description が存在するか。description にトリガーワードがあるか
- **新機能活用チャンス**: CC の新機能で既存スキルを強化できないか
  （例: 新しい frontmatter フィールド、skill hooks、context:fork 等）

#### 2.5.3 Global Agents (`~/.claude/agents/*.md`)

各エージェント定義を Read で確認:

- **品質チェック**: model 指定、maxTurns、disallowedTools の有無
- **新機能活用**: CC の新エージェント機能（memory スコープ、isolation:worktree 等）の活用余地
- **参照の有効性**: 参照しているスキルやツールが現存するか

#### 2.5.4 Settings Hooks (`~/.claude/settings.json`)

hooks 定義を確認:

- **孤立検出**: 参照先スクリプトが存在するか
- **新フックイベント活用**: CC が新たに追加したフックイベント（PostCompact, WorktreeCreate 等）の活用余地
- **evolve-anything hooks との整合**: プラグインの hooks.json と settings.json で重複・競合がないか

**重複判定は `if` 条件まで見る（誤検出防止）**: CC の hook の実効的な同一性は `command` 単独でなく
`(event, matcher, command, if)` の組で決まる。同じ `command` でも `matcher` や `if` 条件
（例: `if: Skill(gstack-ship)` と `if: Skill(commit)`）が異なれば、それは**別トリガーであり重複ではない**。
hook を列挙・比較するときは command だけを抜き出さず、必ず `matcher` と `if` を併記して突合する。
`if` を落として command だけで数えると、発火条件の違う hook を重複と誤検出する。
Bash/Python で settings.json をパースして確認する場合も、`if` フィールドを必ず出力に含めること。

#### 2.5.5 Memory (`~/.claude/projects/*/memory/MEMORY.md`)

現在プロジェクトの MEMORY.md を Read で確認:

- **エントリ数**: 200 行の上限に対する使用率
- **陳腐化チェック**: 古いバージョン番号、完了済みタスク、存在しないファイルへの参照

### Step 3: What's New サマリー生成 & 突合分析

#### 3.0 バージョン別サマリー（全体概観）

突合分析の前に、未チェック差分の各バージョンについて「何が追加・改善・修正されたか」を
プロジェクト関連かどうかを問わず全体概観としてまとめる。
これが Step 4 の `What's New 📋` セクションの素材になる。

- 1バージョンあたり主要変更を 5〜10 件程度箇条書き
- 大量のバグ修正は「バグ修正多数（UI/MCP/auth 等）」のようにグループ化してよい
- 「Internal fixes のみ」「Windows 固有修正のみ」等の場合はその旨だけ記載
- **3.0.1 で 🌟 ハイライトに昇格した項目は、ここでは1行 + 「→ 🌟 参照」に留める**（二重詳細化しない）

#### 3.0.1 🌟 新機能ハイライトの抽出

未チェック差分の中から、**ユーザーが日常操作で直接触れる新登場物**を抽出し、レポート冒頭の
🌟 セクションで1項目ずつ詳しく紹介する。箇条書きサマリーに混ぜると新コマンド・新スキルが
埋もれて見落とされるため、専用セクションに昇格させるのが目的。

**ハイライト対象**（該当するものすべて）:
- 新スラッシュコマンド（例: `/doctor`, `/usage`）・既存コマンドの大幅な機能拡張
- 新ビルトインスキル・新エージェント種別
- 新ツール・新 CLI サブコマンド・主要な新フラグ
- ワークフローを変えうる大型機能（新 hook イベント・新設定機構・新 frontmatter フィールド等）

**ハイライトにしないもの**: バグ修正・パフォーマンス改善・表示微調整・API/SDK のみの変更
（What's New の1行扱いで十分）。

**裏取り**: CHANGELOG の1行では用途・使い方が分からないことが多い。ハイライトに載せる項目は
`claude help <command>`（または `claude <subcommand> --help`）の実行や公式 docs の WebFetch で
用途・引数・動作を確認してから書く。確認できなかった場合は名前から用途を推測して断定せず、
「CHANGELOG 記載のみ・詳細未確認」と明記する。

#### 3.1 プロジェクト環境との突合

未チェック分のリリースノートについて、**プラグイン環境**と**グローバル環境**の両方との関連性を判定する。

**突合の関連性判定基準**:
- **plugin/skill 関連**: frontmatter 新フィールド、skill hooks、context:fork、${CLAUDE_SKILL_DIR} 等
- **hook 関連**: 新フックイベント（PostCompact, WorktreeCreate 等）、フック改善
- **agent 関連**: model フィールド、memory スコープ、isolation:worktree、background:true
- **API/SDK 関連**: Agent SDK 変更、構造化出力、新ツール
- **UX 改善**: コマンド改善、パフォーマンス改善、影響を受けるバグ修正
- **グローバル環境影響**: 新機能がグローバル rules/skills/agents/settings hooks に影響するか
- **環境代替**: Step 2.5 で検出した CC 代替可能項目との紐づけ

**適用先の分類**: 各項目について以下のいずれかを明記する:
- **プラグイン適用**: evolve-anything プラグイン内のファイル変更
- **グローバル適用**: `~/.claude/` 配下のファイル変更
- **両方**: 両環境に影響

**適用範囲の制約**（誤提案防止）:
- `once: true` は **skill hooks 専用**。settings hooks（hooks.json）では使えない
- `context: fork` はステップバイステップ実行のスキルのみ。対話的スキルには不適
- `${CLAUDE_SKILL_DIR}` は SKILL.md 内のみ。Python コードは `Path(__file__)` ベース

**除外基準**: IDE/OS 固有修正、適用済み機能、プロジェクト無関係の機能

### Step 4: 優先度分類 & レポートの組み立て・保存（チャット出力はしない）

2 セクション構成のレポートを**組み立てる**。`--env-only` の場合は Part 1 を省略し Part 2 のみ。

**このステップではチャットに出力しない**。CC はツール呼び出しに挟まれた途中テキストを表示せず
session transcript にも保存しないことがある（2026-07-17 実測・claude-fable-5・interleaved thinking で
text ブロックが欠落）。表示・保存が保証されるのは、後続のツール呼び出しがないターン最終メッセージ
のみ。このスキルは Step 4 の直後に Step 5（memory 書込）・Step 6（実装提案・AskUserQuestion 等）で
ツール呼び出しが続くため、ここでチャット出力するとレポート全文が消失する事故が起きる。

組み立てたレポート全文は、Write でセッションの scratchpad（利用可能なら）または `/tmp` 相当に
`release-notes-report-<YYYY-MM-DD>.md` として保存する。これは表示事故時の保険であり、恒久ストア
ではないので `store_registry` への登録は不要。Step 7 で読み戻してチャットに出力する。

```markdown
# CC Release Notes & Environment Health Report

**分析日**: YYYY-MM-DD
**CC バージョン**: vX.Y.Z → vA.B.C（前回チェック: vX.Y.Z）

---

## 前回保留の決着（`pending` が空なら省略）

Step 0.5 の決着結果を1行ずつ:
- ✅ [項目] — resolved: [CC 側で解消 / 適用済 / 観測して不要と確定]
- 🗑 [項目] — dropped: [N レビュー持ち越しで棄却 / 前提消失]
- 🟡 [項目] — carry(×N): [まだ何を待っているか]

---

## Part 1: Release Notes Review

### 🌟 新機能ハイライト（新コマンド・新スキル・大型機能）

Step 3.0.1 の抽出結果。該当があれば What's New より先に1項目ずつ詳しく紹介する
（見落とし防止の最優先セクション）。該当ゼロなら「今回ハイライトなし」と1行だけ書く。

#### 🌟 [/コマンド名 or 機能名]（vX.Y.Z・種別: スラッシュコマンド / スキル / ツール / 大型機能）
- **何ができるか**: 2〜4行。CHANGELOG の1行転記でなく用途と動作を説明する
- **使い方**: 具体的な起動例（コマンド・引数・典型フロー）
- **裏取り**: help 出力 / 公式 docs で確認済みか、「CHANGELOG 記載のみ・詳細未確認」か
- **自分の環境との関係**: 既存 rules/skills/hooks との重複・代替・組み合わせ方（あれば）

### What's New 📋（バージョン別サマリー）

未チェック差分の各バージョンについて、主要な新機能・改善・バグ修正を全体概観として紹介する。
プロジェクトへの適用可否は問わない。

#### vX.Y.Z
- [新機能・改善・バグ修正の箇条書き（主要 5〜10 件、多数のバグ修正はグループ化可）]

#### vX.Y.(Z-1)
- [同上、または「Internal fixes のみ」等]

---

**プロジェクト環境への適用分析**（検出 N件: 即適用 X / 中期 Y / 長期 Z）

### 即適用可能 🟢
- frontmatter 1行追加、hooks.json エントリ追加等、コード変更不要 or 極めて軽微

#### [項目名]
- **バージョン**: vX.Y.Z
- **適用先**: プラグイン / グローバル / 両方
- **内容**: 1-2行の説明
- **適用方法**: 具体的な変更手順
- **影響ファイル**: 変更が必要なファイル一覧
- **期待効果**: 何が改善されるか

### 中期検討 🟡

**この節に出した項目は Step 5 で `pending` に登録する**（観測待ち・保留はここで一元管理）。
次回レビューの Step 0.5 で必ず決着させ、死蔵させない。

#### [項目名]
- **バージョン**: vX.Y.Z
- **適用先**: プラグイン / グローバル / 両方
- **内容**: 1-2行の説明
- **必要な作業**: 実装ステップ概要
- **影響範囲**: 変更が及ぶモジュール/スキル
- **決着条件**: 何を観測できたら resolved / dropped と判定するか（pending の追跡根拠になる）

### 長期 🔵

#### [項目名]
- **バージョン**: vX.Y.Z
- **適用先**: プラグイン / グローバル / 両方
- **内容**: 1-2行の説明
- **検討理由**: なぜ今すぐ適用しないか

---

## Part 2: Global Environment Health

### Rules (N total)
問題なし:
- [rule] — OK

要確認:
- [rule] — [issue: 行数超過 / 重複 / 矛盾 / 陳腐化]

CC 代替候補:
- [rule] — CC [vX.Y.Z] の [feature] で代替可能

### Custom Skills (N total, gstack 内蔵除外)
問題なし:
- [skill] — OK

CC 機能重複:
- [skill] — CC の [built-in feature] と重複

強化チャンス:
- [skill] — CC [vX.Y.Z] の [feature] で改善可能

### Agents (N total)
- [agent] — OK / [issue]

### Settings Hooks
- [hook event] — OK / [issue: 孤立 / 新イベント未活用]

### Memory
- エントリ数: N行 / 200行上限
- 陳腐化候補: [entry] — [reason]
```

### Step 5: チェック済みバージョンの記録

`--env-only` の場合はこのステップをスキップする（リリースノートを確認していないため）。

Step 4 でレポートを組み立てた後、auto-memory に `release_notes_last_checked.md` を Write で保存する。
**この file は index 用の reference memory ＝スリムに保つ**。詳細なレビュー所見は Step 7 のチャット出力に
残るので、ここには**保存しない**（過去は note に全バージョンの詳細を溜め込み
数百行に肥大して reference memory を汚していた。その反省でスリム化する）。

保持するのは「最後にチェックしたバージョン + 日付」と「未決着の 🟡 保留リスト」のみ:

```markdown
---
name: release-notes-last-checked
description: release-notes-review で最後にチェックした Claude Code バージョン
type: reference
---

last_checked_version: <現在の CC バージョン>
last_checked_date: <本日の日付>
pending:
  # Step 0.5 で decision した後の carry のみ残す（resolved/dropped は消す）
  - item: <保留項目名>
    since_version: <積んだ時の CC バージョン>
    carried_count: <持ち越し回数>
    resolve_when: <何を観測できたら決着するか>
```

`pending` が空なら `pending: []` と書く。**carried_count が 3 を超えたら Step 0.5 で dropped 検討**。

**詳細所見を残したい場合**（大きめの新機能を分析した回など）は、別 archive memory
`release_notes_review_history.md`（type: reference・index には1行ポインタのみ）に**追記**する。
last_checked.md 本体には混ぜない。

**移行（初回のみ）**: 既存の `release_notes_last_checked.md` が過去 note で肥大している場合は、
その詳細履歴を `release_notes_review_history.md` に一度退避してから、本体を上記スリム形に置き換える。

### Step 6: 実装提案（`--report-only` でない場合）

ユーザーに確認し、選択された項目について:
1. **プラグイン適用項目**: evolve-anything 内のファイル変更手順を提示
2. **グローバル適用項目**: `~/.claude/` 配下の変更手順を提示
3. 環境の問題（ルール行数超過、孤立 hook 等）の修正を提案
4. CC 代替候補について、移行手順を提示
5. 設計判断がある場合は `/spec-keeper adr` で ADR を作成する
6. **実装後レビュー**: ファイル変更を行った後、`git diff` が存在する場合は `Skill` tool で `skill: "review"` を呼び出し変更内容のコードレビューを実施する。`--report-only` の場合はスキップ。

### Step 7: レポート全文出力（ターン終端・MUST）

Step 4〜6 のすべてのツール呼び出しが完了した後、Step 4 で保存したレポート全文を
**ターンの最終メッセージとして**チャットに出力する（`--report-only` でスキップされた項目があっても
このステップ自体は必ず実行する）。

**この出力の後にツールを一切呼ばないこと（MUST）**。理由は Step 4 に記載の通り、CC がツール呼び出しに
挟まれた途中テキストを表示・保存しないことがあるため。表示・保存が保証されるのは後続のツール呼び出しが
無いターン最終メッセージのみなので、レポート出力は必ずそのターンの一番最後にする。

## 注意事項

- リリースノートは膨大になりうる。未チェック分に絞った後でも、
  まず plugin/skill/hook/agent 関連キーワードでフィルタし、関連エントリのみ詳細分析する。
- 既存の ADR や CHANGELOG に同等の変更がある場合、重複提案を避ける。
- 「適用済み」の判定は実際のファイル内容に基づく（CLAUDE.md の記述だけで判断しない）。
- エージェント定義は `.claude/agents/` と `agents/` の両方を確認する。
- evolve-anything プラグインの `/audit` とは役割が異なる: audit はテレメトリ駆動の環境品質スコアリング、
  こちらは CC 更新との突合に特化した定性レビュー + グローバル環境健康診断。
- 環境診断はファイルの Read のみで行う。LLM による高コスト分析は避け、構造的チェックに徹する。
