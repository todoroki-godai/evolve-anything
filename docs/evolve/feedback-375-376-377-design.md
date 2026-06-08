# evolve フィードバック対応 設計（#375 / #376 / #377）

出典: sys-bots evolve session 2026-06-08 の手動フィードバック → issue #375 / #376 / #377。
本ドキュメントは3 issue・8修正のアーキテクチャを確定し、3PR に分割して実装するための plan artifact。

## 背景と核となる設計判断

- **#375** result JSON のキー名が doc（SKILL.md / references）と実装（evolve.py / reorganize.py）で乖離。
- **#376** `usage_count==0` のスキルを skill_evolve が medium（変換可能）と判定。使用実績の重みが効いていない。
- **#377** UX/構造の弱点5点（token見積過大 / hardcoded doc 誤検知 / per-item 承認の質問攻め / fitness 母集団导线 / self_analysis の盲点）。

**EUREKA**: #375・#376 は「歪みの実例」、#377-5 は「その種の歪みを自動検出したい」という同じ問題の2階層。
invariant（不変条件）を**1度だけ定義**し、契約テスト（#375）・assess ガード（#376）・self-detect（#377-5）の3者が
同じ定義を consume する構造にする。doc 手修正だけだと再ドリフトするが、この構造なら歪みが test で落ち、
将来分は evolve が自己申告する（DRY）。

## 全体構造（3PR）

```
P1: instances + invariant 層
  evolve_result_schema.py (新・1ソース)
    CANONICAL = [ KeyInvariant(path, kind, note), UsageSuitabilityInvariant() ]
      ├─► 契約テスト (result conforms)         … #375
      └─► assess の usage guard (==0→保留)      … #376
          │ 同じ invariant を import（DRY）
          ▼
P2: self-detect (evolve_introspect / evolve_consistency.py)  … #377-5
    _detect_consistency_drift(result):
      - canonical key 乖離を surface
      - usage↔suitability 矛盾を regression guard として残置

P3: UX群（invariant 非依存・独立）  … #377-1, #377-3, #377-4
別途: #377-2 は fix/359 ブランチに追補（残余ギャップ実証済み）
```

---

## P1 — #375 + #376（invariant 層）／本 PR で着手

### #375: result-schema contract

**根本原因**: SKILL.md/references が result のキー名を手書き → 実装からドリフト。
- `evolve.py:680-685`: `phases.remediation.proposable` は `len(classified["proposable"])` の**数値（件数）**。実体は `classified.proposable[]`。
- `reorganize.py:104-105`: split 候補は `skill_name` / `line_count`（doc が言う `.skill` / `.content_lines` は不在）。

**設計**:
1. 新規 `scripts/lib/evolve_result_schema.py`（~80行、予算内）に canonical キー一覧を1ソース化。
   ```python
   CANONICAL = [
     Key("phases.remediation.proposable",            kind=int,  note="件数。実体は classified.proposable[]"),
     Key("phases.remediation.classified.proposable", kind=list, item_keys=["type","file","confidence"]),
     Key("phases.reorganize.split_candidates",        kind=list, item_keys=["skill_name","line_count"]),
     # … remediation.auto_fixable / manual_required / proposable_custom / proposable_global ほか
   ]
   ```
2. 契約テスト `scripts/tests/test_evolve_result_schema.py`:
   - 既存 fixture result（test_evolve_integration の出力）に対し各 Key の path 存在 + kind 一致を assert。
   - SKILL.md 内の **コードフェンス / 明示 dotted path のみ**抽出し canonical 集合への membership を assert
     （散文は対象外＝precision 優先。prose パースの脆さを避ける）。
3. doc 修正: SKILL.md / references の誤キーを canonical 名に直す
   （`proposable[].target → classified.proposable[]`, `.skill → .skill_name`, `.content_lines → .line_count`）。

**右サイズ判断**: 散文まで検証すると脆い。構造化参照（code fence 内 dotted path）のみを test 対象にする。

### #376: usage_count==0 ガード

**根本原因**: `telemetry_scoring.py:18-24` で usage=0 でも frequency=**1**、`assessment.py` で total 6-9 → medium。usage ガード不在。

**DRY 警告**: 評価ロジックが2経路に重複 — `skill_evolve_assessment`（assessment.py:57、バッチ）と
`assess_single_skill`（:267）。両方に `scores→classify_suitability→anti-pattern→verification_bypass` が並走。

**設計**: 終端処理を1関数に抽出してから機能追加（make the change easy, then make the easy change）。
```python
# 新: _finalize_suitability(scores, suitability, skill_name, skill_dir, telemetry) -> (suitability, recommendation, flags)
#   既存の anti-pattern rejection + verification bypass をここへ集約
#   + 新ガード:
#     if telemetry["usage_count"] == 0 and not verification_bypass:
#         suitability = "insufficient_usage"          # 新区分
#         recommendation = "使用実績待ち（エラー蓄積後に候補化）"
# 両経路（:57 と :267）はこの helper を呼ぶだけに変更
```
- `classify_suitability` は純関数（score→high/med/low）のまま据え置き。usage は score に混ぜず post-classification で降格。
- SKILL.md: `insufficient_usage` を「保留（使用実績待ち）N件」として表示、proposable/変換可能の件数から除外。
- 検証系スキル（verification_bypass）は usage=0 でも medium 維持（既存契約 v1.13.0 を尊重）。

### P1 エッジケース / テスト

- remediation が error の時 result に classified が無い → invariant は optional path 扱い。
- dry-run でも result 構造は同一であることを test で固定。
- usage=0 + verification skill → medium 維持（バイパス優先）を test。
- 境界: usage=1 は従来どおり（==0 のみガード）。
- 全 PR で `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` + `claude plugin validate`。

---

## P2 — #377-5（self-detect、P1 の invariant を consume）

- 検出ロジックは新規 `scripts/lib/evolve_consistency.py` に置き、`evolve_introspect.py`（現719行）から import（肥大化回避）。
- `_detect_consistency_drift(result)`:
  1. `from evolve_result_schema import CANONICAL`（P1 と同一ソース）→ 実 result 上の kind 不一致を improvement_opportunities 候補化。
  2. assessments で `usage_count==0 かつ suitability∈{medium,high}` → 矛盾候補（P1 修正後は0件＝regression guard。split↔archive:88 と同パターン）。
  3. 各候補は既存 candidate 形（dedup_key/severity/suggested_label）に準拠。0件でも summary_line に「✓ 該当なし」。

---

## P3 — UX群（独立・invariant 非依存）

| # | 修正 | 設計 |
|---|------|------|
| 377-1 | token見積過大 | `assessment.py:146` の `estimated_tokens` を **cache-miss スキルのみ**で算出（llm_scoring の hash 突合を再利用）。`batch_guard_trigger` に `cache_hit_count`/`cache_hit_tokens_saved` を併記し SKILL.md で「cache hit M件=0コスト」を表示 |
| 377-3 | per-item承認 質問攻め | `evolve.py:680` 付近で `classified.proposable` を confidence で `proposable_high_conf`/`proposable_low_conf` に分割（しきい値は `remediation/confidence.py` 再利用）。SKILL.md Step 5.5 を「低conf群はデフォルトまとめてスキップ（個別展開は任意）、高conf群のみ per-item」に変更 |
| 377-4 | fitness母集団 导线 | fitness section builder で `insufficient_data` 時に具体アクションを1行出力。**注: ADR-041 / `evolve_decisions`（#360-A）で accept/reject の決定論キャプチャは実装済み**。#356 と重複の可能性が高いので着手前に両者の現状を確認し、カバー済みなら #377-4 はそちらに畳む |

---

## #377-2（fix/359 への追補、close 不可を実証）

実測で残余3経路を確認（detector を実 .md でドッグフード）:
1. `aws_arn`(conf 0.75) が .md テーブル行 `| ... |` で素通り。
2. `aws_arn`(conf 0.75) が通常の説明文（番号なし散文）で素通り。
3. `slack_id` `B0AJRU27Z2Q`(conf 0.65) — `_SLACK_DOC_ID_RE` が `C0`/`A0` のみ、`B0`(Bot ID) 未カバー。

**追補**（`hardcoded_detector.py`、現324行・予算内）:
- `_SLACK_DOC_ID_RE` に `B0` を追加（Bot ID も公開参照値）。
- `.md` ファイル限定でテーブルセル（`|` 区切り行）を doc 文脈に追加。
- 散文 ARN は precision 優先で保留（config doc の本物 ARN を取りこぼす FN リスク）。テーブル+B0 だけ塞ぎ、散文は #377-2 に「既知の残課題」として明記。

---

## 実装順序

1. **#377-2 追補**（fix/359、独立・実証済み・小）→ 先にマージして 359 を畳む。
2. **P1**（#375 schema + #376 guard）← 本 PR。invariant の土台。
3. **P2**（#377-5）→ P1 の invariant を consume（P1 マージ後）。
4. **P3**（#377-1,3,4）→ 並行可能だが #377-4 は #356 / evolve_decisions 確認後。

各 PR は feat/fix ブランチ。worktree 並行時は file-level で衝突しない（P2=evolve_consistency.py 新規、P3=別ファイル群）。
