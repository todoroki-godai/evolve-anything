---
name: pitfall-curate
effort: medium
description: |
  任意PJの pitfalls.md を「育てる」PJ非依存スキル。類似 pitfall の重複排除、
  普遍性分類（universal/project/instance + 汎用度1-5）、三段階開示の配布版(Top-N)生成、
  記録↔分類↔配布の同期ゲートを提供する。pitfall が貯まって重複・肥大化・配布漏れが
  起きているとき、または新規PJで pitfall 運用の型を導入したいときに使う。
  Trigger: pitfall-curate, pitfalls 整理, pitfall 重複, pitfall 分類, pitfall 配布版,
  pitfalls top-N, pitfall 同期, pitfall を育てる, pitfall 運用
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# pitfall-curate — pitfalls.md を育てる

figma-to-code で 200 件超まで pitfall を磨く過程で確立した運用の型を、特定ドメインに
依存しない形で任意PJに提供する。rl-anything 既存の `pitfall_manager`（自己進化スキル
専用のライフサイクル管理）とは別物で、こちらは**どのPJの pitfalls.md でも使える汎用ツール**。

## なぜこのスキルが要るか

pitfall は放置すると必ず次の3つで破綻する。このスキルはそれぞれに対応する:

1. **重複**: 同じ失敗が言い回しを変えて何度も記録される → **dedup**（類似検出 + supersede）
2. **肥大化**: 200 件超を agent に渡すと認知過負荷で実装品質が下がる → **distill**（Top-N 配布版生成）
3. **配布漏れ**: 「どれを先回りチェックすべきか」の選定が手動だと必ずズレる → **sync**（3層 drift 検出）

加えて、どの pitfall を配布版に載せるかを機械的に決めるための **classify**（普遍性分類）がある。

## 役割分担（重要）

- **判断は agent（あなた）が行う**: 普遍性分類（universal/project/instance + 汎用度）と
  reframing 文（「するな」→「しろ。理由〜」）は意味理解が要るので LLM が担う。
- **決定論処理は script が行う**: parse / 類似度計算 / 配布版の選定・描画 / drift 検出 /
  フィールド書き込みは `scripts/pitfall_curate.py` に委譲する。手で markdown を編集しない。

この分担により、script 側は LLM 非依存でテスト可能になっている。

## 対象 pitfalls.md のフォーマット

rl-anything 標準（`pitfall_manager` と共通）:

```markdown
## Active Pitfalls

### <pitfall のタイトル>
- **Status**: Active
- **Root-cause**: action — 一行で原因
- **Pre-flight対応**: Yes
- **Transferability**: universal   ← このスキルが付与
- **Generality**: 5                ← このスキルが付与

## Candidate Pitfalls
## Graduated Pitfalls
```

`Transferability` / `Generality` が無い pitfall は「未分類」として扱う。

### 有機的に育った実フォーマットへの耐性

実PJの pitfalls.md は正準スキーマと完全一致しないことが多い（このスキル自身を
atlas-browser でドッグフードして判明）。パーサは次のゆらぎを吸収する:

- **セクション見出しの fuzzy match**: `## Active`（`## Active Pitfalls` でなくても）、
  `## New（…）` は Candidate 相当、`## Graduated` 系を認識する。
- **`## N.` 番号付きエントリ**: `### ` でなく `## 1. タイトル` 形式（sys-bots 等）も
  エントリとして認識する。番号なしの構造見出し（`## カテゴリ一覧` 等）は拾わない（足切り）。
- **`### サブ見出し`の降格**: 文書が番号付き `### N.` エントリを使う流儀（atlas 等）の場合、
  番号なし `### 真の原因` 等は1エントリ内のサブ見出しとみなし `normalize` で `#### ` へ降格する
  （幽霊エントリ化を防ぐ）。番号は保持され冪等。正準形（番号なしエントリのみ）はこの降格をしない。
- **メタデータ 2 形式**: `- **Key**: value` バレットに加え、`**Status**: A | **Last-seen**: B`
  のインラインパイプ形式（sys-bots / atlas）もフィールドとして取り込む。
- **HTML コメントのスキップ**: `<!-- -->` 内の `### [タイトル]` テンプレートを phantom
  エントリにしない（docs-platform の空ひな型対策）。
- **dedup の判別信号 fallback**: `Root-cause` フィールドが無いエントリは、内容フィールド
  （`症状` / `対策` / `検出` 等。メタデータは除外）を類似判定の信号に使う。これが無いと
  dedup が「タイトルだけ」になり実ファイルで重複を一切検出できない。
- **日本語の細粒度マッチ**: 類似度は空白区切りトークンに CJK 文字 bigram を加えて算出する
  （`similarity.py` の `\W` 分割は日本語を分割しないため）。

これらで吸収しきれないほど構造が崩れた既存ファイルは、後述の `normalize` で正準形に
寄せてから dedup/distill にかける（収束路線）。決定論コアは `scripts/core.py`（curate）と
`scripts/parse.py`（フォーマット I/O: parse / seed / normalize）に分離されている。

## 普遍性分類の語彙

特定ドメイン語（figma の U/M/E 等）に縛られない汎用語彙を使う:

| Transferability | 意味 | 配布版 |
|-----------------|------|--------|
| `universal` | このPJ種別全般に起こりうる汎用原則 | 載せる |
| `project` | このPJ固有だが複数箇所で再発しうる | 汎用度次第で載せる |
| `instance` | 特定実装1件にしか当てはまらない | **載せない**（載っていれば降格漏れ） |

`Generality`（汎用度 1-5）: 5=どんな実装でも起こる … 1=この特定実装だけ。
判断基準の具体定義は対象PJの CLAUDE.md / ドメインに合わせて解釈する。

## 導入パターン（収束路線）

フォーマットは PJ ごとにバラつくため、curate の前に正準フォーマットへ寄せる:

- **新規PJ（pitfalls.md が無い）** → `seed` で正準ひな型を配る:
  ```bash
  python3 "$PFC" seed --out <path>      # 既存があれば拒否（--force で上書き）
  ```
- **既存PJ（独自フォーマットで育っている）** → `normalize` で正準形へ変換してから curate:
  ```bash
  python3 "$PFC" normalize --pitfalls <path>            # stdout に出力（dry-run）
  python3 "$PFC" normalize --pitfalls <path> --out <path>  # 同じパスで in-place 変換
  ```
  normalize は構造（見出しレベル・セクション・メタdataのバレット化）だけを揃え、本文の
  散文・コードは保持する。**エントリ0件で実質コンテンツ（テーブル/リンク等）がある場合は
  インデックス/TOC ファイルとみなし wipe せず中断する**（sys-bots の index `pitfalls.md` で
  全 wipe しかけた事故から導入）。インデックスは normalize 対象外、category ファイルだけ掛ける。**H1 タイトルの説明文（`# atlas-browser: 既知の問題と対策`）と、
  H1 と最初のセクションの間のプリアンブル散文（`> 自動チェック…` 等）も保持する**（実 PJ
  ドッグフードで消失バグを発見し修正済み）。冪等（正準→正準で不変）。
  既知の制限: セクション見出しの注釈（`## New（未検証…）` の括弧書き）は正準セクション名が
  固定のため失われる。番号なし `## ` の非セクション見出し（番号付きエントリ流儀の中の例外的な
  pitfall タイトル）は足切りされ前エントリ本文に折り込まれるため、手動で番号付きエントリ化が要る。
  変換前に必ず内容を確認し、ユーザーに diff を提示してから in-place 上書きする。

## ワークフロー

ユーザーが `/rl-anything:pitfall-curate [pitfalls.mdのパス]` を呼んだら以下を実行する。
パス未指定なら、カレントPJの `.claude/skills/*/references/pitfalls.md` を Glob で探し、
複数あれば AskUserQuestion で対象を確認する。フォーマットが正準でない場合は先に `normalize`
を提案する（収束路線）。

スクリプトは次で呼ぶ:

```bash
PFC="$CLAUDE_PLUGIN_ROOT/skills/pitfall-curate/scripts/pitfall_curate.py"
# CLAUDE_PLUGIN_ROOT 未設定なら plugin リポジトリルートを使う
```

### Step 1: 未分類を解消する（classify）

```bash
python3 "$PFC" unclassified --pitfalls <path>
```

返ってきた各 pitfall について、title と root_cause から **agent が** Transferability と
Generality を判断し、対象PJの性質に照らして書き戻す:

```bash
python3 "$PFC" classify-set --pitfalls <path> \
  --title "<title>" --transferability universal --generality 5
```

件数が多い場合は判断結果をまとめてユーザーに提示し、承認を得てから書き戻す
（LLM バッチ処理の事前確認ルール）。**この分類は LLM 呼び出しを伴わない**
（agent 自身が判断するだけ）ので、件数に応じたトークン見積もりは不要。

### Step 2: 重複を排除する（dedup）

```bash
python3 "$PFC" dedup --pitfalls <path>
```

類似ペアが出たら、どちらが新しい/上位概念かを agent が判断し、ユーザーに確認の上で
古い方を新しい方に supersede 記録する:

```bash
python3 "$PFC" supersede --pitfalls <path> --old "<旧title>" --new "<新title>"
```

threshold は既定 **0.12**（日本語主体のコーパス向け）。日本語は CJK bigram jaccard で
0.1〜0.2 帯に分布するため、英単語主体のコーパスなら `--threshold 0.3`〜`0.4` に上げる。
検出が多すぎ/少なすぎる場合に調整する。dedup は「人間レビュー用の候補出し」なので、
recall 寄り（やや低め）の閾値が扱いやすい。

### Step 3: 配布版を生成する（distill）

```bash
python3 "$PFC" distill --pitfalls <path> --out <dist-path> --top 20
```

`universal` かつ汎用度 ≥ 4 は無条件で配布版入り。残り枠を普遍性・汎用度の高い順に埋める。
生成された配布版には各 pitfall の `<!-- reframe: ... -->` プレースホルダが残るので、
**agent が positive reframing 文を記入する**（「〜するな」ではなく「〜しろ。理由は〜」）。

`--top` は対象PJの好みで調整可能（figma は 20）。配布版のパスは慣習上
`references/pitfalls-top<N>.md` 等。

> なぜ配布版が要るか: フル pitfalls.md（数百件）を実装 agent に渡すと認知過負荷で
> 品質が下がる（figma で実測）。先回りチェックには厳選した Top-N だけを渡す。

### Step 4: 同期を確認する（sync）

```bash
python3 "$PFC" sync --pitfalls <path> --dist <dist-path> --top 20 --check
```

記録↔分類↔配布版の3層 drift を検出する:
- **未分類**: Active/Candidate で Transferability/Generality 欠落 → Step 1 へ
- **必須漏れ**: universal/汎用度≥4 なのに配布版に無い → Step 3 を再実行
- **降格漏れ（stale）**: 配布版にあるが資格を失った（superseded / instance 化）→ 配布版から外す

`--check` は未同期なら exit 1 を返すので、CI や Stop hook で pitfalls.md 変更時に回せる。

## 設定で吸収するもの（ドメイン非依存を保つ）

- pitfalls.md / 配布版のパス: 引数で渡す
- `--top` の N: PJごとに調整
- `--threshold`（dedup）/ `--mandatory-generality`（distill/sync）: 既定 0.12 / 4
- 分類カテゴリの具体的な判断基準: 対象PJの CLAUDE.md に従って agent が解釈

## スコープ外

- figma-to-code の既存 TS 運用（`pitfall-similarity.ts` 等）の置き換え — 当面併存
- pitfall コンテンツ自体の全PJ横断集約（それは `bin/rl-fleet recall` の領域）
- `pitfall_manager`（自己進化スキル専用）との統合 — 別ライフサイクルとして共存

## テスト

```bash
python3 -m pytest skills/pitfall-curate/scripts/tests/ -v
```
