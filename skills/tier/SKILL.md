---
name: tier
effort: low
description: |
  モデルティア（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）の正典を対話的に変更する。
  正典 CLI `bin/evolve-tier`（`~/.claude/model-tiers.json`、#193）の薄い UX ラッパーで、
  現状表示 → 変更内容の解釈 → 正典更新 → sync の dry-run diff 提示 → ユーザー承認 →
  --apply → drift advisory、の順に安全にティアを切り替える。
  Trigger: モデル変更, model change, ティア変更, tier 変更, HEAD を変えて, HEAD を fable にして,
  モデルティア, ティアのモデル, model tier, HEAD/HARD/NORMAL/MECH/REVIEW のモデル変更, evolve-tier
allowed-tools: Bash, Read, AskUserQuestion
---

# /evolve-anything:tier — モデルティア正典の対話変更

model-routing のティア（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）の正典は
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。以前は
model-routing rule・各 PJ の agent frontmatter・settings.json に散在し、モデル変更のたびに
手動で全ファイルを追従する必要があった（2026-07-10 opus 4.8 廃止時に HEAD が fable⇄sonnet を
同日中に往来した実例）。**このスキル自体はファイルを直接編集しない** — 全ての変更は
`bin/evolve-tier` CLI 経由で行い、このスキルは「何をどう変えるか」の対話的な聞き取りと、
sync 適用前の diff 提示・承認取得を担う UX レイヤーに徹する。

## Usage

```
/evolve-anything:tier                          # 会話から意図を汲み取って対話的に進める
/evolve-anything:tier HEAD を fable にして
```

## 実行手順

### Step 1: 現状のティア表を表示

**必ず `${CLAUDE_PLUGIN_ROOT}` 経由で呼ぶ**（相対パスは対象 PJ の cwd で実行されるため
`No such file` になる既知 pitfall。`scripts/tests/test_skill_md_plugin_paths.py` が回帰検出する）。

```bash
${CLAUDE_PLUGIN_ROOT}/bin/evolve-tier show
```

正典ソース（file/defaults）と全ティアの現在の model/effort、targets 件数が出る。
`_load_error` 付きで表示された場合（config JSON 破損等）はそのままユーザーに伝える
（defaults へ fail-open した表示なので、実際の正典とズレている可能性がある）。

### Step 2: 変更内容を解釈する

ユーザー発話から「どのティアを」「どの model に」「どの effort に」変えたいかを読み取る。
以下が曖昧・未指定なら `AskUserQuestion` で確認する（**推測で進めない** — model/effort の
誤指定は他 PJ の agent frontmatter や settings.json に波及する変更なので、確認コストが安い側に
倒す）:

- **tier**: 選択肢は必ず `HEAD` / `HARD` / `NORMAL` / `MECH` / `REVIEW` の5つ
- **model**: 選択肢は `opus` / `sonnet` / `haiku` / `fable`（エイリアスのみ。exact ID や
  `inherit` は tier の model として使えない — 指定すると CLI が拒否する）
- **effort**: `low` / `medium` / `high` / `xhigh` / `max`、または `haiku` を選んだ場合は
  「effort なし」（haiku は effort 非対応で、指定すると CLI がエラーを返す）

### Step 3: 正典を更新する

```
${CLAUDE_PLUGIN_ROOT}/bin/evolve-tier set <TIER> --model <MODEL> --effort <EFFORT>
```

haiku の場合は `--effort <EFFORT>` の代わりに `--no-effort` を付ける。

バリデーションは CLI 側が行う（model は既知エイリアスのみ・effort は5値のみ・haiku+effort 併用は
拒否・exact ID や `inherit` は拒否）。**スキル側で事前チェックはしない** — CLI がエラー
（exit code 2）を返したら、そのエラーメッセージをそのままユーザーに提示する（言い換えたり
握りつぶしたりしない）。

config が壊れている場合（JSON 破損・`tiers` キー欠落等）、`set` は defaults で黙って上書きせず
**exit code 1 の strict エラー**を返す設計になっている。この場合も同様にエラーをそのまま提示し、
「config を直してから再試行してください」と伝える（自動修復はしない）。

### Step 4: sync の dry-run diff を全件提示する

```bash
${CLAUDE_PLUGIN_ROOT}/bin/evolve-tier sync
```

`--apply` を付けない既定呼び出しは dry-run（書込ゼロ）で、`targets`（agents/settings/
routing_rules に登録済みのファイルのみ・自動検出はしない設計）ごとの diff を出す。
**この diff を省略・要約せず全件そのままユーザーに見せる**（何がどう変わるか事前確認できる
ことが安全性の核）。

drift が 0 件（変更が既存 target に実質何も影響しない）なら、その旨を伝えて Step 6 へ進んでよい。

### Step 5: ユーザー承認後にのみ適用する

`AskUserQuestion` で明示承認を取る:

```
上記の diff を適用しますか？

A) Yes - sync --apply する
B) No - 適用せず中止する（Step 3 で set 済みの正典はそのまま残る）
```

**承認前に `--apply` を実行しない**。A が選ばれた場合のみ:

```
${CLAUDE_PLUGIN_ROOT}/bin/evolve-tier sync --apply
```

適用後、`in_sync`/`drift`/`skip`/`missing` の結果一覧を報告する。

### Step 6: drift advisory を表示する

```bash
${CLAUDE_PLUGIN_ROOT}/bin/evolve-tier drift
```

正典のどの tier の model にも使われなくなったエイリアス（例: 撤去したモデル名）が
`advisory_scan` 対象ディレクトリの散文（rules 等）に残っていないかを検出する。
**見つかった箇所は絶対に自動書き換えしない** — 散文の言及は文脈依存で機械編集すると
文意を壊すリスクが高いため、一覧を提示して人間が個別に判断する設計（sync target とは
明確に別扱い）。

### Step 7: 対象外の注意点を伝える

- 現在の頭（オーケストレーター）セッション自身のモデルは `~/.claude/settings.json` の
  グローバル設定に依存するが、**これは sync の対象外**（sync は `targets` に明示登録された
  ファイルだけを触る）。ユーザーが「今のセッションのモデルも変えたい」と言った場合は
  ティア正典の変更とは別の話なので `/model` コマンドを案内する。
- `targets` に未登録のファイルは sync で拾われない（自動検出はしない設計上の確定事項）。
  「変えたはずなのに反映されない」場合は `~/.claude/model-tiers.json` の `targets` に
  対象ファイルが登録されているか確認するよう伝える。

## 制約（必ず守ること）

- `sync --apply` はユーザーの明示承認を得てからのみ実行する。diff を見せずに、または
  承認前に `--apply` することは禁止。
- `drift` が見つけた散文中の古いモデルエイリアスは自動で書き換えない。一覧提示のみ。
- `~/.claude/model-tiers.json` が壊れている場合、`set` は defaults での上書きでなく
  strict エラーを返す。これをスキル側で握りつぶしたり、defaults で「動くように見せる」
  ことはしない — エラーをそのままユーザーに見せる。

## エッジケース

- **`bin/evolve-tier` が見つからない**: プラグイン未更新の可能性。`${CLAUDE_PLUGIN_ROOT}` が
  正しく展開されているか確認する。
- **`~/.claude/model-tiers.json` が未作成**: `show`/`sync` は defaults ベースで動く
  （`_source: "defaults"`）。`set` は初回呼び出しで defaults から config を新規作成する
  ため、通常このスキルのフローでは意識不要。
- **`sync` の drift が 0 件**: 正典は既に全 target と一致している。適用不要としてそのまま
  Step 6 へ進む。
- **ユーザーが tier だけ指定し model/effort を言わない**（例:「HEAD を変えて」だけ）: 何に
  変えたいかが分からないため、必ず `AskUserQuestion` で model（+ 必要なら effort）を聞く。
  デフォルト値を勝手に補わない。

## 関連

- 正典 CLI 本体・set/sync/drift のコアロジックは `scripts/lib/tier_policy.py` /
  `tier_policy_sync.py` / `tier_policy_drift.py` / `tier_policy_cli.py`（#193）。
  このスキルはそれらへの対話 UX ラッパーであり、ロジックの実体は持たない。
- グローバル rule `model-routing.md` のティア表は sync target（`routing_rules`）に
  登録すれば `<!-- evolve-tier:begin -->`〜`<!-- evolve-tier:end -->` マーカー間が
  自動反映される。

## Tags

tier, model-routing, evolve-tier, HEAD, HARD, NORMAL, MECH, REVIEW, model change
