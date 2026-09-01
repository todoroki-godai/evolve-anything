# #595: corrections.jsonl の全 writer をロック協調させ、追記行の消失を止める設計（初版）

## 0. スコープ（issue #595 完成条件 round 0 から転記）

### ① 守る対象
`corrections.jsonl` へ正しく書かれたレコードが、別の writer の書き戻しによって、**気づかれないまま**失われること。

### ② 信頼境界
脅威に数えるのは**自分たちの運用ミスのみ**（作業の中断・並行セッション・移行スクリプトの流し忘れ・手編集）。悪意ある改変・意図的なデータ破壊は数えない。

### ③ 対象外
- 柱2の測定そのもの（#587。本 issue が終わってから戻る）
- `results_board.py` への表示配線
- 新しい保存先ファイルの新設（#379 新設凍結は継続。既存 `corrections.jsonl` の中で完結させる）
- `corrections.jsonl` 以外のストアの writer 協調

### ④ blocking
- (a) 全文書き換え中に追記された行が失われる経路が1つでも残る
- (b) 失われたことが後から検出できない
- (c) 書き換え側と追記側が別のロックを取る（協調していない）
- (d) index を使う経路と使わない経路で、同じファイルに対する「何行目か」の解釈が食い違う
- (e) `corrections.jsonl` へ書く経路の洗い出しに、機械的な裏付けがない

### ⑤ 検証方法
(a)〜(e) 各1件以上の陰性試験（赤になるべき変異）＋陽性対照（正常データ・意味を変えない書き換えで誤検出しないこと）。委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する。緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する。並行性の検証は同期点を置いた実行だけに頼らない（呼出順を記録して assert する決定論試験を併用する）。

### 制約（委譲プロンプトより）
- 新しい保存先ファイルを作らない
- 追記の唯一の境界は `append_correction_record`。ここを迂回する新しい書き込み口を作らない
- `file_lock.py` が既にあるので、ロック機構を新規に発明しない
- 既存の契約を弱めない（dry-run 純度・人間承認・write barrier・データ契約）

---

## 1. 現状の棚卸し（自分で実測）

### 1.1 実測コマンドと結果

**取得時刻**: 2026-09-01T04:51:08Z（UTC）／対象 SHA: `547a032a76403136b1bc6230ac59625daf2ab4f0`（`/Users/matsukaze-takashi/wt/ea-595`、`origin/main` 起点の worktree）

**手順**:

1. 文字列 `"corrections.jsonl"` の直接参照を洗い出す:
   ```
   grep -rn "corrections\.jsonl" --include="*.py" scripts hooks skills bin \
     | grep -v "/tests/" | grep -v "test_"
   ```
   → 219 ヒット・約100ファイル（コメント・docstring 込み）。

2. 変数名（`CORRECTIONS_FILE` / `CORRECTIONS_PATH` / `corrections_file` / `corrections_path`）経由の間接参照も含めてファイル単位で洗い出す（文字列 grep だけでは変数越しの参照を取りこぼすため）:
   ```
   grep -rln "corrections\.jsonl\|CORRECTIONS_FILE\|CORRECTIONS_PATH\|corrections_file\|corrections_path" \
     --include="*.py" scripts hooks skills bin \
     | grep -v "/tests/" | grep -v "test_"
   ```
   → **71 ファイル**。

3. 71 ファイルそれぞれについて、書込みシグナル（`write_text` / `os.replace(` / `atomic_write_text` / `append_jsonl` / `append_correction_record` / `open(..., "w")` / `open(..., "a")` / `fdopen(..., "w")`）を機械的に grep（正規表現1本でなく、書込み手段ごとに複数パターンを OR で当てた）。

4. ヒットしたファイルを実際に Read し、対象パスが `corrections.jsonl` そのものか（他ファイルへの書込みは除外）を確認。

### 1.2 見つかった writer と issue 本文の6件との差分

**issue 本文の6件との照合**: 6件は全て実在を確認できた（一致）。加えて、上記の切り口②③④（`open` 系＋`atomic_write_text` の機械 grep）で**issue 本文に無い2件**を新規発見した。

| # | writer | file:line | 現状の書き方 | 何が失われるか | issue本文 |
|---|---|---|---|---|---|
| 1 | `update_reflect_status` | `skills/reflect/scripts/reflect.py:631-745`（書込みは`:735`） | 全読取→`filepath.write_text` | 読取後に追記された全レコード | ○（一致） |
| 2 | `invalidate_idiom_corrections` | `scripts/lib/correction_semantic/promote.py:584-649`（書込みは`:636-642`） | ロックなし・tmp+`os.replace` | 同上＋invalidation自体の巻き戻り | ○（一致） |
| 3 | `cleanup_corrections` | `scripts/lib/prune/corrections.py:51-117`（書込みは`:111-115`） | 全読取→`write_text` | 削除条件に合わない行も含め全て | ○（一致） |
| 4 | `migrate`（reflect_confirmed→promoted） | `scripts/migrate_reflect_promoted_status.py:51-80`（書込みは`:78`） | 全読取→`write_text` | 同上 | ○（一致） |
| 5 | `invalidate_subagent_contaminated_corrections` | `scripts/lib/corrections_subagent_invalidation.py:59-113`（書込みは`:106`） | 全読取→`atomic_write_text` | 同上 | ○（一致） |
| 6 | `migrate`（correction_id backfill） | `scripts/migrate_correction_id_backfill.py:60-190`（書込みは`:139-166`） | 全読取→identity確認→tmp+`os.replace` | 確認〜replace間の窓（ロック非共有） | ○（一致） |
| **7** | `backfill_corrections`（turn_index付与） | `scripts/lib/backfill_turn_indices.py:202-256`（書込みは`:253-254`、`_atomic_write`実体は`:65-72`） | 全読取→tmp+`Path.replace` | 全読取後に追記された全レコード（1回限りの移行スクリプトだが、`bin/`等から任意タイミングで再実行され得る） | **× issue本文に無し** |
| **8** | `_backfill_jsonl`（pj_slug正規化・`corrections`ストア分） | `scripts/lib/pj_slug_backfill.py:76-111`（呼出しは`:201-202`、`_atomic_write`実体は`:60-73`） | 全読取→tmp+`os.replace` | 同上 | **× issue本文に無し** |

**「これで全部」と言える根拠**: 71ファイルの機械 grep で書込みシグナルを持っていたのは上記8件のみ。残り63ファイルは全て read-only（集計・表示・フィルタ・診断）だった（個別に Read して確認済み）。ただし後述§7の限界により「絶対に0件」の証明はできない — 未実測の範囲を明示する。

### 1.3 除外した「書込みに見えるが対象外」のもの

- `append_correction_record` / `append_jsonl` を使う経路（`hooks/correction_detect.py:168`、`scripts/backfill_preceding_tool_calls.py:256`、`scripts/lib/correction_semantic/promote.py:562`、`scripts/migrate_reflect_queue.py:126`）は**新規レコードの追記のみ**で、既存レコードを書き換えない。追記境界を経由しており、本設計の対象は「これらと協調していない**全文書き換え**」なので、これらの内部実装は変更しない（協調させる側＝下記§2でロックを追加する対象ではあるが、書込みロジック自体は変更しない）。
- `scripts/migrate_reflect_queue.py:144` の `LEARNINGS_QUEUE.write_text(...)` は `learnings-queue.json` への書込みで `corrections.jsonl` ではない（対象外）。
- `scripts/lib/pj_slug_backfill.py` は corrections 以外に6ストア（subagents/usage/workflows/skill_activations/errors/usage-registry/sessions.db）も書き換えるが、完成条件③「`corrections.jsonl` 以外のストアの writer 協調」は対象外のため、**`corrections.jsonl` を書く呼出し経路だけ**を本設計の対象にする（§2.5）。

---

## 2. 移行後の書き込み契約

### 2.1 方式の選択: 追記オンリー変換ではなく「共有ロック下の read-modify-write」

issue タイトルは「追記へ統一する」だが、round 0 の blocking (a)〜(e) はいずれも「全文書き換えを完全に禁止し追記イベントだけにせよ」とは要求していない。要求しているのは (a) 消失ゼロ (b) 検出可能性 (c) 同一ロックでの協調 (d) index 解釈の一致 (e) 洗い出しの裏付け、の4点＋網羅性である。

真の追記オンリー化（既存レコードの `reflect_status` 更新・`invalidated` フラグ立てを「更新イベント行」として追記し、読取側で fold する）は、round 3 レビューが指摘した通り「読取側の fold ロジック（index/一意性/重複排除規則）」を新設する必要があり、これはまさに **#587 が正典として持つべき設計**（本 issue の対象外③に明記）である。#587 を経ずにここで fold 契約を作ると、#587 が改めてそれを設計し直す二度手間になり、かつ #587 の完成条件で定義される「イベント種別」「反映日時の定義」を先取りして固定してしまうリスクがある。

よって本設計は **choice B: 全 writer（追記・全文書き換えの両方）を単一の共有ロックで直列化し、全文書き換え writer は「ロックを保持したまま read → mutate → atomic replace」を行う** ことで、blocking (a)(b)(c) を解消する。追記イベント方式への転換は #587 に委ねる（§6 残存リスクに明記）。

**この選択で (a)(c) がなぜ解消するか**: 現状の消失は「追記 writer が `corrections.jsonl` 自体に取る flock」と「全文書き換え writer が無ロックまたは別ロックで tmp+`os.replace` する」が**協調していない**ことに起因する。`os.replace` は inode を差し替えるため、`corrections.jsonl` 自体に取ったロックは replace 後の新 inode を守らない（`file_lock.py` の docstring が既に明記している設計原則）。この原則に従い、**ロックは対象ファイルでなく sidecar（`corrections.jsonl.lock`）に取る**。全 writer が同じ sidecar ロックを直列に取得すれば、「読取→書換」の最中に他の writer が割り込む余地が構造的に無くなる。

### 2.2 新規ヘルパー: `corrections_write_lock`（新しいロック機構ではなく既存 `file_lock.file_lock` の適用）

`scripts/lib/rl_common/correction_id.py`（既存の「唯一の追記境界」モジュール）に追加する。新しいロック**プリミティブ**ではなく、既存の `file_lock.file_lock`（sidecar 排他ロック）を `corrections.jsonl` 専用のパスで呼ぶだけの薄いラッパー。

```python
# scripts/lib/rl_common/correction_id.py（追加）
from contextlib import contextmanager
from .file_lock import file_lock as _file_lock


def _corrections_lock_path(filepath: Path) -> Path:
    """corrections.jsonl の sidecar ロックパス（他 sidecar と同じ命名規約: <name>.lock）。"""
    return filepath.with_name(filepath.name + ".lock")


@contextmanager
def corrections_write_lock(filepath: Path):
    """corrections.jsonl への read-modify-write を全 writer 間で直列化する共有ロック区間。

    追記（append_correction_record）と全文書き換え（reflect status 更新・invalidation・
    prune・各種 backfill）が同じ sidecar ロックを取ることで、tmp+os.replace による
    inode 差し替えを跨いでも協調が壊れない（file_lock.py の設計原則どおり）。
    """
    with _file_lock(_corrections_lock_path(Path(filepath))):
        yield
```

### 2.3 `append_correction_record` の変更（唯一の追記境界はそのまま・内部でロックを取るだけ）

```python
# scripts/lib/rl_common/correction_id.py（既存関数の変更）
def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """correction record の唯一の追記境界。検証と重複拒否を常に実行する。"""
    if not persistence._HAVE_FCNTL:
        return AppendResult(status="unsupported_platform", reason="...")

    from .store_write import guard_problem
    problem = guard_problem("corrections.jsonl")
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    filepath = Path(filepath)
    with corrections_write_lock(filepath):          # ← 追加
        result = persistence.append_jsonl(
            filepath, record,
            duplicate_check=lambda existing: has_duplicate_id(existing, correction_id),
        )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)
```

`persistence.append_jsonl` 自体（`corrections.jsonl` を `"a"` open して個別 fd に flock する既存ロジック）は**無変更**。他ストア（`store_write` 経由の各種ストア）が同関数を使い続けるため、この関数のロック方式を変えると影響範囲が `corrections.jsonl` 以外に波及し、完成条件③「`corrections.jsonl` 以外のストアの writer 協調は対象外」を破る。`corrections_write_lock` はあくまで `append_correction_record`（＝corrections.jsonl 専用の境界関数）の**外側**に追加する。

二重ロック（sidecar ロック → その内側で `append_jsonl` 自身の fd ロック）になるが、両者は別ファイルのロックであり、同一ロックの入れ子（`file_lock.py` が警告する自己 deadlock）には当たらない。

### 2.4 全文書き換え writer 8件の変更パターン（共通形）

各 writer の**マッチング条件・削除条件・移行対象条件は一切変更しない**。read → mutate → write の全体を `corrections_write_lock` で包むことと、write を必ず atomic replace（tmp + `os.replace`）にすることの2点だけを揃える（`update_reflect_status` と `migrate`（reflect_confirmed→promoted）は現状 `write_text` 直書きのため、ここで tmp+`os.replace` に変える＝atomic 化の追加修正）。

共通形（擬似コード。実装は各ファイルの既存関数シグネチャを変えない）:

```python
def some_rewrite_writer(corrections_path: Path, ...) -> ...:
    with corrections_write_lock(corrections_path):
        if not corrections_path.exists():
            return ...  # 既存の not_found 分岐はロック内でも同じ

        text = corrections_path.read_text(encoding="utf-8")
        before_ids = _collect_valid_ids(text)          # 検出用（§2.6）

        # --- 既存のマッチング・変換ロジック（変更なし） ---
        new_lines, matched = _existing_transform(text)
        # -----------------------------------------------

        if dry_run or not matched:
            return ...  # dry-run はロックを取るだけで一切書かない（純度契約は維持）

        new_content = "\n".join(new_lines) + "\n" if new_lines else ""
        after_ids = _collect_valid_ids(new_content)
        _assert_no_unexpected_loss(before_ids, after_ids, removed_ids=_intended_removals)

        atomic_write_text(corrections_path, new_content)   # tmp + os.replace（file_lock.py 既存関数）
    return ...
```

**§1.2 の8件それぞれへの適用**:

| # | writer | 変更点 |
|---|---|---|
| 1 | `update_reflect_status` | 関数本体を `with corrections_write_lock(filepath):` で包む。`filepath.write_text(...)` を `atomic_write_text(filepath, ...)` に置換（tmp+replace化）。index 計算ロジックは §3 で共有ヘルパーへ切り出す |
| 2 | `invalidate_idiom_corrections` | 既存の tmp+`os.replace` はそのまま。全体（読取〜replace）を `corrections_write_lock` で包む |
| 3 | `cleanup_corrections` | `write_text` を `atomic_write_text` に置換。全体を `corrections_write_lock` で包む |
| 4 | `migrate`（promoted status） | `write_text` を `atomic_write_text` に置換。全体を `corrections_write_lock` で包む |
| 5 | `invalidate_subagent_contaminated_corrections` | 既存の `atomic_write_text` 呼出しはそのまま。全体を `corrections_write_lock` で包む |
| 6 | `migrate`（correction_id backfill） | 既存の identity 確認はそのまま維持（defense-in-depth・§6）。読取〜replace 全体を `corrections_write_lock` で包む（これにより identity 確認の「窓」は構造的に閉じる） |
| 7 | `backfill_corrections`（turn_index） | 全体を `corrections_write_lock` で包む。`_atomic_write` はそのまま |
| 8 | `_backfill_jsonl`（pj_slug正規化） | **corrections.jsonl 呼出し1箇所だけ**特別扱い: `backfill()` 内で `_backfill_jsonl(data_dir / "corrections.jsonl", ..., apply=apply)` を呼ぶ箇所だけ `corrections_write_lock` で包む。他6ストアの呼出しは対象外（完成条件③） |

### 2.5 `pj_slug_backfill.backfill()` の corrections だけを特別扱いする実装

```python
# scripts/lib/pj_slug_backfill.py（backfill() の変更）
def backfill(data_dir: Path, *, apply: bool = False) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    result: Dict[str, Any] = {"applied": apply, "data_dir": str(data_dir)}
    for key, filename, field in _JSONL_STORES:
        path = data_dir / filename
        if filename == "corrections.jsonl":
            from rl_common.correction_id import corrections_write_lock
            with corrections_write_lock(path):
                result[key] = _backfill_jsonl(path, field, apply=apply)
        else:
            result[key] = _backfill_jsonl(path, field, apply=apply)
    result["sessions_db"] = _backfill_sessions_db(data_dir / "sessions.db", apply=apply)
    return result
```

`_backfill_jsonl` 自体（読取・正規化・`_atomic_write` 呼出し）は無変更。この特別扱いは「1つの共有関数が複数ストアを触る」という既存構造をそのまま残しつつ、`corrections.jsonl` の呼出し箇所だけをロックで包む最小差分。

### 2.6 検出可能性（blocking b）: `_assert_no_unexpected_loss`

ロック協調により消失は構造的に起きなくなるはずだが、「本当に起きていない」ことを実行時に確認できないと、将来ロック取得を忘れる実装ミス（TOCTOU の再導入）が再び無音の消失を生む。`correction_id.py` の既存プリミティブ（`validate_correction_id` / `find_duplicate_ids` 相当のID集合演算）を使い、書換え直前に軽量な不変条件チェックを入れる。

```python
# scripts/lib/rl_common/correction_id.py（追加）
def _collect_valid_ids(text: str) -> set[str]:
    """有効な correction_id を持つ行だけを集合として返す（順不同・重複は1個に畳む）。"""
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and validate_correction_id(rec.get("correction_id")):
            ids.add(rec["correction_id"])
    return ids


class UnexpectedCorrectionLossError(RuntimeError):
    """全文書き換えで、削除意図の無い correction_id が消えたことを検出したときに送出する。"""


def assert_no_unexpected_loss(
    before_ids: set[str], after_ids: set[str], *, removed_ids: frozenset[str] = frozenset()
) -> None:
    unexpected = (before_ids - after_ids) - removed_ids
    if unexpected:
        raise UnexpectedCorrectionLossError(
            f"corrections.jsonl 書換えで {len(unexpected)} 件の correction_id が"
            f"意図せず消失: {sorted(unexpected)[:5]}..."
        )
```

各 rewrite writer は書込み直前に `assert_no_unexpected_loss(before_ids, after_ids, removed_ids=...)` を呼ぶ。**削除を意図する writer**（`cleanup_corrections`）は削除対象の `correction_id` 集合を `removed_ids` として明示的に渡す。これにより「意図した削除」と「意図しない消失」を区別する。

**既知の限界**: `correction_id` は #593（PR #594）以降のレコードにのみ存在する。それ以前の legacy レコード（`correction_id` フィールド無し）は本チェックの対象外（集合に入らないため、消えても検出できない）。これは §6 に残存リスクとして明記する（新しい識別子を発明して legacy レコードを遡及識別することは本 issue のスコープ外＝#587 や別 issue の対象）。

---

## 3. index 契約の整合（blocking d）

### 3.1 現状の index 依存経路は1つだけ

洗い出した8 writer のうち、**行/レコードの「何番目か」に依存するのは `update_reflect_status`（#1）だけ**。他7件はすべて `record.get(<field>) == <value>` 形式のフィールド述語でマッチし、位置には依存しない（例: `promoted_by == "idiom_dict" and idiom_key in target`、`source_path` に `/subagents/` を含む、`correction_id` が未設定、等）。

`update_reflect_status` の index は、呼出側（reflect CLI）が `load_corrections()`（`skills/reflect/scripts/reflect.py:111-124`）で読んだ配列の位置を指す。`load_corrections` と `update_reflect_status` は**現状は独立した2つのループ**で「空行はスキップ・壊れたJSON行はスキップ・有効なレコードだけを0始まりで数える」という**同じ規約**を別々に実装している（#588 の index ずれバグは、この規約が過去にずれていたことが原因）。同じ規約を2箇所に書き続ける限り、将来また片方だけ直して再びずれる構造的リスクが残る。

### 3.2 対策: 共有の enumerate ヘルパーへ集約

`scripts/lib/rl_common/persistence.py` に汎用ヘルパーを追加し、`load_corrections` と `update_reflect_status` の両方がこれを使う（「同じ規約」でなく「同じ関数」にする）。

```python
# scripts/lib/rl_common/persistence.py（追加）
def iter_indexed_records(text: str):
    """JSONL テキストを (0始まりの有効レコードindex, dict) の列として yield する。

    空行・JSON decode に失敗する行はスキップし、index をインクリメントしない
    （load_corrections / update_reflect_status が共有する唯一の enumerate 規約。
    #588: 2箇所の独立実装が同じ規約のつもりでずれていた反省を踏まえ、規約でなく
    関数そのものを共有する）。
    """
    idx = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield idx, record
            idx += 1
```

`reflect.py` の `load_corrections` と `update_reflect_status` は、それぞれの行走査ループをこのヘルパーの呼出しに置き換える（`load_corrections` は index を捨てて record だけ集める、`update_reflect_status` は index を使ってマッチ判定する）。**壊れた行・空行をそのまま出力に残す**という既存の非破壊契約（`update_reflect_status` が壊れた行を素通しする挙動）は、生の行文字列を別途保持することで維持する（ヘルパーは「index 計算」だけを共有し、書換え時の行再構成ロジックは各呼出し側に残す）。

### 3.3 「index を使わない経路」との整合

他7 writer はフィールド述語でマッチするため、index の解釈とは無関係。ただし §2.6 の `assert_no_unexpected_loss` は correction_id という**位置に依存しない識別子**で消失検出するため、index 経路とフィールド述語経路のどちらであっても同じ検出手段が効く（(d) の「解釈の食い違い」を検出という別の切り口からも保険する）。

---

## 4. 移行手順

### 4.1 これはスキーマ移行ではない（既存データは無変更）

本設計はレコードのフィールドやファイル形式を変えない。変わるのは「書き込み時にロックを取る」というコードの振る舞いだけなので、**既存の `corrections.jsonl` データに対する変換・バックフィルは不要**。デプロイは通常のコード変更として扱える（新しい `.lock` sidecar ファイルは初回書込み時に自動生成される。既存の `file_lock.file_lock` の実装が `lock_path.parent.mkdir(parents=True, exist_ok=True)` と `open(lock_path, "a")` を行うため、事前準備は不要）。

### 4.2 デプロイ中の混在期間（自PR適用前後で writer のバイナリが揃わない場合）

worktree/PR単位でこのコードが段階的に配布される間、**旧バージョンの writer プロセス**（ロックを取らない）と**新バージョンの writer プロセス**（ロックを取る）が同時に動く可能性はゼロではない（例: 別セッションが古いコードのまま `migrate_reflect_promoted_status.py` を直接実行する）。この場合、新バージョン側がロックを取っていても、旧バージョン側は無視して割り込めるため、消失は理論上まだ起こり得る。

対策は「1つの atomic なコード切替」以上のことをしない（完成条件の対象外＝ローリングデプロイ耐性は求められていない。信頼境界②「運用ミス」の範囲内で、マージ後は新バージョンのみが使われる前提）。

### 4.3 途中で中断した場合

- ロック取得前に中断: 何も起きていないので影響なし。
- ロック保持中（read〜mutate〜atomic replace の間）に中断: `atomic_write_text` / `os.replace` パターンは「書きかけの tmp ファイルが残る」ことはあっても、`corrections.jsonl` 本体は最後の完全な状態のまま（tmp→replace は不可分操作）。sidecar ロック（`corrections.jsonl.lock`）は `open(..., "a")` で保持したファイルディスクリプタが close されればOS側で自動解放されるため、プロセス強制終了でもロックは残留しない（`flock` は advisory lock でプロセス終了時に自動解放される）。
- 既存の `migrate_correction_id_backfill.py` の中断耐性（#593 で確立済み: バックアップ・再実行時の identity 再確認・部分失敗ステータス）はそのまま維持する。

---

## 5. 検証方法

### 5.1 陰性試験（各 blocking に1件以上）

| ID | blocking | 変異内容（壊す不変条件） | 通したい検査経路 |
|---|---|---|---|
| N-a-1 | (a) | `update_reflect_status` の呼出し**直前**に、別プロセス（simulate: 直接 `persistence.append_jsonl` を同じファイルへ）で新規レコードを追記し、`update_reflect_status` 完了後に**その新規レコードが残っているか**を検証。ロックを取らない旧実装（`corrections_write_lock` の呼出しをコメントアウトした変異）では、書換え中に読み込んだスナップショットに新規行が含まれず消える → 赤 | ロック実装ありでは新規行が保持される → 緑 |
| N-a-2 | (a) | `cleanup_corrections` の read と write の間に別プロセスが追記するタイミングを、flock をモンキーパッチして決定論的に同期点を作り再現（N-a-1 と別の writer で再現・「回避手段とは種類の違うもの」） | 同上 |
| N-b-1 | (b) | `assert_no_unexpected_loss` の呼出しを削除する変異を入れ、意図的に消失させるテストコードで「例外が飛ばない＝検出できない」ことを確認してから、削除しない実装で例外が飛ぶことを確認（削除変異＝赤、実装＝緑） | `UnexpectedCorrectionLossError` が飛ぶ |
| N-c-1 | (c) | `append_correction_record` から `corrections_write_lock` の呼出しだけを外す変異を入れ、N-a-1 と同じ手順（全文書き換え中に追記）を**追記側を無ロックにして**再現 → 消える | ロックを戻すと消えない |
| N-c-2 | (c) | 全文書き換え writer 側の `corrections_write_lock` の呼出しだけを外す変異（N-c-1 の逆方向） → 消える | ロックを戻すと消えない |
| N-d-1 | (d) | `iter_indexed_records` を使わず、`update_reflect_status` 側だけ「空行もカウントする」独自実装に戻す変異を入れ、空行を含む fixture で `load_corrections` が返す index と食い違うことを確認（#588 の再現） | 共有ヘルパー実装では一致する |
| N-e-1 | (e) | §1.1 の grep コマンドから `atomic_write_text` パターンを意図的に外し、writer #5・#8 相当が「見つからない」状態を作って、洗い出し結果が6件に減ることを示す（＝機械的裏付けが弱いとどう壊れるかを実演） | 全パターンを含む grep では8件出る |

### 5.2 陽性対照

- P-1: ロック実装ありの状態で、通常の読取（`load_corrections`）・通常の追記（1レコード）・通常の `cleanup_corrections`（decay 対象なし）を順に実行し、全レコードが1件も減らず・意味も変わらないことを確認（誤検出しないこと）。
- P-2: `assert_no_unexpected_loss` に、削除を**意図した** `removed_ids` を正しく渡すケース（`cleanup_corrections` が実際に decay 対象を消す通常ケース）で例外が飛ばないことを確認。

### 5.3 「回避手段とは種類の違うもの」2件以上（実際に適用して結果を報告する）

上記 N-a-1／N-a-2 は writer が異なる2種の再現（`update_reflect_status` と `cleanup_corrections`）で、原因（ロック非共有）は同じだが**発火経路が異なる**ため2件として数える。加えて:

- 変異C: `corrections_write_lock` の中身を `contextlib.nullcontext()` に差し替える（ロック関数は呼ばれるが実効しない）→ N-c-1/N-c-2 とは異なる「呼び出されているのに効いていない」パターンで消失を再現。
- 変異D: `atomic_write_text` を `path.write_text(...)`（非atomic）に差し替え、書込み中のプロセスkillをシミュレートして部分書き込みが起きないことのロック非依存の確認（tmp+replaceの不可分性そのものの検証。ロック契約とは別の不変条件）。

**未実測**: 本設計文書の時点では上記の陰性試験・陽性対照は実装前のため**未実行**。実装 PR で全件を実際に走らせ、緑残りが無いことを報告する（実装は本 issue のスコープ外＝本タスクは設計のみ）。

### 5.4 決定論的な呼出順アサーション試験

`test_append_jsonl_correction_id.py` の `test_exclusive_lock_is_acquired_before_duplicate_check` と同型のパターンで、`fcntl.flock` をスパイして呼出し順を記録し、以下を assert する:

- `corrections_write_lock` の `LOCK_EX` 取得が、read（`read_text` 呼出し）より前であること
- write（`os.replace` 呼出し）が `LOCK_UN` より前であること
- 上記を `update_reflect_status` と `cleanup_corrections` それぞれについて固定する（同期点を挟んだ2プロセス実行だけに頼らない・#593 の教訓を踏襲）

---

## 6. 受容する残存リスク

- **legacy レコード（`correction_id` 無し）の消失検出は効かない**（§2.6）。#593 以前のレコードは識別子が無いため `assert_no_unexpected_loss` の対象外。
- **ローリングデプロイ中の新旧混在**は対象外（§4.2）。信頼境界②の「運用ミス」の範囲内で許容する。
- **`fcntl` 非対応環境**（`persistence._HAVE_FCNTL is False`）では `append_correction_record` は `unsupported_platform` を返して何もしないが、全文書き換え writer 側は `corrections_write_lock` 内部で `fcntl.flock` を呼ぶため同様に失敗する（`file_lock.py` は fcntl 前提）。この場合の扱いは既存の `file_lock.py` の契約（例外送出）に従い、本設計では新たな fallback を作らない。
- **真の追記オンリー化（イベント fold）は行わない**。#587 が改めて設計する。本設計はそれまでの間、全文書き換え自体を安全にするだけ。

## 7. 未実測の項目

- §1 の洗い出しが完全であることの証明は無い（grep ベースの機械的裏付けであり、動的 import・`getattr(module, "corrections" + "_file")` のような難読化された参照があれば見逃す。今回はそのような難読化パターンは確認されなかったが、悉皆探索ではない）。
- §5 の陰性試験・陽性対照は実装前のため未実行（設計文書の時点の限界。実装 PR で実測結果を報告する）。
- ロック取得の待ち時間・スループットへの影響（`corrections.jsonl` への書込みが直列化されることで、複数 writer が同時に動く運用でレイテンシが増える可能性）は測っていない。
