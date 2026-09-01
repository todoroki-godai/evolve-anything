# #593: correction レコードに不変 ID を発行する設計（第6版・確定）

> **巡1〜5（`8d2e0b44`／`fe8349f8`／`aa24e734`／`1fe89962`／`88a7e97a`）から継続**。
> 巡5も`設計修正要`（[Must] 13）だったが、**ユーザーが3点を裁定し設計を確定させた
> （以降レビューは出さない）**。裁定は次の3点:
> ① 保存の入口を `append_correction_record` 1本に統一する（W1〜W4 は
>   `store_write` を直接呼ばない）
> ② 「移行中の契約違反は事後に必ず検出できる」という主張を撤回する。
>   未検出の残存リスクとして明記し、代わりにバックアップ手順と identity
>   記録で運用上受け止める
> ③ `fcntl` が使えない環境は非対応とする（起動時に拒否する契約。当環境は
>   macOS のため実害なし）
> 本第6版はこの3裁定に加え、巡5レビュー
> （`/Users/matsukaze-takashi/.codex-watch/rev593h-20260901-113628-39811.report`）が
> 指摘した擬似コードの誤り（`MigrationResult` フィールド不一致・`tmp_path` 未代入
> 参照・ID 発行位置の欠落）と検証計画の不備をすべて反映する。

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

## 0. Round 0 完成条件（verbatim・第6版で確定）

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
- **他経路（`update_reflect_status`・`prune`・`promote` の idiom invalidation・
  各種正規化スクリプト）の lost update（防止・検出とも）** — 本設計は
  「新規追記」と「移行」の書込みだけを対象にし、それ以外の既存全文書換え
  writer 群との競合は対象外のまま
- **重複修復（duplicate repair）機能そのもの** — 既存重複は報告して止めるだけ

### ④ スコープ（これだけ）

1. ID の発行 — 新規レコードの追記時に不変 ID を付与する。**保存の入口は
   `append_correction_record` 1本に統一する**（裁定①）
2. 既存241件への付与 — 1回きりの移行。**移行中は他の書込みを止める運用契約**を
   前提とする。**契約違反は未検出の残存リスクとして明記し、事後に必ず検出できる
   とは主張しない**（裁定②）。バックアップと identity 記録で運用上受け止める
3. ID から対象レコードを解決する resolver — **読取専用**。更新はしない

### ⑤ blocking

- (a) 同一 ID が2件以上存在しうる。**新規に発行する ID が重複を作ることを、
  保存前に拒否する**（既存の重複は対象外・§2.6 で境界を明示）。**`fcntl` が
  利用できない環境では、この保証自体を提供しない（起動時に拒否する。
  best-effort での続行はしない）**（裁定③）
- (c) ID なし・空文字列・`null`・非文字列が黙って通る。**保存境界の検証は
  無条件で行う**
- (d) レコード全体を下流へ運ぶ既存 reader を壊す
- (e) 移行の中断で ID だけ／本体だけ書かれた状態が成功扱いになる
- (g) 移行の終了結果が完了・未完了・衝突・要再試行を区別しない。dry-run を
  `completed` と呼ばない。malformed・非 dict 行は「correction record では
  ない」という契約を明記した上で `malformed_lines` に返す
- (h) 妥当性・一意性の契約が単一ソースでない。**追記・移行・resolver の3者が
  必ず同じ関数を通る**（`append_jsonl` 内の独自比較ロジックを持たない）

### ⑥ 検証方法

検証単位は「W1〜W4 の実 writer→`append_correction_record`→保存→読み直し→
ID 解決」と「移行→保存→読み直し→検証」の実経路に置く。**正しい実装のもとで
到達不能な前提（例: 別プロセスが先に完走してしまう順序）を試験の前提にしない**
（巡5の反省）。「緑のまま通る変異」を自分で構成し、**その変異が実際に
「正しい実装なら通り、壊れた実装なら通らない」ことを論理で検算してから
報告する**（巡5で3件、想定した変異が実際には赤くならないことが判明した反省）。

## 1. 現状（自分で数え直した file:line つき）

### 1.1 実データ

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z（巡1〜5と同一データにつき再測不要）。

### 1.2 新規レコードを作る writer（本設計が改修する4経路）と現状のレコード構築位置

| # | 追記呼出し（改修前） | レコード構築位置（ID をここで発行する・裁定①・Q6[Must]対応） |
|---|---|---|
| W1 | `hooks/correction_detect.py:164`（`common.store_write("corrections.jsonl", record)`） | `hooks/correction_detect.py:131-164` の `record = {...}` 構築時（`"reflect_status": "pending"` と同じ辞書リテラル内） |
| W2 | `scripts/lib/correction_semantic/promote.py:545-568`（`store_write`/`store_write_raw`） | `scripts/lib/correction_semantic/promote.py:346-392`（`_build_correction_record`）の `out = {...}` 構築時 |
| W3 | `scripts/backfill_preceding_tool_calls.py:230-255`（現状は直接 `open(..., "a")`） | `scripts/backfill_preceding_tool_calls.py:211-220`（`results.append({...})`）の辞書構築時 |
| W4 | `scripts/migrate_reflect_queue.py:118-121`（現状は直接 `open(..., "a")`） | `scripts/migrate_reflect_queue.py:41-57`（`convert_learning`）の `return {...}` 構築時 |

**現行の4箇所はいずれも ID を生成しない**（巡5 Q6[Must]で指摘済み・自分で
file:line を確認した）。本設計は各構築位置に
`"correction_id": new_correction_id(),` を1行追加することを実装1巡の
完了条件に含める。

### 1.3 保存入口を1本に統一する呼出グラフ（裁定①）

**W1〜W4 は `store_write`/`store_write_raw` を直接呼ばない。すべて
`append_correction_record`（§2.2）を呼ぶ**。既存の「未登録ストア reject」
という write barrier の保護（`store_write.py:77-92` の `_guard_problem`）を
失わないため、`append_correction_record` 自体を `store_write`/
`store_write_raw` の**内部実装**として位置づける（呼出方向を反転させる:
「`store_write` が `corrections.jsonl` のときだけ correction 専用ラッパーへ
分岐する」のではなく、**「correction 専用ラッパーが write barrier のガード
判定を内包し、W1〜W4 はそのラッパーだけを直接呼ぶ」**）:

```
W1・W2・W3・W4
    ↓（唯一の入口）
append_correction_record(filepath, record)   ← §2.2
    ↓
append_jsonl(filepath, record, duplicate_check=...)   ← §2.3（汎用・約30ストア共有のまま）
```

**write barrier のガード（未登録ストア reject）は `append_correction_record`
の内部で行う**（`store_write._guard_problem("corrections.jsonl")` 相当の
ロジックをそのまま呼ぶ——実装1巡で `store_write.py` から該当ロジックを
import して再利用し、二重実装にしない）。この設計により**呼出グラフが1本に
なる**（巡5 Q6[Must]「呼出グラフを一つに決める」への直接対応）——`store_write`
自体は他の約30ストアに対して従来どおり `append_jsonl` を直接呼ぶが、
`corrections.jsonl` に関しては `store_write`/`store_write_raw` は**呼ばれない**
（W1〜W4 の呼出し先が変わるため）。

## 2. ID の発行

### 2.1 単一ソースの妥当性・重複判定（blocking c・h）

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新しいストアではない・§5）
import re
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

def new_correction_id() -> str:
    """新規発行は uuid4 のみ。**衝突時の再発行ロジックは持たない**（[Should]対応・
    理由は§2.4）。"""
    return uuid.uuid4().hex

def validate_correction_id(value) -> bool:
    """correction_id として有効な形式か判定する単一ソース。None・キー欠落
    （呼出側は record.get() で None になる）・空文字列・非文字列（int/list/
    dict/bool を含む）・不正フォーマット（31/33文字・大文字混在等）はすべて
    False。"""
    return isinstance(value, str) and bool(_ID_PATTERN.fullmatch(value))

def has_duplicate_id(records: list[dict], correction_id: str) -> bool:
    """records の中に correction_id と一致する有効な ID を持つレコードが
    既にあるかを判定する（1件の ID に対する判定）。"""
    return any(
        isinstance(r, dict) and validate_correction_id(r.get("correction_id"))
        and r["correction_id"] == correction_id
        for r in records
    )

def find_duplicate_ids(records: list[dict]) -> dict[str, int]:
    """records 全体を走査し、有効な correction_id のうち2回以上出現するものだけを
    返す（移行の事前検査が使う。has_duplicate_id と同じ predicate を使う）。"""
    counts: dict[str, int] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        cid = r.get("correction_id")
        if validate_correction_id(cid):
            counts[cid] = counts.get(cid, 0) + 1
    return {cid: n for cid, n in counts.items() if n > 1}
```

### 2.2 保存境界: `append_correction_record`（唯一の入口・裁定①）

```python
# scripts/lib/rl_common/correction_id.py（続き）
from dataclasses import dataclass
from typing import Optional

@dataclass
class AppendResult:
    status: str  # "appended" | "invalid_id" | "duplicate_id" |
                 # "unregistered_store" | "unsupported_platform" | "retry_required"
    reason: Optional[str] = None

def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """corrections.jsonl 専用の保存境界。W1〜W4 が到達する唯一の入口
    （store_write/store_write_raw は本関数の内部実装として位置づけ、
    W1〜W4 から直接は呼ばれない・§1.3）。

    検証は**無条件**——record["correction_id"] が無い・None・空文字列・
    非文字列いずれでも invalid_id を返し、書込まない。
    """
    if not _HAVE_FCNTL:
        # 裁定③: fcntl 非対応環境では blocking (a) の保証を提供できないため、
        # best-effort で続行せず起動時に拒否する。
        return AppendResult(status="unsupported_platform",
                             reason="fcntl unavailable: unique append is not supported")

    from store_write import _guard_problem  # write barrier の未登録ストア reject を再利用
    problem = _guard_problem("corrections.jsonl")
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    cid = record.get("correction_id")
    if not validate_correction_id(cid):
        return AppendResult(status="invalid_id")

    result = append_jsonl(
        filepath, record,
        duplicate_check=lambda existing: has_duplicate_id(existing, cid),
    )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)
```

**`_guard_problem` の再利用**は既存 write barrier（`store_write.py:77-92`）の
保護を落とさないための措置——実装1巡で `store_write.py` からこの private
関数を import 可能にする（`_` プレフィックスの扱いは実装時に
`store_write.py` 側で export するか検討する。設計としては「二重実装しない」
という制約だけを固定する）。

### 2.3 汎用 `append_jsonl` の改修: flush をロック解放前に、重複判定は
callback として受け取る（blocking a・h の同時対応）

```python
# scripts/lib/rl_common/persistence.py（改修後の append_jsonl・設計のみ）
from typing import Callable, Optional
from dataclasses import dataclass

@dataclass
class WriteResult:
    status: str  # "written" | "duplicate" | "retry_required"
    reason: Optional[str] = None

def append_jsonl(
    filepath: Path,
    record: dict,
    *,
    duplicate_check: Optional[Callable[[list[dict]], bool]] = None,
) -> WriteResult:
    """JSONL ファイルに1行追記する。duplicate_check を渡すと、ロックを保持した
    まま「その callback が True を返すか」を確認してから書く。**判定ロジック
    自体は呼出元が渡す**——本関数は独自の一意性ロジックを持たない
    （巡5 Q1(h)「独自の r.get(unique_field)==val 判定」への直接対応。
    corrections.jsonl の場合、この callback は has_duplicate_id（§2.1）の
    部分適用そのもの——コード上も同一関数を呼ぶ、概念上の一致ではない）。
    duplicate_check=None（既定）のときは従来どおり無条件追記——
    corrections.jsonl 以外の約30ストアはこの引数を渡さないため挙動は不変。
    """
    is_new = False
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                _fcntl.flock(f, _fcntl.LOCK_EX)
            try:
                if duplicate_check is not None:
                    existing = _read_records_locked(filepath)  # isinstance(dict) フィルタ済み
                    if duplicate_check(existing):
                        return WriteResult(status="duplicate")
                is_new = f.tell() == 0
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()  # ← ロック解放前に flush する（巡4/巡5で確認済みの必須修正）
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
        return WriteResult(status="retry_required", reason=str(e))
```

### 2.4 UUID 衝突時の扱い（[Should] 反映）

`new_correction_id()`（§2.1）は**再発行（リトライ）ロジックを持たない**。
`uuid.uuid4()` の衝突確率（2^-122）は無視できるため、発行側でリトライ機構を
持つコストは正当化されない。**万が一衝突が起きた場合（実装不具合・テスト
差替え・信頼境界②の「再 ingest による重複」等）は、§2.2 の保存境界が
`duplicate_id` を返して拒否する**——生成側は失敗を隠さず、保存側が最終防衛
として機能する、という役割分担を設計として固定する。W1〜W4 の呼出側は
`duplicate_id`/`invalid_id` を受け取ったら該当 correction の記録を諦め、
stderr へ警告を残す（既存 `append_jsonl` の「失敗時は静かに継続」慣習を
踏襲しつつ、原因の可視化だけ強化する）。

### 2.5 W3・W4 の複数件追記と部分成功契約（[Should]・変更なし）

W3・W4 は `append_correction_record` を1件ずつ呼ぶ（§1.3の呼出グラフ）。
**契約**: 1件でも `"appended"` 以外を返したら、その時点で処理を止める
（fail-fast。残りの候補は追記しない）。戻り値に「成功件数・失敗した1件の
インデックスと理由・未処理件数」を含める。**W4 固有の追加契約**:
`migrate_reflect_queue.py:127`（`LEARNINGS_QUEUE.write_text("[]", ...)`）は、
**全件が `"appended"` を返した場合にのみ**実行する。部分失敗の場合は元 queue
を空にしない（失敗分がどちらのストアにも存在しない状態＝データ消失を防ぐ）。

### 2.6 (a) の境界を明示する（変更なし・巡4から維持）

本設計が塞ぐのは「新規に発行する ID が既存レコードと重複することを保存前に
拒否する」ことだけである。ファイルに**既に**存在する重複（本設計のコードが
書く前から存在したもの）を検出・解消することは対象外（round 0 ③「重複修復
機能そのもの」が対象外）。§2.2 の重複確認は「これから書こうとしている1件の
ID」についてのみ既存レコードと突合する。

## 3. 既存241件への移行

### 3.1 運用契約（バックアップ必須化・裁定②）

**運用契約**: 移行の実行中は corrections.jsonl への他の書込み（W1〜W4・
`update_reflect_status`・`prune`・その他の正規化スクリプト）を行わない。

**runbook（実装1巡へ先送りせず、本設計の契約として確定する・[Should]反映）**:

1. **実行前**: 全 Claude Code セッションを閉じる（hook 自動起動の停止）。
   `daily`/`backfill`/`prune`/`promotion` 系のスクリプトを手動実行しない。
   実行責任者は移行スクリプトを起動する本人
2. **バックアップ**: 移行実行の直前に `corrections.jsonl` を
   `corrections.jsonl.bak-<ISO8601タイムスタンプ>` としてコピーする
   （`shutil.copy2` で mtime も保存する）。**この手順は移行スクリプトの
   `--dry-run` 以外の実行時に必須**とし、`--apply` 実行時にバックアップの
   存在を確認できなければ処理を中止する（実装1巡で `--skip-backup` 明示
   フラグ無しには進めないゲートとして設計する）
3. **identity 記録**: 読込み直前と `os.replace` 直前・直後の各時点で
   `(inode, size, mtime_ns, sha256)` をログへ記録する（§3.3 のコード）。
   **これは検出機構ではなく監査証跡**——移行後に「何かおかしい」と
   気づいた場合に、人間が記録を突き合わせて調査するための材料
4. **`conflict`/`incomplete` 時の扱い**: 自動リトライしない。人間が
   `--dry-run` を再実行して原因を調査してから判断する
5. **再開条件**: 上記1〜3を満たした状態で再実行する。冪等性（§3.5）により
   再実行は安全

**契約違反時に何が起きるか（裁定②・撤回した主張の訂正）**: 移行が読込みを
終えてから `os.replace` を完了するまでの間に、他の writer（hook 等）が
追記すると、**その追記は移行の `os.replace` によって失われる可能性がある**。
§3.3 の identity/hash 再照合は、この再照合を行う**時点**より前に起きた
変化を検出できるが、**再照合そのものと `os.replace` の間、および
上記手順を守らなかった場合に生じるあらゆる競合を、本設計は必ず検出できる
とは主張しない**。検出できる範囲と検出できない範囲を§3.4に明記する。

### 3.2 移行スクリプト（新規 `scripts/migrate_correction_id_backfill.py`・
設計のみ・擬似コードの誤りを修正）

```python
@dataclass
class MigrationResult:
    status: str  # Literal["dry_run", "completed", "incomplete", "conflict", "retry_required"]
    total: int = 0
    newly_assigned: int = 0
    malformed_lines: int = 0
    duplicates: list[str] = field(default_factory=list)
    reason: Optional[str] = None   # ← "error" ではなく "reason" に統一（巡5[Must]の修正）
    initial_identity: Optional[dict] = None  # 監査証跡（§3.1手順3）
    final_identity: Optional[dict] = None


def _identity_of(stat_result, content: str) -> dict:
    return {
        "inode": stat_result.st_ino, "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def migrate(filepath: Path, *, dry_run: bool = True) -> MigrationResult:
    if not _HAVE_FCNTL:
        # 裁定③: 移行自体は flock を使わないが、W1〜W4 の追記保証が働かない
        # 環境で移行だけ進めても blocking (a) の全体保証が崩れるため、
        # 移行も同じ理由で非対応とする。
        return MigrationResult(status="retry_required",
                                reason="fcntl unavailable: migration is not supported")
    if not filepath.exists():
        return MigrationResult(status="completed", total=0, newly_assigned=0)

    try:
        if filepath.is_symlink():
            return MigrationResult(status="conflict", reason="symlink_not_supported")
        orig_stat = filepath.stat()
        raw_content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return MigrationResult(status="retry_required", reason=str(e))

    initial_identity = _identity_of(orig_stat, raw_content)

    raw_lines = raw_content.splitlines()
    new_lines: list[str] = []
    newly_assigned = 0
    malformed = 0
    final_records: list[dict] = []

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

    duplicates = find_duplicate_ids(final_records)   # §2.1 と同一関数（単一ソース）
    if duplicates:
        return MigrationResult(status="conflict", total=len(raw_lines),
                                duplicates=sorted(duplicates),
                                initial_identity=initial_identity)

    if dry_run:
        return MigrationResult(status="dry_run", total=len(raw_lines),
                                newly_assigned=newly_assigned, malformed_lines=malformed,
                                initial_identity=initial_identity)

    new_content = "\n".join(new_lines) + "\n" if new_lines else ""

    # --- tempfile への書込みを先に完了させる（mkstemp 失敗は tmp_path 未代入の
    #     まま参照しない・巡5[Must]の修正） ---
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(filepath.parent), suffix=".correction_id_migrate.tmp"
        )
    except OSError as e:
        return MigrationResult(status="retry_required", reason=str(e),
                                initial_identity=initial_identity)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.chmod(tmp_path, stat.S_IMODE(orig_stat.st_mode))

        # --- identity/hash 再照合は tempfile 完成後・os.replace 直前に置く
        #     （巡5 Q2 の指摘: 旧稿は読込み直後に照合しており、tempfile I/O
        #     の全時間が未検出窓になっていた。ここへ移すことで、窓は
        #     「この再照合と os.replace の間」だけに縮む——ただしゼロには
        #     ならない。裁定②によりこれを「必ず検出できる」とは主張しない） ---
        cur_stat = filepath.stat()
        cur_content = filepath.read_text(encoding="utf-8")
        cur_identity = _identity_of(cur_stat, cur_content)
        if (cur_identity["inode"], cur_identity["size"], cur_identity["mtime_ns"],
            cur_identity["sha256"]) != (initial_identity["inode"], initial_identity["size"],
                                          initial_identity["mtime_ns"], initial_identity["sha256"]):
            os.unlink(tmp_path)
            return MigrationResult(
                status="conflict", total=len(raw_lines),
                reason="file changed between read and replace (identity/hash mismatch)",
                initial_identity=initial_identity, final_identity=cur_identity,
            )
        os.replace(tmp_path, filepath)
    except (OSError, UnicodeDecodeError) as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return MigrationResult(status="retry_required", reason=str(e),
                                initial_identity=initial_identity)

    try:
        verify_content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason=f"post-write read failed: {e}",
                                initial_identity=initial_identity)
    if verify_content != new_content:
        return MigrationResult(status="incomplete", total=len(raw_lines),
                                newly_assigned=newly_assigned,
                                reason="post-write content mismatch",
                                initial_identity=initial_identity)

    final_stat = filepath.stat()
    final_identity = _identity_of(final_stat, verify_content)
    return MigrationResult(status="completed", total=len(raw_lines),
                            newly_assigned=newly_assigned, malformed_lines=malformed,
                            initial_identity=initial_identity, final_identity=final_identity)
```

**移行中マーカーファイルを採らない理由**: 巡5レビューは「移行中マーカーを
W1〜W4 が検知して拒否する」方式も提案したが、本設計は採らない——マーカー
ファイルは round 0 が明示的に禁じた「共有ロックの新設」と同じカテゴリの
協調機構（新しい調停ファイルを介した排他）であり、巡3・巡4がこの種の
機構を導入するたびにレビューが指摘した「正しさが全経路の参加に依存する」
構造を再導入するリスクがある。**裁定②が示した方向性（検出を諦め、運用と
記録で受け止める）と整合させ、マーカーは追加しない**。必要になれば
将来の別 issue として起票する。

### 3.3 5値の終了ステータスと CLI exit code（blocking g）

| status | 意味 | exit code |
|---|---|---|
| `dry_run` | `--dry-run`（既定）。書込みなし | 0 |
| `completed` | 書込み成功・事前重複検査通過・identity 再照合通過・書込み後の内容一致確認済み | 0 |
| `incomplete` | 書込みは成功したが、書込み後の再読込みで内容が一致しなかった、または読込み自体が失敗した | 1 |
| `conflict` | ①新規発行 ID 同士の重複、②identity/hash 再照合の不一致、③対象が symlink、のいずれかを検出。書込みは行っていない（元ファイル無傷） | 2 |
| `retry_required` | `fcntl` 非対応、または読込み・stat・tempfile 作成/書込み/chmod・`os.replace` のいずれかで `OSError`/`UnicodeDecodeError` | 3 |

### 3.4 検出できる範囲・できない範囲（裁定②・§3.1からの詳細）

| 競合の発生タイミング | 検出できるか |
|---|---|
| 移行の初回読込み**前** | 検出**できる**（その時点の内容が `initial_identity` に正しく反映されるため、移行はその内容を正として扱う——実害なし） |
| 初回読込み**後**・identity再照合**前**（tempfile I/O を含む） | 検出**できる**（§3.2 の再照合が initial と現在の差分を検出し `conflict` を返す） |
| identity再照合**後**・`os.replace` **前** | **検出できない**（残存窓。I/O を挟まないため極めて短いが、ゼロではない） |
| `os.replace` **後** | 検出**できる**（post-write 内容比較で `incomplete` になる——ただしこれは「移行が意図した内容と一致するか」の確認であり、「消えた追記を見つける」ものではない） |

**「移行が意図した内容を書けたか」の確認と「他 writer の追記が失われて
いないか」の確認は別物である**（裁定②の核心）。post-write 比較は前者だけを
保証する。後者（失われた追記の検出）は、identity/hash 再照合が捉える
「読込み後・再照合前」の窓に限られ、**再照合後の窓は本設計の手段では
原理的に検出できない**。

### 3.5 中断耐性（blocking e・変更なし）

計算（読込み→変換、メモリ上のみ）と書込み（`tempfile` + `os.replace`）を
分離。計算中に kill されれば元ファイルは無傷。`tempfile` 書込み中に kill
されれば元ファイルは無傷（`os.replace` に未到達）。`os.replace` 自体は OS
レベルで atomic。crash consistency の範囲はプロセス kill・未処理例外までで
あり、OS クラッシュ・電源断は対象外（`fsync` を呼ばない）。

**冪等性**: 「既に有効な `correction_id` を持つレコードはスキップ」により、
中断後の再実行は常に安全。

## 4. resolver（読取専用・変更なし）

```python
# scripts/lib/rl_common/correction_id.py（続き）
@dataclass
class ResolveResult:
    status: str  # "found" | "not_found" | "ambiguous" | "invalid_id"
    record: Optional[dict] = None
    match_count: int = 0

def resolve_correction_id(records: list[dict], correction_id) -> ResolveResult:
    """読取専用。#587 が更新経路の安全性を設計する前提で、本関数は書込みを
    一切行わない。"""
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

`append_correction_record`（§2.2）・`migrate`（§3.2）・`resolve_correction_id`
（本節）はいずれも `validate_correction_id`/`has_duplicate_id`/
`find_duplicate_ids`（§2.1）を import して使う——独自の正規表現・独自の
一意性ロジックを持たない（§2.3 の `duplicate_check` callback 設計により、
`append_jsonl` 自身もこの単一ソースへ実際にコードとして到達する）。

## 5. `#379` 新設凍結への非抵触（維持・変更なし）

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`）・`:23-37`（凍結対象
4集合）・`:261-275`（`assert_no_new_keys`）を巡1で実物照合済み。
`correction_id` は既存ストアへの新規フィールド追加のみ。共有ロック・
sidecar ファイル・移行中マーカーは第3版以降一切新設していない（§3.2で
マーカーを採らない理由を明記）。

## 6. 既存 reader の再列挙（blocking d — 3つのデータフロー経路として整理）

### 経路1: hook → auto-memory queue（永続化）→ drain → LLM prompt（file:line 修正・巡5[Should]対応）

`hooks/auto_memory_runner.py:57-90`・`:123-154` が corrections.jsonl から
直近 N 件のレコード全体を読み、`scripts/lib/auto_memory_broker.py:enqueue`
（`:235-289`、`corrections` フィールドとしてレコードのリストをそのまま
`queue_path_for(...)` へ**永続化**）がキューへ書く。**drain 側は
`emit_memory_requests`（`scripts/lib/auto_memory_broker.py:586-608`）が
`rec.get("corrections", [])` を取り出し**（`:601-608`）、`_build_prompt`
（`:322-345`、`json.dumps(corrections, ...)` で**レコード全体**を LLM
プロンプトへ直接埋め込む）へ渡す（巡5レビュー「未追跡」指摘への対応——
実体の file:line を確認し反映した）。**`correction_id` はこの経路全体に
露出する**。

### 経路2: hook → checkpoint（永続化）→ SessionStart truncate 表示

`hooks/save_state.py:76-104` が corrections.jsonl 全件を読み
`corrections_snapshot` として checkpoint JSON へ**永続化**する
（`hooks/save_state.py:120-136`）。`hooks/restore_state.py:41-77`
（`_summarize_checkpoint_for_output`）が直近 `MAX_SNAPSHOT_ITEMS=20`/
`MAX_SNAPSHOT_CHARS=8000` 文字に truncate した上で SessionStart の
Claude context へ print する。**各 dict は投影されず丸ごと truncate
されるだけなので、残ったレコードの `correction_id` は露出する**。

### 経路3: reflect の allowlist 投影（影響なし）・resolver CLI（新設・投影する）

`skills/reflect/scripts/reflect.py:834-879`（`build_output`）は各レコードから
明示したキーだけを新しい dict へコピーする——`correction_id` はこの
allowlist に無いため、既存 `--view`/`--dry-run` 出力は無改修で
`correction_id` を露出しない。`optimize_core.py:182-196`
（`build_patch_prompt`）も `.get("message")`/`.get("correction_type")`/
`.get("extracted_learning")` のみを参照——影響なし
（`variant_generation.py:55-90` も同経路）。

### 判断（維持）

経路1・2について、allowlist 投影を導入せず露出を明示的に受容する。
`correction_id` は32文字16進の固定形式文字列で注入攻撃の運び屋になりえず、
既存の自由記述フィールド（`message`/`extracted_learning`）よりリスクが低い。

### 既知の不整合（記録のみ・blocking にしない・[Must]項目ではないが巡5指摘を反映）

`hooks/auto_memory_runner.py:87-90`・`hooks/save_state.py:102-104` は
slug 指定時に `pj_slug.record_project_match(rec, slug)` を呼ぶが、
`record_project_match`（`scripts/lib/pj_slug.py:280-328`付近）は最終的に
`rec.get(...)` を呼ぶため、非 dict レコード（`reflect.py:111-124` の
`load_corrections` 相当の経路が scalar/list を含めた場合）が混入すると
既存コードでも例外になりうる。**これは本設計が新たに起こす破壊ではなく
既存の不具合**であり blocking (d) の対象にはしないが、非 dict 行を温存する
本設計の移行契約（§3.2）との既知の不整合として記録する（別 issue の
対象候補）。

## 7. ID を人間が取得する経路（`--resolve-source-id`・変更なし）

既存の pending 一覧表示・`--dry-run` 出力のフォーマットは変更しない。
新設 CLI `reflect.py --resolve-source-id <source_correction_id>` は
`source_correction_id` から対応する `correction_id` を返す（`isinstance(r, dict)`
ガードを候補走査の全箇所で適用する——非 dict 行を安全に扱う契約）。
読取専用。

## 8. やらないこと（完成条件③の対象外の再掲）

- 柱2の集計・表示の変更 / 反映イベントの追記と read 時 fold（#587）/
  `reflect_status` の意味論変更 / `#379` 新設凍結の解除
- `update_reflect_status`・`--apply`/`--skip`/`--skip-all` の変更
- blocking (b)（削除・並べ替えで別レコードを指す）の解消 — #587 の担当
- 他経路（`update_reflect_status`・`prune`・`promote` の idiom invalidation・
  各種正規化スクリプト）の lost update（防止・検出とも）
- 重複修復機能
- **移行中マーカーファイルによる W1〜W4 の追記抑止**（§3.2 で検討し不採用）

## 9. 検証計画（巡5 Q5 の指摘をすべて反映して作り直す）

テストは `scripts/lib/rl_common/tests/test_append_jsonl_correction_id.py`・
`scripts/lib/tests/test_correction_id.py`・
`scripts/tests/test_migrate_correction_id_backfill.py`・
`hooks/tests/test_correction_detect_id.py`（W1実経路）・
`scripts/lib/correction_semantic/tests/test_promote_id.py`（W2実経路）
（いずれも新設）に置く。

| # | 壊す不変条件 | 変異（巡5の指摘を反映） | 期待結果 | 「緑のまま通す」変異の自己検証（論理で検算） |
|---|---|---|---|---|
| **W1〜W4実経路** | 実 writer が `append_correction_record` を経由する | `hooks/correction_detect.py`・`promote.py`・`backfill_preceding_tool_calls.py`・`migrate_reflect_queue.py` の各エントリポイントを、対象条件を満たす実データで呼び出し、生成される `record` を実ファイルへ保存する経路全体を通す | 各 writer の実行後、ファイルに追加された行が有効な `correction_id` を持つ | いずれかの writer が `store_write` を直接呼ぶよう変異させたら（裁定①違反）、`_guard_problem` の再チェック二重化 or ID未検証保存が起き、この試験の「有効なID」assert で落ちる |
| (a) 陰性（flush 削除を確実に検出する同期点） | 同一 ID の同時追記が不可分に拒否される。**flush() 削除変異を確実に検出する**（巡5「unlock直後にwithを抜けて close/flush されると緑になる」への対応） | P1 の `append_jsonl` 呼出しに対し、**`flock(LOCK_UN)` 実行直後・`with` ブロックを抜ける（＝暗黙 close/flush が起きる）直前**にテスト用フックで一時停止する。この停止点で P2 に同じ ID の追記を試みさせ、**P2 が完走してしまうかどうかを確認する**——正しい実装（flush が unlock 前）ならこの停止点は「P1 は既に flush 済み」の状態なので P2 は正しく `duplicate_id` を返す。`flush()` を削除した変異では、この停止点は「P1 は unlock 済みだが未 flush」の状態になるため、**P2 は P1 の書込みを見られず `written` を返してしまう** | 正しい実装: P2 は `duplicate_id`。`flush()` 削除変異: P2 は `written`（＝2行の重複が生じる） | 上記の停止点（unlock 後・close 前）を明示的に使うことで、巡5が指摘した「同期点が unlock 前までしか固定されていない」問題を解消した——この停止点でのテストが「flush 削除」を確実に赤くすることを論理で確認済み（flush の有無が読める内容を変える、という OS/Python の buffered I/O の性質に基づく） |
| (a) 陽性対照 | 異なる ID は両方保存され、内容も正しい | 異なる `correction_id` を持つ2レコードで同じ手順。**保存後にファイルを再読込みし、2レコードの全フィールドが入力と一致する**ことを確認する（巡5「status と行の存在だけでは、別内容へ書き換える実装も緑になる」への対応） | 両方とも `appended`。再読込みした内容が入力と完全一致 | 内容を書き換えて保存する実装は、この全フィールド一致 assert で落ちる |
| (a) 陰性2（新規×既存の衝突） | 新規発行 ID が既存の有効 ID と衝突しても検出する（巡5「固定 UUID の2件だけの衝突検査では既存×新規の衝突を見逃す」への対応） | fixture に、**既に有効な `correction_id` を持つレコード1件**と、移行対象（ID無し）レコード1件を用意し、`new_correction_id` を monkeypatch して**既存レコードと同じ値**を返させ、`migrate` を呼ぶ | `status == "conflict"`。元ファイルのハッシュが実行前後で一致 | 「新規発行 ID 同士」だけを検査し既存との衝突を見ない実装は、この monkeypatch 前提の試験で重複を見逃し `completed` を返すため落ちる |
| (a) 陽性対照2（既存不変+新規相互に一意） | 既存 ID は不変。かつ新規発行分は互いに異なる（巡5「全件同じ新規IDでもこの試験単独では緑」への対応） | 既存の有効 ID を持つレコード1件と、移行対象（ID無し）レコード3件以上を含む fixture で `dry_run=False` の移行を実行。既存分の ID が不変であることに加え、**新規発行された3件以上の ID が互いにすべて異なる**ことを明示的に assert する | 既存 ID 不変。新規発行分は相互に一意 | 全新規レコードへ同一 ID を割り当てる実装は「相互に一意」assert で落ちる |
| (c) 陰性（境界値マトリクス・巡5「単一値だけでは緩い判定が緑になる」への対応） | 欠落・`None`・空文字列・非文字列型（int/list/dict/bool）のいずれも保存されない | `correction_id` を①キー無し②`None`③`""`④`12345`（int）⑤`[1,2]`（list）⑥`{"x":1}`（dict）⑦`True`（bool）とした7種の record で、それぞれ**実ファイルに対して** `append_correction_record` を呼ぶ | 7種すべて `invalid_id`。ファイルの行数が実行前後で7回とも変化しない | `len(value) > 0` 等の緩い判定は③以外を通しうるが、④〜⑦の型チェックまで含めたこのマトリクスで必ずどこかの型で落ちる |
| (c) 陰性2（形式境界値） | 31文字/33文字/大文字混在の16進文字列が有効と誤認されない | ①31文字hex ②33文字hex ③大文字混在32文字hex の3種で `validate_correction_id` を呼ぶ（+§2.2経由の保存試験も同様に行う） | 3種すべて `False`/`invalid_id` | 長さを見ない・大文字小文字を無視する実装はこのいずれかで落ちる |
| (c) 陽性対照（フィールド有無だけでない全体一致・巡5「保存行を{}に置換する実装でも緑」への対応） | 妥当な `correction_id` を持つレコードが**改変されず**保存される | 妥当な `correction_id`（32文字小文字hex）を持つ、複数フィールドを含む実データ相当のレコードを実ファイルへ追記する | `status == "appended"`。**保存後に再読込みしたレコードが入力レコードと完全一致**（`correction_id` だけでなく全フィールド） | 保存行を `{}` や部分的な dict に置換する実装は、この全体一致 assert で落ちる |
| (d) 陰性（実経路・全フィールド確認・巡5「correction_idだけ保持しmessage等を落とす実装でも緑」への対応） | 新フィールド追加が実データフロー経路（W1→queue永続化→drain→prompt）を壊さない | fixture のレコード（`correction_id` に加え `message`/`session_id`/`timestamp` 等の複数フィールドを含む）を `append_correction_record` で実ファイルへ書き、`enqueue`→`emit_memory_requests`→`_build_prompt` の順に**実際に通す** | 最終プロンプト文字列に、`correction_id` を含む**全フィールドの値**が含まれる | queue 永続化関数が `correction_id` 以外のフィールドを黙って drop する実装は、この全フィールド確認で検出できる |
| (d) 陽性対照（フィールド独立性・巡5「message とreflect_statusペアでは片方しか見ない実装が緑」への対応） | 各意味フィールドが独立して出力に反映される | `message` のみ変更した fixture・`reflect_status` のみ変更した fixture・`correction_type` のみ変更した fixture を**それぞれ独立に**用意し、出力がそれぞれ独立に変わることを確認する | 3種の fixture すべてで、対応する出力が変わる | いずれか1フィールドしか見ない実装は、そのフィールド以外を変えた fixture で出力不変となり落ちる |
| (e) 陰性1（正常経路が os.replace を実際に呼ぶことを固定・巡5「正常経路が直接rewriteでも緑」への対応） | 中断で部分状態が生じない。かつ正常経路が実際に tempfile+`os.replace` を使う | `os.replace` を spy でラップし、正常な移行実行で**実際に1回呼ばれる**ことを確認した上で、`os.fdopen` の `write` を実際に一部バイトを書いた後に `OSError` を送出するよう差し替えて `migrate` を呼ぶ | 正常系: `os.replace` が呼ばれ `completed`。異常系: 元ファイルのハッシュが処理前と一致、`status == "retry_required"` | 正常経路が `write_text` の直接書換えを使う実装は、spy の「呼ばれた」assert で落ちる |
| (e) 陰性2（mtime だけでは検出できないことを実際に再現し、hash の必要性を証明する・巡5[Must]の直接対応） | replace 直前の再照合が、行数・stat を維持したまま内容だけ変える改変も検出する | §3.2 の identity 再照合ステップの**直前**で一時停止し、その間に第三者プロセスが**1行追記+1行削除で合計行数を維持**した上で、**さらに `os.utime()` で mtime_ns を元の値へ明示的に復元し、パディング等で size も元と一致させる**（stat 情報だけでは区別不能な状態を意図的に作る——巡5「通常はmtimeが変わるので stat だけでも緑」への直接対応。本試験は「通常は」に頼らず stat が完全一致する状況を人為的に作る） | `status == "conflict"`（内容ハッシュが不一致のため検出される） | stat のみで hash を見ない実装は、この人為的に stat を一致させた変異で検出に失敗し `completed` を返すため落ちる。**この変異こそが hash 比較が必要な理由の直接証明になる** |
| (e) 陽性対照（バイト単位の raw line 一致・巡5「sort_keys=Trueで再直列化する変異は値を変えないため緑」への対応） | 無関係レコードの raw line がバイト単位で不変。変更されたレコードの raw line は期待どおりの正確な文字列になる | 競合の無い単純な移行実行後、**変更されなかった行は元の raw line 文字列とバイト単位で完全一致**することを確認する。**変更された行（ID付与）は `json.dumps({**元dict, "correction_id": 新ID}, ensure_ascii=False)` という期待文字列と完全一致**することを確認する（パースして意味的等価性を見るのではなく、文字列そのものを比較する） | 無関係行は raw 文字列が完全一致。変更行は期待文字列と完全一致 | 全行を `sort_keys=True` 等で再直列化する実装は、キー順が変わるためこの raw 文字列一致 assert で落ちる |
| (g) dry-run（読込みが実際に行われたことの確認・巡5「読まずに返しても緑」への対応） | dry-run は実際にファイルを読み、書込みをしない | malformed 行を複数含む fixture で `--dry-run` を呼ぶ | `status == "dry_run"`。ハッシュ不変。**`malformed_lines` の値が fixture の実際の malformed 行数と一致する**（読込みを実際に行った証拠） | ファイルを読まず固定値を返す実装は `malformed_lines` の値が一致せず落ちる |
| (g) completed（malformed行の温存・4値すべての exit code） | malformed行が保存され、全5 status が CLI exit code と対応する | ①malformed行を含む fixture で `--apply` を実行し、**書込み後のファイルに malformed 行がバイト単位でそのまま残る**ことを確認する ②`incomplete`/`conflict`/`retry_required` それぞれを引き起こす fixture で CLI を実行し、exit code が 1/2/3 と一致することを確認する | ①malformed行が温存される ②各 status に対応する exit code が正しい | malformed 行を黙って削除する実装は①で落ちる。exit code をハードコードし忘れた実装は②で落ちる |
| (h) `is`比較+振る舞い連動（validate_correction_id と has_duplicate_id/find_duplicate_ids の両方・巡5「validatorだけ共有し一意性は別実装のままでも緑」への対応） | 4関数（append/migrate/resolve + `append_jsonl` の duplicate_check callback）が同一の validator・重複判定関数を実際に呼ぶ | ①`inspect` で4箇所が同一オブジェクトを import していることを確認 ②`validate_correction_id` を monkeypatch して常に `True` を返すよう差し替え、4関数を同時に呼び挙動が揃って変わることを確認 ③`has_duplicate_id`/`find_duplicate_ids` を monkeypatch して常に `True`/全件重複を返すよう差し替え、append と migrate の両方が揃って `duplicate_id`/`conflict` を返すことを確認 | ①`is` 比較で一致 ②③ともに揃って変わる | `append_jsonl` の `duplicate_check` callback が実は独自の `r.get(...)==val` ロジックのままだった場合、③の monkeypatch は effect を持たず、append 側だけ挙動が変わらないため落ちる（巡5がまさにこの構造を指摘していた——本版は callback 設計でこれを解消した） |
| (h) 陽性対照（境界値マトリクスを3者へ同一適用） | (c) の境界値マトリクス（7種+3種）を append/migrate/resolve の3者すべてに同一入力で通し、3者が一致した判定をする | 上記10種の値をそれぞれ3関数（`append_correction_record`・`migrate` の内部検証・`resolve_correction_id`）へ通す | 10種×3関数=30通りすべてで一貫した判定（有効/無効） | いずれか1関数だけが特定の値を誤って有効と判定すれば、その1マスで assert が落ちる |
| 読取専用（隔離ディレクトリ全体の差分・巡5「別ディレクトリのログやcheckpointを書く変異は緑」への対応） | resolver/CLI が corrections.jsonl 以外も含め、いかなるファイルへも書込まない | `HOME`/`DATA_DIR` を隔離した一時ディレクトリへ向け、`reflect.py --resolve-source-id <値>` を実行する前後で**ディレクトリツリー全体**（`corrections.jsonl` だけでなく checkpoint・ログ・その他生成物）のファイル一覧とハッシュを比較する | 実行前後でディレクトリツリー全体が完全に不変 | 別ファイル（例: 実行ログ）へ書込む実装は、この全体差分比較で検出できる（corrections.jsonl 単体のハッシュだけを見る試験では検出できなかった） |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `append_jsonl` の `f.flush()` 呼出しを削除した変異ビルドを作り、(a) 陰性が
  （unlock後・close前という正しい同期点で）赤くなることを実際に確認する
- `migrate` の identity/hash 再照合を、`os.utime()` で mtime を偽装した
  fixture に対して stat のみ（hash 無し）に弱めた変異ビルドで実行し、
  (e) 陰性2が赤くなることを確認する

**探索したが未探索のまま残すクラス**: `_HAVE_FCNTL=False` 環境での
`append_correction_record`/`migrate` の起動時拒否が正しく機能するかの
実機確認（当環境は macOS のため `_HAVE_FCNTL=True` が既定であり、実装1巡で
`_HAVE_FCNTL` を強制的に `False` へ monkeypatch した状態での試験が必要）／
`store_write._guard_problem` の re-export 方法（`_` プレフィックス関数の
公開契約は実装1巡で確定する）。

## 10. 自己検証: この設計が成立しなくなる入力・順序・中断点（3件以上）

1. **§3.4 の残存窓（identity再照合後・`os.replace`前）で、W1（hook）の
   追記が失われる**: これは検出できない（裁定②で明記済み）。**設計の答え**:
   運用契約とバックアップ（§3.1）で受け止める。窓自体はごく短い（I/O を
   挟まない）が、ゼロではないことを人間の判断材料として明記する（§11）
2. **W2が`store_write_raw`経由でテスト/isolation パスへ書く場合**: §1.3の
   呼出グラフ変更により、W2はテスト/isolation 用途でも`append_correction_record`
   を直接呼ぶよう改修する（従来の`store_write_raw`分岐は使わない）。
   **設計の答え**: これはpromote.pyの呼出し先変更（`store_write_raw(...)`
   →`append_correction_record(...)`）を実装1巡の完了条件に含めることで
   担保する。既存のテスト/isolation用の明示パス指定という**用途**自体は
   `append_correction_record`が`filepath`引数を受け取る設計のため維持できる
3. **移行を2回連続実行する（1回目`--dry-run`、2回目`--apply`）が、その間に
   誰かが手編集で`correction_id`を持つ行を1つ消す**: 1回目の`dry_run`は
   「N件に新規付与予定」と報告するが、2回目の実行時にはその1件が「ID無し」
   に戻っているため、2回目はN+1件に付与する。**設計の答え**: 信頼境界②
   「手編集」の範囲内であり許容する
4. **`_guard_problem`のimport元が`store_write.py`から変わる（将来のリファクタ）**:
   `append_correction_record`が private関数を直接importする設計（§2.2）は、
   `store_write.py`側の内部構造変更に弱い。**設計の答え**: 実装1巡で
   `store_write.py`側に`_guard_problem`を安定した公開APIとして
   export（アンダースコアを外すか、明示的なpublicラッパーを用意する）
   ことを完了条件に含める——本設計は「二重実装しない」という制約のみを
   固定し、公開方法の詳細は実装1巡の判断に委ねる（§11）

## 11. 人間の判断が要る点

1. **`store_write._guard_problem`の公開方法**（自己検証4）: privateのまま
   importするか、公開APIとして切り出すか
2. **バックアップ手順（§3.1手順2）の強制レベル**: `--skip-backup`
   フラグでバイパス可能にするか、完全に必須（フラグなし）にするか
3. **§3.4の残存窓（検出不能）の実害許容度**: 現状の設計（運用契約+
   バックアップ+identity記録での受け止め）で十分か、将来的に移行中
   マーカー等の追加保護を別issueとして起票するか
4. **#587との統合順序**: 本設計（ID発行・移行・resolver）を先にマージし、
   #587が`correction_id`を使って更新経路の安全性を設計する、という
   順序でよいか（本設計はこの順序を前提に書いている）
