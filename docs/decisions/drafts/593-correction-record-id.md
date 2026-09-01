# #593: correction レコードに不変 ID を発行する設計（第4版）

> **巡1（`8d2e0b44`）・巡2（`fe8349f8`）・巡3（`aa24e734`）は不採用**。巡3は
> compare-and-swap（読み直し→ID再解決→atomic置換→書込み後検証）で更新 API を
> 自己完結させようとしたが、外部レビューが次の並びを構成した:
> `P1: A/B/C を読む → P2: A を削除して置換 → P1: 古い A/B/C を置換 → P1: B=applied
> を確認して成功`。P1 は対象 B を正しく更新しているが **P2 の A 削除を復活させる**。
> 事後検証は自分の対象しか見ないためこれを検出できない。共有ロック（巡2）も
> 自己確認（巡3）も同じ壁——「レコードを書き換える」という記録方式そのもの——に
> 当たる。**この壁を越える設計（追記イベント方式・read 時 fold）は #587 の担当**であり、
> #587 がかつて却下されたのは「どのイベントがどのレコードの話か確定しない」ためだった。
> **本 issue が不変 ID を発行すれば、その却下理由は消える**。ゆえに本第4版は
> **ID の発行・移行・読取専用の解決だけに専念し、更新経路には一切触れない**。

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

## 0. Round 0 完成条件（verbatim・第4版で確定）

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
- `reflect_status` の意味論変更
- `#379` 新設凍結の解除
- **blocking (b)（削除・並べ替えで別レコードを指す）** — #587 が担当
- 他経路の lost update（防止・検出とも）
- **重複修復（duplicate repair）機能そのもの** — 既存重複は報告して止めるだけ

### ④ スコープ（これだけ）

1. **ID の発行** — 新規レコードの追記時に不変 ID を付与する
2. **既存241件への付与** — 1回きりの移行。**移行中は他の書込みを止める運用契約**を
   前提とし、移行時の並行書込みは設計の対象外にする
3. **ID から対象レコードを解決する resolver** — **読取専用**。更新はしない

### ⑤ blocking

- (a) 同一 ID が2件以上存在しうる。**追記は「重複確認→追記」を1つのロック保持区間で
  不可分に行う**ことで塞ぐ。**移行は `os.replace` の**前**に一意性を検査する**
- (c) ID なし・空文字列・`null`・非文字列が黙って通る
- (d) レコード全体を下流へ運ぶ既存 reader を壊す
- (e) 移行の中断で ID だけ／本体だけ書かれた状態が成功扱いになる
- (g) 移行の終了結果が完了・未完了・衝突・要再試行を区別しない。**dry-run を
  `completed` と呼ばない**。malformed・非 dict 行は「correction record ではない」
  という契約を明記した上で `malformed_lines` に返す
- (h) 妥当性・一意性の契約が単一ソースでない。**追記・移行・resolver の3者が
  必ず同じ関数を通る**

### ⑥ 検証方法

検証単位は「追記→保存→読み直し→ID 解決」と「移行→保存→読み直し→検証」の実経路に
置く。(a) は同じ ID を2プロセスが同時に追記しようとする順序を決定論的に再現し、
ロックを外す変異で赤くなることを確認する。(e) は書込み開始後・置換前に failpoint を
注入する。「書かない関数が書かないことしか見ない」トートロジーを置かない。陽性対照は
値の型・形式・一意性・既存 ID の不変まで assert する。各試験について「緑のまま通る
実装変異」を自分で構成し、論理で検算する。

## 1. 現状（自分で数え直した file:line つき・本 issue のスコープに関係する範囲のみ）

### 1.1 実データ

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z（巡1〜3と同一データにつき再測不要）。既存241件に
識別子相当のフィールドは無い。

### 1.2 新規レコードを作る writer（本設計が改修する4経路）

| # | file:line | 何を作るか |
|---|---|---|
| W1 | `hooks/correction_detect.py:132-165`（`store_write("corrections.jsonl", record)`、line 164） | hook 検出による新規レコード |
| W2 | `scripts/lib/correction_semantic/promote.py:346-393`（`_build_correction_record`）→ `:565-568`（`store_write`/`store_write_raw`） | weak signal 昇格による新規レコード |
| W3 | `scripts/backfill_preceding_tool_calls.py:229-254`（`persist_to_corrections`） | 過去セッションからの一括バックフィル |
| W4 | `scripts/migrate_reflect_queue.py:94-125`（`migrate`、追記は line 118-121） | `learnings-queue.json` からの1回限りマイグレーション |

**この4経路のみが対象**。既存レコードを書き換えるだけの経路（`update_reflect_status`・
`prune/corrections.py`・`promote.py` の idiom invalidation・その他の一括正規化
スクリプト）には**一切触れない**（対象外・#587 か将来の別 issue の担務）。この
判断により、**本設計の正しさは「corrections.jsonl を書き換える全経路の列挙」に
依存しない**（巡2・巡3が繰り返し踏んだ壁を、対象を絞ることで構造的に回避する）。

### 1.3 現行の追記関数（改修対象）

```python
# scripts/lib/rl_common/persistence.py:154-172（append_jsonl・現行）
def append_jsonl(filepath: Path, record: dict) -> None:
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)  # ブロッキング取得（意図的）
            try:
                is_new = f.tell() == 0
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                if _HAVE_FCNTL:
                    _fcntl.flock(f, _fcntl.LOCK_UN)
        ...
```

`flock` 取得（line 160）から即座に `write`（line 163）まで、**重複確認を
一切行わずに書く**。W1・W2 はこの関数（`store_write`/`store_write_raw` 経由）を
使う。**W3・W4 は `append_jsonl` を経由せず、独自に `open(..., "a")` で
直接追記している**（`backfill_preceding_tool_calls.py:250`、
`migrate_reflect_queue.py:118-121`。いずれもロック無し・確認済み）。

## 2. ID の発行

### 2.1 スキーマと単一ソース（blocking c・h）

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新しいストアではない・§6）
import re
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

def new_correction_id() -> str:
    return uuid.uuid4().hex

def validate_correction_id(value) -> bool:
    """correction_id として有効な形式か判定する単一ソース。
    None・空文字列・非文字列・不正フォーマットはすべて False。"""
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))

def has_duplicate_id(records: list[dict], correction_id: str) -> bool:
    """records の中に correction_id と一致する有効な ID を持つレコードが
    既にあるかを判定する単一ソース（blocking a・h）。"""
    return any(
        isinstance(r, dict) and validate_correction_id(r.get("correction_id"))
        and r["correction_id"] == correction_id
        for r in records
    )
```

W1〜W4 のレコード構築コードは `new_correction_id()` を呼んで
`record["correction_id"]` に代入するだけでよい（実際の妥当性検証は §2.2 の
保存境界で行う——builder に検証を要求しない。巡3の欠陥「builder がフィールドを
代入するだけで検証を呼んでいなかった」を踏まえ、検証は必ず書込み関数の内部に置く）。

### 2.2 追記関数の改修: 「重複確認→追記」を同一ロック保持区間で不可分に行う

```python
# scripts/lib/rl_common/persistence.py（改修後の append_jsonl・設計のみ）
def append_jsonl(filepath: Path, record: dict) -> "AppendResult":
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)  # 既存どおりブロッキング取得
            try:
                # --- 保存境界（blocking c・h）---
                cid = record.get("correction_id")
                if cid is not None and not validate_correction_id(cid):
                    return AppendResult(status="invalid_id")
                if cid is not None:
                    # --- 保存境界（blocking a）: ロックを手放さずに読む ---
                    # f はこの時点で追記モードで開いているため、読み直すには
                    # 別に read モードで同じパスを開く（同一プロセス内・同一
                    # ロック保持中に行うので、他プロセスの追記はこの区間内に
                    # 割り込めない——flock は同一 inode に対する排他であり、
                    # 自分がロックを保持している間は他の flock 待機者は進めない）。
                    existing = _read_records_locked(filepath)  # isinstance(dict) フィルタ済み
                    if has_duplicate_id(existing, cid):
                        return AppendResult(status="duplicate_id")
                is_new = f.tell() == 0
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                if _HAVE_FCNTL:
                    _fcntl.flock(f, _fcntl.LOCK_UN)
        if is_new:
            try:
                filepath.chmod(0o600)
            except OSError:
                pass
        return AppendResult(status="appended")
    except OSError as e:
        return AppendResult(status="retry_required", error=str(e))
```

**なぜこれが blocking (a) を塞ぐか**: `flock(LOCK_EX)` を取得している間、
**同じロックを取ろうとする他の呼出し（W1・W2 経由の別プロセスの `append_jsonl`
呼出し）は待たされる**。重複確認（`has_duplicate_id`）と実際の追記
（`f.write`）が**同一のロック保持区間の中**にあるため、「2プロセスが両方
『重複なし』を確認してから両方追記する」という巡3で指摘された穴
（check→append の2段が別々のロック区間だった場合の穴）が構造的に発生しない。

**この保護が及ぶ範囲**: `append_jsonl` を経由する経路（W1・W2）**のみ**。
W3・W4（§1.3）は `append_jsonl` を経由しないため、この保護の外にある。
**この設計は W3・W4 も `append_jsonl` を経由するよう改修することを実装1巡の
必須要件に含める**（W3・W4 は現状「1回限りの手動実行スクリプト」であり、
書込み1行を `f.write(json.dumps(c, ...) + "\n")` から
`append_jsonl(corrections_file, c)` の呼出しに置き換えるだけで済む、
既存の低リスクな改修）。

### 2.3 追記関数が返す結果と CLI/呼出側の契約

`AppendResult.status`: `"appended"`（成功）/ `"duplicate_id"`（blocking a により
拒否）/ `"invalid_id"`（blocking c により拒否）/ `"retry_required"`（`OSError`）。
W1〜W4 の呼出側（hook・promote・バックフィルスクリプト）は `"appended"` 以外を
失敗として扱う——**特に `"duplicate_id"`/`"invalid_id"` は `new_correction_id()`
の実装かレコード構築コードのバグを意味する**ため、無視して処理を継続しては
ならない（hook（W1）は stderr へ警告を出し correction 自体の記録をスキップする、
という既存の `append_jsonl` の「失敗時はサイレント」慣習に倣いつつ、この2つの
理由だけは区別可能なログを残す——実装1巡で確定）。

## 3. 既存241件への移行

### 3.1 運用契約: 移行中は他の書込みを止める

**移行時の並行書込みへの対応を設計しない**——その代わりに、移行スクリプトの
実行前提として「移行の実行中は corrections.jsonl への他の書込み（hook・
`prune`・その他のバックフィル/正規化スクリプト）を行わない」という**運用上の
契約**を明記する。この契約により、移行のロジック自体は**単一プロセス・
競合なし**を前提に単純に書ける（§1.2 の対象4経路を除く、§1.3 で列挙した
既存レコードを書き換える他の経路 — `prune`・`promote` の invalidate・各種
正規化スクリプト — も含めて、移行実行中は動かさない）。

**この契約は技術的には強制しない**（機械的な排他制御は導入しない——導入すると
「全経路を1つのロックへ」という巡2の壁に戻るため）。かわりに §3.3 の
`incomplete` ステータスが、契約が破られたことを（完全にではないが）事後的に
検知する保険として機能する。

### 3.2 移行スクリプト（新規 `scripts/migrate_correction_id_backfill.py`・設計のみ）

```python
def migrate(filepath: Path, *, dry_run: bool = True) -> "MigrationResult":
    if not filepath.exists():
        return MigrationResult(status="completed", total=0, newly_assigned=0)

    try:
        raw_lines = filepath.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return MigrationResult(status="retry_required", error=str(e))
    except UnicodeDecodeError as e:
        # ファイル全体が decode できない = 個別行の malformed とは異なり読込み自体が
        # 失敗している。retry_required とし、人間の調査を要求する。
        return MigrationResult(status="retry_required", error=str(e))

    new_lines: list[str] = []
    newly_assigned = 0
    malformed = 0
    final_ids: list[str] = []  # 書込み後に存在するはずの全ての有効 ID（重複検査用）

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)  # correction record ではない。触らずカウントのみ
            malformed += 1
            continue
        if not isinstance(rec, dict):
            new_lines.append(line)  # 同上（scalar/list は record ではない）
            malformed += 1
            continue

        cid = rec.get("correction_id")
        if validate_correction_id(cid):
            final_ids.append(cid)
            new_lines.append(json.dumps(rec, ensure_ascii=False))
            continue

        new_id = new_correction_id()
        rec = dict(rec)
        rec["correction_id"] = new_id
        newly_assigned += 1
        final_ids.append(new_id)
        new_lines.append(json.dumps(rec, ensure_ascii=False))

    # --- 保存境界（blocking a）: os.replace の前に一意性を検査する ---
    seen: set[str] = set()
    duplicates: set[str] = set()
    for cid in final_ids:
        if cid in seen:
            duplicates.add(cid)
        seen.add(cid)
    if duplicates:
        return MigrationResult(status="conflict", total=len(raw_lines),
                                newly_assigned=0, duplicates=sorted(duplicates))

    if dry_run:
        return MigrationResult(status="dry_run", total=len(raw_lines),
                                newly_assigned=newly_assigned, malformed_lines=malformed)

    new_content = "\n".join(new_lines) + "\n" if new_lines else ""
    orig_mode = filepath.stat().st_mode  # [Should]: permission 継承
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.chmod(tmp_path, orig_mode)  # os.replace は mode を継承しないため明示コピー
        os.replace(tmp_path, filepath)
    except OSError as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return MigrationResult(status="retry_required", error=str(e))

    # --- 軽量な書込み後チェック（CAS ではない・バルクの粗い健全性確認）---
    # 運用契約（§3.1）が守られていれば以下は常に一致するはず。破られていた場合の
    # 事後検知として、件数だけを比較する（個々のレコードを ID で再解決しない
    # ——それは巡3で否決された per-record 再検証であり、本設計は行わない）。
    try:
        verify_lines = filepath.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason=f"post-write read failed: {e}")
    if len(verify_lines) != len(new_lines):
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason=f"post-write line count mismatch: "
                                       f"expected {len(new_lines)}, got {len(verify_lines)}")

    return MigrationResult(status="completed", total=len(raw_lines),
                            newly_assigned=newly_assigned, malformed_lines=malformed)
```

### 3.3 4値の終了ステータスと CLI exit code（blocking g）

| status | 意味 | exit code |
|---|---|---|
| `dry_run` | `--dry-run`（既定）。書込みなし。件数のみ報告。**`completed` と呼ばない**（[Nit] 反映） | 0 |
| `completed` | 書込み成功・事前重複検査を通過・書込み後の行数一致を確認 | 0 |
| `incomplete` | 書込みは成功したが、書込み後の粗い健全性確認（行数比較）が一致しなかった。**運用契約（§3.1）違反の疑い**として人間に調査を促す | 1（無条件の再実行は勧めない——まず §3.1 の契約が守られていたか確認する） |
| `conflict` | `os.replace` の**前**に、書込み予定の ID 集合内で重複を検出した。書込みは行っていない（元ファイル無傷） | 2（`--list-duplicates` 等で重複元を調査してから再実行——本設計は自動修復機能を持たない・§8） |
| `retry_required` | 読込み・tempfile 書込み・`os.replace` のいずれかで `OSError`/`UnicodeDecodeError` | 3（元ファイルは無傷なので、そのまま再実行してよい） |

### 3.4 読込・検証エラーの対応表

| 事象 | 結果 |
|---|---|
| ファイル不在 | `completed`（対象0件・移行すべきものが無い） |
| ファイルオープン/読込み時 `OSError`（権限等） | `retry_required` |
| ファイル全体の `UnicodeDecodeError` | `retry_required` |
| 行単位の `json.JSONDecodeError` | `malformed_lines` としてカウント、行は温存、ID 付与対象にしない（移行の成功可否には影響しない） |
| 行が dict でない（scalar/list） | 同上 |
| 事前重複検査で重複を検出 | `conflict`（書込みなし） |
| tempfile 書込み/`os.chmod`/`os.replace` で `OSError` | `retry_required`（元ファイル無傷） |
| 書込み後の読込みで `OSError` | `incomplete` |
| 書込み後の行数不一致 | `incomplete` |

### 3.5 中断耐性（blocking e）

計算（読込み→変換、メモリ上のみ）と書込み（`tempfile` + `os.replace`）を分離。
計算中に kill されれば元ファイルは無傷。`tempfile` 書込み中に kill されれば
元ファイルは無傷（`os.replace` に未到達）。`os.replace` 自体は OS レベルで
atomic——「rename の途中」という状態は存在しない。**crash consistency の範囲は
プロセス kill・未処理例外までであり、OS クラッシュ・電源断は対象外**
（`fsync` を呼んでいない——`promote.py:634-642` 等の既存 atomic write パターンも
同様に `fsync` を呼んでおらず、本設計は既存コードベースの durability 前提を
踏襲するだけで後退させない）。

**冪等性**: 「既に有効な `correction_id` を持つレコードはスキップ」（§3.2 の
`validate_correction_id(cid)` 分岐）により、中断後の再実行は常に安全
（最初からやり直すだけでよい。241件という規模で1回の実行に完結できるため
進捗マーカー方式は採らない）。

### 3.6 permission/mode の継承（[Should]）

`os.replace` は tempfile（`tempfile.mkstemp` が既定で作る mode、通常 0600）を
そのまま差し替えるため、元ファイルの mode を継承しない。§3.2 のコードは
`os.chmod(tmp_path, orig_mode)` を `os.replace` の**前**に呼び、元ファイルの
mode ビットを明示的にコピーする。**owner（uid/gid）は明示的に継承しない**
（単一ユーザーのローカルファイルであり、chown には通常 root 権限が要るため
本設計のスコープでは扱わない——既存の `promote.py:634-642` 等の atomic write
パターンも owner を継承していない。本設計はこの点で既存パターンより
mode 継承の分だけ厳格にする）。

## 4. resolver（読取専用）

```python
# scripts/lib/rl_common/correction_id.py（続き）
from dataclasses import dataclass
from typing import Optional

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

**単一ソースの確認（blocking h）**: `append_jsonl`（§2.2）の重複確認・
`migrate`（§3.2）の事前重複検査・`resolve_correction_id`（本節）はいずれも
`validate_correction_id`（§2.1）を import して使う——独自の正規表現・独自の
dict 判定ロジックを持たない。3者が同一関数オブジェクトを参照していることは
`inspect` による同一性確認をテストに含める（§9）。

**このモジュールに更新機能を追加しない**——`resolve_correction_id` はレコードを
返すだけで、それをファイルへ書き戻す関数は本設計に存在しない。書き戻しが
必要な操作（`--apply`/`--skip` 等）は#587 が扱う。

## 5. `#379` 新設凍結への非抵触（維持・変更なし）

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`、72行目に
`"corrections.jsonl"`）・`:23-37`（凍結対象4集合）・`:261-275`
（`assert_no_new_keys`、ストア名等の文字列集合のみを検査、JSON フィールド
粒度は対象外）を巡1で実物照合済み（巡2・巡3のレビューでも覆っていない）。
`correction_id` は既存ストア `corrections.jsonl` への新規フィールド追加のみ。
**第4版は共有ロック・sidecar ファイルを一切新設しない**ため、巡2・巡3で
検討していた「新設ファイルが凍結検査に引っかからないか」という論点自体が
存在しない。

## 6. 既存 reader の再列挙（blocking d — レコード全体を下流へ運ぶ経路に限定）

`.get(key)` で個別キーだけ読む reader（`reflect_status` を読む6箇所、
`error_category` のみ読む `telemetry.py:score_failure_distribution` 等）は
新規フィールド追加の影響を受けない（`dict.get` は未知キーを無視する）ため
網羅を求めない（巡2・巡3から維持）。**巡3で指摘された、選択的フィールドしか
読まない2経路もこのカテゴリに追加する**（自分で確認済み）:

- `skills/genetic-prompt-optimizer/scripts/optimize_core.py:60-84`
  （`collect_corrections`）→ `:161-`（`build_patch_prompt`）: `corr.get("message")`/
  `.get("correction_type")`/`.get("extracted_learning")` のみ参照
  （`optimize_core.py:187-193` 確認済み）。`json.dumps` でレコード全体を
  ダンプしていない——新規フィールドは影響しない
- `skills/evolve-loop-orchestrator/scripts/variant_generation.py:55-90`
  （`generate_variants`、`collect_corrections` を呼び `build_patch_prompt` へ渡す、
  line 73・102） — 上記と同じ関数を呼ぶだけで、独自のレコード全体シリアライズは
  行っていない（確認済み）。**影響なし**

**レコード全体を下流へ運ぶ経路**（`json.dumps(record_全体, ...)` する箇所。
巡3から再掲・変更なし）:

- `hooks/auto_memory_runner.py:57-90`・`:123-154` → `scripts/lib/auto_memory_broker.py:322-335`
  （`_build_prompt`、`json.dumps(corrections, ...)` で LLM プロンプトへ直接埋め込み）
- `hooks/save_state.py:76-104` → `hooks/restore_state.py:41-77`
  （`_summarize_checkpoint_for_output`、`MAX_SNAPSHOT_ITEMS=20`/
  `MAX_SNAPSHOT_CHARS=8000` で truncate した上で SessionStart の Claude
  context へ print）

**判断（巡1〜3から維持）**: allowlist 投影を導入せず、露出を明示的に受容する。
`correction_id` は32文字16進の固定形式文字列で注入攻撃の運び屋になりえず、
既存の自由記述フィールド（`message`/`extracted_learning`）よりリスクが低い。
サイズ影響も既存の truncate 機構（件数・合計文字数の両方）に吸収される。

## 7. ID を人間が取得する経路（表示は変えない）

既存の pending 一覧表示（`--view`）・`--dry-run` 出力のフォーマットは変更しない
（人間が読む文面に32文字の ID を混ぜない）。代わりに新設 CLI サブコマンド
`reflect.py --resolve-id <source_correction_id>` を追加する: 既存の
`make_source_correction_id(sid, ts) == args.resolve_id` で一致するレコードを
**全件**集め（先頭一致で打ち切らない）、それぞれの `correction_id` を機械可読
JSON で返す:

```json
{"status": "found", "matches": [{"correction_id": "a1b2...", "session_id": "...", "timestamp": "..."}]}
```

2件以上一致する場合は `status: "ambiguous"` とし `matches` に複数列挙する
（`§4` の resolver とは別に、`source_correction_id` という**実質一意**な
検索キーからの解決なので、resolver 自体とは別の薄いラッパーとして実装する
——resolver（§4）は `correction_id` を入力に取るが、この CLI コマンドは
`source_correction_id` を入力に取り、内部で候補を絞り込んでから
`correction_id` を出力する、という逆方向の変換）。**この CLI コマンドも
読取専用**であり、何も書き込まない。

## 8. やらないこと（完成条件③の対象外の再掲）

- 柱2の集計・表示の変更 / 反映イベントの追記と read 時 fold（#587）/
  `reflect_status` の意味論変更 / `#379` 新設凍結の解除
- **`update_reflect_status`・`--apply`/`--skip`/`--skip-all` の変更**
  （#587 が「指した個体を更新する時点でも同じ個体である」ことを保証する
  設計を持ってから着手する）
- **blocking (b)（削除・並べ替えで別レコードを指す）の解消** — #587 の担当
- 他経路（§1.3 で列挙した既存レコードを書き換える経路群）の lost update
  （防止・検出とも） — §3.1 の運用契約に委ねる
- **重複修復機能** — `conflict`/検出のみ行い、既存重複を自動的に解消する
  スクリプトは作らない（巡3の `--keep` 方式も含めて設計しない。人間が
  必要と判断すれば別 issue で扱う）

## 9. 検証計画

検証単位を「追記→保存→読み直し→ID 解決」（W1・W2 経由の `append_jsonl`）と
「移行→保存→読み直し→検証」の実経路に置く。テストは
`scripts/lib/tests/test_correction_id.py`・
`scripts/lib/rl_common/tests/test_append_jsonl_correction_id.py`・
`scripts/tests/test_migrate_correction_id_backfill.py`（いずれも新設）に置く。

| # | 壊す不変条件 | 変異（実経路） | 期待結果 | この試験を「緑のまま通す」実装変異（自己検証） |
|---|---|---|---|---|
| (a) 陰性 | 同じ ID の同時追記を、ロック内の不可分な確認で拒否する | **実プロセス2つ**。同じ `correction_id` を持つ2つのレコードを、2つの子プロセスからほぼ同時に `append_jsonl` へ渡す。決定論的再現のため、片方の子プロセス（P1）が `flock` 取得**直後**・重複確認**前**で一時停止するテスト用フックを使い、その間にもう片方（P2）に**先に完走**させ、その後 P1 を再開させる | P1 の重複確認は P2 の追記後の内容を見るため `duplicate_id` を返す。ファイルには**1行だけ**その ID を持つレコードが存在する（`grep -c` 相当で確認） | `flock` 取得を重複確認より後（追記の直前）に移す変異、または重複確認と追記の間で明示的に `flock` を一度解放する変異を作り、この試験が2行（重複）を許してしまうことを確認する——赤くなることを自分で検算してから報告する |
| (a) 陽性対照 | 同上 | 異なる `correction_id` を持つ2レコードで同じ手順 | 両方とも `appended`。ファイルに2行とも存在する | — |
| (a) 陰性2（移行） | 事前検査で重複を検出したら書込みをしない | fixture に、移行対象（ID 無し）2件と、既に重複する有効 ID を持つ2件を混在させる | `status == "conflict"`。元ファイルのハッシュが実行前後で一致（無傷） | 事前検査を `os.replace` の**後**に移す変異を作り、この試験が「ファイルは書き換わったのに `conflict` を返す」矛盾状態になることを確認する（ハッシュ不一致で赤くなる） |
| (a) 陽性対照2 | 同上 | 重複の無い fixture | `completed`。全件が一意な ID を持つ | — |
| (c) 陰性1 | 欠落フィールドが偶然一致しない | `resolve_correction_id`/`has_duplicate_id` を `""`/`None` で呼ぶ | `invalid_id`（resolver）／`False`（`has_duplicate_id`、空文字列は「重複判定対象外」）。**この2つの関数の挙動差**（resolver は拒否・重複判定は「対象外なので重複ではない」）を意図的な設計として明示し、テストでも別々に assert する | `rec.get("correction_id", "") == cid` 型の実装は空文字列引数で欠落レコードにマッチするため、いずれかの assert で落ちる |
| (c) 陰性2 | `{"correction_id": ""}`/`null`/非文字列が「キーあり」として通らない | この3種を混在させた fixture を `resolve_correction_id`/`has_duplicate_id` へ渡す | いずれも「有効な ID」として扱われない | 存在チェックのみ（`"correction_id" in rec`）の実装はこの3種を有効と誤認し、assert で落ちる |
| (c) 陽性対照 | 同上 | 妥当な `correction_id` を持つレコード1件 | 正しく検出される | — |
| (d) 陰性 | 新フィールド追加が pass-through reader を壊さない | `_build_prompt`・`_summarize_checkpoint_for_output`・`build_patch_prompt` に、`correction_id` 付き fixture と無し fixture を渡し出力を比較する | 前2者は `correction_id` の値が出力文字列に含まれる（受容判断の反映確認）。`build_patch_prompt` は出力が**完全一致**（選択的フィールドのみ使うため無関係） | `build_patch_prompt` が意図せず全フィールドを dump するよう変更された場合、この試験の「完全一致」assert で落ちる（この経路が本当に選択的読取であることの回帰確認になっている） |
| (d) 陽性対照 | 同上 | `reflect_status`/`message` の値自体を変えた fixture | 全関数で出力が変わる | — |
| (e) 陰性1（書込み開始後・置換前の中断） | 中断で部分状態が生じない | `os.fdopen`（tempfile への書込み）の途中で例外を注入する failpoint を仕込み、`migrate` を呼ぶ（対象レコードを実際に1件以上変更する fixture を使う——空 fixture でのトートロジーを避ける） | 元ファイルのハッシュが処理前と一致。`status == "retry_required"` | tempfile 書込み前に元ファイルを直接 truncate してから書き直す実装（非 tempfile パターン）に変異させ、この試験のハッシュ不一致 assert で落ちることを確認する |
| (e) 陰性2（書込み後チェックが効いていることの確認） | 書込み後の行数不一致検知が機能する | `os.replace` 完了**直後**、書込み後読込みの**前**に、テスト用フックで停止させ、その間に第三者プロセスが1行追記する。その後読込みを進めさせる | `status == "incomplete"`（`completed` を返さない） | 書込み後チェックを丸ごと省略した実装、または行数比較をせず常に `completed` を返す実装は、この試験の `status != "completed"` assert で落ちる |
| (e) 陽性対照 | 同上 | 競合を発生させない単純な移行実行 | `completed`。行数・ID 件数が期待どおり | — |
| (g) 陰性 | dry-run が completed と呼ばれない | `--dry-run`（既定）で `migrate` を呼ぶ | `status == "dry_run"`（`"completed"` ではない） | dry-run 分岐を `completed` にまとめた実装は、この文字列一致 assert で落ちる |
| (g) 陽性対照 | 同上 | `dry_run=False` で正常実行 | `status == "completed"` | — |
| (h) 陰性 | 妥当性判定が単一ソースでない実装との差分を検出する | `append_jsonl` の重複確認・`migrate` の事前検査・`resolve_correction_id` の3箇所が `inspect` により**同じ** `validate_correction_id` 関数オブジェクトを import していることを確認する | `is` 比較で全て一致 | 1箇所でも独自実装に置き換わっていたら `is` 比較の assert で落ちる |
| (h) 陽性対照 | 同上 | 3箇所とも正しく import している設計どおりの実装 | 一致 | — |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `append_jsonl` の重複確認ロジック（§2.2）を削除した変異ビルドを作り、
  (a) 陰性が赤くなることを実際に確認する
- `migrate` の事前重複検査（`os.replace` の前の一意性チェック）を
  `os.replace` の**後**に移動した変異ビルドを作り、(a) 陰性2が赤くなることを
  確認する

**探索したが未探索のまま残すクラス**: `_HAVE_FCNTL=False`（`fcntl` 非対応環境）
での §2.2 の重複確認——ロックが無効化された場合、重複確認自体は行われるが
「不可分」の保証は失われる（既存 `append_jsonl` が既に持つ環境依存の限界を
本設計はそのまま引き継ぐ）／`tempfile.mkstemp` が `corrections.jsonl` と
異なるファイルシステム上にある場合の `os.replace` の atomic 性／
`--resolve-id` の出力に含まれる `session_id`/`timestamp` 経由で、間接的に
個人特定可能な情報が機械可読出力へ露出するリスク（§7 で `message` の先頭
文字を含めるかどうかは実装1巡で再検討が要る——本文書では含める前提で
書いたが未検証）。

## 10. 自己検証: この設計が成立しなくなる入力・順序・中断点（3件以上）

1. **§3.1 の運用契約が守られない**: hook（W1）が移行スクリプト実行中に
   corrections.jsonl へ追記すると、移行の書込み後チェック（§3.2 末尾）が
   「行数不一致」を検出し `incomplete` を返す——**ただし検出できるのは
   件数の不一致だけ**であり、hook の追記が移行の書込みに巻き込まれて
   消えた（lost）のか、逆に移行が hook の追記を正しく引き継いだ
   （たまたま件数が一致した）のかは、この粗いチェックでは区別できない
   場合がある（例: hook が1件追記した直後に別の何かが1件消えていれば
   件数は偶然一致し `completed` になってしまう）。**設計の答え**: これは
   意図した限界として明記している（§3.2 のコメント「per-record 再検証は
   行わない」）。真の保証が必要なら§3.1の運用契約を技術的に強制する
   仕組み（巡2が試みたものと同種）が要るが、それは対象外
2. **`--resolve-id` が呼ばれている最中に §3の移行が実行される**: resolver
   は読取専用なので、移行の書込みと衝突しても resolver 自身がデータを
   壊すことは無い。ただし resolver が返す結果は「呼び出した瞬間の
   スナップショット」であり、移行が同じ瞬間にレコードへ ID を付与して
   いれば、resolver の結果（`not_found`）と実際の最新状態（ID 付与済み）が
   食い違いうる。**設計の答え**: これは読取専用操作に一般的な
   TOCTOU（read 直後に古くなる）であり、書込みを一切行わない以上データ
   破壊のリスクは無い。利用者は必要なら再度 `--resolve-id` を呼び直せばよい
3. **W2（`promote.py`）が `store_write_raw` 経由でテスト/isolation パスへ
   書く場合**: §2.2 の改修は `append_jsonl` の内部に置くため、`store_write`
   と `store_write_raw` のどちらを呼んでも最終的に同じ `append_jsonl` を
   経由する限り保護は効く。**確認済み**（`scripts/lib/rl_common/store_write.py:101-104`
   の `store_write` と `:141-163` の `store_write_raw` はいずれも
   `from rl_common import append_jsonl; append_jsonl(...)` を呼んでおり、
   実装は共通の1関数に集約されている——取得時刻: 本文書作成セッション内、
   `grep -n "def store_write_raw\|append_jsonl" scripts/lib/rl_common/store_write.py`
   で再現可能）。したがって本項目は懸念というより設計が正しく機能する
   根拠として記録する
4. **移行スクリプトを2回連続で実行する（1回目が `dry_run`、2回目が
   `--apply`）が、その間に誰かが手編集で `correction_id` を持つ行を1つ
   消す**: 1回目の `dry_run` は「N件に新規付与予定」と報告するが、
   2回目の実行時にはその1件が「ID 無し」に戻っているため、2回目は
   N+1件に付与する。**設計の答え**: これは信頼境界②「手編集」の範囲内で
   あり許容する——(f) を落とした巡3以降の判断（「未移行」と「移行後に
   手編集で消えた」を区別しない）と整合する。dry-run の件数と実際の
   適用件数が一致しないことがある、という限界を CLI のヘルプ文言に
   明記することを実装1巡に含める

## 11. 人間の判断が要る点

1. **§3.1 の運用契約が破られた場合の実害許容度**: `incomplete` の粗い検出で
   十分か、より強い保証（技術的な排他）を求めるか——求める場合は
   別 issue として起票する前提でよいか
2. **§7 の `--resolve-id` 出力に `message` 先頭文字を含めるか**: 個人特定
   可能な情報の露出リスク（§9 未探索クラス）をどう評価するか
3. **§8 の重複修復機能を持たない判断**: `conflict` を検出したら人間が
   手動でファイルを編集する以外に手段が無い状態を許容するか
4. **W3・W4 を `append_jsonl` 経由に改修すること**（§2.2 末尾）を
   本 issue の実装1巡に含めるか、別 PR に分けるか
5. **#587 との統合順序**: 本設計（ID 発行・移行・resolver）を先にマージし、
   #587 が `correction_id` を使って更新経路の安全性を設計する、という
   順序でよいか（本設計はこの順序を前提に書いている）
