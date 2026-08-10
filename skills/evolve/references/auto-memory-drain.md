# auto-memory キュー drain 詳細（Step 6.5・2相・[ADR-037] Phase 2）

Stop hook（auto_memory_runner）は corrections を生成前ゲートして PJ スコープキュー
`DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue するだけのゼロ LLM 化済み。
LLM 生成・生成後ゲート（belief_entropy）・memory 書き込みはここで assistant が
ファイルベース2相（emit→インライン→ingest）で消化する。reflect Step 5.5 と同じ書式。

## Phase A（リクエスト生成 — claude -p なし）

キューを読んで各 prompt を出力する。空なら「auto-memory キュー: 0 件 ✓」（沈黙≠評価）で本ステップを終了する。

```python
import os, sys
from pathlib import Path
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
import rl_common, auto_memory_broker

# result は Step 1 で Read した $OUT の JSON。env CLAUDE_PROJECT_DIR は未設定/空文字になりうる
# うえ、単一 cwd から他 PJ の project_dir を渡すバッチ経路（#400）では実行元 PJ を指してしまう。
slug = rl_common.project_name_from_dir(result["project_dir"])
records = auto_memory_broker.read_queue(slug, rl_common.DATA_DIR)
if not records:
    print("auto-memory キュー: 0 件 ✓")  # 沈黙≠評価。ここで終了
else:
    emit = auto_memory_broker.emit_memory_requests(records)
    for r in emit["requests"]:
        print(r["id"], "\n", r["prompt"], "\n---")  # Phase B でこの prompt にインライン回答（subscription 課金）
```

## Phase B→C（インライン生成 → 回収 → 反映）

`requests` が非空なら各 prompt を読み、memory frontmatter v2 形式のエントリをインラインで生成し
（claude -p を呼ばない）、`responses = {request_id: 生テキスト}` を組んで再 emit（決定論・冪等）して ingest する:

```python
emit = auto_memory_broker.emit_memory_requests(records)  # 同一結果（決定論）
memory_dir = Path.home() / ".claude" / "projects" / slug / "memory"
memory_md_path = memory_dir / "MEMORY.md"  # index は entry .md と同じ memory/ 内（相対リンク成立のため）
summary = auto_memory_broker.ingest_memory_results(
    records, emit["requests"], responses,
    memory_dir, memory_md_path, rl_common.DATA_DIR,
)
print(f"auto-memory: stored={summary['stored']} blocked={summary['blocked']} skipped={summary['skipped']}")
```

- ingest が生成後ゲート（belief_entropy）を内蔵: ソースを落とした要約は書込なしで `belief_blocks.jsonl` に記録（blocked にカウント）
- 空応答（skipped）はキューに残り次回 drain で再試行される。stored/blocked は消化される
- 結果（stored/blocked/skipped）を Report に報告する
