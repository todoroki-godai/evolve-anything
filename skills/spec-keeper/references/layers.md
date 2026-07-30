# spec-keeper Progressive Disclosure レイヤー詳細

SKILL.md から参照される。init / update / status コマンドのレイヤー判定・肥大化チェックで使用する。

## コンセプト: 層構造

```
README.md              <- 外部向け（人間ファースト）。インストール・使い方・主要コマンド
CLAUDE.md               <- 動作ルール（ガードレール、スキル、規約）。AI 向け
SPEC.md                <- 現在の仕様全体像（AI がセッション開始時に読む）。AI 向け詳細
CONTEXT.md              <- Ubiquitous Language（用語集）。PJ 固有 jargon を 1 語で decode（任意）
docs/decisions/         <- ADR（設計判断の「なぜ」を記録）
~/.gstack/projects/     <- セッション単位の設計ドック（gstack 管理）
```

README.md は「そのプロジェクトが何者か（外部視点）」、CLAUDE.md は「どう動くか（AI 行動ルール）」、SPEC.md は「今何ができるか（AI 詳細仕様）」、CONTEXT.md は「この PJ の用語は何を指すか」、ADR は「なぜそうなったか」。CONTEXT.md は存在する場合のみ管理対象（`glossary_drift.py` で鮮度検出）。

README.md と SPEC.md は **同じ情報を重複させない**。README.md はユーザーが GitHub で最初に見る薄い入口で、内部実装・ADR・アーキテクチャ詳細は SPEC.md / docs/ に委譲する。

## Progressive Disclosure レイヤー

SPEC.md は AI がセッション開始時に全量読むドキュメント。Context rot 研究により、無関係だが正しい情報が増えるほど LLM の出力品質が劣化することが実証されている。PJ 規模に応じて適切なレイヤー構成を取る。

### レイヤー定義

| Layer | 構成 | SPEC.md 目安 | 対象PJ |
|-------|------|-------------|--------|
| **L1** | SPEC.md のみ | ~100行以下 | 個人ツール、Bot、小さい API |
| **L2** | SPEC.md (hot) + spec/ (cold) | hot ~60行 | Plugin、SaaS バックエンド |

L3（ドメイン別3層）は該当PJ出現時に検討。

### 閾値

**chars（bytes, `wc -c`）が主指標、行数は補助指標。** 根拠: Read ツールの実質上限（≈25K tokens）を超えると丸読みで truncate される実害があり、行数だけでは「読める量」を代表しない（1行あたりの情報量が巨大な md は行数基準では健全に見えてしまう。issue #216）。

**単一ファイル閾値（hot/cold 問わず・最優先指標）**

| 指標 | Healthy | Caution | Action |
|------|---------|---------|--------|
| 単一 md ファイル bytes | ~50KB以下 | 50-100KB | **>100KB: 分割必須** |

**hot（SPEC.md）専用閾値**

| Layer | 指標 | Healthy | Caution | Action |
|-------|------|---------|---------|--------|
| L1/L2 | SPEC.md bytes | ~20KB以下 | 20-35KB | >35KB: cold へセクション移動 |
| L1 | SPEC.md 行数（補助） | ~80行以下 | 81-100行 | >100行: L2 昇格を提案 |
| L2 | hot 行数（補助） | ~60行以下 | 61-80行 | >80行: cold へセクション移動 |

**cold（spec/ 配下）**

cold 合計の行数閾値（旧: >300行で L3 検討）は廃止。各 cold ファイルは上記「単一ファイル閾値」（bytes）で個別判定する。L3（ドメイン別3層）は該当PJ出現時に検討。

**巨大 cold ファイルの消費ルール**: >50KB の cold ファイルは丸読みせず、Grep で該当箇所を特定してから部分 Read で消費する。該当ファイルの冒頭に消費ルール1行（例: 「本ファイルは大きいため Grep→部分 Read で読むこと」）を置く。

### レイヤー判定

- `spec/` ディレクトリが存在する → L2
- 存在しない → L1

### L1 → L2 昇格手順（update 中に提案、承認で即実行）

1. `spec/` ディレクトリを作成
2. 最も行数の多いセクション（通常 Architecture）の詳細を `spec/architecture.md` に移動
3. SPEC.md にサマリー + ポインタを残す（`references/templates.md` の Layer Split Guide 参照）
4. 次に大きいセクションも同様に（hot が 60行以下になるまで）
5. CLAUDE.md の Specification セクションに `- 詳細仕様: [spec/](spec/)` を追加

### 原則

- **ポインタ > インライン** — 詳細はファイルパスで参照し、エージェントが必要時に Read する
- **全タスクに必要な情報のみ hot に残す** — 特定タスクにしか使わない情報は cold へ
- **ツールに委譲できることは書かない** — linter/テストで保証できることは SPEC.md に不要
