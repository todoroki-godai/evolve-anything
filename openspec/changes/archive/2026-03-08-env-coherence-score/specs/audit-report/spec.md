## MODIFIED Requirements

### Requirement: /audit レポートに Coherence Score セクションを含めなければならない（MUST）
audit レポートに `--coherence-score` オプションが指定された場合、`compute_coherence_score()` を呼び出し、"## Environment Coherence Score" セクションをレポートに追加しなければならない（MUST）。`--coherence-score` 未指定時はセクションを表示してはならない（MUST NOT）。Coherence Score セクションは既存セクションの先頭（## Skill Quality Trends の前）に表示する。

#### Scenario: --coherence-score 指定時のセクション表示
- **WHEN** `/rl-anything:audit --coherence-score` を実行し、coherence スコアが overall=0.85, coverage=1.0, consistency=0.7, completeness=0.9, efficiency=0.8 の場合
- **THEN** レポートに "## Environment Coherence Score" セクションが含まれ、以下のフォーマットで overall スコアと4軸の内訳が表示される:
  ```
  ## Environment Coherence Score: 0.85
  Coverage:     1.00 ████████████████████
  Consistency:  0.70 ██████████████░░░░░░ ← CLAUDE.md に skill-x が記載されているが実在しない
  Completeness: 0.90 ██████████████████░░
  Efficiency:   0.80 ████████████████░░░░
  ```

#### Scenario: --coherence-score 未指定時
- **WHEN** `/rl-anything:audit` を実行する（`--coherence-score` なし）
- **THEN** レポートに "## Environment Coherence Score" セクションは含まれない

#### Scenario: 低スコア軸への改善アドバイス表示
- **WHEN** いずれかの軸スコアが 0.7 未満の場合
- **THEN** Coherence Score セクション内に、0.7 未満の軸ごとに fail したチェック項目を箇条書きで表示する
