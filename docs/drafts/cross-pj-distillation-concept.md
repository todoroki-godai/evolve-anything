# 横断蒸留（cross-PJ distillation）構想 — 概念と方針

Status: **draft**（未コミット・議論用） / 2026-07-06
出典: subagent 実地調査2本（evolve-anything 既存横断機構の棚卸し / zundamon-explainer・bots kuni-isle・figma-to-code の実地調査）

## 0. 一言で

全 PJ の会話ログ・スキル・rules・memory から「**表現は違うが同型**」のパターンを抽出し、ドメイン非依存の**メソッドスキル（型）**に蒸留する。以後は各 PJ での指摘・修正がその型に焼き戻され、CC で会話を続けるほど型が磨かれる。

## 1. 仮説の裏取り（実地調査で確認された事実）

ユーザー仮説「台本チェックとプレゼン/サイト生成の指摘は通じる」「評価ループには共通構造がある」は、実ファイル引用レベルで裏が取れた。

### 1-1. 同型観点（高同型 9 件）

| 観点（抽象名） | 動画側の実物 | figma-to-code 側の実物 |
|---|---|---|
| 冒頭N秒で目的把握 | explainer-script-method「冒頭5秒で見続けたくなるか」 | figma-eval-expert「3秒以内にページの目的を理解できるか」 |
| 装飾とコンテンツの衝突回避 | zundamon「帯・主要要素を下1/3に侵入させない」 | evaluate-human「decoration that occludes content」+ collision でスコア上限キャップ |
| 総合採点は局所欠陥を平均化する→決定論チェックを別レイヤで挟む | 「TTS誤読は reviewer でなく機械が拾う」(L0 lint) | check-text-integrity「行落ち1文字は誤差に丸められる→決め打ち実測で100%捕まえる」 |
| 一次情報・実測での裏取り | 「WebSearch要約だけで固有名・主張を断定しない」 | 「ground-truth は figma JSON を Read してから断定」 |
| 評価役は read-only・指摘のみ | 「評価役はファイルを編集させない」+ reviewer の tools 制限 | figma-eval-expert 禁止事項「個別デザインへの単独修正提案」 |
| 指摘は恒久台帳へ焼き戻す | 「観点へ恒久化。その場で直すだけで終わらせない」 | 「学びは skill に落とし skill 経由で実装。個別手当てを選択肢に出さない」 |
| 台帳の版管理 + 配布用蒸留 | review_rubric.md + changelog（Living Rubric） | pitfalls.yaml (SoT) → pitfalls-top20.md |
| 完了宣言の鮮度検証（stale 機械ブロック） | factcheck.json ハッシュ封印 | fresh verdict.json 必須 + Stop hook block |
| 空疎コンテンツ排除 | 観点「固有名を名前を出して終わりにしない」 | placeholder 文字列の CRITICAL 検出 |

### 1-2. 評価ループの共通スケルトン

3 PJ とも `入力 → 評価軸 → 採点者（決定論L0 + LLM評価者）→ 反復条件 → 人間ゲート → 記録（append-only台帳）` の**独立再発明**。差分は人間ゲートの強度（zundamon=自動公開+猶予枠 / kuni-isle=採否人間判断 / figma-to-code=signoff fail-closed）と台帳の構造化度。

### 1-3. 先行実例: explainer-script-method

「型=グローバルスキル / 中身=PJスキル」分離を手動で実装済みの正典。
- 双方向参照（global 末尾に PJ ポインタ節、PJ 冒頭に「global が正本」宣言）で二重管理を構造的に禁止
- 焼き戻し先の昇格判定 3 分岐（既存観点該当→global / チャンネル非依存→1チャンネル目で即昇格 / 固有→PJ のみ）
- 運用ルールを実測で事後修正した実績（2026-07-04「2チャンネル再現待ち」廃止）

## 2. 概念モデル: 3層 × 4レーン

### 3層（資産の構造）

| 層 | 内容 | 例 |
|---|---|---|
| 中身 (instance) | PJ 固有の具体 | キャラ正典、Figma 設定、投稿手順、lint_config 差分 |
| 型 (method) | ドメイン横断メソッドスキル | explainer-script-method（既存）、evaluation-loop-method（候補） |
| 型の型 (meta) | 抽出・帰属・進化の仕組み | evolve-anything 本体（本構想の実装先） |

### 4レーン（横断の粒度。L1 まで既存、L2 以降が新規）

- **L1 同一テキスト照合**（既存）: fleet recall / cross_pj_priority / memory_dup_residue。全て keyword・jaccard・完全一致の表層照合
- **L2 意味同型判定**（新規）: 語彙が違う同型パターンの検出。表層一致では原理的に不可能 → LLM バッチ判定が必須。correction_semantic / auto_memory と同型の **2相**（決定論 enqueue → LLM バッチ判定 → 決定論 ingest）に載せる。写像スキーマは skill_extractor の4軸分解（routing/workflow/semantics/attachments）を転用
- **L3 型スキル蒸留**（半自動）: 候補提示 → **人間承認** → skill-creator で生成 → 双方向参照の配線。全自動化しない
- **L4 継続進化**: 各 PJ の corrections を型スキルへ帰属 → fleet queue に型スキルの学習素材も載る → evolve 対象化。効果測定は outcome_attribution / negative_transfer / paired_trajectory (#15) を流用
- **L4+ constitution 配線**（2026-07-06 決定）: 型スキルを `constitutional` fitness の原則ソースとして食わせる。これにより「会話 → 型スキルが育つ → 育った型が全 PJ のスキル進化を採点する」の双方向ループが閉じる。型スキルの消費者は3種: ①人間（新 PJ ブートストラップ）②PJ スキル（正本参照）③evolve 自身（採点基準）

### L2 の enqueue 設計（決定論シグナル 4 段ファネル）

組合せ爆発は「ペア空間の選び方」で構造的に回避する。raw ログ同士は比較しない。

| 段 | シグナル | 実体 |
|---|---|---|
| 0. 対象限定 | 比較単位は**蒸留済み資産のみ**（Living Rubric 観点 / pitfalls / confirmed idioms / グローバル観点）。1 PJ 数十件 → 全 PJ 掛け合わせでも数百〜数千ペア | 各 PJ の rubric / pitfalls.md / correction_idioms.jsonl |
| 1. 普遍性タグ | pitfall-curate の `universal / 汎用度4-5` のみ通す（人間承認済みメタデータ）。project/instance は enqueue しない | pitfall_curate 分類 |
| 2. 構造スロット一致 | skill_extractor 4軸（routing/workflow/semantics/attachments）で同スロット同士のみペア化（評価観点×評価観点等）。ペア空間をブロック対角化 | skill_extractor/decomposition.py |
| 3. 優先度 | (a) L1「惜しい外れ」帯 = jaccard 中間帯（完全一致未満・無関係以上）(b) correction 再発率が高い観点 (c) 複数 PJ で同種チャネルの素材が同時多発。**結合は OR・max 採用（加重和にしない）**: (b)(c) は表層類似と独立の「痛みシグナル」なので、jaccard≈0 でも立てば強制 enqueue — 語彙が完全に異なる同型（L2 本来の標的）を (a) の低スコアで落とさない（2026-07-06 外部レビュー指摘で明確化） | similarity.py / outcome_metrics / fleet queue |

運用ガード: daily cap（idiom_autopromote 流）+ 件数/char 上限（GEPA ガードレール流）+ 実行前トークン見積もり提示（llm-batch-guard rule）。規模イメージ: 3 PJ × 30 観点 → フィルタ後数百ペア → 上位 10〜30 ペア/日を Haiku バッチ（1日数千〜数万トークン級）。

## 3. 設計原則（この環境の既存の流儀を踏襲）

1. **決定論と LLM の分離**: Python 本体は claude CLI を呼ばない（ADR-037 準拠の 2 相）
2. **人間承認ゲート**: 初見の抽象化候補を機械が自動昇格しない（ADR-047 不変条件）
3. **実コーパス較正**: 同型判定の precision は実 PJ データ dry-run で較正（relevance_gate 閾値流用が #578 で破綻した教訓）
4. **silence ≠ evaluated**: 候補ゼロでも「評価済み・候補なし」を明示（advisory 共通枠 #115）
5. **新ストアは store_registry + store_write barrier 経由**（ADR-049）
6. **型/中身の二重管理禁止**: explainer-script-method の双方向参照 + 昇格 3 分岐を正典とする
7. **手動先行・機械化後追い**: 型スキルをまず 2〜3 個手動で蒸留し、その経験を自動抽出の設計データにする（explainer-script-method の昇格ルールが実測で事後修正された前例に従う）

## 4. 型スキル候補（実地調査から抽出、優先順）

1. **evaluation-loop-method** — §1-2 のスケルトンをドメイン中立に定義。3 PJ が独立再発明済みで需要実証済み。最有力
2. **correction-to-permanent-ledger** — 指摘の恒久台帳化（append-only + changelog + Top-N 蒸留 + 多点同時更新）。evolve-anything の reflect / pitfall-curate 自身が3つ目の独立実装であり統合余地あり ✅ **2026-07-07 作成済**（Phase 0 型スキル②・§5 進捗参照）
3. **ground-truth-over-summary** — 一次情報・実測優先、LLM 自己申告/二次情報を完了根拠にしない。グローバル rule verify-before-claim と親和 ✅ **2026-07-07 作成済**（Phase 0 型スキル③・§5 進捗参照。rule との差分＝enforcement の how を裏取りで切り分け）
4. **read-only-critic-role-template** — 評価役 agent の設計テンプレ（tools 制限 + 禁止事項明文化 + 採否分離）。agent-brushup のチェック項目化と相性良し
5. **decoration-content-collision-check** — 装飾/コンテンツ衝突の検出型。ui-ux-pro-max との守備範囲確認が先（未確認）

## 5. ロードマップ案

| Phase | 内容 | 機械化 |
|---|---|---|
| 0. 手動蒸留 | evaluation-loop-method を skill-creator で作成。figma-to-code と explainer-script-method を最初の2インスタンスとして配線 | 不要（今すぐ可能） |

**Phase 0 進捗（2026-07-07 更新）**

型スキル **3個手動蒸留完了**（§7 原則#7「2〜3個手動先行」を満たす）:

**型スキル① evaluation-loop-method**（評価ループ全体の型）
- ✅ 作成済（`~/.claude/skills/evaluation-loop-method/SKILL.md`・123行）。§1-2 スケルトンをドメイン非依存に定義
- ✅ 双方向参照 **3/3 完成**: explainer-script-method ⇔ 型、figma-eval-expert.md ⇔ 型（両 instance→global 配線済）

**型スキル② correction-to-permanent-ledger**（指摘の恒久台帳化＝①の⑦焼き戻し段を深掘りした子の型）
- ✅ 作成済（`~/.claude/skills/correction-to-permanent-ledger/SKILL.md`・118行）。3独立実装（explainer Living Rubric / figma pitfalls.yaml→top20 / evolve-anything pitfall-curate+reflect）から6ステージ骨格（記録→分類/昇格→版管理→配布蒸留→同期ゲート→焼き戻し必須化）を file:line 裏取りで蒸留
- ✅ 逆リンク **3/3 完成**: pitfall-curate（自PJ）/ explainer-script-method（自グローバル）/ figma-eval-expert.md（別PJ min-sys owner・2026-07-07 承認済）⇔ 型。figma-eval-expert.md は eval-loop と台帳の2型スキルへの逆リンクを併記

**型スキル③ ground-truth-over-summary**（一次情報を根拠にする＝要約/自己申告/集計丸めで断定しない）
- ✅ 作成済（`~/.claude/skills/ground-truth-over-summary/SKILL.md`・89行）。3独立実装（explainer factcheck ハッシュ封印 / figma JSON Read-before-assert + 行落ち決め打ち実測 / evolve-anything user_only_text + 実コーパス dry-run 較正）から4原則を蒸留
- ✅ **既存 rule と非重複を裏取りで切り分け**: factual-claims / verify-before-claim は「言う前に確認せよ」の自己申告的宣言、型は enforcement の how（封印/Read-before-assert/dry-run 較正/自己出力排除）→ rule 早見表を本体併記
- ✅ 逆リンク: global `factual-claims.md` に1行ポインタ（rule=「何を」/ 型=「how」・全PJ影響ゆえ承認済）。forward は本体で3インスタンス名指し。instances が skill/CLAUDE.md/rule 混成ゆえ双方向は最小配線で運用開始

**その他**
- ℹ️ figma-to-code SKILL.md 破損疑いは**誤読**と確定（frontmatter は正常な単一 YAML ブロック・二重化なし）
- 副産物: 高同型9件（§1-1）+ 台帳6ステージの実測「なぜ」を、同型判定 precision 計測の**初期正例コーパス**として確保（§8 対応）
- 次候補（未着手）: §4 の候補4-5（read-only-critic-role-template = agent-brushup と重複確認先 / decoration-content-collision-check = ui-ux-pro-max と守備範囲確認先）。候補5は blocker あり
| 1. 帰属レーン | corrections → 型スキルのマッピングを reflect/evolve に追加。fleet queue が型スキルの素材も数える | 決定論 + 既存 LLM 判定拡張 |
| 2. 同型検出レーン | utterances.db + 全 PJ skills/rubrics を素材に、候補ペアを 2 相 LLM バッチで提案（`fleet distill` 仮称） | 新規 2 相バッチ |
| 3. 効果測定 | 型スキル導入前後の outcome delta を advisory 表示 + **降格ゲート**（negative_transfer gate/rollback #10 を型スキルへ適用）+ **定期棚卸し**（人間による反証レビュー = エコーチェンバー対策） | 既存部品流用 |

**Phase 1-3 の over-eng 点検 結論（2026-07-07・second-opinion cold-read + 一次コード裏取り）**

判定 = **Phase 1-3 は現時点で作らない（保留）**。理由:
- この環境は「観測厚いが作用薄い」（変換率 4.3% / write-only advisory 18個・[[project_critique_20260706]]）。Phase 1-3 はいずれも「新しい観測・提案の機械」を足す側で、19個目の write-only 化リスクが高い。
- **Phase 1（帰属レーン）は観測とアクションの対象不一致**: fleet queue が型スキル素材を数えても、消費アクションである evolve の apply/焼き戻しは global 型スキル本体へ配線されていない（corrections→型スキル焼き戻しは 100% 手動）。土台となる消費習慣が未確立の上にカウンタを積むことになる。
- **Phase 2（同型自動検出）は n=3 からの早すぎる一般化**: precision 計測用の負例コーパスが未整備（正例9件のみ）。検証手段がないまま検出器を作るのは [[learning_gate_design_needs_real_corpus_dryrun]] に反する。
- **Phase 3「既存部品流用」は過小見積もり**: outcome_attribution/negative_transfer は PJ スコープ前提。型スキルは PJ 外（~/.claude/skills）ゆえ横断集約層が新規に要る（§6 未解決1 の置き場所問題を解かないと軽くない）。母数 N も小さく insufficient_data で永久沈黙する公算。

**裏取りで判明した重要な訂正**: cold-read agent は「L4+ constitution 配線が既に閉じたループ＝型スキルが採点基準として読まれる」と断言したが、**現行コードは未実装**（`scripts/rl/fitness/principles.py:145-166` の原則ソースは `CLAUDE.md + project rules のみ`、global 型スキルを読まない）。§6 決定済みの L4+ は「決定だが未配線」。つまり現時点で型スキルの自動消費経路は**ゼロ**（参照呼び出し + 双方向参照による二重管理防止のみが Phase 0 の実価値）。

**最小増分（コードを書かず価値検証）**: 型スキル3個 × 各インスタンス PJ で、corrections に気づくたび「この修正は型スキル X に関連」と手書きログ1行。2〜4週間で **型スキルの SKILL.md を実際に焼き戻し編集した回数**を数える。0回なら「可視化しても行動が変わらない」証拠＝Phase 1 自動化は不要と確定。2回以上なら自動化の投資対効果を再検討。

**もし1手だけ機械化するなら Phase 1-3 でなく L4+ 配線**（principles.py の原則ソースに global 型スキルを追加）。型スキル本文の編集が採点に自動伝播する唯一の直接レバーだが、これも constitutional fitness が実際に走り消費される需要が確認できてから（速断しない）。

## 6. 論点と決定

### 決定済み（2026-07-06 ユーザー確認）

- **型スキルの使い道 = 参照リンク + 採点基準の両方**: PJ スキルからの正本参照（explainer-script-method 方式）に加え、`constitutional` fitness の原則ソースとして evolve に配線する（§2 L4+）。「会話するほど進化する」の実装先はこの循環。**⚠ 2026-07-07 裏取り: L4+ は「決定」だが未配線**。現行 `principles.py:145-166` は `CLAUDE.md + project rules` のみを原則ソースとし global 型スキルを読まない。採点基準としての消費は未実現（§5 over-eng 点検結論参照）
- **手動先行・機械化後追い**: 同型判定の自動化（L2）は、Phase 0 の手動蒸留で「人間が同型と認定した実例」（今回の高同型9件が初期正例）を貯めてから。correction_semantic の個人辞書と同じ育て方
- **発火設計**: 型スキルは自動発火に頼らない（抽象スキルは recall 0% 頭打ちの実測あり）。蒸留時に PJ スキルとの双方向参照を張る作業を必須手順とする
- **プラグイン機能との境界**: 機構（ストア・hook・CLI）はプラグイン、判断規範（何を焼き戻すか・粒度・書式）は型スキル

### 未解決

1. **型スキルの置き場所**: ~/.claude/skills（現行 explainer-script-method 方式・軽い）vs 専用プラグイン（版管理・配布に強い）vs evolve-anything 同梱。まず global で運用開始し、数が増えたらプラグイン化が現実的か
2. **ADR-025（vector 非採用）との関係**: L2 を vector でなく LLM バッチ判定で実装すれば ADR-025 は維持できる。「semantic recall が常用ニーズになったら再検討」トリガーには本構想が該当し始めている
3. **Epic #78（daily-evolve）への接続**: Phase 2 (#81) / Phase 3 (#82) の中身として位置づけるか、独立 Epic を切るか
4. **同型判定の precision 計測の具体設計**（方針は決定済み、測り方の詳細が未設計）

## 7. 関連 issue / ADR

- daily-evolve Epic #78（Phase 2 #81 / Phase 3 #82 = 本構想の接続候補）
- skill_extractor 系: #238 / #291 / #381 / #387 / #27（4軸分解 = L2 の写像スキーマ）
- cross_pj_priority #462 / idiom_filter #527 / multiview_eval #564 / relevance_gate #565 / paired_trajectory #15
- ADR-025（keyword-only recall）/ ADR-037（2相）/ ADR-047（人間承認）/ ADR-049（write barrier）

## 8. 外部レビュー（Gemini, 2026-07-06）指摘と対応

| 指摘 | 判定 | 対応 |
|---|---|---|
| 抽象化によるトークン浪費・降格機構の欠如 | 妥当 | 降格は新設せず negative_transfer gate + rollback (#10) を型スキルへ適用（§5 Phase 3 に追記）。型スキル本文は Progressive Disclosure で常駐させない |
| L2 バッチのコスト爆発・偽陽性 | 妥当（設計済み） | §2「L2 enqueue 4段ファネル」参照。raw ログを比較せず蒸留済み資産のみ、スロット分割 + daily cap |
| 型スキルのバージョンピン | **不採用** | この環境には versionless stale の実害 pitfall があり、pin は silent stale の温床。代替 =「PJ 別 outcome 監視で当該 PJ の悪化を検出 → PJ 側に opt-out 節」（explainer-script-method の PJ 固有差分の流儀と一致） |
| constitution 配線のエコーチェンバー | 妥当 | 「型スキルの定期棚卸し（人間の反証レビュー）」を §5 Phase 3 に追加。ADR-047 人間ゲートと併用 |
| precision 計測 = 正解/不正解ペアのテストコーパス | 妥当 | 高同型9件 = 初期正例。負例（絶対に同型でないペア）の整備を Phase 0 の副産物として貯める |
| ファネル優先度 (a) の死角 = jaccard≈0 の真の同型が落ちる | 妥当 | §2 に反映: (a)(b)(c) は OR・max 結合。(b)(c) の痛みシグナルが立てば jaccard≈0 でも強制 enqueue |
| 型スキル同士のコンフリクト（constitution の分裂・矛盾） | 妥当（Phase 1 で設計） | 型スキル間の矛盾検出は agent_team（役割重複の決定論検出）の型スキル版として実装可能。調停は「PJ 側 opt-out 節が最終勝者」の単純規則から始める |
| 人間ゲートのラバースタンプ化 | 妥当 | §9 新設（流量制御・可逆性・形骸化の自己計測） |

## 9. 人間ゲートの UX 設計（ラバースタンプ化対策）

「読まずに Approve 連打」への対策は UI 装飾でなく、この環境で実測済みの原則を積む。

1. **流量制御が第一防衛**: 提案は daily cap 最大5件/日（daily_review の実運用値を踏襲）。レビュー疲れは表示の工夫でなく量で防ぐ
2. **判定+行動を最上部・証拠は従属**: 「レポートは行動しか読まれない」は 2026-07-04 に実測済みの学習（report-action-first）。1件 = 1問「PJ A の観点 X と PJ B の観点 Y を型スキル Z へ統合しますか？」+ 統合後文面の diff プレビュー + 実物引用は details 従属
3. **根拠は実物引用（file:line）、LLM 要約は不可**: ground-truth-over-summary（型スキル候補3）を蒸留パイプ自身に適用。representative の user_only_text（assistant 引用 strip）と同思想
4. **機械の事前反証で人間の役割を「検証」から「拒否権」へ**: multiview_eval 4視点（過学習疑い/退行リスク等）+ second-opinion の adversarial pre-check を通過した提案だけ人間に出す。反証で死んだ候補は observability に件数 surface（silence≠evaluated）
5. **可逆性で承認の重みを下げる**: 承認 = 仮採用。revoke レバー（`--revoke-idiom` 同型）+ negative_transfer 降格ゲートが事後安全網。誤承認が安価に巻き戻せる構造なら、ラバースタンプが起きても被害は限定される
6. **形骸化の自己計測**: accept 率の長期 100% 張り付き・即答連続を advisory で「ラバースタンプ疑い」として surface（optimize_history_store の accept/reject 履歴を流用）。形骸化も観測対象にする
7. **定期棚卸しは全件 y/n でなく「反証1件方式」**: agent に最強の反証候補を1つ選ばせ、人間はその1件だけ深く判断する。網羅レビューを人間に求めない
