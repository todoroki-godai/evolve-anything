---
name: audit
effort: medium
disallowed-tools: [Edit, Write, MultiEdit]
description: |
  Run an environment health check: inventory all skills/rules/memory, check line limits,
  aggregate usage stats, and generate a one-screen report with Scope Advisory.
  Trigger: audit, 健康診断, health check, レポート, report, 棚卸し
---

# /rl-anything:audit — 環境の健康診断

全 skills / rules / memory の棚卸し + 行数チェック + 使用状況集計 + Scope Advisory を含む1画面レポートを出力する。

## Usage

```
/rl-anything:audit [project-dir]
```

## 実行手順

### Step 0.5: Coherence Score / Telemetry Score（オプション）

`--coherence-score` が指定された場合、環境全体の構造的整合性スコアを算出してレポート先頭に表示する:

```bash
rl-usage-log "audit"
rl-audit "$(pwd)" --coherence-score
```

4軸（Coverage / Consistency / Completeness / Efficiency）の重み付き平均で 0.0〜1.0 のスコアを算出。
0.7 未満の軸には改善アドバイスを表示。

`--telemetry-score` が指定された場合、テレメトリデータから環境の実効性を3軸で測定:

```bash
rl-audit "$(pwd)" --telemetry-score
```

3軸（Utilization / Effectiveness / Implicit Reward）の重み付き平均で 0.0〜1.0 のスコアを算出。
データ不足時（30セッション未満 or 7日未満）は警告を表示。

`--constitutional-score` が指定された場合、CLAUDE.md/Rules から抽出した原則に対する LLM Judge 評価と Chaos Testing を実行:

```bash
rl-audit "$(pwd)" --constitutional-score
```

Constitutional Score: 各原則 × 各レイヤーの遵守度を LLM Judge で評価。Coherence Coverage < 0.5 の場合はスキップ。
[ADR-037] により `rl-audit --constitutional-score` 本体は claude -p を呼ばず cache
（`principles.json` / `constitutional_cache.json`）を読むだけ。cache の再評価が要るときは Step 3.5 の
ファイルベース2相（principles round → constitutional round）を先に回す。
Chaos Testing: Rules/Skills の仮想除去による堅牢性テスト + SPOF 検出。

複数指定時は Environment Fitness（統合スコア）も表示:

```bash
rl-audit "$(pwd)" --coherence-score --telemetry-score --constitutional-score
```

### Step 1: Audit スクリプト実行

```bash
rl-audit "$(pwd)"
```

出力されるレポートをユーザーに表示する。

### Step 1.2: グローバルスキル使用状況サマリー（自動）

`rl-audit` の出力に「未使用グローバルスキル」セクションが含まれる場合、
その内容をレポートに表示し、`/rl-anything:prune` による整理を提案する。

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from skill_usage_stats import get_skill_activation_summary
summary = get_skill_activation_summary(days=90)
# summary["has_data"] が False の場合はスキップ（蓄積待ち）
# summary["unused_count"] > 0 の場合: 未使用スキル名リストと /prune 提案を表示
```

### Step 1.5: Memory Semantic Verification

MEMORY のセクション内容がコードベースの実態と整合しているかを LLM で検証する。

1. 検証コンテキストを取得:
```bash
rl-audit "$(pwd)" --memory-context
```

2. 出力 JSON が空（`sections` が空配列）の場合は「検証対象の MEMORY セクションがありません」と表示してスキップ。

3. 各セクションについて、`content` と `codebase_evidence` を突合し3段階で判定:
   - **CONSISTENT**: コードベースと整合。変更不要
   - **MISLEADING**: 正確だが誤解を招く表現がある。書き換え案を提示
   - **STALE**: コードベースと矛盾している。更新/削除を推奨

判定基準チェックリスト:
- [ ] セクションに記載されたツール名・コマンド名はコードベースに存在するか
- [ ] 記載されたファイルパスは実在するか（Step 1 の Stale References と合わせて確認）
- [ ] 記載された動作説明はコードの実装と一致するか
- [ ] 記載が冒頭や目立つ位置にあり、実態と異なる印象を与えないか（MISLEADING 判定）
- [ ] archive_mentions に関連する完了済み change があり、その変更が MEMORY に反映されているか

4. 判定結果をレポートの Memory Health セクション内に「### Semantic Verification」サブセクションとして表示:
   - 全セクションが CONSISTENT → 「全セクション整合」
   - MISLEADING/STALE あり → セクション名・判定・修正提案を表示

### Step 2: クロスラン集計（オプション）

optimize / rl-loop の実行履歴がある場合:

```bash
rl-audit-aggregate
```

### Step 3: 品質モニタリング（オプション）

高頻度 global/plugin スキルの品質スコアを計測し劣化を検知する。
claude -p は使わず、ファイルベース2相でオーケストレーションする（[ADR-037]）:

1. **Phase A — 再スコア対象を得る（LLM ゼロ）**:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/quality_monitor.py --emit-requests
   ```
   stdout は `{"requests":[{"id":<skill名>,"prompt":"...","meta":{...}}], "skipped":[...]}`。
   `requests` が空なら採点対象なし＝Step 3 はここで完了。
2. **Phase B — Claude（あなた）がインラインで CoT 採点**: 各 `requests[i].prompt` を読み、
   指示どおり CoT 形式（clarity/completeness/structure/practicality + total）の JSON で
   **インラインで採点**する（claude -p は呼ばない＝interactive subscription 課金）。
   採点結果を `{<skill名>: <CoT JSON 文字列>}` の形で `quality-resp.json` に Write する。
3. **Phase C — 集約・baselines 更新（LLM ゼロ）**:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/quality_monitor.py --ingest \
     --requests quality-req.json --responses quality-resp.json
   ```
   （`quality-req.json` は Phase A の stdout を保存したもの）。baselines 追記・劣化検知を行う。

- `--dry-run`: 採点対象スキルのみ表示（LLM・書き込みなし）
- audit レポートの "Skill Quality Trends" セクションで品質推移を確認
- audit パイプライン（`rl-audit` 本体）は LLM を呼ばず既存 baselines を読むだけ。再スコアは
  この Step 3 の2相でのみ走る（`--skip-rescore` は後方互換で受理されるが本体は LLM を起動しない）

### Step 3.5: Constitutional 再評価（2相・オプション）

constitutional スコア（原則 × レイヤーの遵守度 + slop 10% ブレンド）を最新化する。[ADR-037] により
claude -p を全廃し、principles 抽出 → レイヤー評価をファイルベース2相で行う。再評価が要るとき
（cache 未生成 / CLAUDE.md・Rules 変更）だけ回す。`requests` が空ならその round は cache 最新＝スキップ。

**依存順序が重要**: レイヤー評価プロンプトに principles を埋め込むため、必ず principles round を先に回す。

1. **Principles round（Phase A→B→C）** — claude -p は呼ばず**インライン生成**（subscription 課金）:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rl/fitness/principles.py --emit-request "$(pwd)" > prin-req.json
   ```
   `requests` が非空なら `requests[0].prompt` を読み、指示どおり JSON 配列
   （id/text/source/category/specificity/testability）をインライン生成して
   `{"principles": <JSON配列文字列>}` を `prin-resp.json` に Write し:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rl/fitness/principles.py --ingest "$(pwd)" \
     --requests prin-req.json --responses prin-resp.json
   ```
2. **Constitutional round（Phase A→B→C）** — 同じくインライン採点:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rl/fitness/constitutional.py --emit-requests "$(pwd)" > con-req.json
   ```
   `principles_missing` が true なら 1 を先に。`requests` が非空なら各 `requests[i].prompt`
   （レイヤー × 原則の遵守度評価）を読み、`{"evaluations":[{principle_id,score,rationale,violations}]}`
   形式の JSON でインライン採点して `{<layer名>: <JSON文字列>}` を `con-resp.json` に Write し:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rl/fitness/constitutional.py --ingest "$(pwd)" \
     --requests con-req.json --responses con-resp.json
   ```
3. 以降の `rl-audit --constitutional-score` は更新後 cache を読んでスコアを表示する。

### Step 4: 意味的類似度の検出（オプション）

行数超過や重複候補が検出された場合、改善アクションを提案する:
- 行数超過 → 分割を提案
- 重複候補 → 統合を提案
- Scope Advisory → スコープ最適化を提案
- Unmanaged Pitfalls（自動強制 未登録）→ 育っている（エントリ3+件）のに pitfall lint/commit-gate 未登録の `pitfalls.md` を提示。`/rl-anything:pitfall-curate` での enable を誘導（install ≠ enforcement の可視化）

## allowed-tools

Read, Bash, Glob, Grep

## Tags

audit, health-check, report
