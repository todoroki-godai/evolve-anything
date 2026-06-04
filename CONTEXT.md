# rl-anything — Ubiquitous Language（用語集）

このプロジェクト固有の jargon を 1 語で decode するための共有言語。
AI も人も、ここの用語を使って会話・命名・記述する（Eric Evans, DDD）。

新しい概念を導入したら **必ずここに 1 行追記する**。腐った用語集は無いより悪い。
鮮度は `scripts/lib/glossary_drift.py`（spec-keeper の update が消費）が検出する。

- **意味** は 1 行で。詳細は SPEC.md / docs/decisions/ に委譲する（重複させない）。
- **初出** は概念が最初に入った issue（`#NNN`）または ADR（`ADR-NNN`）。

| 用語 | 意味 | 初出 |
|------|------|------|
| BES | 進化探索。後ろ向きサブゴール分解(#253)と前向き進化探索(#256)の総称 | #253 |
| MemTrace | episodic 検索エラーを 3 類型に分類し event_id へ帰属する診断 | #254 |
| slop | AI 定型句。日英 10 パターンを決定論 regex で検出 | #255 |
| subgoal fitness | 候補を 5 サブゴールに分解して返す密な中間フィードバック | #253 |
| SIRI | 成功軌跡からスキルを採掘→検証→蒸留する3段階。①採掘=`skill_extractor`（discover で発火し triage に合流）/②検証=chaos fitness/③蒸留=evolve | #291 |
| Observe hooks | LLM コストゼロで使用・エラー・修正を自動記録する hook 群 | ADR-002 |
| 直接パッチ最適化 | 遺伝的アルゴリズムでなく LLM 1 パスでパッチを当てる最適化方式 | ADR-003 |
| coherence | fitness の一種。構造的整合性 4 軸スコア | ADR-004 |
| telemetry | fitness の一種。行動実績テレメトリ 3 軸スコア | ADR-005 |
| constitutional | fitness の一種。原則ベース LLM Judge 評価 | ADR-006 |
| env_score | environment fitness の統合スコア（0.0-1.0）。growth-level の素 | ADR-004 |
| cross-PJ recall | keyword 決定論で全 PJ memory を横断検索（vector 非採用） | ADR-025 |
| pitfall-curate | PJ 非依存の pitfalls.md キュレーション（自己進化専用の manager とは別物） | ADR-026 |
| 正準フォーマット収束 | pitfalls.md を寛容パーサでなく書式収束で扱う方針（無破壊 lint） | ADR-027 |
| observability contract | 必ず surface すべき行を単一ソース `_OBSERVABILITY_BUILDERS` 化し markdown/構造化の両経路が消費する契約 | ADR-028 |
| silence ≠ evaluated | 沈黙だと「評価して該当なし」か「配線漏れ」か区別できない。該当なしでも ✓ を1行残す原則 | ADR-028 |
| Belief Entropy | 生成後の memory 要約がソース corrections を保持(retention)・非接地化(drift)していないか測る決定論ゲート。memory_gating(生成前)の後段 | #285 |
| calibration drift | fitness の score-acceptance 相関が閾値を割った状態。audit で可視化＋trigger で evolve-fitness を proactive 提案（変更は人間承認 MUST） | #286 |
| component transfer | 更新コンポーネント（追加スキル）別に既存スキルの成功率 delta を isolation window で分離し「どの更新が回帰させたか」を帰属する negative transfer の ablation 版 | #288 |
| eval saturation | forward-gen trigger eval が「緑でも頑健でない」飽和兆候（positive 偏重/易しい negative/クエリ過少）を eval 実行なし決定論で測る。TASTE 着想、calibration drift と同帯で surface | #292 |
| self_analysis | evolve の result を読み 3 カテゴリ（提案矛盾/phase 例外/系統的却下）の issue 候補を生成する evolve メタ層の自己解析。`evolve_introspect` が決定論生成、Step 11 が人間承認後 todoroki-godai/rl-anything へ半自動起票 | #299 |
| SkillPyramid | 同一クラスタの低レベル（小型）スキル群を上位スキルへ束ねる階層統合提案。reorganize が split/merge と別軸（低→上位）で検出し max_skill_count 張り付きを構造的に抑える。`hierarchy_candidates` で surface、適用は人間判断 | #303 |
| hook_drift | 他ツール追従 hook（gstack flow 参照 hook 等）の陳腐化を決定論検出する `scripts/lib/hook_drift.py`。第一フェーズは stale_pin のみ | ADR-036 |
| stale_pin | hook が参照する外部ツールの version pin（flow-chain.json の `gstack_version`）が実環境（.last-setup-version）から乖離した状態。表記ゆれが無く false positive しない drift 種 | ADR-036 |
| ファイルベース2相 | claude -p を Python から追い出す3相分離。Phase A（決定論=リクエスト JSON 生成）→ Phase B（assistant がインライン採点/生成、subscription 課金）→ Phase C（決定論=応答パース・ゲート）。Bash 境界を JSON ファイルで越える | ADR-037 |
| llm_broker | ファイルベース2相の共通基盤 `scripts/lib/llm_broker.py`。build_requests/parse_responses/parse_score/passthrough を提供、IO-free・LLM-free（mock 不要） | ADR-037 |
