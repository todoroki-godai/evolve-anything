# 設計案: reject された提案が次回 emit で再提示される（issue #446）

作成: 2026-08-14（設計のみ・コード変更ゼロ）。対象は「A レーン」= `evolve_decisions` の
emit→drain lane（skill_diff / skill_evolve / advisory 提案）。#446 本文・#417（identity 表記）を
前提とする。

## 1. 問題の要約

`proposal_id = f(repo_id, relative_path, before_sha)` は「対象ファイルの現在世代」を指す。

- **reject 後**: `before_sha` 不変 → 次回 emit も同じ `proposal_id` → emit は reject history を
  見ないため再提示される
- **accept 後**: 適用でファイルが変わり `before_sha` が変わる → 新しい `proposal_id`

## 2. 調査結果（実コード確認・実データ実測）

### 2.1 emit 側の現状（確認済み）

`scripts/lib/evolve_decisions/_emit.py:162-224`（`emit_decisions`）が `pending` を構築する
ループには reject history を参照する分岐が**一切ない**。`history = _store.load_history(slug)`
（`_emit.py:169` / `_read_disk_and_history`, `_emit.py:74`）は読まれるが、使い道は
`revert_generation_for_target`（`_emit.py:184-189`）だけで、reject 済み `proposal_id` の除外には
使われていない。`scripts/lib/evolve_decisions/_candidates.py` の `_extract_candidates` /
`_advisory_pending` にも reject 参照は無い（grep で `reject`/`history` の語が1件もヒットしない）。

**差し込み位置（単一ソース。2026-08-14 round2 訂正: `_emit.py:217` ではなく `:224`
直後——codex round2 [Nit]1）**: `seen_ids = {entry["id"] for entry in pending}`
（`_emit.py:217`）の直後は advisory 候補のマージ（`_advisory_pending()` の
`try/except Exception: pass`、`_emit.py:218-224`）が**まだ終わっていない**ため、
そこで `pending` を確定扱いするのは誤り（advisory 候補を suppression フィルタに
通し損なう）。正しい chokepoint は advisory マージ完了後の `_emit.py:224`
（`except Exception: pass` の直後）——ここで初めて `pending`（skill_diff・
skill_evolve・advisory の全件）が確定し、全 emit 経路を1回で通る唯一の
chokepoint になる。ここで `pending` をサプレッションフィルタに通せば、キュー書込
（`_emit.py:239-250`）とマーカー書込（`_emit.py:257-269`）の両方に自動的に反映される
（1箇所の filter で二重 writer 問題が起きない）。

### 2.2 既にある抑制機構の棚卸し

| 機構 | ファイル | キー | TTL | store 登録 |
|---|---|---|---|---|
| `triage_ledger` | `scripts/lib/triage_ledger.py` | `candidate_key(skill_name)`（正規化文字列のみ・`file` 概念なし） | 45日 + 7日クールダウン + 3回エスカレーション | **未登録**（`store_registry.py` に `triage_decisions` の grep 一致なし。`shrink_freeze.py` の `FROZEN_STORES` にも無い＝既存の隙間） |
| `remediation.suppression_ledger` | `scripts/lib/remediation/suppression_ledger.py` | `dedup_key(issue)` = sha256(`type`+`file`+`detail`の一部キー)（`suppression_ledger.py:78-96`） | 45日（`DEFAULT_TTL_DAYS`, `suppression_ledger.py:49`） | **登録済み**（`shrink_freeze.py:89` に `"remediation_suppression/<slug>.jsonl"`） |
| `advisory_decision_log` | `scripts/lib/advisory_decision_log.py` | `(pj_slug, proposal_id, terminal)`（`advisory_decision_log.py:110-117`） | なし（TTL 概念自体が無い。terminal は最新が勝つのみ） | 登録済み（`shrink_freeze.py` の `advisory_decisions.jsonl`） |
| `optimize_history`（reject 記録） | `optimize_history_store.py` / `record_evolve_diff_decision`（`skills/evolve-fitness/scripts/fitness_evolution.py:166`） | `id`（judgment event の opaque hash）のみ。**`skill_name`はあるが `repo_id`/`relative_path`/`before_sha` は reject には無い**（後述） | なし | 登録済み（既存） |

**重要な実測発見（`_ingest.py:124-127`, `:145-147` 確認）**: `record_evolve_diff_decision` の
`revert_fields` は **`kind=="accept"` のときだけ**渡される（decision2「恒久保存は accept のみ」）。
そのため **reject の optimize_history エントリには `repo_id`/`relative_path`/`before_sha` が
一切保存されない**（`entry` は `id`/`source`/`skill_name`/`diff_summary`/`timestamp`/
`fitness_func`/`best_fitness`/`human_accepted`/`rejection_reason`/`run_id`/`decision_source`
のみ・`fitness_evolution.py:220-232`）。よって **skill_diff/skill_evolve レーンは
`optimize_history` を直接引いても「この `(repo_id, relative_path)` が reject 済みか」を
判定できない**（`skill_name` だけでは同名スキルの複数候補を区別できず、粒度が粗すぎる）。

**advisory レーンは事情が違う（重要）**: advisory の `proposal_id`（`proposal.id`,
`_candidates.py:57,63-65` のコメント "advisory の id は既に detector+相対targets ベースで
worktree 非依存"）は **`before_sha` を含まない**。`_ingest.py:112-114` で reject は
`_record_advisory_event(..., kind="reject", ...)` → `advisory_decision_log.record_advisory_decision`
に**既に記録されている**。つまり **advisory レーンは「記録はある・consult されていないだけ」**
で、emit 側で `advisory_decision_log.read_advisory_decisions(slug)` を引いて直近 terminal が
`reject` の `proposal_id` を除外するだけで直る。**新規ストア・新規フィールドとも不要**。

**skill_diff/skill_evolve レーンは記録自体が無い**ので、以下のいずれかが要る:
　(A) `optimize_history` の reject entry に `repo_id`/`relative_path` を追加で持たせる
　(B) `remediation.suppression_ledger` を流用し、reject 時に別途 `record_rejection` を呼ぶ

→ §3.1 で両案を比較。

### 2.3 accept 後の再生成 — 解釈の整理（2026-08-14 改訂: 早期解除の専用機構は削除）

**解釈A（採用）**: 「別の提案」。accept は実際にファイル内容を変えた行為であり、新しい
`before_sha`（＝新しい `proposal_id`）はその新しい世代に対する正当な新規識別子である。
抑制すべきは「同じ内容に対する同じ判断の繰り返し」であって、「内容が変わった後の再提案」まで
抑制すると「1回 accept したら二度とそのファイルへ提案が来ない」という別の欠陥を生む。
**帰結**: #446 の修正対象は reject 側のみでよく、accept 側の ID 再生成ロジックには一切手を
入れない。

**解釈B（当初案）は独立の仕組みとして不要と判明した**（codex round1 [Must]1・[Must]4 を反映。
初版で示した「`(repo_id, relative_path)` の粗い抑制キー + accept を検知して早期解除する」
という2段構えの設計は撤回する）。抑制キーを `proposal_id`（= `(repo_id, relative_path,
before_sha)`、§3.1）に訂正した結果、reject 後にファイル内容が変わる（typically は accept
による適用）と `before_sha` が変わり、次回 emit は別の `proposal_id` を生成する——その
新しい ID は ledger に reject 記録が無いので抑制されない。

**ただし before_sha ベースの解除は「実質的な変化」の完全な代理指標ではない**（2026-08-14
round2 [Should]3 訂正: 旧稿の「自動的に起きる」は before_sha 変化＝解釈Bの要件充足、と
言い切っていたが正確ではない）。`before_sha` はファイルの**バイト単位の全内容ハッシュ**
なので、コメント修正・空白調整・無関係な別編集など、人間が「意味のある変化」と見なさない
差分でも解除される（過剰解除側の誤差）。逆に、ファイルが一切変わらないまま指摘内容の
解釈だけが変わるようなケース（discover pattern の変化等・§3.1 参照）は本来は「別内容」
だが `before_sha` は不変のままなので抑制が続く（過小解除側の誤差）。したがって
before_sha の変化は「解釈Bが本来意図した“実質的な変化”検知」の**粗い近似**であり、
完全な代替ではない。それでも: ① 実装が単純（既存の identity 計算をそのまま使い回す）
② 誤差の方向（過剰解除・過小解除）はどちらも致命的ではない（過剰解除＝抑制が早く解ける
＝最悪でも余分な y/n 提示が1回増えるだけ、過小解除＝TTL 45日で結局解ける）ため、
専用の早期解除ロジックを別途持つコストに見合わないと判断する（§2.3 末尾・§3.2 参照）。

`optimize_history` を読んで「reject より新しい accept があるか」を照合する専用ロジックは
持たない（削除）。理由は3点: ① before_sha の変化と accept 検知という**二重の解除経路**を
持つと、実際にどちらが効いたか判別しにくくなる ② `load_effective_history()` は「その
path の最新イベント種別」を直接返す形になっておらず、reject entry には path 自体が無く・
revert イベント自体や revert 済み accept は `fold_effective()` で除外される
（`optimize_history_store.py:292-314`）ため「path 単位の最新状態」を安全に再構成する専用
ロジックが別途必要になる ③ ①②のコストに見合う効果が無い（before_sha 変化だけで同じ結果が
得られる）。

**帰結**: #446 の修正は reject 側の抑制ロジック追加のみで完結する。accept 側・`before_sha`
の計算ロジック・`optimize_history` の読み方には一切手を入れない。

### 2.4 実データでの規模（read-only 実測・`~/.claude/evolve-anything/`）

```
$ optimize_history/*.jsonl の human_accepted==false 件数（3ファイル走査）
  evolve-anything.jsonl: total=38 reject=0
  receipt.jsonl:          total=1  reject=0
  sys-bots.jsonl:         total=2  reject=0
  合計: total=41 reject=0

$ advisory_decisions.jsonl
  exists: False（advisory レーンは decision 記録自体が一度もない）

$ evolve_decisions/*.jsonl（現在の pending 件数）
  _unattributed.jsonl: 0 / docs-platform.jsonl: 0 / evolve-anything.jsonl: 3 /
  rl-anything.jsonl: 0 / sys-bots.jsonl: 2
```

**実測結論**: 本環境では reject 記録が現時点で **0 件**（skill_diff/skill_evolve/advisory の
いずれも）。したがって「現在の queue の中に reject 済みなのに再提示されている提案」は
**実測 0 件**。バグは実コード確認で構造的に確定しているが、**このリポジトリではまだ発火して
いない**（reject 操作自体がまだ一度も行われていないため）。次に人間が1件でも reject すると
発火する潜在バグ、として扱う。

**副次発見（実測）**: `evolve_decisions/evolve-anything.jsonl` の pending 3件は `repo_id`
キー自体が存在しない旧スキーマ（`#402` 導入前の残留 entry）だった（`skill_path`/`before_sha`/
`fitness_func`/`id`/`pattern`/`proposal_type`/`skill_name` の7キーのみ、`repo_id`/
`relative_path`/`scope`/`resolved_path`/`revert_*` を一切持たない）。**新しい抑制ロジックは
`repo_id`/`relative_path` が entry に存在しない場合を必ず考慮する**（fallback は §3.1）。

## 3. 設計

### 3.1 抑制の単位 と emit/ingest 側の実装契約（2026-08-14 改訂: codex [Must]1/[Must]2/[Must]5/[Must]6 を反映）

**抑制キー = `proposal_id`（`entry["id"]`）そのもの**（レーン共通。codex [Must]1 を反映し、
旧稿の `(repo_id, relative_path)` への粗粒度化は撤回する）。

**なぜ粗粒度化を撤回したか**: `_extract_candidates()` が同一 `skill_path` を1件に畳むのは
**同一 run 内**だけである（`_candidates.py:90,100-102`）。reject 後に手動編集・discover
pattern の変化・別 run の再検出が起きれば、同じ path に**別内容**の提案が実在しうる。
`before_sha` を含む既存の `proposal_id` をそのまま抑制キーに使えば、reject 後に内容が
不変なら次回も同じ ID になり抑制が効く（#446 の本旨どおり）一方、内容が変われば別 ID に
なり自然に抑制対象から外れる（§2.3 で述べたとおり、解釈Bが求めていた「実質的な変化での
解除」を専用ロジック無しで実現する）。

`entry["id"]` は emit の最も早い段階で計算され（skill_diff/skill_evolve: `_emit.py:192`
の `proposal_id_from_identity(identity, before_sha)`。advisory: `_candidates.py:57` の
`proposal.id`、detector+相対targets ベースで before_sha 非依存）、**旧スキーマの pending
entry（§2.4 実測: `repo_id`/`relative_path` を持たない entry）にも必ず存在する**。
したがって抑制キーの計算に `repo_id`/`relative_path` の解決可否は一切影響しない
（codex [Must]6 が懸念した「reject 記録時の `repo_identity()` 再導出」は構造的に不要になる
——旧スキーマ問題は §3.1-b の「表示用ラベル」にのみ残る非機能要件）。

**レーン間の意味差（codex round1 [Should] / round2 [Should]2 への回答。
2026-08-14 round2 訂正: advisory ID の内容依存性についての記述を修正）**:
skill_diff/skill_evolve の `proposal_id` は対象ファイルの **`before_sha`**（バイト単位の
全内容ハッシュ）を含むため、ファイルへのどんな変更も新しい ID を生む——**内容世代単位**。
一方 advisory の `proposal_id`（`AdvisoryProposal.id` = `_proposal_id(detector_id,
target_paths, evidence)`、`advisory_proposals.py:40-54`）は **detector_id + target_paths +
evidence のハッシュ**であり、旧稿が書いた「内容世代非依存」は不正確だった——`evidence` は
各 detector が選んだ構造化フィールド（例: `invalid_frontmatter` は `{"skill_name",
"error"}`、`testpaths_coverage` は `{"declared_testpaths", "uncovered_test_dirs"}`、
`advisory_proposals.py:72-99`）であり、これらの値が変われば `proposal_id` も変わる。
つまり advisory レーンにも「内容が変われば ID が変わり抑制が自然に解ける」経路は**部分的に
存在する**。

**それでも非対称は残る（ここが reject 抑制上の実害）**: `evidence` は detector が選んだ
**代表フィールドのみ**であり、ファイルの全内容とは無関係に決まる粗い信号である。
対象の問題が実質的に変化しても evidence の値（例えば `error` メッセージの文言、
`uncovered_test_dirs` の集合）が**たまたま変わらない**限り `proposal_id` は変わらず、
reject 抑制は TTL（45日）まで解除されない。skill レーンは「ファイルの任意の変更」で
確実に解除されるのに対し、advisory レーンは「detector が拾う特定フィールドの変更」でしか
解除されないという**確率的な非対称**が残る。**この非対称は許容する**（advisory 側に
別途 accept/対象消滅ベースの早期解除を追加しない。§5 論点1で判断根拠と harm を明示する）。

#### 3.1-a データフロー（2箇所への挿入。単一の filter chokepoint）

```
emit 側（_emit.py）:
  pending 確定（advisory マージの try/except ブロック完了直後 = _emit.py:224 の
  `pass` の後、`persisted = False`（_emit.py:226）より前。codex round2 [Nit]1 で
  `:217` から訂正——§2.1 参照）
    → suppression.filter_rejected(pending, slug=slug) を呼ぶ
    → 戻り値 kept を後続のキュー書込（_emit.py:239-250）・マーカー書込（_emit.py:257-269）
      の両方に使う（元の pending でなく kept を使うことで二重 writer 問題を避ける・§2.1 と同じ
      chokepoint 設計）
    → 戻り値 stats を emit_decisions() の返り値へ meta キーとして追加（§3.3）

ingest 側（_ingest.py の判断ループ、_ingest.py:83-149）:
  **両レーンの合流点**（_ingest.py:149 の
  `(accepted_out if kind == "accept" else rejected_out).append(pid)` の直前。
  codex round2 [Must]3 で訂正——advisory 分岐は `_record_advisory_event(...)`
  （_ingest.py:112-114）のみを呼び `record_evolve_diff_decision` を呼ばないため、
  旧稿の「`record_evolve_diff_decision` を呼んだ直後」という挿入位置は skill レーン
  にしか到達せず、advisory の reject が記録漏れになる。`kind` の判定
  （accept/reject/skip の分岐、_ingest.py:102-110）そのものは両レーン共通で
  行われているので、その結果を使った後・両レーンの分岐（_ingest.py:112-148）が
  完全に終わった直後が唯一の共通到達点）:
  `kind == "reject"` のときだけ
    → suppression.record_pending_rejection(entry, slug=slug) を呼ぶ
    → 失敗（戻り値が非 None）でも pid を rejected_out へ積む処理・キュー消化は続行する（§3.1-c）
    → 失敗はリストに集め、ingest_decisions() の返り値へ meta キーとして追加
```

#### 3.1-b 新設する薄い adapter（`remediation.suppression_ledger` は無改造のまま呼ぶだけ）

codex [Should]「`filter_suppressed()` は既存 remediation 用の汎用関数なので、evolve固有の
早期解除やmetadata取得を無理に載せるより、ledgerの `load_ledger()` と `dedup_key()` を使う
薄い evolve adapter を既存 `evolve_decisions` 内に置く方が契約が明瞭」を第一候補として採用する。

**なぜ `filter_suppressed()` をそのまま呼べないか（codex [Must]2）**: `filter_suppressed()`
は **issue dict を受け取り issue dict を返す**契約（`suppression_ledger.py:202-228`）。
pending entry ↔ issue の対応を呼び出し側で復元する処理が別途要り、順序・重複の保持を保証
する仕組みも既存関数には無い。`load_ledger()`（生の dict 読み取り・副作用なし）と
`dedup_key()`（キー計算・副作用なし）という**2つのプリミティブだけ**を借り、pending entry の
形のまま evolve 側で判定する adapter を書く方が対応関係が明瞭になる。

新設場所: `scripts/lib/evolve_decisions/_suppression.py`（`_emit.py`/`_ingest.py`/
`_candidates.py` と同じ private submodule 規約。#379 が禁止するのは新 store/section/
advisory adapter/weak_signal channel であり、新しい `.py` モジュールの追加そのものは対象外・
codex [Nit] で非抵触が確認済み）。

契約（実装時のための擬似コード。型注釈・import 位置は実装時に整える）:

```python
def _issue_for(entry: Dict[str, Any]) -> Dict[str, Any]:
    """pending entry から dedup_key() 用の issue dict を組み立てる（副作用なし）。

    detail.target = entry["id"]（= proposal_id）がキーの一意性を担保する唯一の成分
    （dedup_key() は detail の "target" キーを採用する既存契約・
    suppression_ledger.py:90 の allowlist に含まれる）。

    **file は entry 由来にせず lane 固定のリテラル文字列にする**（codex round2
    [Must]1: `dedup_key()` は `type + file + detail` を**丸ごとハッシュ**する
    （`suppression_ledger.py:78-96`）ため、file が entry ごとに変わると同じ
    `proposal_id`（= detail.target）でも dedup_key が変わってしまい、抑制が
    成立しなくなる。旧稿の「file はデバッグ用の表示ラベルに過ぎず一意性に
    寄与しない」は誤りだった——dedup_key() の実装は file もハッシュ入力に
    含めるため、file の変動は一意性に**直接**寄与する。`record_rejection` が
    永続化するのは `{dedup_key, type, file, decided_at, ttl_days}` のみで
    元の entry 情報（repo_id/relative_path/skill_path 等）は保存されないため、
    file を固定値にしても失う情報は無い（旧スキーマ entry の repo_id/
    relative_path 欠落を fallback で埋める必要自体が消える）。
    """
    proposal_type = entry.get("proposal_type") or "unknown"
    issue_type = "advisory" if proposal_type == "advisory" else "evolve_diff"
    return {
        "type": issue_type,
        "file": "evolve_decisions",  # lane 固定リテラル。entry 由来の値を混ぜない（[Must]1）
        "detail": {"target": entry["id"]},
    }


def filter_rejected(
    pending: List[Dict[str, Any]], *, slug: str, now: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """reject 抑制中の候補を pending から除外する（順序保存・fail-open）。

    fail-open の境界を**3段に分離**する（codex round2 [Must]2:
    「ledger 全体が読めない」と「個別候補のキー計算が壊れる」は原因も影響範囲も
    別物なので、同じ except 節に混ぜず独立させる）:
      ① ledger 読み込み（`load_ledger()`）失敗 → **全件そのまま通す**（lane 全体は
         落とさない。読めない以上どの候補が抑制対象か判定できないため）
      ② 個別候補のキー計算（`_issue_for()`/`dedup_key()`）失敗 → **その候補だけ**
         抑制しない（他候補の判定は継続する。①とは try のスコープが違うので、
         ある候補の entry 形不正が他候補やレコード全体の読み込みを巻き込まない）
      ③ 個別レコードの `decided_at`/`ttl_days` が不正値 → **その候補だけ**抑制しない
         （既存どおり。②とはさらに別スコープ——②は「キーが引けるか」、③は
         「引けたレコードの値が正しいか」で失敗の意味が異なる）

    Returns: (kept_pending, stats)
      stats = {
        "suppressed_total": int,
        "suppressed": [{"id": str, "file": str}, ...],  # silence != evaluated 用
        "ledger_read_error": str | None,  # ① の失敗時のみ非 None
        "candidate_errors": [{"id": str, "error": str}, ...],  # ② の失敗一覧（空なら []）
      }
    """
    from remediation.suppression_ledger import DAY_SECONDS, DEFAULT_TTL_DAYS, dedup_key, load_ledger

    stats: Dict[str, Any] = {
        "suppressed_total": 0,
        "suppressed": [],
        "ledger_read_error": None,
        "candidate_errors": [],
    }
    try:
        ledger = load_ledger(slug)
    except (OSError, UnicodeDecodeError) as e:
        # ①: ledger 自体が読めない。抑制判定の材料が無いので全件そのまま通す。
        stats["ledger_read_error"] = f"{type(e).__name__}: {e}"
        return list(pending), stats

    now = now if now is not None else time.time()
    kept: List[Dict[str, Any]] = []
    for entry in pending:
        try:
            issue = _issue_for(entry)
            record = ledger.get(dedup_key(issue))
        except (AttributeError, KeyError, TypeError) as e:
            # ②: この候補のキー計算だけが失敗。他候補の判定には影響させず、
            # この候補は抑制しない側に倒す（過剰抑制＝指摘が黙って消える、を避ける）。
            stats["candidate_errors"].append(
                {"id": entry.get("id"), "error": f"{type(e).__name__}: {e}"}
            )
            kept.append(entry)
            continue
        if record is None:
            kept.append(entry)
            continue
        try:
            decided_at = float(record.get("decided_at", 0.0))
            ttl_days = int(record.get("ttl_days", DEFAULT_TTL_DAYS))
            suppressed = now <= decided_at + ttl_days * DAY_SECONDS
        except (TypeError, ValueError):
            # ③: レコードの値が壊れている。候補単位で「抑制しない」に倒す。
            suppressed = False
        if suppressed:
            stats["suppressed_total"] += 1
            stats["suppressed"].append({"id": entry["id"], "file": issue["file"]})
        else:
            kept.append(entry)
    return kept, stats


def record_pending_rejection(
    entry: Dict[str, Any], *, slug: str, now: Optional[float] = None,
) -> Optional[str]:
    """reject された pending entry を suppression ledger に記録する。

    例外を投げない契約（呼び出し側 _ingest.py の主フロー — 判断記録とキュー消化を
    絶対に止めない・§3.1-c）。**`_issue_for()` の呼び出し自体も try に含める**
    （codex round2 [Must]2: 旧稿は `_issue_for()` を try の外で呼んでおり、entry の
    形が想定外だとここで未捕捉例外が上がって `_ingest.py` の reject 処理全体を
    落としかねなかった）。`now` は本関数自体では未使用（`record_rejection` が
    内部で `decided_at` を決める）だが、契約テストで `decided_at` を決定論に
    固定するための注入経路として公開する（codex round2 [Should]1）。
    戻り値は失敗時のみエラーメッセージ文字列、成功時 None。
    """
    from remediation.suppression_ledger import DEFAULT_TTL_DAYS, record_rejection

    try:
        issue = _issue_for(entry)
        record_rejection(
            issue, slug=slug, now=now, ttl_days=DEFAULT_TTL_DAYS, persist=True,
        )
        return None
    except (
        OSError,
        UnicodeDecodeError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        return f"{type(e).__name__}: {e}"
```

**fail-open の全経路（codex round1 [Must]5 / round2 [Must]2 への対応・網羅表。
3つの try スコープを独立させた結果が下表）**:

| 失敗箇所 | 境界 | 契約 |
|---|---|---|
| `load_ledger()` の `read_text()` が `OSError`/`UnicodeDecodeError` | ① lane 全体 | `filter_rejected` 全体が fail-open（**全件そのまま通す**）。`stats.ledger_read_error` に記録 |
| `_issue_for()`/`dedup_key()` が `AttributeError`/`KeyError`/`TypeError` | ② 候補単位 | その**候補だけ**抑制しない。`stats.candidate_errors` に記録。他候補・ledger 読み込み自体には影響しない |
| 個別レコードの `decided_at`/`ttl_days` が不正値 | ③ 候補単位 | その**候補だけ**抑制しない（他候補は通常判定を続行）。lane 全体は落とさない |
| `record_pending_rejection()` 内（`_issue_for()` 呼び出し込み）の失敗 | 候補単位（ingest 側） | 例外を外に投げない（[Must]2: `_issue_for()` も try の内側）。呼び出し元（`_ingest.py`）は reject 処理（判断記録・`rejected_out` への追加・キュー消化）を**続行**する（§3.1-c） |

**明示的に禁止する実装（codex round1 [Must]5 後段）**: `filter_rejected` の呼び出しを、
advisory 収集の既存 `try: ... except Exception: pass`（`_emit.py:218-224`）の
**内側に混ぜない**。これをやると suppression フィルタの失敗が advisory 候補収集の
失敗と同じ扱いになり、advisory 候補が**全件**（抑制対象でないものまで）消える。
`filter_rejected` は advisory 収集ブロックが完了した**後**（`_emit.py:224` の `pass`
より後、`persisted = False`（`_emit.py:226`）より前）、それ自身の fail-open 契約
（上表）の下で独立に呼ぶ（挿入位置の確定は §3.1-a・codex round2 [Nit]1）。

#### 3.1-c reject 記録とキュー消化の順序（codex round1 [Should] / round2 [Must]3 への対応）

`_ingest.py` の現行フロー（`_ingest.py:83-149` のループ→`_ingest.py:151-159` のキュー消化）は
「各 entry の判断記録 → ループ完走後にキュー消化」の順で、判断記録とキュー消化は**既に別
フェーズ**になっている。`record_pending_rejection` は §3.1-a で確定した**両レーンの
合流点**（`_ingest.py:149` の `.append(pid)` 直前。advisory/skill いずれの分岐を
通った entry でも到達する）に置く（＝ループ内・キュー消化より前。旧稿の「`recorder(...)`
呼び出しの直後」は skill レーンの分岐内部にしかなく、advisory レーンの reject を
取りこぼす誤りだった——codex round2 [Must]3）。これにより:

- ledger 書込が失敗しても、各レーンの source of truth（skill: `record_evolve_diff_decision`
  ／advisory: `_record_advisory_event`）は合流点に到達する時点で既に成功済み（reject の
  記録自体は失われない。suppression ledger はあくまで派生キャッシュで、source of truth
  とは別物という位置づけは両レーンで共通）
- キュー消化（`rejected_out` に積まれた pid の除去）は ledger 書込の成否に関わらず実行する
  （ledger はあくまで「抑制のための派生キャッシュ」であり、キュー消化を止める理由にしない —
  止めると reject 済みの entry がキューに残留し続け、別の欠陥を生む）
- ledger 書込失敗時は該当 entry を `ingest_decisions()` の返り値の新キー
  `suppression_ledger_errors: [{"id":..., "error":...}, ...]`（新セクション禁止・既存
  dict へのキー追加のみ）に積み、`bin/evolve-daily-run` の既存サマリ行に1文足す（§3.3）

### 3.2 TTL / 解除条件（2026-08-14 改訂: codex [Must]3 を反映）

**TTL = 45日**（`triage_ledger.DEFAULT_TTL_DAYS` / `remediation.suppression_ledger.
DEFAULT_TTL_DAYS` と同値。プロジェクト全体の TTL 慣習——weak_signals・judge cutoff 案——と
揃える。新しい定数を発明しない）。

**TTL 経過後の挙動を訂正する（旧稿の誤りを削除）**: `load_ledger()`/`filter_rejected` は
**読み取り専用**であり、TTL 判定は「今の時刻が `decided_at + ttl_days` を超えているか」を
毎回計算するだけである（`remediation.suppression_ledger.is_suppressed`/`filter_suppressed`
と同じ判定式・§3.1-b の擬似コード参照）。TTL を超えたレコードは ledger 上に残ったままだが
「抑制しない」と判定されるだけで、**そのレコードを消したり「1回だけ再提示してから再度
抑制する」ような状態遷移は一切起きない**（旧稿の「TTL 経過後は1回だけ再提示する」
「追加実装不要」は実装と異なる誤った記述だったため削除する）。正しい仕様:

- TTL 経過後は**毎回**（再び reject されるまで）提示される
- 再び reject されれば `record_pending_rejection` が同じキーで新しい `decided_at` を
  upsert し（`suppression_ledger.py:130-134` の `_upsert` は append-only・読み取り時
  last-write-wins）、そこから改めて45日抑制される

**早期解除の専用ロジックは持たない**（§2.3 参照。抑制キーが `proposal_id`（before_sha 込み）
である以上、ファイル内容が変われば新しい ID になり抑制は解除される——ただし §2.3 末尾で
訂正したとおりこれは「実質的な変化」の粗い近似であって完全な代替ではない。`optimize_history`
を読んで accept を検知する複雑な照合は、その近似を精緻化するコストに見合わないため不要・
削除する）。

### 3.3 silence != evaluated の担保（2026-08-14 改訂: §3.1 の stats 契約と一致させる）

新しい observability section は作らない（#379 抵触）。**既存 emit/ingest 結果 dict への
キー追加のみ**:

- `emit_decisions()` の返り値（`_emit.py:277-292`）に `filter_rejected` の `stats`
  （§3.1-b）をそのまま展開する: `reject_suppressed_total: int` /
  `reject_suppressed: [{"id":..., "file":...}, ...]`（0件でもキーは出す・空リスト） /
  `suppression_ledger_read_error: str | None`。既存の `revert_generation_discarded` と
  同じ「新セクションでなく meta 返却」パターン。advisory/skill 両レーンの抑制が同じリストに
  混在するが、`id` の prefix（advisory は `advisory_` 系、skill は `evdiff_` 系・既存の
  ID 採番規約のまま）でレーンを判別できるため、追加のレーン別内訳フィールドは持たない
- `ingest_decisions()` の返り値（`_ingest.py:161`）に `suppression_ledger_errors:
  [{"id":..., "error":...}, ...]`（§3.1-c）を追加する
- 表示は `bin/evolve-daily-run` の既存サマリ行（`revert_generation_discarded` を出している
  行の近傍）に1文足すだけ（新規セクション禁止・既存行への追記）。0件のときは追記しない
  （既存の「0件ならノイズを足さない」流儀に合わせる）

### 3.4 #379 非抵触の確認

- 新 store: **0**（案B採用時。既存 `remediation_suppression/<slug>.jsonl` を再利用）
- 新 observability section: **0**（既存 emit/ingest 結果 dict へのキー追加のみ・§3.3）
- 新 advisory adapter: **0**
- 新 weak_signal channel: **0**（本 issue は weak_signals と無関係）
- `shrink_freeze.assert_no_new_keys` へは抵触しない（`store_registry`/observability builder/
  advisory adapter/weak_signal channel のいずれの live 集合にも新規キーを増やさない）。
  codex レビューでも「案B自体は新 store・observability section・advisory adapter・
  weak_signal channel を増やさず、`shrink_freeze.py` の frozen 集合上は非抵触」と確認済み
  （codex [Nit]）。`scripts/lib/evolve_decisions/_suppression.py` という新規 `.py` モジュール
  の追加自体は #379 の対象外（禁止対象は store/section/adapter/channel の4種のみ）

## 4. やらないこと（スコープ外）

- **`triage_ledger` の store 未登録問題の是正**（§2.2 で見つかった別の欠陥。本 issue とは
  無関係の pre-existing gap。別 issue で記録のみ推奨）
- **`before_sha` を含む識別子体系そのものの見直し**（#417 の identity 定義自体は変えない。
  抑制キーは既存の `proposal_id` をそのまま使い、新しい識別子は発明しない・§3.1）
- **`optimize_history` の reject entry スキーマ拡張**（§3.1 の案A。§6 裁定1のとおり案Bを
  採用したため実施しない）
- **advisory レーンへの早期解除機構の追加**（accept・対象消滅・内容変更を検知して抑制を
  解く仕組み。§3.1 で述べた非対称を許容する裁定・§5 論点1）
- **remediation レーン自体の抑制ロジック変更**（`suppression_ledger.py` の中身は無改造で
  そのまま呼ぶだけ）
- **advisory レーンの新規フィールド追加**（§2.2 の発見どおり不要）
- **`(repo_id, relative_path)` 単位の粗い抑制**（初版で検討したが codex [Must]1 により撤回。
  §3.1 参照）

## 5. 未解決の論点

初版にあった論点のうち、抑制キーの粒度（旧論点3）・早期解除機構の要否（旧論点2）は
codex [Must]1/[Must]4 により**論点ではなく確定事項になった**（§2.3・§3.1・§3.2 参照。
「粗い抑制＋早期解除」の2段構え自体が撤回され、`proposal_id` 単位の抑制に一本化された
ため、選ぶ余地がなくなった）。案(A) vs 案(B)（旧論点1）・TTL 45日（旧論点4）は
オーケストレーターが既に裁定済み（§6）。残る未解決の論点は以下の1件のみ。

**論点1（codex round1 [Should] / round2 [Should]2 由来）: advisory レーンの
「evidence が変わらない限り抑制が続く」非対称を許容するか**
→ §3.1 で訂正したとおり、advisory の `proposal_id` は detector_id + target_paths +
evidence のハッシュであり内容依存ではあるが、evidence は detector が選んだ**粗い代表
フィールドのみ**（`advisory_proposals.py:40-54`, `:72-99`）なので、対象の実質的な状態が
変わっても evidence 値が変わらなければ `proposal_id` は変わらず、reject 後は最大45日間
抑制され続ける（skill レーンはファイル全体の `before_sha` を ID に含むため、どんな変更でも
確実に ID が変わり自動解除される）。

**harm（round2 [Should]2 で追加）**: この非対称の実害は「対象が実際には直っていない
のに evidence だけたまたま変わって再提示される」誤検知ではなく、逆に**「evidence が
変わらない再発・regression が最大45日間サイレントに握り潰される」**方向である。
例えば `testpaths_coverage` の advisory が一度 reject された後、別の tests/ ディレクトリが
新たに未収集になっても `uncovered_test_dirs` の**集合が偶然一致**すれば evidence は
変わらず、新しい問題であっても抑制対象として消える。#376「数字が嘘をつかない」・
「silence != evaluated」の原則からは、この遅延は`stats.suppressed`（§3.3）で機械可読には
残るため完全な silence ではないが、**y/n 提示という人間が見る経路からは最大45日消える**
という非対称の性質は明示しておく必要がある。

- **選択肢A（推奨）: 現状のまま許容する**。理由: ① advisory の検出対象
  （`invalid_frontmatter`/`testpaths_coverage` 等・`_candidates.py:33-34` のコメント参照）は
  性質上「直しても構造的に同じ種類の指摘が再検出されやすい」ものが多く、evidence が
  たまたま変わった程度で安易に再提示すると reject の意図（「この指摘は要らない」）を
  裏切りやすい。② 45日という TTL 自体が「いずれ再評価の機会を与える」ための安全弁として
  既に機能し、しかも `stats.suppressed`（§3.3）で機械可読な記録は残るため genuinely
  silent ではない。③ 実装が単純（advisory 側の判定ロジックを一切変えない）
- **選択肢B: advisory にも「対象ファイルの sha が変わったら解除」を追加する**（`_advisory_pending`
  が既に `before_sha`（対象ファイルのsha256）を候補に持っている・`_candidates.py:67`）。
  ただし advisory の `proposal_id` 自体は before_sha を含まないため、抑制キー
  （`entry["id"]`）とは別に「対象ファイルの現在 sha」を ledger 側へ追加で持たせる必要があり、
  スコープが `remediation.suppression_ledger` の既存契約（issue の type/file/detail のみ）を
  超える。skill レーンと同様の複雑さを advisory 側にも持ち込むことになり、§3.1/§3.2 で削除した
  「早期解除ロジック」を advisory 版として復活させるに等しい

**ユーザー判断を仰ぐ**（初期実装は選択肢Aで進めることを推奨するが、選択肢Bのニーズが
明確なら follow-up issue で扱う）。

---

## 6. オーケストレーターの裁定（2026-08-14 制定 / 2026-08-14 codex レビュー反映で一部改訂）

§5（初版）の未解決4件を裁定した。いずれも**後戻りコストが小さい**ので暫定採用で着手し、
運用後に覆ったら差し替える方針だった（`~/.claude/rules/provisional-over-blocker.md`）。
その後 codex の着手前ゲート（`~/.claude/rules/design-review-gate.md`）で round1 が
`設計修正要`（[Must]6件）となり、下表のうち**2件を撤回**した（codex [Nit] のパス指摘を受け、
本節の rules 参照もグローバル `~/.claude/rules/` 配下の実在パスへ修正した）。round1 反映後の
差分レビュー（round2）も `設計修正要`（[Must]3件/[Should]3件/[Nit]2件）となり、下部の
「codex round2 の解消対応表」のとおり全件反映した。round2 反映後は
`~/.claude/rules/design-review-gate.md` の「レビュアーが条件を明示したら着手後にさらに
巡を重ねない」の運用に従い、3巡目のレビューは行わず実装へ進む。

| 論点 | 裁定 | 状態 |
|---|---|---|
| 1. 案(A) vs 案(B) | **案(B)（`remediation.suppression_ledger` 流用）** | **維持**。理由: `optimize_history` は fitness calibration の**母集団**であり、その均質性は既存の設計原則。抑制目的のフィールドを混ぜると「何のための行か」が濁る。加えて案(A) が触る `record_evolve_diff_decision` は `optimize.py` / `run_loop.py` とも共有され、影響が `evolve_decisions` に閉じない。命名の違和感（remediation という名の store に evolve_diff が入る）は `dedup_key` の一意性を `detail.target = proposal_id` が担保するため実害が無い（§3.1-b で確定） |
| 2. 早期解除条件 | ~~含める~~ → **撤回・機構自体を削除** | codex [Must]1/[Must]4 により、抑制キーを `proposal_id`（before_sha 込み）に訂正した結果、早期解除は before_sha の変化で自動的に達成されると判明した。`optimize_history` を読んで accept を検知する専用ロジックは不要（§2.3・§3.2） |
| 3. 抑制の粒度 | ~~`(repo_id, relative_path)` の粗い抑制~~ → **撤回・`proposal_id`（＝既存の内容世代単位識別子）に訂正** | codex [Must]1: `_extract_candidates` の「同一 path を1件に畳む」は**同一 run 内限定**であり、reject 後の手動編集・別 run の discover pattern 変化で同じ path に別内容の提案が実在しうる。粗い抑制はそれを45日間黙って握り潰す。`before_sha` を含めても reject 後に内容不変なら次回も同じ ID になるため抑制は成立する（§3.1） |
| 4. TTL 45日 | **45日で開始** | **維持**。他3機構と横並び。新しい定数を発明しない。頻度の実測ができていない（reject 実績 0件）ので、運用後に調整する前提。ただし TTL 経過後の**挙動の記述**は誤りだったため訂正した（§3.2: 「1回だけ再提示」ではなく「毎回提示、再rejectで45日更新」） |

### 実装の優先度についての注記

§2.4 の実測どおり **本環境の reject 記録は現時点で0件**（`optimize_history` 41件中 reject 0 /
`advisory_decisions.jsonl` は未存在）。つまり**バグは構造的に確定しているが、まだ一度も発火していない**。

ただし **PR #450（#444・`evolve --drain --rejected` の CLI 化）が 2026-08-14 にマージされた**ことで、
**これから reject が記録され始める**。それまで `--rejected` を渡す経路自体が無かったのが 0件の理由なので、
「未発火だから後回し」ではなく「**発火する直前だから今直す**」が正しい読み。

### 実装前に必ずやること

§2.4 で発見された **現 queue の旧スキーマ残留（`repo_id` キー欠落）** は、§3.1 で述べたとおり
**抑制キー（`entry["id"]`）の計算には一切影響しない**（旧スキーマでも `id` は必ず存在する）。
round2 [Must]1 反映後は `file` も entry 由来でなく lane 固定リテラルになったため、
旧スキーマの repo_id/relative_path 欠落は `_issue_for()` に一切影響しない（§3.1-b 冒頭）。
**この fail-open 契約（§3.1-b の表）を契約テストで固定すること**（過剰抑制は「ユーザーの
指摘が黙って消える」＝この PJ が最も嫌う挙動なので、判定不能なら必ず出す側に倒す）。

**実装時に併せて直すこと（codex round2 [Nit]2）**: `scripts/lib/remediation/
suppression_ledger.py` のモジュール docstring（9行目付近）と `DEFAULT_TTL_DAYS` の
コメント（48行目付近）に「TTL 経過後は1回だけ再 surface する」という**誤った**記述が
残っている（実装（`is_suppressed`、183行目付近）は §3.2 で確認したとおり「TTL 経過後は
毎回提示、再rejectで45日更新」が正しい）。この design doc 自体の §3.2 記述は round1
改訂で既に正しい記述になっている（変更不要）ので、直すのは `suppression_ledger.py`
側の2箇所のソースコードコメントのみ。ロジック自体は変更しない（コメントのみの修正）。

### codex [Must] 6件の解消対応表（2026-08-14 改訂で追加）

| codex 指摘 | 解消箇所 |
|---|---|
| [Must]1 抑制キーの粗粒度化を撤回し `proposal_id` にする | §3.1 冒頭「抑制キー = `proposal_id`」+ 本節の裁定3 |
| [Must]2 案Bは「そのまま流用」不可。pending↔issue adapter 契約が必要 | §3.1-b（`_issue_for`/`filter_rejected`/`record_pending_rejection` の擬似コード契約） |
| [Must]3 TTL契約の記述訂正（「1回だけ再提示」は誤り） | §3.2（「TTL 経過後の挙動を訂正する」の節） |
| [Must]4 早期解除判定を実装可能な契約へ、または削除 | §2.3・§3.2（`optimize_history` 参照ロジックを削除し理由を明記） |
| [Must]5 fail-open が全経路で保証されていない | §3.1-b「fail-open の全経路（網羅表）」+「明示的に禁止する実装」段落 |
| [Must]6 旧schema fallback は reject 記録時にも必要 | §3.1 冒頭（`entry["id"]` が旧スキーマでも必ず存在するため構造的に不要と判明）+ §3.1-b（`_issue_for` の `file` フォールバック） |
| [Should] advisory/skill レーンの意味差を明示 | §3.1「レーン間の意味差」+ §5 論点1 |
| [Should] reject記録とqueue消化の失敗順序 | §3.1-c |
| [Should] `dedup_key` 衝突（type だけでは不十分） | §3.1-b（`detail.target = entry["id"]` で一意性を担保） |
| [Should] `filter_suppressed` でなく薄い adapter を置く | §3.1-b（採用・`_suppression.py` 新設） |
| [Nit] #379 非抵触の確認 | §3.4 に codex 確認済みの旨を追記 |
| [Nit] rules 参照パスの実在確認 | 本節冒頭を `~/.claude/rules/` 配下の実在パスへ修正 |

### codex round2（2巡目）[Must]3件 / [Should]3件 / [Nit]2件 の解消対応表（2026-08-14 追加）

ログ: `codex_446_r2.log`。3件の [Must] はいずれもオーケストレーターが実コード
（`_emit.py`/`_ingest.py`/`suppression_ledger.py`/`advisory_proposals.py`）で裏取り済み。

| codex round2 指摘 | 解消箇所 |
|---|---|
| [Must]1 `_issue_for()` の `file` が entry 由来（repo_id/relative_path 等）で、`dedup_key()` は `type+file+detail` を丸ごとハッシュするため file の変動が一意性に直接影響する（旧稿の「file は表示ラベルに過ぎない」は誤り） | §3.1-b `_issue_for()` を書き換え、`file` を lane 固定リテラル `"evolve_decisions"` に変更。旧稿の誤った主張を削除 |
| [Must]2 `filter_rejected`/`record_pending_rejection` の fail-open 境界が「ledger 読み込み」と「個別候補のキー計算」を区別しておらず、`_issue_for()` が `record_pending_rejection` の try の外にあった | §3.1-b: `filter_rejected` を①ledger読込／②候補ごとのキー計算／③レコード値の3境界に分離し例外型を広げた（`UnicodeDecodeError`/`AttributeError`/`KeyError`/`TypeError`/`ValueError` を追加）。`record_pending_rejection` は `_issue_for()` 呼び出しを try 内側へ移動。fail-open 網羅表を3境界対応に更新 |
| [Must]3 reject 記録の挿入位置が `record_evolve_diff_decision`（skill レーンのみが呼ぶ）の直後になっており、advisory レーンの reject を取りこぼす | §3.1-a・§3.1-c: 挿入位置を両レーンの合流点（`_ingest.py:149` の `.append(pid)` 直前）へ訂正し、`kind == "reject"` ガードで両レーン共通に効くようにした |
| [Should]1 `now` 注入経路が `filter_rejected` にしかなく `record_pending_rejection` に無い（契約テストで `decided_at` を決定論固定できない） | §3.1-b: `record_pending_rejection(entry, *, slug, now=None)` を追加し `record_rejection(..., now=now, ...)` へ渡す |
| [Should]2 advisory の `proposal_id` は「内容世代非依存」ではなく `evidence` 込みでハッシュされる（`advisory_proposals.py:40-54`）。非対称の harm（regression のサイレント抑制）が未記載 | §3.1「レーン間の意味差」を訂正（evidence 込みの部分的内容依存を明記）+ §5 論点1に harm 段落を追加（evidence 不変な regression が最大45日 y/n 提示から消える） |
| [Should]3 「before_sha の変化が early-release を完全代替する」という言い切りが過剰主張 | §2.3 末尾・§3.2: before_sha は「実質的な変化」の粗い近似（過剰解除・過小解除の両誤差がある）であり完全代替ではない、と訂正。それでも専用ロジックを持たない判断根拠（誤差の方向がどちらも致命的でない）を追記 |
| [Nit]1 差し込み位置の行番号が `_emit.py:217` は誤り（advisory マージがまだ完了していない） | §2.1・§3.1-a: `_emit.py:224`（advisory マージの `except Exception: pass` 直後）に訂正 |
| [Nit]2 `suppression_ledger.py` のソースコードコメント2箇所（9行目・48行目付近）に旧稿と同じ「TTL経過後は1回だけ再提示」の誤記述が残っている | §6「実装前に必ずやること」に実装時修正タスクとして明記（design doc 自体の §3.2 記述は round1 で既に正しいので変更不要。直すのは `suppression_ledger.py` 側のコメントのみ） |
