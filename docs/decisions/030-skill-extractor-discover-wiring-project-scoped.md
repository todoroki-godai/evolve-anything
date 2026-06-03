# ADR-030: skill_extractor (SIRI ①) を discover に配線し、採掘は project スコープに限定する

Date: 2026-06-03
Status: Accepted
Related: #291（実装）, #238（skill_extractor Phase 1 実装）

## Context

成功軌跡からスキルを採掘する `skill_extractor`（SIRI ① 相当、arXiv 2606.02355）は #238 Phase 1 で実装済みだったが、`SPEC.md` / `spec/architecture.md` / 自身のテストからしか参照されず、**discover / evolve / audit / hooks のいずれの recurring ループからも呼ばれていなかった**。tech-eval で照合した結果「実装済みだが配線漏れで休眠」状態が判明した。

これは過去に複数回踏んだ「version ≠ enforcement」「install ≠ enforcement」と同型の失敗である（[learning_install_is_not_enforcement]）。モジュールが存在することと、それが実際に発火することは別問題で、配線先を「分類上の正しさ」（"採掘は spec 系だから spec-keeper 管轄"等）で選ぶと、ユーザーが滅多に回さない手動 CLI に置いてしまい実質死蔵する。

配線にあたり、`extract_skill_candidates` は既定で `~/.claude/projects/` 全体（全 PJ）を walk する設計だった。これを discover にそのまま接続すると、project スコープで動く discover の他検出（`query_sessions(project=...)` 等）と粒度が食い違い、無関係な他 PJ の成功軌跡が候補に混入する。実際、グローバル採掘のまま接続したところ、空の tmp project に対する discover が他 PJ 由来の候補を `missed_skill_opportunities` に混入させ、既存テスト `test_report_no_missed_skills` が回帰した。

## Decision

1. **配線先は recurring ループ = `run_discover()`**。discover は evolve Phase 2.6 が消費するため、evolve のたびに自動発火する。手動 CLI・単発スキル止まりにしない。
2. **採掘は project スコープに限定する**。`_project_transcript_dir()` で project_root を CC の transcript ディレクトリ命名規則（`str(path)` の `/` と `.` を `-` に置換）でエンコードし、`extract_skill_candidates(projects_root=...)` に渡す。これで discover と同じ project 粒度に揃え、cross-PJ noise を防ぐ。
3. **出力は既存の合流点に接続する**。`generalizability_score >= TRAJECTORY_SKILL_SCORE_THRESHOLD`（既定 0.25）でフィルタして `trajectory_skill_candidates` に surface しつつ、純粋ヘルパー `_trajectory_candidates_to_missed()` で triage 互換の `missed_skills` 形式（`skill` / `session_count` / `triggers_matched`）へ変換し、`missed_skill_opportunities` へ extend する。新しい合流パスを作らず、既存の CREATE/UPDATE 判定 + `meta_quality_check`（#203）の noise フィルタに乗せる。
4. **閾値を noise lever として config 化する**。採掘候補の精度が低く noise になる場合は `TRAJECTORY_SKILL_SCORE_THRESHOLD` を引き上げて対処する（issue #291 の再評価条件）。

## Alternatives Considered

### 代替案A: グローバル採掘のまま配線する
`extract_skill_candidates` の既定（全 PJ walk）をそのまま使う。実装は最小だが、project スコープで動く discover と粒度が食い違い、他 PJ の軌跡が候補に混入する。実際に既存テストが回帰し、cross-PJ noise が観測されたため却下。

### 代替案B: spec-keeper / 専用 CLI に配線する
"採掘は仕様・スキル設計の領域だから spec 系スキルが管轄" という分類上の整理は可能。しかし spec-keeper は手動で叩く単発スキルであり、ユーザーが思い出して回す依存になる＝休眠状態の再生産。recurring に回る discover/evolve でないと「evolve のたびに候補が浮上する」という目的（#291 After 像）を満たせないため却下。

### 代替案C: 新しい候補チャネルを triage に追加する
trajectory 候補専用の入力パスを skill_triage に新設する案。triage 側の改修が必要で、CREATE/UPDATE 判定ロジックと meta_quality フィルタを二重持ちになる。既存の `missed_skill_opportunities` 契約（`skill`/`session_count` を `.get()` 参照）に変換して合流させれば triage 無改修で済むため却下。

## Consequences

- discover/evolve を回すたびに、当該 project の成功軌跡から採掘したスキル候補が `trajectory_skill_candidates` として surface し、既存の skill-triage CREATE/UPDATE パスに合流する。SIRI ①（採掘）が宙に浮いた状態を解消。
- 採掘は当該 project の transcript のみを見る。worktree から回した場合は worktree 固有の transcript ディレクトリにスコープされる（discover の既存の project 粒度と一致）。
- スキル名の名前空間ゆらぎ（`<command-name>` 生 vs CLAUDE.md 宣言名）で既存スキルが CREATE 候補に誤ルートする可能性は残るが、`TRAJECTORY_SKILL_SCORE_THRESHOLD` + `meta_quality_check` + 最終人間承認ゲートで緩和される。精度が問題化したら閾値を上げる。
- 決定論・LLM 非依存を維持（採掘・スコアリング・変換すべて）。
