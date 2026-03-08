Related: #21

## Why

現状の fitness 評価は Skill テキストのキーワードマッチのみ（`skill_quality.py`）で、環境全体（CLAUDE.md / Rules / Skills / Memory / Hooks）の構造的な整合性を測定できない。ロードマップ Gap 1 Phase 0 として、LLM コストゼロの Coherence Score を実装し、全ての進化メカニズムの基盤となる「環境品質のベースライン」を確立する。

## What Changes

- `scripts/rl/fitness/coherence.py` を新規作成: Coverage / Consistency / Completeness / Efficiency の4軸で環境構造スコア（0.0〜1.0）を算出
- `audit` スキルに `--coherence-score` オプションを追加: 既存レポートに Coherence Score セクションを統合表示
- 既存の `audit.collect_issues()` / `skill_quality.py` / `hardcoded_detector.py` / `skill_triggers.py` を再利用し、新規ロジックは最小限に抑える

## Capabilities

### New Capabilities
- `coherence-score`: 環境全体の構造的整合性を4軸（Coverage/Consistency/Completeness/Efficiency）で測定する fitness 関数

### Modified Capabilities
- `audit-report`: Coherence Score セクションの追加（4軸スコア + 詳細チェック結果の表示）

## Impact

- 新規ファイル: `scripts/rl/fitness/coherence.py`, テスト
- 変更ファイル: `skills/audit/SKILL.md`（`--coherence-score` オプション追加）, audit 実行スクリプト
- 依存: 既存の `audit.collect_issues()`, `skill_quality.py`, `hardcoded_detector.py`, `skill_triggers.py`, `reflect_utils.py`
- Issue: [#15](https://github.com/todoroki-godai/evolve-anything/issues/15)
