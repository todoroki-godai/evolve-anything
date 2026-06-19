# 日次 evolve リワードループ設計 — 「毎日 evolve を叩くだけで環境が進化する」体験

- Status: 確定版（5 論点すべて決定済み — §5 に決定と理由を記録）
- Date: 2026-06-12（同日確定）
- 関連 ADR: ADR-028（observability contract 単一ソース）, ADR-041（evolve decision capture）, ADR-046（outcome metrics advisory→weight）, ADR-047（human-confirmed idiom proxy・本設計と同時起票）
- 関連 issue: #431（correction capture 二層化）, #432（weak_signals レーン）, #434（store_registry）, #185（測定バグメタ検査）
- 関連 learning: learning_skill_md_must_not_enforcement（散文ステップは発火しない）, learning_dryrun_verification_blind_spot, learning_gate_design_needs_real_corpus_dryrun, learning_observability_quality_evidence_and_meaning

---

## 0. なぜこの設計が必要か（症状）

実測（2026-06-12, `~/.claude/evolve-anything/`）:

| ストア | 状態 | 含意 |
|---|---|---|
| `weak_signals.jsonl` | **313 件・全件 channel=llm_judge・promoted=False・昇格 0 件** | バッチ LLM 判定は溜まっているが、人間確認の入口がないため一切 corrections に昇格していない |
| `corrections.jsonl` | 9 件 / 76 日（human-source は 1 件） | フェーズ昇格条件 `corrections>=10`（human-source のみカウント）が永久未達 |
| `growth-state-*.json` | 全 11 PJ が `initial_nurturing` / `bootstrap` 固定 | 「環境が育つ」体験がユーザーに見えない |
| `correction_idioms.jsonl` | 313 行・**全件 provenance.judge="llm_haiku"**（人間未確認） | 個人辞書はあるが human-confirmed が 0 → 自動再適用の発火条件を一切満たさない |

根因は **昇格経路が `skills/reflect/SKILL.md` Step 7.7 の散文ステップだけ** である点。
hooks/ と trigger_engine.py に weak_signals への言及はゼロ。learning_skill_md_must_not_enforcement
が示すとおり「SKILL.md の MUST」は実行され損ねる（reflect を毎日叩く運用が存在しない以上、
昇格は構造的に 0 のまま）。

→ **昇格の入口を `evolve` の決定論 phase に移し、毎日叩かれる経路に乗せる**のが本設計のコア。

### ゴール体験（ユーザー決定済み・変更不可）

> 「毎日 `/evolve-anything:evolve` を叩くだけで、環境が自然に進化していく」

すべての新機能は **evolve の中に決定論 phase として配線**する。
reflect・SessionStart・手動コマンドへの配線は禁止（実質発火しないため）。

---

## 1. 既存アーキテクチャ（接地点）

### 1.1 evolve の phase emit 機構

`skills/evolve/scripts/evolve.py` の `run_evolve()`（L431-1164）が全 phase を実行し、
1 つの巨大 result dict を返す。phase の書き方は確立されている:

```python
# evolve.py L1131-1136（既存 weak_signals phase）
try:
    from weak_signals import batch as _ws_batch
    _ws_slug = _resolve_pj_slug(project_dir)
    result["weak_signals"] = _ws_batch.run_batch(_ws_slug, dry_run=dry_run)
except Exception as e:
    result["weak_signals"] = {"error": str(e)}
```

特徴（本設計が踏襲する制約）:
- **常時 emit**: try/except で必ず result にキーを置く。eligible でなくても error でも emit。
- **dry_run 貫通**: `run_batch(..., dry_run=dry_run)` のように最下層 store write までゲートを渡す。
- **slug スコープ**: `_resolve_pj_slug(project_dir)` を全 phase が使う（DATA_DIR 全PJ共通 pitfall）。
- 出力は `--output <path>` でファイル化（ADR-039）。SKILL.md は `$OUT` を Read して phase を消費するだけ。

result の top-level 構造（本設計が触る部分）:
- `result["phases"]["<name>"]` — 大半の phase（observe/fitness/discover/audit/...）。
- `result["weak_signals"]` / `result["correction_semantic"]` / `result["evolve_decisions"]` — top-level（L1126-1162）。
- `result["observability"]` — `collect_observability(proj)` の戻り（L620-623）。

### 1.2 observability contract（ADR-028）

`scripts/lib/audit/observability.py` の `_OBSERVABILITY_BUILDERS`（L36-50）が単一ソース。
markdown 経路（report.py）と構造化経路（`collect_observability` → evolve `result["observability"]`）の
双方がこのリストを消費する。**weak_signals は既に登録済み**（L49 + `sections_weak_signals.py`）で、
「暗黙修正シグナル N 件・うち未昇格 M 件」を surface している。

builder 契約: `(project_dir: Path) -> Optional[List[str]]`。非該当のみ None、該当時は clean でも行を返す。

### 1.3 昇格フローと human-source 原則

- `scripts/lib/correction_semantic/promote.py` `promote_signals(signal_keys, ...)`:
  指定 signal_key の未昇格 weak_signal を corrections に `source="reflect_confirmed"` で昇格し、
  weak_signal を `promoted=True` にマーク（原子的 rename・dry_run ゼロ書込）。
- `scripts/lib/correction_semantic/provenance_weight.py`:
  `HUMAN_SOURCES = frozenset({"reflect_confirmed"})`。`count_human_corrections()` が
  human-source だけを数える。**フェーズ昇格はこのカウントのみで駆動**（ユーザー決定・変更不可）。
- `scripts/lib/correction_semantic/promote.py` `read_unpromoted(weak_signals_path, channel)`:
  未昇格レコードを返す（昇格候補の取得口・既に存在）。

### 1.4 成長フェーズ計算

`scripts/lib/audit/orchestrator.py` L339-394 が成長ナラティブを生成（audit が唯一の権威）:
```python
corrections_count = count_human_corrections(corrections or [])   # L354
phase = detect_phase(sessions_count, corrections_count, crystallized, coherence_score)  # L369
progress = compute_phase_progress(...)  # L370
update_cache(project_name, phase, progress, _cache_extra)        # L385
```
`growth_engine.compute_phase_progress`（L114-145）が `corrections/10.0` を進捗率に含む。
evolve は audit を phase として回す（L612）ので、**この計算は既に evolve 内で走っている**。

### 1.5 store_registry（#434）

`scripts/lib/store_registry.py` `_DECLARATIONS`。weak_signals.jsonl / correction_idioms.jsonl /
correction_judged.jsonl は宣言済み（L165-194, `writer_locus="batch"`）。
**スキーマ変更・新フィールドはここに宣言を更新する**（本設計の #2/#5 が該当）。

---

## 2. 設計する 7 機能

各機能は **決定論 phase の emit**（evolve.py）と **その phase 出力を消費する SKILL.md ステップ** に分離する。
SKILL.md 側に判定ロジックを書かない（散文ステップ禁止・learning_skill_md_must_not_enforcement）。

### データフロー全体図

```
                       ┌─────────────────────── evolve.py run_evolve(dry_run) ───────────────────────┐
                       │                                                                              │
 weak_signals.jsonl ──▶│ [既存] weak_signals.run_batch  → result["weak_signals"]                       │
 (313, llm_judge)      │                                                                              │
                       │ ★#5 weak_signals_ttl.mark_expired(dry_run) → result["weak_signals_ttl"]        │
                       │       (45日超を expired=True マーク・昇格候補から除外)                          │
                       │                                                                              │
                       │ ★#1 daily_correction_review.build_review(dry_run)                             │
                       │       前回 evolve 以降の新規 unpromoted を idiom 単位 group 化               │
                       │       → result["correction_review"] = {groups:[...≤5], seen, eligible}      │
                       │                                                                              │
                       │ ★#3 bootstrap_backlog.build(dry_run) (初回のみ)                                │
                       │       既存 backlog を per-PJ scope + 簡易類似 group 化                         │
                       │       → result["correction_review"]["bootstrap"] = {pj_groups, total}        │
                       │                                                                              │
                       │ [既存] audit phase → growth phase 計算 (orchestrator.py)                       │
                       │ ★#6 growth_report.build → result["phases"]["audit"] 配下 or top-level           │
                       │       「corrections 7/10 — あと3件」「今日 idiom 2件が自動化対象に昇格」       │
                       │                                                                              │
                       │ ★#4 [既存] observability weak_signals builder (登録済み・行文言を強化)          │
                       │ ★#7 measurement_bug.detect → observability builder 追加                        │
                       │                                                                              │
                       └──────────────────────────────┬───────────────────────────────────────────────┘
                                                       │ result を $OUT に書く (--output)
                                                       ▼
          ┌──────────────────── skills/evolve/SKILL.md（$OUT を Read して消費）─────────────────────┐
          │ Step X: result["correction_review"]["groups"] を AskUserQuestion で y/n（最大5）            │
          │   y → evolve-reflect --promote-weak <signal_keys> + ★#2 idiom を human-confirmed 化            │
          │   Skip/Other → 確認なしで完走（分岐は §2.1 エッジケース）                                  │
          │ Step Report: result の growth_report を末尾表示                                            │
          └────────────────────────────────────────────────────────────────────────────────────────┘
                                                       │ apply 後
                                                       ▼
                              evolve --drain（既存 ADR-041 経路に相乗り or 独立 promote）
```

---

### 機能 #1: evolve 内「今日の修正確認」phase

**目的**: 前回 evolve 以降の新規 weak_signal を idiom 単位でグループ化し、1 日最大 5 グループを
AskUserQuestion で y/n 確認（1 分以内で終わる UX）。y → corrections.jsonl に human-source 昇格 +
idiom を human-confirmed 化。

**新規モジュール**: `scripts/lib/correction_semantic/daily_review.py`（決定論・LLM 非依存）

```python
def build_review(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    idioms_path: Optional[Path] = None,
    seen_path: Optional[Path] = None,     # correction_review_seen.jsonl（既読キー集合）
    max_groups: int = 5,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """前回 evolve 以降の新規 unpromoted weak_signal を idiom 単位 group 化して返す。

    Returns（常時 emit。eligible でなくても groups=[] で返す）:
      {
        "eligible": bool,                 # groups が 1 件以上あるか
        "groups": [                       # 最大 max_groups 件
          {"idiom": str,                  # 代表 idiom（個人辞書から照合）
           "signal_keys": [str, ...],     # この group に属する weak_signal の signal_key
           "channel": "llm_judge",
           "evidence": {"text": str, "reason": str, "session_id": str, "count": int},
          }, ...
        ],
        "remaining": int,                 # max_groups を超えて未提示の group 数
        "reviewed_keys_count": int,       # 既読集合（correction_review_seen）の現在サイズ
        "dry_run": bool,
      }
    """
```

**phase 配置**: evolve.py L1136（weak_signals run_batch 直後）に追記。weak_signals 検出が走った
**後**に置く（新規シグナルがその run で書かれた可能性があるため、store を再読する）。

```python
try:
    from correction_semantic import daily_review as _dr
    result["correction_review"] = _dr.build_review(_ws_slug, dry_run=dry_run)
except Exception as e:
    result["correction_review"] = {"error": str(e)}
```

**group 化アルゴリズム（決定論・LLM なし）**:
1. `read_unpromoted(channel="llm_judge")` で未昇格を取得。`expired`（#5）は除外。
2. **既読集合（correction_review_seen）に含まれない `signal_key` のレコードだけに絞る**（= 新規）。
3. 各 weak_signal の `provenance.text`/`reason` を correction_idioms の `idiom` と物理キー
   (`source_path:line_no`) で突合し、同一 idiom_key を 1 group に集約。
4. group を `evidence.count`（同 idiom の再発回数）降順で並べ、上位 `max_groups` を返す。
   残りは `remaining`。**昇格件数で優先度を付ける** = 頻出パターンから人間に確認させる。

**既読ストア（決定済み・論点2）**: `correction_review_seen.jsonl`（PJ slug スコープ・新規ストア →
store_registry 宣言）。**`correction_judged.jsonl` と同方式の物理キー集合**（append-only・
1 行 `{"key": "<signal_key>", "pj_slug": ..., "decision": "promoted"|"rejected", "reviewed_at": ...}`）。

> **detected_at 時刻ベース cursor は却下**: 同時刻に複数シグナルが書かれた場合に
> 「時刻 ≤ cursor」判定で取りこぼす境界バグの温床になる。キー集合なら厳密。
> **肥大化は無視できる**: 母集団は weak_signals（現在 313 件・TTL 45 日で自然減衰）であり、
> 1 行 ~120 byte × 数百件 = 数十 KB オーダー。correction_judged.jsonl が同方式で既に実運用されている。

既読集合への追記は **apply 時のみ**（dry_run では読むだけ・書かない）。追記タイミングは promote 確定後。

**SKILL.md 消費ステップ**（新 Step・reflect Step 7.7 を evolve へ移植）:
- `$OUT` の `result["correction_review"]["groups"]` を Read。
- 各 group を AskUserQuestion の 1 question にする（最大 5 question を 1 回の AskUserQuestion バッチで提示）。
  選択肢 `はい（本物の修正）` / `いいえ（ノイズ）` / `Skip`。
- 「はい」を選んだ group の `signal_keys` をまとめて
  `evolve-reflect --promote-weak <key1,key2,...>` で昇格（既存 promote_signals に相乗り）。
- 昇格後、その idiom を human-confirmed 化（#2）+ 既読集合へ追記。

**1 分 UX の担保**: max_groups=5。AskUserQuestion は select UI で 5 問 1 画面。
group は頻出順なので「効くものから 5 つ」に絞られる。残りは `remaining` 件として次回に回る。

---

### 機能 #2: human-confirmed idiom の自動昇格

**目的**: human-confirmed な idiom に一致する新規 weak_signal を `promoted_by="idiom_dict"` で
自動昇格（人間が一度承認したパターンの機械的再適用 = human-source 原則を維持）。
**詳細な設計判断は ADR-047 を参照**（本ファイルと同時起票）。

**スキーマ変更（store_registry 宣言更新が必要 #434）**:

`correction_idioms.jsonl` に新フィールド追加:
| フィールド | 型 | 意味 |
|---|---|---|
| `confirmed` | bool | 人間が #1 の review で「はい」を選んだか（初期 False） |
| `confirmed_at` | ISO8601 / null | 確認時刻 |
| `confirmed_by` | str / null | 確認 source（`daily_review`） |
| `revoked_at` | ISO8601 / null | 安全弁③で取り消した時刻（取り消し時に confirmed=False へ戻す） |

> **重要な不変条件**: 現在の 313 idiom は全件 `provenance.judge="llm_haiku"`（人間未確認）。
> `confirmed` フィールドは存在しない = `confirmed=False` 扱い。
> **`confirmed=True` が立つまで自動昇格は一切発動しない**。これにより、起動時点で
> 313 件が雪崩式に corrections へ流れ込む事故を構造的に防ぐ（ADR-047 の却下案＝ロンダリング防止）。

`corrections.jsonl` 昇格レコードの `source`（**決定済み・論点1**）:
- #1 の人間確認昇格 → `source="reflect_confirmed"`（既存・human-source）。
- #2 の自動昇格 → `source="idiom_dict"`。**`HUMAN_SOURCES` に追加し重み 1.0 の同等扱い**
  （`provenance_weight.HUMAN_SOURCES = frozenset({"reflect_confirmed", "idiom_dict"})`）。
  根拠: human が一度承認した idiom の機械的再適用なので human-source とみなす（ADR-047）。
  0.8 割引案・advisory 並走案は却下（重み和でフェーズ表示「7/10」の整数性が崩れる /
  体験ゴール「毎日 evolve で自然進化」が遅れる — ADR-047 却下案 D/E 参照）。
  FP リスクは重み割引でなく**下記の安全弁 3 点（別レバー）で吸収**する。
  追加レコードには `promoted_by="idiom_dict"` + `idiom_key=<確認済み idiom>` を残し provenance を保つ。

**安全弁 3 点（決定済み・1.0 同等扱いとセット）**:

| # | 安全弁 | 実装 |
|---|---|---|
| ① | **日次自動昇格の上限** | userConfig `idiom_autopromote_daily_cap`（number・デフォルト 10）。既存項目（`min_sessions` / `skill_lr_budget` 等）と同じ「フラット number + description にデフォルト明記」の粒度。上限超過分は昇格せず次回 run に持ち越し、`result["idiom_autopromote"]["capped"] = N` で surface |
| ② | **自動昇格の毎回 surface** | `sections_weak_signals.py` builder（ADR-028 observability contract）に「本 run の idiom_dict 自動昇格 N 件（idiom 一覧）」行を追加。markdown / 構造化両経路に自動伝播し、evolve レポートで必ず見える |
| ③ | **idiom 単位の取り消し** | `evolve-reflect --revoke-idiom <idiom_key>`: 該当 idiom を `confirmed=False` + `revoked_at` に戻し、その idiom_key 由来の `promoted_by="idiom_dict"` corrections レコードを `invalidated=True` に原子的 rewrite（`_rewrite_promoted` と同型）。`count_human_corrections` は invalidated を除外 → フェーズ進捗が正しく巻き戻る。weak_signals 側の `promoted=True` は維持（再提示しない） |

**phase 配置**: #1 の `build_review` の後、別 phase `idiom_autopromote`:
```python
try:
    from correction_semantic import idiom_autopromote as _iap
    result["idiom_autopromote"] = _iap.autopromote(_ws_slug, daily_cap=_cap, dry_run=dry_run)
except Exception as e:
    result["idiom_autopromote"] = {"error": str(e)}
```
`autopromote()` は: confirmed=True（かつ未 revoke）の idiom 集合を読み → 新規 unpromoted
weak_signal のうち `idiom_key` が confirmed 集合にあるものを、**daily_cap 件まで**
`promote_signals(..., source="idiom_dict")` で昇格。超過分は `capped` として返す。
**dry_run では「昇格するはずだった件数」だけ返し書き込まない**。

**起動時無発火の検証**: 実 PJ で `confirmed` 未設定の現状を dry-run で流し、
`result["idiom_autopromote"]["promoted"] == 0` を assert する E2E を Test Plan 必須化。

---

### 機能 #3: 初回バックログ bootstrap モード（決定済み・ハイブリッド方式）

**目的**: 既存 313 件のバックログを消化する。**決定（論点3）: ハイブリッド** —
アクティブ PJ のみ初回 bootstrap モードでまとめて確認（per-PJ 15-30 分）、
残り PJ は日次 5 件 + TTL 45 日の自然失効に任せる。

**実データによる圧縮試算（python3・LLM 非呼び出し・本設計のために実走）**:

| 手法 | 入力 313 件 → グループ数 | 含意 |
|---|---|---|
| 正規化（NFKC + 記号除去 + lower）後の完全一致 | **293** | idiom はほぼ全件ユニーク |
| 文字 bigram jaccard ≥ 0.8（weak_signals rephrase 閾値と整合） | **292** | ほぼ圧縮されない |
| 文字 bigram jaccard ≥ 0.6 | **287** | 同上 |
| 内容キーワード（漢字/カタカナ 2 字以上の名詞）jaccard ≥ 0.5 | **267**（うち多member 31 group が 77 件を吸収） | 最も効くが圧縮率 15% |

> **設計上の重要発見**: correction_idioms の `idiom` は**正規化された修正パターンではなく、
> 生のユーザー発話断片**（median 10 文字・例「金額がきれてる」「書き直しして」）。
> よって決定論的な文字列類似では圧縮がほとんど効かない（313→267 が現実的下限）。

**per-PJ 分布（実測・bootstrap の現実的な配り方）**:
```
116  figma-to-code      24  atlas-breeaders    13  sys-bots        6  docs-platform
 48  amamo              20  receipt            12  ai-daily-report 2  kazevolve
 47  evolve-anything        16  daily-ai-github-trending   9  aws-cost-guardian
```

**ハイブリッド方式（決定済み・論点3）**:
- **アクティブ PJ（evolve-anything 47 件・figma-to-code 116 件など上位）**: 初回 bootstrap モードで
  まとめて確認する。グルーピング（内容キーワード jaccard≥0.5・31 group が 77 件を吸収）込みで
  per-PJ 15-30 分の集中セッション。クラスタ代表 1 件を確認すれば同 group を一括昇格できる。
- **残り PJ（低頻度・少件数）**: bootstrap を実行せず、日次 5 件（#1）+ TTL 45 日（#5）の
  **自然失効に任せる**。これは手抜きではなく **「古い修正候補は腐る」を意図した間引き**である —
  45 日間一度も evolve で確認されなかった低活動 PJ のシグナルは、現在の作業文脈との関連が
  失われており、昇格させてもノイズになる確率が高い（TTL がそのまま品質フィルタとして機能する）。
- **bootstrap 対象 PJ の選択は実行時に AskUserQuestion で人間が選ぶ**: 初回 evolve 時に
  backlog 件数つきで「この PJ の backlog X 件を bootstrap で消化しますか？
  （まとめて確認 / 日次 5 件ずつ / TTL 失効に任せる）」を提示する。機械が勝手に
  「アクティブ」を判定しない（件数は判断材料として表示するだけ）。

bootstrap は **現在 cwd の PJ slug の backlog のみ**を対象にする（DATA_DIR 全PJ共通 pitfall）。

**新規モジュール**: `scripts/lib/correction_semantic/bootstrap_backlog.py`
```python
def build(pj_slug, *, idioms_path=None, weak_signals_path=None, marker_path=None, dry_run=False):
    """初回（marker 未設定）のみ、当該 PJ の全 unpromoted backlog を group 化して返す。

    Returns:
      {"is_bootstrap": bool,        # marker 未設定なら True
       "pj_total": int,             # 当該 PJ の未昇格 backlog 件数
       "groups_total": int,         # 内容キーワード jaccard≥0.5 圧縮後の group 数
       "groups": [...],             # bootstrap 選択時に使う全 group（代表 idiom + signal_keys）
       "dry_run": bool}
    marker（bootstrap_done-<slug>.marker）が立っていたら is_bootstrap=False で即返す。
    """
```
bootstrap は #1 の `correction_review` に `["bootstrap"]` キーとして相乗りさせる。
SKILL.md は is_bootstrap=True のとき AskUserQuestion で 3 択
（**まとめて確認** / **日次 5 件ずつ** / **TTL 失効に任せる**）を提示し:
- 「まとめて確認」→ groups を順に AskUserQuestion バッチで確認（15-30 分・代表確認で一括昇格）。
  完了時に marker を立てる。
- 「日次 5 件ずつ」→ marker を立てず #1 の通常ページネーションに合流。
- 「TTL 失効に任せる」→ marker を立てる（以後 bootstrap を再提示しない。TTL #5 が間引く）。

---

### 機能 #4: observability contract 登録（既存・文言強化）

**現状**: weak_signals builder は **既に登録済み**（`observability.py` L49 +
`sections_weak_signals.py`）。「暗黙修正シグナル N 件・うち未昇格 M 件」を surface している。

**本設計での変更**: builder の文言に **「未昇格 N 件 → /evolve-anything:evolve の今日の修正確認で昇格可能」**
を追記し、ユーザーを #1 の入口へ誘導する。さらに `correction_review` の `remaining` が大きいときは
「backlog 消化中（残 X group）」を併記。新規 builder は作らず既存 builder の戻り行を強化するだけ
（learning_install_is_not_enforcement: 新入口を増やさない・#278 の教訓）。

→ audit/evolve のたびに必ず surface される（ADR-028 の単一ソース経由で両経路に自動伝播）。

---

### 機能 #5: weak_signals TTL（corrections decay 45 日と整合）

**現状調査結果**: weak_signals.jsonl に **TTL は存在しない**。
`weak_signals/store.py` の `WeakSignal` dataclass に expiry フィールドなし。
`store_registry.py` の weak_signals.jsonl 宣言の retention を要確認（permanent か ttl か）。
corrections の decay 45 日は `discover/patterns.py` の `constraint_decay`（CLAUDE.md 記載）に存在。

**設計**: 期限切れは **削除でなく `expired=True` マーク**（昇格候補から除外）。

**スキーマ変更（store_registry 宣言更新が必要 #434）**:
`weak_signals.jsonl` に新フィールド:
| フィールド | 型 | 意味 |
|---|---|---|
| `expired` | bool | `detected_at` から TTL_DAYS 超で True（初期 False） |
| `expired_at` | ISO8601 / null | マーク時刻 |

store_registry の weak_signals.jsonl 宣言を `retention="ttl"` + `ttl_days=45` に更新
（現状が permanent なら変更・現状確認は実装時）。

**新規モジュール**: `scripts/lib/weak_signals/ttl.py`
```python
TTL_DAYS = 45   # corrections の constraint_decay と整合（単一定数で揃える・将来 config 化）

def mark_expired(*, weak_signals_path=None, now=None, dry_run=False) -> Dict[str, Any]:
    """detected_at から TTL_DAYS 超かつ未昇格・未expired のレコードを expired=True に
    原子的 rewrite（promote.py の _rewrite_promoted と同型）。
    dry_run はマークせず「マークするはずだった件数」だけ返す。
    Returns: {"expired": int, "scanned": int, "dry_run": bool}
    """
```

**phase 配置**: evolve.py の weak_signals run_batch 直後・#1 build_review の**前**
（review が expired を除外できるように）:
```python
try:
    from weak_signals import ttl as _ws_ttl
    result["weak_signals_ttl"] = _ws_ttl.mark_expired(dry_run=dry_run)
except Exception as e:
    result["weak_signals_ttl"] = {"error": str(e)}
```
`read_unpromoted` 側に `exclude_expired=True` を足し、#1/#2/#3 すべてが expired を見ないようにする。

---

### 機能 #6: 成長レポート（evolve レポート末尾・決定論）

**目的**: evolve レポート末尾に PJ の成長状態を決定論で表示。
例:「corrections 7/10 — あと 3 件で次フェーズ」「今日の確認で idiom 2 件が自動化対象に昇格」。

**現状**: audit orchestrator（L387-394）が既に Level/Phase/Progress バーを生成し、evolve は
report-narration.md で成長レベルをナレーションしている（SKILL.md L579-583）。
**不足しているのは「次フェーズまであと N 件」の具体的な残数表示**と「今日の昇格成果」。

**新規モジュール**: `scripts/lib/growth_report.py`（決定論・LLM 非依存）
```python
def build_growth_report(
    project_name: str,
    *,
    corrections: List[Dict],            # query_corrections の戻り
    review_result: Dict,                # result["correction_review"]
    autopromote_result: Dict,           # result["idiom_autopromote"]
) -> Dict[str, Any]:
    """成長レポート行（決定論）を返す。

    Returns:
      {"phase": str, "phase_ja": str,
       "corrections_human": int,        # count_human_corrections
       "corrections_target": int,       # 次フェーズ閾値（initial→structured は 10）
       "remaining_to_next": int,        # max(0, target - human)
       "promoted_today": int,           # 今 run で reflect_confirmed 昇格した件数
       "autopromoted_today": int,       # 今 run で idiom_dict 昇格した件数
       "lines": ["corrections 7/10 — あと3件で構造化育成へ",
                 "今日の確認で idiom 2件が自動化対象に昇格"]}
    """
```
**閾値の単一ソース化（決定済み・論点4）**: growth_report は `corrections_target=10` を
**ハードコードせず、growth_engine からモジュール定数を import する**。実装手順:
1. `growth_engine.py` に `STRUCTURED_CORRECTIONS_TARGET = 10`（および sessions/rules の閾値）を
   モジュール定数として切り出し、`detect_phase` / `compute_phase_progress` 内のリテラルを置換。
2. `growth_report.py` はこの定数を import して使う。
理由: 二重実装は片直し事故（#419 の轍 — 同じ値を 2 箇所で持つと一方だけ直して
全 PJ 同値バグの温床になる）を招く。閾値変更時に growth_engine だけ直せば
フェーズ判定とレポート表示が同時に追従する。

**phase 配置**: audit phase の後（成長計算済み）に top-level emit:
```python
try:
    from growth_report import build_growth_report
    result["growth_report"] = build_growth_report(
        Path(project_dir or ".").name,
        corrections=_corrections_for_report,
        review_result=result.get("correction_review", {}),
        autopromote_result=result.get("idiom_autopromote", {}),
    )
except Exception as e:
    result["growth_report"] = {"error": str(e)}
```
**SKILL.md 消費**: Step 9（Report クライマックス）に `result["growth_report"]["lines"]` を
そのまま列挙（report-narration.md の成長レベル表示の直後）。

---

### 機能 #7: #185 メタ検査（並行軸・別 issue）

**目的**: 「複数 PJ で集計値が完全一致したら測定バグ」検出を audit に追加。
learning_measurement_layer_diagnosis（「全 PJ 同値カウント = 測定バグ強シグナル」）の自動化。

**新規モジュール**: `scripts/lib/audit/measurement_bug.py` + observability builder
`scripts/lib/audit/sections_measurement.py`
```python
def detect_measurement_bug(metrics_by_pj: Dict[str, Dict[str, float]]) -> List[Dict]:
    """複数 PJ で同一指標が完全一致（かつ ≥3 PJ）したら測定バグ候補として返す。

    対象指標: env_score / utilization / coherence / 各 outcome 軸 など。
    Returns: [{"metric": str, "value": float, "pj_count": int, "pjs": [...]}]
    閾値: 3 PJ 以上で bit-exact 一致（floating 誤差を許さず ==）。1-2 PJ 一致は偶然なので除外。
    **0 / 0.0 / None 等の自明値は検出対象から除外する**（決定済み・論点5）。
    """
```
**指標選定（決定済み・論点5）**: **0 を除外した非自明値の PJ 間一致のみ**検出する。
- 理由: utilization=0.0 のような「0 同値」は未測定・データ不足で正当に起きる（#423 で既出）。
  これを拾うと FP の山になり advisory が無視されるようになる。
- precision 優先は ADR-043（hardcoded doc context suppression — 高 confidence 系は
  proposable 上位埋没=実質 FN なので precision 優先）の方針と整合。
- 非自明値（>0 の実測値）が 3 PJ 以上で bit-exact 一致するのは、PJ ごとに skills 数・
  セッション数が異なる前提では確率的にほぼ起きない → 一致 = 測定層バグの強シグナル。

データ源: `growth-state-*.json`（全 PJ のキャッシュ）を walk して env_score/level 等を集める
（evolve-fleet status と同じ集計経路を再利用）。
observability builder として `_OBSERVABILITY_BUILDERS` に 1 行追加（ADR-028 経由）。
**advisory のみ・スコア非関与**（#185 は検出であって自動修正ではない）。

> これは #1-#6 とデータ依存がない（並行実装可）。別 issue として切る（§issues-draft 参照）。

---

## 2.1 エッジケース・分岐設計

### AskUserQuestion で Skip/Other を選んだ場合（MUST）
- AskUserQuestion は evolve 実行中の対話。**ユーザーが Skip/Other を選んでも evolve 全体は完走する**。
- 分岐:
  - 「はい」→ その group の signal_keys を promote_signals + idiom confirmed 化 +
    **既読集合に decision="promoted" で追記**。
  - 「いいえ（ノイズ）」→ 昇格しない。だが **既読集合に decision="rejected" で追記**
    （再提示しない＝既読扱い）。将来 #5 の TTL で自然消滅。
  - 「Skip」→ 昇格せず **既読集合にも追記しない**（次回再提示）。
  - AskUserQuestion 自体が返らない / ユーザーが全体を中断 → 既読集合不変・promote 0 件で
    evolve は次フェーズへ進む（review は best-effort・evolve をブロックしない）。
- **dry-run（`--dry-run`）時は AskUserQuestion を出さない**。dry-run は分析のみ。
  build_review は groups を返すが SKILL.md 側で「dry_run なら表示のみ・確認なし」と分岐。

### 既読集合追記の原子性
- 既読追記と promote は同一 apply 内。promote が部分失敗したら該当 group の signal_keys を
  既読集合に追記しない（未昇格分を取りこぼさない）。promote_signals の戻り `{"promoted": N}` を
  見て、N == 期待件数のときだけ既読追記する。append-only なので部分追記でも壊れない
  （重複追記は read 側の set 化で無害）。

### バックログ 0 件 / 新規 0 件
- `build_review` は `eligible=False, groups=[]` を常時 emit。SKILL.md は eligible=False なら
  「今日の修正確認: 新規なし」を 1 行表示して次へ（AskUserQuestion を出さない）。

### dry-run ゼロ書込（pitfall 貫通）
- `build_review` / `autopromote` / `mark_expired` / 既読集合書込 / bootstrap marker すべてが
  `dry_run` を受け、True なら **一切ファイルに触れない**（件数だけ返す）。
- 既存 `append_signals` / `promote_signals` / `_rewrite_promoted` の dry_run パターンを踏襲。
- 実 PJ で `--dry-run` を流し DATA_DIR の mtime が一切変わらないことを assert する E2E を必須化
  （pitfall_dryrun_stateful_store_write）。

---

## 3. ストアスキーマ変更まとめ（store_registry #434 宣言更新が必要）

| ストア | 変更 | retention |
|---|---|---|
| `weak_signals.jsonl` | `expired` / `expired_at` 追加（#5） | `ttl` / `ttl_days=45` に更新 |
| `correction_idioms.jsonl` | `confirmed` / `confirmed_at` / `confirmed_by` / `revoked_at` 追加（#2 + 安全弁③） | permanent 維持 |
| `corrections.jsonl` | idiom_dict 昇格レコードに `promoted_by` / `idiom_key` / `invalidated`（安全弁③） | permanent 維持 |
| `correction_review_seen.jsonl` | **新規**。物理キー集合（correction_judged 方式・append-only・decision 付き） | permanent（母集団は TTL 45 日で減衰・313 件規模で肥大化は無視できる） |
| `bootstrap_done-<slug>.marker` | **新規**。空ファイル marker（#3） | permanent |

新規ストア（seen / marker）は `store_registry._DECLARATIONS` に
`writer_locus="batch"`（evolve batch 書込）で宣言を追加する。宣言なしで書くと orphan_store が
`undeclared` として surface する（#434）。

`HUMAN_SOURCES` への `idiom_dict` 追加（重み 1.0）は corrections のセマンティクス変更なので
ADR-047 で明示的に正当化する（安全弁 3 点とセット）。

userConfig 追加（manifest）: `idiom_autopromote_daily_cap`（number・デフォルト 10・
安全弁①）。既存 18 項目と同じフラット number + description 記載の粒度に合わせる。

---

## 4. 実装順序の制約（依存グラフ）

```
#5 TTL ──┐
         ├─▶ #1 daily_review ──▶ #2 idiom_autopromote ──▶ #6 growth_report
#3 bootstrap ─┘   (seen-set/group)   (confirmed→autopromote)   (昇格成果を表示)
#4 observability文言強化（#1 と並行可・低リスク）
#7 measurement_bug（完全独立・並行可）
```
- #5（TTL）と #3（bootstrap）は #1 の入力前提（expired 除外・backlog group 化）なので #1 より先。
- #2 は #1 の confirmed 化が前提。#6 は #1/#2 の結果を表示するので最後。
- #4 は既存 builder の文言だけ・#7 は独立 → どちらも並行可。

---

## 5. 決定済み論点（2026-06-12 確定・5/5）

1. **`idiom_dict` の HUMAN_SOURCES 扱い** → **採用 = 重み 1.0 同等扱い + 安全弁 3 点**（§機能#2）。
   `HUMAN_SOURCES = frozenset({"reflect_confirmed", "idiom_dict"})`。フェーズ表示は「7/10」の整数のまま。
   安全弁: ①日次自動昇格上限（userConfig `idiom_autopromote_daily_cap`・デフォルト 10）
   ②自動昇格を observability contract 経由で毎回 surface ③idiom 単位の取り消し
   （confirmed 解除 → 該当 idiom_dict 昇格分を invalidate）。
   **0.8 割引案・advisory 並走案は却下**: 重み和でフェーズ表示の整数性が崩れる /
   体験ゴール「毎日 evolve で自然進化」が遅れる。FP リスクは重み割引でなく
   安全弁 3 点という別レバーで吸収する（詳細: ADR-047 却下案 D/E）。
2. **cursor の単位** → **物理キー集合**（correction_judged と同方式・§機能#1）。
   detected_at 時刻ベースは却下（同時刻シグナルの取りこぼし境界バグ）。
   313 件規模・TTL 45 日減衰の母集団ではキー集合の肥大化は無視できる（数十 KB オーダー）。
3. **bootstrap の方式** → **ハイブリッド**（§機能#3）。アクティブ PJ（evolve-anything 47 件・
   figma-to-code 116 件など上位）のみ初回 bootstrap でまとめて確認（per-PJ 15-30 分）。
   残り PJ は日次 5 件 + TTL 45 日の自然失効に任せる（**「古い修正候補は腐る」を意図した間引き**）。
   bootstrap 対象 PJ の選択は実行時に AskUserQuestion で人間が選ぶ（機械判定しない）。
4. **growth_report の閾値** → **単一ソース化**（§機能#6）。`corrections_target=10` を
   ハードコードせず `growth_engine.STRUCTURED_CORRECTIONS_TARGET` を import。
   二重実装の片直し事故（#419 の轍）を構造的に防ぐ。
5. **measurement_bug の指標選定** → **0 を除外した非自明値の PJ 間一致のみ検出**（§機能#7）。
   utilization=0.0 等の「0 同値」は未測定で正当に起きる（#423 既出）ため除外。
   FP 回避・precision 優先は ADR-043 の方針と整合。
