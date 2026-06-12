---
name: evolve-skill
effort: medium
description: |
  特定スキルに自己進化パターン（Pre-flight / pitfalls.md）を組み込む独立コマンド。
  適性判定→テンプレート組み込み→人間確認を1コマンドで実行。
  Trigger: evolve-skill, スキル進化, 自己進化パターン, self-evolve
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# スキル自己進化パターン組み込み

特定スキルに自己進化パターン（Pre-flight Check / Failure-triggered Learning / pitfalls.md）を組み込む。

## 実行手順

ユーザーが `/rl-anything:evolve-skill` を呼び出したら、以下の手順で実行する。

### 1. 対象スキルを解決する

引数からスキルディレクトリを解決する:

- **スキル名** (例: `my-skill`): `.claude/skills/{name}/` に解決
- **ファイルパス** (例: `.claude/skills/my-skill/SKILL.md`): 親ディレクトリをスキルディレクトリとして使用
- **引数なし**: 対象スキルの指定を求めるメッセージを表示して終了

`--dry-run` が指定されている場合はファイル変更を行わないモードとして記憶する。

### 2. 適性判定を実行する

判断複雑さ（judgment_complexity）軸は [ADR-037] により claude -p を全廃し、以下の2段階で採点する:

1. **静的指標による自動算出（決定論・LLM 非依存）** — キャッシュミス時は `_score_judgment_complexity_static()` が
   3軸の静的シグナルから 1-3 を自動決定する (#354):
   - 条件分岐語（if/elif/else/when/unless/場合/条件/判断）の出現数
   - 番号付きリスト手順数（行頭 `1.`。markdown 見出し番号 `### 1.` は文書構造なので除外、
     さらに `STEPS_SIGNAL_CAP=5` で頭打ち＝長い線形チェックリストの張り付き防止）
   - `AskUserQuestion` の出現数 ×`ASK_USER_WEIGHT=2`（判断委譲の主信号なので重み付け）
   - signal 合計: < 3 → 1 / 3-7 → 2 / >= 8 → 3（steps 単独では cap により 3 に到達しない）
2. **LLM 精度が必要な場合**: `emit_judgment_requests` → assistant inline → `ingest_judgment_scores` の
   2相で後追い更新する（`judgment_source="llm"` でキャッシュ上書き）。

`compute_llm_scores` は LLM-free なので、LLM 品質の採点が欲しい場合は assess の前に judgment refresh を回す（emit が空なら cache 最新＝スキップ）。

**Phase A（判断複雑さ採点リクエスト生成 — claude -p なし）:**

```python
import os, sys, json
from pathlib import Path
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from skill_evolve import emit_judgment_requests
proj = Path(skill_dir).parent
emit = emit_judgment_requests(proj, [skill_dir])
if emit["requests"]:
    print(emit["requests"][0]["prompt"])  # Phase B でこの prompt にインライン回答（subscription 課金）
```

**Phase B→C（インライン採点 → 回収 → 適性判定）:** `requests` が非空なら上の prompt を読み、
1-3 の数字をインラインで決定し（claude -p を呼ばない）、再 emit（決定論・冪等）して ingest する:

```python
import os, sys
from pathlib import Path
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from skill_evolve import emit_judgment_requests, ingest_judgment_scores, assess_single_skill
proj = Path(skill_dir).parent
emit = emit_judgment_requests(proj, [skill_dir])  # 同一結果（決定論）
if emit["requests"]:
    ingest_judgment_scores(proj, emit["requests"], {skill_name: "<assistant が決めた 1-3>"})
result = assess_single_skill(skill_name, skill_dir)  # 更新後 cache を読む
```

### 3. 判定結果を表示する

5軸スコアと適性レベルを表示する:

```
## 自己進化適性判定: {skill_name}

| 軸 | スコア |
|----|--------|
| 実行頻度 | {frequency}/3 |
| 失敗多様性 | {diversity}/3 |
| 出力評価可能性 | {evaluability}/3 |
| 外部依存度 | {external_dependency}/3 |
| 判断複雑さ | {judgment_complexity}/3 |

合計: {total_score} / 適性: {suitability}
推奨: {recommendation}
```

アンチパターンがある場合はその詳細も表示する。

`workflow_checkpoints` が存在する場合（ワークフロースキル判定 True）、チェックポイントギャップを表示する:

```
### Workflow Checkpoint Gaps
| Category | Evidence | Confidence |
|----------|----------|------------|
| infra_deploy | 3 | 0.75 |
```

ギャップがない場合は「チェックポイントギャップなし」と表示する。

### 4. 適性に応じた処理

- **already_evolved**: 「既に自己進化対応済みです」と表示して終了
- **low / rejected**: 「変換非推奨」と表示して終了
- **medium / high**: 次のステップ（承認フロー）に進む
- **--dry-run**: 判定結果のみ表示し、ファイル変更を行わず終了

### 5. パターン組み込みの承認と実行

テンプレートカスタマイズも [ADR-037] により claude -p を全廃し、ファイルベース2相で行う。
LLM カスタマイズ不要なら `evolve_skill_proposal`（決定論・テンプレそのまま）でも良いが、
スキル文脈に合わせたい場合は次の2相を使う。

**Phase A（カスタマイズ・リクエスト生成 — claude -p なし）:**

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from skill_evolve import emit_customize_request
emit = emit_customize_request(skill_name, skill_dir)
if emit.get("requests"):
    print(emit["requests"][0]["prompt"])  # Phase B でこの prompt にインライン回答
```

**Phase B→C（インライン整形 → proposal 組み立て → 承認 → 適用）:** `requests` が非空なら
上の prompt を読み、テンプレ構造（見出し・テーブル）を維持したカスタマイズ済みマークダウンを
インライン生成し（claude -p を呼ばない）、再 emit（決定論・冪等）して ingest する:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from skill_evolve import emit_customize_request, ingest_customized_proposal, apply_evolve_proposal
emit = emit_customize_request(skill_name, skill_dir)  # 同一結果（決定論）
proposal = ingest_customized_proposal(
    skill_name, skill_dir, emit["requests"],
    {skill_name: "<assistant が生成したカスタマイズ済みマークダウン>"},
)
# proposal の内容（追加セクション概要）をユーザーに表示

# ユーザーに承認を求める（AskUserQuestion）。承認された場合のみ:
result = apply_evolve_proposal(proposal)
```

> fence 除去 + diff budget gate（#196, #199）は `ingest_customized_proposal` 内で適用される。
> 予算超過 / 応答欠損時はテンプレそのままに安全フォールバックする。

承認された場合のみ実行し、結果サマリーを表示:
- 追加されたセクション一覧
- `references/pitfalls.md` の作成（**既存ファイルがある場合は上書きしない** — SKILL.md への追記のみ行う）
- バックアップパス（`.md.pre-evolve-backup`）

> **#350 ガード**: `apply_evolve_proposal()` は `references/pitfalls.md` が既に存在する場合、
> テンプレートで上書きしない。既存の実エントリを保護するため、存在ガードが実装済み。
> pitfalls.md への変更が必要な場合は `/rl-anything:pitfall-curate` を使う。

却下された場合はファイルに変更を加えず終了。

### Pre-flight チェック

スキルに変更を加える前に以下を確認する。

#### 冪等性チェック（12-factor-agents Factor 5-6）

このスキルが変更するファイルやデータに対して冪等性を確認すること:

- **副作用の検出**: スキルが同じ入力で複数回実行された場合、2回目以降の結果が1回目と同一になるか？
- **ファイル追記の重複**: SKILL.md や rules への追記が重複しないか？（既存内容のチェック必須）
- **jsonl への追記**: 同一 session_id で重複レコードを書かないか？

**pre-flight 出力に含めること:**

```
idempotency_check: pass / fail
理由: [具体的に何をチェックしたか]
```

`fail` の場合は実装を中止してユーザーに報告する。

## 使用例

```
/rl-anything:evolve-skill my-skill                # スキル名指定
/rl-anything:evolve-skill .claude/skills/my-skill/SKILL.md  # パス指定
/rl-anything:evolve-skill my-skill --dry-run       # 判定結果のみ
```
