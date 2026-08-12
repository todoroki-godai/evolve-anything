# ADR-053: #402 PR-2「1コマンド revert」— lock protocol / effective view / apply 手順

- Status: Accepted（設計確定＝codex 4巡 + tacchi 差分レビュー反映済み / 実装は `feat/402-revert-cli` で段階1〜3 進行中）
- Date: 2026-08-12
- Issue: #402（epic: 採用パッチの revert）, #420（PR-1: 記録拡張・main `4364ae52` でマージ済み）
- Related: ADR-049（write barrier — 新ストア書込ゲート）, ADR-031（PJ スコープ slug）,
  ADR-051（result-dependent capture / drain migration）,
  `scripts/lib/optimize_history_store.py`, `scripts/lib/rl_common/file_lock.py`,
  `scripts/lib/fitness/fitness_evolution.py`, `scripts/lib/audit/outcome_promotion_readiness.py`,
  `scripts/lib/results_board.py`, `bin/evolve-revert`（段階4 で追加予定）,
  pitfall: `pitfall_dryrun_stateful_store_write` `pitfall_large_json_stdout_truncation`
  `pitfall_content_identity_with_run_id` `pitfall_pj_rename_legacy_slug_orphan`
- 上位の正典: `design_402_v6.md`（決定1/2/4/5/8 は確定済み・本書は決定3 / 決定6 / 決定7 を実装レベルまで降ろしたもの）

**復元来歴**: 本 ADR の本文は、設計作業ファイル `design_402_pr2_v2.md`（scratchpad 上の作業版）を
PC 強制終了で `/private/tmp` ごと失った後、セッション transcript
（`6dd023d5-8711-40eb-8165-5a5e1d7b2b50.jsonl`）に残っていた Write / Edit の tool_use を
時系列に再生して復元したもの。復元後の行番号は transcript 中の最終 grep 実測
（`## §2` = 300 行目 / `## §3` = 406 行目）と一致する。**本文は要約・リライトせず原文のまま**
（設計の細部がそのまま実装の契約になっているため）。以下、元文書の本文。

---

# #402 PR-2「1コマンド revert」設計案 v2

PR-1（記録拡張・#420・main `4364ae52`）はマージ済み。本 PR-2 で **実際に戻せるようにする**。

上位の正典は `design_402_v6.md`（決定1/2/4/5/8 は確定済み・再検討しない）。本文書は決定3 / 決定6 / 決定7 を実装レベルまで降ろし、**PR-1 が残した前提（dry-run の history lock）** を解く。

## v2 で閉じた指摘（v1 への codex / tacchi 並行レビュー）

両者が**独立に同じ [Must]** を出した:

| # | 指摘 | 出所 | 反映先 |
|---|---|---|---|
| **M1** | **§0 の「窓が閉じる」証明が TOCTOU で不成立**。「sidecar 不在を観測した時点では復元も追記も起きていない」は観測**時点**の話でしかなく、その後 disk と generation を読むまでの間に revert が割り込めば「復元後 disk × 追記前 generation」＝決定8 が排除したい中間状態そのものを読む | codex + tacchi（一致） | §0 全面改訂（seqlock 型 check-after） |
| M2 | 不変条件が **sidecar の単調性**（削除されない）に依存しているのに明文化されていない。tmp cleaner / 手動 / 別ツールが `.lock` を消せば「revert 済みなのに sidecar 不在」になる。lock 保持中の unlink→同名再作成では**旧 inode と新 inode に別々の lock が成立**する | codex + tacchi | §0（単調性契約 + 異常検出 + 「不在なら 0」の短絡をやめる） |
| M3 | `flock` は POSIX 標準でなく BSD 拡張。NFS では exclusive lock に書込 open を要する実装があり、SMB でも意味論が変わる。対応環境の境界が未記述 | codex | §0（対応環境 + `ENOTSUP`/`ENOLCK` は安全側に失敗） |
| M4 | **reader 移行の規模を過小評価**。`fitness_evolution.load_history()` は `optimize_history_store` の wrapper では**なく**、指定 `history_file` を直接 JSONL 読みする別実装。同じ関数が writer の raw dedup と calibration の業務読取の**両方**に使われており、import 置換では分離できない | codex | §1・§6 |
| M5 | **atomic replace の副作用契約が未定義**。所有者 / ACL / xattr / file flags / hardlink は replace で保持されない。hardlink では対象 pathname だけが新 inode になり他リンクは after のまま残る | codex | §2 |
| S1 | §1 の「reader は壊れにくい」は楽観的。`aggregate_runs.py:96-105` の `history[-10:]` は raw の revert event を直近10件に**含めて** `pending` 表示し、**本物の decision を押し出す**。比率は壊れなくても score trend が静かに汚染される | codex | §1 |
| S2 | 契約テストの向きが逆。「業務 reader が raw を呼ばない」の列挙式は**新規 reader が列挙外で素通り**する。**raw を呼んでよい閉じた許可リスト**を固定し、それ以外を一律 fail にすべき。判定は bare 名でなく **import 元**で（`trigger_engine/session_corrections.py:53` は同名別物の `fitness_evolution.load_history()` を呼ぶので名前マッチだと FP） | tacchi + codex | §5 |
| S3 | conflict 時の「before 本文の提示」は global 最大 180 KB を stdout に吐くと切断 pitfall の再演。一時ファイルへ書き出してパス提示 + diff 要約に。**生 base64 は見せない** | tacchi | §2 |
| S4 | board 導線がフィールド運搬で終わっており「次に何をするか」が無い。PR-1 の敗因（導線ゼロ）の再演。`revert_available=true` の行に**実行コマンドそのもの**を印字するまでを完了条件に | tacchi | §3 |
| S5 | `outcome_promotion_readiness.py:273-280` は任意 root を全 slug glob する独自 API。slug 単位・union read 前提の `load_effective_history(slug)` に 1〜2 行で置換できるとは限らない。**API 形を実装前に確定**せよ | codex | §1・§5 |
| S6 | `load_raw_history = load_history` の単なる別名は避け、**正準名を `load_raw_history()`** にして旧名を後方互換 wrapper にする | codex | §5 |
| S7 | `== before_sha` は「利用者や別ツールが手動で before に戻した」場合にも起きる。状態から中断原因は識別できないので**意味論を明記**せよ | codex | §2 |
| S8 | 「中断状態は1種類」は通常のプロセスクラッシュ限定でしか成立しない。電源断まで含めるなら temp / directory / history append の `fsync` 順序を固定する必要がある | codex | §2（耐障害範囲を明記） |
| N1 | revert された accept は effective view から消える＝accept でも reject でもないので `fleet/propose.py` の再提示抑制に掛からず**同じパッチが再提案されうる**。「戻したのにまた出た」は驚くので意図であることを明記 | tacchi | §8 |
| N2 | revert イベントを weak_signal 等の学習系に流さないこと（強い negative シグナルだが #379 凍結中。黙って流す実装を作らない） | tacchi | §8 |

数字の訂正: v1 は「24 ファイルが `optimize_history_store` を import」と書いたが、これは docstring 中の言及を含む `grep -l` の値。tacchi 実測では **import 実体は 23（テスト込み）・非テスト 9**。リネーム回避の論拠自体は変わらない。

## v2 round2 で閉じた指摘（v2 初版への codex / tacchi 再レビュー）

codex round2 は「seqlock の中核は閉じた」と判定した上で新規 [Must] 4件。tacchi は反映 7/7 完了を確認した上で §0.3 に1点。

| # | 指摘 | 出所 | 反映先 |
|---|---|---|---|
| **R1** | **`fold_effective` の適用単位が誤り**。`load_history(slug)` は canonical と legacy を**同一 slug で union read** する一方 revert writer は canonical に追記するため、accept が legacy 側・revert が canonical 側に分かれうる。ファイル単位 fold では両者が出会わず revert 済み accept が anchor に残る | codex | §1（**同一 slug の cross-dir を集約してから fold 1回**・別 slug は畳まない） |
| **R2** | **conflict 時の一時ファイル出力が dry-run 契約と矛盾**。既定 dry-run なのにファイルを書く設計になっていた | codex | §2（既定は **diff 要約のみ**。全文は明示コマンド `--dump-before <path>` に分離） |
| **R3** | **pending marker の dry-run 書込をゼロ書込扱いにしていた**（上位契約の誤変更）。marker の dry-run 書込は #505→#513 を受けた**意図された例外** | codex | §0.5 テスト3・§2 末尾（marker を対象外と明記） |
| **R4** | メタデータ方針が「拒否 or opt-in」の**二択のまま**で実装判断が残っている | codex | §2 手順4（検出方法・属性ごとの方針・flag・検出失敗時を**表で1つに固定**） |
| R5 | リトライ上限超過時の emit 契約が未定義 | codex | §0.2 末尾（公開しない・既存を壊さない・**公開前に確定を完了する順序**） |
| R6 | AST allowlist は**ファイル単位でなく関数 / callsite 単位**にすべき（`fitness_evolution.py` に raw と業務が混在するため） | codex | §5（粒度・完全修飾名解決・allowlist 消失時も fail・**AST の限界**を明記） |
| R7 | **段階2 の AST gate は段階4 まで green にならない**（移行前の reader が残るため） | codex | §6（段階2 は checker と fixture まで、**gate 有効化は段階4**） |
| R8 | §0.3 の「安全側に失敗」が**行き止まり**。data dir 移送・バックアップ復元で `.lock` が付いてこない良性シナリオが現実にあり、dry-run は daily runner の無人経路なので毎朝黙って失敗し続ける | tacchi | §0.3（**warn + 続行 + 回復手順の併記**へ変更。裁定の記録も併記） |
| R9 | テスト7 の「検出または非対応化」は本文で既に非対応と決めているので二択にしない | codex | §0.5 テスト7 |

## v2 round3 で閉じた指摘（codex 3巡目）

codex round3 は R3 / R5 / R6 / R7 / R9 を**解消**、R1 / R2 / R4 を**部分解消**と判定し、**§0.3 の裁定（tacchi 採用＝warn + 続行）は妥当**と追認した上で新規 [Must] 2件 + [Should] 2件。

| # | 指摘 | 出所 | 反映先 |
|---|---|---|---|
| **R10** | **R1 の cross-dir 修正が PJ rename alias を含んでいない**。`load_history(slug)` は「複数 data dir の同じ sanitized filename」だけを union し alias を適用しないため、旧 slug の accept と現 slug の revert がなお出会わない。§2 手順1 の entry lookup も同じ穴（旧 slug にしかない entry_id を revert できない） | codex | §1（**6段階の集約順序を固定** + 実測表 + worktree basename slug は診断のみ）／§2 手順1（同じ alias 集合で引く） |
| **R11** | **uid/gid 保持判定と hardlink override が未確定**。「元ファイルの uid/gid が実行ユーザーと同じ」では replace 後の所有権保持を保証できない（temp の group は親の setgid に影響される）。また `--allow-metadata-loss` が `st_nlink != 1` の拒否まで解除できるように読める | codex | §2 手順4（**temp と source の `fstat()` 突合**へ変更／**hardlink 拒否は flag でも解除不可**と明記） |
| R12 | conflict 判定から replace までの**対象ファイル TOCTOU**（history lock は対象ファイルを排他しない） | codex | §2 手順4（**replace 直前に6項目を再検証**。残る微小窓は「非協調 writer とは保証外」と設計境界を明記） |
| R13 | `--dump-before` と diff 要約の CLI 契約が実装者判断に残っている | codex | §2 手順3（排他・既存拒否・部分ファイル非残置／diff 要約の必須項目と binary 表示） |

## v2 round4 で閉じた指摘（codex 4巡目 + tacchi 差分レビュー）

codex round4 は R11 を**解消**、R10 / R12 / R13 を**部分解消**と判定して [Must] 2件 + [Should] 2件。tacchi は利用者に見える面で [Must] 1件 + 導線3件。**tacchi の [Must] は私の設計の事実誤認を実測で反証したもの**。

| # | 指摘 | 出所 | 反映先 |
|---|---|---|---|
| **R14** | **xattr の記述が二重に誤り**。`os.listxattr` は **Linux 限定 API で macOS の CPython に存在しない**（実測 `'listxattr' in dir(os)` → `False`）。さらに実 SKILL.md は repo・global とも全件 `com.apple.provenance` が付くので「空でなければ拒否」は**発火率 100%**。この2つが重なり macOS で **revert が一度も素通りしない**設計になっていた | tacchi | §2 手順4（**temp↔source の xattr 差分突合**へ変更。検出は Linux=`os.listxattr` / macOS=`/usr/bin/xattr` subprocess。検出能力が環境に無い場合は ACL と同じ「検査せず明示表示」） |
| **R15** | **`--allow-metadata-loss` の override 範囲が広すぎる**。「上記の拒否を通す」だと replace 直前の drift や検査失敗まで解除でき、**R12 の再検証そのものを迂回する flag** になる | codex | §2 手順4（override 可否を**4分類の表**で固定。可=初回検査で既にあった損失のみ／不可=観測後の変化・検査失敗・hardlink） |
| **R16** | dump 出力の publish が原子的でない。「存在しないことを確認 → `os.replace`」は確認後に作られたファイルを上書きする | codex | §2 手順3（**`os.link` による atomic no-clobber publish**。不変条件3点を明記） |
| R17 | dedup の全順序が未確定。canonical slug が辞書順で旧 alias より後になる将来の rename で旧 slug が勝つ | codex | §1 手順4（**data-dir-major の全順序**を固定） |
| R18 | temp の mode 検証と source fd の保持が未固定 | codex | §2 手順4（項目6 に temp↔source mode 比較を追加・fd 保持を明記） |
| R19 | **conflict / 拒否メッセージに実コマンドの印字が要求されていない**（§3 board には課しているのに非対称）。また S7 の冪等パスという**回復ループが設計内にあるのに導線が繋がっていない** | tacchi | §2 手順3（次アクション3手順の印字・hardlink 拒否の文言・diff の向きラベル） |
| R20 | 理由コードが内部用語のまま。board 表示は日本語なのに `pre_extension` / `lane` では意味が取れない | tacchi | §3（**コード + 日本語1行の2層**） |
| R21 | 「戻したのにまた出た」を**ヘルプ1行**で済ませている。驚いた瞬間にヘルプは読まれない | tacchi | §8 N1（apply 完了メッセージに**予告**を出す＝PR-2 必須／提示側の注記は次 PR 可） |

**§0.3 の裁定に対する codex の追認**（成立条件3点）: ①revert writer は毎回 `file_lock` を通り同じ sidecar を作る ②production は sidecar を削除しない ③読区間中の外部 unlink／再作成は非対応。**過去の削除では壊れない**ため warn + 続行で正しい。あわせて文中に残っていた「安全側に失敗」の記述2箇所（§0.3 末尾・却下案）を裁定と一致させた。

---

## §0 ロックプロトコル【全面改訂】

### 却下: (b) apply 直前の locked re-snapshot

決定4「`revert_generation` は emit 時に導出して pending へスナップショットする（**drain 時に導出し直さない**）」と衝突する。これは「同じ pending / result JSON を再 drain しても ID が変わらない＝冪等」の根拠そのもの（v6 決定4 の表1行目）。apply 直前に取り直すと2回目の drain で generation が変わり `_decision_event_id` が変わる → **#279 の N 重記録が再発**する。

（注: 後述の §0.2 で行う「emit **時**の同一 pending 内での再試行」はこれとは別物。emit が1回の pending 生成を完了させるまでの内部リトライであり、pending 確定後は二度と導出し直さない。codex も「決定4 には反しない」と確認。）

### 採用: (a) 書込ゼロの read-only lock + seqlock 型 check-after

#### §0.1 `read_only_file_lock`

`flock` は**読み取り open でも `LOCK_EX` を取れる**。実測（一時ディレクトリ）:

| 操作 | inode | size | mtime | ctime | 内容 hash |
|---|---|---|---|---|---|
| 読み取り open + `LOCK_EX` の前後 | 不変 | 不変 | 不変 | 不変 | 不変 |

- 現行 `file_lock` が dry-run 純度を破る原因は `lock_path.parent.mkdir(...)` と `open(lock_path, "a")`（**不在なら作成する**）の2つ
- 読み取り open は不在ファイルを作らない（`FileNotFoundError`）

`rl_common/file_lock.py`（ロックの単一ソース）に追加する:

```python
@contextmanager
def read_only_file_lock(lock_path: Path) -> Iterator[bool]:
    """既存 sidecar を**書込ゼロ**で排他取得する（#402 PR-2）。

    取得できたら True、sidecar 不在なら False を yield する（取得しない）。
    `file_lock` と違い parent の mkdir も append open もしないため、dry-run
    純度契約（1バイトも書かない）を破らない。

    `flock` が ENOTSUP / ENOLCK 等で失敗した場合は**例外を送出**する
    （unlocked read へ暗黙フォールバックしない・M3）。
    """
```

既存の `file_lock` / `try_file_lock` は**一切変更しない**（後方互換）。

#### §0.2 sidecar 不在時のプロトコル（M1 の修正）

**v1 の証明は誤りだった。** 「不在を観測 ⟹ 安全」は観測時点しか保証しない。正しくは **check-after**（seqlock 型）:

```
1. read_only_file_lock を試みる
   → 取得できた（True）: そのまま lock 下で disk と generation を読む。完了
   → 不在（False）: 以下へ
2. lock 無しで disk 内容と history（generation）を読む   ← 暫定 snapshot
3. **読了後に sidecar の不在を再確認する**
   → まだ不在: 単調性（§0.3）より、読区間全体にわたり revert は flock を
      取得していない＝復元も追記も起きていない。よって暫定 snapshot は
      pre-revert の一貫した世界。採用する
   → 出現していた: 読区間中に revert が動いた可能性がある。**暫定 snapshot を
      破棄し、1 へ戻って locked 経路で読み直す**（リトライ上限を設け、超えたら
      emit 全体を失敗させる。黙って古い snapshot を採らない）
```

**リトライ上限超過時の emit 契約（v2 round2 codex [Should]）**: 単調性が守られる正常系では sidecar の「不在→存在」は一度しか起きないので、通常は1回の再試行で locked 経路に移る。何度も再試行が必要な状態は単調性違反・path の不安定化・観測エラーのいずれかなので、上限超過時は:

- **non-zero / 例外で emit 全体を失敗させる**
- 当該 emit の**新しい pending を queue / marker / result のいずれにも公開しない**
- **既存 pending は変更も削除もしない**
- 呼び出し元が**警告だけで成功扱いにしない**
- **marker を先に公開してから失敗する順序を作らない**（公開前にスナップショット確定を完了する。「最大 N 回」の値より**この順序**の方が重要）

このリトライは **emit 時の同一 pending 生成内で完結**するため、却下した (b)（drain / apply 時の再導出）とは別物であり決定4 に反しない。

#### §0.3 sidecar 単調性の契約（M2）

上の証明は「**sidecar は一度作られたら消えない**」に依存する。これを明文化し機械で守る:

- **契約**: production コードは history の `.lock` sidecar を `unlink` / `rmtree` しない。実測で現在ゼロ（tacchi 確認）。**契約テストで固定**する（`.lock` を消す production コードが存在しないことを静的検査）
- **`generation=0` への短絡をしない**: §0.2 の手順2 では **history を実際に読み**、その実 generation を使う（codex: 「『不在なら generation=0』と短絡せず、history は実際に読むべき」）
- **異常検出は warn + 続行**（fail させない）: 「history に revert イベントが存在するのに sidecar が不在」は単調性契約違反の**痕跡**なので検出して surface するが、**処理は続行する**。理由:
  - check-after の健全性は**現在の読区間に閉じている** — 「読区間中に revert が flock を取ったなら sidecar は存在する」（revert は必ず sidecar を作る `file_lock` を通る）。**過去に外部削除があったかどうかは、現在の読み取りの正しさに影響しない**。壊れるのは「読区間中に**2度目の**外部削除が重なる」場合だけで、それは上で非対応と宣言した領域
  - fail に倒すと**良性シナリオを無人経路で殺す**: data dir の移送・バックアップ復元では jsonl が運ばれても `.lock` sidecar は付いてこない（当リポジトリには legacy `rl-anything` → canonical 移行や PJ rename の実績があり机上の話ではない）。dry-run は daily runner / `fleet propose` の**無人経路**でもあるため、該当 PJ は毎朝黙って失敗し続け、しかも誰も直し方を知らないという行き止まりになる
  - surface 先: emit の返り値 meta + dry-run 出力の warning 1行。**回復手順も併記する**（sidecar は正規の locked 経路が次回書込時に再作成するので、回復は実質「一度 apply 経路を通す」だけ。それを書かずに警告だけ残さない）
  - 新しい observability section は作らない（#379 Step2 に非抵触）

  **レビュアー間で見解が割れた点（裁定の記録）**: codex は「外部削除時は**安全側に失敗**する契約が必要」、tacchi は「fail は**行き止まり**（良性シナリオを無人経路で毎朝殺す）。warn + 続行で正しさは失わない」と主張した。**tacchi を採った**。決め手は、check-after の健全性が「読区間中に revert が flock を取ったなら sidecar は存在する」という**現在の読区間に閉じた性質**に依存しており、**過去の外部削除は現在の読み取りの正しさに影響しない**こと。codex の本来の懸念は「`不在なら generation=0` と短絡するな」であり、それは history 実読で満たしている。異常を**検出して surface する**点は両者の要求を共に満たす。
- **lock 保持中の unlink→同名再作成**は旧 inode と新 inode に別々の lock を成立させる。これは検出も防止も困難なので、**非対応として明記**する（上の contract テストで production からの削除を封じる。外部要因による削除は**警告して続行**し、読区間中の外部 unlink／再作成は保証外とする）
- **保証の境界（codex round3 が追認）**: 成立条件は ①revert writer は毎回 `file_lock` を通り同じ sidecar を作る ②production は sidecar を削除しない ③読区間中の外部 unlink／再作成は非対応、の3点。**過去の削除では壊れない**（読区間中に writer が始まれば sidecar が出現し check-after が snapshot を破棄する）。壊れるのは「writer が sidecar を作った後、読了後チェックより前に外部プロセスが再び削除する」場合のみで、これは③の非対応領域

#### §0.4 対応環境の境界（M3）

- **対応**: macOS のローカル filesystem / 通常の Linux filesystem
- **非対応**: NFS / SMB 等のネットワーク FS（exclusive lock に書込 open を要する実装があり、書込ゼロが成立しない）
- `flock` が `ENOTSUP` / `ENOLCK` 等で失敗したら **unlocked read へフォールバックせず**、dry-run を安全側に失敗させる
- `flock` は **advisory lock** であり非協調 writer は排除しない（この前提を docstring に明記）

#### §0.5 契約テスト（この節の完了条件・M4 で 5→10 本）

1. `read_only_file_lock` が既存 sidecar の inode / size / mtime / ctime / 内容 hash を変えない
2. sidecar 不在時に `False` を yield し、**ファイルもディレクトリも作らない**
3. dry-run emit の E2E で **対象ファイル・history lock sidecar・temp・history** に書込ゼロ（PR-1 の `dry_run_no_write_e2e` を拡張）。**pending marker は対象外**（意図された dry-run 書込・#505→#513）。marker-less の result 同梱経路だけを見るテストなら、その条件をテスト名と docstring に明記する
4. revert が sidecar を保持中は dry-run emit が generation を読めない（**ロック保持中に相手が進めないことの確認 + daemon thread で hang→fail 変換**）
5. **first-writer race**: sidecar 不在判定の直後に writer が作成・保持した場合、check-after が snapshot を破棄して locked 経路で読み直す
6. **過去に revert 済みで sidecar だけ削除された状態**: `generation=0` に短絡せず **history の実 generation で続行**し、警告 + 回復手順を meta / 出力に surface する（**fail させない**。data dir 移送・バックアップ復元という良性シナリオで起き、dry-run は daily runner の無人経路でもあるため）
7. **unlink→再作成は対応保証外**であることを契約として固定する — production コードに sidecar 削除経路が存在しないことの静的検査（本文で非対応と決めているので、テスト名も「検出 or 非対応化」の二択にしない）
8. `flock` が `ENOTSUP` / `ENOLCK` で失敗した場合に **unlocked read へフォールバックしない**
9. lock 待機中の例外・割込みでも fd と lock が確実に解放される
10. **revert writer が必ず sidecar を作る経路（通常の `file_lock`）を通る**（history へ書く前に sidecar が存在することを assert）

---

## §1 revert イベントと effective view（v6 決定3）

### 現状（実測・`4364ae52`）

`optimize_history_store.py` の公開 API は `resolve_slug` / `history_path` / `load_history(slug)` / `normalize_entry_timestamp` / `append_entry(entry, slug)`。**履歴 read は `load_history` 1本**。

`human_accepted` を読む production 箇所:

| # | ファイル:行 | 判定式 | 分類 |
|---|---|---|---|
| 1 | `results_board.py:84` | `entry.get("human_accepted")` が bool か | 業務（戦果ボード） |
| 2 | `audit/outcome_promotion_readiness.py:280` | `is True` | 業務。**`load_history` を経由せず `base / "optimize_history"` を全 slug glob して jsonl 直読み**（S5） |
| 3 | `fleet/propose.py:194` | `is False` | 業務（reject 再提示抑制） |
| 4 | `fleet/queue_verify.py:96` | `is not True` | 業務（verify pending） |
| 5 | `skills/audit/scripts/aggregate_runs.py:71,72,98` | `is True` / `is False` / truthy | 業務（accept/reject 集計）**※ S1 の汚染箇所** |
| 6 | `skills/evolve-fitness/scripts/fitness_evolution.py:285,340,500,562` | `is not None` / truthy | 業務（fitness calibration・同一モジュール内4箇所） |
| 7 | `audit/sections_meta.py:212` | score と human_accepted の集計 | 業務（`fe.load_history` 経由の間接 consumer） |
| — | `legacy_accept_migration.py:51` | `is not True` | **migration・診断（raw のまま）** |

`load_history` を呼ぶ他の箇所: `evolve_decisions/_emit.py:93`（PR-1 の generation 読み）、`fitness_evolution.py:236`（**writer の raw dedup**: `any(rec.get("id") == entry_id ...)`）。この2つは**性質上 raw が正しい**。

### revert イベント追加の影響（S1 で v1 の評価を訂正）

revert イベントは `human_accepted` を持たない。判定式は `is True` / `is False` / `is not None` が大半なので、**accept/reject に誤カウントされる事故は起きない**（この評価は codex も追認）。

**ただし v1 の「reader は壊れにくい」は楽観的だった。** `aggregate_runs.py:96-105` の `history[-10:]` は raw の revert イベントを直近10件に**含めて** `pending` として表示し、**本物の decision を押し出す**。比率は壊れなくても score trend が静かに汚染される。**effective view への移行は必須**。

### 出力契約（v6 決定3・確定済み）

- `reverted_entry_id` で畳まれた accept entry は `load_effective_history()` の出力から**除外**（フラグを立てない）
- revert イベント自体も出力に含めない（判断母集団ではない）
- revert の事実が必要な reader は `load_revert_events()` を使う

### revert イベントの必須フィールド

| フィールド | 意味 |
|---|---|
| `event_type` | 固定値 `"revert"` |
| `reverted_entry_id` | 畳む対象の accept entry ID |
| `revert_event_id` | deterministic（決定6 の冪等再実行の判定キー） |
| `revert_generation` | この revert 実行後の世代 |
| `scope` / `repo_id` / `relative_path` | **必須**。PR-1 の `_revert_generation_for_target` がこの3つで対象一致を判定している |
| `timestamp` / `skill_name` | 表示・診断用 |

### `fitness_evolution` の raw/effective 分離（M4）

`fitness_evolution.load_history()` は `optimize_history_store.load_history()` の wrapper ではなく、指定 `history_file` を直接 JSONL 読みする**別実装**。同じ関数が

- `record_evolve_diff_decision()` の **raw ID dedup**（`:236`）— raw が正しい
- **calibration の業務読取**（`:285,340,500,562`）— effective が必要

の両方から呼ばれているため、**単純な import 置換では分離できない**。モジュール内で raw 経路と effective 経路を明示的に分ける。間接 consumer の `trigger_engine/session_corrections.py:53` と `audit/sections_meta.py:231` も契約対象に含める。

### `outcome_promotion_readiness` の API（S5）

任意 root を全 slug glob する独自実装なので `load_effective_history(slug)` への 1〜2 行置換にはならない。**実装前に API 形を確定する**:

**採る形**: `fold_effective(records) -> List[Dict]` という**純粋関数**を `optimize_history_store` に置き、`load_effective_history(slug)` はその薄いラッパーにする。これで「任意 root を受ける effective reader」を新設せずに済み、fold ロジックは1箇所に留まる。

**fold の単位（v2 round2 codex [Must]・重要）**: `load_history(slug)` は canonical と legacy / plugins-data を**同一 slug について union read** する。一方 revert writer は **canonical history に追記**する。したがって次が成立しうる:

- accept entry: **legacy 側**の `optimize_history/<slug>.jsonl`
- revert イベント: **canonical 側**の `optimize_history/<slug>.jsonl`

**ファイル単位で fold すると両者が出会わず、revert 済み accept が readiness の anchor に残る。**

**さらに PJ rename の alias もまたぐ（v2 round3 codex [Must]）**: 現行 `load_history(slug)` は「複数 data dir の**同じ sanitized filename**」だけを union し、**rename alias は適用しない**（`optimize_history_store.py:85`）。alias の SoT は `pj_slug.canonical_pj_slug()` / `pj_slug_aliases_for()` にあり、**リポジトリ内の約15箇所の reader は既に alias を適用している**（`capture_rate` / `store_read_union` / `session_store` / `discover/errors` / `audit/usage` / `fleet/queue_materials` / `telemetry_query` 等）。`load_history` だけが慣習から外れている。

**集約順序を以下に固定する**:

1. 現 project slug を `canonical_pj_slug()` で canonical 化する
2. `pj_slug_aliases_for(canonical_slug)` で同値 slug 集合を得る
3. 各 alias × 全 data-dir のレコードを集約する
4. 同一 `id` の dedup 優先順位は**全順序として固定する**（v2 round4 codex [Should]。「canonical dir 優先 → alias は `sorted()`」だけでは、**canonical slug が辞書順で旧 alias より後になる将来の rename で旧 slug が勝つ**。現行の `evolve-anything` / `rl-anything` は偶然 canonical が先なだけ）:

   ```
   data-dir（major）: canonical dir → 残りは iter_read_data_dirs の順
   slug（minor）    : canonical_slug → sorted(aliases - {canonical_slug})
   ```

   **data-dir-major** にするのは既存 `load_history` の「候補列 canonical 先頭・先勝ち」契約を保つため。先に出た `id` が勝つ（先勝ち）
5. その集合へ `fold_effective()` を **1回だけ**適用する
6. **異なる canonical slug 間では畳まない**

**実データの現状（実測・2026-08-12）** — この [Must] は**潜在的な契約落ちで、今日の実害はゼロ**:

| 確認項目 | 実測結果 |
|---|---|
| `optimize_history/rl-anything.jsonl` | 全 data dir・backup 含め**存在しない** |
| `evolve_decisions/rl-anything.jsonl` | canonical に存在するが **0 バイト** |
| `iter_read_data_dirs` の候補 | canonical + `plugins/data/*` 2件（`rl-anything.backup-*` は**候補に入らない**＝backup 混入なし） |
| `PJ_SLUG_ALIASES` | `{"rl-anything": "evolve-anything"}` の1件のみ |

よって「今すぐ壊れているから直す」ではなく、**新設 reader だけが repo の慣習から外れる状態を作らないために直す**。段階2 の `fold_effective` / `load_effective_history` / `load_revert_events` の**新設時点**で alias 対応を入れる（後付けにしない）。

**worktree basename 由来 slug の扱い**: 過去に worktree ディレクトリ名そのもので作られた履歴ファイル（`detect_worktree_name_slugs` が検出する `agent-*` / `worktree-*` 系）は元 project を復元できない。これらは **alias 対象に含めず、診断のみ・revert 非対応**とする（誤った PJ の accept を畳むリスクの方が大きい）。

### `optimize_history_store.load_history` の実 callsite（実測・段階2 完了時点・段階4 の作業台帳）

**段階2 の worker 報告にあった「実 callsite は `_emit.py` の1箇所のみ」は誤り**（頭が `raw_history_gate` を実ツリーに dogfood して確認。worker は設計 §5 が名指しした `fitness_evolution` / `legacy_accept_migration` の2件だけを検証し「1箇所のみ」に過度一般化した）。実測は **5箇所**:

| # | callsite（`<path>:<qualname>`） | 段階4 の処遇 |
|---|---|---|
| 1 | `scripts/lib/evolve_decisions/_emit.py:_read_disk_and_history` | **raw のまま**（generation 読み）→ allowlist に載せる**唯一の**entry |
| 2 | `scripts/lib/results_board.py:build_results_board` | effective へ移行 |
| 3 | `scripts/lib/fleet/propose.py:filter_previously_rejected_candidates` | effective へ移行 |
| 4 | `scripts/lib/fleet/queue_verify.py:_load_optimize_history_with_aliases` | effective へ移行（**下記の落とし穴あり**） |
| 5 | `skills/audit/scripts/aggregate_runs.py:load_history` | effective へ移行 |

`fitness_evolution.py` は `optimize_history_store` を呼ばず**同名の自前実装**を持つ（§1 の M4 と一致）。`legacy_accept_migration.py` は `history_file.read_text()` の直読み。よって設計 §5 が挙げていた「`fitness_evolution.record_evolve_diff_decision` の dedup / migration / 診断」は **allowlist の対象にならない**（AST gate の射程外＝§5 が認めた「AST の限界」側で管理する）。**allowlist は #1 の1件のみ**になる。

**段階4 の落とし穴（頭が発見・実測）**: `queue_verify._load_optimize_history_with_aliases` は**既に自前の alias 集約**を持つ（`fleet.queue._equivalence_slugs` + 自前 dedup）。`load_effective_history` へ移行するときは**ローカルの alias ループを消す**こと（残すと二重集約）。

- **slug 集合は等価**（実測: `evolve-anything` / `rl-anything` / 非 rename PJ の3ケースで `_equivalence_slugs` と `pj_slug_aliases_for(canonical)` が完全一致）→ **レコード欠落は起きない**
- **dedup 順序だけが変わる**: 現行は **slug-major・`sorted()`**（canonical 先頭でない）、新実装は **data-dir-major・canonical 先頭**。同一 `id` が別 dir × 別 slug に重複したときの勝者が入れ替わる。これは codex R4 [Should] で意図的に固定した順序なので**修正が正**だが、移行 commit のテストで**明示的に確認**する

---

## §2 apply 手順（v6 決定6 + M5 / S3 / S7 / S8）

1. entry を引く。**§1 と同じ alias 集合**（`pj_slug_aliases_for(canonical_pj_slug(slug))` × 全 data-dir）で引く（v2 round3 codex [Must]）。現 project slug だけに限定すると**旧 slug にしか存在しない entry_id を指定した revert が「見つからない」で失敗する**。union read で同一 ID が複数出たら §1 手順4 と同じ優先順位（canonical 優先）で採り、不整合は明示する
2. 対象パスを scope 別契約で解決:
   - PR-1 は scope / `relative_path` を**字句的絶対パス**（symlink 非追従）で記録済み。apply 側は `repo root + relative_path` / `global root + relative_path` で解決し直す
   - **最終要素の `lstat` regular-file 判定**（symlink 自体を replace するのを防ぐ）と、**解決後の実体が root 配下**であること（親ディレクトリ symlink 経由の脱出を防ぐ）を**別々の検査**として実施（N1）
   - **`st_nlink != 1` は conflict として拒否**（M5。hardlink では対象 pathname だけが新 inode になり、他リンクは after 内容のまま残るため）
3. **以下 3〜5 は同一 history lock 内**。現ディスク sha を検証:
   - `== after_sha` → 正常系。復元 → revert イベント append
   - `== before_sha` → **冪等パスだが「何もしない」ではない**。deterministic な `revert_event_id` が履歴に存在すれば完全冪等（何も書かない）、無ければ**イベントのみ追記して復旧**
     - **意味論の明記（S7）**: この観測は「前回の中断（復元済み・イベント欠落）」でも「利用者や別ツールが手動で before 内容へ戻した」でも起きる。**状態から中断原因は識別できない**ので、どちらの場合も revert イベントを追記して**正式な revert とみなす**
   - どちらでもない → **conflict**。黙って上書きしない
     - **既定（dry-run / apply とも）: 現 disk との diff 要約のみを表示する。** 全文も生 base64 も出さない（global は最大 180 KB あり stdout に吐くと巨大出力の切断 pitfall を再演する）
     - **全文が要るときは別コマンド `--dump-before <path>` を明示的に叩く**（v2 round2 codex [Must]）。tacchi の「一時ファイルへ書き出してパス提示」を既定に入れると、**既定 dry-run なのにファイルを書く**ことになり dry-run 純度契約と矛盾する。書込を伴う操作は既定から分離し、ユーザーが明示的に要求したときだけ行う
     - `--dump-before` は「revert を実行せず before 本文を指定パスへ取り出すだけ」の操作（PR-1 の CHANGELOG に書いた decode ワンライナーの CLI 版）。**dry-run の例外を作らない**
     - **`--dump-before` の契約（v2 round3 codex [Should]・実装者判断に残さない）**:
       - `--dump-before` と `--apply` は**排他**（同時指定はエラー）
       - 出力先が**既存なら既定で拒否**する（上書きしない）
       - **publish は atomic no-clobber で行う（v2 round4 codex [Must]）**: 「事前に存在しないことを確認 → `os.replace(temp, dest)`」は**確認後に作られたファイルを上書きする**ので「既存なら拒否」契約を満たさない。同一 filesystem 上で temp を完成させてから **`os.link(temp, dest)`**（既存なら `FileExistsError`）で publish し、成功後に temp を unlink する。固定する不変条件は3つ — ①publish 時点でも既存を上書きしない ②完成前の内容を出力先の名前で公開しない ③失敗時に出力先へ部分ファイルを残さない
       - **出力先が対象ファイル自身と同一パスなら拒否**（tacchi）。skills ディレクトリ内へ dump しようとして対象を壊す事故を防ぐ
     - **conflict 時の diff 要約に必ず含める項目（v2 round3 codex [Should]）**: `before_sha` / 現ディスク sha / それぞれの byte 数 / 変更行数 / **行数上限付きの hunk**。before が decode 不能・binary の場合は hunk を出さず「binary または decode 不能（N bytes）」と表示する
     - **diff の向きのラベル（v2 round4 tacchi）**: この diff は before↔現 disk であり、**採用パッチと採用後の変更の両方**を含む。利用者は「diff＝自分が後から足した変更」と誤読するので、要約の先頭に1行明示する — 「この差分は『戻した場合に失われる内容』です（採用パッチと採用後の変更の両方を含みます）」。after↔disk（純粋な後続変更）は after 本文を保存していないため**出せない**
     - **conflict メッセージは行き止まりにしない（v2 round4 tacchi [Must]・§3 の S4 と対称にする）**: board には実行コマンドを印字させておきながら conflict 出力に同じ要求を課さないのは非対称。conflict 時は次を必ず印字する:

       ```
       次アクション:
         1) bin/evolve-revert <entry_id> --dump-before <path>   # 変更前の全文を取り出して確認
         2) 内容に納得したら、その全文で対象ファイルを置き換える
         3) bin/evolve-revert <entry_id> --apply                # 履歴も整合させて revert を確定
       ```

       手順3 が効くのは、手順3 の `== before_sha` 冪等パス（S7 の意味論）が「手動で before 内容へ戻した場合も正式な revert とみなしてイベントを追記する」と決めているため。**回復ループは設計内に既に存在するのに導線が繋がっていない**状態だったので繋ぐ（繋がなければ S7 は誰にも使われない内部意味論のまま残る）
     - **拒否メッセージにも実コマンドを印字する（同・tacchi）**: メタデータ拒否は `--allow-metadata-loss` 付きの実コマンドを添える。**hardlink だけは flag で解除できない**ので、理由と回復手順を1行で書く:

       ```
       このファイルは hardlink されています（nlink=N）。置換すると他のリンク先と内容が分岐するため
       revert では扱えません（--allow-metadata-loss でも解除不可）。リンクを解消してから再実行してください。
       ```

       「メタデータ損失ではなく整合性破壊」という理由は設計には書いてあるが、**利用者に見えるメッセージに載る保証がどこにも無かった**（設計だけ正直の型）
4. 復元は同一ディレクトリに temp 生成 → 復元後 sha が `before_sha` と一致することを再検証 → **atomic replace**
   - **メタデータ方針（M5 / v2 round2 [Must]・二択を残さず1つに固定する）**:

     | 属性 | 検出方法 | 方針 |
     |---|---|---|
     | mode | `os.lstat().st_mode` | **保持する**（temp へ引き継いでから replace） |
     | uid / gid | **temp と source の `fstat()` を replace 直前に突合**（下記手順） | **一致しなければ replace しない**（`--allow-metadata-loss` で明示的に通す） |
     | xattr | **temp と source の xattr 集合を突合**（下記「xattr の実測訂正」） | **`source − temp` の差集合が空でなければ拒否**（空なら通す） |
     | file flags | `os.lstat().st_flags`（macOS） | **0 以外なら apply を拒否**。`st_flags` を持たない環境では検査をスキップ |
     | hardlink | `os.lstat().st_nlink` | **`!= 1` なら conflict として拒否。この拒否は `--allow-metadata-loss` でも解除できない**（v2 round3 codex [Must]。hardlink はメタデータ損失ではなく**リンク間で内容が分岐する整合性破壊**であり、「失ってよいメタデータ」と同じ flag で通してはならない） |
     | ACL | **標準ライブラリで検出不能** | 検出しない。**「ACL は保持されない・検出もしていない」旨を dry-run に明示表示**する（検出できないものを理由に拒否はしない） |

   - **replace 直前の再検証（v2 round3 codex [Must] + [Should]・順序を固定する）**: history lock は**対象ファイルを排他しない**。手順3 で `after_sha` を確認してから temp 作成・メタデータ検査を行う間に、非協調 writer が対象を書き換えると **`os.replace()` がその変更を黙って消す**。よって `os.replace()` の直前に以下を**すべて**再検証し、1つでも食い違えば replace せず conflict として中止する:

     1. `lstat` identity（`st_dev` / `st_ino`）が手順2 の観測と同一
     2. regular file である
     3. `st_nlink == 1`
     4. 現ディスク内容の sha が依然 `after_sha`
     5. temp と source の `fstat().st_uid` / `st_gid` が一致（temp の group は親ディレクトリの setgid 等の影響を受けるため、「元ファイルの uid/gid が実行ユーザーと同じ」だけでは replace 後の所有権保持を保証できない）
     6. source の mode / xattr / flags が手順2 の検査結果と一致し、**かつ temp と source の mode も一致**（`stat.S_IMODE(fstat(temp_fd).st_mode) == stat.S_IMODE(fstat(source_fd).st_mode)`。「source が手順2 から変わっていない」だけでは temp 側への mode 引き継ぎが実際に効いたことを保証しないため・v2 round4 codex [Should]）

     **fd の扱い（v2 round4 codex [Should]）**: source は**検査中ずっと fd を保持**し、`fstat(source_fd)` の `st_dev` / `st_ino` が手順2 の identity と一致することを確認したうえで、uid/gid・mode の比較にも**同じ fd を使う**（パス経由で stat し直すと、その間の差し替えを検出できない）

     この再検証と `os.replace()` の間にはなお微小な窓が残るが、**非協調 writer との同時更新は保証外**とする（advisory lock の設計境界。§0.4 と同じ扱い）

   - **`--allow-metadata-loss` の override 境界（v2 round4 codex [Must]・「上記の拒否を通す」では広すぎる）**

     | 分類 | 対象 | override |
     |---|---|---|
     | 利用者が受容できるメタデータ損失 | 手順2 の**初回検査で既に存在していた**所有者不一致・ユーザー由来 xattr・file flags 非0 | **可**（`--allow-metadata-loss`） |
     | 観測後の変化 | replace 直前の再検証で検出した identity / 内容 / メタデータの drift | **不可** |
     | 検査の失敗 | 検出手段はあるが実行が失敗した（権限不足・subprocess 異常終了） | **不可** |
     | 整合性破壊 | `st_nlink != 1`（hardlink） | **不可** |

     この区別が無いと、`--allow-metadata-loss` が **R12 の再検証そのものを迂回する flag** になる（再検証中に別プロセスが chmod / xattr 変更しても上書きできてしまう）。「利用者が事前に見て受け入れた損失」と「見ていない間に起きた変化」は別物として扱う。
     - dry-run は「何が変わる予定か」を必ず表示する（保持: mode / 失う可能性: 所有者・xattr・flags・ACL）

   - **xattr の実測訂正（v2 round4 tacchi [Must]・設計の事実誤認を修正）**

     v2 初版の「`os.listxattr()`（macOS / Linux とも標準ライブラリで検出可）」+「空でなければ拒否」は**二重に誤っていた**。実測（2026-08-12・Python 3.14.6 / darwin）:

     | 確認項目 | 実測結果 |
     |---|---|
     | `'listxattr' in dir(os)` | **`False`**（`os.listxattr` / `os.getxattr` は **Linux 限定 API**で macOS の CPython に存在しない） |
     | 実 SKILL.md の xattr | repo・global とも**全件** `com.apple.provenance` が付く（macOS が自動付与） |
     | 同ディレクトリに作った temp の xattr | **`com.apple.provenance` が自動付与される**（＝差分突合が成立する） |
     | `/usr/bin/xattr` | 存在する（macOS 側の検出手段） |

     つまり初版のままだと macOS で ①検出 API が無いので検査が必ず失敗し ②仮に検査できても xattr は常に非空なので**全 revert が拒否**され、全員が `--allow-metadata-loss` を常用して flag が「本当に守りたい xattr」を守る力を失う。

     **採る形**: uid/gid と**同じ temp↔source 突合**に揃える（属性ごとに検査の型を変えない）。

     - 検出手段: **Linux は `os.listxattr()`、macOS は `/usr/bin/xattr` subprocess**（CLI であり hot path ではないので subprocess は許容）
     - 判定: **`source の xattr 集合 − temp の xattr 集合` が空なら通す**。差分（＝OS 自動付与ではなくユーザー由来の xattr）があるときだけ拒否する
     - **検出能力そのものが環境に無い場合**（両手段とも使えない）は ACL と同じ扱い＝**検査せず、「xattr は保持されない・検出もしていない」と dry-run に明示表示**して続行する。これは*静的に既知の制約*であり、下の「検査不能は override 不可」とは区別する
     - **検出手段はあるが実行が失敗した場合**（権限不足・subprocess 異常終了）は **fail-closed で拒否**し、`--allow-metadata-loss` でも通さない（下記 override 境界）
5. ロック下から呼ぶ関数は `_locked` 版を使い自己 deadlock を避ける

**耐障害範囲（S8）**: 本 PR が保証するのは**通常のプロセスクラッシュ**まで。電源断は対象外とし、その旨を明記する（temp / directory / history append の `fsync` 順序固定は行わない）。この範囲では中断状態は「復元済み・イベント欠落」の1種類に保たれる。

**dry-run のゼロ書込の範囲（v2 round2 codex [Must]・上位契約の誤変更を訂正）**: `--dry-run` を既定にし、**対象ファイル・history lock sidecar・temp・history へはゼロ書込**。

**ただし pending marker は除く。** marker の dry-run 書込は当リポジトリで**意図された設計**（CLAUDE.md 明記・#505→#513 の事故を受けた仕様）であり、本 PR で変更しない。v2 初版が marker をゼロ書込対象に含めていたのは上位契約の誤変更だったので訂正する。

---

## §3 戦果ボード導線（v6 決定7 + S4）

- withdrawal candidate に `entry_id` / `revert_available` / `revert_unavailable_reason` / `reverted` を構造化結果まで運ぶ（`results_board.py:206-211` で `entry_id` が落ちていることは tacchi が確認済み・実装時の再確認不要）
- revert 済みの除外は `load_effective_history()` 経由、`reverted` 表示は `load_revert_events()` 経由（results_board 個別実装にしない）
- **`revert_available=true` の行には実行コマンドそのものを印字する（S4・§3 の完了条件）**:
  ```
  bin/evolve-revert <entry_id>            # 何が起きるか確認（既定 dry-run）
  bin/evolve-revert <entry_id> --apply    # 実際に戻す
  ```
  既定 dry-run なので**2段案内**にする。これが無いと体験4 は「データはあるがコマンドを推理する」に留まり、PR-1 の敗因（導線ゼロ）を再演する
- `revert_available=false` の理由コード:

**理由コードは2層で持つ（v2 round4 tacchi）**: `pre_extension` / `lane_unsupported` / 「記録拡張」「lane」はいずれも**プラグイン内部の PR 履歴・ADR を知らないと意味が取れない**内部用語。board の表示は日本語（`results_board.py:264` 実測）なので、**コード（機械用）+ 日本語1行（人間用）**を組で持ち、表示には日本語を出す。期待値の3段差（恒久不能／恒久対象外／次回は可能かも）はこの表の肝なのに、コード名だけでは読者に伝わらない。

| 理由コード | 表示文言（人間用） | 期待させてよいか |
|---|---|---|
| `pre_extension` | 「戻す機能の導入前に採用されたため、変更前の内容が残っていません（今後も戻せません）」 | **後から戻せるようにはならない** |
| `lane_unsupported` | 「この種類の採用（rules / hooks 等）は戻す機能の対象外です（skill の採用のみ対象）」 | **恒久的に対象外** |
| `before_too_large` | 「変更前ファイルが保存上限を超えていたため戻せません（同じスキルを次に採用した時は戻せる可能性があります）」 | 再 accept 時に載る可能性はある |

- **git 復元の自動 fallback はしない**（コミット境界と accept 境界が一致する保証がなく別変更まで戻す危険）。手動案内に留める

---

## §4 CLAUDE.md の是正（PR-2 の完了条件）

体験4 の本文「採用は1コマンドで戻せる」→ **「skill 採用は1コマンドで戻せる」**。remediation（rules/hooks）の採用は ADR-041 の意図的スコープ外で戻せないため、設計文書だけ正直で hot が過剰約束のまま残る直し残しを作らない。

---

## §5 API 命名（S6 / S2）

- **正準名を `load_raw_history()`** にする。旧 `load_history()` は**後方互換の raw wrapper** として残し、`load_raw_history()` を呼ぶ（単なる別名にはしない。docstring・型・将来の deprecation を独立管理できるようにする）
- `load_effective_history(slug)` / `load_revert_events(slug)` を追加。fold ロジックは純粋関数 `fold_effective(records)` に集約（§1 の `outcome_promotion_readiness` 対応）
- **契約テストの向き（S2）**: 「業務 reader が raw を呼ばない」の列挙式は新規 reader が素通りするので採らない。**raw を呼んでよい閉じた許可リスト**を固定し、**production 全体でそれ以外の raw 呼び出しを一律 fail** にする。新規コードは既定で effective に落ちる
- **allowlist の粒度は関数 / callsite 単位にする（v2 round2 codex [Should]・ファイル単位にしない）**: `fitness_evolution.py` は**同一ファイル内に raw dedup と業務読取が混在**するため、ファイル単位 allowlist にすると業務側の raw 回帰を見逃す。具体的には:
  - import 元の**完全修飾名を解決**する（import alias と `module.load_history()` の両形を扱う）
  - 許可は**関数単位**（可能なら安定した callsite ID 単位）
  - 許可するのは `fitness_evolution.record_evolve_diff_decision` の dedup / store 内部 / migration / 診断 / `_emit.py` の generation 読み — **いずれも関数単位で列挙**
  - **allowlist entry が消失した場合も fail** させる（古い許可が野放しにならないように）
- 判定は **bare 名でなく import 元**で行う（`trigger_engine/session_corrections.py:53` は同名別物の `fitness_evolution.load_history()` を呼ぶので名前マッチだと FP。リポジトリには `skill_declaration_reachability.py` の AST 静的解析の作法が既にある）
- **AST の限界を認める**: 任意の `_read_jsonl()` / `Path.read_text()` が「raw history 読取」かは AST だけでは判定できない。**既知の history direct reader**（`outcome_promotion_readiness` の glob 経路など）は AST gate とは**別の明示的な契約テスト / 棚卸し対象**として管理する

---

## §6 実装の段階化（PR は分割しない）

**PR は1本にする。** PR-1 のレビューで「PR-1 単独価値は導線ゼロ」と指摘された経緯があり、ここで更に「イベントと view だけ入れて CLI は次」に割ると「動かないものが2つ積まれる」を再演する（tacchi・codex とも分割しない判断に同意）。

ただし **v1 の「各改修は 1〜2 行の置換が中心」は誤りだった**（M4 / S5）。内部を4段階に分け、各段階でテスト可能にする（commit 単位）:

| 段階 | 内容 | 主なテスト |
|---|---|---|
| 1 | **lock protocol** — `read_only_file_lock` + seqlock check-after + 単調性契約 + 環境境界 | §0.5 の契約テスト10本 |
| 2 | **store / effective view** — revert イベント schema + `fold_effective` 純粋関数 + `load_raw_history` / `load_effective_history` / `load_revert_events` | fold の単体テスト + **AST checker 本体と期待違反 fixture**（production 全体への gate は段階4 で有効化する・下記） |
| 3 | **apply engine** — 復元・冪等・conflict・メタデータ契約（`st_nlink` 拒否 / ACL・xattr 非保持の明示） | 決定6 の分岐テスト + crash/race テスト |
| 4 | **reader migration + board + CLI** — 業務 reader 7 箇所（`fitness_evolution` の raw/effective 分離 / `outcome_promotion_readiness` の slug 単位 cross-dir 集約 + fold 適用を含む）+ `results_board` の候補 schema + `bin/evolve-revert` + CLAUDE.md | reader 契約テスト + **AST gate を production 全体で有効化** + CLI の dry-run/apply テスト |

**段階順の注意（v2 round2 codex [Should]）**: AST gate（allowlist 外の raw 呼出し禁止）を段階2 で production 全体に適用すると、**段階4 で移行予定の既存 reader が残っているため必ず失敗する**。各 commit を独立して green に保つため、段階2 では **checker 本体と期待違反 fixture まで**を入れ、**production 全体への gate 有効化は reader migration 完了後の段階4** で行う。apply engine が store / event API に依存する段階順（1→2→3）自体は問題ない。

CLI 作法（実測で確定・S5 の「worker に残さない」に対応）: `bin/evolve-*` は**薄いラッパー**で、`sys.path` に `scripts/lib` を挿して `*_cli.py` の `main()` を呼ぶだけ（`bin/evolve-tier` が雛形）。実体は `scripts/lib/evolve_revert_cli.py` に置き、`--apply` で実書込・**既定 dry-run**（`tier_policy_cli.py:46,126,141` の作法に揃える）。

---

## §7 正直に書く制限【v6 から不変】

- **遡及不能**: 記録拡張（PR-1）前の accept は戻せない。可/不可を**理由コード付きで**区別表示する
- **lane 限定**: 戻せるのは skill diff の採用のみ。remediation（rules/hooks）の採用は対象外

---

## §8 意図的にそうする挙動（明記して驚きを消す）

- **N1**: revert された accept は effective view から消える＝accept でも reject でもない。よって `fleet/propose.py` の再提示抑制（`human_accepted is False`）に掛からず、**同じパッチが再提案されうる**。generation 分離により ID は別になるので冪等も壊れない。

  **「ヘルプに1行」では足りない（v2 round4 tacchi）**: 驚いた瞬間にヘルプを読む人はいない。表示は驚きの**発生地点**に置く（§3 で board に実コマンドを印字させたのと同じ理屈）。2段で対応する:

  - **PR-2 の必須**: revert の apply 完了メッセージに**予告**を1行出す — 「戻しました。なお同じ改善が今後また提案されることがあります（意図した動作です。不要なら提示時に n で拒否してください）」。驚きの**発生前**に届く
  - **望ましい（次 PR でも可）**: 提示側で `load_revert_events()` + 元 entry の content sha を突合し、朝の y/n 提示に「⤺ このパッチは以前採用して戻したものと同内容です」と注記する。決定論・ゼロ LLM で出せる。これがあると「戻したのにまた出た」という驚きが「以前戻したもの。今回はどうする？」という**情報**に変わる

  ヘルプの1行自体は残してよいが、**それを充足条件にしない**
- **N2**: revert イベントを weak_signal 等の**学習系に流さない**。強い negative シグナルだが #379 Step1 で新規 weak_signal channel は凍結中。黙って流す実装を作らない

---

## 却下した案（v6 から継承 + v2 で追加）

- diff 保存 → 失敗モードのみ増える（決定1）
- blob ディレクトリ / backup sidecar → 新ストア新設で #379 Step1 凍結に抵触
- git commit 単位で戻す → 採用パッチは commit されるとは限らず global には git が無い
- 適用境界（apply 時）に before を読む → drain 時には disk が既に after で**構造的に不存在**
- 元 accept に `reverted=True` フラグを立てる → 既存 reader が `human_accepted` で拾い続ける
- emit が lock を取らずに generation を読む → lock は非取得読者を排除しない
- **（v2 追加）apply 直前の locked re-snapshot** → 決定4 の「drain 時に導出し直さない」と衝突し #279 の N 重記録が再発
- **（v2 追加）sidecar 不在なら無条件に `generation=0`** → 外部削除で「revert 済みなのに不在」が成立しうる。history を**実際に読んで** generation を導出し、不整合は**警告して続行**する（fail させない。§0.3 の裁定）
