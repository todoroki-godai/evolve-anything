# 467 設計ドラフト: 提案の種類を全て朝の y/n に到達させる（Stage 0 / Stage 1）

- 対象 issue: #467（Epic）
- 関連: #459（reader ゼロの blocking 検出・一般形）/ #379（新設凍結）/ #402・ADR-053（revert）/ ADR-054 §9.4
- 状態: **rev5 — Stage 0 は実装・マージ済み（PR #476 / `cfa77249`）。Stage 1 は 2026-08-16 の実測で
  設計前提が2つとも崩れたため着手を取り消し、設計に差し戻し中。§1.5 が実測、§10 が再設計で
  答えるべき問い**
- 前提コミット: `cfa77249`（main・Stage 0 マージ後）

---

## 0. この設計が答える問い

1. 「朝の y/n に到達する」とは何が揃うことか（**rev2 で再定義**）
2. Stage 0（棚卸しの機械化）をどう **CI blocking** にするか
3. Stage 1 のパイロットに**どの提案種別を選ぶか**
4. #379 凍結との衝突をどう解くか

---

## 1. 実測した現状（file:line 付き・2026-08-15）

### 1.1 生成側は8種、decision lane が読むのは2種だけ

（訂正・2026-08-15 Stage 0 実装レビュー: 本節見出しは当初「7種」としていたが、下表は
当初から8行あり本文側の数え間違いだった。`rule_violation_observed` も他7種と同様の
result 直下キーであり、生成元 `rule_violation_lane.py` 以外に読み手がいない＝未接続の
提案種別として §3 の宣言表・baseline に含める）

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

### 1.5 実測（2026-08-16）— rev4 の前提を2つ崩した観測結果

Stage 1 着手前に「§4.4 が MUST にしている実データ較正」を実行した結果、**較正どころか
パイロット選定そのものが成り立たない**ことが判明した。以下はすべて実測値であり推定を含まない。

#### 1.5.1 パイロット `instruction_violations` は本番で 0 件（前提崩壊①）

| 観測 | 値 | 根拠 |
|---|---|---|
| `corrections.jsonl` 総数 | 171 | 実ストア（全PJ共通・2026-05-15〜2026-08-15） |
| うち `last_skill` が truthy | **0 / 171** | 同上 |
| 生成の足切り条件 | `c.get("last_skill")` が truthy な correction のみ | `discover/runner.py:392-393` |

**根本原因は検出器ではなく入力側**。corrections の書き手は2系統あり、支配的な方が
`last_skill` を **None 固定**で書いている:

| writer | 件数 | `last_skill` |
|---|---|---|
| `correction_semantic/promote.py:563`（朝の y/n で採用 → `source="reflect_confirmed"`） | 161 | `promote.py:365` で **`None` ハードコード** |
| `hooks/correction_detect.py:163`（`source="hook"`） | 2 | `read_last_skill(session_id)` 経由・実測 None |
| backfill | 8 | キー無し |

`hooks/observe.py:87` の `write_last_skill` は正常に動作している（`$TMPDIR` に実ファイルが存在し
`{"skill_name": ..., "timestamp": ...}` を保持。`usage.jsonl` は 5,574 件の Skill 呼び出しを記録）。
壊れているのは**採用経路が session→skill の対応を運ばないこと**であり、
一時ファイルの TTL（24h・`rl_common/workflow.py:16`）でも書込み側でもない。

修理した場合の上限も実測した: **同一セッション内で correction より前に Skill 呼び出しがあった
correction は 28 / 171（16%）**。1 correction = 最大1 violation（`runner.py:440` の無条件 `break`）
なので、violation の上限は 28 件。そこから対立動詞マッチに当たったものだけが提案になる。

同じ入力欠落が **`pitfall_candidates` も殺している**（`pitfall_manager/detection.py:165-167` が
同一条件で足切り）。**1箇所の修理で2種別が 0 → 稼働に変わる**。

#### 1.5.2 §4.4「confidence を実データで較正」は実装不能（前提崩壊②）

production の `confidence` は**連続値ではなく2値固定**である:

| 経路 | confidence | needs_review | file:line |
|---|---|---|---|
| Stage1 対立動詞 | **0.95 固定** | 既定 False | `critical_instruction_extractor.py:355-363` |
| Stage2 keyword overlap ≥3 | **0.50 固定** | **True 固定** | `critical_instruction_extractor.py:366-375` |

LLM Judge 経路（`emit_violation_judge_requests` / `ingest_violation_judges`・:397-508）は
**production から一度も呼ばれていない**（caller は自己参照とテストのみ。ADR-037 Phase 1d-i で
2相 API 化したが SKILL.md への配線が入っていない）。
`needs_review=True` を除外した時点で残るのは 0.95 のみなので、閾値は
0.5〜0.95 のどこに置いても結果が変わらない。**「実データで較正する」という MUST は
現行コードに対して意味を持たない。**

#### 1.5.3 未接続13種の実産出量（全数実測・LLM 呼び出しゼロ）

`PROPOSAL_KINDS` の `lane_connected=False` 全13種を、生成関数を個別 import して直接呼んで計測
（`run_discover()` 全体は未実行）。対象は本リポジトリ。

| 種別 | 産出件数 | 束ね後の y/n 回数 | 0件の理由（file:line） |
|---|---|---|---|
| `repeating_patterns` | 124 | 124（**束ねキー無し**） | — |
| `rule_violation_observed` | 25 | `violated_command` 単位なら **1** | — |
| `recommended_artifacts` | 12 | 12 | — |
| `trajectory_skill_candidate` | 1 | 1 | — |
| `missed_skill_opportunities` | 1 | 1（上記由来） | — |
| `pitfall_candidates` | 0 | — | `pitfall_manager/detection.py:165-167`（§1.5.1 と同一原因） |
| `hook_candidates` | 0 | — | `discover/errors.py:15` 閾値3に未達 |
| `instruction_violation` | 0 | — | `runner.py:392-393`（§1.5.1） |
| `verification_needs` | 0 | — | `verification_catalog/runner.py:80-100` 全件導入済み判定 |
| `stall_recovery_patterns` | 0 | — | `tool_usage_analyzer/stall.py:47` 2セッション跨ぎ未達 |
| `workflow_checkpoint_gaps` | 0 | — | `runner.py:489-490` `<repo>/.claude/skills/` 不在 |
| `constraint_decay_warnings` | 0 | — | `discover/patterns.py:115` `decay_rate > 0.3` 該当なし |
| `constraint_decay_findings` | 0 | — | 同上 |

**中身の質（人間が判断するための実サンプル）**:

- `repeating_patterns` 上位は `git`(1606) / `grep`(1228) / `gh auth`(1060) — 日常コマンドの
  使用頻度そのもの。「スキル化しますか」に落ちる質ではない＝**y/n 不適**
- `rule_violation_observed` は内容が具体的で行動に直結する
  （例: `cd` 禁止ルールが導入済みなのに 522 回観測 → `reason="rule_installed_but_not_enforced"`、
  推奨は hook enforce）。ただし 25 件すべて `violated_command="cd"` の**1指摘が
  ディレクトリ差で分裂したもの**
- `recommended_artifacts` は `skills/discover/SKILL.md:57-67` に**既に専用の y/n フロー**を持つ

#### 1.5.4 rev4 が見落としていた構造的事実（最重要）

**中身が出る3種はいずれも「新しいファイルを作る」提案**であり、
§1.4 の「revert lane は既存ファイルの書き換えしか表現できない」に直撃する。

一方 §4 がパイロットに選んだ `instruction_violation` は、**唯一「既存 SKILL.md の書き換え」型
だったが産出が 0**（§1.5.1）。

> **つまり「産出がある種別」と「既存 lane に乗る種別」の積集合が空である。**
> rev4 は §5（新規ファイル作成の accept/revert）を *Stage 2 の前提* として先送りしたが、
> 実測はこれを **Stage 1 の前提**へ格上げする。

なお `rule_violation_observed` は孤児ではない。`phases_remediate.py:99-102` が
`make_hook_candidate_issues_from_rule_violations` を呼び、
`RULE_VIOLATION_HOOK_THRESHOLD=20` 以上の違反 head を**1つの hook scaffold に束ねて**
issue 化している（`rule_violation_lane.py:344-388`）。
未接続なのは「朝の y/n レーン（`_extract_candidates`）」に対してのみである。
**束ねの実装は既に存在するので、rev4 が §4.2 で新設しようとした集約契約は
この関数の再利用で足りる可能性がある**（要検証）。

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
    selector: str        # "list_of_dict" | "dict_of_list" | "scalar" — 要素の取り出し方
    element_key: str | None  # selector が dict_of_list のときの内側キー
    lane_connected: bool # §1.3 の4点セットを満たすか
PROPOSAL_KINDS: tuple[ProposalKind, ...] = (...)
```

`selector` が必要な理由（rev3・codex round2 [Must]1）: 種別ごとに格納形が違う。
`repeating_patterns` は `phases.discover.tool_usage_patterns` という **dict の内側**にあり
（`runner.py:317,332` で `tool_result["repeating_patterns"]` に再代入される）、
dotted path だけでは「そこから要素をどう取り出すか」を表せない。

配置: `scripts/lib/proposal_lane_coverage.py`（純関数モジュール。store も observability section も作らない）。

### 3.2 抜け道をどこまで塞ぐか（codex 1巡目 [Must]4 / 2巡目 [Must]1 反映）

**未接続を許す対象を、Stage 0 時点で固定した baseline に限定する。**
baseline は**実装から独立した git 追跡ファイル**に置く（`shrink_freeze` の凍結定数と同型）:

```
scripts/lib/fixtures/proposal_lane_unconnected_baseline.txt
  repeating_patterns
  rule_violation_observed
  pitfall_candidates
  hook_candidates
  instruction_violation
  trajectory_skill_candidate
```

（訂正・2026-08-15: 当初5行から `rule_violation_observed` を追加し6行に。§1.1 の訂正と同時）

契約テスト（`scripts/lib/tests/test_proposal_lane_coverage.py`）:

1. `PROPOSAL_KINDS` の各 `(source_path, selector)` が**合成 envelope** から要素を取り出せる（宣言と実装の乖離を検出）
2. `lane_connected=True` の種別が `_extract_candidates` の実出力に現れる（接続したと宣言して実装を忘れたら赤）
3. `lane_connected=False` の種別が baseline ファイルの部分集合（**新種を未接続で足したら赤**）
4. baseline ファイルの各行が `PROPOSAL_KINDS` に実在する（**接続済みなのに baseline に残っていたら赤**＝単調減少の強制）

Stage 3 の完了条件は baseline ファイルが空。テスト3が自動的に「全種接続」を要求する形になる。

**機械では塞げない残余を明示する（rev3）**: baseline ファイル自体に1行足せばテスト3は通る。
これは `shrink_freeze` の `FROZEN_*` 定数が持つ性質と同じで、**機械契約では原理的に塞げない**。
採る対処は次の2つで、「完全に機械強制できる」とは書かない:

- baseline は**独立ファイル**なので、緩めた事実は必ず PR diff の1行として現れる（コード変更に紛れない）
- baseline への追加を許すのは **Stage 2 完了まで**の期限付きとし、Stage 3 でファイルごと削除する

### 3.3 生成側の網羅（AST は補助・正典は宣言表）

テスト1〜4 は「宣言済みの種別」しか見ない。**runner が新キーを書いたのに宣言しない**ケースを
完全に機械検出することはできない（helper 関数の戻り値経由・動的キーは静的に追えない）。
rev1/rev2 の「AST で `result[...] =` を列挙して突合」は、`tool_result["repeating_patterns"] = ...`
（`runner.py:332`）のようなネスト更新を取りこぼす（codex round2 [Must]1）。

したがって位置づけを下げる:

- **正典は `PROPOSAL_KINDS` の宣言表**（人が書く）
- AST 走査は **best-effort の補助検出**とし、`result[...]` / `<name>[<str>] =` / `.setdefault(` / `.update(`
  を拾って「宣言表に無い候補キー」を**警告として列挙**する（赤にはしない＝誤検知で狼少年にしない）
- 走査パターンは `skill_declaration_reachability.py:169 _iter_py_files` / `:192 build_call_graph_index` を流用
- golden snapshot は条件付きキーを構造的に取りこぼすため使わない（#458 の実測・#459 コメント）

### 3.4 enforcement の置き場所は dogfood ではなく CI portable suite（codex [Should] を実測で強化）

codex の指摘「CI が当該 Layer 2 を blocking で実行する証拠がない」を検証した結果、**より強い事実**が出た:

- `.github/workflows/ci.yml` に `dogfood` の実行は**存在しない**（grep 一致は :68 のコメントのみ）
- CI の pytest は**5ファイルの portable suite 限定**（`ci.yml:71-77`）:
  `test_distribution_check.py` / `test_glossary_drift.py` / `test_shrink_freeze.py` /
  `test_dependency_metadata.py` / `test_readme_drift.py`
- `pre-push.local:37` は exit 1 を警告に留め push を継続する

→ **`test_proposal_lane_coverage.py` を `ci.yml` の portable suite に追加することが blocking の実体**。
dogfood Layer 2（`invariants.py:183-188` の `_CHECKS`）への追加はローカル早期警告として任意で行う。

**hermetic 受入条件（MUST・rev3 / codex round2 [Should]）**: portable suite は
「ホスト依存テストを除く」契約（`ci.yml:64-69`）なので、新テストは次を満たすこと。
満たせない検査は portable suite に入れず dogfood 側へ回す。

- `~/.claude` / 実 PJ データ / DuckDB 実ストア / LLM / ネットワークを一切参照しない
- 検証入力は **source の AST** と **`tmp_path` 内に組み立てた合成 envelope** のみ
- `run_discover()` の実行はしない（実行すると HOME 依存と実データ依存が入る）
- テスト2（`_extract_candidates` の実出力）は合成 envelope を渡した純関数呼び出しで行う

**副次の指摘（本 Epic のスコープ外だが記録）**: #459 が「Layer 2 blocking で止める」と書いているのも
同じ理由で CI では止まらない。#459 側の設計も CI 配線の再確認が要る。

---

## 4. 判断2: Stage 1 のパイロットは `instruction_violations` — **rev5 で撤回**

> **⚠ 本節（§4 全体）は rev5 で撤回した。** §1.5.1 の実測により、この種別は本番で 0 件しか
> 産出しない。パイロットとして採用すると「配管は通ったが1件も人間に届かない」状態で
> 完了扱いになり、**#467 が潰そうとしている欠陥そのものを再生産する**。
>
> §4.1（運搬契約）・§4.1b（suppression identity 分離）・§4.2（集約契約）の**設計内容自体は
> 実測と矛盾しておらず、種別非依存の契約として再利用できる**（`_emit.py:190-213` の pending entry に
> `detail` 相当が無いこと、`_suppression.py:23-50` が `entry["id"]` のみを一意性成分にしていることは
> 2026-08-16 に再確認済み）。撤回するのは**パイロットの選定**と §4.4 である。
>
> 以下は撤回前の記述として保存する。

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

### 4.1b 再提示契約 — reject 抑制の identity を分離する（rev3・codex round2 [Must]3）

`detail` を identity から外すだけでは足りない。reject 抑制（#446）は
`_suppression.py::_issue_for`（:23-56）が `detail.target = entry["id"]`（＝`proposal_id`）**だけ**を
一意性の成分にしている。`proposal_id` は `(repo_id, path, before_sha)` なので、
**同じ SKILL.md・同じ before_sha に後日まったく別の violation が出ても同じ ID になり、
新しい判断材料が既定 45 日間まるごと抑制される**。

**採る設計**: proposal identity / 判断イベント identity は現行のまま維持し、
**suppression identity だけを分離**する。

```
suppression_target = proposal_id                      # skill_diff / skill_evolve（現行と同一）
suppression_target = f"{proposal_id}:{fingerprint}"   # instruction_violation
  fingerprint = sha256( canonical_json(正規化済み violations) )[:12]
```

`正規化済み violations` の定義は **§4.2 の ④ を通過した集合**（dedup → 統合 → 整列 → 品質ゲート）。
`canonical_json` は キー昇順・区切り固定（`separators=(",", ":")`）・`ensure_ascii=False`・
文字列は NFC 正規化とする（プラットフォーム間で fingerprint がぶれないため）。

- **後方互換 MUST**: 既存 2 種別の `suppression_target` は現行と**バイト等価**にする
  （fingerprint を付けない）。付けると既存 ledger の抑制記録が全て失効し、
  一度却下した提案が再提示される
- fingerprint は種別ごとに `proposal_lane_coverage.py` へ宣言し、契約テストで固定する
- 「同じ内容が再検出されたら抑制、内容が変わったら再提示」が満たすべき不変条件。
  回帰テスト2本（同一内容→抑制 / 1件追加→再提示）を必須にする

### 4.2 集約契約（[Must]3）

提案 identity がパス単位である以上、**同一 `skill_path` の複数 violation は 1 提案に束ねる**
（`_candidates.py:93` の `seen` による1件畳み込みと整合。1 SKILL.md = 1 提案 = 1 判断・#444）。

- `detail.violations` に**全件**を入れる（順序依存の情報欠落を作らない）
- y/n は 1 回。部分承認はしない（部分承認は identity 単位と矛盾する）

**正規化パイプライン（rev4・codex round3 [Must] 反映）**。入力順に依存しないことを保証するため、
次の 4 段を**この順に**適用する。各段は契約テストで固定する。

**① dedup キー**: `(instruction_text, correction_message, match_type)`
（rev3 の 2 要素キーでは `match_type` が異なる別種の検出を1件に潰してしまうため 3 要素に訂正）

**② 重複レコードの統合規則**（同一 dedup キーの複数レコードを 1 件へ畳む・全フィールドを明示）:

| フィールド | 統合規則 |
|---|---|
| `confidence` | **max** |
| `needs_review` | **OR**（1件でも True なら True＝安全側） |
| `reason` | `confidence` が最大のレコードのもの。同値が複数なら `reason` の**昇順で最小** |
| `instruction_text` / `correction_message` / `match_type` | dedup キーなので全レコードで同一 |

**③ 全順序**（②の後は次の 4 タプルで**一意**に定まる）:
`(-confidence, instruction_text, correction_message, match_type)` の昇順。
rev2 の tie-breaker「correction の時刻」は `make_instruction_violation_issue`
（`issue_schema.py:418-442`）が timestamp を運ばず実装不能だった（codex round2 [Must]2）。
運搬契約を増やさず現行フィールドだけで全順序を定義する。

**④ 品質ゲート適用**（§4.4）: `needs_review=True` と `confidence` 閾値未満を除外する。
**suppression fingerprint（§4.1b）は、この④を通過した後の集合から計算する**
（表示される集合と抑制判定の集合を一致させる。ゲート前後で不一致だと
「表示されていない violation の変化で再提示される」不整合が起きる）。

### 4.3 表示・承認手順（[Must]1）

`skills/evolve/SKILL.md` Step 3 / Step 7.8 を**種別非依存**に書き換える:

- 「`matched_skills` を skill_path 単位にグループ化して提示」→
  「`result.evolve_decisions.pending[]` を **`skill_path` 単位に提示**し、`proposal_type` に応じて
  diff（`skill_diff` / `skill_evolve`）または `detail.violations`（`instruction_violation`）を表示する」
- ID の受け渡し（`--accepted` / `--rejected`）は現行のまま（pending[].id は種別非依存）
- **SKILL.md の記述を契約テストで固定する**（`test_report_feedback_contract.py` と同型。
  SKILL.md の MUST は強制力を持たない＝`learning_skill_md_must_not_enforcement`）

### 4.4 品質ゲート — **rev5 で撤回**

> **⚠ 実装不能。** §1.5.2 のとおり production の `confidence` は 0.95 / 0.50 の2値固定で、
> 0.50 は常に `needs_review=True` とセット。`needs_review` を除外した時点で 0.95 だけが残るため、
> **閾値をどこに置いても結果が変わらない**。「実データで較正する」という MUST は
> 現行コードに対して空文である。
>
> 再設計で決めるべきは「閾値をいくつにするか」ではなく
> **「LLM Judge 経路（`critical_instruction_extractor.py:397-508`）を配線するか、
> 品質ゲートを confidence 以外の軸で定義するか」**（§10-Q4）。

以下は撤回前の記述として保存する。

`detail.needs_review=True` または `confidence` が閾値未満の violation は y/n に出さない。
閾値は**実データで較正**する（合成 fixture で決めない＝`learning_synthetic_fixture_false_confidence`）。

---

## 5. 新規ファイル作成の accept / revert（**rev5 で Stage 1 の前提へ格上げ**・方針のみ）

> **rev5 の変更**: rev4 は本節を「Stage 2 の前提」として先送りしたが、§1.5.4 の実測により
> **産出のある種別はすべてここに依存する**ことが判明した。したがって本節は Stage 1 の
> クリティカルパスであり、先送りできない。独立 ADR の起票が再設計の第一歩になる（§10-Q2）。

**scope ではなく操作種別で表現する**（codex [Should] 反映）。`_SUPPORTED_SCOPES` の第三の値は作らない
— `project` / `global` 配下に作る限り scope は同じで、必要なのは `op: modify | create` の区別。

不足点（rev1 の6点 → codex 1巡目 [Must]6 で 11 点 → 2巡目 [Must]4 で **12 点**）:

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
| 12 | **未存在 target の identity 解決**。`repo_identity` は親ディレクトリが存在しないと repo 情報を捨て絶対パスへ縮退するため、新規スキル用ディレクトリごと未存在だと **worktree ごとに proposal identity が分裂する**。最寄りの存在する祖先から repo/worktree と相対パスを解決する変更 + 回帰テスト（同一 repo の別 worktree で同一 identity になること） | `evolve_decision_ids.py:51-55` |

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
   （rev5 追記: 実産出 **1件**。コストは高いが量は問題にならない）
2. `repeating_patterns` は `rule_violation_lane.py` 経由の経路がある（到達可否未検証）。Stage 2 の対象に含めるか
   （rev5 追記: 実産出 **124件で束ねキー無し**。上位は `git`/`grep`/`gh auth` の使用頻度でノイズ。
   **接続対象から外す判断が妥当**と実測は示唆する → §10-Q3）
3. 朝の y/n の**1日あたり上限**。5種類が全て到達すると件数が跳ねる（現状 `daily_review` は weak_signal を最大5件）
   （rev5 追記: **必須化**。§10-Q5）

---

## 9. 未確認事項（この設計で埋めていないもの）

- `discover/enrich.py` の `matched_skills` 完全スキーマ（`_candidates.py` からの逆引きのみ）
- ~~`repeating_patterns` 要素の dict 構造~~ → §1.5.3 で実測（`{pattern, count, subcategory, examples}`）
- `evolve_decisions/_emit.py` 全体（読んだのは pending payload 構築 :180-213 と dry-run 例外の行番号のみ）
- `_ingest.py:85-120` の accept 判定が「不在 → 存在」遷移をどう扱うか（§5-4 の裏取りは未実施）

---

## 10. rev5 の再設計で答えるべき問い（実装着手はこれらが埋まるまで凍結）

実測（§1.5）が rev4 の前提を崩したため、Stage 1 は**パイロット選定からやり直す**。
以下 Q1〜Q5 に決着がつくまで実装コードを書かない。

### Q1. パイロットをどう選び直すか

制約は「産出がある」∩「既存 lane に乗る」が**空集合**であること（§1.5.4）。取りうる形は3つ:

| 案 | 内容 | 前提になる工事 | 実測に基づく見込み |
|---|---|---|---|
| A | 入力欠落（`last_skill`）を先に直し `instruction_violation` を復活させる | 採用経路が session→skill を運ぶ（または read 時に `usage.jsonl` から join） | 上限 28 correction。実際の提案数は**未知（0 の可能性あり）** |
| B | `rule_violation_observed` を `violated_command` で束ねて出す | 新規ファイル作成の accept（§5） | y/n **1件**。内容は具体的（`cd` 522回） |
| C | §5 を先に完成させてから3種を一斉接続 | §5 の12点すべて + 独立 ADR | y/n は最大 12〜25件／日。上限設計（§8-3）が必須 |

**A の注意**: 「修理すれば動く」は仮説であって実測ではない。A を採るなら
**修理後に実際に何件出るかを測ってからパイロット確定**とし、0 件なら A を破棄する
（`learning_dryrun_verification_blind_spot`: 適用後にしか出ない効果を完了基準にしない）。

### Q2. §5（新規ファイル作成の accept / revert）を独立 ADR として先に起票するか

§1.5.4 より、B と C はどちらもここに依存する。A を採る場合でも、
`instruction_violation` の修正提案が「既存 SKILL.md への追記」で表現できるかは要確認。

### Q3. 束ねキーの正典をどこに置くか

`rule_violation_observed` は `violated_command` で束ねると 25 → 1 になる（§1.5.3）。
`repeating_patterns` は**束ねキーが存在しない**（124件が 124 の y/n になる）。
`proposal_lane_coverage.py::ProposalKind` に `bundle_key` を追加して宣言表に載せるのが
Stage 0 の資産と整合するが、**束ねキーを持てない種別を接続対象から外す判断**が先に要る。

なお `rule_violation_lane.py:344-388` に**既存の束ね実装がある**（`violated_command` の集合を
1 hook scaffold にまとめる）。rev4 §4.2 の集約契約を新設する前に、この関数を
`_extract_candidates` から再利用できるかを確認すること（再発明の回避）。

### Q4. 品質ゲートを何の軸で定義するか

§4.4 は撤回済み。選択肢は (a) LLM Judge 経路（`critical_instruction_extractor.py:397-508`）を
SKILL.md に配線して confidence を連続値に戻す / (b) confidence を捨て
`match_type` のホワイトリスト（`opposing_verb` のみ）で定義し、**それが実質的に
固定ホワイトリストであることを設計に明記する** / (c) 品質ゲート自体を Stage 1 では持たない。

(b) を選ぶ場合、`OPPOSING_VERBS`（`critical_instruction_extractor.py:44-52`）は
7ペアの固定辞書なので、**検出能力の上限がこの辞書で決まる**ことを受入条件に書くこと。

### Q5. 朝の y/n の1日あたり上限（§8-3 の再掲・rev5 で必須化）

C を採ると最大 25件／日になり、`daily_review` の既存上限（weak_signal 最大5件・
`daily_review.py:374`）と桁が合わない。**上限と溢れた分の扱い（翌日繰越 / 破棄 / 優先度順）を
決めずに接続しない。**

### 再設計のレビュー要件

本 rev5 は**実測の記録と問いの整理**であり、Q1〜Q5 の**答えを含まない**。
答えを書いた rev6 に対して `design-review-gate` に従い codex 1巡を通し、
`[Must]` が残る間は実装に着手しない。
