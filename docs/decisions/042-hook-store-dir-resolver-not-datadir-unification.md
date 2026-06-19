# ADR-042: hook-writer 系ストアは reader 側 resolver で正準 dir を解決する（DATA_DIR 一元化はしない）

- Status: Accepted
- Date: 2026-06-08
- Issue: #358（prune が全スキルを zero_invocation と誤判定）
- Related: #360（optimize_history 空の調査で同根を疑った件）, [ADR-031](031-*) / [ADR-037](037-*), pitfall `pitfall_datadir_hook_tool_split`

## 背景（症状）

prune スキルが「全スキルが zero_invocation（テレメトリ上一度も呼ばれていない）」と
誤判定する。実際にはスキルは活発に使われている。

## 根本原因

`scripts/lib/rl_common/__init__.py` の DATA_DIR 解決が実行コンテキストで食い違う:

```python
DATA_DIR = Path(env) if (env := os.environ.get("CLAUDE_PLUGIN_DATA")) else Path.home()/".claude"/"evolve-anything"
```

- **hook 実行時**: CC が `CLAUDE_PLUGIN_DATA` を設定 → `~/.claude/plugins/data/evolve-anything-evolve-anything/`（plugin-data dir）に書く
- **standalone tool/skill 実行時**: env 未設定 → fallback `~/.claude/evolve-anything/` に解決

結果、import 時に凍結される DATA_DIR が実行コンテキストで分岐し、**ストアごとに
「生きているディレクトリ」が分かれた**:

| ストア | 正準 dir | writer |
|---|---|---|
| usage.jsonl / skill_activations.jsonl / sessions / subagents / tool_durations / workflows | plugin-data | hook |
| corrections / evolve-state / audit-history / eval-sets / episodic.db / evolution_memory | fallback | tool/skill |

prune（tool）は usage を fallback（168 stale）から読み、hook が plugin-data に書いた
live（1846）を取り逃すため、全スキルが未使用に見える。

## 検討した選択肢

### A. DATA_DIR 全体一元化 + migration（当初案、却下）

env 未設定時に plugin-data dir を使うよう DATA_DIR を一本化し、fallback → plugin-data へ
全データを migration する。

却下理由（実データ計測で判明）:
- tool/skill 系ストア（corrections / evolve-state 260K live / audit / eval-sets / episodic）は
  **fallback にしか無い**。DATA_DIR を一斉スイッチすると、それらが一瞬空に見え
  evolve/audit が壊れる（12K stale state を読む等）新規 breakage を生む。
- 両 dir に **10GB + 2.2GB の DuckDB sessions.db** が live で割れており、実マージは
  遅い・壊れるリスクが大きい。
- plugin-data dir は reinstall で wipe される既知リスクがあり、正準先として脆い。

### B. hook-writer 系ストア限定の reader resolver（採用）

「reader は writer の dir を読む」を最小 blast radius で実現する。hook が書く
ストア（usage / skill_activations）の **読み取り経路のみ** を正準化し、tool/skill 系
ストアの解決（fallback）は一切触らない。migration もしない。

`scripts/lib/rl_common/store_paths.py` の `hook_store_path(filename, base=None)`:

1. `base`（既定 = `rl_common.DATA_DIR`）が既定 fallback **以外** なら最優先で尊重
   （hook の凍結 DATA_DIR=plugin-data / custom 環境 / テストの DATA_DIR patch）。
   env より優先することでテスト isolation（conftest が `CLAUDE_PLUGIN_DATA=tmp_path`
   を強制）下でも個別テストの `audit.DATA_DIR` patch を壊さない。
2. base が既定 fallback のとき `CLAUDE_PLUGIN_DATA` env を見る（hook 実行）。
3. それも無ければ install レイアウト `~/.claude/plugins/data/<*evolve-anything*>` を
   決定論で探索し、hook が書いた dir を回収（tool/skill 実行）。
4. 探索失敗時は base（既定 fallback）を返す（後方互換・graceful degrade）。

配線箇所（usage / skill_activations の reader default のみ）:
`audit/usage.py:load_usage_data` / `skill_usage_stats.py`（5 default）/
`discover/patterns.py`（2）/ `telemetry_query/usage_errors.py`（usage 2 default）。

## 決定

**B を採用。** hook-writer 系ストアは reader 側 resolver で正準 dir を解決する。
DATA_DIR の一元化・migration は行わない。

## トレードオフ / 既知の限界

- 「ストアごとに正準 dir が違う」現状を**仕様として固定**する（混乱の余地は残るが、
  用途別に安定して分散しているため低リスク）。
- resolver は plugin-data の install レイアウト命名（`<marketplace>-evolve-anything`）に
  依存する。プラグイン名 `evolve-anything` を含む dir を mtime 降順で 1 つ選ぶ。
- errors.jsonl（両 dir に split）は本 fix の対象外（usage/skill_activations に限定）。

## Phase 2（別 issue・後続）

全体 DATA_DIR 一元化は、ユーザーが「両 dir 管理が煩雑」と判断したときに別 epic で
計画的に行う。その際は **reinstall 耐性のため plugin-data → fallback の逆 migration**
（fallback は再 clone でも残るが plugin-data は uninstall で wipe）+ dry-run + 冪等を
満たす設計とする。本 ADR はその前提（現状の分散マップ）を記録する役割も持つ。
