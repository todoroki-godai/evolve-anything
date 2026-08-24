# #543 設計メモ v1（実装着手前レビュー待ち）

対象: `compute_signal_key`（`scripts/lib/weak_signals/store.py:57-68`）の dedup key が
実行条件（line_no / similarity 等）を含むため、同一の指摘が別 key として朝の確認
（daily_review・以下「y/n 確認」）に再提示される問題。

## 0. 要約（5行）

- 原因は「provenance の生 JSON を丸ごとハッシュしている」こと。line_no・similarity・model
  等の**実行条件フィールド**が1つでも違うと別 key になり、既読が引き継がれない。
- 実害は現状 **rephrase チャネルの再提示 7 件**（全既読 184 件中）。他チャネルは実質ゼロ。
  想定していた Unicode 正規化差は**実データに0件**で、issue 起票時の想定は誤りだった。
- 解決策は「provenance からハッシュ対象を絞った `content_key`」を**新たに計算する純関数**
  として追加し、y/n 確認の既読判定に使う。**新しい永続フィールドは作らない** — 既存
  `signal_key` は挙動を変えず、`content_key` は都度 provenance から再導出する。
- この設計だと「移行」は存在しない: 古いレコードの `provenance` は削除されず永続保存され
  ている（`weak_signals/ttl.py:108` 削除しない実測）ため、既読 184 件も新規レコードも
  同じ関数で同じように content_key を導出でき、書き換え・バッチ移行が一切不要。
- 除外フィールドは「実行条件（消しても content の実体が変わらない）」のみに絞った
  allowlist。session_id を含めないと実害化する事例（"続けて" が28セッションに跨がる）を
  実データで確認済み — 除外しすぎの危険を先に潰した。

## 1. 問題の再定義（実測ベース・issue #543 想定との差分）

### 1.1 想定と実データの食い違い（正直な訂正）

issue 起票時は「provenance の Unicode 正規化差（NFC/NFD）や改行混入で key が割れる」を
主因と想定していた。**実測でこれは否定された**:

- weak_signals.jsonl 全 1511 件の `provenance.text` をコードポイント単位で走査し NFC/NFD
  差を検出したが **0 件**（測定コマンド: `unicodedata.normalize('NFC', t) == t` を全件確認、
  下記 1.2 の再測定スクリプトに同梱。取得日 2026-08-25）。
- 改行/CR混入は自己再測定（`'\n' in t or '\r' in t` で全件走査、取得日 2026-08-25）で
  688件中246件確認したが、**分裂の原因にはなっていない**（分裂した group 内では
  `provenance.text` の生値が完全一致していた。1.3 の測定で確認。引き継ぎ時点の数値「315件」
  とは対象日時のデータ量差で異なるが、質的結論＝分裂原因ではない、は変わらない）。

真因は正規化差ではなく、**provenance に「実行条件」フィールドが同居し、それらが hash 入力
に無差別に混ざっている**こと。同一の実体験（同一発話・同一セッション）でも、検出された
物理位置（line_no）や類似度スコア（similarity）が違えば別 key になる。

### 1.2 実測: 規模と実害（2026-08-25 時点で自分で再測定）

`~/.claude/evolve-anything/weak_signals.jsonl`（1511件）/
`~/.claude/evolve-anything/correction_review_seen.jsonl`（184件）を実データとして使用。
測定スクリプトと結果:

```
$ wc -l weak_signals.jsonl correction_review_seen.jsonl
    1511 weak_signals.jsonl
     184 correction_review_seen.jsonl
```

channel 別件数（`json.loads` 全件走査）:

```
Counter({'esc_interrupt': 656, 'llm_judge': 497, 'rephrase': 191,
          'manual_edit_after_ai': 153, 'verbosity': 8, 'permission_deny': 6})
```

**「同一発話」の定義**を `(session_id, source_path, NFC正規化+空白除去済み provenance.text)`
とし、この単位でグルーピング → 同一発話が複数の signal_key に分裂している件数と、その中で
「一部だけ既読・一部が未読のまま残る」実害件数を測定（y/n 確認対象の4チャネルのみ）:

| channel | 総件数 | 発話グループ数 | 分裂グループ数 | 実害（再提示）件数 |
|---|---|---|---|---|
| llm_judge | 497 | 496 | 1 | 0 |
| rephrase | 191 | 156 | 24 | **7** |
| permission_deny | 6 | 2 | 1 | 0 |
| verbosity | 8 | 5 | 1 | 0 |

分裂原因フィールド（rephrase の24分裂グループ内で値が割れているフィールドの出現数）:

```
prev_line_no 16 / line_no 16 / similarity 8 / prev_text 1
```

（issue 引き継ぎメモの数値は similarity 10 / line_no 6 / prev_line_no 6 / prev_text 1 —
1日の差分データが増えた影響で件数が動いているが、**割れているフィールドの種類は完全一致**。
再現性を確認済み。測定スクリプトはこの設計文書と同じ commit に含めない使い捨てのため、
`scripts/lib/tests/test_weak_signal_content_key.py`（後述）に実コーパス相当のケースとして
固定化する。）

### 1.3 実害の規模判断（やる価値の根拠）

- **分母 184 件中 7 件（約 3.8%）** が実際に「一度既読にしたのに再提示される」状態。
- rephrase チャネルは検出ロジック上、構造的に**同一の短い発話が複数箇所で検出されやすい**
  （`weak_signals/detectors.py:251-307` — 隣接ペア単位で毎回別レコードを作る設計）。
  つまり時間が経つほど分裂率は下がらず、**むしろ蓄積する**（フロー構造上の恒常的な漏れで
  あって偶発的なノイズではない）。
- 修正コストは「provenance から一部フィールドを除いたハッシュ関数を1つ追加し、
  y/n 確認の既読チェックで使う」だけで、**新規ストア・書き換えマイグレーション・スキーマ
  変更のいずれも不要**（後述2, 3）。低コスト・高確度で直る部類と判断し、**実装する**。
- 代替案（やらない）を採るなら: 「rephrase チャネルを y/n 確認対象から外す」
  （`REVIEW_CHANNELS` から削除・`correction_semantic/review_channels.py:37-39`）で実害は
  0 になるが、rephrase 由来の正当な指摘も見えなくなり本末転倒。採用しない。

## 2. key 設計

### 2.1 何を identity として残すか（allowlist の根拠）

**判定基準**: 「そのフィールドの値が変わっても、実世界で起きた"同じ訂正"という事実は
変わらないか」で allowlist を決める。変わらない（＝実行条件・計測条件）なら除外、
変わる（＝内容そのもの）なら含める。除外リスト方式ではなく**含めてよい側を明示する
allowlist**（迷ったら除外でなく含める側へ倒す・#379/CLAUDE.md 方針）。

| channel | identity に含める | 除外する（根拠 file:line） |
|---|---|---|
| `llm_judge` | `session_id`, `source_path`, `provenance.text`（正規化後） | `line_no`（`batch.py:339-340` utterance の物理行 = 実行条件） / `prev_action`（`batch.py:345` 直前行動の記述 = 実行条件） / `reason`（`batch.py:346` Haiku 判定文 = LLM呼び出し結果で再現性なし） / `idiom`（`batch.py:350` 同上） / `judge`,`model`,`prompt_fingerprint`,`category_schema_version`（`batch.py:351,357-359` "producer 時点の測定条件"と自己申告済み。docstring `batch.py:353` 参照） / `category`（`batch.py:356` LLM判定結果） |
| `rephrase` | `session_id`, `source_path`, `provenance.text`（正規化後） | `line_no`,`prev_line_no`（`detectors.py:296-297` 隣接ペアの物理位置） / `similarity`（`detectors.py:295` 計算値） / `prev_text`（`detectors.py:298` 直前発話。今回の分裂原因の1件だが、同一 `text` が複数の直前発話から生まれても指摘の実体は同じと判断） / `detector`（固定値 "rephrase" で無意味） |
| `permission_deny` | `tool_name`, `tool_input_summary` | `timestamp`（実行条件） / `denial_reason`（実測: 6件中6件が "unknown"＝100%。取得日2026-08-25。実害0件のため保守的に**今回は変更を最小化し除外のみ**。値が有効なケースが増えたら再検討） |
| `verbosity` | `provenance.hash`（`hooks/record_verbosity.py:96` — 応答テキストの sha256。**既に content-only の安定ハッシュ**） | `patterns`,`note`（Haiku 判定結果で再現性なし・llm_judge の reason/idiom と同種リスク） / `char_len`,`project`（実行条件） |
| 未知 channel（例: 将来の Codex 由来・#534） | フォールバックとして `channel + provenance 全体`（＝**現行 `compute_signal_key` と同じ挙動**） | — （2.4 で詳述） |

`text` の正規化は「NFC 正規化 + 前後空白 strip」のみ（`correction_semantic/store.py:153-165`
の `normalize_idiom_text` と同方針。全角半角統一・casefold は意図的に入れない — 同モジュール
の既存コメント通り、日本語の短い断片は casefold で別意味を取り違えるリスクの方が大きい）。

### 2.2 陰性方向の検証（衝突しないことの実測）

**identity から `session_id` を落として良いか**を実データで検証した（落とせれば cross-セッション
でも同じ指摘を1回に畳めるので理想的だが、危険がないか先に確認する）:

```
llm_judge: session_id 込み 496 groups / 抜き 495 groups（差1）
rephrase : session_id 込み 152 groups / 抜き 110 groups（差42）
```

`session_id` を落とした場合の rephrase の衝突例（同一テキストが跨るセッション数）:

```
'続けて'    → 28 セッションに出現
'お願い'    → 14 セッションに出現
'終わりそう？' → 3 セッションに出現
```

**これは危険な衝突**: 「続けて」はどのセッションでも起きうる短い催促で、`REPHRASE_MIN_TOKENS=2`
（`detectors.py:41`）を満たす最短級の発話。28個の無関係なセッションでの「続けて」の言い直しを
1つの signal_key に潰すと、**最初の1回を既読にした瞬間に残り27件が問答無用で再提示不能になる
（黙って握りつぶす）**。これは今回直したい「過剰再提示」の逆の欠陥（過少提示）であり、
実装してはいけない変更だと判明した。→ **`session_id` は必ず identity に含める**（2.1 の表に反映済み）。

この検証により、「除外するのは実行条件のみ・内容に関わる軸（session_id・text）は残す」という
2.1 の判定基準が実データでも成立することを確認した。

### 2.3 実装: 新しい純関数（永続フィールドではない）

`scripts/lib/weak_signals/identity.py`（新規ファイル。**新規ストアではない** — 関数のみで
永続化しない）:

```python
IDENTITY_FIELDS: Dict[str, Tuple[str, ...]] = {
    "llm_judge": ("text",),
    "rephrase": ("text",),
    "permission_deny": ("tool_name", "tool_input_summary"),
    "verbosity": ("hash",),
}

def normalize_identity_text(text: str) -> str:
    ...  # NFC + strip のみ

def compute_content_key(channel: str, provenance: dict, session_id: str = "") -> str:
    """signal_key と別の、実行条件を除いた「同一指摘」判定用キー。
    未知 channel は compute_signal_key と同じ全 provenance ハッシュにフォールバックする。
    """
    fields = IDENTITY_FIELDS.get(channel)
    if fields is None:
        # 2.4: 未知 channel は安全側（現行 compute_signal_key と同一ロジック）
        payload = {"channel": channel, "provenance": provenance}
    else:
        vals = {}
        for f in fields:
            v = provenance.get(f, "")
            vals[f] = normalize_identity_text(v) if isinstance(v, str) else v
        payload = {"channel": channel, "session_id": session_id, "identity": vals}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
```

`compute_signal_key`（`store.py:57-68`）自体は**変更しない** — `signal_key` は
append 時の物理 dedup キーとして現行どおり使い続ける（後方互換の要。3節）。

### 2.4 未知 provenance 形状への申し送り（#534 Codex ログ由来の将来統合）

`#534` の Codex セッションログ取り込みは実装未着手・時期未定で、provenance の形が
CC 由来と異なる可能性がある。本設計では **`IDENTITY_FIELDS` に channel が無ければ、
現行 `compute_signal_key` と同じ「全 provenance ハッシュ」にフォールバックする**
（2.3 の `compute_content_key` 参照）。理由:

- 未知の形状に対して勝手にフィールドを選別すると、意図しない過剰結合（衝突）を生みうる
  （2.2 の実測どおり、identity の絞り込みは慎重な検証が要る）。
- フォールバックは「現状維持」なので、**新チャネル追加時に本設計が壊れることはない**
  （新チャネルは compute_signal_key と同じ挙動から始まり、分裂が実際に問題化した時点で
  同じ手順（1.2 のような実測）で allowlist を足せばよい）。
- `#534` 側が provenance 形状を確定させた時点で `IDENTITY_FIELDS` に1行追記するだけで
  対応できる設計にしてあるので、**いま合わせにいく必要はない**という要求を満たす。

## 3. 後方互換・移行（最重要）

### 3.1 結論: 移行は不要（設計そのものが移行を要求しない）

既存の 184 件の既読レコード（`correction_review_seen.jsonl`）は `{"key": <signal_key>, ...}`
という**物理 signal_key の集合**でしかない。本設計は:

- `signal_key` の計算方法（`compute_signal_key`）を**一切変更しない**。
- `content_key` は **provenance から都度計算する導出値であり、どこにも永続化しない**。

したがって「新しい key 計算に合わせて既存データを書き換える」という操作自体が発生しない。
これは根拠 A（読取時に旧 key でも照合）を一般化したもので、旧 key を「別途保持」する必要も
ない — **旧レコードの `provenance` 自体は削除されず永続保存されている**
（`weak_signals/ttl.py:108` の docstring「削除はしない」、実装上も `mark_expired` は
`expired`/`expired_at` フィールドを立てるだけで record を消さない・`ttl.py:82-93` の
`_rewrite` は「expired フラグを書き換えて全件を書き戻す」処理であって record を減らさない）
ため、**古い record も新しい record も、まったく同じ `compute_content_key` 関数に通せば
同じ content_key が出る**。

### 3.2 既読判定の書き換え（実際に触る箇所）

現状 `daily_review._read_new` → `promote.filter_actionable` は「候補レコードの
`signal_key` が既読集合（`read_reviewed_keys()`）に含まれるか」だけを見ている
（`daily_review.py:106-119` の `read_reviewed_keys` は文字通りの signal_key 集合）。

変更後は、既読集合の**意味を signal_key から content_key に拡張**する:

1. `read_reviewed_keys()` が返す signal_key 集合をそのまま使う（変更しない・後方互換）。
2. `filter_actionable`（`correction_semantic/promote.py`）の判定直前に、
   `weak_signals.jsonl` の全レコードから「signal_key → content_key」の対応表を作る
   （`read_signals()` は既に全件読んでいるので追加コストは `compute_content_key` の
   O(件数) 呼び出しのみ）。
3. 既読の signal_key 集合を、この対応表を通して **「既読の content_key 集合」** に変換する
   （対応表に無い signal_key＝当該レコードが `weak_signals.jsonl` から消えている場合は、
   フォールバックとして signal_key そのものを既読 content_key 集合にも加える。安全側 —
   見つからないケースは「今までどおり signal_key 一致でしか救えない」に留まるだけで、
   新たな見落としを増やさない）。
4. 候補レコードの判定は「`signal_key` が既読集合に**ある** OR `content_key` が既読
   content_key 集合に**ある**」の OR 条件にする（**signal_key 一致は消さない** — 万一
   content_key 側にバグがあっても、現行の一致判定は必ず効くフォールバックとして残す）。

この設計により、既存の 7 件の実害（1.2）は**次回 daily_review 実行時に自動的に解消**する
（コードをデプロイするだけで直る。マイグレーションスクリプトの実行や、その完了待ちは不要）。

### 3.3 検討した他の移行方式とその失敗モード（不採用の理由）

| 案 | 失敗モード | 中断時に何が起きるか |
|---|---|---|
| B. 書込時に新旧両方を記録（`signal_key` に加えて `content_key` を weak_signals.jsonl / correction_review_seen.jsonl の新フィールドとして書く） | 新フィールド追加は `store_registry` のスキーマ実質変更に当たり、レビューコストが増える。かつ**既存 184 件の過去レコードには新フィールドが無い**ため、結局 3.2 と同じ「provenance から都度導出するフォールバック」が要る＝二重に実装することになるだけで 3.2 に対する優位がない | 該当なし（forward-write のみなので中断しても新規書込が欠けるだけ。ただし過去データは救えないので結局 3.2 の read-time reconciliation を実装する必要が残る） |
| C. 一度きりの移行スクリプト（`weak_signals.jsonl`・`correction_review_seen.jsonl` を新 key で書き換え） | ①書き換え中のプロセス中断で `_rewrite` 系ヘルパー（`ttl.py:82-93` と同型の tmpfile→os.replace）は atomic なので**片方のファイルは救えるが、2ファイルを跨ぐ移行は atomic にできない**（weak_signals だけ書き換わり seen が未書き換えの中間状態が起きうる）。②`store_write` barrier は 1レコード単位の追記契約（`rl_common/store_write.py`）で、全件書き換えは barrier の設計外（`store_write_raw` の例外口を使うことになり、単一ゲート経由の原則から外れる）。③そもそも 3.1 の通り provenance は消えていないので、書き換えなくても導出できる — **書き換えは差分ゼロの結果を得るために可逆性リスクだけを負う過剰な手段** | 中断すると weak_signals.jsonl だけ新 key・correction_review_seen.jsonl は旧 key のまま残り、**両者の対応が壊れて全既読が一時的に無効化**する（本 issue が「防ぎたい」と言っている 182 件同時再提示の事故そのものを引き起こしうる。中断時が最悪のケースになる設計は採らない） |
| A'. 単純な「旧 key でも照合」（signal_key の新旧2種類を計算して両方を突き合わせる） | 「新 key 計算」を導入する前提が要るが、signal_key 自体は変更しない設計（3.1）なので新旧という概念が発生しない。3.2 の「content_key を additional に見る」設計に自然に収斂する | 該当なし |

採用は **3.2（読取時 content_key 併用照合。B/A' の考え方を一般化したもの）**。中断・欠損に対して
壊れるモードが存在しない（純関数の追加のみで、書込フローに変更がないため「移行が中断する」
という状態そのものが起きない）。

## 4. 検査の有効性（変異を実際に適用する）

実装者は以下を**実際にコードへ適用してテストが赤くなることを確認**した上で元に戻し、
その結果（どの変異がどのテストで検出されたか）を報告すること。①〜④は下限、上限ではない。

### 4.1 陽性対照（誤検知しないことの確認）

- 正常データ: 1.2 で使った実コーパスから、`llm_judge` の 495 グループ（未分裂）と `rephrase` の
  132 グループ（未分裂・156グループ中24分裂を除いた数。2026-08-25 実測）を fixture 化し、
  **変更前後で content_key のユニーク数が変わらない**
  （grouping が壊れて別々の指摘まで潰れていないか）ことを snapshot で確認する。
- 意味を変えない書き換え（全角/半角統一なし・casefold なしなので対象外だが）代わりに
  「同一発話の provenance dict のキー順序を変える」「JSON 化時の空白を変える」を適用し、
  content_key が変わらないことを確認する（`sort_keys=True` の効果を陽性側から確認）。

### 4.2 陰性試験（下限4件。壊す不変条件と通したい検査経路を明記）

| # | 分類 | 変異内容 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|---|
| 1 | ①要素を消す | `IDENTITY_FIELDS["rephrase"]` から `session_id` を**外す**（2.2 で検証した危険な変更そのものを再現） | 「無関係な短い発話（'続けて' 等）が別セッションで衝突しない」という不変条件 | 実コーパス由来の fixture（28 セッションに跨る '続けて'）で content_key のユニーク数が 28→1 に潰れることを検出するテスト |
| 2 | ②語は残して意味を壊す | `session_id` パラメータの型・名前は残すが、呼び出し側で常に空文字列 `""` を渡すようにする（`filter_actionable` 側の配線を壊す） | 同上（実質①と同じ不変条件だが、壊し方が「フィールド定義」でなく「呼び出し側の配線漏れ」という別クラス） | 同上の fixture で content_key が session 非依存になっていないかを確認するテスト。定義側だけでなく配線側の欠落も検出できることを示す |
| 3 | ③分散・入替 | `IDENTITY_FIELDS["rephrase"]` に `line_no` を**追加で戻す**（除外したはずのフィールドを混入させる） | 「同一発話・同一セッションなら別 line_no でも同じ content_key になる」という不変条件（1.2 の 24 分裂グループの再現防止） | 1.2 の実コーパス由来 24 分裂グループを fixture 化し、`compute_content_key` を通すと 24 グループが 0 に減ることを確認する回帰テスト。line_no を混入させると再び 24 分裂に戻ることを確認する |
| 4 | ④検査を無効化する | `compute_content_key` を `compute_signal_key` の単純な別名（同一実装）にすり替える（"変更した体で実は何もしていない"を模す） | 「rephrase の 24 分裂グループが 0 になる」という本設計の目的そのもの | 上記 #3 と同じ回帰テストが red になることを確認（テストがモックに対してではなく実装の中身に対して効いていることの確認） |

`#3`/`#4` は「通したい検査経路」が同一だが、壊す変異が異なる（フィールド allowlist の
値を壊す vs 関数自体をすり替える）ため重複扱いにしない。

### 4.3 未探索の入力クラス（明示的に対象外にする）

- **巨大入力**: provenance.text は既に書込時点で 120〜200 文字に truncate されている
  （`batch.py:344` `[:200]`、`detectors.py:298-299` `[:120]`）ため、content_key 計算での
  巨大入力は構造的に発生しない。探索しない。
- **並行実行**: `compute_content_key` は純関数で共有状態を持たないため競合状態が原理的に
  存在しない。`filter_actionable` 側の read（`read_signals`/`read_reviewed_keys`）は
  既存の union-read 契約（複数プロセスの追記に対して set 化で安全）をそのまま使うので
  新たな並行性リスクを追加しない。探索しない。
- **実行順序**: `weak_signals.jsonl` の読み込み順は content_key 計算に影響しない
  （signal_key→content_key の対応表はレコードごとに独立に計算される）。探索しない。
- **キャッシュ鮮度**: `compute_content_key` はキャッシュを持たない設計（都度 provenance
  から計算）なので鮮度問題が構造的に発生しない。探索しない。
- **未探索のまま残すもの**: `permission_deny` の `tool_input_summary` に極端に長い/特殊文字
  混じりのコマンド文字列が来た場合の挙動（_DENY_SUMMARY_TRUNC=120 で表示側は truncate
  されるが `provenance` 自体の生値は truncate されていない可能性がある）。実データは 6 件
  のみで確認しきれていない。**測定不能・理由**: 実害が 0 件のチャネルであり、今回のスコープ
  （rephrase の再提示解消）に対して優先度が低いため意図的に見送る。将来 permission_deny の
  分裂が実害化したら 1.2 と同じ手順で再測定する。

## 5. 制約の遵守（チェック済み）

- **新設凍結（#379 Step 1）**: `weak_signals/identity.py` は純関数のみ・新規ストア/
  observability section/advisory proposal adapter/weak_signal channel のいずれも追加しない。
  `shrink_freeze.FROZEN_STORES`（`shrink_freeze.py:71,107`）に `weak_signals.jsonl` /
  `correction_review_seen.jsonl` は既に登録済みで、本設計はどちらのスキーマも変更しない
  （フィールド追加なし）ため凍結ゲートに抵触しない。
- **store_write barrier**: 本設計は既存の書込経路（`append_signals`/`record_reviewed`）を
  一切変更しない。`store_write_raw` の新規使用もない。
- **dry-run 純度**: `compute_content_key` は読み取り専用の純関数であり、dry-run/非dry-run
  の分岐すら不要（副作用が無いため）。
- **file-size-budget**: `weak_signals/identity.py` は新規ファイルで数十行規模の見込み
  （`IDENTITY_FIELDS` 定義 + `compute_content_key` + `normalize_identity_text`）。
  500行に遠く及ばない。

## 6. 将来との整合（申し送り・再掲）

2.4 で述べた通り、`IDENTITY_FIELDS` に channel が未登録の場合は `compute_signal_key` と
同じ「全 provenance ハッシュ」にフォールバックする。`#534` Phase 1.5（Codex CLI セッション
ログ由来の発話を既存パイプラインに流す設計、実装未着手）が具体化した時点で、新しい
provenance 形状に対して 1.2 と同じ実測手順（同一発話グルーピング→分裂検出→allowlist 追記）
を再実行すればよい。**いま両者を統合する設計判断はしない**（対象が存在しないため実測不能）。

## 7. 未解決・判断を仰ぎたい点

1. **rephrase 以外のチャネルへの適用範囲**: 実害 0 件の permission_deny/verbosity/llm_judge
   にも allowlist を適用する設計にしている（2.1）。実害が無い分、リグレッションリスクに
   見合うかは判断が分かれうる。**推奨: 適用する**（理由: 4.2 の検査で担保されるコストの
   低い変更であり、`model`/`prompt_fingerprint`/`reason` 等 LLM 呼び出し由来フィールドを
   identity に含めたままにするのは rephrase と同型の潜在リスクを放置することになるため）。
   適用を見送り rephrase だけに絞る場合、`IDENTITY_FIELDS` から llm_judge/permission_deny/
   verbosity のエントリを削除するだけで良く、設計・実装コストへの影響は小さい。
2. **`denial_reason` を permission_deny の identity から外すか**（2.1 で「今回は変更を
   最小化」として除外のみとしたが、含めるかは実データが薄く暫定判断）。実装着手後に
   permission_deny の実データが増えたら再評価する（暫定採用・
   `.claude/rules/provisional-over-blocker.md` 方針）。
