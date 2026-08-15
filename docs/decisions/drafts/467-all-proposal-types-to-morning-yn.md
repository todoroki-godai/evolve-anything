# 467 設計ドラフト: 提案の種類を全て朝の y/n に到達させる（Stage 0 / Stage 1）

- 対象 issue: #467（Epic）
- 関連: #459（reader ゼロの blocking 検出・一般形）/ #379（新設凍結）/ #402・ADR-053（revert）/ ADR-054 §9.4
- 状態: **draft rev2（codex 1巡目 `設計修正要`・[Must]6 / [Should]5 を反映済み。着手前）**
- 前提コミット: `493c3173`（main）

---

## 0. この設計が答える問い

1. 「朝の y/n に到達する」とは何が揃うことか（**rev2 で再定義**）
2. Stage 0（棚卸しの機械化）をどう **CI blocking** にするか
3. Stage 1 のパイロットに**どの提案種別を選ぶか**
4. #379 凍結との衝突をどう解くか

---

## 1. 実測した現状（file:line 付き・2026-08-15）

### 1.1 生成側は7種、decision lane が読むのは2種だけ

`scripts/lib/discover/runner.py` が result に書く提案種別。**格納位置は同一階層ではない**
（rev2 訂正: `repeating_patterns` は result 直下ではなく `tool_usage_patterns` 配下）:

| 種別 | result 上の dotted path | 生成 file:line | 要素の構造 |
|---|---|---|---|
| `matched_skills` | `phases.discover.matched_skills` | runner.py:296 | `{skill_path, matched_skill, pattern, ...}` |
| `skill_evolve` | `phases.skill_evolve.assessments` | skill_evolve 経路 | suitability high/medium のみ lane が採用 |
| `repeating_patterns` | `phases.discover.tool_usage_patterns.repeating_patterns` | runner.py:317-332（`rule_violation_lane` で分離後に再代入） | tool_usage_analyzer 側（構造は**未確認**） |
| `rule_violation_observed` | `phases.discover.rule_violation_observed` | runner.py:331 | 同上 |
| `pitfall_candidates` | `phases.discover.pitfall_candidates` | runner.py:369（実体 `pitfall_manager/detection.py:201-206`） | `{title, root_cause, skill_name, source}` |
| `hook_candidates` | `phases.discover.hook_candidates` | runner.py:374（実体 `discover/errors.py:75-107`） | `{type, pattern, full_message, count, suggestion, reason}` |
| `instruction_violations` | `phases.discover.instruction_violations` | runner.py:443（`issue_schema.py:418-442`） | `{type, file, detail:{skill_name, instruction_text, correction_message, match_type, confidence, reason, needs_review}, source}` |
| `trajectory_skill_candidates` | `phases.discover.trajectory_skill_candidates` | runner.py:272 | skill_extractor 候補（4軸分解つき） |

朝の y/n を組み立てる `_extract_candidates`（`scripts/lib/evolve_decisions/_candidates.py:79-125`）が読むのは
`matched_skills`（:97-106）と `skill_evolve.assessments`（:109-123）の **2 経路のみ**。

### 1.2 「到達」は `_extract_candidates` だけでは決まらない（rev2 の中核訂正）

codex [Must]1 の指摘どおり、**実際に人間の目に出るかを決めているのは SKILL.md の対話手順**である。

- `skills/evolve/SKILL.md:196`（Step 3）: 「`matched_skills` は `skill_path` 単位にグループ化してから
  改善提案を diff で提示し AskUserQuestion で承認/スキップ（MUST）」と、**種別を名指しで固定している**
- `skills/evolve/SKILL.md:297`（Step 7.8 への ID 受け渡し）も `matched_skills` を前提に書かれている

さらに codex [Must]2 のとおり、pending payload が種別固有の判断材料を運ばない:

- `_emit.py:190-200` の pending entry は `skill_name` / `skill_path` / `pattern` / `proposal_type` と
  identity 系（`id`/`run_id`/`before_sha`/`worktree_root`/`fitness_func`）のみ
- `instruction_violations` の `instruction_text` / `correction_message` / `match_type` / `confidence` は**捨てられる**
- → 朝の利用者は「何を直すのか」を pending から復元できない

### 1.3 「接続済み」の定義（rev2 で新設・以後この4点セットを満たしたときのみ接続と呼ぶ）

| # | 要件 | 実体 |
|---|---|---|
| 1 | 候補抽出 | `_candidates.py::_extract_candidates` が当該種別を返す |
| 2 | 運搬 | `_emit.py` の pending payload が判断に必要な情報を落とさず運ぶ |
| 3 | 表示・承認 | `skills/evolve/SKILL.md` Step 3 / Step 7.8 の手順が当該種別を扱う |
| 4 | 固定 | 1〜3 を固定する契約テストが **CI portable suite に登録されている**（§3.4） |

### 1.4 revert lane は「既存ファイルの書き換え」しか表現できない

| 事実 | file:line |
|---|---|
| `_SUPPORTED_SCOPES = ("global", "project")` | `evolve_revert/_availability.py:41` |
| availability は before 本文の存在を必須にしている | `_availability.py:66` |
| 3分岐（normal / 冪等 / conflict）は current_sha と after/before_sha の比較 | `_apply.py:295-300` |
| `resolve_target` は `lstat()` 失敗＝`REASON_NOT_FOUND` で即失敗 | `_target.py:77-80` |
| `_restore_normal` は `os.replace(tmp, target)` のみ＝**復元＝上書き** | `_apply.py:131-222`（:211） |
| before payload 必須検査 / `--dump-before` も本文前提 | `_apply.py:248`, `_dump.py:52` |
| before_content は文字列前提。「不在」sentinel と schema version が無い | `evolve_decision_ids.py:258,279,300-313` |

---

## 2. 判断1: #379 凍結との衝突は起きない（codex [Should] で追認）

凍結の機械契約は `shrink_freeze.assert_no_new_keys`（`shrink_freeze.py:259-273`）で、対象は 4 定数のみ —
`FROZEN_STORES`(:61-108) / `FROZEN_OBSERVABILITY_SECTIONS`(:111-158) /
`FROZEN_ADVISORY_PROPOSAL_ADAPTERS`(:161-166) / `FROZEN_WEAK_SIGNAL_CHANNELS`(:171-180)。
CI が比較する live 集合はこの4つだけ（`test_shrink_freeze.py:62,70,78,88`）。

`_extract_candidates` は `advisory_proposals` を経由せず `phases.*` を直接読む独立経路なので、
この経路の拡張は4定数のどれも増やさない。

### 採る方針（rev2 で受入条件を明文化）

- **凍結文言は変更しない。** 接続は advisory adapter 追加ではなく `_extract_candidates` の入力拡張として行う
- **受入条件（MUST）**: 本 Epic の実装 PR は
  **新しい store / observability section / advisory proposal adapter / weak_signal channel を1つも追加しない**。
  レビュー時に `test_shrink_freeze.py` 緑であることを証拠として要求する
- 「凍結に触れないから何でも足してよい」の抜け道は §3 の宣言表で塞ぐ

---

## 3. Stage 0: 到達性の棚卸しを機械化する

### 3.1 宣言スキーマは dotted path + selector にする（codex [Must]5 反映）

種別ごとに格納階層が違う（§1.1）ため、キー名の集合比較では検査できない。単一の宣言表を持つ:

```python
@dataclass(frozen=True)
class ProposalKind:
    kind: str            # "instruction_violation"
    source_path: str     # "phases.discover.instruction_violations"（dotted path）
    lane_connected: bool # §1.3 の4点セットを満たすか
PROPOSAL_KINDS: tuple[ProposalKind, ...] = (...)
```

配置: `scripts/lib/proposal_lane_coverage.py`（純関数モジュール。store も observability section も作らない）。

### 3.2 抜け道を塞ぐ契約（codex [Must]4 反映）

rev1 の3本では「新種を `PROPOSAL_KINDS` に足しつつ `lane_connected=False` にすれば全部通る」。
**未接続を許す対象を、Stage 0 時点で固定した baseline に限定する**（`assert_no_new_keys` と同型）:

```python
UNCONNECTED_BASELINE = frozenset({
    "repeating_patterns", "pitfall_candidates", "hook_candidates",
    "instruction_violation", "trajectory_skill_candidate",
})  # Stage 0 で凍結。追加不可。Stage 1-2 で接続するたび減る
```

契約テスト（`scripts/lib/tests/test_proposal_lane_coverage.py`）:

1. `PROPOSAL_KINDS` の各 `source_path` が実 result envelope に到達可能（**宣言と実装の乖離を検出**）
2. `lane_connected=True` の種別が `_extract_candidates` の実出力に現れる（**接続したと宣言して実装を忘れたら赤**）
3. **`lane_connected=False` の種別が `UNCONNECTED_BASELINE` の部分集合**（**新種を未接続で足したら赤**）
4. `UNCONNECTED_BASELINE` は単調減少のみ（増やす差分は赤）

Stage 3 の完了条件は `UNCONNECTED_BASELINE == frozenset()`。テスト3が自動的に「全種接続」を要求する形になる。

### 3.3 生成側の網羅をどう担保するか

テスト1 は「宣言済みの種別が実在するか」しか見ない。**runner が新キーを書いたのに宣言しない**ケースは
別途必要。`discover/runner.py` の `result[...] = ` 代入を AST で列挙し、宣言表と突合する
（`skill_declaration_reachability.py:192 build_call_graph_index` / `:169 _iter_py_files` の走査パターンを流用）。
golden snapshot は条件付きキーを構造的に取りこぼすため使わない（#458 の実測・#459 コメント）。

### 3.4 enforcement の置き場所は dogfood ではなく CI portable suite（codex [Should] を実測で強化）

codex の指摘「CI が当該 Layer 2 を blocking で実行する証拠がない」を検証した結果、**より強い事実**が出た:

- `.github/workflows/ci.yml` に `dogfood` の実行は**存在しない**（grep 一致は :68 のコメントのみ）
- CI の pytest は**5ファイルの portable suite 限定**（`ci.yml:71-77`）:
  `test_distribution_check.py` / `test_glossary_drift.py` / `test_shrink_freeze.py` /
  `test_dependency_metadata.py` / `test_readme_drift.py`
- `pre-push.local:37` は exit 1 を警告に留め push を継続する

→ **`test_proposal_lane_coverage.py` を `ci.yml` の portable suite に追加することが blocking の実体**。
dogfood Layer 2（`invariants.py:183-188` の `_CHECKS`）への追加はローカル早期警告として任意で行う。

**副次の指摘（本 Epic のスコープ外だが記録）**: #459 が「Layer 2 blocking で止める」と書いているのも
同じ理由で CI では止まらない。#459 側の設計も CI 配線の再確認が要る。

---

## 4. 判断2: Stage 1 のパイロットは `instruction_violations`（rev1 の結論を維持・ただし「そのまま乗る」は撤回）

issue 本文は `hook_candidates` を最有力としていたが、対象が**存在しないファイル**であるため
accept 判定（`_ingest.py:85-120` が `read_text()` と `after_sha != before_sha` を要求）にも
revert（§1.4）にも乗らず、パイロットに revert lane の新規実装が混入する。

`instruction_violations` は対象が既存 `SKILL.md`（`runner.py:415-441` が実在の SKILL.md を読んで検出）
なので、**新規ファイル作成という別問題を持ち込まない**。この理由でパイロットに選ぶ。

ただし codex [Must]2/[Must]3 のとおり **「既存 lane にそのまま乗る」は誤り**。以下を設計する:

### 4.1 運搬契約（[Must]2）

pending entry に**表示専用の `detail` フィールド**を追加する:

- `detail` は **proposal identity に含めない**（`proposal_id` は `(repo_id, repo相対path, before_sha)` のまま）。
  identity を触ると #279/#286/#290 の再発（N重記録 / 永久欠落）を招く
- `detail` は種別ごとのスキーマを `proposal_lane_coverage.py` に宣言し、契約テストで固定する
- `instruction_violation` の `detail`: `{violations: [{instruction_text, correction_message, match_type, confidence, reason, needs_review}], skill_name}`

### 4.2 集約契約（[Must]3）

提案 identity がパス単位である以上、**同一 `skill_path` の複数 violation は 1 提案に束ねる**
（`_candidates.py:93` の `seen` による1件畳み込みと整合。1 SKILL.md = 1 提案 = 1 判断・#444）。

- `detail.violations` に**全件**を入れる（順序依存の情報欠落を作らない）
- 表示順は `confidence` 降順 → 同値は `correction` の時刻昇順で**決定論固定**（契約テストで固定）
- y/n は 1 回。部分承認はしない（部分承認は identity 単位と矛盾する）

### 4.3 表示・承認手順（[Must]1）

`skills/evolve/SKILL.md` Step 3 / Step 7.8 を**種別非依存**に書き換える:

- 「`matched_skills` を skill_path 単位にグループ化して提示」→
  「`result.evolve_decisions.pending[]` を **`skill_path` 単位に提示**し、`proposal_type` に応じて
  diff（`skill_diff` / `skill_evolve`）または `detail.violations`（`instruction_violation`）を表示する」
- ID の受け渡し（`--accepted` / `--rejected`）は現行のまま（pending[].id は種別非依存）
- **SKILL.md の記述を契約テストで固定する**（`test_report_feedback_contract.py` と同型。
  SKILL.md の MUST は強制力を持たない＝`learning_skill_md_must_not_enforcement`）

### 4.4 品質ゲート

`detail.needs_review=True` または `confidence` が閾値未満の violation は y/n に出さない。
閾値は**実データで較正**する（合成 fixture で決めない＝`learning_synthetic_fixture_false_confidence`）。

---

## 5. 新規ファイル作成の accept / revert（Stage 2 の前提・方針のみ）

**scope ではなく操作種別で表現する**（codex [Should] 反映）。`_SUPPORTED_SCOPES` の第三の値は作らない
— `project` / `global` 配下に作る限り scope は同じで、必要なのは `op: modify | create` の区別。

不足点（rev1 の6点 → codex [Must]6 を統合して 11 点）:

| # | 不足 | 根拠 |
|---|---|---|
| 1 | before_content に「不在」sentinel と **schema version** | `evolve_decision_ids.py:258,279,300-313` |
| 2 | `REVERT_FIELD_KEYS` へ sentinel を永続化 | `evolve_decision_ids.py:258` |
| 3 | 候補抽出の「読めない対象は落とす」を不在許容へ（壊れたファイルを不在扱いしないこと） | `_candidates.py:51-54` |
| 4 | accept 判定に「不在 → 存在」の遷移 | `_ingest.py:85-120` |
| 5 | `compute_revert_availability` が before 本文必須をやめる | `_availability.py:66` |
| 6 | `resolve_target` の `REASON_NOT_FOUND` に「削除済み＝冪等成功」分岐 | `_target.py:77-80` |
| 7 | `_apply` に「復元＝削除」分岐（`os.replace` でなく `unlink`） | `_apply.py:131-222` |
| 8 | `apply_revert` の before payload 必須検査を op 別に分ける | `_apply.py:248` |
| 9 | `--dump-before` の意味定義（create の before は空。空出力と失敗を区別する） | `_dump.py:52` |
| 10 | 削除時の containment 検証（repo 外・シンボリックリンク経由の削除を拒否）と再検証順序 | `_target.py` / `_apply.py` |
| 11 | 表示文言（「戻す＝ファイルを削除します」と明示。上書きと同じ文言にしない） | `evolve_revert_cli.py` |

**独立 ADR にする**（ADR-053 の改訂ではなく後継）。accept と revert の両側にまたがるため。

---

## 6. dry-run ゼロ書込みとの関係（codex [Should] の裁定）

`emit_decisions` は dry-run でも pending marker を書く既存例外がある（`_emit.py:141,260`）。

**裁定: 既存例外を正式に維持する。** 根拠:

- CLAUDE.md が「pending marker の dry-run 書込は**意図された設計（消さない）**」と明記
- 純度ゲートが意図された dry-run 書込みを誤って殺し emit→drain が全死した前科がある（#505→#513）

**種別追加による増分**: pending marker は run 単位で1件のため、種別が増えても marker の**件数は増えない**。
pending entry の件数は増えるが、これは marker ファイル内の配列要素であり新規書込み先ではない。
実装時に「dry-run 実行後に marker 以外へ書込みゼロ」を E2E で assert する（既存 dogfood Layer1 の不変と同型）。

---

## 7. #459 との関係（rev2 で断定を撤回）

rev1 は「#459 を先に入れると `hook_candidates` は grandfathering で見逃される」と断定したが、
**#459 の baseline 実装は現作業ツリーに存在せず、実コードで裏が取れていない**（codex [Should]）。

**採る方針: #467 は #459 に依存しない設計にする。**
§3 の宣言表と契約テストは #459 の baseline 仕様が未確定でも単独で成立する。
#459 が入った後に「宣言表を #459 の baseline から自動生成できるか」を再検討する（それまでは手動宣言）。

---

## 8. 未決定・ユーザー判断が要る点

1. `trajectory_skill_candidates`（新規スキル作成）を朝の y/n に出すか。1件あたりの人間コストが他種別より明確に高い
2. `repeating_patterns` は `rule_violation_lane.py` 経由の経路がある（到達可否未検証）。Stage 2 の対象に含めるか
3. 朝の y/n の**1日あたり上限**。5種類が全て到達すると件数が跳ねる（現状 `daily_review` は weak_signal を最大5件）

---

## 9. 未確認事項（この設計で埋めていないもの）

- `discover/enrich.py` の `matched_skills` 完全スキーマ（`_candidates.py` からの逆引きのみ）
- `repeating_patterns` 要素の dict 構造（tool_usage_analyzer 側 未読）
- `evolve_decisions/_emit.py` 全体（読んだのは pending payload 構築 :180-200 と dry-run 例外の行番号のみ）
