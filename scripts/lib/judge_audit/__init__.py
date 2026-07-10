"""judge_audit — LLM judge の false-pass 欠陥注入監査（#188, The Blind Curator arXiv 2607.07436）。

自己進化のスキル退役（`audit/outcome_attribution.py` の `apply_outcome_ranking` が
negative_transfer フラグで駆動する rollback）は、失敗を正しく見抜く LLM judge に依存する。
judge が false-pass（失敗を合格と誤判定）を出すと、退役は無音で無効化され、しかもどの集計
指標にも表れないサイレント故障になる。本モジュールは既知の欠陥タスク fixture（正解=失敗と
分かっている入力）を judge の実プロンプト（`scripts/rl/fitness/constitutional.py` の
`_build_eval_prompt` / `_parse_layer_response` を再利用）に流し、合格(false-pass)と誤判定する
割合を計測する opt-in CLI ハーネスである。

verbosity（#75）と同型の分離パターン:
- `fixtures.py`: 決定論の欠陥タスク（LLM 生成しない・正解=違反ありが既知）。
- `harness.py`: dry-run 既定（llm-batch-guard 準拠）。`--run` で judge を呼び、
  verdicts を store_write barrier 経由で永続化する。subprocess は 1 箇所（`call_judge_llm`）
  に集約し単体テストで mock する（no-llm-in-tests 完全整合）。
- `store.py` / `query.py`: 読み書き分離・floor ゲート付き集計。
- `audit/sections_judge_audit.py`: 決定論 read-only の advisory section（重み非反映）。

PJ スコープ（verbosity/subagent_traces と同型）。
"""

# judge の「合格（違反なし）」判定閾値。constitutional.py の評価プロンプト自身の rubric
# （"violations should be empty list if score >= 0.8"）と単一の基準に揃える。ここで
# 閾値を捏造せず、既存 judge の rubric が定義する合格ラインをそのまま再利用する。
PASS_THRESHOLD = 0.8
