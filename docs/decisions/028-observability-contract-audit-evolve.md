---
date: 2026-05-29
status: accepted
---
# observability は markdown 選択読みでなく audit↔evolve の構造化 contract で surface する

## Context

[#272](../../CHANGELOG.md) で audit の `build_unmanaged_pitfalls_section` を「候補ゼロでも `✓ 評価したが該当なし` を1行残す」よう改修し、`silence != evaluated`（沈黙だと「評価して該当なし」か「配線が走っていない」か区別できない）の穴を audit 単体では塞いだ。

ところが docs-platform で evolve を実行したセッション（ev-v6, プラグイン **v1.78.0** = fix 込み）のログを精査すると、`✓` 行が**一度も surface されていなかった**。

原因は配線にあった:

- `run_evolve` の Phase 3 は `run_audit()` の戻り値（217KB の markdown レポート、約3961行）を `result["phases"]["audit"]["report"]` に**丸ごと文字列で格納**するだけ。
- evolve の SKILL.md は assistant に対し**名前付きフェーズ**（fitness / layer_diagnose / skill_evolve / pitfall_hygiene / remediation 等）を個別に「確認しろ」と指示している。
- Unmanaged Pitfalls / Glossary Drift の observability 行はレポート**中盤**（rules 行数バジェット直後・Skill Quality Trends 直前）に埋もれており、選択読みの読み込みウィンドウに入らず、サマリにも出ない。

つまり **`silence != evaluated` 原則が、その原則を守るための観測性 fix 自身の配線で再発した**。ユーザーが日常的に叩くのは audit 単体でなく evolve なので、観測性の意図が実質的に届いていなかった。

## Decision

audit が「必ず surface すべき observability 行」を**構造化フィールド**として返し、evolve はそれを決定論的に出力する。markdown 経路と構造化経路は**単一ソース**を共有する。

1. `scripts/lib/audit/observability.py` を新設し、`_OBSERVABILITY_BUILDERS`（`(key, builder)` のリスト = observability セクションの単一ソース）と `collect_observability(project_dir) -> Dict[str, List[str]]` を定義する。builder が `None` を返す項目（その PJ に非該当: CONTEXT.md / pitfalls.md が無い）は除外する。
2. `report.py`（markdown 経路）を、個別呼び出し（`build_glossary_drift_section` + `build_unmanaged_pitfalls_section`）から `_OBSERVABILITY_BUILDERS` の反復消費に統一する。これで markdown と構造化が**同じ順序・同じ内容**になる。
3. `run_evolve` は audit phase 直後に `result["observability"] = collect_observability(proj)` を格納する。
4. evolve の SKILL.md に **Step 3.8: Observability（必ず surface する — MUST）** を新設し、「`observability` フィールドの各 key の行をそのまま必ずサマリに列挙する。clean（`✓`）でも省略しない。`report` は参考に留め、この構造化フィールドを正準ソースとする」と指示する。

将来 observability セクションを足すときは `_OBSERVABILITY_BUILDERS` に1行登録するだけで markdown 経路と構造化経路の両方に自動伝播する（モグラ叩きにならない）。

## Alternatives considered

- **`run_audit` の戻り値型を変えて observability を同梱する**: audit skill の CLI（`print(run_audit(...))`）が「str を返す」契約に依存しており、ブラスト半径が大きい。却下。
- **evolve が markdown から observability 行を文字列抽出する**: まさに今回潰している「埋もれた行を脆く拾う」結合の復活。却下。
- **Unmanaged Pitfalls だけを専用フィールドにする**（最小修正案 B）: 次に observability セクションを足したとき同じ穴を再度掘る。senpai 相談で「markdown blob を選択読みさせる設計自体が observability の単一障害点を量産している」と指摘され、contract 化（全 observability セクションを単一ソース化）へ拡大した。

## Consequences

- builder（`discover_pitfalls` の `rglob` 等）は markdown 経路（`generate_report` 内）と `collect_observability` で**計2回**走る。決定論 read のため結果は同一で、これは意図的トレードオフ（戻り値型変更＝CLI 波及、または文字列抽出＝脆い結合、を避ける対価）。evolve は既に 217KB レポート生成 + constitutional LLM 呼び出しをしており、`rglob` 1パス追加は無視できるコスト。
- `report.py` から `build_glossary_drift_section` / `build_unmanaged_pitfalls_section` の直接 import を削除（observability.py 経由に一本化）。`collect_observability` を `audit/__init__.py` から re-export。
- contract テスト7件を追加。中でも `test_report_markdown_uses_same_single_source` は `collect_observability` が返す全セクションの見出しが `generate_report` の markdown にも出ることを検査し、**片方だけに項目が出る drift を回帰ガード**する。
- 実 PJ docs-platform で `run_evolve(dry_run=True, skip_llm_evolve=True)` を実行し、`result["observability"]["unmanaged_pitfalls"]` に `✓ enable すべき育った pitfalls.md なし（検査 4 件すべて登録済み）` が surface することを確認（ev-v6 で消えていた行が構造化フィールドとして取り出せることを実証）。
- `observability` フィールドの型は成功時 `Dict[str, List[str]]` / エラー時 `Dict[str, str]`（`{"error": ...}`）。SKILL.md Step 3.8 でエラー時はそのまま表示すると明記。
- この ADR は [ADR-009](009-simplify-pipeline-3-stage.md)（evolve は Diagnose 段で audit を消費する）の上に立つ。「install ≠ enforcement」「silence ≠ evaluated」の系譜（#268 glossary→audit 配線、#271 Unmanaged 可視化、#272 ✓ 行）の "もう一段上"（audit→evolve の surface 保証）に当たる。

## References

- 実装: `scripts/lib/audit/observability.py`, `scripts/lib/audit/report.py`, `skills/evolve/scripts/evolve.py`, `skills/evolve/SKILL.md`
- テスト: `scripts/tests/test_observability_contract.py`
- 関連 ADR: [009 3ステージパイプライン](009-simplify-pipeline-3-stage.md)
- 学習: silence != evaluated は1箇所塞いでも consumer 側の選択読みで再発する（surface は生成側でなく出力経路の契約で保証する）
