# correction_semantic 意味判定 drain 詳細（Step 6.6・2相 Phase A→B, #431/#339）

`correction_semantic`（バッチ LLM 意味判定・[ADR-037] と同型の 2 相構成）は Phase A
（`emit_judgement_requests`・決定論・LLM 非呼び出し）だけが `phases_capture.py` から
常時呼ばれており、Phase B（Haiku 判定）と Phase C（`ingest_judgement_results`）に
production 呼び出し元が無かった（#339）。Phase B は本質的に対話的（assistant が
インラインで応答を生成する）ため、非対話の `evolve --drain` 単体では実行できない。
このステップでは Phase A→B をここで行い、Phase C は **Step 7.8 の `evolve --drain`**
（apply 境界・他の apply 系書込 = weak_signals_persisted 等と同型）に委ねる。

auto-memory-drain（Step 6.5）と違い、本ステップは Haiku バッチ呼び出しのため
**llm-batch-guard**（件数・見積もりトークンを事前提示して y/n 承認）を通す（MUST）。

## Phase A（リクエスト生成 — claude -p なし）+ llm-batch-guard 事前確認

```python
import os, sys
from pathlib import Path
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
import rl_common
from correction_semantic import batch as cs_batch

slug = rl_common.project_name_from_dir(os.environ.get("CLAUDE_PROJECT_DIR", ""))
emitted = cs_batch.emit_judgement_requests(slug)

if emitted["unjudged"] == 0:
    print("correction_semantic 意味判定: 0件 ✓")  # 沈黙≠評価。ここで終了
else:
    utterances = [
        u for r in emitted["requests"] for u in (r.get("meta") or {}).get("utterances", [])
    ]
    est = cs_batch.estimate_tokens(utterances)
    print(
        f"未判定 {emitted['unjudged']}件 / {emitted['batches']}バッチ "
        f"/ 概算 {est['est_total_tokens']}トークン（モデル: Haiku）"
    )
```

`unjudged == 0` なら本ステップはここで終了する。`unjudged > 0` なら AskUserQuestion で
1問（MUST）:

- 質問: 「correction_semantic 意味判定を実行しますか？（未判定 N 件・概算 M トークン・Haiku）」
- options: 「実行する」（`detail`: 「Phase B でインライン判定 → Step 7.8 の drain で反映」）/
  「スキップ」（`detail`: 「次回 evolve で再度この件数が emit される」）

## Phase B（インライン判定 → responses ファイル保存）

「実行する」が選ばれたら、各 `emitted["requests"][i]["prompt"]` を読み、
`{"verdicts": [{"index": int, "is_correction": bool, "idiom": str|None, "reason": str}, ...]}`
形式の JSON を **インラインで**（`claude -p` を呼ばない）生成する。プロンプトは
バッチ内の全 index について verdict を返すよう指示している（欠落は「非修正」扱いになる
契約・`ingest_judgement_results` の docstring 参照）。

```python
responses = {}  # {request_id: 生成した JSON テキスト}
for req in emitted["requests"]:
    ...  # req["prompt"] を読みインラインで判定し responses[req["id"]] に JSON テキストを格納

import json
resp_path = Path(f"/tmp/rl_correction_responses_{slug}.json")
resp_path.write_text(json.dumps(responses, ensure_ascii=False), encoding="utf-8")
print(f"correction_semantic responses: {resp_path}")
```

**Phase C（ingest）はここでは呼ばない。** Step 7.8 の `evolve --drain` に
`--correction-responses "$(<resp_path の絶対パス>)"` を追加して初めて実行される
（apply 境界に書込を集約する設計・#339）。「スキップ」を選んだ場合は `resp_path` を
作らず、Step 7.8 のコマンドにも `--correction-responses` を付けない。

## Step 7.8 との合流

本ステップで responses ファイルを書いたときだけ、Step 7.8 の drain コマンドに
1 フラグを追加する:

```bash
OUT="$(evolve --project-dir "$(pwd)" --print-out-path)"
evolve --drain --result-json "$OUT" --correction-responses /tmp/rl_correction_responses_<slug>.json
```

drain 側は `emit_judgement_requests(slug)` を再実行して `emitted` を再構成し
（utterances.db・判定進捗が本ステップ実行時から変化していない前提・決定論なので
同一入力なら同一結果）、`ingest_judgement_results(emitted, responses, dry_run=False)`
を呼んで weak_signals（channel=llm_judge）+ 個人辞書 + 判定進捗（`correction_judged.jsonl`）
へ確定させる。結果（corrections/non_corrections/weak_written/idioms_written/judged_written）
は drain サマリの `correction_semantic_persisted` に載る。Report にこの件数を報告する。

`--correction-responses` 未指定・ファイル不読・不正 JSON はいずれも
`correction_semantic_persisted: {"skipped": "<理由>"}` で graceful skip し、他の
drain persist は継続する（`--result-json` の graceful degradation と同型）。
