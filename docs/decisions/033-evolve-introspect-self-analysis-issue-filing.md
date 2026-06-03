# ADR-033: evolve 実行後の自己解析を独立モジュールで行い、GitHub issue を半自動起票する

Date: 2026-06-03
Status: Accepted
Related: #299（実装）, [ADR-028]（observability contract）

## Context

evolve は Observe → Diagnose → Compile → Housekeeping → Report を回して**対象 PJ** を改善するが、**evolve 自身の実行結果**（提案の質・誤検出・実行時エラー）を振り返る経路が無かった。evolve が出した提案にバグや改善余地があっても、それは人間が気づいて手で issue を立てるまで構造に残らない。これは過去に複数回踏んだ「install ≠ enforcement」（自動で回るループに載らないものは育たない、[learning_install_is_not_enforcement]）と同型のメタ層の配線漏れである。

#299 はこのメタ層のループ（パイプライン自身を改善するループ）を閉じることを求めた。設計上の主要な分岐は2つ:

1. **解析の実体をどこに置くか** — 既存 audit の observability contract（`_OBSERVABILITY_BUILDERS`、[ADR-028]）に builder を1本足す形か、独立モジュールか。前者なら markdown/構造化の両経路に自動伝播しモグラ叩きを避けられる利点がある。
2. **起票モデルと起票先** — 全自動 vs 半自動、起票先 repo の固定 vs 対象 PJ。

## Decision

1. **独立モジュール `scripts/lib/evolve_introspect.py` として実装する**。observability builder は `(project_dir) -> list[str]` のシグネチャで、PJ の**静的状態**しか見られない。一方、自己解析の対象（各フェーズが `{"error": ...}` で握り潰した例外、split↔archive の提案矛盾、remediation の budget 悪化提案、systematic rejection / calibration regression）は **evolve の `result` dict（実行結果）** を読まないと検出できない。したがって builder の契約には載らず、`analyze_evolve_result(result, project_dir) -> dict` を持つ独立モジュールにする。ADR-028 の「surface すべきものを単一ソースに集約」という判断軸は踏襲するが、入力が project_dir でなく result なので配線は別系統になる。

2. **配線先は `run_evolve()` の末尾（全フェーズ集約後）**。`result["self_analysis"]` に格納し、**evolve のたびに自動発火**させる（手動 CLI・単発スキル止まりにしない＝install≠enforcement の再発防止）。

3. **3カテゴリの検出ロジックを持ち、0 件でも summary_line に ✓ を残す**（silence ≠ evaluated）:
   - `self_detection`: split↔archive 矛盾 / line-limit 超過ファイルへの additive fix 提案
   - `runtime_errors`: phase 例外 / observability 取得失敗
   - `improvement_opportunities`: systematic rejection / calibration regression

4. **起票は半自動（提案 → 人間承認 → 起票）**。evolve SKILL.md Step 11 が候補を per-item 提示し、AskUserQuestion で個別承認したものだけ `gh issue create` する。全自動起票はノイズ issue 量産・誤検出の固定化リスクがあるため採らない（silence≠evaluated の裏返し＝confident に誤検出を起票しない）。

5. **起票先は常に `todoroki-godai/rl-anything` 固定**。検出対象は evolve パイプライン自身のバグ・改善余地であり、evolve がどの PJ 上で動いても、起票先は rl-anything repo に集約する。`gh` のデフォルト（cwd の origin）ではなく `--repo todoroki-godai/rl-anything` を明示する。

6. **重複起票防止は root cause 単位の dedup_key を二段で照合する**。body に隠しマーカー `<!-- rl-evolve-introspect:<dedup_key> -->` を埋め込み（最強シグナル）、マーカー無しの手動起票にはタイトル類似度（SequenceMatcher、閾値 0.80）でフォールバックする（`filter_duplicates`）。dedup_key は提案単位でなく root cause 単位（例 `runtime_error:discover:<正規化シグネチャ>`）にし、`_error_signature` がパス・行番号・16進 ID を落として場所違いの同一例外を1件に潰す。これで同一原因が毎 evolve で重複起票されない。

7. **決定論・LLM 非依存**。検出・dedup・整形はすべて決定論。LLM が関与するのは「起票するか」の人間判断のみ（llm-batch-guard のトークン見積もりは不要）。

## Alternatives Considered

### 代替案A: observability contract に builder を足す
`_OBSERVABILITY_BUILDERS` に1行登録すれば markdown/構造化の両経路に自動伝播する。しかし builder のシグネチャは `(project_dir) -> list[str]` で、result の `phases.*.error` / 提案矛盾 / rollback を読めない。無理に載せると「未配線モジュール検出」のような project_dir だけで取れる静的情報に機能が縮小し、#299 の AC「3種の解析対象（自己検出 / 実行時エラー / 改善余地）」を満たせないため却下。判断軸（単一ソース surface）は踏襲しつつ、入力が違うので別モジュールにする。

### 代替案B: 独立スキル `/evolve-introspect` として切り出す
evolve とは別の手動スキルにする案。evolve から自動で呼ぶ配線を別途用意しないと AC「evolve のたびに自動で回る（手動 CLI 止まりにしない）」を満たせず、install≠enforcement を再生産する。evolve の result に密結合（result dict を入力に取る）しており独立実行の意味が薄いため却下。

### 代替案C: 全自動起票
承認を挟まず検出即起票。ノイズ issue を量産し、誤検出が issue として固定化される。`silence ≠ evaluated` の運用（誤検出を confident に surface しない）と整合しないため却下。半自動（人間承認）にする。

### 代替案D: 起票先を対象 PJ の origin にする
`gh` デフォルトの cwd origin に起票。検出対象が rl-anything のバグなのに、evolve を回した無関係な PJ の repo に issue が立つ。混乱を招くため却下し、`todoroki-godai/rl-anything` 固定にする。

## Consequences

- evolve を回すたびに `result["self_analysis"]` が生成され、Step 11 で3カテゴリの状態が必ず surface される（0 件でも ✓）。evolve 自身のバグ・改善余地が宙に浮く状態を解消。
- 検出した候補は人間承認のうえ rl-anything repo に集約して起票される。root cause 単位のマーカー dedup により、同じバグが毎 evolve で重複起票されない。
- 検出は `result` の構造（`phases.*.error` キー、remediation の `classified`、reorganize `split_candidates`、prune の archive 系キー、self_evolution の `false_positives.systematic_flags` / `regression`）に依存する。これらの phase 出力契約が変わると検出ロジックの追従が必要（テストが回帰検出する）。
- 新カテゴリの検出を足す場合は `evolve_introspect.py` に detector を1本足し、`analyze_evolve_result` に組み込む。surface（Step 11）と dedup（マーカー）は共通経路を再利用できる。
- 決定論・LLM 非依存を維持。起票判断のみ人間ゲート。
