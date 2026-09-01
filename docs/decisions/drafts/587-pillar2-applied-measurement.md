# #587: 柱2「照合済み反映」を測れるようにする設計（第3版）

> **第2版は不採用**。codex 設計レビュー3巡目（`rev587dr1`・2026-09-01 07:58:04 発注）で
> [Must] 7件（値域不一致・ordinal・commit protocol・dual-write・`--skip-all`・
> results_board 配線・全文hash）を受け、頭（オーケストレータ）が**スコープを縮小**して
> 本第3版を作成した（issue #587 コメント「頭が確定した縮小スコープ」・2026-09-01）。
> 第2版の本文は git 履歴（`git log -p -- docs/decisions/drafts/587-pillar2-applied-measurement.md`）
> で参照できる。
>
> **第2版からの主要な変更点**:
> 1. **PR #594（merge commit `547a032a`）で `scripts/lib/rl_common/correction_id.py` が
>    main に入った**。各 correction レコードに位置非依存の不変 `correction_id`（32桁hex）を
>    持たせ、`append_correction_record` という**唯一の安全な追記境界**（flock 済み・
>    重複拒否付き）が既に存在する。第2版が独自に設計していた「ordinal + ハッシュ再確認による
>    identity-safe な `update_reflect_status`」（§2.2〜2.3、150行超）は**丸ごと不要になった**。
>    本版はこの新しい土台を使う
> 2. **`update_reflect_status`（既存の全文書き換え経路）には一切手を入れない**。安全な
>    追記だけで完結させ、全文書き換えの commit protocol 設計は別 issue へ切り出す（§0 対象外）
> 3. **`reflect_status` と反映イベントの dual-write をやめる**。柱2の集計はイベント行だけを
>    正とする。基底レコードの `reflect_status` は既存 UI 互換のため残すが集計には使わない
> 4. **`results_board.py` への配線と表示は本 issue から切り出す**。本設計は「数えられる
>    ようにする」ところまでで完結させる
> 5. **`--skip-all` を含む既存の pending 抽出経路がイベント行を誤って pending 扱いしない
>    規則を明記する**（第2版はこれを次巡候補に放置していた）

対象: `#587`（前身 `#567`）。本文書は **設計のみ**。コードは1行も変更しない（実装は次巡）。
巡数の継承: 総上限2巡（設計1巡＋実装1巡・issue #587 コメント「巡3以降の一括承認」・
2026-08-30 に人間が一括承認）。本文書がその設計1巡の対象。**本版は同じ設計1巡の書き直しで
あり、新規の巡を消費しない**（頭の裁定・2026-09-01）。

## 0. Round 0 完成条件（issue #587 コメント「Round 0 完成条件（改訂版）」より verbatim 転記）

### ① 守る対象

1. 柱2として表示する数字が、実際に反映されたものと食い違うこと
2. **「指定した correction を更新した」という報告が事実であること**（#588 から継承）

### ② 信頼境界（誰の能力を脅威に数えるか）

**自分たちの運用ミスのみ**。具体的に数えるのは: 手編集 / 別プロセスの追記（hook）/ 処理の中断 /
同時に走る2つの更新 / 移行スクリプトの未実行。
**数えない**: 悪意ある偽装・意図的な数字の水増し・第三者による改竄。

### ③ 対象外（第3版で追加した3件は末尾に「なぜ本 issue で扱わないか」を付す）

- 柱2の目標値（3件）の妥当性。2026-08-26 にユーザーが「根拠なしの暫定値」と明記して決めたもの
- **hook / pitfall への反映測定**（記録自体が無く新しい保存先が要る＝#379 新設凍結に抵触）
- **memory への反映測定**（`auto_memory_broker` に配線は実在するが実データ188件中1件・別途）
- `#379` 新設凍結の解除。**本 issue は既存レコード列への追記のみで、新しい保存先を作らない**
- `results_board` の既存4軸表示の並び替え
- `reflect_status` の意味論そのものの再定義（値域の追加は可、既存値の意味変更は対象外）
- **【第3版で追加】`results_board.py` への配線と表示**: 数えられるようにするのが本 issue で、
  見せ方（reader injection・health key・renderer）は別 issue（rev587dr1 [Must]6）。理由:
  配線・表示契約の設計だけで [Must] 1件分の独立した検討量があり、本 issue の総上限（設計1巡+
  実装1巡）に収まらない
- **【第3版で追加】既存の全文書き換え経路（`update_reflect_status`）の commit protocol の
  全面設計**（rev587dr1 [Must]3）: prune・revoke・migration・invalidation・backfill を横断する
  共有 lock/atomic-replace 契約の設計。**本設計は追記のみで完結させ、全文書き換え経路には
  触れない**。理由: §2.1 で述べる通り、柱2の集計をイベント行だけの正とすることで、
  `update_reflect_status` 自体の安全性は柱2の正確性に影響しなくなる（§2.1 の「両立の理由」参照）。
  ゆえに commit protocol の全面設計は柱2の完成条件と独立に解決してよい別問題になる。
  **切り出す理由（頭の裁定・2026-09-01）**: この論点は #588 巡1 → #567 設計2巡 →
  本 issue の巡2（`rev587d`〜`rev587dr1`）と、2巡連続で同族（全文書き換えの identity/lock 安全性）
  の指摘が出ており `review-round-cap.md` の族2巡打ち切り条項に該当する。ユーザーが
  2026-09-01「縮小して1巡で決着させる」と裁定した
- **【第3版で追加】`#379` 新設凍結の解除**（既に上に列挙済み。**新しい保存先ファイルを作らない
  ことを再確認**: `corrections.jsonl` への追記で完結させる）
- **【第3版で追加】CLI に `correction_id`/ordinal を明示指定するオプション**（§6 旧記載を
  ここへ統合）: `--apply <source_correction_id>` が複数候補に一致する場合の曖昧性を、
  ユーザーが `--apply-ordinal N` や `--apply-correction-id <id>` のような形で明示解決できると
  運用上は便利だが、**無くても柱2は測れる**（§2.2 手順(a)で `resolve_source_correction_id` が
  `"ambiguous"` を返した場合はイベント行を追記しないだけで、柱2の数字が誤ることはない——
  曖昧なケースは単に legacy 側にもイベント側にも計上されず欠落するだけで、過大計上にはならない）。
  ゆえに実装1巡のスコープに含めない（頭の裁定・2026-09-01・裁定4）

### ④ blocking の定義

| | 内容 | 出所 | 第3版での扱い |
|---|---|---|---|
| (a) | 反映日時が残らない | #567 巡1 | 解消（§2.4 イベント行の `reflect_applied_at`） |
| (b) | 反映先の種別が残らない（`CLAUDE.md` と skill が区別できない） | #567 巡1 | 解消（§2.5 値域拡張） |
| (c) | 同一の反映が複数件に数えられる | #567 巡1 | 解消（§3 fold の重複排除規則） |
| (d) | 無効化済み（idiom revoke）が件数に残る | #567 巡1 | 解消（§3.2 の invalidate フィルタ） |
| (e) | 照合を通っていない旧レコードが件数に混ざる | #567 巡1 | 解消（§4.1 legacy 判定） |
| (f) | 読取後に有効レコードが挿入・削除されると、別 correction を更新して成功を返す | #588 巡1 [Must]4 | **柱2の集計からは無関係化**（§2.1）。`update_reflect_status` 自体の修正は対象外として切り出し |
| (g) | 並行する追記が消える／2つの更新が後勝ちで巻き戻る。いずれも成功を返す | #588 巡1 [Must]5 | 同上 |
| (h) | 同一 `source_correction_id` を持つレコードが2件以上あるとき、要求していないレコードまで更新される | 並行セッションの #588 別実装に対する tacchi 巡1 [Must]1 | **`correction_id`（#594）が構造的に解消**（§2.3）。`source_correction_id` の重複問題自体は残るが、本設計はそれを識別子として使わない |

**(f)(g) を「柱2の集計からは無関係化」と扱ってよい理由**: (f)(g) は `update_reflect_status`
（既存の read-full-file → 全行書き戻し関数）が起こしうる事故であり、**その関数が書き換えるのは
`reflect_status` フィールドだけ**である。第3版は柱2の集計を `reflect_status` に依存させない
（§2.1 dual-write 廃止）ため、`update_reflect_status` が (f)(g) の事故を起こしても、
**それによって柱2の数字が変わることはない**（イベント行という別の記録が正である）。
これは「(f)(g) が直っていなくてよい」という意味ではなく、「(f)(g) は `reflect_status`
フィールド自体の信頼性の問題として独立に残り、柱2の完成条件からは外れる」という意味である。
§7 に残存リスクとして明記する。

### ⑤ 検証方法

- 陰性試験（赤になるべき）を (a)〜(e) 各1件以上。(h) は #594 の `correction_id.py` 側で
  既に単体テスト（`scripts/lib/tests/test_correction_id.py`）がある構造的解消のため、
  本設計側では「重複 `correction_id` を持つ2レコードが fold で正しく分離される」ことだけを
  追加検証する
- 陽性対照を対で置く。陰性試験と混ぜて数えない
- 委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する。
  緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する

### 設計の出発点（前巡までの指摘から確定済み・再検討の対象外）

1. `update_reflect_status` は既存 JSON 行を書き換えており append-only ではない。
   柱2用の新規フィールドは追記イベント行として持ち、read 側で fold するモデルにする
2. 部分文字列一致は紐付けにならない。correction ID と最終 draft 全文のハッシュで結ぶ二段階が要る
3. `reflect_applied_at` に `datetime.now()` を入れても「ファイルを編集した時刻」ではなく
   「`--apply` を叩いた時刻」。何の時刻かを名前と文書で明示する
4. 二重計上キーは相対/絶対/symlink で別グループ化すると偽陽性、削除後の再反映を潰すと偽陰性
5. **【第3版で追加】ordinal は不変 ID（`correction_id`）に置き換える**（#594 で解決済みの
   土台を使う。rev587dr1 [Must]2）

## 1. 現状（実測・file:line つき）

### 1.1 書込み側

- `skills/reflect/scripts/reflect.py:631-` `update_reflect_status(status=...)` は
  **フルファイル読取 → 全行走査 → 全行書き戻し**。#588 で index 空間ずれのバグは修正済みだが
  （load_corrections と同じ index 空間で照合するようになった）、identity の再確認とロック協調は
  まだ無い（本設計の対象外・上記④参照）
- 同じファイルへの並行書込み経路が別に存在する:
  `hooks/correction_detect.py:135` が `correction_id: common.new_correction_id()` を新規
  レコードに付与してから `common.store_write("corrections.jsonl", record)` →
  `scripts/lib/rl_common/store_write.py` → `append_jsonl` → `persistence.py:177-211`
  `append_jsonl` は **`fcntl.flock(f, LOCK_EX)` を伴うブロッキング追記**
- **【新規・#594】`scripts/lib/rl_common/correction_id.py`
  `append_correction_record(filepath, record) -> AppendResult`** が
  **corrections.jsonl への唯一の安全な追記境界**として既に存在する（実測: 同ファイル
  50-77行目、2026-09-01 確認）。内部で `store_write.guard_problem` による write barrier 照合 →
  `correction_id` の validate → `persistence.append_jsonl(..., duplicate_check=...)` を呼ぶ。
  `duplicate_check` はロック保持中に評価される（`persistence.py:190-193`）ため、
  「読取→判定→追記」の TOCTOU が起きない。**本設計はこの関数をそのまま使い、独自の
  ロック機構を新設しない**
- `scripts/lib/correction_semantic/promote.py:584-649` `invalidate_idiom_corrections` は
  **第3の書込み経路**: 全読取 → メモリ上で `invalidated=True` → `tempfile.mkstemp` +
  `os.replace`。これも `append_jsonl` のロックを見ない。**本 issue の blocking (f)(g)(h) には
  含まれない**（上記④の理由により、本設計はそもそも `reflect_status`/`invalidated` の
  書込み経路そのものに触れない）

### 1.2 読み取り側 — `reflect_status` を直接読む独立パーサが複数ある

`reflect.py` の `load_corrections`（`reflect.py:111-124`）以外に、**`corrections.jsonl` を
独自に `json.loads` で走査して `reflect_status` を直接読む箇所が最低5つ**ある。
これらは `reflect.py` の `load_corrections` を経由しないため、`update_reflect_status` の
書込み方式を変えても**自動的には追随しない**（`pitfall_copied_parse_convention_partial_fix`
と同型のリスク）:

| ファイル:行 | 何を読むか | イベント行の扱い（第3版で確定） |
|---|---|---|
| `scripts/lib/prune/corrections.py:17-48`（`load_corrections`）/ `:51-117`（`cleanup_corrections`） | decay 超過の `applied`/`skipped` レコードを**物理削除**する。§4 で扱う | §4.2 で扱う |
| `scripts/lib/issues_summary.py:35-42` | `reflect_status == "applied"` 以外を unprocessed として数える | **要修正**（§4.3） |
| `scripts/lib/correction_semantic/correction_backlog.py:106` | `reflect_status == "promoted"` を在庫として数える | 変更不要（イベント行は `reflect_status` を持たないため `None != "promoted"` で自然に除外される） |
| `scripts/lib/audit/memory.py:482` | `reflect_status == "applied"` を数える | 変更不要（同上の理由で自然除外） |
| `skills/genetic-prompt-optimizer/scripts/optimize_core.py:79` | `reflect_status == "applied"` を数える | 変更不要（同上） |
| `scripts/lib/discover/suppression.py:198` | `reflect_status in ("pending", "promoted")` でフィルタ | **要修正**（§4.3。`.get("reflect_status")` の既定値次第で "pending" 扱いになりうる） |

**第2版の「6箇所すべて変更不要」という主張は誤りだった（第3版で訂正）**。
イベント行に `reflect_status` フィールドを**一切持たせない**という設計（§2.4）を前提に
6箇所を実測し直した結果、`.get("reflect_status", "pending")` のように**既定値へ "pending"
を使う2箇所**（`load_corrections`/`extract_pending` 自身の `reflect.py:165` と
`discover/suppression.py:198`）と、**`!= "applied"` を「未処理」とみなす1箇所**
（`issues_summary.py`）は、イベント行を新規に混入させると誤ってカウントする。
残り3箇所（`correction_backlog.py`/`audit/memory.py`/`optimize_core.py`）は
`== "promoted"` または `== "applied"` の**肯定一致**のみで、`None` は一致しないため
自然に除外される。§4.3 で対応する。

### 1.3 実データ確認（`~/.claude/evolve-anything/corrections.jsonl`・2026-08-30 実測・引継ぎ）

`reflect_status == "applied"` のレコード3件がいずれも `target_path`/`draft_line`/適用日時に
相当するフィールドを持たない（旧版 §1 から引き継ぐ実測。再取得コマンド:
`jq -c 'select(.reflect_status=="applied")' ~/.claude/evolve-anything/corrections.jsonl`。
再現できない場合は「測定不能」と明記すること）。**本版での追加確認は未実施**（§7 未実測）。

## 2. 採用する記録モデル

### 2.1 「追記だけで完結させる」— `update_reflect_status` には一切触れない

**設計判断（根拠つき、第3版の中心的な変更）**: 第2版は「`update_reflect_status` 自体を
identity-safe + lock-safe にする」（旧§2.2）ことで (f)(g)(h) を塞ごうとした。これは
`reflect_status` フィールドの書込み経路そのものを直す大改造であり、rev587dr1 [Must]3
（全 writer 共有の commit protocol が必要）を招いた。

**第3版の方針**: `update_reflect_status` には一切触れない。代わりに、**`--apply`/`--skip`
コマンドが `update_reflect_status` を呼んで `reflect_status` の更新を試みた「後」に、
その成否とは独立した追記操作として、`append_correction_record`（#594・既に安全）を使って
イベント行を1件追記する**。

**両立の理由（rev587dr1 [Must]3・[Must]4 への回答）**:

1. **柱2の集計はイベント行だけを正とする**（dual-write 廃止・rev587dr1 [Must]4 への直接回答）。
   基底レコードの `reflect_status` フィールドは既存 UI（`--view` 等）との互換のため書き込み
   続けるが、**柱2の `count_applied_reflections`（§3.2）はこのフィールドを一切読まない**。
   基底レコードと event レコードが内容不一致・orphan・重複であっても、**柱2の数字は
   event レコードの集合だけから計算されるため、不整合状態を「数える／隔離する／エラー表示する」
   という分岐が最初から不要になる**（rev587dr1 [Must]4 が要求する dual-write 整合性規則は、
   dual-write をやめたことで対象そのものが消える）
2. **`update_reflect_status` 自身の commit protocol（(f)(g)(h)）は柱2の完成条件と無関係になる**
   （§0④で述べた理由）。ゆえに `prune`/`revoke`/`migration`/`invalidation`/`backfill` を横断する
   共有 lock 契約の設計（rev587dr1 [Must]3）は、柱2を測れるようにするために**必要ではない**。
   これは別の正しさの問題（`reflect_status` フィールド自体の信頼性）として別 issue に切り出す
3. **`corrections.jsonl` 自身が「基底のみ・追記なし」という前提を崩さない**（旧版のこの前提は
   維持する）。イベント行は基底レコードとは別の `record_kind` を持つ行として同じファイルに
   追記されるだけであり、基底レコードの内容・順序は一切変更しない

**この方針で失うもの**: 「`--apply` が『反映した』と報告したのに、実際は競合で別レコードが
壊れていた」（(f)(g)(h) の直接的な害）は直らない。ただしこれは元々 `reflect_status`
フィールド自体の信頼性の問題であり、柱2（本 issue）の完成条件①「柱2として表示する数字が
実際に反映されたものと食い違うこと」には効かない（柱2はイベント行しか見ないため）。
①②の**②「指定した correction を更新したという報告が事実であること」**は、`--apply` の
JSON 応答全体（`reflect_status` の更新結果を含む）についての要求であり、これは
`update_reflect_status` 自体の話であって柱2固有の完成条件ではない。この点は §0④の表と
§7「受容する残存リスク」に明記し、**人間の判断を仰ぐ**（§8）。

### 2.2 イベント行の追記タイミングと呼出契約

`reflect.py` の `--apply` ハンドラ（現行 `reflect.py:1274-1364`）を次のように拡張する
（`update_reflect_status` 自体は変更しない）:

1. 現行どおり `update_reflect_status(corrections_file, [target_index], "applied", ...)` を呼ぶ
2. **戻り値が `{"status": "applied", ...}` のときだけ**、追加で以下を行う:
   a. 対象レコードの `correction_id` を取得する。取得元は現行の `target_index` 探索
      （`reflect.py:1305-1312`、`make_source_correction_id` による先頭一致）が指すレコード
      **ではなく**、`resolve_source_correction_id`（reflect.py:127-153・#594 で追加済み・
      読取専用）を先に呼んで解決する。**`resolve_source_correction_id` が `"ambiguous"` を
      返した場合、イベント行は追記せず `{"status": "ambiguous_source", ...}` を返して
      非0終了する**（複数該当は #594 が既に検出可能にしているので、ここで初めて活用する。
      rev587dr1 [Must]2・tacchi #588 別実装 [Must]1 への対応。**現行 `target_index` 探索の
      「先頭一致で確定」動作自体は変更しない**——`update_reflect_status` へ渡す index は
      従来どおり——が、**イベント行の紐付けだけは曖昧なら諦める**という非対称な安全策。
      理由: `target_index` 側の是正は `update_reflect_status` の commit protocol 全面設計
      （§0対象外）を要するため本 issue のスコープに入らないが、イベント行の追記は新規の
      独立した操作なので、ここでだけ fail-closed にできる）
   b. `correction_id` が取得できたら、`append_correction_record` で §2.4 のイベント行を追記する
   c. 追記が `{"status": "appended"}` 以外（`"duplicate_id"`/`"unsupported_platform"`/
      `"unregistered_store"`/`"retry_required"`）を返した場合、`--apply` の JSON 応答に
      `"pillar2_event"` キーとしてその結果を含める（黙って握り潰さない）。ただし
      **`reflect_status` の更新自体（手順1）はイベント追記の成否と独立に成功したまま返す**
      （柱2の記録失敗を理由に、既存の `--apply` の主機能を失敗扱いにしない。round 0 ②
      「指定した correction を更新したという報告が事実であること」は手順1の結果についての
      要求であり、イベント追記はそれに追加される副次記録）

**`--skip` も同型の `correction_skipped` イベントを追記する**（§2.4）。ただし §0③により
柱2の集計対象ではなく、監査証跡としてのみ持つ。

### 2.3 識別子は `correction_id` のみ（#594 の不変IDをそのまま使う）

**第2版で採用していた `(source_correction_id, ordinal)` 複合キーは廃止する**
（rev587dr1 [Must]2）。理由: `correction_id` は #594 により

- 新規レコードには検出時（`hooks/correction_detect.py:135`）に必ず付与される
- 既存レコードには `scripts/migrate_correction_id_backfill.py` が一度だけのバックフィルを
  提供する（**未実行環境が成立する**——この場合の扱いは§4.1で扱う）
- **重複を構造的に検出できる**（`find_duplicate_ids`・`has_duplicate_id`）。
  `append_correction_record` はロック保持中に重複 `correction_id` を reject するため、
  新規追記された `correction_id` が既存と衝突することはない（新規発行は `uuid.uuid4().hex`
  なので実務上の衝突確率は無視できる）
- **位置に依存しない**（物理行番号でも `load_corrections` の配列 index でもない）ため、
  §2.2 の identity 解決に使っても、`update_reflect_status` の commit protocol の状態と
  無関係に安定する

イベント行は `target_correction_id`（§2.4）で基底レコードを参照する。fold（§3）は
`target_correction_id` の値だけでグルーピングする。**`source_correction_id`（既存の
`session_id`+`timestamp` 複合キー。`reflect.py:890` 等で既に使われている別概念）とは
**フィールド名を明確に分けて衝突を避ける**（第2版は `source_correction_id` を新概念で
再利用しており、既存の同名フィールドと意味が混同する余地があった。第3版で是正）。

**既存レコードに `correction_id` が無い場合（バックフィル未実行）**: §2.2 手順(a)の
`resolve_source_correction_id` は `candidates[0].get("correction_id")` が `None` のとき
`resolve_correction_id(records, None)` を呼び、`validate_correction_id(None)` が `False` を
返すため `{"status": "invalid_id"}` になる。この場合もイベント行は追記せず
`{"status": "unmigrated_source", ...}` を返す（**fail-closed**。round 0 出発点5・
移行スクリプト未実行環境の扱いとして、静かに古い方式へフォールバックしない）。

### 2.4 追記イベント行のスキーマ

`corrections.jsonl` へ追記する新しい行の型。既存の基底レコード（`record_kind` フィールドを
持たない）と区別するため、**新フィールド `record_kind` を導入する**（round 0「値域の追加は可」
に該当・既存値は変更しない）:

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `record_kind` | `"reflect_event"`（固定） | ○ | 基底レコードと区別する discriminator。既存レコードにこのキーは無い |
| `correction_id` | str（32桁hex） | ○ | **このイベント行自身の不変ID**（`new_correction_id()` で新規発行）。`append_correction_record` が重複拒否の対象にするフィールドと同じ名前・同じ関数を使う（イベント行同士の重複追記も同じ機構で防げる） |
| `schema_version` | int（現在値 `1`） | ○ | 【rev587dr1 [Should]】将来のフィールド追加・意味変更時に fold 側が版で分岐できるようにする |
| `event_type` | `"correction_applied"` \| `"correction_skipped"` | ○ | イベント種別 |
| `target_correction_id` | str（32桁hex） | ○ | 対象の基底レコードの `correction_id`（§2.3）。fold はこのキーだけでグルーピングする |
| `reflect_applied_at` | str (ISO8601 UTC) | `event_type=="correction_applied"` のみ | **`--apply` を実行した時刻**（ファイル編集時刻ではない。フィールド名・本コメントの両方で明示する。round 0 出発点3） |
| `reflect_target_kind` | §2.5 の値域 | 同上 | §2.5 |
| `reflect_target_path` | str | 同上 | 反映先ファイルの**正規化後**パス（§2.6） |
| `reflect_draft_line` | str | 同上 | 起草行の全文（正規化前・§2.7 の照合対象） |
| `correction_message_sha256` | str (SHA-256 hex) | 同上 | **completion condition の全文hash要件**（round 0 出発点2・rev587dr1 [Must]6）。対象 correction の `extracted_learning`（無ければ `message`）の**正規化後全文**の SHA-256。正規化: 前後空白除去 + 改行を `\n` へ統一（CRLF→LF）+ NFC Unicode 正規化。照合時点: イベント追記時に対象 correction の当該フィールドから直接計算する（`--draft-line-file` 経由の別ファイルではなく、`load_corrections` で読んだレコードそのものから計算するため改ざん・取り違えの余地がない） |

**追記先は `corrections.jsonl` 自身**（新しい store を作らない）。根拠:
`scripts/lib/shrink_freeze.py:72` `FROZEN_STORES` に `"corrections.jsonl"` が既に列挙されて
おり、同一 basename への追記は「新しい store」ではない。`record_kind` という新フィールドを
既存 store の行に持たせることは `store_registry`/`_OBSERVABILITY_BUILDERS`/
`ADVISORY_PROPOSAL_ADAPTERS`/`WEAK_SIGNAL_CHANNELS`（凍結対象4種、`shrink_freeze.py:23-37`）
のいずれの登録簿にも新規エントリを作らないため、`assert_no_new_keys`（`shrink_freeze.py:261-275`）
の検査対象にならない。

**イベント行は `reflect_status` フィールドを一切持たない**（§1.2 の表で確定した設計）。

### 2.5 `reflect_target_kind` の値域拡張（blocking b の解消・rev587dr1 [Must]5）

分類ロジックは `reflect.py:505-546` の `_rule_scope_identity` を
**`scripts/lib/reflect_apply_match.py` へ `classify_target_kind(target_path) -> str` として
移設・拡張**し、`reflect.py` 側は薄いラッパーにする。値域を次のとおり拡張する
（rev587dr1 の指摘「`CLAUDE.md` とskillが区別できない」への直接対応）:

| 値 | 判定条件 |
|---|---|
| `"global_rule"` | 現行 `_rule_scope_identity` と同じ（`~/.claude/rules/` 配下） |
| `"project_rule"` | 現行と同じ（`<repo>/.claude/rules/` 配下） |
| `"global_claude_md"` | `Path(target_path).expanduser().resolve() == (Path.home() / ".claude" / "CLAUDE.md").resolve()` |
| `"project_claude_md"` | `repo_identity(target_path)["relative_path"] == "CLAUDE.md"`（repo_id が取れる場合のみ） |
| `"skill"` | **best-effort**: `repo_identity` の `relative_path` が `skills/` を含み、かつファイル名が `SKILL.md`（グローバルは `~/.claude/skills/**/SKILL.md`、プロジェクトは `<repo>/.claude/skills/**/SKILL.md` または `<repo>/skills/**/SKILL.md` — 本リポジトリ自身が後者の配置のため両方許容する） |
| `"other"` | 上記いずれにも一致しない |

**既知の限界（§7 に転記）**: skill のパス規約は PJ・プラグインごとに揺れがあり
（`.claude/skills/` と `skills/` の両方が実在する。本 issue の対象コーパスは
`~/.claude/evolve-anything/corrections.jsonl` の実データ範囲でしか検証していない）、
網羅した規約に一致しないパスは `"other"` に落ちる。`"other"` は §3.2 で
`not_measured.other_target_kind` として理由つきで除外し、「測れないものが測れない」ことを
明示する（rev587dr1「'other' を一律測れないとするのは不適切」への回答: **測定不能の理由が
"skill/CLAUDE.md ではない" ではなく "既知の3パターンに一致しないパス形式" であることを
`reflect_target_kind` 自体の値で表現できるようにした**。真に skill/CLAUDE.md であっても
未知の配置なら `"other"` に落ちる残余リスクは残るが、既知の2つの盲点＝旧設計が単純に
除外していた `CLAUDE.md` と skill 標準配置は解消した）。

### 2.6 `reflect_target_path` の正規化（rev587dr1 [Should] path canonicalization）

イベント追記時に次の順で正規化してから保存する（§3.2 の重複排除グルーピングキーにも
この正規化後の値を使う。blocking c の偽陽性——相対/絶対/symlink違いによる過剰計上——を防ぐ）:

1. `Path(target_path).expanduser()`
2. `.resolve()`（symlink 解決・絶対化。対象ファイルが既に存在することは §2.2 の時点で
   `update_reflect_status` 内の `check_line_applied` が確認済みなので `resolve()` は失敗しない）
3. `repo_identity()` が repo_id を返せば `f"{repo_id}:{relative_path}"`（worktree 間で
   同一ファイルを同一キーにする——`global_rules_root` 配下や home 直下の `CLAUDE.md` は
   repo_id が無いので、絶対パス文字列をそのまま使う）

**保存時点で存在確認しか行わない**（§2.2 の時点。rev587dr1 [Should] 「観測時に対象行が
まだ存在するか確認しないなら『直近30日のapply確認操作数』と限定すべき」への回答）:
本設計は表示ラベルを変えず、代わりに §3.2 の集計関数のドキュメンテーション文字列に
「`reflect_applied_at` 時点で確認された事実であり、その後の削除・変更を追跡しない」ことを
明記する（read 時に対象ファイルへ毎回アクセスして再検証するのは、fold がファイルI/Oを
一切行わない現行設計（§3）の前提を壊すため採用しない）。

### 2.7 correction とイベントの紐付け強度（欠陥3・rev587dr1「保留3点」への回答）

**第2版の部分文字列一致（`check_line_applied` の再利用のみ）は不採用**にする。
rev587dr1 の「保留3点」レビュー意見（「最終draft全文をcorrection IDへ明示的に関連付け、
その全文と対象ファイルを正規化後完全一致させる方式を推奨」）を採用する:

- `check_line_applied`（`reflect_apply_match.py`）による「draft_line が対象ファイルに
  存在するか」の確認は**そのまま維持する**（`update_reflect_status` の既存契約であり、
  §0対象外の commit protocol とは別の話——ここは変更しない）
- **追加で**、イベント行に `correction_message_sha256`（§2.4）を持たせることで、
  「このイベントがどの correction の内容に対応するか」を**correction 本文のハッシュで
  固定する**。fold 側では使わない（監査用）が、`欠陥3系` の陰性試験（§5）で
  「別の無関係な correction の内容から偶然同じ draft_line が作れても、
  `correction_message_sha256` が対象 correction の内容と一致しないことを検出できる」
  ことを検証する
- **人間判断は残る**（§8）: 「言い換え」を許容するかどうか（`extracted_learning` の
  意訳が draft_line と完全一致しないケース）は本設計では解決しない。現状は
  `check_line_applied` の正規化後完全一致のみで判定し、`correction_message_sha256` は
  監査補助にとどめる

## 3. read 時 fold の擬似コード

新規共有モジュール **`scripts/lib/reflect_fold.py`** を作る（`results_board.py` にも
`reflect.py` にも置かない。理由: 読み手が6+1箇所に分散しており、`reflect.py` に置くと
`prune/corrections.py` 等が `reflect.py` を import する逆方向の依存になり不自然）。

```python
# scripts/lib/reflect_fold.py（設計・未実装）
from dataclasses import dataclass, field
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


def fold_corrections(raw_records: list) -> list[FoldedCorrection]:
    """corrections.jsonl の生レコード列（基底+イベント混在）を基底単位に畳む。

    raw_records は「読取失敗しなかった生の json.loads 結果」の列でよい（順序は不問。
    第2版と異なり ordinal を使わないため、ファイル出現順である必要がなくなった）。
    """
    bases_by_id: dict[str, dict] = {}          # correction_id -> 基底レコード
    order: list[str] = []                       # 出現順（表示の安定性のためだけに保持）
    events: list[dict] = []

    for rec in raw_records:
        if not isinstance(rec, dict):
            continue  # 述語の単一ソース化（§3.1）
        if rec.get("record_kind") == "reflect_event":
            if rec.get("schema_version") == 1:  # 未知の schema_version は §3.1 で別扱い
                events.append(rec)
            continue
        cid = rec.get("correction_id")
        if not isinstance(cid, str) or not cid:
            continue  # correction_id 未付与の基底レコード（バックフィル未実行）は fold 対象外
        if cid not in bases_by_id:
            order.append(cid)
        bases_by_id[cid] = rec  # 同一 correction_id の重複は最後を正とする（構造的に稀）

    folded_by_id = {cid: FoldedCorrection(base=bases_by_id[cid]) for cid in order}

    # イベントは「同一 target_correction_id に対する最新の correction_applied」だけを採用する。
    # イベント回数の合算ではなく「今 applied かどうか」という状態1個（blocking c の一部）。
    latest_applied_event: dict[str, dict] = {}
    for ev in events:
        if ev.get("event_type") != "correction_applied":
            continue
        target_id = ev.get("target_correction_id")
        if target_id not in folded_by_id:
            continue  # orphan event（対象の基底レコードが fold 対象に無い）は無視（§3.1）
        # ファイル出現順で後のものが「最新」。同一 key への2回目の apply は §2.2 の
        # duplicate_check が append_correction_record レベルでは防がないため
        # （duplicate_check は「このイベント行自身の correction_id」の重複しか見ない）、
        # fold 側で「最新を正とする」規則を持つ。読取専用の状態導出であり書込みではないため
        # ②信頼境界の対象外。
        latest_applied_event[target_id] = ev

    for cid, ev in latest_applied_event.items():
        f = folded_by_id[cid]
        f.reflect_applied_at = ev.get("reflect_applied_at")
        f.reflect_target_kind = ev.get("reflect_target_kind")
        f.reflect_target_path = ev.get("reflect_target_path")
        f.reflect_draft_line = ev.get("reflect_draft_line")
        f.correction_message_sha256 = ev.get("correction_message_sha256")
        f.has_pillar2_fields = bool(f.reflect_applied_at and f.reflect_target_kind)

    return [folded_by_id[cid] for cid in order]
```

### 3.1 有効レコード・イベントの述語を単一ソース化する

- 非 dict レコードは弾く（現行 `load_corrections` は弾かない。`isinstance(rec, dict)` を
  `fold_corrections` の入口に置く——第2版から継承）
- **未知の `schema_version` を持つイベント行は無視する**（rev587dr1 [Should] 冪等性契約）。
  `fold_corrections` は `schema_version != 1` のイベントを黙って捨てるのではなく、
  呼出元（§3.2）が `degraded` health としてカウントできるよう、捨てた件数を返り値の
  タプル第2要素（`FoldHealth`）に含める:

```python
@dataclass
class FoldHealth:
    orphan_events: int = 0          # target_correction_id が見つからないイベント
    unknown_schema_events: int = 0  # schema_version != 1 のイベント
    malformed_records: int = 0      # 非 dict レコード（raw_records 側で既に除外されている場合は
                                     # ここでは重複カウントしない——§3.2 の呼出側が
                                     # read_corrections_records_with_health の health と
                                     # 合算する契約にする）
```

`fold_corrections` は `(list[FoldedCorrection], FoldHealth)` のタプルを返す
（rev587dr1 [Should]「degraded read を0件と区別すべき」への回答。malformed 行そのものの
検出は §3.2 が `queue_materials.read_corrections_records_with_health` を再利用して行う
——新規実装しない）。

### 3.2 `count_applied_reflections`（設計のみ・実装は次巡）

`scripts/lib/pillar2_metrics.py`（新規モジュール）が `reflect_fold.fold_corrections` を呼ぶ。
**raw record の取得は新規実装せず、`fleet.queue_materials.read_corrections_records_with_health`
を再利用する**（rev587dr1 [Should] 「既存の corrections health reader と同様に構造化 health を
返す」への直接対応。当該関数は既に `(records, health)` を1回の read から返す設計になっており
——`readable`/`error`/`malformed_lines`——、独自実装すると2箇所目の同種ロジックになる）。

```python
def count_applied_reflections(
    slug: str, *, corrections_path=None, now=None, window_days: int = 30
) -> dict:
    from fleet.queue_materials import read_corrections_records_with_health
    records, read_health = read_corrections_records_with_health(
        corrections_path or default_corrections_path()
    )
    folded, fold_health = fold_corrections(records)
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
            legacy_unverified += 1  # blocking e（correction_id はあるが pillar2 イベント無し）
            continue
        if f.reflect_target_kind == "other":
            other_kind_count += 1
            continue
        ts = _parse_timestamp(f.reflect_applied_at)  # results_board._parse_timestamp を import
        if ts is None or not (window_start <= ts <= now):
            continue
        scope = classify_project_scope(f.base, slug)
        if scope not in ("same-project", "global-looking"):  # rev587dr1 [Must]1 の値域修正
            continue
        eligible.append(f)

    # blocking c の残り半分: 別 correction が同じ実世界の反映を指している場合の重複。
    # (target_kind, 正規化済み target_path, normalize(draft_line)) でグルーピング。
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
        not read_health["readable"]
        or read_health["malformed_lines"] > 0
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
            "readable": read_health["readable"],
            "read_error": read_health["error"],
            "malformed_lines": read_health["malformed_lines"],
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
**固定フィールド名**で読む。pillar2 は `reflect_applied_at` という別フィールド名を窓判定する
ため `_in_window` をそのまま呼べない。**`_parse_timestamp`（汎用・フィールド名を引数に取らない
値パーサ）だけを import し、窓判定は `pillar2_metrics.py` 側にインラインで書く**。

**`results_board.py` への配線は本 issue のスコープ外**（§0対象外）。`count_applied_reflections`
はここでは呼び出されない独立関数として設計するだけであり、`build_results_board` への
reader injection・戻り値 schema への統合・renderer への表示追加は別 issue で行う。

### 3.3 `classify_project_scope` の再利用（rev587dr1 [Must]1 の修正）

**第2版の記述誤りを訂正する**: `classify_project_scope`（`reflect.py:169-201`）の実際の
戻り値は `"same-project"` / `"global-looking"` / `"project-specific-other"` の3値である
（`("current", "shared")` という第2版の記述は実装に存在しない値であり、そのまま実装すると
**全件が `project-specific-other` として除外され柱2は常に0件になる**——rev587dr1 [Must]1が
指摘した通り）。

**採用する値域**: `scope in ("same-project", "global-looking")` を対象とする
（`"project-specific-other"` は除外）。理由: `global-looking` は「別 PJ 由来だが汎用的な
内容」（`always`/`never`・モデル名を含む・DB名やパスを含まない）を指し、CLAUDE.md の柱2定義
「反映先は rule に限らない」の対象として妥当。`project-specific-other`（DB名やファイルパスを
含む PJ 固有の内容）は他 PJ での反映が柱2の趣旨（同一 PJ の学習が繰り返し反映されているか）と
ずれるため除外する。

**`slug` の絞り込み規則**（rev587dr1 [Should]）: `classify_project_scope` の第2引数
`current_project` には**リポジトリの絶対パス**を渡す（`reflect.py:1498` の既存呼び出し
`classify_project_scope(c, current_project)` と同じ形。`current_project` は `reflect.py` の
CLI 内で `Path.cwd()` 等から解決済みの変数）。`pillar2_metrics.count_applied_reflections` の
`slug` 引数は**将来の複数 PJ 横断表示のための予約**であり、本設計の `classify_project_scope`
呼び出しでは直接使わない（`current_project` パスへの変換が必要になった場合は
`correction_backlog.py` が使う `_correction_slug`/`pj_slug_match` の経路を再利用する——
新方式を発明しない。ただし本 issue は単一 PJ 表示のみを対象とするため、この変換自体は
次巡（results_board 配線）まで実装しない）。

## 4. 移行

### 4.1 既存データ（`correction_id` が無い、または新4フィールドが無い `applied` レコード）

- `correction_id` を持たない基底レコード（`migrate_correction_id_backfill.py` 未実行）は
  `fold_corrections` の対象外になる（§3 擬似コード）。**移行スクリプトの実行はこの設計の
  前提ではない**——未実行でも「柱2に含まれない」という安全側に倒れるだけで、誤ってカウント
  されることはない
- `correction_id` はあるが pillar2 イベントが無い既存 `applied` レコードは
  `has_pillar2_fields=False` のままなので `legacy_unverified_count` に分類される（blocking e）
- **`migrate_correction_id_backfill.py` を実行するかどうかの判断は本設計の対象外**
  （既に main にマージ済みの独立したツールであり、実行の要否・タイミングは実装1巡または
  運用判断に委ねる。実行すれば `legacy_unverified_count` の母数が変わりうるが、`count`
  （分子）には影響しない——イベント行が無ければ `correction_id` の有無に関わらず
  `has_pillar2_fields=False` のまま）

### 4.2 `prune/corrections.py` の decay 削除との相互作用

`scripts/lib/prune/corrections.py:105-115` `cleanup_corrections` は
`reflect_status in ("applied", "skipped")` かつ `timestamp` が `decay_days`
（既定 `DEFAULT_DECAY_DAYS = 90`）を超えたレコードを**物理削除**する。

**イベント行への影響**: `cleanup_corrections` の現行実装（`prune/corrections.py:17-48`
`load_corrections`）が `record_kind == "reflect_event"` を認識しない場合、イベント行は
`reflect_status` フィールドを持たないため `record.get("reflect_status") in ("applied","skipped")`
判定に一致せず、**削除対象にならない**（安全側）。よって基底レコードが decay で削除されても
対応するイベント行は残り続け、`fold_corrections` は `target_correction_id` が指す基底が
無いオーファンイベントとして扱う（§3.1 の `orphan_events` カウント）。

**本設計での扱い**: この相互作用の完全解消（イベント行にも decay を適用する、または
基底削除時にイベントも連動削除する）は round 0 対象外とする。pillar2 の測定窓は既定30日、
prune の既定 decay は90日であり、基底が decay で消える頃には測定窓をとうに外れているため、
実害は小さい（旧版 §4.2 の分析を継承）。**未実測**: `decay_days` のカスタム設定が実運用に
存在するかは未確認（§7）。

### 4.3 §1.2 で見つかった読み手のうち「要修正」2箇所

**訂正**: 第2版は「§1.2 の6箇所は書込み方式を変えても変更不要」と主張していたが、
これはイベント行という**新しい record_kind そのものが読み手に混入すること**を検討して
いなかったための誤りだった。第3版で実測し直した結果は次の通り（次のレビュアーが同じ確認を
繰り返さないよう、判定根拠をここに残す）。6箇所のうち **3箇所は肯定一致述語（`==`）のため
イベント行（`reflect_status` フィールドを持たない）を自然に除外する（変更不要）**が、
**3箇所は既定値または否定条件でイベント行を誤ってカウントする**ため、
**同一の1行ガードを追加する**（イベント行を弾くだけで、既存の `reflect_status` 値の意味は
一切変えない——round 0 ③の範囲内）:

1. **`skills/reflect/scripts/reflect.py:111-124`（`load_corrections`）**:
   **ガードを入れなかった場合に壊れるもの**: `extract_pending`（`reflect.py:156-166`）が
   `r.get("reflect_status", "pending") in ("pending", "promoted")` で判定するため、
   イベント行（`reflect_status` フィールド無し）は既定値 `"pending"` にフォールバックし
   **未処理の correction として朝の対話レビュー・`--skip-all` の対象に混入する**。
   `--skip-all` はさらに `update_reflect_status` へこの index を渡すため、
   イベント行の JSON に `reflect_status="skipped"` が書き込まれ、**イベント行自体が
   壊れる**（`record_kind` は残るが `fold_corrections` が想定しないフィールドが増える）。
   追加するガードは `if record.get("record_kind") == "reflect_event": continue`。
   これにより `load_corrections` を経由する `extract_pending`・`--apply`/`--skip`/
   `--skip-all` の index 計算・`--view` の全てが自動的にイベント行を除外する
   （**単一箇所の修正で `reflect.py` 内の全消費者に効く**——`load_corrections` が
   `reflect.py` 内の唯一の読み込み関数であるため）
2. **`scripts/lib/issues_summary.py:35-42`**:
   **ガードを入れなかった場合に壊れるもの**: `reflect_status == "applied"` 以外を
   unprocessed として数える実装のため、イベント行（`reflect_status` が無い＝`!= "applied"`）
   が**未処理 correction の件数に1件ずつ混入し、`--apply` を実行するたびにこの数字が
   水増しされる**。同じガードをループの先頭に追加する
3. **`scripts/lib/discover/suppression.py:198`**:
   **ガードを入れなかった場合に壊れるもの**: `reflect_status in ("pending", "promoted")`
   でフィルタする実装。`.get("reflect_status")` の既定値実装次第では #1 と同型で
   イベント行が pending 扱いになり、抑制対象の判定に**本来存在しない「pending
   correction」が紛れ込む**。同じガードを追加する

**変更不要と確定した3箇所の根拠（再確認・肯定一致述語のため自然除外）**:
- `correction_backlog.py:106` の `== "promoted"`: イベント行に `reflect_status` フィールドは
  無いため `record.get("reflect_status") == "promoted"` は常に `False`
- `audit/memory.py:482` の `== "applied"`: 同上
- `optimize_core.py:79` の `== "applied"`: 同上

**実装1巡の完了条件に含める**: 上記3箇所の1行ガード追加＋既存テストが無改修分含め全て通ること
（`load_corrections` のガードは `reflect.py` 内の消費者を回帰確認すれば足りる）。

## 5. 検証計画

各陰性試験に「壊す不変条件」と「通したい検査経路」を書く。同じ変異を陰性/陽性で使い回さない。
テストは `scripts/lib/tests/test_reflect_fold.py`（fold 単体）・
`scripts/lib/tests/test_pillar2_metrics.py`（集計）・
`skills/reflect/scripts/tests/test_reflect_apply_event.py`（§2.2 のイベント追記）に新設する。

| # | 壊す不変条件 | 変異 | 通したい検査経路 | 期待結果 |
|---|---|---|---|---|
| (a) 陰性1（フィールド欠落） | 反映日時が集計に使われる | fixture イベント行から `reflect_applied_at` を削除 | `has_pillar2_fields` 判定 | legacy 扱いに落ちる |
| (a) 陰性2（誤ったフィールドの窓判定・rev587dr1指摘） | 窓判定に `timestamp`（検出時刻）でなく `reflect_applied_at` が使われる | `timestamp`=窓内・`reflect_applied_at`=窓外の fixture と、その逆（`timestamp`=窓外・`reflect_applied_at`=窓内）を対で用意 | `count_applied_reflections` の窓判定 | 前者は `count` から除外、後者は `count` に含まれる（`_in_window` 誤用なら結果が逆転し検出できる） |
| (a) 陽性対照 | 同上 | `reflect_applied_at` を window 内の妥当な値のまま残す | 同上 | `count` に1件として残る |
| (b) 陰性1（フィールド欠落） | 反映先種別が区別される | `reflect_target_kind` を欠落させたイベント fixture | `has_pillar2_fields` 判定 | legacy 扱いに落ちる |
| (b) 陰性2（CLAUDE.md誤分類・rev587dr1指摘） | `CLAUDE.md` が測定対象として分類される | `target_path=/repo/CLAUDE.md` を `classify_target_kind` に通す | §2.5 の分類ロジック | `"project_claude_md"` を返す（`"other"` ではない） |
| (b) 陰性3（skill誤分類） | skill が `"other"` に落ちて非測定対象になる | `target_path=.claude/skills/foo/SKILL.md` | 同上 | `"skill"` を返す |
| (b) 陽性対照 | 同上 | `reflect_target_kind="project_rule"` を持つ正常 fixture | 同上 | `count` に含まれ `applied_list` に `target_kind` が出る |
| (c) 陰性1（同一 base への重複イベント） | 反映は1状態として数える | 同一 `target_correction_id` に対し `correction_applied` イベントを2行追記した fixture | `fold_corrections` の latest 選択 | `count == 1` |
| (c) 陽性対照1 | 同上 | イベント1行のみの正常 fixture | 同上 | `count == 1` |
| (c) 陰性2（path別名の偽陽性・rev587dr1指摘） | 同一物理ファイルの相対/絶対パス表記違いを別グループにする | 同一ファイルを指す `reflect_target_path` の相対表記版・絶対表記版を持つ2イベント fixture | §2.6 の正規化 | `count == 1`（2ではない） |
| (c) 陽性対照2 | 同上 | 実際に異なる2ファイルへの反映 | 同上 | `count == 2` |
| (c) 陰性3（正当な再反映の偽陰性・rev587dr1指摘） | 削除後の正当な再反映を1件に潰す | 同一 target_path/draft_line で `reflect_applied_at` が異なる2件（別 correction 由来）を fixture 化 | グルーピングの代表時刻選択 | **設計判断としてグルーピングし `count == 1` になることを確認した上で**、`applied_list` の `reflect_applied_at` が2件のうち古い方（`min`）になることを明示的にテストし、この既知の限界を固定する（§7 に残存リスクとして明記——真の解消は別 issue） |
| (d) 陰性 | 無効化済みは数えない | `invalidated=True` の基底 fixture（有効なイベントも付与） | invalidate フィルタ | `count` から除外、`invalidated_count` に計上 |
| (d) 陽性対照 | 同上 | `invalidated` キー自体が無い正常 applied fixture | 同上 | `count` に含まれる |
| (e) 陰性 | 旧レコードは数えない | `correction_id` はあるがイベント行が無い fixture（実データ同型） | legacy 判定 | `legacy_unverified_count` に入り `count` には入らない |
| (e) 陽性対照 | 同上 | イベント行を伴う正常 fixture | 同上 | `count` に入る |
| (h) 陰性（`correction_id` 重複時の fold 分離） | 重複 `correction_id` の基底が2件あっても、それぞれ独立に扱われる | `find_duplicate_ids` が検出する重複 fixture を `fold_corrections` に通す | fold のグルーピング | 例外を出さず、`bases_by_id` が最後の1件を正として fold される（構造的に重複を作れない前提なので「壊れずに縮退する」ことだけを確認する——重複自体は `append_correction_record` が拒否するため通常経路では発生しない） |
| Must1 (project scope) 陰性 | 値域不一致で全件除外される | `scope="same-project"` の fixture を実際の `classify_project_scope` 出力と突き合わせる | §3.3 の値域 | `("current","shared")` のような存在しない値と比較する実装なら `count == 0` になり検出できる |
| Must1 (project scope) 陽性対照 | 同上 | `scope="project-specific-other"` の fixture | 同上 | `count` から除外される（除外の意図どおり） |
| 欠陥3系 陰性（偶然一致・rev587dr1指摘） | draft_line は correction 本文由来でなければならない | `extracted_learning="変更後はテストを実行すること"` の correction に対し、対象ファイル中の無関係な既存行 `"テストを実行する"`（別件由来）を `draft_line` として渡す | `correction_message_sha256` の照合（§2.7） | `check_line_applied` は `matched=True` を返す（正規化後完全一致するため）が、`correction_message_sha256` が対象 correction の `extracted_learning` 正規化ハッシュと**一致しない**ことを別途検証するテストを追加し、この監査フィールドが偶然一致を検出できることを確認する（**イベント追記自体は防げない**——§2.7 で述べた通り本設計は言い換え問題を完全には解かない。これは既知の残存リスクとして§7に明記する） |
| 欠陥3系 陽性対照 | 同上 | `draft_line` が対象の correction 本文から直接生成されたケース | 同上 | `correction_message_sha256` が一致する |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:
- `corrections.jsonl` に**空行のみの行**を複数混在させた状態で `fold_corrections` を実行し、
  空行が `isinstance(rec, dict)` チェックで正しく除外されることを確認する
  （§3.1 の述語チェックの直接検証。第2版はこれを (h) の ordinal 文脈でしか検証していなかったが、
  ordinal を廃止したため文脈自体が消え、単独の述語検証として独立させる）
- `count_applied_reflections` の `read_health["readable"]=False`（例: 親ディレクトリの
  権限エラーを模した fixture）を強制注入し、`health.degraded=True` が返ることを確認する
  （§3.2 の health 集約ロジックが実際に劣化を検出することの確認。緑のまま残らないことを
  `health.degraded` を常に `False` に固定する変異ビルドで確認する——
  `verify-checks-by-breaking.md` の直接適用）

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示）:
境界値（`window_days` ちょうど30日目の日時）／Unicode 正規化差（全角/半角。
`correction_message_sha256` の NFC 正規化が全角/半角を統一しない点は§7で明記）／
`corrections.jsonl` が空行のみ・末尾に改行が無い場合／`reflect_draft_line` に改行を含む
複数行草稿／`append_correction_record` が `"retry_required"` を返した場合の呼出側リトライ方針
（本設計は未定義——rev587dr1 が指摘した commit protocol 全面設計を切り出したことの副作用として
残る）。

## 6. やらないこと（完成条件③の対象外の再掲・理由つき）

- **柱2の目標値（3件）の妥当性**: ユーザーが暫定値と明記して既に決定済み。再検討しない
- **hook / pitfall への反映測定**: 記録自体が存在せず、記録を作るには新しい保存先が要る。
  `#379` 新設凍結に抵触するため見送る
- **memory への反映測定**: 実績が188件中1件しかなく、測定基盤を作るコストに見合わない
- **`#379` 新設凍結の解除**: 本設計は既存 store（`corrections.jsonl`）への追記のみ
- **`results_board.py` への配線と表示**: §0対象外に理由を明記。別 issue とする
- **`reflect_status` の意味論の再定義**: 既存値の意味は変えない
- **`update_reflect_status`（既存の全文書き換え経路）の commit protocol 全面設計**:
  §0対象外・§2.1 に理由を明記。柱2の完成条件と独立した別問題として別 issue とする
- **`scripts/lib/weak_signals/ttl.py` の malformed 行削除を直す**: `weak_signals/` 配下は
  別セッションの担当範囲であり本設計では**触らない**
- **`prune/corrections.py` の decay 削除を tombstone 方式へ変える**: §4.2 で述べた通り
  リスクは実質的に低い（decay 90日 > 測定窓30日）ため、prune 自体の改修は別 issue とする
- **`promote.py` の `invalidate_idiom_corrections` のロック協調**: §0④の理由（柱2はイベント行
  だけを見る）により、この経路の安全性は柱2の完成条件に効かない
- **CLI に `correction_id`/ordinal 明示指定オプションを追加する**: §0対象外へ移設（頭の裁定・
  2026-09-01・裁定4）。`--apply`/`--skip` の「先頭一致」動作自体は変えない（§2.2）
- **正当な再反映（削除→別correctionでの再反映）を独立した2件として数える**（§5 (c)陰性3）:
  既知の限界として残す。真の解消には「反映イベントの原因 correction が変わったこと」を
  検出する仕組み（削除検出等）が要り、本 issue の総上限（設計1巡+実装1巡）を超える

## 7. 残る限界と未実測

- **(f)(g)(h) の `update_reflect_status` 自体の安全性は未解決のまま残る**。柱2の数字には
  影響しないが（§0④）、「`--apply` の応答が事実であること」（round 0 ①-2）という別の完成条件
  要素には引き続き影響しうる。別 issue での解決が必要（§8 で人間判断を仰ぐ）
- **正当な再反映が1件に潰れる**（§5 (c)陰性3・§6）。**頭の裁定（2026-09-01・裁定3）:
  許容する。** 柱2は「反映件数」の表示であり、この既知の限界は常に**過小計上**（実際に
  起きた反映件数より少なく数える）へ倒れる方向にしか働かない——`groups` によるグルーピングは
  同一 `(target_kind, target_path, draft_line)` の複数件を1件に**まとめる**だけで、
  存在しない反映を新たに作り出す方向（過大計上）には働かない。round 0 の守る対象①
  「柱2として表示する数字が実際に反映されたものと食い違うこと」との関係では、過大計上
  （実際より多く見せる＝ユーザーを誤って安心させる）の方が実害が大きく、本設計はその方向を
  構造的に避けている。過小計上（実際より少なく見せる）は「まだ測れていないものがある」と
  同じ性質のリスクであり、既に legacy_unverified_count・not_measured 等で許容している
  非計上パターンと同種
- **skill パスの分類は best-effort**（§2.5）。既知の2配置（`.claude/skills/` と `skills/`）
  以外の配置は `"other"` に落ち、`not_measured` として除外される（誤カウントはしないが
  過小カウントの可能性は残る）
- **`correction_message_sha256` は偶然一致を「防止」しない、監査補助にとどまる**
  （§2.7・§5「欠陥3系」・**頭の裁定（2026-09-01・裁定2）: 残存リスクとして許容する**）。
  3点を明記する:
  1. `correction_message_sha256` は「このイベントがどの correction 本文に由来するかを
     事後に突合できる」ための監査フィールドであり、**イベント追記そのものを止める
     ゲートではない**。`check_line_applied` の正規化後完全一致が真になれば、
     `correction_message_sha256` の値に関わらずイベント行は追記される
  2. **偶然一致が起きたときに何が起きるか**: 無関係な correction の `draft_line` が
     対象ファイルの既存行と偶然一致すると、実際には反映していない correction に対して
     `correction_applied` イベントが1件誤って追記され、**その1件が柱2の `count` に
     誤って計上される**（過大計上の一種——ただし「反映していないのに反映したと数える」
     という点で§0④の守る対象①に抵触しうる残存リスク）
  3. **許容した根拠**: round 0 ②信頼境界が「自分たちの運用ミスのみ。悪意ある偽装・
     意図的な数字の水増しは数えない」と定めており、偶然の部分文字列一致は
     **意図的な水増しではなく信頼境界の外側**（通常の運用ミス相当）にあたる。
     ハッシュ一致必須化（イベント追記自体をブロックする）は、言い換え（意訳）を
     許容する既存の `check_line_applied` の設計方針と衝突し、実装コストも増える
     （§2.7 で述べた通り「言い換えを許容するかどうか」自体が別の未決定事項）
- **`correction_message_sha256` の Unicode 正規化は NFC のみ**。全角/半角の統一は行わない
  （§5 未探索クラス）
- **`append_correction_record` が `"retry_required"` を返した場合の呼出側の扱いは未定義**
  （§5 未探索クラス）。実装1巡で決める
- **§4.2 の decay_days 実運用値は未確認**。実装1巡の開始時に
  `grep -c '"decay_days"' ~/.claude/evolve-anything/corrections.jsonl` で確認すること
  （本文書では実行していない＝factual-claims.md「取得時刻を併記できないなら書かない」を適用）
- **`migrate_correction_id_backfill.py` の実運用での実行有無は未確認**。実行されていなければ
  `legacy_unverified_count` が実際より大きく出る（`count` には影響しない）
- **§3.2 の集計関数のパフォーマンス**は未計測（corrections.jsonl の実サイズでの
  fold 所要時間）。実装1巡でベンチマークを取ること
- **効果の実測（本設計が実際に柱2を正しく測れるようにするか）は未検証**。実装1巡の完了後、
  `bin/evolve-audit --growth` で柱2の表示が `not_measured` から実測値に切り替わることを
  確認する必要がある——ただし §0対象外により `results_board` 配線自体が別 issue なので、
  この確認は results_board 配線 issue の完了条件になる（本 issue の完了条件ではない）
- **本設計のレビュー巡数は総上限2巡（設計1巡＋実装1巡）の設計側1巡を消費する**。第3版は
  同じ設計1巡の書き直しとして扱う（頭の裁定・冒頭参照）

## 8. 人間の判断が要る点

**頭の裁定（2026-09-01）**: 以下4件はすべて頭（オーケストレータ）が裁定済み。
疑問だけを残さず、裁定内容を対にして記録する。

| # | 疑問 | 裁定（2026-09-01） | 反映先 |
|---|---|---|---|
| 1 | `update_reflect_status` の commit protocol 全面設計（(f)(g)(h) の根本解消）を別 issue に切り出し、本 issue は「柱2の数字が (f)(g)(h) の影響を受けない」ことだけを保証する、という縮小方針でよいか | **承認**。理由: #588巡1→#567設計2巡→本issue巡2 と2巡連続で同族（全文書き換えの identity/lock 安全性）の指摘＝`review-round-cap.md` の族2巡打ち切りに該当し、ユーザーが「縮小して1巡」を裁定した | §0③ |
| 2 | 欠陥3（照合の紐付け強度）の残存: `correction_message_sha256` は監査補助にとどまり、偶然一致による誤ったイベント追記そのものは防げない。この残存リスクを許容するか、イベント追記自体をハッシュ一致必須にする（＝言い換えを許容しない）よう強化するか | **残存リスクとして許容する**（ハッシュ一致必須化はしない）。理由: round 0 ②信頼境界が「自分たちの運用ミスのみ。悪意ある偽装・意図的な水増しは数えない」であり、偶然一致はこの境界の外側にあたる | §7 |
| 3 | 正当な再反映（削除→別correctionでの再反映）が1件に潰れる既知の限界を許容するか | **許容する**。理由: 柱2は「反映件数」の表示であり、この限界は常に過小計上（実態より少なく見せる）へ倒れる。過小計上は安全側で、過大計上（実態より多く見せる）は起きない | §7 |
| 4 | CLI `correction_id`/ordinal 明示指定オプション（Should 項目）を実装1巡に含めるか | **含めない**。理由: あれば運用上便利だが、無くても柱2は測れる（`resolve_source_correction_id` が `"ambiguous"` を返す場合はイベント行を追記しないだけで欠落するだけであり、過大計上にはならない） | §0③ |
