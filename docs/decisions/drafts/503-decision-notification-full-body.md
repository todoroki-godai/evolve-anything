# #503 実装前設計 — 判断を求める通知は digest に畳まない

- **対象 issue**: #503（通知が2件以上あると改善案の本文が捨てられ利用者に届かない）
- **改訂する契約**: ADR-054 Phase 0 §3 仕分け表 #7 / §4.2 / §4.4（`docs/decisions/drafts/054-phase0-notification-routing.md`）
- **状態**: rev3（codex round1/round2 + tacchi 反映済み・実装着手可）
- **日付**: 2026-08-18

## 0'. codex round2 + tacchi 対応表（rev3）

| 指摘 | 出所 | ラベル | 対応 | 反映先 |
|------|------|--------|------|--------|
| §3.1-5（producer 側 truncate + 改行正規化）は #503 のスコープ外。`build_proposal_prompt`（additionalContext）は生の代表文を使うため、user 可視本文だけ短縮すると**両チャネルが不一致**になる | codex r2 | [Must] | **採用（削除）**。§3.1-5 を本設計から外し、別 issue に切り出す。行長が構造的に無上限なのは既存状態のまま（ADR-054 §4.4(c) が icebox reason で同じ裁定を済ませている） | §3.1, §7 |
| `_truncate(t, 200)` は実際には最大201字。`all_representatives` の件数は無上限のまま | codex r2 | [Must] | **解消**（上記削除により該当なし） | — |
| rev2 の未実測前提（200字で足りるか・改行正規化の影響・チャネル間の一貫性） | codex r2 | [Must] | **解消**（上記削除により該当なし）。別 issue 側で測る | §7 |
| §5-5「item を先頭/中間/末尾に置く」は fixture の変化であって陰性試験ではない | codex r2 | [Must] | **採用**。「先頭の decision item だけ処理する」実装差し替えの mutation に置換 | §5-5 |
| §5-6 は §4 の不変条件だけでは赤くならない（二重 prefix でも本文は1回含まれる） | codex r2 | [Must] | **採用**。不変条件を segment 単位の等値比較へ強化 | §4 |
| §4 は前置き・後置きを混ぜる書き換えでも通る | codex r2 | [Must] | **採用**。**最終 systemMessage 全体を期待文字列と等値比較**する契約に変更 | §4 |
| ADR-054 本文の改訂を実装と同じ変更単位で必須にすること | codex r2 | [Should] | **採用**。§3.3 を実装の完了条件に格上げ | §3.3 |
| §5-4 の mutation が曖昧 | codex r2 | [Should] | **採用**。具体的な削除箇所と期待値を固定 | §5-4 |
| suffix 2種（`（ほか: …）`・`→ /evolve-anything:queue で開始`）の位置が未規定。現実装では**本文の後ろ**に付き、CTA が改善案の導線に誤読される | tacchi | [Should] | **採用**。結合順序を「Tier1 → 予算内 Tier2 → （ほか: …） → tail 導線 → decision 本文」に確定 | §3.1-2 |
| 本文を出しても、**y/n が来なかったときに利用者が取れる手段がゼロ**。E8 の苦情は正確には「提示が無かった」なので同じ苦情が再発しうる | tacchi | [Should] | **採用**。本文末尾に pull 導線を1句足す（§3.1-5'）。producer の**文言追加のみ**でチャネル間の意味不一致を生まない（削除した §3.1-5 とは別種） | §3.1-5' |
| E7 / §3.1-3 の所在が誤り。NotificationItem 構築は `restore_state.py:213-219`、`collectors.py` 側は dict の組み立て | tacchi | [Should] | **採用**（頭が実コードで確認）。2ファイルに分けて記述 | §2 E7', §3.1 |
| §3.1-4 の行番号は 559-568 が正しい | tacchi | [Nit] | **採用** | §3.1-4 |
| 既存文言の主語欠落（「応答のあとで」は誰の応答か）・非表示時の但し書きの常駐 | tacchi | [Nit] | **見送り**（既存文言・#503 の不変条件と無関係）。§7 に記録 | §7 |

## 0. codex round1 対応表

| 指摘 | ラベル | 対応 | 反映先 |
|------|--------|------|--------|
| E6「フル文は代表文2件分で頭打ち」は誤り。`all_representatives` は無上限に列挙する | [Must] | **採用**。E6 を撤回し実 PJ 9件で再実測（§2 E6'）。さらに `representative` が一切 truncate されていない事実（`proposal_digest.py:144`）を受け、producer 側に既存 `_truncate` 慣習（200字）と改行正規化を入れて上限を作る | §2, §3.1-5 |
| E1/E2 は JSONL を構造解析せず全文 grep しており「user 可視通知282回中131回」を証明できない | [Must] | **採用**。頻度の主張を撤回し「**測定不能**」と明記（§2 E2'）。構造抽出を試みたが transcript 側が `Output too large` で本文を切っており計数不能であることまで実測した。設計の根拠は頻度でなく構造（§2 E2''）へ移す | §2 |
| E3/E4 は単一サンプルを代表値として使っている | [Must] | **採用**。実 PJ 9件（提案がある6件）で再実測。max 224字 / median 174字（§2 E3'） | §2 |
| `needs_decision` は量と詳しさの両方を上書きするので「独立した第3軸」ではない。tier2 の定義改訂と優先順位の明記が必要 | [Must] | **採用**。「第3の軸」という表現を撤回し、**優先順位規則**として定義し直す（§3.0） | §3.0, §3.3 |
| 陰性試験1と4は計画どおり赤くならない（digest 自体をフル本文に変えるため、フラグを外しても400字以内なら本文が出る） | [Must] | **採用（設計変更）**。`digest` は `改善案N件` のまま維持し、本文は**専用フィールド `decision_text`** に載せる。これでフラグ／配線を外すと表示が `改善案N件` に戻り、長さに依存せず赤くなる | §3.1, §5 |
| 陰性試験3はトートロジー | [Must] | **採用**。「常に `items[0].text` を返すスタブ」を廃し、順序変異・切り詰め変異・digest 差し戻し変異に置換 | §5 |
| 守るべき不変条件が「部分一致」で通ってしまう | [Must] | **採用**。不変条件を「producer 出力（prefix 除去後）が最終 systemMessage に**完全一致でちょうど1回**含まれる」と定義し、producer→最終 JSON の E2E で検査する | §4, §5 |
| `digest` をフル本文へ転用するとモデル契約を破る。既存テストも壊れる | [Should] | **採用**（上と同じ変更で解消）。`digest` の意味は変えない | §3.1 |
| prefix 除去方法が未規定 | [Should] | **採用**。`removeprefix` に固定し、prefix が無い場合の扱いと完全一致テストを契約化 | §3.1-3 |
| 「Tier1 合計は約40字」は単一観測 | [Nit] | **採用**。E5 を「観測例」と明記し、正しさの前提から外す（構造的に予算外へ置くため） | §2 E5' |

## 1. 問題

`_merge_notification_text`（`scripts/lib/session_notify/merge.py:12-49`）は発火系統が
2件以上のとき各 item の `digest` だけを結合する。session_proposal の digest は
`f"改善案{len(groups)}件"`（`scripts/lib/session_notify/collectors.py:583`）なので、
**朝の y/n で何を聞かれるのかが利用者の画面から消える**。

## 2. 実測（evidence）

| # | 前提 | 実測値 | 取得コマンド / 出所 | 取得日 |
|---|------|--------|--------------------|--------|
| E1 | 畳まれたバナーの実文字列 | `[evolve-anything] 記録待ち提案1件（evolve --drain） / judge持ち越し6582件（自動） / evolve待ち5PJ / 改善案2件 / icebox59件・最古35日 → /evolve-anything:queue で開始`（132字）。**この文字列が実在することの確認にのみ使う。出現回数は根拠にしない**（E2'） | `find ~/.claude/projects -name '*.jsonl' -mtime -30 -print0 \| xargs -0 grep -ho '\[evolve-anything\][^"\\]\{0,80\}' \| grep 改善案` | 2026-08-18 |
| E2' | 本文が捨てられる**頻度** | **測定不能**。transcript 側の hook 出力が `Output too large (12.2KB). Preview (first 2KB)` で切られており、構造抽出できた改善案入り systemMessage は SessionStart hook 86 イベント中 **1件**のみ。persisted stdout（65ファイル）にも0件。生 grep の 282件は会話中の引用・再掲を含み計数根拠にならない | 上記 grep + `attachment.hookEvent == "SessionStart"` の構造パース + `tool-results/hook-*-stdout.txt` の全走査 | 2026-08-18 |
| E2'' | 頻度に代わる構造的根拠 | ADR-054 §4.1 が「**2件以上の同時発火が通常経路**」と実測で結論済み（同 ADR §9「Phase 0 は平常時0〜1件発火前提／実測は4系統・412字」）。現行コードでは発火2件以上で proposal は必ず digest 化される（`merge.py:24-25` の分岐に例外が無い）ため、**畳みは確率事象ではなく、2件以上発火時の確定挙動** | `docs/decisions/054-four-pillars-completion-design.md:942` / `merge.py:24-40` | 2026-08-18 |
| E3' | 提案フル文の長さ（実 PJ 分布） | 提案がある6 PJ で **max 224字 / median 174字**（zundamon-explainer 224 / figma-to-code 213 / updater-index 174 / evolve-anything 161 / amamo 137 / juken 117。いずれも groups=2・reps=2） | 実 `evolve-queue.json` に対し `build_proposal_systemmessage(build_session_proposals(...), pj_slug=s)` を全 PJ 分実行 | 2026-08-18 |
| E4' | 本文の長さは**構造的に無上限** | `representative` は truncate されていない（`proposal_digest.py:144`）。実データに改行入りの代表文が存在（`「… 再委譲。既存の変更はありません）\n\n【タスク】skill_triage」`）＝現行の1件発火時パスでも既にバナーが複数行に割れうる | `proposal_digest.py:144` / 上記 grep 出力 | 2026-08-18 |
| E5' | Tier2 予算 | `TIER2_BUDGET_CHARS = 400`（`merge.py:9`）。Tier1 合計は**観測例**で約40字（E1）。ただし本設計は decision 本文を構造的に予算外へ置くため、**この観測値に正しさを依存させない** | `merge.py:9` | 2026-08-18 |
| E6' | 提案 group の上限 | `MAX_SESSION_PROPOSALS = 2` が制限するのは **group 数のみ**。各 group の `all_representatives` は無上限（`proposal_digest.py:757-765`）。今日の実データは全 PJ で reps=2 だが、上限として使えない | `proposal_digest.py:38,757-765` | 2026-08-18 |
| E7' | 現行の tier と**所在** | session_proposal は `tier=2`。`NotificationItem` の構築は **`hooks/restore_state.py:213-219`**、`collectors.py` 側（`_build_session_proposal_output`）は `systemMessage`/`digest`/`hookSpecificOutput` を持つ dict を返すだけ（`collectors.py:581-588`）。**変更は2ファイルにまたがる** | `hooks/restore_state.py:213-219` / `collectors.py:581-588`（頭が実コードで確認） | 2026-08-18 |
| E8 | 「Claude が AskUserQuestion するから Tier2 でよい」（ADR-054 §3 [Nit-t6]）の成否 | **破綻を実測**。2026-08-18、利用者が「『案：続けて』っていう提示もなかったよ？」と報告。ADR 自身も「prompt instruction 遵守は機械的に保証できない」と自認（`proposal_digest.py:739-742`） | 本セッションの利用者発話 / 当該 docstring | 2026-08-18 |

**測定不能と明示するもの**:
- 「行が長くなると読まれなくなるか」→ 利用者の可読性を測る手段が無い。ADR-054 §4.4 の既存裁定「情報消失より横長を選ぶ」に従う。
- 「畳みが起きた頻度」→ E2'（transcript 側の切り詰めにより計数不能）。

## 3. 設計

**原則**: 利用者に判断を求める通知は、詳しさの軸（digest / フル文）の対象外にする。
「表示予算に収まらないので中身を捨てる」設計と「判断を求める」機能は両立しない。

### 3.0 既存2軸との関係（codex [Must] 反映）

`decision_text` は ADR-054 の2軸（tier＝量 / digest・フル文＝詳しさ）と**独立した第3の軸ではない。
両方に優先する規則**である。契約を次のとおり改訂する:

1. **`decision_text` を持つ item は、発火件数にかかわらず digest 化されない**（詳しさの軸に優先）。
2. **`decision_text` を持つ item は、Tier2 予算の計算対象外であり、overflow に落ちない**（量の軸に優先）。
3. したがって **tier2 の定義は「`decision_text` を持たない場合に限り、予算超過時に落としてよい」** と改める。
4. `decision_text` を持つ item は tier の値によらず上記1・2が適用される（現状の唯一の保持者 session_proposal は `tier=2` のまま。tier の値を変えると「量の軸」の意味が変わるため触らない）。

### 3.1 変更点

1. `NotificationItem` に **`decision_text: str | None = None`** を追加（`model.py`）。
   意味は「この通知は利用者に判断を求める。この文字列がその材料であり、**短縮も省略もしない**」。
   **`digest` の意味は変えない**（`改善案N件` のまま維持）。
2. `_merge_notification_text`（`merge.py`）:
   - `decision_text` を持つ item は Tier1/Tier2 の digest 集合から**外す**（`改善案2件` と本文の二重掲出を避ける）。
   - 予算計算に**含めない**。overflow ラベルにも入れない。
   - **結合順序を確定する（tacchi [Should]）**:
     `[evolve-anything] ` + Tier1 digest → 予算内の Tier2 digest → `（ほか: …）` → `→ /evolve-anything:queue で開始`
     → **decision_text 群**（発火順）。
     suffix 2種を decision 本文より**前**に置くのが要点。現実装は両 suffix を行末に付けるため、
     そのままだと `→ /evolve-anything:queue で開始` が改善案本文の直後に来て
     「改善案の採否は queue コマンドでやるのか」と誤読される（tacchi 指摘）。
   - 発火1件のときの挙動（`items[0].text` をそのまま返す）は**変更しない**。
3. `hooks/restore_state.py:213-219`（NotificationItem 構築側）で
   `decision_text=proposal_output.get("decision_text")` を渡す。
   `collectors.py:581-588`（dict 組み立て側）で
   `"decision_text": system_message.removeprefix(_PREFIX)`（`_PREFIX = "[evolve-anything] "`）を返す。
   `removeprefix` は prefix が無ければ**原文をそのまま返す**（例外にしない・fail-open）。
   merge 側が `f"[evolve-anything] {body}"` を付け直すため、これで二重 prefix を防ぐ。
4. **提案が0件のときの短い notice**（`collectors.py:559-568`「今日の新規提案はありません」）は
   `decision_text` を返さない。判断を求めていないため。
5. **（削除）** rev2 にあった producer 側 truncate + 改行正規化は **#503 のスコープ外**として外した
   （codex r2 [Must]）。`build_proposal_prompt`（additionalContext）が生の代表文を使うため、
   user 可視本文だけ整形すると両チャネルが不一致になる。行長が構造的に無上限（E4'）なのは
   **既存状態のまま据え置き**であり、本変更で新たに悪化するものではない（1件発火時パスでは既に無上限）。
   別 issue に切り出す（§7）。
5'. **pull 導線の1句を足す（tacchi [Should]・in-scope）**: `build_proposal_systemmessage` の
   本文末尾に「聞かれなければ『改善案を教えて』と言ってください。」相当の1句を追加する。
   E8 の苦情は正確には「**提示が無かった**」であり、本文が届いても
   y/n が来なかったときに利用者が取れる手段が現状ゼロだから。
   これは**文言の追加のみ**で、削除した5とは違い代表文を加工しないため
   additionalContext との意味不一致を生まない。

### 3.2 変更しないもの

- `additionalContext`（Claude 向け・常時フル）の内容と経路。
- `TIER2_BUDGET_CHARS` の値。
- `NotificationItem.digest` の意味と、pending_trigger / icebox レーン1 の既存 digest 免除。
- AskUserQuestion の提示内容（#498 で配線済み）。
- session_proposal の `tier=2`。

### 3.3 ADR-054 への反映

`docs/decisions/drafts/054-phase0-notification-routing.md` に追記する:
- §3 仕分け表 #7 の理由欄に「**[Nit-t6] の Tier2 正当化は 2026-08-18 に撤回**（#503・E8）」を追記。
- §4.2 に §3.0 の優先順位規則1（digest 化されない）を追記。**digest 免除の3件目**だが、
  pending_trigger（本文消失）・icebox レーン1（個別列挙契約）とは理由が異なることを明記。
- §4.4 の実効上限契約 (a)(b)(c) に **(d)「`decision_text` は予算外・overflow 対象外」** を追加し、
  tier2 の定義を §3.0-3 のとおり改訂。

## 4. 守る不変条件（codex round2 [Must]3件を反映して強化）

rev2 の「本文が完全一致でちょうど1回**含まれる**」は弱すぎる。包含条件は
①二重 prefix（`[evolve-anything] [evolve-anything] <body>`）でも真になり、
②`decision_text = "前置き" + body + "後置き"` のような混入でも真になる。
したがって**包含でなく等値**で契約する:

> **I1（全体等値）**: 最終 systemMessage は、
> `"[evolve-anything] " + " / ".join(digest 群 + overflow suffix + tail 導線) + " " + " ".join(decision_text 群)`
> を期待値として組み立てた文字列と **完全一致**する（同時発火数・Tier1 合計長・発火順・予算にかかわらず）。
> 区切り文字と順序まで含めて固定する。
>
> **I2（segment 等値）**: 最終文字列から decision segment を機械的に切り出したとき、
> それは producer が返した `decision_text` と**境界込みで等値**である（前置き・後置き・切り詰めが無い）。
>
> **I3（prefix 一意）**: 最終文字列に `[evolve-anything] ` は**ちょうど1回**しか現れない。

I1 があるので I2/I3 は本来冗長だが、**I1 が期待値の組み立てごと壊れる書き換え**
（テスト側が実装と同じ関数で期待値を作ってしまう自己参照）を検出するために、
I2/I3 は**実装を呼ばずに literal で書いた期待値**で独立に検査する。

## 5. テスト方針（verify-checks-by-breaking）

契約テストは `hooks/tests/test_restore_state_notification_contract.py`、
E2E は producer→最終 JSON を通す既存 E2E テスト群に追加する。

**陽性対照（緑のままであるべき・陰性試験と混ぜて数えない）**:
- 発火1件のときフル文がそのまま返る（既存テスト維持）。
- `decision_text` を持たない item だけの結合結果が現行と**一字も変わらない**（既存 golden 維持）。
- item の `label` だけを変えても結合結果の本文が変わらない（意味を変えない書き換えで誤検出しない）。

**陰性試験（赤になるべき）**。各件に「壊す不変条件」と「通したい検査経路」を明記すること。
以下6件は**下限であって網羅ではない**:

1. **要素を消す**: `collectors.py` の返り値から `decision_text` キーを落とす。
   壊す不変条件=I1／経路=結合結果が `改善案N件` に戻る。**本文長に依存せず赤**。
2. **意味を壊す（予算）**: decision item を予算計算に含め、超過時に overflow へ落とす。
   Tier1 合計が 400字を超える fixture を**明示的に用意**する。壊す不変条件=I1（予算非依存）。
3. **意味を壊す（切り詰め）**: merge が `decision_text[:80] + "…"` を結合する実装に差し替える。
   壊す不変条件=I2。**包含検査しか無ければ緑のまま通る**ことの確認を兼ねる。
4. **配線を外す**: `merge.py` の decision 分岐**全体**を削除し、`decision_text` を持つ item も
   従来どおり `digest` で結合する実装に戻す（部分削除でなく分岐まるごと）。壊す不変条件=I1。
5. **意味を壊す（複数件）**: merge を「**decision item は先頭の1件だけ結合する**」実装に差し替える。
   decision item を2件持つ fixture で赤。壊す不変条件=I1。
   （rev2 の「item を先頭/中間/末尾に置く」は fixture を変えるだけで実装を壊しておらず、
   正しい実装なら全ケース緑＝陰性試験になっていなかった。codex r2 [Must] を反映して差し替え）
6. **prefix 除去を外す**: `collectors.py` の `removeprefix` を素通しにする。
   壊す不変条件=I3（および I1）。**I1/I3 が無く包含検査だけなら緑のまま通る**。
7. **混入**: `collectors.py` が `decision_text = "【要確認】" + body` を返す。
   壊す不変条件=I2。**包含検査だけなら緑のまま通る**（codex r2 [Must] が指摘した抜け穴の直接検査）。

**実装者が自分で選ぶ別種2件以上を必ず追加する**（上の6件と種類が重ならないこと）。
境界値・表現差（空白／Unicode／改行／エスケープ／巨大入力）、実行文脈（順序／並行／権限）、
鮮度（キャッシュ・古い成果物）のクラスを探索し、**探索したクラスと結果を全件列挙する**。
緑のまま残った変異が1件でもあれば完了扱いにしない。

## 6. この設計が依拠する前提のうち実測で裏取りされていないもの

- 「行が長くなっても利用者は読む」→ **測定不能**（§2 末尾）。既存裁定（ADR-054 §4.4「情報消失より横長」）に従う。
- 「畳みが起きた頻度」→ **測定不能**（E2'）。頻度でなく構造（E2''）を根拠にする。
- 「pull 導線の1句（§3.1-5'）で、y/n が来なかったときに利用者が実際にそれを使うか」→ **未実測**。
  使われたかを測る手段が現状無い。効果検証は行わず、手段がゼロである状態の解消のみを目的とする。

## 7. 本設計のスコープ外（別 issue に切り出す）

1. **producer 側の本文長の上限**: `representative` は truncate されておらず（E4'）、
   改行入りの代表文が実在する。user 可視バナーが複数行に割れうる。
   ただし `build_proposal_prompt`（additionalContext）は生の代表文を使うため、
   **片側だけ整形すると両チャネルが不一致になる**（codex r2 [Must]）。両チャネル同時の設計が要る。
   → #503 とは別 issue。
2. 既存文言の主語欠落（「応答のあとで採否をお聞きします」は誰の応答か）と、
   表示されている読者に「表示されなかった場合は…」の但し書きが常駐している違和感（tacchi [Nit]）。
