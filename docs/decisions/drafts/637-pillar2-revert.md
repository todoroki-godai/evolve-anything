# 637 柱2が「取り消された反映」を数え続ける — 設計

対象 issue: #637
起草: 頭（Claude / Opus 5）・2026-09-10
状態: 設計レビュー発注前（round 0）

---

## 0. 完成条件（round 0・定型）

① **守る対象**: 柱2「実際に反映された改善（直近30日）」の件数が、**後で取り消された反映を数え続ける**こと。
   数字が実態と食い違ったまま、それに人間が気づけない状態を防ぐ。

② **信頼境界（誰の能力を脅威に数えるか）**: **自分たちの運用ミスのみ**。
   反映した rule を後で revert する／反映先ファイルを消す／別 PJ の checkout が手元に無い、等。
   悪意ある改変・意図的な数字の水増しは脅威に数えない。

③ **対象外**:
   - 柱1・柱3・柱4 の測定
   - `corrections.jsonl` の writer 協調（#595）
   - 新ストア・新 observability section の追加（#379 新設凍結を継続。**既存ストア
     `reflect_apply_events.jsonl` への追記と、既存 health フィールドの追加にとどめる**）
   - `optimize_history` 側の revert（`fold_effective` は既に存在し、本件と無関係）
   - 「反映が今も現存するか」を文字列で機械判定すること（§2 の実測により不採用）

④ **blocking の定義**:
   - (a) 取り消したことが記録できる手段が無い（人間が知っていても数字を直せない）
   - (b) 取り消しを記録したのに、柱2の件数から畳まれない
   - (c) 柱2の表示・仕様文が「取り消しを追跡している」と読める（実態は申告ベース）
   - (d) 取り消しイベントが、対応する反映イベントを持たないまま黙って無視される
       （記録したのに効かない＝(b) の裏側。health に出ないこと自体を blocking とする）
   - (e) 既存の反映イベント（取り消していないもの）が畳まれる＝過少カウント

⑤ **検証方法**: (a)〜(e) 各1件以上の陰性試験（赤になるべき変異）＋**陽性対照**
   （取り消していない反映が畳まれないこと・正常データで health が degraded にならないこと）。
   変異は「(a) 守る不変条件を壊す差分がある」「(b) その分岐が当該テストの実行で読まれた」の
   両方を機械で確かめてから陰性試験に数える。
   委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する。
   **緑のまま残ったものが1件でもあれば完了扱いにしない。**

⑥ **この成果物が目的文の物差しで削る量**:
   目的文は CLAUDE.md「効果は週1の数字で実感」＝**柱2の件数が実態と一致していること**。
   同じ単位（誤カウント件数）の直接観測値:
   - **現在の誤カウント: 1件 / 有効反映イベント 26件**（2026-09-10 実測）
   - 取得コマンド:
     ```
     python3 -c "import json,os;p=os.path.expanduser('~/.claude/evolve-anything/reflect_apply_events.jsonl');\
rows=[json.loads(l) for l in open(p) if l.strip()];\
print(sum(1 for r in rows if r.get('event_type')=='correction_applied'))"
     ```
     → `26`（2026-09-10T取得）。誤カウント1件は `target_correction_id`
     `34cc2398ee4c4f79b1d54f409d59e23e`（`~/.claude/rules/explain-clearly.md` への反映を
     `claude-config` の `bd1a93a` で revert 済み・#637 本文の実害記録）
   - **削る量: 1件（3.8%）**。将来分は未測定なので 0 と数える。

---

## 1. いま起きていること（根因）

- `scripts/lib/reflect_fold.py:152` が受け付ける `event_type` は
  `correction_apply_attempted` / `correction_applied` の2つだけ。**取り消しを表す遷移が無い**
- `scripts/lib/pillar2_metrics.py` の `count_applied_reflections` docstring は
  「反映先の存在は記録時点の事実であり、その後の削除・変更は追跡しない」と明記している。
  つまり**現在の挙動は意図された設計**であり、バグではなく**仕様と柱2の定義の食い違い**
- CLAUDE.md は柱2を「**実際に反映された改善**」と定義し、`report-by-four-pillars.md` は
  「申告ベースは照合が無いので使わない」と定めている。**照合したのは「反映した瞬間」だけ**で、
  「今も残っているか」は照合していない

---

## 2. issue #637 の推奨案（案2＝read 時に反映先を照合する）は成立しない（実測）

issue 本文は「集計時に反映先ファイルへ当該行が今も実在するかを見て、無ければ畳む」を推奨していた。
**実データで測ったところ、この方式は動かない。**

### 2.1 測定

対象: `~/.claude/evolve-anything/reflect_apply_events.jsonl` の
`correction_apply_attempted` 31件（2026-09-10 取得・ファイル 82行）。
`reflect_target_path`（`<repo_id>:<相対パス>` 形式）を絶対パスへ戻し、
`reflect_draft_line` の先頭 L 文字（NFC 正規化・空白畳み）が現ファイル本文に含まれるかを見た。

再現スクリプト（全文）:

```python
import json, os, unicodedata
from pathlib import Path
p = os.path.expanduser("~/.claude/evolve-anything/reflect_apply_events.jsonl")
rows = [json.loads(l) for l in open(p) if l.strip()]
def resolve(np):
    for i, ch in enumerate(np):
        if ch == ":" and Path(np[:i]).is_dir():
            return Path(np[:i]) / np[i + 1:]
    return Path(np)
def norm(s):
    return unicodedata.normalize("NFC", " ".join(s.split()))
for L in (40, 60, 80, 120, 200, 10**9):
    ok = ng = 0
    for r in rows:
        if r.get("event_type") != "correction_apply_attempted":
            continue
        f = resolve(r.get("reflect_target_path") or "")
        d = (r.get("reflect_draft_line") or "").strip()
        try:
            text = norm(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = norm(d)[:L]
        (ok := ok + 1) if key in text else (ng := ng + 1)
    print(L, ok, ng)
```

取得日: 2026-09-10。

| 先頭 L 文字で照合 | 「現存」と判定 | 「不在」と判定 |
|---|---|---|
| 40 / 60 / 80 / 120 | 28 | 3 |
| 200 | 26 | 5 |
| 全文一致 | 24 | 7 |

### 2.2 判定（この方式を採らない理由）

- **本件の実害（#637 が起票された唯一の事例）を L≦120 では検出できない**。
  revert されたのは `explain-clearly.md` の1項目に**後から足した節**であり、
  行の先頭部分（`- **読み手は社長**。結論を1行目に置き…`）は今も残っている
- **L を伸ばすと偽陽性が出る**。L=200 で「不在」と判定された5件のうち少なくとも3件は、
  **取り消しではなく、rules 圧縮で言い回しが書き換わっただけ**の項目
  （例: `**表を出したら表だけで終えない**` は現在
  `**選択肢や推奨の根拠を表で示したら、表だけで終えない**` として実在する）。
  これは完成条件 ④(e)（過少カウント）に正面から該当する
- **どの L でも、取り消しと書き換えを分離できない**。文字列の一致・不一致は
  「その反映が今も生きているか」の同一性に対して閉じていない。
  `no-denylist-checks.md`（当PJ）と `verify-checks-by-breaking.md`（共通）の
  「**文字列で同一性を判定する検査は blocking 保証に使わない**」に該当する。
  柱2の件数は表示の正典なので、この判定は実質 blocking である

**結論: 案2 は不採用。** 実測の表とスクリプトを残し、再検討時の資産とする。

---

## 3. 採用案（R2・巡1 の指摘を反映した最小構成）

R1（A+B）は codex 設計レビュー巡1 で `設計修正要`（[Must] 6件 / [Should] 6件）。
Q5 で提示された、より機構の少ない構成を採る（`think-before-coding.md`「機構を足す前に減らす」）。

**最小構成は4点**: ①既存ストアに取り消しイベント1種 ②柱2 reducer を target ごとの2状態へ
③既存表示に固定1行 ④read-only の「いま有効な反映」一覧＋取り消しコマンド。
**新ストア・新 observability section・文字列同一性検査は作らない。**

### 3.1 取り消しイベント（既存ストア `reflect_apply_events.jsonl` に1種だけ追加）

| フィールド | 内容 |
|---|---|
| `event_type` | `correction_reverted` |
| `schema_version` | `1`（既存2種と同じ） |
| `correction_id` | このイベント自身の不変 ID |
| `reverts_applied_id` | 打ち消す `correction_applied` イベントの `correction_id`。**参照の正典はこれ1つ** |
| `reverted_at` | ISO8601（tz 必須） |
| `revert_reason` | 1行・空不可。**監査用であって状態解決には使わない** |

**`target_correction_id` は持たせない**（巡1 Q5）。target は参照先の `correction_applied` から
導出する。二重に ID を持つと「applied はあるが target が不一致」という状態と検証分岐が増える。

`correction_reverted` の validator（不正値は既存 `invalid_events` へ接続）:
`correction_id` / `reverts_applied_id` が正当な correction ID、`reverted_at` が tz-aware、
`revert_reason` が空でない1行、`schema_version == 1`。

### 3.2 reducer（`reflect_fold.fold_corrections`）— target ごとの2状態へ畳む

target ごとに、時刻順（`(timestamp, correction_id)`・既存 `latest_pair` と同じ全順序）で
`applied` / `reverted` の2状態を遷移させる。

- **revert は CAS（compare-and-set）**: 参照先 `reverts_applied_id` が **その時点で有効な最新の
  `applied`** であるときだけ状態を `reverted` に閉じる
- **過去の applied は復活しない**。`A1 → A2 → revert(A2)` は無効（A1 は戻らない）
- **revert より後の新しい `applied` だけが再反映になる**。`A1 → A2 → revert(A2) → A3` は A3 が生きる
- **revert 済み target は reconciliation の対象外**。現行 `reflect_fold.py:279-296` は
  confirmation が無くても base の `reflect_status == "applied"` なら最新 attempt から柱2情報を
  再構成するので、これを通ると取り消した target が `reconciled=True` で復活する（巡1 [Must]）
- **曖昧な状態は数えず health に出す**（巡1 [Must]・④(e) を過少にも過大にも倒さない）:
  - `reverted_at` が参照先の `reflect_applied_at` より前（時刻の逆転）
  - `applied` と `reverted` の時刻同着
  → その target は件数に含めず `ambiguous_reverts` を加算する。
  **ランダムな ID の大小で業務状態を決めない**

### 3.3 health（既存の柱2 payload にフィールドを足す。新 section は作らない）

| キー | 意味 |
|---|---|
| `stale_reverts` | 参照先が不在／validation 無効／その時点で有効でない／同じ applied の二重取り消し |
| `ambiguous_reverts` | §3.2 の時刻逆転・同着 |

- どちらも `pillar2_metrics.py` の `degraded` 条件（現行は列挙式・`:262-278`）に接続する
- **読者向けの表示理由にも接続する**。`test_results_board.py:993-1024` は全数値キーに
  表示理由を要求しているので、これを満たす
- revert イベント自身の ID 重複は既存 `duplicate_event_row_count` が扱う（新設不要）

### 3.4 CLI（`bin/evolve-reflect`）

- **`--list-applied`**（read-only）: いま有効な反映を、`applied_id` / 反映先 / 反映日時つきで印字する。
  現在の柱2 `applied_list` は kind/path/time しか返さず ID を出さないため、
  **人間が取り消しを知っていても操作できない**（巡1 [Must]・④(a)）
- **`--revoke <applied_id> --reason "<1行>"`**: 同じストアの **lock 内で
  「その applied がいま有効な最新か」を再確認してから追記する**。
  読取→追記の間に新しい `applied` が入っていたら**書かずに `retry-required` で終了**する
  （成功表示のまま取り消しが効かない状態を作らない・巡1 [Must]）
- **`--revoke` は `--apply` / `--skip` / weak decision 系フラグと相互排他**にする
  （現行 parser で相互排他なのは weak decision の3フラグだけ・`reflect.py:1071-1089`）
- `evolve-revert`（skill diff の巻き戻し器）とは**統合しない**。対象もストアも別物

### 3.5 表示（ゼロ機構）

- 柱2の行に固定の1行「**取り消しは申告があったぶんだけ反映**」を常時足す。
  **`revert_tracking` のような新しい機械フィールドは作らない**（巡1 Q5）。
  構造化された読み手に同じ限界を渡す必要が実際に出たら、そのときフィールド化する
- `report-by-four-pillars.md` の柱2の説明にも同じ限界を1行で書く

### 不採用にした案

| 案 | 不採用の理由 |
|---|---|
| 案2（read 時の文字列照合） | §2 の実測。取り消しと書き換えを分離できない。codex が独立に再現し数字も一致 |
| `evolve-revert` 成功時に自動追記 | 今回の実害は `git revert` 由来で `evolve-revert` を通っていない。穴は残るのに機構だけ増える |
| 反映先を git で追跡して差分検出 | 反映先が git 管理外・別マシンにあり得る（#602）。機構が大きく ③ の対象外へはみ出す |
| `target_correction_id` を revert にも持たせる（R1） | 参照が二重になり、不一致状態と検証分岐が増える（巡1 Q5） |

---

## 4. 受け入れるトレードオフ（正直に書く）

1. **申告ベースである**。人間が `--revoke` を打たなければ取り消しは数字に出ない。
   `report-by-four-pillars.md` が「申告ベースは使わない」と定めたのは**水増しの方向**の申告
   （`promoted` / `already_reflected`）についてで、本件は**件数を減らす方向**なので水増しには使えない。
   打ち忘れは残る＝ §3.5 の固定1行でその限界を可視化する
2. **過去の1件は手で記録する**。`34cc2398…` の取り消しは、マージ後に `--revoke` を1回実行する
3. **取り消しの取り消し（再反映）は、新しい `applied` を記録する運用**で表す。専用の遷移は作らない
4. **曖昧な状態（時刻逆転・同着）は「取り消された」とも「生きている」とも数えない**。
   人間の再操作を求める。自動で片方に倒すと、どちらに倒しても嘘になる

---

## 5. 検証計画（⑤ の具体化・巡1 の指摘を反映）

| 種別 | 内容 | 期待 |
|---|---|---|
| 陰性 (a) | `--revoke` / `--list-applied` を CLI から到達不能にする | 取り消し操作のテストが赤 |
| 陰性 (b) | `correction_reverted` の分岐を no-op 化 | 畳み後の件数テストが赤 |
| 陰性 (b') | revert 済み target を reconciliation 対象から外す処理を消す | `attempt A → applied B → revert(B)` で件数が残り赤 |
| 陰性 (c) | 柱2表示から固定1行を削除 | 表示契約テストが赤 |
| 陰性 (d) | `stale_reverts` / `ambiguous_reverts` を health・degraded・表示理由に接続しない | health テストと `test_results_board` 相当が赤 |
| 陰性 (e) | CAS を外し「参照先を消すだけ」にする | `A1 → A2 → revert(A2)` で A1 が復活し赤 |
| 陰性 (f) | `--revoke` の lock 内再確認を外す | 読取→追記の間に `A3` を挿す決定論試験が赤 |
| 陽性対照1 | 取り消していない反映のみ | 件数・health とも変化なし |
| 陽性対照2 | `A1 → A2 → revert(A2) → A3` | A3 が1件として生き、degraded にならない |
| 陽性対照3 | 正常データで既存テスト全緑（`test_reflect_fold.py` の並べ替え不変契約を含む） | 緑のまま |

- 変異は適用後に「その分岐が当該テストの実行で読まれたこと」を機械で確かめてから陰性試験に数える
- 並行性の検証は同期点だけに頼らず、**呼出順を記録して assert する決定論試験**を併用する（#593 の実測）
- 委譲側が挙げた回避手段とは**種類の違うものを2件以上**、実際に適用して結果を報告する。
  **緑のまま残ったものが1件でもあれば完了扱いにしない。** 探索した入力クラスと変換も列挙する

---

## 6. レビュー巡の履歴

**新規系列。総上限: 2**（#587 / #595 とは対象成果物が異なる＝前身の巡数は継承しない。
本件は「柱2をどう数えるか」ではなく「取り消し遷移の追加」で、#595 の
`corrections.jsonl` writer 協調とも別ファイル・別問題）

| 巡 | 種別 | 発注日時 | 対象 SHA | レビュアー | 実測入力トークン | 判定 | 族タグ |
|---|---|---|---|---|---|---|---|
| 1 | 設計 | 2026-09-10 07:34 | `2deb1aff` | codex `rev637r1` | 102,130 | 設計修正要 | 状態遷移の未定義（復活・CAS・同着）／操作到達性 |
| 2 | コード | （実装後に記入） | | tacchi | | | |

巡1 の指摘の反映状況:

| 指摘 | 反映先 |
|---|---|
| [Must] reconciliation で復活する | §3.2 4点目・§5 陰性 (b') |
| [Must] 古い applied が再浮上する | §3.2 2〜3点目・§5 陰性 (e) |
| [Must] 操作到達性（ID が取れない） | §3.4 `--list-applied` |
| [Must] `reverts_applied_id` を CAS として定義 | §3.2 1点目 |
| [Must] read→append の競合 | §3.4 lock 内再確認・§5 陰性 (f) |
| [Must] 時刻順の全順序が未定義 | §3.2 最終点（曖昧は数えない） |
| [Should] `--revoke` の排他契約 | §3.4 3点目 |
| [Should] health を degraded と表示理由へ接続 | §3.3 |
| [Should] orphan/duplicate の分類 | §3.3 `stale_reverts` の定義 |
| [Should] validator | §3.1 末尾 |
| [Should] 「既存 health フィールドを1つ足す」の語義 | §3.3 見出しで section でないことを明記 |
| [Should] Q5 の最小構成 | §3 冒頭・§3.1・§3.5 |

Q3（#379 新設凍結との整合）は codex が**抵触しないと判定**（`shrink_freeze.py:62-92` で
`reflect_apply_events.jsonl` は登録済み、`assert_no_new_keys` が見るのはレジストリのキー集合で
既存ストア内の `event_type` 追加は対象外）。
Q2（§2 の実測）は codex が独立に再現し、数字は一致した。
