# #593: correction レコードに不変 ID を発行する設計（第5版）

> **巡1〜4（`8d2e0b44`／`fe8349f8`／`aa24e734`／`1fe89962`）から継続**。巡4は
> `設計修正要`（[Must] 13）だったが**骨格（ID発行・1回限りの移行・読取専用
> resolverの3点に絞る方針）は否定されなかった**。ユーザー裁定でスコープは
> 変えず、指摘13件＋[Should]6件＋[Nit]2件を反映する。**本設計は総上限7巡中の
> 設計5巡目であり、これが最後の設計巡**（`実装着手可` にならなければ人間の裁定）。
> レビュー全文: `/Users/matsukaze-takashi/.codex-watch/rev593g-20260901-095443-20806.report`。

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

## 0. Round 0 完成条件（verbatim・巡4から変更なし）

### ① 守る対象

correction レコードの個体を、ファイル内の位置に依存せず一意に指せる**識別子**を
持たせること。**この識別子を使って更新を安全に行う方法は本 issue の対象外**
（#587 へ送る）。

### ② 信頼境界

自分たちの運用ミスのみ（手編集 / 別プロセスの追記 / 中断 / 同時に走る2つの更新 /
移行スクリプトの未実行 / 再 ingest による重複）。悪意ある偽装・第三者の改竄は脅威に
数えない。

### ③ 対象外

- 柱2の集計・表示（`results_board`）の変更
- 反映イベントの追記と read 時 fold、およびそれを用いた**更新経路の安全化**
  （#587 の本体。`update_reflect_status`・`--apply`/`--skip`/`--skip-all` は
  **一切触れない**）
- `reflect_status` の意味論変更 / `#379` 新設凍結の解除
- **blocking (b)（削除・並べ替えで別レコードを指す）** — #587 が担当
- 他経路の lost update（防止・検出とも） / **重複修復（duplicate repair）
  機能そのもの** — 既存重複は報告して止めるだけ

### ④ スコープ（これだけ）

1. ID の発行 — 新規レコードの追記時に不変 ID を付与する
2. 既存241件への付与 — 1回きりの移行。**移行中は他の書込みを止める運用契約**を
   前提とし、契約違反は**事後に必ず検出できる**機械的な保険を持つ（巡4で追加）
3. ID から対象レコードを解決する resolver — **読取専用**。更新はしない

### ⑤ blocking

- (a) 同一 ID が2件以上存在しうる。**新規に発行する ID が重複を作ることを、
  保存前に拒否する**（既存の重複——本設計より前から存在するもの——を検出・
  解消することは対象外。§2.5 で境界を明示する）
- (c) ID なし・空文字列・`null`・非文字列が黙って通る。**保存境界の検証は
  無条件で行う**（フィールドの有無で分岐しない）
- (d) レコード全体を下流へ運ぶ既存 reader を壊す
- (e) 移行の中断で ID だけ／本体だけ書かれた状態が成功扱いになる
- (g) 移行の終了結果が完了・未完了・衝突・要再試行を区別しない。dry-run を
  `completed` と呼ばない。malformed・非 dict 行は「correction record では
  ない」という契約を明記した上で `malformed_lines` に返す
- (h) 妥当性・一意性の契約が単一ソースでない。**追記・移行・resolver の3者が
  必ず同じ関数を通る**

### ⑥ 検証方法

検証単位は「追記→保存→読み直し→ID 解決」と「移行→保存→読み直し→検証」の
実経路に置く。(a) は同じ ID を2プロセスが同時に追記しようとする順序を、
**実際にロック待ちで止まることを確認した上で**決定論的に再現する（正しい
実装では不可能な順序を試験の前提にしない）。(e) は書込みが**実際にバイト列を
tempfile へ書き始めた後**・置換前に failpoint を注入する。「書かない関数が
書かないことしか見ない」トートロジーを置かない。「読取専用」は純関数だけでなく
実ファイルへの操作で試す。陽性対照は値の型・形式・一意性・既存 ID の不変・
無関係フィールドの不変まで assert する。各試験について「緑のまま通る実装変異」を
自分で構成し、論理で検算する——**正しい実装のもとで到達不能な前提を試験に
置かない**（巡4の反省）。

## 1. 現状（自分で数え直した file:line つき）

### 1.1 実データ

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z（巡1〜4と同一データにつき再測不要）。

### 1.2 新規レコードを作る writer（本設計が改修する4経路）

| # | file:line | 何を作るか |
|---|---|---|
| W1 | `hooks/correction_detect.py:131-164`（`store_write("corrections.jsonl", record)`、line 164） | hook 検出による新規レコード。`hooks/hooks.json:3-16` の `UserPromptSubmit` に登録され、**すべてのプロンプト送信ごとに自動起動する**（確認済み） |
| W2 | `scripts/lib/correction_semantic/promote.py:346-393`（`_build_correction_record`）→ `:545-568`（`store_write`/`store_write_raw`） | weak signal 昇格による新規レコード |
| W3 | `scripts/backfill_preceding_tool_calls.py:230-255`（`persist_to_corrections`） | 過去セッションからの一括バックフィル（複数件を順に追記） |
| W4 | `scripts/migrate_reflect_queue.py:94-127`（`migrate`、追記は line 118-121、その後 line 127 で元 queue を空にする） | `learnings-queue.json` からの1回限りマイグレーション（複数件を順に追記後、元ファイルを空配列にする） |

### 1.3 共通書込みゲート（`store_write`/`store_write_raw`）の現物確認

```python
# scripts/lib/rl_common/store_write.py:77-104（store_write・現行）
def store_write(store_name: str, record: dict, *, guard_mode=None) -> None:
    ...
    import rl_common
    from rl_common import append_jsonl
    rl_common.ensure_data_dir()
    append_jsonl(rl_common.DATA_DIR / store_name, record)   # 戻り値を捨てている

# scripts/lib/rl_common/store_write.py:141-163（store_write_raw・現行）
def store_write_raw(filepath: Path, record: dict, *, guard_mode=None) -> None:
    ...
    from rl_common import append_jsonl
    append_jsonl(filepath, record)   # 同じく戻り値を捨てている
```

**`append_jsonl` は `corrections.jsonl` 専用ではない**。`scripts/lib/rl_common/__init__.py:198-205`
の export を経由し、`usage.jsonl`・`errors.jsonl`・`sessions.jsonl` など約30の
store（正確な数は `store_registry` の宣言に依存し本文書では未計測——ここでは
「1つの専用関数ではなく共有インフラである」ことのみを file:line で確認する）が
同じ関数を使う。**この共有関数へ correction 専用の必須 ID 検証を直接埋め込むと、
他ストアには ID フィールドが無いため検証を無効化する分岐が必要になり、それが
`None` を素通りさせる穴の温床になる**（巡4のレビュー Q1(c)・Q6[Must] の核心）。

### 1.4 現行の追記関数（改修対象）

```python
# scripts/lib/rl_common/persistence.py:154-172（append_jsonl・現行）
def append_jsonl(filepath: Path, record: dict) -> None:
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)       # line 160
            try:
                is_new = f.tell() == 0
                f.write(json.dumps(record, ensure_ascii=False) + "\n")  # line 163
            finally:
                if _HAVE_FCNTL:
                    _fcntl.flock(f, _fcntl.LOCK_UN)   # line 166
        ...  # ← f.write のバッファが実際に flush されるのはここ（with を抜けた時点）
```

**`flock(LOCK_UN)`（line 166）は `TextIOWrapper` のユーザー空間バッファを flush
しない**。実際の flush（＝他プロセスから見えるようになる）は `with open(...)`
ブロックを抜ける時点（line 158-167 の外）である。ロックを解放してから flush
するまでの間に別プロセスがロックを取得して読むと、**まだ見えていないはずの
自分の書込みを見逃した状態で重複確認を行い、同じ ID を書いてしまう**
（巡4レビュー Q3[Must]の核心）。

## 2. ID の発行

### 2.1 単一ソースの妥当性・重複判定（blocking c・h）

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新しいストアではない・§5）
import re
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

def new_correction_id() -> str:
    return uuid.uuid4().hex

def validate_correction_id(value) -> bool:
    """correction_id として有効な形式か判定する単一ソース。
    None・空文字列・null・非文字列・不正フォーマット（長さ違い・大文字混在含む）は
    すべて False。"""
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))

def has_duplicate_id(records: list[dict], correction_id: str) -> bool:
    """records の中に correction_id と一致する有効な ID を持つレコードが
    既にあるかを判定する（1件の ID に対する判定・追記の保存境界が使う）。"""
    return any(
        isinstance(r, dict) and validate_correction_id(r.get("correction_id"))
        and r["correction_id"] == correction_id
        for r in records
    )

def find_duplicate_ids(records: list[dict]) -> dict[str, int]:
    """records 全体を走査し、有効な correction_id のうち2回以上出現するものだけを
    返す（複数件の一括判定・移行の事前検査が使う）。`has_duplicate_id` と同じ
    predicate（`validate_correction_id`・完全一致）を使う——巡4レビュー Q1(h)
    「一意性が二重実装」の指摘への対応。移行はこの関数を呼ぶだけにし、独自の
    `seen`/`duplicates` ループを持たない。"""
    counts: dict[str, int] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        cid = r.get("correction_id")
        if validate_correction_id(cid):
            counts[cid] = counts.get(cid, 0) + 1
    return {cid: n for cid, n in counts.items() if n > 1}
```

**3者の対応（拒否／置換／除外）は用途に応じて意図的に異なるが、判定そのもの
（有効か・重複か）は上記2関数に統一する**（append・migrate・resolver が
`is` 比較で同一関数オブジェクトを import していることをテストで確認する・§9）。

### 2.2 保存契約の分離: `append_jsonl` を汎用のまま保ち、correction 専用の
検証は別関数に置く（Q6[Must]・Q1(c)[Must]の対応）

**`append_jsonl` 自体（約30ストア共有）には correction 専用の ID 検証を
埋め込まない**。かわりに、`append_jsonl` へ**汎用の**「一意キー確認つき追記」
オプションを追加し（他ストアが使わなければ既定どおり単純追記のまま——後方
互換）、correction 専用の必須検証は**別の薄いラッパー関数**に集約する。

```python
# scripts/lib/rl_common/persistence.py（改修後の append_jsonl・設計のみ）
@dataclass
class WriteResult:
    status: str  # "written" | "duplicate" | "retry_required"
    error: Optional[str] = None

def append_jsonl(
    filepath: Path,
    record: dict,
    *,
    unique_field: Optional[str] = None,   # 既定 None＝既存30ストアは無改修で動く
) -> WriteResult:
    """JSONL ファイルに1行追記する。unique_field を指定すると、ロックを保持した
    まま「その field の値が既存レコードと重複していないか」を確認してから書く
    （blocking a）。unique_field=None（既定）のときは従来どおり無条件追記——
    corrections.jsonl 以外の約30ストアはこの引数を渡さないため挙動は不変。"""
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)
            try:
                if unique_field is not None:
                    # 保存境界（blocking a）: ロックを手放さず読み直す。同一
                    # inode に対する flock を保持している間、他の append_jsonl
                    # 呼出し（同じロックを待つ）は進めない。
                    existing = _read_records_locked(filepath)  # isinstance(dict) フィルタ済み
                    val = record.get(unique_field)
                    if any(isinstance(r, dict) and r.get(unique_field) == val
                           for r in existing):
                        return WriteResult(status="duplicate")
                is_new = f.tell() == 0
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()  # ← 巡4 Q3[Must]: unlock 前に flush してバッファを確定させる
            finally:
                if _HAVE_FCNTL:
                    _fcntl.flock(f, _fcntl.LOCK_UN)
        if is_new:
            try:
                filepath.chmod(0o600)
            except OSError:
                pass
        return WriteResult(status="written")
    except OSError as e:
        return WriteResult(status="retry_required", error=str(e))
```

```python
# scripts/lib/rl_common/correction_id.py（続き）
@dataclass
class AppendResult:
    status: str  # "appended" | "invalid_id" | "duplicate_id" | "retry_required"
    error: Optional[str] = None

def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """corrections.jsonl 専用の保存境界。W1〜W4 が最終的に到達する唯一の入口。

    検証は**無条件**——record["correction_id"] が無い・None・空文字列・非文字列
    いずれでも invalid_id を返し、書込まない（巡4 Q1(c)[Must] の直接対応:
    「cid is not None のときだけ検証する」条件付き検証を廃止した）。"""
    cid = record.get("correction_id")
    if not validate_correction_id(cid):
        return AppendResult(status="invalid_id")
    result = append_jsonl(filepath, record, unique_field="correction_id")
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", error=result.error)
```

**なぜこれで blocking (a) を「新規追加分について」塞げるか（Q3[Must]の
修正込み）**: `flock(LOCK_EX)` を取得している間、重複確認
（`_read_records_locked`）と実際の追記（`f.write`）に加え、**`f.flush()` も
同じロック保持区間・同じ `finally` の手前に置く**（改修版コード参照）。
これにより「ロック解放後・flush 前に別プロセスが割り込む」という Q3 の
反例（P1 が write 後 flush 前に unlock し、P2 がまだ見えていない状態で
重複無しと確認してしまう）が構造的に発生しない——**flush はロック保持中に
完了しているため、ロックを解放した時点で自分の書込みは既に他プロセスから
見える状態になっている**。

### 2.3 失敗 status を W1/W2 へ届ける（Q3[Must]・巡4項目13）

`store_write`/`store_write_raw` は現行 `-> None` で `append_jsonl` の戻り値を
捨てている（§1.3）。**両関数の戻り値を `WriteResult`（§2.2）に変える**
（既存の約30ストアの呼出し元が戻り値を無視していても、Python は戻り値を
無視して呼ぶことができるため後方互換——既存呼出し元の改修は不要）。

```python
# scripts/lib/rl_common/store_write.py（改修後・設計のみ）
def store_write(store_name: str, record: dict, *, guard_mode=None) -> "WriteResult":
    ...
    return append_jsonl(rl_common.DATA_DIR / store_name, record,
                         unique_field=_unique_field_for(store_name))

def _unique_field_for(store_name: str) -> Optional[str]:
    """store 名から一意性検証の対象フィールドを引く単一ソース。corrections.jsonl
    以外は None（既存動作のまま）。新しいレジストリは作らず、本モジュール内の
    固定 dict とする（#379: 新設ストア・新設チャネルではなく、既存関数内の
    定数追加なので抵触しない）。"""
    return {"corrections.jsonl": "correction_id"}.get(store_name)
```

`store_write_raw` も同様に `WriteResult` を返す（`filepath.name` から
`_unique_field_for` を引く）。**W1（`correction_detect.py:164`）・
W2（`promote.py:565-568`）はこの戻り値を検査する**よう改修する:

```python
# hooks/correction_detect.py（改修後・該当箇所のみ）
result = common.store_write("corrections.jsonl", record)
if result.status != "written":
    print(f"[evolve-anything:correction-detect] 保存失敗: {result.status}", file=sys.stderr)
    return  # correction を記録せず終了（既存の「失敗時は静かに継続」慣習を踏襲しつつ、
            # 原因をログへ残す点だけ強化する）
```

`result.status` が `"duplicate"`（＝ここでは `unique_field="correction_id"`
指定時の重複）を返すのは、`new_correction_id()` の衝突（確率的に無視できる）か
実装バグを意味する——**警告ログを残すが、correction の記録自体は失敗として
扱い、後続処理（memory 書込み等）へは進まない**。W2 も同様の扱いとする。

### 2.4 (a) の境界を明示する（Q1(a)への対応）

**本設計が塞ぐのは「新規に発行する ID が既存レコードと重複することを保存前に
拒否する」ことだけである**。ファイルに**既に**重複が存在する状態（信頼境界②の
「手編集」等、本設計のコードが書く前から存在した重複）を検出・解消することは
**対象外**（round 0 ③「重複修復機能そのもの」が対象外——検出だけでも新しい
スキャン処理を追記のたびに全件へ行うことになり、W1（hook・全プロンプトで
自動起動）のたびに O(N) の全件走査が発生するコスト増を招く）。**§2.2 の
重複確認は「これから書こうとしている1件の ID」についてのみ既存レコードと
突合する**——ファイル全体に無関係な既存の重複が残っていても、その事実には
関与しない。この境界により「(a) は完全に閉じたか」という問いに対しては、
**「新規追加分について閉じている。既存の重複は§3の移行が事前検査で検出する
（新規に生成する ID 同士の衝突のみ）が、移行前から存在する任意の重複の解消は
本設計のスコープ外」**と答える。

`_HAVE_FCNTL=False`（`fcntl` 非対応環境）では、§2.2 の重複確認は「読んで
比較する」ことは行うが、**その読み直しと書込みの間の不可分性が失われる**
（既存 `append_jsonl` が既に持つ環境依存の限界——`persistence.py:11-15` の
フォールバック——を本設計はそのまま引き継ぐ。新しい環境依存を追加しない）。

### 2.5 W3・W4 の複数件追記と部分成功契約（[Should]）

W3（`backfill_preceding_tool_calls.py:246-255`）・W4
（`migrate_reflect_queue.py:118-127`）は複数件を1回のスクリプト実行で
追記する。§2.2 の `append_correction_record` を1件ずつ呼ぶよう改修すると、
**途中の1件が `duplicate_id`/`invalid_id`/`retry_required` を返す可能性が
生まれる**（従来の無検証追記には無かった失敗モード）。

**契約**: 1件でも失敗したら**その時点で処理を止める**（fail-fast。残りの
候補は追記しない）。戻り値に「成功件数・失敗した1件のインデックスと理由・
未処理件数」を含める:

```python
{"appended": 12, "failed_at_index": 12, "failure_reason": "duplicate_id", "remaining": 5}
```

**W4 固有の追加契約**: `migrate_reflect_queue.py:127`
（`LEARNINGS_QUEUE.write_text("[]", ...)`）は、**全件が `appended` を返した
場合にのみ**実行する。部分失敗（1件でも `appended` 以外）の場合は元 queue を
空にしない——空にしてしまうと、失敗した分のレコードが `corrections.jsonl`
にも `learnings-queue.json` にも存在しない状態（データ消失）になる。この
契約は W4 の既存コード（§1.2の`migrate`）に対する**振る舞い変更**であり、
実装1巡のレビュー観点に明記する。

## 3. 既存241件への移行

### 3.1 運用契約と、契約違反を事後に必ず検出する機械的保険（巡4 Q2・Must#6-8）

**運用契約**: 移行の実行中は corrections.jsonl への他の書込み（W1〜W4・
`update_reflect_status`・`prune`・その他の正規化スクリプト）を行わない
（「全 Claude Code セッションを閉じる／hook を一時停止する／daily・
backfill・prune・promotion を実行しない」という具体的な runbook として
実装1巡でドキュメント化する）。

**この契約は技術的に強制しない**（強制は共有ロックの新設を要し、巡2・巡3が
繰り返し当たった壁に戻るため——round 0 ④「共有ロックは新設しない」を維持）。
**かわりに、契約違反を必ず事後検出できる機械的な保険を置く**（巡4レビューが
明確に反例を示した「行数比較だけでは検出できない」問題への直接対応）。

**巡4で示された反例（行数ベースの旧設計が見逃す）**:
1. M（移行）が N 行を読む
2. H（hook 等）が1行追記し、ファイルは N+1 行になる
3. M が読んでおいた N 行から作った tempfile を `os.replace` する。H の1行が消える
4. M は置換後の N 行を読み、期待 N 行と一致するため `completed` を返す
   （**旧設計の欠陥**: H の追記が消えたことを見逃す）

**修正: `os.replace` の直前に、読込み時点のファイル identity と再照合する**
（行数比較を廃止し、`(inode, size, mtime_ns)` の stat 情報 **と** 内容の
SHA-256 ハッシュの両方を使う——stat だけでは「サイズが偶然一致する同時
追加+削除」を見逃しうるため、内容ハッシュで最終確認する）:

```python
def migrate(filepath: Path, *, dry_run: bool = True) -> "MigrationResult":
    if not filepath.exists():
        return MigrationResult(status="completed", total=0, newly_assigned=0)

    try:
        if filepath.is_symlink():
            # [Should] symlink: os.replace はエントリ自体を通常ファイルへ
            # 置換するため、symlink の意味（別ファイルへの参照）を壊す。
            # 移行は symlink を拒否する。
            return MigrationResult(status="conflict", reason="symlink_not_supported")
        orig_stat = filepath.stat()
        raw_content = filepath.read_text(encoding="utf-8")
    except OSError as e:
        return MigrationResult(status="retry_required", error=str(e))
    except UnicodeDecodeError as e:
        return MigrationResult(status="retry_required", error=str(e))

    orig_identity = (orig_stat.st_ino, orig_stat.st_size, orig_stat.st_mtime_ns)
    orig_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

    raw_lines = raw_content.splitlines()
    new_lines: list[str] = []
    newly_assigned = 0
    malformed = 0
    final_records: list[dict] = []  # 事前重複検査（find_duplicate_ids）用

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)  # correction record ではない。触らず温存
            malformed += 1
            continue
        if not isinstance(rec, dict):
            new_lines.append(line)  # scalar/list も record ではない
            malformed += 1
            continue

        cid = rec.get("correction_id")
        if not validate_correction_id(cid):
            rec = dict(rec)
            rec["correction_id"] = new_correction_id()
            newly_assigned += 1
        final_records.append(rec)
        new_lines.append(json.dumps(rec, ensure_ascii=False))

    # --- 保存境界（blocking a）: 単一ソースで事前重複検査 ---
    duplicates = find_duplicate_ids(final_records)   # §2.1 と同一関数
    if duplicates:
        return MigrationResult(status="conflict", total=len(raw_lines),
                                newly_assigned=0, duplicates=sorted(duplicates))

    if dry_run:
        return MigrationResult(status="dry_run", total=len(raw_lines),
                                newly_assigned=newly_assigned, malformed_lines=malformed)

    new_content = "\n".join(new_lines) + "\n" if new_lines else ""

    # --- 巡4 Q2/Must6-8: os.replace 直前に identity を再照合する ---
    try:
        cur_stat = filepath.stat()
        cur_content = filepath.read_text(encoding="utf-8")
    except OSError as e:
        return MigrationResult(status="retry_required", error=str(e))
    except UnicodeDecodeError as e:
        return MigrationResult(status="retry_required", error=str(e))
    cur_identity = (cur_stat.st_ino, cur_stat.st_size, cur_stat.st_mtime_ns)
    cur_hash = hashlib.sha256(cur_content.encode("utf-8")).hexdigest()
    if cur_identity != orig_identity or cur_hash != orig_hash:
        return MigrationResult(
            status="conflict", total=len(raw_lines), newly_assigned=0,
            reason="file changed between read and replace (identity/hash mismatch)"
        )
    # ここから os.replace までの間は CPU 命令のみで I/O を挟まない
    # （残存する競合窓の説明は本節末尾に明記する）。

    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(filepath.parent), suffix=".correction_id_migrate.tmp"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.chmod(tmp_path, stat.S_IMODE(orig_stat.st_mode))  # [Should]: mode ビットのみ継承
        os.replace(tmp_path, filepath)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return MigrationResult(status="retry_required", error=str(e))

    # --- 書込み後の軽量健全性確認（内容整合性まで見る）---
    try:
        verify_content = filepath.read_text(encoding="utf-8")
    except OSError as e:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason=f"post-write read failed: {e}")
    except UnicodeDecodeError as e:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason=f"post-write decode failed: {e}")
    if verify_content != new_content:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason="post-write content mismatch")

    return MigrationResult(status="completed", total=len(raw_lines),
                            newly_assigned=newly_assigned, malformed_lines=malformed)
```

**巡4の反例への回答（差し替え後）**: H が read（`raw_content` 取得）後・
`os.replace` 前に1行追記すると、その追記は `cur_stat`/`cur_content` の
再取得時点で `orig_identity`/`orig_hash` と一致しなくなる（サイズ・mtime・
内容ハッシュのいずれも変わる）。**したがって replace は行われず、
`status="conflict"` を返す**——H の追記は消えず、M は失敗として報告される
（`completed` を騙らない）。「行数チェックが保険になる」という巡4以前の
誤った説明はここで訂正する。

**残存する競合窓（明記する）**: 最終的な identity/hash 再照合
（`cur_stat`/`cur_content` の取得）と `os.replace` の呼出しの間には、なお
CPU 命令のみで I/O を挟まないごく短い時間窓が存在する。この窓の間に他の
書込みが割り込む確率は極めて低いが**ゼロではない**——本設計はこれを
「検出できない残存リスク」として明記する（round 0 対象外の「他経路の
lost update の完全な防止」に踏み込まない、という判断と整合する。これを
完全に閉じるには排他制御の新設が要り、それは対象外）。

### 3.2 5値の終了ステータスと CLI exit code（blocking g・[Should] 型固定）

```python
@dataclass
class MigrationResult:
    status: str  # Literal["dry_run", "completed", "incomplete", "conflict", "retry_required"]
    total: int
    newly_assigned: int
    malformed_lines: int = 0
    duplicates: list[str] = field(default_factory=list)
    reason: Optional[str] = None
```

`status` は Enum 相当の閉じた語彙5値とする（本節の見出しは「5値」に訂正する
——巡4 [Should] の指摘どおり、旧稿の見出し「4値」と本文の5値が食い違って
いた。dry-run は書込みを伴わない**別カテゴリ**であり、残る4値
（`completed`/`incomplete`/`conflict`/`retry_required`）が実際の書込み結果を
分類する、という位置づけを明記する）。

| status | 意味 | 必須フィールド | exit code |
|---|---|---|---|
| `dry_run` | `--dry-run`（既定）。書込みなし | `total`/`newly_assigned`/`malformed_lines` | 0 |
| `completed` | 書込み成功・事前重複検査通過・書込み後の内容一致確認済み | 同上 | 0 |
| `incomplete` | 書込みは成功したが、書込み後の再読込みで内容が一致しなかった、または読込み自体が失敗した | 同上 + `reason` | 1（再実行前に§3.1の契約違反が無かったか調査） |
| `conflict` | `os.replace` の**前**に、①新規発行 ID 同士の重複を検出、②identity/hash 再照合で不一致、③対象が symlink、のいずれかを検出。書込みは行っていない（元ファイル無傷） | `duplicates` または `reason` | 2（`--dry-run` を再実行して原因を調査。本設計に自動修復機能は無い） |
| `retry_required` | 読込み・stat・tempfile 作成/書込み/chmod・`os.replace` のいずれかで `OSError`/`UnicodeDecodeError` | `reason` | 3（元ファイルは無傷。そのまま再実行してよい） |

### 3.3 読込・検証エラーの対応表（Q1(g)[Must] 反映・try 範囲を全面へ拡張）

| 事象 | 結果 |
|---|---|
| ファイル不在 | `completed`（対象0件） |
| 対象が symlink | `conflict`（`reason="symlink_not_supported"`） |
| `filepath.stat()` の `OSError`（読込み前） | `retry_required`（§3.1 コード: `try` を stat 呼出しごと囲む） |
| ファイル読込み時 `OSError`/`UnicodeDecodeError`（読込み前） | `retry_required` |
| 行単位の `json.JSONDecodeError` | `malformed_lines` としてカウント、行は温存、ID 付与対象にしない |
| 行が dict でない（scalar/list） | 同上 |
| 事前重複検査（新規発行 ID 同士）で重複を検出 | `conflict`（書込みなし） |
| identity/hash 再照合で不一致（他の書込みが割り込んだ） | `conflict`（書込みなし） |
| `tempfile.mkstemp()` の `OSError` | `retry_required`（§3.1 コード: `try` を mkstemp 呼出しごと囲む） |
| tempfile 書込み/`os.chmod`/`os.replace` の `OSError` | `retry_required`（元ファイル無傷） |
| 書込み後の読込みで `OSError`/`UnicodeDecodeError` | `incomplete` |
| 書込み後の内容不一致（行数でなく全文比較） | `incomplete` |

### 3.4 中断耐性とtempfile残骸（blocking e・[Should]）

計算（読込み→変換、メモリ上のみ）と書込み（`tempfile` + `os.replace`）を
分離。計算中・identity再照合中に kill されれば元ファイルは無傷。`tempfile`
書込み中に kill されれば元ファイルは無傷（`os.replace` に未到達）。
`os.replace` 自体は OS レベルで atomic。**crash consistency の範囲は
プロセス kill・未処理例外までであり、OS クラッシュ・電源断は対象外**
（`fsync` を呼ばない——既存 atomic write パターンと同じ前提を踏襲するのみ）。

**tempfile 残骸**: kill が `os.replace` 到達前に起きると `.correction_id_migrate.tmp`
サフィックスの一時ファイルが `DATA_DIR` に残りうる（[Should]）。この
サフィックスにより、他の一時ファイル（`store_write` 等が使う無関係な
`.tmp`）と区別できる。実装1巡で「移行スクリプト起動時に、このサフィックスを
持つ古い残骸を検出して警告する（削除はしない——安全側）」ことをオプション
（`--check-stale-tmp`）として設計するかは§11で人間へ確認する。

**冪等性**: 「既に有効な `correction_id` を持つレコードはスキップ」
（`validate_correction_id(cid)` 分岐）により、中断後の再実行は常に安全
（最初からやり直すだけでよい。241件という規模で1回の実行に完結できるため
進捗マーカー方式は採らない）。

## 4. resolver（読取専用）

```python
# scripts/lib/rl_common/correction_id.py（続き）
@dataclass
class ResolveResult:
    status: str  # "found" | "not_found" | "ambiguous" | "invalid_id"
    record: Optional[dict] = None
    match_count: int = 0

def resolve_correction_id(records: list[dict], correction_id) -> ResolveResult:
    """records（すでに読み込まれた生配列）から correction_id 一致レコードを
    解決する。**読取専用**——呼出元がこの結果を使って何かを書く場合の安全性は
    一切保証しない（それは #587 の担当）。"""
    if not validate_correction_id(correction_id):
        return ResolveResult(status="invalid_id")
    matches = [
        r for r in records
        if isinstance(r, dict) and validate_correction_id(r.get("correction_id"))
        and r["correction_id"] == correction_id
    ]
    if not matches:
        return ResolveResult(status="not_found")
    if len(matches) > 1:
        return ResolveResult(status="ambiguous", match_count=len(matches))
    return ResolveResult(status="found", record=matches[0], match_count=1)
```

`append_correction_record`（§2.2）・`migrate`（§3.1）・`resolve_correction_id`
（本節）はいずれも `validate_correction_id`/`find_duplicate_ids`/
`has_duplicate_id`（§2.1）を import して使う——独自の正規表現・独自の一意性
ループを持たない。4関数が同一のモジュールから同一の関数オブジェクトを
import していることを `inspect` による同一性確認と、**`validate_correction_id`
を差し替えたときに全員が連動して挙動を変えること**の両方でテストする（§9）。

このモジュールに更新機能を追加しない——`resolve_correction_id` はレコードを
返すだけで、それをファイルへ書き戻す関数は本設計に存在しない。

## 5. `#379` 新設凍結への非抵触（維持・変更なし）

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`、72行目に
`"corrections.jsonl"`）・`:23-37`（凍結対象4集合）・`:261-275`
（`assert_no_new_keys`、ストア名等の文字列集合のみを検査）を巡1で実物照合
済み（巡2〜4のレビューでも覆っていない）。`correction_id` は既存ストアへの
新規フィールド追加のみ。§2.3 の `_unique_field_for` は `store_write.py`
内部の固定 dict であり、新しいレジストリ・新しいストア・新しい channel を
作らない。共有ロック・sidecar ファイルは第4版から一切新設していない。

## 6. 既存 reader の再列挙（blocking d — 3つのデータフロー経路として整理）

巡4レビュー Q4[Should]「列挙を実データフロー単位に直すべき」に従い、
`corrections.jsonl` から出た値が最終的にどこへ届くかを経路単位で整理する。

### 経路1: hook → auto-memory queue（永続化）→ drain → LLM prompt

`hooks/auto_memory_runner.py:57-90`・`:123-154`（`read_recent_corrections`/
`_load_all_corrections`）が corrections.jsonl から直近 N 件のレコード全体を
読み、`scripts/lib/auto_memory_broker.py:enqueue`（**確認済み** file:line
`auto_memory_broker.py:234-290` 付近。`corrections` フィールドとして
レコードのリストをそのまま `queue_path_for(...)` のファイルへ**永続化**する。
巡4レビュー Q4 が指摘した、旧版の列挙から欠けていた永続化ステップ）が
キューへ書く。後続の drain 処理（本文書では未追跡——キュー読出し側の
file:line は実装1巡で確認する）が `_build_prompt`
（`auto_memory_broker.py:322-335`、`json.dumps(corrections, ...)` で
**レコード全体**を LLM プロンプトへ直接埋め込む）へ渡す。**`correction_id`
はこの経路全体（queue 永続化・drain・prompt 埋め込み）に露出する**。

### 経路2: hook → checkpoint（永続化）→ SessionStart truncate 表示

`hooks/save_state.py:76-104`（`_load_corrections_snapshot`）が corrections.jsonl
全件を読み `corrections_snapshot` として checkpoint JSON へ**永続化**する。
`hooks/restore_state.py:41-77`（`_summarize_checkpoint_for_output`）が
直近 `MAX_SNAPSHOT_ITEMS=20`/`MAX_SNAPSHOT_CHARS=8000` 文字に truncate した
上で SessionStart の Claude context へ print する。**各 dict は投影
（allowlist）されず丸ごと truncate されるだけなので、残ったレコードの
`correction_id` は SessionStart context に露出する**。

### 経路3: reflect の allowlist 投影（影響なし）・resolver CLI（新設・投影する）

`skills/reflect/scripts/reflect.py:834-858`（`build_output`）は各レコードから
**明示したキーだけ**を新しい dict へコピーする（`index`/`message`/
`correction_type`/`confidence`/`importance_score`/`routing_hint`/
`suggested_file`/`duplicate_found`/`duplicate_in`/`extracted_learning`/
`preceding_tool_calls`/`line_limit_warning`/`source_correction_id`/`apply`/
`episodic_context` の allowlist、`reflect.py:834-871` で確認済み）——
`correction_id` はこのリストに無いため、**既存 `--view`/`--dry-run` 出力は
無改修で `correction_id` を露出しない**（§7 で新設する resolver CLI だけが
明示的に `correction_id` を出力する）。

`skills/genetic-prompt-optimizer/scripts/optimize_core.py:182-196`
（`build_patch_prompt`）も `corr.get("message")`/`.get("correction_type")`/
`.get("extracted_learning")` のみを参照し、レコード全体をダンプしない
——影響なし（`skills/evolve-loop-orchestrator/scripts/variant_generation.py:55-90`
は `collect_corrections` を呼ぶだけで同じ経路を通るため同様に影響なし）。

### 判断（維持）

経路1・2について、allowlist 投影を導入せず露出を明示的に受容する。
`correction_id` は32文字16進の固定形式文字列で注入攻撃の運び屋になりえず、
既存の自由記述フィールド（`message`/`extracted_learning`）よりリスクが低い。
サイズ影響は既存の truncate 機構に吸収される——ただし32文字増加により
`MAX_SNAPSHOT_CHARS=8000` の境界で従来より1件早く古いレコードが truncate
される可能性がある（[Should] 反映: これは truncate 契約内の挙動変化であり
不具合ではないが、実装1巡の回帰試験で境界値（ちょうど8000文字前後）を
確認することを完了条件に含める）。

## 7. ID を人間が取得する経路（`--resolve-source-id`・[Nit] 改名・[Must] 型安全化）

既存の pending 一覧表示（`--view`）・`--dry-run` 出力のフォーマットは変更しない
（§6 経路3で確認済み——`correction_id` は元々投影されない）。代わりに新設 CLI
サブコマンドを追加する。**巡4 [Nit]の指摘どおり、このコマンドは
`source_correction_id`（実質一意な旧来キー）を入力に取り、対応する不変
`correction_id` を返す——`--resolve-id` という名前は方向が曖昧なので
`--resolve-source-id` に改名する**（§4 の resolver は逆方向＝`correction_id`
を入力に取る、と明確に区別する）。

```python
def resolve_source_id(records: list[dict], source_correction_id: str) -> dict:
    """§4 の resolve_correction_id とは逆方向: source_correction_id
    （make_source_correction_id 形式）から一致する correction_id を引く。

    非 dict 行を安全に扱う契約（巡4 Q4[Must]・Q1(d) の直接対応）:
    load_corrections（reflect.py:111-124）は json.loads が成功すれば
    dict でない値（scalar/list）もそのまま配列に含める。本関数は records を
    走査する**すべての箇所**で isinstance(r, dict) を先に確認し、非 dict は
    候補にしない（§4 resolve_correction_id と同じガードを、逆方向の解決でも
    必ず適用する——旧版はこのガードを resolver 本体にしか書いておらず、
    本 CLI 側の疑似コードには書いていなかった、という巡4の指摘への対応）。
    """
    matches = []
    for r in records:
        if not isinstance(r, dict):
            continue
        sid = r.get("session_id", "")
        ts = r.get("timestamp", "")
        if sid and ts and make_source_correction_id(sid, ts) == source_correction_id:
            cid = r.get("correction_id")
            matches.append({
                "correction_id": cid if validate_correction_id(cid) else None,
                "session_id": sid, "timestamp": ts,
            })
    if not matches:
        return {"status": "not_found", "matches": []}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": matches}
    return {"status": "found", "matches": matches}
```

CLI: `reflect.py --resolve-source-id <source_correction_id>` は上記関数の
結果を機械可読 JSON で出力する。malformed JSON・空行・dict でない JSON・
正常な dict の4種を含む実ファイルに対する CLI 経由の試験を実装1巡に含める
（巡4 Q4[Must]の要求）。**この CLI コマンドも読取専用**であり、何も書き込まない。

## 8. やらないこと（完成条件③の対象外の再掲）

- 柱2の集計・表示の変更 / 反映イベントの追記と read 時 fold（#587）/
  `reflect_status` の意味論変更 / `#379` 新設凍結の解除
- `update_reflect_status`・`--apply`/`--skip`/`--skip-all` の変更
- blocking (b)（削除・並べ替えで別レコードを指す）の解消 — #587 の担当
- 他経路（`update_reflect_status`・`prune`・`promote` の idiom invalidation・
  各種正規化スクリプト）の lost update（防止・検出とも） — §3.1 の運用契約と
  identity/hash 再照合に委ねる（移行**自身**の安全性のみ保証する。移行
  **以外**の書込み同士の lost update は対象外のまま）
- 重複修復機能 — `conflict` の検出のみ行い、既存重複を自動的に解消する
  スクリプトは作らない

## 9. 検証計画（巡4 Q5 の指摘をすべて反映して作り直す）

検証単位は「追記→保存→読み直し→ID 解決」（`append_correction_record`）と
「移行→保存→読み直し→検証」（`migrate`）の実経路に置く。テストは
`scripts/lib/rl_common/tests/test_append_jsonl_correction_id.py`・
`scripts/lib/tests/test_correction_id.py`・
`scripts/tests/test_migrate_correction_id_backfill.py`（いずれも新設）に置く。

| # | 壊す不変条件 | 変異（実経路・巡4の指摘を反映） | 期待結果 | この試験を「緑のまま通す」実装変異（自己検証・論理で検算） |
|---|---|---|---|---|
| (a) 陰性 | 同じ ID の同時追記を、ロック内の不可分な確認+flush で拒否する | **実プロセス2つ**。P1 が同じ `correction_id` を持つレコードで `append_correction_record` を呼び、テスト用フックで**重複確認を終えた直後・`f.flush()`+unlock の前**に一時停止する（巡4 Q3 の指摘どおり、check と flush の間を同期点にする）。この間に P2 が同じ ID で `append_correction_record` を呼ぶ——**P2 が `flock` を取得できずブロックされていることを、`multiprocessing.Event` 等で明示的に確認する**（正しい実装では P2 は完走できない、という前提を試験内で検証してから先へ進む——巡4「試験順序が成立しない」への直接対応）。P1 を再開させ flush+unlock させた後、P2 が進んで結果を得る | P1 は `appended`。P2 は `duplicate_id`（P1 の flush 済みの内容を読めるため）。ファイルにはその ID を持つ行が**1行だけ**存在する | `flush()` を呼ばない変異（旧設計）に対しては、P1 の再開後に flush 前の状態のまま P2 を進めた場合に2行（重複）が生じうることを、上記の「P2 がブロックされることを確認してから進める」手順自体で検出する（P2 のブロック確認が無い試験は、flush 有無の差を検出できない——これが巡4の核心指摘なので、ブロック確認を試験の必須ステップとして明記する） |
| (a) 陽性対照 | 同上 | 異なる `correction_id` を持つ2レコードで同じ手順（ブロック確認はしない——正常系なので競合しない） | 両方とも `appended`。ファイルに2行とも存在する | — |
| (a) 陰性2（移行・新規発行 ID の衝突） | 事前検査は「新規に付与する ID 同士」の衝突も検出する（巡4「既存 ID 同士しか検査しない変異でも緑になる」への対応） | `new_correction_id` を**テスト用に固定値を返すよう monkeypatch**し、移行対象（ID 無し）のレコードを2件以上含む fixture で `migrate` を呼ぶ——2件とも同じ固定値の ID を新規発行させる | `status == "conflict"`。元ファイルのハッシュが実行前後で一致（無傷） | 「既存の有効 ID 同士」だけを検査し新規発行分同士を検査しない実装は、この monkeypatch 前提の試験で重複を見逃し `completed` を返すため、status assert で落ちる |
| (a) 陽性対照2（移行） | 既存の有効 ID は不変のまま維持される | 既に有効な `correction_id` を持つレコードを含む fixture で `dry_run=False` の移行を実行し、実行前後でその ID の値が**一致する**ことを明示的に assert する（「全件一意」だけでなく「既存分は変わっていない」を確認——巡4の指摘） | 既存の有効 ID は不変。新規分のみ ID が追加される | 既存 ID まで毎回振り直す実装は、この「不変」assert で落ちる |
| (c) 陰性1（保存境界・実ファイル） | 欠落フィールド・`null` が実際に保存されない | `record` に `correction_id` キーが無い場合、`{"correction_id": null}` の場合の両方で `append_correction_record(filepath, record)` を実ファイルに対して呼ぶ | `status == "invalid_id"`。**ファイルの行数が実行前後で変化しない**ことを確認する（validator 単体でなく保存境界そのものを実ファイルで試験——巡4「resolver だけ正しくても append が受理する現設計のままで緑」への対応） | 保存境界の検証が resolver 側にしか無い実装（append 側が無条件で書く）は、この行数不変 assert で落ちる |
| (c) 陰性2 | `{"correction_id": ""}`/非文字列/不正形式（31文字・33文字・大文字混在の16進）が「有効」として通らない | この5種（空文字列・非文字列・31文字hex・33文字hex・大文字混在hex）を混在させた fixture で `append_correction_record` を個別に呼ぶ | 全て `invalid_id`。ファイル行数不変 | `len(value) > 0` のような緩い判定は空文字列以外の不正値を通すため、31/33文字・大文字混在のケースで assert が落ちる |
| (c) 陽性対照 | 同上 | 妥当な `correction_id`（32文字小文字16進）を持つレコード1件を実ファイルへ追記する | `status == "appended"`。行数が1増える | — |
| (d) 陰性 | 新フィールド追加が実データフロー経路を壊さない | **実経路**（fixture のレコードを `append_correction_record` で実ファイルへ書く→§6経路1相当のキュー永続化関数→`_build_prompt` の順に**実際に通す**。直接 fixture dict を `_build_prompt` へ渡すだけの試験は行わない——巡4「途中の queue/checkpoint を経由しない試験は緑になる」への対応） | `correction_id` の値が最終的な LLM プロンプト文字列に含まれる | queue 永続化関数が新フィールドを黙って drop する実装が混入した場合、この経路全体を通す試験でのみ検出できる（fixture 直渡しでは検出できない） |
| (d) 陽性対照 | 同上 | `message` のみ変えた fixture と `reflect_status` のみ変えた fixture を**それぞれ独立に**用意し、出力がそれぞれ独立に変わることを確認する（巡4「両方同時に変える fixture は片方しか見ない実装でも緑になる」への対応） | 各 fixture で出力が変わる | `message` だけを見る実装は `reflect_status` のみ変えた fixture で出力不変となり assert が落ちる（逆も同様） |
| (e) 陰性1（書込み開始後・置換前の中断） | 中断で部分状態が生じない | `os.fdopen` で得たファイルオブジェクトの `write` を、**実際に一部バイトを書いた後に** `OSError` を送出するよう差し替える（`write` 呼出し自体をトラップするだけの monkeypatch ではなく、prefix を実際に tempfile へ書いてから例外を出す——巡4「1byteも書いていない可能性がある」への対応）。対象レコードを実際に1件以上変更する fixture を使う | 元ファイルのハッシュが処理前と一致。`status == "retry_required"`。tempfile が残っていれば内容が部分的であることを確認する（残っていてもいなくても、元ファイルへの影響が無いことが本質） | 空の transform で「何も変わらない」ことだけを見る試験は避けている（fixture は実際に変更を含む）。tempfile 書込み前に元ファイルを直接書き換える実装（非 tempfile パターン）に変異させ、この試験のハッシュ不一致で落ちることを確認する |
| (e) 陰性2（identity/hash 再照合が効いていることの確認） | replace 直前の再照合が、行数を変えない改変も検出する | §3.1 の identity 再照合ステップの**直前**で一時停止するテスト用フックを使い、その間に第三者プロセスが**1行追記すると同時に1行削除**する（合計行数を維持したまま内容を変える——巡4「行数一致の変異は検出できない」への直接対応）。その後再照合を進めさせる | `status == "conflict"`（内容ハッシュが不一致のため、行数が同じでも検出される） | stat（inode/size/mtime）だけを見て内容ハッシュを見ない実装は、サイズが偶然一致するこの変異で検出に失敗し `completed` を返すため、status assert で落ちる |
| (e) 陽性対照 | 同上 | 競合を発生させない単純な移行実行。**書込み後、変更されなかった既存レコードの `message`/`session_id` 等が実行前後でバイト単位で一致する**ことを確認する（巡4「行数・ID件数だけ見る対照では内容破壊を見逃す」への対応） | `completed`。無関係フィールドは完全不変 | ID 追加時に無関係フィールドを正規化（キー順ソート等）してしまう実装は、この対照のバイト一致 assert で落ちる |
| (g) 陰性・dry-run | dry-run は書込みをしない | `--dry-run`（既定）で `migrate` を呼ぶ**前後でファイルのハッシュが完全一致する**ことを確認する（巡4「status しか見ない試験は書き換えてから dry_run を返す実装でも緑になる」への対応） | `status == "dry_run"`。ハッシュ不変 | 書込み後に `dry_run` ステータスを返す実装は、このハッシュ不一致 assert で落ちる |
| (g) 陽性・completed | 完了報告が実データと一致する | `dry_run=False` で正常実行後、**関数の戻り値を信用せず、テストコード自身が独立にファイルを再読込みし**、全レコードが有効な一意の `correction_id` を持つことを確認する（巡4「戻り値の status だけを見る試験は、書込みをせず completed を返すだけの実装でも緑になる」への対応） | `status == "completed"`。独立な再読込みでも全件が有効・一意な ID を持つ | 書込みをせず `completed` を返すだけの実装は、独立再読込みでの「全件が ID を持つ」assert で落ちる |
| (h) 陰性（単一ソース・`is` 比較） | import 元が一致する | `append_correction_record`・`migrate`・`resolve_correction_id` が使う validator/重複判定関数が `inspect` により同一オブジェクトであることを確認する | `is` 比較で全て一致 | 1箇所でも独自実装に置き換わっていたら落ちる |
| (h) 陰性2（単一ソース・振る舞い連動、巡4「is 比較だけでは弱い」への対応） | `validate_correction_id` を差し替えると全員が連動して変わる | `validate_correction_id` を monkeypatch して「常に True を返す」よう差し替え、`append_correction_record`（不正な ID を持つレコード）・`migrate`（既存の不正値レコード）・`resolve_correction_id`（不正な入力）を**同時に**呼ぶ | 3者とも monkeypatch 後の挙動（＝不正値を「有効」として扱う）に**揃って**変わる | いずれか1つが自前でコピーした関数やローカルにシャドーした実装を持っていれば、その1つだけ挙動が変わらず、3者の一致 assert で落ちる |
| (h) 陽性対照 | 同上 | monkeypatch を元に戻した状態で同じ3関数を呼ぶ | 3者とも通常の（不正値を拒否する）挙動 | — |
| 読取専用の確認（新規・巡4「純関数だけで試すのはトートロジー」への対応） | resolver/CLI が実ファイルに書込まない | `reflect.py --resolve-source-id <値>` を**実ファイルに対して**実行し、実行前後で①ファイルのハッシュ ②mtime ③同ディレクトリの生成物一覧（`ls` 相当）が**すべて不変**であることを確認する | 3つとも不変 | ファイルパスを受け取るだけで内部的に in-memory の list しか触らない試験（純関数テスト）は、実装が誤って書込みを行っても検出できない。実ファイル・実ディレクトリを見る本試験がその代わりになる |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `append_jsonl` の `f.flush()` 呼出しを削除した変異ビルドを作り、(a) 陰性が
  （P2 のブロック確認を経た正しい試験手順のもとで）赤くなることを実際に
  確認する
- `migrate` の identity/hash 再照合ステップを stat のみ（内容ハッシュ無し）に
  弱めた変異ビルドを作り、(e) 陰性2（追記+削除で行数維持）が赤くなることを
  確認する

**探索したが未探索のまま残すクラス**: `_HAVE_FCNTL=False` 環境での §2.2 の
重複確認（不可分性が失われる——既存の環境依存をそのまま引き継ぐ限界）／
`--resolve-source-id` の出力に含める情報の個人特定可能性（`message` 等は
含めない設計にしたため実質未探索のまま残る論点ではなくなった——本節で
削除する。巡4 [Nit] 指摘どおり「tempfile が別 filesystem」の項目も
`dir=str(filepath.parent)` により該当しないため削除する）／
移行スクリプトを冪等性に反して2回連続実行した場合の `newly_assigned` 件数の
意味（1回目と2回目で異なる値になる——ドキュメントに明記するかは§11）。

## 10. 自己検証: この設計が成立しなくなる入力・順序・中断点（3件以上）

1. **§3.1 の運用契約が破られ、identity/hash 再照合の窓の外（読込み前、または
   最終照合と `os.replace` の間）で競合が起きる**: 読込み前の競合は
   `orig_identity`/`orig_hash` にそのまま反映されるため無害（移行はその
   時点の内容を正として扱う）。最終照合と `os.replace` の間の競合は§3.1末尾に
   明記した残存窓であり、検出できない。**設計の答え**: この窓は「CPU 命令の
   実行時間のみ」であり、I/O を挟む他の窓（読込み〜最終照合の間）よりも
   桁違いに小さい。完全な排除には排他制御が要り対象外——これは round 0 の
   「共有ロックを新設しない」判断の直接的な帰結として受容する
2. **`--resolve-source-id` が呼ばれている最中に §3 の移行が実行される**:
   resolver は読取専用なので、移行の書込みと衝突しても resolver 自身が
   データを壊すことは無い。ただし resolver が返す結果は「呼び出した瞬間の
   スナップショット」であり、移行が同じ瞬間にレコードへ ID を付与していれば、
   resolver の結果（`not_found`）と実際の最新状態（ID 付与済み）が食い違い
   うる。**設計の答え**: これは読取専用操作に一般的な TOCTOU であり、
   書込みを一切行わない以上データ破壊のリスクは無い
3. **W2 が `store_write_raw` 経由でテスト/isolation パスへ書く場合**:
   §2.3 の改修は `store_write`/`store_write_raw` 両方の戻り値を変えるため、
   どちらを呼んでも保護は効く（`store_write.py:101-104`・`:141-163` の
   両方が `append_jsonl` を呼ぶことを確認済み・§1.3）
4. **移行を2回連続実行する（1回目 `--dry-run`、2回目 `--apply`）が、その間に
   誰かが手編集で `correction_id` を持つ行を1つ消す**: 1回目の `dry_run` は
   「N件に新規付与予定」と報告するが、2回目の実行時にはその1件が「ID 無し」
   に戻っているため、2回目は N+1件に付与する。**設計の答え**: 信頼境界②
   「手編集」の範囲内であり許容する。dry-run の件数と実際の適用件数が
   一致しないことがある、という限界を CLI のヘルプ文言に明記することを
   実装1巡に含める
5. **W3・W4 の部分成功契約（§2.5）が、fail-fast で止めた後の再実行で
   二重追記を生む**: W3 が12件目で `duplicate_id` を返して停止した場合、
   1〜11件目は既にファイルに存在する。再実行時、W3 のソース側（過去
   セッションの走査）が同じ11件を再度対象にすると、`correction_id` は
   毎回新規発行されるため（§2.1・呼出しごとに `uuid.uuid4()`）、**内容は
   同じだが ID が異なる重複レコードが生まれる**（信頼境界②の「再 ingest に
   よる重複」に該当）。**設計の答え**: これは §2.4 で明示した「(a) は新規
   追加分同士の重複のみ防止する」設計の直接的な限界——内容が同じでも ID が
   異なれば `has_duplicate_id` は重複と判定しない。W3 自身が「既に処理した
   session_id/timestamp をスキップする」冪等性ロジックを持つかどうかは
   W3 の既存実装（`backfill_preceding_tool_calls.py`）に依存し、本設計は
   ここに手を入れない（round 0 対象外——W3 の冪等性は本 issue のスコープに
   含まれていない既存の関心事）

## 11. 人間の判断が要る点

1. **§3.4 の tempfile 残骸検出オプション（`--check-stale-tmp`）**を実装1巡に
   含めるか、単なるドキュメント記載に留めるか
2. **§9 未探索クラスの「2回連続実行時の `newly_assigned` 件数の意味」**を
   ヘルプ文言以上にドキュメント化する必要があるか
3. **§10-5（W3の部分成功後の再実行による重複）**を許容範囲とするか、
   W3 自身の冪等性ロジックの見直しを別 issue として起票するか
4. **#587 との統合順序**: 本設計（ID 発行・移行・resolver）を先にマージし、
   #587 が `correction_id` を使って更新経路の安全性を設計する、という
   順序でよいか（本設計はこの順序を前提に書いている）
