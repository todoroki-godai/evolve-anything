# 467 設計ドラフト: 提案の種類を全て朝の y/n に到達させる（Stage 0 / Stage 1）

- 対象 issue: #467（Epic）
- 関連: #459（reader ゼロの blocking 検出・一般形）/ #379（新設凍結）/ #402・ADR-053（revert）/ ADR-054 §9.4
- 状態: **draft（着手前・codex 1巡待ち）**
- 前提コミット: `493c3173`（main）

---

## 0. この設計が答える問い

1. Stage 0（棚卸しの機械化）を #459 とどう合流させるか
2. Stage 1 のパイロットに**どの提案種別を選ぶか**（issue 本文の想定を実測で覆す提案を含む）
3. #379 凍結との衝突をどう解くか

---

## 1. 実測した現状（file:line 付き・2026-08-15）

### 1.1 生成側は7種、decision lane が読むのは2種だけ

`scripts/lib/discover/runner.py` が result に書く提案種別:

| envelope キー | 生成 file:line | 要素の構造 |
|---|---|---|
| `matched_skills` | runner.py:296 | `{skill_path, matched_skill, pattern, ...}` |
| `skill_evolve.assessments` | （skill_evolve 経路） | suitability high/medium のみ lane が採用 |
| `repeating_patterns` | runner.py:336 | tool_usage_analyzer 側（構造は**未確認**） |
| `pitfall_candidates` | runner.py:369（実体 `pitfall_manager/detection.py:201-206`） | `{title, root_cause, skill_name, source}` |
| `hook_candidates` | runner.py:374（実体 `discover/errors.py:75-107`） | `{type, pattern, full_message, count, suggestion, reason}` |
| `instruction_violations` | runner.py:443（`issue_schema.py:418-442`） | `{type, file, detail:{skill_name, instruction_text, correction_message, match_type, confidence, reason, needs_review}, source}` |
| `trajectory_skill_candidates` | runner.py:272（`_trajectory_candidates_to_missed` runner.py:109-168） | skill_extractor 候補（4軸分解つき） |

朝の y/n を組み立てる `_extract_candidates`（`scripts/lib/evolve_decisions/_candidates.py:79-125`）が読むのは:

- `phases.discover.matched_skills`（`_candidates.py:97-106`）
- `phases.skill_evolve.assessments`（`_candidates.py:109-123`）

の **2 キーのみ**。残り5種は生成されるが decision lane が一切参照しない（issue #467 の「孤児」の直接証拠）。

skill 提案の必須スキーマは `{skill_name, skill_path, pattern, proposal_type}`（`_candidates.py:103-106`）。

### 1.2 advisory 経路は「既存ファイル」を構造的に前提にしている

`_advisory_pending`（`_candidates.py:29-72`）は `advisory_proposals.collect_advisory_proposals()` の
`AdvisoryProposal.target_paths[0]` を `read_text()` し、**OSError なら `continue` で候補から落とす**
（`_candidates.py:51-54`）。「読めない対象は accept 判定できないので載せない」という設計。

apply 側 `ingest_decisions`（`evolve_decisions/_ingest.py:31`、中核 `:85-120`）も
`Path(tracked).read_text()` → `after_sha != before_sha` が accept 判定の前提。

→ **既存 lane は「既に存在するファイルの書き換え」しか表現できない。**

### 1.3 revert lane も同じ前提

| 事実 | file:line |
|---|---|
| `_SUPPORTED_SCOPES = ("global", "project")` | `evolve_revert/_availability.py:41` |
| 3分岐（normal / 冪等 / conflict）は current_sha と after/before_sha の比較 | `_apply.py:295-300` |
| `resolve_target` は `lstat()` 失敗＝`REASON_NOT_FOUND` で即失敗（「対象が無い＝削除成功」分岐なし） | `_target.py:77-80` |
| `_restore_normal` は `os.replace(tmp, target)` のみ＝**復元＝上書き**。削除分岐が未実装 | `_apply.py:131-222`（:211） |
| before_content は文字列前提。「ファイル不在」を表す sentinel が無い | `evolve_decision_ids.py:300-313` |

→ **「新規作成された提案を1コマンドで戻す」＝ファイル削除は、現状どこにも実装が無い。**

---

## 2. 判断1: #379 凍結との衝突は「起きない」（実測により訂正）

issue #467 本文は「`ADVISORY_PROPOSAL_ADAPTERS` が2キーに凍結されており、この経路で新種を足すと CI が赤くなる」
ことを前提に、凍結文言の絞り込み（「新検出器は禁止 / 既存孤児の接続は許可」）を提案していた。

実測すると **その前提が成り立たない**:

- 凍結の機械契約は `shrink_freeze.assert_no_new_keys(current, frozen, kind)`（`shrink_freeze.py:259-273`）で、
  対象は 4 定数のみ — `FROZEN_STORES`(:61-108) / `FROZEN_OBSERVABILITY_SECTIONS`(:111-158) /
  `FROZEN_ADVISORY_PROPOSAL_ADAPTERS`(:161-166) / `FROZEN_WEAK_SIGNAL_CHANNELS`(:171-180)
- `_extract_candidates` は `advisory_proposals` を**経由せず** `phases.discover.*` を直接読む独立経路
  （`_candidates.py:97-123`）

→ **`_extract_candidates` を拡張して孤児キーを読ませる限り、4 定数のどれも増えない＝凍結契約に触れない。**

### 採る方針

**凍結文言は変更しない。** 接続は `advisory_proposals` アダプタ追加ではなく、
`_extract_candidates` の入力拡張として実装する。

- 副次効果として「advisory adapter を足す」誘惑を断てる（#379 の上位意図＝行動につながらない advisory を増やさない、に整合）
- 凍結の文言変更というユーザー判断イベントを1つ消せる

**残るリスク**: 「凍結に触れないから何を足してもよい」という抜け道になりうる。
対策として Stage 0 の契約テスト（§3）で「接続対象の allowlist」を明示し、
そこへの追加が PR diff に現れるようにする。

---

## 3. Stage 0: 到達性の棚卸しを機械化する（#459 と合流）

### 3.1 合流の仕方 — 「同じ検査の2つの入力」にする

#459 は「envelope の新規 leaf key に production reader が無ければ Layer 2 を赤にする」。
#467 Stage 0 は「提案種別が朝の y/n に到達するか」。

**両者は同じ検査ではない。** 分けるべき2軸:

| 軸 | 問い | 由来 | 強制力 |
|---|---|---|---|
| reachability | その key を production の誰かが読むか | #459 | **blocking**（新規 key のみ・baseline で grandfathering） |
| decision-lane coverage | その key が朝の y/n に到達するか | #467 | **契約テスト**（接続済み集合を allowlist で固定） |

`hook_candidates` は #459 では「reader ゼロ」だが、#467 では「y/n 未到達」。
**#459 を先に入れると `hook_candidates` は grandfathering で見逃される**（baseline に既存キーとして載るため）。
つまり #459 だけでは #467 は閉じない。逆に #467 の接続を先に入れると #459 の baseline が1つきれいになる。

→ **依存順序: #467 Stage 1（接続）を先に進めても #459 と競合しない。#459 の baseline 生成は #467 の接続後に取る。**

### 3.2 Stage 0 の成果物

`scripts/lib/proposal_lane_coverage.py`（新規・store も observability section も作らない純関数モジュール）:

```python
PROPOSAL_KINDS = (...)          # 生成される提案種別の宣言（単一ソース）
LANE_CONNECTED = frozenset(...) # 朝の y/n に接続済みの種別（allowlist）
def audit_coverage(result: dict) -> list[dict]:  # 種別ごとに generated / lane_connected / reachable を判定
```

契約テスト（`scripts/lib/tests/test_proposal_lane_coverage.py`）:

1. `PROPOSAL_KINDS` が `discover/runner.py` の実 envelope キーと一致する（**新種を足して宣言を忘れたら赤**）
2. `LANE_CONNECTED` の各種別が `_extract_candidates` の実出力に現れる（**接続したと宣言して実装を忘れたら赤**）
3. `LANE_CONNECTED` に無い種別は「未接続」として明示的に列挙される（silence != evaluated）

Stage 3 で 3 を「全種別が `LANE_CONNECTED` に入っていること」へ強めて完了条件にする。

### 3.3 Layer 2 への配線

`scripts/lib/dogfood/invariants.py:183-188` の `_CHECKS` タプルに `(name, fn)` を1行追加する
（`fn(result) -> List[Dict[str,str]]`、空なら green）。red は `dogfood/cli.py:236-239` `_layer2_has_red`
→ `:297` で `exit_red` に加算される。**advisory（`cli.py:304-309` 型）にはしない** — #459 の議論どおり
非ブロッキング advisory は人間に届かない（`pre-push.local:21` が全出力を捨て `全緑 ✓` しか出さない）。

---

## 4. 判断2: Stage 1 のパイロットは `hook_candidates` ではなく `instruction_violations` を推す

issue #467 本文は「完全孤児で純増・既存表示と重複がない」ことを理由に `hook_candidates` を最有力としていた。
**実測すると `hook_candidates` は最も高コストな選択肢**である。

| 観点 | `hook_candidates` | `instruction_violations` |
|---|---|---|
| 孤児か | 完全孤児 | 完全孤児 |
| 対象ファイル | **新規 hook ファイル + settings.json**（存在しない） | 既存の `SKILL.md`（`detail.skill_name` / `file`） |
| 既存 lane に乗るか | **乗らない**（`_candidates.py:51-54` / `_ingest.py:85-120` が既存ファイル前提） | 乗る |
| 柱4（1コマンドで戻せる） | **revert lane の新規実装が必要**（§1.3 の4点全て） | 既存 revert がそのまま効く |
| パイロットで検証したいこと | 「接続の型」を検証したいのに、revert 拡張という別の大工事が混ざる | 接続の型だけを純粋に検証できる |

`design-before-fanout` は「1本目はパイロットとして通しレビュー完了まで走らせ、出た欠陥を
チェックリスト化してから残りを量産する」。**パイロットには最も型が素直な種別を選ぶべき**であり、
最も特殊な種別（新規ファイル作成）を選ぶのは順序が逆。

### 採る方針

- **Stage 1 パイロット = `instruction_violations`**（既存 SKILL.md への提案＝既存 lane・既存 revert がそのまま効く）
- **Stage 2 = `pitfall_candidates`（既存 pitfalls.md）→ `trajectory_skill_candidates`（新規作成）→ `hook_candidates`（新規作成 + settings.json）**
- 新規ファイル作成 revert（§5）は Stage 2 の前段で独立 PR として設計・実装する

### `instruction_violations` を y/n に出すときの品質ゲート

`detail` には `confidence` と `needs_review` がある（`issue_schema.py:418-442`）。
朝の負荷を増やさないため、**`needs_review=True` または confidence が閾値未満の候補は y/n に出さない**。
閾値は実データで較正する（合成 fixture では決めない＝`learning_synthetic_fixture_false_confidence`）。

---

## 5. 新規ファイル作成の accept / revert（Stage 2 の前提・本設計では方針のみ）

新規作成を lane に載せるには、最低限これらが要る（§1.2 / §1.3 の実測に対応）:

1. before_content に「ファイル不在」を表す sentinel を導入する（`evolve_decision_ids.py:300-313` の圧縮/復元が文字列前提）
2. `_candidates.py:51-54` の「読めない対象は落とす」を、**不在を許容する分岐**に広げる（誤って壊れたファイルを不在扱いしないこと）
3. `_ingest.py:85-120` の accept 判定（`after_sha != before_sha`）に「不在 → 存在」の遷移を加える
4. `_target.py:77-80` の `REASON_NOT_FOUND` に「削除済み＝冪等成功」分岐を足す
5. `_apply.py:131-222` に「復元＝削除」分岐（`os.replace` でなく `unlink`）を足す
6. `_availability.py:41` の `_SUPPORTED_SCOPES` 拡張の要否を判定する

**この6点は accept と revert の両側にまたがるので、独立 ADR にする**（ADR-053 の改訂ではなく後継）。

---

## 6. 未決定・ユーザー判断が要る点

1. `trajectory_skill_candidates`（新規スキル作成）を朝の y/n に出すか。1件あたりの人間コストが他種別より明確に高い
2. `repeating_patterns` は既に `rule_violation_lane.py` 経由の経路がある（到達可否未検証）。Stage 2 の対象に含めるか
3. 朝の y/n の**1日あたり上限**。5種類が全て到達すると件数が跳ねる（現状 `daily_review` は weak_signal を最大5件）

---

## 7. 未確認事項（この設計で埋めていないもの）

- `discover/enrich.py` の `matched_skills` 完全スキーマ（`_candidates.py` からの逆引きのみ）
- `repeating_patterns` 要素の dict 構造（tool_usage_analyzer 側 未読）
- `dogfood/cli.py:332` 以降の exit code マッピング詳細
- `evolve_decisions/_emit.py`（emit 側。今回読んだのは `_ingest.py`（drain/apply）のみ）
