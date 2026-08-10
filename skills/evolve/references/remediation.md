# Remediation フェーズ詳細（Step 5.5 / 5.5.1）

remediation.py は audit の検出結果を confidence_score / impact_scope ベースで3カテゴリに動的分類する。
**カテゴリ閾値・MUST one-liner（AskUserQuestion / Q&A前提示 / 4択上限 / 対応 type）は SKILL.md 側に残してある。**
ここは出力テンプレと2相品質回復のコード。

## カテゴリ別の出力テンプレと手順

### auto_fixable (confidence ≥ 0.9, impact_scope in (file, project))

`generate_auto_fix_summaries(issues, project_root=Path.cwd())` を呼び出し、**AskUserQuestion の前に**以下のフォーマットでテキスト出力する（MUST。`project_root` を明示しないと `paths_suggestion` 生成が暗黙 `Path.cwd()` フォールバックに依存する — #400）:

```
**修正候補 N件:**
1. `<ファイルパス>` — <proposal>（理由: <rationale>）
2. ...
「一括修正」を選ぶとこれらが順に適用されます。
```

⚠ **pitfall — 補足説明は Q&A の前に出す（MUST）**: proposal/rationale をテキストとして先に出力してから AskUserQuestion を呼ぶ。選択肢の description に rationale を詰め込まない。ユーザーが Yes/No を判断できる状態を作ってから質問する。

その後、AskUserQuestion で「一括修正」「個別承認」「スキップ」を選択（MUST）:
- 一括修正: 全 auto_fixable を順に実行
- 個別承認: 各 issue の proposal/rationale を提示しながら1件ずつ承認を取り、承認分のみ実行
- スキップ: 何もしない

承認後: `FIX_DISPATCH[issue_type]` で対応する fix 関数を実行 → `verify_fix()` + `check_regression()` で2段階検証。
対応 type: stale_ref, stale_rule, claudemd_phantom_ref, claudemd_missing_section, skill_evolve_candidate, verification_rule_candidate。
regression 検出時: `rollback_fix()` で復元し manual_required に格上げ。結果を `record_outcome()` で記録。
`collect_issues()` は内部で `diagnose_all_layers()` を統合済みのため、別途マージ不要。

### proposable (confidence ≥ 0.5, scope != global, confidence < 0.9 for non-file/project)

**confidence で2分割して質問攻めを防ぐ（#377-3）。** evolve.py が `proposable_custom` を決定論で
`partition_proposable_by_confidence`（しきい値 0.7）にかけ、`classified.proposable_custom_individual[]`
（conf ≥ 0.7）と `classified.proposable_custom_batch_skip[]`（conf < 0.7）に分けて result に surface する。
SKILL は count を消費するだけで、しきい値判定はコード側に置く（MUST が効かない class の再発防止）。

**個別承認対象 = `proposable_custom_individual`（conf ≥ 0.7）:**

- `proposable_custom_individual > 0` の場合のみ個別承認フローを実行（MUST）
- **提案詳細プロトコルに従う**: `generate_proposals(issues, project_root=Path.cwd())` で各 issue の `{proposal, rationale}` を取得し、**1件ずつ**「対象・根拠（detail の実値）・変更内容」を提示してから AskUserQuestion で個別承認（MUST。`project_root` を明示しないと `paths_suggestion` 生成が暗黙 `Path.cwd()` フォールバックに依存する — #400）
- **⚠ pitfall — 補足説明は Q&A の前に出す（MUST）**: 「なぜ必要か」「どんな効果があるか」を AskUserQuestion と同じターン内の Q&A より前のテキストとして先に出力すること。ユーザーが Yes/No を判断できる状態を作ってから質問する。
- **⚠ pitfall — AskUserQuestion の options は最大 4 択（MUST）**: individual が 5 件以上の場合に 5 択以上の options を1問で出してはならない。proposal-protocol.md の方式 A（1件ずつ）または方式 B（グループ分割）を使う。
- 同じ type の issue が複数あっても件数に丸めない（例: `missing_effort` が 10 スキル分あるなら各スキル名 + 推定 effort + reason を per-item で展開する。10 件超は他 M 件と誘導）
- 対応 type: line_limit_violation, near_limit, orphan_rule, stale_memory, memory_duplicate, missing_effort, skill_triage_create/update/split/merge
- 承認された修正のみ実行 → 検証 → 記録
- **⚠ skill_triage の confidence は detail["confidence"] が権威（#522-1）**: skill_triage 系 issue（CREATE=0.70 等）は `compute_confidence_score` が default 0.5 に降格させず detail から引き継ぐ。これを怠ると CREATE が永久に batch_skip 落ちし個別承認レーンに乗らない。

**まとめてスキップ対象 = `proposable_custom_batch_skip`（conf < 0.7）:**

- FP 集中帯（hardcoded_value / duplicate 低類似 / skill_evolve medium 等、conf 0.5〜0.65）。**デフォルトはスキップ**。
- 「低 confidence の proposable {batch_skip}件をまとめてスキップしました（個別に見る場合は展開可）」と**1行表示**する。**1件ずつ AskUserQuestion を出してはならない（MUST NOT）**。
- ユーザーが希望した場合のみ、提案詳細プロトコルで個別展開し承認フローに乗せる。

- `proposable_custom_individual == 0`（個別対象なし）の場合は AskUserQuestion を出さず、batch_skip の1行表示のみで Step を終える。batch_skip も0件なら「proposable: 個別対象なし ✓」を残す（沈黙≠評価）。
- `proposable_custom == 0` かつ `proposable_global > 0` の場合: 「proposable: global スキルのみ {M}件（参考値） — 対応不要」と1行表示してスキップ
- **例外（#225）**: type が `*_hook_candidate` にマッチする issue（hook インストール系アクション）は `partition_proposable_by_scope` が impact_scope/origin に関わらず常に custom 側へ合流させるため、上記の global 折り畳みには現れない。共有設定（~/.claude 配下）の書き換えは影響半径が最大のため、必ず個別承認レーン（`proposable_custom_individual`）で生成スクリプト/diff 全文を表示する。

### manual_required (confidence < 0.5, or impact_scope = global)

- 問題の概要、推奨アクション、分類理由を表示のみ

**サマリ**: 「Remediation 完了: N件修正 / M件スキップ / K件ロールバック（要手動対応）」

## Step 5.5.1: proposable の line_limit_violation / split_candidate に対する2相品質回復（[ADR-037] Phase 1d-ii）

`fix_line_limit_violation` / `fix_split_candidate` は [ADR-037] で claude -p を全廃し
**決定論フォールバック**（proposable 降格 / fixed=False、または決定論 proposal_text）で完走する。
承認後に assistant がここでファイルベース2相（emit→インライン→ingest）で実際の圧縮/分離を行う。

**対象 issue**:
- `line_limit_violation`（非 rule ファイル）→ `emit_compression_request / ingest_compression`
- `line_limit_violation`（rule ファイル） → `emit_separation_request / ingest_separation`
- `split_candidate` → `emit_split_request / ingest_split`（書込なし・proposal_text 生成）

**⚠ pitfall — 関数ごとに signature が違う（#524-1）。** emit 3 関数は引数の数が揃っていない。
特に `emit_compression_request`（3引数）の例だけ見て `emit_separation_request`（**4引数**・`path` が入る）
に流用すると `TypeError` になる。**実 signature の単一ソースは下表（`scripts/lib/remediation/fixers_llm.py`）:**

| 関数 | signature |
|------|-----------|
| `emit_compression_request` | `(issue, original_content, limit)` |
| `ingest_compression` | `(issue, path, original_content, limit, requests, responses)` |
| `emit_separation_request` | `(issue, path, original_content, limit)` ← compression と違い `path` が入る |
| `ingest_separation` | `(issue, path, original_content, limit, requests, responses)` |
| `emit_split_request` | `(issue, content)` ← `limit` なし |
| `ingest_split` | `(issue, requests, responses)` ← `path`/`limit`/`original` なし・書込なし |

**Phase A（リクエスト生成 — claude -p なし）:** 承認された issue を渡す。

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from remediation.fixers_llm import (
    emit_compression_request, emit_separation_request, emit_split_request,
    ingest_compression, ingest_separation, ingest_split,
)
from pathlib import Path

path = Path(issue["file"])
original = path.read_text(encoding="utf-8")
limit = issue.get("detail", {}).get("limit", 3)

# 例1: line_limit_violation（非 rule ファイル）の圧縮 — emit は 3 引数
emit = emit_compression_request(issue, original, limit)

# 例2: line_limit_violation（rule ファイル）の分離 — emit は 4 引数（path が入る）
#   suggest_separation が None（非適用）なら emit["requests"] == [] になる。
#   prompt の参照リンクは PJ ルート相対パス（.claude/references/<name>.md）で生成される（#524-2）。
#   実際の書込先（絶対パス）は meta["reference_path"] に保持される。
emit = emit_separation_request(issue, path, original, limit)

# 例3: split_candidate（SKILL.md の分割提案）— emit は 2 引数（limit なし・書込なし）
content = path.read_text(encoding="utf-8")
emit = emit_split_request(issue, content)

for r in emit["requests"]:
    print(r["id"], "\n", r["prompt"], "\n---")  # Phase B でインライン回答（subscription 課金）
```

**Phase B→C（インライン応答 → ingest）:** `requests` が非空なら各 prompt を読み、
圧縮/要約/分割提案テキストをインラインで決定し、`responses = {request_id: 生テキスト}` を組んで ingest する。
**ingest も signature が異なる**（上表参照）:

```python
# 圧縮 ingest（6 引数）— path に書込
result = ingest_compression(issue, path, original, limit, emit["requests"], responses)

# 分離 ingest（6 引数）— meta["reference_path"]（絶対）に原文、path に要約を書込
result = ingest_separation(issue, path, original, limit, emit["requests"], responses)

# 分割 ingest（3 引数）— ファイル書込なし。proposal_text(str) を返す
proposal_text = ingest_split(issue, emit["requests"], responses)
# result["fixed"] が True なら成功。False なら proposable のまま（手動確認）
```

- `fixed=True` → ファイル書き込み完了（`ingest_compression` / `ingest_separation` が IO を担当）
- `fixed=False` → `result["error"]` を表示し手動対応を案内
- `ingest_split` は dict でなく `str`（提案テキスト）を返す — 書込なし

## 情報レーンの dismiss（Step 5.5・#103）

`proposable_global` / `phases.discover.rule_violation_observed` は「対応しない」判断をしても却下記録の入口が無く**毎回再提示**されていた（#26 の対象外レーンで同型再発）。ユーザーが「この PJ では意図的運用なので以後出さなくてよい」と判断したら下記で dismiss を記録する（PJ スコープ・TTL45日）。dismiss 済みは `remediation.proposable_global_suppressed` / `remediation.rule_violation_suppressed` に件数で畳んで surface する（silence != evaluated）。**dry-run のときは記録しない（MUST NOT）**。明示 dismiss しなくても、連続提示された advisory は `reconcile_surfaced` が閾値回数（既定2）で自動畳み込みする（下記 #494 fallback が情報レーンにも効く）。

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from remediation.suppression_ledger import record_rejection, resolve_slug
from rule_violation_lane import rule_violation_suppression_issue

slug = resolve_slug()
# (a) proposable_global の issue dict を dismiss（classified.proposable_global[] の要素）
for issue in dismissed_global_issues:
    record_rejection(issue, slug=slug)
# (b) rule_violation_observed を violated_command 単位で dismiss（意図的運用フラグ）
for v in dismissed_rule_violations:  # phases.discover.rule_violation_observed[] の要素
    record_rejection(rule_violation_suppression_issue(v), slug=slug)
```

## 却下/スキップの記録（Step 5.5・べき等性 — 重複提案 MUST NOT、#477）

個別承認 AskUserQuestion でユーザーが**却下／スキップ**を選んだ提案は、`record_rejection` で suppression ledger に記録する（dedup_key 単位・TTL45日）。これにより次回 evolve で同じ提案が再出しない（run_evolve が `_apply_remediation_suppression` で却下済みを既に除外し、`remediation.suppressed_by_ledger` 件数を surface する）。**dry-run（`--dry-run`）のときは記録しない（MUST NOT）**。記録対象は「採用しなかった issue dict」（`classified.proposable_custom_individual[]` の要素そのもの）。下記コードで一括記録する（**#479: 直 import は ModuleNotFoundError になるため sys.path 設定込みの完全コードで実行する**）:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from remediation.suppression_ledger import record_rejection, resolve_slug

# rejected_issues = ユーザーが却下/スキップした issue dict のリスト（個別承認で不採用にしたもの）
# dry_run = True のときは下のループを実行しない（MUST NOT — suppression ledger に書かない）
slug = resolve_slug()  # worktree 安全 slug（git-common-dir の親 basename）
for issue in rejected_issues:
    record_rejection(issue, slug=slug)  # dedup_key 単位・TTL45日で記録（last-write-wins）
print(f"suppression ledger: {len(rejected_issues)} 件を却下記録（次回 evolve で再提示しない）")
```

**決定論 fallback（#494）**: 上の inline 記録を取りこぼしても、run_evolve が remediation phase で `reconcile_surfaced` を毎 run 呼び、解決されないまま連続で個別承認に出続けた提案を閾値回数（既定2）で**自動却下**する安全網がある（`remediation.auto_rejected_by_reconcile` に件数 surface・dry-run 非書込）。これは Step 5.5 の散文 MUST が唯一の却下入口だった構造（却下が永久消失するレーン）を塞ぐためのもの。**それでもユーザーが明示却下した提案は上の record_rejection で即記録するのが正**（fallback は次回以降に効くため、即時抑制は inline 記録が担う）。

## Step 5.6: /simplify ゲート

Remediation でファイルが変更された場合、Python コードの品質チェックを行う。

**判定条件**:
1. Remediation の `record_outcome()` 結果から `fix_detail.changed_files` を集約する
2. 以下の条件で分岐:
   - `.py` ファイルが1つ以上含まれる → `/simplify` を実行
   - `.md` ファイルのみ → スキップ（「/simplify: Markdown のみ — スキップ」と表示）
   - 変更なし（0件 or dry-run）→ スキップ

**実行手順** (`.py` ファイルあり):
1. `/simplify` を実行する
2. `/simplify` の結果（git diff）をユーザーに提示する
3. AskUserQuestion で「適用」「元に戻す」を選択させる（MUST）
4. 結果をレポートに記録:
   - 適用: 「/simplify: N件の改善を適用」
   - 元に戻す: 「/simplify: 実行済み・変更なし」

**後方互換**: `/simplify` スキルが利用不可の場合（古い Claude Code）はスキップし、「/simplify: スキップ（未対応バージョン）」と表示する
