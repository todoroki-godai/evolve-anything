# ADR-020: 哲学原則を SEED_PRINCIPLES 経由で配布する

Date: 2026-04-15
Status: Accepted
Related: philosophy-review スキル新設、Karpathy 4原則導入

## Context

`philosophy-review` スキル新設に伴い、Karpathy 4原則（think-before-coding / simplicity-first / surgical-changes / goal-driven-execution）を `category: "philosophy"` でプラグインに組み込む必要が生じた。

design doc では当初「`.claude/principles.json` に手動追加」する方針を取った。実装着手後、以下が判明:

- `.claude/principles.json` は CLAUDE.md + rules から LLM 抽出した結果の **runtime cache** である
- これまで一度も git commit されていない（`.gitignore` 対象ではないが慣習的に untracked）
- 一方 `SEED_PRINCIPLES` (`scripts/rl/fitness/principles.py`) はコード hardcoded で、コード経由で全環境に配布されている

design doc の方針だと、Karpathy 原則が **他開発者・自分の他 PC 環境に配布されない** ため、`philosophy-review` スキル本体（`category=philosophy` でフィルタ）が空の評価対象に対して動作することになる。これは機能として破綻している。

senior-engineer に相談し、配布チャネルとしての SEED の利用が妥当との結論を得た。

## Decision

**Karpathy 4原則を `SEED_PRINCIPLES` (principles.py) に hardcode して配布する。**

具体的には以下を実施:

1. `SEED_PRINCIPLES` 配列に4件追加（`seed: true`, `category: "philosophy"`, `source: "seed"`）
2. `.claude/principles.json` への手動追加は取り消し（runtime cache に戻す）
3. `openspec/specs/principle-extraction/spec.md` の seed セクションを「数値固定」（5つ）から「カテゴリ別構造」（コア + philosophy）に再構造化。将来の seed 追加時に spec 更新が不要な構造とする

## Rationale

- **配布チャネルの一致**: 既存5つのコア seed と同じチャネルで配布される
- **普遍性**: Karpathy 4原則は PJ 固有でないコーディング哲学であり、seed の性格と一致
- **上書きリスク回避**: `.claude/principles.json` を commit する案は他開発者の同名ファイルを上書きする懸念があった
- **意味的正しさ**: `user_defined: true` ではなくプラグイン同梱の `seed: true` が原則の出自を正確に表す
- **spec の保守性**: 数値固定（「5つ」）から構造化記述に変更し、将来の seed 追加時に二重管理を避ける

## Consequences

### Positive
- philosophy-review スキルが任意の rl-anything インストール環境で機能する
- seed 追加時の spec 修正コストが減る（カテゴリ単位記述）
- principles.json は引き続き runtime cache の役割に専念

### Negative
- Karpathy 原則のテキスト編集にコード変更（principles.py）が必要
- ユーザーが個人で哲学原則を追加する場合は依然として `.claude/principles.json` の `user_defined: true` 経由となり、配布されない（現状の seed 仕組みと同じ制約）

## Alternatives Considered

- **A. `.claude/principles.json` を 1 度だけ commit**: runtime cache を git 管理するのは本質的に間違い。`--refresh` で `user_defined: true` 以外が消えるリスクが残る
- **C. 個人環境のみ（配布せず）**: philosophy-review スキル本体は配布されるため、評価対象が空になり機能破綻
