# #587: 柱2「照合済み反映」を測れるようにする設計（第7版・確定版）

> **第7版は巡2（最終）レビュー（対象 SHA `f3709e9f`・判定 `設計修正要`・[Must]3件・
> [Should]4件・[Nit]1件）への対応**。**2026-09-01 にユーザーが「3件を直して実装へ進む」
> と裁定し、設計レビューはこれで打ち切りとなった**。新規系列の総上限2巡を使い切ったため、
> **本版が設計の確定版**であり、以降の設計変更は実装レビュー（`#595`型の別ゲート）へ
> 引き継ぐ。
>
> **第6版からの変更点（[Must]3件＋関連 [Should]。詳細は §1）**:
> 1. **[Must]1**: `correction_applied` を名実ともの確認イベントにした。`confirms_attempt_id`
>    による参照解決・ID形式検証・対象基底の message ハッシュとの突合を fold に実装し、
>    孤立/重複確認は除外して degraded にする（§7・§11）
> 2. **[Must]2**: 新ストアに `store_registry` の `write_boundary` 宣言を追加し、
>    generic `store_write` からの直接書込みを拒否する契約にした（§3.4）。あわせて
>    フェーズ1の追記が失敗したらフェーズ2（`update_reflect_status`）へ進まないよう
>    §8 を変更した（第6版の「主機能を止めない」という判断を撤回）
> 3. **[Must]3**: base/event の2ファイルを read する際、stat 比較による snapshot
>    一貫性チェック（有限回 retry・不安定なら `measured=False`）を追加した（§12）
> 4. **[Should]**: `measured` の意味を「全実反映件数」と「照合できた部分集合」で分離
>    （`legacy_unverified_count>0` も `measured=False` の条件に含めた・§12）。
>    `project_root`/`slug` 引数の二重契約を解消。sibling worktree でのスコープ誤判定
>    （実測で確認したバグ）も低コストで解決できたため合わせて修正した（§13）。
>    `spec/components-observability.md` の凍結説明にも例外を追記する手順を追加（§4）
>
> 検証計画（§15）も上記の設計変更に合わせて全面的に作り直した（重複ID件数の命名・
> 期待値の誤り、legacy判定試験の無効化、`(j)`の安全性主張の誤りを訂正）。

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
- `results_board.py` への配線と表示（見せ方は別 issue。ただし本設計は producer 側の
  返り値契約——`count`・`measured`・`health`——までを完成条件に含める。§12）
- `reflect_status` の意味論そのものの再定義（値域の追加は可、既存値の意味変更は対象外）
- **既存の全文書き換え経路（`update_reflect_status`）の commit protocol 全面設計**
  （`#595` が担当。理由は §2.1 参照。ただし巡1レビューで指摘された「二段書込みの間の
  中断」は柱2自身の集計精度に直結するため `#595` へ切り出さず本設計で解決する——
  §8 の2フェーズ追記）
- **`#379` 新設凍結の全体解除**。本設計が解除するのは新設する1ストア（§4）だけ
- **CLI に `correction_id`/ordinal を明示指定するオプションを追加する**

### ④ blocking の定義

| | 内容 | 出所 | 第6版での扱い |
|---|---|---|---|
| (a) | 反映日時が残らない | #567 巡1 | 解消（§7 イベント行の `reflect_applied_at`／`attempted_at`） |
| (b) | 反映先の種別が残らない | #567 巡1 | 解消（§5 実コードに基づく分類・値域） |
| (c) | 同一の反映が複数件に数えられる | #567 巡1 | 解消（§11 fold の重複排除規則。path正規化は§6で実体化） |
| (d) | 無効化済み（idiom revoke）が件数に残る | #567 巡1 | 解消（§11.2 の invalidate フィルタ＋重複ID除外） |
| (e) | 照合を通っていない旧レコードが件数に混ざる | #567 巡1 | 解消（§11.1 legacy 判定＋イベント値域検証） |
| (f)(g) | `update_reflect_status` 自体の commit protocol 起因の事故 | #588 巡1 | 柱2の集計からは無関係（§2.1）。`#595` の対象 |
| (h) | `source_correction_id` 重複時の誤更新 | tacchi 巡1 | `correction_id`（#594）が構造的に解消（§9） |
| (i) | project scope 判定の値域不一致 | 巡2 [Must]1 | 解消（§13。第5版で実測し訂正済み） |
| (j) | 反映イベントと基底レコードの二表現が同時に正 | 巡3人間承認 | 構造的に消滅（別ファイルのため dual-write が無い。§2.1）。**巡1レビューでこの主張自体は妥当だが証明する試験が無効だったため §15 で作り直した** |
| (k) | `--skip-all` がイベント行を対象に含みうる | 巡3人間承認 | 構造的に消滅（§14.3）。同上、試験を §15 で作り直した |
| (l) | 完成条件の「最終 draft 全文のハッシュ」が schema に無い | 巡3人間承認 | 解消（§7 `correction_message_sha256`） |
| **(m)** | **2段書込み（`update_reflect_status` 成功→イベント追記）の間の中断で恒久的・無検出の過小計上が起きる** | **巡1 [Must]（Q1(a)・Q2）** | **解消（§8 の2フェーズ追記による照合。完全な検出は不能な1経路が残る——§16 で原理を明記）** |
| **(n)** | **重複 `correction_id` を持つ基底レコードが「最後を正」で無検出のまま集計される** | **巡1 [Must]（Q1(d)・Q5(h)）** | **解消（§11.2 `find_duplicate_ids` による除外＋degraded）** |
| **(o)** | **イベントの必須項目・値域・hash形式を検証しないため、手編集・誤移行の最小イベントが照合済みとして混入する** | **巡1 [Must]（Q1(e)・Q2）** | **解消（§11.3 イベント値域検証。違反時は `measured=False`。§12）** |

### ⑤ 検証方法

- 陰性試験（赤になるべき）を (a)〜(e)(m)(n)(o) 各1件以上。**陰性試験＝プロダクトコードを壊す
  変異を加え、検査が赤くなることを確認するもの**であり、異常データを入力して正しく弾ける
  ことを見る通常テストとは区別する（巡1 [Must]・§15 で全面的に作り直した）
- 陽性対照を対で置く。陰性試験と混ぜて数えない
- 委譲側が挙げた回避手段とは種類の違うものを2件以上、**実際にプロダクトコードへ適用して**
  結果を報告する。緑のまま残ったものが1件でもあれば完了扱いにしない

## 1. レビュー対応の記録

### 1.1 巡1レビュー対応（[Must] 16件の対応表）

| # | 指摘（要約） | 対応 | 節 |
|---|---|---|---|
| M1 | (a)保証不可。二段書込みの間の中断で恒久的過小計上。「dual-writeは存在しない」という主張は誤り | 修正。2フェーズ追記＋read時照合で検出・復元可能にする。§0④(m)の通り、phase1自体の追記失敗のみ原理的に検出不能として§16に明記 | §8・§11.3・§16 |
| M2 | 中断・追記失敗・ファイル削除に永続的無検出の過小計上経路 | 修正。上と同一の2フェーズ設計で解決 | §8・§11.3 |
| M3 | 手編集を信頼境界に含めるなら全必須項目・値域・hash形式・基底ID重複を検証し違反時はcountでなくdegraded | 修正。§11.3 で値域検証、違反時は `measured=False` を producer 契約として明記 | §11.3・§12 |
| M4 | (b)保証不可。§6に値域も分類擬似コードもなく `classify_target_kind` が実在しない | 修正。実コード（`repo_identity`/`global_rules_root`/`global_skills_root`）に基づく `classify_reflect_target_kind` を新規に書いた | §5 |
| M5 | (c)保証不可。スキーマが参照する「§7 path正規化」が存在しない | 修正。path正規化を§6として実体化し、スキーマの参照先を修正した | §6・§7 |
| M6 | (d)保証不可。重複correction_idが最後勝ちで無検出 | 修正。`find_duplicate_ids` で重複基底IDを検出し、該当IDをcountから除外＋degraded | §11.2 |
| M7 | `classify_target_kind` が実在せず設計にも定義が無い | M4と同一。§5で実体化 | §5 |
| M8 | project scopeの引数契約が文書内で矛盾（slug vs 絶対パス）。実データのproject_pathはslug形式 | 修正。`project_name_from_dir` を使った slug/絶対パス両対応の wrapper を追加 | §13 |
| M9 | `append_unique_record` の汎化がwrite barrierの「保存先を呼出側が指定できない」契約を弱める | 修正。`filepath` 引数を撤回し `store_name` のみから内部解決する設計に変更（`store_write` と同じ契約） | §3 |
| M10 | 既存契約テスト（`test_store_classification.py`・`test_write_barrier.py`）が赤くなる手順 | 修正。両ファイルの golden 更新箇所・挿入位置を実装手順に明記 | §4.1 |
| M11 | 検証表の大半が通常テストで陰性試験になっていない | 修正。§15を全面的に作り直し、各変異に「壊すプロダクトコード」「壊す不変条件」「baseline緑→変異赤」を明記 | §15 |
| M12 | 「委譲側と種類の違う変異2件」も変異になっていない | 修正。§15で実装変異2件に置き換え | §15 |
| M13 | (j)がbaselineでも緑になる無効な試験形 | 修正。event-only行を`corrections.jsonl`へ実際に混入させ`extract_pending`が誤カウントしないことを確認する形へ | §15 |
| M14 | (h)の試験説明と期待値が逆（重複IDを独立に扱うと言いつつ最後勝ちを期待） | 修正。M6の設計変更に伴い、重複基底IDはdegraded＋count除外を期待する試験へ書き換えた | §15 |
| M15 | イベント順序の契約が自己矛盾（順序不問と言いつつファイル内最後を採用） | 修正。`reflect_applied_at`（無ければ`attempted_at`）の値でmax選択する決定的な規則へ変更。真に順序不問になった | §11.1 |
| M16 | 通常の90日prune後、healthが恒久的にdegradedになる | 修正。`prune.config.DEFAULT_DECAY_DAYS` を閾値に「期待されるorphan」と「異常なorphan」を区別 | §11.4 |

### 1.2 巡2（最終）レビュー対応（[Must] 3件・[Should] 4件の対応表）

| # | 指摘（要約） | 対応 | 節 |
|---|---|---|---|
| [Must]1 | `correction_applied` が確認イベントになっていない（`confirms_attempt_id`をfoldが参照せず、attemptが無くても1件と数える。ID検証・基底messageとのhash一致も無い） | 修正。`confirms_attempt_id`解決・ID形式検証（32桁hex）・対象基底のmessageハッシュとの突合をfoldに実装。孤立/重複確認は除外しdegraded。`correction_applied`からkind/path/draft/hashフィールド自体を削除し、唯一の真実源をattemptに一本化（fieldの不一致検証という項目自体を不要にした） | §7・§11 |
| [Must]2 | 新ストアにgeneric `store_write`経由の別の書込み口が開く | 修正。`StoreDeclaration`へ`write_boundary`フィールドを追加し、`store_write._guard_problem`が専用境界ストアへの直接書込みを拒否する契約にした。既存テスト`test_write_barrier.py`のprobeループへのskip追加＋専用拒否テストの新設を実装手順に明記。あわせてフェーズ1追記が失敗したらフェーズ2へ進まないよう§8を変更（第6版の判断を撤回） | §3.4・§8.3 |
| [Must]3 | base/eventを別々に無整合で読むためsnapshotが一貫しない | 修正。2ファイルのstat比較＋有限回retry（既定3回）＋不安定なら`measured=False`を実装 | §12 |
| [Should]1 | `count_applied_reflections`の`slug`引数と絶対パスを渡す契約が二重 | 修正。引数を`project_root: Path`へ統一 | §12・§13 |
| [Should]2 | `_parse_iso8601_utc`のimport/公開APIが無い | 対応不要（既に解決済み）。第6版の時点で`reflect_fold.py`内のprivate関数として定義し`results_board`への依存自体を作っていない設計だった（意図的な複製・重複コード）。ただしQ6の別[Should]「measuredの意味を分ける」は新規対応（下記参照） | §11 |
| [Should]3 | `correction_message_sha256`をfoldで一切使わないのは監査効果と矛盾 | 修正。[Must]1のhash突合実装により`hash_mismatch_count`としてhealthに surface するようにした（監査用途を実質化） | §11.3・§12 |
| [Should]4 | `spec/components-observability.md:16`にも#587の例外を追記すべき | 修正。§4.1に手順5として追加 | §4.1・§4.2 |
| [Should]（Q6由来・追加対応） | `measured`の意味を「全実反映件数」と「照合できた部分集合」で分けるべき | 修正。`legacy_unverified_count>0`も`measured=False`の条件に追加。ただし未反映の通常バックログ（pending等）を誤って混入させない1行ガードを§12へ追加（§16「見落としやすい1行」） | §12・§16 |
| [Should]（Q3由来・追加対応） | sibling worktreeでのproject scope誤判定（実測で確認済みのバグ） | 修正。低コストで解決できたため合わせて対応。`resolve_pj_slug`を第一候補、`project_name_from_dir`を第二候補として両方に一致を試す | §13 |
| [Should]（Q3由来・追加対応） | テストファイルパスの誤記（`scripts/lib/rl_common/tests/test_correction_id.py`は実在しない） | 修正。実在する2ファイルへ訂正 | §15 |
| [Should]（Q4由来・追加対応） | `append_unique_record`の公開方法（re-export）が未定義 | 修正。`rl_common/__init__.py`からのre-exportに統一する方針を明記 | §3.2 |
| [Should]（Q4由来・追加対応） | CI blocking範囲の表現が不正確 | 修正。「§4.3のみCI blocking、他2ファイルはfull suite」と区別して明記 | §4.4 |
| [Nit] | `duplicate_base_ids`の命名（コメント「件数」・値は「種類数」）が不統一 | 修正。`duplicate_base_row_count`へ改名し、値も「重複に関与した行数の合計」（2重複なら2）へ実装ごと訂正 | §11 |

**対応しなかった項目（理由つき）**: トップリストの[Must]4（sibling worktree誤判定）は
低コストで解決できたため上記[Should]枠で対応済み。[Must]5（"other"の対象外/分類不能の
分離・`skill_origin.classify_skill_origin`の再利用）と[Must]6（§15の一部項目）のうち
§15は本版で作り直したが、"other"の再分類（`skill_origin`活用）は§5の分類ロジック自体の
再設計を要し、頭が「3件を直して実装へ進む」と裁定した本巡のスコープには含めなかった。
実装1巡または次回改訂の候補として`実装者への申し送り`に明記する（後述の報告参照）。

## 2. 新ストアの定義

### 2.1 なぜ別ファイルが `update_reflect_status` の安全性と無関係になるか

（第5版から継承）`--apply`/`--skip` ハンドラは現行どおり `update_reflect_status(...)` を呼び続け、
`corrections.jsonl` 側の全文書換え経路そのものには一切触れない（`#595` の担当）。
新ストアへの追記は別ファイル・別ロックであり、`corrections.jsonl` への dual-write は
そもそも発生しない。**この主張自体は巡1レビューでも認められている**（Q1(a)冒頭「両ストアが
別ファイルであることに異論はない」）。レビューが指摘したのは「別ファイルであっても、
1つの事実（『反映した』）を2つの独立した書込み操作（`update_reflect_status` の成功と
イベント追記）で表す以上、両者の間に原子性のギャップが残る」という点であり、これは
ファイルが同一か別かとは独立の問題だった。§8 の2フェーズ追記でこのギャップ自体を
照合可能にする。

### 2.2 ストア名・置き場所

（第5版から継承・変更なし）

- **basename**: `reflect_apply_events.jsonl`（頭の裁定・2026-09-01。他候補
  `reflect_events.jsonl`／`pillar2_events.jsonl` を検討した上で採用）
- **置き場所**: `rl_common.DATA_DIR / "reflect_apply_events.jsonl"`

### 2.3 版数・一意キー

行スキーマは `schema_version` フィールドを持つ（現在値 `1`）。一意キーはイベント行自身の
`correction_id`（32桁hex）。**第6版で追加**: `correction_applied` イベントは、対応する
`correction_apply_attempted` イベントを `confirms_attempt_id` で参照する（§7・§8）。

### 2.4 追記の唯一境界

§3 の `append_unique_record`（`store_name` のみを受け取る汎化版）を唯一の追記口とする。
**第6版では1回の `--apply` 成功に対して最大2回**（`correction_apply_attempted` を
`update_reflect_status` 呼出し前に1回、`correction_applied` をその成功後に1回）この
関数を呼ぶ（§8）。`--skip` は従来どおり単発（`correction_skipped`、2フェーズ化しない
——理由は §16「2フェーズを`--skip`に適用しなかった理由」）。

## 3. 追記境界の汎化（`append_unique_record`）

### 3.1 巡1指摘（M9）: 汎化案は write barrier の契約を弱めていた

第5版の `append_unique_record(filepath: Path, store_name: str, record: dict)` は、
`filepath` と `store_name` を別々の引数として受け取っていた。しかし `store_write` の
中核契約は「呼出側が保存先パスを指定できず、`store_name` から `DATA_DIR/store_name` へ
**必ず**内部解決する」ことである（`scripts/lib/rl_common/store_write.py:82-84,103` の
docstring「保存先は呼び出し側が一切指定できない」・2026-09-01 実測）。第5版のAPIは
`store_name` が正しくても任意の `filepath` を渡せてしまうため、名前は正しいがパスだけ
別の場所を指す通常の配線ミスで、writer と reader が物理的に分裂しうる（過小計上の新しい
発生源になる）。**これは write barrier の契約を弱める設計であり、頭の方針（既存契約を
弱める案は採れない）に反する。撤回する。**

### 3.2 修正: `store_name` のみから内部解決する

```python
# scripts/lib/rl_common/correction_id.py（設計・未実装）

def append_unique_record(store_name: str, record: dict) -> AppendResult:
    """任意の store_name への、correction_id 重複拒否つき唯一の追記境界。

    保存先は store_write と同じ規約（DATA_DIR/store_name）で内部解決し、呼出側は
    パスを一切指定できない（write barrier の契約を弱めない）。
    """
    if not persistence._HAVE_FCNTL:
        return AppendResult(
            status="unsupported_platform",
            reason="fcntl unavailable: unique append is not supported",
        )
    from .store_write import guard_problem
    import rl_common

    problem = guard_problem(store_name)
    if problem is not None:
        return AppendResult(status="unregistered_store", reason=problem)

    correction_id = record.get("correction_id")
    if not validate_correction_id(correction_id):
        return AppendResult(status="invalid_id")

    rl_common.ensure_data_dir()  # #587 巡1 [Should]: store_write は追記前に必ず呼ぶ
                                  # （store_write.py:108）。汎化版も同じ順序にする
    filepath = rl_common.DATA_DIR / store_name

    result = persistence.append_jsonl(
        filepath,
        record,
        duplicate_check=lambda existing: has_duplicate_id(existing, correction_id),
    )
    if result.status == "written":
        return AppendResult(status="appended")
    if result.status == "duplicate":
        return AppendResult(status="duplicate_id")
    return AppendResult(status="retry_required", reason=result.reason)


def append_correction_record(filepath: Path, record: dict) -> AppendResult:
    """corrections.jsonl 専用の従来どおりの窓口（既存呼出元は無改修）。

    既存呼出元は明示的な filepath を渡す契約のまま維持する（#594 の既存呼出元
    hooks/correction_detect.py 等のシグネチャは変えない）ため、store_name 専用の
    append_unique_record とは実装を共有しない（同じロック・重複拒否プリミティブ
    persistence.append_jsonl + has_duplicate_id を個別に呼ぶ2つの薄い関数として
    共存させる——第5版が意図した「1関数への抽出統合」は、store_name 内部解決契約と
    既存 filepath 契約が両立しないため撤回する）。
    """
    if not persistence._HAVE_FCNTL:
        return AppendResult(
            status="unsupported_platform",
            reason="fcntl unavailable: unique append is not supported",
        )
    from .store_write import guard_problem

    problem = guard_problem("corrections.jsonl")
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
```

**既存 `append_correction_record` の実装は無改修**（第5版のように内部実装を共有する
リファクタは行わない。既存呼出元への回帰リスクをゼロにする）。新ストア専用の
`append_unique_record` だけを新規に追加する。

**テストでのパス隔離**: `append_unique_record` はテスト用の `filepath` 注入口を持たない。
既存の慣習どおり `mock.patch.object(rl_common, "DATA_DIR", tmp_path)` で `DATA_DIR` を
差し替える（`store_write` のテストと同じ手法。`rl_common.DATA_DIR` は呼出時に
`rl_common.ensure_data_dir()`/`filepath = rl_common.DATA_DIR / store_name` として
**call-time 参照**するため、モジュールレベル import コピー（pitfall_module_level_datadir_import_copy）
を踏まない）。

**公開方法（[Should] 巡2指摘）**: 既存の correction ID API 群は `rl_common/__init__.py:229-239`
から re-export されている（`append_correction_record`/`find_duplicate_ids`/
`validate_correction_id` 等）。**`append_unique_record` も同じ `from .correction_id import
(...)` へ追加し、`reflect.py` からは `rl_common.append_unique_record` 経由で呼ぶ**
（`rl_common.correction_id.append_unique_record` を直接 import する経路と2つの入口を
作らない——実装1巡でどちらか1つに固定する）。

### 3.3 戻り値表記の統一（[Should]）

`AppendResult` は dataclass であり `.status` 属性で読む（`correction_id.py:44` 実測）。
本文書内で `{"status": "appended"}` のような dict リテラル表記を使っている箇所は、
**JSON 応答へ含める際の変換結果を示す略記**であり、実装は常に `AppendResult` dataclass を
返し、CLI 応答へ含めるときに `dataclasses.asdict(result)` で dict 化する、という契約に
統一する。

### 3.4 `write_boundary`: generic `store_write` からの迂回を塞ぐ（巡2 [Must]2）

**巡2指摘**: §3.2 で `store_name` のみから内部解決する設計に直しても、`store_registry` へ
active 登録した時点で、**誰でも generic `store_write("reflect_apply_events.jsonl",
任意のdict)` を直接呼べてしまう**（`store_write` は `guard_problem` で registry の
active/kind だけを見て任意 dict を追記する・`store_write.py:82-109` 実測）。実際、
既存テスト `test_write_barrier.py:363-374`
`test_store_write_resolves_every_active_store_under_canonical` は**全 active jsonl ストアへ
generic `store_write` で probe を書く**ため、`reflect_apply_events.jsonl` を active 登録
すると、このテスト自身がその迂回を実演してしまう。**これは §3 の「`append_unique_record`
（重複ID拒否・ID検証）が唯一の追記境界」という設計を機械的に破る**（ID検証も重複拒否も
経由しない行が紛れ込みうる）。

**修正: `store_registry` に「専用境界（specialized write boundary）」を宣言する場を持たせ、
generic `store_write` がそれを見て拒否する契約にする**（write barrier を**弱めるのではなく
強める**変更であり、頭の方針「既存契約を弱めない」に反しない）。

**`StoreDeclaration`（`scripts/lib/store_registry.py:73-` 実測）へ新フィールドを1つ追加**
（既定値 `None` のため、既存の全宣言に無変更で影響しない）:

```python
@dataclass(frozen=True)
class StoreDeclaration:
    ...（既存フィールドは無改修）
    write_boundary: Optional[str] = None
    """generic store_write からの直接書込みを拒否し、この名前の専用関数を経由することを
    要求する（例: "rl_common.correction_id.append_unique_record"）。None（既定）なら
    従来どおり generic store_write で書ける。#587 巡2 [Must]2 で新設。"""
```

**`reflect_apply_events.jsonl` の宣言（§4.1）へ `write_boundary="rl_common.correction_id.
append_unique_record"` を追加する**。

**2026-09-01 の実装裁定**: 公開 `guard_problem` は `_guard_problem` の薄いラッパーで、
`append_unique_record` 自身もこれを呼ぶ。したがって専用境界の拒否を `_guard_problem` に
置くと専用境界まで自己拒否する。`_guard_problem` は従来どおり active/kind だけを見て、
**`store_write()` 本体の `_guard_problem` 呼出し直後へ1分岐追加する**:

```python
def store_write(store_name: str, record: dict, *, guard_mode=None) -> None:
    ...（既存の _guard_problem による未登録/非active/kind=json判定はそのまま）
    decl = declaration_for(store_name)
    boundary = getattr(decl, "write_boundary", None)
    if boundary is not None:
        raise StoreWriteError(
            f"ストア '{store_name}' は専用の追記境界 '{boundary}' を経由する必要があります"
            "（generic store_write からの直接書込みは拒否・#587）"
        )
```

**`append_unique_record`（§3.2）自体は `store_write` を経由しない**（`persistence.append_jsonl`
を直接呼び、`guard_problem` は専用境界を拒否しない）ため、この拒否分岐の影響を受けない
——専用境界関数だけが書ける状態になる。

**既存テストへの実測した影響（実装手順に明記）**:
- `test_write_barrier.py:363-374` `test_store_write_resolves_every_active_store_under_canonical`
  のループは、`write_boundary` が設定されたストアを **skip** するよう1行追加する
  （`if getattr(store_registry.declaration_for(name), "write_boundary", None): continue`）
- 同ファイルへ**新規テスト**を追加する: `store_write("reflect_apply_events.jsonl", {"probe": 1})`
  が `StoreWriteError` を送出することを確認する
  （`test_generic_store_write_rejects_specialized_boundary`）

## 4. 凍結解除の手順

### 4.1 何を変えるか（機械的に確認できる形にする）

**巡1指摘（M10）**: 第5版は「2ファイルの各1行追加だけで完結する」としていたが、
既存の golden test 契約テストが2本、追加なしでは赤くなる（2026-09-01 実測）。

1. **`scripts/lib/store_registry.py`**: `_DECLARATIONS` リストへ1件追加
   （`write_boundary` は §3.4・巡2 [Must]2 対応で追加したフィールド）

   ```python
   StoreDeclaration(
       name="reflect_apply_events.jsonl",
       writer="skills/reflect/scripts/reflect.py --apply/--skip ハンドラ（柱2反映イベント）",
       reader="scripts/lib/reflect_fold.py・scripts/lib/pillar2_metrics.py",
       retention="permanent",
       classification="raw_event",
       writer_locus="batch",
       write_boundary="rl_common.correction_id.append_unique_record",
       note=(
           "#587 柱2測定用。#379 新設凍結の例外としてユーザー裁定（2026-09-01）で"
           "追加。corrections.jsonl とは別ファイル・別ロック。generic store_write"
           "からの直接書込みは write_boundary により拒否される（#587 巡2 [Must]2）。"
       ),
   )
   ```

2. **`scripts/lib/shrink_freeze.py:62-` `FROZEN_STORES`**: 同じ basename を1件追加し、
   理由コメントを直上に置く（第5版から継承）。

3. **【第6版で追加】`scripts/lib/tests/test_store_classification.py`**:
   - `_RAW_EVENT`（32行目〜）へ `"reflect_apply_events.jsonl"` を追加
   - `assert len(_RAW_EVENT) == 12`（147行目）を `== 13` へ更新

   （実測: `_RAW_EVENT` は現在12件・`test_classification_golden_counts_and_names` が
   集合一致とこの件数を両方 assert する。2026-09-01 `grep -n "_RAW_EVENT = {" -A 20
   scripts/lib/tests/test_store_classification.py` で確認）

   **さらに重要**: 同ファイル `test_frozen_stores_snapshot_unchanged_by_classification`
   （161-165行目）は `set(store_registry.declared_store_names()) == set(shrink_freeze.FROZEN_STORES)`
   を**完全一致**で assert する（部分集合ではない）。これは #4.1 の手順1・2の両方を
   必ず行う理由そのものであり、片方だけの更新では必ずこのテストが赤くなる
   （2026-09-01 実測: 現在 `declared_store_names()` と `FROZEN_STORES` はいずれも
   **45件で完全一致**——`python3 -c "import sys; sys.path.insert(0,'scripts/lib');
   import shrink_freeze, store_registry; live=set(store_registry.declared_store_names());
   print(len(live), live==set(shrink_freeze.FROZEN_STORES))"` → `45 True`）。

4. **【第6版で追加】`scripts/lib/tests/test_write_barrier.py`**:
   `_EXPECTED_ACTIVE_STORES`（316行目〜、ソート済みリスト）へ `"reflect_apply_events.jsonl"`
   を挿入する。**挿入位置**: `"pj_slug_cache.json"` の直後・`"remediation-outcomes.jsonl"`
   の直前（アルファベット順で `reflect_...` は `remediation...` より前——`f` < `m`）。
   `test_active_store_path_set_snapshot` がこのリストと `store_registry.active_store_names()`
   の完全一致を assert する。**加えて §3.4（巡2 [Must]2）の
   `test_store_write_resolves_every_active_store_under_canonical` ループ skip 追加と
   専用拒否テストの新設も同ファイルへ行う**（§3.4 参照。二重管理を避けるため手順の
   詳細は §3.4 側だけに書く）。

5. **【第7版で追加・巡2 [Should]4】`spec/components-observability.md`**:
   コードと `CLAUDE.md` だけでなく、この仕様記述のコンポーネント表より前に独立段落を置き、
   #587 の `reflect_apply_events.jsonl` 1件だけがユーザー裁定による凍結スナップショットの
   追加例外であることを明記する。

### 4.2 CLAUDE.md・仕様記述の同時更新

（第5版から継承・第7版で対象を拡張）「新設凍結（#379 Step 1）」節（`CLAUDE.md:45`）へ
例外1文を追記する。**加えて §4.1 手順5 の `spec/components-observability.md` 更新も同時に行う**
（プロジェクトの決まり「コードだけ直して終わりは禁止」に従い、コード・CLAUDE.md・仕様記述の
3箇所を同一 PR で揃える）。

### 4.3 「1件だけ」の機械的な保証（[Should] 巡1指摘で修正）

**巡1指摘**: 第5版の専用契約テスト案（`FROZEN_STORES - OLD_SNAPSHOT == {"reflect_apply_events.jsonl"}`）
は `OLD_SNAPSHOT` を `FROZEN_STORES` 自身や live 集合から動的生成すると baseline でも
常時緑になり、検査として無効になる。**固定集合または固定 hash を設計に明記する必要がある**
との指摘に対し、以下を採用する:

**実測（2026-09-01・再現コマンド併記）**:
```
python3 -c "
import sys; sys.path.insert(0, 'scripts/lib')
import shrink_freeze, hashlib
names = sorted(shrink_freeze.FROZEN_STORES)
print(len(names))
print(hashlib.sha256('\n'.join(names).encode()).hexdigest())
"
```
出力: `45` / `9f076b1d1fffb482743c00d32ebaa76e5cb64f6d2e996805ff9a8c95041c85d7`
（本設計着手前・第6版 SHA `968143f2` 時点の `FROZEN_STORES` はこの45件・このhash）

**専用契約テスト**（`scripts/lib/tests/test_shrink_freeze.py` へ追加）:

```python
# #587 着手前（2026-09-01）の FROZEN_STORES スナップショット固定値。
# sorted(FROZEN_STORES) を "\n".join して SHA256 した値（動的生成しない——
# 動的生成すると baseline でも常に緑になり検査として無効になるため、リテラルで固定する）。
_PRE_587_FROZEN_STORES_SHA256 = "9f076b1d1fffb482743c00d32ebaa76e5cb64f6d2e996805ff9a8c95041c85d7"
_PRE_587_FROZEN_STORES_COUNT = 45


def test_frozen_stores_587_exception_is_exactly_one() -> None:
    """#379凍結中に許可された新設例外は reflect_apply_events.jsonl の1件だけであることを保証する。

    #587（2026-09-01 ユーザー裁定）で凍結の例外を1件だけ認めた。将来別のストアが
    同じ手口（store_registry と FROZEN_STORES への追加）で2件目の例外を作ろうとしたとき、
    このテストが「例外が増えている」ことを検出する。
    """
    live = set(shrink_freeze.FROZEN_STORES)
    added = live - {"reflect_apply_events.jsonl"}
    assert len(added) == _PRE_587_FROZEN_STORES_COUNT, (
        f"#587 着手前と比べて FROZEN_STORES の要素数が変わっています "
        f"(期待 {_PRE_587_FROZEN_STORES_COUNT}・実際 {len(added)})。"
        "#379 新設凍結の例外は reflect_apply_events.jsonl の1件のみの想定です"
    )
    digest = hashlib.sha256("\n".join(sorted(added)).encode()).hexdigest()
    assert digest == _PRE_587_FROZEN_STORES_SHA256, (
        "FROZEN_STORES から reflect_apply_events.jsonl を除いた残りが "
        "#587 着手前のスナップショットと一致しません"
    )
    assert "reflect_apply_events.jsonl" in live
```

**巡2 [Should] 指摘**: 現行 `test_shrink_freeze.py`（1-18行目実測）に `import hashlib` は
無い。上記テストを追加する際、ファイル冒頭へ `import hashlib` を追加することを実装手順に
明記する。

このテストが検出できるのは「`FROZEN_STORES`（および連動する `store_registry`）へ2件目以降が
追加されること」だけである。次項 4.4 でこの限界を明記する。

### 4.4 「なし崩しに増える経路がない」という主張の限界（[Should] 巡1指摘で訂正）

**巡1指摘**: 現行 runtime 凍結ゲート（`store_write_raw` の `_raw_freeze_problem`）が直接
守るのは `store_write_raw` 経由の書込みだけであり、`store_registry` の stale 突合は
hook writer 中心（`writer_locus="batch"` は対象外・`store_registry.py:858` 実測）。
batch コードが `open()`/`persistence.append_jsonl` を直接使って未登録ファイルを新規作成する
経路は、§4.3 の専用テストの集合差にも `store_write`/`store_write_raw` の runtime ゲートにも
現れない。**これは既存の凍結機構全体（#379 Step 1）が元から持つ限界であり、本設計が
新たに作ったものではない**。第5版の「なし崩しに増える経路がない」という記述を
「§4.3 の専用テストは `store_registry`/`FROZEN_STORES` 経由の新設を1件に制限する。
`store_write`/`store_write_raw` を経由しない直接 `open()` 書込みによる新設は、
本設計の対象外である既存凍結機構全体の限界として残る（#379 Step 1 自体の課題）」
と訂正する。

**巡2 [Should] 指摘（CI 強制範囲）**: portable CI が blocking で直接実行するのは
`test_shrink_freeze.py` のみである（`.github/workflows/ci.yml:79-87` 実測）。
§4.1 手順3・4（`test_store_classification.py`・`test_write_barrier.py`）の golden 更新は
通常の `python3 -m pytest`（full suite）では検証されるが、**この portable contract suite の
列挙には含まれていない**。「CI blocking で4テストすべてが保証される」とは書かず、
「§4.3 の専用テストは CI blocking、§4.1 手順3・4 の golden 更新は full suite（ローカル/
PR の通常テスト実行）で検証される」と区別して表現する。

## 5. 反映先種別の分類（`classify_reflect_target_kind`）

**巡1指摘（M4・M7）**: 第5版は `reflect_target_kind` の値域を分類する関数名
`classify_target_kind` を検証計画（§15）が呼ぶ前提で書いていたが、その関数の**定義自体が
どこにも存在しなかった**（設計文書内にも実コードにも無い）。実在するのは
`reflect.py:505` `_rule_scope_identity` だが、これは kind 文字列でなく
`{"scope", "repo_id", "relative_path"}` の dict または `None` を返す別物である
（`reflect.py:527,539` 実測）。以下、実コードのプリミティブに基づいて新規に定義する。

### 5.1 使う実プリミティブ（すべて実測済み・2026-09-01）

- `evolve_revert._target.global_rules_root() -> Path`: `~/.claude/rules`
  （`scripts/lib/evolve_revert/_target.py:47-49` 実測）
- `evolve_decision_ids.global_skills_root() -> Path`: `~/.claude/skills`
  （`scripts/lib/evolve_decision_ids.py:341-344` 実測）
- `evolve_decision_ids.repo_identity(path: str) -> Dict[str, Optional[str]]`:
  `{"repo_id", "relative_path", "worktree_root"}` を返す。git 管理外/不可なら
  `repo_id=None, relative_path=<元のpath>`（`evolve_decision_ids.py:37-53` 実測）

### 5.2 値域と分類擬似コード

| 値 | 判定条件 |
|---|---|
| `"global_rule"` | 絶対化後のパスが `global_rules_root()` 配下 |
| `"global_claude_md"` | 絶対化後のパスが `~/.claude/CLAUDE.md` と一致 |
| `"skill"`（global） | 絶対化後のパスが `global_skills_root()` 配下、かつファイル名が `SKILL.md` |
| `"project_rule"` | `repo_identity()` が `repo_id` を返し、`relative_path` が `.claude/rules/` で始まる |
| `"project_claude_md"` | `repo_identity()` が `repo_id` を返し、`relative_path == "CLAUDE.md"` |
| `"skill"`（project） | `repo_identity()` が `repo_id` を返し、`relative_path` が `.claude/skills/`または`skills/` で始まり、かつファイル名が `SKILL.md` |
| `"other"` | 上記いずれにも一致しない |

```python
# scripts/lib/reflect_apply_match.py（設計・未実装。既存 check_line_applied と同じファイルへ追加）

_KNOWN_TARGET_KINDS = frozenset({
    "global_rule", "project_rule", "global_claude_md",
    "project_claude_md", "skill", "other",
})


def classify_reflect_target_kind(target_path: str) -> str:
    """反映先ファイルの種別を分類する（#587 blocking (b)）。値域は _KNOWN_TARGET_KINDS。"""
    from pathlib import Path
    from evolve_decision_ids import repo_identity, global_skills_root
    from evolve_revert._target import global_rules_root

    p = Path(target_path).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p

    try:
        resolved.relative_to(global_rules_root().resolve())
        return "global_rule"
    except ValueError:
        pass

    if resolved == (Path.home() / ".claude" / "CLAUDE.md").resolve():
        return "global_claude_md"

    try:
        resolved.relative_to(global_skills_root().resolve())
        if resolved.name == "SKILL.md":
            return "skill"
    except ValueError:
        pass

    identity = repo_identity(str(p))
    repo_id = identity.get("repo_id")
    rel_posix = (identity.get("relative_path") or "").replace("\\", "/")
    if repo_id:
        if rel_posix.startswith(".claude/rules/"):
            return "project_rule"
        if rel_posix == "CLAUDE.md":
            return "project_claude_md"
        if rel_posix.endswith("/SKILL.md") or rel_posix == "SKILL.md":
            if rel_posix.startswith(".claude/skills/") or rel_posix.startswith("skills/"):
                return "skill"

    return "other"
```

**既知の限界**: skill のパス規約は PJ・プラグインごとに揺れがあり（`.claude/skills/` と
`skills/` の両方が実在する。本 issue の対象コーパスは `~/.claude/evolve-anything/
corrections.jsonl` の実データ範囲でしか検証していない）。網羅した規約に一致しないパスは
`"other"` に落ちる。§12 で `other_kind_count` として理由つきで除外する。

**fold 側は `reflect_target_kind` が `_KNOWN_TARGET_KINDS` に含まれることを検証する**
（§11.3・M4 の後半「fold も値域検証をせず truthy なら成立扱い」への対応）。未知値は
イベントとして不正扱いになり `has_pillar2_fields=False` に落ちる。

## 6. path 正規化

**巡1指摘（M5）**: 第5版のスキーマ表は「§7 の path 正規化」を参照していたが、実際の
第5版 §7 は correction ID の識別子節であり、path 正規化の実体は存在しなかった。
以下で新規に定義する（第3版・第4版が持っていた内容を、実コードのプリミティブ名
——`repo_identity` の実キー——に合わせて書き直したもの）。

イベント追記時に次の順で正規化してから保存する（§11.2 の重複排除グルーピングキーにも
この正規化後の値を使う。blocking c の偽陽性——相対/絶対/symlink違いによる過剰計上——を防ぐ）:

1. `Path(target_path).expanduser()`
2. `.resolve()`（symlink 解決・絶対化）
3. `evolve_decision_ids.repo_identity(str(resolved))` が `repo_id` を返せば
   `f"{repo_id}:{relative_path}"`（worktree 間で同一ファイルを同一キーにする——
   `global_rules_root` 配下や home 直下の `CLAUDE.md` は `repo_id` が無いので、
   絶対パス文字列（手順2の結果）をそのまま使う）

**保存時点で存在確認しか行わない**: 本設計は表示ラベルを変えず、`count_applied_reflections`
（§12）の docstring に「`reflect_applied_at` 時点で確認された事実であり、その後の削除・
変更を追跡しない」ことを明記する。

## 7. 追記イベント行のスキーマ（2フェーズ）

**巡1 M1・M2 への対応として第6版でイベントを2フェーズ化し、巡2指摘（[Must]1）を受けて
第7版で `correction_applied` を名実ともに「既存 attempt の確認」に限定する**（§8で詳述）。
3つの `event_type` を持つ:

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `correction_id` | str（32桁hex・`validate_correction_id`で検証） | ○ | このイベント行自身の不変ID |
| `schema_version` | int（現在値 `1`） | ○ | フィールド追加・意味変更時の版分岐用 |
| `event_type` | `"correction_apply_attempted"` \| `"correction_applied"` \| `"correction_skipped"` | ○ | イベント種別 |
| `target_correction_id` | str（32桁hex・`validate_correction_id`で検証） | ○ | 対象の基底レコード（`corrections.jsonl`）の `correction_id`（§9） |
| `reflect_target_kind` | §5 の値域（`_KNOWN_TARGET_KINDS`） | `correction_apply_attempted` のみ | §5 |
| `reflect_target_path` | str（正規化後・§6） | `correction_apply_attempted` のみ | §6 |
| `reflect_draft_line` | str（正規化前・全文） | `correction_apply_attempted` のみ | §10 の照合対象 |
| `correction_message_sha256` | str（`^[0-9a-f]{64}$`） | `correction_apply_attempted` のみ | §10。§11.3で基底のmessageハッシュと突合する |
| `attempted_at` | str (ISO8601 UTC・**timezone省略不可**) | `event_type=="correction_apply_attempted"` のみ | **`update_reflect_status` を呼ぶ直前**の時刻（§8 手順1で捕捉） |
| `reflect_applied_at` | str (ISO8601 UTC・**timezone省略不可**) | `event_type=="correction_applied"` のみ | `update_reflect_status` が `"applied"` を返した直後の時刻（§8 手順3で捕捉） |
| `skipped_at` | str (ISO8601 UTC・**timezone省略不可**) | `event_type=="correction_skipped"` のみ | `update_reflect_status` が `"skipped"` を返した直後の時刻 |
| `confirms_attempt_id` | str（32桁hex・`validate_correction_id`で検証） | `event_type=="correction_applied"` のみ | 対応する `correction_apply_attempted` イベント自身の `correction_id`（§8・§11.1） |

**巡2 [Must]1 対応（`correction_applied` を名実ともに確認イベントにする）**: `reflect_target_kind`
/`reflect_target_path`/`reflect_draft_line`/`correction_message_sha256` は
**`correction_applied` には持たせない**（第6版はこれらを attempt/applied 両方に重複させる
前提で書いていたが、「両者が一致することを検証する」規則自体を持たなかったため、
片方だけを手編集すれば整合性チェックなしにすり抜けられた）。**唯一の真実源は
`correction_apply_attempted` であり、`correction_applied` は `confirms_attempt_id` で
それを参照するだけの薄い確認印である**（§11.1 で fold 時に attempt から値を取得する）。
これにより「attempt/applied 間の不変フィールド一致」という検証項目そのものが不要になる
（比較対象が最初から1つしか無いため——第6版の「両方を検証する」設計より単純かつ堅牢）。

**timezone 省略の禁止（[Should] 巡2指摘）**: `attempted_at`/`reflect_applied_at` が
timezone 情報を持たない naive 文字列の場合、§11 の `_parse_iso8601_utc` は UTC とみなして
受理していた（第6版まで）。**手編集された local 時刻を誤った瞬間として受理しうるため、
naive timestamp は invalid として扱う**（§11.3 の検証に含める。value 例:
`"2026-09-01T12:00:00"` は不正、`"2026-09-01T12:00:00Z"`/`"+00:00"` サフィックス付きのみ有効）。

**`reflect_status` フィールドは持たない**（別ファイルなので概念自体が存在しない）。

## 8. イベント追記のタイミングと呼出契約（2フェーズ）

`reflect.py` の `--apply` ハンドラ（現行 `reflect.py:1274-1364`）を次のように拡張する
（`update_reflect_status` 自体は変更しない）。**挿入位置は実コードで確認済み**
（`reflect.py:1305-1332` 実測: `target_index` 探索 → `update_reflect_status` 呼出し
→ `result` 取得、その直後 `1337行目` から revert 記録処理があり、`before_content_file` が
無ければ `sys.exit(1)` で早期終了する）。

### 8.1 手順

1. **§9 の `resolve_source_correction_id` で `target_correction_id` を解決する**
   （現行の `target_index` 探索の**前**、または並行して行ってよいが、`"ambiguous"` の場合は
   フェーズ1自体を実行せず `{"status": "ambiguous_source", ...}` を返して非0終了する
   ——現行 `target_index` 探索の「先頭一致で確定」動作自体は変更しない）
2. **フェーズ1（`update_reflect_status` を呼ぶ直前）**: `attempted_at = datetime.now(timezone.utc)`
   を捕捉し、§7 の `correction_apply_attempted` イベントを §3 の `append_unique_record`
   で `reflect_apply_events.jsonl` へ追記する。この時点で `args.target_path`・
   `draft_line`（`reflect.py:1296-1303` で既に読み込み済み）・§5/§6 の分類・正規化・
   §10 のハッシュは全て計算可能（`update_reflect_status` の結果を待たずに揃う）。
   **【巡2 [Must]2 で第6版から変更】追記結果が `{"status": "appended"}` 以外の場合、
   フェーズ2（`update_reflect_status` 呼出し）へ進まず、非0終了で
   `{"status": "pillar2_event_failed", "pillar2_event": <追記結果>}` を返す**
   （§8.3・第6版の「柱2の記録失敗を理由に主機能を止めない」という設計判断を撤回した理由は
   §8.3 に明記する）
3. **フェーズ2**: 現行どおり `reflect.py:1329-1332` の `update_reflect_status(...)` を呼ぶ
   （無改修。フェーズ1が `appended` を返した場合のみここへ到達する）
4. **フェーズ3（`result.get("status") == "applied"` のときだけ・`reflect.py:1332` の
   直後、`1337行目` の revert 記録処理より**前**）**: `reflect_applied_at =
   datetime.now(timezone.utc)` を捕捉し、§7 の `correction_applied` 確認イベント
   （`confirms_attempt_id` にフェーズ1で発行した `correction_id` を入れる。§7の設計変更
   により `reflect_target_kind`等は含めない）を追記する。
   **revert 記録処理より前に置く理由**: revert 記録処理は `before_content_file` が
   無いと `sys.exit(1)` で終了する経路を持つ（`reflect.py:1338-1344`）。柱2のイベント
   追記をそれより後ろに置くと、基底更新済み・イベントなしの新たな中断経路になる

### 8.2 `--skip`

`--skip` も `correction_skipped` イベントを1回だけ追記する（**2フェーズ化しない**。
理由は §16「2フェーズを `--skip` に適用しなかった理由」）。§0③により柱2の集計対象では
なく、監査証跡としてのみ持つ。

### 8.3 追記失敗時の扱い（巡2 [Must]2 で第6版から変更）

**第6版までの設計判断（「柱2の記録失敗を理由に既存の `--apply` の主機能を失敗扱いにしない」）
を撤回する**。理由（巡2レビュー指摘）: 「最初の durable write は原理的に失敗し得る」という
説明は、**成功を副作用実行の前提にすれば実際には回避できる**ため、フェーズ2を続行してよい
理由にはならなかった。§16 で説明する「原理的に検出不能な唯一の残存ケース」は
「フェーズ1が失敗したにもかかわらずフェーズ2を実行してしまう」ことで初めて発生していた
——**フェーズ1の成功をフェーズ2実行の前提条件にすれば、この経路自体が構造的に発生しなくなる**
（この設計変更以降に行われた `--apply` に限る。過去分・移行未実施分は引き続き
`legacy_unverified_count` で扱う・§11.1）。

フェーズ1が失敗した場合、ユーザーは `--apply` を再実行できる（対象ファイルはまだ
一切変更されていないため副作用は無い・べき等に再試行可能）。

フェーズ3（確認イベント追記）が `{"status": "appended"}` 以外を返した場合は、既存どおり
`reflect_status` の更新自体（フェーズ2）が成功したまま返る（フェーズ3の失敗は §11.1 の
2フェーズ照合——`base.reflect_status=="applied"` かつ attempt はあるが confirmation が無い
——で read 時に復元できるため、フェーズ1と同じ扱いにする必要がない）。

### 8.4 `--skip-all` は対象外

`--skip-all` は本設計の対象外（旧 blocking (k)）。`corrections.jsonl` 側の pending index を
一括処理する既存経路であり、新ストアには一切書込まない。

## 9. 識別子は `correction_id` のみ

（第5版から継承・変更なし）`correction_id` は #594 により新規レコードに必ず付与され、
既存レコードには `migrate_correction_id_backfill.py` が一度だけのバックフィルを提供する。
位置に依存しない。イベント行は `target_correction_id` で基底レコードを参照する。

**既存レコードに `correction_id` が無い場合**: §8.1 手順1の `resolve_source_correction_id`
が `"unmigrated_source"` 相当を返し、フェーズ1自体を実行しない（fail-closed）。

## 10. correction とイベントの紐付け強度

（第5版から継承）`check_line_applied`（`scripts/lib/reflect_apply_match.py:49`）による
「draft_line が対象ファイルに存在するか」の確認はそのまま維持する。追加で
`correction_message_sha256`（§7）を持たせ、「このイベントがどの correction の内容に
対応するか」を correction 本文のハッシュで固定する。fold 側では使わない（監査用）。

**「言い換え」を許容するかどうかは本設計では解決しない**（人間判断・§17 継承事項2）。

## 11. read 時 fold の擬似コード

新規共有モジュール `scripts/lib/reflect_fold.py` を作る。**巡1対応（第6版）**: 重複
`correction_id` 検出（M6）・イベント値域検証（M3）・決定的な最新イベント選択（M15）・
2フェーズ照合（M1/M2）・decay を考慮した orphan 判定（M16）。**巡2対応（第7版・
[Must]1）**: `correction_applied` を「既存 attempt への参照＋対象基底との hash 一致」を
必須とする名実ともの確認イベントにする。§7 の設計変更（`correction_applied` は
`reflect_target_kind` 等を持たない）により、attempt/applied 間の不変フィールド一致検証
という項目自体が不要になった（真実源が1つしかないため）。

```python
# scripts/lib/reflect_fold.py（設計・未実装）
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from rl_common.correction_id import validate_correction_id, find_duplicate_ids

_KNOWN_TARGET_KINDS = frozenset({
    "global_rule", "project_rule", "global_claude_md",
    "project_claude_md", "skill", "other",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# ISO8601 UTC のみ許可（naive/local timezoneは弾く。巡2 [Should]）。
# "...Z" は _parse_iso8601_strict が事前に "+00:00" へ正規化してから呼ぶ。
_TZ_AWARE_RE = re.compile(r".+[+-]\d{2}:\d{2}$")


def _parse_iso8601_utc(raw) -> Optional[datetime]:
    """timestamp を aware UTC datetime にパースする（timezone省略なら None・巡2 [Should]対応）。

    results_board._parse_timestamp と同じロジックを意図的にこのモジュール内へ複製する
    （#587 巡1 [Should]: private 関数への依存は将来 results_board が pillar2_metrics を
    表示配線したときに循環importを作る。results_board.py 自身が既に「growth_report._is_today
    と同型」と明記する既知の重複パターンを踏襲し、共有モジュールは新設しない）。
    **ただし results_board._parse_timestamp と異なり、naive文字列をUTCとみなして救済しない**
    （手編集された local 時刻の誤受理を防ぐ。巡2レビュー指摘）。
    """
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if not _TZ_AWARE_RE.match(value):
        return None  # naive timestamp は invalid
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


def _hash_correction_message(base: dict) -> Optional[str]:
    """基底 correction の message ハッシュを算出する（§7・§10・巡2 [Must]1 対応）。

    対象フィールド: extracted_learning（無ければ message）。
    正規化: NFC Unicode正規化 → 改行を \\n へ統一（CRLF→LF）→ 前後空白除去。
    correction_apply_attempted 追記時に同じ関数で計算した値と一致するはずの参照実装。
    """
    text = base.get("extracted_learning") or base.get("message")
    if not isinstance(text, str) or not text:
        return None
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class FoldedCorrection:
    base: dict
    reflect_applied_at: Optional[str] = None
    reflect_target_kind: Optional[str] = None
    reflect_target_path: Optional[str] = None
    reflect_draft_line: Optional[str] = None
    correction_message_sha256: Optional[str] = None
    has_pillar2_fields: bool = False
    reconciled: bool = False  # フェーズ3の確認イベントが無く、フェーズ1から復元した


@dataclass
class FoldHealth:
    orphan_events_unexpected: int = 0   # decay window内なのに基底が見つからないattemptイベント（異常）
    orphan_events_expected: int = 0     # decay window外＝基底がprune済みと想定できるattemptイベント
    unknown_schema_events: int = 0
    invalid_events: int = 0             # 必須項目欠落・値域外・hash形式不正・naive timestamp
    duplicate_base_row_count: int = 0   # 重複 correction_id を持つ基底の**行数**（除外対象。#587巡2 [Nit]で命名訂正）
    orphan_confirmations: int = 0       # confirms_attempt_id が解決しないapplied（巡2 [Must]1）
    duplicate_confirmations: int = 0    # 同一attemptを複数appliedが参照（巡2 [Must]1）
    hash_mismatch_count: int = 0        # attemptのhashが基底messageと不一致（巡2 [Must]1・[Should]3）
    # ID不正行は project scope を判定できる pillar2_metrics で集計する。
    # FoldHealth に同名の全PJ合算カウンタは置かない。
    invalid_base_id_records: list[dict] = field(default_factory=list, repr=False)


def _attempt_is_valid(ev: dict) -> bool:
    """correction_apply_attempted の必須項目・値域・hash形式・ID形式を検証する。"""
    if not validate_correction_id(ev.get("correction_id")):
        return False
    if not validate_correction_id(ev.get("target_correction_id")):
        return False
    if ev.get("reflect_target_kind") not in _KNOWN_TARGET_KINDS:
        return False
    path = ev.get("reflect_target_path")
    if not isinstance(path, str) or not path:
        return False
    draft = ev.get("reflect_draft_line")
    if not isinstance(draft, str) or not draft:
        return False
    sha = ev.get("correction_message_sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        return False
    return True


def _applied_is_valid(ev: dict) -> bool:
    """correction_applied の必須項目・ID形式を検証する（§7設計変更によりkind/path/draft/hashは持たない）。"""
    if not validate_correction_id(ev.get("correction_id")):
        return False
    if not validate_correction_id(ev.get("target_correction_id")):
        return False
    if not validate_correction_id(ev.get("confirms_attempt_id")):
        return False
    return True


def fold_corrections(
    base_records: list, event_records: list, *, now: Optional[datetime] = None,
    decay_grace_days: int = 90,  # prune.config.DEFAULT_DECAY_DAYS を呼出側が渡す（#587 巡1 M16）
) -> tuple[list[FoldedCorrection], FoldHealth]:
    """corrections.jsonl の基底レコード列と reflect_apply_events.jsonl のイベント列を結合する。"""
    now = now or datetime.now(timezone.utc)
    health = FoldHealth()

    # --- 基底: 重複 correction_id を検出し、除外集合を作る（M6・blocking d の後半）---
    dup_counts = find_duplicate_ids([r for r in base_records if isinstance(r, dict)])
    duplicate_ids = set(dup_counts.keys())
    # 巡2 [Nit]: 「重複IDの種類数」でなく「重複に関与した行数の合計」を持つ（同一IDが2行なら2）。
    health.duplicate_base_row_count = sum(dup_counts.values())

    bases_by_id: dict[str, dict] = {}
    order: list[str] = []
    for rec in base_records:
        if not isinstance(rec, dict):
            continue
        cid = rec.get("correction_id")
        if not isinstance(cid, str) or not cid:
            continue
        if cid in duplicate_ids:
            continue  # fail-closed: 同定不能なので数えない
        if cid not in bases_by_id:
            order.append(cid)
        bases_by_id[cid] = rec

    folded_by_id = {cid: FoldedCorrection(base=bases_by_id[cid]) for cid in order}

    # --- イベント: type別に分け、値域検証する ---
    attempts_by_own_id: dict[str, dict] = {}   # attempt.correction_id -> attempt（有効なもののみ）
    attempted_by_target: dict[str, list[dict]] = {}
    applied_events: list[dict] = []            # 有効な applied（未grouping。confirms解決前）
    for ev in event_records:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("event_type")
        if etype not in ("correction_apply_attempted", "correction_applied"):
            continue
        if ev.get("schema_version") != 1:
            health.unknown_schema_events += 1
            continue
        if etype == "correction_apply_attempted":
            if not _attempt_is_valid(ev):
                health.invalid_events += 1
                continue
            ts = _parse_iso8601_utc(ev.get("attempted_at"))
            if ts is None:
                health.invalid_events += 1
                continue
            # 巡2 [Must]1: attempt のハッシュを対象基底の message ハッシュと突合する。
            # 対象基底が fold 対象に無い（未 backfill・重複除外・prune済み等）場合は
            # 突合できないため、この場では invalid にせず後段の orphan/reconcile 判定に委ねる。
            target_id = ev.get("target_correction_id")
            base = bases_by_id.get(target_id)
            if base is not None:
                expected_hash = _hash_correction_message(base)
                if expected_hash is not None and ev.get("correction_message_sha256") != expected_hash:
                    health.hash_mismatch_count += 1
                    continue
            attempts_by_own_id[ev["correction_id"]] = ev
            attempted_by_target.setdefault(target_id, []).append(ev)
        else:  # correction_applied
            if not _applied_is_valid(ev):
                health.invalid_events += 1
                continue
            ts = _parse_iso8601_utc(ev.get("reflect_applied_at"))
            if ts is None:
                health.invalid_events += 1
                continue
            applied_events.append(ev)

    # --- 巡2 [Must]1: applied は「既存attemptへの参照」であることを検証する ---
    # 同一attemptを複数appliedが参照する（重複確認）場合は両方を無効化する。
    confirmation_count: dict[str, int] = {}
    for ev in applied_events:
        confirmation_count[ev["confirms_attempt_id"]] = confirmation_count.get(ev["confirms_attempt_id"], 0) + 1

    applied_by_target: dict[str, list[tuple[dict, dict]]] = {}  # target -> [(applied_ev, attempt_ev), ...]
    for ev in applied_events:
        attempt_id = ev["confirms_attempt_id"]
        if confirmation_count[attempt_id] > 1:
            health.duplicate_confirmations += 1
            continue
        attempt = attempts_by_own_id.get(attempt_id)
        if attempt is None:
            health.orphan_confirmations += 1  # 参照先attemptが無い（無効化済み・存在しない・hash不一致で除外済み等）
            continue
        if attempt.get("target_correction_id") != ev.get("target_correction_id"):
            health.orphan_confirmations += 1  # target不一致＝手編集の疑い
            continue
        applied_by_target.setdefault(ev["target_correction_id"], []).append((ev, attempt))

    # --- M15: 最新イベントの選択は「ファイル順」でなく (timestamp, 自身のcorrection_id) の
    #     決定的な max。真に順序不問になる。並行書込みで古い操作が後着しても上書きされない ---
    def _latest_pair(pairs: list[tuple[dict, dict]]) -> Optional[tuple[dict, dict]]:
        if not pairs:
            return None
        return max(
            pairs,
            key=lambda p: (_parse_iso8601_utc(p[0].get("reflect_applied_at")), p[0].get("correction_id", "")),
        )

    def _latest_attempt(events: list[dict]) -> Optional[dict]:
        if not events:
            return None
        return max(
            events,
            key=lambda e: (_parse_iso8601_utc(e.get("attempted_at")), e.get("correction_id", "")),
        )

    for target_id, pairs in applied_by_target.items():
        if target_id not in folded_by_id:
            # M16: decay を考慮した orphan 判定。基底が正規に decay 削除された可能性がある
            # 期間より新しいイベントだけを「異常」として数える。
            latest = _latest_pair(pairs)
            ts = _parse_iso8601_utc(latest[0].get("reflect_applied_at")) if latest else None
            if ts is not None and (now - ts).days > decay_grace_days:
                health.orphan_events_expected += 1
            else:
                health.orphan_events_unexpected += 1
            continue
        applied_ev, attempt_ev = _latest_pair(pairs)
        f = folded_by_id[target_id]
        # 巡2 [Must]1: kind/path/draft/hash は「唯一の真実源」である attempt からのみ取得する
        # （applied自身はこれらのフィールドを持たない・§7）。
        f.reflect_applied_at = applied_ev.get("reflect_applied_at")
        f.reflect_target_kind = attempt_ev.get("reflect_target_kind")
        f.reflect_target_path = attempt_ev.get("reflect_target_path")
        f.reflect_draft_line = attempt_ev.get("reflect_draft_line")
        f.correction_message_sha256 = attempt_ev.get("correction_message_sha256")
        f.has_pillar2_fields = True
        f.reconciled = False

    # --- M1/M2: フェーズ3の確認イベントが無いが、base.reflect_status=="applied" かつ
    #     フェーズ1のイベントがあるものは、フェーズ1から復元する（2フェーズ照合）---
    for target_id, events in attempted_by_target.items():
        if target_id not in folded_by_id:
            continue
        f = folded_by_id[target_id]
        if f.has_pillar2_fields:
            continue  # 既に確認イベントで確定済み
        if f.base.get("reflect_status") != "applied":
            continue  # 基底側もまだ applied でないので復元しない（未完了の通常の試行）
        latest = _latest_attempt(events)
        f.reflect_applied_at = latest.get("attempted_at")
        f.reflect_target_kind = latest.get("reflect_target_kind")
        f.reflect_target_path = latest.get("reflect_target_path")
        f.reflect_draft_line = latest.get("reflect_draft_line")
        f.correction_message_sha256 = latest.get("correction_message_sha256")
        f.has_pillar2_fields = True
        f.reconciled = True

    return [folded_by_id[cid] for cid in order], health
```

### 11.1 legacy 判定（blocking e）

`correction_id` はあるが `has_pillar2_fields=False` のまま（確認イベントも復元可能な
フェーズ1イベントも無い）基底レコードは `legacy_unverified_count`（§12）に分類され、
`count` には含めない。

### 11.2 重複 correction_id（M6・blocking d）

`find_duplicate_ids` で検出された基底 `correction_id` は fold の入口で完全に除外する
（fail-closed）。`health.duplicate_base_row_count` で件数を可視化し、1件でもあれば
`degraded=True`（§12）。

### 11.3 イベント値域検証・確認イベントの正当性検証（M3・[Must]1・blocking o）

`_attempt_is_valid`/`_applied_is_valid` が ID形式・値域・hash形式を検証する。1件でも
不正なら `FoldHealth.invalid_events` に計上し、そのイベントは fold に取り込まない。
**巡2 [Must]1 対応**: `correction_applied` は追加で次の3条件を満たさない限り確認として
採用されない: ①`confirms_attempt_id` が実在する有効な attempt を指す ②その attempt の
`target_correction_id` が applied 自身の `target_correction_id` と一致する ③同一 attempt を
複数の applied が参照していない（重複確認）。①②に違反したものは `orphan_confirmations`、
③は `duplicate_confirmations` に計上し、いずれも fold から除外する。さらに attempt 自身は
対象基底の `extracted_learning`/`message` から計算した期待ハッシュと一致することを検証し
（`hash_mismatch_count`）、不一致なら attempt ごと無効化する（=それを参照する applied も
連鎖して `orphan_confirmations` になる）。§12 で上記いずれかが1件でもあれば `measured=False`
を返す。

### 11.4 orphan の decay 考慮（M16・blocking の恒久degraded化の解消）

基底が `prune/corrections.py` の decay（既定 `DEFAULT_DECAY_DAYS=90`）で物理削除されると、
対応するイベントは orphan になる。**decay grace 期間（既定90日。`decay_grace_days` 引数で
`prune.config.DEFAULT_DECAY_DAYS` を呼出側が渡す——マジックナンバーの独自定義を避ける）を
超えたイベントは「基底が正規に decay 削除された」と想定し `orphan_events_expected` へ**、
**それより新しいイベントは「まだ decay されているはずがないのに基底が見つからない」という
異常として `orphan_events_unexpected` へ**分類する。`degraded` の判定（§12）は
`orphan_events_unexpected` だけを見る（`orphan_events_expected` は通常運用で必ず発生する
ため degraded に含めない）。

**既知の限界**: 個別レコードの `decay_days` フィールドは既定値 `90` をカスタム上書きできる
（`prune/corrections.py:91` `record.get("decay_days", DEFAULT_DECAY_DAYS)`）。基底が
prune で消えた後は元の `decay_days` 値を読めないため、`decay_grace_days` は既定値の
90日で近似する。カスタム decay_days（既定より短い）を使った基底が90日以内に prune された
場合、そのイベントは実際には正規の decay 削除なのに `orphan_events_unexpected`（異常側）に
誤分類されうる。**この誤りは安全側**（degraded=True へ倒れ、過小評価より過剰な注意を
促す方向）であり、§16 の未実測項目として記録する。

## 12. `count_applied_reflections`（設計のみ・実装は次巡）

`scripts/lib/pillar2_metrics.py`（新規モジュール）が `reflect_fold.fold_corrections` を呼ぶ。
raw record の取得は `fleet.queue_materials.read_corrections_records_with_health` を
再利用する（`corrections_path: Path` を明示引数に取る汎用実装。basename ハードコード無し・
`scripts/lib/fleet/queue_materials.py:227-` 実測）。

**巡2 [Must]3 対応（snapshot 一貫性）**: base と event を別々に read する以上、read の間に
別セッションが invalidate/prune すると、無効化済みなのに読取済みの古い値を使って `count` へ
残りうる（blocking (d) を破る）。**2ファイルの `stat()` を read 前後で比較し、変化があれば
有限回 retry、それでも安定しなければ `measured=False` にする**:

```python
def _snapshot_stat(path) -> Optional[tuple]:
    """path の (mtime_ns, size) を返す。存在しなければ None（"無い"も比較対象の状態にする）。"""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_stable_snapshot(
    corrections_path, events_path, *, max_retries: int = 3,
):
    """base/eventの2ファイルを、read前後でstatが変化しない状態（安定snapshot）まで
    有限回re-readする（巡2 [Must]3）。安定しなければ最後に読んだ内容と stable=False を返す。
    """
    from fleet.queue_materials import read_corrections_records_with_health

    for _ in range(max_retries):
        stat_before = (_snapshot_stat(corrections_path), _snapshot_stat(events_path))
        base_records, base_health = read_corrections_records_with_health(corrections_path)
        event_records, event_health = read_corrections_records_with_health(events_path)
        stat_after = (_snapshot_stat(corrections_path), _snapshot_stat(events_path))
        if stat_before == stat_after:
            return base_records, base_health, event_records, event_health, True
    return base_records, base_health, event_records, event_health, False


def count_applied_reflections(
    project_root, *, corrections_path=None, events_path=None, now=None, window_days: int = 30
) -> dict:
    """project_root: 対象PJのリポジトリ絶対パス（#587巡2 [Should]1: 第6版の引数名
    `slug` は絶対パスを渡す契約と矛盾していたため `project_root` へ統一）。
    §13の _pillar2_project_scope 内部でこの絶対パスから2種の識別子（authoritative slug /
    hot-path slug）を導出する。
    """
    from prune.config import DEFAULT_DECAY_DAYS
    import rl_common

    base_records, base_health, event_records, event_health, snapshot_stable = (
        _read_stable_snapshot(
            corrections_path or (rl_common.DATA_DIR / "corrections.jsonl"),
            events_path or (rl_common.DATA_DIR / "reflect_apply_events.jsonl"),
        )
    )
    now = now or datetime.now(timezone.utc)
    folded, fold_health = fold_corrections(
        base_records, event_records, now=now, decay_grace_days=DEFAULT_DECAY_DAYS,
    )
    window_start = now - timedelta(days=window_days)
    invalid_base_id_counts = _count_scoped_invalid_base_ids(fold_health, project_root)
    invalid_base_id_applied_row_count = (
        invalid_base_id_counts["invalid_base_id_applied_same_project_row_count"]
        + invalid_base_id_counts["invalid_base_id_applied_global_looking_row_count"]
    )
    invalid_base_id_non_applied_row_count = (
        invalid_base_id_counts["invalid_base_id_non_applied_same_project_row_count"]
        + invalid_base_id_counts["invalid_base_id_non_applied_global_looking_row_count"]
    )

    eligible = []
    legacy_unverified = 0
    invalidated_count = 0
    other_kind_count = 0
    for f in folded:
        if f.base.get("invalidated"):
            invalidated_count += 1
            continue
        # 第7版で追加した1行: reflect_status!="applied"（pending/promoted/skipped等、
        # そもそも反映されていない通常の在庫）は柱2と無関係であり legacy_unverified に
        # 含めない。この行が無いと legacy_unverified_count が「未反映の通常バックログ」で
        # 恒常的に埋まり、下の measured=False 条件（[Should]2）が実運用でほぼ常に発火して
        # しまう（実装時に必ず入れること。§16「見落としやすい1行」参照）。
        if f.base.get("reflect_status") != "applied":
            continue
        if not f.has_pillar2_fields:
            legacy_unverified += 1
            continue
        if f.reflect_target_kind == "other":
            other_kind_count += 1
            continue
        ts = _parse_iso8601_utc(f.reflect_applied_at)
        if ts is None or not (window_start <= ts <= now):
            continue
        scope = _pillar2_project_scope(f.base, project_root)  # §13
        if scope not in ("same-project", "global-looking"):
            continue
        eligible.append(f)

    groups: dict[tuple, list] = {}
    for f in eligible:
        key = (f.reflect_target_kind, f.reflect_target_path, f.reflect_draft_line.strip())
        groups.setdefault(key, []).append(f)

    count = len(groups)
    applied_list = [
        {
            "target_kind": k[0], "target_path": k[1],
            "reflect_applied_at": min(x.reflect_applied_at for x in v),
            "reconciled": any(x.reconciled for x in v),
        }
        for k, v in groups.items()
    ][:10]

    # 巡2 [Should]2: measured の意味を「全実反映件数」と「照合できた部分集合」で分ける。
    # legacy_unverified_count（未照合の旧/中断レコード）が1件でもあれば、count は
    # 「照合できた分だけ」であり全実反映件数ではないため measured=False にする
    # （第6版は validation/duplicate 系のみを見ており、legacy件数を見ていなかった）。
    degraded = (
        not snapshot_stable  # 巡2 [Must]3
        or not base_health["readable"]
        or not event_health["readable"]
        or base_health["malformed_lines"] > 0
        or event_health["malformed_lines"] > 0
        or fold_health.orphan_events_unexpected > 0
        or fold_health.unknown_schema_events > 0
        or fold_health.invalid_events > 0
        or fold_health.duplicate_base_row_count > 0
        or fold_health.orphan_confirmations > 0       # 巡2 [Must]1
        or fold_health.duplicate_confirmations > 0     # 巡2 [Must]1
        or fold_health.hash_mismatch_count > 0         # 巡2 [Must]1・[Should]3
        or legacy_unverified > 0                       # 巡2 [Should]2
        or invalid_base_id_applied_row_count > 0       # #606: 数値の基底を照合不能
    )

    return {
        "count": count,
        "measured": not degraded,  # producer契約: countは常に整数だが、measured=Falseの
                                    # ときは「照合できた部分集合」に過ぎず全実反映件数ではない
                                    # ——表示配線（別issue・§0対象外）はこのフラグに従う義務を負う
        "legacy_unverified_count": legacy_unverified,
        "invalidated_count": invalidated_count,
        "other_kind_count": other_kind_count,
        "reconciled_count": sum(1 for f in eligible if f.reconciled),
        "applied_list": applied_list,
        "health": {
            "degraded": degraded,
            "snapshot_stable": snapshot_stable,
            "base_readable": base_health["readable"],
            "base_read_error": base_health["error"],
            "base_malformed_lines": base_health["malformed_lines"],
            "events_readable": event_health["readable"],
            "events_read_error": event_health["error"],
            "events_malformed_lines": event_health["malformed_lines"],
            "orphan_events_unexpected": fold_health.orphan_events_unexpected,
            "orphan_events_expected": fold_health.orphan_events_expected,
            "unknown_schema_events": fold_health.unknown_schema_events,
            "invalid_events": fold_health.invalid_events,
            "duplicate_base_row_count": fold_health.duplicate_base_row_count,
            "orphan_confirmations": fold_health.orphan_confirmations,
            "duplicate_confirmations": fold_health.duplicate_confirmations,
            "hash_mismatch_count": fold_health.hash_mismatch_count,
            "invalid_base_id_applied_row_count": invalid_base_id_applied_row_count,
            "invalid_base_id_non_applied_row_count": invalid_base_id_non_applied_row_count,
            # 以下4キーは合計の内訳。0件でも必ず出力する。
            "invalid_base_id_applied_same_project_row_count": invalid_base_id_counts["invalid_base_id_applied_same_project_row_count"],
            "invalid_base_id_applied_global_looking_row_count": invalid_base_id_counts["invalid_base_id_applied_global_looking_row_count"],
            "invalid_base_id_non_applied_same_project_row_count": invalid_base_id_counts["invalid_base_id_non_applied_same_project_row_count"],
            "invalid_base_id_non_applied_global_looking_row_count": invalid_base_id_counts["invalid_base_id_non_applied_global_looking_row_count"],
        },
        "not_measured": {
            "hook": {"reason": "no_store"},
            "pitfall_memory": {"reason": "mtime_collision"},
        },
        "generated_at": now.isoformat(),
    }
```

`invalid_base_id_*_row_count` の6キーは public `health` の project-scope 済み集計である。
内訳は `same-project` と `global-looking` を対象にし、`project-specific-other` と
`invalidated` は除外する。`FoldHealth` は判定前の `invalid_base_id_records` だけを保持し、
全PJ合算カウンタと public health の同名カウンタが同居しないようにする。

**producer 契約（[Should]2 対応で確定）**: `count` は常に整数（`None` にはしない）。
`measured=True` のときのみ「対象期間の全実反映件数」として扱ってよい。`measured=False`
のときは「照合・分類・snapshot安定性のいずれかが未達で、実際に反映されたものと食い違う
可能性がある部分集合」であり、**表示配線（別issue・§0③）はこの `measured` フラグに従い、
`False` のときは確定値として表示してはならない**契約とする。

**既知の帰結（§16「移行期間中は measured=False が続く」参照）**: `legacy_unverified_count`
を degraded 条件に含めたことで、本設計の適用直後〜旧レコードの decay（既定90日）が
進むまでの期間は `measured=False` が続く見込みが高い。これは「まだ測れていないものが
残っている」という正直な信号であり、round0 の信頼境界（自分たちの運用ミスのみ）と
整合する。

**`results_board.py` への配線は本 issue のスコープ外**（§0対象外）。

## 13. `classify_project_scope` の再利用（slug/絶対パス両対応・sibling worktree 対応）

**巡1指摘（M8）**: 実データの `project_path` フィールドは絶対パスではなく**リポジトリ
slug**である（`hooks/correction_detect.py:148` が `common.project_name_from_dir(_proj_dir)`
を書く。`project_name_from_dir` は `pj_slug_fast` に委譲する slug 生成関数——
`scripts/lib/rl_common/persistence.py:25-40` 実測、#492 で basename から slug 正規化へ
変更）。一方 `classify_project_scope`（`reflect.py:169-201`）の第2引数 `current_project`
は絶対パスを渡す契約であり、内部で `_normalize_path` による**パス比較**のみを行う。
slug 値とパス値を直接比較すると `"same-project"` に一致しない。

**この不一致自体は `classify_project_scope` 側の既存の問題であり、本設計はその関数を
改変しない**（既存関数の挙動変更は対象外）。代わりに、pillar2 専用の薄いラッパーで
両形式を先に吸収する。

**巡2 [Must]4 対応（sibling worktree での誤判定・実測で確認済みのバグ）**: `project_name_from_dir`
は `pj_slug_fast` に `data_dir` を渡さずに呼ぶため（`persistence.py:36-39`）、
`/.claude/worktrees/` マーカーの無い sibling-dir worktree（本設計文書自身が置かれている
`/Users/matsukaze-takashi/wt/ea-587` がまさにこれ）では SessionStart cache を参照せず
**basename**（`"ea-587"`）へ落ちる。一方、同じ論理プロジェクトのメイン checkout
（`~/matsukaze-utils/evolve-anything`）から検出された correction は `project_path=="evolve-anything"`
になる。**同一プロジェクトなのに worktree の違いだけで slug 表現が食い違い、
`_pillar2_project_scope` が単一の slug（`project_name_from_dir` のみ）としか比較しないと、
片方のセッションから集計するともう片方由来の反映が `same-project` に一致せず除外される**
（実測: `python3 -c "..."` で `/Users/matsukaze-takashi/wt/ea-587` を渡すと
`"ea-587"`、`resolve_pj_slug` は `"evolve-anything"` を返すことを確認・2026-09-01）。

**修正**: `resolve_pj_slug`（git-common-dir 経由の authoritative slug。worktree 間で安定）を
**第一候補**とし、`project_name_from_dir`（writer の hot-path が実際に書く可能性のある
値。cache 未整備の sibling worktree では basename）を**第二候補**として、どちらかに
一致すれば `same-project` とする:

```python
def _pillar2_project_scope(correction: dict, project_root) -> str:
    """classify_project_scope を呼ぶ前に、project_path が slug 形式でも一致判定できるよう
    現在の repo の slug 表現（authoritative / hot-path の両方）を突合する
    （#587 巡1 [Must]8・巡2 [Must]4）。

    既存 classify_project_scope（reflect.py:169）は current_project を絶対パスとして
    比較する契約のまま変更しない。ここでは「slugとして一致するか」を先に確認し、
    一致すれば same-project として早期return、しなければ既存関数へそのまま委譲する。
    """
    from pj_slug import resolve_pj_slug
    from rl_common.persistence import project_name_from_dir

    project_path = correction.get("project_path")
    if isinstance(project_path, str) and project_path:
        # 第一候補: authoritative slug（worktree間で安定・巡2 [Must]4 対応）
        if project_path == resolve_pj_slug(project_root):
            return "same-project"
        # 第二候補: hot-path slug（writerが実際に書いた可能性のある値。#492以前の
        # 絶対パス由来レコードや cache未整備worktree由来のレコードを拾う）
        if project_path == project_name_from_dir(str(project_root)):
            return "same-project"

    from reflect import classify_project_scope  # 既存関数（絶対パス比較）
    return classify_project_scope(correction, str(project_root))
```

`count_applied_reflections`（§12）は `project_root` に**リポジトリの絶対パス**を渡す
（`reflect.py:1498` の既存呼び出しと同じ形）。

**残る限界**（§16 に転記）: `resolve_pj_slug`/`project_name_from_dir` のどちらとも一致しない
旧 slug（プロジェクトの rename 以前の名称等）は本ラッパーではまだ拾えない。
`pj_slug.PJ_SLUG_ALIASES`/`pj_slug_aliases_for` という既存の別名解決 SoT があるが、
`_pillar2_project_scope` はまだそれを使っていない——実装1巡で
`pj_slug_aliases_for(resolve_pj_slug(project_root))` の戻り値集合との一致も試すことを
検討する（本設計では確定しない・§17 新規疑問）。

**採用する値域**: `scope in ("same-project", "global-looking")`（`"project-specific-other"`
は除外）。実測: `classify_project_scope` の戻り値3値は `reflect.py:169-201` で確認済み。

## 14. 移行

### 14.1 既存データ

（第5版から継承）`correction_id` を持たない基底レコードは fold の対象外。
移行スクリプトの実行はこの設計の前提ではない。

### 14.2 `prune/corrections.py` の decay 削除との相互作用

§11.4 で decay grace（`DEFAULT_DECAY_DAYS`）を考慮した orphan 判定を導入したことで、
通常運用（90日 decay の範囲内でイベントが発見される）では `degraded` が恒久化しない
（M16 の解消）。

### 14.3 読み手への影響

（第5版から継承・変更なし）`corrections.jsonl` を読む既存6箇所はイベント行を一度も
目にしない（別ファイルのため）。**変更不要**。

## 15. 検証計画（真の変異試験）

**巡1指摘（M11〜M14）への全面対応**: 「異常データを与えて期待どおり除外できるかを見る
通常テスト」と「プロダクトコードを壊す変異を入れて検査が赤くなることを確認する陰性試験」
を区別する。各行に「壊すプロダクトコード（ファイル:関数）」「壊す不変条件」
「通したい検査経路（test id）」「baseline緑→変異赤」を書く。

テストファイル: `scripts/lib/tests/test_reflect_fold.py`（fold 単体）・
`scripts/lib/tests/test_pillar2_metrics.py`（集計）・
`scripts/lib/tests/test_correction_id.py` および `scripts/lib/rl_common/tests/
test_append_jsonl_correction_id.py`（§3 の `append_unique_record` 追加分。**巡2 [Should]
指摘で実在パスへ訂正**——第6版は前者のみを`scripts/lib/rl_common/tests/`配下と誤記していた。
実在するのは`scripts/lib/tests/test_correction_id.py`と`scripts/lib/rl_common/tests/
test_append_jsonl_correction_id.py`の2ファイルで、新規分はロジックの近さから後者へ追加する）・
`scripts/lib/tests/test_shrink_freeze.py`（§4.3 の専用契約テスト）・
`scripts/lib/tests/test_store_classification.py`・`scripts/lib/tests/test_write_barrier.py`
（§4.1 golden 更新の回帰確認）・
`skills/reflect/scripts/tests/test_reflect_apply_event.py`（§8 の2フェーズ追記の統合試験）。

| # | 壊すプロダクトコード | 壊す不変条件 | 通したい検査経路 | baseline緑→変異赤 |
|---|---|---|---|---|
| (a)-1 | `reflect_fold.fold_corrections`: `f.reflect_applied_at = latest.get(...)` の代入行を削除する変異 | 反映日時が集計に伝播する | `test_pillar2_metrics.py::test_count_applied_reflections_uses_reflect_applied_at` | baseline: 正常fixtureで`applied_list`に日時が入り緑。変異適用後: 日時が常に`None`になり、後段の`_parse_iso8601_utc(None)`が`None`を返して該当エントリが窓外扱いになり`count`が0へ落ちて赤 |
| (a)-2（誤フィールド窓判定） | `pillar2_metrics.count_applied_reflections`: `ts = _parse_iso8601_utc(f.reflect_applied_at)` を `ts = _parse_iso8601_utc(f.base.get("timestamp"))` に置換する変異 | 窓判定は検出時刻でなく反映時刻を使う | 同上（`timestamp`=窓内・`reflect_applied_at`=窓外の fixture と、その逆を対で用意） | baseline: 前者除外・後者含有で緑。変異適用後: 判定が逆転し赤 |
| (b)（巡2指摘で単体/統合を分離） | `reflect_apply_match.classify_reflect_target_kind`: `CLAUDE.md` 判定の `return "global_claude_md"` を削除し `"other"` へフォールスルーさせる変異 | CLAUDE.md が測定対象として分類される | `test_reflect_apply_match.py::test_classify_reflect_target_kind_claude_md`（**分類関数の戻り値のみを直接assertする単体試験**。`other_kind_count`/`count`のような集計層への波及はassertしない——配線試験は下の(b)-統合が別途担う） | baseline: `classify_reflect_target_kind(...)`が`"global_claude_md"`を返し緑。変異適用後は`"other"`を返し赤 |
| (b)-統合 | `reflect.py` の `--apply` ハンドラで、フェーズ1イベントの `reflect_target_kind` を分類関数の戻り値でなく固定文字列 `"other"` にハードコードする変異 | classifier配線がCLI経路まで届く | `test_reflect_apply_event.py::test_apply_writes_classified_kind`（CLI起動→イベント行→fold→count まで通す統合試験） | baseline: 分類結果がイベント行に書かれ緑。変異適用後: 常に`"other"`になり`other_kind_count`に落ちて赤（分類器単体テストだけでは検出できない配線ミスを狙う・巡1 [Should]対応） |
| (c)-1（重複イベント畳み込み・巡2指摘で強化） | `reflect_fold.fold_corrections`: `_latest_pair`（選択ロジック）を「fixture出現順の先頭」固定に置換する変異 | 同一反映は「最新の」1状態として数える | `test_reflect_fold.py::test_fold_selects_latest_by_timestamp_not_order`（**`FoldedCorrection.reflect_applied_at`/`reflect_target_path`の値そのものをassertする**——巡1版は`count==1`だけを見ており、値を見なければ「先頭固定」変異でも`count`は1のまま変わらず検出できなかった。**新旧2fixtureの出現順を正順・逆順の両方で用意し**、どちらの順でも最新の値が選ばれることを確認する） | baseline: 出現順に関わらず最新の`reflect_applied_at`/`reflect_target_path`が選ばれ緑。変異適用後は出現順が逆のfixtureで古い値が選ばれ赤 |
| (c)-2（path別名の偽陽性） | `reflect_apply_match`側のpath正規化ステップ（§6手順3の`repo_identity`合成）を`str(Path(target_path))`だけに簡略化する変異 | 同一物理ファイルの別名表記を同一キーにする | `test_reflect_apply_event.py::test_apply_normalizes_path_before_grouping`（**フィクスチャを事前正規化せず**、`--apply`に相対パスと絶対パスを別々に渡して実際にイベントを書かせ、書かれた `reflect_target_path` の値そのものが一致することを確認する——巡1 [Should]「fixture作成前に正規化するとbaselineでも緑になる」への対応） | baseline: 相対/絶対どちらで`--apply`しても書かれる`reflect_target_path`が同一になり、2回のapplyでも`count==1`で緑。変異適用後は正規化前の文字列がそのまま書かれ、`count==2`になり赤 |
| (c)-3（正当な再反映） | 既知の限界として残す（変異試験は作らない。§16に理由を明記） | — | — | — |
| (d)（巡2指摘でfile:function訂正） | `pillar2_metrics.count_applied_reflections`（**`reflect_fold`ではなくここ**——`invalidated`判定は§12にのみ存在し`fold_corrections`は関知しない。巡2指摘で訂正）: `if f.base.get("invalidated"): invalidated_count += 1; continue` を削除する変異 | 無効化済みは数えない | `test_pillar2_metrics.py::test_invalidated_excluded_from_count` | baseline: `invalidated=True`の基底（有効なイベント付き）が`count`から除外され緑。変異適用後は`count`に混入し赤 |
| (e)（巡2指摘で作り直し） | `reflect_fold.fold_corrections`: `has_pillar2_fields`判定を`bool(f.base.get("correction_id"))`（イベントの有無を見ない）に置換する変異 | 旧レコードは数えない | `test_reflect_fold.py::test_legacy_folded_correction_has_pillar2_fields_false`（**`fold_corrections`の戻り値を直接assertする。`count_applied_reflections`経由の統合試験にしない**——巡2指摘: legacy行は`reflect_applied_at`が`None`のままなので、`has_pillar2_fields`を誤って`True`にする変異を入れても窓判定で弾かれ`count`は変わらず、統合試験だとbaseline・変異後ともに緑になり検出できなかった） | baseline: `correction_id`はあるがイベント（attempt/applied）が一切無い基底の`FoldedCorrection.has_pillar2_fields`が`False`で緑。変異適用後は`True`になり赤（fold層で直接検出するため、後続のwindow判定の有無に左右されない） |
| (m)-1（2フェーズ照合。M1/M2） | `reflect_fold.fold_corrections`: フェーズ1からの復元ブロック（`attempted_by_target`のループ）全体を削除する変異 | 二段書込みの中断は照合可能でなければならない | `test_reflect_fold.py::test_reconciles_from_attempted_event_when_confirmation_missing` | baseline: `correction_apply_attempted`のみ存在し`correction_applied`が無く、かつ`base.reflect_status=="applied"`のfixtureで`has_pillar2_fields=True・reconciled=True`となり`count`に含まれ緑。変異適用後は復元されず`legacy_unverified_count`に落ちて赤（=中断による過小計上が再現し、検査がそれを捕捉する） |
| (m)-2（陽性対照） | 同上 | 同上 | 同上 | `correction_applied`確認イベントが正常に存在するfixtureでは`reconciled=False`のまま`count`に含まれ、変異の有無に関わらず緑（陰性試験(m)-1と対にする陽性対照） |
| (n)（巡2 [Nit]で命名・期待値訂正） | `reflect_fold.fold_corrections`: 重複ID除外ブロック（`if cid in duplicate_ids: continue`）を削除する変異 | 重複 correction_id は同定不能として数えない | `test_reflect_fold.py::test_duplicate_base_rows_excluded_and_flagged` | baseline: 同一 `correction_id` を持つ基底2行（片方 `invalidated=True`）のfixtureで、両方が`count`から除外され`health.duplicate_base_row_count==2`（**2行**。`find_duplicate_ids`は重複IDの**種類数**でなく個々の判定に使う——第6版は`len(duplicate_ids)==1`という誤った値をテストに期待していたため`(n)`自体がbaselineから赤だった。第7版で`sum(dup_counts.values())`へ実装を直し、期待値も2行に統一）・`degraded==True`になり緑。変異適用後は「最後を正」として片方（非invalidated側）が`count`に混入し赤 |
| (o) | `reflect_fold._attempt_is_valid`: 全チェックを`return True`に固定する変異 | 手編集・誤移行の不正attemptは照合済みとして混入しない | `test_reflect_fold.py::test_invalid_attempt_event_rejected`（`correction_message_sha256`を`"not-a-hash"`にした最小attemptイベントのfixture） | baseline: `has_pillar2_fields=False`のまま`invalid_events`にカウントされ`measured=False`になり緑。変異適用後は検証をすり抜けて`count`に混入し赤 |
| **(p)（巡2 [Must]1・新設）** | `reflect_fold.fold_corrections`: `confirms_attempt_id`解決チェック（`attempt = attempts_by_own_id.get(attempt_id); if attempt is None: ...continue`）を削除し、参照先の有無を確認せず無条件に受理する変異 | `correction_applied` は既存 attempt への参照でなければならない | `test_reflect_fold.py::test_orphan_applied_confirmation_is_degraded`（reviewer提案の変異そのもの） | baseline: `confirms_attempt_id`が実在しないattemptを指す孤立`applied`イベントのfixtureで、`orphan_confirmations==1`・`degraded==True`になり`count`には混入せず緑。変異適用後は検証なしで受理され、kind/path/draft/hashが取得できず（attemptが無いため）例外になるか、実装次第で不正な値のまま`count`に混入し赤 |
| (p)-陽性対照 | 同上 | 同上 | 同上 | 正しい`confirms_attempt_id`で実在attemptを参照する正常fixtureでは`count`に含まれ緑 |
| **(q)（巡2 [Must]1・新設）** | `reflect_fold.fold_corrections`: hashハッシュ突合ブロック（`if expected_hash is not None and ev.get(...) != expected_hash: continue`）を削除する変異 | attemptのhashは基底messageと一致しなければ照合済みと認めない | `test_reflect_fold.py::test_attempt_hash_mismatch_rejected` | baseline: 基底の`extracted_learning`から計算した期待ハッシュと異なる`correction_message_sha256`を持つattemptのfixtureで、`hash_mismatch_count==1`になり`count`から除外され緑。変異適用後は無検証で採用され`count`に混入し赤 |
| **(r)（巡2 [Must]2・新設）** | `store_write.store_write`: §3.4 の `write_boundary` 拒否分岐（`if boundary is not None: raise StoreWriteError(...)`）を削除する変異（2026-09-01実装裁定） | 専用境界を持つストアへの generic 直接書込みを拒否する | `test_write_barrier.py::test_generic_store_write_rejects_specialized_boundary` | baseline: `store_write("reflect_apply_events.jsonl", {"probe": 1})` が `StoreWriteError` を送出し緑。変異適用後は例外を送出せず書込みが成功して赤 |
| **(s)（巡2 [Must]2・新設）** | `reflect.py` の `--apply` ハンドラ: §8.1手順2の「フェーズ1が`appended`以外ならフェーズ2へ進まない」分岐を削除し、第6版の挙動（常にフェーズ2を実行する）へ戻す変異 | フェーズ1の成功をフェーズ2実行の前提条件にする | `test_reflect_apply_event.py::test_apply_aborts_when_phase1_append_fails` | baseline: フェーズ1が`retry_required`を返すようモックした状態で`--apply`を実行すると、`update_reflect_status`が呼ばれず`reflect_status`は`"pending"`のまま・非0終了で緑。変異適用後は`reflect_status`が`"applied"`になるのにイベントが無い状態が発生し赤 |
| **(t)（巡2 [Must]3・新設）** | `pillar2_metrics._read_stable_snapshot`: stat比較（`if stat_before == stat_after: return ...`）を常に`True`扱いにする変異（=snapshot安定性チェックの無効化） | base/eventのread前後でファイルが変化していないことを確認する | `test_pillar2_metrics.py::test_unstable_snapshot_forces_not_measured` | baseline: `read_corrections_records_with_health`呼出しの間に`corrections.jsonl`のmtimeが変化するようモックしたfixtureで、3回のretryが尽きて`snapshot_stable==False`・`measured==False`になり緑。変異適用後は不安定な読取のまま`measured==True`が返り赤 |
| **(u)（[Should]2の実装ガード・新設）** | `pillar2_metrics.count_applied_reflections`: `if f.base.get("reflect_status") != "applied": continue` の行を削除する変異（§16「見落としやすい1行」） | 未反映の通常バックログ（pending等）は柱2の legacy_unverified に混入しない | `test_pillar2_metrics.py::test_pending_backlog_does_not_pollute_legacy_unverified` | baseline: `reflect_status="pending"`の基底が複数あっても`legacy_unverified_count==0`・`measured==True`（イベントも無反映もない通常状態）で緑。変異適用後は全ての`pending`基底が`legacy_unverified_count`に混入し`measured`が恒常的に`False`になり赤 |
| (j)（M13→巡2で再訂正） | プロダクトコードは壊さず、`extract_pending`（`reflect.py:156-166`）へ event-shaped row（`event_type`は持つが`reflect_status`は持たない dict）を含む records を渡す | — | `test_reflect.py::test_extract_pending_treats_event_shaped_row_as_pending`（**baselineから赤い前提の回帰確認試験として位置づけ直す**。巡2レビュー指摘: 現行`extract_pending`の実装（`r.get("reflect_status", "pending") in ("pending","promoted")`）はこの行を**pending扱いする**ため、「コード変更なしで安全」という第6版の主張は誤りだった） | この試験は「安全であることの証明」ではなく「新ストアの行を`corrections.jsonl`へ絶対に書いてはならない（§2.4「唯一境界」）という運用契約の必要性を実証する」ものとして扱う。テスト自体は`event-shaped row`が`extract_pending`の戻り値に混入することを**期待どおり確認する**（baseline=期待どおりpending化される=`assert`は「混入すること」を検証。プロダクトコード変異は伴わない——運用契約違反のシミュレーションであり、他の陰性試験とは性質が異なることを明記する。第7版はこれを陰性試験の総数に含めない） |
| (k) | プロダクトコードは壊さず、`reflect_apply_events.jsonl` に大量のイベント行（1000件）を追記した状態を作る | `--skip-all` は新ストアを一切読み書きしない | `test_reflect.py::test_skip_all_does_not_touch_event_store`（`--skip-all`実行前後で`reflect_apply_events.jsonl`のmtime・行数・内容が完全に不変であることを確認） | `--skip-all`のコードは`corrections.jsonl`しか触らないため、変異を入れずとも常に緑（構造的に発生し得ないことの直接確認。§0④(k)の「構造的に消滅」という主張を裏付ける回帰試験。プロダクトコード変異は伴わない） |

**委譲側が挙げた回避手段とは種類の違うものを2件、実際にプロダクトコードへ適用して報告する
（実装1巡の完了条件に含める。ここでは列挙のみ）**。**巡2指摘**: 第6版のモック故障注入・
外部ファイル削除は「プロダクトコードへの変異」ではなく、かつ不一致を正常期待していた
（Round0の「緑のまま残ったものが1件でもあれば未完了」の趣旨に反する）。**第7版では
reviewer 自身が提案した実プロダクトコード変異2件に置き換える**（上表 (p)・(r) と同一。
ここでは実装1巡の完了条件としての位置づけを再掲するのみ）:
1. **(p) の変異**（§11 `confirms_attempt_id`解決チェックの削除）を実際に適用し、
   `test_orphan_applied_confirmation_is_degraded` が赤になることを確認・報告する
2. **(r) の変異**（§3.4 `write_boundary`拒否分岐の削除）を実際に適用し、
   `test_generic_store_write_rejects_specialized_boundary` が赤になることを確認・報告する

**探索したが未探索のまま残すクラス**: 境界値（`window_days`ちょうど30日目）／Unicode
正規化差／`reflect_apply_events.jsonl`が空行のみ・末尾改行無し／複数行草稿／
`append_unique_record`が`"retry_required"`を返した場合の呼出側リトライ方針（未定義）／
`decay_grace_days`のカスタム値と実際の`decay_days`が食い違うケースの組合せ網羅／
sibling-worktree slug変異（§13の`_pillar2_project_scope`を`Path(project_root).name`に
置換し、`ea-587`と`evolve-anything`のfixtureで同一PJ反映が除外され赤になることを確認する
——reviewer提案。実装1巡の追加候補として残す）／unknown event type変異（未知typeを
invalid healthへ計上する分岐を`continue`のみに戻す変異——reviewer提案。実装1巡の
追加候補として残す）。

## 16. 残る限界と未実測

- **`store_write_raw` は `write_boundary` を見ない**: この例外口からは検証なしの行を
  追記できる。fold 側の検証で degraded になり無音の誤カウントにはならないが、§3.4 の
  「専用境界だけが書ける」は generic `store_write` 経路に限った言明である
- **原理的に検出不能な唯一の残存ケース（巡2 [Must]2 対応で範囲を縮小）**: 巡2の設計変更
  （§8.3・フェーズ1が失敗したらフェーズ2へ進まない）により、**「`reflect_status` は
  `applied` になったのにイベントが一切無い」という状態は、この設計変更以降に行われた
  `--apply` では構造的に発生しなくなった**（フェーズ1が失敗すればフェーズ2＝
  `update_reflect_status` 自体が呼ばれないため）。残る不可視ケースは**フェーズ1が
  失敗したあと、ユーザーが `--apply` を再試行しない**場合だけであり、これは
  「反映されていないので数えなくて正しい」——柱2の完成条件①（実際に反映されたものと
  食い違わないこと）には抵触しない。**唯一残る真の検出不能ケース**は、フェーズ1の
  追記自体は成功した（＝durable な記録は残る）が、フェーズ2（`update_reflect_status`）が
  何らかの理由で成功も失敗も返さないまま異常終了し、かつ後日それが再試行されるケースで、
  この場合は attempt イベントが残るため §11.1 の2フェーズ照合（reconciliation）が拾える
  ——つまり **append-only 設計の限界として本当に残るのは「フェーズ1の追記操作自体が
  失敗し、かつユーザーがそれに気づかず／気づいても再試行しない」場合のみ**であり、
  これは§8.3の非0終了・エラーメッセージにより**運用上は検出可能な形で表面化する**
  （「柱2の記録に失敗しました。再実行してください」という形で `--apply` 自体が失敗する
  ため、無音のまま進む経路ではない）。「手を抜いた」のではなく、append-only な設計である
  以上、最初の書込み操作自体をユーザーが実行し直すことまでは保証できないという構造的な
  事実として記録する
- **見落としやすい1行（§12実装時の注意）**: `count_applied_reflections` の集計ループで
  `reflect_status != "applied"` の基底（pending/promoted/skipped 等、そもそも反映されて
  いない通常の在庫）を早期 `continue` する行を落とすと、`legacy_unverified_count` が
  「未反映の通常バックログ」で常に埋まり、[Should]2 の `measured=False` 条件が実運用で
  ほぼ常時発火してしまう（§12 コード内のコメント参照）
- **reconciled 時刻は実際の反映日時ではなく attempt 観測時刻（[Should] 巡2指摘）**:
  §11 の2フェーズ照合で確認イベントが欠落した場合、`reflect_applied_at` には
  `attempted_at`（`update_reflect_status` を呼ぶ**直前**の時刻）を代入する（§7・§8.1）。
  これは「ファイルへ実際に反映が確定した時刻」ではなく「反映を試みた時刻」であり、
  対象ファイルの編集と `--apply` 実行が日境界・週境界をまたぐ場合、週次集計の
  帰属先が実態とわずかにずれうる。フィールド名を `reflect_confirmed_at` 等へ改める
  選択肢もあるが、schema・fold・metricsの複数箇所に跨る変更になり本巡の必須修正3件の
  範囲を超えるため、本設計では対応せず既知の限界として記録するにとどめる
  （実装1巡または次回改訂で判断）
- **2フェーズを `--skip` に適用しなかった理由（頭の裁定・2026-09-01・承認）**: `--skip` は
  反映していないので柱2の件数に入らない。**件数に入らないものに、件数の正確性のための
  仕組みを付ける必要はない**（「今回は見送る」ではなく構造上不要という判断）。
  `--skip` が件数へ影響する唯一の経路は §11 fold の `correction_skipped` イベント処理
  だが、fold は `event_type != "correction_apply_attempted"/"correction_applied"` の
  イベントを読み込みループの先頭で捨てる（§11「if etype not in (...): continue」）ため
  `correction_skipped` は fold の集計に一切寄与しない——影響しないことをコードで確認済み。
  将来 `--skip` を集計対象にする仕様変更が入った場合は、その時点で同型の2フェーズ化を
  追加すればよい
- **正当な再反映が1件に潰れる**（§15 (c)-3。前巡の頭の裁定を継承・許容）。過小計上へ
  倒れる方向にしか働かない
- **`correction_message_sha256` は偶然一致を「防止」しない、監査補助にとどまる**
  （前巡の頭の裁定を継承・許容）
- **skill パスの分類は best-effort**（§5）。既知の2配置以外は `"other"` に落ちる
- **decay_grace_days のカスタム値との食い違い**（§11.4「既知の限界」）。安全側（degraded側）
  に倒れることのみ確認済み、実際の発生頻度は未計測
- **retention は `"permanent"` のまま。実装1巡に含めない（頭の裁定・2026-09-01・承認）**。
  **retention 根拠の訂正（Q6 Should）**: 第5版は「イベント数は corrections 件数を
  超えない」としていたが、本設計は1回の `--apply` 成功で最大2行（`correction_apply_attempted`
  + `correction_applied`）を書き、失敗した試行のイベントも累積するため、**イベント行数は
  基底レコード件数の厳密な部分集合ではなく、反映操作の試行回数に比例して増える**
  （誤りだった記述を訂正）。
  **再検討の引き金**: `reflect_apply_events.jsonl` が**10,000行**を超えた時点。
  根拠は絶対規模: `corrections.jsonl` は**247行・約320KB**（再現コマンド
  `wc -l -c ~/.claude/evolve-anything/corrections.jsonl`・取得時刻
  2026-09-01T05:28:12Z）——**10,000行は現規模の約40倍にあたる**。イベント行数と
  基底行数の間に上の訂正どおり厳密な部分集合関係は無いが、いずれも同じ「個人利用
  規模の `--apply`/`--skip` 実行頻度」に律速されるため、この40倍という桁は
  「想定より1桁以上多い異常な増加ペース」を検知する目安として使える。**この規模に
  達するまでは対処が要る問題にならない**（正確な成長モデルの実測はしていないため、
  厳密な数理的根拠ではなく規模の目安であることを明記する）。
  **閾値到達時に何を再検討するか**: `corrections.jsonl` が既に採用している
  `DEFAULT_DECAY_DAYS`（90日）decay 方式を踏襲するか、`correction_apply_attempted` の
  うち対応する `correction_applied` 確認イベントが既に来ている行（§11 の
  `reconciled=False` かつ確認済み）は監査目的の価値が下がるため優先的に compaction
  するか、を実装時に判断する（本設計では決定しない・次回改訂の入口条件として記録する）
- **`append_unique_record` が `"retry_required"` を返した場合の呼出側の扱いは未定義**
- **`append_unique_record` は追記ごとに既存全行を読むため、連続追記の累積 O(N²) 成長は未計測**
- **§12 の集計関数のパフォーマンスは未計測**
- **本設計のレビュー巡数は新規系列の総上限2巡の設計側2巡目（最終）を消費する**

## 17. 人間の判断が要る点

| # | 疑問 | 状態 |
|---|---|---|
| 1 | `update_reflect_status` の commit protocol 全面設計（(f)(g)の根本解消）を `#595` に委ねてよいか | 前巡で承認済み。継承 |
| 2 | 欠陥3（照合の紐付け強度）の残存リスクを許容するか | 前巡で承認済み・許容。継承 |
| 3 | 正当な再反映が1件に潰れる既知の限界を許容するか | 前巡で承認済み・許容。継承 |
| 4 | CLI `correction_id`/ordinal 明示指定オプションを実装1巡に含めるか | 前巡で承認済み・含めない。継承 |
| 5 | 新ストアの basename | 裁定済み（2026-09-01）。`reflect_apply_events.jsonl`。継承 |
| 6 | retention を `permanent` としたこと | 裁定済み（2026-09-01）。実装1巡に含めない。継承（根拠は§16で訂正） |
| 7 | `--skip` を2フェーズ化しない判断でよいか | **裁定済み（2026-09-01）**。承認。根拠: 反映していないので柱2の件数に入らない。件数に入らないものに件数正確化の仕組みは要らない（構造上不要）。`correction_skipped` がfoldの集計に一切寄与しないことをコードで確認済み（§16） |
| 8 | `reflect_apply_events.jsonl` の再検討閾値を絶対件数10,000行としたことでよいか | **裁定済み（2026-09-01）**。承認。根拠は`corrections.jsonl`の実測規模（247行・約320KB・2026-09-01T05:28:12Z実測）の約40倍。閾値到達時の再検討内容（decay方式踏襲 or reconciled済み行のcompaction）も§16に明記（§16） |

**以下は設計レビュー打ち切り後の実装時判断（設計レビュー対象外・実装者への申し送り）**:

| # | 論点 | 状態 |
|---|---|---|
| 9 | `"other"` が「対象外」と「分類不能」を兼ねる問題（巡2トップリスト[Must]5）。`skill_origin.classify_skill_origin`（`scripts/lib/skill_origin.py:160-`）を再利用してplugin skill等の分類を広げる余地がある | **未対応**（§1.2「対応しなかった項目」参照）。実装1巡または次回改訂で判断。§5の分類ロジックを再設計する規模の変更のため、着手前に軽く確認を取ることを推奨 |
| 10 | `_pillar2_project_scope`（§13）が `pj_slug.PJ_SLUG_ALIASES`/`pj_slug_aliases_for` という既存の別名解決SoTをまだ使っていない（旧プロジェクト名からのrename事例を拾えない） | **未対応**。実装1巡で`pj_slug_aliases_for(resolve_pj_slug(project_root))`の戻り値集合との一致も試すことを検討する（§13に記載済み） |
