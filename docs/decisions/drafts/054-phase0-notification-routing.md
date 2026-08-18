# ADR-054 Phase 0（B1）実装前設計 — SessionStart 通知の1行化

- **Status**: Draft rev7（codex round1 [Must]8/[Should]4/[Nit]1・codex round2 [Must]9/[Should]4（新規1件含む）・tacchi 体験レビュー [Must]2/[Should]3/[Nit]2・tacchi digest文言レビュー [Must]2/[Should]3/[Nit]2 を全て反映済み。rev6 で ack 方式（pending_trigger/icebox）・実効上限契約・「pure」表記統一を反映。**rev7 で digest 文言を確定**（対処導線・警報性を保つ具体語へ変更、icebox レーン1の混在時短縮フレーム、末尾導線の条件付き付与）。着手可条件を全て充足・digest文言これ以上の往復なし）
- **対象**: `hooks/restore_state.py:handle_session_start`
- **親文書**: `docs/decisions/054-four-pillars-completion-design.md` §2.3(a), §5 Phase 0, §8
- **実測日**: 2026-08-12
- **範囲**: 本文書は**表示ルーティングの設計のみ**。柱1（capture 修理）・柱3（週1の数字）・柱4（revert 導線）には触れない。

---

## 0. 要約

ADR-054 は「現行9系統」と書いているが、実測すると **9系統で数は合っているが、内訳の性質は3つの異なる層に分かれる**:

1. **freshness gate 付き**（3系統・evolve_queue_notice / judge_cap / icebox）: 生成物の `generated_at` が古ければ内容を解釈せず health notice に自動置換する既存機構を持つ
2. **live 計算・staleness 概念なし**（5系統・pending_trigger / spec_drift / evolve_drain / data_dir_migration / utterance_staleness）: 外部 marker の鮮度でなく、その場の git/ファイル状態を直接判定
3. **2チャネル同時出力**（1系統・session_proposal）: 唯一 `systemMessage` + `hookSpecificOutput.additionalContext` を両方持つ

rev1 では「間引いてよいかどうか」を Tier1/Tier2 の2値で単純化していたが、codex round1 レビューで**副作用のタイミング（読む/消費する処理が『表示するかどうか』を待たずに先に走ってしまう）**を系統ごとに実コードで確認していなかったことが判明した。rev2 は全9系統について「表示を決める前に何が既に確定してしまうか」を実装まで降りて確認し、**Tier 判定を「間引いても再現するか」の実測ベースに作り直した**（§5）。

rev3 は tacchi（体験・実態突合レビュー）の指摘を反映した。tacchi は独立に codex と同じ spec_drift の消失リスクを検出した（[Must-t1]＝codex [Must]2 と同一）ほか、**「発火0〜1件が平常時」という rev1/rev2 の前提が実測（今朝の transcript で4系統同時発火・計約325字）と食い違うこと**（[Must-t2]）、**結合フォーマット自体が未設計だったこと**（[Should-t3]）、**work_context summary が単一 JSON dict 化しても依然として画面占有の主犯級であること**（[Should-t4]）を指摘した。rev3 は「**2件以上の同時発火が通常経路**」という前提に作り直し、**digest（短縮形）とフル文の2軸表示モデル**を新設し、work_context summary の圧縮も Phase 0 のスコープに含めた（頭の推奨に従う）。

---

## 0.1 codex round1 + tacchi 対応表

tacchi のレビューメッセージは受領済み。codex 分と tacchi 分で同一指摘は「codexと同一」と注記し、表を1つに統合した。

| 指摘 | 種別 | 対応 | 反映箇所 |
|---|---|---|---|
| SessionStart 出力を stdout 全体で単一 JSON dict にすべき（work_context が別行のまま） | [Must] | **採用**。work_context summary を `hookSpecificOutput.additionalContext` に統合し、stdout は「0行 or 1行」の厳密な二値にする | §4.1, §6.3 |
| spec_drift は表示前に reminder/cooldown を消費し永久消失しうる | [Must] | **採用**。`spec_trigger.detect()` に `persist=False` を渡し marker 保存を分離、実際に表示できたときだけ `save_marker()` を呼ぶ two-phase 化 | §5.2, §6.2 |
| judge_cap の `capped`/`out_of_range_verdicts` は Tier2 のままでは消失しうる（daily runner が次回上書きする一回性スナップショット） | [Must] | **採用**。judge_cap は全分岐を Tier1 に格上げ（部分 Tier1 化ではなく全面） | §3, §5.1 |
| icebox の on_shown 遅延案は既存 lock 契約（read→decide→print→record_seen の atomic 化）を壊す | [Must] | **採用（設計変更）**。icebox レーン1「成立」を Tier1 に固定し、**状態変更（record_seen）は現行どおり lock 内・収集時点で完結させ、defer するのは印字文字列だけ**にする。lock スコープは変更しない | §5.1, §5.3 |
| icebox レーン1の間引き規則が仕分け表とテスト方針で矛盾 | [Must] | **採用**。Tier1 固定に一本化（「畳まれる場合」の記述を削除） | §3, §7.1 |
| producer 不存在と破損を同じ沈黙にしている（evolve-queue.json 等） | [Must] | **採用**。「ファイル不在＝沈黙」と「ファイル存在するが読めない＝破損＝Tier1 health notice」を明示的に分離する分類ヘルパーを追加 | §5.4, §6.2 |
| pending trigger の Tier1 化だけでは最終出力までの生存を保証しない（serialize/print 失敗時に復元不能） | [Must] | **採用**。builder 単位の try/except 隔離 + 最終 merge/print 専用の fail-safe を設計に追加。新規 E2E テストで「収集成功→最終 JSON に必ず含まれる」を保証 | §5.5, §7.2 |
| 契約テスト「JSON行が高々1つ」は弱すぎる（非JSON平文・0件でも通る） | [Must] | **採用**。「stdout 非空なら splitlines() が厳密に1、json.loads 可、期待キーが同一 dict に共存」に強化 | §7.1 |
| 例外時の行数契約（stdout/stderr の切り分け）が不足 | [Should] | **採用**。「1行」契約は stdout のみに適用し stderr は対象外と明記。stderr 複数行 + stdout 1行の組み合わせを正式契約として明文化 | §4.3 |
| 「8個を pure builder へ」という表現が実態と不一致（副作用を伴う系統がある） | [Should] | **採用**。系統別に「状態変更は収集時点で完結」「状態変更を defer する」の2パターンに分類し直した（§5 参照） | §5 |
| 暫定400字が未完成仕様（文字/byte・区切り・「ほかN件」を含むか） | [Should] | **採用（rev5 で確定）**。測定単位=文字数（`len()`）・区切り=`" / "`・Tier1は上限に関係なく全件・超過分は digest単位で「ほか:系統名」表示（件数のみ禁止）。**頭裁定により上限は400字**（実 transcript 実測412字/79字digest後を根拠に併記） | §4.1, §4.4 |
| Tier 判定が freshness の文字列一致に依存し分岐網羅テストが未定義 | [Should] | **採用**。builder が tier を明示的に返す契約にし、分岐ごとのテスト一覧を追加 | §6.1, §7.1 |
| 「pending_trigger が唯一消失する系統」は言い過ぎ | [Nit] | **採用**。表現を「最も直接的な例」に弱め、他系統の消失経路も§5で個別に扱う | §5 冒頭 |
| spec_drift の消失リスク | [Must-t1]（**codexと同一** [Must]2） | 重複のため統合済み。rev2 の two-phase 化（`persist=False` + `on_shown`）をそのまま採用。`MAX_REMINDERS=1`（初回+リマインド1回の計2回で打ち止め・`spec_trigger.py:113`）という実装詳細も確認し、「未表示の間は disk 上のカウンタが一切増えない＝表示されない限り exhaustion が原理的に起きない」ことを証明として追記 | §5.2 |
| 「発火0〜1件が平常」という前提が実測と食い違う（今朝4系統・約325字が実測） | [Must-t2] | **採用**。「2件以上の同時発火が通常経路」に前提を作り直し、§4.1 の場合分け・テストの重心を結合パスへ移した。**rev4 で自分でも実 transcript を実測（412字・tacchi目算より大）。rev5 で頭裁定により Tier2 予算を400字に確定** | §4.1, §4.4, §7.1 |
| 結合フォーマットが未設計（prefix重複・並び順・畳み方） | [Should-t3] | **採用（tacchi/頭の digest 案を全面採用）**。1件発火時はフル文・2件以上発火時は全件 digest（短縮形）に統一。prefix は最終結合時に1回だけ付与。畳み表記は件数でなく系統名（「ほか: queue/judge」）に変更 | §4.1, §4.2, §4.4, §6.2 |
| work_context summary が画面占有の主犯級 | [Should-t4]（一部 **codex [Must]1 と同一**箇所） | **採用**。単一 JSON dict 化（codex分）に加え、**中身の圧縮も Phase 0 に含める**（頭の推奨どおり）。直近コミットは件数+先頭1件、未コミットファイルは件数のみ（少数なら列挙）に圧縮 | §4.5, §6.4 |
| session_proposal を Tier2 に置ける理由の明記 | [Nit-t6] | **採用**。additionalContext は常時フルのため Claude 経由の提示は Tier2 化の影響を受けないと1行追記 | §3 |
| CC の複数行 stdout 解釈は今朝の実測では4本とも記録されていた | [Nit-t7] | **採用**。「未確認」表記は維持しつつ、この実測結果を support する observation として追記（結論は変えない）。**rev4: 頭裁定#3により「未確認のままでよいが実装PRで実測必須」を §12 完了条件へ格上げ** | §2, §12 |

---

## 0.2 codex round2 対応表（rev6）

codex round2 の判定は `設計修正要`（[Must]6/8解消・2件未解消 → rev6で解消／[Should]1件解消・3件部分解消 → rev6で解消／新規[Must]1件 → rev6で解消）。全文: `codex_phase0_r2.log`（§11参照）。round1 で解消済みと判定された [Must]1/2/3/5/6/8・[Should]4 は本 rev で再検討しない（頭裁定どおり）。

| 指摘 | 種別 | 対応 | 反映箇所 |
|---|---|---|---|
| 2件以上発火時の digest が破壊的読み取り済み pending_trigger の本文を `トリガー提案1件` に置換し、内容を永久消失させる | [Must-new] | **採用（頭推奨のフル文例外）**。pending_trigger を digest 化免除（icebox レーン1と同型の例外2）とし、常にフル文のまま結合する | §4.2 |
| icebox は実際の stdout 印字前に record_seen される設計へ変わっており、最終merge/print失敗時に未表示のverdictが既読化される | [Must]4 未解消→解消 | **採用**。ack 方式へ全面改訂。lock を decide〜commit（print成功後のrecord_seen）まで保持し続けることで、既存の並行SessionStart二重通知防止契約を壊さずに commit を print成功後まで遅らせる | §5.3 |
| pending_trigger は読み取り時に削除されたままで、print失敗時の情報消失を設計自身が認めている。失敗3経路（builder例外/merge失敗/print失敗）を担保しない | [Must]7 未解消→解消 | **採用**。`peek_pending_trigger()`/`delete_pending_trigger()` を新設し ack 方式へ。新規 lock sidecar で icebox と同型の二重通知防止を実現。失敗3経路それぞれのE2Eテストを追加 | §5.5, §7.1-8 |
| marker書込失敗固有のテストが無い（抽象的な「1系統の内部例外」に留まる） | [Should]1 部分解消→解消 | **採用**。`save_marker`/`record_seen`/`delete_pending_trigger` それぞれを個別に例外送出させる具体テストを追加 | §7.1-4, §7.1-5 |
| 副作用分類表を追加したのに、実装方針では副作用を持つ8関数を依然「pure」と呼び文書内で矛盾 | [Should]2 部分解消→解消 | **採用**。「pure」という語を全箇所から排除し「印字を行わない収集関数」に統一 | §5, §6.1, §6.2, §7.2 |
| 400字が実効上限になっていない（Tier1無制限・suffix予算外・iceboxレーン1のreason長無制限で「最悪ケース約273字」評価が不成立） | [Should]3 部分解消→解消 | **採用**。「(a)最終文字列全体への強制上限は無い (b)400字はTier2予算配分ルール (c)icebox reasonは現状無制限でそのまま許容する」を明示する実効上限契約を新設。rev5の「約273字」見積もりは撤回 | §4.4 |
| 「Tier1は全件フル表示」と「Tier1もdigest化」が文書内で矛盾 | [Should-new] | **採用**。「Tier」（量の軸）と「フル文/digest」（詳しさの軸）を独立2軸として用語を再定義し、「全件フルで結合」という表現を「全量・無条件に結合」へ置換 | §4.2 |

---

## 0.3 tacchi digest 文言レビュー対応表（rev7）

tacchi の判定は `文言修正要`（Must 2・Should 3・Nit 2）。§4.1 で確定した「2件以上の同時発火が常用経路」という前提により、フル文はほぼ出番が無く **digest が実質唯一の日常表示になる**ため、digest が「対処導線」「警報性」を落とすと当該情報は毎朝出てこない、という指摘が Must 2件の共通の根。severity のタグは team-lead のメッセージ本文の強調度から判断した（表内で個別タグが明示されなかった行は文脈から分類）。

| 指摘 | 種別（推定含む） | 対応 | 反映箇所 |
|---|---|---|---|
| evolve_drain の digest `未drain提案1件` が唯一の対処導線（`evolve --drain`）を落としている | [Must] | **採用**。`記録待ち提案{N}件（evolve --drain）` に確定 | §4.2 |
| judge_cap/out_of_range の digest `judge範囲外N件` が「モデル劣化の警報」を capped と混同されうる形にしている | [Must] | **採用**。`judge異常応答{N}件（要確認）` に確定 | §4.2 |
| judge_cap/capped の digest `judge残{N}件` が行動不要の自動処理を宿題のように誤読させる | [Should] | **採用**。`judge持ち越し{N}件（自動）` に確定 | §4.2 |
| data_dir_migration の digest が実コマンド名（`evolve-fleet migrate-data`）まで届いていない | [Should] | **採用**。`DATA_DIR分裂（要migrate-data）` に確定 | §4.2 |
| icebox レーン1の敬体フル文がdigest列（体言止め）に混在すると文体が不自然。§4.4 最悪ケースの主犯（105字）でもある | [Should] | **採用**。混在時のみ `icebox成立: {body}` に短縮（body/reasonは一切パースしない）。ライブラリ側に `build_met_body()` 切り出しが必要 | §4.2, §6.2 |
| utterance_staleness の digest が「何が止まっているか」を明示していない | [Nit] | **採用**。`発話取込{N}日停止（要ingest）` に確定 | §4.2 |
| 行頭 prefix `今朝:` が daily runner 由来でない live 検出系統（drain/DATA_DIR）に誤った示唆を与える | [Nit] | **採用**。`今朝:` を削除し `[evolve-anything]` のみに | §4.2 |
| digest 行が状態羅列で終わり次の行動に繋がらない（目標体験「朝: 1行 → y/n だけ」に対して導線が無い） | [Should]1 | **採用**。末尾に `→ /evolve-anything:queue で開始` を条件付き（queue/drain/icebox のいずれかが含まれる場合のみ）で付与 | §4.2', §7.1-12 |
| 400字は行全体の上限でなく Tier2 の追加予算。畳みが実際に発動するのはほぼ icebox レーン1フル文が Tier1 予算を食った時だけ | tacchi 観察（§4.4 と同根の指摘） | **採用**。§4.4 の実効上限契約（(a)(b)(c)、rev6で新設済み）に「畳み機構の直接テストは icebox レーン1混在ケースを本命とする」を追記 | §4.4, §7.1-3 |

---

## 1. 現状の全数調査（実測）

`hooks/restore_state.py:handle_session_start`（712-737行）の呼び出し順。全て `try/except` で個別に保護され、例外は stderr へ。

| # | 系統 | deliver 関数 | 発火条件 | 出力形式（現状） |
|---|---|---|---|---|
| 1 | pending_trigger | `_deliver_pending_trigger`（163-176） | `pending-trigger.json` 存在 かつ非スヌーズ中（`trigger_engine/pending.py:35-50`） | plain `print(f"[evolve-anything:auto-trigger] {message}")`（173） — **非 JSON** |
| 2 | spec_drift | `_deliver_spec_drift`（178-193） | main 着地後の未追従コミットを `spec_trigger.detect()` が検出（ADR-044） | plain `print(message)`（191） — **非 JSON** |
| 3 | evolve_drain | `_deliver_evolve_drain`（216-289） | marker root 存在 かつ該当 slug に未 drain marker かつ apply 済み entry あり（244-264） | plain `print(...)`（274-278 or 282-286） — **非 JSON**、2文言分岐 |
| 4 | data_dir_migration | `_deliver_data_dir_migration_reminder`（291-339） | hook 文脈 かつ CC install layout かつ `needs_migration()` | plain `print(...)`（326-331 or 332-337） — **非 JSON**、初回/再発の2文言分岐 |
| 5 | utterance_staleness | `_deliver_utterance_staleness`（363-387） | hook 文脈 かつ install layout かつ 最終 ingest から14日超（`utterance_staleness_advisory`, 342-360） | plain `print(message)`（385） — **非 JSON** |
| 6 | evolve_queue_notice | `_deliver_evolve_queue_notice`（424-463） | hook 文脈 かつ install layout かつ（STALE/UNKNOWN　**または** FRESH で待ち PJ あり）（`queue_notice.py:40-92`） | `print(json.dumps({"systemMessage": ...}))`（461） — JSON、`hookSpecificOutput` なし |
| 7 | session_proposal | `_build_session_proposal_output`（466-550, 副作用なしの収集関数） | 同型 env ガード かつ proposal group あり かつ 未既読キーが残る | `{"systemMessage": ..., "hookSpecificOutput": {...}}` を返す。`handle_session_start` が checkpoint と1つの dict にマージ（759-773） |
| 8 | judge_cap | `_deliver_judge_cap_notice`（553-588） | 同型 env ガード かつ（STALE/UNKNOWN　**または** FRESH で capped/source_failed/skipped_locked/out_of_range） | `print(json.dumps({"systemMessage": ...}))`（586） — JSON、`hookSpecificOutput` なし |
| 9 | icebox | `_deliver_icebox_notice`（591-675） | 本体 repo かつ hook 文脈 かつ install layout かつ（レーン1成立未既読 **または** 棚卸し閾値超過/STALE/UNKNOWN） | `print(json.dumps({"systemMessage": ...}))`（657 or 673） — JSON、`hookSpecificOutput` なし |

**9という数はADRと一致**（実測で再確認・食い違いなし）。`hookSpecificOutput` を持つのは #7 のみ。#6/#8/#9 は JSON だが `systemMessage` のみ。checkpoint 復元時は `work_context` サマリーがさらに**別の非 JSON plain text 行**として出る（775-780行）。

---

## 2. 既存 merge 契約の確認（根拠）

`restore_state.py:466-484`（`_build_session_proposal_output` の docstring）:

> `**print しない純関数**（#412 [Must]2）— SessionStart hook の stdout は「hookSpecificOutput を含む行が高々1つ」でなければならず（複数行に分かれると片方が黙って捨てられうる）`

契約テストは `hooks/tests/test_restore_state_session_proposals.py:172-208`。ただしそのコメント（183-185行）どおり、**このテストは「hookSpecificOutput 行の一意性」しか見ておらず、systemMessage-only の JSON 行が複数出ることや非JSON平文行の混在は許容されたまま**（§7.1 で強化する）。

**未確認（要検証）**: Claude Code 本体が SessionStart hook の stdout を複数行それぞれ個別 JSON として解釈するのか、stdout 全体を1つの JSON として解釈しようとするのかは一次情報で確認できなかった。CHANGELOG（141行目、#412）の確定事実は「`hookSpecificOutput` を含む行が2行あると片方が黙って捨てられる」という観測結果のみ。**tacchi の実測（2026-08-12 今朝の transcript）では evolve_drain / queue_notice / judge_cap / icebox の4系統が全て記録されており「出力から消えた」観測は無い**（[Nit-t7]）。ただしこれは systemMessage-only の JSON 行が複数ある1サンプルにすぎず、#412 で確認された「`hookSpecificOutput` 行が2つ競合するケース」は今回の観測対象に含まれていない。結論は変えず「未確認」のまま維持するが、実装 PR での実環境確認を引き続き推奨する。

---

## 3. 仕分け表（tier 確定版）

ADR の3分類（① `systemMessage`・1行に集約 / ② `additionalContext`・潰さない / ③ stderr）に9系統＋分岐を当てはめる。**Tier1 = 常にフル文字列で結合対象に含める（絶対に truncate/drop しない）。Tier2 = 複数同時発火時、文字数上限で「...ほかN件」に畳んでよい（畳んでも次回セッションで同じ内容が再現することを§5で個別に確認済みの系統のみ）**。

| # | 系統・分岐 | 分類/Tier | 理由（§5 で副作用タイミングを確認済み） |
|---|---|---|---|
| 1 | pending_trigger | ① Tier1 | 読み取り自体が破壊的（§5.5）。委譲しても消えないよう常時フル表示 |
| 2 | spec_drift | ① Tier2 | marker 保存を defer する two-phase 化により、間引いても次回 reminder として再現することを保証（§5.2） |
| 3 | evolve_drain | ① Tier1 | 柱4（信頼）に直結する「適用済みなのに未記録」情報。`drain_pending` の副作用自体は収集時点で完結し変更しない（§5.1） |
| 4 | data_dir_migration | ① Tier1 | 副作用なし（純読み取り）。データ整合性の警告を埋もれさせない |
| 5 | utterance_staleness | ① Tier1 | 副作用なし。#351「16日沈黙」再発防止が主目的 |
| 6a | evolve_queue_notice（STALE/UNKNOWN health notice） | ① Tier1 | producer 停止シグナル |
| 6b | evolve_queue_notice（FRESH・待ちPJあり） | ① Tier2 | 待ち PJ リストは evolve されるまで残り続ける冪等な状態。間引いても翌日再現（§5.6） |
| 6c | **evolve-queue.json 破損**（新規判定・§5.4） | ① Tier1 | producer が壊れた書き込みをした可能性。#351 と同じ理由でサイレントにしない |
| 7 | session_proposal（systemMessage部分） | ① Tier2 | 副作用なし（`read_reviewed_keys` は read-only）。間引いても既読化されていないので消えない。**[Nit-t6] の Tier2 正当化（「additionalContext が常時フルだから systemMessage の間引きは主機能に影響しない」）は 2026-08-18 に撤回（#503・E8）**: 実際には Claude 側の prompt instruction 遵守は機械的に保証できず、`systemMessage` が digest 化されると利用者から本文が消える事故が実測された。tier=2 の値自体は変えないが、`decision_text`（#503 §3.0）により digest 化そのものを回避する |
| 7' | session_proposal（additionalContext） | ② 常時フル | 変更なし（既存契約） |
| 8 | judge_cap（全分岐: capped/source_failed/skipped_locked/out_of_range） | ① **Tier1（全面）** | daily runner の一回性スナップショット。翌日上書きされると復元不能なため部分 Tier1 化ではなく全分岐を Tier1 化（§5.1） |
| 9a | icebox（STALE/UNKNOWN health notice） | ① Tier1 | producer 停止シグナル |
| 9b | icebox レーン1「成立」 | ① **Tier1（固定・畳まない）** | 個別 issue 名指し。record_seen は既存 lock 内で収集時点に完結、defer しない（§5.3） |
| 9c | icebox フォールバック（#194 件数集約） | ① Tier2 | seen-tracking なし。`oldest_days` は単調増加なので間引いても翌日再現 |
| 9d | **icebox-status.json / icebox-verdicts.json 破損**（新規判定・§5.4） | ① Tier1 | 同上 |

**③ stderr には9系統のどの本文も割り当てない**（頭の裁定どおり）。stderr は各 `except Exception` の実装エラー専用チャネルとして維持する（§4.3）。

**[Nit-t6] session_proposal を Tier2 に置ける理由**: Tier1/Tier2 は① `systemMessage`（人間向け・1行集約）の中での「間引かれうるか」の軸にすぎない。session_proposal の本体（「y/n で確認してください」という Claude への行動指示）は② `hookSpecificOutput.additionalContext` に載っており、この② チャネルは Tier 分類の対象外＝**常にフルで残る**。したがって① 側が Tier2 で間引かれても、Claude が AskUserQuestion を提示するという主機能（柱2の本体）は死なない。① の `systemMessage` はあくまで「人間が UI で見る通知」の可視性を左右するだけ。

**Tier（①内での間引き可否）と digest/full（表示の詳細度）は別の軸**であることに注意（§4.2 で導入）。Tier1 でも複数系統同時発火時は digest 表示になりうる（例外はicebox レーン1のみ）。Tier は「絶対に消えないか」、digest/full は「どこまで詳しく書くか」を決める。

---

## 4. 表示契約

### 4.1 「平常時」の再定義（tacchi [Must-t2] 反映）＋「単一 JSON dict」の対象範囲（codex [Must]1 反映）

**rev1/rev2 の「発火0〜1件が平常時」という前提は誤りだった。** tacchi の実測（2026-08-12 今朝の transcript）: evolve_drain（約95字）+ queue_notice（約100字）+ judge_cap（約55字）+ icebox（約75字）= **4系統同時発火・計約325字**。構造的な理由も特定済み: queue 待ちが1PJでもあれば queue_notice は毎朝発火、llm_judge 滞留10,225件（ADR §2.3-d）で judge_cap も毎朝発火、icebox 58件も毎朝発火（ADR 実測値）。**「2件以上の同時結合」が常用経路であり、「0〜1件」の方が例外。** 本 rev はこの前提で全体を設計し直す（§4.2 の digest 表示モードが主眼）。

**自分でも実 transcript を実測して裏取りした**（`~/.claude/projects/-Users-...-evolve-anything/{3abc3c94,6dd023d5}-*.jsonl` を `json.loads` で正しくパースし、Python `len()` で文字数を確定。以下は tacchi の目算でなく実測値・2026-08-12）:

| 系統 | 実際の文言（抜粋） | 文字数 |
|---|---|---|
| evolve_drain | 「適用済みの evolve 提案が 1 件あります。次回セッションの...」 | **114字** |
| evolve_queue_notice | 「evolve 待ち: figma-to-code, evolve-anything, updater-index, amamo（4 件）...」 | **131字** |
| icebox（フォールバック） | 「icebox 58件・最古31日。`gh issue list --label icebox --state closed`...」 | **95字** |
| judge_cap | 「llm_judge 日次上限に到達（200件処理・残り10311件は翌日以降に持ち越し）。」 | **63字** |
| **4系統合計（`" / "`区切り込み・フル文連結）** | — | **412字** |

tacchi の目算（約325字）より**実測は412字と大きい**（tacchi は概算であり、正確な数値は本 rev で実測により確定した）。この事実自体が tacchi の主張（「400字は平常日でも畳みが発生する値」）を裏付ける — **フル文をそのまま連結する設計だったら、400字上限は今朝の実データだけで既に超過していた**。

同じデータで **digest 化後**（§4.2）を計算すると:

```
[evolve-anything] 記録待ち提案1件（evolve --drain） / evolve待ち4PJ / icebox58件・最古31日 / judge持ち越し10311件（自動）
```
= **96字**（フル文412字の約1/4。rev7 の tacchi 確定文言で再計算— 導線目的の語を含む分、rev5 時点の暫定文言79字よりは長いが、実効上限400字には十分収まる）。**末尾導線**（§4.2'）を付けた場合は `→ /evolve-anything:queue で開始`（18字）が加わり **125字**（自分で `len()` により実測・再検証済み。team-lead 提示の目安「79→97字程度」より実際にはやや長いが、400字予算に対しては依然として無視できる差）。

さらに実測できた他の実サンプル（同じ transcript 群から）: icebox レーン1「成立」のフル文実例「icebox 再開条件が成立しました: #205（subagent_traces.first_try_success_rate = 0.2821 < 0.5 を満たしました）」= **105字**、session_proposal のフル文実例「改善案があります: 「docs-platformで実際にevolveしてみて...」」= **111字**。work_context summary の実例（コミット5件・ブランチ名のみ、未コミットファイル無しのケース）= **480字**（`hooks/save_state.py` の `_MAX_RECENT_COMMITS=5` に到達した5件分の `git log --oneline` を `, ` 連結した結果。未コミットファイルが多いケースはさらに伸びる）。

この実測データを根拠に §4.4 で頭が **上限を400字に確定した**（未確定のまま実装に送らない・P7）。

上位 ADR §8 の完了条件「SessionStart の出力が単一 JSON dict」は**そのまま維持する**（狭めない）。これに合わせ、**checkpoint の `work_context` summary も同じ dict に統合する**（§4.5 で中身も圧縮）。

- 統合先: `hookSpecificOutput.additionalContext`。理由: work_context summary（ブランチ/直近コミット/未コミットファイル）は人間が y/n を判断する対象ではなく、Claude がセッション継続の文脈として読む情報であり、既存の `_format_work_context_summary`（142-160行）の内容自体が「Claude へのセッション復元情報」という性質（`[evolve-anything:restore_state] 作業コンテキスト復元:` という prefix も人間向け UI 文言ではない）。ADR-038 の channel 契約（systemMessage=user可視 / additionalContext=Claude可視）に照らし additionalContext が妥当と判断した。
- 結果: `additionalContext` は「work_context summary（圧縮版・あれば）+ 区切り + session_proposal の AskUserQuestion 指示（あれば）」の連結。
- **stdout は「0行」か「厳密に1行」の二値になる**。checkpoint 有無・通知発火有無の組み合わせ4パターン全てで、最終的に印字するかどうかは「dict が空でないか」だけで決まる（checkpoint 分岐と通知分岐を別コードパスにしない。§6.3）。

### 4.2 表示モード: digest（短縮形）/ full（フル文）の2軸設計（tacchi [Should-t3] 反映）

tacchi 指摘の核心: 各系統の文言は「単独表示」前提のフル文（敬体・`[evolve-anything]` prefix 付き）で書かれており、§4.1 で確定したとおり**2件以上の同時発火が常用経路**であるため、単純連結すると「[evolve-anything] 〜。[evolve-anything] 〜。」が3〜4回続く長い壁になる。

**ルール**（tacchi 代案を採用。頭も digest 方式を支持）:
- **① systemMessage に含める発火系統が合計1件のみ** → その系統の既存フル文をそのまま使う（`[evolve-anything] ...`）。現状の単独発火時とほぼ同じ体験。
- **① systemMessage に含める発火系統が2件以上** → **全件を digest（短縮形）に変換して結合する**。`[evolve-anything]` prefix は最終結合時に**1回だけ**先頭に付与し、各系統の個別 prefix は使わない。
  - **例外1: icebox レーン1「成立」**。既存契約（`icebox_notice.py:197-199`「個別列挙が仕様」）により、named issue のリストは digest 化せずフル文のまま結合する（Tier1 固定・§3 で確定済みと整合）。
  - **例外2: pending_trigger**（rev6・codex round2 [Must-new] 反映）。§5.5 のとおり pending_trigger は破壊的読み取り済みの本文であり、digest（`トリガー提案1件` 等）に置換すると**本文そのものが永久に失われる**（ファイルは既に削除されており、フル文以外に本文を保持する手段が無い）。したがって pending_trigger は**常にフル文のまま結合する**（digest 化しない）。icebox レーン1と同型の「digest 化免除」系統。
  - **例外3: session_proposal（`decision_text` 保持時。2026-08-18・#503 追加）**。§3.0 の優先順位規則: `decision_text` を持つ item は発火件数にかかわらず digest 化されない（詳しさの軸に優先）。pending_trigger・icebox レーン1に続く**digest 免除の3件目**だが、免除理由は異なる: pending_trigger は「本文が物理的に消失する」ため、icebox レーン1は「個別 issue 列挙が既存契約」であるため免除されるのに対し、session_proposal は**利用者に判断を求める内容だから**免除される（[Nit-t6] の Tier2 正当化が破綻したため。§3 #7 参照）。`digest`（`改善案N件`）自体は引き続き計算されるが、digest 集合には加えず、専用フィールド `decision_text` として結合末尾に別枠で連結する（`scripts/lib/session_notify/merge.py:_merge_notification_text`）。
- 区切り文字: `" / "`。
- 例（rev7 確定文言・今朝の実データに近い構成。末尾導線つき）:
  `[evolve-anything] evolve待ち1PJ / 記録待ち提案1件（evolve --drain） / judge持ち越し10311件（自動） / icebox58件・最古31日 → /evolve-anything:queue で開始`

**系統別 digest テンプレート（rev7・tacchi 文言レビュー確定版）**: 全て既存の library 関数が既に計算している値の再利用。新しい調査・API呼び出しは追加しない。**§4.1 で確定したとおり「2件以上の同時発火が常用経路」であるため、フル文が出る「1件のみ発火」はほぼ来ない＝digest が実質唯一の日常表示になる**。この前提に立ち、tacchi レビューにより「対処導線」「警報性」を落とさない文言へ以下のとおり確定した（これ以上の文言変更は行わない）:

| 系統 | full（1件のみ発火時） | digest（2件以上発火時・確定） | tacchi による変更理由 |
|---|---|---|---|
| pending_trigger | 既存文言そのまま | **digest化しない（常にフル文・例外2・不変）** | 破壊的読み取り済みの本文を短縮すると永久消失するため（codex round2 [Must-new]） |
| spec_drift | 既存文言そのまま | `spec-keeper提案{len(surfaced)}件` | 変更なし |
| evolve_drain | 既存文言そのまま | **`記録待ち提案{N}件（evolve --drain）`** | 唯一の対処導線（`evolve --drain` Step 7.8）が digest で消えていた。柱4直結の Tier1 が「叩くもの」まで示すよう変更 |
| data_dir_migration | 既存文言そのまま | **`DATA_DIR分裂（要migrate-data）`** | 実コマンドは `evolve-fleet migrate-data`（`restore_state.py:329-330`）。半端切りをやめ実コマンド名に直結 |
| utterance_staleness | 既存文言そのまま | **`発話取込{N}日停止（要ingest）`** | 「何が止まっているか」（発話取込）を明示 |
| evolve_queue_notice（health） | 既存 health_notice | `evolve-queue更新停止` | 変更なし |
| evolve_queue_notice（FRESH） | 既存文言そのまま | `evolve待ち{count}PJ` | 変更なし |
| evolve-queue.json 破損 | 新規 health notice | `evolve-queue破損` | 変更なし |
| session_proposal（systemMessage部分） | 既存文言そのまま | `改善案{len(groups)}件` | 変更なし |
| judge_cap / capped | 既存文言そのまま | **`judge持ち越し{N}件（自動）`** | capped は行動不要の自動処理 FYI。「残」は宿題に誤読され毎朝ほぼ同数で出続けるため |
| judge_cap / source_failed・skipped_locked | 既存文言そのまま | `judge障害` / `judgeスキップ` | 変更なし |
| judge_cap / out_of_range | 既存文言そのまま | **`judge異常応答{N}件（要確認）`** | フル文は「モデル応答の質を確認してください」＝モデル劣化の警報。「範囲外」だと capped と混同され読み流されるため |
| icebox（health） | 既存 health_notice | `icebox更新停止` | 変更なし |
| icebox レーン1成立 | **常にフル**（例外1・畳まない・既存文言そのまま） | **混在時のみ `icebox成立: {body}` に短縮**（`body` = `#205（reason）` 形式の issue 列挙・**reason 文字列は一切パースしない**） | 体言止め10〜25字の列に敬体105字がそのまま挟まるのは文体として不自然。既存契約の本体は「issue 名指し + reason 列挙」であって敬体プレフィックス（「icebox 再開条件が成立しました:」）ではないため、混在時はプレフィックスだけ短縮する。§4.4 の可変長リスクの主要因（実測105字）の緩和も兼ねる |
| icebox フォールバック | 既存文言そのまま | `icebox{count}件・最古{oldest_days}日` | 変更なし |
| icebox-status/verdicts 破損 | 新規 health notice | `icebox破損` | 変更なし |

**行頭 prefix（tacchi 確定・rev5 から変更）**: `[evolve-anything] 今朝: ...` ではなく **`[evolve-anything] ...`**（`今朝:` は削除）。理由: evolve_drain・data_dir_migration は daily runner 由来でなくその場の live 検出であり、「今朝」という語が誤った示唆になる。系統間の区切りは `" / "` で足りる。

**icebox レーン1の `body` 抽出について**（実装メモ）: `daily/icebox_notice.py` の `build_met_notice()` は現状 `body`（issue 列挙部分）と `msg`（`[evolve-anything] icebox 再開条件が成立しました: {body}` という敬体プレフィックス付き全文）を一体で構築している。混在時の短縮形 `icebox成立: {body}` を作るには `body` 単独を取得できる必要があるため、`build_met_notice()` 内部の body 構築ロジックを `build_met_body(verdicts) -> str` として切り出し、`build_met_notice()` はそれを呼ぶ形にリファクタする（**`daily/icebox_notice.py` への小さな変更が1件追加される**。§6.2 の「icebox_notice.py は無改修」という記述をこの1点だけ訂正する）。振る舞い（`build_met_notice()` の戻り値）は不変。

digest 文字列は概ね10〜30字に収まる（既存フル文の1/3〜1/5）。これにより「2件以上発火が常用」でも合計長が大幅に縮む（§4.4）。**digest 化免除の pending_trigger は動的長かつ完全不変**。icebox レーン1は**混在時に短縮フレームを使うが body（issue列挙+reason）はパースせず不変**という中間形態で、こちらも動的長。この2系統が2件以上発火時の合計長に与える影響は§4.4で別枠評価する。

### 4.2' digest 行の末尾導線（tacchi [Should]1・rev7 で新設）

digest 行（2件以上発火時）の末尾に **` → /evolve-anything:queue で開始`** を付ける。

**理由**: 実測79字の digest 行は状態の羅列（`evolve待ち1PJ / 記録待ち提案1件（evolve --drain） / ...`）で終わり、「で、私は何をすればいいのか」に直接答えない。目標体験（ADR-054 冒頭）は「朝: 1行 → y/n だけ」であり、状態を見せるだけで次の行動への導線が無いと y/n に繋がらない。

**付与条件（無条件に付けない）**: 表示対象の系統に **evolve_queue_notice（FRESH）・evolve_drain・icebox（レーン1 or フォールバック）のいずれかが含まれる場合にのみ**付ける。理由: `/evolve-anything:queue` は「evolve 待ち一覧を表示し上から対話 evolve へ誘導する」全PJ横断の入口コマンドであり、上記3系統はいずれも evolve ワークフロー（および icebox 棚卸し）に直接つながる内容のため、単一の入口コマンドへ集約する UX 上の単純化として妥当と判断した。data_dir_migration（`evolve-fleet migrate-data`）・utterance_staleness（`evolve-fleet ingest`）・spec_drift（`/evolve-anything:spec-keeper`）は queue コマンドの対象外の別コマンドを要するため、これら**のみ**が発火している場合は付けない（誤誘導になるため）。judge_cap は自動処理の FYI（capped）か障害通知であり、能動的な次アクションが `/evolve-anything:queue` と直接結びつかないため対象外とする。

**予算への影響**: suffix は固定長・約18字（`" → /evolve-anything:queue で開始"`）であり、§4.4 の Tier2 予算400字には含めない（「ほか:系統名」suffix と同じ扱い）。実測79字の digest 行に付けた場合 97字程度になる（tacchi 試算どおり、予算に対し無視できる）。

**用語の統一（rev6・codex round2 [Should-new] 反映）**: 「Tier」と「digest/full」は独立した2軸であり、**「Tier1」という言葉だけでは『フル文で表示される』ことを意味しない**。rev5 まで「Tier1 は全件フルで結合」という表現を使っていた箇所があり、これは「digest化されても結合対象から絶対に外れない（量の軸）」という意味で書いたつもりだったが、「フル文のまま表示される（詳しさの軸）」と誤読されうる表現だった。以後は用語を以下のように統一する:
- **「Tier1」「Tier2」**: ①内で**絶対に落とさない**か／**予算超過時に落としてよい**か（量の軸）。
- **「フル文」「digest」**: 1件のみ発火時のフル文か、2件以上発火時の短縮形か（詳しさの軸。表示モードは発火件数だけで決まり、Tier とは独立）。
- **「全量結合する／取りこぼさない」**: Tier1 の性質を指すときはこの表現を使い、「フルで結合する」という表現（フル文と誤読されうる）は使わない。

したがって正しい言明は: 「Tier1 は2件以上発火時に digest 化されても、結合対象からは絶対に外れない（量として保証）。**汎用の digest テンプレート（短縮名詞句への置換）が適用されない例外は pending_trigger と icebox レーン1の2系統のみ**（Tier とは別の理由による＝pending_trigger は digest化すると本文消失・icebox レーン1は個別列挙契約のため）。ただし『適用されない』の中身は2系統で異なる: **pending_trigger は本文が完全不変**（1件のみ発火時と2件以上発火時で文字列が同一）。**icebox レーン1は混在時（2件以上発火時）にプレフィックスだけ短縮**され（`icebox成立: {body}`）、issue番号・reason を含む `body` 自体は一切変更しない（§4.2 rev7 確定）」。

### 4.3 「1行」の適用範囲（codex [Should]1 反映）

**「1行」契約は stdout にのみ適用する。** stderr は対象外とし、既存どおり各 `_build_*` の内部例外ごとに複数行出てよい（fail-safe の実装詳細であり、ユーザー向け「1行」保証の対象ではない）。

- 正常系: stdout 0〜1行。
- 一部の builder が内部例外を出した場合: **stdout は残りの builder が集めた内容で引き続き0〜1行**（例外を出した builder の分だけ内容が欠けるが、他の builder の内容は失われない。§5.5 の per-builder try/except 隔離が前提）。stderr にはその builder のエラーメッセージが独立して出る（既存どおり）。
- **stdout 1行 + stderr N行** は正式な契約として許容する組み合わせ。「1行」はあくまで stdout の話であり、stderr の行数を制限する契約ではない。

### 4.4 Tier2 の畳み方（codex [Should]3 一部反映・tacchi [Must-t2] で重心を移動）

§4.2 の digest 化により「2件以上発火 = 即座に長大化」という懸念自体は大きく緩和される（digest 1件あたり10〜25字・9系統全発火でも概算200〜225字程度）。ただし動的長の系統（icebox レーン1の named issue 列挙、multiple な judge の詳細等）が重なるケースは依然として長くなりうるため、上限は残す。

- 測定単位: Python の文字数（`len(str)`）。既存の `MAX_SNAPSHOT_CHARS` 上限判定（`restore_state.py:117`）と一貫させ、バイト数は使わない。
- 結合順序: **Tier1（digest化されたものも含む）を全量・無条件に先に結合**（上限なし・絶対に truncate しない）→ 残り予算があれば Tier2 を発火順に追加 → 予算超過分は追加せず**「（ほか: <系統名を`/`区切りで列挙>）」**を末尾に付す。
  - tacchi 指摘（[Should-t3]）を採用: 単なる件数「ほかN件」でなく**系統名を出す**（`ほか: queue/judge` のように）。件数だけだと「毎朝同じほかN件」が続き実質沈黙と同じになるため、どの系統が省かれたか分かる形にする。系統名は内部ラベル（pending_trigger→`trigger`, spec_drift→`spec`, evolve_drain→`drain`, data_dir_migration→`datadir`, utterance_staleness→`utterance`, evolve_queue_notice→`queue`, session_proposal→`proposal`, judge_cap→`judge`, icebox→`icebox`）を使う。
  - この suffix 自体は結合予算に含めない（固定長・短文のため）。
- **具体的な上限値: systemMessage 全体で `400` 字に確定する**（rev5・頭裁定）。測定単位は Python の `len()`（コードポイント数）で UTF-8 byte 数ではない — 理由: ターミナル表示幅に近いのは文字数側であり、日本語主体の文言は byte 数だと（1文字3byte換算で）実際の見た目より過小評価されるため。

  **確定ルール**（頭裁定を反映）:
  1. **Tier1 は上限に関係なく必ず全量載せる**。400字はTier1を落とす根拠にしない。Tier1 だけの合計が400字を超えたら、そのまま超過を許容する（情報消失より横長を選ぶ）。
  2. **Tier2 は残り予算（400字 − Tier1合計）に入る分だけ digest を載せる**。あふれた分は追加しない。
  3. **超過分は「（ほか: 系統名を`/`区切りで列挙）」で表示する**（例: `ほか: queue/judge`）。件数のみの「ほかN件」は禁止（tacchi 指摘のとおり、中身への導線が無く実質沈黙と同じになるため）。
  4. **切り詰めは digest 単位で行い、文字列の途中では切らない**。ある digest 全体を「載せる/載せない」の二値で判定し、`...` 等での途中切断はしない（途中で切られた系統名は意味を成さないため）。
  5. 区切り文字は `" / "`（rev2 で確定済み・踏襲）。suffix「（ほか: 系統名）」自体は予算に含めない（固定長・短文のため）。

  **実効上限契約（rev6・codex round2 [Should]3 反映 — 「400字は最終出力の保証か、Tier2 予算配分ルールか」を明示する）**:

  (a) **最終文字列全体に対する強制上限は無い。** 400字は**Tier2 の予算配分ルールの入力**であって、systemMessage 最終文字列の長さそのものを保証する仕組みではない。ルール1（Tier1 は無条件で全量）により、**Tier1 の合計だけで400字を超えるケースは起こりうる**し、その場合でも情報は落とさない（意図的なトレードオフ・頭裁定）。

  (b) したがって「9系統全発火でも200〜225字に収まる」という見積もりは、**Tier2 に分類される系統（digest 化される・上限で畳まれうる部分）についてのみ有効**であり、**digest 化免除の2系統（pending_trigger・icebox レーン1）を含めた最終文字列全体の上限ではない**。

  (c) **可変長要素の扱い**:
  - **pending_trigger のフル文**: 元の trigger メッセージ長に依存（実測114字程度が典型）。上限なし（Tier1・digest免除のため）。
  - **icebox レーン1「成立」のフル文**: `MAX_MET_ISSUES=10`（`icebox_notice.py:37`）で named issue の**件数**は既に上限があるが、各 issue の `reason` 文字列自体は**現状無制限**（`icebox_notice.py:204-205` の `f"#{v.get('number')}（{v.get('reason', '')}）"`）。したがって「icebox レーン1 + 他8系統」の最悪ケースは reason の実際の長さ次第で数百字にも達しうる（rev5 で書いた「実測105字を根拠にした最悪ケース約273字」という見積もりは、reason が長い verdict では成立しない。**この見積もりは撤回する**）。
  - この可変長リスクへの対応は (a) のとおり「そのまま許容する」を正式な設計判断とする。reason 文字列を切り詰める追加ガード（例: `MAX_REASON_CHARS` の新設）は、digest 文言確定の議論と切り離した**将来の改善候補**として §10 に残し、Phase 0 では導入しない（頭裁定の rule1「情報消失より横長を選ぶ」の方針と、新設を増やさない P3 の両方に沿うため）。

  (d) **`decision_text` は予算外・overflow 対象外（2026-08-18・#503 §3.0-2 追加）**: `NotificationItem.decision_text` が非 None の item は、Tier2 予算計算（`TIER2_BUDGET_CHARS=400`）に一切含めない。Tier1 合計が単独で400字を超えていても、`decision_text` はその超過とは無関係に必ず全文が結合される（「（ほか: 系統名）」による overflow 畳みの対象にもならない）。**tier2 の定義をこれに合わせて改める**: 「予算超過時に落としてよい」のは `decision_text` を持たない item に限る（`decision_text` を持つ item は tier の値（現状 session_proposal は tier=2 のまま）に関係なく常に全文結合される）。

  **根拠となる実測**（§4.1 参照。あくまで「典型的な Tier2 部分の目安」であり (b) の限定つき）:
  - 実測した今朝の4系統同時発火（最も典型的な「常用パス」、pending_trigger/iceboxレーン1は不発火）は、digest 化後で **79字**。400字の閾値は通常運用で**まず発動しない**（約5倍の余裕）。
  - **仮に digest 対象の8系統（pending_trigger・iceboxレーン1混在時短縮形を除く。spec_drift/evolve_drain/data_dir_migration/utterance_staleness/evolve_queue_notice/session_proposal/judge_cap/iceboxフォールバックの各1分岐）が全て同時発火**した場合を、rev7 確定文言で実際に `len()` で計算すると **171字**（末尾導線つきで200字）。旧見積もり「約100〜170字」の上限付近だが、rev7 の文言確定（対処導線・警報性を残すため一部を意図的に長くした）を踏まえても400字には十分収まる。
  - **digest を採らず旧設計（フル文そのまま連結）のままだった場合、実測412字となり400字を明確に超過していた**（tacchi の目算は約325字だったが、自分で実 transcript を `json.loads` で正しくパースして実測したところ412字だった）。**digest 化はこの400字という値を実用的な安全弁として機能させるために必須の設計変更だった**（digest が無ければ400字はそもそも成立しない上限値だった）。
  - この400字はあくまで運用開始時の初期値であり、実装 PR で実運用ログを1〜2週間収集し乖離があれば調整してよい（provisional-over-blocker: 値は確定済みなので着手をブロックしない）。

### 4.5 work_context summary の圧縮（tacchi [Should-t4] 反映・頭の推奨により Phase 0 に含める）

tacchi の実測: 今朝の `作業コンテキスト復元:` ブロックが400字超の複数行 plain text。原因を `hooks/save_state.py` まで遡って確認した:

- `_MAX_RECENT_COMMITS = 5`（`save_state.py:20`）: `git log --oneline -5` で既に5件に制限されているが、`_format_work_context_summary`（`restore_state.py:150-152`）は**5件全部を `, ` 区切りでそのまま連結**する。
- `_MAX_UNCOMMITTED_FILES = 30`（`save_state.py:19`）: 最大30件のファイルパスが**全部 `, ` 区切りでそのまま連結**される（`restore_state.py:154-156`）。長い diff 中は容易に数百字になる。

**保存側（`save_state.py` の 5件/30件キャップ）は変更しない**（他の用途 `post_compact.py` 等が全件を必要とする可能性があるため。ここは checkpoint への保存であり Phase 0 の対象外）。**変更するのは SessionStart 表示専用の `_format_work_context_summary` の圧縮ロジックのみ**:

- 直近コミット: 件数 + 先頭1件（`git log --oneline` の1行、50字を超える場合は末尾を `...` で truncate）。例: `直近コミット5件（先頭: a1b2c3d fix: バリデーション...）`
- 未コミットファイル: 3件以下ならそのまま列挙、4件以上なら件数のみ。例: `未コミット12件` / `未コミット2件（a.py, b.py）`

この圧縮は additionalContext 内の記述であり、Claude はセッション中いつでも `git log`/`git status` を実行して詳細を取得できるため、情報の完全性は損なわれない（要約であって隠蔽ではない）。

### 4.6 producer 不存在 vs 破損（codex [Must]13 反映）

`read_queue()` / `read_icebox_status()` / `read_icebox_verdicts()` は現状「ファイル不在」と「ファイル存在するが JSON 破損 / OSError」を同じ `None` に潰している（`queue_notice.py:29-37`, `icebox_notice.py:58-66`, `icebox_notice.py:136-144`）。これを明示的に分離する:

- **ファイル不在 → 沈黙**（daily runner が一度も走っていない＝未セットアップ。既存どおり）。
- **ファイル存在するが読めない（破損）→ Tier1 health notice**（daily runner が壊れた書き込みをした、または破損した。#351 と同じ「silence != evaluated」を適用）。

実装は `hooks/restore_state.py` 側に軽量な分類ヘルパーを追加し、`path.exists()` を `read_*()` 呼び出しの直前に見て判定する（対象ライブラリ関数のシグネチャは変更しない。§6.2）。**適用範囲は evolve-queue.json / icebox-status.json / icebox-verdicts.json の3ファイルに限定する**（daily runner が書く一回性スナップショットという共通の性質を持つファイル群）。`pending-trigger.json` の破損は `read_and_delete_pending_trigger()` 内で既に「破損なら黙って削除」という別の既存挙動を持ち（`trigger_engine/pending.py:46-50`）、spec_trigger の marker 破損も `load_marker` が `{}` へ静かにフォールバックする（`spec_trigger.py:223-230`）。**これら2件は Phase 0 のスコープ外とする**（明示的な決定。P5「対策を2つ積む前に1つ目の効果を測る」に従い、evolve-queue.json 系の3ファイルより発生頻度・影響度が低いと判断したため。§10 に残す）。

### 4.7 異常時に何が起きたら何行になるか

| 事象 | Phase 0 後 |
|---|---|
| 平常時（発火0〜1件） | stdout 0〜1行 |
| 複数系統が同時発火 | stdout **1行**。Tier1 は全てフル表示、Tier2 は畳まれても件数が見える |
| utterance staleness 16日沈黙 | Tier1 として常にフル表示。他系統がどれだけ同時発火しても埋もれない |
| pending_trigger 発火 かつ 他系統多数同時発火 | Tier1 固定のため常にフル表示 |
| judge_cap の capped/out_of_range | 全分岐 Tier1 のため常にフル表示（翌日上書きされる一回性データのため） |
| icebox レーン1「成立」 | Tier1 固定・畳まない・既存 lock 内 record_seen は変更なし |
| evolve-queue.json / icebox-status.json / icebox-verdicts.json が破損 | Tier1 health notice を新規発火（旧: 沈黙） |
| marker 書込失敗等の内部例外 | **現状維持（是正しない）**。stderr に例外が出るのみ。stdout 側は他 builder の内容で1行以内を維持（§4.3）。可視化強化は Phase 0 のスコープ外（頭裁定#2） |
| session_proposal の additionalContext | 変更なし。work_context summary と連結されるが内容は truncate しない |

---

## 5. 副作用のコミット境界（builder / commit 分離、codex [Must]2/3/4/6・[Should]2・[Nit] ＋ tacchi [Must-t1]・[Should-t5] 反映）

**系統別「build段階で許容する副作用」一覧**（tacchi [Should-t5]・rev6 で codex round2 [Must]4/7 を受けて pending_trigger と icebox lane1 の「defer するか」を更新）:

| 系統 | build時点の副作用 | defer するか |
|---|---|---|
| pending_trigger | あり（ファイル削除・§5.5） | **する**（rev6・ack 方式。lock + print成功後 commit・§5.5） |
| spec_drift | あり（marker保存） | **する**（`persist=False` + `commit`・§5.2） |
| evolve_drain | あり（`drain_pending()` の orphan除去） | しない（Tier1固定・現状のまま・§5.1） |
| data_dir_migration | なし（純読み取り） | 対象外 |
| utterance_staleness | なし（純読み取り） | 対象外 |
| evolve_queue_notice | なし（純読み取り） | 対象外 |
| session_proposal | なし（`read_reviewed_keys` は read-only） | 対象外 |
| judge_cap | なし（純読み取り） | 対象外 |
| icebox（レーン1成立） | あり（`record_seen`） | **する**（rev6・ack 方式。lock + print成功後 commit・§5.3） |
| icebox（フォールバック） | なし（純読み取り） | 対象外 |

rev5 までは pending_trigger と icebox レーン1 を「defer しない（副作用は収集時点で確定・現状のまま）」としていたが、codex round2 [Must]4/[Must]7 の指摘により、この2系統は**最終 print の成否を待たずに副作用が確定してしまう**（icebox は print 前に `record_seen`、pending_trigger は builder 呼び出し時点でファイル削除）という問題が残っていた。rev6 は両系統を **ack 方式**（§5.3/§5.5 参照）に変更し、defer する側に倒す。evolve_drain だけは元々「情報が消えても next session で再現する」性質（`undrained_applied` が毎回ライブ再評価のため）があるため defer 不要のまま据え置く。

rev1 は「pending_trigger だけが読んだら消える特別な系統」と書いたが、これは言い過ぎだった（codex [Nit]）。正しくは、**9系統それぞれが「表示の可否を待たずに副作用が先に確定するか」を個別に持ち、その確定タイミングが Tier 判定と実装方針を決める**。

### 5.1 「収集時点で副作用が完結し、変更しない」系統（最も単純）

evolve_drain・judge_cap（読み取りのみ・書き込みなし）・data_dir_migration・utterance_staleness・evolve_queue_notice・icebox フォールバック（#194）。

これらは以下のいずれか:
- 副作用が無い（純読み取り: data_dir_migration, utterance_staleness, evolve_queue_notice, judge_cap, icebox フォールバック）
- 副作用があるが今日から Tier1 に固定するため「先に確定しても問題ない」（evolve_drain の `drain_pending()` 呼び出しによる orphan 除去。Tier1＝常に表示されるので、副作用を defer する必要そのものが無い）

→ **実装方針**: 現行の副作用実行タイミングを一切変えない。`print()` の呼び出しだけを「文字列を返す」に変える（純粋な出力フォーマットの変更）。

judge_cap を全分岐 Tier1 にした理由（codex [Must]3 の核心）: `capped`/`out_of_range_verdicts` は `evolve-queue.json["llm_judge"]` フィールドの値であり、**次回 daily runner 実行（次の朝）で上書きされる一回性のスナップショット**。spec_drift のような reminder/cooldown 機構が無いため、今日の Tier2 予算で間引かれると「今日 llm_judge が上限に達した/失敗した」事実は明日には消えている。部分的に Tier1（`source_failed`/`skipped_locked` のみ）にする案は、`capped`（供給が実際に止まっている最も緊急度が高い状態）を Tier2 に残す矛盾があったため、全分岐 Tier1 に統一した。

### 5.2 「defer が必要」な系統: spec_drift（唯一の two-phase 化対象。codex [Must]2 = tacchi [Must-t1]、独立検出）

tacchi は独立に同じ箇所を指摘し、加えて `MAX_REMINDERS = 1`（`spec_trigger.py:113`「初回 + リマインド1回 = 最大2回までで打ち止め」）という具体的な閾値を示した: **表示していないのに reminders カウントが消費されるため、2回畳まれたら一度も読まれずに `pending` から drop される**。しかも spec_drift を後から見る CLI 導線が無く、SessionStart 通知が唯一の出口。

`spec_trigger.detect(cwd, persist=True)`（現状のデフォルト引数）は、呼び出し時点で:
1. `marker["last_sha"] = head` を計算（338-354行の `pending` 再構成を経て）
2. `save_marker(slug, marker)` を無条件に呼ぶ（356-357行）

これは「今回 surface した commit を reminder リストへ積む・cooldown を更新する・`MAX_REMINDERS` 到達分は削除する」処理を**表示の成否と無関係に確定**させる。したがって rev1 の「Tier2 で安全（marker が保持するため）」は不正確だった: **marker への保存自体が表示前に起きるため、Tier2 の truncate によって「一度も表示されないまま `MAX_REMINDERS` 到達で削除される」経路が実在する**（codex [Must]2）。

**解決策**: `detect()` を `persist=False` で呼び、計算済みの `marker` dict を呼び出し側が保持する。実際に最終出力へ含められた場合だけ `save_marker(slug, marker)` を呼ぶ。

- `persist=False` でも `new_commits`/`surfaced`/`pending` の計算自体は現状と同じロジックで行われる（338-352行の分岐は `persist` を見ていない）。**変わるのは「計算結果をディスクに書くかどうか」だけ**。
- 表示されなかった場合、`last_sha` がディスク上で更新されないため、**次回 `detect()` 呼び出しは同じコミット範囲を再度 `new_commits` として検出する**（決定論的に同じ `surfaced` を再生成する。副作用が完全に冪等）。
- 必要な変更（`spec_trigger.py` 側）: `detect()` の返り値に `marker`（計算済みだが未保存の dict）を追加する。現状の呼び出し元は本モジュール内で1箇所のみ（`restore_state.py:188`、grep で確認済み）のため後方互換上のリスクは低い。追加キーは既存の `{"message", "fires", "reminders"}` に `"marker"` を足すだけで、既存フィールドの意味は変えない。
- `restore_state.py` 側: `_build_spec_drift_output()` は `detect(cwd=cwd, persist=False)` を呼び、`message` があれば Tier2 として登録し、`commit` コールバックに `lambda: _spec_trigger.save_marker(slug, result["marker"])` を紐付ける。

**`MAX_REMINDERS` 消耗が起きないことの根拠**: `persist=False` の下では、表示に至らなかった呼び出しの `reminders`/`cooldown_until` 更新はディスクに一切反映されない。次回 `detect()` 呼び出しは常にディスク上の直近保存済み状態（表示に成功した最後の状態）から再計算するため、**「表示されないまま reminders が積み上がって `MAX_REMINDERS` に達し drop される」という経路は、一度も表示に成功していない限り原理的に発生しない**。exhaustion が起きうるのは「実際に2回表示された後」のみであり、それは仕様どおりの「nag しない」動作（`spec_trigger.py:349` のコメントどおり）。

初回検出（`last_sha` が無い最初の呼び出し、308-312行）は元々「marker をセットするだけで過去分を flood しない」という副作用だけの分岐で `message` を返さない。この分岐は `persist=False` でも `marker` を計算するだけで保存しないため、**初回セットアップだけ「次回セッションでも初回扱いが繰り返される」**（=永久に spec_drift が発火しない）というリスクがある。これを避けるため、**この「初回セットアップ」分岐だけは例外的に無条件で `save_marker` する**（表示すべき `message` が無い＝ユーザー体験に影響しない副作用のため、defer する理由が無い）。

### 5.3 「lock を保持したまま ack する」系統: icebox レーン1「成立」（rev6・codex round2 [Must]4 反映で全面改訂）

**rev5 の設計は誤りだった**。codex round2 [Must]4 の指摘: rev5 は「lock 内で record_seen まで完結させ、defer するのは印字文字列だけ」としていたが、これは**print 前に record_seen が実行される**ことを意味し、以下の失敗経路で verdict が「表示されないまま既読化される」:
- icebox の builder 自体は成功するが、後続の他系統の builder が例外を出し、そこから先の merge/print に到達できない場合
- merge 関数（`_merge_notification_text`）自体が例外を出す場合
- 最終 `print`/`json.dumps` が失敗する場合

いずれも「record_seen は済んでいるが verdict は一度も画面に出ていない」状態になり、レーン1「成立」の verdict は**再提示されない**（`unseen_met_verdicts` が既読を除外するため）。§5.5 の pending_trigger と全く同じ「commit が print の成否を待たずに確定してしまう」問題であり、rev5 はこれを pending_trigger にしか適用していなかった。

**ack 方式への変更**: 「read（seen_keys）→ decide（未既読判定）→ **print 成功後に** record_seen」の順に変える。ただし既存の**並行 SessionStart 二重通知防止契約**（`hooks/tests/test_restore_state_icebox_notice.py:329-350`）を壊さないことが必須条件（codex round2 が明示的に要求）。

**二重通知が起きない根拠**: lock を decide の直後に解放せず、**print 成功後の commit（record_seen）が終わるまで保持し続ける**。実装は `contextlib.ExitStack` を使う（§6.2）:

1. icebox の収集関数が呼ばれた時点で `stack.enter_context(_file_lock(lock_path))` により lock を取得する（`with` ブロックでなく `ExitStack` に登録するため、この関数を抜けても lock は解放されない）。
2. lock 保持下で `seen_keys` を読み、`shown`（今回表示すべき未読 verdict）を決定する。
3. `text = build_met_notice(shown, ...)`（既存の敬体フル文）、`digest = f"icebox成立: {build_met_body(shown)}"`（tacchi 確定・§4.2/§6.1 rev7）を計算し、`NotificationItem(tier=1, text=text, digest=digest, commit=lambda: record_seen(shown, path=seen_path))` を返す（**この時点では record_seen を呼ばない**）。
4. 他8系統の収集・merge・print が完了する（この間 lock は保持されたまま。他の SessionStart プロセスが同じ lock を取ろうとしても `file_lock` は blocking のため待たされる — 二重通知の原因になる「両方が seen_keys を未読と読む」ケースが構造的に起きない）。
5. print 成功後、`item.commit()` を呼び `record_seen` を実行する。
6. `handle_session_start` 全体を包む `with ExitStack() as stack:` を抜けた時点で lock が解放される（成功時は commit 済みの状態で解放、失敗時は commit されないまま解放 — どちらの経路でも確実に解放される。§5.5 参照）。

この設計により、**別プロセスの SessionStart は本プロセスが lock を解放するまで decide フェーズにすら入れない**ため、rev5 までの実装と同じ強さの排他性を保ったまま、commit を print 成功後まで遅らせることができる。lock 保持時間は「他8系統の収集＋merge＋print」の間だけ伸びるが、各収集関数は既存の設計指針（`pitfall_hot_hook_eager_import`）により軽量読み取りのみのため、実質ミリ秒オーダーで許容範囲内と判断する。

**既存テストへの影響**: `test_deliver_serializes_via_file_lock`（330-367行）は「lock 保持中は他プロセスが進めない」ことを検証しているため、lock の**取得〜解放の境界が広がっても検証内容自体は変わらない**（依然として lock 保持中は他プロセスをブロックする）。ただし呼び出し対象が `_deliver_icebox_notice()` から新しい収集関数＋`ExitStack` 経由の commit 呼び出しへ変わるため、**テストコード自体は書き換えが必要**（§7.2）。

### 5.4 producer 破損の扱い（§4.6 参照）

evolve-queue.json / icebox-status.json / icebox-verdicts.json の3ファイルについて、`path.exists()` を `read_*()` 呼び出し直前に判定し、「存在するが読めない」を Tier1 health notice に昇格させる。副作用は無い（純読み取りの分類のみ）ので defer の問題は発生しない。

### 5.5 pending_trigger を ack 方式へ（rev6・codex round2 [Must]7 反映で全面改訂）

`trigger_engine/pending.py:35-50` の `read_and_delete_pending_trigger()` は呼んだ瞬間に無条件でファイルを削除する。§5.1〜5.4 で見たとおり、この「呼んだら即座に副作用が確定する」性質自体は evolve_drain や icebox レーン1とも共通する。pending_trigger が他と違うのは**削除が完全に取り消し不能**（marker のように「次回また同じ内容が計算される」冪等性が無い）という一点であり、この意味で「最も直接的な例」（codex round1 [Nit] の言うとおり "唯一" は言い過ぎ）。

**rev5（Tier1 固定・副作用は収集時点で確定・変更しない）は不十分だった**。codex round2 [Must]7 の指摘: rev5 は「収集時点で `read_and_delete_pending_trigger()` を呼び、ファイルは即削除。あとは print が失敗しても仕方ない」という設計であり、**print 失敗時に本文が永久に失われることを設計自身が認めていた**（rev5 §6.3 の「pending_trigger 等は print 失敗時でも既にファイルが消えている」という記述がその告白）。これは §5.3 の icebox と全く同型の欠陥であり、同じ ack 方式で解決する。

**新しい primitive**（`scripts/lib/trigger_engine/pending.py` に追加。既存の `read_and_delete_pending_trigger()` は後方互換のため削除しない）:

```python
def peek_pending_trigger() -> dict | None:
    """pending-trigger.json を読むが削除しない。スヌーズ中/不在/破損は None。"""
    if not PENDING_TRIGGER_FILE.exists():
        return None
    if _is_snoozed():
        return None
    try:
        return json.loads(PENDING_TRIGGER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def delete_pending_trigger() -> None:
    """pending-trigger.json を削除する（既に無ければ no-op）。"""
    try:
        PENDING_TRIGGER_FILE.unlink()
    except OSError:
        pass
```

**lock を新設する**: `pending-trigger.json.lock`（sidecar・`rl_common.file_lock` を使用。既存の `icebox_verdict_seen.jsonl.lock` と同型パターン）。pending_trigger には従来 lock が無かったが（`peek`→`commit` の分離により決定・commit 間に「別プロセスも同じ内容を未消費と見なす」窓が開くため、icebox と同じ理由でロックが必要になった。§8 で凍結非抵触を確認する）。

**ack 手順**（icebox §5.3 と同型）:
1. 収集関数が `stack.enter_context(_file_lock(pending_trigger_lock_path))` で lock を取得（`ExitStack` 登録・関数を抜けても解放しない）。
2. lock 保持下で `peek_pending_trigger()` を呼ぶ（削除しない）。`message` が空/無ければ `None` を返す（lock は `ExitStack` が最終的に解放する）。
3. `NotificationItem(tier=1, text=full_message, digest=full_message, commit=delete_pending_trigger)` を返す（`digest` に `text` と同一の値を入れることで §4.2 例外2＝完全不変を表現する。§6.1 rev7 の再設計）。**この時点では削除しない**。
4. 他系統の収集・merge・print が完了する（lock 保持中は他プロセスの pending_trigger 収集がブロックされる＝二重表示防止）。
5. print 成功後、`item.commit()`（= `delete_pending_trigger()`）を呼ぶ。
6. `with ExitStack()` を抜けて lock を解放する。

**3つの失敗経路を明示的に担保する**（codex round2 [Must]7 の直接的な要求）:

| 失敗経路 | 発生タイミング | 結果 |
|---|---|---|
| ①収集関数自身の例外（例: ファイル読み取り時の予期しない例外） | pending_trigger の builder 内 | 個別 try/except で捕捉・stderr に記録。ファイルは削除されていない（`peek` は非破壊）ので次回セッションで再度候補になる |
| ②他系統の builder 例外／merge 関数の例外 | pending_trigger の収集成功後、全体 merge 前後 | pending_trigger の `commit` は呼ばれない（commit は print 成功後のみ実行）。ファイルは削除されていないので次回再候補 |
| ③最終 `print`/`json.dumps` の失敗 | 全収集・merge 成功後 | 同上。`commit` は呼ばれず、ファイルは残る |

いずれの経路でも **pending_trigger のファイルは削除されない**ため、「次回セッションで再提示される」ことが保証される（destructive でなくなったことで、rev5 が抱えていた「print 失敗時は本文が永久に失われる」という自己申告済みの欠陥が解消される）。

**新規 E2E テスト**（§7.2）で以下を保証する:
- 正常経路: `pending-trigger.json` を書く → `handle_session_start` → 最終 stdout の1行に必ずそのメッセージが含まれる → ファイルが削除されている。
- 失敗経路①〜③（上表）: それぞれ該当箇所を monkeypatch で例外送出させ、`handle_session_start` 完了後も `pending-trigger.json` が**削除されていない**ことを確認する。現行の `test_e2e_trigger_flow.py:51-62` は `read_and_delete_pending_trigger()` を直接呼ぶだけで `restore_state` の集約・出力を経由しておらず、この保証を持っていない（codex round1 指摘のとおり。round2 でさらに失敗3経路の担保が要求された）。

### 5.6 evolve_queue_notice（FRESH・待ちPJ）が Tier2 で安全な理由

`queue = queue_data.get("queue")`（待ち PJ の一覧）はイベントログでなく「現在の状態」。今日の SessionStart で間引かれても、対象 PJ が実際に evolve されるまで明日以降の `evolve-queue.json` にも同じ PJ が residual として残り続ける（daily runner はスナップショット全体を再生成するが、待ち条件が解消されない限り同じ PJ を再度含む）。judge_cap（§5.1）とは異なり「イベントの記録」ではなく「未解消の状態」であるため、間引いても情報は失われない。

---

## 6. 実装方針

### 6.1 変更の形（rev6・codex round2 [Should]4/[Must]4/7 反映、rev7・tacchi digest 文言確定で `digest` フィールドへ再設計）

各系統の収集関数（「pure」と呼ばない。§5 冒頭参照）は、以下の形の値を返す（print しない）:

```python
NotificationItem = {
    "tier": 1 | 2,          # 分岐ごとに明示的に決める（§3 の表を収集関数内の分岐にそのまま対応させる。
                             # freshness の文字列一致など間接的な判定に頼らない — codex [Should]4）
    "text": str,             # 1件のみ発火時（フル文モード）に使うテキスト
    "digest": str,           # 2件以上発火時（digest モード）に使うテキスト。収集関数自身が計算する
                             # （§4.2 のテンプレートは各収集関数内にそのまま実装し、汎用ディスパッチャ
                             #  には頼らない — rev6 の `_to_digest()` 集中管理案は rev7 で撤回。理由は下記）
    "commit": Callable[[], None] | None,  # 印字成功後にだけ呼ぶ副作用（ack 方式。使うのは
                             # spec_drift・pending_trigger・icebox レーン1 の3系統のみ）
}
```

**rev7 での再設計理由**: rev6 は `digest_exempt: bool` + 汎用 `_to_digest(item)` ディスパッチャで「2件以上発火時は一律 digest 化・例外2系統だけ素通し」というモデルだったが、tacchi 確定文言（§4.2）で icebox レーン1が「pending_trigger と同じ完全不変」でも「他系統と同じ汎用テンプレート適用」でもない**第3の形**（混在時のみ独自の短縮フレーム `icebox成立: {body}` を使うが、body 自体は不変）を持つことが判明し、bool 1個では表現できなくなった。そこで **各収集関数が `text`（フル文）と `digest`（短縮形）の両方を自分で計算して返す**方式に変更する:
- **pending_trigger**: `digest = text`（完全に同一の文字列。§4.2 例外2）。
- **icebox レーン1**: `text` = 既存の `build_met_notice()` の全文（敬体プレフィックス付き）。`digest` = `f"icebox成立: {build_met_body(shown)}"`（プレフィックスだけ短縮・body は不変。§4.2 例外1・rev7 確定）。
- **他7系統**: `digest` = §4.2 のテンプレートどおり収集関数内で組み立てる（各収集関数が自分のドメインデータ——`len(applied)`、`count` 等——を既に持っているため、外部ディスパッチャに渡す必要が無い）。

この再設計により、**merge 側のロジックは「発火件数が1なら `text` を、2以上なら `digest` を選んで結合するだけ」**という単純な規則に統一され、系統ごとの特殊分岐（bool 1個では足りなかった icebox の第3の形）を merge 側に持ち込まずに済む。

- **`commit` を使うのは spec_drift（§5.2）・pending_trigger（§5.5）・icebox レーン1（§5.3）の3系統**。他の系統は副作用を収集時点で完結させる（または副作用が無い）ため `commit=None`。
- rev5 では `on_shown` という名前だったが、rev6 で「収集関数の呼び出し元が明示的に実行する副作用」であることをより明確にするため `commit` に改称した（意味は同じ）。
- pending_trigger・icebox レーン1 の2系統は `commit` に加えて **lock 保持** も伴う（§6.2 の `ExitStack` 設計）。
- additionalContext 系統（session_proposal・work_context）は `NotificationItem` と別枠で扱う（① とは truncate ルールが異なるため。§6.3）。

### 6.2 具体的な関数変更（rev6・ExitStack ベースの ack 実装を追加）

- `hooks/restore_state.py`: 8個の `_deliver_*` を `_build_*_output`（**印字を行わない収集関数**・`NotificationItem | None` を返す）へリネーム・変換。「pure」という語は使わない（rev6・codex round2 [Should]2 反映 — この8関数のうち pending_trigger・evolve_drain・icebox は副作用を持つため「pure」は不正確。「印字しない」ことだけを契約とする）。`_build_session_proposal_output` は既存のまま（ただし戻り値の `systemMessage` 部分だけ `NotificationItem` 形式へ揃え、`additionalContext` は従来どおり別枠で保持）。
  - 新規: `_classify_daily_snapshot_file(path: Path) -> Literal["absent", "corrupt", "ok"]`（§4.6/§5.4 の分類ヘルパー。evolve-queue.json / icebox-status.json / icebox-verdicts.json の3箇所で使い回す）。
  - 新規: `_collect_notifications(stack: ExitStack) -> list[NotificationItem]`（9系統＋corrupt判定を順に呼ぶ。`ExitStack` を pending_trigger・icebox レーン1 の lock 登録先として各収集関数へ渡す。**各系統呼び出しは個別 try/except で保護し、1系統の例外が他系統の収集結果を巻き込まない**ことを維持する — 既存の設計指針そのまま）。
  - 新規: `_merge_notification_text(items) -> str | None`（**rev7**: 発火件数が1なら該当 item の `text`（フル文）をそのまま使う／2件以上なら全 item の `digest` を使う → Tier1 全量結合 → Tier2 予算内追加 → 超過分は「（ほか: 系統名を`/`区切りで列挙）」→ 条件を満たせば末尾導線 `→ /evolve-anything:queue で開始` を付与。§4.2, §4.2', §4.4）。汎用ディスパッチャ（rev6 の `_to_digest()`）は廃止 — 各収集関数が `digest` を自分で計算するため不要になった（§6.1）。
  - 新規: `_commit_all(items) -> None`（実際に結合文字列へ含めた item の `commit` を呼ぶ。**print 成功後にのみ呼ばれる**。spec_drift・pending_trigger・icebox レーン1 が実処理、他は no-op）。
  - `handle_session_start` 全体を `with ExitStack() as stack:` で包む（§6.3）。
- `scripts/lib/trigger_engine/pending.py`: 新規 `peek_pending_trigger()`（非破壊 read）・`delete_pending_trigger()`（delete のみ）を追加（§5.5）。既存の `read_and_delete_pending_trigger()` は削除しない（後方互換。他の呼び出し元は無いことを grep で確認済みだが、シグネチャ変更でなく追加のため安全側）。`PENDING_TRIGGER_FILE` は既存の module-level 定数をそのまま再利用する。
- `scripts/lib/spec_trigger.py`: `detect()` の返り値に `"marker"`（計算済み・未保存の dict）を追加。既存フィールドは変更しない。呼び出し元は `persist=False` を渡す新しいコードパス（`restore_state.py`）を追加するのみで、`detect()` のデフォルト引数（`persist=True`）自体は変更しない（後方互換）。
- `daily/queue_notice.py`: **変更不要**（`read_*()` のシグネチャは変えず、呼び出し側の `restore_state.py` が `path.exists()` を追加でチェックするだけ）。**§4.2 の digest テンプレートも、これらライブラリに新規関数を追加せず `restore_state.py` 側で完結させる** — `_build_*_output` は元々 `queue_data`/`verdicts_payload`/`status` 等の生 dict を既に受け取っており（`_resolve_queue_data()` が一度読んだものを共有）、digest はその生データ（例: `len(queue_data.get("queue"))`）から `restore_state.py` 内で直接組み立てる。`utterance_staleness_advisory` は元々 `hooks/restore_state.py` 内（daily/ 配下でない）で定義されているため、この関数だけは age_days を追加で返すよう拡張する（ローカル関数のシグネチャ変更のみ）。
- `daily/icebox_notice.py`: **read 関数のシグネチャは変更不要**。ただし icebox レーン1の lock 取得タイミングが変わる（§5.3・§6.3）ため、`unseen_met_verdicts`/`build_met_notice` を呼ぶ側（`restore_state.py`）のコードが変わる。**rev7 で1点訂正**: tacchi 確定文言（§4.2）の「混在時のみ `icebox成立: {body}` に短縮」を実現するため、`build_met_notice()` 内部の `body` 構築ロジックを `build_met_body(verdicts) -> str` として切り出す小さなリファクタが追加で必要（`build_met_notice()` はこれを呼ぶだけに変わり、戻り値は不変）。それ以外のライブラリロジックは無改修。

**新規 lock sidecar（`pending-trigger.json.lock`）の凍結非抵触**: `shrink_freeze.FROZEN_STORES` を確認したところ、`.lock` sidecar ファイルは1件も列挙されていない（既存の `icebox_verdict_seen.jsonl.lock` 等も同様に未列挙）。lock ファイルは業務データを持たない一時的な排他制御用ファイルであり store_registry の対象外という既存の扱いと同じ前提に立つ（§8 で再確認）。

### 6.3 checkpoint マージの一本化 ＋ ack フロー全体（codex round1 [Must]1・round2 [Must]4/7 反映で書き直し）

現行の「checkpoint が無ければ通知だけ出して return」「checkpoint があれば checkpoint + 通知をマージ」という2分岐（742-773行）を、以下の単一フローに統合する。**rev6 の主眼は「commit（副作用の確定）が必ず print 成功の後に来ること」を構造的に保証すること**（codex round2 [Must]4/7）。

```python
def handle_session_start(event: dict) -> None:
    _persist_pj_slug_cache()
    with ExitStack() as stack:
        items = _collect_notifications(stack)   # 9系統＋corrupt判定。pending_trigger と icebox
                                                  # レーン1 はここで lock を取得するが commit はしない
        try:
            system_message = _merge_notification_text(items)          # ①
            additional_context = _build_additional_context(items)     # ②（work_context + session_proposal）
            checkpoint = common.find_latest_checkpoint(project_dir)
            output = _build_final_output(checkpoint, system_message, additional_context)
            if output:
                print(json.dumps(output, ensure_ascii=False))
            # ── ここに到達 = print 成功 ── 副作用を確定してよい
            _commit_all(items)
        except Exception as e:
            print(f"[evolve-anything:restore_state] merge/print failed: {e}", file=sys.stderr)
            # commit を一切呼ばない。pending_trigger は未削除、icebox は未既読、
            # spec_drift は marker 未保存のまま → 次回セッションで再度候補になる
    # `with ExitStack()` を抜けた時点で pending_trigger / icebox の lock は
    # 成功時は commit 済みの状態で、失敗時は commit されないまま、必ず解放される
```

手順を分解すると:

1. `_collect_notifications(stack)` で9系統＋corrupt判定を集める（各系統は個別 try/except。pending_trigger・icebox レーン1 はここで lock を取得し `ExitStack` に登録するが、削除/既読化はまだ行わない）。
2. `_merge_notification_text(items)` で ① systemMessage 文字列を組み立てる（無ければ None）。
3. work_context summary（あれば）+ session_proposal の additionalContext（あれば）を連結して ② additionalContext 文字列を組み立てる（無ければ None）。
4. checkpoint（あれば `restored`/`checkpoint`/`sessionTitle` を保持）と ①②を1つの dict にマージする。
5. dict が空でなければ `print(json.dumps(dict, ensure_ascii=False))` を1回だけ呼ぶ。空なら何も print しない。
6. **手順5が例外を出さずに完了した場合のみ** `_commit_all(items)` を呼ぶ（実際に結合文字列へ含めた item の `commit` を実行。spec_drift の marker 保存・pending_trigger のファイル削除・icebox の record_seen がここで初めて確定する）。
7. 手順2〜6のいずれかで例外が出た場合は catch し、stderr にエラーを出すのみで **`_commit_all` を呼ばない**（= 何もコミットしない。fail-safe 側に倒す）。
8. `with ExitStack()` のブロックを抜ける際、pending_trigger・icebox レーン1 が取得した lock は（手順6を通っていれば commit 済みの状態で、手順7で抜けていれば commit されないまま）必ず解放される。lock の解放漏れ（デッドロック化）はこの構造上起こらない。

**評価**: この設計により、codex round2 が要求した3つの失敗経路（①収集関数自身の例外 ②merge関数の例外 ③print自体の失敗）の全てで、pending_trigger・icebox レーン1・spec_drift の commit が実行されないことが構造的に保証される（§5.3, §5.5 のテーブル参照）。

### 6.4 work_context summary の圧縮実装（tacchi [Should-t4]・§4.5 参照）

`_format_work_context_summary`（`restore_state.py:142-160`）を以下のロジックへ書き換える:

```python
def _format_work_context_summary(work_context: dict) -> str:
    parts = []
    branch = work_context.get("git_branch", "")
    if branch:
        parts.append(f"ブランチ: {branch}")

    commits = work_context.get("recent_commits", [])
    if commits:
        first = commits[0]
        first = first if len(first) <= 50 else first[:50] + "..."
        parts.append(f"直近コミット{len(commits)}件（先頭: {first}）")

    files = work_context.get("uncommitted_files", [])
    if files:
        if len(files) <= 3:
            parts.append(f"未コミット{len(files)}件（{', '.join(files)}）")
        else:
            parts.append(f"未コミット{len(files)}件")

    return "作業コンテキスト復元: " + " / ".join(parts) if parts else ""
```

呼び出し元（`handle_session_start`）は、この結果を work_context summary として単独の非JSON行で出す（旧実装）のではなく、§4.1 のとおり `hookSpecificOutput.additionalContext` へ連結する。**`save_state.py` の `_MAX_RECENT_COMMITS`/`_MAX_UNCOMMITTED_FILES` は変更しない**（保存はフル、表示だけ圧縮）。

---

## 7. テスト方針

### 7.1 契約テストで assert すべきこと（codex [Must]7 反映で強化）

1. **単一 JSON dict であること（強化版）**:
   ```python
   out = capsys.readouterr().out
   if expect_output:
       lines = out.strip().splitlines()
       assert len(lines) == 1          # 非JSON平文が何行あっても・JSONが0件でも通ってしまう旧assertionを廃止
       payload = json.loads(lines[0])  # 単独で valid JSON であること
       # 期待するキー（systemMessage / hookSpecificOutput.additionalContext / restored / checkpoint）が
       # 同一 dict に共存することを個別に assert
   else:
       assert out == ""
   ```
2. **平常時1行**: 発火系統0〜1件のときの出力行数が現状と同等であることの回帰確認。
3. **Tier1 が Tier2 の truncate に巻き込まれないこと**: Tier1 複数 + Tier2 大量同時発火のfixtureで、Tier1 テキストが必ず結合文字列に含まれることを assert。
   - **畳み機構の本命ケース（tacchi 指摘・rev7 で完了条件に追加）**: 抽象的な「上限超過」fixture だけでは不十分（codex round2 [Should]3 / tacchi が指摘したとおり、Tier2 は digest 化後たかだか数十字にしかならず、実際に畳みが発動するのはほぼ「icebox レーン1のフル文（またはreasonが長い場合の混在時短縮形）が Tier1 予算を大きく食い、Tier2 の残り予算がほぼ無くなるケース」だけである）。したがって**「icebox レーン1に複数 named issue（reason が長いものを含む）が同時に成立し、かつ他の Tier2 系統も複数発火する」fixture を必須の直接テストケースとして張る**（抽象的な文字数超過 fixture だけで済ませない）。このケースで (a) icebox レーン1の内容（issue番号・reason）が一切失われないこと、(b) Tier2 側の一部が「（ほか: 系統名）」に畳まれること、の両方を assert する。
4. **spec_drift の two-phase**（新規・§5.2）:
   - 表示されなかった（Tier2 予算超過で truncate された）場合、`save_marker` が呼ばれていないこと（`monkeypatch` でスパイし呼び出し0回を確認）。
   - 次回呼び出しで同じ `surfaced` が再現すること（`last_sha` がディスク上で更新されていないことの確認）。
   - 表示された場合は `save_marker` が1回だけ呼ばれること。
   - 初回セットアップ分岐（`last_sha` 無し）は `persist=False` でも `save_marker` が呼ばれること（§5.2 の例外規則）。
   - **[Should]1 固有テスト（rev6・codex round2 反映）**: `save_marker` 自体を monkeypatch で例外送出させ、(a) `handle_session_start` がクラッシュしないこと、(b) stderr にエラーが出ること、(c) print は既に成功しているため今回はメッセージが表示されること、(d) marker が保存されていないため次回セッションでも同じ `surfaced` が再現すること、を確認する。
5. **icebox レーン1 ack 方式**（新規・§5.3・rev6 で全面改訂）:
   - レーン1「成立」が Tier2 予算に関わらず必ず結合文字列に含まれること（digest 化されずフル文のまま）。
   - 既存の並行 SessionStart lock テスト（`test_deliver_serializes_via_file_lock`）が「lock 保持中は他プロセスが進めない」という検証内容を維持したまま、新しい ack フロー（lock は decide〜commit まで保持）に書き換わっていること。
   - `record_seen` が **print 成功後**に呼ばれること（lock 内・かつ merge/print の後、という新しい順序を確認する回帰テスト）。
   - **失敗3経路の E2E**（codex round2 [Must]4 の直接要求）: builder例外／merge失敗／print失敗のそれぞれを monkeypatch で発生させ、いずれのケースでも `record_seen` が呼ばれておらず、次回セッションで同じ verdict が再び「未読」として候補になることを assert する。
   - **`record_seen` 自体が例外を出すケース**（[Should]1 と同型の marker 書込失敗テスト）: print は既に成功しているため今回はユーザーに表示されるが、`record_seen` の失敗により次回also再表示されうる（重複表示は許容・情報消失より優先）ことを確認する。
6. **producer 破損判定**（新規・§4.6/§5.4）:
   - evolve-queue.json が存在するが不正 JSON → Tier1 health notice が発火。
   - evolve-queue.json が存在しない → 沈黙（既存どおり）。
   - icebox-status.json / icebox-verdicts.json も同様に2ケースずつ。
7. **judge_cap 全分岐 Tier1**（新規・§5.1）: capped / source_failed / skipped_locked / out_of_range の4分岐全てが、他 Tier2 系統の同時大量発火があっても結合文字列から漏れないこと。
8. **pending_trigger の ack 方式 E2E**（新規・§5.5・rev6 で全面改訂）:
   - 正常経路: `pending-trigger.json` を書く → `handle_session_start` → 最終1行にメッセージが含まれること → ファイルが削除されていること。
   - **失敗3経路の E2E**（codex round2 [Must]7 の直接要求。§5.5 の表と対応）: ①収集関数自身の例外 ②他系統/merge関数の例外 ③print自体の失敗、をそれぞれ monkeypatch で発生させ、いずれのケースでも `pending-trigger.json` が**削除されずに残っている**ことを assert する（＝次回セッションで再提示される）。
   - 現行の `test_e2e_trigger_flow.py:51-62` は `read_and_delete_pending_trigger()` を直接呼ぶだけで `restore_state` の集約・出力を経由しておらず、上記いずれの保証も持っていない（codex round1/round2 で指摘済み）。
9. **stdout/stderr の切り分け**（新規・§4.3）: 1系統が内部例外を出しても、他系統の内容が stdout 1行に残ること、かつ stderr にエラーメッセージが出ること。
10. **digest/full 切り替え**（新規・tacchi [Must-t2]/[Should-t3]・§4.1/§4.2）:
    - 発火系統が1件のみ → 既存のフル文がそのまま出ることの回帰確認。
    - 発火系統が2件以上 → 各 item の `digest` フィールド（§4.2 のテンプレート）が使われていること、pending_trigger は `digest == text`（完全不変）、icebox レーン1は `digest` が短縮フレーム `icebox成立: {body}` になっていること（body は不変）、`[evolve-anything]` prefix が1回だけであることを確認。
    - **今朝の実データに近い fixture**（evolve_drain + queue_notice + judge_cap + icebox フォールバックの4件同時発火）で、結合後の文字列が読みやすい長さ（目安200字未満）に収まることを確認する回帰テストを追加する（tacchi [Must-t2] の実測値をそのまま fixture 化する）。
    - **pending_trigger・icebox レーン1 が同時発火する fixture**で、両方ともフル文のまま結合され、digest 化されていないことを確認する回帰テスト（codex round2 [Must-new] の直接的な回帰防止）。
    - Tier2 予算超過時の末尾表記が「（ほか: 系統名）」形式であり、件数のみの「ほかN件」でないことを確認。
11. **work_context 圧縮**（新規・tacchi [Should-t4]・§4.5/§6.4）: 6件の commits・35件の uncommitted files を持つ fixture で、圧縮後の文字列が「件数+先頭1件」「件数のみ」の形式になっていることを確認する。
12. **digest 行の末尾導線**（新規・tacchi [Should]1・§4.2'）:
    - evolve_queue_notice（FRESH）・evolve_drain・icebox（レーン1 or フォールバック）のいずれかが発火している fixture → 末尾に `→ /evolve-anything:queue で開始` が付くこと。
    - data_dir_migration・utterance_staleness・spec_drift・judge_cap のいずれか**のみ**が発火している fixture（上記3系統が1つも無い）→ 末尾導線が付かないこと。
    - 発火系統が0〜1件（フル文表示）のときは末尾導線を付けない（digest 行専用の仕様であることの回帰確認）。
13. **icebox レーン1 body 抽出のリファクタ回帰**（新規・tacchi 確定文言・§4.2）: `daily/icebox_notice.py` の `build_met_body()` 切り出し後も `build_met_notice()` の戻り値（1件発火時のフル文）が無改修であることを既存テストで確認する。

### 7.2 影響を受ける既存テスト（実測 grep・更新版）

| ファイル | 影響度 | 変更内容 |
|---|---|---|
| `hooks/tests/test_restore_state_queue_notice.py` | 高 | `_deliver_evolve_queue_notice` → `_build_evolve_queue_output` に追従。直接呼び出しは収集関数（副作用なし）の戻り値を assert する形に書き換え |
| `hooks/tests/test_restore_state_judge_cap_notice.py` | 高 | 同様のリネーム追従。**加えて全分岐が Tier1 になったことを明示するテストを追加**（§7.1-7） |
| `hooks/tests/test_restore_state_icebox_notice.py` | **高（rev6で格上げ）** | リネーム追従に加え、`record_seen` タイミングのテスト（211-330行台）を **ack 方式（lock は decide〜commit まで保持、record_seen は print 成功後）** に合わせて書き換え。並行 lock テスト（330-367行）は検証内容を維持したまま新フローに対応させる。失敗3経路の新規テストを追加（§7.1-5） |
| `hooks/tests/test_restore_state_drain.py` | 高 | リネーム追従（print → 戻り値） |
| `hooks/tests/test_restore_state_migration.py` | 高 | リネーム追従 |
| `hooks/tests/test_restore_state_utterance_staleness.py` | 中 | `_deliver_utterance_staleness` はリネーム追従。`utterance_staleness_advisory`（副作用なしの収集関数）は無改修 |
| `hooks/tests/test_hooks_session.py` | 高（rev1より格上げ） | work_context summary の別行テスト（402-428行）を**単一 JSON dict の additionalContext 内アサーションへ書き換え**（codex [Must]1 の直接的な影響） |
| `hooks/tests/test_restore_state_session_proposals.py` | 中 | `TestSingleJsonResponse` の2テストを §7.1-1 の強化版アサーションへ書き換え |
| `hooks/tests/test_restore_state_session_title.py` | 中 | checkpoint マージの一本化（§6.3）に伴い、checkpoint-only fixture でも新しい単一フローを通ることを確認 |
| `hooks/tests/test_restore_state_queue_read_dedup.py` | 低 | `_resolve_queue_data()` に破損分類ロジックが乗るため、破損ケースの重複読み込み防止も合わせて確認 |
| `hooks/tests/test_restore_state_pj_slug_cache.py` | 低 | 通知系と独立。影響小 |
| `hooks/tests/test_e2e_trigger_flow.py` | **高（rev6でさらに格上げ）** | §7.1-8 の新規 E2E アサーション（正常経路＋失敗3経路）をこのファイルに追加。`peek_pending_trigger`/`delete_pending_trigger` の新規追加に伴い、既存テストが `read_and_delete_pending_trigger()` を直接呼んでいる箇所は影響を受けないか確認（後方互換で残すため無改修のはず） |

影響なし: `test_hooks_worktree.py`, `test_hooks_misc.py`, `test_hooks_workflow.py`, `test_marker_root_isolation.py`, `test_hooks_observe.py`, `test_hooks_safety.py`, `test_save_state_corrections_scope.py`。

---

## 8. 凍結非抵触の根拠

`scripts/lib/shrink_freeze.py` の4凍結集合を確認した:

- `FROZEN_STORES`（61-108行）: Phase 0 は新しいファイル/DB を一切作らない。読む対象は全て既存 store（`evolve-queue.json`/`icebox-status.json`/`icebox-verdicts.json` は既に列挙済み・82-83行、78行）。producer 破損判定（§4.6/§5.4）も既存ファイルの `exists()` チェックを追加するだけで新規ファイルを作らない。**新規 store なし**。**rev6 で追加する `pending-trigger.json.lock` sidecar**（§5.5/§6.2）についても確認した — `FROZEN_STORES` の61-108行に `.lock` ファイルは1件も列挙されておらず（既存の `icebox_verdict_seen.jsonl.lock` 等の sidecar も同様に registry 対象外）、lock ファイルは業務データを保持しない一時的排他制御用ファイルという扱いが既存コードベース全体で一貫している。この precedent に従い、新規 lock sidecar は「新設 store」に該当しないと判断する。
- `FROZEN_OBSERVABILITY_SECTIONS`（111-158行）: `audit/observability.py` の別系統。Phase 0 は audit を一切触らない。**無関係・非抵触**。
- `FROZEN_ADVISORY_PROPOSAL_ADAPTERS`（161-166行）: `advisory_proposals.py` のアダプタ登録。Phase 0 は関与しない。**無関係・非抵触**。
- `FROZEN_WEAK_SIGNAL_CHANNELS`（171-180行）: weak_signals 関連。Phase 0 は一切触らない。**無関係・非抵触**。

**結論**: Phase 0（spec_trigger.detect の戻り値追加を含む）は凍結対象外。実装着手の障害にならない。

---

## 9. 仕分け表サマリ（1行ずつ・rev7 確定版）

※ 以下は Tier（絶対に落とさないか）の分類。実際の表示が digest/full どちらになるかは §4.2 の別軸（発火系統数による）で決まる — Tier1 でも2件以上同時発火時は digest 化される（icebox レーン1のみ例外）。

- pending_trigger → ① systemMessage・Tier1固定・digest化免除（フル文のまま）。**rev6: ack方式**（lock取得→peek→print成功後にdelete。失敗3経路でファイルは削除されない）
- spec_drift → ① systemMessage・Tier2（marker 保存を `persist=False` + `commit` で defer し、間引いても冪等に再現することを保証）
- evolve_drain → ① systemMessage・Tier1（柱4の信頼に直結。副作用は収集時点で完結・変更なし）
- data_dir_migration → ① systemMessage・Tier1（副作用なし）
- utterance_staleness → ① systemMessage・Tier1（副作用なし。#351 沈黙再発防止）
- evolve_queue_notice → ① systemMessage・health notice分岐は Tier1／FRESH内容分岐は Tier2（状態が冪等なため）／ファイル破損分岐は新規 Tier1
- session_proposal → ①(systemMessage部分)Tier2・副作用なし ＋ ②(additionalContext)常時フル。work_context summary と同じ additionalContext 枠に統合
- judge_cap → ① systemMessage・**全分岐 Tier1**（一回性スナップショットのため部分Tier1化を撤回）
- icebox → ① systemMessage・health notice/レーン1成立は Tier1固定・digest化免除。**rev6: レーン1は ack方式**（lockをdecide〜commitまで保持、record_seenはprint成功後）／フォールバック件数集約は Tier2／ファイル破損分岐は新規 Tier1
- ③ stderr は9系統のどれにも割り当てず、既存の実装エラー経路のまま維持

---

## 10. 未決事項 / 頭に確認したいこと（rev7）

頭裁定（rev2〜rev5）は全て反映済み:
- ③stderrの解釈確定・例外系可視化強化はスコープ外確定・work_context の additionalContext 統合確定（rev2〜3）
- **systemMessage の Tier2 予算は頭裁定により `400字` に確定**（§4.4。測定単位=`len()`／Tier1は上限に関係なく全量／超過分はdigest単位で「ほか:系統名」表示／区切りは`" / "`。**rev6 で「400字は最終出力の保証ではなく Tier2 予算配分ルール」と明示化**（§4.4 実効上限契約）。rev4 で提案した300字は不採用・旧「未確定のまま」は解消）
- pending-trigger.json / spec-drift marker の破損時挙動を Phase 0 スコープ外とする判断は「その判断でよい」と承認済み。理由は §4.6 に明記済み（silence ≠ evaluated を踏まえた明示的スコープ決定）
- CC の stdout 解釈の実環境検証は「未確認のままでよい・実装PRで実測するタスクとして完了条件に明記」との裁定どおり、**§12 完了条件に必須項目として追加**（「推奨」から「必須」へ格上げ）

tacchi 指摘（体験レビュー §0.1・digest文言レビュー §0.3）・codex round1/round2（§0.1/§0.2）への対応は全て反映済み。**digest 文言は rev7 で確定とし、これ以上の変更は行わない**（頭からの明示指示）。

**実装 PR で決めてよい事項（ブロッカーではない）**:
1. 400字閾値は運用開始時の初期値。実運用ログを1〜2週間収集し乖離があれば調整可（§4.4）。
2. **icebox レーン1の `reason` 文字列に長さ上限を追加するか**（§4.4(c)）: Phase 0 では「そのまま許容する」（頭裁定rule1の方針）を採用し、`MAX_REASON_CHARS` のような新規ガードは導入しない。運用開始後、実際に長い reason が届いて表示が乱れるようなら、追加の改善課題として別途検討する。
3. **pending-trigger.json.lock の新設が実装時に問題ないか**: 設計上は既存の `icebox_verdict_seen.jsonl.lock` と同型パターンだが、pending_trigger には従来ロックが無かったため、実装 PR でロック取得のオーバーヘッド（ほぼ皆無のはずだが）と既存呼び出し元（`read_and_delete_pending_trigger()` を直接呼ぶ経路が他に無いか）を最終確認する。

いずれもブロッカーではなく、暫定方針を明記した上で実装に進められる（provisional-over-blocker）。

---

## 11. 参照

- `docs/decisions/054-four-pillars-completion-design.md` §2.3(a), §5 Phase 0, §8
- `hooks/restore_state.py`（142-160, 163-802行）
- `scripts/lib/daily/queue_notice.py`, `scripts/lib/daily/icebox_notice.py`, `scripts/lib/daily/freshness.py`
- `scripts/lib/trigger_engine/pending.py`（破壊的読み取りの根拠）
- `scripts/lib/spec_trigger.py`（113行 `MAX_REMINDERS`、182-363行 `detect`/`load_marker`/`save_marker`）
- `hooks/save_state.py`（19-20行 `_MAX_UNCOMMITTED_FILES`/`_MAX_RECENT_COMMITS`、40-74行 `_collect_work_context`）
- `scripts/lib/shrink_freeze.py`（凍結4集合）
- `docs/decisions/038-stop-hook-additional-context-subagentstop-only.md`（systemMessage/additionalContext のチャネル契約）
- `hooks/tests/test_restore_state_session_proposals.py`, `hooks/tests/test_restore_state_icebox_notice.py`, `hooks/tests/test_hooks_session.py`（既存契約テスト）
- CHANGELOG.md 141行目（#412 の原因と是正内容）
- `/private/tmp/claude-501/-Users-matsukaze-takashi-matsukaze-utils-evolve-anything/6db8d639-a569-4acd-996a-0cc9e5e1a8f4/scratchpad/codex_phase0_findings.md`（codex round1 レビュー原文）
- `/private/tmp/claude-501/-Users-matsukaze-takashi-matsukaze-utils-evolve-anything/6db8d639-a569-4acd-996a-0cc9e5e1a8f4/scratchpad/codex_phase0_r2.log`（codex round2 レビュー原文。`codex_phase0_r2.log`）
- tacchi 体験レビュー（team-lead 経由メッセージ・2026-08-12 受領。体験レビュー本体 + digest 文言レビューの2回）
- `~/.claude/projects/-Users-matsukaze-takashi-matsukaze-utils-evolve-anything/{3abc3c94,6dd023d5}-*.jsonl`（§4.1 の実測データ源。頭の指示により本人が実測）
- `scripts/lib/daily/icebox_notice.py`（197-214行 `build_met_notice`。rev7 で `build_met_body()` 切り出しの対象・§4.2, §6.2）

---

## 12. 完了条件（Phase 0 全体）

§7.1 の契約テスト・§7.2 の既存テスト更新に加え、以下を Phase 0 完了の必須条件とする:

1. `python3 -m pytest` exit 0（§7.1/§7.2 の新規・更新テストを含む）。
2. `bin/evolve-dogfood-gate --layer light` exit 0。
3. `claude plugin validate` exit 0。
4. **CC 実環境での SessionStart 複数系統同時発火の目視確認**（§2 の未確認事項・頭裁定#3で「未確認のままでよいが実装 PR で実測するタスクとして完了条件に明記」）: 実装 PR のどこかで、複数の①systemMessage系統＋②additionalContext（session_proposal）が同時に発火する状態を意図的に作り、実際の Claude Code セッションで SessionStart を発火させ、
   - stdout が本設計どおり単一 JSON dict（0行 or 1行）になっているか
   - systemMessage の内容が UI に正しく表示されるか
   - additionalContext の内容を Claude が実際に認識しているか（次の応答で言及されるか等）
   を目視確認し、結果を実装 PR の説明に記載する。この確認は§2の「未確認」を解消するためのものであり、想定外の挙動が見つかった場合は本設計文書へフィードバックし修正する。
