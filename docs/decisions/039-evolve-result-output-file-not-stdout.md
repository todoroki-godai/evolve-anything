# ADR-039: evolve の巨大 result JSON は `--output` でファイル化し stdout 一発出力をやめる

Date: 2026-06-05
Status: Accepted
Related: PR #334, [[pitfall_large_json_stdout_truncation]], [ADR-028]（observability contract — evolve 出力を多段で消費する設計）

## Context

`evolve.py:main` は `run_evolve()` の結果（result dict）全体を `print(json.dumps(result, ensure_ascii=False, indent=2))` で stdout に一発出力していた。一方 evolve SKILL.md は Stage をまたいで **15 ステップ以上**でこの単一 JSON を「evolve.py の出力に含まれる `X` フェーズを確認する」と繰り返し参照する設計（observe / fitness / discover / layer_diagnose / skill_evolve / audit issues / observability / self_analysis …）。

実機 dry-run の出力は **実測 116 KB**（フェーズ全部入り + `indent=2`）。この規模の JSON を stdout に一発で出し、SKILL がそれを読む前提だが、**ファイルへ逃がす指示がどこにも無かった**。

結果、ユーザー環境で evolve のたびに「head -200 で切れて JSON が不完全でした。全量をファイルに保存し直します」というやり直しが多発していた。原因は Claude のミスではなく **出力契約と SKILL.md のミスマッチ**:

- Claude が Bash で `evolve` を実行すると、Bash ツールの出力上限で末尾が切られる、または
- 巨大化を見越して Claude が `| head -200` を自分で挟む

→ どちらも `indent=2` の JSON を**構造の途中で**切り、invalid JSON 化 → パース失敗 → ファイル保存にフォールバックするやり直しが発生する。

## Decision

`evolve.py` に **`--output <path>`** を追加する。

- 指定時: full result JSON を `<path>` に書き、stdout には `_summarize_result()` が返す **1行サマリ** `{"output": <path>, "phases": [...], "env_tier": ...}` だけを出す（`phases` は `result["phases"]` 配下の実フェーズ名。env_score は result に存在しない〔audit セクション配下にネスト〕ため出さず、top-level に必ずある `env_tier` を surface）。
- 未指定時: 従来どおり full JSON を stdout に出す（**後方互換**）。

evolve SKILL.md を更新する:

- Step 1（`--dry-run`）と Step 7（`--confirmed-batch` 再実行）を `--output /tmp/rl_evolve_out.json` **必須**にする。
- 「evolve.py の出力に含まれる `X` フェーズを確認する」全箇所を **「`/tmp/rl_evolve_out.json` を Read（必要なら offset/limit）で参照、`| head` / `| tail` 禁止」** に統一する。

出力契約をコード側（`--output` + 1行サマリ）で固定することで、SKILL のどのステップも安定して full JSON を読める。

## Alternatives Considered

### 代替案A: SKILL.md だけ修正（`evolve … > /tmp/out.json` に書き換え）
最小コストだが、出力契約は依然「巨大 JSON を stdout に出す」ままで、別の呼び出し経路（手動実行・他スキル）が同じ罠を踏む。SKILL の散文に依存した運用回避であり、コード側の保証にならないため不採用（install ≠ enforcement の構図）。

### 代替案B: `--output`（採用）
出力先と stdout サマリをコードで固定。後方互換を保ちつつ SKILL の全消費点を1経路に揃えられる。stdout が常に1行なので「途中切断」が原理的に起きない。

### 代替案C: `indent=2` をやめて compact 1行出力にする
サイズは減るが 116 KB → 数十 KB 規模は残り、Bash 出力上限や head での切断は解消しない。本質は「巨大出力を stdout に流すか/ファイルにするか」であり、compact 化は補助にしかならないため単体では不採用。

## Consequences

- evolve 実行中の「JSON が不完全 → 保存し直し」のやり直しが解消（stdout は常に1行サマリ）。
- 後方互換維持: `--output` 未指定なら従来の full JSON stdout のまま。既存の他経路は無改修で動く。
- SKILL の全フェーズ参照が `/tmp/rl_evolve_out.json` の Read に一本化され、`| head`/`| tail` を挟む余地が消える。
- 決定論・LLM 非依存（`_summarize_result` は result のキーから固定構造を組むだけ）。`no-llm-in-tests.md` に抵触しない。
- 将来 evolve の出力に新フェーズを足しても、SKILL は同じファイルを読むだけで追従できる。
