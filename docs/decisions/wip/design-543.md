# #543 設計メモ v2（頭の裁定でスコープ縮小・レビュー2巡目）

v1 は Claude系/GPT系レビュー2系統とも「設計修正要」。頭の裁定によりスコープを縮小し、
**rephrase チャネル限定・daily_review 層限定・identity から `similarity` のみ除外**の
最小構成に作り直した。v1 の Must（両系統重複含む M1-M12）・Should（S1-S4）・Nit（N1-N2）に
すべて対応する。対応表は文末（9節）。

対象: `compute_signal_key`（`scripts/lib/weak_signals/store.py:57-68`）の dedup key が
実行条件を含むため、同一の指摘が別 key として朝の確認（daily_review・以下「y/n 確認」）に
再提示される問題。

## 0. 要約（5行・v2）

- 実害を再測定したところ、**7件中6件は同一の物理行ペア（line_no/prev_line_no 完全一致）を
  再検出し `similarity` の浮動小数だけが変わった重複**。残り1件（"続けて"）は別の物理行
  ペア＝本当に別の発話で、これは畳んではいけない（2.2 実測で確認済みの危険）。
- **スコープを rephrase チャネル・daily_review 経路限定に縮小**し、identity から
  **`similarity` だけ**を除外する。`line_no`/`prev_line_no`/`prev_text`/`text`/`session_id`/
  `source_path`/`detector` は identity に残す。
- 実装は **`filter_actionable`（共有 predicate）に触れず**、daily_review 側で呼び出し前に
  `seen_keys` を拡張するだけ（signal_key はそのまま・新しい key 空間を作らない）。
  bootstrap 等の他 reader は無改変。
- 永続フィールド・新規ストアは無し（v1 から変更なし）。実害解消 6件・見送り1件（"続けて"）・
  想定外リスク（llm_judge の model/prompt_fingerprint 分裂）は**別 issue へ切り出し**、
  本設計では扱わない。
- 追加コストは実測 0.15ms/call（実データ191件）・1.03ms/call（10倍相当の合成1910件）で
  daily_review の他処理（jsonl 読み込み等）に対して無視できる規模。

## 1. 問題の再定義（実測ベース・再測定で訂正）

### 1.1 issue 起票時の想定との差分（変更なし・v1 から維持）

issue 起票時は Unicode 正規化差（NFC/NFD）を主因と想定していたが、**実測で否定**:
`weak_signals.jsonl` 全1511件の `provenance.text` を `unicodedata.normalize('NFC', t) == t`
でコードポイント単位に走査し **NFC/NFD差0件**（取得日 2026-08-25、自己再測定）。
改行/CR混入は688件中246件確認したが、分裂した group 内では `text` の生値が完全一致しており
**分裂の原因にはなっていない**（1.3 で確認）。真因は「provenance に実行条件フィールドが
同居し、hash 入力に無差別に混ざっている」こと。

### 1.2 実害の再定義（GPT系レビュー M2 反映・最重要の訂正）

`~/.claude/evolve-anything/weak_signals.jsonl`（1511件）/
`correction_review_seen.jsonl`（**184件**。`wc -l` で行数・unique key 数とも184で一致。
取得日 2026-08-25 自己再測定）を実データに使用。

rephrase channel（191件）を `(session_id, source_path, 正規化text)` でグルーピングすると
156グループ・24グループが分裂・うち「既読/未読が混在」する実害は**7グループ**（v1 と同数、
1日分のデータ増減の影響を除けば再現）。**この7グループの中身を個別に確認**した結果:

| # | text（先頭20字） | 分裂原因フィールド | line_no（両側） | prev_line_no（両側） | 既読フラグ |
|---|---|---|---|---|---|
| 1 | `[Image#1]このrepositor` | `similarity` のみ | 59, 59（**同一**） | 52, 52（**同一**） | True, False |
| 2 | `>正しいanchor（青バー／黒帯）で測` | `similarity` のみ | 11899, 11899 | 11897, 11897 | True, False |
| 3 | `https://formulae.bre` | `similarity` のみ | 193, 193 | 170, 170 | True, False |
| 4 | `docs/design/claim-ga` | `similarity` のみ | 11392, 11392 | 11389, 11389 | True, False |
| 5 | `反映するなら次の3点です。1.報告資料:` | `similarity` のみ | 19847, 19847 | 19844, 19844 | True, False |
| 6 | `https://doc.up-idx.t` | `similarity` のみ | 33820, 33820 | 33813, 33813 | True, False |
| 7 | `続けて` | `line_no`, `prev_line_no` | **1091, 1288**（別） | **965, 1091**（別） | False, True |

（取得日 2026-08-25。測定スクリプトはこの設計と同じ commit に含めないが、9節の変異表の
fixture として同内容を固定化する。）

**#1〜#6 は同一の物理行ペア（line_no/prev_line_no が完全一致）を検出器が2回検出し、
similarity の丸め誤差だけが違う純粋な重複**。#7 の「続けて」は line_no/prev_line_no が
どちらも異なる＝**別の発話**（`~/.claude/evolve-anything/weak_signals.jsonl:1272-1273`
相当）で、これを畳むのは誤り（同じ短い相槌が別の文脈で28回起きても、内容が同じというだけで
1回の確認に潰してよいわけではない — 2.2 の実測で確認済みの危険と同じ種類の問題）。

→ **実害の定義を「rephrase の similarity 差 6 件」に訂正する。** "続けて" の再提示は
本設計では解消しない（対象外として明記する。1.4 参照）。

### 1.3 実害の規模判断（訂正後）

- 分母 184 件中 **6 件（約 3.3%）** が「同一物理行の重複検出で similarity だけ違う」ために
  再提示される。検出器（`weak_signals/detectors.py:251-307`）は日次の再ingestやセッション
  再走査のたびに同じ隣接ペアを再評価しうる構造なので、**時間が経つほど蓄積する恒常的な
  漏れ**（1.2 の6件はいずれも今日時点のスナップショットでの実害で、今後も同種の重複が
  発生し続けると推定される）。
- 修正コストは「daily_review 内の1関数追加＋既読判定の呼び出し前に seen_keys を拡張する
  だけ」で、共有 predicate・他チャネル・他 reader に一切触れない。**低コストで直せる**
  と判断し実装する。

### 1.4 対象外にするもの（v1 からの後退・正直に明記）

- **"続けて"型の再提示（1件）は解消しない。** 同一テキストが別の物理行ペアで検出された
  ケースを畳むには「同じ物理行の重複検出」より強い「同じ会話上の同じ訂正」という判定が
  要り、今回の実測（2.2）でその強い判定は危険（無関係な発話を握りつぶす）と分かっている。
  本設計のスコープ外とし、代替の解決策は無い（issue #543 は"完全解決"ではなく"実害6件の
  解消"として閉じる）。
- **llm_judge / permission_deny / verbosity には一切適用しない。** 実害0件であり、
  Claude系レビューが指摘した「llm_judge の model/prompt_fingerprint/reason が
  category_schema_version 更新時に一斉分裂しうる」将来リスクは実在するが、**別 issue として
  切り出す**（起票は頭が行う。本設計に issue 番号は書かない）。理由は 6 節。

## 2. key 設計（縮小版）

### 2.1 identity の定義（1つに統一・M1 対応）

rephrase channel の identity は以下の7フィールドの組。**`similarity` のみを除外**し、
他は一切変更しない:

```
identity = (session_id, source_path, line_no, prev_line_no,
            normalize(prev_text), normalize(text), detector)
```

`normalize()` は NFC 正規化 + 前後空白 strip（`text`/`prev_text` の2フィールドのみに適用。
S4 対応・4.4 節）。`line_no`/`prev_line_no`/`session_id`/`source_path`/`detector` はそのまま
値比較する（int/str のいずれであっても正規化不要 — 検出器が生成した値をそのまま比較する）。

この定義は v1 の §1.2/§2.1/§2.3 で3通りに割れていた `source_path` の扱い（M1 の指摘）を
解消する: **`source_path` は identity に含める**（同一セッションでも異なる transcript file
に跨る記録は理論上あり得るため、除外すると衝突源になりうる。含めるコストは実測上ゼロ
— 2.1 の7件中どのケースも `source_path` は各ペア内で同一だった）。

### 2.2 陰性方向の検証（v1 から維持・危険の実測）

`session_id` を identity から落とす案は**実データで危険と確認済み**（v1 §2.2 と同一の実測、
再掲）:

```
rephrase: session_id 込み 152 groups / 抜き 110 groups（差42）
'続けて'    → 28 セッションに跨って出現
'お願い'    → 14 セッションに跨って出現
```

`session_id` を落とすと無関係な28件の「続けて」が1つの key に潰れ、最初の1回を既読にした
瞬間に残り27件が二度と提示されなくなる（黙って握りつぶす）。**v2 のスコープ縮小後も
`session_id` は identity に必ず含める**という結論は変わらない。同じ理由で `line_no`/
`prev_line_no` も identity に残す（1.2 の "続けて" 実例が示す通り、これらを外すと
本当に別の発話が同一 key に潰れる）。

## 3. 実装: 実際に触る箇所（M4/M5 対応・共有 predicate へ広げない）

### 3.1 配線点: `filter_actionable` を変更せず、呼び出し前に `seen_keys` を拡張する

v1 は「`weak_signals.jsonl` 全件から signal_key→content_key の対応表を作り
`filter_actionable` に content_key ベースの判定をさせる」設計だったが、これは
`filter_actionable` の契約（呼出側でスコープ済みのレコードしか受け取らない・
`correction_semantic/promote.py:207-235`）と衝突し、実装不能だった（M4）。

v2 は **`filter_actionable` のシグネチャ・実装を一切変更しない**。代わりに
`daily_review._read_new`（`daily_review.py:187-225`）が `filter_actionable` を呼ぶ**前**に、
渡す `seen_keys` 集合そのものを拡張する:

```python
# scripts/lib/weak_signals/rephrase_dedup.py（新規ファイル・純関数のみ・新規ストアではない）
"""rephrase channel の similarity-only 重複を daily_review の既読判定でのみ吸収する（#543）。

filter_actionable の契約を変えない: 呼び出し前に seen_keys 集合を拡張するだけ。
scope: rephrase channel・daily_review 経路限定。bootstrap 等の他 reader には配線しない
（M4/M5: 共有 predicate へ広げない・頭の裁定）。
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Set, Tuple

REPHRASE_CHANNEL = "rephrase"


def _normalize_text(v: Any) -> str:
    if not isinstance(v, str) or not v:
        return ""
    return unicodedata.normalize("NFC", v).strip()


def _dedup_identity(rec: Dict[str, Any]):
    """similarity を除いた rephrase の同値類キー。

    M6 fail-safe: identity を構成する必須値（session_id / source_path / text /
    line_no / prev_line_no）のいずれかが欠けていたら None を返す。呼び出し側は
    None のレコードをグルーピング対象から外す（＝現行どおり signal_key 単独判定に
    フォールバックする。誤って別内容を同一 identity に丸め込まない安全側の挙動）。
    """
    if rec.get("channel") != REPHRASE_CHANNEL:
        return None
    prov = rec.get("provenance") or {}
    session_id = rec.get("session_id")
    source_path = prov.get("source_path")
    line_no = prov.get("line_no")
    prev_line_no = prov.get("prev_line_no")
    text = _normalize_text(prov.get("text"))
    prev_text = _normalize_text(prov.get("prev_text"))
    detector = prov.get("detector")
    if not session_id or not source_path or not text:
        return None
    if line_no in (None, "") or prev_line_no in (None, ""):
        return None
    return (session_id, source_path, line_no, prev_line_no, prev_text, text, detector)


def expand_seen_keys_for_rephrase_dupes(
    scoped_records: List[Dict[str, Any]],
    seen_keys: Set[str],
) -> Set[str]:
    """rephrase channel の similarity-only 重複を、既読 signal_key の集合へ拡張する。

    scoped_records は呼び出し側（daily_review._read_new）が既に pj_slug + REVIEW_CHANNELS
    でスコープ済みのレコード（filter_actionable 適用前）。identity が同一で、そのうち
    どれか1つの signal_key が既に既読なら、同一 identity の他の signal_key も
    「既読」とみなして拡張後の集合に加える。新しい key 空間は作らない
    （拡張後も要素はすべて既存の signal_key 文字列。N1: 別名前空間を混ぜない）。
    """
    groups: Dict[Any, List[str]] = {}
    for rec in scoped_records:
        ident = _dedup_identity(rec)
        if ident is None:
            continue
        key = rec.get("signal_key")
        if not key:
            continue
        groups.setdefault(ident, []).append(key)

    expanded = set(seen_keys)
    for keys in groups.values():
        if len(keys) < 2:
            continue
        if any(k in seen_keys for k in keys):
            expanded.update(keys)
    return expanded
```

`daily_review._read_new` の変更点（1関数呼び出しの追加のみ）:

```python
def _read_new(pj_slug, *, weak_signals_path=None, seen_keys, marker_base=None, scoped=None):
    from correction_semantic.promote import filter_actionable
    if scoped is None:
        scoped = _scoped_review_candidates(pj_slug, weak_signals_path)
    from weak_signals.rephrase_dedup import expand_seen_keys_for_rephrase_dupes
    effective_seen_keys = expand_seen_keys_for_rephrase_dupes(scoped, seen_keys)
    return filter_actionable(scoped, pj_slug, seen_keys=effective_seen_keys, marker_base=marker_base)
```

`S1` 対応: `expand_seen_keys_for_rephrase_dupes` は `scoped_records`/`seen_keys` とも
デフォルト値なしの必須位置引数（呼び出し側の配線漏れをデフォルト値で静かに許さない）。

`S2` 対応（畳んだ件数を surface する）: この設計の失敗モードは「過剰再提示」ではなく
逆側＝「衝突しすぎて正当な未読を黙って握りつぶす」こと（4.2 #3 で検証する誤衝突と同じ
リスク）。`_read_new` で `effective_seen_keys - seen_keys` の要素数を
`rephrase_similarity_dedup_count` として計算し、`build_review` の返り値 dict に
**新規キーとして1行追加**する（`excluded_machinery_total`・`excluded_machinery_by_channel`
と同型・同じ dict 内。新規ストア/新規 observability section ではなく、既存の常時 emit
dict に1フィールド足すだけ）。異常な増加を daily_review の出力上で目視できるようにする
（silence != evaluated の既存方針を踏襲）。

### 3.2 bootstrap は無改変（M4/M5 への回答）

`bootstrap_backlog.py` は `_scope_backlog_candidates`（`bootstrap_backlog.py:351-375,
408-413`）を経由して**独自に** promoted/expired の事前除外を行っており、`_read_new` とは
別の関数・別のコードパスである。本設計は `weak_signals/rephrase_dedup.py` を
**`daily_review.py` からしか import しない**ため、bootstrap の判定ロジックには一切触れず、
bootstrap は従来どおり `signal_key` の完全一致でのみ既読判定する。

これにより M5 が指摘した「全 reader で既読判定が一致する」という不変条件は、**そもそも
本設計の要求事項にしない**: 本設計が保証するのは daily_review 経路のみであり、bootstrap は
意図的に対象外（縮小の一部）。これは新しい非一致を持ち込むわけではない — **v1 以前から
bootstrap と daily_review は別の除外条件（machinery 除外・promoted 除外のタイミング等）を
持つ別々の predicate**であり（`daily_review.py:206-217` のコメント参照）、本設計はその
既存の非一致の集合に「similarity-only 重複の扱い」という軸を1つ追加するだけで、新しい
種類の分裂を生まない。

残るリスク: bootstrap 実行時（初回導入・新PJ）に、bootstrap 対象のバックログ内に
similarity-only 重複が含まれていた場合、**bootstrap は畳まずに2回 y/n 確認を出す**。
これは許容する（bootstrap は一生に一度・小規模な導入フローであり、daily_review のように
「時間経過で蓄積する恒常的な漏れ」ではないため優先度が低い。1.3 の規模判断と同じ基準）。

## 4. 検査の有効性（M10 全面作り直し）

v1 の変異テストは実行不能・期待値誤り・重複ありと3系統とも指摘された（M10）。実装可能な
関数（3.1）に対して、実データ（1.2 の7件）を fixture として作り直す。

### 4.1 陽性対照

- 1.2 の7件fixtureに加え、rephrase 191件全体を fixture 化し、`expand_seen_keys_for_rephrase_dupes`
  適用前後で **6件（similarity-only）以外の 185 件の signal_key が一切拡張集合に混入しない**
  ことを、**個々の signal_key の集合**で assert する（件数比較だけでなく要素比較。M10 の
  「unique 件数だけでは誤衝突1件+誤分裂1件の相殺を検出できない」指摘への対応）。
- dict のキー順序・JSON 化時の空白を変える書き換えを適用し、`_dedup_identity` の戻り値
  （tuple）が変わらないことを確認する（tuple 構築は値ベースであることの陽性確認）。

### 4.2 陰性試験（下限4件+dry-run純度+store書込ゲートの計6件）

| # | 分類 | 変異内容 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|---|
| 1 | ①要素を消す | `_dedup_identity` の `if not session_id or not source_path or not text: return None`（fail-safe ガード）を消す | M6 fail-safe: 必須フィールド欠損レコードが誤って他レコードと同一 identity に丸め込まれない不変条件 | 1.2 の7件fixtureに「`text` が空文字列の rephrase レコード」を1件追加した拡張fixtureで、そのレコードが既存グループに誤って混入しない（＝拡張集合のサイズが変わらない）ことを確認するテスト |
| 2 | ②語は残して意味を壊す | `expand_seen_keys_for_rephrase_dupes` の `if any(k in seen_keys for k in keys)` を `if all(...)` に変える（命名・シグネチャは無変更） | 「片方が既読なら他方も既読とみなす」という本設計の目的（1.2 の6件で、既読側1件・未読側1件の状態から未読側を救う） | 1.2 の7件fixtureを通し、6件（#1〜#6）が拡張集合に含まれることを確認する E2E テストが red になる（all にすると「両方既読」でない限り拡張されず、6件とも救われない） |
| 3 | ③分散・入替 | `_dedup_identity` の tuple から `line_no`/`prev_line_no` を外す（issue 想定の広いスコープに戻す変異） | 2.2 で確認した「別の物理行ペアの発話を誤って同一 identity にしない」不変条件（"続けて" の実例） | 1.2 の #7（"続けて"）fixture で、line_no/prev_line_no を含めた場合は拡張集合に含まれない（=引き続き2回確認される）ことを確認するテスト。line_no/prev_line_no を外すと #7 も拡張集合に混入し red になる |
| 4 | ④検査を無効化する | `expand_seen_keys_for_rephrase_dupes` を `return set(seen_keys)`（no-op）にすり替える | 本設計の目的そのもの（1.2 の6件解消） | #2 と同じ E2E テストが red になる。#2 は「条件式のロジック反転」、#4 は「関数全体の無効化」で壊し方のクラスが異なるため重複として数えない（通したい検査経路は同じ E2E テストだが、要求されているのは「壊す不変条件」と「検査経路」の組が同一なら重複＝ここは壊す変異の性質が違う） |
| 5 | ④検査を無効化する（dry-run純度） | `expand_seen_keys_for_rephrase_dupes` 内部に、計算結果をどこかへ書き出すコード（例: ログファイルへの `open(path, "a").write(...)`）を追加する。関数の返り値は変えない | dry-run 純度（本関数は読み取り専用の純関数であるべきという不変条件） | `daily_review.build_review(..., dry_run=True)` 呼び出し前後で `DATA_DIR` 配下のファイル一覧・各ファイルの bytes 数・mtime を比較し、変化が無いことを assert するテスト。加えて `rl_common.store_write`/`store_write_raw` を `unittest.mock.patch` で「呼ばれたら例外」にして、呼ばれないことを確認する |
| 6 | ④検査を無効化する（store書込ゲート） | `weak_signals/rephrase_dedup.py` 内に `store_write_raw` の直接呼び出し、または `open(path, "w")`/`open(path, "a")` を追加する | store_write barrier を経由しない書込みが紛れ込まない不変条件（5節） | 実装 diff に対する静的チェック: `rg -n "store_write_raw|open\(.*[\"']?[wa]" scripts/lib/weak_signals/rephrase_dedup.py` が0件であることを CI で確認する（軽量・機械的） |

`#3` と `#4` は「壊す不変条件」が異なる（#3 は過剰統合防止・#4 は目的達成そのもの）ため
重複として数えない。`#2` と `#4` は同じ E2E テストで検出されるが、変異の性質
（ロジック反転 vs 関数丸ごと無効化）が異なるため別カウントとする。

「配線を一切実装しない変異」（`_read_new` に `expand_seen_keys_for_rephrase_dupes` の
呼び出しを追加しない）は、上記 #2/#4 の E2E テストが `daily_review.build_review` を
エンドツーエンドで呼ぶ限り自然に検出される（配線が無ければ拡張が一切起きず #1〜#6 が
救われないまま red になる）ため、独立した変異として追加しない（#2/#4 と同一の検査経路・
同一の壊れ方のクラスであり重複になるため）。

「map 逆引きの誤り」（`groups.setdefault(ident, []).append(key)` を
`groups.setdefault(key, []).append(ident)` のように取り違える）は #4 と同じ「関数の目的を
達成しない」壊れ方に収束するため、#4 の E2E テストで検出される（キーと値を取り違えると
`groups` は事実上シングルトンの集まりになり、拡張が一切起きない）。独立変異として追加は
不要（重複回避のため明示的に不採用理由を書く）。

### 4.3 未探索の入力クラス

- **巨大入力**: `text`/`prev_text` は書込時点で 120 文字に truncate 済み
  （`weak_signals/detectors.py:298-299`）。探索しない。
- **並行実行**: `_dedup_identity`/`expand_seen_keys_for_rephrase_dupes` は純関数で共有状態を
  持たない。探索しない。
- **実行順序**: `scoped_records` の走査順は identity の同値類判定に影響しない
  （dict の構築順は出力の集合には無関係）。探索しない。
- **キャッシュ鮮度**: キャッシュを持たない設計。探索しない。
- **10倍データでの実測**（M12・4.4節で詳述）: 探索**した**（未探索ではない）。

### 4.4 追加コストの実測（M12 対応）

`expand_seen_keys_for_rephrase_dupes` と同等の計算（identity 構築 + グルーピング + 集合拡張）
を実データ・合成データで実測（取得日 2026-08-25）:

```
N=191（実データ全件）:   0.1482 ms/call（100回平均）
N=1910（10倍相当・合成）: 1.0283 ms/call（20回平均）
```

O(N) の線形増加が確認でき、10倍データでも1ミリ秒程度。daily_review 自体が
`weak_signals.jsonl`（1511件）・`correction_review_seen.jsonl`（184件）の jsonl 読み込みを
毎回行っている既存コストに対して無視できる規模と判断する。

## 5. 制約の遵守（v1 から維持・スコープ縮小でむしろ余裕が増えた）

- **新設凍結（#379 Step 1）**: `weak_signals/rephrase_dedup.py` は純関数のみ。新規ストア/
  observability section/advisory proposal adapter/weak_signal channel のいずれも追加しない。
  既存2ストアのスキーマ変更もない（フィールド追加なし）。
- **store_write barrier**: 本設計は既存の書込経路を一切変更しない。4.2 #6 の変異テストで
  「新規ファイルが store_write barrier を経由しない書込みを持たないこと」を機械的に確認する。
- **dry-run 純度**: 4.2 #5 の変異テストで実測ベースに確認する（v1 は「副作用が無いため
  分岐不要」と書くだけだったが、それ自体を検査で担保する）。
- **file-size-budget**: `weak_signals/rephrase_dedup.py` は新規ファイルで50行未満の見込み。

## 6. 対象外にした将来リスク（Claude系レビュー指摘・別issueへ）

Claude系レビューが指摘した「llm_judge の provenance に `model`/`prompt_fingerprint`/`reason`
という再現性のないフィールドが同居しており、`category_schema_version` が上がった瞬間に
全既読が割れる」リスクは実在する（`correction_semantic/batch.py:351-359` の
producer時点測定値という自己申告どおり、Haiku呼び出し結果や schema version は将来変わり
うる）。**今回は適用しない**（頭の裁定・実害0件のため）。このリスクへの対応は**別issueとして
切り出し**（起票は頭が行う）、本設計では扱わない。

同様に、`#534` Phase 1.5（Codex CLI セッションログ由来の発話を既存パイプラインに流す設計、
実装未着手）が持ち込みうる未知の provenance 形状への懸念（M11 で「今日代理測定せよ」と
指摘された項目）も、**スコープが rephrase channel 限定になったことで対象外**になった:
本設計は llm_judge の provenance に一切触れないため、Codex 統合が llm_judge のprovenance
形状をどう変えても本設計のコードパス（rephrase_dedup.py）には影響しない。M11 の該当項目は
「対象が消滅した」ため測定不要と結論する（3問の②: 片側だけでも今出る結論＝
「rephrase 限定なのでこの懸念は本設計の範囲外」は今日time出せる。①③は対象消滅につき
該当なし）。

同じ理由で、`permission_deny`/`verbosity` の特殊入力・巨大入力・`denial_reason` の扱い
（M11 の他3項目）も、**本設計がこれらのチャネルに一切触れないため対象外**。実害0件の
まま現状維持であり、触れていないコードに対する検査は不要と判断する。

## 7. 後方互換・移行（v1 から維持・スコープ縮小でさらにリスクが下がる）

v1 §3 の結論は変わらない: `signal_key` の計算方法は変更せず、拡張は `seen_keys` という
既存の literal signal_key 集合をランタイムで広げるだけで、新しい key 空間・永続フィールドを
作らない。**移行という操作自体が発生しない。**

**184/184 の受入 fixture（M8/M9 対応）**: `correction_review_seen.jsonl` の184件全ての
`key` が、現在の `weak_signals.jsonl` に対応する record を持つことを確認済み
（`wc -l` 一致・取得日2026-08-25）。この対応関係を凍結した fixture として固定し
（1.2 の7件を含む実データサブセット）、`expand_seen_keys_for_rephrase_dupes` が
KeyError 等を起こさず184件を処理できることを受入テストにする。

**元レコードが欠落しているケース（M8）**: `expand_seen_keys_for_rephrase_dupes` は
`scoped_records`（現在 `weak_signals.jsonl` に存在するレコードのみ）から identity 集合を
構築するため、既読 signal_key に対応する元レコードが物理的に欠落していても、それは
単に「グルーピング対象に含まれない」だけで例外にはならない。欠落した側を無視して
残っているレコードだけで判定するため、**握りつぶし方向には倒れない**（安全側 —
その signal_key は従来どおり `seen_keys` の literal 一致でしか救えないが、これは
本設計を適用する前と同じ挙動であり後退ではない）。現在のスナップショットでは184件全件が
存在するため、この分岐は実データでは発火しない（受入 fixture で確認済み）。

**ロールバック時の挙動（M7・正直に明記）**: 本設計をデプロイ後にロールバックすると、
`weak_signals/rephrase_dedup.py` の import が失われ `_read_new` は拡張前の `seen_keys` へ
戻る。その結果、**ロールバック後に新規発生した similarity-only 重複（ロールバック後の
検出器再走査で生まれたもの）は再び1件ずつ y/n 確認に出る**。これは「壊れるモードが
存在しない」という v1 の主張が過大だった点（M7 の指摘どおり）を訂正するもので、
許容する: 拡張結果は一切永続化されないため、ロールバックで**データが壊れることはなく**、
単に v1 以前の（今回直したい）挙動に戻るだけ。実害は 1.3 の規模（3.3%）程度に留まる。

## 8. 未解決・判断を仰ぎたい点

無し（v1 の未解決点2件はいずれも頭の裁定で決着: ①適用範囲は rephrase 限定に確定、
②`denial_reason` は permission_deny 自体が対象外になったため議論不要）。

## 9. M/S/N 対応表（頭の裁定への回答）

| # | 対応 |
|---|---|
| M1 | 2.1 で identity を1つに統一（`source_path` は含める）。3通りの食い違いを解消 |
| M2 | 1.2 で実害を「similarity差6件」に訂正。"続けて"は別発話と確認し対象外に切り出し（1.4） |
| M3 | 3.1/2.2 で回答: 縮小案は line_no/prev_line_no を identity に残すため反例（"続けて"）は発生しない。理由を明記 |
| M4 | 3.1 で回答: `filter_actionable` は無改変。呼び出し前に `seen_keys` を拡張するだけの配線に変更し実装可能にした |
| M5 | 3.2 で回答: bootstrap は無改変・意図的に対象外。全reader一致は本設計の要求事項にしない理由を明記 |
| M6 | 3.1 の `_dedup_identity` に fail-safe（必須フィールド欠損で None→対象外）を実装。4.2 #1 で検査 |
| M7 | 7節「ロールバック時の挙動」で正直に明記。データ破壊はないが再提示は復活することを認める |
| M8 | 7節「元レコードが欠落しているケース」で回答。安全側に倒れることを説明し184/184を受入fixtureに固定 |
| M9 | 184に統一（本文中の182表記は無し。旧v1にあった1箇所も本rewriteで解消） |
| M10 | 4.2 で全面作り直し。実行可能な変異6件（陰性4件+dry-run純度+store書込ゲート）、期待値を実測値に修正、重複を明示的に不採用理由付きで除外 |
| M11 | 6節で回答: 変異テストは実測済み（4.2/4.4）。permission_deny特殊入力・Codex provenance・denial_reasonはスコープ縮小により対象消滅、3問の②で説明 |
| M12 | 4.4 で実測（0.15ms@191件・1.03ms@1910件） |
| S1 | 3.1: `expand_seen_keys_for_rephrase_dupes` は必須位置引数のみ（デフォルト値なし） |
| S2 | 3.1 で具体化: `rephrase_similarity_dedup_count` を `build_review` 返り値dictに新規キーとして追加（`excluded_machinery_total`と同型）。新規ストア/section は作らない |
| S3 | **縮小により実質解消**: rephrase の `text[:120]`/`prev_text` truncate 長が将来変わると再分裂しうる構造的な穴は残るが、対象チャネルが1つに絞られたことでリスク面が縮小。穴自体は防げないことを4.3の「未探索」枠外として認識しておく |
| S4 | 2.1 で明記: NFC+strip は `text`/`prev_text`（str型フィールド）のみに適用。他フィールドは値のまま比較 |
| N1 | **縮小により解消**: 新しい key 空間（content_key）を作らない設計に変更したため、対応表のフォールバック冗長性という問題自体が発生しない |
| N2 | **縮小により解消**: 対象チャネルが rephrase 1つになったため、チャネル横断の表自体が不要になった |
