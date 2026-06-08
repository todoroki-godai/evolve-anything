# Remediation フェーズ詳細（Step 5.5 / 5.5.1）

remediation.py は audit の検出結果を confidence_score / impact_scope ベースで3カテゴリに動的分類する。
**カテゴリ閾値・MUST one-liner（AskUserQuestion / Q&A前提示 / 4択上限 / 対応 type）は SKILL.md 側に残してある。**
ここは出力テンプレと2相品質回復のコード。

## カテゴリ別の出力テンプレと手順

### auto_fixable (confidence ≥ 0.9, impact_scope in (file, project))

`generate_auto_fix_summaries(issues)` を呼び出し、**AskUserQuestion の前に**以下のフォーマットでテキスト出力する（MUST）:

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

- `proposable_custom > 0` の場合のみ個別承認フローを実行（MUST）
- **提案詳細プロトコルに従う**: `generate_proposals(issues)` で各 issue の `{proposal, rationale}` を取得し、**1件ずつ**「対象・根拠（detail の実値）・変更内容」を提示してから AskUserQuestion で個別承認（MUST）
- **⚠ pitfall — 補足説明は Q&A の前に出す（MUST）**: 「なぜ必要か」「どんな効果があるか」を AskUserQuestion と同じターン内の Q&A より前のテキストとして先に出力すること。ユーザーが Yes/No を判断できる状態を作ってから質問する。
- **⚠ pitfall — AskUserQuestion の options は最大 4 択（MUST）**: proposable_custom が 5 件以上の場合に 5 択以上の options を1問で出してはならない。proposal-protocol.md の方式 A（1件ずつ）または方式 B（グループ分割）を使う。
- 同じ type の issue が複数あっても件数に丸めない（例: `missing_effort` が 10 スキル分あるなら各スキル名 + 推定 effort + reason を per-item で展開する。10 件超は他 M 件と誘導）
- 対応 type: line_limit_violation, near_limit, orphan_rule, stale_memory, memory_duplicate, missing_effort
- 承認された修正のみ実行 → 検証 → 記録
- `proposable_custom == 0` かつ `proposable_global > 0` の場合: 「proposable: global スキルのみ {M}件（参考値） — 対応不要」と1行表示してスキップ

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

# 例: line_limit_violation（非 rule）の圧縮
original = Path(issue["file"]).read_text(encoding="utf-8")
limit = issue.get("detail", {}).get("limit", 3)
emit = emit_compression_request(issue, original, limit)
for r in emit["requests"]:
    print(r["id"], "\n", r["prompt"], "\n---")  # Phase B でインライン回答（subscription 課金）
```

**Phase B→C（インライン応答 → ingest）:** `requests` が非空なら各 prompt を読み、
圧縮/要約テキストをインラインで決定し、`responses = {request_id: 生テキスト}` を組んで ingest する:

```python
# 例: 圧縮 ingest
result = ingest_compression(issue, Path(issue["file"]), original, limit,
                             emit["requests"], responses)
# result["fixed"] が True なら圧縮成功。False なら proposable のまま（手動確認）
```

- `fixed=True` → ファイル書き込み完了（`ingest_*` が IO を担当）
- `fixed=False` → `result["error"]` を表示し手動対応を案内
