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

**N-a（残存分裂の母数）**: `line_no`/`prev_line_no` が異なる（＝本当に別の発話・畳まないのが
正しい）rephrase の分裂は、1.2 の old-split 24グループ中 **16グループ**（取得日2026-08-25。
similarity のみが差の8グループを除いた残り）。このうち既読/未読が混在する実害は「続けて」
**1件のみ**（他15グループは両方既読 or 両方未読で実害なし）。本設計はこの型を解消しない
（上記の対象外判断）ため、**将来この型の再提示が増えたとき、この母数16グループ・実害1件と
比較して規模の変化を判断できるよう記録しておく**。

**N-b（両方未読のsimilarity-only重複）**: similarity-onlyの8グループのうち、既読/未読混在
（実害）は6グループ、**両方未読のグループが2グループ実在する**（両方既読は0グループ）。
`daily_review` は group 化で同一 idiom/keyword の signal_keys を束ねるため、この2グループが
同一朝に別々の確認として二重提示される可能性は低いと見込むが、**実装時の受入テストで
実際に1グループとして提示されることを確認する**（`_group_new` の group 化ロジックと
`expand_seen_keys_for_rephrase_dupes` の相互作用は本設計では検証していないため、
組み合わせの動作確認を受入条件に追加する）。

## 2. key 設計（縮小版）

### 2.1 identity の定義（denylist に修正・R1/M1 対応）

**R1（v2からの訂正）: v2 は7フィールドを列挙する allowlist だったが、この形だと将来
rephrase provenance にフィールドが追加された瞬間、そのフィールドが無条件に identity から
除外され別内容が既読扱いになりうる。→ denylist（`similarity` だけを除外し、残りは
provenance の全フィールドをそのまま identity に含める）へ改める。**

```
identity = (session_id, {k: v for k, v in provenance.items() if k != "similarity"})
```

ただし `text`/`prev_text` の2フィールドのみ NFC正規化+前後空白 strip を適用する
（S4対応。`k in ("text", "prev_text")` で判定 — 型による判定ではなくフィールド名による
判定にすることで、将来 provenance に文字列型の新フィールドが増えても無関係に
正規化されない）。identity は `(session_id, json.dumps(正規化済み辞書, sort_keys=True,
ensure_ascii=False))` のタプルとして扱う。

**denylist を選ぶ理由**: rephrase provenance の生成箇所は
`weak_signals/detectors.py:290-301` の1箇所のみ（1関数が単独で書く固定 shape）。
将来ここにフィールドが追加された場合、denylist なら**自動的に identity に含まれる**
（安全側 — 新フィールドの値が違えば別 identity になるだけで、over-merge は起きない。
worst case でも「本来同一視すべきものが分裂したまま」という**現状と同じ失敗モード**に
留まり、新たな over-merge を生まない）。allowlist だと逆に「新フィールドが無条件に
除外される」という**検出しづらい**危険な失敗モードになる。

**新フィールド追加を検出する契約テスト（R1 の「穴の検出手段」）**: `detectors.py:290-301`
が書く provenance のキー集合を凍結した frozenset（例:
`{"detector","similarity","prev_line_no","line_no","prev_text","text","source_path"}`）と
実際の `detect_rephrase` の出力キー集合を突合するテストを追加する。フィールドが増減したら
このテストが red になり、メンテナが「このフィールドは identity に含めてよい内容か
（実行条件でないか）」を意識的に判断する契機になる（denylist 自体は自動的に安全側に
倒れるが、判断の痕跡を残すためにテストで気づけるようにする）。

`line_no`/`prev_line_no`/`session_id`/`source_path`/`detector` はそのまま値比較する
（int/str のいずれであっても正規化不要）。

この定義は v1 の §1.2/§2.1/§2.3 で3通りに割れていた `source_path` の扱い（M1 の指摘）を
解消する: **`source_path` は identity に含める**（denylist なので `similarity` 以外は
自動的に含まれる。同一セッションでも異なる transcript file に跨る記録は理論上あり得るため、
除外すると衝突源になりうる。含めるコストは実測上ゼロ — 1.2 の7件中どのケースも
`source_path` は各ペア内で同一だった）。

### 2.2 陰性方向の検証（`session_id` を含めない軸での測定・R2 対応）

`session_id` を identity から落とす案は**実データで危険と確認済み**。**この節の測定は
`source_path` を含めない `(session_id有無, text)` の軸**（1.2 の `(session_id, source_path,
text)` の3フィールド軸とは別の測定）:

```
rephrase: (session_id, text) 152 groups / (text のみ) 110 groups（差42）
'続けて'    → 28 セッションに跨って出現
'お願い'    → 14 セッションに跨って出現
```

（1.2 の「156グループ」は `(session_id, source_path, text)` の3軸、本節の「152グループ」は
`(session_id, text)` の2軸で `source_path` を含めない。156と152の差はこの軸の違いによるもの
であり、同じ測定の食い違いではない。）

`session_id` を落とすと無関係な28件の「続けて」が1つの key に潰れ、最初の1回を既読にした
瞬間に残り27件が二度と提示されなくなる（黙って握りつぶす）。**v2 のスコープ縮小後も
`session_id` は identity に必ず含める**という結論は変わらない。同じ理由で `line_no`/
`prev_line_no` も identity に残す（1.2 の "続けて" 実例が示す通り、これらを外すと
本当に別の発話が同一 key に潰れる。4.2 変異#3で実測確認済み）。

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

R1: identity は denylist（similarity のみ除外）。将来 provenance にフィールドが増えても
自動的に identity へ含まれ、over-merge を新たに生まない安全側の設計（2.1 参照）。
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

REPHRASE_CHANNEL = "rephrase"
_EXCLUDED_FIELDS = frozenset({"similarity"})
# R4: identity に含める全フィールド（denylist 適用後に残るもの）の欠損検査対象。
# 2026-08-25 時点の detect_rephrase（detectors.py:290-301）が書く全キー
# （similarity を除く）と一致させる。フィールドが増減したら下記の契約テストで検知する。
_REQUIRED_FIELDS = ("source_path", "line_no", "prev_line_no", "prev_text", "text", "detector")


def _normalize_text(v: Any) -> str:
    if not isinstance(v, str) or not v:
        return ""
    return unicodedata.normalize("NFC", v).strip()


def _dedup_identity(rec: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """similarity を除いた rephrase の同値類キー（denylist・R1）。

    R4 fail-safe: identity に含める全フィールド（_REQUIRED_FIELDS・session_id 含む）の
    いずれかが欠けていたら None を返す。呼び出し側は None のレコードをグルーピング対象
    から外す（＝現行どおり signal_key 単独判定にフォールバックする。誤って別内容を
    同一 identity に丸め込まない安全側の挙動）。
    """
    if rec.get("channel") != REPHRASE_CHANNEL:
        return None
    prov = rec.get("provenance") or {}
    session_id = rec.get("session_id")
    if not session_id:
        return None
    for field in _REQUIRED_FIELDS:
        v = prov.get(field)
        if v is None or v == "":
            return None
    ident_prov = {k: v for k, v in prov.items() if k not in _EXCLUDED_FIELDS}
    for field in ("text", "prev_text"):
        if field in ident_prov and isinstance(ident_prov[field], str):
            ident_prov[field] = _normalize_text(ident_prov[field])
    return (session_id, json.dumps(ident_prov, sort_keys=True, ensure_ascii=False))


def expand_seen_keys_for_rephrase_dupes(
    scoped_records: List[Dict[str, Any]],
    seen_keys: Set[str],
) -> Set[str]:
    """rephrase channel の similarity-only 重複を、既読 signal_key の集合へ拡張する。

    scoped_records は呼び出し側（daily_review.build_review）が既に pj_slug +
    REVIEW_CHANNELS でスコープ済みのレコード（filter_actionable 適用前）。identity が
    同一で、そのうちどれか1つの signal_key が既に既読なら、同一 identity の他の
    signal_key も「既読」とみなして拡張後の集合に加える。新しい key 空間は作らない
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


# R1 契約テスト（実装 PR で追加）: detect_rephrase の provenance キー集合が
# _EXCLUDED_FIELDS ∪ _REQUIRED_FIELDS と一致することを assert する。
# 新フィールド追加時にこのテストが red になり、fail-safe/allowlist是非の再検討を促す。
```

**R7（S2 の生産点・消費点の配線を修正）**: `daily_review._read_new` は変更しない
（元の signature・実装のまま）。件数計算と `seen_keys` の拡張は **`build_review`
（`daily_review.py:369-` 。既に `scoped` スナップショットを持っている）側で行う**:

```python
def build_review(pj_slug, *, weak_signals_path=None, idioms_path=None, seen_path=None,
                  corrections_path=None, max_groups=5, exclude_signal_keys=None,
                  dry_run=False, marker_base=None):
    seen_keys = read_reviewed_keys(seen_path)
    scoped = _scoped_review_candidates(pj_slug, weak_signals_path)

    # R7: 「実際に出力から除外した件数」を数えるため、拡張前後で filter_actionable を
    # 2回通し、その差分（signal_key の集合差）だけを数える。promoted/expired/
    # bootstrap消化済み/machinery で元々除外される予定だった key は差分に出ない
    # （baseline 側も同じ filter_actionable を通しているため）。
    from weak_signals.rephrase_dedup import expand_seen_keys_for_rephrase_dupes
    expanded_seen_keys = expand_seen_keys_for_rephrase_dupes(scoped, seen_keys)

    baseline_new = _read_new(pj_slug, seen_keys=seen_keys, marker_base=marker_base, scoped=scoped)
    new_records = _read_new(pj_slug, seen_keys=expanded_seen_keys, marker_base=marker_base, scoped=scoped)
    rephrase_similarity_dedup_count = len(
        {r["signal_key"] for r in baseline_new} - {r["signal_key"] for r in new_records}
    )
    # 以降は従来どおり new_records（拡張後の結果）を groups 化する。
    ...
    return {
        ...,
        "rephrase_similarity_dedup_count": rephrase_similarity_dedup_count,  # 新規キー1つのみ
    }
```

`_read_new` を2回呼ぶコストは 4.4 の実測（0.15ms@191件）に対して定数倍でしかなく無視できる。
`filter_actionable`/`_read_new` の**シグネチャ・実装は一切変更しない**ため M4 の解決は維持
される（`build_review` の呼び出し方だけが変わる）。

`S1` 対応: `expand_seen_keys_for_rephrase_dupes` は `scoped_records`/`seen_keys` とも
デフォルト値なしの必須位置引数（呼び出し側の配線漏れをデフォルト値で静かに許さない）。

`S2` 対応（畳んだ件数を surface する）: この設計の失敗モードは「過剰再提示」ではなく
逆側＝「衝突しすぎて正当な未読を黙って握りつぶす」こと（4.2 変異#3で検証する誤衝突と
同じリスク）。`rephrase_similarity_dedup_count` を `build_review` の返り値 dict に
**新規キーとして1行追加**する（`excluded_machinery_total`・`excluded_machinery_by_channel`
と同型・同じ dict 内。新規ストア/新規 observability section ではなく、既存の常時 emit
dict に1フィールド足すだけ）。異常な増加を daily_review の出力上で目視できるようにする
（silence != evaluated の既存方針を踏襲）。**GPT系の指摘どおり、この件数は
「promoted/expired/bootstrap消化済み/machineryで元々除外される予定だったkey」を含まない
（baseline_new・new_records とも同じ filter_actionable 通過後の結果を比較するため）。**

### 3.2 bootstrap は無改変（M4/M5 への回答）

`bootstrap_backlog.py` は `_scope_backlog_candidates`（`bootstrap_backlog.py:351-375,
408-413`）を経由して**独自に** promoted/expired の事前除外を行っており、`_read_new`/
`build_review` とは別の関数・別のコードパスである。本設計は `weak_signals/rephrase_dedup.py`
を **`daily_review.build_review` からしか import しない**（R7 の配線変更後も同じ — import元
が `_read_new` から `build_review` に移っただけで「daily_review.py 限定」という境界は不変）
ため、bootstrap の判定ロジックには一切触れず、bootstrap は従来どおり `signal_key` の
完全一致でのみ既読判定する。

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

## 4. 検査の有効性（M10 全面作り直し・R5/R6 で実データに対し実際に実行）

v2 の変異テストは実行不能・期待値誤り・重複ありと3系統とも指摘された（M10・R5）。
denylist 版の `_dedup_identity`/`expand_seen_keys_for_rephrase_dupes`（3.1）と**等価な
参照実装**を今日実際に書き、実データ・合成 fixture に通して結果を確認した
（R6: 変異テストを「実施計画」でなく実施結果として記載する。取得日2026-08-25。
本番実装は実装 PR で `scripts/lib/tests/test_weak_signal_rephrase_dedup.py` として
作成し、下記と同じ fixture ・期待値をそのまま使う）。

### 4.1 陽性対照（実行結果）

`expand_seen_keys_for_rephrase_dupes` の baseline（正しい実装）を rephrase 191件全件に
適用:

```
baseline: newly-expanded signal_key count: 6
harm groups resolved by baseline: 6 / 7
delta size: 6 / expected member-key set size(6harmグループの両端計12件中、既読でない側): 6
unexpected keys in delta (should be empty set): set()
delta == sim_only_harm_keys - seen_keys_real ? True
```

**R5 の指摘どおり**、「拡張集合そのもの」に185件が混入しないかを見るのは既読keyの初期包含と
矛盾するため誤り。正しい陽性対照は**差分**（`expanded - seen_keys`＝新たに拡張された要素）
だけを見ることで、これが6件のsimilarity-onlyペアの未読側ちょうど6件と**完全一致**する
（多すぎも少なすぎもしない）ことを確認した。

### 4.2 陰性試験（下限4件+dry-run純度+store書込ゲートの計6件・全件実行済み）

各変異について「baseline（正しい実装）の結果」と「変異後の結果」を実際に比較した
（R5: 壊す不変条件と検査経路が同じものは重複除去。R6: 実行結果を記載）。

| # | 分類 | 変異内容 | 壊す不変条件 | 検査経路と実行結果 |
|---|---|---|---|---|
| 1 | ①要素を消す | `_dedup_identity` の必須フィールド欠損ガード（R4 で `_REQUIRED_FIELDS` 全件チェックに拡張済み）を削除 | R4 fail-safe: 必須フィールド欠損（この変異では `text=""`）レコードが誤って他レコードと同一 identity に丸め込まれない不変条件 | fixture: 同一session/source/line_no/prev_line_noだが`text=""`の2レコード（FS_A既読/FS_B未読）。**baseline: FS_B in expanded? False（安全）** → **mutant: FS_B in expanded? True（BUG検出）** |
| 2 | ②語は残して意味を壊す | `_normalize_text` から `unicodedata.normalize("NFC", ...)` を削除（strip のみ残す＝NFC正規化だけを壊す） | text の同一性判定（NFC/NFDの表記揺れを正しく同一視する不変条件） | fixture: 濁点分解（は+゙+けて／ばけて）の合成/分解ペア（NFC_A既読/NFC_B未読）。**baseline: NFC_B in expanded? True（正しく統合）** → **mutant: NFC_B in expanded? False（BUG検出＝under-merge、#4とは異なる壊れ方）** |
| 3 | ③分散・入替 | `_dedup_identity` の identity から `line_no`/`prev_line_no` を除外（issue想定の広いスコープに戻す変異） | 2.2で確認した「別の物理行ペアの発話を誤って同一identityにしない」不変条件 | fixture: 実データの"続けて"harm組（line_no 1091/1288が異なる本物の別発話）。**baseline: 混入しない（False、正しい）** → **mutant: 混入する（True、BUG検出＝over-merge）** |
| 4 | ④検査を無効化する | `expand_seen_keys_for_rephrase_dupes` を `return set(seen_keys)`（no-op）にすり替える | 本設計の目的そのもの（6件解消） | 実データ191件。**baseline: 6/7解消** → **mutant: 0/7解消（BUG検出）**。#2 とは「壊れ方」（正規化ロジックの欠落 vs 関数全体の無効化）も「検出fixture」（synthetic NFC/NFDペア vs 実データharmセット）も異なるため独立変異として数える |
| 5 | ④検査を無効化する（dry-run純度） | `expand_seen_keys_for_rephrase_dupes` 内部に計算結果を書き出すコード（例: `open(path,"a").write(...)`）を追加。返り値は変えない | dry-run純度（本関数は読み取り専用の純関数であるべき不変条件） | 実装PRで実施: `daily_review.build_review(..., dry_run=True)` 呼び出し前後でDATA_DIR配下のファイル一覧・bytes数・mtimeを比較し無変化をassert。加えて`rl_common.store_write`/`store_write_raw`を`unittest.mock.patch`で「呼ばれたら例外」にし、呼ばれないことを確認する（本関数はどちらも呼ばない設計のため、この変異はmock例外で即red） |
| 6 | ④検査を無効化する（store書込ゲート） | `weak_signals/rephrase_dedup.py`に`store_write_raw`直接呼び出しまたは`open(path,"w"/"a")`、`Path(...).write_text(...)`、`json.dump(..., open(...))`のいずれかを追加 | store_write barrier を経由しない書込みが紛れ込まない不変条件（5節） | 実装diffに対する静的チェック `rg -n "store_write_raw\|open\(.*[\"']?[wa]\|write_text\|json\.dump" scripts/lib/weak_signals/rephrase_dedup.py` が0件であることをCIで確認する。**Claude系指摘への対応**: このregexは検出漏れがありうる（他の書込手段が今後増える可能性）ため過信しない — **#5のruntime側（DATA_DIR前後比較+store_writeモック）が本命の検出手段**であり、本静的チェックは早期発見のための補助と位置づける |

「配線を一切実装しない変異」（`build_review` に `expand_seen_keys_for_rephrase_dupes` の
呼び出しを追加しない）は、#2/#4のE2Eテストが `daily_review.build_review` をエンドツーエンド
で呼ぶ限り自然に検出される（配線が無ければ拡張が一切起きず#1〜#6が救われないままredになる）
ため、独立した変異として追加しない（#4と同一の検査経路・同一の壊れ方のクラスであり重複）。

「map逆引きの誤り」（`groups.setdefault(ident, []).append(key)`を
`groups.setdefault(key, []).append(ident)`のように取り違える）は#4と同じ「関数の目的を
達成しない」壊れ方に収束するため、#4のE2Eテストで検出される（キーと値を取り違えると
`groups`は事実上シングルトンの集まりになり、拡張が一切起きない）。独立変異として追加は
不要（重複回避のため明示的に不採用理由を書く）。

R5指摘の「相殺の見逃し」対策: 4.1の陽性対照が**差分の完全一致**（多い/少ないの両方向）を
確認するため、「誤衝突1件＋誤分裂1件」が unique 件数だけを比較していれば相殺されて
見えなくなるケースも、`delta == sim_only_harm_keys - seen_keys_real`（集合の完全一致）で
検出できる（片方でも余分/不足があれば `False` になる）。

### 4.3 未探索の入力クラス

- **巨大入力**: `text`/`prev_text` は書込時点で 120 文字に truncate 済み
  （`weak_signals/detectors.py:298-299`）。探索しない。
- **並行実行**: `_dedup_identity`/`expand_seen_keys_for_rephrase_dupes` は純関数で共有状態を
  持たない。探索しない。
- **実行順序**: `scoped_records` の走査順は identity の同値類判定に影響しない
  （dict の構築順は出力の集合には無関係）。探索しない。
- **キャッシュ鮮度**: キャッシュを持たない設計。探索しない。
- **10倍データでの実測**（M12・4.4節で詳述）: 探索**した**（未探索ではない）。
- **`text[:120]`/`prev_text` の truncate 長・`user_only_text` 仕様変更**（S3・N-d 対応）:
  意図的に**探索しない**まま残す構造的な穴。truncate 長が将来変わると、同じ実発話でも
  旧レコードと新レコードで `text` の値そのものが変わり、denylist 方式でも
  `_dedup_identity` の identity が変わって再分裂する。allowlist/denylist いずれの設計でも
  防げない（identity の入力である provenance の値自体が変わるため）。**防げないことを
  無いかのように書かない**（S3 の要求どおり）: この穴は本設計のスコープ外として認識し、
  対応しない。

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
「rephrase 限定なのでこの懸念は本設計の範囲外」は今日出せる。①③は対象消滅につき
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

**ロールバック時の挙動（R3・M7 未解消の指摘に対する正直な訂正）**: 本設計をデプロイ後に
ロールバックすると、`weak_signals/rephrase_dedup.py` の import が失われ `build_review` は
拡張前の `seen_keys` へ戻る。拡張結果は一切永続化されないため、**ロールバック直後には
新規発生分だけでなく、デプロイ中に抑止されていた既存6件の未読側（1.2 の #1〜#6）も
含めて再び y/n 確認に出る**（v2 版は「新規発生分のみ」と書いていたが誤り。拡張は
毎回 `scoped_records` から都度計算される一時的な効果であり、`seen_keys` 自体には
何も追記されないため、拡張ロジックが無くなれば直ちに元の signal_key 単独一致判定に
戻り、6件全てが再提示対象に戻る）。これはデータが壊れるわけではなく、単に修正前
（issue #543 が問題視している）状態に戻るだけであり、その意味では許容範囲だが、
「壊れるモードが存在しない」という主張は誤りだったため訂正する。実害の規模は
1.3 の規模（184件中6件・約3.3%）に留まる。

## 8. 未解決・判断を仰ぎたい点

無し（v1 の未解決点2件はいずれも頭の裁定で決着: ①適用範囲は rephrase 限定に確定、
②`denial_reason` は permission_deny 自体が対象外になったため議論不要）。

## 9. M/S/N 対応表（R2巡目・頭の裁定への回答）

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
| S3 | 4.3 に truncate長変更時の再分裂リスクを明記（N-d対応で追記済み）。防げないことを正直に書き、意図的に対応しないと明示 |
| S4 | 2.1 で明記: NFC+strip は `text`/`prev_text`（str型フィールド）のみに適用。他フィールドは値のまま比較 |
| N1 | **縮小により解消**: 新しい key 空間（content_key）を作らない設計に変更したため、対応表のフォールバック冗長性という問題自体が発生しない |
| N2 | **縮小により解消**: 対象チャネルが rephrase 1つになったため、チャネル横断の表自体が不要になった |

## 10. M/S/N 対応表（R3巡目・GPT系残Mustへの回答）

前巡から解消済みと判定された項目（M2/M3/M4/M8/M9/M12/S1/S3/S4/N1/N2、Claude系がidentity
3分裂の本体を解消済みと判定した箇所）は再検討していない。以下は R3 の残項目のみ。

| # | 対応 |
|---|---|
| R1 | 2.1/3.1 で allowlist（7フィールド列挙）を denylist（`similarity` のみ除外）へ改めた。新フィールド追加時の検出手段として、`detect_rephrase` の provenance キー集合を凍結 frozenset と突合する契約テストを3.1に明記 |
| R2 | 2.2 に「1.2の156グループは`(session_id,source_path,text)`の3軸・2.2の152グループは`(session_id,text)`の2軸でsource_pathを含まない」と明記。数字自体は変更なし |
| R3 | 7節「ロールバック時の挙動」を訂正: 新規発生分だけでなく既存6件の未読側も含めて再提示が復活することを正直に明記。データ破壊はないが「壊れるモードが存在しない」は誤りだったと認めた |
| R4 | 3.1の`_dedup_identity`のfail-safeを`_REQUIRED_FIELDS`（source_path/line_no/prev_line_no/prev_text/text/detector全件）に拡張。prev_text/detectorの欠損も検査対象に |
| R5 | 4.2を全面作り直し。#2を「NFC正規化欠落」に差し替え#4（no-op）と非重複化。4.1の陽性対照を「拡張集合そのもの」でなく「差分（delta）」で判定するよう修正しR5指摘の矛盾を解消。#6の静的チェックにwrite_text/json.dumpも追加しregexへの過信を明記 |
| R6 | 4節冒頭・4.1・4.2に「今日実際に参照実装を書いて実行した」ことを明記し、コマンド相当の結果（baseline/mutant比較）を全件記載。実装PRでは同じfixture・期待値をそのままpytest化する旨を明記 |
| R7 | 3.1で`_read_new`は無改変のまま、`build_review`が`_read_new`を（拡張前/拡張後の）2回呼んで差分を数える設計に変更。promoted/expired/bootstrap/machineryで元々除外される予定だったkeyは差分に出ないことを明記 |
| N-a | 1.4に「line_no/prev_line_noで割れた分裂の母数16グループ・既読混在は続けて1件のみ」を追記 |
| N-b | 1.4に「両方未読のsimilarity-only重複が2グループ実在」「group化との相互作用は実装時の受入テストで確認する」を追記 |
| N-c | 「2.1の7件中」を「1.2の7件中」に修正（2.1で回答を書いた際の誤参照。7件は1.2で測定・列挙している） |
| N-d | 4.3にtruncate長変更時の再分裂リスクを追記（従来「4.3に記載」としていたが実際には未記載だった不整合を解消）。2.1の正規化定義の参照も4.4（性能測定）ではなくS4対応の記述に修正済み |
| N-e | 「今日time出せる」の編集残骸を「今日出せる」に修正 |

### R3 実施した実測（コマンド・出力の要約）

denylist版の参照実装（`_dedup_identity`/`expand_seen_keys_for_rephrase_dupes`相当）を
今日実際に書き、以下を実行（取得日2026-08-25）:

```
baseline: newly-expanded signal_key count: 6 / harm groups resolved: 6/7
mutant#1(fail-safeガード削除): FS_B in expanded? True (baseline False → BUG検出)
mutant#2(NFC正規化削除): NFC_B in expanded? False (baseline True → BUG検出、under-merge)
mutant#3(line_no/prev_line_no除外): '続けて'ペア混入? True (baseline False → BUG検出、over-merge)
mutant#4(no-op): harm groups resolved: 0/7 (BUG検出)
positive control: delta == sim_only_harm_keys - seen_keys_real ? True（完全一致）
```
