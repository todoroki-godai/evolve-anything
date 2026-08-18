# ADR-054: 4本柱（#379 目標体験）完成設計

- **Status**: Accepted
- **Date**: 2026-08-12
- **関連**: #379（CLOSED）/ #400（OPEN）/ #401（OPEN）/ #402（マージ済み）/ #442 / #443 / #444 / #445 / #446 / #447 / ADR-041 / ADR-053

> **この文書は単体で完結している。**会話文脈なしで再開できるよう、実測値・判断の根拠・却下した案を全て含む。
> レビュー: tacchi（体験・過剰約束）+ codex（設計の正しさ）を各1巡。指摘は §9 に反映箇所を記載。
> 実測は全て 2026-08-12。
>
> **この ADR は #379 の「新設凍結解除の条件＝#400-#402 の実装方針確定」を満たす文書である。**

## Context

2026-08-04 にユーザーと合意した目標体験（#379）:

> **「記録は全自動・判断は朝の30秒・効果は週1の数字で実感」**
>
> 1. **普段**: 各PJで普通にチャットするだけ。プラグインの存在を忘れている（observe は全自動・無音）
> 2. **朝**: セッション開始の1行通知 → 改善案を**ユーザーの言葉**で1件ずつ提示 → y/n だけ
> 3. **週1**: 戦果ボードで「手直し回数の減少」「採用した改善が効いたか」を数字で見せる。効いていないものは自動で取り下げ候補
> 4. **信頼**: 表示する数字が嘘をつかない / 適用は必ず人間の y/n / skill 採用は1コマンドで戻せる

#379 本体は 2026-08-10 に CLOSED（縮小 Step 0〜4 完走）。残ギャップは **#400 / #401 / #402** に引き継がれ、
#402 は 2026-08-12 マージ済み、**#400 / #401 は OPEN**。
#379 最終コメントは「新設凍結は **#400-#402 の実装方針が固まるまで現状維持**」と定義しており、
**本設計書がその凍結解除の条件そのもの**。

---

## 1. 総括 — 4本柱の実際の到達度

| 柱 | 判定 | 一言 |
|---|---|---|
| 1. 普段（記録は全自動） | **❌** | correction の記録レーンが**事実上死んでいる**（hook 由来は3か月で1件）。加えて記録の 23.2% が subagent 由来の混入 |
| 2. 朝（30秒の判断） | **⚠ 配線◯ 中身✗** | 通知が9系統・提案の 50% が委譲プロンプト・並び順が構造的に FP を上位固定 |
| 3. 週1（効果を数字で） | **❌** | 起動導線ゼロ・有効 accept 0件・「手直し」を測る指標が全滅 |
| 4. 信頼 | **⚠** | 無人適用なしは成立。revert は実装済みだが対象0件。数字が嘘をつく箇所が複数残る |

**当初「柱1は完成」と見ていたが、実測の結果それも成立していなかった。**

---

## 2. 実測（全て 2026-08-12・根拠つき）

### 2.1 柱1 — correction capture が壊れている【最重要】

**corrections.jsonl 151件の writer 別内訳**

| writer | 発火条件 | `source` | 実件数 |
|---|---|---|---|
| `hooks/correction_detect.py:handle_user_prompt_submit` | UserPromptSubmit ごと。正規表現28本にヒット時 | `hook` | **1**（2026-07-01 のみ） |
| `correction_semantic/promote.py:promote_signals` | 人が evolve/reflect で weak_signals を承認したバッチ | `reflect_confirmed` | **142** |
| `idiom_autopromote.py`（ADR-047） | 確認済み idiom 一致時 | `idiom_dict` | **0**（#379 Step1 で凍結中） |
| `scripts/backfill_preceding_tool_calls.py:_persist` | 手動 `--persist` 時のみ | `backfill` | 8 |

**検出器リプレイ実測**: 07-27 以降の dialogue 発話 **2,841件**に `should_include_message` + `detect_correction` を適用 → **マッチ 0件**。
- 500字超で除外: 1,503件
- skip / machinery 除外: 214件
- 修正語（違う・やめて・直して・言ったよね 等）を含む発話: **108件** → フィルタ通過 31件 → **正規表現ヒット 0件**
- 検出漏れ実例: `2026-07-31「なんで、matsukaze-mindenでコメントしちゃったの、、、時々あるからやめてほしい、、、」`
- 原因: 28 パターンが `^違う[、，,.\s]` / `^いや` / `^no[,. ]+` のような**行頭アンカー中心**

**帰結**
- `sessions.jsonl` 全 2,517行の `correction_count` は**すべて 0**（`count_session_usage` が corrections.jsonl を session_id で引くため構造的に常時 0）
- 週次 corrections 系列は**手直し量ではなく reflect の実行頻度**を測っている
  （書込のあった日: 直近30日で 6日のみ・最長連続ゼロ 11日）

### 2.2 柱1 — 記録の 23.2% が subagent 由来

- `isSidechain` の判定が `utterance_archive/extractor.py` にも `weak_signals/detectors.py` にも**存在しない**
  （走査: `grep -rn "isSidechain|sidechain" --include='*.py' scripts hooks` → ヒットは `token_usage_store.py` 系のみ）
- utterances.db 全 dialogue: `main 7950 / sidechain 2401 / 元ファイル消失 2728` → **解決可能分の 23.2%**
- 汚染は rephrase だけでない: llm_judge 336件中 **33件**が `provenance.source_path` に `/subagents/` を含み、
  **うち2件は promoted=True で corrections まで到達済み**

### 2.3 柱2 — 朝の導線

**(a) 通知が9系統**
`hooks/restore_state.py:handle_session_start` は集約点を持たず 9 個の deliver 関数が順に stdout へ吐く（今朝は4本）。
`restore_state.py:466-484` に「proposal と checkpoint は同一行へ merge」の既存契約があり、
**`hookSpecificOutput` を含む JSON 行は高々1つ**でなければ片方が黙って捨てられる（#412 Must2）。

**(b) 提案の 50% が委譲プロンプト**
rephrase 検出の除外は `_DISPATCH_MARKERS`（文字列10個）のみで、`rl_common.detection.is_machinery_prompt` に
**委譲していない唯一の実装**（機構ターン除外は他に3実装ある）。
今朝の 12 group（signal_keys 14）: 委譲プロンプト由来 **7件（50%）** / 人間発話 7件。
`MAX_SESSION_PROPOSALS=2` により通知に出る**先頭2件が両方とも委譲プロンプト**だった。

**(c) 並び順が構造的に FP を上位固定**
`daily_review.build_review:387-399` は頻度降順 → cross-PJ confirmed 優先 → 上限で切る。
rephrase は「同一の長文を複数回投げる」ため構造的に count が大きい。
`_exclude_bootstrap_consumed` により **figma-to-code / amamo は llm_judge が構造的に1件も出せない**。

**(d) llm_judge 滞留 10,225件**（`DEFAULT_DAILY_UTTERANCE_LIMIT = 200`）。新規流入ゼロ仮定で **52日**。

**(e) 汚染を除いた提案の中身は y/n を押す価値がある**（tacchi が実データで確認）
実文例:「フルスイートをなんで頭でまわしちゃったの？token無駄じゃん」「テスト結果をコピーできるようにしてほしい」

### 2.4 柱3 — 見せる数字が存在しない

**accept の実測（数え方で見え方が変わるので全て併記）**

| 数え方 | 件数 | 意味 |
|---|---|---|
| `human_accepted=True` | 10 | 人間が y/n で採用した記録。ただし**全10件が `invalidated_at` 有り・`fitness_eligible=False`**（#376 legacy 無効化） |
| `approved=True` | 2 | run_loop 経路。うち1件はテスト汚染パス |
| `classify_decision` → accepted | 1 | 唯一の accepted は `id=None`＝**revert 対象にもならない** |
| **有効な人間 accept** | **0** | ← 柱3(b) の実質的な分母 |

（`excluded` は全PJ 40 / evolve-anything 単体 35。内訳はテスト汚染パス30 + legacy 無効化10。最終書込は 2026-08-04）

**3軸の状態**

| 軸 | 状態 | 理由 |
|---|---|---|
| `rework_rate` | **測定不能** | `sessions.jsonl` 全2,487件に `tool_sequence` が0件（writer が集計値しか書かない） |
| `correction_recurrence` | **測定不能（恒久・A5 で裁定済み）** | `correction_type` の語彙が実質1種（distinct_types=1 < floor 5）。**A5（drafts/054-a5-correction-category.md §2.0）で `correction_type` は変更しない・`correction_recurrence` は復活させないと確定**。floor 到達後の「粗い語彙で再発率1.0に張り付く」構造劣化は `outcome_metrics.correction_recurrence_rate` の決定論飽和ゲート（`recurring==distinct_types` かつ `rate>=0.9` → `reason="saturated"`）で恒久防止 |
| `first_try_success` | 0.9403 | **唯一動いている軸** |

per-skill 帰属: 147 skill 中 first_try_success non-null 46 / rework 0 / recurrence 0 / degraded 101（69%）。
起動導線: **ゼロ**（`bin/evolve-audit "$(pwd)" --growth` を手で叩く以外に到達経路なし。hooks 26本・bin 26本・daily runner に参照なし）。
取り下げ候補: **0件**（REGRESSED 7件は全てテスト汚染パスで excluded）。

### 2.5 柱4 — 信頼

- **無人適用なし: 成立**。launchd は `com.evolve-anything.daily` **1本のみ**。書込先は queue/icebox の4ファイル + 蓄積系のみで、skill/rules/hooks には一切触らない
- `safe_llm_call` は4重防御（`--tools ""` / `--strict-mcp-config` / `--safe-mode` / `--settings` deny）
- **revert: 実装済みだが対象0件**。revert 情報を保存するのは **emit→drain 由来の accept だけ**（`evolve_decisions/_ingest.py:120-148`）。
  `optimize.py::save_history_entry` / `run_loop.py` 経由は**今後も** `pre_extension`
- entry_id を人に見せる導線は**戦果ボード1箇所のみ**。現状は全件が「戻せません」分岐

### 2.6 数字が嘘をつく箇所（全リスト）

1. `rework_rate` が `no_data` を返す — sessions は 2,487件あるのに「データが無い」と表示（実際は**測定していない**）
2. 戦果ボードの rework headline「0→10件に増加」— previous_30d の raw は1件。**分母1桁の比較を断定形で出している**
3. **`sessions.jsonl` の行数は Stop フック発火数**。W32 は 1,543行だが unique session は **298**（約5倍の過大計上）
4. audit の Correction Capture が **分母 n=2 で「枯渇兆候なし ✓」と断言**（閾値判定に最小分母が無い）
5. 同セクションが `source` を見ずに channel 名を `hook` と決め打ち（実際は `reflect_confirmed`。当PJの hook 由来は30日窓内0件）
6. corrections の `project_path` 空8件 — 全PJ合計と PJ別合計が一致しない。どこにも surface されない
7. `excluded` の**理由**（テスト汚染 / legacy 無効化）が画面に出ない
8. 週次表示を素直に実装すると**欠測週が「改善した週」に化ける**

### 2.7 観測→作用 変換率

```
全期間 promoted/total: 142/1170 = 12.1%
直近30日:                9/659  =  1.4%
  esc_interrupt         0/586 (0%)   ← 最大の観測源が作用ゼロ
  manual_edit_after_ai  0/146 (0%)
  llm_judge           123/336 (36.6%)
  rephrase             11/88  (12.5%)
```
advisory の decision lane は開通以来 **1件も書かれていない**（`advisory_decisions.jsonl` がファイルごと不在）。

### 2.8 分母として使える実データ（利用可能期間つき）

| 候補 | 実測 | 期間 | 備考 |
|---|---|---|---|
| `utterances.db` の dialogue | 13,079件 | 2026-05-13〜08-11 | **会話ターンに最も近い唯一の分母（推奨）** |
| `sessions.db` + `sessions.jsonl`（union read） | 118,848 + 2,516行 | 2026-03-25〜08-12 | 1行=Stop発火。**unique session_id で数え直しが必要** |
| `usage.jsonl` | 5,405行 | 2026-03-25〜08-12 | tool/skill 呼び出し数。会話ターンではない |
| `token_usage.db` | 595,217行 | 2026-04-28〜08-12 | uuid 単位のアシスタント応答。最も密 |

`sessions.jsonl` が W31 以前に無いのは `session_store.ingest()` が取り込み後に rotate するため。
**過去分は `sessions.db` に残っており `session_store.query`（db + 未 ingest jsonl の union read）で全期間読める**。

---

## 3. 中核の診断

### 3.1 朝の y/n と週1の accept は**別レーン**（codex [Must]）

```
レーン1（朝）: 観測 → weak_signals → 朝の y/n
                → evolve-reflect --promote-weak / --reject-weak
                → corrections.jsonl（human-confirmed correction）
                                          ↓
              ★ emit→drain の skill-diff candidate lane には入らない ★
                                          ↓
レーン2（週1）: evolve 実行中の pending proposal
                → 対象ファイルの実変更 AND 明示 accepted ID
                → optimize_history の accept → 戦果ボード / revert
```

根拠: `daily/proposal_digest.py:393-460`、`skills/evolve/references/correction-review.md:63-64`（朝の y/n の実体）
／ `skills/evolve/SKILL.md:288-303`、`evolve_decisions/_ingest.py:83-107`（accept の発生条件）

**Phase A/B が直接増やすのは human-confirmed correction であって accepted decision ではない。**
真のボトルネックは記録量ではなく、**correction → 適用可能な skill diff → 明示 drain の変換・起動経路が存在しないこと**。

**2026-08-14 訂正（設計正典: plan `drafts/054-phase-be-design.md` §1.2(1)。codex [Should]1）**:
「★ ここに変換経路が無い ★」は不正確だった。**レーンごとに答えが違う**:

| レーン | 判定 | 根拠 |
|---|---|---|
| corrections → evolve の **emit→drain skill-diff candidate lane** | **経路が無い** | `_candidates.py:79-125` の入力は discover の `matched_skills` と skill_evolve の `assessments` だけ。discover 側の入力は `usage.jsonl` / `errors.jsonl` / `optimize_history.rejection_reason`（`runner.py:186-200` 他） |
| corrections → genetic optimizer / evolve-loop の `collect_corrections` | **入力としては存在するが実質空** | writer（`correction_semantic/promote.py:277-288`）が consumer の2フィルタ（`reflect_status != "applied"` かつ `last_skill` 部分一致）両方に落ちる値をハードコードしており、実データ162件の `last_skill` は全件 `None` |
| corrections → pitfall / hook candidate / instruction violation | 経路はあるが `matched_skills` に合流しない | `runner.py:365-455` |
| 過去10件の accept | **corrections 由来ではない** | 全件 `skill_evolve:medium`・`decision_source` 無し＝#376 以前の hash-proxy 誤検出。全件 invalidated |

**この厳密化を踏まえた Phase E の設計は §5 Phase E（2026-08-14 rev）を参照。**

### 3.2 柱3は2つの数字の合成で、依存先が違う

| 系 | 数字 | 依存 |
|---|---|---|
| **(a) 手直しの減少** | `rework` / `correction_recurrence` | **accept に非依存**。記録の質（capture 修理）+ 語彙修正で成立 |
| **(b) 採用した改善が効いたか** | accept 効果・取り下げ候補・revert | **レーン2の accept が発生すること**に依存 |

### 3.3 依存の全体像

**（2026-08-12 A0 実測を受けて訂正済み。旧図は「A0 が (a) 系の数字を作る」と読めたが、実際は作らない）**
**（2026-08-13 再訂正: C(a)/C(b) を分離し、G1 計測ゲートを挿入した。codex [Must]4 / tacchi [Must]2）**

```
capture の修理（A0・完了）── 「hook レーンが死んでいる」を止める（precision 87.5%・recall 約4.5%）
       │                     ※ (a) 系の数字を A0 単独では作れない（§5-A0）
       ↓
sidechain 除去（A2 → A1）── 提案の質・llm_judge 滞留
       ↓
   ┌── G1 計測ゲート ──────────────────────────────────────────────┐
   │ correction_semantic（llm_judge）レーンの recall/precision を   │
   │ 固定実コーパスで実測する。**通らなければ C(a) は作らない**     │
   └───────────────┬──────────────────────────────────────────────┘
                   │ 通過                        │ 不通過
                   ↓                             ↓
        A5（分類語彙）→ C(a) 週1の数字     C(a) は捨てる。柱3(a) の headline は
                                            first_try_success 一本に絞る（§7.4）

朝の提示（B）── A2 後。G1 とは独立
C1/C2（数字の正直さ・欠測表示）── **E にも G1 にも非依存**。A/B と並行して先に出す
correction → accept の変換経路（E）── これが無いと (b) 系が永久にゼロ
       ↓
C(b)（採用の効果・取り下げ候補・C4）
```

**G1 のコスト**: 新規ラベリング不要。A0 が作った正解ラベル（`a0_eval_set.jsonl` の TP 10件 +
census TP 7件）が weak_signals の llm_judge レーンに何件現れるかを突合するだけ。

---

## 4. 設計方針

- **P1: 上流優先**。記録の質を直さずに下流の表示を作らない
- **P2: 文字列 allowlist でなく構造フラグ**（`learning_detector_fp_context_not_allowlist.md`）
- **P3: 新設ゼロで解く**。凍結は「表示場所」でなく **store / observability builder key / advisory adapter / weak-signal channel の4集合**（`shrink_freeze.py:23-37,61-179`）。既存 store の read-only bin・既存 SessionStart 分岐は非抵触
- **P4: 数字は「測っていない」と「データが無い」を区別する**（silence ≠ evaluated）
- **P5: 対策を2つ積む前に1つ目の効果を測る**
- **P6: 派生状態は read 時導出**（`learning_derive_state_from_logs_not_forward_write.md`）
- **P7: 「決めていないのに決めたつもり」を残さない**。二択のまま Phase に入れない
- **P8: 検出器は実コーパスでリプレイ検証する**。合成 fixture の緑は false confidence（`learning_synthetic_fixture_false_confidence.md`。今回まさに28パターンが3か月1件だったのを実コーパスで発見した）

---

## 5. Phase 構成

### Phase 0 — 通知の1行化【柱2・他と独立・最速】

| ID | 内容 | 実装先 |
|---|---|---|
| B1 | SessionStart 通知を **1 JSON dict にマージ**して1行化 | `hooks/restore_state.py:handle_session_start` |

**設計要件**
- 出力は**必ず単一 JSON dict にマージ**（`hookSpecificOutput` を含む行は高々1つ・`restore_state.py:466-484`）
- **仕分け表を実装前に確定**: 9系統それぞれを ①人間向け `systemMessage`（1行に集約）／②Claude 向け `additionalContext`（潰さない）／③stderr health alert のどれに載せるか（ADR-038）
- **「1行」は平常時の1行**。異常時（producer 停止・#351 の16日沈黙）は必ず surface する。
  **何が起きたら何行になるかの表示契約**を設計に書く
- 現行9系統: pending_trigger / spec_drift / evolve_drain / data_dir_migration / utterance_staleness /
  evolve_queue_notice / session_proposal / judge_cap / icebox

### Phase A — 記録の質【柱1・全ての前提】

| ID | 内容 | 実装先 |
|---|---|---|
| **A0** | **correction capture の修理**（最優先） | `hooks/correction_detect.py` |
| A1 | `isSidechain` 除外を**記録層で根治** | `utterance_archive/extractor.py` + 再 ingest migration |
| A2 | `_DISPATCH_MARKERS` 文字列 allowlist を廃止し構造フラグ + `is_machinery_prompt` 委譲へ統合 | `weak_signals/detectors.py` |
| A3 | 既存 FP の後始末（rephrase 16 / llm_judge 33 / **corrections 昇格済み2**） | — |
| A5 | **rev2/rev3 でスコープ縮小（§2.0）**: `correction_type` は変更しない・`correction_recurrence` は復活させない。judge 判定時に `provenance.category`（対象軸8値 enum）を1つ付け、C(a) の TP をカテゴリで分解して見せるだけにする | `correction_semantic/` + `correction_rate.py` |

**A0 — correction capture の修理【新設・最優先】**
現状: 3か月で hook 由来1件。実コーパス 2,841発話でマッチ0件。修正語を含む31件（フィルタ通過後）にも0ヒット。

**⚠ 2026-08-12 実コーパスリプレイで本 ADR の当初方針1・2は否定された**（設計 `drafts/054-a0-capture-repair.md`）。
固定窓（2026-07-27 〜 08-12・両端固定）で本番 `detect_correction` をそのまま回した結果:

| 当初方針 | 実測結果 |
|---|---|
| 1. 行頭アンカーを外す | **効果ゼロ**（新規検出0件）。アンカーは主因ではなかった |
| 2. 500字超の一律除外を見直す | **効果ゼロ**（新規検出8件が**全件 FP**）。除外は妥当だった |

**真因は語彙の欠落**（`直して` / `修正して` / `訂正して` / `やめてほしい` が28パターンのどれにも無い）。
確定方針:

1. **`CORRECTION_PATTERNS` に2件だけ追加する**（`naoshite-request` / `yamete-request`）。
   複合動詞（作り直して・書き直して等）は lookbehind 拡張で構造的に除外する（P2）
2. `_MACHINERY_MARKERS` に1行追加し、harness 生成の停止通知本文（`N background agents were stopped by the user: ...`）を構造的に除外する
3. `pattern_version` フィールドを追加し、**`capture_rate.py` を source × pattern_version で層分離**する。
   これにより §2.6-5 の「channel 決め打ち」も同時に解消する（A0 に同梱・別 PR にしない）
4. **凍結との関係**: 既存 hook → corrections.jsonl の直接書込を改善する範囲＝**新 channel でないので非抵触**。
   weak_signals 経由に作り替える案は新 channel になりうるので採らない

**A0 の効果は小さい（誠実な見積もり）**: 新規検出9件・真陽性7件＝**precision 77.8%**（Wilson 95% CI 45.3〜93.7%。
`_MACHINERY_MARKERS` 追加後は 7/8 = 87.5%）。一方 **recall は低いまま**で、母集団の真の修正発話 ≈155件に対し
点推定 **約4.5%**。すなわち:

> **A0 単独では柱3(a) の分子は作れない。** A0 は「hook レーンが実質死んでいる（3か月1件）」状態を
> 低コスト・高 precision で終わらせるものであり、recall の底上げは `correction_semantic`（llm_judge 意味判定）の役割。

したがって §3.3 の「A0 が無いと (a) 系の数字が永久に嘘」は**半分だけ正しい**（A0 は嘘を止めるが、
真の数を作るのは A5 + `correction_semantic` 側）。§7.2 の分子の最終形もこれを前提に決める。

**A1 — 記録層で根治（ユーザー判断 2026-08-12）。必須の付随設計**
`EXTRACTOR_VERSION` は記録するだけで consumer が無い（`extractor.py:26-31`）＝**再 ingest 機構は未実装**。
ingest は mtime/offset の増分判定で既処理ファイルをスキップし（`ingest.py:84-99`）、既存行は `ON CONFLICT DO NOTHING`（`store.py:31-66`）。
→ **除外を足すだけでは既存行は変わらない。**必要なもの:
1. トランザクション内で旧 version 行と対応 `ingest_state` を削除して再抽出する migration、**または** DB 全再構築 + atomic swap
2. 失敗時 rollback と再実行冪等性
3. wall time 実測（PJ rule `transcript-store-bench`: 9,925 jsonl / 1.9GB で 75分暴走の前科）
4. `correction_judged.jsonl` の既判定キー（`f"{source_path}:{line_no}"`）が再 ingest 後と突合できるか。
   **2026-08-13 実測（`a1-cost-estimate`）: 「最悪 3,604件の再判定」は実データで再現できなかった。**
   `line_no` の採番方式（`enumerate(f)` 由来の物理行番号）を変更しなければ既判定キーは再 ingest 後も
   突合できる。実測内訳: 既判定 3,052件 / 未判定 10,419件（**A1 と無関係の既存バックログ** —
   sidechain 除外や prev_action 充填とは独立に、A1 着手前から未判定だった発話） / 孤児キー
   （transcript ファイル消失等で突合先が無いキー）750件。
   費用実測（worst case = キー突合に失敗し**既判定 3,052件**を再判定する場合）: 入力 587,157 tokens・
   Haiku 4.5・**概算 $1.27**。未判定 10,419件は A1 と無関係の既存バックログであり、A1 の着手判断には
   含めない（参考値: 入力 2,736,846 tokens・概算 $5.08。daily runner の 200件/日上限で流れる通常コスト）。
   出力トークンは未実測の目算を含み、単価は 2026-06-24 時点の cache 由来（**未実測の
   部分は未実測と明示する**・#376「数字が嘘をつかない」）。
   **新規制約: `line_no` の採番方式（物理行番号）を変更しない。** 変更すると既判定キーが全て
   ずれ、既判定 3,052件も再判定対象に落ちる（当初懸念していた worst case が現実化する）。
   extractor.py v3（本 PR）はこの制約を遵守し、`line_no = idx + 1`（`enumerate(f)` 由来）を
   一切変更していない
5. **`prev_action` が `extractor_version=2` の行で全件 null**（A0 の実測窓 1,124件すべてで確認・2026-08-12）。
   correction の文脈判定に使えない既存データ欠損であり、**A1 の再抽出設計にこの列の充填を含める**
   （A0 スコープ外として A1 へ申し送り）。充填しない場合、B の提示品質は「発話単独」の情報しか使えない。
   **2026-08-13 root cause 特定・修正済み**: `extractor.py` の tool_result 行到達時に
   `pending_tool_names = []` で誤ってリセットしていた（コメントは「リセットしない」と書かれて
   いたが実装が逆）。実 transcript では assistant の tool_use 直後に必ず tool_result 行が続くため、
   蓄積した tool 名が human 発話に届く前に毎回消えていた。該当行を削除して修正（extractor.py v3）。
   新規 ingest 行は `prev_action` が正しく充填される（`test_prev_action_survives_tool_result_rows`
   で検証）。既存 v2 行の遡及充填は次PRの全履歴 migration に含める

**A1 — 完了条件の定義（2026-08-13 forward 修正で確定）**
「sidechain 由来 0件」は**消失 transcript 2,728件があるため定義不足**だったため、
新規記録と既存行を分けて完了条件を確定した:

- **新規記録**: `ingest.py` が走査候補から `*/subagents/*.jsonl` を除外（実データ検証
  2026-08-13: main-level transcript 2464 ファイル全数走査で isSidechain:true = 0 件、
  subagents/ 側は 30 ファイルサンプルで 7323/7323 = 100% true）+ `extractor.py` の行単位
  `isSidechain` チェック（extract_utterances が sidechain 混在ファイルへ直接呼ばれた
  場合の第二防御）。→ **新規 ingest で sidechain 由来 0件を達成（完了）**。
- **既存 DB 行（sidechain 由来 2,955件・削除済み transcript 2,728件を含む）**: **purge しない**。
  `query.py` の `_build_query` に `source_path NOT LIKE '%/subagents/%'` を常時（opt-in
  不可で）追加し、**read 時に恒久的に除外**する。新設凍結（#379）のため新ストアは作らず
  既存 query の WHERE 条件に一元化した。DB 行自体は残るが表示・判定には一切混ざらない。
- 旧 `ingest_state` に残る subagents ファイルのエントリの掃除、および削除済み transcript
  由来行の物理 purge は**本 PR のスコープ外**（次 PR の全履歴 migration へ申し送り）。

**A1 — 除外の粒度（2026-08-13 実装確定）**
sidechain 除外は user 行だけでなく `prev_action` の境界にも影響する（`extractor.py:217-269`）。
実データ検証の結果、**両方採用**した:
- **ファイル単位（一次防御）**: `ingest.py` が `*/subagents/*.jsonl` を走査候補から除外。
  main-level transcript は isSidechain:true が実測 0 件なのでこれだけで新規 sidechain 混入は
  塞がる（コスト面でも subagents/ 配下ファイルを読まずに済み効率的）。
- **行単位（第二防御）**: `extractor.py` が各行の `isSidechain: true` を無条件 continue で
  スキップ（`pending_tool_names` に一切触れない）。extract_utterances が将来別経路
  （harness 変更・別呼び出し元）で sidechain 混在ファイルへ直接呼ばれても正しく振る舞う
  ことを保証する防御的実装。
- sidechain user 行を飛ばした際に sidechain 内 assistant の tool 名を次の main user 発話の
  `prev_action` へ持ち越さないテストを追加（`test_utterance_extractor.py::
  test_sidechain_tool_use_does_not_leak_into_prev_action`）。

**A1 第二段階（全履歴 migration）— 保留の裁定（2026-08-13）**

**決定: 全履歴 re-ingest migration は実装しない（保留）。G1 の測定は本番 DB を書き換えず一時 DB で行う。**

判断の根拠（実測 + codex / tacchi 両レビュー）:

1. **前提は成立していた**（やれないから止めるのではない）
   - v3 extractor + 現行 store の end-to-end で `prev_action` は実際に埋まる（一時 DB へ実走・非 null **67.55%**）
   - null は全件「構造的に正しい null」（セッション冒頭 69.8% / 直前 assistant が tool 未使用 30.2% /
     **バグ候補 0件**）。`_INSERT_SQL` と `_utt_params()` の対応も突合済みで store 側の別バグは無い
   - wall time 約 50〜125秒（`transcript-store-bench` の 75分暴走の前科は杞憂）、ディスクも 442GiB 空きで余裕
2. **便益が薄い（決定要因）**
   - migration が埋めるのは既存 15,371件の `prev_action`。しかし `judge_runner` は
     **newest-first + 日次上限200件**（`judge_runner.py:109-126`）で、未判定在庫 10,419件の後ろにいる
     古い発話には**到達しない**
   - `prev_action` が実際に効くのは新規発話であり、それは第一段階（PR #432）で**達成済み**
   - 残る実効便益は「**G1 を実運用と同じ条件で測ること**」だけ
3. **コストが重い（codex [Must] 6件 + tacchi [Must] 1件）**
   - DuckDB は複数プロセス同時 write 非サポート。旧 DB を開いた writer は rename 後も**旧 inode に書き続ける**
     → 全 writer 共有の application-level flock を**新設**する必要がある
   - `close()` では file handle が解放されない場合がある（DuckDB Python は DB instance が保持）
   - **WAL を置き去りにする rename は禁止**（`CHECKPOINT` → close → `.wal` 不在確認 → 同一 FS 内 rename → 親 dir fsync）
   - **orphan の定義が狭すぎた**: 引き継ぐべきは「ファイル消失 3,542件」でなく `old PK − rebuilt PK` の
     **全 residual**（走査対象外になった subagents 由来 2,955件を含む）。狭い定義のままだと
     ADR が決めた「既存行は purge しない・read 時除外」契約に反する
   - 完了条件は**集合包含 + `text_hash` 一致**まで検証しないと、既判定 3,052件の silent 誤帰属を見逃す
     （件数一致では入れ替わりを検出できない／`new >= old` は別の新規行で欠落を相殺できる）
   - transcript が抽出中に追記される競合（「未抽出なのに処理済み」の永久欠落）
4. **#379 は縮小が方針**。上記の機構を新設して得るものが「judge が届かない過去データの充填」では釣り合わない

**解凍条件**（いずれかが成立したら再検討する）:
- **B3**（judge の日次上限見直し / 対象の絞り込み）で**古い発話が judge の到達範囲に入る**ようになった
- **G1 の測定で「`prev_action` の有無が recall/precision を有意に変える」ことが実証された**
  （＝過去データの充填に実利が生まれた）

**保留に伴う既知の副作用**（silence != evaluated なので明記する）:
- 既存 18,913件の `prev_action` は null のまま残る（v1 で 99.1% / v2 で 100%）
- orphan のうち未判定分は「未判定 N 件」の表示を恒久に水増しし続ける（newest-first cap で消化されない在庫）。
  対策機構は新設しない（#379）が、**この数字を読むときは偏りを前提にする**

**設計文書の誤りの訂正**: レビュー依頼時に「observe hook がセッション中に随時 `utterances.db` へ書く可能性がある」
と書いたが**誤り**。`hooks/` に `utterances.db` の writer は存在しない（テストのみ）。実際の書き手は ingest 経路
（対話 evolve の capture / `evolve-fleet ingest` / daily runner）。codex / tacchi 双方が実装で確認して指摘した。

**G1 の測定方式（本裁定により確定）**: 本番 `utterances.db` を書き換えず、A0 の人手ラベル 86件が載る
**41 transcript ファイル**を v3 extractor で一時 DB へ再抽出し、その上で judge を走らせて recall/precision を測る。
**破壊的操作はゼロ**。ラベル 86件のうち **6件（すべて not_TP）は transcript 消失で再抽出できない**ため、
**分母から黙って外すのでなく `not_measured` として明示する**（TP 10件はすべて現存ファイル上なので recall の
分子・分母は無傷。影響を受けるのは precision の分母のみ）。

**A3 — 判定基準**
決定論基準は **`provenance.source_path` に `/subagents/` を含む**こと。
扱い: 放置 / TTL 45日の自然失効 / 対象を絞った一括 expired 化。
**corrections に昇格済みの2件は TTL では消えない**ので別途 invalidate するか判断が要る。

### Phase B — 朝の提示の質【柱2】※ Phase A 後

| ID | 内容|
|---|---|
| B2 | ~~提案の並び順を見直す（A2 適用後の実データを測ってから方式を決める）~~ → **2026-08-14 訂正: machinery の read 時除外 + 順位と打ち切りの分離**（issue #443。詳細は下記） |
| B3 | ~~llm_judge 滞留の解消（A1 で -23%、上限見直し、非PJ `matsukaze-takashi` 1,017件の扱い）~~ → **2026-08-14 訂正: 母集団の是正・上限据え置き・cutoff 宣言**（issue #442。詳細は下記） |

**B2 の未決事項（2026-08-13 時点。下記の実測で解消済み）**: 公平性を入れるなら ①cross-PJ confirmed 優先との順位 ②channel 内順位
③候補が1 channel だけの場合 ④SessionStart 上限2件との整合 を決める。~~現時点で方式を確定しない（P5/P7）。~~

#### B2・B3 の実測と設計（2026-08-14 追加。設計正典: plan `drafts/054-phase-be-design.md` §1.1/§2。実測は全て2026-08-13・read-only）

朝の提示は5点の構造欠陥が実測で判明した:

| # | 発見 | 根拠 |
|---|---|---|
| B-a | **朝の候補の 15.7% が委譲メッセージ**。`REVIEW_CHANNELS` の未昇格300件のうち **47件** が `<teammate-message` を含む（rephrase 25 / llm_judge 22）。`is_machinery_prompt` は **47/47 を捕捉できる** | 実測 |
| B-b | **A2（PR #431）は書込側の修理**なので、既に検出済みの在庫はそのまま残っている | 同上 |
| B-c | 順位を直しても届かない候補がある。digest 生成時に `build_review(max_groups=3)` で PJ ごと3件に切ってから global 化・既読差し引きをするため、**4件目以降は順位規則の適用対象に入らない** | `proposal_digest.py:262,290` / `daily_review.py:332` |
| B-d | **global レーンが構造的に死んでいる**。per_pj を先に連結するため per_pj に未既読が2件あれば global は永久に出ない | `proposal_digest.py:343-347,388-389` |
| B-e | **judge 未判定 10,419件のうち 2,942件（28.2%）が tracked 外 PJ**。tracked 外は判定枠を消費するが `proposals` に到達する経路が無い（2026-08-13 ユーザー裁定後は 1,157件（11.1%）に縮小。下記参照） | `ingest.py:137,145` / `proposal_digest.py:283` / `fleet/cli.py:454-460` |

**順位キーの識別力が実データでほぼ無い**（tacchi 実測。設計の前提を覆した）: `evidence.count` はほぼ全 group で1（「再発回数」キーが実質機能しない）、`cross_pj_confirmed` は今朝の全 group で空（confirmed idiom は131件あるが正規化完全一致という照合の粗さで一致0）。結果、旧設計の合成キーは実質「新しさ」だけで並び、そこに B-a の委譲メッセージが乗っていた。

**B3-1（issue #442）— judge の母集団を tracked に絞る**

judge の需給実測（ADR §6 が要求していた「A1/A2 後の流入量再計測」への回答）:

| | 全 PJ | tracked のみ |
|---|---|---|
| 週次流入（W27〜W32 平均） | 約1,200件 | 約1,030件 |
| 日次上限 200 × 7日 | 1,400件 | 1,400件 |
| 週あたりの余剰 | 約+200 | 約+370 |

→ **上限200/日は既に流入を上回っている。B3 の解は「上限引き上げ」ではなく「母集団の是正」に確定した。**
**日次上限は引き上げない。** `zundamon-explainer`（714件）と `ai-office`（271件）を `tracked_projects` に
追加するユーザー裁定（§7.1）を経て、実際に対象から外れるのは `matsukaze-takashi`（home起動セッション
1,017件）とゴミ slug 13個（140件）のみに縮小し、余剰は **+232/週**（実測。当初見積り+370/週から縮小）。

契約: ①**alias fold**（`rl-anything → evolve-anything` は既存の `pj_slug_match` 系の正規化関数を使う。
新実装しない） ②**処理順の固定**（tracked filter → judged key 除外 → unjudged_total 算出 → daily cap 選定）
③**除外 PJ の発話は `correction_judged.jsonl` に書かない**（tracked 復帰時に通常の未判定として復帰できる）
④**除外の可視化**（`excluded_untracked_total` / `excluded_untracked_by_pj` を dry-run/run/lock-skip/
source-failure の全分岐で返す。silence != evaluated） ⑤**古い在庫の cutoff 宣言**（発話時刻
`utterances.timestamp` が `now - 90日` 以降なら対象、境界 `==` は含める。既定90日は userConfig
`judge_utterance_max_age_days` で変更可。**TTL 45日とは別段階・別時計**——cutoff は「未判定 utterance を
judge に入れるか」、TTL は「判定後の weak_signal を提示するか」。正直な効果の見積り: utterances.db の
保持は現時点で約3ヶ月なので、90日 cutoff は現在の在庫をほとんど減らさない。将来 DB が長期化したときの
予防措置）。

**B2（issue #443）— 朝の提示（machinery 除去 → 順位と打ち切りの分離 → 表示）**

- **machinery の read 時除外**（最優先・tacchi [Must]1）: 既存5 reader の単一 predicate である
  `filter_actionable`（`correction_semantic/promote.py:125`）に `is_machinery_prompt` を適用し、
  独自 reader の `_read_backlog`（`bootstrap_backlog.py:351`）にも**同じ述語**を通す（`_read_new` だけに
  入れると母集団が分裂する・codex 2巡目 [Must]1）。除外件数は `excluded_machinery_total` /
  `excluded_machinery_by_channel` として digest・queue・observability の返り値に載せる。新設ゼロ（既存
  dict へのキー追加のみ）・判定は `is_machinery_prompt` を単一ソースとする（文字列 allowlist を新設しない）
- **順位と打ち切りの分離**（codex [Must]2）: 現行は PJ ごとに3件へ切ってから global 化・既読差し引きを
  するため4件目以降が順位規則の対象外だった。`build_review(max_groups: Optional[int] = 5)` として
  `None` で無制限化し、**digest 側だけ `max_groups=None` で呼ぶ**（既定5のまま既存呼び出しは非影響。
  新関数は増やさない）
- **composite sort のキー**（実データの識別力に合わせて再定義）:
  ①PJ横断で見えているか（`cross_pj_confirmed` が非空 **または** global レーン所属。両者は別物。
  tacchi [Must]2） ②再発回数（`evidence.count`。実データでは大半1のため実質 tie-break）
  ③鮮度（**発話時刻**。`detected_at` は判定時刻であって発話時刻ではないため `utterances.db` を
  `source_path:line_no` で read 時 join。既読差し引き後も再計算できるよう group に
  `signal_meta_by_key`（各 `signal_key` の `uttered_at`/`detected_at`/`cross_pj`）を保持する契約に
  する——`_slim_group` は集約後の `count` しか持たず個別 key 情報を失うため・codex 2巡目 [Must]3）
  ④決定論の担保（`min(signal_keys)`）
- **提示文**: 発話の実時刻（相対表記）と観測ベースの cross-PJ（`reps_by_pj`）を出す。confirmed 一致
  より遥かに発火しやすい。channel 名（`llm_judge`/`rephrase`）は出さない（ジャーゴンで判断材料にならない）

### Phase D — 信頼【柱4】※ Phase A/B と**並行可**・C4 の前提

| ID | 内容 |
|---|---|
| D1 | `pre_extension` 残存2経路（`optimize.py::save_history_entry` / `run_loop.py`）を emit→drain lane に寄せる → **PR2/PR3 は凍結（下記）** |
| D2 | entry_id の導線拡充（`bin/evolve-revert --list` 相当） → **実施済み（2026-08-13）**。`bin/evolve-revert --list`（`--json` 対応）が accepted entry を revert 可否つきで新しい順に列挙。PR2/PR3 対象外レーンの entry も `pre_extension` 理由つきで一覧に残す（黙って落とさない・#376） |

**D1 の PR2/PR3 凍結裁定（2026-08-13・ユーザー判断）**

実測（`~/.claude/evolve-anything/optimize_history/` 全 41 entry を writer 別に分解）:

| レーン | writer | 記録数 | accept | 備考 |
|---|---|---|---|---|
| A | evolve drain（`evolve_decisions/_ingest.py`） | 10 | 10 | 全て #376 で無効化。**PR1 で修理済み＝今後の accept は revert 可能** |
| B | `optimize.py::save_history_entry`（**PR3 の対象**） | 13 | **0**（史上ゼロ） | 全 entry が `human_accepted=None` |
| C | `run_loop.py`（**PR2 の対象**） | 18 | 2 | うち1件は pytest tmpdir 汚染＝**実質1件** |

→ **PR2・PR3 は作らない**。採用実績がほぼ無いレーンに、本設計中で最も複雑な仕組み
（append-only decision chain + `supersedes_id` + 2段検索）を入れるのは #379 の縮小方針に反する。
代わりに **両経路に「この経路の採用は revert 対象外」と1行明記**し、CLAUDE.md 柱4の「1コマンドで
戻せる」にも適用範囲を書く。**実際に使われ始めたら解凍する**（解凍条件: どちらかのレーンで
テスト由来でない accept が3件以上）。
**PR4（`--list`）は実施する**（A レーンの新規 accept は今日から revert 可能で、PR2/PR3 に非依存）。

> tacchi は当初「PR3（optimize.py）は accept 2件だから後回し」としたが、実測では 2件だったのは
> PR2 側（run_loop）で、PR3 側は 0件だった。方向は正しく対象が入れ替わっていたため、頭が実測して
> 両方の凍結に確定した。

### Phase E — correction → accept の変換経路【柱3(b) の前提・**E1 は実施 / E2・E3 は凍結（解凍条件つき）**】

§3.1 のとおり柱3(b) の真のボトルネック。設計すべき最低3点（codex [Must]）:
1. 昇格した correction が**どの処理で具体的な skill diff 候補になるか**
2. その処理が**いつ起動されるか**
3. 朝の y/n の後、適用確認と `drain_pending(accepted=...)` まで**どう到達するか**

現行 accept は evolve の Step 3（matched skill の提案・適用）と Step 7.8（inline drain）に依存し、
通常の `--drain` 単体では accept を記録しない（`skills/evolve/SKILL.md:195-197,288-303`）。

**完了条件**: ~~correction → skill diff → accept が synthetic E2E で1周する~~ →
**2026-08-14 訂正**: E1（accept 記録の CLI 化）が synthetic E2E で1周する。correction → skill diff の
変換（E2/E3）は凍結。下記参照。

**着手前ゲート（2026-08-13・tacchi [Must]5 / codex [Must]5）**

§3.1 は「変換経路が存在しない」と断定しているが、部品（Diagnose の入力 / Step 3 の提案 /
Step 7.8 の drain）は全て既存で、過去に `human_accepted=True` が10件生まれてもいる（#376 で無効化）。
**「経路が無い」のか「経路はあるが correction が提案に反映されない・起動されない」のかで作るものが
全く違う**（後者なら配線と起動導線の修理で済み、新設凍結にも収まる）。

~~→ E の設計着手前に、実 repo で手動1周実験を行う（1日）。実 correction を積んだ状態で evolve を
回し、correction 由来の提案が出るか → y → drain → `optimize_history` に revert 可能な accept が
載るか、を実測する。この結果が E 設計書の §1 になる。synthetic E2E はその後。~~

**2026-08-14 訂正: 手動1周は実施せず、静的解析 + 実コーパス実測で代替した**（設計正典: plan
`drafts/054-phase-be-design.md` §1.2。codex [Should]1 のとおり、ADR のゲート変更として明示裁定する）。
理由: `_candidates.py:79-125` を読んだ時点で、emit→drain の skill-diff candidate lane の入力は
discover の `matched_skills` と skill_evolve の `assessments` だけであり、corrections はどちらの
入力経路にも含まれないことがコードで確定した。この条件下で手動で evolve を1周回しても、correction
由来の提案は構造的に出ないことが事前に分かっており、「経路が無いのか起動しないのかを見極める」という
実験の目的を果たさない（実験しても結果は自明）。代わりに、実データでの Jaccard 通過率実測（下記
E0 実測結果）という、より直接的で再現性の高い方法で着手前ゲートを判定した。

**E0 実測結果（2026-08-13・着手前ゲート）— 通過条件 10件に対して 0件**

有効 corrections **155件**の `message` を pattern text として `skills/*/SKILL.md` **23件**に当てた結果、
`JACCARD_THRESHOLD = 0.15` を超えたものは **0件**（最大 **0.0721**）。

**理由は2つあり、両者は独立している**（tacchi 観点4）:
- **①照合器が日本語を読めない（技術的欠陥・issue #447）** — `similarity.tokenize` は
  `re.split(r"[\s\W_]+")` で、日本語は `\w` に含まれるため分割されない（実測: 平均トークン長8.5文字）。
  **この欠陥がある限り、内容が何であれ Jaccard は構造的に閾値を超えない。**
- **②材料が skill diff ではない（本質的欠陥）** — corrections の中身は「PRじゃないの？」
  「もっとわかりやすく整理して確認して」等の**行動規範**。`correction_type` は
  `semantic_idiom 145 / stop 8 / iya 1 / naoshite-request 1`。さらに `[Image` 始まり37件（23.9%）、
  `Stop hook feedback:` 8件、**assistant 自身の出力が semantic_idiom として登録されている行**もある
  （corrections ストアの入力衛生の問題。issue #445）。

**したがって E0 は「①と②を区別できない実験」だった。** ただし②は message を直接読めば判定でき、
**②単独で E2 を止める理由として十分**なので、①を直してから再実験する必要はない。

**(4) 既存の設計思想と正面衝突する**: `_candidates.py:79-90` の docstring は「remediation の fix は
target が rules/hooks/構造と異種で skill_quality 母集団の均質性を壊すため対象外（ADR-041 follow-up
の意図的スコープ）」と明記している。

#### 頭の裁定: E1（accept 記録の CLI 化）は実施する

**ここは codex と tacchi が正面から食い違った唯一の点**なので、根拠を明記する。

- **codex [Must]5**: `evolve --drain --accepted <id>` を agent が任意に実行できると、その CLI 呼出し
  自体が「人間が y を押した」根拠になる。現行の inline python MUST と**同じ信頼境界**を、より呼びやすい
  CLI に移しただけ → 見送るべき
- **tacchi 観点6**: `learning_skill_md_must_not_enforcement` の既知欠陥類型の**根治**であり、
  E0 の結果と無関係に価値がある → やるべき

**裁定: やる。両者の指摘は排他ではない。** 現行の inline python も**実行するのは Claude** なので、
信頼境界は既に「Claude が対話の結果を受けて実行する」。CLI 化しても境界は**悪化しない**（codex の
指摘は「E1 は承認 provenance の問題を解決しない」であって「E1 が問題を悪化させる」ではない）。一方で
E1 は「実行され損ねて accept が記録されない」という**別の実害を確実に消す**。したがって実施し、
**codex [Must]5 の要求を E1 の設計要件として全て取り込む**:

1. accepted/rejected ID は**直前の対話結果からどう受け渡すか**を SKILL.md に明記する
2. **既知の非対話 call site（hook / daily runner / `--auto` 系）が decision 引数を渡さないことを
   テストで固定する**。※「機械的に保証する」は**過剰約束だったので撤回**（codex 2巡目 [Must]4）。
   `collectors.py:234-235` の不変条件は「hook 自身が渡さない」という呼び出し規約にすぎず、
   **CLI は呼出元を認証できない**（daily runner でも hook でも任意プロセスでも同じ引数を生成できる）。
   偽造不能な承認 capability を対話ホストが発行する仕組みは現状存在しない。必要なら**承認 token の
   発行・検証境界として別設計**にする（本 PR のスコープ外）
3. `--accepted` と `--rejected` の**重複指定・未知 ID・理由なし reject を拒否**する
4. synthetic E2E で「**applied だが accepted なしは deferred**」を固定する
5. 引数名は既存 optimizer の `--accept`/`--reject`（単数・別意味）と紛らわしいので、
   ヘルプに「proposal ID の複数指定」であることを明示する（codex [Nit]3）

（issue #444 に対応。PR3。#401 / #402 に紐づく）

#### E2/E3（correction → skill diff）は凍結する

**理由は3つ**（tacchi 観点6 が最も明快なので採用）:

1. **材料テストが不通過** — 0/155。内容は行動規範であって skill 本文の diff ではない（上記 E0 実測）
2. **反映先の経路は既にある** — corrections → CLAUDE.md / rules は **reflect が既に持っている責務**。
   E を rules 側へ「切り替える」のは **reflect の再発明**で、#379 が最も嫌う重複
3. **柱3(b) の分母は E2 なしでも作れる可能性がある** — `_extract_candidates` には skill_evolve
   assessments という既存の提案源が生きており（`skill_evolve/assessment.py:109` が未進化 skill に
   high/medium を生成し、`_emit.py` が pending 化する経路をコードで確認）、E1 で accept が
   機械記録可能になれば既存レーンだけで accept が積み始めうる。
   **「柱3(b) は E が前提」という当初の前提は、E1 と E2 を束ねたことによる過大主張だった。**
   ただし **「E1 導入後に必ず積み始める」とまでは言えない**（codex 2巡目 [Should]3 — 全 skill が
   `already_evolved` / `skip_llm_evolve` / batch guard に当たれば0件）。
   **PR3 の完了後に実データで pending 生成件数を実測して確かめる**（実測するまで数字を主張しない）

**解凍条件**（凍結は永久ではない）: E1 導入後、**既存レーンで accept が積まれてもなお
「correction 由来の提案が欲しい」という要求が実際に観測されたら**再検討する。

#### E をやるとしたら何が必要か（将来の別 ADR 用に保存。全て未解決）

1. **provenance が下流に伝わらない** — `_enrich_patterns` が運ぶのは `type` / `pattern` /
   `matched_skill` / `skill_path` / `jaccard_score` だけ。correction identity は `_emit.py` に残らず
   N↔N 追跡が成立しない
2. **再提示の抑止が逆向き**（issue #446） — `proposal_id = (repo_id, relative_path, before_sha)` は
   「対象ファイルの現在世代」を指すので、**reject 後は同じ ID が次回も emit され**（emit は reject
   history を見ない）、**accept 後は before_sha が変わって新しい ID として再生成される**。これは
   corrections 由来に限らず現行 A レーン全体の性質
3. **correction → pattern の schema が未定義** — 何を `pattern` にするか、複数 correction の集約、
   `count` / `type` / `suggestion`、invalidated 判定、PJ alias fold
4. **合格判定は量だけでは弱い** — unique correction 数 / unique target skill 数 / pair 数 / score 分布 /
   人手 precision / 同一 skill への集中度を分けて測る
5. **`similarity.tokenize` が日本語を分割できない**（issue #447） — 照合方式そのものの変更が要る
   （上記 E0 実測の理由①）

朝の y/n は「correction として採用」か「生成された patch の適用承認」か（後者を暗黙に兼ねさせるのは
柱4違反。patch を見た後の明示承認が別途必要）、状態遷移に使う既存 artifact（`corrections.jsonl` /
既存 pending proposal / 既存 optimize history。**新 store は不可**）、correction identity ↔ diff
proposal identity の対応（1→N・N→1・重複の定義）、起動点（既存 SessionStart / evolve / queue の
どこか）、状態機械の各境界と再実行時の冪等性、「価値ある diff が生成される率」の評価は、いずれも
E2/E3 解凍時に確定する。

### Phase C — 週1の数字【柱3】

| ID | 内容 | 系 | 凍結 |
|---|---|---|---|
| C1 | 数字の正直さ（§2.6 の8件を潰す） | (a) | なし |
| C2 | 週次系列（欠測週は「データなし」と明示） | (a) | なし |
| C3 | 週1の起動導線 | 両方 | **要裁定（下記）** |
| C4 | 取り下げ候補 → revert への接続 | (b) | なし（D1 + E1 が前提。E2/E3 は凍結のため非依存・2026-08-14 訂正） |

**C1 の影響範囲（codex [Must]）**
`rework` の分岐: `outcome_metrics.py:366-390` で「対象 session 件数あり・測定可能 session 0件」なら
`reason=not_measured` とし、evidence に `measured_sessions / total_sessions` を持たせる。
**表示側も同時修正が必須**: `sections_outcome.py:29-51` は `insufficient_sample` 以外を全て「データ不足」と表示し、
同 :84-110 は3軸とも None なら reason を見ずセクション全体を沈黙させる。
最低影響範囲は **`outcome_metrics.py` + `sections_outcome.py` + 双方の契約テスト**。
per-skill 系にも同じ意味を要求するなら `outcome_attribution.py` / `outcome_promotion_readiness.py` まで契約統一。
**加えて §2.6 の 3/4/5 も C1 に含める**（sessions の unique 数え直し・capture の最小分母・channel 決め打ちの是正）。

**C2 の注意**
accept は「適用した改善**数**」であって効果ではない。**accept 件数と outcome 改善は分けて表示する**。
効果判定には適用前後の時間窓・最低分母・複数 accept が重なる場合の帰属が要る。

**C3 の凍結裁定（codex [Must]）**
凍結は表示場所でなく4集合を CI で検査する。
→ **既存 store の read-only 専用 bin、または既存 SessionStart 出力への分岐だけなら非抵触。**
　 **新しい last-shown store / 専用 weak channel / adapter を足せば audit 外でも違反。**
「週1」の保証手段（TZ・週境界・**未起動週の扱い**まで決める）:
1. 既存 store の時刻から**決定論的に導出**（P6 に合致・**推奨**）
2. ユーザーが専用 bin を週1で起動する（状態不要）
3. 既存の許可済み状態へ意味互換なフィールドを追加

**C4 の完了確認**
実データで候補0・revert 対象0なので **synthetic E2E が必須**。
候補判定の最小サンプル・REGRESSED の定義・excluded を母集団から外す規則・revert 不可な legacy accept の表示を fixture で定義。

---

## 6. 実施順

**（2026-08-13 改訂。旧 `A1,A3,A5` 並列は誤り＝A3 の汚染 cleanup が A1 の再取り込みと衝突する。
codex [Must]3。実施済み Phase は完了印を付けた）**

```
Phase 0 (B1) ✅完了 / A0 ✅完了 / Phase D PR1 ✅完了

A2 ✅完了 ──┐
C1 ✅完了 ──┘ 並行可（C1 は表示層のみ・A/B/E/G1 の全てに非依存）
  ↓
A1 第一段階（forward 修正）✅完了（PR #432）
  ↓
A1 第二段階（全履歴 migration）── **保留**（2026-08-13・§5-A1「migration 保留の裁定」）
  ↓
A3（汚染 cleanup）
  ↓
G1 計測ゲート ✅ **PASS**（2026-08-13・一時 DB 方式で本番 DB 非汚染。recall 80.0% / §7.4）
  └─不通過なら C(a) は作らない（今回は発動せず）
  ↓ 通過
A5 → C(a): C2
  ↓
B3-1 ∥ B2 ∥ E1 ──→ C(b): C3,C4
　（2026-08-14 訂正・plan `drafts/054-phase-be-design.md` §4。旧
　`B2/B3 → E（手動1周 → 設計 → 実装）→ C(b)` を置換。owned paths が重ならないため3本は並行可）
E2/E3（correction → skill diff の変換経路）── **凍結**（解凍条件: E1 導入後もなお correction 由来
提案の要求が実際に観測されたら再検討。§5 Phase E）
Phase D PR4（--list）──── 独立・いつでも可
Phase D PR2/PR3 ──── **凍結**（§5 Phase D の裁定）
```

- **A2 が次の最優先**（朝の提案の 50% が委譲プロンプト＝体験毀損の最大要因。安く効く）
- **C1 を前倒しする**（§2.6 の8件は**今日も嘘をつき続けている**。表示層の修正で他に依存しない。
  柱4は「将来直す」でなく「今止血」・tacchi [Should]3）
- **A0 → A2 の順序は固定**。A0 が `_MACHINERY_MARKERS` に構造除外を1行足すため、
  A2（`_DISPATCH_MARKERS` の文字列 allowlist 廃止 → `is_machinery_prompt` へ統合）は A0 の除外を前提に設計する
- **A1 は二段階**（codex [Should]）。forward 修正と read 時 sidechain 除外で新規・表示の正しさを先に確保し、
  全履歴 migration は限定 corpus のベンチと既判定キー再利用率を見てから。
  **着手前に llm-batch-guard の費用見積もり（最悪 3,604件の再判定）を確定する**
- **A3 は縮小**（tacchi [Should]）。corrections 汚染2件の invalidate だけ即時実施し、
  残り 49件は TTL 45日の自然失効に任せる。フルの後始末フェーズは作らない
- **A5 / C(a) は G1 の結果に従属**（A5 単独の `distinct_types >= 5` は語彙を増やせば達成できる
  vanity gate なので成果指標から降格・codex [Should]）
- ~~**B3（llm_judge 上限引き上げ）は A1/A2 後に流入量を再計測してから**。
  10,225件を処理すること自体は価値でなく、古い低品質候補に LLM 費用を払う危険がある（codex [Should]）~~
  → **2026-08-14 訂正**: 再計測の結果、**B3 は「上限引き上げ」ではなく「母集団の是正（B3-1）」**に
  決定した。日次上限200/日は据え置く（詳細は §5 Phase B）
- **C4 は D1 と E1（Phase E のうち実施するのは E1 のみ。E2/E3 は凍結）が前提**

**worker の衝突回避**: A2（`detectors.py`）と A1（`extractor.py` + migration）は同ファイル群なので**順次**。
E（drain 周辺）と Phase D の書込境界も重なるので並行させない。並行してよいのは A2 ∥ C1 まで。

**2026-08-14 追記（Phase B/E 実装フェーズ）**: PR1（judge 母集団の是正=B3-1）/ PR2（朝の提示=B2）/
PR3（E1: accept 記録の機械化）は owned paths が重ならないため**並行可**（plan
`drafts/054-phase-be-design.md` §4）。上記「worker の衝突回避」段落は Phase A/G1 段階の記述であり、
Phase B/E 段階には非適用。

**実データ検証の挿入点（合成 fixture での完了を禁止・P8）**:
①A2 後＝同じ 12 group サンプルを再抽出し「委譲プロンプト0件」を実測 ②A1 後＝再抽出時間・行数差・
`prev_action` 充填率・既判定キー再利用率 ③G1＝固定 corpus の recall/precision・channel 間重複
④**2026-08-14 訂正**: ~~E 後＝実 correction から有用 diff が生成され、明示承認・accept・revert まで
到達すること~~ → **E1 後**＝synthetic E2E で emit → 適用 → `--accepted` → optimize_history の accept
→ `bin/evolve-revert` で戻せることを実測（E2/E3 は凍結のため「有用 diff が生成される」検証は対象外）

---

## 7. 判断の記録

### 7.1 決定済み（2026-08-12 / 2026-08-13 ユーザー判断）

| 論点 | 決定 | 影響 |
|---|---|---|
| sidechain 除外の層 | **記録層（extractor）で根治** | 再 ingest migration の新規開発が Phase A に入る。llm_judge 滞留も 23% 減 |
| 柱3(b)（採用効果・取り下げ候補） | **Phase E を作って完成させる** | v1 案の「4週の計測ゲート」は撤回 |
| A4「手直し」の定義 | **capture 調査の結果、corrections を分子に使う案（A4-ii）は却下** | 下記 7.2 |
| A3 の既存 FP（rephrase 16 / llm_judge 33 / corrections 昇格済み2）の扱い | **縮小**（2026-08-13）。corrections 昇格済み分は即時 invalidate、まだ corrections に到達していない weak_signal（残り分）は TTL 45日の自然失効に任せる。フルの後始末フェーズは作らない。**scope 訂正（2026-08-13 頭の実測）**: 当初 llm_judge の2件のみを対象としたが、決定論基準（`weak_signal_provenance.source_path` に `/subagents/` を含む）は channel を問わない。既に corrections まで昇格済みの rephrase channel 6件も llm_judge の2件と同じ状態（＝「残り49件」に含まれる未昇格 weak_signal ではない）と判断し、対象を**計8件**へ拡大。**機構実装済み（2026-08-13）**: `scripts/lib/corrections_subagent_invalidation.py`（dry-run 既定・`--apply` で書込、channel 不問）を追加。対象8件（`weak_signal_key`: llm_judge=836826fb11c47e48,65deb6a40830d9f4／rephrase=1c22c8ecabb3bf7a,a62cc27b9c3b8baf,22afaa00b84a51a5,f758ac0dd4f4776f,bfea72bb5163cb1d,86bfd7340c1c30b5）を実データ dry-run で確認済み（既存の `invalidated` フラグを流用し論理無効化。物理削除しない）。実データへの `--apply` 実行は実環境ストア書込のため頭側が行う。rephrase 6件の `detected_at` は全て **2026-07-30T23:13**（A2 マージ=2026-08-13T00:40 UTC の2週間前）で検出済みだった weak_signal — **A2 の穴ではない**（promote 時刻が 08-12〜08-13 なのは検出時刻でなく人間確認のタイミングの問題）。**残課題（この PR では未対処・記録のみ）**: A2 は検出側の forward 修正であり、A2 マージ以前に検出済みの subagent 由来 weak_signal は TTL 45日（detected_at 起点＝2026-09-13 頃まで生存）の間、朝の y/n 提示に出続け昇格し得る構造的な穴が残る。実際 6件中4件は A2 マージの21分後（2026-08-13T01:01）に昇格した。weak_signals 側の read 時除外（A1 で `query.py` に入れた `source_path NOT LIKE '%/subagents/%'` と同型のフィルタ）が要るかは別途判断 | Phase A3 のスコープ縮小・2026-08-13 に対象8件へ訂正。§6 実施順に反映済み |
| B3（llm_judge 上限200件/日）を上げるか対象を絞るか | ~~**即決しない**（2026-08-13）。A1/A2 後に流入量を再計測してから判断する（10,225件処理自体は価値でなく古い低品質候補に LLM 費用を払う危険）~~ → **決定済み（2026-08-14）**。再計測の結果、**「母集団の是正（B3-1）」に確定**。日次上限は引き上げない | B3 着手を A1/A2 完了後に後ろ倒し。§6 実施順・§5 Phase B に反映済み |
| 再 ingest（A1）に伴う LLM 再判定費用の事前見積もり | **A1 を二段階に分割**（2026-08-13）。forward 修正 + read 時 sidechain 除外を先に実施し、全履歴 migration は費用見積もり（最悪3,604件）とベンチの後に着手判断する | A1 の完了条件が二段階化。§5-A1・§6 に反映済み |
| tracked 外の実在 PJ の扱い（B3-1 の対象確定） | **`zundamon-explainer`（714件）と `ai-office`（271件）を `tracked_projects` に追加する**（ユーザー選択・2026-08-13）。現役 PJ の学習素材を捨てる理由がなく、追加しても週次流入は約+170件で判定枠の余剰+370/週に収まる。結果、B3-1 で実際に対象から外れるのは `matsukaze-takashi`（1,017件・home起動セッション）とゴミ slug 13個（140件）のみに縮小し、余剰は当初見積り+370/週から**+232/週**に縮小 | §5 Phase B の B3-1 に反映済み |

### 7.2 A4 の結論 — corrections を「手直し回数」に使わない

調査結果（§2.1）により:
- corrections.jsonl は「指摘の発生ログ」ではなく **reflect を人が回した日のログ**
- hook レーンは3か月で1件＝**指摘の発生を捉えていない**
- したがって **A4-ii（human correction 件数を手直しとする案）は却下**。
  `results_board` が既にこの数え方を rework 表示に使っているが、**これ自体が §2.6-2 の「嘘をつく数字」の一部**

**方針**: まず **A0 で capture を直す**。直った後に、
- 分子: corrections（capture 修理後）+ weak_signals（esc_interrupt / rephrase / manual_edit_after_ai）の併用を検討。
  **A0 実測（§5-A0）により「corrections 単独」案は消える**（recall 約4.5%＝分子として桁が足りない）。
  併用、または `correction_semantic` の意味判定を主分子に据える形が前提
- 分母: **`utterances.db` の dialogue 発話数**（2026-05-13 以降・会話ターンに最も近い唯一の分母）
- `sessions.jsonl` の生行数は Stop 発火数で約5倍過大なので**分母に使わない**（使うなら unique session_id で数え直す）

参考: tool 列を新規記録する案（A4-i）のコスト実測 — transcript 2,469本のサンプル25本で
tool_use **平均21回/セッション**（最大284）。全体 ~51,000行相当で現行 `usage.jsonl` 5,405件の**約10倍**。
過去分は復元不能。**A0 の結果を見てから再検討する**（現時点では採らない）。

#### 7.2.1 分子・分母の確定（2026-08-13・設計正典は [drafts/054-c-a-numerator.md](drafts/054-c-a-numerator.md)）

G1 PASS を受けて分子の最終形を確定した。**設計レビューは codex 2巡（初回 [Must]7 → 差分 [Must]3）+ tacchi 1巡**を
経ており、全 [Must] 反映済み・**実装着手可**。要点のみ以下に、詳細と実測は drafts を正典とする。

| 項目 | 決定 |
|---|---|
| 名称 | **指摘率**（correction rate）。「手直し率」と呼ばない（承認行為と無関係だと読める語に） |
| 週の切り方 | `utterances.timestamp`（発話の実時刻）・UTC 固定。**`detected_at` は「judge を回した日」なので使わない**（§2.1 と同型の罠） |
| 分母 | その週の発話のうち judge が判定した件数 |
| 分子 | そのうち TP と判定された件数。**`promoted`（朝の y/n 通過）は不問**（分子にすると「朝レビューをサボらなかった度」を測る） |
| 表示条件 | **カバレッジ 100%（未判定0件）の確定週のみ**。進行中の週は出さない |
| 過去週 | **バックフィルしない**。W25〜W31 の空洞は `not_measured` 行すら出さず、系列は「最初の全量判定週」から始める |
| freeze | 週 W の cutoff を `W 終了 + D 日` とし、**3ストア全て**（`ingested_at` / `judged_at` / `detected_at`）に同じ cutoff を課す。確定後は値が動かない |
| 表示先 | 戦果ボード。**現行の rework 表示（`count_human_corrections`＝§2.6-2 の「嘘をつく数字」）は置換**。併存させない |
| 表示開始ゲート | **全量判定の確定週が k=4 週連続で揃うまで系列を表示しない**（B3 の結論に自然従属させる。§6 実施順は改訂しない）。**#508（2026-08-18）でこのゲートは系列専用に限定**: 全量判定の確定週が1件でもあれば、その1週分を点として先に表示する（詳細は [drafts/508-single-week-rate-point.md](drafts/508-single-week-rate-point.md)） |

**実測の土台**（2026-08-13）: daily runner 稼働後は新規発話が当日〜翌日に **100% 判定されている**
（08-08〜08-11 のカバレッジが 100.0%）。週合計 385〜1,566件 に対し週上限 200×7=1,400件 なので、
平常週は週内に全量判定が完了する。**「週内100%判定」は現行設定のまま達成可能**で、
W25〜W31 が 0% なのは daily runner 稼働前だからである。

**G1 の位置づけの訂正**: G1 は TP 10 / not_TP 10 の均衡 corpus での測定なので、
実運用の低ベースレートにおける PPV は保証しない。「hook より捕捉性能が高いことは確認したが、
**母集団率の校正は未検証**」が正しい表現（codex [Must]）。

### 7.3 未決

- ~~A3 の既存 FP（rephrase 16 / llm_judge 33 / **corrections 昇格済み2**）の扱い~~
  → **決定済み（2026-08-13・§7.1）**。縮小方針で決着
- ~~B3 の llm_judge 上限 200件/日を上げるか、対象を絞るか~~
  → **決定済み（2026-08-13・§7.1）**。「A1/A2 後に再計測してから判断する」まで決着。
  上げる/絞るの結論そのものは再計測後に確定する
- ~~再 ingest に伴う LLM 再判定費用（最悪 3,604件・`llm-batch-guard` 該当）の事前見積もり~~
  → **決定済み（2026-08-13・§7.1）**。A1 の二段階化により、全履歴 migration 着手前に確定する運びで決着
- ~~A0 修理後の「手直し」分子の最終形（corrections 単独か weak_signals 併用か）~~
  → **決定済み（2026-08-13・§7.2.1）**。`correction_semantic`（llm_judge）の意味判定を分子に据え、
  分母は「その週の発話のうち judge が判定した件数」。設計正典は
  [drafts/054-c-a-numerator.md](drafts/054-c-a-numerator.md)（codex 2巡 + tacchi 1巡・全 [Must] 反映済み）
- `run_id` の秒精度（`optimize.py:97` の `strftime("%Y%m%d_%H%M%S")`）を UUID 化するか
  → **Phase D スコープ外・別 issue**（D1 は複数一致エラー検出で正しさを担保済み）

### 7.4 G1 の実施結果（2026-08-13・**PASS**）と、通らなかった場合の扱い

**G1 は 2026-08-13 に実施し PASS した。** 以下は実測値。

**判定基準は測定前に宣言した**（測ってから閾値を決めると恣意的になり #376 に反するため）:
> recall の Wilson 95% 信頼区間の**下限が 4.5%（hook レーンの recall）を上回る**こと。
> TP 10件という小標本では点推定で「50%を超えた」等を断定できないため、
> **CI 下限で「hook レーンより明確に良い」と言えるか**を判定に使う。

| 指標 | 点推定 | Wilson 95% CI | 事前基準との突合 |
|---|---|---|---|
| **recall** | **8/10 = 80.0%** | **[49.0%, 94.3%]** | CI 下限 49.0% > 4.5% → **成立（大差）** |
| precision | 8/10 = 80.0% | [49.0%, 94.3%] | A0（hook レーン）の 87.5% を CI 内に含む＝**有意差なし**（劣ってもいない） |

**測定条件**: 本番 `utterances.db` を書き換えず、A0 ラベル 86件が載る transcript を v3 extractor で
一時 DB へ再抽出し、実運用と同一の実装（`emit_judgement_requests` → `judge_runner.call_haiku` →
`prompt.parse_verdicts_result` → `ingest_judgement_results`）で判定した。プロンプトの自作なし。
本番の `correction_judged.jsonl` / `weak_signals.jsonl` は非汚染（mtime で確認済み）。

- 突合できた **74/86 件**（**A0 の TP 10件はすべて含む**＝recall の分子・分母は無傷）
- 欠落12件は**すべて not_TP**: transcript 消失 6件 + machinery 除外 6件
  （後者は `_is_harness` が harness 注入行を正しく弾いた**仕様通りの動作**であり取りこぼしではない）
- 測定時の `prev_action` 非 null 率 **59.5%**（74件中44件）
- LLM 呼び出し 3バッチ（30/30/14）すべて成功（`call_failed=0` / `parse_failed=0` / `omitted_verdicts=0`）
- **実課金トークン・費用は未実測**（`safe_llm_call.call_claude_headless` は stdout テキストのみを返し
  usage を返さないため測定手段が無い）。事前見積もりは入力 約5,212 tokens

**誤判定の内訳（4件）— すべて A0 の人手ラベル自身が「境界例・保守的判定」と注記したケース**:

| 種別 | 件数 | 分類 |
|---|---|---|
| FN（A0=TP, judge=非修正） | 2 | 依頼文型（明示的な修正語彙がなく新規依頼と誤読・A0 も「弱い TP」と注記）1件 / 文脈依存型（直前の完了報告との矛盾でしか修正と分からず、`prev_action` の tool 名列では文脈不足）1件 |
| FP（A0=not_TP, judge=修正） | 2 | 暗黙批判型（質問形の暗黙批判。A0 も「意図は読めるが保守的に not_TP」と注記）1件 / 文断片型（引用が途中で切れ A0 も「判定不能で保守的に not_TP」と注記）1件 |

**明確な TP / not_TP での誤判定は 0件。**

**この結果の限界（断定しないこと）**:
- **n=10 の極小標本**で CI 幅が広い（49〜94%）。「hook レーンの N 倍良い」といった倍率の主張はできない
- precision は hook レーンと**統計的に区別できない**。「llm_judge の方が正確」とは言えない
- **`prev_action` の有無が recall を変えるかは未検証**（今回は非 null 59.5% の条件で1回測っただけで、
  prev_action なしとの対照は取っていない）。したがって §5-A1 の migration 解凍条件のうち
  「G1 で prev_action の有無が有意差を生むと実証された」は**依然として未成立**

---

**（以下は G1 が通らなかった場合の扱い。今回は PASS したため発動しないが、
将来の再測定で閾値未達になった場合に備えて残す）**

**G1（correction_semantic/llm_judge レーンの recall/precision 実測ゲート・§3.3）が閾値未達の場合**:
C(a)（週1の「手直しの減少」系列）は**作らない**。柱3(a) の headline は **`first_try_success`
（唯一動いている軸・実測 0.9403）一本に絞り**、`rework_rate` / `correction_recurrence` 系列は
**表示しない**（`not_measured` 表示すら出さない＝そもそも系列を作らない）。

分母が信頼できない状態で無理に系列を出すより、出さない方が #376「数字が嘘をつかない」に忠実。
半端に測った rework 系列は §2.6-2 の「分母1桁の断定表示」と同じ失敗を再生産する。

---

## 8. 検証（各 Phase の完了条件）

| Phase | 完了条件 |
|---|---|
| 0 (B1) | SessionStart の出力が**単一 JSON dict**。平常時1行。異常系 fixture で必要情報が消えないこと |
| A0 | **実コーパスリプレイ**で修正語を含む発話の検出率と FP 率を実測。合成 fixture の緑では完了としない |
| A1〜A5 | **新規記録**で sidechain 由来0件（既存行の扱いは §5-A1 の方針どおり）。`prev_action` 持ち越しのテスト。**A5 は rev2/rev3 で `correction_type` を触らない方針に確定した（§2.0）ため `correction_recurrence` は恒久的に None のまま**（saturated ゲート含め決定論テストで固定）。A5 の完了条件は `provenance.category` の内訳が C(a) の TP と同一母集団で表示されること |
| B | **固定 corpus + 複数 PJ/global group のテスト**で「sidechain 0 / machinery 0 / content-rich 供給あり / 既読差引き後も順位規則を満たす」（単日目視では不十分）。**2026-08-14 追記**: machinery 0 の確認は合成 fixture に加え、**実ストアに対する dry-run を1本必須**とする（合成 fixture だけでは §5 Phase B の実測を再現できないため。tacchi 追加要求） |
| G1 | ✅ **2026-08-13 PASS**。`a0_eval_set.jsonl` の正解ラベルと llm_judge レーンの判定を固定実コーパス（本番 DB 非汚染の一時 DB）で突合。**recall 80.0%（Wilson 95% CI [49.0%, 94.3%]）で CI 下限が hook レーンの 4.5% を大差で上回る**。precision 80.0% は hook レーンの 87.5% と有意差なし。詳細・限界は §7.4 |
| D（PR4 のみ） | ✅ **2026-08-13 実施済み**。新規 accept（A レーン＝evolve drain 経由）が `revert_available=true` で記録され、`bin/evolve-revert` が dry-run で復元内容を印字。`bin/evolve-revert --list` が entry_id 一覧を出す（read-only・書込ゼロを実測確認）。**PR2/PR3 は 2026-08-13 凍結中につき対象外**（`optimize.py::save_history_entry` / `run_loop.py` 経由の採用は revert 対象外のまま。§5 Phase D） |
| E | ~~correction → skill diff → accept が synthetic E2E で1周する~~ → **2026-08-14 訂正**: **E1**: synthetic E2E で accept 1周（emit → 適用 → `--accepted` → optimize_history の accept → `bin/evolve-revert` で戻せる）+ 失敗系（applied だが accepted なし＝deferred / 未知 ID / 重複指定 / 非対話経路からの拒否）。**E2・E3**: 凍結（解凍条件は §5 Phase E を参照） |
| C | `not_measured` と `no_data` が表示上区別される。欠測週が「改善」に化けない（**欠測の定義に分母側 `utterances.db` の ingest 停止週も含める**——#351 の16日沈黙の前科を踏まえる）。C2/C4 は因果を断定せず「適用後に指標が改善／悪化した**関連**」として表示し、最低分母・観測窓・複数 accept が重なる場合の `unattributed` を明示する。C4 は synthetic E2E |

共通: `python3 -m pytest` **exit 0**（件数は契約にしない）+ `bin/evolve-dogfood-gate --layer light` exit 0 + `claude plugin validate`。

---

## 9. レビュー結果と反映

- **tacchi**（修正要 → 反映済み）
  - M1 数値の食い違い → 実測で決着（§2.4 に全ての数え方を併記）
  - M2 柱3の因果分解 → §3.2
  - M3 llm_judge・corrections の汚染 → §2.2 / A3
  - M4 再 ingest 未実装 → §5-A1
  - S1 出力チャネル契約 / S2 週次 marker の凍結 / S3 C4 は D1 依存 / S4 B2 は実測後に決める → 各所
  - **「汚染を除いた提案の中身は y/n を押す価値がある」ことを実データで確認**（§2.3-e）
- **codex**（設計修正要 → 反映済み）
  - **[Must] 朝の y/n と週1 accept は別レーン** → §3.1 を全面改訂・Phase E を新設
  - [Must] Phase 順の是正（D1 は並行可・C4 は D1 後）→ §6
  - [Must] 再 ingest の具体設計 / A1 完了条件の定義不足 → §5-A1
  - [Must] A4 は proxy を設計段階で確定 → §7.2
  - [Must] C1 の影響範囲（表示側も同時修正）→ §5-C1
  - [Must] C3 の凍結裁定の精緻化 → §5-C3
  - [Nit] pytest 件数を合否契約にしない → §8

~~**capture 調査（§2.1）は codex/tacchi のレビュー後に実施したため未レビュー。**~~
→ **2026-08-12 実施済み**。Phase 0 / A0 / Phase D の3設計をそれぞれ **codex 2巡**（round1 で全て `設計修正要`
→ 指摘反映 → round2 で差分のみ再レビュー）、Phase 0 は **tacchi も併走**（利用者に見える面のため）。
設計文書は `docs/decisions/drafts/054-{phase0-notification-routing,a0-capture-repair,phaseD-revert-lane}.md`。

**このレビューで ADR 本体の記述が覆った箇所**（本 rev で訂正済み）:

| 覆った記述 | 訂正先 |
|---|---|
| A0 の方針1（行頭アンカー）・方針2（500字除外の見直し）が主因 | §5-A0（両方とも実測で効果ゼロ。真因は語彙欠落） |
| A0 が (a) 系の数字を作る（§3.3 の依存図） | §3.3・§5-A0（recall 約4.5%＝A0 単独では作れない） |
| Phase 0 は「平常時 0〜1件発火」前提 | 実測は4系統・フル文連結412字＝**2件以上の結合が常用経路**（`drafts/054-phase0-notification-routing.md` §4.1） |
| `optimize.py` の accept 記録は健全 | `--auto --dry-run` が `approved=True` の entry を書く＝**柱3(b) の分母汚染**。Phase D PR1 で修正 |

### 9.1 2026-08-13 rev（tacchi / codex 各1巡 + 頭の実測）

| レビュアー | 指摘 | 反映先 |
|---|---|---|
| tacchi [Must]2 / codex [Must]4 | C(a)/C(b) を分離し、correction_semantic（llm_judge）の recall/precision を実測してから C(a) を作るべき | §3.3（G1 計測ゲート新設）/ §7.4（不通過時は柱3(a) headline を `first_try_success` に絞る） |
| tacchi [Must]5 / codex [Must]5 | Phase E は「経路が無い」のか「経路はあるが起動しない」のかを実測せず設計すると誤設計になる | §5 Phase E（着手前ゲート：実 repo で手動1周を先に実施し、その結果を E 設計書 §1 とする） |
| codex [Must]3 | A1・A3・A5 を並列と表記していたが、A3 の汚染 cleanup は A1 の再取り込みと衝突する | §6 実施順（A2∥C1 → A1 → A3 → G1 → A5 の直列化） |
| tacchi [Should]3 | §2.6 の8件は「いま現在」嘘をつき続けている。C1 は他 Phase に非依存なので前倒しできる | §6（C1 を A2 と並行の最優先へ前倒し） |
| codex [Should] | A1 の全履歴 migration は費用（最悪3,604件の再判定）を見積もる前に着手すべきでない | §6（A1 二段階化）/ §7.1（2026-08-13 決定） |
| tacchi [Should] | A3 のフル後始末フェーズは過剰。corrections 昇格済み2件だけ即時対応すれば足りる | §6（A3 縮小）/ §7.1（2026-08-13 決定） |
| codex [Should] | A5 単独の `distinct_types >= 5` は語彙を増やすだけで達成できる vanity gate | §6（成果指標から降格） |
| codex [Should] | B3 の上限引き上げは流入量再計測を経ずに判断すべきでない | §6（B3 は A1/A2 後）/ §7.1（2026-08-13 決定） |
| 頭の実測（`optimize_history` 全41 entry の writer 別分解） | D1 の PR2/PR3 対象レーンの accept 実績を確認したところ、**tacchi は当初「PR3（optimize.py）は accept 2件だから後回し」としたが、実測では2件だったのは PR2 側（run_loop）で、PR3 側は0件だった**。「後回し（凍結）」という方向自体は正しく、対象レーンが入れ替わっていたため、頭が実測して両方の凍結に確定した | §5 Phase D（D1 の PR2/PR3 凍結裁定） |

### 9.2 2026-08-13 rev2（A1 第二段階 migration の設計レビュー・tacchi / codex 各1巡 + 頭の実測）

| レビュアー | 指摘 | 反映先 |
|---|---|---|
| tacchi [Must]① | `extractor_version=3` が本番 DB に0件＝**`prev_action` 修正が実環境で効く証拠がゼロ**のまま 18,913件を作り直そうとしている | 頭が一時 DB で end-to-end 実走を実測（非 null 67.55% / バグ候補0件）。§5-A1「migration 保留の裁定」1 に記録 |
| tacchi [Must]② / codex [Must]5 | 完了条件「キーが突合する」は不十分。件数一致では**入れ替わりを検出できず**、transcript 書き換え時は同じ `(source_path, line_no)` が別発話を指したまま通る＝既判定 3,052件の silent 誤帰属 | §5-A1「migration 保留の裁定」3（集合包含 + `text_hash` 一致。migration 自体は保留につき将来の解凍時の要件として保存） |
| codex [Must]1・2・3 | atomic rename 単体では安全でない（DuckDB は複数プロセス同時 write 非サポート・旧 inode への書き続け・`close()` で file handle が解放されない・WAL 置き去り禁止） | 同上3。**全 writer 共有 flock の新設が必要**＝コストが重いという裁定の根拠 |
| codex [Must]4 | orphan を「ファイル消失行」に限ると、走査対象外になった subagents 由来 2,955件が消え、ADR の「既存行は purge しない」契約に反する。引き継ぐべきは `old PK − rebuilt PK` の全 residual | 同上3 |
| codex [Must]6 / tacchi | transcript が抽出中に追記される競合で「未抽出なのに処理済み」の永久欠落が起こりうる | 同上3 |
| tacchi [Should] / codex [Should] | 新 CLI を `bin/` に足すな（既に一度きり migration CLI の残骸が2本ある / `scripts/migrations/` の一回性スクリプトが適切） | migration 保留により CLI 自体が不要になり解消 |
| tacchi [Should] / codex（実装確認） | 設計文書の「observe hook が随時 `utterances.db` へ書く」は**事実誤り**（hooks に writer なし） | §5-A1「設計文書の誤りの訂正」。頭も grep で実測確認 |
| **頭の裁定**（tacchi 実測 `judge_runner.py:109-126` から導出） | judge は newest-first + 日次上限200件で、未判定在庫 10,419件の後ろの古い発話に**到達しない**。よって既存行の `prev_action` を埋めても judge は使わない＝**全履歴 migration の実効便益は G1 の測定条件を揃えることだけ**。それは本番 DB を触らず一時 DB で達成できる | §5-A1「migration 保留の裁定」2 / §6（A1 第二段階を保留・G1 は一時 DB 方式） |

### 9.3 2026-08-14 rev3（Phase B/E 設計レビュー・codex 2巡 + tacchi 1巡 + 頭の実測3件）

設計正典: plan `drafts/054-phase-be-design.md`（v4）。実測は全て2026-08-13・read-only（DuckDB は
`read_only=True`、ストアの sha256 不変を確認）。

**codex 1巡目（`設計修正要`）**

| 指摘 | 反映 |
|---|---|
| [Must]1 group に鮮度も単一 `signal_key` も無く順位キーが計算できない | §5 Phase B の代表値契約（`min(signal_keys)` / 既読差引き後の再計算） |
| [Must]2 「二重ソートは冪等」は誤り。`max_groups=3` の早期打ち切りで4件目以降が候補外 | §5 Phase B で**順位と打ち切りの分離**に設計変更。主張を撤回 |
| [Must]3 `source_correction_keys` は enrich を通らず provenance が伝播しない | §5 Phase E「E をやるとしたら」1 に保存（E2 凍結） |
| [Must]4 再提示抑止が逆（reject 後は同 ID 再 emit、accept 後は新 ID 再生成） | 新2として起票（**issue #446**） |
| [Must]5 E1 は承認 provenance を機械強制せず信頼境界が変わらない | 頭が裁定（実施するが要件を取り込む。下記「頭の裁定」参照） |
| [Must]6 correction → pattern の schema が未定義 | §5 Phase E「E をやるとしたら」3 に保存 |
| [Should]1〜6 | tracked/alias fold の処理順・除外内訳の返却 schema・PR3/PR5 の書込境界衝突は誤り（撤回）・E0 の合格判定・E0 不通過時の扱いを設計者が決める、等。全て反映済み |
| [Nit]1〜4 | `cross_pj_confirmed` の型・契約テスト追加・`--accepted`/`--accept` 名前衝突・shrink-freeze 検証計画明示。全て反映済み |

**tacchi 1巡目（`設計修正要` — 条件付き着手可）**

| 指摘 | 反映 |
|---|---|
| [Must] 順位規則の前に read 時 machinery 除外が要る（朝の候補300件中47件が委譲メッセージ・上位10件中6件） | §5 Phase B の PR2-a を新設し最優先に。`is_machinery_prompt` が47/47捕捉することを実測で裏取り |
| [Must] 規則1が global レーンを持ち上げない（confirmed と global observed は別物） | §5 Phase B のキー1を「confirmed **または** global 所属」に再定義 |
| [Must] E0 は「実施予定」でなく「実測済み・不通過」として扱い、この設計書で裁定せよ | §5 Phase E へ実測結果を記録 + 頭が凍結を裁定 |
| [Should] `detected_at` は判定時刻であって発話時刻ではない | §5 Phase B のキー3を発話時刻（read 時 join）に変更 |
| [Should] `cross_pj_confirmed` は発火0件＝実質no-op。発話の実時刻と観測ベースcross-PJを出せ | §5 Phase B の提示文を全面改訂 |
| [Should] 「52週→20週」は三重の但し書きが要る | §5 Phase B の B3-1 効能を「余剰+370/週」に書き換え（さらにユーザー裁定で+232/週に確定・§7.1） |
| [Must] 「配線するだけで下流は改造不要」は形式的に真だが実質空 | E2凍結。当該表現を削除 |
| 観点6 柱3(b)はE2なしで作れる（skill_evolveレーンが生きている）。「Eが前提」は過大主張 | 「E1導入後に積み始めうる」に訂正（過大主張は撤回） |
| 追加指摘 | corrections ストアの入力衛生（**issue #445**）／実ストア dry-run テスト（§8 B の完了条件）／A1保留との相互作用 |

**codex 2巡目（差分レビュー・`設計修正要` → 全て反映）**

| 指摘 | 反映 |
|---|---|
| [Must]1 PR2-aの除外位置が誤り（`_read_new` だけでは `bootstrap_backlog._read_backlog` に machinery が残り母集団分裂） | `filter_actionable` に集約 + `_read_backlog` も同述語 + `excluded_machinery_*` の surface 契約 |
| [Must]2 「順位付けと limit を分離した呼び出し口」は既存 API に存在しない | signature を明記（`max_groups: Optional[int] = 5`・既定5のまま） |
| [Must]3 既読差引き後の再計算が現データ形で実装不能（`_slim_group` が個別 key 情報を失う） | `signal_meta_by_key` の保持契約を追加 |
| [Must]4 「非対話経路から accepted を渡せないことを機械的に保証」は実現不能（CLI は呼出元を認証できない） | E1要件2を「既知 call site をテストで固定」に弱め、承認 token は別設計と明記（過剰約束を撤回） |
| [Should]1 join は PJ ごと・group ごとの query だと全DB走査の反復 | O(U+S) の一括 map 方式 + 失敗4種の区別 |
| [Should]2 cutoff は TTL と重複しないが日付・境界・設定場所・返却分岐が未定義 | §5 Phase B の cutoff 契約に仕様表を追加（90日／境界規則／userConfig／全分岐返却）+ 正直な効果の見積り |
| [Should]3 skill_evolveレーンの主張はコード上正しいが「必ず積み始める」は保証できない | 表現を「積み始めうる」に弱め、PR3後に実測する条件を追加 |
| [Nit] キー4のfixture定義が不十分 | §8 B の契約テストにキー1〜3同値+既読差引きで順序が変わるケースを追加 |

**レビューは2巡で打ち切った**（rules/design-review-gate: レビュアーの指摘が全て具体的な修正指示の形で
解釈の余地がないため、3巡目は手続きのための手続きになる）。

**codex と tacchi が食い違った1点（E1 の可否）を頭が裁定した**

- **codex [Must]5**: `evolve --drain --accepted <id>` を agent が任意に実行できると、その CLI 呼出し
  自体が「人間が y を押した」根拠になる。現行の inline python MUST と同じ信頼境界を、より呼びやすい
  CLI に移しただけ → 見送るべき
- **tacchi 観点6**: `learning_skill_md_must_not_enforcement` の既知欠陥類型の根治であり、
  E0 の結果と無関係に価値がある → やるべき
- **頭の裁定: 実施する**。両者の指摘は排他ではない。現行の inline python も実行するのは Claude なので
  信頼境界は既に「Claude が対話の結果を受けて実行する」。CLI化しても境界は悪化しない（codexの指摘は
  「E1は承認provenanceの問題を解決しない」であって「E1が問題を悪化させる」ではない）。一方でE1は
  「実行され損ねてacceptが記録されない」という別の実害を確実に消す。したがって実施し、
  codex [Must]5 の要求をE1の設計要件として全て取り込む（詳細は §5 Phase E）。

**頭の実測3件**（設計判断の裏取り）:
1. `is_machinery_prompt` が朝の候補47件を47/47捕捉すること（§5 Phase B の machinery 除外の根拠）
2. judge需給表（週次流入・上限・余剰。tracked外PJの扱いのユーザー裁定を経て+200→+232/週に確定・§7.1）
3. E0（corrections→SKILL.md のJaccard通過率）: 有効corrections 155件×SKILL.md 23件で通過0件・最大0.0721

### 9.4 2026-08-14 Phase B/E 実装時に3 PR 連続で出た同一の型（codex cold review）

**型: 「返り値にキーを足しても、それを読む側が旧契約 / 早期 return のままなら画面に出ない」。**
実装は毎回正しく、毎回 **目的を達成していない**。`learning_skill_md_must_not_enforcement`
（MUST と書いても手動 python は実行され損ねる）の裏返しで、**こちらは「機械は正しく値を出しているのに、
それを読む層が受け取っていない」**side。

| PR | 現れ方 | 層 |
|---|---|---|
| #450（E1）1巡目 | `--accepted` を実装したのに `references/diagnose.md` が旧契約（「drain が自動 accept する」「reject ID だけ控えろ」）のまま。**その文書に従うと accept 0件問題がそのまま再発する** | 手順書 |
| #450（E1）2巡目 | 1巡目の修正で「提案ごとに `id` を併記せよ」と書いたが、提案 identity は `(repo_id, repo相対path, before_sha)` ＝**ファイル単位**で `_extract_candidates` が同一 `skill_path` を1件に畳む。マッチ単位で提示すると**複数の提示が同じ1個の `id` を共有**し提案単位の判断が成立しない → `skill_path` 単位のグループ化（1 SKILL.md = 1 提案 = 1 判断）に統一 | 手順書（前巡の修正が生んだ新欠陥） |
| #452（B2）1巡目 | `build_review()` が `excluded_machinery_*` を返すのに `daily/proposal_digest.py` が捨てていた／`references/correction-review.md` に表示指示が無く、**全件 machinery のとき「新規なし ✓」とだけ出て除外が完全に隠れる** | consumer + 手順書 |
| #452（B2）2巡目 | 1巡目の修正後も `session_notify/collectors.py` の `if not groups: return None` により**全件 machinery のとき除外件数を読む前に return**（修正が実経路から到達不能）／`bootstrap_backlog.build` の marker 済み早期 return が `excluded_machinery_total: 0` を固定し手順書の MUST が実行不能 | hook consumer + phase 出力 |

**得られた運用規則**（以後の advisory / observability 追加に適用する）:

1. **キーを足したら、そのキーを読む層を全列挙して1つずつ潰す**（producer だけ直して完了にしない）。
   最低でも: phase 出力 → digest → hook consumer → SKILL.md/references の4層
2. **「他に出すものがあるときだけ添える」設計にしない。** その機能が存在する理由が最悪ケース
   （＝除外が全件に効いたケース）なら、**最悪ケースでこそ黙る**設計になっていないかを必ず確認する。
   ノイズ懸念は**表示条件でなく文面**で解く
3. **早期 return を持つ関数にキーを足すときは、早期 return 側の固定値が嘘にならないか確認する。**
   `bootstrap_backlog` の marker 済み分岐は `0` を固定しており、daily 側は
   `_exclude_bootstrap_consumed` で marker 前の検出を落とすため、**どちらのレーンからも見えない死角**が生まれていた
4. **差分レビューを省略しない。** #450 2巡目の [Must] は **1巡目の修正が生んだ新しい欠陥**で、
   1巡で打ち切っていたら実行不能な手順書がそのままマージされていた

---

## 10. 参照

- 設計対象 repo: `/Users/matsukaze-takashi/matsukaze-utils/evolve-anything`
- 関連 issue: **#400**（全PJ一括の対話 evolve 入口・OPEN）/ **#401**（戦果ボードの週1導線・OPEN）/ #402（1コマンド revert・2026-08-12 マージ済み）/ #379（縮小・CLOSED）
- Phase B/E（2026-08-14 rev3）起票 issue: **#442**（judge 母集団の是正＝B3-1）/ **#443**（朝の提示の是正＝B2）/
  **#444**（accept 記録の機械化＝E1）/ **#445**（corrections ストアの入力衛生）/
  **#446**（reject された提案が次回 emit で再提示される）/ **#447**（`similarity.tokenize` が日本語を分割できない）
- 凍結の単一ソース: `scripts/lib/shrink_freeze.py`（`SHRINK_FREEZE_ACTIVE = True`）
- codex レビュー全文: `<session scratchpad>/codex_review.log`（**clear 後は消える可能性があるため、本文書の §9 が要点の正典**）
