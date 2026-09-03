# #595: corrections.jsonl の全 writer をロック協調させ、追記行の消失を止める設計（第3版・巡1レビュー反映）

## 変更履歴

- 初版（巡前）: 共有sidecarロック + ID集合差分/件数突合の2系統検出
- 第2版: legacy検出補強・ローリングデプロイ対象外理由・洗い出し明記（頭の3裁定を反映）
- **第3版（本版）**: 巡1レビュー（`rev595r1`・[Must]27件）を反映。§1 再洗い出し（再現スクリプト化）、
  §2 ロック実体同一性（symlink正規化・sidecar削除耐性）、検出を「ID集合＋件数」の2系統から
  **内容identity（ID or 行hash）の multiset 差分1本**へ統合、§3 index契約を単一tokenizer化＋
  lock取得後の identity 再確認、§4 「古いworktree/中断セッション」をローリングデプロイ対象外から
  切り離し明示的リスク受容、§5 陰性試験を全8 writer対象へ拡張＋レビュー提案6変異を追加、
  §6/§7 を全面更新
- **2026-09-03 巡3裁定追記**: U+2028/U+2029/U+0085 の物理行分割をLFへ限定。
  AST検査は既知sinkだけを検出するadvisoryであり迂回可能と訂正し、blocking (e) は未充足と明記。

---

## 0. スコープ（issue #595 完成条件 round 0 から転記）

### ① 守る対象
`corrections.jsonl` へ正しく書かれたレコードが、別の writer の書き戻しによって、**気づかれないまま**失われること。

### ② 信頼境界
脅威に数えるのは**自分たちの運用ミスのみ**（作業の中断・並行セッション・移行スクリプトの流し忘れ・手編集）。悪意ある改変・意図的なデータ破壊は数えない。

### ③ 対象外
- 柱2の測定そのもの（#587。本 issue が終わってから戻る）
- `results_board.py` への表示配線
- 新しい保存先ファイルの新設（#379 新設凍結は継続。既存 `corrections.jsonl` の中で完結させる。**`.lock` sidecar はデータの新保存先ではなく同期artifactであり本条項の対象外** — 巡1レビュー[Nit]を反映）
- `corrections.jsonl` 以外のストアの writer 協調
- **デプロイ機構としてのローリングデプロイ**（新旧バイナリを意図的に並行運用する仕組み）。この環境はローカル単一利用者運用でそのような仕組み自体が存在しないため対象外とする。**ただし「古い worktree や中断済みセッションが本 issue 適用前のコードを実行する」ことは別の経路であり対象外にしない**（§4.2 で扱う。巡1レビュー Q7 [Must] を反映し前版の記述を訂正）
- 可用性（`file_lock` の無期限待機によるハング）・electric/filesystem durability（`fsync` 欠如による電源断耐性）。いずれも「レコード消失」ではなく別カテゴリの障害のため対象外（巡1レビュー Q7 [Should]。§6 に残存リスクとして明記）

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
- `file_lock.py` が既にあるので、ロック機構を新規に発明しない（**§2.2 は既存 `file_lock()` 関数自体の既知の競合を閉じる修正であり、新しいロック*プリミティブ*の追加ではない**。全 `file_lock` 利用者に恩恵があり後方互換）
- 既存の契約を弱めない（dry-run 純度・人間承認・write barrier・データ契約）

---

## 1. 現状の棚卸し（自分で実測・再現スクリプト化）

### 1.1 巡1レビュー指摘の是正

巡1レビューで以下2点の [Must] を受けた:
1. 第1段階のヒット数が前版記載の219件でなく**218件**だった（再実行で確認・前版の記載ミス）
2. 第3段階（71ファイルへの書込みシグナル grep）が「複数patternをORで当てた」としか書いておらず**実行できるコマンドが無かった**

両方とも本版で是正する。洗い出し全体を**単一の再現可能な Python スクリプト**に書き直し、スクリプト全文をここに埋め込む。

### 1.2 再現スクリプト全文

```python
#!/usr/bin/env python3
"""#595 corrections.jsonl 書込み経路の機械的洗い出し（再現用スクリプト全文）。
実行: python3 sweep_595.py <repo_root>
"""
import re
import sys
from pathlib import Path

WRITE_SIGNALS = re.compile(
    r"write_text\(|write_bytes\(|os\.replace\(|\.replace\(tmp|atomic_write_text\(|"
    r"append_jsonl\(|append_correction_record\(|store_write\(|store_write_raw\(|"
    r'open\([^)]*["\']w|open\([^)]*["\']a|fdopen\([^)]*["\']w|'
    r"shutil\.move\(|shutil\.copy2?\(|Path\.open\(|\.open\([^)]*mode\s*="
)

REF_SIGNALS = re.compile(
    r"corrections\.jsonl|CORRECTIONS_FILE|CORRECTIONS_PATH|corrections_file|corrections_path|"
    r'"corrections"\s*,|corrections_file\s*=|store_name\s*==\s*"corrections'
)


def main(root: str) -> None:
    root_path = Path(root)
    targets = [root_path / d for d in ("scripts", "hooks", "skills", "bin")]
    py_files = []
    for base in targets:
        if base.exists():
            py_files.extend(
                p for p in base.rglob("*.py")
                if "/tests/" not in str(p) and "test_" not in p.name
            )

    ref_files = []
    for p in py_files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if REF_SIGNALS.search(text):
            ref_files.append(p)

    write_hits = []
    for p in ref_files:
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if WRITE_SIGNALS.search(line):
                write_hits.append((str(p.relative_to(root_path)), i, line.strip()))

    print(f"REF_FILES={len(ref_files)}")
    print(f"WRITE_SIGNAL_LINES={len(write_hits)}")
    for path, lineno, line in write_hits:
        print(f"{path}:{lineno}: {line}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
```

**実行結果**（取得時刻 2026-09-01T05:10:00Z UTC／対象 SHA `2955865c65e44dac41c85a4542cbe860640de7bb`／コマンド `python3 sweep_595.py /Users/matsukaze-takashi/wt/ea-595`）:

- `REF_FILES=75`（前版の71より広い。パターンに `store_write(` / `store_write_raw(` / `write_bytes(` / `shutil.move` / `shutil.copy2` / `Path.open` / `open(..., mode=...)` を追加したため増加。巡1レビュー Q2 [Must] の穴の指摘を反映）
- `WRITE_SIGNAL_LINES=64`

単純な文字列参照カウント（第1段階、前版の再検証）:
```
grep -rn "corrections\.jsonl" --include="*.py" scripts hooks skills bin \
  | grep -v "/tests/" | grep -v "test_" | wc -l
```
→ **218 件**（前版の219件は記載ミス。本コマンドをそのまま実行し直して確認済み）。

### 1.3 64件の書込みシグナルを1件ずつ判定した結果

64行のうち、実際に `corrections.jsonl` を対象とする書込みは以下の**12件**（rewrite 8 + append 4）のみ。残り52件は個別に対象ファイルを確認し、いずれも別ファイル（バックアップファイル・reportファイル・別ストア・docstring内の言及）であることを確認した。**確認できなかったものは無い**（全64行を Read または grep で対象パスまで追跡した）。

**曖昧だった候補とその判定根拠**（巡1レビュー Q2 [Must] 「`store_write`/`store_write_raw` は任意の登録storeへappend可能で、現在のcallerが`corrections.jsonl`を渡していないことは手作業confirmに依存する」への対応）:

| 候補 | 疑った理由 | 確認方法 | 結果 |
|---|---|---|---|
| `auto_memory_broker.py:518` `_store_write(_TRANSITION_STORE_NAME, record)` | store_write は任意store名を受ける | `grep -n "TRANSITION_STORE_NAME\s*=" memory_guard.py` | `"memory_transition_checks.jsonl"`（別ストア） |
| `correction_semantic/daily_review.py:177,179` `store_write(SEEN_STORE_NAME, rec)` / `store_write_raw(store, rec)` | 同上 | `grep -n "SEEN_STORE_NAME\s*=\|store\s*=" daily_review.py` | `SEEN_STORE_NAME = "correction_review_seen.jsonl"`（別ストア） |
| `auto_memory_purge.py:30-32` | docstringが「全PJ共有ストア（corrections.jsonl）」に言及 | `grep -n "^def \|DATA_DIR" auto_memory_purge.py` | 対象は `DATA_DIR/auto_memory_queue/<slug>.jsonl`（別ファイル。docstringは経緯説明であり書込み先ではない） |
| `store_write`/`store_write_raw` の**全呼出し元**（repo横断） | 動的に `store_name` へ `"corrections.jsonl"` が渡る経路が無いか | `grep -rn "store_write(\|store_write_raw(" scripts hooks skills` （全27箇所を列挙し個々の store 定数を確認） | 全て `sessions.jsonl` / `usage.jsonl` / `usage-registry.jsonl` / `errors.jsonl` / `subagents.jsonl` / `workflows.jsonl` / `false_positives.jsonl` / `correction_review_seen.jsonl` / `memory_transition_checks.jsonl` / idiom・judged・verdicts・queue_state 等の別ストア。`corrections.jsonl` を渡す呼出しは0件 |
| `bench/measure_467_proposal_kinds.py:978` `out_path.write_text` | ファイル名に `corrections` を含む変数が近くにある | 前後を Read | `out_path = Path(args.output)`（CLI引数のreportファイル。読取専用ツール） |
| `bench/golden_extractor.py:129` | 同上 | 前後を Read | `output`（CLI引数の抽出結果ファイル） |
| `migrate_correction_id_backfill.py:58` `shutil.copy2(filepath, backup_path)` | corrections.jsonl のバックアップ | 前後を Read | バックアップ**コピー**（別ファイル名へ複製するだけで `corrections.jsonl` 自体は書き換えない。本設計の対象は corrections.jsonl 自体への書込みなので対象外） |

この表と§1.2の再現スクリプトは既知の書込シグナルを再確認するためのadvisoryな根拠に留まる。
sink種別を列挙するdenylist型で迂回可能なため、issue完成条件(e)「洗い出しの機械的裏付け」は
**現状未充足**である。

### 1.4 8件の rewrite writer と issue 本文の6件との差分（変更なし・再確認済み）

| # | writer | file:line | 現状の書き方 | issue本文の棚卸し表 |
|---|---|---|---|---|
| 1 | `update_reflect_status` | `skills/reflect/scripts/reflect.py:631-745`（書込み`:735`） | 全読取→`filepath.write_text` | ○ 記載あり |
| 2 | `invalidate_idiom_corrections` | `scripts/lib/correction_semantic/promote.py:584-649`（書込み`:636-642`） | ロックなし・tmp+`os.replace` | ○ 記載あり |
| 3 | `cleanup_corrections` | `scripts/lib/prune/corrections.py:51-117`（書込み`:111-115`） | 全読取→`write_text` | ○ 記載あり |
| 4 | `migrate`（reflect_confirmed→promoted） | `scripts/migrate_reflect_promoted_status.py:51-80`（書込み`:78`） | 全読取→`write_text` | ○ 記載あり |
| 5 | `invalidate_subagent_contaminated_corrections` | `scripts/lib/corrections_subagent_invalidation.py:59-113`（書込み`:106`） | 全読取→`atomic_write_text` | ○ 記載あり |
| 6 | `migrate`（correction_id backfill） | `scripts/migrate_correction_id_backfill.py:64-190`（書込み`:139-166`） | 全読取→identity確認→tmp+`os.replace` | ○ 記載あり |
| 7 | `backfill_corrections`（turn_index付与） | `scripts/lib/backfill_turn_indices.py:202-256`（`_atomic_write`実体`:65-72`） | 全読取→tmp+`Path.replace` | **× issue本文の棚卸し表に記載無し。設計文書で新規発見（巡1レビューが独立再走査し追加無しと確認）** |
| 8 | `_backfill_jsonl`（pj_slug正規化・`corrections`分） | `scripts/lib/pj_slug_backfill.py:76-111`（`_atomic_write`実体`:60-73`） | 全読取→tmp+`os.replace` | **× 同上** |

**既知8件の根拠と限界**: §1.2のスクリプトは列挙した書込シグナルをOR検索し、64件を
人手で追跡した。これは現HEADの既知8件を確認する棚卸しであり、9件目が存在しないことの証明ではない。
特に以下は確認範囲外である（§6・§7にも明記）:
- shell/CLI経由の間接呼出し（`.py` 以外の呼出し）
- 動的 `getattr` / 文字列結合によるモジュール参照
- `*.py` の実行時にのみ組み立てられる Path（例: f-string で `"correc" + "tions.jsonl"` のような難読化）は未探索

---

## 2. 移行後の書き込み契約

### 2.1 方式の選択（変更なし）: 共有ロック下の read-modify-write

issue タイトルは「追記へ統一する」だが、round 0 の blocking (a)〜(e) は「全文書き換えを完全に禁止し追記イベントだけにせよ」とは要求していない。真の追記オンリー化（イベント fold）は #587 の対象外③に明記された正典であり、本設計はそれを先取りしない。よって引き続き **choice B: 全 writer を単一の共有ロックで直列化し、全文書き換え writer はロックを保持したまま read → mutate → atomic replace する** を採用する。

巡1レビューが明らかにしたのは、この選択自体の誤りではなく、**「同じロック関数を呼ぶ」だけでは実体の同一性（同じ inode）を保証しない**という実装上の穴だった。本版はその穴を塞ぐ。

### 2.2 ロック実体の同一性を保証する（blocking a・c、巡1レビューQ1・Q3）

**穴1: symlink別名**。前版の `_corrections_lock_path` は入力パスをそのまま `.lock` へ変換していたため、同じ実ファイルを指す2つの異なるパス（symlinkとその実体、あるいは相対パスの違い）が**別々のsidecarロック**を生む。対策: パスを実体へ正規化してからロックパスを作る。

```python
# scripts/lib/rl_common/correction_id.py（修正）
def _corrections_lock_path(filepath: Path) -> Path:
    """corrections.jsonl の sidecar ロックパス。symlink・相対パスを実体へ正規化してから
    導出することで、別名パスからの書込みが別sidecarを取得する事故を防ぐ（巡1レビュー[Must]）。
    resolve(strict=False) はファイル不在でも例外を投げない（初回書込み時にまだ
    corrections.jsonl が存在しないケースに対応）。
    """
    resolved = Path(filepath).resolve()
    return resolved.with_name(resolved.name + ".lock")
```

**穴2: 稼働中の sidecar 削除**。`.lock` を「不要な一時ファイル」と誤認して手で削除すると、削除直後に別プロセスが同名パスで新規 `.lock` を作り、既存の lock 保持者とは**別 inode** の排他ロックを取ってしまう（flock は inode 単位のため、path が同じでも inode が違えば別ロック）。既存 `file_lock.py:file_lock()` はこの再検証をしていない。

対策: `file_lock()` 自体に、**ロック取得直後、自分が開いた fd の inode が現在のディレクトリエントリと一致するかを再確認し、不一致ならリトライする**標準的な緩和策を追加する。これは新しいロック**プリミティブ**ではなく、既存関数の既知の競合を閉じる修正であり、全 `file_lock` 利用者（`optimize_history_store.py` 等）に恩恵があり後方互換（正常系では常に一致するため挙動は変わらない）。

```python
# scripts/lib/rl_common/file_lock.py（file_lock() の修正）
@contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        fh = open(lock_path, "a", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                # lock取得後、開いたfdの実体が「今のディレクトリエントリ」と同じ inode かを
                # 再確認する。稼働中に他プロセスが同名sidecarをunlink→recreateしていた場合、
                # 自分のfdは既に「誰も参照していない古い実体」を指しており、後続の
                # openerは別inodeで別ロックを取得できてしまう（協調が壊れる）。
                # 不一致なら開き直してリトライする（標準的な delete-recreate race 対策）。
                try:
                    cur = os.stat(lock_path)
                except OSError:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    continue
                held = os.fstat(fh.fileno())
                if (cur.st_dev, cur.st_ino) != (held.st_dev, held.st_ino):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    continue
                yield
                return
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
```

（`import os` を file_lock.py 冒頭に追加する。）

**この修正で完全に閉じない残存窓**: 上記の再検証は**取得時点**の一致確認であり、**ロック保持中（`yield` 区間の最中）に外部が sidecar を unlink し、別プロセスが新規に flock を取得してしまう**ケースまでは防げない（保持中は自分のfdを閉じないため再検証のしようがない）。これは「稼働中の`.lock`を手で消す」という運用ミスが**臨界区間の最中**というピンポイントなタイミングで起きた場合にのみ残る窓であり、取得時点の再検証により**窓は大幅に縮小されるが、ゼロにはならない**。§6に残存リスクとして明記し、運用上の対策（`.lock` ファイルを手動削除しない）をドキュメント化する。

### 2.3 `corrections_write_lock`（新しいロック機構ではなく既存 `file_lock.file_lock` の適用）

```python
# scripts/lib/rl_common/correction_id.py（追加）
def fcntl_unsupported_reason() -> Optional[str]:
    """fcntl 非対応環境かどうかを判定する。corrections_write_lock を呼ぶ前に
    rewrite writer 側が確認する（巡1レビュー[Should]: file_lock は module-level で
    `import fcntl` するため、fcntl 非対応環境では import 自体が失敗する。
    append_correction_record は persistence._HAVE_FCNTL を早期チェックして
    file_lock を import する前に unsupported_platform を返すのと同じパターンを、
    全 rewrite writer にも適用する）。
    """
    return None if persistence._HAVE_FCNTL else (
        "fcntl unavailable: corrections.jsonl の排他書込みは未対応"
    )


def corrections_write_lock(filepath: Path):
    """corrections.jsonl への read-modify-write を全 writer 間で直列化する共有ロック区間。

    file_lock は module-level で fcntl を import するため、呼出し側
    （append_correction_record・各 rewrite writer）が fcntl_unsupported_reason() で
    事前チェック済みであることを前提に、ここで初めて import する（遅延import）。
    """
    from .file_lock import file_lock  # 遅延import（fcntl非対応環境でのimport失敗を避ける）
    return file_lock(_corrections_lock_path(Path(filepath)))
```

`corrections_write_lock` はコンテキストマネージャを**返す**関数にした（`@contextmanager` を直接付けると、デコレータ適用時に関数本体が定義されるだけで実行はされないため遅延importの意味は保てるが、`file_lock` 自体が contextmanager なので二重にラップするより「取得して返す」形の方が素直なため）。呼出し側は `with corrections_write_lock(path):` とそのまま書ける。

### 2.4 `append_correction_record` の変更（変更なし・§2.3の新関数を使うだけ）

```python
# scripts/lib/rl_common/correction_id.py（既存関数の変更）
def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    if not persistence._HAVE_FCNTL:
        return AppendResult(status="unsupported_platform", reason="fcntl unavailable: unique append is not supported")

    from .store_write import guard_problem
    problem = guard_problem("corrections.jsonl")
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    filepath = Path(filepath)
    with corrections_write_lock(filepath):
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

`persistence.append_jsonl` 自体は無変更（他ストアへの影響を避けるため）。二重ロック（sidecar → `append_jsonl` 内部のfdロック）は別ファイル同士のため自己deadlockには当たらない（変更なし）。

### 2.5 検出（blocking b）: ID or 行hash による内容identityの multiset 差分（2系統から1系統へ統合）

**前版からの変更点**: 前版は「ID集合差分」と「件数突合」の2つの独立検査だったが、巡1レビューQ1(b)・Q3「legacy行の同数置換」が、この2つを**同時に**満たす反例（`correction_id`を持たない legacy 行Aを、別のlegacy行Bへ内容ごと置き換える。件数は変わらず、IDが無いのでID集合も無反応）を示した。本版はこれを1つの**内容identity**へ統合し解消する。

**設計**: 各行の identity を「`correction_id` があればそれ、無ければ**行全体のsha256ハッシュ**」と定義する。legacy行でも**内容が変われば別identityになる**ため、同数置換（Aが消えてBに変わる）は「Aのidentityが消えた」として検出できる。

```python
# scripts/lib/rl_common/correction_id.py（追加。json/hashlib/Counterのimportが必要）
import hashlib
import json
from collections import Counter


def _line_identity(raw_line: str) -> str:
    """1行の identity key。correction_id があればそれ、無ければ生の行文字列全体のsha256。

    legacy レコード（correction_id 無し）も「内容が変わったら別 identity になる」ことで
    消失検出の対象に含める（巡1レビューQ3「legacy 行の同数置換」への対応）。
    """
    stripped = raw_line.strip()
    try:
        rec = json.loads(stripped)
    except json.JSONDecodeError:
        rec = None
    if isinstance(rec, dict) and validate_correction_id(rec.get("correction_id")):
        return f"id:{rec['correction_id']}"
    return f"hash:{hashlib.sha256(stripped.encode('utf-8')).hexdigest()}"


def snapshot_identities(text: str) -> "Counter[str]":
    """corrections.jsonl テキストの行ごと identity を多重集合として返す（空行は数えない）。"""
    counter: "Counter[str]" = Counter()
    for line in split_corrections_lines(text):
        if line.strip():
            counter[_line_identity(line)] += 1
    return counter


class UnexpectedCorrectionLossError(RuntimeError):
    """touched として宣言されていない行の identity が、書換え前後で失われたときに送出する。"""


def assert_no_unexpected_content_loss(
    before: "Counter[str]",
    after: "Counter[str]",
    *,
    touched_before: "Counter[str]" = Counter(),
) -> None:
    """touched_before（このwriterが意図的に変更・削除すると宣言した行の identity 多重集合）を
    差し引いた上で、それ以外の行が書換え後も全部残っているかを確認する。

    意図的な変更（フィールド更新）は identity が変わる（hashが変わる／新規IDを持つ）ため、
    変更後の新しい identity を検証対象にする必要は無い。検証するのは
    「touched と宣言していない行が、書換え後も同じ multiset で残っているか」だけ。
    """
    expected_untouched = before - touched_before
    missing = expected_untouched - after
    if missing:
        raise UnexpectedCorrectionLossError(
            f"corrections.jsonl 書換えで {sum(missing.values())} 件の行が"
            f"意図せず消失（touched 宣言に無い identity）: {list(missing.elements())[:5]}"
        )
```

**touched_before の作り方**: 各 writer が「今回変更・削除すると実際にマッチした」行（raw_line）から機械的に算出する（見積もりではなく、実際にマッチしたレコードそのものから作る）。§2.6 で writer ごとの取得元を明記する。

**この設計が閉じる巡1レビューの指摘**:
- Q1(b)「同数入替が両検査を通る」→ 内容が変わればidentityが変わるため通らない
- Q3「legacy行の同数置換」→ 同上（§6に検出できない残りのケースを明記）

### 2.6 全文書き換え writer 8件の変更（実コードに即して writer ごとに記述）

**前版の反省**: 前版は「共通の擬似コードに全writerを当てはめる」形で書き、実際には存在しない共通の戻り値タプルを前提にしていた（巡1レビュー[Must]「`_existing_transform` が返す前提の `intended_removed_ids` 等は実コードに無い」）。本版は8件それぞれについて、**実際の関数のRead結果に基づいて**変更内容を個別に記述する。

**共通の骨格**（全writer共通の3手順。中身は個別）:
1. `fcntl_unsupported_reason()` を確認し、非対応なら早期return（writer固有の失敗表現で）
2. `with corrections_write_lock(path):` の中で、`text = path.read_text(...)` → `before = snapshot_identities(text)` → 既存のマッチング・変換ロジック（変更なし）→ `touched_before = snapshot_identities("\n".join(touched_raw_lines))` を実際にマッチした行から構築 → `new_content` 組立 → `after = snapshot_identities(new_content)` → `assert_no_unexpected_content_loss(before, after, touched_before=touched_before)` → `atomic_write_text_preserving_mode(path, new_content)`（§2.7）
3. **dry-run はロックを取らない**（§2.8）

#### #1 `update_reflect_status`（`skills/reflect/scripts/reflect.py:631-745`）

現状（実装済み・変更しない部分）: `filepath.read_text()` → 行ごとに `json.loads` を試み、`record_idx in index_set` なら `record["reflect_status"] = status` として `json.dumps` し直す → `filepath.write_text(...)`。

変更点:
- 関数全体を `with corrections_write_lock(filepath):` で包む
- `filepath.write_text(...)` を `atomic_write_text_preserving_mode(filepath, ...)` に置換
- `touched_before`: `record_idx in index_set` でマッチした**元の行文字列**（`json.dumps` する前の生行）を集めて構築
- **index契約そのものの変更は §3 参照**（本節はロック・検出のみ）

#### #2 `invalidate_idiom_corrections`（`scripts/lib/correction_semantic/promote.py:584-649`）

現状: `open(corrections_path, "r", ...)` で1行ずつ読み `promoted_by=="idiom_dict" and idiom_key in target and not invalidated` を判定、マッチ行は `r["invalidated"]=True` 等を設定して `recs` に積む → 全件を `json.dumps` して `os.fdopen(tmp_fd, "w")` + `os.replace`。

変更点:
- 関数全体を `with corrections_write_lock(corrections_path):` で包む
- `touched_before`: `matched` をインクリメントした際に、**変更前の生行**（`json.loads` する前の `line`）を別リストに集めて構築
- 既存の tmp+`os.replace` を `atomic_write_text_preserving_mode` に統一（現状は自前でtmp+replaceしているが、mode保存の一貫性のため §2.7 のヘルパーへ寄せる）

#### #3 `cleanup_corrections`（`scripts/lib/prune/corrections.py:51-117`）

現状: 行ごとに `reflect_status` と `decay_days` 超過を判定し、超過なら `removed += 1`（**行を`kept_lines`に積まない＝破棄**）、そうでなければ `kept_lines.append(line)`。最後に `corrections_file.write_text("\n".join(kept_lines) + ...)`（`dry_run` なら書かない）。

変更点:
- 関数全体を `with corrections_write_lock(corrections_file):` で包む（`dry_run` 時はロックを取らない。§2.8）
- **`removed_lines: List[str] = []` を新設**し、破棄する行で `kept_lines.append` せず捨てている箇所で `removed_lines.append(line)` も行う（既存の `removed` カウンタの隣に実データを持たせる。前版が「既存戻り値をそのまま使う」としたのは誤りで、この収集ロジック自体が新規追加になることを明記する）
- `touched_before = snapshot_identities("\n".join(removed_lines))`
- `write_text` を `atomic_write_text_preserving_mode` に置換

#### #4 `migrate`（reflect_confirmed→promoted・`scripts/migrate_reflect_promoted_status.py:51-80`）

現状: `_load_jsonl` で全レコードを読み、`is_migration_target` に一致する `r` の `reflect_status` を書き換え、全件を `json.dumps` して `corrections_file.write_text(...)`。

変更点:
- 関数全体を `with corrections_write_lock(corrections_file):` で包む
- **`_load_jsonl` を、生の行文字列も保持するバージョン（またはテキストを直接 `snapshot_identities` に渡す）に変更**し、`is_migration_target(r)` にマッチした**元の行**を `touched_before` の元データにする
- `write_text` を `atomic_write_text_preserving_mode` に置換

#### #5 `invalidate_subagent_contaminated_corrections`（`scripts/lib/corrections_subagent_invalidation.py:59-113`）

現状: 行ごとに `_is_subagent_contaminated_candidate` を判定し、マッチ行は `rec["invalidated"]=True` 等を設定、`lines`（rawまたはdict混在リスト）に積む → `body` を組み立てて `atomic_write_text(corrections_file, body)`。

変更点:
- 関数全体を `with corrections_write_lock(corrections_file):` で包む
- マッチした時点の `raw_line`（`stripped` になる前の元の行）を別途 `touched_before` 用に集める
- `atomic_write_text` の呼出しを `atomic_write_text_preserving_mode` に置換

#### #6 `migrate`（correction_id backfill・`scripts/migrate_correction_id_backfill.py:64-190`）

現状: 既に identity 確認（`_identity_of` によるinode/size/mtime_ns/sha256の再確認）を持つ（#593実装）。

変更点:
- 読取〜identity確認〜replace の全体を `with corrections_write_lock(filepath):` で包む
- **既存の identity 確認はそのまま維持する**（defense-in-depth。ロック協調により「窓」は構造的に閉じるが、二重の安全弁として残す。削除しない）
- `touched_before`: `newly_assigned` された行（`correction_id` の無かった元の行）から構築
- `os.replace` を維持しつつ、書換え直前に `assert_no_unexpected_content_loss` を呼ぶ

#### #7 `backfill_corrections`（turn_index付与・`scripts/lib/backfill_turn_indices.py:202-256`）

現状: 各行を `json.loads` し `turn_index` を付与、`records` に `json.dumps` し直した行を積む → `dry_run` でなく `added > 0` なら `_atomic_write(corrections_path, ...)`。

変更点:
- 関数全体を `with corrections_write_lock(corrections_path):` で包む
- `turn_idx is not None` で実際に書き換えた行の**元の生行**を `touched_before` 用に集める
- `_atomic_write` を `atomic_write_text_preserving_mode` に統一

#### #8 `_backfill_jsonl`（pj_slug正規化・`scripts/lib/pj_slug_backfill.py:76-111`）

現状: `field` の値を `_normalize` し、変わったレコードだけ `rec[field] = new` して `normalized += 1`、`apply and normalized` なら全件を `json.dumps` して `_atomic_write`。

変更点（§2.9 で詳述する `corrections.jsonl` 特別扱いと合わせて）:
- **呼出し元 `backfill()` 側**で `filename == "corrections.jsonl"` のときだけ `with corrections_write_lock(path):` を追加（他6ストアは対象外＝完成条件③）
- `_backfill_jsonl` 内部で、`new != raw` により変更した**元のレコードのjson.dumps文字列**（変更前の行）を `touched_before` 用に集める。これは `corrections.jsonl` 以外の呼出しでも共通のコード経路になるが、`touched_before` は呼出し元が使わなければ無害（他ストアは `assert_no_unexpected_content_loss` を呼ばない）

### 2.7 atomic replace 時の permission 保存（巡1レビュー[Should]）

既存 `atomic_write_text`（`file_lock.py`）は temp ファイルの mode を既存ファイルから引き継がない。`append_jsonl` は新規ファイルを明示的に0600にするのに対し、rewrite writer が0600のファイルをreplaceすると、tempファイルは `tempfile`/`mkstemp` のデフォルトmode（通常0600、環境のumask依存）になり、既存の意図的な0600契約が暗黙に壊れうる。

対策: `correction_id.py` に corrections.jsonl 専用の薄いラッパーを追加し、既存ファイルのmodeを保存してからreplaceする（`file_lock.py` の `atomic_write_text` 自体は変更しない＝他の消費者への影響を避ける）。

```python
# scripts/lib/rl_common/correction_id.py（追加）
def atomic_write_text_preserving_mode(path: Path, text: str) -> None:
    """atomic_write_text の corrections.jsonl 専用ラッパー。既存ファイルの mode
    （append_jsonl が新規作成時に設定する0600）を保存してから置換する。
    """
    from .file_lock import atomic_write_text
    import stat as _stat
    existing_mode = None
    try:
        existing_mode = _stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass
    atomic_write_text(path, text)
    if existing_mode is not None:
        path.chmod(existing_mode)
```

（`migrate_correction_id_backfill.py` は既に同種のmode保存を自前で行っている＝#6は変更不要。#1〜#5,#7,#8はこのラッパーへ統一する。）

### 2.8 dry-run 純度（blocking外だが既存契約。巡1レビュー[Must]）

**前版の誤り**: 「lockを取っても一切書かない」と記述したが、`file_lock()` は sidecar ファイルを `open(..., "a")` で不在なら**作成する**ため、dry-run でロックを取るだけで副作用（sidecarファイルの新規作成）が生じ、既存の「dry-runは1バイトも書かない」契約（`invalidate_idiom_corrections` の dry-run、`cleanup_corrections` の dry-run）を壊す。

対策: **dry-run 判定はロック取得の外側で行い、dry-run なら `corrections_write_lock` を呼ばない**。読取（read-only）はロック無しで行う（既存の atomic replace パターンにより、他writerの書込み最中でも読取は「完全に古い内容」か「完全に新しい内容」のどちらかしか見えない＝torn readは起きない。今回のロック導入は writer 同士の直列化のためのものであり、reader保護のためではないので、読取専用のdry-runがロックを取る必要は元々無い）。

```python
def cleanup_corrections(dry_run: bool = False) -> Dict[str, int]:
    ...
    if not corrections_file.exists():
        return {"removed": 0, "kept": 0}
    if dry_run:
        # 既存契約どおりロックを取らず読取のみ（file_lockのsidecar作成副作用を避ける）
        text = corrections_file.read_text(encoding="utf-8")
        return _compute_without_writing(text)  # 既存の計算ロジックをそのまま使う
    with corrections_write_lock(corrections_file):
        text = corrections_file.read_text(encoding="utf-8")
        ...（書込みまで一貫してロック内）
```

8件それぞれについて、dry-run分岐が「ロックを取らない」側に来るよう §2.6 の記述を読み替える（`update_reflect_status` と `migrate_correction_id_backfill` は dry-run 概念が既存コードに元々あるので同様に処理、それ以外は元々 dry-run 引数を持たない一括実行系なので該当なし）。

### 2.9 `pj_slug_backfill.backfill()` の corrections だけを特別扱いする実装（変更なし）

```python
# scripts/lib/pj_slug_backfill.py（backfill() の変更）
def backfill(data_dir: Path, *, apply: bool = False) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    result: Dict[str, Any] = {"applied": apply, "data_dir": str(data_dir)}
    for key, filename, field in _JSONL_STORES:
        path = data_dir / filename
        if filename == "corrections.jsonl":
            from rl_common.correction_id import corrections_write_lock, fcntl_unsupported_reason
            reason = fcntl_unsupported_reason()
            if reason is not None:
                result[key] = {"normalized": 0, "total": 0, "error": reason}
                continue
            with corrections_write_lock(path):
                result[key] = _backfill_jsonl(path, field, apply=apply)
        else:
            result[key] = _backfill_jsonl(path, field, apply=apply)
    result["sessions_db"] = _backfill_sessions_db(data_dir / "sessions.db", apply=apply)
    return result
```

---

## 3. index 契約の整合（blocking d、巡1レビュー Q5 全面反映）

### 3.1 前版の誤り

前版は「共有enumerateヘルパーで `(index, dict)` を返す」設計だったが、巡1レビューが2点の構造的欠陥を指摘した:

1. **物理行情報が無い**: `(index, dict)` だけでは、書換え側が「どの物理行を書き換えるか」を別の走査で再対応付けする必要があり、#588 の原因だった「2つの独立した走査」を実質的に残していた
2. **read-modify-write の間に別レコードが削除されると、indexが有効なまま別レコードを指す**: CLIが `--apply`/`--skip`/`--skip-all` でスナップショットからindexを決定し（`reflect.py:1274,1366,1450`）、後から別のロック区間（`update_reflect_status` 呼出し時）でそのindexを適用する構造そのものが、「indexが指す対象が変わる」余地を残す。これは index計算ロジックの共有だけでは解決できない

### 3.2 対策: 単一tokenizer + lock取得後のidentity再確認

**a) 単一tokenizer**（巡1レビュー提案を採用）: `(record_index, physical_line_index, record, raw_line)` を1関数で返す。

```python
# scripts/lib/rl_common/persistence.py（追加）
from dataclasses import dataclass
from typing import Iterator


@dataclass
class IndexedLine:
    record_index: int          # 有効レコードとしての0始まり通し番号
    physical_line_index: int   # ファイル内の0始まり物理行番号（壊れた行・空行を含む）
    record: object              # json.loads の結果（dict とは限らない。load_corrections の
                                 # 現行契約＝「型を問わず追加」に合わせる。呼出側でdict確認する）
    raw_line: str                # 元の行文字列（書き戻し用にそのまま保持）


def iter_indexed_lines(text: str) -> Iterator[IndexedLine]:
    """JSONL テキストを、JSON decode に成功した行だけ
    (record_index, physical_line_index, record, raw_line) として yield する。

    #588: load_corrections と update_reflect_status が同じ「規約」のつもりで独立実装し
    ずれた反省を踏まえ、規約でなく関数そのものを共有する。
    #595 巡1レビュー: index計算だけでなく raw_line と物理行番号も同時に返すことで、
    呼出側が別の走査で対応付け直す必要を無くす（構造的な単一化）。

    現行 load_corrections の契約（json.loads成功なら型を問わず追加）に合わせ、
    record が dict であることはここでは強制しない（呼出側が isinstance チェックする）。
    """
    record_index = 0
    for physical_line_index, line in enumerate(split_corrections_lines(text)):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        yield IndexedLine(record_index, physical_line_index, record, line)
        record_index += 1
```

`reflect.py` の `load_corrections` はこのヘルパーで `record` だけ集める形に置き換える。`update_reflect_status` もこのヘルパーで走査し、`raw_line` をそのまま保持して書き戻す（生の行文字列を独自に再構築するロジックを削除）。

**b) lock取得後のidentity再確認**（巡1レビュー[Must]の核心）: CLI（`--apply`/`--skip`/`--skip-all`）は、スナップショット読取時に**index だけでなく、対象レコードの identity（`correction_id` があればそれ、無ければ `(session_id, timestamp)` のペア）も一緒に確定**し、両方を `update_reflect_status` へ渡す。`update_reflect_status` はロック取得後に**再度ファイルを読み、渡された index の位置にある現在のレコードの identity が、CLIが確定した identity と一致するかを確認してから初めて更新する**。不一致なら「対象が入れ替わっている」ことを検出し、`identity_mismatch` として拒否する（黙って別レコードを更新しない）。

```python
# scripts/lib/rl_common/persistence.py（追加）
def record_identity(record: dict) -> tuple:
    """correction_id があればそれ、無ければ (session_id, timestamp) を identity とする。
    #587 が導入する不変IDより弱いが、#595 のスコープ内（read-modify-writeの再確認）で
    実現できる最善の手段（correction_id backfill 未実施の legacy レコードも対象にする）。
    """
    cid = record.get("correction_id")
    if isinstance(cid, str) and cid:
        return ("id", cid)
    return ("legacy", record.get("session_id", ""), record.get("timestamp", ""))
```

```python
# skills/reflect/scripts/reflect.py（update_reflect_status の契約変更）
@dataclass
class UpdateTarget:
    index: int
    expected_identity: tuple  # record_identity() の戻り値


def update_reflect_status(
    filepath: Path,
    targets: list["UpdateTarget"],   # 変更: indices: list[int] から置き換え
    status: str,
    *, target_path: str | None = None, draft_line: str | None = None,
) -> dict:
    ...（applied確認は変更なし）...
    if not targets:
        return {"status": status, "target": target_path, "reason": None}

    with corrections_write_lock(filepath):
        if not filepath.exists():
            return {"status": "not_found", "target": target_path,
                    "reason": f"corrections ファイルが存在しません: {filepath}"}

        text = filepath.read_text(encoding="utf-8")
        by_index = {il.record_index: il for il in persistence.iter_indexed_lines(text)}

        target_by_index = {t.index: t for t in targets}
        updated_lines: list[str] = []
        matched_indices: set[int] = set()
        mismatched: list[int] = []
        touched_raw: list[str] = []
        record_idx = 0
        physical_lines = split_corrections_lines(text)
        for phys_idx, raw in enumerate(physical_lines):
            il = by_index.get(record_idx) if record_idx in by_index and by_index[record_idx].physical_line_index == phys_idx else None
            # ↑ iter_indexed_lines の出力を physical_line_index で突合する
            if il is None:
                updated_lines.append(raw)
                continue
            t = target_by_index.get(il.record_index)
            if t is None:
                updated_lines.append(raw)
                record_idx += 1
                continue
            if not isinstance(il.record, dict) or persistence.record_identity(il.record) != t.expected_identity:
                # index は範囲内だが指している中身が変わっている＝別レコード。適用しない。
                mismatched.append(il.record_index)
                updated_lines.append(raw)
                record_idx += 1
                continue
            rec = dict(il.record)
            rec["reflect_status"] = status
            updated_lines.append(json.dumps(rec, ensure_ascii=False))
            matched_indices.add(il.record_index)
            touched_raw.append(raw)
            record_idx += 1

        if mismatched:
            return {"status": "identity_mismatch", "target": target_path,
                    "reason": f"index は範囲内だが対象が入れ替わっています: {sorted(mismatched)}"}

        missing = set(target_by_index) - matched_indices
        if missing:
            return {"status": "not_found", "target": target_path,
                    "reason": f"指定インデックスに対応するレコードが見つかりません (index: {sorted(missing)})"}

        new_content = "\n".join(updated_lines) + "\n"
        before = correction_id.snapshot_identities(text)
        after = correction_id.snapshot_identities(new_content)
        touched_before = correction_id.snapshot_identities("\n".join(touched_raw))
        correction_id.assert_no_unexpected_content_loss(before, after, touched_before=touched_before)
        correction_id.atomic_write_text_preserving_mode(filepath, new_content)

    return {"status": status, "target": target_path, "reason": None}
```

**CLI側の変更**（`reflect.py:1274,1366,1450` 付近、`--apply`/`--skip`/`--skip-all`）: これまで `indices: list[int]` を渡していた箇所を、スナップショット読取時点で `record_identity(record)` を一緒に計算し `UpdateTarget(index, identity)` のリストへ変える。この変更は3箇所とも同型（スナップショットの各対象レコードから identity を計算するだけ）。

**この設計が閉じる巡1レビューの指摘**: 「index決定後・更新前にcleanupが先行レコードを削除し、別レコードを書き換える」シナリオ（Q3の5番目）は、更新時に再読込した対象の identity が CLI 確定時と一致しないため `identity_mismatch` として**明示的に拒否**される（黙って別レコードを書き換えない）。**これはイベントfoldや柱2測定ではなく、blocking (d) 自体の解消であり#595のスコープ内**（巡1レビュー自身がそう明記している）。

### 3.3 「index を使わない経路」との整合（変更なし）

他7 writer はフィールド述語でマッチするため index の解釈とは無関係。§2.5 の内容identity差分検出はすべての writer に共通で効く。

---

## 4. 移行手順

### 4.1 これはスキーマ移行ではない（変更なし）

既存の `corrections.jsonl` データに対する変換は不要。`.lock` sidecar は初回書込み時に自動生成される。

### 4.2 「古い worktree・中断済みセッションが旧コードを実行する」への対応（巡1レビュー Q7 [Must] を反映し全面書き直し）

**前版の誤り**: 前版はこれを「ローリングデプロイ中の新旧混在」として §0③の対象外に含めていたが、巡1レビューが指摘した通り、これは**別の経路**である。ローリングデプロイ（対象外として維持。§0③参照）は「デプロイ機構として意図的に新旧を並行運用する」ことを指すが、ここで問題になるのは**別セッションが本 issue 適用前に作られた worktree で、pre-#595 のコード（ロックを取らない全文書き換え）をそのまま実行する**ことであり、これは issue の信頼境界②「並行セッション・作業中断」そのものであり対象外にできない。

**扱い**: **受け入れる（技術的に完全な防止は不可能）+ 運用手順で確率を下げる**。

理由: Python はビルド・バージョンピン留めの仕組みを持たないインタプリタ言語であり、古い worktree のファイルシステム上のコピーがそのまま実行可能である以上、**新しいコードの内部にどんなロジックを追加しても、それを一度も経由しない旧コードの実行を止めることはできない**（新コード側の検出機構は「新コードが呼ばれること」が前提のため）。また、旧コードによる書換えが**既に完了してディスクに反映された後**に新コードの writer が書込む場合、新コードの `before` スナップショットは「既に旧コードによって壊された状態」を読むことになり、§2.5の内容identity差分検出も「これが本来あるべき状態からの差分か」を判定するための独立した基準（ground truth）を持たない（#379の新設凍結により、そのような基準を新しいストアとして持つことはできない）。

**運用手順による緩和**（コードでなくチェックリスト）:
1. #595 マージ後、コミット前に存在した `corrections.jsonl` 関連スクリプトを触る worktree は**再利用せず削除**する（`git worktree remove` または手動削除）
2. 中断済みセッションを再開する前に、当該 worktree が `#595` マージ後の `main` を取り込んでいるか（`git log --oneline -1 -- scripts/lib/rl_common/correction_id.py` 等で `corrections_write_lock` の存在を確認）を確認する
3. これらは `/evolve-anything:cleanup` の「マージ済みブランチ・stale worktree削除」フローで部分的に自動化されている（既存機能。新規実装は不要）

### 4.3 途中で中断した場合（変更なし・§2.2で追加した再検証ロジックの説明を補足）

- ロック取得前に中断: 影響なし
- ロック保持中に中断: `atomic_write_text_preserving_mode` は tmp+`os.replace` のため部分書込みは残らない。sidecarロックは advisory lock でプロセス終了時に自動解放される
- §2.2 の inode 再検証は、取得**時点**の一致を見るだけで、保持中の外部unlinkまでは防げない（§2.2に明記の残存窓）

---

## 5. 検証方法

**設計段階では未実行**。下記の陰性試験・陽性対照・呼出順アサーション試験は、この設計文書の作成時点では1件も実行していない。実装 PR の完了条件として、実装者が実際に走らせ、緑残りが無いことを報告する。

### 5.1 陰性試験（各 blocking に1件以上。巡1レビューの反例6件を吸収）

| ID | blocking | 変異内容 | 巡1レビュー指摘との対応 |
|---|---|---|---|
| N-a-1 | (a) | `update_reflect_status` の**read呼出し完了後・write呼出し前**という正確な同期点（`monkeypatch` で `Path.read_text` の戻り値を横取りするフックを噛ませ、そのコールバック内で別プロセス相当の追記処理を同期的に実行）で追記を挟む。旧実装（`corrections_write_lock`呼出しを外した変異）ではこの追記行が消える | Q6 [Must]「呼出し直前では旧実装でも緑になる」を修正。read完了後・write前へ確実に挟む |
| N-a-2〜N-a-8 | (a) | 8 writer**全て**について N-a-1 と同型の再現を行う（前版はupdate_reflect_statusとcleanup_correctionsの2件のみだった） | Q6 [Must]「残る6件でread/判定をlock外へ移す変異が緑のまま残る」を解消 |
| N-b-1 | (b) | `assert_no_unexpected_content_loss` の呼出しを削除する変異＋ID付きレコードを1件消すfixtureで、例外が飛ばないことを確認してから、削除しない実装で飛ぶことを確認 | 前版N-b-1の継続 |
| N-b-2 | (b) | `correction_id` を持たない legacy レコードを、**別の既存legacy行と同数入替**する変異（Q3「legacy行の同数置換」の直接再現）。内容identity（hash）ベースの検出により消失が検出されることを確認 | Q3 [Must] を解消（前版のN-b-2は件数突合だったが同数入替では効かなかった。本版のhashベース検出で解消） |
| N-c-1 | (c) | `append_correction_record` の `corrections_write_lock` 呼出しを外す変異でN-a-1を追記側無ロックで再現 | 前版継続 |
| N-c-2 | (c) | rewrite writer側の `corrections_write_lock` 呼出しを外す変異（逆方向） | 前版継続 |
| N-c-3 | (c) | rewrite 1件だけ lock path を `corrections.lock`（sidecarの命名規約を1件だけ変える）にする変異。「全writerがlock関数を呼んでいる」ことだけを見る検査では緑になるが、実inode不一致で消失が起きることを確認 | Q6 [Must]①を追加 |
| N-c-4 | (c) | canonical path と symlink alias から同時に書込む（symlink正規化を外す変異）。`_corrections_lock_path` の `resolve()` を外すと、別sidecarが取得され消失が起きることを確認 | Q6 [Must]③・Q1(a)symlink指摘を解消 |
| N-c-5 | (c) | lock保持中に sidecar を unlink し、別プロセスが同名pathで新規lockを取得できてしまう（§2.2で閉じ切れない残存窓の再現）。§2.2の再検証ロジックが**取得時点**では防げることと、**保持中unlink**では防げないことの両方を確認する（前者は緑、後者は既知の残存リスクとして赤のまま残ることを明示する陽性・陰性のペア） | Q6 [Must]④・Q3「稼働中sidecar削除」を解消（縮小はするが完全解消でないことも検証で示す） |
| N-d-1 | (d) | CLIのindex決定後・`update_reflect_status`のlock取得前に、別プロセス相当の処理で対象より前のレコードを削除する変異。identity再確認（§3.2b）がこれを`identity_mismatch`として拒否することを確認 | Q5 [Must]・Q6 [Must]⑤の核心を解消 |
| N-d-2 | (d) | `iter_indexed_lines` を使わず「空行もカウントする」独自実装に戻す変異（#588再現） | 前版N-d-1継続 |
| N-e-1 | (e) | §1.2のスクリプトから `atomic_write_text(` パターンを外す変異を実際に適用し、再実行結果の変化を報告する（前版は「#5・#8が消えて6件」と誤った予測をしていた。**予測でなく実際にスクリプトを改変して実行し、結果をそのまま報告する**） | Q6 [Must]「#8は`_atomic_write`内の`os.replace`なので`atomic_write_text`パターンだけでは落ちない」を解消 |
| N-e-2 | (e) | 既知sink種別（例: `store_write_raw` へ `"corrections.jsonl"` を直接渡す形）がadvisory AST検査で検出されることを確認する。helper・未列挙sink・動的pathの迂回は検出しない | blocking (e) の充足には使えない既知パターンの回帰確認 |

### 5.2 陽性対照（巡1レビュー[Should]反映）

- P-1: 通常の読取・通常の追記・通常の `cleanup_corrections`（decay対象なし）で全レコードが減らず意味も変わらないことを確認（§2.5の統合検出が誤検出しないこと）
- P-2: 削除・更新を**意図した**`touched_before`を正しく渡すケース（`cleanup_corrections`が実際にdecay対象を消す、`update_reflect_status`が実際にstatusを更新する）で例外が飛ばないことを確認
- P-3（新規・巡1レビュー[Should]）: dry-run前後で、ディレクトリ一覧（`.lock`sidecarの有無を含む）・`corrections.jsonl`のmode・元ファイルのバイト列が完全に不変であることを確認する（§2.8のdry-run純度修正の直接検証）

### 5.3 「回避手段とは種類の違うもの」2件以上（巡1レビュー提案の6変異を採用）

巡1レビューQ6が提示した6変異を、上表 N-a-2〜N-a-8／N-c-3〜N-c-5／N-d-1／P-3 として既に取り込んでいる。加えて:
- 変異E: `record_identity()` を「常に一致する（`return ("always",)`）」に差し替える変異。§3.2bのidentity再確認が無効化された状態でN-d-1を再現し、赤になることを確認する

**未実測**: 上記はいずれも設計段階で未実行。実装PRの完了条件とする。

### 5.4 決定論的な呼出順アサーション試験（8 writer全件に拡張）

`test_append_jsonl_correction_id.py` の `test_exclusive_lock_is_acquired_before_duplicate_check` と同型のパターンを、**8 writer全てについて**適用する（前版は2件のみだった＝Q6 [Must]で指摘）。`fcntl.flock` をスパイし、**どのpath/inodeのfdに対する呼出しか**も記録する（append側がsidecar lockの内側でファイル自体のfdにもlockを取るため、内側lockを外側lockと誤認しないよう区別する。巡1レビュー[Should]）。

各writerについて: `LOCK_EX(sidecar)` → `read_text(corrections.jsonl)` → `os.replace`/atomic write → `LOCK_UN(sidecar)` の順を固定する。unlock後の再writeが無いことも確認する。

---

## 6. 受容する残存リスク（巡1レビューの6シナリオを1件ずつ裁定）

信頼境界②「自分たちの運用ミス」の範囲内である以上、以下は**対象外にせず**、防ぐ／検出する／受け入れるのいずれかを個別に選ぶ。

| # | シナリオ | 裁定 | 根拠 |
|---|---|---|---|
| 1 | 古いworktree・中断済みセッションが旧コードを実行 | **受け入れる**（運用手順で確率低減。§4.2） | §6.1参照 |
| 2 | エディタの古いbufferを保存（手編集） | **受け入れる** | §6.1参照 |
| 3 | 稼働中のsidecar削除 | **防ぐ（取得時点）+ 受け入れる（保持中の窓）** | §6.2参照 |
| 4 | symlink別名での同一実体への書込み | **防ぐ** | `_corrections_lock_path` の `resolve()` により、同一実体を指す全てのpathが同一sidecarへ収束する（§2.2） |
| 5 | index決定後・更新前のcleanupによる別レコード書換え | **防ぐ** | §3.2bのidentity再確認により、ロック取得後に対象が入れ替わっていれば`identity_mismatch`として明示的に拒否する（黙って別レコードを書き換えない） |
| 6 | legacy行の同数置換（migration未実行） | **防ぐ（大部分）+ 受け入れる（完全同一内容の偶然一致）** | §6.2参照 |

### 6.1 #1・#2: advisory lock の原理的限界（「手を抜いた」ではなく「この方式では原理的に防げない」）

`flock`（advisory lock）は**協調するプロセス同士でのみ**排他を保証する仕組みであり、ロックを取らないプロセスの動作を止める強制力を一切持たない。これは実装の作り込み不足ではなく、**advisory lock という機構そのものの定義**である。ロックを取らずに直接 `corrections.jsonl` へ書き込むプロセス（#1: 本 issue 適用前のコードを実行する古い worktree・中断セッション／#2: 手編集でエディタの古い buffer を保存する行為）を止めるには、OS レベルの強制ロック（mandatory lock）やファイルシステムの書込み権限を書込みプロセス単位で制御する仕組みが必要であり、これは Python の advisory lock ベースの設計では原理的に実現できず、本 issue のスコープ（`corrections.jsonl` の writer 協調）を超える。

**「頑張れば防げるが手を抜いた」との違い**: 本設計が導入した `corrections_write_lock`・identity再確認・内容identity差分検出は、いずれも**その仕組み自体を経由するプロセス同士**の間でのみ機能する。#1・#2は、そもそもこの仕組みを経由しない（経由できない）プロセスによる書込みであり、対策を「もっと頑丈に作る」方向に強化しても届かない領域にある。**信頼境界②の内側にある運用ミスであることを認めた上で、技術的な防止・検出の手段が原理的に存在しないため受け入れる**（対象外として境界の外へ押し出しているのではない）。

**緩和策（コードでなく運用手順）**: §4.2 の worktree 削除手順、「`corrections.jsonl` を直接編集しない」という運用ドキュメント化。これらは確率を下げるだけで、ゼロにはしない。

### 6.2 #3・#6: 検出も防止もできない残余と、発生時に何が起きるか

**#3（sidecar 保持中の unlink→recreate）**: §2.2の inode 再検証は**ロック取得時点**の一致確認であり、**取得後・保持中**に外部から `.lock` が削除され、別プロセスが新規 inode で即座にロックを取得してしまうケースまでは防げない（自分の fd を保持したまま外部の削除操作を検知する通知機構が無い）。**発生時に何が起きるか**: 元の writer（W1）が読み込んだスナップショットを atomic replace で書き戻す際、その間に新しい writer（W2、新inodeのロックを保持）が正常に追記した行が、W1の書き戻しで消える。**数字のずれ方**: `corrections.jsonl` の行数が、W2が追記したはずの件数だけ**実際より少なく**なる（W1のbefore/afterの内容identity差分検査は自分のスナップショット内でしか整合性を見ないため、W2の追記自体を知らず検出できない）。

**#6（legacy行の完全同一内容の偶然一致）**: §2.5の内容identity（sha256）は「内容が変われば別identity」で検出するため、置換後の内容が**既存の別の行とバイト単位で完全一致**する場合にのみ、multisetのcount差分が生じず検出できない。**発生時に何が起きるか**: 消えた行Aの情報（Aが持っていた固有の文脈・タイムスタンプ等）が失われるが、`corrections.jsonl` の総行数・内容identityの多重度はどちらも変化しないため、どの検査にも引っかからない。**数字のずれ方**: 行数・identity集合のいずれにも現れないため、**この経路による消失は数字の上では0件のまま**（唯一検出可能なのは、後日 `correction_id` backfill が2件が同一内容だったことに気づいた場合のような、本設計の外側での偶然の発見に限られる）。

**両者に共通する裁定の位置づけ**: いずれも信頼境界②「自分たちの運用ミス」の内側にある事象であり、境界の外（悪意ある改変）へ押し出して対象外にしているのではない。#3は「本設計の検出・防止機構を経由する2つのwriterの間で、片方が保持中に外部要因（sidecar削除）が割り込む」という、本設計が対処しようとした対象そのものに対する**部分的な**成功であり（取得時点の窓は閉じた）、#6は「検出手段（内容identity）の定義上、区別不能な入力が理論的に存在する」という**検出手段固有の限界**である。どちらも追加の実装で確率をさらに下げることは可能だが（例: #3は保持中の定期的な自己inode再確認、#6はより強いidentity＝#587の不変ID遡及付与）、いずれも本issueのスコープ（#379新設凍結下での corrections.jsonl 内完結・#587スコープの不可侵）を超えるため、本設計では受け入れる。

**その他の残存リスク**:
- 可用性（`file_lock`の無期限待機によるハング）は §0③で対象外と明記済み。ハング時の観測・中断方法は運用手順に委ねる
- `fsync`欠如によるOS/マシン障害後のdurabilityは §0③で対象外と明記済み
- 真の追記オンリー化（イベントfold）は#587が改めて設計する
- U+2028/U+2029/U+0085を含む正常レコードは、8 rewrite writer・`iter_indexed_lines`・
  `snapshot_identities`・追記時のlock内readerではLFだけを物理改行として扱うよう修正した。
  それ以外のread-only consumerには`splitlines()`利用が残り、恒久破壊はしないものの当該レコードを
  読み飛ばして集計を過小表示する可能性がある。
- AST検査は既知のsink種別を列挙するadvisoryであり、helper経由、`os.open`+`os.write`、動的mode・
  動的path、rename/unlink等で迂回できる。`scan_repository()`は`rglob("*.py")`のため、`bin/`の
  拡張子なしPythonスクリプトも走査対象外である。検査の作り直しは別issueへ切り出す。

---

## 7. 未実測の項目

- §1のスクリプトによる洗い出しが完全であることの証明は無い（shell/CLI経由の間接呼出し・動的モジュール参照・文字列難読化は未探索。§1.4に明記）
- §5の陰性試験・陽性対照・呼出順アサーション試験は実装前のため未実行（実装PRで実測結果を報告する）
- §2.2の inode 再検証によるロック取得の待ち時間への影響（リトライループが発生する頻度・レイテンシ）は測っていない
- §2.5の内容identity（sha256）計算コストが、8 writerの通常運用（多くは数十〜数百件規模）で問題になるかは測っていない
- blocking (e) は未充足。現行AST検査は既知sinkのadvisory検査で、新しい書込経路を足せば
  必ず落ちる完全なallowlistではない。収束形への反転は別issueで扱う。
