# #587: 柱2「照合済み反映」の6欠陥を直す設計

> **この設計は採用されていない（2026-08-30）。** codex の設計レビューで `設計修正要`・[Must] 7件。
> 「反映日時」「照合の結びつき」「二重計上」の3族が前巡から2巡連続したため、
> `review-round-cap.md` の族2巡打ち切りが発動し、ユーザー裁定 **②切り出し** となった。
> **作り直す際の必須の出発点**（レビュー指摘の要点）:
> 1. `update_reflect_status` は既存 JSON 行を書き換えており **append-only ではない**。
>    `correction_applied` のような**追記イベント行**を書き `source_correction_id` で元を参照し、
>    read 側で fold するモデルへ直す
> 2. `load_corrections` は空行・壊れた行を捨てた配列 index を返すのに
>    `update_reflect_status` は元ファイルの物理行を同じ index で更新する
>    （**空行が1つあるだけで別レコードを書き換える既存バグ**）
> 3. `reflect_applied_at` に `datetime.now()` を入れても「ファイルを編集した時刻」ではなく
>    「`--apply` を叩いた時刻」。窓判定も `_in_window` が固定で `record["timestamp"]` を読む
> 4. 部分文字列一致は紐付けにならない（短い汎用文が偶然含まれる）。correction ID と
>    最終 draft 全文のハッシュで結ぶ二段階が要る
> 5. `reflect_target_kind` の値域では `CLAUDE.md` と skill がどちらも `other` になり区別できない
> 6. 二重計上キーは相対/絶対/symlink で別グループ化（偽陽性）、削除後の再反映を潰す（偽陰性）
> 7. 陰性試験が blocking を検証していない（日時フィールドの存在しか壊さない等）
>
> レポート全文: `~/.codex-watch/rev587d-20260830-231856-89057.report`（ローカル・消えうる）。
> 要点は issue #587 のコメントに転記済み。

対象: `#587`（前身 `#567`・分母裁定コメント 2026-08-30・codex `rev567p2` 巡1 判定 `修正要`）。
本文書は **設計のみ**。コードは1行も変更しない（実装は次巡）。

## 0. Round 0 完成条件（`review-gate` 定型・issue #587 コメントより verbatim 転記）

1. **守る対象**: 柱2として表示する数字が、実際に反映されたものと食い違うこと（欠陥1〜6の各経路）
2. **信頼境界**: 脅威に数えるのは自分たちの運用ミスのみ。悪意ある偽装・意図的な数字の水増しは数えない
3. **対象外**: 柱2の目標値（3件）の妥当性／hook・pitfall への反映測定（記録自体が無く新設が要る）／
   memory への反映測定（経路はあるが実績1件・別途）／#379 凍結の解除／`results_board` の既存4軸表示の並び替え
4. **blocking**: (a) 反映日時が残らない (b) 反映先の種別が残らない (c) 同一の反映が複数件に数えられる
   (d) 無効化済みが件数に残る (e) 照合を通っていない旧レコードが件数に混ざる
5. **検証方法**: 陰性試験を (a)〜(e) 各1件以上＋陽性対照を対で置く

継承: 総上限2巡（設計1巡＋実装1巡・族2巡打ち切り条項は今回不該当と人間裁定済み）。

## 1. 現状（実測・file:line つき）

- `skills/reflect/scripts/reflect.py:602-663` `update_reflect_status(status="applied")` は
  `reflect_apply_match.check_line_applied` で `target_path` に `draft_line` が実在するかを
  確認したうえで `record["reflect_status"] = status` だけを書く（**line 655**）。
  戻り値には `target`/`reason` が入るが **JSONL には一切残らない**（欠陥1・2・3・4・6の根）
- `scripts/lib/reflect_apply_match.py:49-76` `check_line_applied` は `target_path` に
  `draft_line`（呼出者が渡す任意の文字列）が正規化後完全一致するかだけを見る。
  correction 自身の内容（`extracted_learning`/`message`）とは一切照合しない（欠陥3）
- `scripts/lib/correction_semantic/promote.py:584-632` `invalidate_idiom_corrections` は
  `invalidated=True` を書くのみで `reflect_status` に触れない（欠陥5）
- `scripts/migrate_reflect_promoted_status.py` は `reflect_confirmed` かつ `applied` の
  旧レコードを `promoted` へ戻す1回限りの移行だが、**既定 dry-run・`--apply` 未実行なら
  何も変わらない**（欠陥6の具体的な発生源の1つ）
- **実データ確認**（`~/.claude/evolve-anything/corrections.jsonl`・2026-08-30 実測）:
  `reflect_status == "applied"` のレコード3件を抽出しキー集合を確認したところ、
  いずれも `target_path`/`draft_line`/適用日時に相当するフィールドを一切持たない
  （キー集合: `confidence, correction_type, ..., reflect_status, ..., timestamp` のみ）。
  欠陥6は仮説でなく**現に存在する状態**
- `scripts/lib/results_board.py`（実測 690 行・`wc -l` 2026-08-30）の
  `decisions.accepted`（line 298-321）は `optimize_history` の accept を数えており、
  柱2の分母裁定（corrections.jsonl の照合済み applied）とは**別物のまま**残っている。
  `.claude/rules/report-by-four-pillars.md`（PJ rule）は既に新分母を指しているため、
  **rule の記述と実測コードが既に食い違っている**（本 issue が直す対象そのもの）

## 2. 欠陥ごとの直し方

### 欠陥1: 反映日時が残らない

**新フィールド `reflect_applied_at`**（ISO8601 UTC・`datetime.now(timezone.utc).isoformat()`）を
`update_reflect_status(status="applied")` が一致確認に成功した分岐（`reflect.py:637` 以降、
`record["reflect_status"] = status` と同じ book-keeping ブロック）で同時に書く。
`status != "applied"` の呼び出し（skipped 等）では書かない（既存 `--skip-all` は無改修）。

### 欠陥2: 反映先の種別が残らない

**新フィールド `reflect_target_kind`**（値域 `"global_rule" | "project_rule" | "other"`）を同時に書く。
分類ロジックは `reflect.py:476-517` の `_rule_scope_identity` を **`scripts/lib/reflect_apply_match.py`
へ `classify_target_kind(target_path) -> str` として移設**し、`reflect.py` 側は薄いラッパー
（`_rule_scope_identity` は `classify_target_kind` の呼び出し + 既存の `repo_id`/`relative_path` 構築だけ残す）
にする。移設理由: 現状 `_rule_scope_identity` は revert 記録用にしか使われておらず、
`reflect_apply_match.py` の外からは呼べない私有関数。**target 種別の判定は「反映先ファイルを
どう解釈するか」という関心事で `reflect_apply_match.py`（§6.2 正規化の単一ソース）が既に持つ
役割と同じ層**なので、2箇所独立実装（skill 側と柱2集計側）を避けるために1箇所へ寄せる
（`design-before-fanout.md` と同じ理由づけ）。

### 欠陥3: 照合が修正本文と結びついていない

`check_line_applied` に**correction 自身の内容との紐付け**を追加する。関数を分離する:

- 既存 `check_line_applied(target_path, draft_line)` は**そのまま残す**（ファイル内一致という
  独立した関心事。呼び出し元を壊さない）
- 新関数 `check_correction_applied(correction: dict, target_path: Path, draft_line: str) -> dict` を追加。
  内部で ① `check_line_applied` の結果 ② `_normalize_plain(draft_line)` が
  `_normalize_plain(correction.get("extracted_learning") or "")` の**部分文字列**であること、
  の両方を要求する。①のみ真・②が偽なら `{"matched": False, "reason": "draft_line_not_from_correction"}`
  を返す（黙って成功にしない・§6.1 と同じ規約）
- `reflect.py` の `--apply` 経路（`reflect.py:1231-1234`）は `update_reflect_status` へ渡す前に
  `check_correction_applied` を通す（`update_reflect_status` のシグネチャに
  `correction: dict` 引数を追加し、内部で `check_line_applied` の代わりに
  `check_correction_applied` を呼ぶ）

**選ばなかった案**: `draft_line` 自体を「correction の一意 ID」で紐付ける（`extracted_learning` の
ハッシュを比較）。**却下理由**: `extracted_learning` は人間が編集して要約することが多く
（reflect スキルの対話フローで LLM が言い換える）、完全一致要求は正当な適用まで
`no_match` にする（過剰検出だが誤検出の方向が「安全側」ではなく「使い物にならない」側になる）。
部分文字列一致は緩いが `verify-checks-by-breaking.md` の「迷ったら過剰検出に倒す」原則との
バランスで、**無関係な既存行を通す攻撃を防ぐには十分**（欠陥3の脅威モデルは運用ミスのみ・
round 0 完成条件②）。

### 欠陥4: 二重計上規則が無い

**書込み時に完全防止はしない**（`corrections.jsonl` は append-only・過去記録を検索してから
書く追加コストは見送る＝round 0 の対象外）。**読み取り（集計）側でのみ dedupe する**:

新フィールド `reflect_target_path`（`target_path` の文字列そのもの）と `reflect_draft_line`
（正規化前の `draft_line` 全文）を `reflect_applied_at`/`reflect_target_kind` と同時に永続化する
（欠陥3の照合対象を後から人間が確認できる監査証跡にもなる）。

集計関数（§4 で定義する `pillar2_metrics.count_applied_reflections`）は、対象レコード集合を
`(reflect_target_kind, reflect_target_path, normalize(reflect_draft_line))` でグルーピングし、
**グループ数**を件数として返す（レコード数ではない）。同一 target・同一 draft_line が2レコードに
分かれていても1件と数える。グループ内の代表タイムスタンプは最も古い `reflect_applied_at`
（「最初に反映した時点」を採用日時とする・後追いの重複記録で日付が新しく見えることを防ぐ）。

### 欠陥5: 無効化済みが残る

集計関数は `invalidated is True` のレコードを**無条件で除外**する（`reflect_status` の値に
関わらず）。`scripts/lib/correction_semantic/promote.py:584` の `invalidate_idiom_corrections`
自体は変更しない（`reflect_status` を書き換える設計変更は revoke の責務を超える・
「無効化済みは数えない」を read 側の1箇所に閉じる方が壊れにくい＝`learning_derive_state_from_logs_not_forward_write`
と同じ理由づけ）。

### 欠陥6: 旧レコードは照合なしで `applied`

**新分母のフィールド（`reflect_applied_at`）が無い `reflect_status == "applied"` レコードは
集計から除外する**（数えない側に倒す＝fail-closed。round-0 完成条件①「食い違い」を作らない）。
除外した件数は `legacy_unverified_count` として集計結果に**別枠で残す**（黙って消さない・
`results_board.py` 既存の `excluded_reasons` パターンと同型）。

**後方互換の扱い（明示決定）**: 「無視するか別カウントにするか」→ **両方**。柱2の分子には
含めない（無視）が、`legacy_unverified_count` として診断表示に出す（別カウント）。
移行スクリプトの実行有無に集計結果を依存させない（欠陥6の核心は「未実行環境が成立する」
ことなので、未実行のままでも集計が壊れない設計を優先する）。

**任意の一次対応（round 0 blocking には含めない・Should）**: 既存の
`scripts/migrate_reflect_promoted_status.py` と同型の1回限り移行スクリプト
`scripts/migrate_legacy_applied_unverified.py` を新設し、`reflect_status == "applied"` かつ
`reflect_applied_at` が無いレコードを `promoted` へ戻す（前例と同じ「検証されていない
`applied` は `promoted` に格下げする」規約）。既定 dry-run・`--apply` のみ実書込。
**これは新規 store ではなく既存 `corrections.jsonl` への read-modify-write** なので #379
凍結には抵触しない。round 0 の blocking (e) は集計側の除外だけで満たせるため、この
スクリプトは実装1巡の必須スコープではなく、余力があれば同巡で足す任意項目とする。

## 3. データモデル（`corrections.jsonl` への追加フィールド）

| フィールド | 型 | 書込タイミング | 既存レコードでの扱い |
|---|---|---|---|
| `reflect_applied_at` | str (ISO8601 UTC) | `check_correction_applied` 一致確認成功時 | 無し → legacy 扱い（§2 欠陥6） |
| `reflect_target_kind` | `"global_rule"\|"project_rule"\|"other"` | 同上 | 無し → legacy 扱い |
| `reflect_target_path` | str | 同上 | 無し |
| `reflect_draft_line` | str（正規化前全文） | 同上 | 無し |

**追記のみ・既存レコードは書き換えない**（`corrections.jsonl` の append-only 契約を維持）。
`reflect_status` フィールド自体の値域・書込み経路は変更しない。**これは既存 store
（`corrections.jsonl`）への新フィールド追加であり、新しい store ではない**（#379 判定・§6 で自己確認）。

## 4. 集計の単一ソースをどこに置くか

**新規モジュール `scripts/lib/pillar2_metrics.py` を作る。`results_board.py` には足さない。**

根拠:
- `results_board.py` は実測 690 行（`wc -l` 2026-08-30）で `file-size-budget.md` の
  500行分割検討線を既に超えている。800行の分割必須線には未達だが、柱2集計ロジック
  （JSONL 走査・グルーピング・legacy 除外・not_measured 5種の組み立てで概算 80〜120行）を
  そのまま足すと 770〜810 行台になり分割必須線に触れる。**新規ロジックは新モジュールに置き、
  `results_board.py` 側は呼び出し + 1キー追加（+15行未満）に留める**
- 柱2集計は `corrections.jsonl` という `results_board.py` がこれまで読んでいなかった
  ストアを新規に読む。既存の `optimize_history`/`correction_rate`/`capture_recall` の
  3系統読取り関心事とは別のデータソースなので、モジュール分離の方が呼び出し元を
  壊さず追加できる（既存3系統の関数シグネチャ・戻り値契約に触れない）
- `pillar2_metrics.py` の公開 API:
  - `count_applied_reflections(slug: str, *, corrections: list[dict] | None = None, now: datetime | None = None, window_days: int = 30) -> dict`
    戻り値: `{"count": int, "legacy_unverified_count": int, "applied_list": [...最大10件...], "not_measured": {"hook": {...}, "pitfall_memory": {...}, "skill": {...}}, "generated_at": iso str}`
  - `corrections` を省略時は `corrections.jsonl` を読む（テストは fixture を注入）
- タイムスタンプの窓判定は **`results_board.py` の `_parse_timestamp`/`_in_window` を import して
  再利用する**（新規実装しない）。理由: `corrections_insights.py:55` の
  `rec.get("timestamp", "").replace("Z", "+00:00") < cutoff` は文字列辞書順比較であり、
  `pitfall_iso8601_lexical_compare_tz_suffix.md`（MEMORY 記録済み既知 pitfall）と同型の
  誤順序バグを踏む経路。**同じ穴を新モジュールで再生産しない**
- プロジェクトスコープの判定は `correction_semantic/promote.py:30` の `_normalize_project_path`
  と同じ正規化（`pj_slug.pj_slug_fast`）を使う。**private 関数を跨いで import しない**ため、
  `pj_slug_fast` を直接呼ぶ（`promote.py` も内部で同じ関数を呼んでいるだけで、
  正規化ロジック自体は `pj_slug` が単一ソース。2箇所が同じ下位関数を呼ぶのは重複でない）

### `results_board.py` 側の変更（設計のみ・実装は次巡）

`build_results_board` の戻り値に新規キー `applied_reflections` を追加する
（既存 `decisions` キーは変更しない＝「採用した改善」と「柱2 照合済み反映」は
別物のまま両方表示する。rule 本文が既にこの区別を明記済み・§1 参照）:

```python
applied_reflections = collect_board_measurements(...)  # 実装時に read_measurement でラップ
board["applied_reflections"] = applied_reflections
```

`measurement_result.py` 側に `render_applied_reflections_health` を追加し、
`render_decisions_health`（既存・line 226-240）と同じ「measured/reason」パターンを踏襲する
（読取失敗を握り潰さない・#567 巡1 codex Must と同型の再発を防ぐ）。

**`.claude/rules/report-by-four-pillars.md` の文言修正は今回のスコープ外**
（issue 制約: `.claude/rules/` 配下は今回触らない）。実装1巡でコード側が揃った後、
rule 側の測定コマンド説明を実コードに合わせて別途レビュー1巡を通す。

## 5. #379 新設凍結の自己確認

`scripts/lib/shrink_freeze.py` を読んだ（2026-08-30）。凍結対象は
①新しい store ②新しい observability section ③新しい advisory proposal adapter
④新しい weak_signal channel の4種。本設計は:

- `corrections.jsonl` への**フィールド追加**（既存 store・新規 store ではない）
- `pillar2_metrics.py` は `results_board.py` の戻り値に**新規キーを1つ足す**が、
  これは `store_registry`/`_OBSERVABILITY_BUILDERS`/`ADVISORY_PROPOSAL_ADAPTERS`/
  `WEAK_SIGNAL_CHANNELS` のいずれの登録簿にも新規エントリを作らない
  （`build_results_board` の戻り値 dict は元々これらの登録簿の対象外）
- `migrate_legacy_applied_unverified.py`（§2 欠陥6 の Should 項目）は既存 store への
  read-modify-write であり、`migrate_reflect_promoted_status.py` と同型の1回限りスクリプト

**結論: #379 凍結には抵触しない。**

## 6. 測れない反映先（`not_measured` として明示）

issue #587 コメント（2026-08-30・柱2分母確定）の実測を引き継ぐ:

| 反映先 | 状態 | 本設計での扱い |
|---|---|---|
| rule / CLAUDE.md への行追記 | 測れる | §2〜4 の対象 |
| hook の新設・変更 | 記録が存在しない | `not_measured.hook = {"reason": "no_store"}` を固定で返す（対象外・新設しない） |
| memory / pitfall（`.md` 直書き） | 時系列で数えられない（全188ファイル mtime 同一） | `not_measured.pitfall_memory = {"reason": "mtime_collision"}` |
| skill | `check_line_applied` は「既存ファイルへの1行追記」形にしか対応しない | `not_measured.skill = {"reason": "apply_match_scope_limited"}`（`reflect_target_kind == "other"` で target が skill 相当のものは `legacy_unverified_count` 側にも `count` 側にも入れず、`not_measured.skill` の参考数のみ増やす。**この扱いは round 0 対象外の「hook/pitfall/memory 測定」とは別**で、skill 宛の `applied` レコード自体は `reflect_target_kind="other"` として §2 の新フィールドは持てるが、`check_correction_applied` の一致判定対象にはなる。「測れない」の意味は skill ファイルの箇条書き形式が `reflect_apply_match` の bullet/plain 判定から外れるケースがある、という既知の限定であり、機構的に排除しているわけではない） |

## 7. 陰性試験・陽性対照（各欠陥 (a)〜(e) 対応、テストは `scripts/lib/tests/test_pillar2_metrics.py` 新設）

**表記対応**: 完成条件④の (a)〜(e) は欠陥1〜6のうち5項目（欠陥1→(a), 欠陥2→(b), 欠陥4→(c), 欠陥5→(d), 欠陥6→(e)）。
欠陥3（照合の非結合）は完成条件①「守る対象」に含まれる派生欠陥として (a)〜(e) とは別に1件追加する。

| # | 壊す不変条件 | 変異 | 通したい検査経路 | 期待結果 |
|---|---|---|---|---|
| (a) 陰性 | 反映日時が集計に使われる | fixture レコードから `reflect_applied_at` を削除 | `count_applied_reflections` の window 判定 | その1件が `legacy_unverified_count` に落ちる（`count` から消える） |
| (a) 陽性対照 | 同上 | `reflect_applied_at` を window 内の妥当な値のまま残す | 同上 | `count` に1件として残る |
| (b) 陰性 | 反映先種別が集計に使われる | `reflect_target_kind` を欠落させた fixture | `count_applied_reflections` | legacy 扱い（(a) と同じ経路。欠陥2は欠陥6と同じ fail-closed 経路で吸収される設計なので、単独の赤化点は「`reflect_target_kind` が無いのに集計対象に混ざる」への陰性で確認する） |
| (b) 陽性対照 | 同上 | `reflect_target_kind="project_rule"` を持つ正常 fixture | 同上 | `count` に含まれ、`applied_list` にも `target_kind` が表示される |
| (c) 陰性 | 同一反映は1件 | 同一 `(target_kind, target_path, normalize(draft_line))` を持つ2レコードを fixture に投入 | グルーピング | `count == 1`（2ではない） |
| (c) 陽性対照 | 同上 | `target_path` か `draft_line` のどちらかだけを変えた2レコード | 同上 | `count == 2`（別反映として正しく2件） |
| (d) 陰性 | 無効化済みは数えない | `reflect_status="applied"` かつ `invalidated=True` の fixture | invalidate フィルタ | `count` から除外され `legacy_unverified_count` にも入らない（別枠 `invalidated_count` で確認） |
| (d) 陽性対照 | 同上 | `invalidated` キー自体が無い正常 applied fixture | 同上 | `count` に含まれる |
| (e) 陰性 | 旧レコードは数えない | 実データと同型（`reflect_applied_at` 等の新フィールド無し・`reflect_status="applied"` のみ）の fixture | legacy 判定 | `legacy_unverified_count` に入り `count` には入らない |
| (e) 陽性対照 | 同上 | 新フィールド4種を全部持つ fixture | 同上 | `count` に入る |
| 欠陥3 陰性 | draft_line は correction 本文由来でなければならない | `draft_line` に対象ファイル中の無関係な既存行（`extracted_learning` と無関係な文字列）を渡す | `check_correction_applied` | `{"matched": False, "reason": "draft_line_not_from_correction"}` を返し `update_reflect_status` は `reflect_status` を書き換えない |
| 欠陥3 陽性対照 | 同上 | `draft_line` が対象ファイルに実在し、かつ `extracted_learning` の部分文字列でもある | 同上 | `{"matched": True}` |

**探索したが未探索のまま残すクラス**（次巡での探索候補として明示。round 0 の必須10件は上表で充足）:
境界値（`window_days` ちょうど30日目の日時）／Unicode 正規化差（全角/半角）／
`corrections.jsonl` が空行のみ・末尾に改行が無い場合／`reflect_draft_line` に改行を含む複数行草稿。

## 8. 人間の判断が要る点

- **欠陥3の紐付け強度**（`extracted_learning` の部分文字列一致）は「緩すぎないか」の
  最終判断が要る。より厳しくする（完全一致）と正当な適用まで弾く実例が出うるが未実測
- **`migrate_legacy_applied_unverified.py`**（§2 欠陥6 Should 項目）を実装1巡の
  必須スコープに含めるか、明示的に見送るかの承認
- **`reflect_target_kind="other"`（skill 等）の扱い**（§6 表内）が「測れない」で正しいか、
  それとも `check_line_applied` の bullet/plain 判定を skill ファイル向けに拡張すべきかは
  round 0 対象外（③「measure `hook`/`pitfall`/`memory`」に準じ、skill 拡張も見送る前提で
  設計したが、issue #587 コメントには skill の明示的な対象外宣言は無い）
