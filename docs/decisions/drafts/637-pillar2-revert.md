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

## 3. 採用案（A + B）

機構を足す前に減らす順（`think-before-coding.md`）で検討した結果、**A（文言の是正・ゼロ機構）を
土台に置き、その上に B（取り消しを1イベントで記録する）だけを足す**。層は重ねない。

### A. 柱2が何を数えているかを正直に書く（ゼロ機構）

- `count_applied_reflections` の結果に **`revert_tracking: "declared_only"`** の意味を持つ
  既存 health フィールドを1つ足し、戦果ボードの柱2の行に
  「**取り消しは申告があったぶんだけ反映**」の1行を出す（silence != evaluated）
- `report-by-four-pillars.md` の柱2の説明に、同じ限界を1行で書く
- **これ単独では ④(a) を満たさない**（人間が知っていても直す手段が無い）。だから B を足す

### B. 取り消しを1イベントで記録する

- **新ストアは作らない**。既存 `reflect_apply_events.jsonl` に `event_type`
  `correction_reverted` を1種類だけ追加する（#379 の新設凍結は「新 store / 新 section /
  新 channel」を対象としており、既存ストアへの遷移追加は対象外。**この解釈はレビューで確認する**）
- スキーマ（既存2種と同じ `schema_version: 1`）:
  | フィールド | 内容 |
  |---|---|
  | `event_type` | `correction_reverted` |
  | `correction_id` | このイベント自身の不変 ID |
  | `target_correction_id` | 取り消す対象の correction |
  | `reverts_applied_id` | **打ち消す `correction_applied` イベントの `correction_id`** |
  | `reverted_at` | ISO8601（tz 必須） |
  | `revert_reason` | 自由記述（1行・監査用） |
- `reflect_fold.fold_corrections` の扱い:
  - `reverts_applied_id` が実在の `correction_applied` を指し、かつ
    `target_correction_id` が一致するときだけ有効。**不一致・不在は `health.orphan_reverts` を
    加算して無視**（黙って捨てない＝④(d)）
  - 有効な取り消しは、対応する `applied` を **`applied_by_target` から除く**。
    同じ target に別の（取り消されていない）`applied` が残っていればそちらが生きる
  - 取り消しの後に**新しい `applied` が来たら、そちらが勝つ**（再反映。時刻順で最新を採る
    既存の `latest_pair` の枠内で処理する）
- 記録手段: `bin/evolve-reflect --revoke <target_correction_id> [--reason "..."]`。
  対象の `applied` が1件に確定できないときは**書かずに終了**し、候補を印字する（fail-closed）
- `evolve-revert --list` との関係: **統合しない**（③ 対象外）。`--revoke` は柱2の記録専用で、
  ファイルを書き戻す機能は持たない（`evolve-revert` は skill diff の巻き戻し器で、対象も
  ストアも別物。混ぜると2つの意味の「revert」が同じ CLI に同居する）

### 不採用にした案

| 案 | 不採用の理由 |
|---|---|
| 案2（read 時の文字列照合） | §2 の実測。取り消しと書き換えを分離できない |
| `evolve-revert` 成功時に自動追記 | 今回の実害は `git revert` 由来で、`evolve-revert` を通っていない。自動化しても同じ穴が残るのに機構は増える |
| 反映先を git で追跡して差分検出 | 反映先が git 管理外の PJ・別マシンにあり得る（`pillars-before-polish.md` の測定不能事例）。機構が大きく、③ の対象外へはみ出す |

---

## 4. 受け入れるトレードオフ（正直に書く）

1. **申告ベースである**。人間が `--revoke` を打たなければ、取り消しは数字に出ない。
   `report-by-four-pillars.md` が「申告ベースは使わない」と定めたのは**水増しの方向**の申告
   （`promoted` / `already_reflected`）についてで、本件は**件数を減らす方向**の申告なので
   水増しには使えない。ただし「打ち忘れ」は残る＝ A の1行表示でその限界を可視化する
2. **過去の1件は手で記録する**。`34cc2398…` の取り消しは、本 PR のマージ後に
   `--revoke` を1回実行して記録する（自動移行はしない）
3. **取り消しの取り消し（再反映）は、新しい `applied` を記録する運用**で表す。
   専用の遷移は作らない

---

## 5. 検証計画（⑤ の具体化）

| 種別 | 内容 | 期待 |
|---|---|---|
| 陰性 (a) | `--revoke` を削除（CLI から到達不能にする）| 取り消しを記録するテストが赤 |
| 陰性 (b) | `fold_corrections` の `correction_reverted` 分岐を no-op 化 | 畳み後の件数を見るテストが赤 |
| 陰性 (c) | 柱2の表示から限界の1行を削除 | 表示契約テストが赤 |
| 陰性 (d) | orphan な取り消しを health に加算せず無視 | health テストが赤 |
| 陰性 (e) | 有効性検証を外し、`target_correction_id` だけで畳む | 別 attempt を巻き添えにするテストが赤 |
| 陽性対照1 | 取り消していない反映のみのデータ | 件数・health とも変化なし |
| 陽性対照2 | 取り消し→再反映の順に記録 | 件数が元に戻り degraded にならない |

変異は適用後に「その分岐が実行で読まれたこと」を機械で確認してから陰性試験に数える。

---

## 6. レビュー巡の履歴

**新規系列。総上限: 2**（#587 / #595 とは対象成果物が異なる＝前身の巡数は継承しない。
本件は「柱2をどう数えるか」ではなく「取り消し遷移の追加」で、#595 の
`corrections.jsonl` writer 協調とも別ファイル・別問題）

| 巡 | 種別 | 発注日時 | 対象 SHA | レビュアー | 実測入力トークン | 判定 | 族タグ |
|---|---|---|---|---|---|---|---|
| 1 | 設計 | （発注時に記入） | | codex | | | |
