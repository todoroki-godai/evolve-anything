# #587: 柱2「照合済み反映」を測れるようにする設計（第5版）

> **第4版までで方針転換**。第2版〜第4版は「反映イベントを `corrections.jsonl` の中に
> 新フィールド（`record_kind="reflect_event"`）として追記する」方式だった。この方式は
> `#587`（設計3巡）・`#593`（3巡）・`#595`（2巡）の計8巡のレビューで、いずれも
> **「1つのファイルに複数の writer が全体を上書きしながら群がる」構造**を起点とする指摘に
> 行き着いた（`corrections.jsonl` には hook の新規追記・`update_reflect_status` の全文書換え・
> `invalidate_idiom_corrections` の全文書換えが既に同居しており、そこへ4つ目の書込み経路を
> 足す設計だった）。
>
> **2026-09-01 にユーザーが方針転換を裁定した**:
> 1. 反映イベントは `corrections.jsonl` に入れず、**別ファイルの追記専用ストア**に置く
> 2. `#379` の新設凍結を**このストア1件だけ**解除する（凍結の目的は肥大化防止であり、
>    中核目的である柱2測定のために1件足すのは趣旨に反しない、という判断）
> 3. `corrections.jsonl` の writer 協調（`update_reflect_status` の commit protocol 全面設計）
>    は本 issue では扱わない（`#595` が担当。柱2のブロッカーではなくなった）
>
> **巡数はここから新規系列として扱う。総上限2巡**（理由は issue #587 本文「巡数の再裁定」
> 参照。第2〜4版までの8巡は継承しない）。本文書がその1巡目（設計巡）の対象。
> 第2〜4版の本文は git 履歴（`git log -p -- docs/decisions/drafts/587-pillar2-applied-measurement.md`）
> で参照できる。

対象: `#587`（前身 `#567`）。本文書は**設計のみ**。コードは1行も変更しない（実装は次巡）。

## 0. Round 0 完成条件

### ① 守る対象

1. 柱2として表示する数字が、実際に反映されたものと食い違うこと
2. 「指定した correction を更新した」という報告が事実であること（`#588` から継承）

### ② 信頼境界（誰の能力を脅威に数えるか）

**自分たちの運用ミスのみ**。具体的に数えるのは: 手編集 / 別プロセスの追記（hook）/ 処理の中断 /
同時に走る2つの更新 / 移行スクリプトの未実行。
**数えない**: 悪意ある偽装・意図的な数字の水増し・第三者による改竄。

### ③ 対象外

- 柱2の目標値（3件）の妥当性。2026-08-26 にユーザーが「根拠なしの暫定値」と明記して決めたもの
- **hook / pitfall への反映測定**（記録自体が無く、本設計が新設する1ストアの対象外）
- **memory への反映測定**（`auto_memory_broker` に配線は実在するが実データ188件中1件・別途）
- `results_board.py` への配線と表示（見せ方は別 issue）
- `reflect_status` の意味論そのものの再定義（値域の追加は可、既存値の意味変更は対象外）
- **既存の全文書き換え経路（`update_reflect_status`）の commit protocol 全面設計**
  （`#595` が担当。理由は §2.1 参照）
- **`#379` 新設凍結の全体解除**。本設計が解除するのは新設する1ストア（§2）だけであり、
  凍結の他の3集合（observability section / advisory proposal adapter / weak_signal channel）
  および他の store には一切触れない
- **CLI に `correction_id`/ordinal を明示指定するオプションを追加する**: `--apply
  <source_correction_id>` が複数候補に一致する場合の曖昧性を、ユーザーが明示解決できると
  運用上は便利だが、無くても柱2は測れる（§4.1 手順(a)で `resolve_source_correction_id` が
  `"ambiguous"` を返した場合はイベント行を追記しないだけで、過大計上にはならない）

### ④ blocking の定義

| | 内容 | 出所 | 第5版での扱い |
|---|---|---|---|
| (a) | 反映日時が残らない | #567 巡1 | 解消（§5 イベント行の `reflect_applied_at`） |
| (b) | 反映先の種別が残らない（`CLAUDE.md` と skill が区別できない） | #567 巡1 | 解消（§6 値域拡張） |
| (c) | 同一の反映が複数件に数えられる | #567 巡1 | 解消（§9 fold の重複排除規則） |
| (d) | 無効化済み（idiom revoke）が件数に残る | #567 巡1 | 解消（§10 の invalidate フィルタ） |
| (e) | 照合を通っていない旧レコードが件数に混ざる | #567 巡1 | 解消（§9.1 legacy 判定） |
| (f) | 読取後に有効レコードが挿入・削除されると、別 correction を更新して成功を返す | #588 巡1 [Must]4 | 柱2の集計からは無関係（§2.1）。`update_reflect_status` 自体の修正は `#595` の対象 |
| (g) | 並行する追記が消える／2つの更新が後勝ちで巻き戻る。いずれも成功を返す | #588 巡1 [Must]5 | 同上 |
| (h) | 同一 `source_correction_id` を持つレコードが2件以上あるとき、要求していないレコードまで更新される | tacchi 巡1 [Must]1（#588 別実装） | `correction_id`（#594）が構造的に解消（§4.2） |
| (i) | project scope 判定の値域が実コード `classify_project_scope` の3値と不一致 | 巡2 [Must]1（第3版で解消済・継承） | 解消（§11。第3版で実測し訂正済みの内容を継承） |
| (j) | 反映イベントと基底レコードの二表現が同時に正（dual-write の整合性規則が無い） | 巡3人間承認 未解決4件のうち1件 | **構造的に消滅**（§1 表）。イベントは別ファイルなので `corrections.jsonl` への dual-write 自体が存在しない |
| (k) | `--skip-all` がイベント行を対象に含みうる | 同上 | **構造的に消滅**（§1 表）。イベント行は `corrections.jsonl` に一切存在しないため `load_corrections`/`extract_pending`/`--skip-all` は最初から目にしない |
| (l) | 完成条件の「最終 draft 全文のハッシュ」が schema に無い | 同上 | 解消（§8。第3版で追加済みの `correction_message_sha256` を継承） |

### ⑤ 検証方法

- 陰性試験（赤になるべき）を (a)〜(e) 各1件以上。(h) は #594 の `correction_id.py` 側で
  既に単体テスト（`scripts/lib/tests/test_correction_id.py`）がある構造的解消のため、
  本設計側では「重複 `correction_id` を持つ2レコードが fold で正しく分離される」ことだけを
  追加検証する。(j)(k) は「構造的に発生し得ない」という主張自体を検証する陰性試験を §13 に置く
- 陽性対照を対で置く。陰性試験と混ぜて数えない
- 委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する。
  緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する

## 1. 第4版から削ったもの（別ストア化で不要になったもの）

| 削った内容 | 理由 |
|---|---|
| `record_kind` フィールド（基底レコードとイベント行を同一ファイル内で区別する discriminator） | イベントが物理的に別ファイルにあるため、区別する必要自体が無くなった |
| §1.2「読み手のうち3箇所は要修正」（`load_corrections`/`issues_summary.py`/`discover/suppression.py` へのガード追加） | いずれも `corrections.jsonl` しか読まない。イベント行はそこに存在しないため、これらの読み手は変更不要のまま自動的にイベント行を見ない（(k) の解消と同根） |
| `schema_version != 1` を握りつぶす際の「基底レコードと混ざっているかもしれない」という前提の分岐 | 混ざりようがない（別ファイル）ので fold のロジックが単純化される |
| `--apply`/`--skip` が `update_reflect_status` の**戻り値が `"applied"` のときだけ**イベント追記する、という順序制御の必要性そのものは残るが、「同じファイルへの書込み順序が worker 間でずれるとどうなるか」という族の懸念（巡2〜3で継続的に指摘された） | 書込み先が別ファイル・別ロックになったため、`corrections.jsonl` 側の同時書込み（hook の新規追記・`update_reflect_status` の全文書換え）とイベント追記のロックが完全に独立する。ロック競合そのものが構造的に起きない |
| dual-write 整合性規則（rev587dr1 [Must]4・巡3人間承認の未解決4件の1つ） | dual-write 自体が存在しない（`corrections.jsonl` は一切書き換えない） |
| `append_correction_record` を「そのまま使う」という前提 | 同関数は `guard_problem("corrections.jsonl")` を**ハードコードしている**（`scripts/lib/rl_common/correction_id.py:50,60`・2026-09-01 実測）ため、別ファイルへの追記にはそのまま使えない。§3 で汎化する |

**残したもの**（別ストア化と無関係に有効な設計要素。第3版から継承・再検証済み）: `correction_id` による識別（§4.2）／`reflect_target_kind` の値域拡張（§6）／path 正規化（§7）／`correction_message_sha256`（§8）／`classify_project_scope` の値域（§11）。

## 2. 新ストアの定義

### 2.1 なぜ別ファイルが `update_reflect_status` の安全性と無関係になるか

**設計判断（根拠つき）**: `--apply`/`--skip` ハンドラは現行どおり
`update_reflect_status(corrections_file, [target_index], "applied", ...)` を呼び続ける
（`corrections.jsonl` 側の全文書換え経路そのものには一切触れない。`#595` の担当）。
**その呼出しが成功したとき「だけ」**、追加の独立した操作として、新ストアへイベント行を
1件追記する。

両者が別ファイル・別ロックであることから:

1. **柱2の集計は新ストアだけを正とする**。`corrections.jsonl` 側の
   `reflect_status`（既存 UI 互換のため書き込み続ける）と新ストアのイベント行は
   **同じファイルに存在しないため、dual-write という概念自体が発生しない**
   （旧 blocking (j) の解消）
2. **`update_reflect_status` 自身の commit protocol（(f)(g)(h)）は柱2の完成条件と無関係のまま**。
   `prune`/`revoke`/`migration`/`invalidation`/`backfill` を横断する共有 lock 契約の設計は
   `#595` が引き続き担当する
3. **`corrections.jsonl` 自身の書式・内容は一切変更しない**（新フィールドの追加も無い）。
   `load_corrections`/`extract_pending`/`--skip-all`/`issues_summary.py`/
   `discover/suppression.py` はいずれもコード変更なしでイベント行を混入させずに動く
   （旧 blocking (k) の解消。§1 表）

**この方針で失うもの**は第3版から変わらない: 「`--apply` が『反映した』と報告したのに、
実際は競合で別レコードが壊れていた」（(f)(g)(h) の直接的な害）は本設計では直らない。
これは元々 `reflect_status` フィールド自体の信頼性の問題であり、柱2（本 issue）の完成条件①
には効かない（柱2は新ストアのイベント行しか見ない）。完成条件①-2「指定した correction を
更新したという報告が事実であること」は `update_reflect_status` 自体の話であり、`#595` の
スコープである。この点は §14「残る限界」に明記する。

### 2.2 ストア名・置き場所

- **basename**: `reflect_apply_events.jsonl`（**頭の裁定・2026-09-01**。他候補
  `reflect_events.jsonl`（`--skip` の `correction_skipped` も含むため厳密には "apply" だけ
  ではないが、主目的が柱2＝反映件数であることを名前に残す）／`pillar2_events.jsonl`
  は PJ 内部の呼び名（柱2）であって外部から読める語彙でないため不採用、を検討した上で採用した）
- **置き場所**: `rl_common.DATA_DIR / "reflect_apply_events.jsonl"`（`store_write` が解決する
  canonical DATA_DIR と同じ規約。既存ストアと同じ階層に並ぶ）
- `reflect.py` 側の `CORRECTIONS_FILE`（`skills/reflect/scripts/reflect.py:50`）は
  `Path.home() / ".claude" / "evolve-anything" / "corrections.jsonl"` という**home 直書き**
  で、`rl_common.DATA_DIR`（`CLAUDE_PLUGIN_DATA` 環境変数を優先する解決）を経由していない
  既存の不整合（実装時点で通常は一致するが、`CLAUDE_PLUGIN_DATA` 設定時に乖離しうる）。
  **この不整合の是正は本設計の対象外**（`corrections.jsonl` 自体の解決方法を変える話であり、
  新ストアの新設とは独立の問題）。新ストアのパス解決は `rl_common.DATA_DIR` 経由に統一する
  （home 直書きの新規踏襲はしない）

### 2.3 版数・一意キー

- 行スキーマは `schema_version` フィールドを持つ（現在値 `1`）。将来のフィールド追加・
  意味変更時に fold 側が版で分岐できるようにする（第3版から継承）
- 一意キーは**イベント行自身の `correction_id`**（32桁hex・`new_correction_id()` で新規発行）。
  基底レコードを指す `target_correction_id` とは別フィールド（§4.2）

### 2.4 追記の唯一境界

**§3 で汎化する `append_unique_record`（新ストア名と filepath を明示的に受け取る版）を
唯一の追記口とする**。`corrections.jsonl` 側の3つの書込み経路（hook 新規追記・
`update_reflect_status` 全文書換え・`invalidate_idiom_corrections` 全文書換え）とは
物理的に別ファイル・別 `fcntl.flock` になるため、新ストアへの追記がそれらと競合すること
自体が構造的に起きない。

## 3. 追記境界の汎化（`append_correction_record` の一般化）

`scripts/lib/rl_common/correction_id.py:50-77` の `append_correction_record` は
`guard_problem("corrections.jsonl")` を関数内にハードコードしており、**別ファイルへの
追記にはそのまま呼べない**（第4版までの「そのまま使う」という記述は誤りだった。§1 表）。

**設計**: 同モジュールに `store_name: str` と `filepath: Path` を明示引数に取る汎化版
`append_unique_record` を追加し、既存 `append_correction_record` はその薄いラッパーへ
書き換える（既存シグネチャ・既存呼出元の挙動は一切変えない — 内部実装の抽出のみ）:

```python
# scripts/lib/rl_common/correction_id.py（設計・未実装）

def append_unique_record(filepath: Path, store_name: str, record: dict) -> AppendResult:
    """任意の store_name/filepath への、correction_id 重複拒否つき唯一の追記境界。

    append_correction_record と同じロック（persistence.append_jsonl の fcntl.flock）・
    重複拒否（has_duplicate_id）・write barrier 照合（store_write.guard_problem）を
    store_name/filepath だけ汎化したもの。新しいロック機構は増やさない。
    """
    if not persistence._HAVE_FCNTL:
        return AppendResult(
            status="unsupported_platform",
            reason="fcntl unavailable: unique append is not supported",
        )
    from .store_write import guard_problem

    problem = guard_problem(store_name)
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    result = persistence.append_jsonl(
        Path(filepath),
        record,
        duplicate_check=lambda existing: has_duplicate_id(existing, correction_id),
    )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)


def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """corrections.jsonl 専用の従来どおりの窓口（既存呼出元は無改修）。"""
    return append_unique_record(filepath, "corrections.jsonl", record)
```

**新ストアの呼出し例**（`reflect.py` の `--apply` ハンドラ側）:

```python
append_unique_record(
    rl_common.DATA_DIR / "reflect_apply_events.jsonl",
    "reflect_apply_events.jsonl",
    event_record,
)
```

**これは #594 の関数を「そのまま使う」ではなく「内部実装を抽出して汎化する」変更である**
（第4版の記述の訂正）。既存の `append_correction_record` 呼出元（`hooks/correction_detect.py`
等）はシグネチャ・戻り値とも無変更のため回帰は起きない想定だが、**実装1巡で
`test_correction_id.py` の既存テストが無改修のまま全緑であることを確認する**（§13 完了条件）。

## 4. 凍結解除の手順

### 4.1 何を変えるか（機械的に確認できる形にする）

`#379` の新設凍結は3層で強制されている（`shrink_freeze.py` 冒頭コメント・CLAUDE.md「新設凍結」節）。
「1件だけ解除」は次の**2ファイルの各1行追加**だけで完結する。

1. **`scripts/lib/store_registry.py`**: `_DECLARATIONS` リストへ1件追加

   ```python
   StoreDeclaration(
       name="reflect_apply_events.jsonl",
       writer="skills/reflect/scripts/reflect.py --apply/--skip ハンドラ（柱2反映イベント）",
       reader="scripts/lib/reflect_fold.py・scripts/lib/pillar2_metrics.py",
       retention="permanent",
       classification="raw_event",
       writer_locus="batch",
       note=(
           "#587 柱2測定用。#379 新設凍結の例外としてユーザー裁定（2026-09-01）で"
           "追加。corrections.jsonl とは別ファイル・別ロック。"
       ),
   )
   ```

2. **`scripts/lib/shrink_freeze.py:62-` `FROZEN_STORES`**: 同じ basename を1件追加し、
   **なぜ凍結中にもかかわらず追加してよいかの理由コメントを直上に置く**（既存の
   「round2 で4件追加」等の先例コメントと同じ書式に揃える）:

   ```python
   # #587（2026-09-01 ユーザー裁定）: 柱2「反映が測れない」問題の唯一の解決策として、
   # 新設凍結の例外を1件だけ認める。凍結の目的は肥大化防止であり、中核目的（柱2測定）の
   # ための1件はその趣旨に反しないという判断。他の新設は引き続き凍結対象。
   "reflect_apply_events.jsonl",
   ```

**なぜこれで「1件だけ」であることが機械的に確認できるか**: `test_shrink_freeze.py` の
`test_store_registry_no_new_names`（`scripts/lib/tests/test_shrink_freeze.py:63-67`）は
`store_registry.declared_store_names()` の**現在の全集合**が `FROZEN_STORES`（スナップショット）
の部分集合であることを CI で強制する。**`FROZEN_STORES` へ2件以上追加すれば、`store_registry`
側にそれ以上の新規宣言が無い限り「宣言していないキーが凍結スナップショットにだけ存在する」
non-issue にしかならない**——つまりこのテスト自体は「凍結スナップショットへの追加数」を
直接は数えない。**「1件だけ」であることの機械的な担保は、実装1巡の PR diff そのもの**
（`shrink_freeze.py` の diff が `+1行` であること）と、次項 §4.2 で追加する**専用の
契約テスト**が担う。

**専用の契約テスト**（実装1巡で追加。§13 に also 記載）: `FROZEN_STORES - <#379 Step1
実装時点のスナップショット（本設計時点の現行値）> == {"reflect_apply_events.jsonl"}` を
assert する。次に別のストアが同じ手口で追加されようとしたとき、この diff テストが
`{"reflect_apply_events.jsonl", "<新規名>"}` という2件差分で失敗し、「例外が増えている」
ことを機械的に検出する。

### 4.2 CLAUDE.md の同時更新

プロジェクトの決まり（「コードだけ直して終わりは禁止」）に従い、CLAUDE.md の
「新設凍結（#379 Step 1）」節（`CLAUDE.md:45`）へ例外を明記する。現行文言:

> 新設凍結（#379 Step 1）: 縮小完了まで新 store / observability section / advisory
> proposal adapter / weak_signal channel の追加は停止する（削除は許容）。単一ソースは
> `scripts/lib/shrink_freeze.py`。(以下略)

**追記案**（既存文の末尾に1文追加。文言そのものの意味は変えない — 例外の存在を明記するだけ）:

> （中略）。**例外1件**: `reflect_apply_events.jsonl`（柱2反映イベント専用ストア。
> `#587`・ユーザー裁定2026-09-01）のみ、凍結中でも追加済み。他の新設は引き続き停止対象。

### 4.3 store_registry への登録の効果

`store_write()` は `shrink_freeze` を直接は参照せず、`store_registry.declaration_for()` の
`status == "active"` のみで書込み可否を判定する（`scripts/lib/rl_common/store_write.py:82-108`・
2026-09-01 実測）。したがって §4.1 の2ファイル更新後は、`store_write("reflect_apply_events.jsonl",
record)` は動くようになるが、**本設計は §3 の `append_unique_record` を経由する**（`store_write`
は `duplicate_check` を渡さない汎用 API のため、重複拒否が要る本ストアには使わない）。

`store_write_raw()` 側の凍結ゲート（`_raw_freeze_problem`・`store_write.py:114-144`）は
`shrink_freeze.FROZEN_STORES ∪ store_registry.declared_store_names()` の**和集合**で
既知ストアを判定するため、§4.1 の2ファイルどちらか片方だけを更新しても未知扱いにはならない
（冗長化されている）。ただし §4.1 は両方を更新する（`declared_store_names()` だけを更新すると
`test_store_registry_no_new_names` が CI で赤くなるため、実務上どのみち両方が必要）。

## 5. 追記イベント行のスキーマ

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `correction_id` | str（32桁hex） | ○ | **このイベント行自身の不変ID**（`new_correction_id()` で新規発行）。同一 store 内での重複追記防止に使う一意キー（§2.3・§3） |
| `schema_version` | int（現在値 `1`） | ○ | 将来のフィールド追加・意味変更時に fold 側が版で分岐できるようにする |
| `event_type` | `"correction_applied"` \| `"correction_skipped"` | ○ | イベント種別 |
| `target_correction_id` | str（32桁hex） | ○ | 対象の基底レコード（`corrections.jsonl` 側）の `correction_id`（§4.2）。fold はこのキーだけでグルーピングする |
| `reflect_applied_at` | str (ISO8601 UTC) | `event_type=="correction_applied"` のみ | **`--apply` を実行した時刻**（ファイル編集時刻ではない。フィールド名・本コメントの両方で明示する） |
| `reflect_target_kind` | §6 の値域 | 同上 | §6 |
| `reflect_target_path` | str | 同上 | 反映先ファイルの**正規化後**パス（§7） |
| `reflect_draft_line` | str | 同上 | 起草行の全文（正規化前・§8 の照合対象） |
| `correction_message_sha256` | str (SHA-256 hex) | 同上 | 完成条件の全文hash要件（round 0 出所・§0④(l)）。対象 correction の `extracted_learning`（無ければ `message`）の**正規化後全文**の SHA-256。正規化: 前後空白除去 + 改行を `\n` へ統一（CRLF→LF）+ NFC Unicode 正規化。照合時点: イベント追記時に対象 correction の当該フィールドから直接計算する |

**`record_kind` フィールドは持たない**（§1 表。別ファイルなので discriminator が不要）。
**`reflect_status` フィールドも持たない**（そもそも `corrections.jsonl` の語彙であり、
この新ストアには存在しない概念）。

## 6. イベント行の追記タイミングと呼出契約

`reflect.py` の `--apply` ハンドラ（現行 `reflect.py:1274-1364`）を次のように拡張する
（`update_reflect_status` 自体は変更しない）:

1. 現行どおり `update_reflect_status(corrections_file, [target_index], "applied", ...)` を呼ぶ
2. **戻り値が `{"status": "applied", ...}` のときだけ**、追加で以下を行う:
   a. 対象レコードの `correction_id` を取得する。取得元は現行の `target_index` 探索
      （`reflect.py:1305-1312`、`make_source_correction_id` による先頭一致）が指すレコード
      **ではなく**、`resolve_source_correction_id`（`reflect.py:127-153`・#594 で追加済み・
      読取専用）を先に呼んで解決する。**`resolve_source_correction_id` が `"ambiguous"` を
      返した場合、イベント行は追記せず `{"status": "ambiguous_source", ...}` を返して
      非0終了する**（現行 `target_index` 探索の「先頭一致で確定」動作自体は変更しない
      ——`update_reflect_status` へ渡す index は従来どおり——が、イベント行の紐付けだけは
      曖昧なら諦めるという非対称な安全策。理由: `target_index` 側の是正は `#595` の
      commit protocol 全面設計を要するため本 issue のスコープに入らないが、イベント行の
      追記は新規の独立した操作なので、ここでだけ fail-closed にできる）
   b. `correction_id` が取得できたら、§3 の `append_unique_record` で §5 のイベント行を
      `rl_common.DATA_DIR / "reflect_apply_events.jsonl"` へ追記する
   c. 追記が `{"status": "appended"}` 以外（`"duplicate_id"`/`"unsupported_platform"`/
      `"unregistered_store"`/`"retry_required"`）を返した場合、`--apply` の JSON 応答に
      `"pillar2_event"` キーとしてその結果を含める（黙って握り潰さない）。ただし
      **`reflect_status` の更新自体（手順1）はイベント追記の成否と独立に成功したまま返す**
      （柱2の記録失敗を理由に、既存の `--apply` の主機能を失敗扱いにしない）

**`--skip` も同型の `correction_skipped` イベントを追記する**。ただし §0③により
柱2の集計対象ではなく、監査証跡としてのみ持つ。

**`--skip-all` は本設計の対象外**（旧 blocking (k)。§1 表・§0④）。`--skip-all` は
`corrections.jsonl` 側の pending index を一括処理する既存経路であり、新ストアには
一切書込まない（本節の手順2はあくまで単発の `--apply`/`--skip` ハンドラの拡張であり、
`--skip-all` はそもそも「反映した」という主張を伴わないため §0① の対象にもならない）。

## 7. 識別子は `correction_id` のみ

（第3版から継承・再検証済み）第4版までに採用していた `(source_correction_id, ordinal)`
複合キーは使わない。`correction_id` は #594 により:

- 新規レコードには検出時（`hooks/correction_detect.py:135`）に必ず付与される
- 既存レコードには `scripts/migrate_correction_id_backfill.py` が一度だけのバックフィルを
  提供する（**未実行環境が成立する**——この場合の扱いは§9.1で扱う）
- **重複を構造的に検出できる**（`find_duplicate_ids`・`has_duplicate_id`）
- **位置に依存しない**（物理行番号でも `load_corrections` の配列 index でもない）

イベント行は `target_correction_id` で基底レコードを参照する。fold（§9）は
`target_correction_id` の値だけでグルーピングする。**`source_correction_id`（既存の
`session_id`+`timestamp` 複合キー。`reflect.py:890` 等で既に使われている別概念）とは
フィールド名を明確に分ける（第2版の混同を継承しない）。

**既存レコードに `correction_id` が無い場合（バックフィル未実行）**: §6 手順(a)の
`resolve_source_correction_id` は `candidates[0].get("correction_id")` が `None` のとき
`resolve_correction_id(records, None)` を呼び、`validate_correction_id(None)` が `False` を
返すため `{"status": "invalid_id"}` になる。この場合もイベント行は追記せず
`{"status": "unmigrated_source", ...}` を返す（**fail-closed**。静かに古い方式へ
フォールバックしない）。

## 8. correction とイベントの紐付け強度

（第3版から継承）部分文字列一致（`check_line_applied` の再利用のみ）は不採用にする:

- `check_line_applied`（`scripts/lib/reflect_apply_match.py:49`）による「draft_line が対象
  ファイルに存在するか」の確認は**そのまま維持する**（`update_reflect_status` の既存契約）
- **追加で**、イベント行に `correction_message_sha256`（§5）を持たせることで、
  「このイベントがどの correction の内容に対応するか」を**correction 本文のハッシュで
  固定する**。fold 側では使わない（監査用）が、§13 の陰性試験で「別の無関係な correction の
  内容から偶然同じ draft_line が作れても、`correction_message_sha256` が対象 correction の
  内容と一致しないことを検出できる」ことを検証する
- **人間判断は残る**（§15）: 「言い換え」を許容するかどうか（`extracted_learning` の
  意訳が draft_line と完全一致しないケース）は本設計では解決しない

## 9. read 時 fold の擬似コード

新規共有モジュール **`scripts/lib/reflect_fold.py`** を作る（`results_board.py` にも
`reflect.py` にも置かない。読み手が複数箇所に分散しており、`reflect.py` に置くと逆方向の
依存になり不自然なため）。

**別ストア化により、第4版の `fold_corrections(raw_records: list)` という単一引数シグネチャは
成立しなくなる**（基底レコードとイベントが別ファイル由来のため）。**シグネチャを
`fold_corrections(base_records, event_records)` の2引数へ変更する**（第4版からの変更点。
`record_kind` による分岐が不要になった分、関数本体はむしろ単純化する）。

```python
# scripts/lib/reflect_fold.py（設計・未実装）
from dataclasses import dataclass
from typing import Optional

@dataclass
class FoldedCorrection:
    base: dict                          # 基底レコードそのもの（既存フィールドは無改変で保持）
    reflect_applied_at: Optional[str] = None
    reflect_target_kind: Optional[str] = None
    reflect_target_path: Optional[str] = None
    reflect_draft_line: Optional[str] = None
    correction_message_sha256: Optional[str] = None
    has_pillar2_fields: bool = False    # legacy 判定（blocking e）に使う


@dataclass
class FoldHealth:
    orphan_events: int = 0          # target_correction_id が見つからないイベント
    unknown_schema_events: int = 0  # schema_version != 1 のイベント


def fold_corrections(
    base_records: list, event_records: list
) -> tuple[list[FoldedCorrection], FoldHealth]:
    """corrections.jsonl の基底レコード列と reflect_apply_events.jsonl のイベント列を
    correction_id/target_correction_id で結合し、基底単位に畳む。

    base_records・event_records はいずれも「読取失敗しなかった生の json.loads 結果」の列
    でよい（順序は不問。ordinal を使わないため、ファイル出現順である必要が無い）。
    """
    bases_by_id: dict[str, dict] = {}          # correction_id -> 基底レコード
    order: list[str] = []                       # 出現順（表示の安定性のためだけに保持）

    for rec in base_records:
        if not isinstance(rec, dict):
            continue
        cid = rec.get("correction_id")
        if not isinstance(cid, str) or not cid:
            continue  # correction_id 未付与の基底レコード（バックフィル未実行）は fold 対象外
        if cid not in bases_by_id:
            order.append(cid)
        bases_by_id[cid] = rec  # 同一 correction_id の重複は最後を正とする（構造的に稀）

    folded_by_id = {cid: FoldedCorrection(base=bases_by_id[cid]) for cid in order}
    health = FoldHealth()

    latest_applied_event: dict[str, dict] = {}
    for ev in event_records:
        if not isinstance(ev, dict):
            continue
        if ev.get("event_type") != "correction_applied":
            continue
        if ev.get("schema_version") != 1:
            health.unknown_schema_events += 1
            continue
        target_id = ev.get("target_correction_id")
        if target_id not in folded_by_id:
            health.orphan_events += 1  # 基底が fold 対象に無い（未 backfill・decay 削除済み等）
            continue
        # ファイル出現順で後のものが「最新」。fold は読取専用の状態導出であり書込みではない
        # ため②信頼境界の対象外。
        latest_applied_event[target_id] = ev

    for cid, ev in latest_applied_event.items():
        f = folded_by_id[cid]
        f.reflect_applied_at = ev.get("reflect_applied_at")
        f.reflect_target_kind = ev.get("reflect_target_kind")
        f.reflect_target_path = ev.get("reflect_target_path")
        f.reflect_draft_line = ev.get("reflect_draft_line")
        f.correction_message_sha256 = ev.get("correction_message_sha256")
        f.has_pillar2_fields = bool(f.reflect_applied_at and f.reflect_target_kind)

    return [folded_by_id[cid] for cid in order], health
```

### 9.1 legacy 判定（blocking e）

`correction_id` はあるが対応する `correction_applied` イベントが無い基底レコード
（`has_pillar2_fields=False`）は「照合を通っていない旧レコード」として §10 の
`legacy_unverified_count` に分類され、`count`（分子）には含めない。

## 10. `count_applied_reflections`（設計のみ・実装は次巡）

`scripts/lib/pillar2_metrics.py`（新規モジュール）が `reflect_fold.fold_corrections` を呼ぶ。
**raw record の取得は新規実装せず、`fleet.queue_materials.read_corrections_records_with_health`
を再利用する**——この関数は名前に反して**`corrections_path: Path` を明示引数に取る汎用実装**
であり（`scripts/lib/fleet/queue_materials.py:227-`、本体に `corrections.jsonl` という
basename のハードコードは無い・2026-09-01 実測）、**新ストアの読取りにもそのまま再利用できる**
（第4版時点では「新規実装しない」とだけ書かれ、この汎用性の確認が未実施だった。第5版で確認済み）。

```python
def count_applied_reflections(
    slug: str, *, corrections_path=None, events_path=None, now=None, window_days: int = 30
) -> dict:
    from fleet.queue_materials import read_corrections_records_with_health
    import rl_common

    base_records, base_health = read_corrections_records_with_health(
        corrections_path or (rl_common.DATA_DIR / "corrections.jsonl")
    )
    event_records, event_health = read_corrections_records_with_health(
        events_path or (rl_common.DATA_DIR / "reflect_apply_events.jsonl")
    )
    folded, fold_health = fold_corrections(base_records, event_records)
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    eligible = []
    legacy_unverified = 0
    invalidated_count = 0
    other_kind_count = 0
    for f in folded:
        if f.base.get("invalidated"):
            invalidated_count += 1
            continue  # blocking d
        if not f.has_pillar2_fields:
            legacy_unverified += 1  # blocking e
            continue
        if f.reflect_target_kind == "other":
            other_kind_count += 1
            continue
        ts = _parse_timestamp(f.reflect_applied_at)  # results_board._parse_timestamp を import
        if ts is None or not (window_start <= ts <= now):
            continue
        scope = classify_project_scope(f.base, slug)
        if scope not in ("same-project", "global-looking"):  # §11 の値域
            continue
        eligible.append(f)

    # blocking c の残り半分: 別 correction が同じ実世界の反映を指している場合の重複。
    groups: dict[tuple, list] = {}
    for f in eligible:
        key = (f.reflect_target_kind, f.reflect_target_path, _normalize_plain(f.reflect_draft_line))
        groups.setdefault(key, []).append(f)

    count = len(groups)
    applied_list = [
        {
            "target_kind": k[0], "target_path": k[1],
            "reflect_applied_at": min(x.reflect_applied_at for x in v),
        }
        for k, v in groups.items()
    ][:10]

    degraded = (
        not base_health["readable"]
        or not event_health["readable"]
        or base_health["malformed_lines"] > 0
        or event_health["malformed_lines"] > 0
        or fold_health.orphan_events > 0
        or fold_health.unknown_schema_events > 0
    )

    return {
        "count": count,
        "legacy_unverified_count": legacy_unverified,
        "invalidated_count": invalidated_count,
        "other_kind_count": other_kind_count,
        "applied_list": applied_list,
        "health": {
            "degraded": degraded,
            "base_readable": base_health["readable"],
            "base_read_error": base_health["error"],
            "base_malformed_lines": base_health["malformed_lines"],
            "events_readable": event_health["readable"],
            "events_read_error": event_health["error"],
            "events_malformed_lines": event_health["malformed_lines"],
            "orphan_events": fold_health.orphan_events,
            "unknown_schema_events": fold_health.unknown_schema_events,
        },
        "not_measured": {
            "hook": {"reason": "no_store"},
            "pitfall_memory": {"reason": "mtime_collision"},
        },
        "generated_at": now.isoformat(),
    }
```

**`_parse_timestamp` は import して再利用するが `_in_window` は再利用しない**
（第2版から継承）: `results_board.py:250-264` `_in_window` は `record.get("timestamp")` を
固定フィールド名で読む。pillar2 は `reflect_applied_at` という別フィールド名を窓判定する
ため `_in_window` をそのまま呼べない。`_parse_timestamp`（汎用・フィールド名を引数に取らない
値パーサ）だけを import し、窓判定は `pillar2_metrics.py` 側にインラインで書く。

**`results_board.py` への配線は本 issue のスコープ外**（§0対象外）。

## 11. `classify_project_scope` の再利用

（第3版から継承・2026-09-01 再実測で一致確認済み）`classify_project_scope`
（`reflect.py:169-201`）の実際の戻り値は `"same-project"` / `"global-looking"` /
`"project-specific-other"` の3値である（`reflect.py:169-201` 実測。旧 blocking (i)）。

**採用する値域**: `scope in ("same-project", "global-looking")` を対象とする
（`"project-specific-other"` は除外）。理由: `global-looking` は「別 PJ 由来だが汎用的な
内容」を指し、CLAUDE.md の柱2定義「反映先は rule に限らない」の対象として妥当。
`project-specific-other`（DB名やファイルパスを含む PJ 固有の内容）は他 PJ での反映が
柱2の趣旨とずれるため除外する。

**`slug` の絞り込み規則**: `classify_project_scope` の第2引数 `current_project` には
**リポジトリの絶対パス**を渡す（`reflect.py:1498` の既存呼び出しと同じ形）。
`pillar2_metrics.count_applied_reflections` の `slug` 引数は**将来の複数 PJ 横断表示のための
予約**であり、本設計の `classify_project_scope` 呼び出しでは直接使わない。

## 12. 移行

### 12.1 既存データ（`correction_id` が無い、または pillar2 イベントが無い `applied` レコード）

- `correction_id` を持たない基底レコード（`migrate_correction_id_backfill.py` 未実行）は
  `fold_corrections` の対象外になる（§9 擬似コード）。移行スクリプトの実行はこの設計の
  前提ではない——未実行でも「柱2に含まれない」という安全側に倒れるだけ
- `correction_id` はあるが pillar2 イベントが無い既存 `applied` レコードは
  `has_pillar2_fields=False` のままなので `legacy_unverified_count` に分類される（blocking e）
- `migrate_correction_id_backfill.py` を実行するかどうかの判断は本設計の対象外
  （既に main にマージ済みの独立したツールであり、実行の要否は実装1巡または運用判断に委ねる）

### 12.2 `prune/corrections.py` の decay 削除との相互作用

`scripts/lib/prune/corrections.py:105-115` `cleanup_corrections` は `corrections.jsonl` の
`reflect_status in ("applied", "skipped")` かつ decay 超過のレコードを物理削除する。

**別ストア化による変化（第4版からの改善点）**: `cleanup_corrections`・`load_corrections`
（`prune/corrections.py:17-48`）は `corrections.jsonl` しか読まない。新ストア
`reflect_apply_events.jsonl` は物理的に別ファイルのため、**`record_kind` を認識するか
どうかという分岐自体が不要になった**（第4版はこの分岐の有無を気にする必要があったが、
第5版では「別ファイルだから触れない」という自明な事実に置き換わる）。

基底レコードが decay で物理削除されると、対応する新ストアのイベント行は残り続け、
`fold_corrections` は `target_correction_id` が指す基底が無いオーファンイベントとして扱う
（§9 の `orphan_events` カウント）。**この相互作用の完全解消**（イベント行にも decay を
適用する、または基底削除時にイベントも連動削除する）は round 0 対象外とする。柱2の測定窓は
既定30日、prune の既定 decay は90日であり、基底が decay で消える頃には測定窓をとうに
外れているため、実害は小さい。**未実測**: `decay_days` のカスタム設定が実運用に存在するかは
未確認（§14）。

### 12.3 読み手（`load_corrections`・`issues_summary.py`・`discover/suppression.py`）への影響

**別ストア化により、第4版 §4.3「3箇所が要修正」は全て不要になった**（§1 表）。
`corrections.jsonl` を読むこれら3箇所（および肯定一致述語で自然除外される
`correction_backlog.py`/`audit/memory.py`/`optimize_core.py` の3箇所、計6箇所）は
**イベント行を一度も目にしない**（別ファイルのため）。**変更不要**。

## 13. 検証計画

各陰性試験に「壊す不変条件」と「通したい検査経路」を書く。同じ変異を陰性/陽性で使い回さない。
テストは `scripts/lib/tests/test_reflect_fold.py`（fold 単体）・
`scripts/lib/tests/test_pillar2_metrics.py`（集計）・
`scripts/lib/tests/test_correction_id.py`（§3 の `append_unique_record` 追加分）・
`scripts/lib/tests/test_shrink_freeze.py`（§4.1 の専用契約テスト追加分）・
`skills/reflect/scripts/tests/test_reflect_apply_event.py`（§6 のイベント追記）に新設する。

| # | 壊す不変条件 | 変異 | 通したい検査経路 | 期待結果 |
|---|---|---|---|---|
| (a) 陰性1（フィールド欠落） | 反映日時が集計に使われる | fixture イベント行から `reflect_applied_at` を削除 | `has_pillar2_fields` 判定 | legacy 扱いに落ちる |
| (a) 陰性2（誤ったフィールドの窓判定） | 窓判定に `timestamp`（検出時刻）でなく `reflect_applied_at` が使われる | `timestamp`=窓内・`reflect_applied_at`=窓外の fixture と、その逆を対で用意 | `count_applied_reflections` の窓判定 | 前者は `count` から除外、後者は `count` に含まれる |
| (a) 陽性対照 | 同上 | `reflect_applied_at` を window 内の妥当な値のまま残す | 同上 | `count` に1件として残る |
| (b) 陰性1（フィールド欠落） | 反映先種別が区別される | `reflect_target_kind` を欠落させたイベント fixture | `has_pillar2_fields` 判定 | legacy 扱いに落ちる |
| (b) 陰性2（CLAUDE.md誤分類） | `CLAUDE.md` が測定対象として分類される | `target_path=/repo/CLAUDE.md` を `classify_target_kind` に通す | §6 の分類ロジック | `"project_claude_md"` を返す |
| (b) 陰性3（skill誤分類） | skill が `"other"` に落ちて非測定対象になる | `target_path=.claude/skills/foo/SKILL.md` | 同上 | `"skill"` を返す |
| (b) 陽性対照 | 同上 | `reflect_target_kind="project_rule"` を持つ正常 fixture | 同上 | `count` に含まれる |
| (c) 陰性1（同一 base への重複イベント） | 反映は1状態として数える | 同一 `target_correction_id` に対し `correction_applied` イベントを2行、**別ファイル（新ストア）へ**追記した fixture | `fold_corrections` の latest 選択 | `count == 1` |
| (c) 陽性対照1 | 同上 | イベント1行のみの正常 fixture | 同上 | `count == 1` |
| (c) 陰性2（path別名の偽陽性） | 同一物理ファイルの相対/絶対パス表記違いを別グループにする | 同一ファイルを指す `reflect_target_path` の相対表記版・絶対表記版を持つ2イベント fixture | §7 の正規化 | `count == 1` |
| (c) 陽性対照2 | 同上 | 実際に異なる2ファイルへの反映 | 同上 | `count == 2` |
| (c) 陰性3（正当な再反映の偽陰性） | 削除後の正当な再反映を1件に潰す | 同一 target_path/draft_line で `reflect_applied_at` が異なる2件（別 correction 由来）を fixture 化 | グルーピングの代表時刻選択 | `count == 1` になることを確認した上で、`applied_list` の `reflect_applied_at` が `min` になることを固定する（既知の限界。§14） |
| (d) 陰性 | 無効化済みは数えない | `invalidated=True` の基底 fixture（有効なイベントも付与） | invalidate フィルタ | `count` から除外、`invalidated_count` に計上 |
| (d) 陽性対照 | 同上 | `invalidated` キー自体が無い正常 applied fixture | 同上 | `count` に含まれる |
| (e) 陰性 | 旧レコードは数えない | `correction_id` はあるがイベント行が**新ストアのどこにも**無い fixture（実データ同型） | legacy 判定 | `legacy_unverified_count` に入り `count` には入らない |
| (e) 陽性対照 | 同上 | イベント行を伴う正常 fixture | 同上 | `count` に入る |
| (h) 陰性（`correction_id` 重複時の fold 分離） | 重複 `correction_id` の基底が2件あっても、それぞれ独立に扱われる | `find_duplicate_ids` が検出する重複 fixture を `fold_corrections` に通す | fold のグルーピング | 例外を出さず、`bases_by_id` が最後の1件を正として fold される |
| **(j) 陰性（構造的消滅の検証）** | dual-write 整合性違反が起きない、という主張自体を壊してみる | `corrections.jsonl` の基底レコードへ**手編集で** `record_kind="reflect_event"` 相当のキーを混入させても（旧設計の再現を模す）、`load_corrections` は `record_kind` を一切見ないため単なる無害な追加フィールドとして無視される | `reflect.py` の `load_corrections`（§12.3。コード変更なしの主張） | 追加フィールドが `extract_pending`/`--apply`/`--skip-all` のいずれの判定にも影響しない（既存テスト全緑のまま） |
| **(k) 陰性（構造的消滅の検証）** | `--skip-all` がイベント行を対象に含む | 新ストア `reflect_apply_events.jsonl` に大量のイベント行を追記した状態で `--skip-all` を実行する fixture | `--skip-all` の対象抽出（`corrections.jsonl` のみを読む） | `reflect_apply_events.jsonl` の内容が `--skip-all` 実行前後で不変（`update_reflect_status` は呼ばれない・イベント行数が変わらない） |
| Must(i) (project scope) 陰性 | 値域不一致で全件除外される | `scope="same-project"` の fixture を実際の `classify_project_scope` 出力と突き合わせる | §11 の値域 | 存在しない値と比較する実装なら `count == 0` になり検出できる |
| Must(i) (project scope) 陽性対照 | 同上 | `scope="project-specific-other"` の fixture | 同上 | `count` から除外される（除外の意図どおり） |
| 欠陥(欠陥3系) 陰性（偶然一致） | draft_line は correction 本文由来でなければならない | `extracted_learning="変更後はテストを実行すること"` の correction に対し、対象ファイル中の無関係な既存行 `"テストを実行する"`（別件由来）を `draft_line` として渡す | `correction_message_sha256` の照合（§8） | `check_line_applied` は `matched=True` を返すが、`correction_message_sha256` が対象 correction の `extracted_learning` 正規化ハッシュと**一致しない**ことを別途検証する |
| 欠陥3系 陽性対照 | 同上 | `draft_line` が対象の correction 本文から直接生成されたケース | 同上 | `correction_message_sha256` が一致する |
| **§4 凍結解除 陰性（範囲逸脱検出）** | 凍結例外が1件を超えて増える | `FROZEN_STORES` に本設計の1件に加えて仮の2件目 `"brand_new_store2.jsonl"` を追加した変異ビルドで §4.1 の専用契約テストを実行 | §4.1 の diff テスト | 差分が `{"reflect_apply_events.jsonl", "brand_new_store2.jsonl"}`（2件）になり、期待値 `{"reflect_apply_events.jsonl"}`（1件）との不一致でテストが失敗する |
| **§4 凍結解除 陽性対照** | 同上 | `FROZEN_STORES` が本設計の1件のみを含む状態（実装後の正しい状態） | 同上 | テストは通過する |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:
- `reflect_apply_events.jsonl` に**空行のみの行**を複数混在させた状態で
  `fold_corrections` を実行し、空行が `isinstance(rec, dict)` チェックで正しく除外される
  ことを確認する
- `count_applied_reflections` の `events_path` を存在しないパスに向け（`base_health`は正常・
  `events_health["readable"]` は `True`・空在庫）、新ストアが未作成の環境（本設計の実装直後
  など）でも `count == 0`・`health.degraded == False` で安全に空表示されることを確認する
  （§9 で「ファイル不在は正常な空在庫」という `read_corrections_records_with_health` の
  既存契約が新ストアにも同じ意味で適用されることの確認）

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示）:
境界値（`window_days` ちょうど30日目の日時）／Unicode 正規化差（全角/半角。
`correction_message_sha256` の NFC 正規化が全角/半角を統一しない点は§14で明記）／
`reflect_apply_events.jsonl` が空行のみ・末尾に改行が無い場合／`reflect_draft_line` に
改行を含む複数行草稿／`append_unique_record` が `"retry_required"` を返した場合の呼出側
リトライ方針（本設計は未定義）／`reflect_apply_events.jsonl` と `corrections.jsonl` が
異なるファイルシステム権限状態（片方だけ読めない）になった場合の `degraded` 判定の
組合せ網羅（§10 では OR 結合のみ設計し、個別の組合せテストは未実施）。

## 14. 残る限界と未実測

- **(f)(g)(h) の `update_reflect_status` 自体の安全性は未解決のまま残る**。柱2の数字には
  影響しないが（§2.1）、「`--apply` の応答が事実であること」（round 0 ①-2）という別の
  完成条件要素には引き続き影響しうる。`#595` での解決が必要
- **正当な再反映が1件に潰れる**（§13 (c)陰性3）。**許容する**（前巡の頭の裁定を継承）。
  柱2は「反映件数」の表示であり、この既知の限界は常に**過小計上**へ倒れる方向にしか
  働かない。round 0 の守る対象①との関係では、過大計上（実際より多く見せる）の方が実害が
  大きく、本設計はその方向を構造的に避けている
- **skill パスの分類は best-effort**（§6）。既知の2配置（`.claude/skills/` と `skills/`）
  以外の配置は `"other"` に落ち、`not_measured` として除外される
- **`correction_message_sha256` は偶然一致を「防止」しない、監査補助にとどまる**
  （§8・§13「欠陥3系」・**残存リスクとして許容する**（前巡の頭の裁定を継承））。3点を明記する:
  1. `correction_message_sha256` は事後突合用の監査フィールドであり、イベント追記そのものを
     止めるゲートではない
  2. 偶然一致が起きると、実際には反映していない correction に対して誤ってイベントが
     追記され、柱2の `count` に誤って計上される（過大計上の一種の残存リスク）
  3. round 0 ②信頼境界が「自分たちの運用ミスのみ。悪意ある偽装・意図的な水増しは
     数えない」と定めており、偶然の部分文字列一致はこの境界の外側にあたる
- **`correction_message_sha256` の Unicode 正規化は NFC のみ**。全角/半角の統一は行わない
- **`append_unique_record` が `"retry_required"` を返した場合の呼出側の扱いは未定義**
  （実装1巡で決める）
- **§12.2 の decay_days 実運用値は未確認**。実装1巡の開始時に
  `grep -c '"decay_days"' ~/.claude/evolve-anything/corrections.jsonl` で確認すること
  （本文書では実行していない）
- **`migrate_correction_id_backfill.py` の実運用での実行有無は未確認**
- **§10 の集計関数のパフォーマンスは未計測**（`corrections.jsonl` と新ストア双方の実サイズ
  での fold 所要時間）。実装1巡でベンチマークを取ること
- **効果の実測（本設計が実際に柱2を正しく測れるようにするか）は未検証**。実装1巡の完了後、
  `bin/evolve-audit --growth` で柱2の表示が `not_measured` から実測値に切り替わることを
  確認する必要がある——ただし §0対象外により `results_board` 配線自体が別 issue なので、
  この確認は results_board 配線 issue の完了条件になる（本 issue の完了条件ではない）
- **新ストアの retention は `"permanent"`。TTL/compaction は実装1巡に含めない
  （頭の裁定・2026-09-01・裁定2）**。根拠は規模の実測: `corrections.jsonl` は
  **247行・約320KB**（再現コマンド `wc -l -c ~/.claude/evolve-anything/corrections.jsonl`・
  取得時刻 2026-09-01T05:28:12Z）。新ストアのイベントは `corrections.jsonl` の**部分集合**
  （反映されたものだけがイベントを持つ。§9・§12.1）であり、行数はこの247件を超えない。
  この規模で TTL/compaction を設計するのは、現時点では無い問題への対処になる。
  **再検討の引き金（観測可能な量で定義）**: `reflect_apply_events.jsonl` の行数が
  `corrections.jsonl` の現在の retention 上限相当（`prune/corrections.py` の
  `DEFAULT_DECAY_DAYS=90` 適用後の実効件数——本設計時点で未計測。実装1巡の完了条件に
  `wc -l ~/.claude/evolve-anything/corrections.jsonl` の decay 後件数を実測することを含める）
  の**10倍**（目安。`corrections.jsonl` 自身が既に90日 decay で定常件数に収束する設計になって
  いるため、その10倍という桁は「想定より1桁多い」ことを検知する閾値として置く。根拠となる
  実測値が無いため暫定値であることを明記する）を超えた時点、または
  **`count_applied_reflections`（§10）の実行時間が実装1巡のベンチマーク値
  （§14「§10の集計関数のパフォーマンスは未計測」参照）の10倍を超えた時点**、のいずれか
  早い方。**現時点では実装しない**（本設計のスコープ外。将来 TTL が必要になった場合は、
  `corrections.jsonl` と同じ decay 方式（§12.2）を踏襲するか、append-only の性質を活かした
  別方式にするかを再設計時に判断する）
- **本設計のレビュー巡数は新規系列の総上限2巡（設計1巡＋実装1巡）の設計側1巡を消費する**

## 15. 人間の判断が要る点

| # | 疑問 | 状態 |
|---|---|---|
| 1 | `update_reflect_status` の commit protocol 全面設計（(f)(g)(h) の根本解消）を `#595` に委ねてよいか | 前巡（第3版）で承認済み。継承 |
| 2 | 欠陥3（照合の紐付け強度）の残存リスクを許容するか | 前巡で承認済み・残存リスクとして許容。継承 |
| 3 | 正当な再反映が1件に潰れる既知の限界を許容するか | 前巡で承認済み・許容。継承 |
| 4 | CLI `correction_id`/ordinal 明示指定オプションを実装1巡に含めるか | 前巡で承認済み・含めない。継承 |
| 5 | 新ストアの basename を `reflect_apply_events.jsonl` とする案でよいか（他候補: `reflect_events.jsonl`・`pillar2_events.jsonl`） | **裁定済み（2026-09-01）**。`reflect_apply_events.jsonl` を採用。理由: 何のイベントかが名前から読める。`pillar2_` は PJ 内部の呼び名で外部から読めないため不採用（§2.2） |
| 6 | retention を `"permanent"` としたこと。`corrections.jsonl` 同様に無期限で増え続けることを許容するか、TTL/compaction を実装1巡の範囲に含めるか | **裁定済み（2026-09-01）**。実装1巡に含めない。根拠は規模実測（`corrections.jsonl` 247行・約320KB・2026-09-01T05:28:12Z実測）——新ストアはその部分集合であり現時点では無い問題。再検討の引き金を§14に観測可能な量で明記（§14） |
