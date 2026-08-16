---
name: implement
effort: medium
description: |
  計画を構造化実装。plan artifact→複雑性アセスメント→shallow/standard/deep の深度別実行→テレメトリ記録。
  Trigger: implement, 実装して, 実装開始, build this, 計画を実装, コーディング開始
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
---

# 構造化実装スキル

plan → implement → ship の「implement」フェーズを構造化する。
計画の決定事項を漏らさず実装し、その軌跡をテレメトリに記録する。

## なぜこのスキルがあるか

「実装して」と言うだけでも Claude は実装できる。しかし:
- plan-eng-review で議論した 8 個の決定事項、全部覚えてる？
- 実装で何タスク・何分かかったか、後から振り返れる？
- 大きめの feature で並列化の判断を毎回手動でやりたい？

このスキルは「やりっぱなしの実装」を「学習する実装」に変える。
さらに、複雑性に応じて **shallow（軽量）/ standard（通常）/ deep（重厚）** を自動選択し、
小さい変更に重すぎる手続きを課さない。

## 実行手順

### Step 0: 計画の収集

まず実装の元になる計画を集める。

**gstack plan artifact がある場合:**

```bash
evolve-usage-log "implement"
setopt +o nomatch 2>/dev/null || true  # zsh compat
GSTACK_SLUG=""
if command -v ~/.claude/skills/gstack/bin/gstack-slug &>/dev/null; then
  eval "$(~/.claude/skills/gstack/bin/gstack-slug 2>/dev/null)" 2>/dev/null || true
  GSTACK_SLUG="$SLUG"
fi
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | tr '/' '-' || echo 'no-branch')
PROJECT_DIR="${GSTACK_HOME:-$HOME/.gstack}/projects/${GSTACK_SLUG:-unknown}"

echo "=== Plan Artifacts ==="
ls -t "$PROJECT_DIR"/*-${BRANCH}-ceo-plan-*.md 2>/dev/null | head -1 || true
ls -t "$PROJECT_DIR"/*-${BRANCH}-*test-plan-*.md 2>/dev/null | head -1 || true
ls -t "$PROJECT_DIR"/*-${BRANCH}-design-*.md 2>/dev/null | head -1 || true
ls -t "$PROJECT_DIR"/*-ceo-plan-*.md 2>/dev/null | head -1 || true
ls -t "$PROJECT_DIR"/*-design-*.md 2>/dev/null | head -1 || true
```

見つかった artifact は全て読む。これが実装の仕様書になる。

**plan artifact が見つからない場合:**
会話の文脈から計画を把握する。ユーザーに「何を実装しますか？」と聞く。
plan-eng-review をこのセッションで実行済みなら、その決定事項を使う。

**会話内に plan mode の plan ファイルがある場合:**
v2.1.111 以降、plan ファイル名はプロンプト内容由来（例: `fix-auth-race-snug-otter.md`）。
`ls -t *.md 2>/dev/null | head -3` でカレントディレクトリの最新 plan ファイルを特定できる。

---

### Step 0.2: 既存実装との突合（必須・スキップ不可）

**書き始める前に「同じものが既に無いか」を実物で確認する。** 計画に書かれていないことは
「存在しない」根拠にならない（計画自体が既存を見ずに書かれている場合がある）。

```bash
# 1. 同名・同義の実装が既にあるか（キーワードは計画から2〜3語）
grep -rn "<keyword>" scripts/ hooks/ skills/ --include=*.py --include=*.md | head -20
# 2. 過去に実装・却下されていないか
git log --oneline -20 --grep "<keyword>"
head -40 CHANGELOG.md
# 3. 仕様上の位置づけ（あれば）
grep -rn "<keyword>" SPEC.md spec/ docs/decisions/ 2>/dev/null | head -10
```

判定と行動:

| 見つかったもの | 行動 |
|---|---|
| 同じ機能が実装済み | **実装しない。** 何が既にあるかを報告して指示を仰ぐ |
| 同型の処理が別箇所にある | 新規実装せず**その部品へ寄せる**（再発明は同じ欠陥を N 回再生産する） |
| 判定・分類ロジックが既にある | 重複実装せず**単一 predicate に集約**する |
| 何も無い | 「突合した結果無かった」と1行残して Step 0.5 へ |

無いことの確認を省くと、同型の実装と欠陥を独立に再生産する（#49 / #103-#105 / #159 / #160）。

---

### Step 0.5: 複雑性アセスメント

収集した計画・会話文脈をもとに、実装深度を判定する。
grep や差分カウントは使わず、LLM がチェックリストを評価する。

**判定チェックリスト:**
- [ ] 新規 API / public インターフェース / export 関数を追加するか？
- [ ] 3 つ以上のモジュール/ディレクトリにまたがる変更か？
- [ ] 外部サービス連携・DB スキーマ変更・インフラ変更を含むか？
- [ ] CLAUDE.md に `implement.complexity_hints` の deep 指定があるか？
  （CLAUDE.md の任意の場所に 1 行追記: `implement.complexity_hints: Terraform変更は常にdeep`）

**判定基準:**

| 条件 | 深度 |
|------|------|
| 上記いずれか 1 つ以上 Yes | **deep** |
| 変更が単一ファイル / docs / config のみ | **shallow** |
| それ以外 | **standard** |

深度を宣言して次のパスへ進む:

```
深度: [shallow / standard / deep]
理由: [判定根拠を 1 行で]
```

---

## shallow パス

タスク分解テーブル・準拠チェックを省略し、即実装に入る。

1. 変更内容を 1 文で宣言（例: `` `path/to/file.py` の `func_name` を修正します ``）
2. 実装
3. テスト実行（テストコマンドがあれば）
4. テスト通過 → コミット
5. 差分確認: `git diff HEAD~1 --stat` を表示して完了

→ [Step 4: テレメトリ記録](#step-4-テレメトリ記録) へ進む。Step 1〜3 はスキップ。

---

## standard / deep パス

### Step 1: タスク分解 / Step 1.5: インターフェース契約（deep のみ）

計画を読んで実装タスクに分解し、ユーザーに見せて OK をもらってから実装に入る。
**循環依存チェックはユーザーに見せる前に必ず実施する（MUST）。** deep のみ Task 0 として
インターフェース契約をユーザー承認後に残タスクへ進む（MUST）。
分解ルール・出力テンプレ・契約テンプレは [references/task-decomposition.md](references/task-decomposition.md) 参照。

---

### Step 2: モード選択と実行

**Standard モード**（タスク 5 未満 or レーン 1 つ）: 依存が解決したタスクから
Ralph Loop（マルチパス）で順に実装・テスト・コミットする。
**テストが失敗したら次のタスクに進まず修正する（MUST）。**
各タスク開始前に「タスク境界の認知分離」（スコープ・インターフェース契約・完了条件の宣言）を行い、
前タスクの実装詳細で判断が汚染されないようにする。

**Parallel モード**（タスク 5 以上 AND 独立レーン 2 以上）: Agent ツールの
`isolation: "worktree"` でレーンごとに並列実行する。
**クロスレーン depends_on がある場合は Standard モードにデグレードする（MUST）。**
別途 read-only の検証エージェントでレーン成果物をレビューする。
全レーン完了後に worktree ブランチをマージし、マージ後にフルテスト実行。

Ready/Blocked 表示・Ralph Loop の手順詳細・認知分離テンプレ・各エージェントへの委譲プロンプトは
[references/execution-modes.md](references/execution-modes.md) 参照。

---

### Step 3: 計画準拠チェック（standard / deep）

全タスク完了後、計画と実装を突き合わせる:

```
計画準拠チェック
═══════════════
| 要件 | 実装状態 | 対応ファイル |
|------|---------|------------|
| ユーザー認証 API | ✓ | src/auth.ts |
| JWT トークン検証 | ✓ | src/auth.ts:42 |
| レート制限 | ✗ 未実装 | — |

準拠率: 2/3 (67%)
```

未実装の要件がある場合、ユーザーに確認:
- 今すぐ実装する
- NOT in scope として記録
- 次のセッションに持ち越す

#### 配線先チェック（新しい仕組み・検出ロジック・自動改善を足した場合）

新機能を実装したとき、それが「いつ発火するか」を1問だけ自問する。
新しい検出・診断・スコアリング・自動改善のロジックを足したなら、配線先を確認する:

> この機能は **recurring に回るループ（evolve / audit / trigger_engine）** で発火するか？
> それとも手動 CLI・単発スキル（spec-keeper update を1回叩く等）でしか起動しないか？

手動 CLI / 単発スキル止まりだと「ユーザーが思い出して叩く」依存になり、
**実質ほとんど発火しない**（version ≠ enforcement と同型のミス）。
「仕様アーティファクトだから spec-keeper 管轄」のような *分類上の正しさ* で配線先を選ぶと、
ユーザーが滅多に回さない場所に置いてしまう。配線先は **実際に回るループか** で選ぶ。

自動で効かせたい意図があるのに recurring ループに乗っていない場合、ユーザーに提示する:

```
この機能は今 {手動 CLI / spec-keeper update} でしか発火しません。
evolve のたびに効かせるなら audit に section を足す配線が必要です。どうしますか？
```

**deep のみ**: 準拠チェック完了後、ADR 起票を推奨する。
新しいインターフェース・設計判断が含まれている場合:

```
設計判断が含まれています。ADR を起票しますか？
→ /evolve-anything:spec-keeper
```

---

### Step 4: テレメトリ記録

実装完了時に evolve-anything のテレメトリ（`usage.jsonl`）に記録する。
**下記コードは直接実行するのではなく、変数を実際の値に置き換えて実行する（MUST）。**
記録フィールド定義とコード全文は [references/telemetry.md](references/telemetry.md) 参照。

---

### Step 5: 完了報告と次のステップ

```
実装完了
═══════
深度: shallow / standard / deep
モード: Standard / Parallel ({N} レーン)
タスク: {completed}/{total}
コミット: {N} 件
テスト: 全パス / {N} 件失敗
計画準拠率: {N}%  ← shallow は N/A

変更ファイル:
  {ファイルリスト}

次のステップ:
  /review — コードレビュー（推奨）
  /qa — QA テスト（UI 変更がある場合）
  /ship — 出荷準備
```

gstack の reviews.jsonl がある環境なら、ビルドログも書く:

```bash
if command -v ~/.claude/skills/gstack/bin/gstack-review-log &>/dev/null; then
  ~/.claude/skills/gstack/bin/gstack-review-log '{"skill":"implement","timestamp":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","status":"STATUS","depth":"DEPTH","tasks_count":N,"tasks_total":N,"mode":"MODE","conformance_rate":RATE,"commit":"'"$(git rev-parse --short HEAD)"'"}'
fi
```

## エッジケース

- **テストコマンドが不明**: ユーザーに聞く。テストなしの場合は警告して続行
- **未コミットの変更がある**: 「先にコミット or stash しますか？」と確認
- **計画が 7 日以上古い**: 「計画が {N} 日前のものです。コードベースが変わっている可能性があります」と警告
- **単一ファイルの変更**: タスク数に関係なく Standard モード（並列化のメリットなし）
- **shallow 判定だが件数が多い**: テーブルを省略しても良いが、「{N} 箇所を修正します」と一覧を示す
