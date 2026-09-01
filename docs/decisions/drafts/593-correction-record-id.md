# #593: correction レコードに位置非依存の不変識別子を与える設計（第2版）

> **旧版（巡1）は不採用**。外部レビューで `設計修正要`（[Must] 20 / [Should] 6）。
> 中心的な欠陥: 「ID を発行するだけ」では blocking (b) を塞げない — `resolve_by_id` で
> UUID を解決した**あと**、更新は依然として解決時点の配列 index を
> `update_reflect_status` に渡していたため、解決と書込みの間に `prune` が1件消せば
> 別レコードを更新する。旧版はこの窓を「#587 の担当」として対象外に送ったが、それが
> 誤りだった（ユーザー裁定でスコープを引き直し）。旧版本文は
> `git show 8d2e0b44:docs/decisions/drafts/593-correction-record-id.md` で参照できる。

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

**関係する issue**: `#587`（柱2の照合済み反映を測れるようにする設計）は codex 設計レビュー
2巡で `設計修正要` が続き、`review-round-cap.md` の族2巡打ち切りによりユーザー裁定で
本 issue へ切り出された（切り出し元:
`docs/decisions/drafts/587-pillar2-applied-measurement.md` §2.3、
branch `design/587-pillar2-append-events` commit `c84637b4`）。その設計の
`(source_correction_id, ordinal)` 複合キーは、`ordinal` が「ファイル出現順の相対位置」
から導出される値であるため削除・並べ替え・重複除去に耐えない、というのが出発点。

## 0. Round 0 完成条件（verbatim・第2版で更新）

### ① 守る対象

correction レコードの個体を、ファイル内の位置に依存せず一意に指せること。

### ② 信頼境界

自分たちの運用ミスのみ（手編集 / 別プロセスの追記 / 中断 / 同時に走る2つの更新 /
移行スクリプトの未実行 / 再 ingest による重複）。悪意ある偽装・第三者の改竄は脅威に
数えない。

### ③ 対象外（変更なし）

- 柱2の集計・表示（`results_board`）の変更。数え方には一切触れない
- 反映イベントの追記と read 時 fold（#587 の本体）
- `reflect_status` の意味論変更
- `#379` 新設凍結の解除。新しい保存先を作らない

### ④ スコープ（第2版で追加・1つの変更単位として設計する3点）

1. 不変 ID の発行（新規 writer・既存レコードの移行）
2. 書込み入口の一本化 — `corrections.jsonl` を書き換える全経路を、**置換
   （`os.replace`）を跨げる固定 sidecar lock** の同一 lock domain へ入れる
3. 更新前の identity 再確認 — ロック取得後に読み直し、対象 ID がちょうど1件で
   あることを確認してから書く。古いスナップショット由来の index を更新 API へ渡さない

### ⑤ blocking（(a)〜(e) 据え置き、(f)〜(h) 追加）

- (a) 同一 ID を持つレコードが2件以上存在しうる。**`ambiguous` にして操作不能にする
  だけでは塞いだことにならない**。一意性を回復する方針まで書く
- (b) レコードの削除・並べ替え・重複除去で ID が変わる、または別レコードを指す
- (c) ID を持たない既存レコードが持つものと**黙って**同じに扱われる（fail-closed
  でない）。`{"correction_id": ""}` や `null`・非文字列も「キーあり」で通してはならない
- (d) ID の発行が既存の reader を壊す
- (e) 中断で ID だけ書かれた／本体だけ書かれた状態が生じ、成功として扱われる
- (f) 「未移行」と「移行後に ID が消えた破損」が区別できず、再移行が同じレコードに
  別 ID を発行する
- (g) 移行の終了結果が `completed`/`incomplete`/`conflict`/`retry_required` を区別せず、
  未完了を成功として返す
- (h) ID の妥当性・一意性を判定する契約が単一ソースになっておらず、writer・移行・
  resolver・更新前再確認で食い違う

### ⑥ 検証方法

(a)〜(h) 各1件以上の陰性試験＋**陽性対照**を対で置く。検証単位は resolver 単体でなく
「writer→保存→reader→解決→競合差込み→更新／拒否」の実経路に置く。lock は mock でなく
実 `fcntl.flock` と複数プロセスで、writer がロック待ちに入ったことを確認してから
置換を進める決定論的試験にする。移行の中断は書込み開始後・置換前に failpoint を
注入して再現する。

## 1. 現状（自分で数え直した file:line つき）

### 1.1 実データ

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z。既存241件に `correction_id` 相当のフィールドは無い
（`(session_id, timestamp)` 重複0件、実測コマンドは巡1と同一・再現可能。取得時刻
2026-08-31T23:32:19Z）。**巡1からの追加実測は行っていない**（データは不変のため）。

### 1.2 書込み側 — 6経路を実物で数え直した結果

**巡1の§1.3「新規追記はすべて `append_jsonl` 経由」は誤り**。直接 `open(..., "a")` する
2経路を見落としていた。以下が実物で確認した全6経路:

| # | file:line | 書込み方式 | ロック | atomic か |
|---|---|---|---|---|
| 1 | `hooks/correction_detect.py:164`（`common.store_write("corrections.jsonl", record)`）→ `scripts/lib/rl_common/store_write.py:77-92`（`store_write`）→ `append_jsonl` | 追記 | **あり**（`persistence.py:159-160` `fcntl.flock(f, LOCK_EX)`、`_HAVE_FCNTL` は `persistence.py:11-15` で判定） | 追記は本質的に atomic（1行 write のみ） |
| 2 | `scripts/lib/correction_semantic/promote.py:565-568`（`store_write`/`store_write_raw`） | 追記 | 経路1と同じ `append_jsonl` 経由（`store_write_raw` も内部で `append_jsonl` を呼ぶ） | 同上 |
| 3 | `scripts/backfill_preceding_tool_calls.py:229-254`（`persist_to_corrections`） | 一括追記（`with open(corrections_file, "a", ...) as f: ... f.write(...)`、line 250） | **なし**。`fcntl` を一切呼ばない | 各 `write` 呼び出しは行単位で atomic ではあるが、ロックが無いため経路1/2との排他は保証されない |
| 4 | `scripts/migrate_reflect_queue.py:118-121`（`migrate` 内、`with open(CORRECTIONS_FILE, "a", ...) as f: for rec in converted: f.write(...)`） | 一括追記 | **なし** | 同上 |
| 5 | `skills/reflect/scripts/reflect.py:602-706`（`update_reflect_status`）。書込みは line 706 `filepath.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")` | **全文読込→全文書き戻し（in-place）** | **なし** | **atomic でない**。`write_text` は既存ファイルを truncate してから書くため、途中で kill されると壊れたファイル（旧内容と新内容が混在、または空）が残りうる |
| 6a | `scripts/lib/prune/corrections.py:51-114`（`cleanup_corrections`）。書込みは line 112 `corrections_file.write_text(...)` | 全文読込→全文書き戻し（in-place、decay 超過行を除外） | **なし** | 同上（atomic でない） |
| 6b | `scripts/lib/correction_semantic/promote.py:584-642`（`invalidate_idiom_corrections`）。書込みは line 634-642 `tempfile.mkstemp` → `os.fdopen` で書込み → `os.replace(tmp_path, corrections_path)` | 全文読込→全文書き戻し（**tempfile+`os.replace`**） | **なし** | atomic（inode 差し替え） |

**確認コマンド**: `grep -n "corrections.jsonl\|CORRECTIONS_FILE\|corrections_path\|corrections_file" <各ファイル>` を該当6ファイルに対し個別実行し、`open(...)`/`write_text(...)`/`flock` の有無を目視確認した（実行時刻 2026-08-31 本文書作成セッション内）。**経路は6ではなく実質7**（6a と 6b は別関数・別書込み方式であるため区別する。指示の「6経路」は 5・6a・6b を1グループとして数えた場合と一致する）。

### 1.3 なぜ「データファイル自身の `flock`」では足りないか（前巡の欠陥の構造）

経路1・2 は `append_jsonl` の `flock` を取る。しかし経路6b（`invalidate_idiom_corrections`）
は **`tempfile.mkstemp` + `os.replace`** で `corrections.jsonl` の inode を丸ごと差し替える。
POSIX の `flock` はファイル**記述子**（正確には open file description）に対するロックであり、
**パス名**に対するロックではない。ゆえに:

- プロセス A が `open("corrections.jsonl", "a")` で fd を取得し `flock(LOCK_EX)` を待機中
- プロセス B（`invalidate_idiom_corrections` 相当）が**別の** fd（`corrections_path` を
  `open(..., "r")` で開いた読込み用 fd — これは `flock` を取っていない、なぜなら
  経路6b は現状ロックを一切取らないため）で読み込み、`tempfile` に書いて
  `os.replace` する。この `os.replace` は**新しい inode をパス名に結びつける**
- プロセス A が経路5/6a のような**ロックなしの `write_text`** で書き込む場合、
  さらに深刻: `flock` を誰も取っていないので、A・B・6a・6b がどんな順序で走っても
  Last-Writer-Wins（後勝ちで先の変更が消える）が起こりうる

**したがって「経路1・2 の `flock` を経路3〜6b にも広げる」だけでは不十分**——
広げたとしても、広げた先の実装が `os.replace` で inode を差し替える限り、
**同じパス名に対して新しく `open()` した fd は常に新しい inode を指す**ため、
「rename 前に既にそのパスを `open()` していた別プロセスの fd」との排他は
成立しない（旧 inode のロックを待っていたプロセスが、rename 後に lock を取得しても、
書き込み先はもはやパスから参照されない孤立 inode になる）。**この問題を解く唯一の
安全な方法は、ロック対象をデータファイル自身ではなく、rename されない固定パスの
sidecar ロックファイルにすることである**（§2）。

## 2. コア設計: sidecar lock による書込み入口の一本化

### 2.1 sidecar lock ファイル

新規パス `<DATA_DIR>/corrections.jsonl.lock`（**新しいストアではない** — §5で
`#379` 抵触の有無を確認する）。中身は使わない（`flock` の対象として存在するだけ、
`open(path, "a")` で作成・開くのみ）。このファイルは**一度作成されたら二度と
rename/unlink されない**（`os.replace` の対象にしない）ことが安全性の前提。

### 2.2 単一書込み関数（新規モジュール `scripts/lib/rl_common/corrections_writer.py`・設計のみ）

```python
import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

LOCK_SUFFIX = ".lock"

@contextmanager
def _corrections_lock(corrections_path: Path):
    lock_path = corrections_path.with_name(corrections_path.name + LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)  # ブロッキング取得（既存 append_jsonl と同じ意図）
        try:
            yield
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def append_correction(corrections_path: Path, record: dict) -> None:
    """新規レコードを追記する（経路1〜4 の統一入口）。sidecar lock を取ってから
    既存 append_jsonl 相当の1行追記を行う。"""
    with _corrections_lock(corrections_path):
        is_new = not corrections_path.exists() or corrections_path.stat().st_size == 0
        with open(corrections_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if is_new:
            try:
                corrections_path.chmod(0o600)
            except OSError:
                pass


def rewrite_corrections(
    corrections_path: Path,
    transform: Callable[[list[dict]], list[dict]],
) -> "RewriteResult":
    """全文読込→transform→全文書き戻しを行う経路5・6a・6b の統一入口。

    sidecar lock を取得した**後に**ファイルを読み込む（呼出元が lock 取得前に読んだ
    スナップショットは一切信用しない — blocking (b) 対策の核心）。
    transform は「生の行リスト（parse 成功 dict のみ）」を受け取り「書き戻す
    dict リスト」を返す純関数。パース不能行・空行は transform に渡さず、
    出力にそのまま温存する（既存 update_reflect_status の慣習を踏襲）。
    書き戻しは必ず tempfile + os.replace（atomic）で行う（経路5・6a のような
    直接 write_text は使わない — blocking (e) 対策）。
    """
    with _corrections_lock(corrections_path):
        if not corrections_path.exists():
            return RewriteResult(status="completed", records_written=0)
        raw_lines = corrections_path.read_text(encoding="utf-8").splitlines()
        parsed: list[dict] = []
        passthrough: list[tuple[int, str]] = []  # 元の行位置を保持し、非dict行を温存する
        for i, line in enumerate(raw_lines):
            stripped = line.strip()
            if not stripped:
                passthrough.append((i, line))
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                passthrough.append((i, line))
                continue
            if not isinstance(rec, dict):
                passthrough.append((i, line))  # blocking (c)/(d) 系: 非 dict は決して個体として扱わない
                continue
            parsed.append(rec)

        try:
            new_parsed = transform(parsed)
        except Exception as e:
            return RewriteResult(status="retry_required", error=str(e))

        # 温存すべき非dict行の位置を保ちつつ、parsed→new_parsed の対応を保って再構成する。
        # transform はレコードの「増減」を行わない契約とする（本設計が要求する transform は
        # 既存レコードの field 更新のみ。新規追加・削除を伴う transform は本関数の対象外
        # ＝経路5・6a はどちらも「既存レコードの1フィールド更新 or 除外」であり増減しない
        # か「除外のみ」なので、この契約で表現できる）。
        ...  # 実装詳細は次巡

        new_content = _serialize(passthrough, new_parsed)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(corrections_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_path, corrections_path)
        except OSError as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return RewriteResult(status="retry_required", error=str(e))
        return RewriteResult(status="completed", records_written=len(new_parsed))
```

**6経路すべてがこの2関数のどちらかを通る**ように改修する（実装1巡のスコープ。
本設計はこの契約を確定させるところまで）:

| 経路 | 改修後 |
|---|---|
| 1 `correction_detect.py:164` | `store_write` の内部実装を `append_jsonl` から `append_correction`（sidecar lock 版）へ差し替え |
| 2 `promote.py:565-568` | 同上（`store_write`/`store_write_raw` 経由なので経路1と同じ差し替えで両方直る） |
| 3 `backfill_preceding_tool_calls.py:250` | `append_correction` を直接呼ぶよう書き換え |
| 4 `migrate_reflect_queue.py:118-121` | 同上 |
| 5 `reflect.py:602-706`（`update_reflect_status`） | `rewrite_corrections` を内部で呼ぶよう書き換え。**API 契約自体も変える**（§4） |
| 6a `prune/corrections.py:51-114`（`cleanup_corrections`） | `rewrite_corrections` を呼ぶよう書き換え（transform は「decay 超過レコードを除外」） |
| 6b `promote.py:584-642`（`invalidate_idiom_corrections`） | `rewrite_corrections` を呼ぶよう書き換え（transform は「該当レコードに `invalidated=True` を立てる」） |

**この一本化により、経路6b の `os.replace` は sidecar lock 保持中にのみ発生する**。
経路1〜4 の追記側も同じ sidecar lock を取るため、6b の rename と1〜4の追記は
互いに排他される（§1.3 で述べた「rename を跨げないロック」問題は、
**ロック対象をデータファイルから sidecar へ移す**ことで構造的に解消する——
sidecar パスは6経路とも rename しないため、常に同一 inode に対する `flock` になる）。

## 3. `correction_id` の発行と resolve（一意性の「無害化」だけでなく「回復」まで書く）

### 3.1 スキーマ

新規フィールド `correction_id: str`（`uuid.uuid4().hex`、32文字16進）と、
発行元を示す `correction_id_schema: int`（固定値 `1`）を**同じ書込みで同時に**追加する
（§6 の (f) 対策で理由を述べる）。単一発行関数:

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新規 store ではない・§7で根拠）
import re
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CURRENT_SCHEMA = 1

def new_correction_id() -> str:
    return uuid.uuid4().hex

def validate_correction_id(value) -> bool:
    """correction_id として有効な形式か判定する単一ソース（blocking h）。
    None・空文字列・非文字列・不正フォーマットはすべて False。"""
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))

def validate_unique_ids(records: list[dict]) -> dict[str, int]:
    """records 内の有効な correction_id ごとの出現回数を返す（重複検出の単一ソース・
    blocking h）。validate_correction_id を通った値のみ数える。非 dict / 無効値は無視。"""
    counts: dict[str, int] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        cid = r.get("correction_id")
        if validate_correction_id(cid):
            counts[cid] = counts.get(cid, 0) + 1
    return counts
```

**writer 4経路（§1.2 の1〜4）は、レコード構築時に両フィールドを設定する**
（`record["correction_id"] = new_correction_id(); record["correction_id_schema"] = CURRENT_SCHEMA`）。
`promote.py:346-393`（`_build_correction_record`）のような構築関数の**内部**に置き、
`store_write`/`store_write_raw` のどちらを呼ぶかの分岐（`promote.py:565-568`）より
**前**に置く（テスト経路・production 経路の両方が取りこぼさないようにするため）。

### 3.2 resolve: fail-closed（(a)(c) の防御）

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ResolveResult:
    status: str  # "found" | "not_found" | "ambiguous" | "invalid_id"
    record: Optional[dict] = None
    index: Optional[int] = None
    match_count: int = 0

def resolve_by_id(records: list[dict], correction_id) -> ResolveResult:
    if not validate_correction_id(correction_id):
        return ResolveResult(status="invalid_id")  # (c): None/空文字列/非文字列/不正形式は即拒否
    matches = [
        (i, r) for i, r in enumerate(records)
        if isinstance(r, dict) and validate_correction_id(r.get("correction_id"))
        and r["correction_id"] == correction_id
    ]
    if not matches:
        return ResolveResult(status="not_found")
    if len(matches) > 1:
        return ResolveResult(status="ambiguous", match_count=len(matches))
    i, r = matches[0]
    return ResolveResult(status="found", record=r, index=i, match_count=1)
```

**`validate_correction_id` を writer・resolver 双方が使う**ことで、`r.get("correction_id", "") == correction_id`
のような「欠落を空文字列として拾って偶然一致させる」実装ミスを構造的に防ぐ
（`validate_correction_id("")` は `False` なので、そもそも比較対象に入らない）。

### 3.3 一意性の**回復**（新規スクリプト `scripts/repair_duplicate_correction_ids.py`・設計のみ）

**(a) は「ambiguous にして操作不能にする」だけでは塞いだことにならない**という
指摘を受け、回復手順を設計する。

1. `rewrite_corrections`（§2.2）でロックを取り、`validate_unique_ids`（§3.1）で
   `correction_id` ごとの出現回数を数える
2. 出現回数が2以上のグループごとに、**ファイル出現順で最も早い1件を「primary」とし、
   primary の `correction_id` は変更しない**。primary 以外の各レコードには
   `new_correction_id()` を新たに発行し、`correction_id`/`correction_id_schema` を
   上書きする
3. **既定は `--dry-run`**（他の移行/修復スクリプトと同じ規約）。`--apply` 指定時のみ
   書き戻す。実行結果には「どのレコードが再発行されたか」（旧 ID・新 ID・
   `session_id`/`timestamp`・file 内での元位置）を全件出力する（人間が事後に
   「この ID を指していた外部参照が無効になった」と気づけるようにするため）

**誤複製へ新 ID を再発行してよい条件**: 本設計は**内容の同一性を条件にしない**
（primary 以外は無条件で再発行する）。理由: 「内容が同じなら安全、違えば危険」という
判定基準を導入すると、判定基準自体にバグが起きた場合に沈黙して間違った方を残す
リスクを生む。一意性の回復という目的に対しては「どちらを残すか」より
「両方とも一意な ID を持つ状態に戻すこと」が本質であり、primary/non-primary の
選び方（ファイル出現順で先頭）は決定論的で監査可能である。**内容が異なる2つの
論理的に別の correction が偶然同じ ID を持っていた場合**（信頼境界②の「手編集」
シナリオ）も、この手順で一意性は回復するが、**参照元が既に存在する場合**（後述）は
別途扱いが要る。

**参照元が既に存在する場合**: 現状のコードベースには「`correction_id` を外部へ
永続保存して後から参照する」経路がまだ無い（§7 の CLI オプションが実装されて
初めて生まれる）。したがって本設計の時点では「参照元」は主に人間の記憶・手元の
メモ・チャット履歴上のコピペである。これらは修復後に無効化される可能性がある
（primary でない側を参照していた場合）ことを、修復スクリプトの出力とドキュメントで
明示する以外に救済手段は無い（round 0 対象外——自動的な参照追跡・書き換えは
「新しいストア」を要する可能性が高く、`#379` 凍結に抵触しうるため見送る）。

**バックアップ復元時の手順**: 過去のバックアップから `corrections.jsonl` を丸ごと
復元すると、復元後のファイルと現在のファイルで同じレコード（同じ `correction_id`）が
重複しうる。本設計はこのケースも「同一 ID の重複」として §3.3 の手順で扱う
（復元 vs 通常運用の重複を区別しない — 区別する情報がそもそも無い）。**運用上の
注意**として、バックアップ復元は「復元→即座に `repair_duplicate_correction_ids.py --dry-run`
を実行して重複件数を確認する」を手順化することを§14で人間に提案する。

## 4. 更新前の identity 再確認（blocking b の直接対応）

### 4.1 `update_reflect_status` の API 契約変更

**現行**: `update_reflect_status(filepath, indices: list[int], status, ...)`
（`reflect.py:602-608`）。`indices` は**呼出元が事前に読んだ配列のスナップショット
位置**であり、ロック取得もしていない（§1.2 経路5）。

**新設計**: シグネチャを `update_reflect_status(filepath, correction_ids: list[str], status, ...)`
に変える。内部実装:

```python
def update_reflect_status(filepath, correction_ids, status, *, target_path=None, draft_line=None):
    if status == "applied":
        # 既存の check_line_applied 検証はロック外で先に行ってよい
        # （target_path の中身は corrections.jsonl と無関係なファイルであり、
        #  identity 再確認の対象ではない）
        ...

    def _transform(records: list[dict]) -> list[dict]:
        updated_ids: set[str] = set()
        result = []
        for r in records:
            cid = r.get("correction_id")
            if validate_correction_id(cid) and cid in correction_ids:
                resolved = resolve_by_id(records, cid)  # ロック内で再解決
                if resolved.status != "found":
                    continue  # ambiguous/not_found はそのレコードを更新しない
                r = dict(r)
                r["reflect_status"] = status
                updated_ids.add(cid)
            result.append(r)
        return result

    outcome = rewrite_corrections(filepath, _transform)
    # updated_ids と correction_ids の差分から not_found を判定して返す（詳細省略・次巡）
    ...
```

**この設計で blocking (b) が解消される理由**: `rewrite_corrections`（§2.2）は
**sidecar lock を取得した後にファイルを読み込む**。呼出元（CLI）が事前に持っている
`correction_ids` の**値そのもの**（UUID 文字列）はレコードの中身であり位置ではないため、
ロック取得後の読み込みでも意味が変わらない。ロック取得後に `prune` が同じ sidecar
lock を取ろうとしても、`update_reflect_status` がロックを解放するまで待たされる
（§2.1 の一本化）ため、「解決と書込みの間に prune が1件消す」という前巡の欠陥の
発生源だった**競合窓自体が構造的に閉じる**（旧設計は index を渡す時点でロック外の
スナップショットを信用していたが、新設計は correction_id という位置非依存の値だけを
ロックを跨いで受け渡し、位置（index）はロック内で毎回作り直す）。

### 4.2 CLI（`reflect.py`）側の変更点

`reflect.py:1263-1269`（`--apply`）・`:1329-1335`（`--skip`）の
`make_source_correction_id(sid, ts) == args.apply` による**先頭一致**（`break`）を、
まず全一致を集める形に変える。1件なら、その1件の `correction_id` を取り出し
（§3.1 の移行済みレコードであれば持っているはず。§8 の gate が「移行未完了時は
このパス自体を無効化する」ため、ここに到達する時点で `correction_id` の存在は
gate が保証する）、`update_reflect_status(filepath, [correction_id], status, ...)`
（§4.1 の新シグネチャ）を呼ぶ。2件以上一致するなら `ambiguous_source_id` として
非0終了する（先頭を黙って選ばない）。

## 5. 移行の状態機械（blocking f・g の対応）

### 5.1 「未移行」と「移行後に破損」を区別する判別子

`correction_id_schema`（§3.1）を判別子として使う:

| `correction_id` | `correction_id_schema` | 判定 |
|---|---|---|
| 無い | 無い | **未移行**（安全に ID を新規発行してよい） |
| 無い | ある（`1`） | **破損**（過去に移行済みだったが `correction_id` だけ消えた——手編集・マージ事故等）。**新規 ID を無条件発行しない**。§5.4 の `--repair-corrupted` フローへ回す |
| ある（形式妥当） | ある（`1`） | 移行済み・正常 |
| ある（形式不正 or 非文字列） | 無関係 | `validate_correction_id` で `False` になるため、実質「無い」と同じ扱い（`(c)` の要求どおり形式チェックを通す） |

**両フィールドを同一書込みで同時に設定する**（§3.1）ため、
「`correction_id_schema` はあるのに `correction_id` は正常」かつ「書込み経路の
バグでこの2フィールドが非同期に書かれた」という状態は、本設計の書込み関数
（`append_correction`/`rewrite_corrections`）を正しく実装する限り発生しない
（1回の JSON 行 write は atomic な単位）。**発生しうるのは人間の手編集のみ**
（信頼境界②で想定済み）。

### 5.2 移行スクリプト（新規 `scripts/migrate_correction_id_backfill.py`・設計のみ）

`rewrite_corrections`（§2.2）を使う。transform 関数:

```python
def _migrate_transform(records: list[dict]) -> "MigrationTransformOutcome":
    newly_migrated = 0
    corrupted = 0
    out = []
    for r in records:
        has_id = validate_correction_id(r.get("correction_id"))
        has_schema = r.get("correction_id_schema") == CURRENT_SCHEMA
        if has_id and has_schema:
            out.append(r)  # 既に移行済み。冪等にスキップ
        elif not has_id and not has_schema:
            r = dict(r)
            r["correction_id"] = new_correction_id()
            r["correction_id_schema"] = CURRENT_SCHEMA
            out.append(r)
            newly_migrated += 1
        else:
            # has_schema かつ not has_id（またはその他の不整合な組合せ）→ 破損。
            # ID を発行しない。レコードはそのまま温存し、件数だけ数える。
            out.append(r)
            corrupted += 1
    return MigrationTransformOutcome(records=out, newly_migrated=newly_migrated, corrupted=corrupted)
```

### 5.3 4値の終了ステータス（blocking g）

```python
@dataclass
class MigrationResult:
    status: str  # "completed" | "incomplete" | "conflict" | "retry_required"
    total_records: int
    already_migrated: int
    newly_migrated: int
    corrupted_detected: int
    malformed_lines: int
    error: Optional[str] = None
```

判定規則（`read_corrections_records_with_health` の `readable`/`error`/`malformed_lines`
という「成功/失敗の2値ではなく複数軸」という設計を踏襲）:

- **`completed`**: `rewrite_corrections` が `status="completed"` を返し、かつ
  `corrupted_detected == 0`
- **`incomplete`**: 書き戻し自体は成功したが `corrupted_detected > 0`。
  「ID 依存操作を全面的に使ってよい」とは言えない状態——§5.4 の repair フローが
  必要であることを呼出元に伝える
- **`conflict`**: `rewrite_corrections` 内部で（sidecar lock 契約に参加していない
  未改修コードが仮に残っていた場合の防御として）書き戻し直前に再読込した内容の
  ハッシュが、transform に渡した内容のハッシュと食い違った場合。**通常運用では
  発生しない**（sidecar lock が全経路を排他するため）が、実装1巡で「契約に
  参加し忘れた経路が残っていないか」を検出する最後の防衛線として組み込む
- **`retry_required`**: `OSError`（ディスク容量・権限）等で書き戻しに失敗した場合。
  再実行が安全（§5.5 の冪等性）なのでこの名前にする

### 5.4 `--repair-corrupted` フロー

破損（`correction_id_schema` はあるが `correction_id` が無い/不正）レコードは
自動修復しない。人間が明示的に `--repair-corrupted` を指定したときのみ、
そのレコードを「未移行」として扱い直し（`correction_id_schema` を含めて
まっさらに再発行する）、修復内容（`session_id`/`timestamp`/新 ID）を全件出力する。
**この操作は「そのレコードが過去に持っていた ID を永久に失う」ことを意味する**——
移行済みだった証拠自体が消えているため、これは避けられない（round 0 対象外の
「完全な自動復旧」は要求されていないと解釈する。§14で確認）。

### 5.5 中断耐性（blocking e、§7.2 旧版から継続）

- `rewrite_corrections` はロック内で「読込→transform（メモリ上のみ）→
  tempfile 書込→`os.replace`」の順に実行する。プロセスが transform 完了前に
  kill されれば、元ファイルはバイト単位で無傷（tempfile すら存在しない可能性がある）
- `os.replace` 自体は OS レベルで atomic。前半（tempfile 書込）で kill されれば
  元ファイルは無傷、後半（rename 自体）に「途中」という状態は存在しない
- 冪等性（§5.2 の「既に移行済みならスキップ」）により、中断後の再実行は
  「最初からやり直す」だけで安全（進捗マーカー方式は採用しない——241件という
  規模で1回のロック区間内に完結できるため。数万件規模になった場合は§14で
  再検討を提起する）

## 6. 既存 reader の母集団を数え直す（`reflect_status` 読取6箇所 + 全レコード読取5経路）

### 6.1 `reflect_status` を直接読む6箇所（巡1から変更なし・再確認済み）

`scripts/lib/audit/memory.py:474-489` / `scripts/lib/correction_semantic/correction_backlog.py:106`
（`fleet.queue_materials.read_corrections_records_with_health` 経由）/
`scripts/lib/discover/suppression.py:198` / `scripts/lib/issues_summary.py:35-42` /
`skills/genetic-prompt-optimizer/scripts/optimize_core.py:60-84` /
`scripts/lib/prune/corrections.py:17-48`（read 側。write 側は §1.2 経路6a）。
いずれも `dict.get(key)` 方式（未知キーを無視する）で読んでおり、新規フィールド
追加は影響しない（巡1 §4 の結論を維持）。

### 6.2 レコード全体を読む5経路（巡1で見落としていたもの・自分で grep して確認）

```
$ grep -rln "corrections.jsonl\|CORRECTIONS_FILE" /Users/matsukaze-takashi/wt/ea-593 \
    --include="*.py" | grep -v "/tests/\|test_"
```
実行時刻: 本文書作成セッション内。この結果から、`reflect_status` だけでなく
**レコード全体（dict 全フィールド）を扱う**箇所を洗い出した:

| # | file:line | 何をするか | `correction_id` 追加の影響 |
|---|---|---|---|
| 1 | `hooks/auto_memory_runner.py:57-90`（`read_recent_corrections`）/ `:123-154`（`_load_all_corrections`） | corrections.jsonl から直近 N 件（`MAX_CORRECTIONS=5` / `max_records=50`）の**レコード全体**を取得し、`scripts/lib/auto_memory_broker.py` へ渡す | 直接の実害は無い（`.get()`/フィルタ処理は未知キーを無視）。ただし §6.3 で述べる下流への露出が新たに増える |
| 2 | `scripts/lib/auto_memory_broker.py:322-335`（`_build_prompt`） | `json.dumps(corrections, ensure_ascii=False, indent=2)` で**レコード全体を JSON 文字列化し、そのまま LLM プロンプトへ埋め込む**（`corrections_text` 変数、line 322） | **`correction_id` フィールドがそのまま LLM プロンプトに露出する**（§6.3） |
| 3 | `hooks/save_state.py:76-104`（`_load_corrections_snapshot`） | corrections.jsonl 全件を読み、`checkpoint["corrections_snapshot"]` としてディスク保存する（コメントに「全件ディスク保存したまま無改変」— `hooks/restore_state.py:52-53` の docstring 記載） | 実害なし（保存のみ） |
| 3' | `hooks/restore_state.py:41-77`（`_summarize_checkpoint_for_output`） | `_load_corrections_snapshot` が保存した checkpoint の `corrections_snapshot` を、直近 `MAX_SNAPSHOT_ITEMS=20`（line 47）件・合計 `MAX_SNAPSHOT_CHARS=8000`（line 48）文字に truncate してから **SessionStart で Claude context へ print** する（line 51-53 のコメントで明記） | **`correction_id` を含むレコード全体が毎セッション Claude のコンテキストへ注入される**（§6.3） |
| 4 | `scripts/lib/corrections_insights.py:31-60`（`load_corrections_for_insights`） | lookback 期間内のレコード全体を読み、コーパスレベルの繰り返しパターン集計に使う（`count_repeated_patterns` の入力） | 実害なし（未知キー無視） |
| 5 | `scripts/rl/fitness/telemetry.py:435-473`（`score_failure_distribution`） | `record.get("error_category")` のみ参照。レコード全体は読むが使うのは1フィールド | 実害なし |
| 6 | `scripts/lib/audit/outcome_metrics.py:113-131`（`_read_jsonl`） | `isinstance(rec, dict)` チェックあり（line 129）の安全なパターン。呼出元でどのフィールドを使うかは別関数 | 実害なし |

（#5・#6 はレコード全体を読み込むが実質的に少数フィールドしか使わないため、
上記11箇所のうち**実際に「レコード全体」を下流へ運ぶのは #1〜#3' の4箇所**。
これが本設計にとって重要な経路——§6.3 参照）

### 6.3 pass-through reader でのフィールド投影: 判断と根拠

**判断: allowlist 投影を導入せず、露出を明示的に受容する。**

**根拠**:

1. `correction_id` は `uuid.uuid4().hex` の32文字16進文字列であり、**注入攻撃の
   運び屋になりえない**（固定文字集合・固定長・意味を持つ自然文でない）。既存の
   露出経路（#2 `_build_prompt`、#3' `restore_state.py`）は既に `message`
   （ユーザー発話全文）や `extracted_learning`（LLM 抽出テキスト）という、
   `correction_id` よりはるかにリスクの高い自由記述フィールドを無条件で運んでいる。
   `correction_id` を追加してもリスクの質は変わらない（新しい攻撃面を開かない）
2. サイズ影響は既存の防御機構内に収まる。`restore_state.py:47-48` の
   `MAX_SNAPSHOT_ITEMS=20`/`MAX_SNAPSHOT_CHARS=8000` は件数・合計文字数の両方で
   既に truncate している。1レコードあたり `correction_id`（32文字）+
   `correction_id_schema`（数文字）を追加しても、20件で最大 `20 * ~40 = 800`
   文字程度の増加であり、8000文字上限に対し無視できる規模（既存の `message` 等の
   自由記述フィールドの方がはるかに大きい）。`auto_memory_runner.py:48`
   `MAX_CORRECTIONS=5` も同様に件数上限で吸収する
3. allowlist 投影を採用する場合、#1〜#3' の4箇所すべてに「投影ロジック」を
   新規実装する必要があり、これは§4/§6.1で確認した「新規フィールド追加は
   reader を壊さない」という利点を自ら手放し、**新たな reader 改修（本 issue の
   スコープ外の書込み4経路とは別の、読取4経路の追加改修）を発生させる**。
   round 0 ④「1つの変更単位」に含めるべき対象（writer 6/7経路・resolve・
   identity 再確認）だけでも十分に大きく、読取4経路の allowlist 化まで含めると
   スコープが再肥大化する

**受容の裏付けとして実装1巡で行う検証**（§10 の一部として明記）: #2 の
`_build_prompt` が生成するプロンプト文字列の長さを、`correction_id` フィールド
追加前後で比較し、既存の LLM 呼出しコスト上限（`llm-batch-guard.md` が要求する
事前確認）に影響する増分でないことを実測する。#3' の `MAX_SNAPSHOT_CHARS` 上限に
対する実際の truncate 発生率が悪化しないことを、実データ241件 + 新フィールド
シミュレーションで確認する。

## 7. `#379` 新設凍結への非抵触（**正しかったので維持**・巡1から再掲）

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`。72行目に `"corrections.jsonl"`）・
`:23-37`（凍結対象の4集合: `store_registry`/`_OBSERVABILITY_BUILDERS`/
`ADVISORY_PROPOSAL_ADAPTERS`/`WEAK_SIGNAL_CHANNELS`）・`:261-275`
（`assert_no_new_keys`、ストア名・section key・adapter key・channel の文字列集合のみ検査）
を実物照合し、**JSON フィールド粒度は凍結の対象外**であることを確認済み
（外部レビューで正しいと確認された）。

**本第2版で新設する `corrections.jsonl.lock`（§2.1）についても同じ根拠で確認**する:
`FROZEN_STORES`（`shrink_freeze.py:62-77`）は corrections.jsonl 等の**データストア**を
列挙したものであり、`.lock` サイドカーはデータを保持しない排他制御用の空ファイルで
`store_registry`/`store_write`（`scripts/lib/rl_common/store_write.py:77-92`）を
一切経由しない（`append_correction`/`rewrite_corrections` は `store_write` の**外側**、
`store_write` が最終的に呼ぶ `append_jsonl` 相当の**より低いレイヤ**に位置する）。
ゆえに `assert_no_new_keys` の検査対象にもならない。**ただし `.lock` ファイルが
`store_registry` や `FROZEN_STORES` の走査（例: ディレクトリ全体を列挙して
「未知のファイル」を検出するような将来の凍結検査）に引っかからないかは、
本設計では「現状のコードが store 名の**列挙**でなく `store_registry` への
**登録有無**で判定している」ことまでしか確認していない。ディレクトリ走査型の
検査が将来追加された場合の扱いは§14で人間に確認する**。

## 8. 起動時 gate — 移行完了までID依存操作を無効化する

新しいストアを作らずに「移行が完了しているか」を判定する必要がある
（`#379` 凍結・§7）。**read 時導出**方式を採る（`weak_signals` の TTL が read 時に
age を導出する既存パターンと同型）:

```python
def correction_id_migration_status(corrections_path: Path) -> str:
    """corrections.jsonl を読み、ID 依存操作を許可してよいかを read 時に判定する。
    永続化しない（#379 新設凍結の遵守）。"""
    if not corrections_path.exists():
        return "completed"  # レコードが無ければ移行すべき対象も無い
    for line in corrections_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        has_id = validate_correction_id(rec.get("correction_id"))
        has_schema = rec.get("correction_id_schema") == CURRENT_SCHEMA
        if not has_id and not has_schema:
            return "not_migrated"
        if has_schema and not has_id:
            return "corrupted"
    return "completed"
```

**gate の適用箇所**: §4.2 の CLI（`--apply`/`--skip` の `correction_id` ベース解決部分）
は、この関数が `"completed"` を返さない限り ID ベースの解決を行わず、
`{"status": "migration_required", "migration_status": "<not_migrated|corrupted>"}`
を返して非0終了する。**旧来の `source_correction_id` ベースの操作は gate の対象外**
（後方互換——移行前でも `--apply <source_correction_id>` は動き続けるが、
§4.2 で述べた「2件以上一致したら ambiguous」という安全策だけが効く。真の
identity 再確認は移行後にしか使えない）。

**この関数は全件を毎回スキャンする**（O(N)・241件では無視できるコスト）。
数万件規模になった場合はキャッシュ等の再設計が要る（§14）。

## 9. CLI での不変 ID 直接指定（`--correction-id`）

`--apply-id <correction_id>` / `--skip-id <correction_id>` を新設する（既存の
`--apply <source_correction_id>` / `--skip <source_correction_id>` は残す・§3 巡1の
判断を維持）。`--apply-id`/`--skip-id` は §8 の gate が `"completed"` でなければ
使用不能（`migration_required` を返す）。`validate_correction_id` に通らない値が
渡された場合は即座に `invalid_id` を返す（CLI 引数レベルでも§3.1の単一ソースを使う）。

**書式判別による自動振り分けは採用しない**（例: 32文字16進なら `correction_id`、
`#` を含めば `source_correction_id`、という sniffing）。理由: `source_correction_id`
の形式（`f"{session_id}#{timestamp}"`）は `session_id` の生成規則に依存しており、
将来 `session_id` が32文字16進のみで構成される可能性を排除できない（UUID 形式の
session_id は実際にありうる）。曖昧な sniffing は round 0 blocking (c)（黙った
誤同定）と同じ種類の危険を生むため、**引数名を分けて明示させる**設計にする。

## 10. 検証計画（前巡は11試験すべてに「緑のまま通る変異」が構成可能だった。作り直す）

**設計原則**: 検証単位は resolver 単体でなく「writer→保存→reader→解決→
競合差込み→更新／拒否」の実経路に置く。lock は実 `fcntl.flock` + 複数プロセス
（`multiprocessing.Process` を使い、子プロセスがロック待ちに入ったことを
`multiprocessing.Event`/pipe で確認してから親プロセス側の操作を進める、という
決定論的な同期を取る——スレッドでなくプロセスを使う理由: `flock` はプロセス単位の
排他が本来の用途であり、同一プロセス内の複数スレッドでは fd 共有の影響で
排他が成立しない場合があるため、実運用に近い検証にはプロセス分離が必要）。
テストは `scripts/lib/tests/test_corrections_writer.py`（新設）・
`scripts/lib/tests/test_correction_id.py`（新設）・
`skills/reflect/scripts/tests/test_reflect_update_status_identity.py`（新設）に置く。

| # | 壊す不変条件 | 変異（実経路） | 期待結果 | この試験を「緑のまま通す」実装変異（自己検証） |
|---|---|---|---|---|
| (a) 陰性 | 重複 ID があっても操作不能・かつ回復可能 | fixture 2件に同一 `correction_id` を書込み（`append_correction` を2回呼び、2回目の直前で強制的に同じ ID を注入する形で作る——通常経路では起きないため直接ファイル操作で作る）。`resolve_by_id` で `ambiguous` を確認した後、`repair_duplicate_correction_ids.py --apply` を実行し、再度 `validate_unique_ids` で重複0件になることを確認 | `ambiguous`→修復後は重複0、primary の ID は不変（修復前後で同一）、non-primary は新 ID | `matches[0]` を無条件で返す実装は `ambiguous` の assert で落ちる。修復スクリプトが「両方に新 ID を発行」する実装は「primary 不変」の assert で落ちる |
| (a) 陽性対照 | 同上 | 重複の無い2件で同じ手順 | `resolve_by_id` は即 `found`。修復スクリプトは対象0件で no-op | — |
| (b) 陰性 | 解決と書込みの間に他プロセスが削除しても、更新は正しいレコードだけに当たるか、識別不能として失敗する | **実プロセス2つ**を使う: 子プロセスP1が `update_reflect_status(filepath, [B の correction_id], "applied", ...)` を呼ぶ直前に**sidecar lock を先に親プロセスが取得**し、P1 がロック待ちに入ったことを `multiprocessing.Event` で確認してから、親プロセスが**別のレコードA を `prune.cleanup_corrections` 相当の transform で物理削除**して `rewrite_corrections` 経由で書き戻し、ロックを解放。その後 P1 のロック取得・処理が進む | P1 の結果は「B の中身が正しく `applied` になっている」（A の削除は B の識別に一切影響しない）。もし新設計が sidecar lock を経由せず旧来の index ベースのままなら、A 削除後は B の index が変わっているため、この試験は誤ったレコードを更新するか `not_found` を返すはずで、この試験はその違いを検出する | ロックを取らず index ベースのままの実装（旧設計）はこの試験で B ではなく別レコードを更新するため、`record["message"]` の一致 assert で落ちる |
| (b) 陽性対照 | 同上 | 親プロセスの削除処理を行わない（A を消さない） | P1 は通常どおり B を更新できる | — |
| (c) 陰性1 | 欠落フィールドが偶然一致しない | `correction_id` キー自体が無い fixture レコードに対し `resolve_by_id(records, "")` と `resolve_by_id(records, None)` を呼ぶ | 両方 `invalid_id`。`record is None` | `rec.get("correction_id", "") == correction_id` 型の実装は、空文字列を渡された場合に欠落レコードへマッチしてしまうため、この試験の `status == "invalid_id"` assert で落ちる |
| (c) 陰性2（**第2版で追加**） | `{"correction_id": ""}` や `{"correction_id": null}`、`{"correction_id": 12345}`（非文字列）を持つレコードが「キーあり」として通ってはならない | fixture にこの3種のレコードを混在させ、有効な `correction_id` を持つ別レコードと合わせて `validate_unique_ids` を呼ぶ | 3種のレコードはいずれも戻り値の集計に含まれない（`validate_correction_id` が全て `False` を返すため） | `"correction_id" in rec` のような存在チェックのみの実装は、この3種を「有効」として数えてしまい、件数の assert で落ちる |
| (c) 陽性対照 | 同上 | 妥当な `correction_id` を持つレコード1件 | `resolve_by_id`/`validate_unique_ids` 双方で正しく検出される | — |
| (d) 陰性 | 新フィールド追加が既存 reader を壊さない | §6.1 の6関数 + §6.2 の `_build_prompt`（#2）・`_summarize_checkpoint_for_output`（#3'）に、`correction_id`/`correction_id_schema` 付き fixture と無し fixture を渡し比較する | `reflect_status` 系6関数は返り値完全一致。`_build_prompt`/`_summarize_checkpoint_for_output` は**出力される文字列に `correction_id` の値が含まれる**ことを確認する（§6.3 の「受容」判断が実装で実際に反映されていることの確認であり、除外されていないことをこの陰性試験で確認する——「除外されていない」ことを陰性側で確認するのは一見逆だが、allowlist 投影を採用しない設計判断が実装で骨抜きにされていないかの検査として位置づける） | `correction_id` を意図せず落とすフィルタが混入した実装は、この試験の「含まれる」assert で落ちる |
| (d) 陽性対照 | 同上 | `reflect_status` の値自体を変えた fixture | 6関数の返り値が変わる（比較ロジックが「違いを検出できる」ことの対照） | — |
| (e) 陰性1 | 中断で部分状態が生じない | `rewrite_corrections` の transform 内で例外を送出させ、書き戻し前に処理を止める。その後ファイルのハッシュを処理前と比較 | ハッシュ一致（無傷）。かつ `retry_required` が返る | 「書かない関数が書かないことしか見ない」トートロジーを避けるため、**この試験は transform を「実際に何かを変える transform」にして、書き戻し**直前**に例外を注入する**（transform 自体は正常に完了し、tempfile 書込みの途中で `OSError` を模擬注入する形にする——`os.fdopen` をモックして書込み半ばで例外を出す）。空の transform で「何も変わらない」ことを確認するだけの試験は、書き戻しロジック自体をバイパスした実装でも通ってしまうため、必ず実際の書込みパスを通す形にする |
| (e) 陰性2 | ロック保持中の追記が失われない | 親プロセスが sidecar lock を保持したまま `rewrite_corrections` の transform 内で意図的に一時停止（`multiprocessing.Event` で子プロセスに合図）、その間に子プロセスが `append_correction` を呼ぼうとしてロック待ちになることを確認してから、親が処理を完了してロックを解放し、子の追記が完了するのを待つ | 親の書き戻し結果と子の追記結果の**両方**がファイルに残る（追記レコードの一意な `message` を grep 相当で確認） | ロックを取らない実装、または経路ごとに別ロックを取る実装（§1.3 の欠陥そのもの）は、この試験で追記が失われるため「両方残る」assert で落ちる |
| (e) 陽性対照 | 同上 | 競合を発生させない単純な移行実行 | 全レコードが正しく `correction_id` を持ち、内容は変化なし | — |
| (f) 陰性 | 未移行と破損が区別される | fixture A（両フィールド無し）・fixture B（`correction_id_schema=1` のみ、`correction_id` 無し）を用意し、`correction_id_migration_status` を呼ぶ | A のみのファイルは `not_migrated`。B を含むファイルは `corrupted`（B が1件でも混ざれば全体を `corrupted` とする——設計は「1件でも破損があれば安全側に倒す」を採用） | `correction_id` の有無だけで判定する実装（`correction_id_schema` を見ない）は、B を「未移行」と誤判定し `not_migrated` を返すため、この試験の `"corrupted"` assert で落ちる |
| (f) 陽性対照 | 同上 | 全レコードが両フィールドを正しく持つファイル | `completed` | — |
| (g) 陰性 | 未完了が成功として返らない | `rewrite_corrections` に渡す transform 内で、破損レコード（§f のB相当）を混入させた fixture で移行を実行 | `MigrationResult.status == "incomplete"`（`corrupted_detected > 0`）であり、単純な bool 成功フラグではなく列挙値であることを型で確認する | `status` を `bool` 1個に潰す実装は、`corrupted_detected > 0` でも `True`（成功）を返しうるため、この試験の `status == "incomplete"` という文字列一致 assert で落ちる |
| (g) 陽性対照 | 同上 | 破損レコードの無い正常な241件相当 fixture | `completed`、`corrupted_detected == 0` | — |
| (h) 陰性 | 妥当性・一意性判定が単一ソースでない実装との差分を検出する | `resolve_by_id`・移行スクリプトの重複検出・修復スクリプトの重複検出の3箇所が、**同じ `validate_correction_id`/`validate_unique_ids` の import**であることをテストコード内で `inspect` により確認する（3箇所が独自に正規表現を再実装していないかを機械的に検査） | import 元のモジュール・関数オブジェクトが同一であることを `is` 比較で確認 | 3箇所のうち1つでも独自実装（別の正規表現・別のロジック）に置き換わっていたら、`is` 比較の assert で落ちる |
| (h) 陽性対照 | 同上 | 3箇所とも正しく import している現状の設計どおりの実装 | 一致 | — |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `rewrite_corrections` の sidecar lock 取得コードを削除した変異ビルドを作り、
  (b) 陰性・(e) 陰性2 の両方が赤くなることを確認する（1つの変異で複数試験が
  同時に検出できることも報告に含める）
- 移行スクリプトの `has_schema and not has_id` 判定を `not has_id`（`has_schema`を
  見ない）に単純化した変異ビルドを作り、(f) 陰性が赤くなることを確認する

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示）:
sidecar lock ファイル自体が何らかの理由で削除された場合（`open(path, "a")` は
存在しなければ新規作成するため、削除後の再作成は新しい inode になり、
削除前からロック待ちしていたプロセスとの排他が破れる——この経路は本設計が
新たに持ち込むリスクであり、実運用で `.lock` ファイルを誰かが `rm` する
シナリオは信頼境界②の「手編集」に含まれるかどうか§14で確認が要る）／
`fcntl` 非対応環境（`_HAVE_FCNTL=False`、`persistence.py:11-15` と同型の
フォールバック）での sidecar lock 無効化時の挙動／`correction_id_schema` の
将来のスキーマバージョンアップ（`CURRENT_SCHEMA` が2以上になった場合の移行）。

## 11. 自己検証: この設計が成立しなくなる入力・順序・中断点（3件以上）

1. **sidecar lock ファイルの手動削除**（§10 の未探索クラスとして触れた
   シナリオを自己検証として明示する）: 運用者が `corrections.jsonl.lock` を
   `rm` すると、その時点でロックを待っていたプロセスと、削除後に新規
   `open()` したプロセスとで、異なる inode に対する `flock` になり排他が
   破れる。**設計の答え**: これは §1.3 で述べた「rename されないことが安全性の
   前提」を運用者が破るケースであり、`corrections.jsonl` 自身への `rm`
   （信頼境界②の「手編集」に類する運用ミス）と同種のリスクとして受容する。
   完全な防止は「lock ファイルの削除も検知して再同期する」仕組みを要し、
   本 issue のスコープ（識別子・書込み一本化・identity 再確認）を超える
2. **`corrections.jsonl` 自体が存在しない状態から複数の writer が同時に
   初回書込みする**: `append_correction`（§2.2）は `corrections_path.exists()`
   の判定と実際の `open(..., "a")` の間に TOCTOU の隙間がある
   （既存 `append_jsonl` の `is_new = f.tell() == 0`（`persistence.py:162`）は
   `flock` 取得**後**に判定しており TOCTOU を回避しているが、新設計の
   `_corrections_lock` は `corrections.jsonl.lock` に対するロックであり、
   `corrections.jsonl` 本体の「新規作成か否か」判定はこの sidecar lock の
   **外**で行われる可能性がある）。**設計の答え**: `is_new` 判定は
   `_corrections_lock` コンテキストの**内側**（`open(corrections_path, "a")`
   の直前ではなく、sidecar lock 取得後）に置くことを§2.2のコード例で明示している
   （`with _corrections_lock(...): is_new = ... ; with open(...)`）。この配置に
   より、複数 writer が同時に初回書込みしても、chmod の重複適用（副作用は
   軽微だが2回目以降は無意味な `chmod` 呼び出しになる）以外の実害は無い
3. **移行スクリプトが `--repair-corrupted` なしで実行され、破損レコードを
   延々と `corrupted` のまま放置する**: §8 の gate は `corrupted` が1件でも
   あれば全体を `not "completed"` とするため、破損レコードが1件でも残る限り
   **ID ベースの CLI 操作（§9 `--apply-id`/`--skip-id`）が全PJ・全レコードで
   使えなくなる**（1件の破損が全体をブロックする設計）。**設計の答え**:
   これは意図した安全側の挙動（fail-closed の帰結）だが、運用上のリスクとして
   §14で明示し、`corrupted_detected` の件数と対象レコードを人間が読める形で
   毎回の gate チェック結果に含めることを実装1巡の要件に加える
   （沈黙した無限ブロックを避けるため）
4. **`validate_correction_id` の正規表現が将来 `uuid.uuid4().hex` 以外の
   フォーマット（例: ハイフン付き標準 UUID 文字列）を発行するよう変更された
   場合**: 過去に発行された ID（ハイフン無し32文字）と新しい ID
   （ハイフン付き36文字）が混在し、`_ID_PATTERN` が片方しか通さないと、
   古い ID を持つレコードが突然「無効」扱いになる。**設計の答え**:
   `CURRENT_SCHEMA` フィールド（§3.1）はこの種のフォーマット変更を
   将来吸収するために用意してある——スキーマバージョンごとに異なる
   `validate_correction_id` の許容パターンを分岐させる拡張点として機能する
   ことを設計意図として明記する（現時点では `CURRENT_SCHEMA=1` のみ実装し、
   分岐ロジック自体は次のスキーマ変更が実際に必要になったときに追加する
   ——YAGNI と将来の破壊的変更のバランスを、フィールドの存在だけで
   確保しておく）

## 12. やらないこと（完成条件③の対象外の再掲・理由つき）

- **柱2の集計・表示（`results_board`）の変更**: 一切触れない
- **反映イベントの追記と read 時 fold**（#587 本体）: #587 が再開されたとき、
  イベント行の識別子を `correction_id` に差し替えるだけで済むよう、本設計は
  独立して成立するよう作った（§13-5）
- **`reflect_status` の意味論変更**: 一切触れない
- **`#379` 新設凍結の解除**: 新しいデータストアを作らない（§7・sidecar lock も
  データを持たない排他制御専用ファイルとして区別する）
- **有効レコードの述語の完全統合**（巡1§6 から継続・対象外）: `rewrite_corrections`
  の入口が非dict行を温存する処理を持つため、5箇所の重複読取実装の統合とは
  独立に安全性を確保できている
- **`--correction-id` 参照元の自動追跡・書き換え**（§3.3）: 新しいストアを
  要する可能性が高く見送る
- **移行スクリプトの数万件規模対応**（§5.5・§8）: 241件を前提にした設計。
  スケール時は§14で再検討を提起する

## 13. 人間の判断が要る点

1. **blocking (a) の「一意性の回復」が要求する強度**: §3.3 の
   `repair_duplicate_correction_ids.py`（primary 以外へ無条件で新 ID 再発行）で
   十分か、それとも内容比較による判定（同一内容のみ再発行）まで求めるか
2. **§3.3 の参照元喪失リスク**: `--correction-id` を使い始めた後にバックアップ
   復元・重複修復が起きると、ユーザーが記憶していた ID が無効になりうる。
   この UX 上のリスクを許容範囲とするか
3. **§5.4 の `--repair-corrupted`**: 破損レコードは過去の ID を永久に失う
   （復元不能）という制約を許容するか。より慎重な設計（例: 破損レコードは
   `--repair-corrupted` 後も「repaired_from_corruption: true」のような
   マーカーを残す）を求めるか
4. **§8 の gate 設計（1件の破損が全体をブロックする）**: §11-3 で述べた
   通り安全側だが厳しい。PJ 単位・時間窓単位でスコープを絞る代替設計
   （ただし round 0 対象外「柱2のスコープ判定ロジック」に触れずに実現
   できるかは未検討）を求めるか
5. **§6.3 のフィールド投影判断**: allowlist 投影を採らず露出を受容する判断で
   よいか。将来 `correction_id` 以外の新フィールドを追加する際にも同じ方針
   （形式が制約された値なら受容）を踏襲してよいか
6. **#587 との統合順序**: 本設計を先にマージし、#587 再開時に
   `source_correction_id`/ordinal 依存部分を `correction_id` ベースに
   差し替える、という順序でよいか
7. **§11-1 の sidecar lock 削除リスク**への追加防御（検知・自己修復）を
   本 issue に含めるか、別 issue に切り出すか
