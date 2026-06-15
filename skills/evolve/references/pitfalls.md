# Pitfalls


## Active Pitfalls


### audit が markdown のみ保持し構造化値を捨てる配線漏れ
- **Status**: Active
- **Last-seen**: 2026-06-15
- **Root-cause**: integration — Phase 3 Audit が `run_audit(...)`（戻り = markdown レポート文字列）だけを `result["phases"]["audit"]["report"]` に格納し、内部で算出済みの構造化 env_score を捨てていた。SKILL.md / references/report-narration.md の「Report クライマックス（成長レベル）」はトップレベル `result["env_score"]` を読む設計なのに、その field が存在せず成長レベル演出が一度も発火しなかった（silence != evaluated 原則の自己違反）。LLM 評価関数が「markdown を返す」型だと、内部で持っている構造化スコアが出力 result に surface されず doc 側 reader と食い違う（#523-2/#526-2）。
- **Avoidance**: markdown を返す評価フェーズの後段で、reader（SKILL.md/references）が読むトップレベル field を**同じ権威ソースの構造化算出**（`compute_environment_fitness` → `compute_level`）から取り直して明示的に surface する。markdown を正規表現でパースする対症療法は避ける。算出失敗時は黙らず degraded=True（前回 level フォールバック）を置く。回帰テストは reader が読むキー名・dict 形・degraded 分岐を直接 assert する。
- **Pre-flight対応**: No
- **Avoidance-count**: 0


### Bash 連続実行後に先送り表現が出やすい
- **Status**: Active
- **Last-seen**: 2026-05-19
- **Root-cause**: behavioral — Bash を 3 回以上連続で実行すると「後で」「別途」「しましょうか？」などの先送り表現を伴う応答が出やすい（rl-anything/docs-platform/sys-bots の3PJ横断セッションログ解析より）。stop hook がブロックするが根本は「タスクが長くなりすぎている」サイン。対処: タスク分割 or subagent 即時委譲。
- **Pre-flight対応**: No
- **Avoidance-count**: 0

### discover オーケストレータの try/except 外 dict subscript が全フェーズを落とす（#521 / #526-3）
- **Status**: Active
- **Last-seen**: 2026-06-15
- **Root-cause**: coding — `run_discover` で内部検出関数の戻り値を **try/except の外**で `result[k]` subscript していると、その関数が None / 想定キー欠落を返した瞬間 `'NoneType' object is not subscriptable` で run_discover 全体が落ちる。さらに上位 `evolve.py` Phase 2 の except が `{"error": str(e)}` だけ残し traceback を捨てるため root cause が永久に観測不能になり result は緑に見える。下流 SKILL.md（`reflect_data_count >= 5`）は欠落キーで None 比較 TypeError になる。
- **対処**: ①各検出ブロックを既存の `try/except → result["<name>_error"] = str(e)` パターンでガードし、None 戻りは `raise TypeError(...)` で観測可能化（握り潰さない）。②`detect_missed_skills` / `_enrich_patterns` の戻りは `.get()` で読む。③`evolve.py` の except は `traceback.format_exc()` を `result["phases"]["discover"]["traceback"]` に残す。④下流が依存する `reflect_data_count` は失敗時も欠落させず degraded sentinel `-1`（int）にフォールバックし、SKILL は数値比較前に `< 0` を先に判定する。**sentinel は必ず int に保つ**: str sentinel（"unknown"）だと CANONICAL の `kind=int` 契約に違反し、runtime self-detect（evolve_consistency）が `wrong_kind` drift を誤検出して幻の「契約乖離 issue」を自作する（/review #530 で発見・degraded 経路を実 conformance 付きで踏むテストが無く全スイート緑をすり抜けた）。
- **Pre-flight対応**: No
- **Avoidance-count**: 0

## Candidate Pitfalls


## Graduated Pitfalls

