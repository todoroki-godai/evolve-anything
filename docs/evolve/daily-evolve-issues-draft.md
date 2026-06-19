# 日次 evolve リワードループ — issue 分割案（確定版）

設計: [daily-evolve-reward-loop-design.md](daily-evolve-reward-loop-design.md)（5 論点すべて決定済み）
ADR: [ADR-047](../decisions/047-human-confirmed-idiom-autopromote-proxy.md)（Proposed・採用案確定）

このPJは **小さい PR を連続マージする文化**（audit.py 段階リファクタ 11 PR / learning_audit_package_split）。
各 issue は **1-2 PR・各 PR ~200-400 行**を目安に切る。全 issue で TDD First（tdd-first.md）・
dry-run ゼロ書込 E2E・実 PJ ドッグフード（learning_synthetic_fixture_false_confidence）を必須化する。

---

## 実装順序（依存グラフ）

```
   ┌── #A (TTL #5) ──┐
   │                 ├──▶ #C (daily_review #1) ──▶ #D (idiom_autopromote #2) ──▶ #F (growth_report #6)
   ├── #B (bootstrap #3) ┘
   │
   ├── #E (observability 文言強化 #4)   ← #C と並行可
   └── #G (measurement_bug #7)          ← 完全独立・いつでも可
```

**先行ブロック**: #A, #B（#C の入力前提）。**並行可**: #E, #G。**末尾**: #F。

---

## issue #A — weak_signals TTL（45日・expired マーク・evolve phase）

**タイトル**: `weak_signals に 45日 TTL を追加し expired を昇格候補から除外（evolve phase 化）`

**body**:
> ## 背景
> weak_signals.jsonl に TTL が存在せず、313 件が無期限に未昇格で滞留している。
> corrections の constraint_decay 45日と整合する TTL を入れ、期限切れを昇格候補から外す。
>
> ## 変更
> - `scripts/lib/weak_signals/ttl.py` 新規: `TTL_DAYS=45`, `mark_expired(dry_run=)`。
>   `detected_at` から 45日超かつ未昇格・未expired を `expired=True` に原子的 rewrite
>   （`promote._rewrite_promoted` と同型）。**削除しない**。
> - `weak_signals/store.py`: `WeakSignal` に `expired` / `expired_at` 追加。
>   `read_unpromoted` に `exclude_expired=True` 引数（promote.py / future readers が使う）。
> - `evolve.py`: weak_signals run_batch 直後・daily_review の**前**に
>   `result["weak_signals_ttl"] = ttl.mark_expired(dry_run=dry_run)` を常時 emit。
> - `store_registry.py`: weak_signals.jsonl 宣言を `retention="ttl", ttl_days=45` に更新。
>
> ## Acceptance Criteria
> - [ ] `mark_expired(dry_run=True)` が store の mtime を一切変えない（実 PJ E2E で assert）
> - [ ] 45日超レコードが `expired=True` になり `read_unpromoted(exclude_expired=True)` から外れる
> - [ ] 既存 promote/append の dry_run 挙動に回帰なし（snapshot）
> - [ ] `claude plugin validate` パス

**依存**: なし（先行ブロック）。**PR サイズ**: 1 PR・~250 行（store + ttl + evolve 配線 + tests）。

---

## issue #B — 初回バックログ bootstrap モード（ハイブリッド・AskUserQuestion で PJ ごとに選択）

**タイトル**: `初回 evolve で backlog の消化方式を選択（まとめて確認 / 日次5件 / TTL 失効に任せる）`

**body**:
> ## 背景
> 既存 313 件（全件 llm_judge・未昇格）を初回 evolve でまとめて確認する入口がない。
> **実測: 文字列類似では 313→267 (15%) しか圧縮できない**（idiom は生の発話断片）。
> **決定（設計 §機能#3）: ハイブリッド方式** — アクティブ PJ（evolve-anything 47件・figma-to-code
> 116件など上位）のみ初回 bootstrap でまとめて確認（per-PJ 15-30分）。残り PJ は日次5件 +
> TTL 45日の自然失効に任せる。これは**「古い修正候補は腐る」を意図した間引き**であり、
> 45日間確認されなかった低活動 PJ のシグナルは現在の作業文脈との関連が失われている
> （TTL がそのまま品質フィルタとして機能する）。
>
> ## 変更
> - `scripts/lib/correction_semantic/bootstrap_backlog.py` 新規: `build(pj_slug, dry_run=)`。
>   marker 未設定なら当該 PJ の未昇格 backlog を内容キーワード jaccard≥0.5 で group 化
>   （31 group が 77件吸収）。`groups`（代表 idiom + signal_keys）を返す（一括昇格 UX 用）。
> - `bootstrap_done-<slug>.marker` 新規ストア（store_registry 宣言・writer_locus=batch）。
> - `evolve.py`: `result["correction_review"]["bootstrap"]` に相乗り emit（#C とキー共有）。
> - `skills/evolve/SKILL.md`: is_bootstrap=True のとき **AskUserQuestion で 3 択を人間が選ぶ**
>   （機械が「アクティブ PJ」を判定しない。backlog 件数は判断材料として表示するだけ）:
>   - 「まとめて確認」→ groups を順に AskUserQuestion バッチで確認（代表確認で同 group 一括昇格）。完了時 marker
>   - 「日次5件ずつ」→ marker を立てず #C の通常ページネーションに合流
>   - 「TTL 失効に任せる」→ marker を立てる（以後再提示しない。TTL #A が間引く）
>
> ## Acceptance Criteria
> - [ ] bootstrap は **cwd の PJ slug の backlog のみ**を対象にする（別 PJ の件数が混入しない）
> - [ ] marker 立ち後は `is_bootstrap=False` で即返す（「TTL 失効に任せる」選択でも marker が立つ）
> - [ ] 3 択いずれを選んでも evolve 全体は完走する
> - [ ] dry_run でファイル不変（実 PJ E2E）
> - [ ] evolve-anything 実 PJ で `pj_total=47`（実測値）が出ることを確認

**依存**: なし（#C と統合するが先行実装可）。**PR サイズ**: 2 PR
（PR1: bootstrap_backlog + marker + evolve emit、PR2: SKILL.md の 3 択分岐）。

---

## issue #C — evolve 内「今日の修正確認」phase（daily_review + 既読キー集合）

**タイトル**: `evolve に「今日の修正確認」phase を追加（前回以降の新規 weak_signal を idiom 単位 group 化・最大5件）`

**body**:
> ## 背景
> 昇格経路が reflect SKILL Step 7.7 の散文ステップのみ → 昇格 0 件。
> 毎日叩かれる evolve に決定論 phase として移植する（learning_skill_md_must_not_enforcement）。
>
> ## 変更
> - `scripts/lib/correction_semantic/daily_review.py` 新規: `build_review(pj_slug, max_groups=5, dry_run=)`。
>   **既読キー集合に含まれない** 未昇格(channel=llm_judge・非expired)を idiom_key で group 化・
>   頻度降順・上位5件。
> - `correction_review_seen.jsonl` 新規ストア（PJ slug スコープ・store_registry 宣言）。
>   **決定（設計 §機能#1・論点2）: correction_judged.jsonl と同方式の物理キー集合**
>   （append-only・`{"key": signal_key, "decision": "promoted"|"rejected", ...}`）。
>   detected_at 時刻 cursor 案は却下（同時刻シグナルの取りこぼし境界バグ）。
>   313件規模・TTL 45日減衰の母集団ではキー集合肥大化は無視できる（数十 KB オーダー）。
>   既読追記は **apply 時のみ**（dry_run は読むだけ）。
> - `evolve.py`: weak_signals run_batch / ttl の後に `result["correction_review"]` を常時 emit。
> - `skills/evolve/SKILL.md`: 新 Step（reflect Step 7.7 を移植）。
>   `$OUT` の groups を AskUserQuestion で y/n（最大5問1バッチ）→「はい」を `evolve-reflect --promote-weak`。
>   **エッジケース分岐を明記**: Skip/Other/中断でも evolve は完走（design §2.1）。dry_run は表示のみ。
>
> ## Acceptance Criteria
> - [ ] 新規 0 件なら `eligible=False, groups=[]` を emit（AskUserQuestion を出さない）
> - [ ] 「いいえ」で既読集合に decision="rejected" 追記・「Skip」は追記しない（次回再提示）
> - [ ] promote 部分失敗時に該当 group を既読集合に追記しない（取りこぼし防止）
> - [ ] 既読集合の重複追記が read 側 set 化で無害であること（冪等性テスト）
> - [ ] dry_run でファイル不変（実 PJ E2E）
> - [ ] reflect SKILL Step 7.7 は残す（後方互換）か evolve 移植に伴い deprecate 注記（頭判断）

**依存**: #A（expired 除外）, #B（bootstrap キー共有）。**PR サイズ**: 2 PR
（PR1: daily_review + 既読集合 + evolve emit、PR2: SKILL.md ステップ + AskUserQuestion 分岐）。

---

## issue #D — human-confirmed idiom の自動昇格（ADR-047・1.0 同等扱い + 安全弁3点）

**タイトル**: `human-confirmed idiom に一致する新規 weak_signal を idiom_dict で自動昇格（daily cap + surface + 取り消し付き）`

**body**:
> ## 背景
> 人間が一度承認したパターンを毎回確認させるのは非効率。confirmed idiom に一致する新規シグナルは
> 機械再適用する。**決定（ADR-047）: HUMAN_SOURCES に重み 1.0 で追加**（0.8 割引・advisory 並走は
> 却下 — フェーズ表示の整数性 / 体験ゴールの遅延。FP リスクは安全弁3点で吸収）。
> **現 313 idiom は全件未確認なので confirmed=True が立つまで一切発動しない**（雪崩防止）。
>
> ## 変更（PR1: 自動昇格本体）
> - `correction_idioms.jsonl`: `confirmed` / `confirmed_at` / `confirmed_by` / `revoked_at` 追加
>   （store_registry 更新）。
> - #C の review で「はい」確定時に該当 idiom を `confirmed=True` 化（daily_review or promote 側）。
> - `scripts/lib/correction_semantic/idiom_autopromote.py` 新規:
>   `autopromote(pj_slug, daily_cap=, dry_run=)`。
>   confirmed（かつ未 revoke）idiom 集合に `idiom_key` 一致する新規未昇格を
>   `promote_signals(source="idiom_dict")` 昇格。**daily_cap 件で打ち切り**、超過分は
>   `capped` として返し次回 run に持ち越す。
> - `provenance_weight.HUMAN_SOURCES = frozenset({"reflect_confirmed", "idiom_dict"})`（重み 1.0）。
>   昇格レコードに `promoted_by="idiom_dict"` + `idiom_key` を残す。
> - `evolve.py`: daily_review の後に `result["idiom_autopromote"]` を常時 emit。
>
> ## 変更（PR2: 安全弁3点の配線）
> - **安全弁①**: userConfig `idiom_autopromote_daily_cap`（number・デフォルト 10）を
>   `.claude-plugin/plugin.json` に追加（既存項目と同じフラット number + description 粒度）。
> - **安全弁②**: `sections_weak_signals.py` builder（ADR-028）に
>   「本 run の idiom_dict 自動昇格 N 件（idiom 一覧）」行を追加。毎 evolve/audit で必ず surface。
> - **安全弁③**: `evolve-reflect --revoke-idiom <idiom_key>` 新規 CLI。
>   confirmed=False + revoked_at に戻し、該当 idiom_key 由来の `promoted_by="idiom_dict"`
>   corrections を `invalidated=True` に原子的 rewrite。`count_human_corrections` は
>   invalidated を除外（フェーズ進捗が正しく巻き戻る）。weak_signals の promoted=True は
>   維持（再提示しない）。
>
> ## Acceptance Criteria（最重要）
> - [ ] **confirmed 未設定の現状で `autopromote` が promoted=0 を返す**（実 PJ dry-run E2E で assert）
> - [ ] confirmed=True の idiom にだけ一致して昇格する
> - [ ] daily_cap 超過分が昇格されず `capped` で surface される
> - [ ] idiom_dict 昇格が `count_human_corrections` に 1.0 でカウントされる（フェーズ進捗が動く）
> - [ ] `--revoke-idiom` 後、invalidated 分が `count_human_corrections` から除外される（進捗巻き戻り）
> - [ ] revoke 済み idiom は autopromote の対象から外れる
> - [ ] 自動昇格が observability 両経路（markdown / 構造化）に surface される
> - [ ] dry_run でファイル不変

**依存**: #C（confirmed 化）。**PR サイズ**: 2 PR
（PR1: autopromote 本体 + HUMAN_SOURCES + cap ロジック ~300 行、PR2: userConfig + surface + revoke ~250 行）。

---

## issue #E — observability builder の文言強化（既存 builder・新入口を作らない）

**タイトル**: `weak_signals observability に「evolve で昇格可能」誘導を追記`

**body**:
> ## 背景
> weak_signals builder は登録済み（ADR-028）。未昇格 N 件を surface しているが、
> ユーザーを #C の入口（evolve の今日の修正確認）へ誘導していない。**新 builder は作らない**（#278 教訓）。
>
> ## 変更
> - `scripts/lib/audit/sections_weak_signals.py`: 戻り行に
>   「未昇格 N 件 → /evolve-anything:evolve の今日の修正確認で昇格可能」を追記。
>   `correction_review.remaining` が取れる文脈なら「backlog 消化中（残 X group）」併記。
>
> ## Acceptance Criteria
> - [ ] markdown 経路と構造化経路（collect_observability）の両方に同じ行が出る（ADR-028 単一ソース）
> - [ ] store 空のときは None（沈黙）を維持

**依存**: なし（#C と並行可・低リスク）。**PR サイズ**: 1 PR・~60 行。

---

## issue #F — 成長レポート（次フェーズまであと N 件・今日の昇格成果）

**タイトル**: `evolve レポート末尾に成長状態を決定論表示（あと N 件で次フェーズ / 今日の昇格成果）`

**body**:
> ## 背景
> 成長レベル/フェーズ/進捗バーは出ているが「あと何件で次フェーズか」「今日の昇格成果」が無い。
> ユーザーが「進化している実感」を毎日得られるようにする。
>
> ## 変更
> - **閾値の単一ソース化（決定・論点4）**: `growth_engine.py` に
>   `STRUCTURED_CORRECTIONS_TARGET = 10`（+ sessions/rules 閾値）をモジュール定数として切り出し、
>   `detect_phase` / `compute_phase_progress` 内のリテラルを置換（挙動不変・snapshot で確認）。
> - `scripts/lib/growth_report.py` 新規: `build_growth_report(...)`（決定論）。
>   閾値は **ハードコードせず growth_engine の定数を import**（二重実装の片直し事故 = #419 の轍を
>   構造的に防ぐ。閾値変更時に growth_engine だけ直せば判定とレポートが同時追従）。
> - `evolve.py`: audit phase 後に `result["growth_report"]` を top-level emit。
> - `skills/evolve/SKILL.md` Step 9: `growth_report.lines` を成長レベル表示直後に列挙。
>
> ## Acceptance Criteria
> - [ ] `corrections 7/10 — あと3件で構造化育成へ` 形式の行が出る
> - [ ] `今日の確認で idiom N 件が自動化対象に昇格` が #C/#D の結果から決定論で出る
> - [ ] corrections が閾値到達済みなら「達成・次フェーズ条件は sessions/coherence」を表示
> - [ ] growth_report に閾値リテラル（10 等）が直書きされていない（growth_engine 定数の import のみ）
> - [ ] growth_engine の定数切り出しで既存フェーズ判定の挙動が不変（snapshot 回帰）

**依存**: #C, #D（昇格成果を参照）。**PR サイズ**: 1 PR・~200 行。

---

## issue #G — #185 メタ検査（複数 PJ で集計値完全一致 = 測定バグ）

**タイトル**: `複数PJで集計値が bit-exact 一致したら測定バグ候補として audit に surface（#185）`

**body**:
> ## 背景
> learning_measurement_layer_diagnosis: 「全 PJ 同値カウント = 測定バグ強シグナル」。
> #419-#423 はこれを手動診断した。自動化して audit に乗せる。advisory のみ・スコア非関与。
>
> ## 変更
> - `scripts/lib/audit/measurement_bug.py` 新規: `detect_measurement_bug(metrics_by_pj)`。
>   **決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出**。
>   ≥3 PJ で bit-exact 一致したら候補。0 同値は未測定・データ不足で正当に起きる（#423 既出）ため
>   除外し FP を構造的に避ける。precision 優先は ADR-043 の方針と整合。
> - `scripts/lib/audit/sections_measurement.py` 新規 builder を `_OBSERVABILITY_BUILDERS` 登録（ADR-028）。
>   データ源は growth-state-*.json walk（evolve-fleet status と同経路）。
>
> ## Acceptance Criteria
> - [ ] 3 PJ 以上で同一の **非ゼロ** env_score が出たら surface・1-2 PJ 一致は無視
> - [ ] 0 / 0.0 / None の一致は候補にしない（テストで明示 assert）
> - [ ] markdown / 構造化両経路に伝播

**依存**: なし（完全独立）。**PR サイズ**: 1 PR・~180 行。Closes #185。

---

## 全 issue 共通チェックリスト（PR ごと）

- [ ] TDD First（実装前にテスト）
- [ ] dry-run ゼロ書込を実 PJ E2E で assert（合成 fixture だけで緑にしない）
- [ ] 新規/変更ストアは store_registry に宣言（#434・orphan_store 検出を踏まない）
- [ ] PJ slug スコープ（DATA_DIR 全PJ共通 pitfall・read 側照合）
- [ ] phase は常時 emit（eligible でなくても error でも result にキーを置く）
- [ ] 単体テストで LLM を呼ばない（no-llm-in-tests.md・本設計は全 phase が決定論なので該当箇所なし）
- [ ] `claude plugin validate` パス
- [ ] file-size-budget（500行で分割検討・各新規モジュールは小さく保つ）
