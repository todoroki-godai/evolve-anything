# ADR-044: main 着地の仕様未追従マージを SessionStart で検出し spec-keeper/ADR を提案

- Status: Accepted
- Date: 2026-06-09
- Issue: （feedback 相談起票・spec-trigger）
- Related: ADR-038（Stop hook の additionalContext は非介入フロー不適）, ADR-031（worktree 安全 slug）, `spec-keeper-trigger.md`（グローバルルール）, learning_install_is_not_enforcement, learning_synthetic_fixture_false_confidence

## 背景（症状）

仕様が変わる作業の後に SPEC.md / ADR を追従させたいが、現状のトリガーは
`spec-keeper-trigger.md`（グローバルルール）と gstack flow-chain の `ship→spec-keeper`
連鎖だけ。hooks・trigger_engine には `merge`/`spec-keeper` 参照がゼロで、**`gh pr merge`
直叩きや GitHub web 上の squash マージでは何も発火しない**。この PJ の直近マージ
（#384/#382/#386 等）はまさに web squash であり、仕様追従の提案が一切出ていなかった。

ルール記載は assistant が忘れる＝`SKILL.md MUST ≠ enforcement`。決定論 hook で塞ぐ。

## 根本原因

マージ検知点が存在しない。かつマージは squash＝main 上の**通常コミット**
（subject に `(#NNN)`、`--merges` では拾えない）であり、merge コミット検出では取れない。

## 検討した選択肢

### A. PostToolUse Bash hook で `gh pr merge`/`git merge` を拾う（却下）

即時だが web squash マージを取りこぼす。web マージは「自分のセッション外で main が
進んだ」状態で、ローカルツールイベントでは**原理的に**観測できない。この PJ の実
マージ手段を外すため不採用。

### B. ルール文言を強化（却下）

enforcement にならない。今と同じ穴（assistant が忘れる）。

### C. SessionStart で git log 差分検知（採用）

起動時に main の tip を前回 last_sha と diff する。マージ手段に依存せず web squash も
拾う。`restore_state.py` が既に `recent_commits` を読み `_deliver_pending_trigger()` で
stdout 配信する機構を持つため、新規 hook を足さず相乗りできる（配線コスト最小）。

## 採用したゲート（実コーパス dry-run で較正）

設計を確定する前に直近 40 commit（≒3週間）へ dry 適用し当たり数を実測した
（learning_synthetic_fixture_false_confidence: FP/FN は仕様でなく実コーパスでしか分からない）:

| ゲート案 | 発火 | 内訳 | 評価 |
|---|---|---|---|
| 素朴（`feat:` + plugin.json 監視） | 8 | 全部 `chore(release)` の version bump | ❌ FP 製造機 |
| structural-only（skill/hook 追加のみ） | 0 | この PJ は scripts/lib 改変で進化 | ⚠️ 事実上死蔵 |
| 広域（挙動コード変更 × spec 未更新） | 12 | 10件が `fix:`（仕様変えず） | ❌ nag |
| **較正版（feat/refactor/feat! × 挙動コード × spec 未更新, CLAUDE.md 含む）** | **2** | 真 TP のみ | ✅ |

データが教えた 2 つの設計修正:

1. **仕様アーティファクト集合に CLAUDE.md を含める**。この PJ の生きた仕様は SPEC.md
   でなく **CLAUDE.md の component table**。SPEC.md 単点監視は FP/FN の両方を出す。
2. **`fix:` を信号源から外す**。バグ修正は挙動コードを触るが仕様は変えない。広域案の
   FP 10件はすべて fix。信号源は構造変化の客観証拠（type ∈ {feat, refactor} or `!`）に限る。

発火条件（`is_spec_relevant_commit`）:
- ① 種別 ∈ {feat, refactor} または breaking(`!`)
- ② diff が `scripts/**.py` または `hooks/**.py`（挙動コード）を変更
- ③ diff が仕様アーティファクト（`SPEC.md | spec/** | docs/decisions/** | CONTEXT.md | CLAUDE.md`）を一切触っていない

ADR 化は breaking(`!`) のときのみ併記提案する（`scripts/lib コア新規`を ADR トリガーに
する案は決定論判定が無理筋＝FP 源なので却下）。

## 重複抑制（cooldown + 解消プロキシ）

at-most-once（無視したら HEAD 前進で二度と出さない）は `silence ≠ evaluated` の沈黙
バグの再発（「忘れた」と「不要と判断した」を区別できない）。逆に毎セッション nag も
嫌われる。中間を採る:

- 新規 fire は即時 surface し pending に積む
- 同一 commit は COOLDOWN（3日）内では再提示しない
- COOLDOWN 明けに未解消なら1回だけリマインド（`MAX_REMINDERS=1`、以後沈黙）
- **解消プロキシ**: 新スキャン範囲に仕様アーティファクトを触ったコミットが1つでもあれば
  pending を全クリア（dev が仕様を維持している＝good state ＝沈黙でよい）

`triage_ledger` の TTL+再発カウンタと同じ思想（前例あり）。

## dry-run 副作用なし

`detect(persist=False)` でマーカー書き込みを一切しない（pitfall_dryrun_stateful_store_write
準拠）。実 temp-git E2E で「persist=False では判定は走るが marker 不変」を assert。

## グローバルルールは置換しない（second-opinion からの逸脱点）

second-opinion は「`spec-keeper-trigger.md` を hook へ一元化して1行参照に痩せさせよ」と
助言したが、同ルールは**グローバル（全 PJ 共通）**であり、rl-anything 固有の hook で
他 PJ 向けの一般ガイダンスを削ると他 PJ が壊れる。よってルールは現状維持、hook は
rl-anything への**加算的 enforcement** とする。

## 配置

- 検出器: `scripts/lib/spec_trigger.py`（決定論・LLM 非依存。slug は
  `optimize_history_store.resolve_slug` に委譲＝worktree 安全, ADR-031）
- 配線: `hooks/restore_state.py` の `_deliver_spec_drift()`（SessionStart, fail-safe）
- 設定: `userConfig.spec_trigger_enabled`（default true、false で完全無効化）
- マーカー: `DATA_DIR/spec_trigger/<slug>.json`（PJ スコープ単一 JSON）
