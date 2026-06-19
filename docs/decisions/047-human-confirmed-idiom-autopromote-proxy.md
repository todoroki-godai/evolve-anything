# ADR-047: human-confirmed idiom proxy — 人間が一度承認したパターンの機械的再適用を human-source（重み 1.0）として扱う

- Status: Proposed
- Date: 2026-06-12（採用案を「1.0 同等扱い + 安全弁3点」で同日確定）
- Issue: #431（correction capture 二層化）, 日次 evolve リワードループ #D
- Related: ADR-041（evolve decision capture: accept は決定論 proxy に落とす）, ADR-046（advisory→weight 昇格判断）, ADR-028（observability contract 単一ソース）, ADR-042（DATA_DIR 一元化）, ADR-031（worktree 安全 slug）, learning_skill_md_must_not_enforcement, learning_install_is_not_enforcement, design: docs/evolve/daily-evolve-reward-loop-design.md

## 背景（症状）

実測（2026-06-12, `~/.claude/evolve-anything/`）:
- `weak_signals.jsonl`: 313 件・**全件 channel=llm_judge・promoted=False・昇格 0 件**。
- `correction_idioms.jsonl`: 313 行・**全件 provenance.judge="llm_haiku"**（人間未確認）。
- `corrections.jsonl`: human-source は 9 件中 1 件。フェーズ昇格条件 `corrections>=10`
  （human-source のみカウント・provenance_weight）が永久未達。全 11 PJ が initial_nurturing 固定。

#431/#432 の二層化により、Haiku がバッチで「修正らしい発話」を判定し weak_signals(channel=llm_judge)
+ 個人辞書(correction_idioms) に隔離記録する仕組みは動いている。だが **corrections 本流への昇格は
人間確認を必須**にしてある（provenance_weight: `HUMAN_SOURCES = {"reflect_confirmed"}`）。
これは正しい設計だが、人間確認の入口が reflect SKILL Step 7.7 の散文ステップだけ
（learning_skill_md_must_not_enforcement: 実質発火しない）→ 昇格が構造的に 0 のまま。

日次 evolve リワードループ #C で「今日の修正確認」を evolve phase に移し、人間が y/n で承認する
入口は作る。しかし **一度人間が承認したパターン（idiom）と同じシグナルが再び現れるたびに、
また人間に y/n を聞くのは非効率**。「四国めたんじゃなくて」を 5 回承認させられるのは UX 退行。

## 問題

human-confirmed な idiom に一致する新規 weak_signal を、人間に再確認させずに corrections へ
昇格してよいか。昇格する場合、その corrections レコードを **フェーズ昇格カウント対象（human-source）**
とみなしてよいか。

## 検討した選択肢

### 却下案 A: llm_judge シグナルを直接 corrections へ昇格（人間確認をスキップ）
- Haiku の意味判定だけで corrections 本流に入れ、フェーズ昇格もカウントする。
- **却下理由（ロンダリング）**: provenance_weight の設計思想（#431 提案3）は
  「機械ノイズで状態を動かさない」。Haiku 判定を human-source に昇格させると、
  313 件が雪崩式に corrections へ流れ込み、`corrections>=10` を即座に満たして全 PJ が
  偽の構造化育成フェーズに昇格する。これは「機械生成で状態が動く」ことそのもので、
  #431 が防ごうとした症状の再来（= LLM 判定を human ラベルで洗浄する＝ロンダリング）。

### 却下案 B: 全件を毎回人間が手動確認（自動昇格なし）
- 既存の reflect 確認フローのまま、再出現も毎回 y/n。
- **却下理由（スループット不足）**: figma-to-code だけで 116 件・全 PJ 313 件。日次 5 件確認でも
  消化に数十回の evolve を要する。さらに同一 idiom の再出現を毎回聞くため、確認総数が
  ユニーク idiom 数を大きく超える。learning_skill_md_must_not_enforcement の通り、
  手間が増えるほど運用は形骸化し、結局昇格 0 のまま固定される。

### 却下案 D: idiom_dict に割引重み（例 0.8）を付けて実数カウント
- `is_human_correction` を bool でなく重みに拡張し（reflect_confirmed=1.0 / idiom_dict=0.8）、
  `count_human_corrections` を実数和にする（ADR-046 の重み昇格と同型の発想）。
- **却下理由（フェーズ表示の整数性が崩れる）**: 成長レポートの中核 UX は
  「corrections **7/10** — あと **3 件**で次フェーズ」という整数の残数表示（設計 §機能#6）。
  重み和を導入すると「7.4/10 — あと 2.6 件」のような実数になり、ユーザーが
  「あと何回 y/n すれば進むのか」を直感できなくなる。proxy の FP リスクは重み割引という
  カウント側のレバーではなく、**安全弁 3 点（量の上限・可視化・取り消し）という別レバー**で
  吸収する方が、体験を壊さずリスクだけ削れる。

### 却下案 E: idiom_dict 昇格を 2-4 週 advisory 並走させてから重み昇格判断
- ADR-046 outcome metrics と同じ「advisory（表示のみ）→ 実測 → 重み昇格」の段取りを踏む。
- **却下理由（体験ゴールの遅延）**: 本イニシアチブのゴールは「毎日 evolve を叩くだけで
  環境が自然に進化していく」体験であり、フェーズ進捗が動くこと自体が報酬ループの中核。
  advisory 並走中はフェーズが動かず、ユーザーは数週間「y/n しても何も進まない」を経験する
  — これは昇格 0 件で固定されている現状の症状の再生産になる。ADR-046 の outcome metrics が
  advisory から始めたのは「重みの値を実測なしに決められない」からだが、本件は重みが
  1.0（人間判断の決定論的展開）と原理的に定まっており、実測すべき未知数がない。
  FP リスクは安全弁 3 点で運用中に継続的に吸収・監査できる。

### 採用案 C: human-confirmed idiom proxy（重み 1.0 + 安全弁3点）
- 人間が #C の review で一度「はい」と承認した idiom に `confirmed=True` を立てる。
- confirmed idiom に `idiom_key` 一致する**新規**未昇格 weak_signal を、人間再確認なしで
  `source="idiom_dict"` + `promoted_by="idiom_dict"` で自動昇格する。
- この source を `HUMAN_SOURCES` に加え、**重み 1.0 で**フェーズ昇格カウント対象とする
  （フェーズ表示は「7/10」の整数のまま）。
- FP リスクは下記の**安全弁 3 点**で吸収する。

## 決定

**採用案 C（1.0 同等扱い + 安全弁3点）を採る。** 根拠は ADR-041 の確立した原則 — 「accept は決定論 proxy
（適用差分）に落とす」 — の idiom への一般化:

> 人間の判断は一度行われれば、その判断を表す **決定論的 proxy（confirmed idiom + idiom_key 一致）**
> を介して機械的に再適用してよい。proxy が人間の明示承認に 1:1 で接地している限り、
> 再適用は新たな機械判断ではなく **過去の人間判断の決定論的展開**である。

したがって idiom_dict 昇格は human-source とみなせる（人間が承認した idiom_key にだけ反応するため）。

### 安全弁 3 点（1.0 同等扱いとセット・FP リスクをカウント側でなく運用側で吸収）

| # | 安全弁 | 実装 |
|---|---|---|
| ① | **日次自動昇格の上限** | userConfig `idiom_autopromote_daily_cap`（number・デフォルト 10）。既存 userConfig 18 項目と同じ「フラット number + description にデフォルト明記」の粒度。上限超過分は昇格せず次回 run へ持ち越し、`result["idiom_autopromote"]["capped"]` で surface。1 回の confirmed 化が引き金で大量昇格する暴走を量で抑える |
| ② | **自動昇格の毎回 surface** | `sections_weak_signals.py` builder（ADR-028 observability contract）に「本 run の idiom_dict 自動昇格 N 件（idiom 一覧）」行を追加。markdown / 構造化の両経路に自動伝播し、evolve レポートで必ず人間の目に入る（黙って進まない） |
| ③ | **idiom 単位の取り消し** | `evolve-reflect --revoke-idiom <idiom_key>`: confirmed=False + `revoked_at` に戻し、該当 idiom_key 由来の `promoted_by="idiom_dict"` corrections レコードを `invalidated=True` に原子的 rewrite（`_rewrite_promoted` と同型）。`count_human_corrections` は invalidated を除外するため、フェーズ進捗が正しく巻き戻る。weak_signals 側の `promoted=True` は維持（再提示によるノイズを避ける） |

### 不変条件（雪崩防止・ロンダリング防止の構造的保証）

1. **confirmed=True が立つまで自動昇格は一切発動しない**。
   現 313 idiom は全件 `provenance.judge="llm_haiku"`・`confirmed` フィールド未設定（=False 扱い）。
   起動時点で `idiom_autopromote` は必ず `promoted=0` を返す（実 PJ dry-run E2E で assert・#D AC）。
   confirmed は #C の人間 y/n でしか立たない。**人間が承認していないパターンは絶対に自動昇格しない。**
2. **idiom_key の物理接地**。confirmed は idiom + 元発話の物理キー（source_path:line_no）の
   安定ハッシュ（`compute_idiom_key`）に紐づく。Haiku が別発話から同じ文字列を拾っても別 idiom_key
   なので、confirmed の効果は「人間が見た具体的なパターン」に限定される（過剰一般化しない）。
3. **provenance を潰さない**。idiom_dict 昇格レコードは `promoted_by="idiom_dict"` +
   `idiom_key=<確認済み>` を残す。後から「これは proxy 再適用だった」と監査・巻き戻しできる。
4. **TTL との整合**（ADR との接続）。weak_signals は 45日 TTL（#A）で expired マークされ、
   expired は昇格候補から除外される。confirmed idiom も古い発話に紐づくものは自然減衰する。

### スキーマ変更（store_registry #434 宣言更新が必要）

- `correction_idioms.jsonl`: `confirmed` (bool) / `confirmed_at` (ISO8601|null) /
  `confirmed_by` (str|null = "daily_review") / `revoked_at` (ISO8601|null・安全弁③) を追加。
  retention=permanent 維持。
- `provenance_weight.HUMAN_SOURCES`: `frozenset({"reflect_confirmed", "idiom_dict"})` に拡張
  （重み 1.0・bool 判定のまま）。`is_human_correction` / `count_human_corrections` は
  `invalidated=True` のレコードを除外する（安全弁③）。
- `corrections.jsonl` の idiom_dict 昇格レコードに `promoted_by` / `idiom_key` /
  `invalidated`（安全弁③・初期 False）フィールドを追加
  （既存 reflect_confirmed レコードは不変・後方互換）。
- userConfig（manifest）: `idiom_autopromote_daily_cap`（number・デフォルト 10・安全弁①）を追加。

## 影響

- フェーズ昇格カウントが human 承認済みパターンの再出現でも進むようになり、
  「毎日 evolve を叩くと corrections が増えて次フェーズに近づく」体験が成立する。
  フェーズ表示は「7/10」の整数のまま（却下案 D の実数和を避けた効果）。
- ロンダリングリスクは不変条件1（confirmed ゲート）で、FP 暴走リスクは安全弁①②③
  （量の上限・毎回可視化・取り消し+巻き戻し）で構造的に封じられる。
- corrections.jsonl のセマンティクスが「人間が直接書いた」から「人間が承認したパターンの展開」へ
  わずかに広がる。`source`/`promoted_by`/`invalidated` で区別可能なので監査性は維持される。
- 万一 1.0 同等扱いが過剰と実運用で判明した場合も、`promoted_by` が全レコードに残っているため
  後から重み付け再計算・一括 invalidate が可能（安全弁③の一般化で対応でき、ADR の差し替えで済む）。

## 検証（Test Plan 必須）

- [ ] **起動時無発火 E2E**: 実 PJ（confirmed 未設定の現状）で `--dry-run` evolve を流し、
      `result["idiom_autopromote"]["promoted"] == 0` を assert。
- [ ] confirmed=True の idiom を 1 件作り、一致する新規 weak_signal が idiom_dict で昇格し、
      `count_human_corrections` が +1（重み 1.0）されることを確認。
- [ ] daily_cap（デフォルト 10）超過分が昇格されず `capped` で返ることを確認（安全弁①）。
- [ ] 自動昇格が observability 両経路（markdown / 構造化）に surface されることを確認（安全弁②）。
- [ ] `--revoke-idiom` 後: confirmed=False + revoked_at が立ち、該当 corrections が
      invalidated=True になり、`count_human_corrections` から除外される（進捗巻き戻り・安全弁③）。
- [ ] revoke 済み idiom が autopromote の対象から外れることを確認。
- [ ] dry_run でファイル不変（pitfall_dryrun_stateful_store_write）。
- [ ] idiom_dict 昇格レコードに promoted_by / idiom_key が残ることを確認。
