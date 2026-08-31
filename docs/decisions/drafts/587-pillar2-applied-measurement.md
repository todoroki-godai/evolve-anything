# #587: 柱2「照合済み反映」を測れるようにする設計（第2版）

> **旧版（初版〜巡2）は不採用**。codex 設計レビュー2巡（`rev567p2`→`rev587d`）で
> `設計修正要`が2巡連続し、`review-round-cap.md` の族2巡打ち切りによりユーザー裁定
> **②切り出し**（issue #587 コメント 2026-08-30）。旧版の本文は git 履歴
> （`git log -p -- docs/decisions/drafts/587-pillar2-applied-measurement.md`）で参照できる。
> 旧版からの主要な変更点: (1) #588 由来の blocking (f)(g)(h) を追加で満たす
> (2) append-only の対象を「新規4フィールドの追加」から「`update_reflect_status` の
> 書込機構そのもの」へ広げた（issue #587 round 0「設計の出発点・再検討の対象外」item 1）
> (3) 設計中に「`reflect_status` を直接読む独立パーサが最低5箇所ある」ことを発見し、
> 移行計画に組み込んだ（§1・§4）。

対象: `#587`（前身 `#567`）。本文書は **設計のみ**。コードは1行も変更しない（実装は次巡）。
巡数の継承: 総上限2巡（設計1巡＋実装1巡・issue #587 コメント「巡3以降の一括承認」・
2026-08-30 に人間が一括承認）。本文書がその設計1巡の対象。

## 0. Round 0 完成条件（issue #587 コメント「Round 0 完成条件（改訂版）」より verbatim 転記）

### ① 守る対象

1. 柱2として表示する数字が、実際に反映されたものと食い違うこと
2. **「指定した correction を更新した」という報告が事実であること**（#588 から継承）

### ② 信頼境界（誰の能力を脅威に数えるか）

**自分たちの運用ミスのみ**。具体的に数えるのは: 手編集 / 別プロセスの追記（hook）/ 処理の中断 /
同時に走る2つの更新 / 移行スクリプトの未実行。
**数えない**: 悪意ある偽装・意図的な数字の水増し・第三者による改竄。

### ③ 対象外

- 柱2の目標値（3件）の妥当性。2026-08-26 にユーザーが「根拠なしの暫定値」と明記して決めたもの
- **hook / pitfall への反映測定**（記録自体が無く新しい保存先が要る＝#379 新設凍結に抵触）
- **memory への反映測定**（`auto_memory_broker` に配線は実在するが実データ188件中1件・別途）
- `#379` 新設凍結の解除。**本 issue は既存レコード列への追記のみで、新しい保存先を作らない**
- `results_board` の既存4軸表示の並び替え
- `reflect_status` の意味論そのものの再定義（値域の追加は可、既存値の意味変更は対象外）

### ④ blocking の定義

| | 内容 | 出所 |
|---|---|---|
| (a) | 反映日時が残らない | #567 巡1 |
| (b) | 反映先の種別が残らない（`CLAUDE.md` と skill が区別できない） | #567 巡1 |
| (c) | 同一の反映が複数件に数えられる | #567 巡1 |
| (d) | 無効化済み（idiom revoke）が件数に残る | #567 巡1 |
| (e) | 照合を通っていない旧レコードが件数に混ざる | #567 巡1 |
| (f) | 読取後に有効レコードが挿入・削除されると、別 correction を更新して成功を返す | #588 巡1 [Must]4 |
| (g) | 並行する追記が消える／2つの更新が後勝ちで巻き戻る。いずれも成功を返す | #588 巡1 [Must]5 |
| (h) | 同一 `source_correction_id` を持つレコードが2件以上あるとき、要求していないレコードまで更新される | 並行セッションの #588 別実装に対する tacchi 巡1 [Must]1 |

### ⑤ 検証方法

- 陰性試験（赤になるべき）を (a)〜(g) 各1件以上。(f)(g) は読取と書込の間に実際に別の書込を
  差し込んで再現すること（N プロセス同時実行は競合窓が µs で再現せず偽の安全網になる）
- 陽性対照を対で置く。陰性試験と混ぜて数えない
- 委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する。
  緑のまま残ったものが1件でもあれば完了扱いにしない。探索した入力クラスと変換も列挙する

### 設計の出発点（前巡の指摘から確定済み・再検討の対象外）

1. `update_reflect_status` は既存 JSON 行を書き換えており append-only ではない。
   `correction_applied` のような追記イベント行を書き、`source_correction_id` で元を参照し、
   read 側で fold するモデルへ直す
2. 部分文字列一致は紐付けにならない。correction ID と最終 draft 全文のハッシュで結ぶ二段階が要る
3. `reflect_applied_at` に `datetime.now()` を入れても「ファイルを編集した時刻」ではなく
   「`--apply` を叩いた時刻」。何の時刻かを名前と文書で明示する
4. 二重計上キーは相対/絶対/symlink で別グループ化すると偽陽性、削除後の再反映を潰すと偽陰性

**(h) の背景**: `make_source_correction_id` は「実質一意」としか保証しない
（`scripts/lib/memory_temporal.py:339-345`）。重複は信頼境界②の内側で作れる
（append-only store への再 ingest／promote の `store_write` 成功後に `_mark_promoted` が
失敗して再昇格）。**`source_correction_id` だけで集合化した瞬間に (h) が復活する**ため、
本設計は識別子を `(source_correction_id, ordinal)` の複合にする（§2.3）。

## 1. 現状（実測・file:line つき）

### 1.1 書込み側

- `skills/reflect/scripts/reflect.py:602-718` `update_reflect_status(status=...)` は
  **フルファイル読取 → 全行走査 → 全行書き戻し**（`filepath.write_text(...)`, line 706）。
  ロックを一切取らない。`reflect.py:640-647` に本 issue 相当の既知の限界コメントが
  既に書かれている（#588 の残タスクとして事前に明記済み）
- 同じファイルへの並行書込み経路が別に存在する:
  `hooks/correction_detect.py:164` `common.store_write("corrections.jsonl", record)` →
  `scripts/lib/rl_common/store_write.py:100-104` → `rl_common.append_jsonl` →
  `scripts/lib/rl_common/persistence.py:154-173` `append_jsonl` は **`fcntl.flock(f, LOCK_EX)`
  を伴うブロッキング追記**（persistence.py:159-166）。**`update_reflect_status` はこのロックを
  一切見ない**ので、`update_reflect_status` の読取後・書戻し前に hook がこのロックを取って
  追記すると、書戻しはその追記前の内容を丸ごと上書きし、追記された行を消す（blocking g の実体）
- `scripts/lib/correction_semantic/promote.py:584-649` `invalidate_idiom_corrections` も
  **第3の書込み経路**: `open(..., "r")` で全読取 → メモリ上で `invalidated=True` を立てる →
  `tempfile.mkstemp` + `os.replace`（atomic rename・promote.py:634-642）。これも
  `append_jsonl` のロックを見ない。**本 issue の blocking (f)(g)(h) には含まれないが、
  同じ「無ロックの read-modify-write」カテゴリの既存レースであることを記録しておく**
  （§6 で対象外として扱う理由の根拠）
- CLI の `--apply`/`--skip` 経路（`reflect.py:1230-1367`）は
  `make_source_correction_id(sid, ts) == <id>` の**先頭一致**で `target_index` を決め
  （`reflect.py:1264-1269`, `1330-1335`）、`update_reflect_status(..., [target_index], ...)` を呼ぶ。
  この `target_index` は「CLI が読んだスナップショットの配列位置」であり、書込み関数側で
  再同定していない（blocking f の実体）

### 1.2 読み取り側 — `reflect_status` を直接読む独立パーサが複数ある（設計中に発見した新事実）

`reflect.py` の `load_corrections`（`reflect.py:111-124`）以外に、**`corrections.jsonl` を
独自に `json.loads` で走査して `reflect_status` を直接読む箇所が最低5つ**ある。
これらは `reflect.py` の `load_corrections` を経由しないため、`update_reflect_status` の
書込み方式を変えても**自動的には追随しない**（`pitfall_copied_parse_convention_partial_fix`
と同型のリスク）:

| ファイル:行 | 何を読むか |
|---|---|
| `scripts/lib/prune/corrections.py:17-48`（`load_corrections`）/ `:51-117`（`cleanup_corrections`） | decay 超過の `applied`/`skipped` レコードを**物理削除**する。§4 で扱う |
| `scripts/lib/issues_summary.py:35-42` | `reflect_status == "applied"` 以外を unprocessed として数える |
| `scripts/lib/correction_semantic/correction_backlog.py:106` | `reflect_status == "promoted"` を在庫として数える |
| `scripts/lib/audit/memory.py:482` | `reflect_status == "applied"` を数える |
| `skills/genetic-prompt-optimizer/scripts/optimize_core.py:79` | `reflect_status == "applied"` を数える |
| `scripts/lib/discover/suppression.py:198` | `reflect_status in ("pending", "promoted")` でフィルタ |

**設計判断**: 完成条件③「`reflect_status` の意味論そのものの再定義（既存値の意味変更は対象外）」を
守るには、これら5+1箇所が**書込み方式の変更後も同じ値を見続けられる**ことが必須。
本設計は「`reflect_status` を書く経路を1本の関数に集約し、その関数を安全にする」方式
（§2.1）を採ることで、上記6箇所のコードは**変更不要**にする（§4 で理由を再確認する）。

### 1.3 実データ確認（`~/.claude/evolve-anything/corrections.jsonl`・2026-08-30 実測・引継ぎ）

`reflect_status == "applied"` のレコード3件がいずれも `target_path`/`draft_line`/適用日時に
相当するフィールドを持たない（旧版 §1 から引き継ぐ実測。再取得コマンド:
`jq -c 'select(.reflect_status=="applied")' ~/.claude/evolve-anything/corrections.jsonl`。
再現できない場合は「測定不能」と明記すること）。

## 2. 採用する記録モデル

### 2.1 「書込みを1本化した上で安全にする」＋「新規4フィールドは追記イベントで持つ」の併用

**設計判断（根拠つき）**: 完成条件①②④を同時に満たすには、次の2つを両方行う必要がある。
どちらか一方だけでは足りない:

1. **`update_reflect_status` 自体を identity-safe + lock-safe にする**（§2.2〜2.3）。
   これは `reflect_status` フィールドの**書込み経路**を直す話であり、値の意味は変えない。
   これをやらないと、§1.2 の6箇所の読み手全員が blocking (f)(g)(h) の影響を受け続ける
   （`reflect_status` 自体が壊れたまま）
2. **新規4フィールド（`reflect_applied_at`/`reflect_target_kind`/`reflect_target_path`/
   `reflect_draft_line`）は `corrections.jsonl` への追記イベント行として持つ**
   （round 0 出発点1）。これらは既存の読み手が誰も読んでいない**新規フィールド**なので
   「既存値の意味変更」に当たらず、append-only で導入してよい

**両立の理由**: 出発点1は「`update_reflect_status` は既存 JSON 行を書き換えており
append-only ではない」ことを問題としているが、問題の本体は「書き換えが識別子の
再確認なし・ロックなしで行われること」（＝blocking (f)(g)(h)）であり、「`reflect_status`
フィールドが基底レコード上に存在すること」自体ではない。**基底レコードの `reflect_status`
フィールドは維持したまま、その更新経路を安全にし、かつ同じ更新操作の副産物として
`correction_applied` 追記イベントも書く**（1回の `--apply` 呼び出しで両方が起きる。
片方だけ成功する状態を作らない・§2.4）。この折衷案を採らず「`reflect_status` を
基底レコードから完全に剥がして fold でしか読めない値にする」設計にすると、
§1.2 の6箇所すべてを本 issue の実装1巡で書き換える必要が生じ、`design-before-fanout.md`
が警告する横展開の破壊的変更になる（round 0 の総上限2巡・時間予算にも収まらない）。

### 2.2 identity-safe な `update_reflect_status`（blocking f・g を解消）

**書込みの流れを次の3段階にする**（`reflect.py:602` の関数を差し替える。関数シグネチャは
`correction: dict` 引数を追加する以外は現行を維持）:

1. **resolve（ロック外）**: 呼出元（CLI の `--apply`/`--skip`）が `source_correction_id`
   から対象を解決する。現行どおり `load_corrections()` で全件を読み、`make_source_correction_id`
   が一致するレコード群を**ファイル出現順**に並べ、要求された論理位置（§2.3）の
   レコードを1件選ぶ。選んだレコードの**生 JSON 行のテキスト（正規化せず `json.dumps`
   した文字列そのもの）の SHA-256** を `resolved_hash` として保持する
2. **commit（ロック内）**: `update_reflect_status` は `corrections.jsonl` を
   **`open(path, "r+")` で開き `fcntl.flock(f, LOCK_EX)` を取得してから**（`persistence.py`
   の `append_jsonl` と同じロック・同じファイルなので同じ mutex を共有し `store_write` の
   追記と直列化される＝blocking (g) の解消）、ロックを保持したまま**再度全行を読み直し**、
   `(source_correction_id, ordinal)` で対象レコードを再解決する（§2.3）。
   再解決した行の SHA-256 が resolve 時の `resolved_hash` と**一致しない場合、または
   その ordinal が存在しない場合は書込みを行わず `{"status": "conflict", "reason": "identity_mismatch"}`
   を返す**（blocking f の解消 — 読取後に他プロセスが該当行を書き換えた／挿入・削除した
   ことを検出し、黙って別レコードを更新しない）
3. 一致すれば、その1行だけを更新した内容でファイル全体を書き戻し、ロックを解放する。
   同一ロック区間内で `status == "applied"` のときは §2.4 のイベント行も同時に追記する
   （同じ書戻し1回に含める。2回の write に分けると片方だけ成功する状態を作れてしまう）

**呼出側の契約変更**: `update_reflect_status` の返り値に `"conflict"` を追加する。
CLI は `"conflict"` を `"not_found"` と同じく非0終了として扱う（黙って成功にしない）。

**この設計で (g) が解消される理由**: `LOCK_EX` は同一ファイルパスに対する
`fcntl.flock` 呼び出し同士を排他する。`append_jsonl`（hook からの新規 correction 追加）と
`update_reflect_status`（本関数）が同じロックを取り合うことで、「追記の直後に
古い内容で上書きする」という blocking (g) の具体的シーケンスが起こり得なくなる
（追記側がロックを離すまで本関数はロックを取得できず、取得した時点で必ず
追記後の内容を読み直す）。

**この設計で (f) が解消される理由**: 「読取後に挿入・削除されると別レコードを
更新する」という故障は、書込み直前にハッシュで**その内容がまだそこにあるか**を
確認することで検出可能になる。検出したら「黙って成功」ではなく `conflict` を返す
（round 0 完成条件②「柱2として表示する数字が実際に反映されたものと食い違うこと」を守る）。

### 2.3 識別子は `(source_correction_id, ordinal)` の複合にする（blocking h を解消）

**選ばなかった案**: `source_correction_id` の一意性を書込み時に強制する
（重複があれば ingest 側で reject する）。**却下理由**: 一意性の強制は ingest 側
（`hooks/correction_detect.py` / `store_write`）の変更を要し、round 0 対象外
（③「`#379` 新設凍結の解除」ではないが、write barrier の契約変更は本 issue の
スコープを超える）。かつ「実質一意」（`memory_temporal.py:343`）という既存の設計判断を
覆すことになり、信頼境界②（運用ミスのみ）の範囲内で重複が起きうる事実を消せない
（idiom promote の再昇格シナリオが示す通り、重複はコードのバグではなく正当な
運用フローの副作用でも起きる）。

**採用する案**: **要求された論理 ordinal の1件だけを更新する**。

- 定義: ある `source_correction_id` を持つ**基底レコード**（イベント行ではない。
  イベント行の判定は §2.4）をファイル出現順に並べたときの 0 始まり位置を `ordinal` とする
- CLI が `--apply <id>` / `--skip <id>` を解決するとき、現行コード（`reflect.py:1264-1269`,
  `1330-1335`）は「最初に一致した1件」を選ぶ（`break`）。これは事実上 `ordinal=0` を
  常に選ぶ実装なので、**振る舞いは変えない**（`ordinal` を明示化するだけ）。
  ordinal を選択可能にする CLI オプション追加は round 0 対象外（Should・§8）
- **ordinal の安定性**: 基底レコードは追記のみで物理削除・並べ替えをしないという
  前提の上で、ある `source_correction_id` を持つ基底レコード集合の**相対順序**は
  新しい基底レコードが追記されても変わらない（末尾に足されるだけ）。この前提が
  崩れる唯一の既知経路は `prune/corrections.py:112` の物理削除（§4 で扱う）。
  §2.2 の hash 再確認が、この前提が崩れた場合の**最終防衛**になる
  （ordinal が指す位置の中身が変わっていれば `conflict` を返す）
- fold（§3）でイベント行を基底レコードへ紐付けるときも、`(source_correction_id, ordinal)`
  の複合キーで結ぶ。**`source_correction_id` 単独でグルーピングしない**
  （単独グルーピングした瞬間 (h) が復活する、という round 0 の警告どおり）

### 2.4 追記イベント行のスキーマ

`corrections.jsonl` へ追記する新しい行の型。既存の基底レコード（`record_kind` フィールドを
持たない、または `"correction"`）と区別するため、**新フィールド `record_kind` を導入する**
（round 0「値域の追加は可」に該当・既存値は変更しない）:

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `record_kind` | `"reflect_event"`（固定） | ○ | 基底レコードと区別する discriminator。既存レコードにこのキーは無い（`record.get("record_kind") == "reflect_event"` で判定） |
| `event_type` | `"correction_applied"` \| `"correction_skipped"` | ○ | イベント種別。skipped も記録する理由: §2.1 の折衷で `reflect_status` は基底レコードに残るが、pillar2 集計とは独立に「いつ・何が原因で skip されたか」の監査証跡を同じ経路で持てる（新規フィールドなので既存読み手に影響しない） |
| `source_correction_id` | str | ○ | 対象の複合キー（`make_source_correction_id` 形式）。fold は `ordinal` と併用 |
| `source_ordinal` | int | ○ | §2.3 の ordinal |
| `reflect_applied_at` | str (ISO8601 UTC) | `event_type=="correction_applied"` のみ | **`--apply` を実行した時刻**（ファイル編集時刻ではない。フィールド名・本コメントの両方で明示する。round 0 出発点3） |
| `reflect_target_kind` | `"global_rule"` \| `"project_rule"` \| `"other"` | 同上 | §2.5 |
| `reflect_target_path` | str | 同上 | 反映先ファイルパス（正規化前） |
| `reflect_draft_line` | str | 同上 | 起草行の全文（正規化前・§2.6 の照合対象） |
| `resolved_hash` | str (SHA-256 hex) | ○ | §2.2 の再確認に使った基底レコードのハッシュ（監査用に保持。fold では未使用） |

**追記先は `corrections.jsonl` 自身**（新しい store を作らない）。根拠:
`scripts/lib/shrink_freeze.py:72` `FROZEN_STORES` に `"corrections.jsonl"` が既に列挙されて
おり、同一 basename への追記は「新しい store」ではない。`record_kind` という新フィールドを
既存 store の行に持たせることは `store_registry`/`_OBSERVABILITY_BUILDERS`/
`ADVISORY_PROPOSAL_ADAPTERS`/`WEAK_SIGNAL_CHANNELS`（凍結対象4種、`shrink_freeze.py:23-37`）
のいずれの登録簿にも新規エントリを作らないため、`assert_no_new_keys`（`shrink_freeze.py:261-275`）
の検査対象にならない。

**書込み方法**: §2.2 の commit 段階で、同一ロック区間・同一 `write_text` 呼び出しに
イベント行を追加する（基底レコードの更新と同じ書き戻しに含める。§2.1 で述べたとおり
「片方だけ成功」を避けるため）。

### 2.5 `reflect_target_kind` の分類ロジック（移設）

分類ロジックは `reflect.py:476-517` の `_rule_scope_identity` を
**`scripts/lib/reflect_apply_match.py` へ `classify_target_kind(target_path) -> str` として移設**し、
`reflect.py` 側は薄いラッパー（`_rule_scope_identity` は `classify_target_kind` の呼び出し +
既存の `repo_id`/`relative_path` 構築だけ残す）にする。移設理由: 現状 `_rule_scope_identity` は
revert 記録用にしか使われておらず、`reflect_apply_match.py` の外からは呼べない私有関数。
「target 種別の判定」は「反映先ファイルをどう解釈するか」という関心事で
`reflect_apply_match.py`（`check_line_applied`/`_normalize_bullet`/`_normalize_plain` — 起草行
正規化の既存単一ソース・`reflect_apply_match.py:38-46`）が既に持つ役割と同じ層なので、
2箇所独立実装（skill 側と柱2集計側）を避けるために1箇所へ寄せる
（`design-before-fanout.md` と同じ理由づけ）。戻り値の値域は
`"global_rule" | "project_rule" | "other"`（`_rule_scope_identity` が `None` を返す場合は
`"other"` に正規化する）。

### 2.6 malformed 行（JSON として parse できない行）の扱いを統一する

**設計中に指摘を受けて追加**（本 issue の記録モデルの一部として決める。実装対象は
`corrections.jsonl` の read/write のみ。`weak_signals/ttl.py` の修正はしない＝§6 参照）。

**現状（file:line）**:
- `reflect.py:687-696`（`update_reflect_status` の commit ループ）は、行が空、または
  `json.loads` が例外を出す行を**そのまま `updated_lines` へ温存**する（レコードとしては
  数えないが、ファイルからは消さない）。#588 で既にこの挙動になっている
- `scripts/lib/weak_signals/ttl.py:82-96`（`_rewrite`）は `read_signals` が返した
  **parse 成功レコードのみ**を書き戻す。`read_signals`（`scripts/lib/weak_signals/store.py:126-` /
  `_read_one`）は parse 失敗行を戻り値に含めず `dropped_lines` としてカウントするだけなので、
  `mark_expired` が書き戻すたびに malformed 行は**結果的に物理削除**される
  （並行セッション #539 レビューで [Should] 指摘済み・`weak_signals/` 配下のため本設計では触らない）

**本設計の決定（`corrections.jsonl` に適用する）: 温存（reflect.py の既存慣習を踏襲する）**。

理由:
1. `corrections.jsonl` は本設計が拡張する対象そのものであり、**同じファイルの中で
   温存と削除が混在する**（`update_reflect_status` は温存するのに、新設する
   §2.2 の commit ロジックだけ削除に倒す）と、round 0 完成条件②「指定した correction を
   更新したという報告が事実であること」の逆側の懸念——**指定していない副作用（他行の消失）が
   黙って起きる**——を新たに作り込むことになる。既存慣習と揃えるのが最小の変更
2. `persist-progress-incrementally.md`: 「実体は 在る／無い の2値でなく不完全という
   第三の状態がある」。`json.loads` に失敗する行は、①本当に破損したデータ
   ②書込み処理が中断され途中で切れた行（§2.2 の commit や `store_write` の
   `write_text`/`append_jsonl` は atomic ではあるが、`corrections.jsonl` を触る
   3つの経路（§1.1）のうち `invalidate_idiom_corrections` は `os.replace` による
   原子的 rename、`update_reflect_status`（新）も同様に `write_text` 一括書込みなので
   OS レベルの原子性はあるが、**プロセスが `write_text` の途中で kill される可能性は
   ゼロではない**）の、どちらかを外部から区別できない。**削除を選ぶと②を「なかったこと」に
   してしまう可能性を否定できない**。温存すればこのリスクを取らずに済む
3. **「温存し続けると壊れた行が永久に溜まる」問題への回答**: 新規リスクではない
   （既に `update_reflect_status` が2026-08-31 時点で同じ挙動を持つ・#588）。
   本設計はこれを悪化させない（±0）。恒久的な掃除（人間レビュー付きの隔離・削除）は
   本 issue のスコープ外とし、必要になった時点で別 issue として起票する
   （round 0 blocking (a)〜(h) のいずれにも malformed 行の蓄積は含まれていない）
4. **reader と writer で述語を単一ソース化する**: `fold_corrections`（§3）の
   `isinstance(rec, dict)` チェック（§3.1）と、§2.2 commit 段階の
   「`json.loads` 成功かつ dict」判定は、**同じヘルパー関数
   `reflect_fold.parse_record_line(line: str) -> dict | None`** を通す
   （成功すれば dict、失敗すれば `None` を返し、呼出側は `None` のとき「行をそのまま
   温存する」（write 側）か「レコードとして数えない」（read 側）かをそれぞれ選ぶが、
   **「parse できるか」の判定自体は1箇所**にする）。現行 `reflect.py:111-124`
   `load_corrections` と `reflect.py:692-696` の commit ループは、それぞれ独立に
   `try: json.loads(...) except JSONDecodeError` を書いており（**述語が2箇所に
   重複**している事実を設計中に発見）、実装1巡でこの重複を `parse_record_line` へ
   一本化することも完了条件に含める

## 3. read 時 fold の擬似コード

新規共有モジュール **`scripts/lib/reflect_fold.py`** を作る（`results_board.py` にも
`reflect.py` にも置かない。理由は §1.2 で見た通り読み手が6+1箇所に分散しており、
`reflect.py` に置くと `prune/corrections.py` 等が `reflect.py` を import する逆方向の
依存になり不自然。中立モジュールに置く）。

```python
# scripts/lib/reflect_fold.py（設計・未実装）
from dataclasses import dataclass
from typing import Optional

@dataclass
class FoldedCorrection:
    base: dict            # 基底レコードそのもの（既存フィールドは無改変で保持）
    reflect_applied_at: Optional[str] = None
    reflect_target_kind: Optional[str] = None
    reflect_target_path: Optional[str] = None
    reflect_draft_line: Optional[str] = None
    has_pillar2_fields: bool = False   # legacy 判定（blocking e）に使う

def fold_corrections(raw_records: list[dict]) -> list[FoldedCorrection]:
    """corrections.jsonl の生レコード列（基底+イベント混在）を基底単位に畳む。

    raw_records は load 側が生成した「ファイル出現順のリスト」であること
    （順序が ordinal の再現に必須）。
    """
    bases: list[dict] = []
    ordinal_by_id: dict[str, int] = {}      # source_correction_id -> 次に割り振る ordinal
    base_key_to_index: dict[tuple[str, int], int] = {}  # (id, ordinal) -> bases の index

    events: list[dict] = []

    for rec in raw_records:
        if not isinstance(rec, dict):
            continue  # 述語の単一ソース化（§3.1）
        if rec.get("record_kind") == "reflect_event":
            events.append(rec)
            continue
        sid, ts = rec.get("session_id", ""), rec.get("timestamp", "")
        if not sid or not ts:
            bases.append(rec)
            continue
        cid = make_source_correction_id(sid, ts)
        ordinal = ordinal_by_id.get(cid, 0)
        ordinal_by_id[cid] = ordinal + 1
        base_key_to_index[(cid, ordinal)] = len(bases)
        bases.append(rec)

    folded = [FoldedCorrection(base=b) for b in bases]

    # イベントは「同一 (id, ordinal) に対する最新の correction_applied」だけを採用する。
    # 複数回 apply イベントが記録されていても（§2.4 の監査証跡上は複数行になりうる）、
    # fold の結果は「今 applied かどうか」という状態1個であって、イベント回数の合算ではない
    # （これが blocking c の一部＝「同一の反映が複数件に数えられる」への直接対応）。
    latest_applied_event: dict[tuple[str, int], dict] = {}
    for ev in events:
        if ev.get("event_type") != "correction_applied":
            continue
        key = (ev.get("source_correction_id"), ev.get("source_ordinal"))
        # ファイル出現順で後のものが「最新」（同一 key への2回目の apply は理論上
        # 起こらない設計だが、起きても最新を正とする — fail-closed に振れる必要はない。
        # 対象が読取専用の状態導出であり書込みではないため②信頼境界の対象外）
        latest_applied_event[key] = ev

    for (cid, ordinal), i in base_key_to_index.items():
        ev = latest_applied_event.get((cid, ordinal))
        if ev is None:
            continue
        f = folded[i]
        f.reflect_applied_at = ev.get("reflect_applied_at")
        f.reflect_target_kind = ev.get("reflect_target_kind")
        f.reflect_target_path = ev.get("reflect_target_path")
        f.reflect_draft_line = ev.get("reflect_draft_line")
        f.has_pillar2_fields = bool(f.reflect_applied_at and f.reflect_target_kind)

    return folded
```

### 3.1 有効レコードの述語を単一ソース化する（round 0「もう1点」対応）

`reflect.py:111-124` の現行 `load_corrections` は `json.loads` が例外を出さなければ
**dict でなくても**（`[]` / `"x"` / `123`）配列に含める（`reflect.py:121` に型チェックが無い）。
`fold_corrections` は上記コードのとおり `isinstance(rec, dict)` を明示チェックし、
非 dict をここで弾く。**この述語チェックを `fold_corrections` の入口に置くことで、
基底レコードの読み込み元（`reflect.py`/`prune/corrections.py`/その他5箇所）が
どこであっても同じ述語を通過する**（呼出元は生の `json.loads` 結果を渡すだけにし、
dict チェックを各所で重複実装しない）。

### 3.2 `results_board.py` からの呼び出し方（設計のみ・実装は次巡）

`scripts/lib/pillar2_metrics.py`（新規モジュール。§4 で `results_board.py` に足さない
理由を述べる）が `reflect_fold.fold_corrections` を呼び、`FoldedCorrection` のうち
`has_pillar2_fields=True` かつ `invalidated` でない（§3.3）ものを対象に、
時間窓・種別ごとの集計を行う。

```python
def count_applied_reflections(
    slug: str, *, raw_records=None, now=None, window_days: int = 30
) -> dict:
    raw_records = raw_records if raw_records is not None else load_raw_corrections()
    folded = fold_corrections(raw_records)
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    eligible = []
    legacy_unverified = 0
    invalidated_count = 0
    for f in folded:
        if f.base.get("invalidated"):
            invalidated_count += 1
            continue  # blocking d
        if f.base.get("reflect_status") != "applied":
            continue
        if not f.has_pillar2_fields:
            legacy_unverified += 1  # blocking e
            continue
        ts = _parse_timestamp(f.reflect_applied_at)  # results_board._parse_timestamp を import
        if ts is None or not (window_start <= ts <= now):
            continue
        if classify_project_scope(f.base, slug) not in ("current", "shared"):
            continue
        eligible.append(f)

    # blocking c の残り半分: 「別 correction が同じ実世界の反映を指している」場合の重複。
    # (target_kind, target_path, normalize(draft_line)) でグルーピングし、グループ数を件数とする。
    groups: dict[tuple, list] = {}
    for f in eligible:
        key = (f.reflect_target_kind, f.reflect_target_path, _normalize_plain(f.reflect_draft_line))
        groups.setdefault(key, []).append(f)

    count = len(groups)
    applied_list = [
        {
            "target_kind": k[0], "target_path": k[1],
            "reflect_applied_at": min(x.reflect_applied_at for x in v),  # 最初に反映した時点
        }
        for k, v in groups.items()
    ][:10]

    return {
        "count": count,
        "legacy_unverified_count": legacy_unverified,
        "invalidated_count": invalidated_count,
        "applied_list": applied_list,
        "not_measured": {
            "hook": {"reason": "no_store"},
            "pitfall_memory": {"reason": "mtime_collision"},
        },
        "generated_at": now.isoformat(),
    }
```

**`_parse_timestamp` は import して再利用するが `_in_window` は再利用しない**
（旧版の記述誤り）: `results_board.py:250-264` `_in_window` は
`record.get("timestamp")` を**固定フィールド名**で読む（`_in_window` 内部で
ハードコード）。pillar2 は `reflect_applied_at` という別フィールド名を窓判定するため
`_in_window` をそのまま呼べない。**`_parse_timestamp`（`results_board.py:231-247`、
汎用・フィールド名を引数に取らない値パーサ）だけを import し、窓判定
（`window_start <= ts <= now`）は `pillar2_metrics.py` 側にインラインで書く**。
これにより `pitfall_iso8601_lexical_compare_tz_suffix.md` と同型の辞書順比較バグを
新モジュールで再生産することは避けつつ、フィールド名の違いを誤魔化さない。

### 3.3 `classify_project_scope` の再利用

プロジェクトスコープ判定は `reflect.py:140-` の既存 `classify_project_scope` を import して
使う（新規実装しない）。旧版が挙げていた `pj_slug_fast` 直接呼び出しは
`classify_project_scope` の内部実装詳細であり、`pillar2_metrics.py` からは
`classify_project_scope` という1段上の関数を呼ぶ（private 関数を跨いで import しない、
という旧版の意図はそのまま維持しつつ、より薄い依存にする）。

## 4. 移行

### 4.1 既存データ（新フィールドが無い `applied` レコード）

新フィールドを持たない既存の `reflect_status == "applied"` レコードは fold 後も
`has_pillar2_fields=False` のままなので、§3.2 の集計で自動的に `legacy_unverified_count`
に分類される（blocking e）。**移行スクリプトは不要**（fold が読み取り時に判定するため、
書き戻しをしなくても正しく除外される。round 0 出発点の「未実行環境が成立する」という
前提を、実行を前提にしない設計で吸収する）。

### 4.2 `prune/corrections.py` の decay 削除との相互作用（設計中に発見した新事実）

`scripts/lib/prune/corrections.py:105-115` `cleanup_corrections` は
`reflect_status in ("applied", "skipped")` かつ `timestamp` が `decay_days`
（既定 `DEFAULT_DECAY_DAYS = 90`・`scripts/lib/prune/config.py:9`）を超えたレコードを
**物理削除**する。これは §2.3 が前提とする「基底レコードは追記のみで物理削除されない」
という仮定を**厳密には満たさない**既存動作である。

**リスクの実際の大きさ**: pillar2 の測定窓は既定30日（`window_days=30`）、prune の既定
decay は90日。ある `applied` レコードが prune で削除されるのは、そのレコード自身の
`timestamp`（correction 検出時刻。`reflect_applied_at` ではない）から90日後であり、
`reflect_applied_at` はほぼ常に `timestamp` 以降なので、削除される頃には測定窓を
とうに外れている（`legacy_unverified_count`/`count` どちらにも数えない期間）。
**ただし `decay_days` はレコードごとに上書き可能**（`prune/corrections.py:91`
`record.get("decay_days", DEFAULT_DECAY_DAYS)`）なので、90日という前提は
**保証ではなく既定値にすぎない**。decay_days を30日未満に設定した運用が存在すれば、
ordinal 安定性の前提が崩れ、§2.2 のハッシュ再確認が `conflict` を返すことで
安全側に倒れる（blocking (f) の防御が効く）ため、**データが壊れることはないが
「本来数えられるはずの反映が conflict で失敗扱いになる」可能性は残る**。

**本設計での扱い（明示決定）**: この相互作用の完全解消（prune を tombstone 追記方式へ
変える等）は round 0 対象外とする（③に列挙されていないが、「既存 store への破壊的変更」を
新たに増やす提案であり、prune 自身の独立した issue にすべき規模）。**未実測**:
実運用で `decay_days` を既定値から変更しているレコードが存在するかは確認していない
（`grep '"decay_days"' ~/.claude/evolve-anything/corrections.jsonl` で確認できるが、
取得時刻を付記できないため本文書では実行しない。実装1巡の開始時に確認すること）。

### 4.3 §1.2 で見つかった5+1箇所の読み手

§2.1 の設計判断（`reflect_status` フィールド自体は基底レコード上に維持する）により、
**これら6箇所は変更不要**。実装1巡の完了条件に「この6箇所が無改修のまま既存テストが
通ること」を含める（回帰確認・§5 参照）。

## 5. 検証計画

各陰性試験に「壊す不変条件」と「通したい検査経路」を書く。同じ変異を陰性/陽性で
使い回さない（取消しは独立変異として数えない）。テストは
`scripts/lib/tests/test_reflect_fold.py`（fold 単体）と
`scripts/lib/tests/test_pillar2_metrics.py`（集計）と
`skills/reflect/scripts/tests/test_reflect_apply_identity.py`（§2.2/2.3 の identity-safe 書込み）
に新設する。

| # | 壊す不変条件 | 変異 | 通したい検査経路 | 期待結果 |
|---|---|---|---|---|
| (a) 陰性 | 反映日時が集計に使われる | fixture イベント行から `reflect_applied_at` を削除 | `count_applied_reflections` の window 判定 | `has_pillar2_fields=False` 扱いになり `legacy_unverified_count` に落ちる |
| (a) 陽性対照 | 同上 | `reflect_applied_at` を window 内の妥当な値のまま残す | 同上 | `count` に1件として残る |
| (b) 陰性 | 反映先種別が区別される | `reflect_target_kind` を欠落させたイベント fixture | `has_pillar2_fields` 判定 | legacy 扱いに落ちる（(a) と同じ経路で確認） |
| (b) 陽性対照 | 同上 | `reflect_target_kind="project_rule"` を持つ正常 fixture | 同上 | `count` に含まれ `applied_list` に `target_kind` が出る |
| (c) 陰性1（同一 base への重複イベント） | 反映は1状態として数える | 同一 `(source_correction_id, source_ordinal)` に対し `correction_applied` イベントを2行追記した fixture | `fold_corrections` の latest 選択 | `count == 1`（イベント2件でも基底レコードは1件） |
| (c) 陽性対照1 | 同上 | イベント1行のみの正常 fixture | 同上 | `count == 1` |
| (c) 陰性2（別 base だが同じ反映） | 別レコードでも同一反映は1件 | 異なる `source_correction_id` を持つ2基底レコードに、同一 `(target_kind, target_path, draft_line)` の applied イベントをそれぞれ追記 | グルーピング | `count == 1`（2ではない） |
| (c) 陽性対照2 | 同上 | `target_path` か `draft_line` のどちらかだけを変えた2件 | 同上 | `count == 2` |
| (d) 陰性 | 無効化済みは数えない | `reflect_status="applied"` かつ `invalidated=True` の基底 fixture（有効なイベントも付与） | invalidate フィルタ | `count` から除外、`invalidated_count` に計上、`legacy_unverified_count` には入らない |
| (d) 陽性対照 | 同上 | `invalidated` キー自体が無い正常 applied fixture | 同上 | `count` に含まれる |
| (e) 陰性 | 旧レコードは数えない | 実データと同型（新4フィールド無し・`reflect_status="applied"` のみ）の fixture | legacy 判定 | `legacy_unverified_count` に入り `count` には入らない |
| (e) 陽性対照 | 同上 | イベント行を伴う正常 fixture | 同上 | `count` に入る |
| (f) 陰性 | resolve 後に対象行の内容が変わっていたら別レコードを更新しない | テストで `update_reflect_status` を2段階に分けて呼ぶ: ① resolve だけ実行してハッシュを取得 → ②そのハッシュを使う直前に、**同じテスト内で** 対象行を書き換えた別内容で `corrections.jsonl` を上書き → ③保持していたハッシュで commit を呼ぶ | §2.2 の commit 段階のハッシュ再確認 | `{"status": "conflict", "reason": "identity_mismatch"}` を返し、ファイルは書き換わらない（内容を diff で確認） |
| (f) 陽性対照 | 同上 | ②の書き換えを行わない（対象行はそのまま） | 同上 | `{"status": "applied", ...}` を返し、対象行だけが更新される |
| (g) 陰性 | 並行する追記は消えない | テストで **`persistence.append_jsonl` が使うのと同じ `fcntl.flock` を先に保持した状態**で `update_reflect_status` を呼び出す（別スレッド/別プロセスを使わず、同一プロセス内でロックを取得した2つ目のファイルディスクリプタから呼ぶことで決定論的にブロックを再現する。`threading.Thread` で「ロックを取得→一定時間保持→解放」と「その間に `update_reflect_status` を呼ぶ」を順序固定で実行し、`update_reflect_status` がロック取得まで待機すること・待機後に読み直した内容に、ロック保持側が追記した行が含まれることを確認する） | §2.2 の `flock(LOCK_EX)` 取得 | 追記された行が消えずに残ったまま、対象行だけが更新される |
| (g) 陽性対照 | 同上 | ロック競合を起こさない単純な `--apply` 呼び出し | 同上 | 通常どおり成功する |
| (h) 陰性 | 重複 `source_correction_id` では要求した ordinal だけが更新される | 同一 `source_correction_id` を持つ基底レコードを2件（先頭=pending、後方=applied 済みイベント付き）を fixture に投入し、`ordinal=0`（先頭）を指定して `--skip` する | (source_correction_id, ordinal) 複合キーでの再解決 | 先頭のみ `skipped` になり、後方（`ordinal=1`）の `applied`/イベント行は変化しない |
| (h) 陽性対照 | 同上 | 重複のない単一レコードで同じ操作 | 同上 | 対象レコードのみ更新される |
| 欠陥3系 陰性（round 0 ①-2 の一部） | draft_line は correction 本文由来でなければならない | `reflect_draft_line` に対象ファイル中の無関係な既存行（`extracted_learning` と無関係な文字列）を渡す | `check_correction_applied`（`reflect_apply_match.py` に新設） | `{"matched": False, "reason": "draft_line_not_from_correction"}` を返し、イベント行は追記されない |
| 欠陥3系 陽性対照 | 同上 | `draft_line` が対象ファイルに実在し、かつ `extracted_learning` の部分文字列でもある | 同上 | `{"matched": True}` |

**委譲側が挙げた回避手段とは種類の違うものを2件以上、実際に適用して結果を報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**:
- 上表の変異とは別に、`corrections.jsonl` に**空行のみの行**を複数混在させた状態で
  (h) の重複シナリオを再実行し、ordinal 計算が空行を数えないことを確認する
  （§3.1 の述語チェックが空行由来の非 dict をどう扱うかの追加確認。空行は
  `json.loads` が例外を出すので `fold_corrections` の呼出前段階で除外される想定だが、
  除外を担う関数（`load_corrections` 相当）側のテストとして書く）
- `resolved_hash` の再確認を**素通りさせる**変異（§2.2 の commit 段階でハッシュ比較の
  行だけをコメントアウトした変異ビルドを一時的に作り、(f) の陰性試験が**緑のまま
  残らない**（＝検査自体が有効に赤化を検出できる）ことを確認する
  （`verify-checks-by-breaking.md` の「検査の有効性は壊して赤くする」の直接適用）

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示）:
境界値（`window_days` ちょうど30日目の日時）／Unicode 正規化差（全角/半角）／
`corrections.jsonl` が空行のみ・末尾に改行が無い場合／`reflect_draft_line` に改行を含む
複数行草稿／§2.2 のロック待機がタイムアウトする経路（`flock` は既定ブロッキングで
タイムアウト概念が無いため、デッドロック検出は本設計のスコープ外）。

## 6. やらないこと（完成条件③の対象外の再掲・理由つき）

- **柱2の目標値（3件）の妥当性**: ユーザーが暫定値と明記して既に決定済み。再検討しない
- **hook / pitfall への反映測定**: 記録自体が存在せず、記録を作るには新しい保存先が要る。
  `#379` 新設凍結に抵触するため見送る（`not_measured.hook`/`not_measured.pitfall_memory`
  として §3.2 の戻り値に固定で出す）
- **memory への反映測定**: `auto_memory_broker` への配線はあるが実績が188件中1件しかなく、
  測定基盤を作るコストに見合わない。別 issue で扱う
- **`#379` 新設凍結の解除**: 本設計は既存 store（`corrections.jsonl`）への追記のみ
- **`results_board.py` の既存4軸表示の並び替え**: 表示順は変えない
- **`reflect_status` の意味論の再定義**: §2.1 の設計判断により既存値の意味は変えない
- **`scripts/lib/weak_signals/ttl.py` の malformed 行削除を直す**: §2.6 で
  `corrections.jsonl` 側の malformed 行の扱い（温存）を決めたが、`weak_signals/` 配下は
  別セッションの担当範囲であり本設計では**触らない**。§2.6 の決定は `corrections.jsonl` に
  限定した記録モデルの一部であり、`weak_signals.jsonl` へ遡及適用しない
- **`prune/corrections.py` の decay 削除を tombstone 方式へ変える**: §4.2 で述べた通り
  リスクは実質的に低く（decay 90日 > 測定窓30日）、§2.2 のハッシュ再確認が安全側の
  fail-closed を保証するため、prune 自体の改修は別 issue とする
- **`promote.py` の `invalidate_idiom_corrections` のロック協調**: §1.1 で見つけた
  第3の無ロック書込み経路だが、本 issue の blocking (f)(g)(h) の対象（`update_reflect_status`
  経路）には含まれない。修正すると本 issue のスコープを超えて promote.py の独立した
  レビューが必要になる。**放置してよい理由**: §2.2 のハッシュ再確認により、
  `update_reflect_status` の commit 直前に `invalidate_idiom_corrections` が同じ行を
  書き換えた場合は `conflict` を返して安全側に倒れる（データ破壊はしない。
  ただし正当な apply が偶発的に失敗しうる — 発生確率は同一 correction に対して
  ミリ秒オーダーで両方が同時実行される場合のみで、通常運用では極めて稀）
- **CLI に ordinal 明示指定オプションを追加する**（§2.3 で触れた Should 項目）:
  現行の「先頭一致」動作を変えない前提なので、round 0 の必須スコープには含めない

## 7. 残る限界と未実測

- **§4.2 の decay_days 実運用値は未確認**。実装1巡の開始時に
  `grep -c '"decay_days"' ~/.claude/evolve-anything/corrections.jsonl` で確認すること
  （本文書では実行していない＝factual-claims.md「取得時刻を併記できないなら書かない」を適用）
- **§2.2 のロック機構は `fcntl` 前提**（`persistence.py:11-15` と同じ `_HAVE_FCNTL` フォールバック
  が必要 — `fcntl` が使えない環境ではロックなしに退化する。この環境依存は既存
  `append_jsonl` が既に持つ制約であり、本設計はそれを引き継ぐだけで新たな環境依存は
  追加しない）
- **flock はプロセス間ロックであり、同一プロセス内の2スレッドが同じ fd を共有する
  ケースでは機能しない**場合がある（POSIX flock の仕様上、同一プロセスの別 fd 間では
  排他されるが、同一 fd の複製（dup）間では排他されないことがある）。CLI は
  単一プロセス・単一 fd での逐次実行が前提のため実運用上は問題にならないが、
  将来 reflect.py が並行化された場合は再検証が必要
- **§3.2 の集計関数のパフォーマンス**は未計測（corrections.jsonl の実サイズでの
  fold 所要時間）。実装1巡でベンチマークを取ること
- **効果の実測（本設計が実際に柱2を正しく測れるようにするか）は未検証**。
  実装1巡の完了後、`bin/evolve-audit --growth` で柱2の表示が `not_measured` から
  実測値に切り替わることを確認する必要がある（`report-by-four-pillars.md` の
  rule 本文修正は §3.2 で述べたとおり今回のスコープ外だが、コード側が揃った後の
  rule 修正レビュー1巡は別途必須）
- **本設計のレビュー巡数は総上限2巡（設計1巡＋実装1巡）の設計側1巡を消費する**。
  本巡で `設計修正要` が出た場合、`review-round-cap.md` の族2巡打ち切り条項が
  再度該当するかどうかは、前巡（`rev587d`）と今巡の族タグが**同一族**かどうかで
  判定する必要があり、事前に断定しない

## 8. 人間の判断が要る点

- **§2.1 の折衷案（`reflect_status` は基底レコード維持＋新4フィールドのみイベント化）**が
  round 0 出発点1「`update_reflect_status` を append-only モデルへ直す」の意図を
  十分に満たしているか。より厳格な解釈（`reflect_status` 自体も基底レコードから
  剥がす）を採る場合、§1.2 の6箇所全ての改修が実装1巡のスコープに追加される
  （総上限2巡内に収まるかは未検証）
- **§4.2 で prune との相互作用を対象外としたこと**の承認。`decay_days` をカスタムで
  30日未満に設定している運用が実在すれば、正当な apply が稀に `conflict` になる
  可能性を許容するかどうか
- **欠陥3（照合の紐付け強度）の緩さ**: `extracted_learning` の部分文字列一致は
  「緩すぎないか」の最終判断が要る（旧版から引き継ぐ未決定事項）
- **CLI ordinal 明示指定オプション**（§6 で見送った Should 項目）を実装1巡に含めるか
