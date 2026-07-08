# 型スキル焼き戻しパイロット（手書きログ・ゼロコード）

> **目的**: cross-PJ 蒸留 Phase 1-3（自動化）を作る前に、「型スキルの存在を可視化したら、実際に型スキルへ**焼き戻す行動**が起きるか」を手作業で実測する。
> **背景**: この環境は変換率 4.3%・write-only advisory 18個の「観測厚く作用薄い」体質。Phase 1-3 はどれも観測機械を足す側なので、先に「作用に変換されるか」を安く検証する（[[cross-pj-distillation-concept]] §5 over-eng 点検結論）。

## パイロット設計

- **開始**: 2026-07-07
- **中間チェック**: 2026-07-21（2週）
- **集計**: 2026-08-04（4週・アウターバウンド）
- **対象型スキル**（3個）と各インスタンス PJ:
  - `evaluation-loop-method` ← explainer-script-method / figma-to-code(figma-eval-expert)
  - `correction-to-permanent-ledger` ← explainer / figma / evolve-anything(pitfall-curate)
  - `ground-truth-over-summary` ← explainer / figma / evolve-anything(representative)
- **やること**: 上記いずれかの PJ で作業中に correction / 修正指摘に気づいたら、下の観測ログに **1行**足すだけ。「この修正は型スキル X に関連しそう」で十分（厳密判定不要）。
- **測る指標**: 期間内に **型スキルの SKILL.md を実際に焼き戻し編集した回数**（焼き戻し = 型スキル本文へ知見を反映する編集）。
- **判定**:
  - **0 回** → 「可視化しても焼き戻し行動は起きない」＝ Phase 1 の自動化は無意味と確定。蒸留は Phase 0 の3個で運用継続、機械化は打ち切り。
  - **1 回** → グレー。中間チェックで継続 or 打ち切り判断。
  - **2 回以上** → 焼き戻し需要あり。Phase 1 帰属レーン or L4+ 配線の投資対効果を再検討（速断しない・負例コーパス等の前提は別途）。

## 観測ログ（気づくたび1行追記）

| 日付 | PJ | correction / 指摘の概要 | 関連しそうな型スキル | 焼き戻した？ |
|---|---|---|---|---|
| （例）2026-07-08 | evolve-anything | dry-run で store 書込を誤ゲート | ground-truth-over-summary | N（気づいただけ） |

## 焼き戻しログ（型スキル SKILL.md を実際に編集したら追記）

| 日付 | 型スキル | 反映した知見 | きっかけ（上の観測ログ行 or 直接） |
|---|---|---|---|
| | | | |

## 集計（4週後に記入）

- 観測ログ行数: __
- 焼き戻し回数: __
- 判定: __
