Related: #33

## Context

verification_catalog は「プロジェクト特性に応じた検証ルールの自動提案」を担うモジュールで、現在 data-contract / side-effect / evidence / cross-layer の4パターンを持つ。discover がこれらを検出し、evolve/remediation 経由でルール提案する。

本変更は5番目のパターン「ハッピーパステスト欠落検出」を追加する。パイプライン/オーケストレーションコードに対して正常系E2Eテストが存在するかを静的解析で判定する。

## Goals / Non-Goals

**Goals:**

- パイプライン関数（複数ステップを順次呼び出す関数）を regex で検出する
- 対応テストファイルでパイプライン関数名を含むテストの有無を判定する
- 検出結果を verification_catalog の標準インターフェースで返す
- RECOMMENDED_ARTIFACTS に `test-happy-path-first` を追加し、ルール未導入PJに提案する

**Non-Goals:**

- テストの内容・網羅性の深い解析（AST解析やカバレッジ連携）
- テスト自動生成
- Python/TypeScript 以外の言語サポート（将来拡張可能な設計にはする）

## Decisions

### D1: パイプライン検出手法 — regex パターンマッチ

パイプラインコードを以下のパターンで検出する:

- **Python**: 関数内で3つ以上の `step_*()` / `phase_*()` / `stage_*()` / `layer_*()` / `process_*()` 呼び出し、または `for step in steps` ループパターン
- **TypeScript**: camelCase 命名パターン（`stepValidate()`, `phaseInit()`, `stageProcess()` 等）+ `await` チェーン。regex: `await\s+(?:step|phase|stage|layer|process)\w+\(`

**代替案**: AST 解析で正確に関数呼び出しグラフを構築する → 過度に複雑、verification_catalog の他パターンとの一貫性を欠く。regex ベースで confidence 上限 0.7 とし LLM エスカレーションで補完する既存パターンに従う。

### D2: テスト欠落判定 — テストファイル内のパイプライン関数名検索

パイプライン関数が検出されたソースファイルに対応するテストファイル（`test_*.py` / `*_test.py` / `*.test.ts`）を探し、パイプライン関数名がテスト内で呼び出されているかを確認する。呼び出しがなければ「ハッピーパステスト欠落」と判定する。

テストファイルの探索範囲:
1. ソースファイルと同ディレクトリ
2. ソースファイル親の `tests/` サブディレクトリ
3. プロジェクトルート直下の `tests/` ディレクトリ（Python）
4. ソースファイル親の `__tests__/` サブディレクトリ（TypeScript）

**注意**: テストファイルにパイプライン関数名が存在しても、それが正常系テストか異常系テストかまでは判定しない（regex の限界）。この曖昧性は `llm_escalation_prompt` で補完する。

### D3: 閾値定数 — HAPPY_PATH_MIN_PATTERNS = 2

パイプライン関数の検出閾値。2以上でないと単発のユーティリティ関数との誤検出が多い。他カタログエントリ（MIN_PATTERNS=3）より低くしているのは、パイプラインコードの出現頻度が相対的に低いため。

### D4: RECOMMENDED_ARTIFACTS エントリ — path ベース検出

`test-happy-path-first` ルールの導入済みチェックは `~/.claude/rules/test-happy-path-first.md` のパス存在で判定する（既存の commit-version 等と同じパターン）。

## Risks / Trade-offs

- **[偽陽性]** 複数関数を呼び出すだけのヘルパー関数をパイプラインと誤検出する可能性 → confidence 上限 0.7 + LLM エスカレーションで緩和
- **[偽陰性]** 独自の命名規則（`run_step1`, `execute_phase_a` 等）は検出できない → 汎用性を優先し、将来的にプロジェクト固有パターンの学習で対応可能
- **[パフォーマンス]** 既存の `_iter_source_files()` を共用するため追加コストは軽微。テストファイル走査が加わるが、パイプライン検出数に比例するため bounded
