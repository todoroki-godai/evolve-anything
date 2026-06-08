# 用語集ブートストラップ詳細（Step 7.7）

Step 3.8 で surface した `result.observability.glossary_drift` に **`用語集未作成（CONTEXT.md 不在）`**
で始まる行があれば、用語集（Ubiquitous Language）を最初に作る trigger がどこにも無い PJ で、
未登録 jargon 候補が `SEED_MIN_CANDIDATES` 以上ある状態。creation が手動依存だと detection（drift 検出）が
永遠に発火しないため、evolve（ユーザーが明示的に回す per-project ループ）でここに作成を提案する。

> **なぜ contract 統合か（#275 → #278）**: #273 ではこの判定を散文ステップに書いたが phase 出力に
> 裏打ちされず実 evolve（docs-platform ev-v6）で消えた。#275 初版は独立 `glossary_seed` phase に
> 格上げしたが、#278 が「surface すべき行」を `_OBSERVABILITY_BUILDERS` 単一ソースに集約したため、
> seed 判定も `build_glossary_drift_section` が emit する形に統合（surface パターンを1本化、
> markdown と `result.observability` の両経路へ自動伝播）。決定論・LLM 非依存。

## observability 出力の利用（判定は済んでいる — 再実行しない）

`result.observability.glossary_drift`（list[str]）を読む:
- `用語集未作成（CONTEXT.md 不在）` 行がある → seed 適格。候補件数とリストは同じ行に含まれる
- 行が無い / `✓ 構造 drift なし` 等 → CONTEXT.md は既にある or 候補が薄い → このステップは黙ってスキップ

## seed 適格のとき

- **`--dry-run` の場合**: 書き込みはせず、Step 3.8 で surface した行をそのままレポートに残す（MUST、観測可能性）。
- **通常実行の場合**: 以下の AskUserQuestion 提案フローに進む。

**通常実行 + seed 適格の場合のみ AskUserQuestion**（提案詳細プロトコルに従う）。LLM で意味を埋めるため、
**件数とトークン見積もりを事前提示する**（プロジェクトの llm-batch-guard 準拠・MUST）:

```
CONTEXT.md が無く、未登録 jargon 候補が {N} 件あります（{候補リスト}）。
LLM で意味を埋めた用語集ドラフトを生成しますか？
（SPEC.md + CLAUDE.md を読み {N} 語の意味を生成。入力 ~{Xk} tokens 見積もり）

A) 生成する — 各行を ⚠UNVERIFIED でマークし、後で確認
B) Skip — 今は作らない
```

**A を選んだ場合のみ**:
1. SPEC.md / CLAUDE.md を読み、各候補語の意味を **1 行で** 生成する。決め打ちで埋めず、
   SoT から意味が確認できる語のみ対象にする（確信が持てない語は除外し B 扱い）。捏造しない
2. `rows = [(term, meaning), ...]` を作り、決定論 writer で書き出す（**LLM は整形に関与しない**）:
   ```python
   gd.write_context_seed(context_path, rows)  # 既存があれば FileExistsError（非破壊）
   ```
3. 全行の初出列に `⚠UNVERIFIED` が入る。これは「人間が意味を確認し初出を `#NNN`/`ADR-NNN` に
   書き換えてマーカーを外す」までの未検証マーク。**drift gate には載らず**、以後の evolve/audit が
   `unverified_terms` advisory で確認を促し続ける（誤り毒・検出器自滅の回避）
4. ユーザーに「CONTEXT.md を {N} 語の seed で生成しました。意味は LLM 推定なので確認してください」と報告

> **なぜ silent でなく確認 + UNVERIFIED か**: 用語集は jargon の権威ある decode。LLM が黙って
> 埋めると誤った意味が静かに混入し「腐った用語集は無いより悪い」状態になる。また SoT から全自動で
> 埋めると drift 検出器の検出対象が消え自滅する。確認 1 回と未検証マーカーでこの両方を防ぐ。
