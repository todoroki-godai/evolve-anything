# #593: correction レコードに位置非依存の不変識別子を与える設計（第3版）

> **巡1（`8d2e0b44`）・巡2（`fe8349f8`）は不採用**。巡2は「`corrections.jsonl` を
> 書き換える全経路を同一 sidecar lock domain へ入れる」方針を採ったが、方針の正しさが
> **経路の全量列挙**に依存する構造だった。巡2は7経路と数えたが、外部レビューは
> 実測11経路を提示し、巡2の主張が崩れた（漏れ: `scripts/migrate_reflect_promoted_status.py:51-78` /
> `scripts/lib/corrections_subagent_invalidation.py:80-106` /
> `scripts/lib/backfill_turn_indices.py:202-254` /
> `scripts/lib/pj_slug_backfill.py:76-109`・`:164-171`）。**本第3版は方針そのものを
> 差し替える**: 共有ロックを新設せず、更新する側が compare-and-swap（読み直し→
> ID 再解決→最小差分での atomic 置換→書込み後の読み直し検証）で**自分だけで
> 正しさを保証する**設計にする。旧版本文は `git show fe8349f8:...` / `git show 8d2e0b44:...`
> で参照できる。

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

## 0. Round 0 完成条件（verbatim・第3版で更新）

### ① 守る対象

correction レコードの個体を、ファイル内の位置に依存せず一意に指せること。
**更新操作が、指定した ID とは別のレコードを書き換えないこと**（これが本 issue の
核心。書込み側の自己確認だけで保証する）。

### ② 信頼境界

自分たちの運用ミスのみ（手編集 / 別プロセスの追記 / 中断 / 同時に走る2つの更新 /
移行スクリプトの未実行 / 再 ingest による重複）。悪意ある偽装・第三者の改竄は脅威に
数えない。

### ③ 対象外

- 柱2の集計・表示（`results_board`）の変更
- 反映イベントの追記と read 時 fold（#587 の本体）
- `reflect_status` の意味論変更
- `#379` 新設凍結の解除。新しい保存先を作らない
- **他経路（`corrections.jsonl` を書き換える、本設計が対象にしない他の関数群）による
  lost update の根治**。検出はするが防止はしない。防止（排他制御の新設）は別 issue

### ④ スコープ（1つの変更単位として設計する）

1. 不変 ID の発行（新規レコードを作る writer・既存レコードの移行）
2. 更新 API（`update_reflect_status`／`--apply`・`--skip`・`--skip-all` の3経路）を
   ID ベースに変え、compare-and-swap で自己完結させる
3. **共有ロックは新設しない**

### ⑤ blocking

- (a) 同一 ID が2件以上存在しうる。**重複を保存前に拒否する**で塞ぐ。**位置依存の
  修復規則（ファイル先頭を primary とする自動選択）は採らない** — 既存の重複が
  見つかった場合は人間が保持対象を明示する
- (b) 削除・並べ替えで別レコードを指す（**中核**）
- (c) ID なし・空文字列・`null`・非文字列が黙って通る
- (d) 既存 reader を壊す。**列挙は「レコード全体を下流へ運ぶ経路」に限定する**
  （`.get()` で個別キーを読むだけの reader は網羅を求めない）
- (e) 中断で ID だけ／本体だけ書かれた状態が成功扱いになる
- (f) **落とす**（巡2から変更）。ID と発行元マーカーを同じレコードに置いても、
  両方を手編集で消されれば「未移行」と区別できないことが巡2で示された。
  手編集で ID を消したら移行スクリプトが新しい ID を振ることを受容し、
  影響（そのレコードの ID 履歴が切れる）を明記する。独立マーカーは新設しない
- (g) 移行の終了結果が完了・未完了・衝突・要再試行を区別せず、未完了を成功として返す
- (h) 妥当性・一意性の契約が単一ソースでない。**保存境界（書込み関数の commit 直前）に
  置く** — builder がフィールドを代入するだけで検証を呼ばない実装を許さない

### ⑥ 検証方法

検証単位は「writer→保存→reader→ID 解決→競合差込み→更新／拒否」の実経路に置く。
競合の差込みは読み直しと書込みの間に実際に別の書込み・削除を入れて決定論的に
再現する。中断は書込み開始後・置換前に failpoint を注入する。「書かない関数が
書かないことしか見ない」トートロジーを置かない。陽性対照は値の型・形式・一意性・
既存 ID の不変まで assert する。各試験について「緑のまま通る実装変異」を自分で
構成し、構成できたら試験を作り直す。

## 1. 現状（自分で数え直した file:line つき）

### 1.1 実データ（巡1・巡2と同一データにつき再測不要）

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z。既存241件に識別子相当のフィールドは無い。

### 1.2 新規レコードを作る writer（4経路・本設計の対象）

| # | file:line | 何を作るか |
|---|---|---|
| W1 | `hooks/correction_detect.py:132-165`（`store_write("corrections.jsonl", record)`、line 164） | hook 検出による新規レコード |
| W2 | `scripts/lib/correction_semantic/promote.py:346-393`（`_build_correction_record`）→ `:565-568`（`store_write`/`store_write_raw`） | weak signal 昇格による新規レコード |
| W3 | `scripts/backfill_preceding_tool_calls.py:229-254`（`persist_to_corrections`） | 過去セッションからの一括バックフィル |
| W4 | `scripts/migrate_reflect_queue.py:94-125`（`migrate`、追記は line 118-121） | `learnings-queue.json` からの1回限りマイグレーション |

**この4経路のみが `correction_id` を新規発行する**。理由は §2 で述べる（既存レコードを
書き換えるだけの経路は新しい ID を発行する必要が無い）。

### 1.3 既存レコードを書き換える経路（本設計は対象にしない・参考として列挙）

外部レビューが指摘した4経路を含め、実物で確認した限りで**少なくとも8関数**が
`corrections.jsonl` の全体または一部を書き換える。**この列挙が完全であることに
本設計の正しさは依存しない**（§3 で述べる理由により、更新 API は「他に何が
起きているか」を知らなくても自己完結できる設計にしたため）。参考として記録する:

| file:line | 書込み方式 | ロック |
|---|---|---|
| `skills/reflect/scripts/reflect.py:602-706`（`update_reflect_status`。書込みは line 706） | 全文読込→`write_text` in-place | 無し（本設計が改修する対象・§4） |
| `scripts/lib/prune/corrections.py:51-114`（`cleanup_corrections`。書込みは line 112） | 全文読込→`write_text` in-place | 無し |
| `scripts/lib/correction_semantic/promote.py:584-642`（`invalidate_idiom_corrections`。書込みは line 634-642） | `tempfile`+`os.replace` | 無し |
| `scripts/migrate_reflect_promoted_status.py:51-78`（`migrate`。書込みは line 75-78） | 全文読込→`write_text` in-place | 無し |
| `scripts/lib/corrections_subagent_invalidation.py:80-106`（書込みは line 101-105、`atomic_write_text`） | `tempfile`+`os.replace` 相当 | 無し |
| `scripts/lib/backfill_turn_indices.py:202-254`（`backfill_corrections`。書込みは line 250-254、`_atomic_write`） | `tempfile`+`os.replace` 相当 | 無し |
| `scripts/lib/pj_slug_backfill.py:76-109`（`_backfill_jsonl`）・`:164-171`（`_JSONL_STORES` に `"corrections.jsonl"` を含む、line 165） | `tempfile`+`os.replace` 相当（`_atomic_write`） | 無し |

**確認コマンド**: 上記7ファイルを個別に Read し、`open(`/`write_text(`/`os.replace(`/
`flock` の有無を目視確認した（実行時刻: 2026-08-31 本文書作成セッション内）。
外部レビューの「実測11経路」との差分（8 vs 11）は、W1〜W4（新規作成）とこの表
（既存書換え）を合わせた数え方の違いによるもので、**本設計はどちらの数え方でも
正しさが変わらない**（§3.3 で理由を述べる）。

## 2. 方針転換の理由: なぜ「全経路の同一ロック」を捨てるか

巡2は「`corrections.jsonl` を書き換える全経路を、置換を跨げる sidecar lock の
同一 lock domain へ入れる」ことで blocking (b) を塞ごうとした。この方針は
**正しさの条件が「見つけた経路の集合が真に全部である」ことに依存する**。
§1.3 で示した通り、この集合は8関数（本設計が確認した数）・巡2の7経路・
外部レビューの11経路と、**数える人によって食い違う**。1件でも改修漏れがあれば、
その経路だけがロックの外側で `corrections.jsonl` を書き換え続け、blocking (b) は
再発する。**この構造そのもの（正しさが全量列挙の完全性に依存すること）が
巡2の欠陥であり、経路を厚く数え直しても同じ往復が続く**。

**第3版が採る方針**: 更新する側（`update_reflect_status`）が、**他の経路が
何をしていても自分だけで正しさを保証する**。具体的には:

1. 書く直前にファイルを読み直す（呼出元が事前に持っていたスナップショットは
   一切信用しない）
2. その読み直した内容の中で、対象 `correction_id` がちょうど1件であることを
   確認してから初めて書く。0件・2件以上なら**書かずに失敗を返す**
3. 書込みは自分が読み直した内容を元に構築し、対象の1行だけを更新した内容で
   `tempfile` + `os.replace`（atomic）を行う。**他の行は自分が読んだ内容を
   そのまま透過する**（他の経路の存在を知らなくても、自分が読んだ時点の内容を
   壊さず引き継ぐ）
4. 書込み後にもう一度読み直し、**自分が意図した更新が実際に反映されているか**を
   検証する。反映されていなければ（他の経路が自分の書込みの前後で競合したことを
   意味する）、**成功と偽らず明示的に失敗を返す**

**この設計が防げること**: 「対象 ID とは別のレコードを更新する」こと
（blocking (b) の核心）。ステップ1〜3が**自分が読んだスナップショット内でのみ
ID→位置の対応を決定し、他プロセスから見た「今の」位置には一切依存しない**
ため、他プロセスが同時に何をしていても、**自分が書く1行の中身は常に
「自分が ID で解決したレコード」である**（他の経路の削除・並べ替えを知らなくても、
勝手に別レコードを指すことは構造的に起こらない——起こりうるのは「自分の書込みが
他プロセスの変更を巻き込む/巻き込まれる」という次項の限界のみ）。

**この設計が防げないこと（明記する）**: 他経路（§1.3 の8関数）との
**lost update**。具体的には:

- 自分がステップ1で読んだ**後**、自分がステップ3で `os.replace` する**前**に、
  他の経路（例: `cleanup_corrections`）が別の `os.replace` を行うと、自分の
  `os.replace` はその変更を**知らずに上書きする**（他経路の変更が消える）
- 逆に、自分の `os.replace` の**直後**に他の経路が `os.replace` すると、
  自分の変更が消える。この場合はステップ4の読み直し検証で**必ず検出**し、
  成功と偽らずに失敗を返す（ただし他経路の変更を守ることはできない——
  自分の変更が失われたことを検出できるだけで、それを自動的にリトライ・
  マージすることは本設計のスコープ外）

**この限界の根治は別 issue へ送る**（round 0 対象外「他経路の lost update の
根治」）。根治には何らかの排他制御（巡2が試みた共有ロック、または
`corrections.jsonl` そのものを単一の書込み経路に統合する設計）が要るが、
それは§1.3の8関数全部の改修を要する大きな変更であり、本 issue のスコープ
（識別子とその ID ベース更新の自己完結性）を超える。

## 3. `correction_id` の発行

### 3.1 スキーマと単一ソース（blocking h — 保存境界に置く）

新規フィールド `correction_id: str`（`uuid.uuid4().hex`、32文字16進）。

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新しいストアではない・§6で確認）
import re
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

def new_correction_id() -> str:
    return uuid.uuid4().hex

def validate_correction_id(value) -> bool:
    """correction_id として有効な形式か判定する単一ソース（blocking c・h）。
    None・空文字列・非文字列・不正フォーマットはすべて False。"""
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))

def validate_unique_ids(records: list[dict]) -> dict[str, int]:
    """records 内の有効な correction_id ごとの出現回数を返す（blocking a・h）。
    validate_correction_id を通った値のみ数える。"""
    counts: dict[str, int] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        cid = r.get("correction_id")
        if validate_correction_id(cid):
            counts[cid] = counts.get(cid, 0) + 1
    return counts
```

**「保存境界に置く」の意味**: `validate_correction_id`/`validate_unique_ids` の
呼出しを、W1〜W4（§1.2）の**レコード構築コード**（例: `promote.py:346-393`）に
置かない。構築コードは `new_correction_id()` を呼んで代入するだけでよい
（巡2の欠陥はここに検証を置かず終わっていた）。**検証は §3.2 の追記用共通関数
`append_correction_record` の内部、実際にバイト列をファイルへ書く直前**に置く。
W1〜W4 はすべてこの関数を経由するよう改修する（実装1巡のスコープ）。

### 3.2 追記時の重複拒否（blocking a — 保存前に拒否する）

```python
def append_correction_record(filepath: Path, record: dict) -> "AppendResult":
    """新規レコードを1件追記する。record は既に correction_id を持つ想定
    （W1〜W4 が構築時に new_correction_id() で設定済み）。

    保存直前に検証する（保存境界）:
    1. record["correction_id"] が validate_correction_id を通らなければ拒否
       （呼出元のバグ検出——通常は起こらないはずだが blocking h の要求どおり
       構築側を信用しない）
    2. ファイルを読み直し、record["correction_id"] が**既存レコードのいずれかと
       重複していないか**を確認する。重複していれば追記せず拒否する
       （blocking a: 重複を保存前に拒否する）
    """
    cid = record.get("correction_id")
    if not validate_correction_id(cid):
        return AppendResult(status="invalid_id")
    existing = _read_records(filepath)  # isinstance(dict) フィルタ済み（§5.4）
    if any(validate_correction_id(r.get("correction_id")) and r["correction_id"] == cid
           for r in existing):
        return AppendResult(status="duplicate_id")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return AppendResult(status="appended")
```

**この追記関数にもロックは無い**（§2 の方針どおり——共有ロックは新設しない）。
「読み直し→重複確認→追記」の間にも他プロセスが同じ ID を追記する競合窓は
理論上残るが、**新規発行される `correction_id` は毎回 `uuid.uuid4()` で
新しく作られる**ため、通常運用（W1〜W4 が普通に動く限り）でこの競合が
実際に同じ ID を生むことは無い（衝突確率は無視できる）。この重複拒否が
実際に効くのは「既に `correction_id` を持つレコードを再度書き込もうとする」
シナリオ（バックアップ復元・再 ingest。信頼境界②に明記済み）であり、
これらは**人間が手動で実行する一度限りの操作**であって、hook のような
高頻度・自動の書込みとは競合頻度の性質が異なる。**この残存競合窓
（読み直しと追記の間に別プロセスが全く同じ既存 ID を割り込ませる）を
本設計は防げない**——これも§2で明記した「他経路との lost update は
検出のみ、防止しない」と同じ性質の限界として扱う（追記側は書込み後の
読み直し検証まではしない設計とする。理由: 新規追記は「別レコードを壊す」
リスクが無い——自分の行を追加するだけで既存行を一切書き換えないため、
blocking (b) の対象外。検証が必要なのは「既存レコードを更新する」§4 の
方だけ）。

### 3.3 既存の重複の解消: 人間が保持対象を明示する（位置依存の自動修復は採らない）

**位置依存の修復（ファイル先頭を primary とする自動選択）を廃止した理由**:
「不変 ID」という目的そのものと矛盾する——ある ID がどのレコードを指すかが
「ファイルの中で何番目か」によって決まるなら、それは ordinal の弱点を
形を変えて持ち込むだけになる。

**採用する手順**: 新規スクリプト `scripts/repair_duplicate_correction_ids.py`
（設計のみ）は、重複を検出したら**自動修復せず、重複グループを人間に提示する**:

```
$ python3 scripts/repair_duplicate_correction_ids.py --list
duplicate correction_id: a1b2c3...（2件）
  [0] session_id=xxx timestamp=2026-08-01T00:00:00Z message="..."
  [1] session_id=yyy timestamp=2026-08-15T00:00:00Z message="..."
```

人間が `--keep <session_id>#<timestamp>` で保持対象を明示的に指定する:

```
$ python3 scripts/repair_duplicate_correction_ids.py --id a1b2c3... --keep xxx#2026-08-01T00:00:00Z --apply
```

指定された1件は ID を変えず、**指定されなかった残りの全件**に
`new_correction_id()` を発行し直す。`--keep` の指定が無い、または指定された
`session_id#timestamp` がその重複グループに存在しない場合は**何もしない**
（黙って先頭を選ばない）。既定は `--dry-run`（他の移行スクリプトと同じ規約）。

**参照元が既に存在する場合**: 現時点のコードベースには `correction_id` を
外部へ永続保存して後から参照する経路が無い（§7 で追加する `--apply-id` が
実装されて初めて生まれる）。修復により ID が変わったレコードへの参照
（人間の記憶・チャット履歴上のコピペ等）は無効化されうる——これは
`--repair-duplicate-correction-ids.py --list` の出力に「どの ID がどう
変わるか」を明示すること以上の救済を、本設計のスコープでは提供しない
（round 0 対象外「参照の自動追跡」）。

## 4. 更新 API: compare-and-swap による自己完結（blocking b・c・e の中核）

### 4.1 現行の契約とその問題

`update_reflect_status(filepath, indices: list[int], status, ...)`
（`reflect.py:602-608`）。`indices` は「`load_corrections` が返す配列の index と
同じ空間」（docstring, `reflect.py:619-621`）——**呼出元が事前に読んだスナップショット
上の位置**であり、`update_reflect_status` 自身はロックも再読込もせず
`filepath.read_text()`（line 679）で改めて読むが、**受け取った index を
そのまま信用する**（`record_idx in index_set` で照合、line 699）。呼出元が
読んでから `update_reflect_status` が実際に書くまでの間に他プロセスが
レコードを削除・並べ替えていれば、index は別レコードを指す
（blocking b の実体）。呼出し元は3経路: `--apply`（`reflect.py:1263-1287`）・
`--skip`（`:1329-1359`）・`--skip-all`（`:1407-1421`、`pending_indices` を
`enumerate(all_records)` から作る、`:1411-1414`）。

### 4.2 新設計: ID を受け取り、書く直前に自分で再解決する

```python
def update_reflect_status(
    filepath: Path,
    correction_ids: list[str],
    status: str,
    *,
    target_path: str | None = None,
    draft_line: str | None = None,
) -> "UpdateResult":
    if status == "applied":
        # target_path の中身は corrections.jsonl と無関係なファイルであり、
        # identity 再確認の対象ではない。ロック外で先に検証してよい（現行踏襲）。
        match = check_line_applied(Path(target_path), draft_line)
        if not match["matched"]:
            return UpdateResult(status="apply_unverified", reason=match["reason"])

    for cid in correction_ids:
        if not validate_correction_id(cid):
            return UpdateResult(status="invalid_id", target=cid)

    # ステップ1: 呼出元のスナップショットを一切信用せず、今ここで読み直す
    if not filepath.exists():
        return UpdateResult(status="not_found", reason="corrections ファイルが存在しません")
    raw_lines = filepath.read_text(encoding="utf-8").splitlines()
    parsed: list[tuple[int, dict | None]] = []  # (元の行index, dict or None)
    for i, line in enumerate(raw_lines):
        stripped = line.strip()
        if not stripped:
            parsed.append((i, None))
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            parsed.append((i, None))
            continue
        parsed.append((i, rec if isinstance(rec, dict) else None))  # 非dictは温存のみ対象

    # ステップ2: correction_ids ごとに「ちょうど1件」であることを確認する
    id_to_line_indices: dict[str, list[int]] = {}
    for i, rec in parsed:
        if rec is None:
            continue
        cid = rec.get("correction_id")
        if validate_correction_id(cid):
            id_to_line_indices.setdefault(cid, []).append(i)

    to_update: dict[str, int] = {}   # correction_id -> line index
    not_found: list[str] = []
    ambiguous: list[str] = []
    for cid in correction_ids:
        matches = id_to_line_indices.get(cid, [])
        if len(matches) == 0:
            not_found.append(cid)
        elif len(matches) > 1:
            ambiguous.append(cid)
        else:
            to_update[cid] = matches[0]

    if not to_update:
        return UpdateResult(status="not_found" if not ambiguous else "ambiguous",
                             not_found=not_found, ambiguous=ambiguous)

    # ステップ3: 自分が読んだ内容だけを元に、対象行だけ差し替えて構築する
    # （他プロセスの存在を知らなくても、自分が読んだ内容を壊さず引き継ぐ）
    new_raw_lines = list(raw_lines)
    for i, rec in parsed:
        if rec is not None and i in to_update.values():
            rec = dict(rec)
            rec["reflect_status"] = status
            new_raw_lines[i] = json.dumps(rec, ensure_ascii=False)
    new_content = "\n".join(new_raw_lines) + "\n"

    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, filepath)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return UpdateResult(status="retry_required", error=str(e))

    # ステップ4: 書込み後に読み直し、自分の変更が実際に入っているか検証する
    verify_lines = filepath.read_text(encoding="utf-8").splitlines()
    lost: list[str] = []
    for cid in to_update:
        found_and_correct = False
        for line in verify_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("correction_id") == cid and rec.get("reflect_status") == status:
                found_and_correct = True
                break
        if not found_and_correct:
            lost.append(cid)

    if lost:
        # 成功と偽らない: 他プロセスとの競合で自分の変更が失われたことを検出した。
        return UpdateResult(status="lost_update_detected", lost=lost,
                             applied=[c for c in to_update if c not in lost],
                             not_found=not_found, ambiguous=ambiguous)

    return UpdateResult(status=status, applied=list(to_update.keys()),
                         not_found=not_found, ambiguous=ambiguous)
```

**呼出側の契約**: `not_found`/`ambiguous`/`invalid_id`/`retry_required`/
`lost_update_detected` はすべて失敗である。CLI は非0終了させ、後続処理
（revert 記録等）へ進めてはならない（#588 [Must] を踏襲・拡張）。
**`ambiguous` は blocking (a) の残存ケース**（§3.3 の修復が未実施のまま
重複 ID が残っている状態）を検出した場合に返る——この場合も書込みは
行わない。

### 4.3 なぜこれで blocking (b) を「防げる」と言えるか

ステップ3で書き換える対象行は、**ステップ1でこの関数自身が読んだ
`raw_lines`** から選ばれる。呼出元が過去に読んだ古いスナップショット
（CLI が表示のために読んだ `all_records`）は一切使わない。したがって:

- 他プロセスが自分の読込み（ステップ1）より**前**に削除・並べ替えを
  行っていれば、それは単に「自分が読んだ時点の内容」に反映されており、
  自分の ID 解決（ステップ2）はその時点の正しい対応で行われる
- 他プロセスが自分の読込み（ステップ1）より**後**、自分の書込み
  （ステップ3の `os.replace`）より**前**に削除・並べ替えを行った場合、
  自分の書込みはそれを知らずに上書きする（§2 で明記した lost update）。
  しかし**自分が書く内容そのものは、自分が読んだ時点で ID 一致を確認した
  レコードの更新結果であり、別のレコードを指すことは無い**（ステップ4が
  それを検出できるのは「自分の変更が消えたか」であって「自分が誤った
  レコードを更新したか」ではない——後者は構造的に起こらない、というのが
  本設計の核心の主張である）

**巡2からの改善点**: 巡2はこの再解決を「ロックを取ってから」行うことで
lost update 自体を防ごうとした（ロック新設が方針の中心）。第3版は
lost update の**防止**を放棄し、**誤ったレコードを指すことの防止**と
**自分の失敗の正直な報告**だけを保証する、という狭い主張に絞った。

## 5. 移行（既存241件への `correction_id` 付与）

### 5.1 スクリプト（新規 `scripts/migrate_correction_id_backfill.py`・設計のみ）

§4.2 と同じ「読込→変換→atomic 置換→読み直し検証」の型を、単一レコード更新
ではなく全件走査に適用する:

```python
def migrate(filepath: Path, *, dry_run: bool = True) -> "MigrationResult":
    if not filepath.exists():
        return MigrationResult(status="completed", total=0, newly_assigned=0)

    raw_lines = filepath.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    newly_assigned = 0
    malformed = 0
    assigned_ids: set[str] = set()
    existing_ids: set[str] = set()

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)  # §5.4: 非dict/壊れた行は温存のみ、ID は付与しない
            malformed += 1
            continue
        if not isinstance(rec, dict):
            new_lines.append(line)
            malformed += 1
            continue

        cid = rec.get("correction_id")
        if validate_correction_id(cid):
            existing_ids.add(cid)
            new_lines.append(json.dumps(rec, ensure_ascii=False))
            continue

        new_id = new_correction_id()
        while new_id in assigned_ids or new_id in existing_ids:  # 保存前の重複拒否（§3.2 と同型）
            new_id = new_correction_id()
        rec = dict(rec)
        rec["correction_id"] = new_id
        assigned_ids.add(new_id)
        newly_assigned += 1
        new_lines.append(json.dumps(rec, ensure_ascii=False))

    if dry_run:
        return MigrationResult(status="completed", total=len(raw_lines),
                                newly_assigned=newly_assigned, malformed_lines=malformed,
                                dry_run=True)

    new_content = "\n".join(new_lines) + "\n" if new_lines else ""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, filepath)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return MigrationResult(status="retry_required", error=str(e))

    # 読み直し検証: 全レコードが有効な correction_id を持ち、かつ重複していないか
    verify_records = _read_records(filepath)  # isinstance(dict) フィルタ済み
    counts = validate_unique_ids(verify_records)
    ids_without = sum(
        1 for r in verify_records if not validate_correction_id(r.get("correction_id"))
    )
    duplicates = {cid: n for cid, n in counts.items() if n > 1}

    if duplicates:
        return MigrationResult(status="conflict", total=len(verify_records),
                                newly_assigned=newly_assigned, duplicates=duplicates)
    if ids_without > 0:
        return MigrationResult(status="incomplete", total=len(verify_records),
                                newly_assigned=newly_assigned, still_missing=ids_without)
    return MigrationResult(status="completed", total=len(verify_records),
                            newly_assigned=newly_assigned, malformed_lines=malformed)
```

### 5.2 4値の終了ステータスと CLI exit code（blocking g）

| status | 意味 | CLI exit code |
|---|---|---|
| `completed` | 書込み成功・読み直し検証で全レコードが有効かつ一意な ID を持つ | 0 |
| `incomplete` | 書込みは成功したが、読み直したら ID を持たないレコードが残っていた（自分の書込みと他プロセスの書込みが競合し、自分の割当てが一部失われた） | 1（再実行してよい——冪等） |
| `conflict` | 読み直したら重複 ID が生じていた（自分の割当てと他の何かが競合した、または既存データに想定外の重複があった） | 2（`repair_duplicate_correction_ids.py --list` で調査してから再実行） |
| `retry_required` | `OSError` 等で書込み自体が失敗した（ディスク容量・権限） | 3（そのまま再実行してよい——tempfile 段階で失敗しており元ファイルは無傷） |

### 5.3 冪等性と中断耐性（blocking e）

- 「既に有効な `correction_id` を持つレコードはスキップ」（§5.1 の
  `validate_correction_id(cid)` 分岐）により、**中断後の再実行は常に安全**
  （最初からやり直すだけでよい。進捗マーカー方式は採らない——241件という
  規模で1回の実行に完結できるため）
- 計算（読込→変換、メモリ上のみ）と書込み（`tempfile`+`os.replace`）を分離。
  計算中に kill されれば元ファイルは無傷。`tempfile` 書込み中に kill されれば
  元ファイルは無傷（`os.replace` に到達していない）。`os.replace` 自体は
  OS レベルで atomic——「rename の途中」という状態は存在しない
- **crash consistency の範囲**: 本設計が保証するのはプロセス kill・
  未処理例外までである。**OS クラッシュ・電源断は対象外**——`os.fdopen` の
  書込みは `fsync` を呼んでおらず（`promote.py:634-642` の既存 atomic write
  パターンも同様に `fsync` を呼んでいない。file:line 確認済み・本設計は
  既存パターンを踏襲するだけで新たな durability 保証を追加しない）、
  電源断時に OS のページキャッシュ上のデータが失われる可能性を排除できない。
  これは既存コードベース全体の性質であり、本設計固有の後退ではない

### 5.4 非 dict 行（受け入れ条件で塞ぐ）

`reflect.py:111-124`（`load_corrections`）は `json.loads` が例外を出さなければ
dict でなくても（`[]`・`"x"`・`123`）配列に含め、直後の `extract_pending`
（`:127-137`）が無条件に `.get()` して壊れる（str/list に `.get` は無くクラッシュ）。
**本設計が新設する読込みロジック（§4.2 のステップ1、§5.1 の移行スクリプト）は
すべて `isinstance(rec, dict)` を明示チェックし、非 dict 行は「レコードでない」
ものとして常に透過するだけに留める**（ID 付与も ID 解決の対象にもしない）。
**`load_corrections`/`extract_pending` 自体は改修しない**（本設計のスコープ外——
CLI がレコード一覧を人間に表示する既存パスであり、ID ベースの更新には使わない。
§7 で述べる新設の `--resolve-id` 等の機械可読出力は本設計の安全な読込みロジックを
使う）。**これにより非 dict 行によるクラッシュは、本設計が新設するコードパスには
一切残らない**が、**既存の `load_corrections`/`extract_pending` を使う CLI の
表示パス自体のクラッシュ耐性は本設計では直さない**（round 0 対象外——
表示ロジックの改修は識別子設計と独立の問題）。

## 6. `#379` 新設凍結への非抵触（維持・巡1・巡2で確認済み、変更なし）

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`、72行目に `"corrections.jsonl"`）・
`:23-37`（凍結対象4集合）・`:261-275`（`assert_no_new_keys`、ストア名等の文字列集合の
みを検査、JSON フィールド粒度は対象外）を実物照合済み。**第3版は sidecar lock ファイルを
新設しない**ため、巡2で追加検討していた「`.lock` ファイルが凍結検査に引っかからないか」
という論点自体が消滅する。`correction_id`/`correction_id_schema`（第3版では
`correction_id_schema` を削除・(f) を落としたため）は既存ストア `corrections.jsonl`
への新規フィールド追加のみであり、巡1で確認した根拠がそのまま成立する。

## 7. 既存 reader の母集団（列挙は「レコード全体を下流へ運ぶ経路」に限定・blocking d）

`.get(key)` で個別キーだけ読む reader（`reflect_status` を読む6箇所・
`error_category` のみ読む `telemetry.py:score_failure_distribution` 等）は
新規フィールド追加の影響を受けない（`dict.get` は未知キーを無視する）ため
**網羅を求めない**（巡2の判断を維持・根拠は巡2の§4.1 と同一——本第3版では
再掲を省略し、file:line は変更が無いため再確認は行っていない）。

**レコード全体を下流へ運ぶ経路のみ再確認する**（巡2 §6.2 から再掲・変更なし）:

- `hooks/auto_memory_runner.py:57-90`・`:123-154` → `scripts/lib/auto_memory_broker.py:322-335`
  （`_build_prompt`、`json.dumps(corrections, ...)` で LLM プロンプトへ直接埋め込み）
- `hooks/save_state.py:76-104` → `hooks/restore_state.py:41-77`
  （`_summarize_checkpoint_for_output`、`MAX_SNAPSHOT_ITEMS=20`/`MAX_SNAPSHOT_CHARS=8000`
  で truncate した上で SessionStart の Claude context へ print）

**判断（巡1・巡2から維持）**: allowlist 投影を導入せず、露出を明示的に受容する。
`correction_id` は32文字16進の固定形式文字列で注入攻撃の運び屋になりえず、
既存の自由記述フィールド（`message`/`extracted_learning`）よりリスクが低い。
サイズ影響も既存の truncate 機構（件数・合計文字数の両方）に吸収される。

## 8. `--apply-id`/`--skip-id` と ID 取得経路

`--apply-id <correction_id>`/`--skip-id <correction_id>` を新設する。§4.2 の
新シグネチャ（`correction_ids: list[str]`）を直接使う。既存 `--apply`/`--skip`
（`source_correction_id` 形式）は残す——ただし内部実装は「一致するレコードを
全部集めて `correction_id` を取り出し、1件なら §4.2 を呼ぶ、2件以上なら
`ambiguous_source_id` として拒否する」に変える（先頭一致 `break` を廃止。
`reflect.py:1263-1269`・`:1329-1335`）。`--skip-all` は `pending_indices` の
代わりに `pending` から `correction_id` の一覧を作り、§4.2 に**リスト**として
渡す（§4.2 の設計はバッチ入力を最初から想定している——1回の読込み・1回の
`os.replace`・1回の検証で複数 ID を処理する）。

**ID 取得経路（人間向け表示は変えない方針を保ったまま）**: 既存の pending 一覧
表示（`--view`、`reflect.py:1400-1403`）や `--dry-run` の出力フォーマットは
変更しない（人間が読む文面に32文字の ID を混ぜない）。代わりに新設
`--resolve-id <source_correction_id>` を追加する: 指定した
`source_correction_id` に一致するレコードの `correction_id` を機械可読 JSON
（`{"status": "found"|"not_found"|"ambiguous", "correction_id": str | null, "candidates": [...]}`）
で返す。2件以上一致する場合は `candidates` に各レコードの `correction_id` と
識別用メタ情報（`session_id`/`timestamp`/`message` 先頭50文字）を列挙し、
人間が `--apply-id` に渡す値を選べるようにする。これにより利用者は生ファイルを
直接開かずに ID を取得できる。

## 9. 検証計画

検証単位は実経路（writer→保存→reader→ID 解決→競合差込み→更新／拒否）に置く。
lock は使わない設計なので「ロック待ち」の確認は不要——代わりに**読込みと
書込みの間に実際に別プロセスが書込む**ことを `multiprocessing.Process` +
`multiprocessing.Event`（子プロセスが「読込み完了・書込み待機中」を親へ通知し、
親がその合図を受けてから競合操作を行い、その後に子の続行を許可する、という
決定論的な同期）で再現する。テストは
`scripts/lib/tests/test_correction_id.py`・
`skills/reflect/scripts/tests/test_reflect_update_status_cas.py`・
`scripts/tests/test_migrate_correction_id_backfill.py`（いずれも新設）に置く。

| # | 壊す不変条件 | 変異（実経路） | 期待結果 | この試験を「緑のまま通す」実装変異（自己検証） |
|---|---|---|---|---|
| (a) 陰性1 | 追記時に重複を拒否する | `append_correction_record` で、既存レコードと同じ `correction_id` を持つ新規レコードを渡す | `status == "duplicate_id"`。ファイルの行数が増えていないことを実際に `wc -l` 相当で確認 | 重複チェックを飛ばして無条件追記する実装は、行数増加の assert で落ちる |
| (a) 陰性2 | 位置依存の自動修復をしない | fixture に同一 `correction_id` を持つ2レコード（内容は異なる）を直接注入し、`repair_duplicate_correction_ids.py --id <cid> --apply`（`--keep` 無し）を実行 | **何も変更されない**（`--keep` 未指定は no-op）。ファイル内容が実行前後で完全一致することをハッシュで確認 | 「先頭を自動的に残す」実装（旧方針）は、この試験のハッシュ不一致 assert で落ちる |
| (a) 陽性対照 | 同上 | `--keep <正しい session_id#timestamp>` を指定して `--apply` | 指定した1件は ID 不変、もう1件は新しい ID を得る（かつ元の ID とは異なることを確認） | — |
| (b) 陰性 | 解決と書込みの間に他プロセスが削除しても、更新は正しいレコードだけに当たる | **実プロセス2つ**。fixture 3件（A/B/C）。子プロセスP1が
`update_reflect_status(filepath, [B の correction_id], "applied", ...)` を呼ぶ。
P1 はステップ1（読込み）完了後・ステップ3（書込み）前に `Event` で親へ
合図し、親からの「進め」合図を待つよう**テスト用フックで一時停止させる**
（本番コードに `sleep` を仕込まず、テスト時のみ差し込む同期ポイントとして
transform 呼出しの前後に optional callback を持たせる設計にする——本文中の
擬似コードには含めていないが実装1巡でテスト可能性のために追加する）。
親プロセスはその間に A を物理削除して `os.replace` で書き戻す。その後
P1 に「進め」を送る | P1 の最終結果: `status == "applied"`、ファイル上の
B の中身が正しく更新されている（`message` フィールドの一致で確認）。
**A の削除は結果に影響しない**（P1 は A の削除を知らないので、その削除は
巻き戻る=lost update——これは想定内。ここで確認するのは「B が正しく
更新されたこと」であり「A の削除が保持されたこと」ではない） | 旧設計
（呼出元スナップショットの index をそのまま使う実装）は、A 削除後は
B の index がずれるため、この試験で誤ったレコード（削除後に B の位置に
来た C）を更新してしまい、`message` の一致 assert で落ちる |
| (b) 陽性対照 | 同上 | 親プロセスの削除操作を行わない | P1 は通常どおり B を更新する | — |
| (c) 陰性1 | 欠落フィールドが偶然一致しない | `correction_id` キー自体が無い fixture レコードに対し `resolve` 相当の内部関数を `""`/`None` で呼ぶ | `invalid_id`。レコードが `None` で返る | `rec.get("correction_id", "") == cid` 型の実装は空文字列引数で欠落レコードにマッチするため、この試験で落ちる |
| (c) 陰性2 | `{"correction_id": ""}`/`null`/非文字列が「キーあり」として通らない | この3種を混在させた fixture で `validate_unique_ids` を呼ぶ | 3種とも集計に含まれない（戻り値の辞書に現れない） | 存在チェックのみの実装は3種を有効として数え、件数 assert で落ちる |
| (c) 陽性対照 | 同上 | 妥当な `correction_id` を持つレコード1件 | 正しく検出される | — |
| (d) 陰性 | 新フィールド追加が pass-through reader（レコード全体を運ぶ経路）を壊さない | `_build_prompt`・`_summarize_checkpoint_for_output` に `correction_id` 付き fixture と無し fixture を渡し、出力文字列を比較する。**「含まれる」ことを確認する**（§7 の受容判断が実装で骨抜きにされていないかの検査） | `correction_id` の値が出力文字列に含まれる。それ以外の出力構造は fixture 間で一致 | 意図せず `correction_id` を除外するフィルタが混入した実装は「含まれる」assert で落ちる |
| (d) 陽性対照 | 同上 | `reflect_status` の値自体を変えた fixture | 出力が変わる（比較ロジックが差を検出できることの対照） | — |
| (e) 陰性1（書込み開始後・置換前の中断） | 中断で部分状態が生じない | `os.fdopen`（tempfile への書込み）の途中で例外を注入する failpoint を仕込み、`update_reflect_status` を呼ぶ | 元ファイルのハッシュが処理前と一致（無傷）。`status == "retry_required"` | 空の transform で「何も変わらない」ことだけを見る試験（トートロジー）は避け、**実際に対象レコードを1件変更する transform を使い**、書込みの実処理の途中に failpoint を置く |
| (e) 陰性2（**ステップ4が効いていることの確認**） | 書込み後の検証が実際に lost update を検出する | `update_reflect_status` のステップ3（`os.replace`）完了**直後**・ステップ4（読み直し検証）**開始前**に、テスト用フックで一時停止させ、その間に**第三者プロセスがファイルを書き換えて自分の変更を巻き戻す**（対象レコードの `reflect_status` を元の値に戻して `os.replace`）。その後ステップ4を進めさせる | `status == "lost_update_detected"`、`lost` に対象 `correction_id` が含まれる。**成功ステータスを返さないこと**を明示的に assert する | ステップ4（読み直し検証）自体を省略した実装、または検証はするが結果を無視して常に成功を返す実装は、この試験の `status != status_expected_success` という assert で落ちる（これが「4が効いていることを、書込み後に第三者が上書きする順序で赤くする試験」の直接該当） |
| (e) 陽性対照 | 同上 | 競合を発生させない単純な更新 | `status == status`（例: `"applied"`）、`lost` は空 | — |
| (f) は blocking から削除。対応する試験なし（ID を手編集で消したら新 ID が振られることを§5.1のロジックがそのまま行う——「未移行」と区別しない設計そのものが期待結果なので、区別できないことを確認する陰性試験は存在しない。代わりに§5.1の「既に有効な ID を持つレコードはスキップする」冪等性を(g)側で確認する） | | | | |
| (g) 陰性1 | 未完了が成功として返らない | 移行実行中、書込み（`os.replace`）**直後**・読み直し検証**前**に、テスト用フックで一時停止させ、その間に第三者プロセスが**新規レコード（ID 無し）を追記**する。読み直し検証を進めさせる | `status == "incomplete"`、`still_missing >= 1` | `status` を bool 1個に潰す実装、または読み直し検証をしない実装は、この試験の文字列一致 assert で落ちる |
| (g) 陰性2 | 衝突が検出される | 書込み直後・検証前に、第三者プロセスが**移行対象と同じ新規 ID を持つレコード**を追記する（衝突を人為的に作る） | `status == "conflict"`、`duplicates` に該当 ID が含まれる | 重複チェックをしない実装はこの試験で `completed` を返し assert で落ちる |
| (g) 陽性対照 | 同上 | 競合の無い正常な241件相当 fixture | `completed`、`newly_assigned` が期待件数と一致 | — |
| (h) 陰性 | 妥当性・一意性判定が単一ソースでない実装との差分を検出する | `append_correction_record`・`update_reflect_status`・`migrate` の3箇所が、`inspect` により**同じ** `validate_correction_id`/`validate_unique_ids` 関数オブジェクトを import していることを機械的に確認する | `is` 比較で全て一致 | 1箇所でも独自の正規表現・独自ロジックに置き換わっていたら `is` 比較の assert で落ちる |
| (h) 陽性対照 | 同上 | 3箇所とも正しく import している設計どおりの実装 | 一致 | — |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `update_reflect_status` のステップ4（読み直し検証）を丸ごと削除し、常に
  ステップ3の成功だけで `status=status` を返す変異ビルドを作り、(e) 陰性2が
  赤くなることを実際に確認する（**巡1で挙げた変異は論理検算の結果、実際には
  対応する陰性試験を赤くしないことが判明した反省を踏まえ、変異を適用する
  *前に*「この変異はどの陰性試験のどの assert に触れるか」を1行で書いてから
  適用する）
- `append_correction_record` の重複チェック（§3.2 のステップ2）を削除した
  変異ビルドを作り、(a) 陰性1が赤くなることを確認する

**探索したが未探索のまま残すクラス**: 同一 `correction_id` を持つレコードが
2つとも移行対象（ID 無し）だった場合に、移行スクリプトが2つに**別々の新 ID**を
発行することの確認（これは重複の「発生」ではなく「解消」になるはずだが、
`existing_ids`/`assigned_ids` の集合更新順序に依存するため実装1巡で境界値
確認が要る）／`--resolve-id` が同時に複数の CLI プロセスから呼ばれた場合の
出力の一貫性（読取専用なので実害は無いはずだが未検証）／`tempfile.mkstemp`
が返すパスが `corrections.jsonl` と異なるファイルシステム上にある場合の
`os.replace` の atomic 性（`dir=str(filepath.parent)` を指定しているため
通常は同一ファイルシステムになるはずだが、`DATA_DIR` がマウント境界を
またぐ環境は未検証）。

## 10. 自己検証: この設計が成立しなくなる入力・順序・中断点（3件以上）

1. **`--skip-all` のバッチ処理中に、対象の一部だけが他プロセスと競合する**:
   §8 で述べた通り `--skip-all` は複数 `correction_id` を1回の
   `update_reflect_status` 呼出しに渡す。ステップ4の検証は ID ごとに行うため、
   一部の ID だけ `lost_update_detected` に分類され、残りは成功する
   （`UpdateResult.applied`/`lost` が両方非空になりうる）。**設計の答え**:
   これは意図した挙動（部分成功を隠さない）だが、CLI 側（`reflect.py:1407-1421`
   相当の改修）は `lost` が非空なら**全体を非0終了させ**、`applied` に
   含まれる分も含めて「何が成功し何が失敗したか」を両方出力する必要がある
   ——実装1巡でこの出力仕様を確定させる（本設計では確定させていない、
   §11で人間に確認する）
2. **移行スクリプトと `update_reflect_status` が同時に実行される**:
   移行は「ID 無しレコードへの新規付与」、`update_reflect_status` は
   「既存 ID を持つレコードの `reflect_status` 更新」であり、対象とする
   フィールドが異なるレコードなら競合しない。しかし**同じレコード**が
   「移行対象（ID 無し）」かつ「たまたま `--apply-id` の対象」になることは
   構造的にありえない（`--apply-id` は ID を指定するので、ID の無いレコードを
   指定できない）。**唯一の競合点**は、移行がステップ3で `os.replace` する
   瞬間に `update_reflect_status` も同時に `os.replace` している場合——
   どちらか片方が完全に上書きされる（lost update）。移行側はステップ4の
   読み直し検証で `incomplete`/`conflict` を検出できるが、`update_reflect_status`
   側も同時に自分のステップ4で `lost_update_detected` を検出しうる
   ——**両方が「自分は失敗した」と正直に報告する**ことになり、データの
   整合性（別レコードを指す、という意味での blocking b）は破れないが、
   **両方の操作が失われる**という最悪ケースが起こりうる。設計はこれを
   検出可能な形で許容する（防止しない、と明記済み）
3. **`--resolve-id` が返した `correction_id` を、別の重複解消（§3.3）が
   その直後に無効化する**: 利用者が `--resolve-id` で ID を取得し、それを
   `--apply-id` に渡すまでの間に、誰かが `repair_duplicate_correction_ids.py`
   を実行してその ID を（重複の non-primary 側として）別の ID へ差し替えると、
   `--apply-id` は `not_found` を返す。**設計の答え**: これは正しい fail-closed
   の挙動（存在しない ID を指定したのと区別しない）——利用者は
   `--resolve-id` を再実行して現在の ID を取り直す必要がある。UX 上の
   不便さは残るが、blocking (c)（黙って別レコードに一致しない）は守られている
4. **`corrections.jsonl` が巨大化した場合の O(N) 全件読込みの繰り返し**:
   §4.2 は1回の呼出しで最低2回（ステップ1・ステップ4）、§5.1 の移行は
   最低2回（読込み・検証）全件読込みを行う。241件では無視できるが、
   数万件規模では性能問題になりうる。**設計の答え**: 本設計は241件という
   実測規模（§1.1）を前提にしており、スケール時の再設計は§11で提起する

## 11. やらないこと（完成条件③の対象外の再掲）

- 柱2の集計・表示の変更 / 反映イベントの追記と read 時 fold（#587）/
  `reflect_status` の意味論変更 / `#379` 新設凍結の解除
- **他経路（§1.3 の8関数）の lost update の根治**（排他制御の新設）。
  検出（§4.2 ステップ4・§5.1 のステップ4）のみ行う
- `--correction-id` 参照元の自動追跡・書き換え（§3.3）
- 移行スクリプトの数万件規模対応（§5.3・§10 未探索クラス）
- `load_corrections`/`extract_pending` の非 dict クラッシュ耐性（§5.4 で
  本設計のコードパスには問題が残らないことのみ確認し、既存 CLI 表示パス
  自体は改修しない）

## 12. 人間の判断が要る点

1. **§10-1（`--skip-all` の部分失敗）**: CLI の出力仕様（何を成功、何を
   失敗として exit code に反映するか）を実装1巡でどう確定させるか
2. **§3.3（重複解消の UX）**: `--keep` の指定を人間に強制する設計は安全だが、
   重複件数が多い場合に運用負荷が高い。バッチでの半自動化（例: 全件
   `session_id`/`timestamp` の新しい方を自動的に primary とする、といった
   決定論的だが位置に依存しない規則）を認めるか
3. **§9 の「未探索のまま残すクラス」**（同時発行時の別ID割当ての境界確認、
   マウント境界をまたぐ `tempfile`）を実装1巡の必須検証に含めるか
4. **§2 で受容した lost update の限界**: 根治（別 issue）の優先度をどう
   位置づけるか。本 issue が生む「検出はするが防止しない」状態を
   どの程度の期間許容するか
5. **#587 との統合順序**: 本設計を先にマージし、#587 再開時に
   `source_correction_id` 依存部分を `correction_id` ベースに差し替える、
   という順序でよいか
