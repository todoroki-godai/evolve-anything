# 467 設計ドラフト: 提案の種類を全て朝の y/n に到達させる（Stage 0 / Stage 1）

- 対象 issue: #467（Epic）
- 関連: #459（reader ゼロの blocking 検出・一般形）/ #379（新設凍結）/ #402・ADR-053（revert）/ ADR-054 §9.4
- 状態: **rev5（codex / tacchi レビュー反映済み）— Stage 0 は実装・マージ済み（PR #476 / `cfa77249`）。
  Stage 1 は 2026-08-16 の実測で設計前提が2つとも崩れたため着手を取り消し、設計に差し戻し中。
  §1.5 が実測（等級は §1.5.0）、§10 が再設計で答えるべき問い、§11 がレビュー記録**
- **読む順序**: §1.5.0（証拠の等級）→ §1.5（実測）→ §10（次に何をするか）。
  §2〜§7 は rev4 の記述で、撤回・保留のマークが付いた節がある
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
パイロット選定そのものが成り立たない**ことが判明した。

#### 1.5.0 証拠の等級（codex [Must]3 / tacchi [Nit]5 反映・rev5 レビュー後に追加）

本節は**性質の違う2種類の根拠**を含む。混ぜて読むと「全部が同じ強さで確定している」と
誤読されるため、等級を分ける。

| 等級 | 内容 | 誰が再検証できるか |
|---|---|---|
| **[コード]** | 本リポジトリのソースから読み取れる事実（閾値・条件分岐・caller の有無） | 誰でも。file:line で追える |
| **[実測]** | このマシンの実ストア（`~/.claude/evolve-anything/*.jsonl`）を読んだ観測値 | **本人の環境でのみ**。他環境では再現しない |

以下、各主張の冒頭に等級を付す。**[実測] は他者の環境では検証できない**。
codex cold review（2026-08-16）も [実測] 群を「未検証」と判定しており、これは正しい。

**[実測] の再現手順**（取得日 2026-08-16 / 対象 commit `cfa77249` / 入力は取得時点の実ストア全量）:

- corrections 系の集計: `~/.claude/evolve-anything/corrections.jsonl` を全行 JSON パースし
  `last_skill` / `correction_type` / `source` を数える
- 「直前スキル」の復元: 同 `session_id` で `usage.jsonl` の `ts` が correction の `timestamp` より
  前にある最新レコードの `skill_name` を引く（tz suffix の混在があるため辞書順比較は使わず
  `datetime` にパースして比較する＝`pitfall_iso8601_lexical_compare_tz_suffix`）
- 産出量の計測: 各種別の生成関数を個別 import して直接呼ぶ（`run_discover()` 全体は回さない）

**実行可能な再現スクリプト**: `scripts/bench/measure_467_proposal_kinds.py`
（join ロジックの単体テストは `scripts/lib/tests/test_measure_467_join.py`）。

```
python3 scripts/bench/measure_467_proposal_kinds.py \
  --output docs/decisions/drafts/artifacts/467-measurements-2026-08-16.json
```

出力 artifact（2026-08-16 に本スクリプトで再実行した結果。rev5 初版記載値との差分の説明つき）:
[`docs/decisions/drafts/artifacts/467-measurements-2026-08-16.md`](artifacts/467-measurements-2026-08-16.md)
/ [`.json`](artifacts/467-measurements-2026-08-16.json)。

**以後、§1.5.1/§1.5.3 の [実測] 値は artifact を正典とし、本文はそれに合わせて更新済み。**
スクリプト化で rev5 初版の記載に**2つの誤りが見つかった**（どちらも本節の結論は変えない）:

| 項目 | 初版 | 訂正後 | 誤りの内容 |
|---|---|---|---|
| `usage.jsonl` の Skill 呼び出し総数 | 5,574 | **888** | 総行数を数えており Agent 呼び出し 4,651 件を除外していなかった |
| correction に先行する Skill 呼び出し | 28 / 171 | **30 / 172** | `usage.jsonl` の旧スキーマ行（`timestamp` キー）を拾い漏れていた |

§1.5.1/§1.5.3 の結論（SKILL.md 解決 0 件・型フィルタで型不一致・未接続13種の産出件数）は
この訂正では変わらない。

**2026-08-16 cold review 3巡目 [Must]1〜3 / [Should]1 の反映（本節・artifact 双方に適用済み）**:

1. SKILL.md 解決規則が `discover/runner.py:417-419` と不一致だった（測定側は global のみ
   探索・本番は global→project の両方）。同じ探索対象・順序に修正した。件数は 0/30 のまま不変
2. `--data-dir` が §1.5.3 の全入力を差し替えるわけではない。差し替え可能／不能を実装確認のうえ
   全件列挙し artifact 側に記録した（`session_store` union read は call-time override
   `session_store._DATA_DIR_OVERRIDE` を追加配線し差し替え可能にした）
3. 「LLM を呼ばない」「`~/.claude/` へ書かない」は grep 監査だけでなく実行時に証明するようにした。
   測定本体を execution-time guard（`socket.socket` / 書込みモード `open()` を検出したら即例外送出）
   で包んで実行し、結果を artifact の `safety_verification` に記録する（常時有効・フラグ不要）
4. join テストに同時刻境界（`dt == corr_dt` は先行 Skill 呼び出し無しと判定）のケースを追加した

再実行コマンドは同一（追加フラグ不要。上記1〜3 は常時有効化した挙動）。詳細は artifact の
「入力パスの全件列挙」「安全性検証」節を参照。

#### 1.5.1 パイロット `instruction_violations` は本番で 0 件（前提崩壊①）

以下の **[実測]** の値は §1.5.0 の再現スクリプトの出力（artifact）を正典とする。
実ストアは追記され続けるため総数は取得時刻で増える。rev5 初版執筆時の値と差がある行には
初版値を併記した。

| 観測 | 値 | 根拠 |
|---|---|---|
| `corrections.jsonl` 総数 | 172（rev5 初版 171） | 実ストア（全PJ共通・2026-05-15〜2026-08-16） |
| うち `last_skill` が truthy | **0 / 172** | 同上。初版 0 / 171 から結論不変 |
| 生成の足切り条件 | `c.get("last_skill")` が truthy な correction のみ | `discover/runner.py:392-393` |

**根本原因は検出器ではなく入力側**。corrections の書き手は2系統あり、支配的な方が
`last_skill` を **None 固定**で書いている:

| writer | 件数 | `last_skill` |
|---|---|---|
| `correction_semantic/promote.py:563`（朝の y/n で採用 → `source="reflect_confirmed"`） | 162 | `promote.py:365` で **`None` ハードコード** |
| `hooks/correction_detect.py:163`（`source="hook"`） | 2 | `read_last_skill(session_id)` 経由・実測 None |
| backfill | 8 | キー無し |

**[コード]** `hooks/observe.py:84-87` は `tool_name == "Skill"` のとき `write_last_skill` を呼ぶ。
**[実測]** `$TMPDIR` に当該一時ファイルが実在し `{"skill_name": ..., "timestamp": ...}` を保持、
`usage.jsonl` は **888 件**の Skill 呼び出しを記録。この2つから**書込み側が機能していると
観測される**（codex [Should] 反映: 「正常に動作」と断定していたのを観測表現に限定した。
TTL 内読取り・session_id 一致まで通した再現は行っていない）。

> **訂正（再現スクリプト化で判明）**: rev5 初版はここを **5,574 件**と書いていたが、これは
> `usage.jsonl` の**総行数**（5,576）であり、Agent 呼び出し 4,651 件と別スキーマ 37 件を
> 除外していなかった。Skill 呼び出しのみを数えると **888 件**である
> （判別規則は `scripts/lib/measure_467_join.py::is_skill_usage_record`、内訳は artifact 参照）。
> この訂正は本節の結論（先行 Skill 呼び出しは少数・SKILL.md 解決は 0 件）を変えない。

支配的な欠落は**採用経路が session→skill の対応を運ばないこと**であり、
一時ファイルの TTL（24h・`rl_common/workflow.py:16`）が主因である証拠は無い。

**[実測]** 修理した場合の上限: 同一セッション内で correction より前に Skill 呼び出しがあった
correction は **30 / 172（17%）**（rev5 初版は 28 / 171。差の +2 は corrections の追記分ではなく、
再現スクリプト化の際に `usage.jsonl` の**旧スキーマ行**（`ts` でなく `timestamp` キーを使う Skill 行）を
拾い漏れていたのを修正した分）。1 correction = 最大1 violation（**[コード]** `runner.py:440` の
無条件 `break`）なので violation の上限も 30 件。
なお **[コード]** `runner.py:398` の `_MAX_CORRECTION_CHECKS = 20` により、
**1回の discover が検査するのは最新 20 件まで**（30 はコーパス上限であって1 run の上限ではない
・tacchi [Nit]6）。

##### 訂正: 「1箇所の修理で2種別が稼働する」は誤り（tacchi [Must]1・2026-08-16 追加実測）

rev5 初版はここに「**1箇所の修理で2種別が 0 → 稼働に変わる**」と書いたが、**これは誤り**である。
追加実測により、`last_skill` を直しても**両種別とも 0 のまま**であることが判明した。

**`pitfall_candidates` — 型フィルタで落ちる**

**[コード]** `pitfall_manager/detection.py:162-166` は `last_skill` の前に
`correction_type in ("stop", "iya")` を要求する。
**[実測]** `correction_type` の内訳は `semantic_idiom` 162 / `stop` 8 / `iya` 1 / `naoshite-request` 1。
支配的 writer の `promote.py:362` は `correction_type` を **`"semantic_idiom"` 固定**で書くため、
`last_skill` を直しても 162 件すべてが型フィルタで落ちる。**通過しうるのは 9 件のみ。**

**`instruction_violation` — スキル名の名前空間が解決できない**

**[コード]** `runner.py:417` は `Path.home().glob(f".claude/skills/{skill_name}/SKILL.md")` で
**bare 名 + global dir** を前提に SKILL.md を探す。
**[実測]** 上記 30 件の直前スキル名を復元して同じ解決を試すと、**30 件すべてが解決不能（0/30）**。
内訳は `evolve-anything:spec-keeper` / `rl-anything:spec-keeper` / `evolve-anything:docs-refresh` 等の
**プラグイン名前空間付き**が多数を占め、残りは PJ ローカル・廃止済みスキル名。
これは既知 pitfall **#577/#578（bare vs `plugin:skill` の join キー名前空間不一致）**の再演である。

→ **修理単独の見込みは 0。** 追加で要る工事は**種別ごとに別物**である
（tacchi 再レビュー [Should]1 反映。初版は2つを混ぜて書いていた）:

| 追加工事 | 効く種別 | 理由 |
|---|---|---|
| (i) スキル名の名前空間正規化 + プラグインスキルのパス解決 | **`instruction_violation` のみ** | `runner.py:417` の glob が bare 名前提。`runner.py:392-393` の足切りは `last_skill` だけで型は見ない |
| (ii) `correction_type` フィルタの見直し | **`pitfall_candidates` のみ** | 型フィルタを持つのは `detection.py:162-166` だけ |

§10-Q1 の A 案（`instruction_violation` 復活）に要るのは **(i) のみ**。
(ii) は「`pitfall_candidates` も同時に稼働させたい場合」の工事であって A 案の前提ではない。

#### 1.5.2 §4.4「confidence を実データで較正」は実装不能（前提崩壊②）

production の `confidence` は**連続値ではなく2値固定**である:

| 経路 | confidence | needs_review | file:line |
|---|---|---|---|
| Stage1 対立動詞 | **0.95 固定** | 既定 False | `critical_instruction_extractor.py:355-363` |
| Stage2 keyword overlap ≥3 | **0.50 固定** | **True 固定** | `critical_instruction_extractor.py:366-375` |

LLM Judge 経路（`emit_violation_judge_requests` / `ingest_violation_judges`・:397-508）は
**production から一度も呼ばれていない**（caller は自己参照とテストのみ。ADR-037 Phase 1d-i で
2相 API 化したが SKILL.md への配線が入っていない）。
`needs_review=True` を除外した時点で残るのは 0.95 のみなので、**閾値を
`0.50 < t <= 0.95` の範囲のどこに置いても結果が変わらない**（codex [Should] 反映:
「どこに置いても」は境界外＝`t > 0.95` なら全件落ちるので誤り。範囲を限定した）。
**「実データで較正する」という MUST は、この範囲内では現行コードに対して意味を持たない。**

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
| `pitfall_candidates` | 0 | — | `pitfall_manager/detection.py:162-166`（§1.5.1。**足切りは2段** = 型フィルタ + `last_skill`） |
| `hook_candidates` | 0 | — | `discover/errors.py:15` 閾値3に未達 |
| `instruction_violation` | 0 | — | `runner.py:392-393`（§1.5.1） |
| `verification_needs` | 0 | — | `verification_catalog/runner.py:80-100` 全件導入済み判定 |
| `stall_recovery_patterns` | 0 | — | 閾値 `STALL_RECOVERY_MIN_SESSIONS=2` は `tool_usage_analyzer/__init__.py:47`、フィルタは `stall.py:110-113`（codex [Should] 反映: 旧記載 `stall.py:47` は単一セッション内走査の行で誤り） |
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

#### 1.5.4 rev4 が見落としていた構造的事実（**rev5 レビューで結論を訂正**）

##### 訂正前（rev5 初版）の主張 — 誤り

> 中身が出る3種はいずれも「新しいファイルを作る」提案なので §1.4 に直撃する。
> `instruction_violation` は唯一の「既存ファイル書き換え」型だが産出 0。
> ゆえに**「産出がある種別」と「既存 lane に乗る種別」の積集合が空**であり、
> §5 は Stage 1 の前提へ格上げされる。

codex [Must]1 と tacchi [Must]2 が独立に否定した。**「積集合が空」は成り立たない。**

##### 訂正後 — `rule_violation_observed` は既存ファイルの書き換えである

**[コード]** `rule_violation_lane.py:339-341` の `_enforcement_hook_script_path()` は
出力先を `~/.claude/hooks/enforce-prohibited-commands.py` に固定している。
**[実測]** このファイルは **2026-07-31 に既に導入済みで実在する**（グローバル rules にも
「PreToolUse hook で機械 enforce 済み」と記載がある）。

したがって**この環境では**この提案は **op = modify（既存ファイルの書き換え）** であり、
§1.4 / §5 の「新規ファイル作成」制約に当たらない。
`instruction_violation` の産出が 0 であっても、積集合は空にならない。

> **重要な限定**（tacchi 再レビュー [Should]2 反映）: hook が実在するのはこのマシンの
> [実測] であって**種別の性質ではない**。§1.5.0 で等級を分けた意味を、結論部で
> 台無しにしないこと。B 案の前提工事（実在チェック）を入れた後の挙動は環境で分岐する:
>
> | 環境 | 提案の op | §5 依存 |
> |---|---|---|
> | hook 実在 + 対象コマンド未収載 | **modify**（prohibited set への追記） | 不要 |
> | hook 実在 + 対象コマンド収載済み | 提案が抑制される（＝そもそも出ない） | — |
> | hook 不在 | **create** に戻る | **必要** |
>
> よって「B は §5 不要」は**原則不要（hook 実在環境）**と読むこと。
> なお「積集合が空は誤り」という訂正自体は、この限定を入れても
> D 案（scoped-C）の存在により維持できる。

##### ただし別の欠陥がある — detector が「もう直っている」ことを見ていない

**[コード]** `rule_violation_lane.py` に `exists()` の出現は **0 件**（`grep -c "exists()"` = 0）。
つまり hook の実在チェックがどこにも無い。
`partition_rule_violations`（def は :214、`reason` 付与は :252-256）も
hook 実在を条件に含めず、`reason="rule_installed_but_not_enforced"` を無条件に付ける。

**[実測]** 観測された `cd` 522 回は**hook 導入（7/31）より前の履歴を含む**。
つまり現状この提案を朝の y/n に出すと「**もう終わっている対処**を承認するか聞く」ノイズになる。

→ 塞ぐべきは §5 ではなく、**(i) enforcement hook の実在チェック
(ii) 観測の時間窓を hook 導入後に限定**の2点（tacchi [Must]2）。これは §5 より遥かに小さい。
この欠陥は #467 と独立に存在するので単独 issue として扱う。

##### 確定していること／していないこと

| | |
|---|---|
| **確定** | rev4 が選んだパイロット `instruction_violation` は、そのままでは実施できない（§1.5.1） |
| **確定** | §4.4 の閾値較正は現行コードに対して空文（§1.5.2） |
| **否定された** | 「積集合が空」「§5 は Stage 1 の前提」 |
| **未確定** | §5 が必要になるかは**パイロット選定の結果に依存する条件分岐**であり、事前に断定できない（§10-Q1 / Q2） |

##### 副次: 束ねの実装は既に存在する

`phases_remediate.py:96-103` が `make_hook_candidate_issues_from_rule_violations` を呼び、
**[コード]** `RULE_VIOLATION_HOOK_THRESHOLD=20`（`rule_violation_lane.py:28`）以上の違反 head を
**1つの hook scaffold に束ねて** issue 化している（同 :344-388）。
未接続なのは「朝の y/n レーン（`_extract_candidates`）」に対してのみ。
**rev4 が §4.2 で新設しようとした集約契約は、この関数の再利用で足りる可能性がある**（要検証・§10-Q3）。

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

## 4. パイロット選定 — **[撤回]**（契約設計 §4.1〜§4.3 は **[存置]**）

> **小節ごとの状態**（codex [Should] / tacchi [Should]3 反映。
> 「§4 全体を撤回」と書きながら一部を再利用可と述べていた矛盾を解消する）:
>
> | 小節 | 状態 | 理由 |
> |---|---|---|
> | §4 前文（パイロット = `instruction_violations`） | **[撤回]** | §1.5.1 の実測により本番 0 件。修理しても 0 が濃厚 |
> | §4.1 運搬契約（pending に `detail`） | **[存置]** | 種別非依存。`_emit.py:190-213` に `detail` 相当が無いことは 2026-08-16 に再確認 |
> | §4.1b suppression identity 分離 | **[存置]** | 種別非依存。`_suppression.py:23-50` が `entry["id"]` のみを一意性成分にすることは再確認済み |
> | §4.2 集約契約 | **[保留・再検証]** | 設計内容は妥当だが、`rule_violation_lane.py:344-388` に既存の束ね実装があるため**新設せず再利用**の可能性を先に潰す（§10-Q3） |
> | §4.3 表示・承認手順の種別非依存化 | **[存置]** | どのパイロットを選んでも必要 |
> | §4.4 品質ゲート | **[撤回]** | §1.5.2 のとおり実装不能 |
>
> **⚠ 以下の本文は撤回前の原文である。** §4.2 の本文が §4.4（撤回済み）を「④品質ゲート適用」として
> 必須段に組み込んでいる点に注意 — §4.2 を再利用する場合は④を差し替える必要がある。
> パイロットを選び直すまで、この本文の記述をそのまま実装根拠にしないこと。

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

## 5. 新規ファイル作成の accept / revert（**パイロット選定に依存する条件分岐**・方針のみ）

> **rev5 の変更（レビュー後に再訂正）**: rev5 初版は本節を「Stage 1 の前提へ格上げ」と
> **断定**したが、codex [Must]1 / tacchi [Must]2 が否定した。§1.5.4 のとおり
> `rule_violation_observed` は既存ファイルの書き換え（op=modify）なので本節に依存しない。
>
> **正しい位置づけ**: 本節が Stage 1 のクリティカルパスになるかは
> **どのパイロットを選ぶかに依存する条件分岐**である。
>
> | パイロット | §5 が必要か |
> |---|---|
> | A `instruction_violation`（既存 SKILL.md 更新） | **不要**。ただし §1.5.1 の追加修理 (i)（名前空間解決）が要る |
> | B `rule_violation_observed`（既存 hook の書き換え） | **原則不要**（hook 実在環境）。**hook 不在環境では create に戻り必要**（§1.5.4 の限定表）。代わりに実在チェック + 時間窓が要る |
> | C 3種一斉接続 / 新規スキル・新規 rule 作成を含む | **必要** |
> | D scoped-C（rules/hook の create に限定し revert=削除で定義） | **一部必要**（§5 の12点のうち削除で自明になる分を除く。tacchi 提案） |
>
> したがって独立 ADR を起票するかは §10-Q1 の決着後に決める（§10-Q2）。

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
以下 Q0〜Q5 に決着がつくまで実装コードを書かない。

**着手順**（tacchi 指摘: Q1〜Q5 が並列に見えるが実際には順序がある）:
**Q0（機構化）→ Q1（パイロット確定）→ Q1 の結果に応じて Q2 / Q3 / Q4 → Q5（上限）**。
Q0 を先に置くのは、Q1 の答えを出す作業そのものが Q0 の受入条件を満たす必要があるため。

### Q0. 同じ失敗（設計4巡・実測ゼロ）を機構で防ぐ — **最優先**（codex [Must]2 / tacchi [Should]4）

今回の失敗の本質は「rev1〜rev4 で codex を3巡通したのに、**実データを1度も測らなかった**」こと。
構造要因は **§4.4 が実測を MUST と書きながら、その実行時期を設計レビューの*後*（Stage 1 着手前）に
置いた**ことである。文書1本の反省文で終わらせず機構にする。

決めるべきこと:

1. **設計文書の各前提に evidence 行を必須化するか。**
   形式案: 前提ごとに `値 / 取得コマンド / 取得日 / 対象 commit` の4点を書く。
   **evidence 欄が空の前提が1つでもあれば `design-review-gate` のレビュー開始条件を満たさない**とする
2. **パイロット候補の受入条件を「production と同じ入口からの dry-run 実測」にするか。**
   測るのは 産出数 / 朝の y/n への到達 / accept / reject / revert の5点。
   合成 fixture での確認では受入としない（`learning_synthetic_fixture_false_confidence`）
3. **測定を再現可能な artifact にするか。** §1.5.0 のとおり、今回の [実測] は
   リポジトリ内にスクリプトが無く他者が再現できない。
   `scripts/bench/` 等に置いて対象 commit と取得日を残す形にするか
4. **上記を本 draft 限りにせず、`docs/decisions/drafts/` のテンプレートと
   `design-review-gate` ルール本体に反映するか**（tacchi 提案）

> **注**: Q1-A に個別の再計測注意を書いてあるが、それは今回の事例の再発防止にすぎず、
> B / C / D や将来の種別に一般化されない（codex [Must]2）。一般化するのが Q0 の役目。

### Q1. パイロットをどう選び直すか

**rev5 初版の「産出がある ∩ 既存 lane に乗る = 空集合」は誤り**（§1.5.4 訂正済み）。
実測を反映した4案:

| 案 | 内容 | 前提になる工事 | 実測に基づく見込み |
|---|---|---|---|
| A | `instruction_violation` を復活させる | ①`last_skill` の運搬（#478）②**スキル名の名前空間正規化 + プラグインパス解決**（#577/#578 の再演）— **この2つだけ**（型フィルタは `pitfall_candidates` 側の工事なので A に不要・§1.5.1 の帰属表） | **0 が濃厚**。30件の直前スキルは**全件 SKILL.md 解決不能**（§1.5.1 訂正）。①だけでは動かない |
| B | `rule_violation_observed` を `violated_command` で束ねて出す | ①enforcement hook の**実在チェック** ②観測の**時間窓を hook 導入後に限定** ③束ねの再利用（§10-Q3） | y/n **1件**。§5 は**原則不要**（hook 実在環境では op=modify。**hook 不在環境では create に戻り §5 に依存**・§1.5.4 の限定表）。①②なしでは「もう終わった対処」を聞くノイズになる |
| C | 3種を一斉接続 | §5 の12点すべて + 独立 ADR | y/n 最大 12〜25件／日。上限設計（Q5）が必須 |
| D | **scoped-C**: rules / hook ファイルの create に限定し、**revert = ファイル削除**で定義（tacchi 提案） | §5 の12点のうち削除で自明になる分を除いた最小契約 | 冪等 revert が自明（削除）なので ADR が薄く済む。C への段階的な入口になる |

**A を採る場合の必須条件**: 「修理すれば動く」は**仮説であって実測ではない**。
①②③をすべて入れた後に**実際に何件出るかを測ってからパイロット確定**とし、
0 件なら A を破棄する（`learning_dryrun_verification_blind_spot`）。

**B を採る場合の必須条件**: 実在チェックと時間窓を入れた**後**に、
`cd` 以外に提案が残るかを測る。残らなければ B も 0 件になる。

### Q2. §5（新規ファイル作成の accept / revert）を独立 ADR として先に起票するか

**Q1 の結果に依存する条件分岐**（§5 冒頭の表を参照）。
A / B を選ぶなら §5 は不要、C なら必須、D なら一部必要。
**Q1 を決めずに §5 の ADR を起票しない**（rev5 初版はこれを断定していた・codex [Must]1）。

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

本 rev5 は**実測の記録と問いの整理**であり、Q0〜Q5 の**答えを含まない**。
答えを書いた rev6 に対して `design-review-gate` に従い codex 1巡を通し、
`[Must]` が残る間は実装に着手しない。

**rev6 の追加要件（Q0 の先取り）**: rev6 の各前提には
`値 / 取得コマンド / 取得日 / 対象 commit` の evidence 行を付ける。
evidence の無い前提を根拠に判断を書かない。

---

## 11. rev5 のレビュー記録（2026-08-16）

| レビュアー | 判定 | 主要指摘 | 反映 |
|---|---|---|---|
| codex（cold review） | `マージ不可`（[Must]3 / [Should]4） | ①§5 格上げは導けない ②再発防止の検証契約が無い ③[実測] 群に再現証跡が無い | ①→§5 冒頭を条件分岐に ②→Q0 新設 ③→§1.5.0 新設 |
| tacchi（実態突合） | 直してからマージ | ①「1箇所の修理で2種別稼働」は実測に反する ②`rule_violation_observed` は既に対処済み対象で op=modify | ①→§1.5.1 に訂正節 ②→§1.5.4 の結論を訂正 |

**2巡目（差分レビュー）**:

| レビュアー | 判定 | 残った指摘 |
|---|---|---|
| codex | `マージ不可` | [Must]C **未解消** — [実測] に実行可能な再現スクリプトと保存 artifact が無い。他の A/B/D/E/F/G と追加2件は解消 |
| tacchi | 直してからマージ（**修正後の再レビュー不要**） | [Should]1 A 案の前提工事③が種別違い ／ [Should]2 「B は §5 不要」が [実測] を種別の性質に一般化 ／ [Nit]2件。前巡7点は全て解消 |

tacchi の [Should]2 件と [Nit]2 件は本 commit で反映済み（§1.5.1 の帰属表 / §1.5.4 の限定表 /
`exists()` 出現 0 件 / `partition_rule_violations` は def :214・reason 付与 :252-256）。
codex [Must]C は §1.5 の実測を再現するスクリプトと出力 artifact の追加で解消する。

**両者が独立に否定した点**: 「積集合が空 → §5 は Stage 1 の前提」。
**tacchi の追加実測が rev5 初版より状況を悪化させた点**: A 案は 30件全件 SKILL.md 解決不能で
「0 の可能性あり」ではなく「0 が濃厚」。
**tacchi が追加した選択肢**: Q1-D（scoped-C）。

この節を残す理由: rev5 初版が「実測を称える文書自身が、実測していない太字の約束を2つ置いていた」
という失敗を記録するため。同種の失敗の再発防止は Q0 で機構化する。
