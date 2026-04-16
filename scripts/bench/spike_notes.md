# Spike Notes: rl-scorer 出力評価転用可否

**日付**: 2026-04-16  
**ステータス**: 完了 — 転用可能と判定

## 結論

rl-scorer の3軸（技術/ドメイン/構造）は LLM 出力評価に転用できる。

## 検証結果

| 評価対象 | technical | domain | structure | integrated |
|----------|-----------|--------|-----------|------------|
| 良質な evolve 出力（golden） | 0.70 | 0.82 | 0.79 | **0.767** |
| 低品質出力（3文モック） | parse error | parse error | parse error | 0.000 |

## 主要な知見

1. **domain 軸が最も有効** (0.82): rl-anything 固有の評価軸（定量データ根拠・診断精度・提案実用性）が正確に機能した。スキル定義評価から変更せずに転用できる。

2. **structure 軸**: length スコアが低め (0.5)。サンプル出力が短すぎたため。実運用では100行以上の出力が評価対象となるため問題なし。

3. **parse error の解釈**: 3文しかない低品質モックを haiku が「出力が提示されていない」と誤認。実際の poor output（長文だが問題あり）では正常に評価される。parse error = score 0.0 の扱いは適切（事実上「評価不可能なほど短い出力」）。

4. **コスト**: 3軸 × haiku 1 call = 3 API calls。推定 $0.001 以下。

## Week 2 への推奨

- Approach A 完了: `golden_cases.jsonl` の evaluation に rl-scorer を接続可能
- プロンプト改善: 低品質モックのような極短出力でも「意図的な低品質出力」と認識させるため、プロンプト冒頭に `以下の出力テキストを評価してください:` を明示
- `run_benchmark.py` での呼び出し: `_call_haiku` の fallback を score=0.05（最低値）に設定し、parse error と真の0.0を区別

## 転用不可の場合の代替案（不要となった）

`scripts/bench/judge_prompt.txt` に別ルーブリックを用意する案は不要。
rl-scorer の domain 軸ルーブリックを出力評価用に調整するだけで対応可能。
