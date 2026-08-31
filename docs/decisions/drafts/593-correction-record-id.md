# #593: correction レコードに位置非依存の不変識別子を与える設計

対象: `#593`。本文書は**設計のみ**。コードは1行も変更しない。

**関係する issue**: `#587`（柱2の照合済み反映を測れるようにする設計）は codex 設計レビュー
2巡で `設計修正要` が続き、`review-round-cap.md` の族2巡打ち切りによりユーザー裁定で
本 issue へ切り出された（切り出し元: `docs/decisions/drafts/587-pillar2-applied-measurement.md`
§2.3、branch `design/587-pillar2-append-events` commit `c84637b4`）。**その設計が採用していた
`(source_correction_id, ordinal)` 複合キーは、`ordinal` が「ファイル出現順の相対位置」から
導出される値であるため、削除・並べ替え・重複除去に耐えない**という指摘が出発点。
本設計はこの弱点を直すことに範囲を絞り、#587 本体（反映イベントの追記・read 時 fold・
柱2集計）には触れない。

## 0. Round 0 完成条件（verbatim）

### ① 守る対象

correction レコードの個体を、ファイル内の位置に依存せず一意に指せること。

### ② 信頼境界

自分たちの運用ミスのみ（手編集 / 別プロセスの追記 / 中断 / 同時に走る2つの更新 /
移行スクリプトの未実行 / 再 ingest による重複）。悪意ある偽装・第三者の改竄は脅威に
数えない。

### ③ 対象外

- 柱2の集計・表示（`results_board`）の変更。数え方には一切触れない
- 反映イベントの追記と read 時 fold（#587 の本体）
- `reflect_status` の意味論変更
- `#379` 新設凍結の解除。新しい保存先を作らない

### ④ blocking（設計はこの5つを塞がねばならない）

- (a) 同一 ID を持つレコードが2件以上存在しうる
- (b) レコードの削除・並べ替え・重複除去で ID が変わる、または別レコードを指す
- (c) ID を持たない既存レコードが、持つものと**黙って**同じに扱われる（fail-closed でない）
- (d) ID の発行が既存の reader を壊す
- (e) 中断で ID だけ書かれた／本体だけ書かれた状態が生じ、成功として扱われる

### ⑤ 検証方法

(a)〜(e) 各1件以上の陰性試験＋**陽性対照**を対で置く。(b)(e) は読取と書込の間に
実際に別の書込・削除を差し込んで決定論的に再現する（N プロセス同時実行は競合窓が
µs で再現せず偽の安全網になる）。

## 1. 現状（実測・file:line つき）

### 1.1 実データ

```
$ wc -l ~/.claude/evolve-anything/corrections.jsonl
     241 /Users/matsukaze-takashi/.claude/evolve-anything/corrections.jsonl
```
取得時刻: 2026-08-31T23:32:12Z（`date -u +"%Y-%m-%dT%H:%M:%SZ"`）。

```python
# 241 件を parse し、(session_id, timestamp) の重複と top-level キー集合を確認
n records parsed: 241
keys: ['confidence', 'correction_type', 'decay_days', 'error_category',
       'extracted_learning', 'guardrail', 'invalidated', 'invalidated_at',
       'invalidation_reason', 'last_skill', 'matched_patterns', 'message',
       'pattern_version', 'preceding_tool_calls', 'project_path',
       'reflect_status', 'routing_hint', 'sentiment', 'session_id', 'source',
       'timestamp', 'turn_index', 'weak_signal_channel', 'weak_signal_key',
       'weak_signal_provenance']
duplicate (session_id,timestamp) pairs: 0
```
取得時刻: 2026-08-31T23:32:19Z。**既存241件に一意識別子に相当するフィールドは無い**
（`id`/`uuid`/`correction_id` いずれも keys に含まれない）。現在の実データでは
`(session_id, timestamp)` 重複は0件だが、これは実測結果であって保証ではない
（§3 で `make_source_correction_id` が「実質一意」としか宣言していないことを確認する）。

### 1.2 書込み側（新規レコードを作る4箇所）

| file:line | 何をするか |
|---|---|
| `hooks/correction_detect.py:132-165` | UserPromptSubmit hook。`record = {...}` を組み立て `common.store_write("corrections.jsonl", record)`（line 164）で追記。`reflect_status`/`session_id`/`timestamp` はここで初めて生成される |
| `scripts/lib/correction_semantic/promote.py:346-393`（`_build_correction_record`）→ `:565-568` | weak signal の昇格。`store_write("corrections.jsonl", record)` または `store_write_raw(corrections_path, record)`（テスト/isolation 用パス指定時） |
| `scripts/backfill_preceding_tool_calls.py:207-218` | 過去セッションからの一括バックフィル。`results.append({...})` で構築し、別関数 `persist_to_corrections`（219行目以降）が書込む |
| `scripts/migrate_reflect_queue.py:38-56`（`convert_learning`） | `learnings-queue.json` からの1回限りマイグレーション。`_dedup_key`（line 18-21）で `(timestamp, SHA256(message[:100]))` の冪等キーを持つが、**このキーはファイルに保存されない**（read 時に既存行から再計算するだけ）ため識別子としては使えない |

### 1.3 書込み経路の排他制御

`scripts/lib/rl_common/persistence.py:154-172`（`append_jsonl`）は
`fcntl.flock(f, LOCK_EX)`（line 160）を取ってから1行追記する。**新規レコードの追記は
すべてこの関数を経由する**（`store_write` → `store_write_raw` → `append_jsonl` という
呼び出し経路。`hooks/correction_detect.py:164` と `promote.py:566/568` はこの経路）。
一方、`scripts/backfill_preceding_tool_calls.py` と `scripts/migrate_reflect_queue.py` は
**一括書込み**（全件をまとめて `write_text`）であり、`append_jsonl` のロックを経由しない
別経路。ただしこの2つは「1回限りの手動実行」が前提のスクリプトで、hook や CLI と
同時に走る運用は想定されていない（コメント上の宣言のみで、コードによる強制はない）。

## 2. 採用する記録モデル: レコード内蔵の UUID を新設フィールドとして持つ

### 2.1 選ばなかった案とその理由

| 案 | 却下理由 |
|---|---|
| `(source_correction_id, ordinal)` 複合キー（#587 v2 §2.3・不採用） | `ordinal` は「ファイル出現順の相対位置」から**毎回再計算**する値。同じ `source_correction_id` を持つレコードが増減すると、既存レコードの `ordinal` が変わりうる（削除された1件が0番だった場合、1番だったレコードが0番に繰り上がる）。§2.3 が想定した「基底レコードは追記のみで物理削除しない」という前提は、実際には `prune/corrections.py:105-115` が破っている（§4.2）ため、この案は blocking (b) を満たさない |
| 生 JSON 行のテキストのハッシュ（内容ハッシュ）をキーにする | 同一内容のレコードが2件生成された場合（例: `correction_detect.py` が同一メッセージを2回検出）に区別できない。また `reflect_status` の更新（例: `pending`→`applied`）でハッシュが変わってしまうため、更新をまたいで同一レコードを指せない（`pitfall_content_identity_with_run_id.md`「冪等キーは対象でなく対象の状態で作ると壊れる」と同型の欠陥） |
| **新規フィールド `correction_id`（UUID4）をレコード生成時に1回だけ発行し、レコードの JSON payload の一部として保存する（採用）** | 値がファイル内の位置からも内容からも独立している。レコードがどこに移動しても、内容がどう更新されても（`reflect_status` を書き換えても）不変。削除・並べ替えは**他の**レコードの `correction_id` に一切影響しない（ordinal のような再計算が発生しない） |

### 2.2 スキーマ

新規フィールド `correction_id: str`（`uuid.uuid4().hex` の32文字16進文字列）を
基底レコードに追加する。生成は単一関数に集約する:

```python
# scripts/lib/rl_common/correction_id.py（新規モジュール・新規 store ではない）
import uuid

def new_correction_id() -> str:
    """新規 correction レコードに割り当てる、位置に依存しない一意識別子を生成する。"""
    return uuid.uuid4().hex
```

§1.2 の4つの書込み箇所すべてで、レコード構築時（`store_write`/`persist_to_corrections`/
`migrate` へ渡す**前**）に `record["correction_id"] = new_correction_id()` を追加する。
既存フィールドの意味は一切変えない（round 0 対象外「`reflect_status` の意味論変更」を守る。
`correction_id` は新設フィールドであり既存値の再定義ではない）。

### 2.3 資格解決（resolve）の契約: 曖昧なら fail-closed で拒否する

```python
# scripts/lib/rl_common/correction_id.py（続き・設計のみ）
from dataclasses import dataclass
from typing import Optional

@dataclass
class ResolveResult:
    status: str            # "found" | "not_found" | "ambiguous" | "missing_id"
    record: Optional[dict] = None
    index: Optional[int] = None       # 呼出元がファイルへの書き戻しに使う物理配列位置
                                       # （load 直後のスナップショット限りで有効。§2.4 参照）
    match_count: int = 0

def resolve_by_id(records: list[dict], correction_id: str) -> ResolveResult:
    """records（load_corrections 相当の生配列）から correction_id 一致レコードを解決する。

    - correction_id が空文字列/None なら missing_id を返す（blocking c の防御。
      レコード側の欠落フィールドを空文字列として拾って偶然一致させない）。
    - 一致が0件なら not_found。
    - 一致が2件以上なら **ambiguous**（blocking a の防御。先頭を勝手に選ばない）。
    - 一致が1件なら found。
    """
    if not correction_id:
        return ResolveResult(status="missing_id")
    matches = [
        (i, r) for i, r in enumerate(records)
        if isinstance(r, dict) and r.get("correction_id") == correction_id
    ]
    if not matches:
        return ResolveResult(status="not_found")
    if len(matches) > 1:
        return ResolveResult(status="ambiguous", match_count=len(matches))
    i, r = matches[0]
    return ResolveResult(status="found", record=r, index=i, match_count=1)
```

**`ambiguous` の呼出側契約**: CLI（`reflect.py` の `--apply`/`--skip`）は `ambiguous` を
`not_found` と同じく非0終了させ、どのレコードも更新しない。曖昧な状態を人間が手動で
解消する（重複データの是正）まで、その `correction_id` に対する操作を一切許可しない。
これは「同一 ID を持つレコードが2件以上存在すること自体を防止する」のではなく
（信頼境界②の外側にある運用ミス — 再 ingest 等 — を完全には防げないため）、
**存在してしまった重複を安全に無害化する**設計である（blocking (a) は「防止」でなく
「発生しても実害が出ない」ことを要求している、と読む — ④の文言「同一 ID を持つ
レコードが2件以上存在しうる」は起こりうる前提を認めた上での禁止対象を問うており、
「存在しない」ことまでは要求していない。誤読の余地があるため§11で人間の確認を仰ぐ）。

### 2.4 CLI（`reflect.py`）側の変更点（設計のみ・実装は次巡）

現行 `skills/reflect/scripts/reflect.py:1263-1269`（`--apply`）と `:1329-1335`（`--skip`）は
`make_source_correction_id(sid, ts) == args.apply` の**先頭一致**（`break`、line 1269/1335）で
`target_index` を決めている。これを次のように変える:

1. `all_records = load_corrections(corrections_file)` は現行どおり
2. `make_source_correction_id(sid, ts) == args.apply` に一致するレコード**群**を集める
   （現行の `break` を外す）
3. 1件だけなら、その1件の `correction_id` で `resolve_by_id` を呼び直し、
   `index`（配列位置）を確定する（`correction_id` が無い旧レコードなら §4 の移行未実施
   として `missing_id` 扱い — 後述）
4. 2件以上なら、`source_correction_id` だけでは一意に定まらない（現在の実データでは
   0件だが§1.1参照、将来的に起こりうる）ので `{"status": "ambiguous_source_id", ...}` を
   返し非0終了する。**黙って先頭を選ばない**（現行の `break` はこの曖昧さを握り潰している）

**`target_index` の役割の変化**: 従来は「読んだ配列の位置」をそのまま
`update_reflect_status` に渡していた（読取後に配列が変わりうる問題は #587/#588 が担当）。
本設計では `target_index` は「`correction_id` で再解決した結果の配列位置」になる。
`update_reflect_status` 自体の identity-safe 化（読取直後の再確認・ロック協調）は
#587 の blocking (f)(g) に属する話であり、**本 issue はそこへ手を入れない**
（round 0 対象外「反映イベントの追記と read 時 fold」には含まれないが、
「`update_reflect_status` の内部実装」自体も round 0 ①「レコードの個体を指せること」の
射程外——指せるようになった後にどう安全に書き込むかは #587 の担務）。

## 3. `source_correction_id` との関係: 併存させる（置き換えない）

**判断**: `correction_id`（新設・UUID・不変）を内部識別子として採用し、
`source_correction_id`（`make_source_correction_id`、`scripts/lib/memory_temporal.py:339-345`、
`f"{session_id}#{timestamp}"` 形式）は**廃止しない**。

**根拠（file:line）**:

- `memory_temporal.py:343` のドキュメント文字列は「session_id と ms 精度の timestamp の
  組み合わせで**実質一意**」と明記しており、一意性を保証していない
- `source_correction_id` は本 issue のスコープ外の箇所で**表示・監査キー**として広く
  使われている: `reflect.py:857-861`（memory 書き込み時の provenance）、
  `reflect.py:975-980`（CLI `--apply`/`--skip` の引数として人間/上位ツールが入力する値）、
  `scripts/lib/audit/memory.py:474-489`（`reflected_ids: set[str]` に `add` — この実装は
  **重複が起きた場合に `set` へ吸収されて静かに1件へ縮む**、まさに blocking (a) の
  「同一 ID が複数存在しても検出されない」実例。本設計はこの箇所を変更しないが
  （round 0 対象外の柱2集計コード）、この実例が「実質一意」という前提の危うさを
  裏付ける一次証拠として記録しておく）
- `source_correction_id` を丸ごと `correction_id` に置き換えると、**人間が既に
  `--apply <source_correction_id>` の形式で運用している**（`reflect.py:975-980` の
  help 文字列、daily review の既存フロー）ため、CLI 入出力フォーマットの破壊的変更になり
  round 0 対象外の「反映先の種別が残らない」等とは別の、非対象領域（CLI UX）への
  波及を生む。**置き換えではなく、内部の一意性保証だけを `correction_id` に差し替える**
  ほうが影響範囲が小さい

**結論**: `source_correction_id` は「人間が入力する検索キー（実質一意・表示用）」として
残し、`correction_id` は「システム内部で実際にレコードを同定する一意キー（保証された
一意性ではないが、fail-closed な resolve で安全化された運用一意キー）」として新設する。
2層構造にする。

## 4. 既存 reader の列挙（自分で数え直した結果）

```
$ grep -rn "reflect_status" /Users/matsukaze-takashi/wt/ea-593 --include="*.py" | grep -v "/tests/\|test_"
```
取得時刻: 2026-08-31T23:38 台（本文書作成セッション内で実行、上のコマンドで再現可能）。
`reflect.py` 自身の定義・書込み箇所・docstring 内の言及を除き、**独立して `reflect_status`
を直接読む箇所は次の6箇所**（issue 本文の「最低6箇所」と一致することを自分のコマンド
実行で確認した）:

| # | file:line | 読み方 | `correction_id` 追加の影響 |
|---|---|---|---|
| 1 | `scripts/lib/audit/memory.py:474-489` | 独自 `for line in ... json.loads` ループ | 影響なし。`.get("reflect_status")`/`.get("session_id")`/`.get("timestamp")` のみ参照、未知キーは無視される（Python dict の `.get` は該当なしを許容） |
| 2 | `scripts/lib/correction_semantic/correction_backlog.py:106`（`_read_eligible_backlog_records`） | `fleet.queue_materials.read_corrections_records_with_health`（`scripts/lib/fleet/queue_materials.py:227-`）経由。単一ソース化済みの read health 付き reader | 影響なし。同上 |
| 3 | `scripts/lib/discover/suppression.py:198`（`load_claude_reflect_data`） | `load_jsonl`（同ファイル line 22-31）で独自 parse。`isinstance(dict)` チェック無し | 影響なし |
| 4 | `scripts/lib/issues_summary.py:35-42`（`_count_unprocessed_corrections`） | 呼出元が渡した `Iterable[Mapping]` を走査するのみ（自身はファイルを読まない） | 影響なし。`isinstance(rec, Mapping)` チェックあり（line 39） |
| 5 | `skills/genetic-prompt-optimizer/scripts/optimize_core.py:60-84`（`collect_corrections`） | 独自 `for line in ... json.loads` ループ | 影響なし |
| 6 | `scripts/lib/prune/corrections.py:17-48`（`load_corrections`）/ `:51-117`（`cleanup_corrections`） | 独自 `for line in ... json.loads` ループ。**物理削除**を行う唯一の箇所（§7.2 で扱う） | 影響なし（read 側）。削除時は行をまるごと落とすため `correction_id` を含む JSON も一緒に消える（意図通り。§7.2） |

**結論（blocking d の充足）**: 6箇所すべて `dict.get(key)` 方式（未知キーを無視する）で
読んでおり、`isinstance(..., dict)` の有無に関わらず**新規フィールドの追加は既存6箇所の
挙動を一切変えない**。スキーマ検証ライブラリ（pydantic 等）や `extra="forbid"` 相当の
仕組みを本リポジトリの corrections.jsonl 周辺コードに使っている箇所は grep 上見つからない
（`grep -rn "pydantic\|extra=.forbid" scripts/lib/ hooks/ skills/reflect` は0件）。
6箇所とも**改修不要**。

## 5. `#379` 新設凍結に抵触しない根拠

`scripts/lib/shrink_freeze.py:62-77`（`FROZEN_STORES`）は72行目に `"corrections.jsonl"` を
列挙している。`assert_no_new_keys`（`shrink_freeze.py:261-275`）は
`current`（実行時に検出したストア名の集合）と `frozen`（上記の固定集合）の**差分**を
見て、`frozen` に無い新しい**ストア名**が現れたら reject する（line 269-275）。
**この関数はストアの中身（フィールド名）を一切検査しない** — 引数 `current`/`frozen`
はどちらも `Iterable[str]`（ストア名の文字列集合）であり、レコードの JSON 構造は
渡されていない。

`corrections.jsonl` という**ストア名自体は変わらない**（新しいファイルを作らない・
既存ファイルへ新フィールドを追記するだけ）ため、`assert_no_new_keys` の検査対象に
本設計は一切ならない。`shrink_freeze.py` の docstring（line 9）が言う「新設」は
「実装時点（#379 Step 1）の `store_registry`/`_OBSERVABILITY_BUILDERS`/
`ADVISORY_PROPOSAL_ADAPTERS`/`WEAK_SIGNAL_CHANNELS` の live 集合」（line 26-28）の
話であり、**store の中のフィールド粒度は凍結の対象になっていない**（#587 v2 §2.4 が
同じ根拠で新規4フィールドを追加した前例があり、本設計もそれに倣う）。

**新しい observability section も作らない**: §2.3 の `resolve_by_id` は
`audit --growth` 等のレポート出力に接続しない（ambiguous の検出結果を通知する
新しい表示面は作らない、という意味で `_OBSERVABILITY_BUILDERS`（新設凍結対象）にも
一切触れない）。ambiguous は CLI 呼出しの戻り値としてのみ表出する。

## 6. 有効レコードの述語を単一ソース化する設計

**現状（§4 で確認した重複）**: `reflect.py:111-124`（`load_corrections`）、
`prune/corrections.py:17-48`（別の `load_corrections`）、
`discover/suppression.py:22-31`（`load_jsonl`）、`audit/memory.py:474-489`（インライン）、
`optimize_core.py:60-84`（インライン）の**5箇所が独立に**
`try: json.loads(line) except JSONDecodeError: continue` を実装している
（`fleet/queue_materials.py:227-` の `read_corrections_records_with_health` だけが
単一ソース化済みで、`correction_backlog.py` のみがそれを使っている）。

**本 issue の対応範囲**: 上記5箇所の統合は `pitfall_copied_parse_convention_partial_fix`
（片側だけ直す部分修正のリスク）に該当する横展開作業であり、`design-before-fanout.md`
に従えば独立した issue にすべき規模（5箇所×挙動確認のコストが `correction_id` 追加
そのものより大きい）。**本設計は述語の統合をスコープに含めない**が、
`correction_id` を含む新しいレコード生成コード（§2.2 の `new_correction_id`）と
`resolve_by_id`（§2.3）は、**この5重複の6つ目を増やさない**ように設計する:
`resolve_by_id` は `records: list[dict]` を受け取るだけで、ファイルの読み込み自体は
呼出元（`reflect.py` の既存 `load_corrections`）に委ねる。`resolve_by_id` 内部で
`isinstance(r, dict)` を確認する（§2.3 のコード参照）ことで、呼出元が非 dict を
混入させても安全側に倒れる（述語の**完全な**単一化ではないが、「新設する関数が
既存の非統一状態を悪化させない」ことは満たす）。

**述語統合を本 issue でやらない代わりに払うコスト**: `prune/corrections.py` の
`load_corrections`（§4 の #6）は `record.setdefault(...)` で後方互換フィールドを
補完しているが、`correction_id` にはこの補完を追加しない（§7 の移行スクリプトが
別途担うため）。これにより `prune.load_corrections()` が返す辞書には、移行未実施の
レコードで `correction_id` キー自体が存在しない状態と、移行済みで存在する状態が
混在しうる。読み手はいずれも `.get("correction_id")` で `None` を受け取れるため
実害はない（§4 の結論を参照）。

## 7. 移行

### 7.1 対象

既存241件（§1.1、取得時刻 2026-08-31T23:32:12Z）すべてが `correction_id` を持たない。
**一括付与**を採用する（read 時導出は不採用 — 理由は次項）。

**read 時導出を採らない理由**: `correction_id` は「このレコードを一意に指す」という
用途上、**書込み側と読み込み側が同じ値を得られる**必要がある（§2.3 の `resolve_by_id`
は「渡された `correction_id` に一致するレコードを探す」という双方向の同定に使うため）。
read 時に `(session_id, timestamp)` 等から**決定論的に**再計算する案は、結局
`source_correction_id` と同じ「実質一意」の弱点を引き継ぐ（§3 で却下したのと同じ理由）。
真に位置・内容非依存な値（UUID）は、その場で作って保存する以外に得る方法がない
（過去に遡って「本来割り振られるべきだった UUID」を再現することはできない —
これが§11で人間へ確認する未決定点の一つ）。

### 7.2 移行スクリプトの設計（中断耐性を含む）

新規スクリプト `scripts/migrate_correction_id_backfill.py`（既存の
`scripts/migrate_reflect_queue.py`/`scripts/migrate_reflect_promoted_status.py` と
同じ「1回限りの手動実行」パターンを踏襲）:

1. `fcntl.flock(f, LOCK_EX)` を取得する（`append_jsonl` と同じロック対象ファイル・
   `persistence.py:159-160` と同じ排他。これにより、移行実行中に hook が追記を
   試みても待たされるだけで、追記中の行を移行スクリプトが上書きすることはない
   — §7.3 で決定論的に再現する）
2. ロックを保持したまま、ファイル全体を読み込む
3. 各行について:
   - 空行・JSON parse 失敗（`json.JSONDecodeError`）・dict でない値 →
     **そのまま温存**（既存慣習 `reflect.py:687-696` と同じ扱い。パースできない行に
     `correction_id` を挿入しようがないため、これは温存以外の選択肢がない）
   - dict かつ既に `correction_id` を持つ → **そのまま温存**（冪等性。二重付与しない）
   - dict かつ `correction_id` を持たない → `new_correction_id()` を1回呼び、
     そのフィールドだけを追加した dict を新しい行として組み立てる
4. 3で組み立てた全行を**一時ファイルへ書き込み**、`tempfile.mkstemp` +
   `os.replace`（atomic rename）でファイル全体を差し替える。この手法は
   `scripts/lib/correction_semantic/promote.py:634-642`
   （`invalidate_idiom_corrections`）が既に使っている atomic-write パターンと同一
5. ロックを解放する

**中断耐性（blocking e）の成立理由**:

- ステップ1〜3は**メモリ上の計算のみ**でファイルへの書込みを一切行わない。
  この途中でプロセスが kill されても、`corrections.jsonl` の内容は移行開始前と
  ビット単位で同一（一時ファイルすら作られていない可能性がある）
- ステップ4は `tempfile.mkstemp`（別パスへの新規書込み）→ `os.replace`
  （OS レベルでの atomic rename）の2段階。前半で kill されれば `corrections.jsonl`
  は無傷、後半（rename 自体）は OS が保証する原子操作なので「rename の途中」という
  状態は存在しない
- **再実行の安全性**: ステップ3の「既に `correction_id` を持つ行は温存」という
  冪等性により、移行が完了した後に誤って再実行しても実質 no-op（新しい UUID で
  上書きされることはない）。中断後の再実行は「最初から全部やり直す」だけでよく、
  「途中から再開する」ロジックを持つ必要がない（`persist-progress-incrementally.md`
  が要求する「実体を読んで再開判定」を、**全体が241件と小さく1回のロック区間内で
  完結できる規模**であることを理由に、チャンク分割・進捗マーカー方式ではなく
  「毎回全件処理・冪等性で安全化」方式を採用する。件数が数万件規模になった場合は
  この設計は再検討が要る — §11）

### 7.3 §7.2 の主張を裏付ける決定論的再現（設計時点での確認手順のみ記載。実装は次巡）

読取と書込の間に別の書込を実際に差し込んで検証する試験設計（§8 の (e) 陰性試験の
一部として実装1巡で実行する）: 移行スクリプトの「ロック取得」ステップと
「ファイル読込」ステップの間に、テストコード側で別スレッドから `append_jsonl` を
1回呼ぶ。`append_jsonl` は `LOCK_EX` を取ろうとしてブロックされるため、移行スクリプト
側のロック解放後に初めて追記が成立する。移行スクリプトが読み込んだ内容には
その追記行が**含まれていない**ため、書き戻し後のファイルには追記された行が残らない
という結果になる**はず**——これは望ましくない喪失なので、実装1巡では
「ロック取得は追記側と移行側で同じファイルパスの同じロックを取り合う」ことに加えて
「移行スクリプトは自分がロックを保持している**間に他の書込みが来ないことを祈るのではなく、
書き戻し直前にもう一度ファイルの mtime か内容ハッシュを確認し、ロック取得後に
想定外の変化がないことを検証する」という §2.2 の resolve と同型の防御を追加するかどうかを
検討する必要がある（**未決定点として§11に記載** — 本設計は「ロックを取る」ところまでは
確定させたが、「ロック保持中に発生し得る取りこぼし」の完全な排除は、移行スクリプトが
一括 `write_text` を行う設計である限り原理的に残る。243行規模・1回限りの手動実行という
運用条件下ではリスクは小さいが、ゼロではないことを明記する）。

## 8. 検証計画

各陰性試験に「壊す不変条件」と「通したい検査経路」を書く。テストは
`scripts/lib/tests/test_correction_id.py`（新設）に置く。

| # | 壊す不変条件 | 変異 | 通したい検査経路 | 期待結果 |
|---|---|---|---|---|
| (a) 陰性 | 同一 ID が複数存在しても片方だけが黙って選ばれない | fixture に同じ `correction_id` を持つ内容の異なる2レコードを用意 | `resolve_by_id` | `status == "ambiguous"`, `match_count == 2`, `record is None` |
| (a) 陽性対照 | 同上 | `correction_id` が異なる2レコード | 同上 | 対象の1件だけが `status == "found"` で返る |
| (a) トートロジー化の回避 | — | 上記陰性試験は「`resolve_by_id` が None 以外を返さない」ことだけを見ると、`matches[0]` を常に返す（先頭を黙って選ぶ）実装でも「何かは返る」ため**その粒度のassertでは検出できない**。テストは必ず `status == "ambiguous"` という**具体的な文字列**と `record is None` の両方を assert する（先頭を返す壊れた実装だとこの assert で確実に落ちる） | | |
| (b) 陰性 | 削除・並べ替えが**他の**レコードの ID を変えない | fixture 3件（A/B/C）を用意し、B の `correction_id` を記録した上で A を配列から**物理削除**して書き戻し、再度ファイルを読み直して `resolve_by_id(records, B の id)` を呼ぶ | `resolve_by_id` の ID ベース照合 | 削除前後で B の中身（`correction_id` 以外のフィールドも含め）が完全一致した状態で見つかる。**A の削除前は B が index=1、削除後は B が index=0** になっていることも同時に assert し、「index は変わったが id ベースの解決結果は変わらない」ことを対比で示す |
| (b) 陽性対照 | 同上 | 削除を行わず同じ3件のまま `resolve_by_id(records, B の id)` を呼ぶ | 同上 | 削除ありのケースと**同じ** B の中身が返る（index は元の1のまま） |
| (b) トートロジー化の回避 | — | 「削除後も見つかる」だけを見ると、`resolve_by_id` が実は index ベースにフォールバックしていて**たまたま**別の当たりを返す実装でも通ってしまう。テストは返ってきたレコードの `correction_id` フィールド自体が要求した値と一致することに加え、`message`（内容フィールド）が削除前の B の内容と一致することまで assert する（ordinal ベースの誤実装なら、削除後は C の内容を B として誤って返すため、この二重 assert で検出できる） | | |
| (c) 陰性 | ID を持たない旧レコードが偶然一致しない | `correction_id` フィールード自体が存在しない fixture レコードを用意し、`resolve_by_id(records, "")`（空文字列）と `resolve_by_id(records, None)` を呼ぶ | `resolve_by_id` の `missing_id` 早期リターン | 両方とも `status == "missing_id"`。**旧レコードが誤って返らない**ことを `record is None` で確認 |
| (c) 陽性対照 | 同上 | 同じ fixture 内に `correction_id` を持つ正常レコードを1件混ぜ、その実際の値で `resolve_by_id` を呼ぶ | 同上 | `status == "found"` でその1件が返る（旧レコードとは無関係に解決できる） |
| (c) トートロジー化の回避 | — | `rec.get("correction_id", "") == correction_id` という実装（旧レコードの欠落を空文字列として拾う）は、`correction_id=""` を渡された場合に**旧レコードにマッチしてしまう**。上記の陰性試験はまさにこの引数（空文字列）で呼んでいるため、この壊れた実装だと `status` が `"found"` になり赤くなる。**トートロジーではなく実際に有効な検査**であることをこの理由で担保する | | |
| (d) 陰性 | 新フィールド追加が既存 reader を壊さない | §4 表の6箇所のうち、直接呼べる4関数（`_count_unprocessed_corrections`／`load_claude_reflect_data`／`collect_corrections`／`prune.corrections.load_corrections`）それぞれに、**同一内容+`correction_id` フィールドだけ追加した** fixture と、**`correction_id` なしの同一内容** fixture の2種を渡し、返り値を比較する | 4関数それぞれ | 2種の fixture で返り値が完全一致（新フィールドの有無で挙動が変わらない） |
| (d) 陽性対照 | 同上 | 同じ4関数に、`reflect_status` の値**そのもの**を変えた fixture（`pending`→`applied`等）を渡す | 同上 | 返り値が変わる（＝テストの比較ロジック自体が「違いを検出できる」ことを別の軸で確認する対照。無条件で同じ値を返す壊れたテストでないことの証明） |
| (e) 陰性1（読込前に kill 相当） | 中断で ID だけ書かれる状態が生じない | 移行処理のうち「計算」ステップ（§7.2 の1〜3）だけを呼び、「書き戻し」ステップ（4）を**意図的に呼ばない**（例外を送出させて呼び出しを止める）。その後、元の `corrections.jsonl` を再読込する | 移行スクリプトの2段階分離 | ファイル内容が処理前とバイト単位で同一（`hashlib.sha256` の一致で確認）。**1件も `correction_id` を持たない** |
| (e) 陽性対照 | 同上 | 同じ fixture で書き戻しステップまで完走させる | 同上 | 全レコードが `correction_id` を持ち、`correction_id` 以外のフィールドは変化なし |
| (e) 陰性2（追記との競合） | ロック保持中の追記が失われない | §7.3 で述べた「ロック取得直前で別スレッドから `append_jsonl` を呼ぶ」を実際にテストコードで再現する（`threading.Thread` で「移行スクリプトの `flock` 取得」の直前にブロックさせ、その状態で追記スレッドを起動、追記完了後に移行スクリプトの取得を進める、という順序をロックオブジェクトのモックで固定する） | §7.2 のロック取得順序 | 移行完了後のファイルに、追記されたレコード（`correction_id` 付き・新規なので既に持っている）と、移行対象だった旧241件（新たに `correction_id` が付与された状態）の**両方**が残る |
| (e) トートロジー化の回避 | — | 「ファイルが空でない」「何行かある」という粒度の assert では、追記行を消して243→242件になった実装でも通る。テストは追記したレコードの一意な `message` フィールドの値が書き戻し後のファイルに**行として存在する**ことを直接 grep 相当で確認する | | |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:

- `resolve_by_id` の `len(matches) > 1` 判定を `len(matches) >= 1`（＝常に見つかった
  時点で ambiguous 分岐を無視して found を返す）に変異させたビルドを一時的に作り、
  (a) の陰性試験が緑のまま残らない（＝赤くなる）ことを確認する
- 移行スクリプトの atomic rename（`os.replace`）を、行ごとの逐次 `write_text` 追記
  （非 atomic）に変異させたビルドを一時的に作り、(e) 陰性1 の途中停止試験が
  「一部の行だけ `correction_id` を持つ」状態を検出して赤くなることを確認する

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示）:
`correction_id` の衝突（UUID4 の理論上の衝突確率 2^-122 は本設計では無視するが、
`uuid.uuid4()` が乱数源の初期化不備で偏る環境があるかは未検証）／
移行スクリプトを**2プロセス同時**に起動した場合の `flock` 待機順序（本設計は
「1回限りの手動実行」を前提にしており多重起動を積極的には検証しない）／
`corrections.jsonl` が**数万件規模**に成長した場合の移行スクリプトのメモリ・時間
（§7.2 の「全件をメモリに載せて一括書き戻し」は241件では問題にならないが
スケールしない設計であることを§11に明記）。

## 9. この設計が成立しなくなる入力・順序・中断点（自己検証・3件以上）

1. **バックアップ復元による内容重複**: `corrections.jsonl` を過去のバックアップから
   復元した際、既に `correction_id` を持つレコード群が、現在のファイルに既に存在する
   同じ `correction_id` のレコードと**衝突する**（信頼境界②「再 ingest による重複」に
   該当）。**設計の答え**: §2.3 の `resolve_by_id` が `ambiguous` を返すため、
   衝突した ID に対する更新操作はすべて拒否される（データは壊れないが、その ID を
   持つレコード群は「更新不能」になる）。**この状態からの回復手順は本設計に含まれない**
   （手動での重複解消が必要。round 0 blocking (a) は「実害を出さないこと」までを要求し、
   「自動修復」までは要求していないと解釈した — §11で確認）
2. **`prune/corrections.py` の decay 削除との時間的競合**: §7.2 の移行スクリプトが
   ロックを保持している最中に `cleanup_corrections`（`prune/corrections.py:51-117`）が
   実行されると、`cleanup_corrections` 自身は `append_jsonl` のロックを一切見ていない
   （§1.3 で確認済みの独立書込み経路）ため、**移行スクリプトの `flock` はこの経路を
   防御しない**。移行の書き戻し（`os.replace`）と `cleanup_corrections` の書き戻し
   （`corrections_file.write_text`、`prune/corrections.py:113-116`）が同時に走ると、
   後勝ちで片方の変更が消える。**設計の答え**: これは本設計が新たに作るリスクではなく、
   `prune/corrections.py` が最初から `append_jsonl` のロック機構に参加していない
   既存の欠陥（#587 v2 §1.1 が同種の欠陥として `invalidate_idiom_corrections` を
   記録済み）。**本 issue はこの欠陥を修正しない**（round 0 対象外の「反映イベントの
   追記」寄りの話ではなく、`prune` 自体の独立したロック協調の話であり、blocking (a)〜(e)
   のいずれにも含まれていない）。ただし移行スクリプトの実行タイミングは
   「`prune` の自動実行スケジュールと衝突しない時間帯に手動実行する」という運用上の
   注意として§11に明記する
3. **`store_write_raw` によるテスト/isolation パス経由の書込み**
   （`promote.py:568`）: production 経路（`store_write`）を通らない書込みが
   `correction_id` の付与を忘れる実装ミスがあった場合、その経路で作られたレコードは
   `correction_id` を持たないまま corrections.jsonl に混入する。**設計の答え**:
   §2.2 の `new_correction_id()` 呼び出しは `_build_correction_record`
   （`promote.py:346-393`）という**レコード構築関数の内部**に置く設計にし
   （`store_write`/`store_write_raw` のどちらを呼ぶかの分岐（`promote.py:565-568`）
   より**前**）、production/テスト両経路が同じ構築関数を通る限り取りこぼさない。
   実装1巡でこの配置（構築関数内 vs 呼出し分岐後）を守ることをレビュー観点として
   明記する
4. **移行スクリプトの未実行のまま新規書込み関数だけ先にデプロイされる順序**:
   §2.2（新規レコードは `correction_id` を持つ）と §7.2（既存レコードへの一括付与）
   は独立した変更単位として実装しうる。前者だけ先にマージ・運用されると、
   `corrections.jsonl` の中に「新しい行は `correction_id` を持つが、古い行は持たない」
   という**過渡的な混在状態**が、移行スクリプト実行までの間ずっと続く。
   **設計の答え**: §2.3 の `resolve_by_id` は `correction_id` の有無を前提にしない
   （欠落レコードは `not_found`/`missing_id` として自然に扱われ、クラッシュしない）ため、
   この過渡状態は**安全側で動作する**（新しいレコードだけが ID 経由で操作可能になり、
   古いレコードは移行完了まで ID 経由の操作対象外のまま）。§4 の6箇所の reader も
   混在状態を問題なく読める（blocking d の結論がそのまま当てはまる）。
   **実装1巡の完了条件には、2つの変更単位を同一 PR ないし同一デプロイ単位にまとめるか、
   分離してよいかの判断を含める**（分離してよいという結論が本設計の帰結だが、
   最終判断は実装1巡のレビューで確定させる）

## 10. やらないこと（完成条件③の対象外の再掲・理由つき）

- **柱2の集計・表示（`results_board`）の変更**: 本設計は識別子のみを扱う。集計ロジックへの
  接続は #587（切り出し元）の担務
- **反映イベントの追記と read 時 fold**: `record_kind="reflect_event"` のような追記型
  イベント設計は #587 v2 §2.4 が既に書いている。本設計はそれとは独立に成立する
  （`correction_id` は #587 が再開されたとき、イベント行の `source_correction_id` を
  `correction_id` に差し替えるだけで、ordinal 由来の脆弱性を継承せずに済む——
  これは§11で人間に確認を仰ぐ「#587 との統合方針」の材料になる）
- **`reflect_status` の意味論変更**: 一切触れない
- **`#379` 新設凍結の解除**: 新しいストアを作らない（§5）
- **有効レコードの述語の完全統合**（§6）: 5箇所の重複を1箇所へ集約する作業は
  規模が本 issue を超えるため別 issue とする
- **`prune/corrections.py` のロック協調**（§9-2）: 既存の欠陥であり本 issue のスコープ外
- **CLI の `--correction-id` オプション新設**（§2.4 で触れた、ユーザーが `correction_id`
  を直接指定できるようにする改善）: round 0 は「レコードを一意に指せること」という
  内部的な性質を要求しているのであって、CLI からの直接指定 UX 改善は Should 相当
  （実装1巡で追加するかは§11で判断）

## 11. 人間の判断が要る点

1. **blocking (a) の解釈**: 「同一 ID を持つレコードが2件以上存在しうる」を
   「発生を完全に防止する」と読むか「発生しても実害が出ないよう無害化する」と読むか。
   本設計は後者を採用した（§2.3）。前者を要求するなら、書込み時に既存ファイル全体を
   スキャンして ID の重複を検出・reject する仕組みが追加で必要になり、
   `append_jsonl` の「ロックを取って追記するだけ」という軽量な書込み経路の性質が変わる
2. **§9-1（バックアップ復元による衝突）からの自動回復**: 本設計は「実害を出さず
   ambiguous として拒否する」までで止めている。人間による重複解消の手順（どちらの
   レコードを残すか、マージするか）を本 issue のスコープに含めるかどうか
3. **§9-2（`prune` のロック未協調）を本 issue で直すか**: 直さない場合、運用上の注意
   （移行スクリプトの実行タイミング）をどこに書くか（README？ 移行スクリプト自身の
   docstring？）
4. **§7.2 のスケーラビリティ**: corrections.jsonl が数万件規模に成長した場合、
   一括読込・一括書き戻し方式の移行スクリプトは再検討が必要になる。今回は241件
   （§1.1）を前提に「全件処理・冪等性で安全化」を採用したが、将来の再設計トリガーを
   件数の閾値として明記するか
5. **#587 との統合順序**: 本設計（`correction_id`）を先にマージし、#587 が
   再開したときに `source_correction_id`/ordinal 依存部分を `correction_id` ベースに
   差し替える、という順序でよいか。あるいは両方を1本の変更系列として扱うか
   （`review.md` の「変更系列はリセットできない・前身の巡数を継承する」規定との整合）
