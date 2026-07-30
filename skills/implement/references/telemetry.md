# テレメトリ記録（Step 4）

実装完了時に evolve-anything のテレメトリに記録する。
**「下記 Python は直接実行するのではなく、変数を実際の値に置き換えて実行する（MUST）」は SKILL.md 側に残してある。**
ここは記録するレコードのフィールド定義とコード。

```python
import json, os, datetime

data_dir = os.environ.get("CLAUDE_PLUGIN_DATA", os.path.expanduser("~/.claude/evolve-anything"))
os.makedirs(data_dir, exist_ok=True)

record = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "skill": "implement",
    "project": os.path.basename(os.getcwd()),
    "depth": DEPTH,                      # "shallow" / "standard" / "deep"
    "tasks_total": TASKS_TOTAL,
    "tasks_completed": COMPLETED_IDS,    # list[str] e.g. ["T1","T2"] — 完了タスク ID 一覧
    "tasks_count": len(COMPLETED_IDS),   # int — 集計用（後方互換）
    "mode": MODE,                        # "standard" or "parallel" (shallow は "shallow")
    "conformance_rate": CONFORMANCE,     # shallow は None
    "lanes": LANES,
    "outcome": OUTCOME,                  # "success" / "partial" / "blocked"
}
with open(os.path.join(data_dir, "usage.jsonl"), "a") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")

if OUTCOME in ("success", "partial"):
    journal = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "type": "implementation",
        "source": "implement-skill",
        "depth": DEPTH,
        "tasks_completed": COMPLETED_IDS,   # list[str]
        "tasks_count": len(COMPLETED_IDS),
        "conformance_rate": CONFORMANCE,
        "mode": MODE,
        "phase": "unknown",
    }
    with open(os.path.join(data_dir, "growth-journal.jsonl"), "a") as f:
        f.write(json.dumps(journal, ensure_ascii=False) + "\n")
```

**上の Python は直接実行するのではなく、変数を実際の値に置き換えて実行する。**
