# 単体テストで LLM を呼ばない
- 単体テストで `subprocess.run(["claude", ...])` / anthropic SDK / openai SDK を直接呼ぶことは禁止。必ず mock する
- LLM を呼ぶプロダクトコードに対するテストは、対象関数または `subprocess.run`/`subprocess.Popen` を mock する
- conftest.py の guard が claude CLI 呼び出しを検出して RuntimeError を投げる。回避は integration テストのみ `RL_ALLOW_LLM_IN_TESTS=1`
- mock のレイヤーは「テストする層の1つ下」を選ぶ。中間関数 (例: `score_variant`) を mock しても、本流が `_score_variant_axes` を呼ぶなら効かない。実装の call graph を読んで mock 位置を選ぶ
