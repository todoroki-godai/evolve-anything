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

**差し込み位置（単一ソース）**: `_emit.py:217`（`seen_ids = {entry["id"] for entry in pending}`の
直後、`pending` が確定した直後）が全 emit 経路（skill_diff・skill_evolve・advisory）を1回で通る
唯一の chokepoint。ここで `pending` をサプレッションフィルタに通せば、キュー書込
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

### 2.3 accept 後の再生成 — 両方の解釈

**解釈A（現状維持でよい）**: 「別の提案」。accept は実際にファイル内容を変えた行為であり、
新しい `before_sha` はその新しい世代に対する正当な新規識別子である。抑制すべきは
「同じ内容に対する同じ判断の繰り返し」であって、「内容が変わった後の再提案」まで抑制すると
「1回 accept したら二度とそのファイルへ提案が来ない」という別の欠陥を生む。
**帰結**: #446 の修正対象は reject 側のみでよく、accept 側の ID 再生成ロジックには一切手を
入れない。

**解釈B（accept を抑制解除のトリガーにする）**: reject 抑制を `(repo_id, relative_path)` 単位
（before_sha 非依存）で行う場合、同じ path に対する **accept** は「その path の状態が実質的に
進んだ」ことを意味するので、**その path に残っている reject 抑制レコードを早期に無効化してよい**
（TTL 満了を待たずに）。これは accept 側のコードを変えるのではなく、抑制側（reject 判定ロジック）
に「対象 path の最新 optimize_history イベントが reject より新しい accept なら抑制しない」という
1条件を足すだけで実現できる。

**両者は排他ではない**: 解釈Aは「ID 計算方式は変えない」という結論、解釈Bは「抑制判定に
accept を考慮する」という結論で、**両方採用可能**（推奨）。詳細は §5 未解決の論点2。

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

### 3.1 抑制の単位（レーン別）

| レーン | 抑制キー | 実装 |
|---|---|---|
| advisory | `proposal_id`（そのまま） | `advisory_decision_log.read_advisory_decisions(slug)` を emit 直前に1回読み、`proposal_id` ごとに直近の terminal decision を求める（`recorded_at` 最大）。`reject` かつ §3.2 の TTL 内なら emit から除外 |
| skill_diff / skill_evolve | `(repo_id, relative_path)`。**`before_sha` は含めない**（#446 の結論どおり。含めると reject 抑制として機能しない） | §3.1-b（下記）で新設が要るため2案を提示 |

`repo_id`/`relative_path` が entry に無い（§2.4 の旧スキーマ実測）場合のフォールバック:
`skill_path`（旧フィールド）から `evolve_decision_ids.repo_identity()` を呼び直して
`(repo_id, relative_path)` を導出する（`_emit.py` が候補ごとに毎回計算しているのと同じ関数。
新しい解決経路は作らない）。それでも解決できない（非 git など）場合は
**抑制しない（fail-open）** — 誤って過剰抑制するより、稀に再提示される方が安全側
（`is_orphaned_worktree` の「判定不能なら保守的に残す」と同じ設計判断）。

**skill_diff/skill_evolve の実装2案**（§2.2 の棚卸しに基づく）:

**案(A) — `optimize_history` の reject entry を拡張する**

`fitness_evolution.record_evolve_diff_decision` の `entry` 構築（`fitness_evolution.py:220-232`）
に `repo_id`/`relative_path` を無条件で追加する（現状 `revert_fields` は accept 限定だが、
この2フィールドは軽量な識別子であり `revert_before_b64`（本文・decision2 が本文を持たせない
理由）とは別物として扱う）。`_ingest.py:104-105` の reject 分岐でも
`entry.get("repo_id")`/`entry.get("relative_path")` を渡すよう1行追加する。emit 側は
`optimize_history_store.load_effective_history(slug)` を読み、`human_accepted is False` かつ
`(repo_id, relative_path)` が候補と一致する最新レコードを見る。

- 長所: 新規ストアもフィールド追加も**既存の唯一の判断台帳**に集約される。読み手が1箇所で済む
- 短所: `optimize_history` は fitness calibration の母集団（母集団の均質性が既存の設計原則）。
  抑制目的のフィールドを混ぜると「何のための行か」を汚す懸念。`record_evolve_diff_decision` は
  optimize.py/run_loop.py とも共有される関数（`_emit.py` 外の2 writer）なので、影響範囲が
  `evolve_decisions` だけに閉じない

**案(B) — `remediation.suppression_ledger` を流用する（推奨）**

`_ingest.py` の reject 分岐（`_ingest.py:104-105` 付近）で、`record_evolve_diff_decision` 呼び出しに
加えて `remediation.suppression_ledger.record_rejection(issue, slug=slug, ttl_days=45)` を呼ぶ。
`issue` は `{"type": "evolve_diff", "file": f"{repo_id}::{relative_path}", "detail": {"path": relative_path}}`
のように組み立てる（`dedup_key` が `type` を hash 入力に含む＝remediation 本来の dedup_key と
衝突しない）。emit 側は `remediation.suppression_ledger.filter_suppressed(candidates_as_issues, slug=slug)`
で `suppressed` を除外する。

- 長所: **新規ストア・新規フィールドとも不要**（`remediation_suppression/<slug>.jsonl` は
  既に `shrink_freeze.py:89` で登録済み）。TTL・再評価は実装済み・テスト済みのコードをそのまま
  使う。`optimize_history` の母集団を一切汚さない
- 短所: 「remediation」という名前の store に evolve_diff/skill_evolve 由来の抑制が混在する
  （命名が示す意味と実際の用途がズレる）。ただし `dedup_key` に `type` が入るため**実害
  （キー衝突）は無い**。将来 store を汎用名へ rename するなら別 issue

**推奨: 案(B)**。理由は上記に加え、#379 の「新設凍結」の趣旨（機能を増やさない）に最も忠実
（案Aも新規ストアではないが `record_evolve_diff_decision` という共有関数のスキーマを触る分、
影響範囲が広い）。命名の違和感は軽微な doc コメントで緩和できる。

### 3.2 TTL / 解除条件

**TTL = 45日**（`triage_ledger.DEFAULT_TTL_DAYS` / `remediation.suppression_ledger.DEFAULT_TTL_DAYS`
と同値。プロジェクト全体の TTL 慣習——weak_signals・judge cutoff 案——と揃える。新しい定数を
発明しない）。TTL 経過後は1回だけ再提示する（`remediation.suppression_ledger` の既存契約
そのまま。「強制的な TTL 経過後の1回だけの再評価」は追加実装不要）。

**早期解除条件（解釈Bの採用）**: emit 時、抑制判定の直前に対象 `(repo_id, relative_path)` の
`optimize_history` 最新イベントを見て、**reject より新しい accept があれば抑制しない**
（ファイルが実質的に進んだので新しい提案は正当）。この判定には `optimize_history_store.
load_effective_history(slug)` を「その path に対する最新イベントの種別と時刻」だけ見るために
使う（reject の本文は見ない・案Aの本文拡張は不要）。

### 3.3 silence != evaluated の担保

新しい observability section は作らない（#379 抵触）。**既存 emit 結果 dict への
キー追加のみ**: `emit_decisions` の返り値（`_emit.py:277-292`）に
`reject_suppressed_total: int` / `reject_suppressed_by_path: [{"repo_id":..,"relative_path":..,
"suppressed_until":..}]`（0件ならキーは出すが空リスト。既存の `revert_generation_discarded`
と同じ「新セクションでなく meta 返却」パターン）を追加する。表示は `bin/evolve-daily-run` の
既存サマリ行（`revert_generation_discarded` を出している行の近傍）に1文足すだけ（新規セクション
禁止・既存行への追記）。advisory レーンの抑制件数も同じキーに合算する（レーン別内訳は
`by_path` の `type` フィールドで判別可能にする）。

### 3.4 #379 非抵触の確認

- 新 store: **0**（案B採用時。既存 `remediation_suppression/<slug>.jsonl` を再利用）
- 新 observability section: **0**（既存 emit 結果 dict へのキー追加のみ・§3.3）
- 新 advisory adapter: **0**
- 新 weak_signal channel: **0**（本 issue は weak_signals と無関係）
- `shrink_freeze.assert_no_new_keys` へは抵触しない（`store_registry`/observability builder/
  advisory adapter/weak_signal channel のいずれの live 集合にも新規キーを増やさない）

## 4. やらないこと（スコープ外）

- **`triage_ledger` の store 未登録問題の是正**（§2.2 で見つかった別の欠陥。本 issue とは
  無関係の pre-existing gap。別 issue で記録のみ推奨）
- **`before_sha` を含む識別子体系そのものの見直し**（#417 の identity 定義自体は変えない。
  抑制は identity とは別レイヤーで行う）
- **reject 理由（`rejection_reason`）に基づく意味的な再提示判定**（「内容が変わったら
  再提示してよい」という粒度の細かい判定は行わない。§3.1 案Bの粗い path 単位が採用スコープ）
- **remediation レーン自体の抑制ロジック変更**（`suppression_ledger.py` の中身は無改造で
  そのまま呼ぶだけ）
- **advisory レーンの新規フィールド追加**（§2.2 の発見どおり不要）

## 5. 未解決の論点

**論点1: skill_diff/skill_evolve の抑制実装は案(A)か案(B)か**
→ 推奨は案(B)（§3.1）。案(A)の「単一台帳に寄せる」思想も一理あり、`optimize_history` の
読み手が増えることを厭わないなら妥当。**ユーザー判断を仰ぐ**。

**論点2: 早期解除条件（解釈B・§3.2）を初期実装に含めるか**
→ 含めない場合は実装がシンプルになる代わり、「reject 後すぐ手動で似た変更を accept した」
ケースで TTL 満了まで新しい提案が出ない（軽微な不便）。含める場合は emit 時に
`optimize_history` を1回余分に読む必要がある（性能影響は軽微・PJ 単位で高々数十行）。
**推奨: 含める**（実装コストが低く、ユーザー体験の悪化を防ぐ）が、初回実装をシンプルに保ちたい
なら見送って follow-up issue にしてもよい。

**論点3: 抑制単位の粒度（`(repo_id, relative_path)` だけでよいか）**
→ 同じ path に将来 **複数の異なる pattern**（例: skill_evolve 由来と discover 由来）が別々に
提案されうる。path 単位で一括抑制すると、reject したのと無関係な種類の新提案まで一緒に
抑制してしまう可能性がある。`dedup_key` の `detail` に `pattern` の先頭数十文字 or
`proposal_type` を足せば緩和できるが、粒度を細かくするほど「本当に同じ提案か」の判定が
曖昧になる（`pattern` の表記ゆれで別提案と誤認されるリスク）。**推奨: 初期実装は
path 単位の粗い抑制**（実装単純・実測 §2.4 で `pattern` は "skill_evolve:medium" のような
短い定型文字列であり、同一 path に複数 proposal_type が同時に競合するケースは現状の
`_extract_candidates`（同一 skill_path は discover 優先で1件に畳む・`_candidates.py:90,100-102`）
では起きない設計になっている）。将来 `proposal_type` が増えて粒度不足が顕在化したら
`detail` へ足す。

**論点4: TTL 45日は妥当か**
→ 他の3機構との横並びで45日を採用したが、evolve_diff の提案頻度（週次流入等）は
weak_signals/judge cutoff と桁が異なる可能性がある。**実測**: 本環境の evolve_diff pending は
現状5件・過去 reject 0件のため頻度実測ができていない（§2.4）。**ユーザー判断待ち**
（45日で開始し、運用後に短すぎる/長すぎるが分かれば調整する前提でよいか）。

---

## 6. オーケストレーターの裁定（2026-08-14）

§5 の未解決4件を裁定した。いずれも**後戻りコストが小さい**ので暫定採用で着手し、
運用後に覆ったら差し替える（`rules/provisional-over-blocker.md`）。

| 論点 | 裁定 | 理由 |
|---|---|---|
| 1. 案(A) vs 案(B) | **案(B)（`remediation.suppression_ledger` 流用）** | `optimize_history` は fitness calibration の**母集団**であり、その均質性は既存の設計原則。抑制目的のフィールドを混ぜると「何のための行か」が濁る。加えて案(A) が触る `record_evolve_diff_decision` は `optimize.py` / `run_loop.py` とも共有され、影響が `evolve_decisions` に閉じない。命名の違和感（remediation という名の store に evolve_diff が入る）は `dedup_key` に `type` が入るため**実害が無く**、doc コメントで足りる |
| 2. 早期解除条件 | **含める** | 実装コストが低く（emit 時に `load_effective_history` を1回読むだけ）、「reject 直後に手動で似た変更を accept したのに TTL 満了まで新提案が出ない」という体験の悪化を防ぐ |
| 3. 抑制の粒度 | **`(repo_id, relative_path)` の粗い抑制で開始** | `_extract_candidates` が同一 `skill_path` を1件に畳む（`_candidates.py:90,100-102`）ので、同一 path に複数 `proposal_type` が同時競合する状態は現設計では起きない。粒度を細かくすると `pattern` の表記ゆれで「別提案」と誤認するリスクの方が大きい。将来 `proposal_type` が増えて粒度不足が顕在化したら `detail` へ足す |
| 4. TTL 45日 | **45日で開始** | 他3機構と横並び。新しい定数を発明しない。頻度の実測ができていない（reject 実績 0件）ので、運用後に調整する前提 |

### 実装の優先度についての注記

§2.4 の実測どおり **本環境の reject 記録は現時点で0件**（`optimize_history` 41件中 reject 0 /
`advisory_decisions.jsonl` は未存在）。つまり**バグは構造的に確定しているが、まだ一度も発火していない**。

ただし **PR #450（#444・`evolve --drain --rejected` の CLI 化）が 2026-08-14 にマージされた**ことで、
**これから reject が記録され始める**。それまで `--rejected` を渡す経路自体が無かったのが 0件の理由なので、
「未発火だから後回し」ではなく「**発火する直前だから今直す**」が正しい読み。

### 実装前に必ずやること

§2.4 で発見された **現 queue の旧スキーマ残留（`repo_id` キー欠落）** は、§3.1 のフォールバック
（`skill_path` から `repo_identity()` を再導出・それも無理なら **fail-open で抑制しない**）で
吸収する設計になっている。**この fail-open を契約テストで固定すること**（過剰抑制は
「ユーザーの指摘が黙って消える」＝この PJ が最も嫌う挙動なので、判定不能なら必ず出す側に倒す）。
