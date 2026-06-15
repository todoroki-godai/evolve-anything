---
name: prune
effort: medium
description: |
  Detect unused artifacts (dead globs, zero invocations, duplicates) and propose archiving.
  Never deletes directly — all archiving requires human approval.
  Trigger: prune, 淘汰, cleanup, archive, 未使用削除, unused
disable-model-invocation: true
---

# /rl-anything:prune — 未使用アーティファクトの淘汰

dead glob・zero invocation・重複の3基準でアーティファクトを検出し、アーカイブを提案する。
直接削除は行わない（MUST NOT）。全淘汰は人間承認が必須（MUST）。

Global 候補の検出は `skill_activations.jsonl`（PostToolUse hook 蓄積）を優先する。
データがない場合は usage-registry.jsonl にフォールバックし、それもなければ空リストを返す（蓄積待ち）。

## Usage

```
/rl-anything:prune [project-dir]
/rl-anything:prune --restore          # アーカイブから復元
/rl-anything:prune --list-archive     # アーカイブ一覧表示
```

## 実行手順

### Step 1: 候補検出

```bash
rl-usage-log "prune"
rl-prune "$(pwd)"
```

### Step 2: 候補リストの表示 + 推薦ラベル判定

検出された候補を以下のカテゴリ別に表示:
- **Dead Glob**: rules の paths 対象がマッチしないもの
- **Zero Invocation**: 30日間使用記録がないもの（カスタムスキルのみ）。ただし以下は除外:
  - `type: reference` の参照型スキル（drift 検出で別途処理）
  - `.pin` ファイルで保護されているスキル
  - 対象 PJ の **CLAUDE.md の Skills セクションに登録されているスキル**（本番運用中とみなす、#351）
  - **観測窓が usage 記録修正日 (#478) をまたぐ間は zero_invocation 全体を suppress（#522-2/#529-1）**。この期間は欠損データで「未使用」を断定できないため、候補を出さず `zero_invocations_suppressed.message`（「計測待ち N 件」）を**1行 surface するだけに留める（MUST）**。per-item 調査・個別承認は行わない（advisory↔MUST 矛盾の解消）。窓全体が修正日以降に蓄積されたら通常判定に自動復帰する。
- **Plugin Unused**: プラグイン由来で未使用のスキル（レポートのみ、アーカイブ対象外）
- **Global 候補**: `skill_activations.jsonl` で90日間未使用・低頻度のグローバルスキルを検出（データなし時は usage-registry.jsonl フォールバック）
- **重複候補**: audit-report の意味的類似度検出結果

Plugin Unused のスキルはアーカイブ対象外とする（MUST）。
「未使用。`claude plugin uninstall` を検討？」とレポートのみ出力する。

#### コンテキスト収集（MUST）

Step 1 の JSON 出力に各候補の `description`（SKILL.md から抽出済み）と `recommendation`（Python 一次判定）が含まれる。

**推薦ラベルの最終判定は Step 3 の個別レビュー内で各スキルごとに行う**。各スキルのレビュー時に SKILL.md を Read で全文読み取り、以下のチェックリストで最終判定を行う（MUST）。Python 一次判定（`recommendation` フィールド）を上書きしてよい。

**archive推奨チェックリスト:**
- [ ] 特定PJ固有で他PJでは使えない
- [ ] 一時デバッグ・hotfix 用途で目的完了済み
- [ ] 他スキルに機能が統合済み
- [ ] description に "deprecated" や "obsolete" を含む

**keep推奨チェックリスト:**
- [ ] 複数PJで利用可能な汎用スキル
- [ ] リファレンス・テンプレート価値がある
- [ ] 定期的に必要になる性質（daily, weekly, deploy 等）
- [ ] Trigger が3個以上定義されている

**判定ルール**: いずれか2つ以上該当 → そのラベル、両方1つずつ or いずれも0 → 要確認

#### description 空文字時のフォールバック

候補の `description` が空文字の場合は `"(説明なし)"` と表示する（MUST）。
その場合、SKILL.md 全文を Read で読み取り、1行要約を生成して表示に使用する（MUST）。

### Step 3: 個別レビューフロー（MUST）

自動的にアーカイブを実行してはならない（MUST NOT）。
AskUserQuestion の options は常に **4つ以下** とする（MUST）。

候補が0件の場合は AskUserQuestion を表示せず「未使用スキルはありません」と報告する（MUST）。

#### 個別レビュー手順

各候補スキルを順番にレビューする。各スキルについて以下の手順を踏む（MUST）:

1. **SKILL.md を Read で全文読み取り**、Step 2 の推薦ラベル判定と合わせて分析を行う
2. **分析テキストを出力**（以下のテンプレートに従う（MUST））:

```
---
**N/M: {スキル名}** [{推薦ラベル}]
説明: {description}

判断理由:
- 未使用の背景: {なぜ使われていないかの分析}
- 今後の使用可能性: {汎用性・トリガー数・季節性等}
- 重複/統合: {他スキルとの重複・統合状況}
- 参照価値: {リファレンス・テンプレートとしての価値}
---
```

3. **AskUserQuestion で判断を確認**:
   - **候補 1-2件目** の選択肢（3択）:
     - label: `アーカイブ`, description: `このスキルをアーカイブする`
     - label: `維持`, description: `このスキルを維持する`
     - label: `後で判断`, description: `今回はスキップする`
   - **候補 3件目以降** の選択肢（3択）:
     - label: `アーカイブ`, description: `このスキルをアーカイブする`
     - label: `維持`, description: `このスキルを維持する`
     - label: `残り全てスキップ`, description: `残りの候補を全て維持し、レビューを終了する`

#### 「残り全てスキップ」選択時

ユーザーが「残り全てスキップ」を選択した場合、残りの候補を全て維持扱いとし、個別レビューを即座に終了する（MUST）。Step 4 に進み、それまでに「アーカイブ」と判断されたもののみ実行する。

#### SKILL.md の Read 失敗時

候補スキルの SKILL.md が Read できない場合（ファイル不在等）、prune.py の JSON 出力に含まれる `description` と `recommendation` のみで判断分析を提示する（MUST）。判断理由の各観点は「情報不足」と記載する。

### Step 4: アーカイブ実行

承認された候補のみ `~/.claude/rl-anything/archive/` に移動:

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from prune import archive_file, check_import_dependencies, SkillDependencyError
archive_file("/path/to/file", "zero_invocation")
```

#### 依存検査（skill ディレクトリ archive 時、MUST）

スキルディレクトリ全体（`skills/<name>`）を archive する場合、`archive_file` は内部で
`check_import_dependencies` を呼び、`scripts/<module>.py` の他スキル/CLI からの import や
`skills/<name>/` パス参照を検出する。参照ありの場合は `SkillDependencyError` が raise され、
archive は中断する（Issue #25 の再発防止）。

検出された場合の対応:
1. `check_import_dependencies(skill_path, repo_root)` の結果をユーザーに提示する
2. **依存断ち切り PR を先行させる** ことを促す（参照側の import 削除 / パス参照差し替え）
3. 依存断ち切り完了を確認してから再度 archive を実行する
4. やむを得ず強制 archive する場合のみ `force=True` を渡す（warning は出るが archive は実行される）

**注意（module 名衝突）**: `check_import_dependencies` は skill ディレクトリ配下の Python モジュール名（`scripts/*.py` の stem）で逆引きする。複数 skill が同名 module（例: `utils.py`）を持つ場合、別 skill 由来の参照を誤検出することがある。誤検出と判断した場合のみ `force=True` でバイパスする。

提示テンプレート:
```
スキル `{skill_name}` は以下から参照されています:
- {referrer_1} ({kind})
- {referrer_2} ({kind})
...

archive する前に依存を断ち切る PR を先行させてください。
強制 archive する場合: archive_file(path, reason, force=True)
```

### Step 5: 復元（--restore 時）

```python
import os, sys
_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd()
sys.path.insert(0, os.path.join(_root, "scripts", "lib"))
from prune import restore_file, list_archive
items = list_archive()  # 一覧表示
restore_file("/path/to/archive/file")  # 復元
```

復元失敗時（archive にファイルが存在しない場合）はエラーメッセージを表示する。

## allowed-tools

Read, Bash, AskUserQuestion, Glob, Grep

## Tags

prune, archive, cleanup
