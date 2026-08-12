# ADR-054: 4本柱（#379 目標体験）完成設計

- **Status**: Accepted
- **Date**: 2026-08-12
- **関連**: #379（CLOSED）/ #400（OPEN）/ #401（OPEN）/ #402（マージ済み）/ ADR-041 / ADR-053

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
| `correction_recurrence` | **測定不能** | `correction_type` の語彙が実質1種（distinct_types=1 < floor 5） |
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
                              ★ ここに変換経路が無い ★
                                          ↓
レーン2（週1）: evolve 実行中の pending proposal
                → 対象ファイルの実変更 AND 明示 accepted ID
                → optimize_history の accept → 戦果ボード / revert
```

根拠: `daily/proposal_digest.py:393-460`、`skills/evolve/references/correction-review.md:63-64`（朝の y/n の実体）
／ `skills/evolve/SKILL.md:288-303`、`evolve_decisions/_ingest.py:83-107`（accept の発生条件）

**Phase A/B が直接増やすのは human-confirmed correction であって accepted decision ではない。**
真のボトルネックは記録量ではなく、**correction → 適用可能な skill diff → 明示 drain の変換・起動経路が存在しないこと**。

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
| A5 | `correction_type` の語彙を増やす（distinct_types=1 → floor 5） | `correction_semantic/` |

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
   **できなければ 3,604件の再判定＝LLM 費用**（`llm-batch-guard` 該当・事前にユーザー確認）
5. **`prev_action` が `extractor_version=2` の行で全件 null**（A0 の実測窓 1,124件すべてで確認・2026-08-12）。
   correction の文脈判定に使えない既存データ欠損であり、**A1 の再抽出設計にこの列の充填を含める**
   （A0 スコープ外として A1 へ申し送り）。充填しない場合、B の提示品質は「発話単独」の情報しか使えない

**A1 — 完了条件の定義**
「sidechain 由来 0件」は**消失 transcript 2,728件があるため定義不足**。
ingest は現存する `*.jsonl` と `*/subagents/*.jsonl` だけを走査する（`ingest.py:73-99`）。
→ **削除済み `source_path` の既存 DB 行を残すか purge するか**を決め、完了条件を
「**新規記録**で sidechain 由来0件」＋「既存行は◯◯として扱う」の形にする。

**A1 — 除外の粒度**
sidechain 除外は user 行だけでなく `prev_action` の境界にも影響する（`extractor.py:217-269`）。
**ファイル単位か各行の `isSidechain` か**を明記し、sidechain user 行を飛ばした際に
**tool 名を次の main user 発話へ持ち越さない**テストを書く。

**A3 — 判定基準**
決定論基準は **`provenance.source_path` に `/subagents/` を含む**こと。
扱い: 放置 / TTL 45日の自然失効 / 対象を絞った一括 expired 化。
**corrections に昇格済みの2件は TTL では消えない**ので別途 invalidate するか判断が要る。

### Phase B — 朝の提示の質【柱2】※ Phase A 後

| ID | 内容 |
|---|---|
| B2 | 提案の並び順を見直す（**A2 適用後の実データを測ってから方式を決める**） |
| B3 | llm_judge 滞留の解消（A1 で -23%、上限見直し、非PJ `matsukaze-takashi` 1,017件の扱い） |

**B2 の未決事項**: 公平性を入れるなら ①cross-PJ confirmed 優先との順位 ②channel 内順位
③候補が1 channel だけの場合 ④SessionStart 上限2件との整合 を決める。**現時点で方式を確定しない**（P5/P7）。

### Phase D — 信頼【柱4】※ Phase A/B と**並行可**・C4 の前提

| ID | 内容 |
|---|---|
| D1 | `pre_extension` 残存2経路（`optimize.py::save_history_entry` / `run_loop.py`）を emit→drain lane に寄せる → **PR2/PR3 は凍結（下記）** |
| D2 | entry_id の導線拡充（`bin/evolve-revert --list` 相当） |

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

### Phase E — correction → accept の変換経路【柱3(b) の前提・**作ると決定**】

§3.1 のとおり柱3(b) の真のボトルネック。設計すべき最低3点（codex [Must]）:
1. 昇格した correction が**どの処理で具体的な skill diff 候補になるか**
2. その処理が**いつ起動されるか**
3. 朝の y/n の後、適用確認と `drain_pending(accepted=...)` まで**どう到達するか**

現行 accept は evolve の Step 3（matched skill の提案・適用）と Step 7.8（inline drain）に依存し、
通常の `--drain` 単体では accept を記録しない（`skills/evolve/SKILL.md:195-197,288-303`）。

**完了条件**: correction → skill diff → accept が **synthetic E2E で1周する**。

**着手前ゲート（2026-08-13・tacchi [Must]5 / codex [Must]5）**

§3.1 は「変換経路が存在しない」と断定しているが、部品（Diagnose の入力 / Step 3 の提案 /
Step 7.8 の drain）は全て既存で、過去に `human_accepted=True` が10件生まれてもいる（#376 で無効化）。
**「経路が無い」のか「経路はあるが correction が提案に反映されない・起動されない」のかで作るものが
全く違う**（後者なら配線と起動導線の修理で済み、新設凍結にも収まる）。

→ **E の設計着手前に、実 repo で手動1周実験を行う**（1日）。実 correction を積んだ状態で evolve を
回し、correction 由来の提案が出るか → y → drain → `optimize_history` に revert 可能な accept が
載るか、を実測する。**この結果が E 設計書の §1 になる**。synthetic E2E はその後。

加えて、E の設計では以下を先に確定する（codex）:
- 朝の y/n は「correction として採用」か「生成された patch の適用承認」か（後者を暗黙に兼ねさせるのは柱4違反。patch を見た後の明示承認が別途必要）
- 状態遷移に使う既存 artifact（`corrections.jsonl` / 既存 pending proposal / 既存 optimize history。**新 store は不可**）
- correction identity ↔ diff proposal identity の対応（1→N・N→1・重複の定義）
- 起動点（既存 SessionStart / evolve / queue のどこか。last-shown 状態を新設せず既存 timestamp から read 時導出）
- 状態機械の各境界と再実行時の冪等性、stale/conflict・生成失敗・history 書込失敗の補償動作
- 「価値ある diff が生成される率」の評価（配線が一周するだけでは E の効果を証明しない）

### Phase C — 週1の数字【柱3】

| ID | 内容 | 系 | 凍結 |
|---|---|---|---|
| C1 | 数字の正直さ（§2.6 の8件を潰す） | (a) | なし |
| C2 | 週次系列（欠測週は「データなし」と明示） | (a) | なし |
| C3 | 週1の起動導線 | 両方 | **要裁定（下記）** |
| C4 | 取り下げ候補 → revert への接続 | (b) | なし（D1 + E が前提） |

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

A2 ──┐
C1 ──┘ 並行可（C1 は表示層のみ・A/B/E/G1 の全てに非依存）
  ↓
A1（forward 修正 → 実測 → migration は費用確定後）
  ↓ 実データ再計測
A3（汚染 cleanup）
  ↓
G1 計測ゲート ──不通過──→ C(a) は作らない（§7.4）
  ↓ 通過
A5 → C(a): C2
  ↓
B2/B3 ──→ E（手動1周 → 設計 → 実装）──→ C(b): C3,C4
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
- **B3（llm_judge 上限引き上げ）は A1/A2 後に流入量を再計測してから**。
  10,225件を処理すること自体は価値でなく、古い低品質候補に LLM 費用を払う危険がある（codex [Should]）
- **C4 は D1 と Phase E の両方が前提**

**worker の衝突回避**: A2（`detectors.py`）と A1（`extractor.py` + migration）は同ファイル群なので**順次**。
E（drain 周辺）と Phase D の書込境界も重なるので並行させない。並行してよいのは A2 ∥ C1 まで。

**実データ検証の挿入点（合成 fixture での完了を禁止・P8）**:
①A2 後＝同じ 12 group サンプルを再抽出し「委譲プロンプト0件」を実測 ②A1 後＝再抽出時間・行数差・
`prev_action` 充填率・既判定キー再利用率 ③G1＝固定 corpus の recall/precision・channel 間重複
④E 後＝実 correction から有用 diff が生成され、明示承認・accept・revert まで到達すること

---

## 7. 判断の記録

### 7.1 決定済み（2026-08-12 / 2026-08-13 ユーザー判断）

| 論点 | 決定 | 影響 |
|---|---|---|
| sidechain 除外の層 | **記録層（extractor）で根治** | 再 ingest migration の新規開発が Phase A に入る。llm_judge 滞留も 23% 減 |
| 柱3(b)（採用効果・取り下げ候補） | **Phase E を作って完成させる** | v1 案の「4週の計測ゲート」は撤回 |
| A4「手直し」の定義 | **capture 調査の結果、corrections を分子に使う案（A4-ii）は却下** | 下記 7.2 |
| A3 の既存 FP（rephrase 16 / llm_judge 33 / corrections 昇格済み2）の扱い | **縮小**（2026-08-13）。corrections 昇格済み2件は即時 invalidate、残り49件は TTL 45日の自然失効に任せる。フルの後始末フェーズは作らない | Phase A3 のスコープ縮小。§6 実施順に反映済み |
| B3（llm_judge 上限200件/日）を上げるか対象を絞るか | **即決しない**（2026-08-13）。A1/A2 後に流入量を再計測してから判断する（10,225件処理自体は価値でなく古い低品質候補に LLM 費用を払う危険） | B3 着手を A1/A2 完了後に後ろ倒し。§6 実施順に反映済み |
| 再 ingest（A1）に伴う LLM 再判定費用の事前見積もり | **A1 を二段階に分割**（2026-08-13）。forward 修正 + read 時 sidechain 除外を先に実施し、全履歴 migration は費用見積もり（最悪3,604件）とベンチの後に着手判断する | A1 の完了条件が二段階化。§5-A1・§6 に反映済み |

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

### 7.3 未決

- ~~A3 の既存 FP（rephrase 16 / llm_judge 33 / **corrections 昇格済み2**）の扱い~~
  → **決定済み（2026-08-13・§7.1）**。縮小方針で決着
- ~~B3 の llm_judge 上限 200件/日を上げるか、対象を絞るか~~
  → **決定済み（2026-08-13・§7.1）**。「A1/A2 後に再計測してから判断する」まで決着。
  上げる/絞るの結論そのものは再計測後に確定する
- ~~再 ingest に伴う LLM 再判定費用（最悪 3,604件・`llm-batch-guard` 該当）の事前見積もり~~
  → **決定済み（2026-08-13・§7.1）**。A1 の二段階化により、全履歴 migration 着手前に確定する運びで決着
- ~~A0 修理後の「手直し」分子の最終形（corrections 単独か weak_signals 併用か）~~
  → **A0 実測により「corrections 単独」は却下**（recall 約4.5%）。残る選択は
  「weak_signals 併用」か「`correction_semantic` の意味判定を主分子に据える」かで、**C1 の設計時に決める**
- `run_id` の秒精度（`optimize.py:97` の `strftime("%Y%m%d_%H%M%S")`）を UUID 化するか
  → **Phase D スコープ外・別 issue**（D1 は複数一致エラー検出で正しさを担保済み）

### 7.4 G1 が通らなかった場合の柱3(a) の扱い

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
| A1〜A5 | **新規記録**で sidechain 由来0件（既存行の扱いは §5-A1 の方針どおり）。`prev_action` 持ち越しのテスト。`correction_recurrence` が None を脱する |
| B | **固定 corpus + 複数 PJ/global group のテスト**で「sidechain 0 / machinery 0 / content-rich 供給あり / 既読差引き後も順位規則を満たす」（単日目視では不十分） |
| G1 | `a0_eval_set.jsonl` の正解ラベル（TP 10件 + census TP 7件）が weak_signals の llm_judge レーンで検出される件数を突合し、recall/precision と channel 間重複を固定実コーパスで実測する。閾値未達なら C(a) は作らず、柱3(a) の headline を `first_try_success` 一本に絞る（§7.4） |
| D（PR4 のみ） | 新規 accept（A レーン＝evolve drain 経由）が `revert_available=true` で記録され、`bin/evolve-revert` が dry-run で復元内容を印字。**PR2/PR3 は 2026-08-13 凍結中につき対象外**（`optimize.py::save_history_entry` / `run_loop.py` 経由の採用は revert 対象外のまま。§5 Phase D） |
| E | correction → skill diff → accept が **synthetic E2E で1周する** |
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

---

## 10. 参照

- 設計対象 repo: `/Users/matsukaze-takashi/matsukaze-utils/evolve-anything`
- 関連 issue: **#400**（全PJ一括の対話 evolve 入口・OPEN）/ **#401**（戦果ボードの週1導線・OPEN）/ #402（1コマンド revert・2026-08-12 マージ済み）/ #379（縮小・CLOSED）
- 凍結の単一ソース: `scripts/lib/shrink_freeze.py`（`SHRINK_FREEZE_ACTIVE = True`）
- codex レビュー全文: `<session scratchpad>/codex_review.log`（**clear 後は消える可能性があるため、本文書の §9 が要点の正典**）
