# Changelog

## [Unreleased]

### Added
- **feat(fleet): `migrate-pj-slug` バックフィルを全7ストアに拡張（refs #602）** — #593 で新設した `bin/rl-fleet migrate-pj-slug`（幻PJ slug の遡及正規化）は実装当初 corrections / subagents / sessions.db の3ストアのみ対象だったが、worktree フルパス由来の汚染は実環境横断スイープで7ストア（追加: usage.jsonl / workflows.jsonl / skill_activations.jsonl / errors.jsonl / usage-registry.jsonl）に及ぶことが判明していた。`pj_slug_backfill.py` の対象を `_JSONL_STORES` 単一ソース宣言に集約し全7ストアへ拡張（既存 `_backfill_jsonl` / `_backfill_sessions_db` を再利用・新方式は発明しない）。各ストアの正規化フィールド名は writer hook の record 構築箇所を Read で確定（usage-registry のみ `project_path`、他4追加ストアは `project`）。dry-run 既定（1バイトも書かない）／`--apply` 実書込／冪等は3ストア版から不変。CLI 本体（`backfill`/`format_summary` を汎用に呼ぶ）はロジック変更なし・help 文言のみ追従。残課題（sibling-dir worktree の write 時解決 = SessionStart cache 案）は #602 に継続。決定論・LLM 非依存。TDD（追加5ストアの dry-run 書込ゼロ / apply 正規化 / 冪等 / worktree・通常・basename パターン + 全7ストア summary・計33件緑）。
- **feat(prune): zero_invocation suppress に解除予定日と自動再評価保証を surface（closes #587）** — usage 計測経路の修正（#478）により `zero_invocations_suppressed` が「計測待ち」になるが、**いつ再評価されるか／自動再評価の保証があるか**が surface されておらず「永久保留になり得る」と誤読させていた。調査の結果、再評価は既に構造的に保証されている（`zero_invocation_window_suppressed` は毎回 live 再計算され、観測窓が修正日をまたがなくなる `fix_date + 観測窓日数` 以降の prune/evolve 実行で suppress が自動で False に転じる／`insufficient_usage` も毎回 live `usage_count` から再計算）。欠けていたのは**解除予定日の可視化のみ**だったため、`zero_invocation_reeval_date()` ヘルパを追加し suppression summary に `reeval_date`・`auto_reeval` を構造化 + message に解除予定日を明示（「観測窓が揃う YYYY-MM-DD 以降の prune/evolve 実行で自動的に再評価される＝永久保留にはならない」）。report 描画は `zero_invocations_suppressed.message` を直接 surface する既存仕様のため描画コード変更なしで反映。決定論・LLM 非依存。TDD（`test_prune.py` に reeval_date surface / ヘルパ計算の2ケース + API surface snapshot 追従）。
- **feat(evolve): 高頻度 `rule_violation_observed` を hook_candidate へ昇格する導線を追加（closes #585）** — `builtin_replaceable` は `tool_usage_hook_candidate` に昇格して remediation proposable に乗るのに、`rule_violation_lane.py` が分離する `rule_violation_observed`（例: `rule_installed_but_not_enforced`・同一コマンドの高頻度違反）は **surface のみ**で hook 候補にも remediation proposable にも乗らず、「最も enforce すべき高頻度違反」が放置されていた（large 環境で `cd` 400回超の rule 違反が毎回観察のみ）。`rule_violation_lane.py` に閾値 gate 付きの hook_candidate 昇格を追加し `evolve.py` に配線。レーン分離（既存）→ 高頻度違反のみ hook 昇格、という builtin_replaceable と同型のフロー。決定論・LLM 非依存。TDD（`test_rule_violation_hook_promotion.py`）。

### Changed
- **refactor(evolve): `_env.py` を抽出 — env_score/slug/tier 系 helper をパッケージ分割（PR 2/8・refs #531）** — [ADR-048](docs/decisions/048-evolve-py-staged-package-split.md) 第2 PR。**振る舞いゼロ変更**: env/slug/tier 系の純粋 helper（`_resolve_data_dir` / `_resolve_evolve_slug` / `_resolve_pj_slug` / `_compute_env_score_struct` / `_env_score_degraded` / `_apply_remediation_suppression` / `_surface_constitutional_status` / `_count_env_artifacts` / `_tier_from_count` / `_compute_env_tier`）と定数 `ENV_TIER_THRESHOLDS` を `skills/evolve/scripts/evolve/_env.py`（297行）へ移設し、`__init__.py` で全名 re-export（`from evolve import X` 後方互換 + `setattr(evolve, ...)` 束縛フェンス維持）。`import re` は `_env` のみで使うため `__init__` から除去。**import 順序の罠を設計補正**: `DATA_DIR` / `EVOLVE_STATE_FILE` は `_env` から frozen 値を re-export すると `del sys.modules["evolve"]` + reimport で `CLAUDE_PLUGIN_DATA` を再評価する #517 契約（`test_evolve_data_dir_env`）が壊れる（`_env` は reimport されず frozen 化）ため、`__init__` で `_resolve_data_dir()` を呼び直して package 属性に束縛（解決ロジック自体は `_env` が単一ソース）。keyset snapshot 不変・既存223 test + 束縛フェンス4 test + env_tier/result_schema 全緑。
- **refactor(evolve): `_capture.py` を抽出 — warning/stderr sink ヘルパーをパッケージ分割（PR 3/8・refs #531）** — [ADR-048](docs/decisions/048-evolve-py-staged-package-split.md) 第3 PR。**振る舞いゼロ変更**: phase 実行中の warning/stderr を決定論的に捕捉する末端ヘルパー（`_capture_warnings`（#341）/ `_TeeStderr`・`_capture_audit_stderr`（#523-1））を `skills/evolve/scripts/evolve/_capture.py`（91行・他 sub-module に非依存）へ移設し、`__init__.py` で re-export（`from evolve import X` 後方互換維持）。移設で未使用化した `import warnings as _warnings` / `from contextlib import contextmanager` を `__init__` から除去（`sys` は他箇所利用で残置）。本文・docstring・#341/#523-1 コメントは原文ママ。keyset snapshot 不変・既存223 test + 束縛フェンス・result_schema 全緑。
- **refactor(evolve): `evolve.py`（1739行）をパッケージ化する足場 + 束縛フェンスを整備（PR 1/8・refs #531）** — file-size-budget の HARD 800 行を大幅超過する `evolve.py` を段階分割する [ADR-048](docs/decisions/048-evolve-py-staged-package-split.md) の第1 PR。**振る舞いゼロ変更**: (1) `skills/evolve/scripts/evolve.py` を `evolve/__init__.py` にパッケージ化（`from evolve import` は透過解決・sys.path 不変）+ `evolve/__main__.py` で `python3 -m evolve` 起動を提供。(2) **束縛フェンス**: 後続のフェーズ抽出 PR で `run_evolve`/`main` を別 module へ移すと `setattr(evolve, "<name>", ...)` の動的束縛がすり抜け **テスト緑のまま実関数が走る silent fail**（ADR が見落としていた罠）になるため、差し替え対象 helper（`check_data_sufficiency`/`check_fitness_function`/`run_evolve`/`_resolve_evolve_slug`）を `import evolve as _ev; _ev.<name>()` 経由に統一し束縛先をパッケージに集約。(3) 安全網テストを先行緑固定: `test_evolve_binding_paths.py`（束縛すり抜けの回帰フェンス4件）+ `test_evolve_keyset_snapshot.py`（実 dry-run result のキー集合 golden・純リファクタで不変を保証）。`bin/rl-dogfood-gate` の evolve 直叩きパスも `-m evolve` に追従。実装計画は `docs/refactoring/evolve-package-split-plan.md`。CANONICAL 契約 + 既存 218 test + 新規5 test + dogfood Layer1/2 全緑。
- **docs(evolve): dry-run 記録可否の一元表を SKILL.md 冒頭に追加（closes #588）** — evolve 手順が Step 0.5〜11 + 多数の MUST と長大で、「dry-run では記録しない」vs「drain は dry-run でも実行」のように**記録可否が Step ごとに分岐**し実行者が取り違えやすかった（実際に長い手順の終盤で実行ミスが発生）。SKILL.md 冒頭に「Step / 操作 / dry-run時 / 非dry-run時」の一元表（8行）を追加し、`mark_done`・`record_reviewed`（dry-run では書かない）と `rl-evolve --drain` の `persist_weak_signals_drain` / pending marker（dry-run でも書く＝#402/#513 の意図的設計）の**違いを明示**。ドキュメントのみ・コード変更なし。
- **perf(prune): PJスコープ evolve で global 淘汰候補をフル配列でなく件数サマリに畳む（closes #586）** — PJスコープの evolve でも prune が global 淘汰候補（実測 ~76件）を毎回フル配列で result に積み、数十KB を生成していた。global 候補は cross-PJ 使用状況が無いと判断できず consumer（SKILL.md）は既に件数1行に畳むだけなので、**producer 側で配列生成自体を止める**。`run_prune` に `pj_scoped: bool = True` を追加し、真のとき `global_candidates` を `make_global_candidates_summary` が返す `{"count", "pointer"}` dict に置換（`total_candidates` は件数を別途加算して維持）。cross-PJ 全件評価が要る CLI 走査では `pj_scoped=False` でフル配列を維持。SKILL.md の表示仕様を新形（`global_candidates.count`）に追従。決定論・LLM 非依存。TDD（`test_prune.py` に PJスコープ畳み / `pj_scoped=False` 全件維持 / 0件 の3ケース + API surface snapshot 追従）。

### Fixed
- **fix(outcome): worktree フルパス由来の幻PJ slug を書込境界で正規化＋既存レコードをバックフィルで回収（closes #593）** — `reflect` を worktree から回すたび、`project_path` に worktree フルパス（`.../.claude/worktrees/<name>`）が生の値で刻まれ、worktree が幻の別PJ slug として cross-PJ 統計（correction_recurrence 軸ほか）に紛れ込んでいた。`project` フィールドは #492 で `project_name_from_dir` 経由に正規化済みだったが、`project_path` を stamp する現役3経路（`hooks/observe.py` usage-registry / `hooks/correction_detect.py` corrections / `correction_semantic/promote.py` reflect 昇格）が生値のままだった。**(1) 集計側（defense-in-depth）**: `outcome_promotion_readiness._pj_of` を `outcome_metrics._normalize_pj` 経由に統一。**(2) 書込側（本筋）**: 上記3経路を `pj_slug_fast` / `project_name_from_dir`（いずれも subprocess なし＝hot-path 制約維持）経由で正規化。`project_path` は全 consumer がパスとして open/stat せず PJ 識別子として扱う（distinct カウント / tail 抽出 / `_normalize_pj`）ことを Read で確認のうえ **slug 化**を選択し `project` 表記と一致させた。**(3) バックフィル**: 既存汚染レコードを遡及正規化する `bin/rl-fleet migrate-pj-slug`（ロジック `scripts/lib/pj_slug_backfill.py`）を新設。dry-run 既定（read_only・1バイトも書かない）／`--apply` で実書込／冪等（再実行で差分ゼロ）／`--data-dir` で対象指定（実 `~/.claude` は明示指定時のみ）。対象3ストア＝corrections.jsonl(project_path 行 rewrite) / subagents.jsonl(project 行 rewrite) / sessions.db(project 列 + raw_json 内 project の両方を DuckDB UPDATE。読み側が raw_json から読むため両方必須)。basename だけの legacy 値はフルパス情報欠落で復元不能のため原値維持。決定論・LLM 非依存。TDD（書込側 5件 + バックフィル 12件: dry-run 書込ゼロ / apply 正規化 / 冪等 / worktree・通常・basename 各パターン）。
- **fix(audit): calibration_drift advisory が bootstrap 母集団でも「あと N件」を畳むよう #479 guard を拡張（closes #584）** — `build_calibration_drift_section` の structural 判定が `status==insufficient_data`（< 5件）のみを見ていたため、skill 提案が構造的に出ない PJ で optimize/rl-loop 由来の少数 accept/reject が history に残り `bootstrap`（5〜29件）に入ると「calibration drift 判定は保留（あと N件）」が出続け「いつか溜まる」と誤読させていた。母集団は『提案が出て初めて』積み上がるため bootstrap でも MIN_DATA_COUNT に永久に届かず、かつ bootstrap 戻り値は `structural_reason` を持たない（producer 契約）。`status==bootstrap` も構造シグナルとして畳む（#479 を拡張）。畳んだ枝でも `{valid_count}/{min_count}` の現状は残し「あと N件」の蓄積前提だけ消す。決定論・LLM 非依存。TDD（`test_calibration_drift_section.py` に bootstrap 畳み / 非 structural 件数維持 の2ケース）。
- **fix(evolve): weak_signals 過去未読分の昇格導線を surface し `correction_capture` 案内文の矛盾を解消（closes #583）** — observability の「未昇格 llm_judge は今日の修正確認 phase で昇格可能」案内が daily/bootstrap の実導線と食い違っていた: `bootstrap_backlog` が marker 済み（`is_done`）だと過去 backlog を一切提示せず、`daily_review` は `max_groups=5` で上位 group しか出さない（既読化されないので 6 番目以降が毎回こぼれる）ため、「marker 済み × daily 上位を超える過去未読分」が両 phase から構造的に外れ、案内どおり進めても入口がなかった。過去 backlog 全件の真の入口は `reflect --show-weak-signals`/`--promote-weak`（`read_unpromoted` ベースで marker/既読を見ず全件拾う）だが surface されていなかった。`sections_weak_signals.py` に `_backlog_lane_lines` を追加し、bootstrap が `is_done` かつ `daily.build_review(dry_run=True).remaining > 0` のときだけ過去 backlog N 件を昇格できる別レーン行を surface（marker 未設定 or daily 全件カバー時は出さず重複・誤誘導を回避）。判定は read-only・取得失敗時は従来挙動へフォールバック。決定論・LLM 非依存。TDD（`test_weak_signals_observability.py`）。

## [1.104.0] - 2026-06-18

### Added
- **feat(report-feedback): evolve/audit レポートのメタレビュー起票スキルを新設し旧 feedback を統合・削除** — 他PJで `/rl-anything:evolve`・`/rl-anything:audit` を回した後に「レポートを見て rl-anything 自体の改善点・バグを探して issue 化する」手作業をスキル化。決定論 `evolve_introspect`（Step 11）が拾えるのは result dict の機械的矛盾だけで、「レポートを読んで初めて気づく」改善（数字の母数欠落・提案の質・誤検知・表示バグ・UX 摩擦）を起票する経路が無かった。新規 `skills/report-feedback/SKILL.md` は LLM がレポートの**中身（対象環境の改善）でなく出来栄え・挙動**をメタレビューし、rl-anything 自身への改善候補を `evolve_introspect` の candidate スキーマで生成 → 同モジュールの `flatten_candidates`/`filter_duplicates`/`render_issue_body` を再利用して dedup・重複防止マーカーを共有 → 人間個別承認のうえ `todoroki-godai/rl-anything` に起票する。2経路（audit 経路=`rl-audit` の stdout レポート本文・`self_analysis` 無し / evolve 経路=result JSON の `self_analysis` を決定論 seed として併用）＋会話経路。他PJから呼ぶため SKILL のスクリプト参照は `${CLAUDE_PLUGIN_ROOT}` 経由（相対パス No such file pitfall 回避）。public repo 起票のため Step5 に「対象PJ固有語を一般化・数値は現象として記述」のプライバシーチェックを MUST 配置。**旧 `feedback` スキル（会話から GitHub Issue 化・全履歴7回のみ使用）を統合・削除**。実 audit レポート（202行）で1回ドッグフードし、Belief Entropy Gate の生フロントマター途中切れ表示・Telemetry ゼロ重み節の見出し誤読リスク等を実際に検出できることを確認。LLM 部分は決定論検証不能だが「候補スキーマ↔dedup/render 配線」は契約テスト `scripts/lib/tests/test_report_feedback_contract.py`（5件・LLM 非依存）が固定。

## [1.103.1] - 2026-06-18

### Fixed
- **fix(audit/multiview_eval): join キーの名前空間不一致を正規化し実データで「該当視点なし」固定を解消（closes #577）** — v1.103.0 で入れた multiview_eval（#564）を実PJ2つ（rl-anything / docs-platform）で dogfood したところ、`classify_multiview` の join 両辺でキーの名前空間が食い違い**実データでは必ず「✓ 評価したが該当視点なし」しか出ない**繋ぎ目バグを発見。`target_skills`（`_custom_skill_names`＝SKILL.md ディレクトリ名）は素の `cleanup` だが、`outcome_attribution` / `negative_transfer` のキー（`attribute_outcomes`＝起動時のスキル名）はプラグイン修飾形 `rl-anything:cleanup` で、同一スキルがプレフィックスの有無だけで交差が空集合になっていた（chaos は設計上 None・negative_transfer 0 件のため outcome 由来3視点が構造的に発火不能）。`_bare_skill_name`（`<plugin>:` プレフィックス剥がし・`Agent:*` は subagent 帰属なので join 対象外）+ `_index_outcomes`（bare と修飾形が衝突したら exact bare 優先・順序非依存）を導入し outcome_attribution / negative_transfer を bare 化して join。実測: rl-anything で join 成功 0→3 スキル（cleanup/docs-refresh/spec-keeper）。pytest が緑だったのは合成 fixture が両辺キーを bare で一致させていたため（合成 fixture の false confidence）。TDD（名前空間 join / Agent: 非 join / negative_transfer 修飾形 / exact-bare 優先 の4件追加）。決定論・LLM 非依存。
- **fix(correction_semantic/relevance_gate): relevance 閾値を dedup 用 0.5 から decouple し実コーパス全件 suppressed を解消（closes #578）** — v1.103.0 で入れた relevance_gate（#565）を rl-anything の実 weak_signals 287件で dogfood したところ、機構は正常（採点・kept/suppressed 分離・理由付与）だが**実文脈では kept=0 / suppressed=287** に倒れていた（自由文文脈の jaccard が max ~0.25・中央値 0.0 で閾値 0.5 に到達せず「関連経験を残す」目的が全件抑制の no-op 化）。根因は `RELEVANCE_THRESHOLD = JACCARD_THRESHOLD`（=0.5）＝ bootstrap_backlog の **near-duplicate クラスタリング用**閾値の流用。relevance（過去経験が現文脈に関係するか）は dedup より緩い関係なので relevance 専用の校正値 0.2 に decouple（metric は jaccard 据え置き＝汎用語1語一致を 1/N に自然減衰し overlap 係数の tiny-set 偽陽性を回避。閾値は `--relevance-threshold` で従来通り上書き可能・#565 スコープの「学習機構は作らない」を維持）。実測: 同一実文脈で kept 0→3 / suppressed 287→284。TDD（decouple 後の閾値 < JACCARD_THRESHOLD / 部分一致 jaccard~0.25 が新既定 kept・旧0.5 suppress の2件追加 + 既存テストの閾値参照を `rg.RELEVANCE_THRESHOLD` へ追従）。決定論・LLM 非依存。

## [1.103.0] - 2026-06-18

### Added
- **feat(correction_semantic): FinAcumen 流の関連度ゲート付き経験提案＋無関係抑制（closes #565）** — 過去経験（weak_signal / idiom）の提案を「現在の文脈キーワード集合」との意味的関連度で選別し、無関係な経験を明示的に抑制する増分実装。新規 `scripts/lib/correction_semantic/relevance_gate.py`（純関数 `candidate_text` / `score_relevance` / `gate_candidates` / `summarize_gate`）が、校正済み閾値（既存 `JACCARD_THRESHOLD`=0.5 流儀・引数で上書き可能）を超えた候補だけを `kept`（提案根拠）に**関連度降順**で残し、閾値未満は**黙って消さず** `suppressed` に分離して `suppressed_reason`（なぜ落としたか）を残す。各候補に `relevance_score`（0.0-1.0）を付与。文脈キーワードが抽出できない場合は `gate_applied=False` で全件素通し（経験を勝手に隠さない安全側フォールバック）。`rl-reflect --show-weak-signals --context "<現在の文脈>"`（`--relevance-threshold` で閾値上書き）に配線し、出力末尾の `relevance_gate` サマリで抑制の効きを確認できる（`--context` 無しは従来通り全件提示の後方互換）。類似度は既存 jaccard 流儀を再利用し独自の閾値学習機構は作らない。決定論・LLM 非依存。TDD（`test_correction_semantic_relevance_gate.py` 16件 + reflect 配線 3件）。
- **feat(audit): SEAGym 流の多視点評価レイヤ — evolve 提案を4視点で決定論分類（closes #564）** — evolve 提案の評価を「単一の accept/reject」から多視点へ拡張する薄い集約レイヤ。既存3部品（chaos 仮想アブレーション / outcome_attribution 一発成功率・rework率 / negative_transfer）を skill 名で join し、各 evolve 対象スキルを4視点（再利用可能な改善 / 過学習疑い / 退行リスク / コスト増）に決定論分類して audit/evolve レポートに advisory surface する。新規 `scripts/lib/audit/multiview_eval.py`（純関数・dry-run 安全・DATA_DIR 再読込なし）+ `sections_multiview.py`（observability builder・chaos は重いため再実行せず outcome/negative-transfer を軽量集約・silence≠evaluated 境界を明示）。chaos しきい値は `config.py` の `CHAOS_THRESHOLDS` から複製し契約テストで drift 検出。replay スナップショット比較は将来拡張フックのみ（スコープ外）。出典: tech-eval（SEAGym, arXiv 2606.17546）。TDD（multiview 分類23件 + observability 隔離 guard 追従）。決定論・LLM 非依存。
- **feat(release): `bin/rl-release-sync` — リリース後のローカルプラグイン自動同期** — marketplace は Directory source（ローカルの作業ディレクトリ）を直接見るため、リリース（bump）が worktree→PR→origin/main に入っても**ローカル main を pull しない限り marketplace が古いまま**で、`claude plugin update` が低い（古い）バージョンを返す慢性的な穴があった。`claude plugin tag --push` の直後に `bin/rl-release-sync` を実行すると「ローカル main を origin/main へ fast-forward → `claude plugin marketplace update rl-anything` → `claude plugin update rl-anything@rl-anything`」を一括実行してその穴を塞ぐ。worktree から呼んでも `git --git-common-dir` 経由で本体 repo を解決し、本体が main 以外をチェックアウト中なら exit 2 で誤同期を防止。`--dry-run` で実行予定コマンドのみ表示。`.claude/rules/commit-version.md` のリリース手順に組み込み済み。TDD（`bin/tests/test_release_sync.py` 3件・dry-run でコマンド順序 / main 以外 abort / repo 外 abort を封じる）。決定論・LLM 非依存。
- **feat(evolve): bootstrap Step6.1 に TF-IDF テーマクラスタ＋バケット multiSelect（closes #558）** — 初回 bootstrap で当PJ未昇格シグナルが多数（実測 48件/45グループ）出ると Step 6.1「各 group を順に AskUserQuestion」が質問マラソンになり explain-clearly（質問を畳む）と衝突していた。`bootstrap_backlog.cluster_groups` を新設し、group 数が `THEME_CLUSTER_THRESHOLD=12` を超えたときだけ既存資産（`similarity.build_tfidf_matrix` + `reorganize` の階層クラスタリング）を再利用してテーマ別バケットに決定論クラスタリングし、バケット単位の multiSelect 1 問に畳む。閾値以下は従来 per-group フロー（挙動不変）。sklearn/scipy 不在や文書僅少時は単一バケットに graceful degradation。SKILL.md Step 6.1 に標準フローを追記。決定論・LLM 非依存。

### Changed
- **fix(reflect): `--promote-weak` CLI 出力を `corrections_human_allpj` に scope 明示リネーム（closes #557）** — CLI が返す全PJ集計値と per-PJ の `growth_report["corrections_human"]` が同名で取り違えやすく `41/10` の不整合表示事故（#526-1）の温床だった。CLI 出力キーを `corrections_human_allpj` に改名し全PJ集計であることを機械的に区別可能化。SKILL.md の「対話前スナップショット補正」MUST 注記も新キー名へ追従（per-PJ 値を上書きするなの警告を維持）。
- **feat(fitness): `fitness_evolution` の insufficient_data 出力を `{verdict, one_liner, details}` に圧縮（closes #559）** — `has_fitness`/`structural_reason`/`next_action`/3段落 `message` が top-level に併存し情報過多で、SKILL 側に誤読防止注記が積み上がり続けていた（#400 バグ#5/#525-1/#526-4/#528-1/#479＝出力契約破綻の兆候）。top-level に `verdict`（機械判定）+ `one_liner`（結論1行）を置き、長文 `message` と冗長フィールドを `details` に隔離。後方互換のため `structural_reason`/`next_action` は top-level にも残す（sections.py/evolve.py が読む）。SKILL.md の注記群を1本化。決定論・LLM 非依存。

### Fixed
- **fix(evolve_consistency): `verification_bypass` を矛盾検出から除外（closes #560）** — `_detect_usage_suitability_contradiction` が usage_count=0 × suitability∈{high,medium} を無条件で矛盾判定し、#376 が意図的に設けた検証系スキルの例外（`verification_bypass=True` で usage0 でも medium 維持）を見ていなかった。結果、検証系 11 スキルを毎 evolve run で false positive 量産していた。guard ループ先頭で `verification_bypass=True` の assessment を除外。決定論・LLM 非依存。
- **fix(evolve): constitutional cache 良性 advisory を `warning_sink` から除外（closes #561）** — 「失敗ではない」と明記された constitutional cache stale の良性 advisory が `warning_sink`→`result["warnings"]`→`_detect_captured_warnings`（scipy RuntimeWarning 等の真の警告用パス）に拾われ、self_analysis runtime_errors に `label: bug` として二重 surface されていた。良性 advisory は observability 行のみに surface し warning_sink には積まない。決定論・LLM 非依存。
- **fix(audit): weak_signals 昇格案内を llm_judge/決定論チャネルに分離（closes #562）** — observability の未読昇格案内が未読を全チャネル横断で数え「今日の修正確認 phase で昇格可能」と一括案内していたが、daily_review phase は channel=llm_judge のみ対象で決定論チャネル（manual_edit_after_ai/esc_interrupt/rephrase）は reflect 経路。未読が全て決定論チャネルだと phase が 0 件しか出さず誤誘導になっていた。hint を `llm_judge 未読 → evolve 今日の修正確認 phase` / `決定論チャネル未読 → reflect --promote-weak` の2系統に分け、llm_judge 未読 0 のときは phase 行を出さない。決定論・LLM 非依存。
- **fix(outcome_metrics): `rework_rate` に最小分母 floor を追加（closes #563）** — `rework_rate` に最小分母 floor が無く分母 1（編集ありセッション 1 件）で 1.0 に張り付き、downstream の measurement_bug（全PJ bit-exact 一致を測定バグと判定 #445）と promotion_readiness 条件1（分散が十分）を構造的に誤発火させていた。`correction_recurrence_rate` の `MIN_DISTINCT_TYPES_FLOOR=5` と同方針で `MIN_EDIT_SESSIONS_FLOOR=5` を導入し、floor 未満では率を `None`「サンプル不足」にする（沈黙 != 評価不能, #393-#396）。さらに **#563-2**: 実 PJ E2E（docs-platform / sys-bots）で、`promotion_readiness` 条件1 が `outcome_metrics` とは別の重複実装 `per_pj_correction_recurrence`（floor 未適用）を使っており、distinct_types < floor の PJ が一斉に 1.0 へ張り付いて「全 PJ 同値 = 測定バグ」を恒久 false positive にしていた繋ぎ目を発見・修正（条件1 の variance 入力を `MIN_DISTINCT_TYPES_FLOOR` で絞り、サブ floor の PJ を除外。残りが 2 PJ 未満なら測定バグでなく insufficient_pj と正しく報告）。決定論・LLM 非依存。
- **fix(glossary_drift): jargon 候補の一般英単語 FP を辞書ベースで根治（closes #567）** — #554/#554-2 で `DEFAULT_STOPLIST` に BEGIN/END/FAILED/SELECT/INFO/GROUP 等の「ALLCAPS だが中身は普通の英単語」を手動列挙し続けていたが本質的にモグラ叩き（learning_detector_fp_context_not_allowlist）だった。常用英単語リスト（`scripts/lib/data/common_english_words.txt` = google-10000-english-no-swears, public domain, 9894語）を同梱し、`find_undefined_terms` の除外判定に「`tok.lower()` が常用英単語リストに含まれるか」を追加（`load_common_english_words` で lazy load・キャッシュ）。stoplist は `.lower()` が辞書に載ると検証できた #554-2 群（BEGIN/END/SELECT/FAILED/INFO/GROUP/ON/OFF/WEB/APP/GET/POST/JavaScript/Lambda 等）を除去し、辞書に載らない頭字語・固有名（API/JSON/AWS/CDK/CloudFront/DynamoDB/TypeScript/CLI/PROD/Athena 等）のみ残す形に縮小。FastAPI/NestJS/UPDATER/AMAMO/DuckDB 等の PJ・framework 固有語は辞書に無いため保持（FN ゼロを回帰テストで担保）。実 PJ dry-run 実測: rl-anything 自身の SoT で undefined 候補 21→15 件（-6, DR/HOME/TASTE/UNION/UNIQUE/VAR を辞書フィルタが除去、PJ固有語は全保持）。決定論・LLM 非依存。
- **fix(bootstrap): テーマクラスタを char n-gram TF-IDF + バケット上限ガードに改修（closes #568）** — #558 のテーマクラスタが word-level TF-IDF（`build_tfidf_matrix`）を使い、日本語の短い発話断片を共通語彙で束ねられず実コーパス（figma-to-code 108 group）で 108→48 バケットにしか畳めず「質問マラソン回避」の狙いを達成できていなかった（root cause: 各発話が固有名詞中心で TF-IDF 語彙が共有されない）。`cluster_groups` を char n-gram TF-IDF（`_build_char_tfidf`・`analyzer='char_wb'`・`ngram_range=(2,3)`）に差し替えて部分文字列の共有（述部・共通語幹）を捉え、さらにバケット数が `MAX_THEME_BUCKETS=10` を超える場合は距離閾値を 0.02 刻みで段階的に上げて再クラスタ（決定論・有限停止）し AskUserQuestion 1 問で扱える規模に必ず収める。実データ実測: figma-to-code 108→10 / receipt 16→9 / atlas 15→6 / amamo 8→6（全て ≤10）。sklearn 不在は単一バケットへ graceful degradation。決定論・LLM 非依存。
- **fix(outcome_promotion_readiness): `per_pj_rework` も最小分母 floor を欠く同類残を修正（closes #569）** — #563-2 で readiness 条件1（correction_recurrence）に `MIN_DISTINCT_TYPES_FLOOR` を適用したが、同モジュールの `per_pj_rework` は依然 `round(rework_sessions / edit_sessions, 4)` で `MIN_EDIT_SESSIONS_FLOOR` 未適用だった。現状 `axes` evidence 表示専用で gate（3条件）には非関与のため実害は小さいが、将来 rework を gate 条件に組み込むと #563 と同じ分母1で 1.0 張り付き FP が再発する潜在バグ。`edit_sessions < MIN_EDIT_SESSIONS_FLOOR` の PJ は `value=None` + `sample_insufficient=True` にし、floor は `outcome_metrics`（#529-2）/ `correction_recurrence`（#563-2）と同一定数を使う（二重管理回避）。決定論・LLM 非依存。
- **fix(glossary_drift): `DEFAULT_STOPLIST` に汎用テック語 + AWS サービス名を追加（closes #554）** — jargon 候補抽出が読者既知の汎用語（GET/JS/JWT/CRUD/SHA/RPC/IaC/CDN/SaaS/TypeScript 等）と AWS サービス名（CloudFront/DynamoDB/EventBridge/S3/Lambda 等）を未登録 jargon として誤検出し件数を水増しして seed 生成判断を歪めていた（実測 33件中約13件がFP）。`DEFAULT_STOPLIST` に汎用テック語セットと AWS サービス名 frozenset を union 追加。真の PJ 固有語（AMAMO/PKCE 等）は巻き込まない。**#554-2**: 実 PJ E2E（docs-platform 22→10 / sys-bots 5→3）で stoplist 通過後も残った SQL キーワード・ログレベル・汎用ステータス語・汎用テック略語（BEGIN/END/FAILED/GENERATED/OFF/WEB/XXX/LOW/PASS/SPA/BFF/RAG/ORM/OWASP 等）を追加除外。FastAPI/NestJS/ThreadPoolExecutor/UPDATER 等の真の PJ・framework jargon は保持。辞書ベースの一般英単語フィルタは将来 issue 化（observed FP の語彙明示除外に留める）。決定論・LLM 非依存。
- **fix(discover): `rule_violation`/`tool_usage` の examples を1行 truncate + cross_pj メタ付与（closes #555）** — examples が巨大な多行スクリプト丸ごとで表示が極端に重く、また cwd 帰属では別PJのソースツリーを指す開発ノイズがアプリPJのテレメトリに載っていた。`truncate_example`（多行は先頭1行、120字超は120字 + `…`）を rule_violation_lane / tool_usage_analyzer の両 example 構築で共有し、別PJ絶対パスを含む example に `cross_pj: true` メタを付与。決定論・LLM 非依存。
- **fix(auto-memory): rule 引用型 correction を enqueue から除外（closes #556）** — Stop hook 由来の「既存 rule 名を再掲するだけのリマインダ」（例 no-defer-use-subagent）が毎 run enqueue→生成→belief_entropy ゲートで block される循環でサイクルを浪費していた。`is_rule_citation`（既知 rule slug のハイフン付き形式を message/corrected/original に部分一致・FP 防止に汎用英単語は除外）で判定し、`enqueue` で全件 rule citation なら投入しない。決定論・LLM 非依存。
- **fix(subagent-guard): distinct agent 数で数え偽の暴走警告を解消（closes #574）** — `subagent_observe._count_recent_session_subagents` が時間窓内の `subagents.jsonl` の**記録行数**を数えていたが、`handle_subagent_stop` は SubagentStop イベントごとに 1 行 append するため、長命 background worker（impl-worker 等）が idle のたびに再発火すると**同一 `agent_id` が複数行**書かれ、distinct な subagent 数を構造的に水増ししていた。実データ（実セッション）で記録 90 行に対し distinct agent は 23（同一 id が最大 18 回）= 約4倍の水増しで、distinct が 2〜3 個でも「17 個生成」と偽の暴走警告を出し subagent-guard.md に従い頭が無駄に作業中断していた。窓内の **distinct `agent_id`（欠落時は `agent_name`）数**を数えるよう変更し、識別子欠落レコードは個別カウント（1 に潰すと暴走を見逃すため過小評価しない保守側）。measurement_bug 系（distinct agent と stop イベント数の取り違え）。回帰テスト 5 件（同一 id × N→1 / 異なる id→N / 識別子欠落→個別 / 窓外除外 / idle 再発火で偽警告が出ないこと）。決定論・LLM 非依存。

## [1.102.0] - 2026-06-17

### Added
- **feat(dogfood): `--layer light` + 非ブロッキング pre-push hook** — `bin/rl-dogfood-gate --layer light` を新設（Layer1a dry-run 不変 + Layer2 report invariants + Layer3 SKILL.md コードブロック、実機約11秒）。フル `--layer all`（Layer1b の `--drain` subprocess が支配的で約3.5分）から重い Layer1b drain と ingest E2E を除外した高速層で、日常 push 向け。`scripts/git-hooks/pre-push.local`（+ `install.sh`）を新設し、gstack-redact の managed pre-push が chain する `pre-push.local` 拡張点へ導入。**非ブロッキング**（赤でも `exit 0`・警告のみ。1人開発で `--no-verify` 迂回を招かないため）。共有 hooks なので install は worktree 横断で1回。**繋ぎ目対策**: hook は gate の終了コードを区別する（0=緑 / 1=実際に赤→警告 / 2 等=gate を実行できず→soft スキップ）。非0を一律「赤」に潰すと、light 未対応の古い gate（共有 hooks を未マージ branch/別 worktree から踏むと argparse が exit 2）を誤警告し狼少年になるため。TDD（`test_cli_light.py` 4件 + `test_prepush_hook.py` 5件 — 後者は実 bash hook を subprocess 実走し exit 0/1/2 の分岐と非ブロッキングを封じる）。実 push E2E で managed→pre-push.local→実 gate 緑→gstack-redact の全段を確認。決定論・LLM 非依存。

## [1.101.0] - 2026-06-16

### Added
- **feat(hook_drift): dead_ref 検出（flow-chain 参照スキルの実在突合）（closes #316）** — ADR-036 第二フェーズ。`~/.gstack/flow-chain.json` が参照する skill 名（chain のソースキー + 各 `next` 遷移先）が live registry（`~/.claude/skills/` ∪ rl-anything 本体 repo の `skills/` ∪ `skill_origin.get_plugin_skill_names()`）に実在しないものを `detect_dead_refs` が検出。表記ゆれによる false positive リスクで第一フェーズ（stale_pin）から除外していた核心を、`normalize_skill_ref`（前後空白 / 先頭 `/` / `plugin:skill` 名前空間 / 引数の除去）に閉じ込め、変換を契約テストで先に固定。**FP 厳禁（precision 優先）**: 正規化不能の参照は flag せず、live registry が空（skill 列挙失敗）なら全参照を dead に見せないため沈黙。`build_hook_drift_section` が stale_pin の後ろに `⚠ 実在しないスキル N 件` を追記（dead が無ければ非表示）。実 `~/.gstack/flow-chain.json`（128 live skills）でドッグフードし FP 0 を回帰テスト化。決定論・LLM 非依存。

### Fixed
- **fix(discover): `workflow_checkpoint_gaps` を常時出力（closes #369）** — `run_discover` の workflow checkpoint 走査は workflow skill 該当なし（skills_dir 不在等）でキー自体を欠落させ、evolve SKILL.md Step 10.4 が「評価したが該当なし」と「そもそも評価していない（silence）」を区別できなかった。`stall_recovery_patterns` と同じく成功・except 両経路で `workflow_checkpoint_gaps` を必ず設定し、該当なしは空リスト `[]` で明示。決定論・LLM 非依存。

### Removed
- **chore: message_display dead code 削除（closes #427）** — orphan_store（#422）が検出した真正 orphan `message_display.jsonl`（writer あり reader 0）は #495 で MessageDisplay hook の不発登録（CC v2.1.175 の標準 hook イベント名ではなく実環境で一度も発火していなかった）と store_registry 宣言を撤去済みだったが、`hooks/message_display.py` 本体と `hooks/tests/test_message_display.py` が dead code として温存されていた。reader 設計が scripts/skills のどこにも存在しないこと（import 0）を確認し本体・テストを削除。spec/components.md の store_registry 説明（"9 ストア … message_display を宣言バックフィル済み"）を実態（8 ストア・撤去済み）に整合。決定論・LLM 非依存。

## [1.100.1] - 2026-06-16

### Fixed
- `observability.skill_triage` の findings 補完（#528-4）: 案内行だけで CREATE/UPDATE/SPLIT/MERGE の実件数を持たなかった findings レーンに、evolve が `phases.skill_triage` の実件数行を注入。`build_skill_triage_counts_lines` を追加し、全 0 件でも surface（silence != evaluated）。実 PJ（docs-platform）の evolve dry-run で正の注入経路を実機確認済み。

## [1.100.0] - 2026-06-16

### Added
- `rule_violation_observed` レーン（#522-3）: 既存 rules で禁止済みのコマンド（例: `cd` 禁止なのに 626 回観測）が repeating_patterns で「スキル候補」提案されるのを防ぐ。rule installed != enforced の違反観測を専用レーンに分離し、`phases.discover.rule_violation_observed` として surface（hook enforce 検討を推奨）。決定論・LLM 非依存 (#522)
- **feat(correction_semantic): 過汎用 idiom の FP guard + representative 品質改善（closes #527, refs #528）** — 過汎用な短文字 idiom が confirmed 化され idiom_autopromote（#463）の FP 製造機になる問題を決定論ゲートで根治。実測（`correction_idioms.jsonl` の confirmed 中の極短 idiom「いやいや」「じゃなくて」「気がする」「比率だけ」「いや、2/24の」）に合わせ `correction_semantic/idiom_filter.py` を新設し 3 ゲートを 1 関数（`idiom_eligible`）に集約: (1) 最小長 floor（正規化後 8 文字未満を弾く）/ (2) 日常語 stopword（相槌・推量・否定のみで具体修正内容を持たない言い回しを弾く。具体名が残れば残す）/ (3) 文脈固有トークン（日付 `2/24` / 割合 `80%` / 序数 `3番目` など再発しても別文脈になる断片を弾く）。`batch.ingest` で idiom 化時に弾き（個人辞書に入れず weak_signal は隔離記録のまま・弾いた件数を `idioms_filtered` で surface）、`idiom_autopromote` にも防御ゲートを置きガード導入前に confirmed 化済みの過汎用 idiom も自動昇格しない。**#527-4**: bootstrap/daily の group に `confirmable_idiom`（「はい」確定で confirmed になる idiom テキスト・eligible 時のみ。過汎用は None）を decision 材料として常時 emit し、AskUserQuestion で idiom 単位の拒否を可能化（提示配線は SKILL.md 側）。**#528-3 (部分)**: `correction_semantic/representative.py` を新設し representative から assistant の過去レポート引用ブロック（`>` markdown quote / code fence / ℹ️・✓・✗ 等のステータス絵文字プレフィックス行）を strip し user 発話のみ抽出（全行が引用なら情報喪失回避で元 text fallback）。一行 representative の判読のため直前 AI 行動の 1 行要約（`prev_action`）を evidence に添える。TDD 新規（idiom_filter 14 / representative 11 + batch/bootstrap/daily に guard・confirmable・representative・prev_action 検証を追加）。決定論・LLM 非依存。
- `rl-evolve --print-out-path`（#525-3）: slug 解決済みの OUT パス `/tmp/rl_evolve_<slug>.json` の1行だけを print して即返す軽量モード（評価本体は回さない）。evolve SKILL.md Step 1 の `SLUG`/`OUT` 再導出ボイラープレート（`python3 -c "...resolve_slug..."`）を1コマンドに短縮。DATA_DIR resolver には触れず slug 解決 + /tmp パス組み立てのみ。決定論・LLM 非依存

### Changed
- **レポート整合性・可読性・冗長性の改善（closes #525, refs #528 #526 #529）** — evolve レポートの読みづらさ・文言矛盾を一掃:
  - **TL;DR 冒頭サマリ必須化（#525-2）**: レポート冒頭に「変更 N 件 / 要対応 M 件 / 残りすべて評価済みクリーン」を MUST 化。全 ✓ の observability 項目は「✓ クリーン: glossary / orphan_store / …」と1ブロックに畳む（clean のみ畳み、名前を残して silence != evaluated を担保）。
  - **Weak Signals のチャネル別×スコープ matrix 化（#528-2 / #490）**: `347 件（全PJ集計）（llm_judge 6）。うち当PJ未昇格 6 件` の桁混在散文を、`<ラベル>（<channel>）: 全PJ N / 当PJ未昇格 M` の matrix 1 行ずつに分解。
  - **Weak Signals 昇格導線の未読分離（#525-1 / weak_signals）**: 「当PJ未昇格 N 件（うち未読 M 件）」と daily_review 既読ストアと突合した未読数を併記し、daily phase「新規なし（既読済）」との噛み合わせを取る。既読ストア未解決時は未読 = 未昇格 にフォールバック。
  - **growth_report の今日の昇格行の出所明示（#525-1 / growth_report）**: 「今日の確認で N 件昇格」が同日の別セッション分を含む問題を、「本日累計 N 件昇格（このrunでは M 件）」と出所を区別。返り値に `promoted_this_run` / `autopromoted_this_run` を追加。
  - **skill_triage observability を findings 化（#528-4）**: `observability.skill_triage` builder から assistant への指示文（「必ず〜せよ」MUST 表現）を除去し、「実データは `phases.skill_triage` にある」と案内する findings 行に変更。表示指示（MUST）は SKILL.md Step 3.8 に移管。
  - **fitness 文言の全体否定誤読を防止（#525-1 / fitness, #528-1）**: SKILL.md Step 8 で next_action の前に「fitness 関数自体は rl-optimize / rl-loop-orchestrator で評価に使用中。対象外なのは calibration（accept/reject 蓄積）だけ」という役割1行を必須化。`structural_reason` がある insufficient_data では件数行（`N/30件`）を省く（#526-4・`0/30` の蓄積前提誤読を防ぐ）。
  - **SKILL Step 9 の per-PJ growth 値上書きを是正（#526-1）**: `rl-reflect --promote-weak` 出力の `corrections_human`（全PJ合計 41 等）で per-PJ growth_report 値（0/10 等）を上書きさせ「41/10」と誤表示させる指示を、「per-PJ 値 + 今回昇格数の加算」に修正し全PJ値の混入を断つ。
  - **SKILL Step 11 にモジュール名明記（#529-3）**: 自己解析の実コードは `evolve_introspect` モジュール（`self_analysis` という名のモジュールは存在せず、result キー名からの誤推測で ModuleNotFoundError になる事故を予防）。
  - **global prune 候補の件数1行化（#525-3）**: PJ 単独 evolve では判断材料不足の global 淘汰候補（実測 76 件規模）を全件持ち回らず「件数1行 + `bin/rl-fleet status` 誘導」に。
  - すべて決定論・LLM 非依存。lib 変更（weak_signals / growth_report / sections_triage）は TDD で文言・分岐を契約化。

### Fixed
- **fix(evolve): `evolve.py` の module-level `DATA_DIR` が `CLAUDE_PLUGIN_DATA` を無視して home 固定だった問題を env 優先解決に統一（closes #517）** — `skills/evolve/scripts/evolve.py` の `DATA_DIR` は `Path.home()/".claude"/"rl-anything"` ハードコードで `CLAUDE_PLUGIN_DATA` 環境変数を読まず、reader 側（hooks / `scripts/lib`）が使う `rl_common.resolve_data_dir`（env 優先 + #364 Phase2 marker redirect）と乖離していた。このため `CLAUDE_PLUGIN_DATA` で隔離した dogfood gate / テストでも evolve.py の読み書き（`evolve-state.json` / `usage.jsonl` / world_context 等）だけ実環境 DATA_DIR に漏れていた。**修正**: `_resolve_data_dir()` を新設し `rl_common.resolve_data_dir(os.environ["CLAUDE_PLUGIN_DATA"])` で解決（import 失敗時は env→home の順に fallback）、`EVOLVE_STATE_FILE` も派生のまま追従。`MARKER_ROOT`（evolve_decisions.py の home 固定は hook/tool パス合意の設計）は不変。既存の `monkeypatch.setattr("evolve.DATA_DIR", ...)` 経路は module-level 属性のまま維持。TDD 新規2（env 尊重 / 未設定 fallback、reload 経由・HOME 隔離）。決定論・LLM 非依存。
- **fix(dogfood): Layer 1b（非 dry-run store 差分）を実装し NotImplemented 枠を解消（closes #518）** — #496 Wave0 で予約のみだった Layer 1b（「書かれるべき store が apply 境界で実際に書かれる」方向の検査）を実装。#484（決定論3チャネルが標準フローで一度も永続化されない繋ぎ目）が #513 で根治されたことを実環境に近い形で封じる回帰ゲート。隔離コピー方式（#515 流用）: (a) DATA_DIR を tmp にコピー、(b) `CLAUDE_PLUGIN_DATA=<コピー先>` で `rl-evolve --drain --result-json <result>` を起動（`--result-json` 指定により `drain_pending` が home 固定 `MARKER_ROOT` を読まず result JSON の `evolve_decisions.pending` から取る＝隔離が完全になり home の実マーカーに依存しない。MARKER_ROOT の home 固定設計は不変）、(c) コピー側の store 差分 + drain サマリで assert: `weak_signals_persisted` が存在し `dry_run=False`（配線生存 + 非 dry-run）/ weak_signals.jsonl 等の決定論チャネル書込が isolated copy に現れる。サマリ欠落 / `dry_run=True` / 例外 dict は fail、drain 非ゼロ終了は error。`run_layer1` に data_dir/out_dir/dry-run result_path を配線し `1b_store_diff` を checks に常時計上。`cli.py` の Layer1 print が `store_changes` も surface するよう拡張。TDD 新規8（CLAUDE_PLUGIN_DATA 伝播 / 非 dry-run assert / dry_run=True fail / サマリ欠落 fail / persist error fail / 非ゼロ終了 error / store_changes surface / run_layer1 統合）+ 既存の NotImplemented テストを実装後挙動へ更新。drain サブプロセスは mock（実 drain 経路は `bin/rl-dogfood-gate --layer 1` で別途確認）。決定論・LLM 非依存。
- **fix(remediation): separation の emit prompt がマシン固有絶対パスで参照リンクを指示し、リファレンスに正しい signature が無い問題を根治（closes #524）** — (1) #524-1: `references/remediation.md` の Phase A サンプルが `emit_compression_request`（3引数）の例のみで、実際は 4 引数の `emit_separation_request(issue, path, original_content, limit)` を流用すると `TypeError` になっていた。emit/ingest 6 関数の実 signature を `scripts/lib/remediation/fixers_llm.py` を単一ソースとした表 + separation/split それぞれの呼び出し例で明示。(2) #524-2: `emit_separation_request` の prompt が `参照リンク: /Users/<user>/.../.claude/references/<name>.md` とホーム配下絶対パスで書き換えを指示していたため、書き換えられてコミットされる rule ファイルにマシン固有パスが埋まり他環境で壊れていた。`reference_link_for_prompt` を新設し `.claude/` セグメント以降の PJ ルート相対パス（`.claude/references/<name>.md`）を prompt に出力。実際の書込先（絶対パス）は `meta["reference_path"]` に保持し ingest は従来どおり絶対パスへ書く（相対表示用 `meta["reference_link"]` も併記）。`.claude/` を含まないパスは素通し（安全側）。TDD 新規（helper 4 / emit 相対リンク 2 / ingest 絶対書込 1）。決定論・LLM 非依存。
- **fix(outcome): correction 再発率の表示側に最小分母 floor を追加し低サンプルの誤シグナルを抑止（#529-2, refs #529）** — `outcome_metrics.correction_recurrence_rate` が分母（distinct correction_type 数）が小さくても率を出していたため、「window 内 correction 9 / distinct type 2 / 再発 type 1」= n=2 で 0.50 のような低サンプル誤シグナルが advisory に表示されていた。promotion_readiness には correction 件数 ≥10 の floor があるのに表示側には無かった。`MIN_DISTINCT_TYPES_FLOOR=5`（distinct 5 なら 1 type の振れは 0.20 に抑えられ、CORRECTION_FLOOR=10 件で平均 2 件/type のとき distinct≈5 と整合する暫定値）未満では率を `None` にし、evidence に `reason="insufficient_sample"` + `distinct_types`/`floor`/`records` を付ける。`sections_outcome` は「データ不足」と区別して「サンプル不足（distinct N type < floor 5）— 率は非表示」と表示。TDD 新規（floor 未満 None / 境界 inclusive / display サンプル不足表示 3）。決定論・LLM 非依存。
- **fix(discover): try/except 外 dict subscript が discover 全フェーズを `'NoneType' object is not subscriptable` で落とし root cause を握り潰す問題を根治（closes #521, #526）** — `run_discover` は内部検出関数（`detect_missed_skills` / `_enrich_patterns` / `determine_scope` / `load_claude_reflect_data`）の戻りを try/except 外で subscript していたため、いずれかが None / 想定キー欠落を返すと run_discover 全体が落ち、上位 `evolve.py` Phase 2 の except が traceback を捨てて `{"error": str(e)}` だけ残すため root cause が永久に観測不能・result は緑に見えた（#521）。さらに discover 失敗で `reflect_data_count` が欠落し、下流 SKILL.md Step 6 / Step 10.1 の `reflect_data_count >= 5` 比較が None で TypeError になっていた（#526-3）。**修正**: (1) 各検出ブロックを既存の `try/except → result["<name>_error"] = str(e)` パターンでガードし、None 戻りは握り潰さず `raise TypeError(...)` で観測可能化、戻り dict は `.get()` で読む。(2) `evolve.py` Phase 2 の except に `traceback.format_exc()` を追加し `result["phases"]["discover"]["traceback"]` を残す。(3) `reflect_data_count` は discover 失敗時も欠落させず degraded sentinel `-1`（int）にフォールバックし、SKILL.md Step 6 / Step 10.1 に「数値比較の前に `< 0`（degraded）を先に判定し『discover 失敗のため reflect 件数 不明』と表示」する degraded-mode 分岐を明記。**sentinel は必ず int に保つ**: str sentinel（`"unknown"`）だと CANONICAL の `kind=int` 契約に違反し、runtime self-detect（`evolve_consistency`）が `wrong_kind` drift を誤検出して幻の「契約乖離 issue」を自作する（/review #530 で発見・degraded 経路を実 conformance 付きで踏むテストが無く全スイート緑をすり抜けた。回帰ガード新設）。TDD 新規（runner 7 / evolve traceback 1）。決定論・LLM 非依存。
- **fix(evolve): 構造化 env_score がサイレント消滅し成長レベル演出が一度も発火しない問題を配線で根治（closes #523 #526）** — Phase 3 Audit が `run_audit(...)`（戻り = markdown レポート文字列）だけを `result["phases"]["audit"]["report"]` に格納し、内部で算出済みの構造化 env_score を捨てていた。SKILL.md / `references/report-narration.md` の「Report クライマックス（成長レベル）」はトップレベル `result["env_score"]` を読む設計なのに、その field が存在せず（evolve.py のコメントも「env_score は result のトップレベルに存在しない」と明言）、`compute_level` が常に null → 成長レベルが構造的に一度も発火しなかった（silence != evaluated 原則の自己違反）。**修正**: audit phase 直後に `_compute_env_score_struct`（同じ権威ソース `compute_environment_fitness` から取り直して `compute_level` まで解決）で `result["env_score"]` を構造化 dict（`score`/`level`/`title_ja`/`title_en`/`sources`/`degraded`）として surface。算出失敗時は黙らず `degraded=True`（`previous_level` は world-context.json フォールバック）を置く。`dry_run=True` 時は `record=False` で fitness 履歴を汚さない。SKILL.md / report-narration.md / `_summarize_result` を新 dict 形に整合（reader は再計算不要でそのまま読む）。markdown を正規表現でパースする対症療法は採らず構造化値を surface。TDD 新規4（成功 surface / degraded fallback / dry-run record=False / stderr 配線）。決定論・LLM 非依存。
- **fix(evolve): Chaos Testing が stale agent worktree を shadow コピーして生タプル stderr を吐く問題を除外で根治（closes #523）** — フル dry-run 中 Chaos Testing の shadow コピー（`_prepare_shadow_project`）が `.claude/` を丸ごと `shutil.copytree` し、`.claude/worktrees/` 配下の stale な agent worktree（壊れた symlink / ファイル不在）で `shutil.Error` が生 Python タプルの長大 stderr を吐いてフル dry-run を汚していた。しかも self_analysis は「stderr 警告なし」と誤報告（stderr が self_analysis に配線されていない）。**修正**: (a) shadow コピー対象から `.claude/worktrees/` を除外（`ignore_patterns("worktrees")` + `ignore_dangling_symlinks=True`。worktrees はアブレーション対象=rules/skills でないため不要）、(b) Chaos/Constitutional のスキップ通知を `_summarize_skip_reason` で 1 行要約化（worktree 残骸由来は「スキップ N 件（worktree 残骸）」、その他は件数/上限長サマリ）、(c) audit phase 実行中の stderr を `_capture_audit_stderr`（tee）で捕捉し `result["warnings"]` 経由で self_analysis.runtime_errors に配線（Python warnings ではない素の stderr print も拾えるようにした）。TDD 新規（chaos worktree 除外 2 / 壊れ symlink 完走 / skip 要約 4）。決定論・LLM 非依存。
- tool_usage 分類が `VAR=value` 代入プレフィックスを実コマンドとして誤計上していた問題を修正（env/sudo 同様にスキップ。`WT=...` 等で head/repeating key が汚染されていた）(#522)
- skill_triage CREATE 候補の confidence が remediation issue 化で default 0.5 に降格し、個別承認レーンに永久に乗らなかった問題を修正（detail["confidence"]=0.70 を top-level confidence_score に引き継ぐ）(#522)
- prune の zero_invocation 候補が「データ欠損で zero と断定不可」の advisory を付けつつ per-item 調査 MUST を課す矛盾を解消。観測窓が usage 記録修正日 (#478) をまたぐ間は candidates を suppress し「計測待ち N 件」サマリ（`zero_invocations_suppressed`）に置換。窓全体が修正後に蓄積されたら通常判定に自動復帰する (#522, refs #529)
- fix(test): `test_evolve_data_dir_env` の cleanup fixture が `del sys.modules["evolve"]` + 素の reimport で sys.modules を別オブジェクトに差し替え、他テストが collection 時に束縛した `run_evolve.__globals__`（＝元モジュール）を orphan 化させ、`monkeypatch("evolve.DATA_DIR")` が効かず実環境 DATA_DIR へ書込が漏れて `test_non_dry_run_writes_calibration_state` が落ちる問題を修正（#407/#408 と同型の sys.modules 汚染）。`-n auto` では別プロセスで露出せず、#517/#518 で test 件数が 5018→5078 に増えた xdist 再分配 + フルスイート `-n 0` で発火。fixture を元の evolve モジュールオブジェクトをそのまま復元する方式に変更して根治

## [1.99.0] - 2026-06-12

### Added
- **feat(dogfood): 通し評価ゲート `bin/rl-dogfood-gate` を新設（#496）** — 「テスト緑・evolve 無エラー・でも成果物がバグだらけ」を構造的に防ぐリリース前 dogfood ゲート。pytest 非依存の独立 CLI（Layer3 が「ユーザーと同じ素の起動経路」を再現する必要があり、conftest の sys.path 補完 / HOME 隔離の下駄を意図的に避けるため）。ロジックは `scripts/lib/dogfood/` パッケージ。**Layer 1（dogfood E2E）**: (1a) dry-run 不変 — DATA_DIR 全ファイルの SHA256 スナップショットを取り `evolve.py --dry-run` を素の python 起動（PYTHONPATH のみ）→ 再スナップショットで差分ゼロを assert（#491 の 4 ファイル書換を赤検出、bypass なし）。(1b) store 差分（書かれるべき方向）は非 dry-run が実環境を汚すため Wave 0 では NotImplemented 枠だけ予約し #484 修正後に実装。さらに実 PJ utterance ingest E2E（旧 `test_real_pj_e2e`・直列35秒）を pytest スイートから本ゲートへ移設（`scripts/lib/dogfood/ingest_check.py`、wall time / DB size / row 数の assertion を維持）。**Layer 2（report invariants）**: dry-run result JSON の機械検査 — 必須 top-level キー存在 / 件数非負 / 当PJ ≤ 全PJ / observability contract（ADR-028 の `_OBSERVABILITY_BUILDERS` を単一ソースに import）突合。**Layer 3（SKILL.md コードブロック抽出実行）**: 全 `skills/*/SKILL.md` の python/bash fenced block を抽出し安全分類 — python は import 文を `import X` に正規化して素の subprocess で import 検証（ゲートは scripts/lib を勝手に注入せず、ブロックが自前で行う sys.path 設定のみ前置＝sys.path 不足 #487/#495 を捕捉）、bash は `--help`/`--dry-run` 付きのみ実行・それ以外（書込/破壊系・プレースホルダ・コマンド置換）は存在検証のみ。CLI: `--layer 1|2|3|all` / `--json` / `--output`、exit 0=全緑 / 1=赤あり / 2=実行エラー。実機1周で #486（削除済み backfill CLI 3本）/ #487（agent-brushup sys.path 不足）/ #495（audit sys.path 不足）/ #488（prune `from scripts.prune import`）を赤検出することを確認。TDD 新規 43 ユニットテスト（合成 fixture・HOME 隔離）。決定論・LLM 非依存。
- feat(tests): フルスイートを pytest-xdist で並列化（`pytest.ini` `addopts` に `-n auto` 追加）— 直列 135 秒 → 42 秒に短縮

### Changed
- **feat(dogfood): Layer1 dry-run 不変チェックを「隔離コピー方式」に改善（#496）** — 旧設計は実 DATA_DIR を直接 snapshot diff していたため、ゲート実行中にライブセッションの hook（trigger_engine の `_save_state` 等）が `evolve-state.json` を書く ambient write と「dry-run の書込バグ」を区別できず flaky だった（実際に偽赤を1回観測）。改善: (a) 実 DATA_DIR を `shutil.copytree(symlinks=True)` で一時ディレクトリ（`out_dir/isolated-data-dir`）へコピーし、(b) `CLAUDE_PLUGIN_DATA=<コピー先>` を subprocess の env に渡して dry-run evolve を起動、(c) SHA256 snapshot 比較はコピー側のみに対して行う（実 DATA_DIR は一切比較対象にしない）。効果: ambient write 混入ゼロ / dry-run バグがあっても実環境を汚さない / 検出力は同等。DuckDB ファイル（utterances.db 等）も copytree でコピー可能で、コピー中の concurrent write で部分コピーになっても「コピー前後の自己比較」なので検出力に影響しない旨をコードコメントに明記。**文書化された除外**を bypass フラグでなくモジュール定数（除外理由コメント付き frozenset）として原則ベースで恒久化: `CACHE_EXCLUDE_NAMES`（ファイル名: `skill-evolve-cache.json` / `constitutional_cache.json` = LLM 再呼び出し回避キャッシュの意図された dry-run 書込）/ `CACHE_EXCLUDE_PATH_PREFIXES`（ディレクトリ prefix: `evolve_pending/` = #402/ADR-041 の意図された運用ポインタ書込・#513 revert 後は dry-run でも書かれるのが正常）/ `CACHE_EXCLUDE_JSON_KEYS`（共有 JSON 内 cache キー: `evolve-state.json::skill_type_cache` = prune の参照型判定 LLM 推定キャッシュが実 state と同居するため JSON キー単位で正規化除外し、同居する実 state の dry-run 書込バグは引き続き検出）。`snapshot_dir` に `exclude_names` / `exclude_path_prefixes` / `exclude_json_keys` 引数を追加。実 PJ ingest E2E（`ingest_check.py`）は元々 tmp dir のみに書き実 DATA_DIR に触れない設計のため隔離追加は不要。TDD 新規（隔離コピー / CLAUDE_PLUGIN_DATA 伝播 / cache 除外3種 / 実 state 変更は検出維持 など 23 ユニットテスト）。実機 `bin/rl-dogfood-gate --layer 1` で緑を確認。決定論・LLM 非依存。

### Fixed
- evolve_decisions pending marker の dry-run 書込（#402/ADR-041 設計）を PR #505 の誤ゲートから復元し emit→drain 捕捉の全死を解消。SHA256 不変契約は文書化された除外リスト（evolve_pending/ + cache 2件）で両立 (#513)
- **fix(weak_signals): 決定論3チャネルが実 PJ で一度も永続化されない問題を apply 境界の drain 永続化で根治（closes #484）** — weak_signals.jsonl の channel 分布が `llm_judge` 313件 + `permission_deny` 5件（テスト一時dir由来）のみで、決定論3チャネル（`manual_edit_after_ai` / `esc_interrupt` / `rephrase`）が全PJ通算0件だった。**根因**: 決定論検出の永続化は `run_evolve` 内の `run_batch(dry_run=dry_run)` だけが担うが、標準 evolve フローは `rl-evolve --dry-run` 分析 → assistant が対話適用、である（#400 と同型）。dry-run 分析パスでは `append_signals` の最下層 dry-run ゲート（#491 invariant）で常にゼロ書き込みになり、非 dry-run の evolve は標準フローでまず走らないため決定論チャネルが永続化されない（`llm_judge` だけは SKILL.md の apply 側 Phase B/C で `dry_run=False` 書込されて存在していた）。**修正**: 決定論検出は冪等（`signal_key` dedup）なので、evolve_decisions の `--drain`（#402）と同型に **apply 境界で永続化する**。`weak_signals.batch.persist_weak_signals_drain`（`run_batch(dry_run=False)` の apply 境界専用入口）を新設し、`rl-evolve --drain`（tool 文脈・非 dry-run・正準 DATA_DIR）の CLI 分岐に配線。結果は drain サマリの `weak_signals_persisted`（detected/written/skipped_dup）で surface。`--dry-run` 分析は #491 契約どおり1バイトも書かない（dry-run パスは不変）。SKILL.md Step 7.8 に永続化が同居する旨を追記。TDD 新規（store 差分 E2E 4 + CLI 配線 2）。決定論・LLM 非依存。
- **fix(seam): LOW 軽微一括 — SKILL.md sys.path 不足4件 / TTL の cross-PJ write / doc stale / last_skill テストの実 /tmp 漏れ / 不発 MessageDisplay hook（closes #495）** — 繋ぎ目調査の LOW 5系統を一括修正。**(1) SKILL.md sys.path 不足4件**: `audit/SKILL.md:75`（skill_usage_stats）+ `evolve-skill/SKILL.md:62,118,129`（skill_evolve）の python ブロックが `sys.path.insert` なしで素の起動時 ModuleNotFoundError になっていたのを、prune #488 と同型の `CLAUDE_PLUGIN_ROOT` 解決 + `scripts/lib` 前置に統一（`bin/rl-dogfood-gate --layer 3` が fail=4 → fail=0）。**(2) weak_signals TTL の cross-PJ write**: `weak_signals/ttl.py` の `mark_expired` が slug フィルタなしで全 PJ の expired を read→rewrite していた（「当 PJ 操作が他 PJ ストアを書き換える」原則違反）のを `pj_slug` 引数を追加し当 PJ レコードのみ対象に限定（pj_slug=None は後方互換で全件）。**(3) doc stale**: `discover/SKILL.md:64` の `verification_catalog.py` 表記をパッケージ表記へ更新 + `reflect/SKILL.md` Usage に `--revoke-idiom`（ADR-047 安全弁③）を追記。**(4) last_skill テストの実 /tmp 漏れ**: `last_skill_path` が `os.environ TMPDIR` を直読みするため observe 経由テストが実 `/tmp` に `rl-anything-last-skill-<session>.json` を漏らしていたのを、hooks/tests/conftest.py に autouse fixture `_isolate_tmpdir`（tmp_path_factory 独立 dir で隔離・副作用ゼロ assert を壊さない）を追加 + `clear=True` テストで隔離 TMPDIR を保持（workflow.py 本体は変更せず）。**(5) 不発 MessageDisplay hook**: `MessageDisplay` は CC v2.1.175 の標準 hook イベント名ではなく実環境で一度も発火していない（message_display.jsonl が MISSING・標準イベント由来 store は全て存在）ことを確認し、hooks.json の不発登録と store_registry の宣言を削除（writer 削除と整合・stale 突合を防ぐ）。message_display.py 本体と単体テストは dead code として温存。TDD 新規（ttl cross-PJ 2 / last_skill TMPDIR 隔離 hooks 全 492 緑）。決定論・LLM 非依存。
- **fix(evolve): SKILL.md 散文 MUST 依存の記録動作を決定論化 — record_rejection の fallback 欠如と growth_report.promoted_today の構造的常時0（closes #494）** — 繋ぎ目調査 C+E+G で発見した 2 件。**(1) record_rejection の決定論 fallback（発見1）**: remediation の却下記録は SKILL.md Step 5.5 の散文 MUST（assistant が inline python で `record_rejection` を叩く）が唯一の入口で、取りこぼすと却下が永久消失し #477 が解いた「同じ提案が毎回再出」が再発する完全に安全網ゼロのレーンだった（learning_skill_md_must_not_enforcement）。`suppression_ledger.reconcile_surfaced`（emit→reconcile・surfaced マーカーで各提案 dedup_key の連続提示回数を追跡）を新設し evolve.py の remediation phase に毎 run 配線。解決されないまま閾値回数（既定2）連続で個別承認に出続けた提案を自動却下（`record_rejection` 昇格）して再提示を止める。今回検出されなくなった提案（修正済み）は marker から落とし却下しない。件数は `phases.remediation.auto_rejected_by_reconcile` に surface（evolve_result_schema CANONICAL に追加）。store `remediation_surfaced/<slug>.json` を store_registry に宣言（batch-writer / permanent / dry-run 非書込）。ユーザー明示却下は従来どおり inline record_rejection が即時抑制を担い、fallback は次回以降の安全網。**(2) growth_report.promoted_today の構造的常時0（発見2）**: `growth_report.py` が `review_result.daily.promoted` を読むが `daily_review.build_review` の返り値に `promoted` キーが存在せず（実 promote は emit 後の Step 6.2 で起きる）コード上必ず0だった。実 promote は `rl-reflect --promote-weak`（source=reflect_confirmed）/ idiom_autopromote（promoted_by=idiom_dict）が corrections.jsonl に永続記録する点を単一の真実とし、`count_promoted_today`（今日 UTC・weak_signal 由来・非 invalidated を決定論カウント）を新設して `promoted_today`/`autopromoted_today` を corrections ストアから導出（明示 live カウントは max で後方互換）。これで「構造的常時0」を根治し実際の昇格数が反映される。TDD 新規（reconcile 5 / wiring 2 / promoted_today corrections 導出 5）+ 既存テスト（schema fixture / store_registry）を新キーへ更新。決定論・LLM 非依存。
- **fix(audit): weak_signals の未昇格件数・by_channel 内訳・昇格導線文を全PJ集計から当PJスコープに修正（closes #490）** — `sections_weak_signals` の `by_channel` 内訳・`unpromoted` 件数・「未昇格 N 件は evolve の今日の修正確認 phase で昇格可能」の導線文が全PJ集計のまま、実際に昇格する `daily_review.py`（当PJフィルタ）と16倍の食い違いが生じていた。`pj_slug_fast(project_dir)` で当PJ slug を導出し `pj_slug` が一致するレコードのみ `by_channel`・`unpromoted` に集計（`pj_slug` 未設定レコードは後方互換で当PJ扱い）。`total` は「（全PJ集計）」のまま残し昇格導線文を「うち当PJ未昇格 M 件が昇格可能」に変更。TDD 新規3テスト（当PJのみ計数 / チャネル内訳当PJ限定 / 当PJ未昇格ゼロで導線文なし）。決定論・LLM 非依存。
- fix(dogfood): evolve result の observability キー `constitutional` / `remediation_batch_skip` が Layer2 invariant check で unknown 扱いされる drift を修正 — 両キーは evolve-only の ad-hoc 書込（`_surface_constitutional_status` / `build_remediation_batch_skip_observability`）で `_OBSERVABILITY_BUILDERS` 外だが意図的なもの。`invariants._observability_builder_keys()` に `_EVOLVE_ONLY_OBSERVABILITY_KEYS` を追加して既知扱いに（closes #504）
- **fix(slug): PJ slug 導出を1関数に単一ソース化し read/write の食い違い（worktree 時限式 silent mismatch）と hook 書込側 basename 固定を根治（closes #492）** — slug 導出が2系統に分裂（`optimize_history_store.resolve_slug` の git-common-dir 方式 / `utterance_archive.pj_slug_from_cwd` の `/.claude/worktrees/` 切り詰め文字列方式）し、同一ストアの read/write で別方式が混ざると worktree 環境で slug が食い違い、書いたレコードを読めない時限式 silent mismatch を生んでいた。**(1) 単一ソース化**: `scripts/lib/pj_slug.py` を新設し `resolve_pj_slug`（authoritative・git-common-dir 親 basename / git 不可かつ worktree マーカーありの時のみ fast フォールバックで本体名へ正規化 / 素の非 git dir は `_unattributed` 温存）と `pj_slug_fast`（文字列のみ・subprocess なし・hot path 用）を提供。既存2関数は thin wrapper に寄せ後方互換 re-export を維持。**(2) read/write 整合**: evolve SKILL.md の `bootstrap_backlog.mark_done` / `daily_review.record_reviewed` を phase 出力の `result.correction_review.bootstrap.slug` / `.daily.slug`（build が read に使った slug）をそのまま渡す方式に変更（resolve_slug の再導出をやめ read=write を構造保証）。`sections_capture._llm_judge_count` は `project_dir` を受け weak_signals 書込側と同じ `pj_slug_fast` で当PJ slug を導出。**(3) hook 書込側の根治**: `rl_common.project_name_from_dir`（sessions/usage の `project` フィールドの供給源）を worktree 安全な `pj_slug_fast` 由来に統一し、worktree cwd でも本体 repo 名が書かれるよう修正（旧実装は素の basename で worktree 名 `feedback`/`bots` が固定され読み側で本体名に復元不能だった・#489 レビュー）。移行日定数 `PJ_SLUG_NORMALIZATION_DATE = "2026-06-12"`（#478 の `USAGE_RECORDING_FIX_DATE` と同型）を記録。TDD 新規（pj_slug 単一ソース 15 / hook project 正規化 5 / capture section slug 整合は既存更新）。決定論・LLM 非依存。
- **fix(schema): evolve result の top-level キー群（correction_review / growth_report / idiom_autopromote 等）を schema 契約の対象化（closes #493）** — `evolve_result_schema.CANONICAL` は `phases.*` のみ登録で、#442-#448 で追加された top-level キー（`correction_review.bootstrap`/`.daily` / `growth_report` / `idiom_autopromote` / `observability` / `evolve_decisions(.pending)` / `weak_signals(_ttl)` / `correction_semantic` / `self_analysis` / `trigger_summary` / `warnings` / `env_tier(_reason)` / `slug` / `project_dir` / `generated_at` / `dry_run`）が契約対象外だった。SKILL.md が7箇所（173/238/360/392/533/567/586/670 行付近）で top-level path を読むのに rename / kind drift が conformance 0 件で素通りし、reader が静かに空表示になる #375 保護の新レーンでの構造的再発。`Key.top_level` を追加して CANONICAL を top-level path も登録できるよう一般化し、上記キーを登録（reader 必須キーは required・それ以外も型契約）。`COVERED_TOPLEVEL` / `UNCOVERED_TOPLEVEL`（`phases`/`timestamp`/`output` は意図的除外）で phase 完全性ゲートと同型の逆方向契約を新設。`extract_documented_paths` を `result.<top>.<...>` dotted（precision のため `result.` 接頭辞必須）と `result["growth_report"]` bracket（1 セグメント可）も拾うよう拡張。runtime drift 検出（`evolve_consistency`）は既存 `_RUNTIME_DRIFT_REASONS` が `missing` を除外済みのため変更不要（部分入力での FP を回避・#377-5/#379-5 の流儀踏襲）。TDD 新規（top-level 完全性 / reader 必須キー登録 / kind drift 検出 / missing 検出 6 件）。実 dry-run result が conformance 0 違反で準拠。決定論・LLM 非依存。
- **fix(evolve): dry-run の evolve が3箇所で書き込む問題を修正 + SHA256 不変 E2E を追加（closes #491）** — `run_evolve(dry_run=True)` の「1バイトも書かない」契約が3箇所で破れていた（dogfood gate Layer1 が実機で4ファイル書換を検出）。**(1) evolve_decisions の pending marker**: `emit_decisions` が dry-run でも marker を作成/削除（二方向違反）→ marker 操作を `if not dry_run:` ブロック内へ移動し、返り値に `marker_written` / `marker_cleared` を追加して観測可能化。**(2) audit 完了記録**: `run_audit` に dry_run 引数自体が無く audit-history.jsonl / evolve-state.json を無条件更新 → `run_audit(dry_run: bool = False)` を追加し evolve.py から貫通（audit 単体 CLI の既定挙動は不変）。**(3) episodic_store の read 経路 materialize**: `query_relevant` が DB 不在でも connect/mkdir/CREATE TABLE で空 DB を物理生成 → `get_db_path().exists()` ゲートで read-only 化。再発予防として隔離 HOME+DATA_DIR で dry-run 前後の全ファイル SHA256 不変を assert する E2E（`test_dry_run_no_write_e2e.py`）を追加（#496 ゲート Layer1 と二重防御）。注: `skill-evolve-cache.json` の dry-run 書込は LLM 再呼び出し回避キャッシュの意図された設計（evolve-ops）であり本契約の対象外 — ゲート側で文書化された cache 除外として扱う。決定論・LLM 非依存。
- **fix(audit): outcome_metrics 3軸 / capture 率が全PJ集計を当PJレポートに無ラベル表示する問題を当PJスコープに直す（closes #489）** — `outcome_metrics`（correction 再発率 / 一発成功率 / rework 率）と `sections_capture`（capture 率）が corrections.jsonl / sessions.jsonl / usage.jsonl を project フィルタなしで読み、当PJ audit/evolve レポートに全PJ集計を無ラベルで表示していた（実測: 一発成功率 全PJ0.73 vs 当PJ0.88 の 15pt 乖離。capture 率は当PJ限定の llm_judge 行と全PJ値が無ラベル併置）。`outcome_metrics` の3軸関数 + `compute_outcome_metrics` に `project` 引数を追加し、`build_outcome_metrics_section` / `build_capture_rate_section` が project_dir を当PJ識別子として渡すよう配線。PJ 識別子・レコード側（corrections=`project_path` フルパス / sessions・usage=`project` basename）の両方を既存共有関数 `utterance_archive.extractor.pj_slug_from_cwd`（`/.claude/worktrees/` を切って本体 repo 名に正規化）で worktree 安全 slug に正規化してから突合する（worktree セッション分の取りこぼし=undercount を防ぐ。実測で usage に worktree basename `feedback`/`bots` が混在。slug 1関数化 #492 に整合）。未帰属レコードは寛容に include。両セクション header に「当PJ」スコープを明記。**全PJ横断の重み昇格判断（ADR-046 / `outcome_promotion_readiness`）は per-PJ 分解（`per_pj_*`）を独自に持ち、本3軸関数を経由しないため cross-PJ 意味はそのまま温存**。`outcome_attribution` も in-memory list 入力で独立のため影響なし。`project` 未指定時は従来通り全PJ集計で後方互換。TDD 新規（outcome_metrics project/worktree scope 8 / section scope 1 / capture_rate project/worktree filter 3 / capture section scope 1）。決定論・LLM 非依存。
- **fix(agent-brushup): agent-brushup SKILL.md Step1 の幻の CLI と sys.path 不足 Python フォールバックを修正（closes #487）** — `agent_quality.py` に `if __name__ == "__main__"` が存在しない幻の CLI / フォールバックの `from agent_quality import` が sys.path 設定なしで ModuleNotFoundError になっていた。`agent_quality.py` と `agent_quality_upstream.py` の `from lib.X` を `from X` に修正（scripts/lib が sys.path にある場合のみ解決できるパターンを排除）し、SKILL.md Step1 を sys.path 設定込みの python3 -c ブロックに統一（prune #488 / evolve #479 と同型）。回帰テスト 3 件追加。
- **fix(backfill): 丸ごと幻だった backfill スキルを廃止リダイレクト化し doc/案内を現行経路へ同期（closes #486）** — `skills/backfill/SKILL.md` が指示する CLI 3本（`rl-backfill` / `rl-backfill-reclassify` / `rl-backfill-analyze`）は #215（v1.65.1）でソースごと削除済みで全て command-not-found だが、`disable-model-invocation: true` のユーザー明示起動スキルのため新規 PJ 導入時の最初の体験が全コマンド失敗になっていた。現行の取り込みは observe hooks の進行形観測 + evolve の batch ingest（`session_store.ingest` / utterance_archive #430）に統合済みで、専用 backfill CLI の再実装は不要（スコープ外）と判定。**(1) SKILL.md を薄い廃止リダイレクトに書換**: 旧 CLI 手順を全削除し「廃止済み。現行は observe hooks 自動記録 + `/rl-anything:evolve`（任意で `bin/rl-fleet ingest`）」へ。即削除はせず呼び出し互換のためスキルは残す。**(2) evolve.py の幻 CLI 案内2箇所を修正**: `check_data_sufficiency` の telemetry_empty メッセージと `_warn_insufficient_data` の stderr ガイダンスが初回ユーザーを削除済み `/rl-anything:backfill` へ誘導していたため、observe hooks 自動記録 + `/rl-anything:evolve` 案内へ差し替え（doc 廃止と runtime 案内の整合）。**(3) doc 同期**: CLAUDE.md / SPEC.md / README.ja.md のクイックスタート・スキル一覧を現行経路に差し替え、backfill を deprecated に分類。verbatim 検証: 新 SKILL.md の `bin/rl-fleet ingest --help` が exit 0、参照スキル audit/evolve 実在を確認。TDD 既存2テストを新案内（`/rl-anything:backfill` を含まず evolve を案内）へ更新。決定論・LLM 非依存。
- **fix(prune): prune SKILL.md Step4/Step5 の `from scripts.prune import` を `sys.path` 設定込みの正準パスに修正（closes #488）** — #479 と完全同型の残存個体。`scripts/__init__.py` が存在しないため verbatim 実行で ModuleNotFoundError になっていた。正準パターン（`_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd(); sys.path.insert(0, os.path.join(_root, "scripts", "lib")); from prune import ...`）に統一。TDD 新規 3 テスト。決定論・LLM 非依存。
- **fix(observe): usage-registry.jsonl の writer 条件が bare 名で永久 False だった問題を修正（closes #485）** — `is_global_skill` がパス前置判定のみで bare スキル名（CC が実際に渡す形式）を判別できず、`usage-registry.jsonl` が一度も書かれず audit の Scope Advisory が構造的に空だった。修正: bare 名の場合は `~/.claude/skills/<name>/SKILL.md` の存在チェックに変更し、パス形式は後方互換で維持。既存テスト `test_global_skill_registers` も実 CC が渡す bare 名形式に修正。
- **fix(evolve): evolve SKILL.md 記載と実体の3箇所の乖離を解消（closes #479）** — (1) **import パス誤り（ModuleNotFoundError）**: Step 6.1/6.2 が `bootstrap_backlog.mark_done(...)` / `daily_review.record_reviewed(...)` を直 import 前提で記載していたが実体は `correction_semantic` パッケージ配下。Step 6.5（auto_memory_broker）と同型の sys.path 設定込み完全コード例（`from correction_semantic import bootstrap_backlog` / `daily_review`・`resolve_slug` で slug 導出・`decision` はキーワード専用）を両 Step に追加し、`python3 -c` で実行検証（dry-run で `mark_done` / `record_reviewed` が期待 dict を返すことを確認）。(2) **所要時間目安が stale**: Step 1 の `large ≈ 8〜20 分`（および line 80 の「数分〜20分」）が [ADR-037] の audit/skill_evolve LLM-free 化以降の実測（large 環境で約34秒）と一桁以上乖離していたため、`small ≈ 〜15 秒 / medium ≈ 15〜30 秒 / large ≈ 30〜60 秒（実測約34秒）`へ再校正し「LLM-free 化以降の実測ベース」と注記。(3) **fitness 文言の3箇所矛盾**: 同一 run で fitness_evolution の next_action（「fitness は使わない設計。対応不要」）と calibration_drift の「あと N 件」（蓄積前提）が矛盾していた。`scripts/lib/audit/sections.py` の `build_calibration_drift_section` が、fitness_evolution の insufficient_data + structural_reason（`skill_evolve_not_scored`）を検出した場合、「あと N 件で判定可能」の蓄積前提断定を「母集団は『提案が出て初めて』積み上がる＝構造的に対象外になり得る」へ切替え、3箇所（Step 2 has_fitness / fitness_evolution next_action / calibration_drift）の文言を統一。SKILL.md Step 8 に整合注記を追記。TDD 新規 1 テスト（test_data_insufficient_structural_caveat）。決定論・LLM 非依存。
- **fix(observe): Skill 発火が usage registry に乗らず prune zero_invocation が構造的 FP / triage CREATE 候補が埋没する問題を修正（closes #478）** — **(item 1 根本原因)** `observe.py` は Skill ツール発火を `usage.jsonl` に記録するコード（telemetry の usage_count の唯一の供給源）を持つが、`hooks/hooks.json` で **`observe.py` が PostToolUse の `Agent` matcher にしか登録されていなかった**。`Skill` matcher には `skill_activation_log.py`（別ファイル `skill_activations.jsonl` への記録・global skill prune 専用）のみが登録されており、Skill 発火時に `observe.py` が一切起動せず `usage.jsonl` への記録が発生しなかった。結果、`compute_telemetry_scores` / evolve の「Usage (last 30 days)」が読む `usage.jsonl` は PJ 固有スキルについて常に空 → `usage_count: 0` が構造的に発生していた。修正: `hooks.json` の PostToolUse `Skill` matcher に `observe.py` を追加登録（`skill_activation_log.py` と併存・別ファイルに書くため二重計上なし）。**(item 2)** 過去データ欠損の緩和として、prune の `zero_invocation` 候補に usage 記録修正日（`USAGE_RECORDING_FIX_DATE`）を含む `advisory` フィールドを付与し、skill_evolve の `insufficient_usage` recommendation にも同趣旨の文言を追記（「修正日以前のデータは欠損のため zero と断定不可」）。**(item 3)** trajectory 由来の新スキル候補（triage の CREATE）が remediation 低 confidence batch_skip に畳まれて埋没する問題に対処 — evolve SKILL.md Step 3.8 に CREATE/UPDATE/SPLIT/MERGE のサマリ表示 MUST を追加し、observability contract（ADR-028 の `_OBSERVABILITY_BUILDERS`）に `skill_triage` builder（`scripts/lib/audit/sections_triage.py`）を登録して「triage 結果を必ず surface せよ」という契約行を markdown / 構造化の両経路に自動伝播。builder は triage を再実行せず（重い・副作用回避）custom スキル存在時のみ契約行を出す決定論判定。TDD 新規（hooks.json registration 1 / prune advisory 1 / skill_evolve advisory 1 / observability builder 2）。決定論・LLM 非依存。
- **fix(evolve): remediation の scope 分類不整合・却下の非永続化・行カウント基準/confidence・既知FP拡充（closes #477）** — evolve の remediation フェーズで顕在化した4件を修正。**(1) scope 分類の不整合**: `impact_scope: "global"`（`~/.claude/rules/` 配下のグローバル rule）の proposable item が `proposable_custom_individual` に振り分けられつつ集計は `proposable_global: 0` になり、SKILL.md 上「参考値・対応不要」のはずの global scope が個別承認 AskUserQuestion に出ていた。根因は `compute_impact_scope`（"global"）と `classify_artifact_origin`（"custom"）の判定食い違いを origin 単独で分割していたこと。新規 `partition_proposable_by_scope`（`scripts/lib/remediation/confidence.py`、impact_scope OR origin=="global" を global と判定）を `evolve.py` の partition に適用し整合化。**(2) 却下の非永続化 → 重複提案**: 個別承認フローで却下/スキップした提案を記録する仕組みが無く、べき等性原則（重複提案 MUST NOT）に反して次回 evolve で同じ提案が再出していた。新規 `scripts/lib/remediation/suppression_ledger.py`（triage_ledger #308 を範に dedup_key 単位 + TTL 45日・per-slug 分離・worktree 安全 slug・**dry-run 非書込**）を追加し、**本流へ配線**: `evolve.py` の remediation phase が `_apply_remediation_suppression`（`filter_suppressed` の読み取り専用ラッパ・import 失敗時は全件 surface に graceful degrade）で却下済み提案を proposable 候補から除外し、抑制件数を `result.phases.remediation.suppressed_by_ledger`（evolve_result_schema の CANONICAL に追加）として surface（silence != evaluated）。SKILL.md の remediation 個別承認フローに、却下/スキップ確定時の `record_rejection` 記録手順（sys.path 設定込みの完全コード例・#479 の直 import ModuleNotFoundError 対策・dry-run では記録しない明記）を追記。ストア `remediation_suppression/<slug>.jsonl` を store_registry に宣言（writer_locus=batch / retention=ttl 45日・#434 ゲート）。**(3) 行カウント基準の明示 + confidence の超過率スケール**: rule は frontmatter 除外の「コンテンツ行」をカウント（`count_content_lines`）するため実 40 行でも `lines: 11 / limit: 10` と報告されうる。rationale に基準（`コンテンツ行（frontmatter 除外）` / `総行数`）を明示。また「1行超過 → 固定 confidence 0.95 → auto_fixable」は超過幅を無視した過剰確信だったため、超過率（excess/limit）で floor 0.55〜cap 0.88 に線形スケールし、わずかな超過は auto_fixable（≥0.9）に昇格させず proposable に留める（160%+ の大幅超過は従来どおり 0.3 で manual_required）。**(4) 既知FPパターンの拡充**: ドキュメント用スキルのフェンス付きコードブロック内に意図的に記載された AWS ARN / 数値 ID / Slack ID を hardcoded_value として個別提案に上げていたのを、`hardcoded_detector.py` でコードブロック境界を追跡し doc 文脈系（ARN/URL/数値 ID/Slack ID）を抑制（api_key は文脈無関係に秘匿対象なので維持）。glossary の jargon 候補に並ぶ汎用略語（PDF/QA/FAQ/CSV/XML/MVP/KPI 等）を `glossary_drift.DEFAULT_STOPLIST` の denylist に追加。除外理由は値でなく文脈で直交分離（ドメイン非秘匿 / 行が散文 / コードブロック内、ADR-043 整合）。TDD 新規（scope 7 / line_count+confidence 8 / codeblock FP 5 / suppression_ledger 16 / glossary denylist 1 / suppression 配線 5 / store_registry 宣言 2）+ 旧 confidence 契約（1行超過=auto_fixable）を主張していた既存テスト2件 + evolve_result_schema fixture を新仕様へ更新。決定論・LLM 非依存。
- correction 系カウンタの不整合とレビューフローの二重提示・stale 表示を解消 (#476)
  - capture_rate observability を channel 別表示（hook N / llm_judge M）にし、llm_judge が捕捉済みなら誤「枯渇」警告を抑制
  - weak_signals 件数に (全PJ集計) ラベルを付与し、bootstrap の (当PJ) 集計との桁の食い違いを明示
  - bootstrap が is_bootstrap=true で発火する run では daily_review から bootstrap-pending の signal_key を除外し二重提示を解消
  - `rl-reflect --promote-weak` が昇格後の `corrections_human` を返し、growth_report の対話前スナップショット問題を補正。`corrections（human-confirmed のみ）` の意味をレポート行に明示

## [1.98.0] - 2026-06-12

### Added
- **feat(evolve): プラグイン本体スキルを skill_evolve / pitfall 剪定の診断対象化（closes #185）** — rl-anything を rl-anything PJ 内で evolve すると本体スキル（repo 直下 `skills/`・23個）が skill_evolve 適性判定 / pitfall 剪定の対象外になり「カスタムスキル 0件のためスキップ」になる構造的ギャップを解消（issue Option C + B）。根本原因は `find_artifacts()` が `.claude/skills/` のみ走査していたこと。**(1) plugin_self origin の導入**: `scripts/lib/skill_origin.py` の `classify_skill_origin` に `plugin_self` を追加 — `.claude-plugin/plugin.json` を持つリポジトリ直下 `skills/<name>/` を本体スキルとして分類（`.claude/skills/` 配下のユーザー自作は対象外・判定は新規 private `_is_plugin_self_skill`）。`audit/classification.py` のテスト後方互換インライン経路も同じ判定を共有。**(2) find_artifacts の plugin_self スキャン**: `scripts/lib/audit/artifacts.py` の `find_artifacts()` が `.claude-plugin/plugin.json` 存在時のみ repo 直下 `skills/` を追加スキャン（#419 の収集除外を共有）。manifest 無しの通常 PJ では挙動を一切変えない（回帰ゼロ）。**(3) skill_evolve_assessment の対象化**: `scripts/lib/skill_evolve/assessment.py` が plugin_self を custom 同等に評価（batch_guard 母集団・per-skill ループの両方）。インストール済み他プラグイン（origin=plugin）は従来どおり除外。**(4) Option B（スキップ理由の明示・ADR-028）**: 除外した origin=plugin スキル数を `_meta=excluded_plugins` サマリで surface（silence ≠ evaluated）。**(5) pitfall 剪定**: `pitfall_hygiene` は origin フィルタを持たず find_artifacts 由来で自動解決（テストで確認のみ）。**(6) auto-apply 安全確認**: `is_protected_skill` を plugin_self も True に拡張 — evolve の診断は対象だが、SKILL.md を無人で書き換える唯一の経路（remediation の `fix_skill_evolve`）は protection ゲートで proposable（人間確認必須）に降格し直接書き換えを塞ぐ。run_evolve の assessment/hygiene phase は読み取りのみで SKILL.md を書かない。TDD 新規（origin 6 / find_artifacts 5 / assessment 2 / pitfall_hygiene 1 / auto-apply 安全 1）。決定論・LLM 非依存。スコープ外: Fitness Evolution のデータ蓄積条件（issue 根本原因2）。
- **feat(correction_semantic): confirmed idiom の PJ 横断優先提示 — cross-PJ 確認集約（closes #462）** — ある PJ で人間が confirm した idiom と**正規化テキスト一致**する他 PJ の未確認 idiom group を daily_review（#446）/ bootstrap_backlog（#443）の提示で**先頭に優先表示**し、機械可読フィールド `cross_pj_confirmed: ["<slug>", ...]`（承認済み他 PJ slug 一覧）を各 group に常時付与する。「git status じゃなくて git diff」のような全 PJ 共通の修正癖で PJ 数ぶんの重複 y/n 確認が daily_review 最大5件/日の帯域を削る問題に対処。新規 `scripts/lib/correction_semantic/cross_pj_priority.py`（`prioritize(groups, pj_slug, idioms_path=) → 一致 group を先頭へ安定 partition + ラベル付与`・read 専用の純関数）+ store に `read_cross_pj_confirmed_idiom_texts(pj_slug)`（**自 slug を除く** confirmed・非 revoke の {正規化テキスト: [他slug]} を集約）。**正規化は autopromote と共有**: store に `normalize_idiom_text`（strip のみ・exact-match の superset で既存 confirmed 照合を壊さない）を切り出し idiom_autopromote と cross_pj_priority の両方が同 1 関数を通す（二重実装しない）。**自動 confirmed 化・自動昇格はしない**（ADR-047 不変条件「人間が承認していないパターンは絶対に自動昇格しない」+ idiom_key 物理接地を維持・提示順とラベルのみ改善）。承認時は #463 の通常フロー（rl-reflect --promote-weak → confirm_idioms）がそのまま効く（本 issue で承認経路は変更しない）。実データ read-only dry-run: 全 313 idiom / confirmed は rl-anything の 30 件（dedup 26 テキスト）→ figma-to-code（116 未確認）/ amamo（48 未確認）で 26 件の cross-PJ confirmed テキストが利用可能、figma-to-code の "いやいや"/"わかりずらい" 2 group が rl-anything 確認済みとして先頭に surface・correction_idioms.jsonl は 313 行のまま不変（読み取り専用確認）。TDD 新規 15 テスト。決定論・LLM 非依存。設計 SoT: ADR-047 + issue #462。
- **feat(audit): ADR-046 重み昇格レディネスの決定論判定 outcome_promotion_readiness（closes #461）** — outcome 3軸（correction 再発率 / 一発成功率 / rework 率近似）を environment fitness の重みへ繰り入れてよいかを、ADR-046 が定めた3条件で決定論判定し audit/evolve に advisory surface する（スコア重みには未反映・判断期日に人が勘で判断するのを防ぐ機構）。新規 `scripts/lib/audit/outcome_promotion_readiness.py`（per-PJ 集計 + 3条件チェッカー、軸計算の素は outcome_metrics #423 の純ヘルパを再利用）+ `scripts/lib/audit/sections_promotion_readiness.py`（observability builder）。**条件1 分散が十分**: 代表軸（correction 再発率）の per-PJ 値が全 PJ で同値でないか（measurement_bug #445 の「全 PJ 同値 = 測定バグ強シグナル」思想を流用、PJ<2 は insufficient_pj）。**条件2 データ件数下限**: 分母（correction≥10 / sessions≥30）を満たす PJ が複数（≥2）あるか + PJ 別分母実測テーブル。**条件3 方向の妥当性**: optimize_history の human_accepted=True（reflect/evolve 適用）を anchor に前後窓（既定 14 日・ADR-044 準拠で実 PJ dry-run の観察値から決定）で first_try_success を比較し期待方向へ動く相関を判定。各条件を ✓/✗ + evidence（実測値・PJ 名）で出し、3条件すべて ✓ なら「重み昇格を提案」行を markdown / 構造化の両経路に surface（builder を `_OBSERVABILITY_BUILDERS`（ADR-028）に登録し自動伝播）。読み取りのみ（DATA_DIR への書込なし）。データ契約: corrections.jsonl(project_path) / sessions.jsonl(project) / optimize_history/<slug>.jsonl(human_accepted)。実 PJ dry-run: 条件1/2/3 すべて ✗（1 PJ のみ correction データ / sessions.jsonl ingest 済みで apply 前後窓 session 不足）を evidence 付きで確認・3 ストア mtime 不変。TDD 新規 22 テスト + snapshot 隔離。決定論・LLM 非依存。

### Fixed
- **fix(audit): outcome_promotion_readiness / outcome_metrics の session 系分母を session_store union read で実効化（closes #469）** — sessions.jsonl は #415 で DuckDB（sessions.db）へ ingest 後 rotate されるため live jsonl がほぼ存在しない。#461 の `outcome_promotion_readiness` は sessions.jsonl を直読していたため、条件2（sessions≥30 分母）と条件3（apply 前後窓の paired session）が構造的にほぼ常に空 = 永遠に ✗ になっていた（実データ dry-run でも条件3 は `no_paired_windows`: anchors=2 / paired session 0）。session 読みを **DuckDB sessions.db + 未 ingest live jsonl の union read** に切り替えて ADR-046 のレディネス判定を実効化する。**union read 関数は session store 側に 1 つだけ実装**（`session_store.read_session_records(data_dir=None, *, since=None)`）し、`outcome_metrics.read_sessions` 経由で `outcome_metrics`（first_try_success / rework）と `outcome_promotion_readiness`（per_pj_first_try_success / per_pj_rework / check_direction）の両方が共有する（二重実装しない）。重複排除は ingest の UNIQUE 制約と同じ `(session_id, timestamp)` キー（db 優先）。db は **read_only 接続**で開きスキーマ作成も mkdir もしないため dry-run の「1バイトも書かない」契約（#461）を維持。duckdb が import できない / db 不在時は jsonl のみへ graceful fallback（既存 HAS_DUCKDB パターンに準拠）。TDD: union read 単体（db のみ / jsonl のみ / 両方+重複 dedup / db 優先 / duckdb 無 fallback / since フィルタ / read-only でファイル増やさない・byte 不変）9 件 + 条件2/3 が db 側レコードで分母を得ること + db 経路 dry-run byte 不変 4 件。決定論・LLM 非依存。
- **fix(process): フルスイートが scripts/lib/tests（1111件）を収集しない問題を根治（closes #468）** — CLAUDE.md の canonical コマンド `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` が `scripts/lib/tests/`（correction_semantic / weak_signals / audit 系単体テストの大半）を**収集しておらず**、歴代のマージゲート・#457 のフルスイート計測がこの 1111 件を含んでいなかった（「コマンドはあるが網羅の保証が無い」ギャップ）。根治: `pytest.ini` に `testpaths = hooks skills scripts/tests scripts/rl/tests scripts/lib/tests bin/tests` を宣言し bare `python3 -m pytest` で全件（4747 件、従来 3608 → scripts/lib/tests 1136 件 + bin/tests 3 件込み）が走るようにしてパス列挙依存を断つ + CLAUDE.md テスト節を `python3 -m pytest -v` に簡約。再発防止: 「`test_*.py` を含む `tests/` ディレクトリが testpaths のどの path 配下にも入らない」漏れを決定論検出する audit チェックを追加（新規 `scripts/lib/testpaths_coverage.py` + observability builder `scripts/lib/audit/sections_testpaths.py` を `_OBSERVABILITY_BUILDERS`（ADR-028）に登録し markdown / 構造化の両経路へ自動伝播、orphan_store #422 と同思想の静的突合）。チェックは実リポジトリで未収録だった `bin/tests`（3 テスト）も検出し testpaths へ追加して uncovered=0 を達成。TDD 新規 10 テスト。決定論・LLM 非依存。
- **テスト隔離 defense-in-depth**: #464 同型バグ（import 時 `Path.home()` 由来の module-level 定数がテスト隔離を貫通しない）の再発防止を 3 件追加 (#471) — (1) **観測ビルダーの隔離漏れ構造ガード**: `scripts/tests/test_observability_isolation_guard.py` を新設。`_OBSERVABILITY_BUILDERS`（ADR-028）の各 builder が import する供給モジュールを AST 走査し、実 `~/.claude` 配下を指す module-level `Path` 定数のうち `_isolate_env` で中和されていないものがあれば fail し追加すべき (module, attr) を指示する（pristine 値を collection 時に frozen dict へ snapshot し live 状態変異に非依存・reload は pytest 内部を壊すため不使用）。ガード自体の検出力もメタテストで担保。実機 60MB の `token_usage_store.{DATA_DIR,USAGE_DB,USAGE_JSONL}` が未隔離だった潜在ギャップを併せて閉じる（`_isolate_env` に reload 追加）。(2) **dry-run の byte 照合強化**: `test_outcome_promotion_readiness.test_dry_run_no_store_write` をファイル名集合比較から `read_bytes()` の before/after 全照合に強化し既存ファイルへの追記・書換も検出。(3) **`scripts/lib/tests/conftest.py` の autouse HOME 隔離**: 新規テストが手動 setattr を忘れても実 `~/.claude` を読まないよう `isolate_home`（#457）を専用 tmp dir で autouse 適用。実 HOME を意図的に読むテストは `@pytest.mark.real_home` でオプトアウト。`scripts/lib/tests/` 1126 件全緑で既存テストとの共存を確認。決定論・LLM 非依存。
- **fix(reward): `--promote-weak` 承認時に対応 idiom を confirmed 化する配線を追加（closes #463）** — ADR-047 の `confirm_idioms` が本流から一度も呼ばれず、idiom_autopromote の雪崩防止不変条件（confirmed 0 件 → promoted 0）により自動昇格が永久 0 件だった配線漏れを修正。`rl-reflect --promote-weak` が promote 成功後に、承認シグナルへ対応する idiom を `confirm_idioms(confirmed_by="reflect_promote_weak")` で confirmed=True にマークする。signal→idiom の突合は新規ライブラリ関数 `correction_semantic.promote.resolve_idiom_keys_for_signals(signal_keys, ...)`（(pj_slug, source_path, line_no) の provenance 物理キー一致・promoted=True 後でも解決可能）。CLI に閉じる（ADR-045）— SKILL.md の散文に手順を足さず `--promote-weak` が confirmed まで一気通貫。dry-run はどのストア（corrections / weak_signals / correction_idioms）にも書かない（最下層 write ゲート貫通）。TDD: 閉ループ E2E（--promote-weak → confirmed → 同テキスト再発 signal が autopromote で実発火）+ provenance 突合 4 件 + dry-run ゼロ書込。決定論・LLM 非依存。
- fix(tests): test_audit_snapshot の order-dependent 隔離漏れを修正（closes #464） — `corrections_insights.CORRECTIONS_FILE` が import 時に `Path.home()` を固定するため `setenv("HOME")` 隔離が貫通せず、実 corrections.jsonl が 10 件（MIN_DISPLAY_RECORDS）を超えた 2026-06-12 に「繰り返し失敗パターン」セクション出現で snapshot mismatch が顕在化。`_isolate_env` の既存パターン（setattr 固定）で CORRECTIONS_FILE を tmp に差し替え。

## [1.97.0] - 2026-06-12

### Added
- **feat(reward): idiom_dict 自動昇格 — confirmed idiom テキスト一致の機械昇格 + 3つの安全弁（closes #447）** — 人間が一度 confirm した修正 idiom と同じ言い回しが再発したとき、毎回 AskUserQuestion で確認する摩擦を除去し weak_signal を機械昇格する。新規 `scripts/lib/correction_semantic/idiom_autopromote.py`（`autopromote(pj_slug, ...) → {promoted, capped, promoted_idioms, slug, dry_run}`・常時 emit）。**照合単位は「pj_slug × idiom テキスト」**（`read_confirmed_idiom_texts`）— 出現ごとに変わる idiom_key ハッシュで照合すると新規再発（別発話→別物理キー）が永遠に不一致で構造的 no-op になる設計欠陥をレビューで検出し修正（回帰テスト `test_promotes_same_text_new_occurrence`）。昇格レコードは `source="idiom_dict"` / `promoted_by="idiom_dict"` で corrections に追記され `HUMAN_SOURCES` に含む（human 起源の confirm を根拠とするためフェーズ昇格を駆動・revoke で巻き戻し可能）。**安全弁①** userConfig `idiom_autopromote_daily_cap`（既定10件/日・超過分は capped として次回繰り越し）。**安全弁②** audit/evolve の weak_signals observability に自動昇格の累計件数 + idiom 一覧を毎回 surface（黙って進まない・ADR-028）。**安全弁③** `rl-reflect --revoke-idiom <idiom_key>` — idiom を confirmed=False + revoked_at に戻し（テキスト単位で同テキスト全 record）、その idiom テキスト由来の `promoted_by="idiom_dict"` corrections を `invalidated=True` に原子的 rewrite（`invalidate_idiom_corrections`）。`count_human_corrections` は invalidated を除外するためフェーズ進捗が正しく巻き戻る（weak_signals の promoted=True は維持＝再提示しない）。confirmed が空なら即 promoted=0（初期データでの昇格雪崩防止）。evolve オーケストレーターに phase 配線（`result["idiom_autopromote"]` 常時 emit・dry-run ゼロ書込を最下層まで貫通）。実 PJ E2E: 全 11 PJ / 未確認 idiom 313 件に対し dry-run・非 dry-run とも promoted=0 / 3 ストア SHA256 不変（confirmed ゼロ時の不発火 invariant を実データで確認）。TDD 新規（autopromote 10 / invalidate 3 / observability 3 / revoke CLI 2 / evolve emit）。決定論・LLM 非依存。設計 SoT: docs/evolve/daily-evolve-reward-loop-design.md 機能#2 + ADR-047。
- feat(agent-brushup): agent frontmatter の `model:` フィールドが exact model ID（`claude-*-N` 形式）の場合に stale リスク警告として検出 — `check_model_pin()` 関数追加、`check_quality()` に `exact_model_id_pin` issue を統合、エイリアス/未指定は警告しない (closes #449)
- **feat(reward): evolve に「今日の修正確認」phase を追加（closes #446）** — 前回 evolve 以降の新規 weak_signal（channel=llm_judge・未昇格・非expired）を idiom 単位で group 化（個人辞書の物理キー突合 → 無ければキーワード jaccard≥0.5）・頻度降順・最大5件を `result["correction_review"]["daily"]` に**常時 emit**（新規 0 件でも eligible=False/groups=[] でキーを置く）。reflect SKILL Step 7.7（散文・手動起動のみ→昇格 0 件）からの移植で、毎日叩かれる evolve の決定論 phase 出力を SKILL.md Step 6.2 が消費し AskUserQuestion で y/n 確認（はい→ `rl-reflect --promote-weak`、promote 成功後のみ既読追記。部分失敗 group は追記しない＝取りこぼし防止）。既読ストア `correction_review_seen.jsonl` 新設（correction_judged と同方式の append-only 物理キー集合・PJ slug スコープ・read 側 set 化で重複追記冪等・detected_at cursor 案は同時刻境界バグで却下・store_registry 宣言済み）。「いいえ」は rejected 追記・「Skip」は追記なし（次回再提示）。dry-run は build（読み取りのみ）だけ走り既読集合に一切書かない（最下層 write ゲート貫通）。reflect Step 7.7 は手動全件レビュー用に残置 + 移植注記。TDD 新規 33 テスト。決定論・LLM 非依存。設計 SoT: docs/evolve/daily-evolve-reward-loop-design.md 機能#1。
- feat(reward): evolve レポート末尾に成長状態を決定論表示 — あと N 件で次フェーズ / 今日の昇格成果（closes #448） — 閾値の単一ソース化（growth_engine に STRUCTURED_CORRECTIONS_TARGET 等 6 定数を切り出し detect_phase / compute_phase_progress のリテラルを置換・挙動不変）、新規 `scripts/lib/growth_report.py`（build_growth_report・閾値リテラル直書き禁止・read-only・LLM 非依存）、evolve.py に result["growth_report"] を常時 emit（audit phase 後・error でもキーを置く）、SKILL.md Step 9 に growth_report.lines 列挙の指示を追記。
- **feat(reward): weak_signals に 45日 TTL を追加 — expired マークと昇格候補からの除外（closes #442）** — weak_signals.jsonl のレコードに TTL（45日・corrections の decay と整合）を導入。期限切れは削除せず `expired` マークし、`read_unpromoted` 等の昇格候補 reader から除外（古い修正候補を昇格させる label noise を防ぐ）。TTL 判定・マーキングは evolve の決定論 phase として常時 emit（dry-run はマーキング書込をしない＝persist ゲートを最下層まで貫通）。store_registry の weak_signals 宣言を retention=ttl(45日) に更新。設計 SoT: docs/evolve/daily-evolve-reward-loop-design.md 機能#5。
- **feat(reward): 初回バックログ bootstrap モード（closes #443）** — 既存 weak_signals バックログ（channel=llm_judge・未昇格・実環境 313 件）を初回 evolve でまとめて確認する入口がなく死蔵していた問題に対処。決定論 phase `correction_semantic/bootstrap_backlog.build(pj_slug, dry_run=)` が marker（`bootstrap_done-<slug>.marker`）未設定なら当該 PJ の未昇格 backlog を内容キーワード（漢字/カタカナ 2 字以上）jaccard≥0.5 で group 化し、`{is_bootstrap, pj_total, groups_total, groups}` を**常時 emit**する（eligible でなくても error でも result にキーを置く）。marker 立ち後は `is_bootstrap=False` で即返す（重い group 化をしない早期 return — 「TTL 失効に任せる」選択でも marker が立つ）。① **slug スコープ厳守**: backlog 集計は **cwd の PJ slug のみ**を対象（DATA_DIR 全PJ共通 pitfall・別 PJ の件数が混入しない）+ channel=llm_judge + promoted=False に絞り、#442（weak_signals TTL）並行実装に備え `expired` フィールドがあれば防御的に除外（深い依存を作らない浅い連携）。② **evolve 配線**: `result["correction_review"]["bootstrap"]` に相乗り emit（#431 の correction_review とキー共有・`setdefault` で防御的生成）。dry_run でも build（読み取りのみ）は走るが marker を書かない。③ **store_registry 宣言**（#434 ゲート）: `bootstrap_done-<slug>.marker` を writer_locus=batch / retention=permanent / disposition=drain で宣言。④ **SKILL.md（Step 6.1）**: phase 出力を消費するだけの記述。`is_bootstrap=True` のとき **AskUserQuestion で 3 択を人間が選ぶ**（まとめて確認 / 日次5件ずつ / TTL 失効に任せる）。機械は「アクティブ PJ」を判定せず件数を判断材料として表示するだけ（散文ステップで判定しない・#275 の教訓）。3 択いずれを選んでも Skip しても evolve 全体は完走する。**dry-run ゼロ書込**（pitfall_dryrun_stateful_store_write）を `mark_done` の最下層まで貫通させ E2E で marker 非書込を assert。TDD 新規（bootstrap unit 16 + evolve emit 2）。決定論・LLM 非依存。
### Changed
- chore(observability): weak_signals builder の文言に evolve 誘導行を追記 — 未昇格 N 件を `/rl-anything:evolve` の今日の修正確認 phase へ誘導するヒントを追加（closes #444）
- **feat(audit): measurement_bug メタ検査 — 複数 PJ で集計値が bit-exact 一致したら測定バグ候補として surface（closes #445, #185）** — learning_measurement_layer_diagnosis の「全 PJ 同値カウント = 測定バグ強シグナル」を #419-#423 では手動診断していたのを自動化して audit/evolve の observability に advisory surface する（スコア重みには未反映）。新規 `scripts/lib/audit/measurement_bug.py`（`detect_measurement_bug` 純関数 + `collect_cross_pj_metrics` の growth-state walk）+ `scripts/lib/audit/sections_measurement.py`（observability builder）。**決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出**し、≥3 PJ が同一の非ゼロ値（env_score / issues_total）を共有したら候補とする。0 同値は未測定・データ不足で正当に揃う（#423 既出）ため構造的に除外し FP を回避（precision 優先・ADR-043 と整合）。データ源は growth-state-*.json walk（rl-fleet status と同経路）で、issues_total は #419 と同じ 5 フィールド合計の契約を共有する。builder を `_OBSERVABILITY_BUILDERS`（ADR-028）に登録し markdown（report.py）/ 構造化（collect_observability）両経路へ自動伝播。先行例 fleet status の `detect_equal_issue_counts`（#419）と同じ検出方針（audit は ≥3 PJ で精度を上げる）。TDD 新規 25 テスト（detect 11 / collect 3 / builder 5 + observability/snapshot 隔離）。決定論・LLM 非依存。

### Fixed
- **fix(tests): run_evolve 系テストの実環境ストア読みを隔離しフルスイートを高速化（closes #457）** — フルスイートが約 32 分（1956.84s）かかっていた根因を実測で特定し根治。`run_evolve(project_dir=tmp_path)` でも後段 post-processing フェーズ（`utterance_archive.ingest.ingest_all_projects` / prune の global skill check / weak_signals 言い直し検出 / correction_semantic）が `Path.home()/.claude/projects`（実環境 ≈9925 jsonl / 1.9GB）を default 走査しており、ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR) 隔離は `Path.home()` 由来パスに効かないため実 store を読んでいた（cProfile 実測 8.69s/件、フルスイート内では cold cache 等で 24〜38s/件まで膨張、`test_evolve_batch_guard.py` 6 件で 182.92s）。新規 `scripts/lib/test_home_isolation.py` の `isolate_home(monkeypatch, tmp_path)` で `HOME` を空 tmp dir へ隔離（`Path.home()` は call-time に HOME を読むため import 後の monkeypatch で全フェーズに効く）し、`skills/evolve/scripts/tests/conftest.py` の autouse fixture で当該ディレクトリ全テストに適用 + `scripts/tests/test_evolve_result_schema.py` でも明示適用。conftest 名衝突（別ディレクトリの同名 conftest を sys.path で shadow）を避けるため helper は専用モジュール化。加えて `test_compaction_rebuilds_bloated_db` の bloat 構築を 60000 行の per-row INSERT ループ（43s）から DuckDB 側 `md5(random())` の bulk INSERT（INSERT 後 close→別 connection で DELETE で free page を残す）に置換し 0.39s に短縮。検証意図は不変（I/O 先のみ隔離・compaction 発火とデータ保全の assert は維持）。HOME 隔離不変条件テスト新規追加で再発検出。**before/after: フルスイート 1956.84s(32:36) → 66.80s(1:06)・3598 passed / 1 skipped・slow マーカー deselect 不要（全件根治）。** CLAUDE.md テスト節に run_evolve 系テストの HOME 隔離手順を追記。

## [1.96.0] - 2026-06-10

### Fixed
- fix(hooks): `tool_duration.py` を no-op 互換 shim として復活（#426 follow-up） — hook 登録はセッション開始時に固定されるため、v1.95.0 で本体を削除した後も旧セッションが発火し続け、毎回 Errno 2 の blocking error がユーザーに表示されていた。stdin 読み捨て + exit 0 の shim でエラー表示だけを止める（何も書き込まない）。旧セッションが掃けた次々リリースで削除可。`plugin.json` の `slow_threshold_ms` description も未使用の実態に追従（key は manifest 互換のため維持、config.py のコメントと整合）
- **fix(session-store): sessions.db 再肥大を書き込みパターンで根治 — jsonl-first + batch ingest 一本化（#415 Phase A）** — `session_store.append()` が hot path（hooks の発火ごと）で DuckDB に **per-fire connect→INSERT→close** していたのが sessions.db 再肥大（9.6GB / 実データ約14MB ≒680倍）の病巣だった。書き込みパターンを変更して根治: ① **append は jsonl 追記のみ**に変更し DuckDB 経路を削除（hot path から接続を消す）。② **`ingest()` 新設** — `sessions.jsonl → sessions.db` を**最上位 1 connection**で取り込む（DuckDB checkpoint pitfall 準拠、per-row connect 禁止を回帰テストで封じる）。重複除去キーは既存 `migrate_from_jsonl` と同じ `(session_id, timestamp)`。取り込み成功確認後に live jsonl を `.ingested-<ts>` へ rotate し、rotate 済みは glob で恒久除外（mtime 非依存）・1世代保持。③ **読み取り系を union read 化** — `count_unique_since` / `query` は現実装の排他分岐（db があれば jsonl を見ない）から、**db の結果 + 未 ingest jsonl の結果を `(session_id, timestamp)` で dedup 合算**へ書き換え。理由: trigger_engine 等は ingest と**非同期**（セッションイベント時）に count を読むため「ingest 直後にしか読まない」仮定は成立しない。④ **保険 compaction** — ingest 完走時に db ファイルサイズ vs `rows×平均行長` の乖離 >10倍（かつ絶対 4MB 超: DuckDB の最小ファイルサイズ床近辺の false compaction を防止）で、新規 db ファイルへ `ATTACH`+`CREATE TABLE AS` でコピー→ファイル swap の rebuild（in-place DROP/CREATE では DuckDB がブロックを返さないため fresh-file 方式）。⑤ ingest 呼び出しを evolve オーケストレーター（`skills/evolve/scripts/evolve.py`）の batch 文脈に同居（**dry-run 時は ingest しない** — DATA_DIR 非書込の規約）。⑥ **直読 reader を union read へ集約** — `telemetry_query.query_sessions`（`sessions_file` 未指定時）が旧 `_query_sessions_table` で **SESSIONS_DB を直読**して未 ingest jsonl を取りこぼしていたため、`session_store.query()`（union read）経由の `_query_sessions_via_store` に置換。下流 `discover.detect_missed_skills` も append 直後・ingest 前のセッションを正しく拾えるようになった（直読が残ると「未 ingest jsonl のセッションが見えない」実害が出る）。`telemetry_query_internal_surface` snapshot も追従更新。⑦ `store_registry.py` の sessions.jsonl 宣言を rotate 運用に整合（retention=compaction / disposition=drain）。既存呼び出し元（trigger_engine 等）は SessionStore API 経由なので無変更。TDD 新規（jsonl-only append / ingest rotate・1世代保持・dedup・1 connection / union read（未 ingest jsonl 反映・dedup・順序・limit）/ compaction 発火・データ保全）+ 既存 telemetry_query / discover テストの直読前提を union read 前提へ修正。決定論・LLM 非依存。

### Added
- **feat(reward): correction capture の二層化 — バッチ LLM 意味判定 + 個人辞書 + provenance 重み付け（closes #431）** — corrections.jsonl が累計 9 件中 本物の人間修正 1 件・残り 8 件が Stop hook の機械生成で、フェーズ昇格条件（corrections>=10）が永久未達 → 全 PJ が initial_nurturing 固定だった飢餓に対処。hot hook（語彙依存・拡充しない）の上に**意味論レイヤー**を足す。新 package `scripts/lib/correction_semantic/`（store / prompt / batch / promote / provenance_weight）。① **バッチ LLM 意味判定**（auto_memory の 2 相 / ADR-037 と同型・モデル Haiku）: #430 utterances.db の dialogue 発話を、Phase A `emit_judgement_requests`（決定論・LLM 非呼び出し・30 件/call バッチプロンプト）→ Phase B 応答（assistant・llm_broker 経由で Python は claude -p を呼ばない）→ Phase C `ingest_judgement_results`（決定論）で処理。「ユーザーが Claude の方向を正したターンか」を二値判定し、修正なら言い回し（idiom）を抽出。判定済み発話は物理キー（`correction_judged.jsonl`）で突合し再判定（無駄な LLM call）を防ぐ。非対話 PJ は query デフォルト `source_kinds=('dialogue',)` で除外。② **weak_signals 隔離記録**: 修正判定は corrections 本流に直接入れず、#432 と共有する weak_signals レーンへ **channel="llm_judge"** で記録（reflect 確認後に昇格）。③ **個人辞書** `correction_idioms.jsonl`: 抽出した言い回しを provenance（元発話の物理キー・判定理由）付きで蓄積。実コーパスで precision 検証後に hot hook の補助パターンへ昇格可能。④ **provenance 重み付け**（`provenance_weight`）: フェーズ昇格カウント（growth_engine の corrections>=10）を **human-source のみ**（`HUMAN_SOURCES={reflect_confirmed}`・`correction_type=stop` と source=hook/backfill は機械として除外）で駆動するよう `audit/orchestrator._build_growth_report` を修正。機械ノイズで状態が動かないことをテストで保証（human=2/total=12 で Structured Nurturing に昇格しない / human=10 で昇格する）。Growth Report は `Corrections: N (human) / M (total)` 表示に。⑤ **reflect 昇格フロー**: `rl-reflect --show-weak-signals [--weak-channel llm_judge]` で未昇格レコード表示 → 人間確認 → `rl-reflect --promote-weak <signal_key,...>` で corrections へ **source=reflect_confirmed**（human-source）レコードを追記 + weak_signal を promoted=True にマーク（二重昇格防止・dry-run ゼロ書込）。SKILL.md に Step 7.7 追記。⑥ **store_registry 宣言**（#434 ゲート）: `correction_idioms.jsonl` / `correction_judged.jsonl` を `writer_locus="batch"` で宣言（後者は disposition=drain の自己消費）。⑦ **evolve 配線 + slug 統一**: evolve オーケストレーターに correction_semantic の Phase A emit を同居（件数・トークン見積りを surface）。あわせて weak_signals 配線の `_ws_slug = Path(project_dir).name`（worktree 内実行で worktree 名になり utterances.db の pj_slug と食い違う PR #440 の既知課題）を、utterance_archive と同型の slug 導出（`/.claude/worktrees/` で切って本体 repo basename）に統一する `_resolve_pj_slug` へ修正。**dry-run ゼロ書込**（pitfall_dryrun_stateful_store_write）を 3 ストアとも最下層 write まで貫通させ E2E で書き込みゼロを assert。TDD 新規（provenance_weight 8 / store 10 / prompt 6 / batch 7 / promote 6 / growth phase 2 / reflect CLI 4 / evolve slug 4 = 47 テスト）。決定論・LLM 非依存（テストは responses dict を直接渡し claude CLI を一切呼ばない）。
- **feat(reward): 暗黙修正シグナルの決定論検出 → weak_signals レーン（#432）** — 明示的な修正発話は語彙依存で稀（#431）だが、修正の**行動シグナル**は語彙非依存でゼロ LLM 検出できる。4 チャネルをバッチ側（hot path 非介入）で検出し、新ストア `weak_signals.jsonl` に provenance 付きで記録する基盤。**corrections 本流には直接入れない**（deny は「今はやるな」・手編集は「続きの作業」の可能性があり本質的にノイジー）。昇格は reflect 確認後（`promoted` フラグ）。新 package `scripts/lib/weak_signals/`（store / detectors / batch）。① **4 チャネル検出器**（決定論・ゼロ LLM）: **直後手編集** = transcript の `<tool_use_error>File has been modified since read...`（attribution は `user_or_linter` と明示）/ **permission deny** = `errors.jsonl` の `permission_denied` レコード（既設 hook を reader 化）/ **言い直し** = #430 utterances.db の同一セッション連続発話の jaccard token 重複（`similarity.tokenize`/`jaccard_coefficient`）/ **Esc 中断** = transcript の `[Request interrupted` user text block。② **言い直しのしきい値 0.8 は実コーパス dry-run で決定**（ADR-044 準拠・固定値を設計前に決めない）: rl-anything 全 PJ utterances.db 3204 発話 / 1289 連続ペアの分布を実測し、0.6 では並列 agent 派遣テンプレの誤検知が大半、0.8 + dispatch 除外で 16 ペア（目視 100% が真の言い直し/再送）。FP 除去は**個別 allowlist でなく「機構生成テンプレ」という除外理由の直交分離**（learning_detector_fp_context_not_allowlist 準拠）。③ **store**: dedup キー（channel+provenance の安定ハッシュ）でバッチ再実行の二重記録を防ぎ、**dry-run は最下層 write まで一切書かない**（pitfall_dryrun_stateful_store_write・E2E で書き込みゼロを assert）。④ **store_registry 宣言**（#434 事前ゲート）: `weak_signals.jsonl` を writer/reader/retention 宣言。batch script 書き込み jsonl が hook-writer 突合に出ず stale 誤検知される問題を、`writer_locus="batch"` フィールド + `stale_exempt_names()`（db と batch を集約）で解消。⑤ **配線**: evolve オーケストレーター（utterance ingest の後段 — 言い直し検出が更新済み utterances.db を入力に使うため）に `run_batch` を同居（dry-run でも検出は走り書き込みのみ弾く）。observability builder `build_weak_signals_section`（`audit/sections_weak_signals.py`）を `_OBSERVABILITY_BUILDERS` に登録しチャネル別件数・未昇格数を advisory surface（スコア重み非関与）。実コーパス検出: 直後手編集 6 / permission deny 5 / 言い直し（全 PJ）16 / Esc 中断 33。TDD 新規 30 テスト・決定論・ゼロ LLM。
- **feat(utterance): 全PJ human 発話の恒久アーカイブ utterances.db（#430・Phase B）** — transcript（`cleanupPeriodDays` で消える）に毎日失われていた human 発話を、ゼロ LLM の batch ingest で恒久 DuckDB ストアに蓄積する基盤。correction 個人辞書（#431）・暗黙シグナル（#432）・遡及分析の土台。新 package `scripts/lib/utterance_archive/`（extractor / store / ingest / query）。① **extractor**: `~/.claude/projects/*/*.jsonl` から human 発話のみ抽出。`isMeta`/`toolUseResult`/`tool_result` content・harness 注入6種（`<system-reminder`/`<command-name`/`<local-command`/`Caveat:`/`[Request interrupted`/`This session is being continued`）を除外。>2000字は `source_kind='long_paste'`、非対話 PJ（`EXCLUDED_PJ_SLUGS`初期値 `bots`）は `excluded_pj` でタグ分類。`prev_action` は直前 human 後の assistant tool_use 名を出現順 join（上限10+`…`）。pj_slug は **encoded dir 名のデコードを諦め transcript の `cwd` から導出**（`/.claude/worktrees/` を切って本体 repo へ正規化、ハイフン入り名を truncate しない。cwd 欠損時は encoded dir 名 fallback）。② **store**: 物理 PK `(source_path, line_no)` + 論理 UNIQUE `(session_id, timestamp, text_hash)` で resume の履歴 replay 複製を弾く。最上位1 connection（DuckDB checkpoint pitfall 準拠）。`ingest_state(mtime, line_offset)` で増分。完走時に staleness marker `utterances_last_ingest_at` を書く。③ **query**: `query_utterances(pj_slug 必須, source_kinds=('dialogue',) デフォルト)` + 横断は明示関数 `query_utterances_all_projects()`（全PJ共通 DATA_DIR 単一ファイル pitfall の read 側照合を API で強制）。④ **配線**: evolve オーケストレーター（dry-run 時は ingest しない）/ `rl-fleet ingest` サブコマンド / SessionStart staleness advisory（`restore_state._deliver_utterance_staleness`、marker 読みのみの observe-first・marker 不在=未 ingest=advisory・閾値14日）。⑤ `store_registry` を `.db` ストア対応に拡張（`kind` フィールド追加、`utterances.db` を retention=permanent で宣言、contract-drift の stale 突合から db を除外）。実機 1 PJ E2E（rl-anything 実 transcript 131 files）: wall 1.76s / 495 件 ingest（dialogue 436 + long_paste 59）/ DB 3MB / 機構ターン混入 0%（20サンプル目視）。DATA_DIR は ADR-042 resolver 経由。TDD 新規（55 テスト）・決定論・ゼロ LLM。設計 SoT: docs/evolve/utterance-archive-430-415-design.md。
- feat(fitness): outcome 2軸（一発成功率 / rework 率）を per-skill 帰属して evolve ターゲットランキングへ自動入力（advisory→閉ループの先行配線）。`audit/outcome_attribution.py` が usage(skill→session_id)↔sessions(error_count/tool_sequence) を in-memory join し、triage 候補を outcome priority 降順に再配置。dry-run 結果に before/after の順位差分を surface（DATA_DIR 非書込）。corrections 再発率軸・fitness 重み変更は別 issue (#433)

- **chore(observe): ストア新設の事前契約ゲート — writer/reader/retention 宣言を必須化（#434）** — orphan_store（#422/#426/#427）は「writer あり reader 0」の**事後**検出でモグラ叩き（`message_display.jsonl` #427 が代表例）だった。新 jsonl ストア追加時に **writer / reader / retention の3点宣言を必須化**する事前ゲートを追加。① 宣言 SoT を機械可読な Python dict で新設（`scripts/lib/store_registry.py` の `StoreDeclaration` リスト。`_OBSERVABILITY_BUILDERS` 等の既存宣言慣習に統一、消費側 orphan_store から import 一発で参照し JSON parse 経路を増やさない）。retention は `permanent`/`ttl`(N日・`ttl_days` 必須)/`compaction`(条件散文必須) の3種別を `validate_declarations` で整合性検証、orphan の処遇は `disposition`(`keep_future`/`drain`/`remove`)で明示。② orphan_store に `detect_store_contract_drift` を追加 — **宣言なしの新規 writer = undeclared**（reader 有無に関わらず検出）/ **宣言あり実 writer 不在 = stale** / 宣言不整合 を突合し、observability builder `build_store_contract_section`（`audit/sections_orphan.py`）を `_OBSERVABILITY_BUILDERS` に登録して audit/evolve に surface。③ 既存 hook writer 9 ストア（corrections/usage/usage-registry/sessions/errors/workflows/skill_activations/subagents/message_display）を宣言バックフィル、`message_display.jsonl`（#427 の orphan）は disposition=keep_future + retention=compaction（1MB ローテーション）で記録。`test_all_live_hook_writers_are_declared` が宣言漏れを回帰検出、`test_contract_section_detects_undeclared_writer` が「宣言なしで新ストアに書く hook を追加すると audit が検出する」を回帰保証。TDD 新規。決定論・LLM 非依存。

## [1.95.0] - 2026-06-10

### Added
- **feat(fitness): アウトカム指標 v1 — utilization 恒久0 の修理 + 行動アウトカム3軸の advisory 導入（#423）** — env_score が全 PJ で 0.6 前後・Lv.6-7 頭打ちだった構造要因2つに対処。**(1) utilization 修理**: `telemetry._find_all_skills` を audit 収集系 `audit.artifacts.find_project_skill_dirs`（`.claude/skills/` と plugin レイアウトのリポジトリ直下 `skills/` の両走査 + #419 収集除外を共有）に統一。plugin レイアウトの本リポジトリで skills 0→21 / utilization 0.0→≈0.54 を実測（telemetry 重み25%が死に枠だった根因の修理、他軸の計算式は不変）。**(2) 行動アウトカム3軸（advisory・スコア重みには未反映）**: correction 再発率 / 一発成功率 / rework 率(近似) を既存ストア（corrections.jsonl / sessions.jsonl）から決定論算出し、observability builder `build_outcome_metrics_section`（`audit/sections_outcome.py`）を `_OBSERVABILITY_BUILDERS` に登録して audit/evolve のたびに surface。各軸に evidence（件数・session_id 例）を併記、データ不足の軸は「データ不足」を明示、3軸とも該当ストア皆無なら None で沈黙。rework は既存ストアに編集対象ファイル ID が無いため tool_sequence の編集バーストを近似 proxy とし限界を ADR-046 に明記。重み昇格は 2〜4 週 advisory 並走→分布実測→判断（[ADR-046]）。TDD 新規（plugin レイアウト探索・3軸算出・builder・observability/snapshot 隔離）。決定論・LLM 非依存。

- **feat(reward): 報酬入力の飢餓解消 — correction capture 率の監視 + SessionStart 自動 drain（#421）** — RL ループの報酬データが実測でほぼ空（corrections 9 件 / 76 日・全件 reflect skipped、evolve_decisions ≒0 件）で、capture 率が正常か異常かを誰も監視していなかった（ADR-041 の決定論キャプチャ配線は正しいが上流に水が流れていない）。2点で対処: ① **correction capture 率を observability に追加**（`scripts/lib/capture_rate.py` + `audit/sections_capture.py`）。capture 率 = 「直近 N 日で `min_turns`(=20)+ ターンを持つセッション（usage.jsonl の同一 session_id レコード数を proxy）」のうち「同一 session に correction を 1 件以上検出したセッション」の割合。分母（active session）と分子（correction を持つ session）を併記して audit/evolve に surface し、9 件/76 日が検出器の仕様通りの少なさか capture 漏れかを判別可能にする。observability contract（`_OBSERVABILITY_BUILDERS`）に登録し markdown/構造化 両経路へ自動伝播。**スコア重みには入れない**（advisory のみ — 壊れた入力の上に重みを作らない）。実機で active 6 件中 capture 0%（枯渇兆候）を即 surface。② **SessionStart 自動 drain**（`restore_state._deliver_evolve_drain`）。#402 のリマインド表示のみだった `_deliver_evolve_drain_reminder` を「apply 済み提案を実際に drain して optimize_history（fitness 母集団）へ記録する」自動回収へ昇格。pending marker 不在は MARKER_ROOT ディレクトリ存在チェック → slug 解決 → marker ファイル存在チェックの軽量 early-return で重い経路に入らず（実測: no-marker 0.001 ms/call、旧リマインドは無条件 `resolve_slug` で ≈6.5 ms/call だったので**むしろ高速化**）。`undrained_applied`（marker の before_sha と現ディスク sha 突合・optimize_history 非読込）をゲートにし、未 apply のときは marker を温存して将来の apply を取り逃さない。drain の書き込み先は `_resolve_canonical_history_file`（marker ゲート付き `rl_common.resolve_data_dir`）で tool reader と同一の正準 DATA_DIR に固定し hook/tool の DATA_DIR split（#358/#364）を踏まない。drain 中の例外で hook を落とさない（degrade）。TDD 新規 14 件（capture_rate 8 / section 4 + drain 6 を auto-drain へ書き換え）+ 既存 observability contract 隔離を 1 件拡張。決定論・LLM 非依存。

### Changed
- **chore(observe): 読者ゼロ観測 `tool_durations.jsonl` を削減し orphan store 検出を audit に追加（#422）** — 主要ストアの producer→consumer 突合で「書きっぱなしで誰も読まない」観測を特定。`tool_durations.jsonl`（実環境 5.1MB）は `hooks/tool_duration.py` が**全 Bash 実行ごとに python3 を起動**して書き込むが reader（scripts/skills 側）が 0 で、純粋なレイテンシ + ディスクコストだった。① tool_duration hook の登録（hooks.json の Bash PostToolUse グループ）・本体・単体テストを削除し、関連ドキュメント（CLAUDE.md/SPEC.md/spec/components.md/spec/architecture.md の Observe hooks 個数 21→20・README の hook 表と 14→13 表記）を整合。② この手動突合を決定論化する `orphan_store.py` を新設 — **writer = hooks.json に登録された hook 本体が書く jsonl ファイル名**（未登録 hook は発火しないので対象外＝false positive 防止）/ **reader = scripts/・skills/（tests 除外）に現れる jsonl ファイル名** をストアファイル名文字列の出現で静的突合し、writer にあって reader 0 のストアを orphan 報告。observability builder `build_orphan_store_section`（`audit/sections_orphan.py`）を `_OBSERVABILITY_BUILDERS` に登録し evolve のたびに surface。`slow_threshold_ms` userConfig は manifest 18 項目を保つため key を残置（未使用化を明記）。TDD 新規（疑似プラグインツリーで writer/reader/orphan 突合 + 実ツリーで tool_durations 不検出ガード + observability/snapshot 隔離）。決定論・LLM 非依存。

### Fixed
- **fix(test-hygiene): growth-journal のテスト汚染を許可リスト方式から構造的隔離へ（#420）** — 実環境 `growth-journal.jsonl` の 87%（977 中 852 件）が test 実行で汚染（`project` が `test_*`/`tmp*`/`unknown`）していた根因を断つ。`scripts/lib/growth_journal.py` は `DATA_DIR` を **import 時に確定**するため、conftest autouse fixture の per-test `monkeypatch.setenv`（import より後）も、手動 patch 許可リスト（session_store / token_usage_store / optimize_history_store の 3 件）にも入らず「4 匹目のモグラ」になっていた。対策3層: ① conftest **トップレベル**で全テストモジュール import より先に `CLAUDE_PLUGIN_DATA` を session 一時 dir に固定（import 時キャプチャ組も実 home から構造的に隔離）② autouse fixture の手動 patch 許可リストを撤去し、`sys.modules` を走査して module-level `DATA_DIR`/`_DATA_DIR_VAL` と派生 `Path` 属性を per-test `tmp_path` に rebase する**機械 sweep** に置換（新 store 追加時の隔離漏れが原理的に起きない）③ pytest 下で scripts/lib 配下の store モジュールが実 home に解決しないことを機械列挙で assert する不変条件テストを追加。加えて `scripts/purge_growth_journal_test_pollution.py`（デフォルト dry-run・`--apply` で backup 付き除去・`unknown`/空は対象外）を追加。TDD 新規46件、全スイート 3546 passed。決定論・LLM 非依存。
- **fix(audit): fleet ISSUES 599 件は測定バグ — hardcoded_values 検出パイプライン3点修正（#419）** — `rl-fleet status` の ISSUES 列が全 PJ で 600 前後（bots/receipt/figma-to-code はぴったり 599 で一致）に揃い env_score の物差しとして死んでいた。再現で 599 件を完全再現し根因3点を確定: ① `hardcoded_detector` の `sk-` regex に単語境界がなく、gstack スキル散文の `ask-only-for-one-way` 等が単語内部にマッチ（api_key 552 件＝全体の 92% を占める FP）→ `(?<![A-Za-z0-9])` 境界を追加。② 検出ループの二重実装の divergence — `audit/issues.py` には global/plugin origin 除外があるが `audit/orchestrator.py` の同型ループには無く、除外なし経路で外部管理スキルの散文まで走査していた → 共通関数 `collect_hardcoded_value_issues` に集約し両 call site が共有。③ 走査対象の汚染 — `find_artifacts` の `rglob("SKILL.md")` が `node_modules` / `.hermes`（`.hermes/skills/` 入れ子含む）/ `.git` 等の任意 dot-dir まで再帰 → `is_excluded_skill_path` を node_modules + 最初の `skills/` 以降の dot-dir 配下を除外するよう拡張。再発予防として fleet status に「複数 PJ の ISSUES total が同値（非ゼロ）なら測定バグ警報」の不変条件チェック（`detect_equal_issue_counts`）を追加し `_run_status` で surface。TDD 新規（regex 境界の FP/回帰 + 本物 secret 検出維持 + 共通関数共有 + 収集除外 + 同値カウント警報）。決定論・LLM 非依存。

## [1.94.1] - 2026-06-10

### Fixed
- **fix(data-dir): `merge_db` のスキーマ乖離・並行書き込みをロバスト化（#414 follow-up, #417）** — `rl-fleet migrate-data` の DuckDB マージで、src/old に同名テーブルがあり列構成が食い違うと `CREATE TABLE AS ... UNION` が Binder Error を投げ、per-entry failure に落ち→ marker が永久に書かれず→ SessionStart リマインドが永久発火する**永久失敗ループ**になっていた（#417 優先）。新規 `_merge_table_both` で3段フォールバック: ① 列完全一致 → 従来 UNION（高速路）② 列集合の差分（バージョン跨ぎの列追加/削除）→ 列名で揃えた superset union（欠損列は `NULL` 補完、行・列とも損失なし）③ 和解不能な型差 → old を残し src を `{table}__src_unmerged` 別テーブルへ退避（**データ損失ゼロ**、`format_summary` が要手動統合として surface）。型乖離でも完走するため marker が立ちループを断つ。並行書き込み窓（#417-2）対策として append-only ログ（`.jsonl`/単発）は merge 前後の `(mtime_ns, size)` 差分でマージ中の外部追記を検知し削除を見送る（次回再実行で dedup 回収、`.db` は writable ATTACH の WAL replay で source 自身が変わるため idle 実行ガイダンスで対処）。UNION の行折り畳み（#417-3）は jsonl 行 dedup と同設計の意図的挙動として docstring 明文化（PK 持ちストアは無害）。副次で、`migrate-data` 実行後に resolver の marker チェックが実 home を読んで probe 系テストが落ちる test 衛生バグ（`pitfall_resolver_marker_reads_real_home`）を `fallback` fixture の実 home 隔離で根治。TDD 新規10件（スキーマ乖離3パターン + 並行窓 + marker 完走）+ 既存8件を隔離修正。決定論・LLM 非依存。

## [1.94.0] - 2026-06-10

### Added
- **feat(data-dir): DATA_DIR hook/tool 分裂の一元化 migration（#364 Phase 2）** — #358/ADR-042 は reader 側の正準化に留まり、書き込み側の分裂（sessions.jsonl が tool 側 5/25 停止・hook 側現役と**鮮度逆転**、errors.jsonl 同様、usage.jsonl は**二重書き**）が実害として残っていた。正準 = `~/.claude/rl-anything` 固定（#402 の env 非依存固定パス前例に整合。plugin-data dir は `<marketplace>-<plugin>` 命名依存で脆い）。① **marker ゲート redirect**: `rl_common.resolve_data_dir`（新設・純関数）が CC install レイアウト（`~/.claude/plugins/data/*`）を指す CLAUDE_PLUGIN_DATA を、正準 dir の marker `.data-dir-unified` 存在時のみ正準へ向け直す。テスト isolation（tmp dir env）は無条件尊重で conftest 隔離を壊さない。`hook_store_path` も marker 存在時は正準を返す（migration 後に ADR-042 の probe が「migration 済みの空 dir」を読む逆転を防止）。② **`rl-fleet migrate-data`**（`scripts/lib/data_dir_migration.py`）: `.jsonl`=行 dedup append / `.db`=DuckDB テーブル単位 union dedup（**書込可 ATTACH で WAL replay**、per-fire connection 開閉で肥大した sessions.db の compaction を兼ねる: 実測 9.6GB・84k 行・実データ約 14MB）/ `.wal`=コピーせず削除（正準側の別 db と不整合ペアになるため）/ その他 = mtime newer-wins、`tmp`/`__pycache__` 除外。dry-run は書き込みゼロ（pitfall_dryrun_stateful_store_write）。marker は全 entry 成功時のみ書き、部分失敗は再実行で回収（冪等）。③ **実行順序の構造化**: 旧版 hook 稼働中に migrate すると分裂が即再発するため、本 fix を含む版のインストール**後**に 1 回実行する。SessionStart（`restore_state._deliver_data_dir_migration_reminder`）が CLAUDE_PLUGIN_DATA env（install レイアウト配下のときのみ＝実環境 probe をテストに漏らさない）から未解消を検出して案内し、migration で marker が立ち自然終息（install ≠ enforcement 対策、#402 drain リマインドと同型）。TDD 新規 26 件（マージ規則・E2E・dry-run 無副作用・冪等・redirect・hook_store_dir・リマインド）。実環境 dry-run で errors 31,800 行 / sessions 60,499 行 / usage 1,892 行等のマージ計画を確認済み。決定論・LLM 非依存。
### Added
- **feat(evolve): observe 先行 pre-flight（`rl-evolve --observe-first`）で軽量判定を重い処理の前に効かせる（#407）** — `observe`（前回 evolve 以降の新規観測有無）は usage.jsonl の行数カウントだけで O(ms) なのに、それを算出するために従来は全フェーズ（discover/audit/skill_evolve/remediation/prune…約20分）を完走してから SKILL Step 1 で「軽量モードにするか」を尋ねており、lightweight 分岐が事実上の事後通知だった。新フラグ `--observe-first` で安価な observe + fitness ゲートだけ算出して early-return（重いフェーズを回さない）。SKILL Step 1 はまず pre-flight で `action`（lightweight/skip/backfill/full）を判定し、フルが必要なときだけ `--observe-first` 無しの dry-run を別途走らせる。実機で 20分→**0.08秒**。dry-run 冒頭の無音対策として、フル実行前に `env_tier` ベースの所要時間目安（small≈1–3分 / medium≈3–8分 / large≈8–20分）をユーザーへ surface することを SKILL.md に MUST 化。

### Changed
- **feat(evolve): 実行結果に同一性 metadata を必須化し別PJ/stale 取り違えを防ぐ（#408 A/B/C/E）** — evolve 出力 JSON に「誰の（どのPJ）・いつの・本実行か」を機械検証する手段が `skill_name` からの推測しか無く、共有固定パス `/tmp/rl_evolve_out.json` に残った別PJの stale 出力を誤読する事故が起きた（dry-run のため無傷だったが本実行なら「別PJのデータで対象PJを変更」になりうる）。修正4点: ① result トップレベルに `slug` / `project_dir` / `generated_at` / `env_tier_reason`（count・breakdown・thresholds で tier 決定根拠を可視化, #408-E）を必須化。② `slug` は `optimize_history_store.resolve_slug`（git-common-dir 親で正規化, ADR-031）で算出し、worktree から呼んでも本体 PJ slug に正規化（SKILL Step 0.5 の `git rev-parse --show-toplevel` basename が worktree 名を返す #408-C を是正）。③ SKILL Step 1 の `--output` を PJ別パス `/tmp/rl_evolve_<slug>.json` に変更し、読込後 slug 照合を MUST 化。④ CLI 1行サマリにも slug/project_dir/generated_at を surface。TDD 新規。決定論・LLM 非依存。
- **docs(CLAUDE.md): コンポーネント表の詳細を `spec/components.md` に移管し CLAUDE.md を 34.4KB → 11.4KB に削減（毎セッション約 6k トークンの context 課税を恒久解消）** — CLAUDE.md は全セッション・全ターンで context に載るが、コンポーネント表の設計経緯プローズ（`evolve_decisions` 単体で 2.5KB 等）が肥大の主因だった。詳細（設計経緯・根拠・issue/ADR 参照）は新設の `spec/components.md` を SoT とし、CLAUDE.md には「1 行サマリ + 実体ファイル」のコンパクト表のみ残す。`spec_trigger` の仕様アーティファクト定義は `spec/**` を含むため仕様 SoT の検出対象は維持される。**運用ルール: 新コンポーネント追加・変更時は spec/components.md に詳細を書き、CLAUDE.md には 1 行だけ追記する**（表ヘッダに明記）。

### Fixed
- **fix(evolve): constitutional の None を「LLM 評価に失敗しました」と誤表示する文言を撤去（#408-D）** — constitutional は [ADR-037] で LLM を全廃し「cache 済みレイヤーのみ集約、全 miss なら `None`」という LLM-free 設計。`None` の正体は「cache が stale / 全 miss で再採点が必要」なのに、レポート本文が ADR-037 全廃前の残骸文言「LLM 評価に失敗しました」を出し、しかも `warnings[]` にも `observability` にも乗らず（silence != evaluated 違反）取り違えを招いていた。`audit/sections.py` の文言を「未算出: cache stale/全 miss（失敗ではない）→ audit Step 3.5 の2相 refresh で再生成」に修正。さらに evolve.py に `_surface_constitutional_status`（cache-only 再集約・LLM 非依存・安価）を新設し、`None`/`overall=None` のとき状態を `warnings[]` と `observability["constitutional"]` に昇格。TDD 新規。決定論・LLM 非依存。
- **fix(hooks): correction_detect が CC 実ペイロードの `prompt` フィールドを読まず、UserPromptSubmit 起点の修正検出が初期実装から一度も発火していなかった（#409）** — CC の UserPromptSubmit イベントは発話を top-level `prompt`（str）で渡すが、`hooks/correction_detect.py` は `event["message"]`（str/dict 形）しか読まなかった。初期実装（328eddb6）から一貫してこの形だったため、**実環境ではユーザー発話の修正パターン（「いや、そうじゃなくて」等）が誕生以来一度も corrections.jsonl に記録されておらず**、既存レコードは save_state（Stop hook feedback）由来のみだった。既存テスト 76 件が全て合成の `message` 形だったため緑のまま機能不全（`learning_synthetic_fixture_false_confidence` の実例）。corrections を上流とする reflect / auto-memory / optimize / constraint_decay / trigger_engine がユーザー発話シグナルを受け取れていなかった。修正は `event.get("prompt")` の優先読み + 旧 `message` 形のフォールバック温存。実ペイロード形（`{"session_id", "transcript_path", "cwd", "hook_event_name", "prompt"}`）の回帰テスト 2 件追加。実ペイロード E2E で `chigau 0.85` の検出復活を実測確認。決定論・LLM 非依存。

### Removed
- **chore(skills): 死蔵していた `skills/enrich/` を削除** — enrich は discover に統合済み（deprecated）だが、SKILL.md の無い scripts ディレクトリだけがプラグインに同梱され続けていた。リポジトリ全域で `skills/enrich` への参照ゼロ・import ゼロを確認のうえ削除（evolve の `enrich` phase は `skills/evolve/scripts/evolve.py` 内で完結しており無関係）。

## [1.93.0] - 2026-06-09

### Added
- **feat(evolve): drain（Step 7.8）の enforcement gap を是正 — `rl-evolve --drain` + SessionStart リマインド（#402）** — accept/reject を母集団 `optimize_history` に記録する `ingest_decisions`（drain）が、決定論コードでなく `skills/evolve/SKILL.md` の指示文だけから呼ばれており、assistant が飛ばすと母集団が永久に空＝fitness が `0/30` から動かない（#360 / `learning_skill_md_must_not_enforcement` と同系統）。修正は3点: ① `evolve_decisions.drain_pending`（`rl-evolve --drain` の実体）で SKILL.md を inline python から**単一コマンド**へ集約し記録漏れの失敗面を縮小。② emit が `--dry-run` でも env 非依存の固定パス `~/.claude/rl-anything/evolve_pending/<slug>.json` に「未 drain 提案」マーカー（`before_sha` 付き）を記録（評価 store/queue とは別の運用状態、drain でクリア）。③ **SessionStart hook**（`restore_state._deliver_evolve_drain_reminder`）が `undrained_applied`（marker の before_sha と現ディスク sha を突合、`optimize_history` を読まない）で「適用済みなのに未 drain」を検出して `rl-evolve --drain` を促す。drain は **tool 文脈（CLI）**で走り reader と同一 DATA_DIR に書くため hook/tool の DATA_DIR split（#358 / `pitfall_datadir_hook_tool_split`）を踏まない。Stop hook auto-drain は apply タイミング非依存にできず env scrub も脆弱なため**不採用**（second-opinion 反映）。timing 問題は「次 SessionStart で見る」ことで構造回避。冪等性（`ingest` の `{pid}_{kind}` entry_id dedup）で「未 apply 空振り→後で apply→再 drain」でも accept は一度だけ記録される。TDD 新規19件（drain 11 / SessionStart リマインド 4 / 既存 +4）。全テストの実 home 汚染を conftest autouse + harness で構造的に封じた。決定論・LLM 非依存。

### Fixed
- **fix(tests): pre-existing なテスト2件の環境依存 false failure を根治（test 衛生）** — canonical な `python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` では緑だが、実行順・収集経路が変わると落ちる潜在 2 件を root-cause で解消。① **`test_no_orphan_archived_skill_refs` の非ハーメチック FP** — オーファン検査が開発機の**グローバル runtime archive**（`~/.claude/rl-anything/archive`、全 PJ・全プラグインの prune 結果が混在）を読むため、他プラグイン由来の `openspec-apply-change`（openspec プラグイン）が archive に入ると、それを正当なフィクスチャ文字列として使う `skills/prune/scripts/tests/test_prune.py` を「オーファン参照」と誤検知していた。`_was_repo_skill`（git 履歴に `skills/<name>/` があるかで判定）を追加し **rl-anything 自身がリポジトリで持っていたスキルのみ**を検査対象に絞る（他プラグインの archive 名は rl-anything の関心事でない）。回帰テスト1件追加。② **`fitness` パッケージ名衝突による collection error** — dead な重複パッケージ `scripts/fitness/`（`__init__.py` + `skill_quality.py` のみ）が本物 `scripts/rl/fitness/`（`coherence` 等を持つ・本番ローダが常に参照）を sys.path 上で shadow し、収集順次第で `from fitness import coherence` が coherence 無しの方に解決され `test_coherence.py`/`test_coherence_snapshot.py` が ImportError で collection error を起こしていた。本番コード・テストとも `scripts/fitness/` への import/パス参照ゼロを確認のうえ削除（`skill_quality` の CSO ロジックは canonical な `scripts/rl/fitness/` 側が上位互換）。フル 4265 passed / 1 skipped。決定論・LLM 非依存。
- **fix(evolve): dry-run 運用で fitness 母集団が永久に貯まらない根因を是正（#400 バグ#1）** — evolve の標準フローは `rl-evolve --dry-run` で分析 → assistant が対話適用、だが `emit_decisions` が `--dry-run` 時にキュー（before_sha）を書かず、旧 Step 7.8 も「dry-run のため未記録」で ingest をスキップしたため、**accept が永久に記録されず optimize_history が空＝fitness が `0/30` から動かない**根因になっていた（ADR-041 の効果が実運用で出ず、`learning_install_is_not_enforcement` の再発）。検証が dry-run で行われ「apply 後にしか出ない効果」を構造的に観測できなかったのが見落としの真因（`learning_dryrun_verification_blind_spot`）。修正: `ingest_decisions(pending=result.evolve_decisions.pending)` で**キュー不在でも result 同梱の pending を直接消費**し apply 後のディスク差分から accept を取る。Step 7.8 は分析が dry-run でも apply 完了後に必ず `dry_run=False` で ingest（純プレビューは全件 skip で self-correcting）。writer(ingest)/reader(fitness load_history) が同一正準ストア `optimize_history_store.history_path(resolve_slug())` を共有することを確認（パス分裂なし）。**apply 境界をまたぐ E2E 回帰テスト**（emit dry-run→apply→ingest→fitness が +1 を観測）で再発封じ。TDD 新規4件。決定論・LLM 非依存。
- **fix(evolve): skill_evolve↔archive の reconcile 欠如を是正（#400 バグ#2）** — 同一スキルが prune の archive 候補かつ skill_evolve high/medium（自己進化を組み込め）と評価される矛盾を解消する reconcile が split↔archive にしか無かった。新規 `evolve_reconcile.reconcile_skill_evolve_archive`（evolve.py Phase 4.2、emit より前）が archive 優先で assessments を `suppressed_by_archive` へ降格（emit_decisions が high/medium のみ拾うので母集団からも外れる）+ high/medium カウント再計算 + remediation の `skill_evolve_candidate` issue を除外し count 整合。除外は `evolve_suppressed_by_archive` に記録（silent に消さない）。TDD 新規5件。決定論・LLM 非依存。
- **fix(skill_evolve): batch_guard が課金ゼロ確定でも停止＋全再実行する無駄を是正（#400 バグ#3）** — 全スキルが cache-fresh（refresh_needed 合計0）＝Phase B の繰り延べコストも ≈0 と機械的に確定しているのに、AskUserQuestion で停止し `--confirmed-batch` で evolve 全体を再実行していた。`skill_evolve_assessment` の batch_guard pre-check で `refresh_needed` 合計が0なら guard sentinel を返さず通常の評価ループへ自動進行（1件でも refresh が要れば従来どおり停止）。SKILL.md / reference も「表示は実見込み `estimated_tokens_cache_aware` を先頭に、worst-case は括弧内参考値」（バグ#4）へ追従。TDD 新規2件。決定論・LLM 非依存。
- **fix(evolve): remediation の batch_skip 件数を observability に強制 surface（#400 バグ#6）** — 低 confidence の proposable まとめスキップ群が1行も表示されず「何件・何を握り潰したか」が完全に不可視で `silence != evaluated` 原則に反していた（SKILL.md の surface MUST が `SKILL.md MUST != enforcement` で守られない）。新規 `evolve_reconcile.build_remediation_batch_skip_observability` が件数を `result["observability"]["remediation_batch_skip"]` に決定論で昇格し Step 3.8 が必ず surface（0件でも「✓ 0件」を残す）。TDD 新規4件。決定論・LLM 非依存。

### Changed
- **feat(skill_evolve): usage=0 のスキルを batch_guard の母集団から事前除外（#400 改善）** — 使用実績ゼロのスキルは自己進化（実ミス蓄積）の効果が無く `insufficient_usage` に降格されるのに guard 母集団に含まれ、実例では評価対象14件中10件が保留で実質4件のために guard が発火していた。`skill_evolve_assessment` の effective targets から `usage_count==0`（検証系は除く）を事前除外。判定は per-skill ループと同じ `compute_telemetry_scores`。閾値超過の見込みがある時だけ評価しコスト最小化。
- **test(evolve): 非 dry-run の outcome 検証用テスト PJ ハーネスを新設（#400 follow-up）** — dry-run 検証は apply 境界を越えないため「dry-run では緑だが実 evolve で効果が出ない」（バグ#1 の症状）を構造的に見逃した（`learning_dryrun_verification_blind_spot`）。各テストがバラバラに組んでいたミニ PJ を `scripts/tests/evolve_pj_harness.py` に集約（隔離 DATA_DIR で正準 store を temp に向け、`apply_skill_change` で apply 境界を模す）。`scripts/tests/test_evolve_e2e_nondryrun.py` が emit→**apply**→ingest→fitness の実サイクルと reconcile/observability の wiring を、**dry-run 出力でなく正準 store の差分（outcome）**で assert（母集団+1 / reconcile 抑制 / batch_skip surface / 純プレビュー副作用なし / reject 記録 / reconcile→batch_skip count 整合、新規6件）。ハーネス fixture は実契約 `proposable_custom_batch_skip(int) == len(classified list)`（schema:71）を満たすよう統一し、reconcile の `len(kept)` 同期と合成したとき count が0に潰れる false green を封じた（`learning_synthetic_fixture_false_confidence`）。これが緑な限り同症状は再発不能。決定論・LLM 非依存（assessment の LLM 判定や apply customization は integration 送り）。
- **feat(fitness_evolution): insufficient_data の結論を1行 next_action で締める（#400 バグ#5）** — 冗長な3段説明で次アクションが埋もれていたのを、`next_action`（提案あり→「放置でOK」/ 提案なし→「このPJでは fitness は使わない設計。対応不要」）の1行に集約。evolve.py が現 run の提案有無（skill_evolve high/medium + discover matched_skills）で確定し、SKILL.md Step 8 はこれを最終行にそのまま出す。`evolve_result_schema.CANONICAL` に `fitness_evolution.next_action` / `skill_evolve_archive_reconcile.suppressed` を登録。TDD 新規4件。

## [1.92.1] - 2026-06-09

### Fixed
- **fix(evolve): observability の誤検知2件を是正（cross_skill の `[category]` 未展開 / unmanaged_pitfalls が worktree を拾う）（#393）** — docs-platform の evolve dry-run で observability に2件の検出ノイズが出た。① pitfalls.md の Root-cause がテンプレ未展開（`[category]`）のまま記録されると `cross_skill_analysis` のキーが `[category]` になり「何のカテゴリで横断しているか」が読めず共通ルール化判断ができなかった → `pitfall_manager/runner.py` の横断集計で角括弧プレースホルダ（`_is_placeholder_category`）を除外。② `pitfall_registry._DISCOVERY_IGNORE` に `worktrees` を追加し、`.claude/worktrees/<name>/...` の一時作業コピー（本体スキルの pitfalls.md と同一内容）を `unmanaged_candidates` が「未登録」と誤検知しないようにした。TDD 新規4件。決定論・LLM 非依存。
- **fix(hook_drift): 検出元パス（evidence）を併記し独自検証の誤判断を防ぐ（#394-1）** — `hook_drift` が「実環境は 1.57.0.0」とだけ出し根拠（どのファイル由来か）が無いため、検証で `gstack --version` の PATH フォールバックが flow-chain.json を読み戻して逆の結論を出しかけた。`HookDriftReport` に `pinned_source`/`actual_source` を追加し、`sections_hook` の警告に「pinned の出元: ~/.gstack/flow-chain.json」「実環境の出元: ~/.gstack/.last-setup-version」を併記。TDD 新規3件。決定論・LLM 非依存。

### Changed
- **feat(skill_evolve): batch_guard の再実行が LLM-free であることを機械可読フラグで明示（#394-2）** — `estimated_tokens_cache_aware` は `cache_fresh_count==0` のとき worst-case と同値になり「≈0」の根拠に使えない。実際に再実行が課金ゼロなのは `--confirmed-batch` 再実行自体が LLM-free（ADR-037）だからで、`estimated_tokens*` は Phase B judgment refresh の繰り延べコスト見積もりに過ぎない。batch_guard_trigger sentinel に `rerun_llm_free: true` と `estimate_meaning` を追加し、フィールドの意味と再実行ゼロの根拠を分離。SKILL.md / skill-evolve-assessment.md も追従。TDD 新規1件。
- **docs(evolve): SKILL/reference の乖離2件を是正（evolve.py 直叩き→rl-evolve ラッパー / skill_evolve 出力の正準明示）（#395）** — ① batch_guard 再実行手順の `python3 evolve.py ...` を、インストール時に PATH に入る `rl-evolve --confirmed-batch ...` ラッパーに統一（実パスの glob 探索が空振りしていた）。② `high_suitability` 等は**件数(int)**で `assessments[]` が正準の詳細配列（フィールド名は `skill` でなく `skill_name`）であることを SKILL.md / reference に明示（`high_suitability[].skill` の配列展開で空振りする事故を防止、#379 の機械可読化と整合）。
- **feat(evolve): 新規観測0でのフル評価 no-op に軽量モードを提案 + fitness 鶏卵問題を正直に説明（#396）** — ① `check_data_sufficiency` に `no_new_observations`（過去データ十分だが前回 evolve 以降の新規観測0）を追加し、observe phase の action を `lightweight_recommended` に。SKILL.md Step 1 が「observability surface のみ確認して重い LLM フェーズ/batch_guard をスキップ」する軽量モードを AskUserQuestion で提案（べき等性は保ちつつ操作コスト削減）。② `fitness_evolution` の insufficient_data メッセージを正直化 — `already_evolved` 飽和（high/medium=0）かつ `matched_skills=0` の PJ では提案自体が構造的に出ず evolve を回しても 0/N のまま（「evolve を回せば貯まる」が空手形）であることと、その PJ では remediation 中心が正常で無理に母集団を貯める必要がないことを明示。TDD 新規3件。決定論・LLM 非依存。

## [1.92.0] - 2026-06-09

### Added
- **feat(spec_trigger): main 着地の仕様未追従マージを SessionStart で検出し spec-keeper/ADR を提案（ADR-044）** — 仕様変更の後に SPEC.md/ADR を追従させたいが、現状トリガー（`spec-keeper-trigger.md` ルール + gstack `ship→spec-keeper`）は /ship 経由でしか発火せず、`gh pr merge` 直叩き・GitHub web squash マージ（この PJ の実マージ手段、直近 #384/#382/#386 等）では無音だった。ルール記載は assistant が忘れる＝`SKILL.md MUST ≠ enforcement` の穴。web squash は「自分のセッション外で main が進んだ」状態でローカルイベント(Stop/PostToolUse)では原理的に拾えないため検知点は **SessionStart 一択**。新規 `scripts/lib/spec_trigger.py`（決定論・LLM 非依存）の `detect()` を `hooks/restore_state.py` の `_deliver_spec_drift()` が fail-safe 呼び出し（新規 hook 不要、既存配信機構に相乗り）。**ゲートは実コーパス 40 commit への dry 適用で較正**（learning_synthetic_fixture_false_confidence: FP/FN は実コーパスでしか分からない）: 素朴 `feat:`+plugin.json 監視=8件全部 version bump の FP / structural-only=0件で死蔵 / 広域=12件中10件が `fix:` の FP → **feat/refactor/feat! × `scripts**.py`・`hooks**.py` 変更 × 仕様アーティファクト未更新で 2件の真 TP**。データが教えた設計修正2点: ① 仕様アーティファクト集合に **CLAUDE.md** を含める（この PJ の生きた仕様は SPEC.md でなく CLAUDE.md の component table、SPEC.md 単点は FP/FN 源）② **`fix:` を信号源から除外**（バグ修正は挙動を触るが仕様は変えない）。ADR 化は breaking(`!`) のみ併記。重複抑制は cooldown(3日)+リマインド1回で打ち止め（at-most-once は `silence≠evaluated` 再発のため不可）＋**解消プロキシ**（スキャン範囲に仕様アーティファクトを触った commit があれば pending 全クリア＝dev が仕様維持＝沈黙）。trunk(main/master) を解決できなければ沈黙（HEAD=現在ブランチには落とさない＝master 既定リポでの自分の作業中ブランチ誤提案を防止、/review 指摘）。`detect(persist=False)` でマーカー書き込みゼロ（pitfall_dryrun_stateful_store_write）。slug は `optimize_history_store.resolve_slug`（worktree 安全, ADR-031）。`userConfig.spec_trigger_enabled`(default true) で無効化可。グローバルルール `spec-keeper-trigger.md` は他 PJ も使うため痩せさせず現状維持・hook は加算的 enforcement（second-opinion の一元化案からの意図的逸脱）。TDD（ゲート純関数11 + 実 temp-git E2E 10 + commit_type 3 = 新規24件、実コーパスで本物モジュールが FIRE=2 を再現）。決定論・LLM 非依存。

## [1.91.0] - 2026-06-09

### Fixed
- **fix(skill_extractor): `routing.trigger_keywords` のノイズの真因（機構ターン混入）を実 PJ E2E で特定・根絶（#387, #381 follow-up）** — #381 マージ後に実 PJ（rl-anything、169 transcript→`max_files=50`）で本流経路を E2E 実走すると、`trigger_keywords` に `if`/`not`/`md`/`claude`/`gstack`/`users`/`todoroki`/`toolu`/`duration` 等のノイズ語が混入していた（合成 fixture では露見せず＝`learning_synthetic_fixture_false_confidence` の再現）。当初 issue は「stopword 拡充」と framing したが、実データ調査で**真因は stopword 不足ではなく**、`user_prompt` に **compaction サマリ・SKILL.md 本体注入・`<task-notification>`・`<system-reminder>`・Stop hook feedback** という「type=user だがユーザー発話でない harness 注入ターン」が混入し、そのパス（`/Users/todoroki/…`）や tool-use-id（`toolu_…`）がキーワード採掘を汚していたと判明（root-cause-first）。3層で対処: ① **機構ターンフィルタ**（`trajectory_sampler._is_machinery_prompt` + `_find_preceding_user_prompt` 配線）— 直前プロンプト探索で機構ターンを飛ばし本物の人間依頼を拾う。これが最大の出所を source で断つ ② **static stopword 拡充**（`decomposition._STOPWORDS` に英語機能語 if/not/is/then 等 + `_EXTENSIONS` に md/py/json 等の拡張子 token、いずれも環境非依存なので静的に持つ）③ **corpus document-frequency 減衰**（`corpus_frequent_tokens` — 環境固有の遍在語をハードコード allowlist せず「ほぼ全スキルに出る token」を DF で落とす、`learning_detector_fp_context_not_allowlist` 準拠）。実 PJ E2E で `claude` が TOP8 中 5候補→1候補（残1件は実発話「claude -p は全部なくしたい」由来の**真陽性**で抑制しない）、`gstack`/パスjunk/tool-dump は全消滅、trigger_keywords が実ユーザー発話（"1bやったら"/"最新のmainとりこんで"/"作成して"）になることを実証（13候補 0.31s）。**受け入れ条件の reframe**: 当初の「review/plan/spec が残る」は、それらが SKILL.md ボイラープレート由来だったため「機構ターンを残す」と同義であり想定違いだった（ユーザー承認済み）。副次効果として `sample_prompts` も機構ターンを surface しなくなり改善。TDD（機構マーカー検出/実依頼非検出・機構スキップで本物依頼を拾う・機構のみ時は空・E2E 抽出、stopword 英語機能語/拡張子除外、corpus DF 遍在語検出/少数コーパス空/static 事前除外、本流経路で遍在語除去×固有語保持、新規31件）。全 2068 テスト緑。決定論・LLM 非依存。
- **fix(evolve): result-schema 契約 + usage==0 ガードで doc↔impl / usage↔suitability の drift を封じる（P1: #375 #376, #378）** — ① evolve result JSON の正準スキーマ契約 `evolve_result_schema.CANONICAL` を1ソース化し、`check_conformance` が実 dry-run result との一致を・`extract_documented_paths` が SKILL.md の dotted path ⊆ canonical を検査（合成 fixture でなく実 `run_evolve(dry_run)` で dogfood、誤キーは contract test で将来の再ドリフトを封じる）。② `skill_evolve_assessment` が未使用スキル（`usage_count==0`）を軒並み medium（変換可能）と判定していたのを `insufficient_usage` に降格（自己進化＝pitfalls 蓄積は実ミスが溜まったスキルに効く仕組みのため本末転倒だった）。終端処理を `_finalize_suitability` に集約（バッチ/単体2経路で共有・DRY）、検証系は medium 維持。③ `triage_all_skills` の result 初期化に SKIP/REVIEW バケツが無く、triage_ledger が SKIP/REVIEW recommendation を返すと `result[action].append` が KeyError でクラッシュする pre-existing バグを実体化・修正（#375 の CANONICAL が REVIEW を valid と宣言したことで顕在化）。`/review` の specialist+adversarial 4 subagent レビューで検出。決定論・LLM 非依存。
- **fix(hardcoded_detector): 説明文中の Bot ID と markdown テーブル内 URL/ARN の過剰検出を是正（#377-2, #382）** — #359 の doc 文脈抑制が未カバーだった2形態を root-cause（issue 化前の `detect_hardcoded_values` 段階）で除外。① 説明文中の実 Bot ID（`B0…`）が slack_id(0.65) で誤検知 → `_SLACK_DOC_ID_RE` の除外プレフィックスに B0(bot) を追加（bot **token**(xoxb-) は秘匿対象だが bot **ID** は C0/A0 と同質の公開参照値。U(user)/W は PII 寄りのため除外せず過剰抑制を回避）。② markdown テーブル行の Secret ARN/URL が aws_arn(0.75)/service_url(0.55) で誤検知 → `_MARKDOWN_TABLE_ROW_RE`（行頭 `|` ＋区切り `|`）を追加（`resource: arn:…` 代入は `|` 始まりにならず構文的に交わらないため代入文脈の検出は維持）。TDD 新規7件。決定論・LLM 非依存。
- **fix(evolve): fitness insufficient_data の導線文言を evolve 自動蓄積込みに是正（#377-4, #384）** — 母集団が貯まらない本体は ADR-041/evolve_decisions（#360-A）で構造的に解決済み（evolve 実行で discover `matched_skills` / skill_evolve high·medium の accept/reject が optimize_history へ自動記録される）なのに、SKILL.md Step 8 の案内が #360-A 以前の古い手動導線（rl-loop/rl-optimize で accept しろ）のままで「手動で貯めなければ」と誤解する状態だった。`fitness_evolution.py` の message（SoT）は既に反映済みのため SKILL.md / evolve-fitness SKILL.md の表示文言を実装に追従させ、「skill_evolve は採点対象外」の stale 記述も ADR-041 に追従（採点対象外なのは remediation の fix=rules/hook・構造修正のみ）。決定論・LLM 非依存。

### Added
- **feat(evolve): result-schema 契約の runtime self-detect を追加（型 drift / usage↔suitability 矛盾）（#380, #377-5）** — P1（#375/#376）で導入した `evolve_result_schema.CANONICAL` 契約を runtime で consume し、evolve のたびに実 result へ当てて設計の歪みを self_analysis で surface する。新規 `scripts/lib/evolve_consistency.py` が ① CANONICAL との型レベル drift（`check_conformance_structured` の wrong_kind/item_key_missing/null_not_allowed のみ。`missing` は部分実行・phase gating の FP ノイズ源のため runtime 除外、完全性は test-time が enforce）② `usage_count==0` なのに suitability∈{high,medium}（#376 修正後 0 件＝regression guard）を検出し、`evolve_introspect._detect_improvement_opportunities` に遅延 import で合流（手動 CLI 止まりにしない＝evolve のたび発火）。健全時 0 件でも improvement zero_line に「整合性 drift なし」を残す（silence≠evaluated）。#379 hardening 同梱（`check_conformance` の機械可読化 `check_conformance_structured`・逆方向契約テスト `COVERED_PHASES ∪ UNCOVERED_PHASES`・`documented_path_drift` の longest-prefix 照合化＋bracket 記法・doc 走査を `references/**/*.md` へ拡張）。TDD 新規24件。全 3376 テスト緑。決定論・LLM 非依存。
- **feat(skill_evolve): batch_guard 見積もりを cache-aware 化（worst-case と実見込みを併記）（#377-1, #385）** — batch_guard の `estimated_tokens` が worst-case（全スキル Phase B 想定）のみで「コスト大」の誤解を生んでいた。Phase B（judgment refresh）は `emit_judgment_requests(refresh=False)` が `is_fresh_llm`（hash 一致 AND `judgment_source==llm`）のスキルを skip するため cache-fresh の実コストは ≈0、`--confirmed-batch` 再実行自体も [ADR-037] で LLM-free（assessment は cache-read）。`is_fresh_llm_judgment` を SoT 述語として抽出し emit_judgment_requests と見積もりで共有（skip 条件と見積もりが drift しない構造）、group に `estimated_tokens_cache_aware`/`cache_fresh_count`/`refresh_needed_count` を追加（worst-case の `estimated_tokens` は後方互換で残置）。TDD 新規7件。決定論・LLM 非依存。
- **feat(remediation): proposable を confidence で個別承認/まとめスキップに2分割し質問攻めを防ぐ（#377-3, #386）** — Step 5.5 の per-item 承認 MUST が低 confidence FP 群（conf 0.5 中心）で AskUserQuestion 連発（質問攻め）になる問題を、しきい値判定を決定論コードに置いて是正（SKILL.md 文言依存は「MUST が効かない」class の再発のため）。`partition_proposable_by_confidence`（しきい値 0.7）が conf>=0.7→individual（1件ずつ個別承認）/ conf<0.7→batch_skip（既定でまとめスキップ、個別展開は任意）に分割（両リスト conf 降順安定ソート・入力非破壊・欠落は batch_skip 側）、evolve.py が `proposable_custom_individual`/`proposable_custom_batch_skip` を surface、CANONICAL に4キー追加（契約 drift 検出を維持）。batch_skip は1行表示（MUST NOT: 1件ずつ AskUserQuestion）、個別対象0件なら「✓」を残す（沈黙≠評価）。TDD 新規12件。決定論・LLM 非依存。
- **feat(skill_extractor): 軌跡スキル候補に Workflow-to-Skill の4軸構造分解を付与（#381）** — tech-eval で抽出した唯一の本質ギャップ。`skill_extractor` は成功軌跡をスキル名でグルーピングして `generalizability_score` を付けるだけで、Workflow-to-Skill (arXiv 2606.06893) が提案する `routing`/`workflow`/`semantics`/`attachments` の構造分解を持たず、候補採用時に「どこで発火・何が要るか」を人が後から調べる必要があった。新規 `scripts/lib/skill_extractor/decomposition.py` の `decompose_candidate` が TrajectoryRecord 群から4軸を**決定論的**に導く（LLM 非依存）: ① **routing**（いつ発火するか）= user_prompt の頻出 trigger_keywords + 代表プロンプト ② **workflow**（どう実行されるか、手順は軌跡に残らないため実行プロファイルで近似）= 呼び出し回数 + outcome 分布 ③ **semantics**（何をするか）= namespace/base_name ④ **attachments**（どの文脈に anchor されているか ≒ 必要リソースの広がり）= distinct session 数、単一セッション由来なら `session_bound=True`（一過性バーストで reuse 証拠が弱い）。`projects` は cross-project 直接 API 用に残置（実 discover の採掘は単一 PJ scope のため projects は弁別せず、`session_count` が wired path でも定着度を弁別する — レビューで死に信号だった旧 `project_bound` を作り直した）。`extract_skill_candidates` の各候補に `decomposition` を付与し、discover runner の `_trajectory_candidates_to_missed` が採用判断に効く2軸（routing/attachments）を merged にも持ち上げて triage/report で surface。配線先は `run_discover`→`skill_extractor`（evolve が回す recurring ループ）で手動 CLI 止まりにしない。`discover/SKILL.md` Step 2 に「候補テーブルに routing/attachments 列を必ず出す」を明記。tokenize/stopword は `agent_team` と同規則を流用。TDD（4軸の存在・空入力骨格維持・routing キーワード抽出/上限・workflow outcome 分布・semantics namespace 分離・attachments session_bound/session_count/cross-project projects・extract 統合、新規14件）。全 4180 テスト緑。決定論・LLM 非依存。

## [1.90.1] - 2026-06-08

### Fixed
- **fix(evolve): `quality_traces` フェーズで握り潰されていた2段ラッチバグを実 PJ ドッグフードで発見・修正** — v1.90.0 リリース直後に実 PJ（sys-bots）で**非 dry-run** のフル evolve を回したところ、`quality_traces` フェーズが `result["phases"]["quality_traces"] = {"error": ...}` で無言失敗し続けていた（self_analysis #299 が high severity で正しく検出）。dry-run では `record_quality_score` が `if not dry_run` ガードでスキップされ、かつテレメトリの薄い PJ では `analyze_traces` が `MIN_SESSION_SAMPLES` 未満で None を返し早期 return するため、**非 dry-run かつ実テレメトリのある PJ でしか発火しない**二重の隠れ方をしていた。① **None 同士のソート比較**（`telemetry_query/usage_errors.py:208`）: `sorted(session_records, key=lambda r: r.get("ts", r.get("timestamp", "")))` の `dict.get(key, default)` は **default がキー欠落時のみ適用され値が `None` のときは None を返す**ため、実テレメトリの `"ts": null` レコードで `'<' not supported between instances of 'NoneType' and 'NoneType'` を投げていた。`r.get("ts") or r.get("timestamp") or ""` の `or` チェーンで None を `""` に畳んで修正。② ①の奥に隠れていた死蔵 import（`quality_engine.record_quality_score`）: `from hooks.common import DATA_DIR` が (a) standalone tool 実行で `hooks` パッケージが import 不能 (b) そもそも `hooks/common.py` に `DATA_DIR` シンボルが無い、の二重で #38(v1.15.0) 以来ずっと壊れており、①のソートクラッシュに隠れて表面化していなかった（非 dry-run の record で常時 `No module named 'hooks'`）。canonical な `from rl_common import DATA_DIR`（fleet_config 等と同経路）に置換。修正後、sys-bots 実機で quality_traces エラー消失・**18スキルのスコア記録**（0→18）・self_analysis ランタイムエラー 0 を実証。TDD（null ts ソート非クラッシュ・default DATA_DIR 経路の非 ModuleNotFoundError、新規2件）。全 4098 テスト緑。決定論・LLM 非依存。

## [1.90.0] - 2026-06-08

### Added
- **feat(evolve): 提案の accept/reject を日次ループで決定論キャプチャし fitness calibration 母集団 `optimize_history` を育てる（#360, [ADR-041]）** — calibration regression 検出（`check_calibration_regression`）の母集団が全 PJ で空だった根因は「accept/reject 記録が evolve SKILL.md の MUST（assistant が手で `record_evolve_diff_decision` を叩く）止まりで決定論コードから呼ばれず実行され損ねていた」こと（`install ≠ enforcement` の SKILL.md 版、#360 当初の「writer 不在」前提は誤りで配線自体は存在した）。新規 `scripts/lib/evolve_decisions.py` が emit→（インライン適用）→drain の2相で **accept=適用実績（ディスク before/after_sha 差分）/ reject=明示却下 / skip=記録しない**（C: ハイブリッド）を取る。`emit_decisions`（`run_evolve` 末尾）が discover の `matched_skills` と skill_evolve の high/medium 適性提案の before_sha をキュー `DATA_DIR/evolve_decisions/<slug>.jsonl` に上書きスナップショット、`ingest_decisions`（evolve SKILL.md Step 7.8 drain）が適用された diff を accept・明示却下 id を reject として既存 `record_evolve_diff_decision` 経由で optimize_history へ冪等記録（fitness_func=`skill_quality` で母集団は混合でなく増量）。accept がディスク差分由来なので記録ステップ未実行という失敗モードを構造的に塞ぐ。remediation fix は target 異種（rules/hooks/構造）で均質性を壊すため対象外。`--dry-run` は emit/ingest とも非書込（pitfall_dryrun_stateful_store_write 準拠）。evolve SKILL.md Step 3 の手動 inline python は drain へ統合。TDD（emit の matched_skills/skill_evolve 抽出・dedup・dry-run 非書込、ingest の accept(適用)/reject(明示)/skip(無変更)/dry-run・queue clear、計13件）+ 本番デフォルト経路 E2E（optimize_history へ実書込を確認）。決定論・LLM 非依存。

### Changed
- **chore(evolve): evolve SKILL.md（989行）を progressive disclosure で 611 行へリファクタ（-38%）** — 肥大化した orchestration スキルを「WHEN（MUST/判断）は全て inline 維持 / rare・conditional 分岐の HOW（コード）と純粋 rationale を `references/*.md` へ外出し」の原則で再構成。新設 reference 9本（proposal-protocol / world-context / skill-evolve-assessment / remediation / prune-merge / glossary-seed / report-narration / recommended-actions / self-analysis）。逐次実行スキルの step-skipping を避けるため MUST one-liner は全て本文に残し、各 Step に `→ references/xxx.md` ポインタを付与。**毎回走る critical drain（Step 6.5 auto-memory / Step 7.8 evolve-decisions #360-A）のコードは inline 維持**（reference 化すると read-hop を挟み「記録ステップ未実行」= #360 同型の失敗を再導入するため）。挙動不変（指示・MUST・出力契約は不変、コード/テンプレ/rationale の配置のみ変更）。全テスト緑・`claude plugin validate` 緑。

### Fixed
- **fix(evolve): dry-run 実機検証で発見した observability/指示書の既存ズレ3件を修正** — evolve リファクタ後の実機 dry-run（rl-anything 自身）で SKILL.md 参照キーを実出力と突合して発見した既存バグ。① **Step 6 の dead reference**: SKILL.md Step 6 が存在しない `phases.reflect.pending_count` を参照していた（reflect は独立フェーズでなく discover に統合済み、未処理件数は `phases.discover.reflect_data_count` にあり Step 10.1 は既に正参照）。Step 6 を `reflect_data_count` 参照に直し、出力に無い「前回 reflect 日付（7日条件）」を件数判定に置換。② **glossary jargon 候補に汎用語混入**: `glossary_drift.py` の denylist に `HEAD/IO/FP/HOLD/DEPRECATED/FALLBACK/RM/SKILL`（git/メタ/汎用状態語）を追加（#353⑫ の AWS 略語除外と同種）。実機で候補 21→13 件、PJ 固有語（DuckDB/VeriTrace/MemOS 等の CamelCase）は残存。③ **agent_team「孤立」の過剰警告**: 役割重複なし・孤立のみの編成を `sections_agent.py` で ⚠「改善余地」から ℹ に下げ「ユーザー直接起動型なら正常」を明示（design-review/doc-writer 等の直接起動型専門家がルーター未参照で誤って改善対象に挙がる問題）。検出ロジック（`agent_team.py`）は不変、表示の重要度・文言のみ変更。TDD（glossary 汎用語除外+固有語残存／agent_team 孤立のみ ℹ・重複あり ⚠ 維持、新規2件）。全 4096 テスト緑。決定論・LLM 非依存。
- **fix(remediation): `known_fp_patterns` を `_should_exclude_fp` に配線し auto_fixable への FP landing を塞ぐ（#357）** — #341 の self_analysis（`evolve_introspect._detect_fp_in_auto_fixable`）が「confidence=0.95 の `auto_fixable` に既知 FP（`extensionless_logical_path`, 対象 `data/bots/wheeling`）が landing している」と検出し続けていた盲点を、検出側でなく**生成側**で塞いだ。`data/bots/wheeling` は相対パスのため #339 の `logical_path`（絶対パス＋実 FS ルート除外）に掛からず、末尾セグメント `wheeling` が 8 文字あるため `short_field_name` にも掛からず、`stale_ref`(0.95) として無確認自動適用され得る位置に landing していた。`remediation/principles.py` の `_should_exclude_fp` 最終段に `known_fp_patterns.match_known_fp_in_issue` を**相対 subject 限定**で配線し、`FP_EXCLUSIONS` に `known_fp_pattern` を追加（14→15）。絶対パスは既存の tmp_path/logical_path と #339 実 FS ルート除外が専管するため対象外にした（カタログの `ssm_style_path` は `/Users` 等の実ルートも拾い #339 回帰ガードと衝突するため）。self_analysis 検出はこれにより 0 件へ収束し regression guard として残る。TDD（相対論理パス除外・汎用略語除外・classify で fp_excluded・拡張子付き正当参照は誤除外しない回帰ガード、新規4件 + 既存 #339 回帰ガード温存）。決定論・LLM 非依存。
- **fix(telemetry): hook が書く plugin-data dir を tool 実行時に取り逃し prune が全スキルを `zero_invocation` と誤判定する問題を修正（#358, [ADR-042](docs/decisions/042-hook-store-dir-resolver-not-datadir-unification.md)）** — 根本原因は `rl_common.DATA_DIR` の解決が実行コンテキストで分岐すること。hook（PostToolUse）は CC が設定する `CLAUDE_PLUGIN_DATA` 配下（`~/.claude/plugins/data/rl-anything-rl-anything/`）に usage.jsonl / skill_activations.jsonl を書くが、standalone な tool/skill（prune・audit・discover）は env 未設定で fallback `~/.claude/rl-anything/` を読むため、live テレメトリ（usage 1846 / skill_activations 377）を取り逃し stale fallback（usage 168）を読んでいた。**DATA_DIR の一斉スイッチや 10GB+2.2GB DuckDB のマージは tool 系ストア（corrections/evolve-state/eval-sets は fallback が正準）を壊すため採らず**、hook-writer 系ストアの **読み取り経路のみ** を正準化する最小修正を採用。新規 `scripts/lib/rl_common/store_paths.py` の `hook_store_path(filename, base=None)` が「明示 base 尊重（hook 凍結 DATA_DIR / テスト patch）→ env → install レイアウト探索 → fallback」の順で hook の書いた dir を決定論で解決する。env より明示 base を優先することで conftest の `CLAUDE_PLUGIN_DATA=tmp_path` 強制下でも個別テストの `audit.DATA_DIR` patch を壊さない。配線は usage/skill_activations の reader default のみ（`audit/usage.py` / `skill_usage_stats.py` / `discover/patterns.py` / `telemetry_query/usage_errors.py`）。実環境スモークで fallback 168→plugin-data 1846 を実証。全体一元化は Phase 2（別 issue）。TDD（resolver 9件 + #358 統合リグレッション2件）。決定論・LLM 非依存。
- **fix(hardcoded_detector): ドキュメント本文・例示コマンド中の URL/ARN を過剰検出する doc 文脈未除外を是正（#359, [ADR-043](docs/decisions/043-hardcoded-doc-context-suppression.md)）** — evolve の `hardcoded_value` 検出が SKILL.md の手順説明（`1. https://api.slack.com/apps にアクセス`）や例示 curl/aws コマンド中の URL・ARN を「抽出すべき設定値」として proposable に挙げ、高 confidence の `service_url`(0.55)/`aws_arn`(0.75) が上位を占めて本来の設定値ハードコードを埋没させていた（sys-bots 実 evolve で proposable 9件中の大半）。**A: allowlist 拡張** — `_OFFICIAL_API_URL_RE` に `api.slack.com/`（開発者ポータル）と `slack.com/oauth/`（OAuth authorize）を追加（公開・非秘匿エンドポイント限定。個別パス列挙はモグラ叩きになるため doc 文脈側と役割分離）。**B: doc 文脈抑制** — `_is_doc_prose_context`（手順番号行 `^\s*\d+\.` ＋ 例示コマンド行 `$`/`>` プロンプト・`curl`/`wget`・`aws <subcommand>`）に該当する行の `service_url`/`aws_arn` を抑制。bullet・非代入判定は採らず、手順番号/例示コマンド行が `key: value` 代入と構文的に交わらないことで `resource: arn:...` 等の設定値検出を構造的に維持（precision 優先＝高 confidence 系のみに文脈フィルタ、`api_key` の本物 token と低 confidence `numeric_id` には適用しない）。実 SKILL.md 模写で doc URL/例示 ARN が除外され webhook secret・設定 ARN は検出維持を実証。TDD（allowlist 2件・doc 文脈抑制 3件・代入文脈の回帰維持 2件、新規7件）。決定論・LLM 非依存。

## [1.89.1] - 2026-06-05

### Fixed
- **fix(evolve-skill): `apply_evolve_proposal` が既存 `references/pitfalls.md` を無条件上書きするデータ損失バグを修正（#350）** — apply フローが空テンプレを `write_text` で無条件書込していたため、実エントリを持つスキルに標準 apply をかけると蓄積した pitfall が全消去された（docs-platform の `check-handbook-drift` で実3件を踏みかけた）。`if not pitfalls_path.exists():` の存在ガードを追加し、既存ファイルがある場合は SKILL.md 追記のみで pitfalls.md は一切触らない。`evolve-skill/SKILL.md` Step 5 にも「既存 pitfalls.md があれば上書きしない」安全分岐を明記（手順に忠実な AI ほど消す問題を文書側でも封鎖）。TDD（既存エントリ温存 E2E・新規作成正常系）。
- **fix(prune): `zero_invocation` が本番オンデマンドスキルで構造的に常時誤発火する問題を緩和（#351）** — invocation_count に実起動を流す供給源がリポ内に存在しないため、CLAUDE.md に登録された本番スラッシュスキルが毎回「zero_invocation・要確認」に並んでいた（docs-platform で11本が誤発火）。`detect_zero_invocations` に `project_dir` 引数を追加し、対象 PJ の CLAUDE.md Skills セクション登録済みスキルを候補から除外する。パーサは #295 修正済の `skill_triggers.extract_skill_triggers`（リスト/テーブル/太字ラベルの3記法対応）を再利用。実 docs-platform で **誤発火 11→4 件**（登録7本を除外、未登録4本は正しく残存）を実証。TDD（3記法での除外・未登録は残存・project_dir なしは後方互換、新規7件）。
- **fix(hardcoded_detector): 正規 API URL を誤検知する「検出が価値と逆」を是正（#352）** — Slack 公式 API（`slack.com/api/`）や `*.amazonaws.com/`（region 込み多段ラベル含む）の参照 URL が `service_url`（confidence 0.55）で FP 検出されていた。`_OFFICIAL_API_URL_RE` を `_is_safe_url` に追加して許可リスト化（Slack webhook `hooks.slack.com/services/` は秘匿対象のため除外しない）。実 docs-platform で S3/region付きSQS/Slack API が FP 除外され webhook のみ検出されることを実証。TDD（公式API除外・region付き除外・webhook 残存ほか）。
- **fix(evolve): レポート運用のノイズ/UX 束を解消（#353）** — ⑥ AskUserQuestion の options 最大4制約に合わせ提案提示プロトコルを修正（5件以上は分割/誘導を明記）。⑨ `reason_refs` を correction 非由来の通常 evolve では非表示にし常時 ✘ ノイズを除去。⑩ `memory_heavy_update` を更新回数単独でなく行数との複合条件（`update_count>=3 AND line_count>=30`）に変更し、活発に正しく更新した小さなメモリの誤検知を解消。⑪ `proposable_custom` の二重持ち（`classified` 側 null と `phases.remediation` 側 count の食い違い）を解消し `classified` にリストを補完。⑫ glossary jargon 候補から AWS/インフラ汎用略語30語（ARN/CDK/SNS/SQS/S3/IAM/VPC/EC2 等）を denylist 除外。実 docs-platform CLAUDE.md で AWS/CDK/KMS/SNS が漏れなく除外されることを実証。
- **fix(skill_evolve): `judgment_complexity` の静的近似を導入し再現性と指標妥当性を改善（#354⑧）** — ADR-037 で LLM-free 化した judgment_complexity が assistant の主観採点で再現性を欠いていた問題に対し、3軸の静的指標（条件分岐語 / 番号付きリスト手順 / `AskUserQuestion` 出現数）で 1-3 を決定論近似する。番号付きリストは markdown 見出し番号（`### 1.`）を除外し `STEPS_SIGNAL_CAP=5` で頭打ち（長い線形チェックリストの complexity=3 張り付き防止）、`AskUserQuestion` は `ASK_USER_WEIGHT=2` で重み付け（判断委譲の主信号）。これにより steps 単独では 3 に到達せず、高複雑度判定は実際の分岐・判断委譲が駆動する。実 SKILL.md 21件で分布 {1:4,2:8,3:9}・docs-platform 固有11件で {1:4,2:4,3:3} の健全分布を実証。TDD（見出し除外・cap・ask 重み・配線）。
- **fix(evolve-fitness): 永久 `insufficient_data`（0/30 固定）の誤解を防ぐ構造的理由を併記（#354⑦）** — 「skill_evolve 提案は採点対象外」のためスキル中心 PJ では accept/reject 母集団が貯まらず 0/30 のまま固定される問題に対し、`insufficient_data` レスポンスに `structural_reason="skill_evolve_not_scored"` と理由メッセージを追加。採点対象拡大は副作用が大きいため表示改善に留める。`evolve-fitness`/`evolve` SKILL.md に表示手順を追記。TDD（理由併記・十分時は通常評価）。

## [1.89.0] - 2026-06-05

### Removed
- **chore(handover): `handover` スキルを廃止し checkpoint 機構へ統合（[ADR-040](docs/decisions/040-retire-handover-skill-into-checkpoint.md)）** — 手動でセッション引き継ぎノート（`.claude/handovers/*.md`）を書き出す `handover` スキルが運用実態として使われなくなっていたため廃止。理由は同じ `restore_state.py`（SessionStart hook）の **checkpoint 復元機構が作業文脈（git_branch / recent_commits / uncommitted_files / evolve_state）を SessionStart で自動復元する**ようになり、手動ノートの動機が吸収されたこと。残る「人が読む引き継ぎ文」用途も `/compact`（同一セッション継続）+ checkpoint（セッション跨ぎ自動復元）でほぼ代替できていた。削除対象: `skills/handover/`（SKILL.md / scripts / tests）・`bin/rl-handover`・`restore_state.py` の handover 依存（`_detect_handover` / `_extract_section` / handover.py import / 関連定数）。**checkpoint 復元・work_context サマリ・session title 生成は温存**（このコアは handover に非依存だったため無傷）。`ctx_guard.py` の context 逼迫警告から「/handover で引き継ぎ」案内を削除し「作業文脈は checkpoint が自動復元」へ置換。ドキュメント（README(.ja).md / SPEC.md / spec/api.md / spec/architecture.md / rl-anything-advisor.md）から handover 行を除去。公開コマンド `/rl-anything:handover` が消えるため MINOR bump 相当。決定論・LLM 非依存。

## [1.88.1] - 2026-06-05

### Fixed
- **fix(evolve): 検出フェーズの誤検知4種を前処理フィルタで除去（_archived / Slack ID / 汎用略語 / トークン見積もり）（#337）** — sys-bots の `/rl-anything:evolve` で remediation/discover が約80%ノイズになっていた共通根「アーカイブ除外・doc文脈ID除外・ストップリスト・truncate見積もり」の欠落を一括修正。**(1) `_archived/` 混入**: `EXCLUDED_SKILL_DIRS`（`audit/_constants.py`、find_artifacts 経由で skill_evolve / hardcoded scanning / 重複検出が共有）に `_archived` / `disabled` を追加し、独自列挙の `effort_detector.detect_missing_effort_frontmatter` にも `is_excluded_skill_path` フィルタを配線。アーカイブ済みスキルへの effort 付与提案（missing_effort 5件全滅）を解消。**(2) Slack ID 誤検知**: `hardcoded_detector` の `slack_id` パターンが doc 文脈の channel ID（`C0...`）/ App ID（`A0...`）を秘匿値として 41件フラグしていた。`_is_slack_doc_id`（`^(?:C0|A0)[A-Z0-9]{8,}$`）を `_should_exclude` に追加して除外（bot token `xoxb-` 等の api_key 検出は不変）。**(3) glossary ストップリスト弱**: `glossary_drift.DEFAULT_STOPLIST` に英大文字ストップワード（ALWAYS/FIRST/INFO/CUSTOM/DIR/WARN/ERROR/DEBUG/ENV/TMP/SRC/DST/MAX/MIN）+ サイズ単位（MB/KB/GB/TB/MD）を追加し、`find_undefined_terms` に Slack ID 除外正規表現（`_SLACK_ID_RE`）を配線。未登録 jargon 56件中45件のノイズを除去。**(4) batch_guard トークン見積もり約50倍過大**: 固定 `_TOKENS_PER_SKILL = 47_000`（全文×全スキル想定）を撤廃し、`_estimate_skill_tokens`（実 Phase B プロンプトの truncate 上限=SKILL.md 先頭2000字 + scaffold をトークン換算）で算出。19スキル893k → truncate ベースの実コスト相当に是正。TDD（_archived/disabled 収集除外・effort アーカイブ除外・Slack doc ID 除外×3・glossary ストップワード/Slack ID 除外/本物 jargon 残存・truncate 見積もり×3、新規14件 + 既存テストの新契約反映）。決定論・LLM 非依存。
- **fix(evolve): `rl-evolve` の stdout 純化（診断を stderr 分離）＋ `emit_customize_request` の Path/str 契約統一（#336）** — sys-bots で `/rl-anything:evolve` をフル実行した際の出力品質バグ2件を修正。**(1) stdout 汚染**: `run_evolve` がデータ未取得/不足時に `print("テレメトリ未取得: ...")` 等を **stdout** へ出していたため、stdout が純粋 JSON でなくなり利用側の `json.loads` が先頭の非 JSON 行で失敗していた（防御的に `JSONDecoder().raw_decode` でスキャンする負担を強いていた）。この4行を新規 `_warn_insufficient_data(sufficiency)` ヘルパに抽出し全て `file=sys.stderr` へ分離（chaos skip / scipy RuntimeWarning は元から stderr）。stdout は result JSON 専用の契約に統一。**(2) Path/str 型不整合**: `emit_customize_request(name, skill_dir)` / `ingest_customized_proposal(...)` に `skill_dir` を `str` で渡すと `proposal.py` の `skill_dir / "SKILL.md"` が `TypeError: unsupported operand type(s) for /: 'str' and 'str'` で落ちていた（`assess_single_skill` は str を受け入れるのに emit/ingest は Path 前提という契約不整合）。両関数の入口で `skill_dir = Path(skill_dir)` 正規化し str/Path どちらでも動くよう統一。TDD（診断が stderr に出て stdout は空・診断があっても main stdout は純粋 JSON・emit/ingest が str dir を受理、新規4件）。決定論・LLM 非依存。
- **fix(evolve-introspect): self_analysis が stderr 警告と auto_fixable への FP landing を検出できない盲点を解消（#341）** — Step 11 の self_analysis（evolve 自身の歪みを振り返るメタ機構）が、実際に起きた2つの歪みを見逃していた。(1) `runtime_errors` が **phase が throw した例外しか見ておらず**、scipy の RuntimeWarning(NaN, #340) のような stderr 警告を拾えず「例外なし ✅」と誤報告。(2) `self_detection` が **「高 confidence バケットに FP が入る」パターンを検出条件に持たず**、`auto_fixable`(confidence 0.95) に既知 FP（SSM 風パス・/tmp パス、#339）が landing しても「矛盾提案なし ✅」。フルオート運用で最も危険な「FP の自動適用」を self_analysis がガードできていなかった。修正: ① `evolve.py` に `_capture_warnings` context manager（`warnings.catch_warnings(record=True)`）を追加し reorganize フェーズの警告を `result["warnings"]` に root-cause 記録、`evolve_introspect._detect_captured_warnings` が dict/str 両形式を受けて root cause 単位（`_error_signature`）で dedup し `runtime_warning:` 候補を surface。② 新規自己完結モジュール `scripts/lib/known_fp_patterns.py`（決定論・純関数・FP パターンカタログ: ssm_style_path / tmp_path / archive_path / extensionless_logical_path / generic_abbreviation）を追加し、`evolve_introspect._detect_fp_in_auto_fixable` が confidence>=0.9 の auto_fixable issue を照合して `self:fp_in_auto_fixable:` 候補を起票。0件時は従来どおり「✓ 評価したが該当なし」を残す（silence≠evaluated）。FP カタログは #337/#339 と概念を共有し将来 remediation からも参照できるよう独立モジュール化（本 PR 単独でマージ可能）。TDD（known_fp 12件 + introspect の警告/FP-landing 軸 9件 + warning-capture 配線 4件）。決定論・LLM 非依存。
- **fix(remediation): `stale_ref` が SSM/tmp パスを confidence 0.95 で `auto_fixable` に分類する安全性バグを修正（#339）** — `/tmp/ab_test.py`（一時ファイルの歴史的引用）や `/docs-platform/strategy`（SSM パラメータの論理パス）のような「ファイル参照ではないパス」が stale_ref として confidence 0.95 → `auto_fixable` バケットに入り、フルオート evolve（非 dry-run + 一括修正）で memory ファイルから誤って削除されるデータ汚染リスクがあった。`_should_exclude_fp`（`scripts/lib/remediation/principles.py`、auto_fixable 判定の前段ゲート）に **`tmp_path`**（`/tmp/`・`/var/tmp/`・`/private/tmp/`・`/var/folders/` 配下）と **`logical_path`**（絶対・全セグメント拡張子なし・実ファイルシステムルート（`/Users`・`/home`・`/var` 等）配下でない論理パス）の 2 除外パターンを追加。これらは `external_url`/`archive_path` と同様 `fp_excluded`（confidence 0.0）として候補から外し auto-fix に到達させない。`/Users/.../foo.py` 等の正当な絶対ファイル参照は除外しない（実ルート先頭セグメント判定 + 拡張子判定で回帰ガード）。TDD（tmp/private/var-folders・SSM 論理パス・classify 統合・正当ファイル参照の非除外 回帰ガード 計11件追加）。決定論・LLM 非依存。
- **fix(reorganize): TF-IDF cosine のゼロノルムベクトル由来 NaN を根本除去（#340）** — evolve の reorganize フェーズで scipy が `RuntimeWarning: invalid value encountered in scalar divide`（`dist = 1.0 - uv / sqrt(uu*vv)`）を出し、退化スキル（stop word のみ等で TF-IDF が全ゼロになる文書）のゼロノルムベクトルが cosine 距離計算に渡って 0 除算 → NaN がクラスタリング距離行列に混入し hierarchy/split 結果を歪めていた。`warnings.filterwarnings` で握り潰さず根本原因（ゼロベクトル）を計算前に除去する方針で二重防御: ① `similarity.py` に `cosine_similarity_safe(vec_a, vec_b)` を追加し `uu==0 or vv==0` を numpy で先回りガード（類似度 0.0 = 距離 1.0 にフォールバック）、`compute_pairwise_similarity` / `filter_merge_group_pairs` の scipy `cosine` 直呼びを置換 ② `reorganize.cluster_skills` の `pdist(metric='cosine')` 経路でゼロノルム行を検出してダミー方向へ退避＋ゼロノルムが絡む距離を最大距離 1.0 に固定し、残存 NaN も `nan_to_num(nan=1.0)` で潰してから `linkage`。非ゼロベクトルの類似度計算は数式不変（回帰なし）、クラスタリングは決定論的。TDD（`cosine_similarity_safe` のゼロノルム/両ゼロ/決定論、退化文書混入時の RuntimeWarning 不在・NaN 不在を `warnings.catch_warnings`/`numpy.isnan` で assert、cluster_skills の NaN/警告不在・決定論・正常系不変の新規12件）。決定論・LLM 非依存。

### Changed
- **chore(test): `test_skill_evolve.py`（1527行）をテーマ別4ファイルに分割（ボーイスカウトルール）** — 肥大化していた単一テストファイルを責務ごとに分割し可読性・編集衝突耐性を改善。`test_skill_evolve.py`（コア: scoring/classify/anti-pattern/assess_single_skill/verification/workflow、30件）/ `test_skill_evolve_proposal.py`（proposal 生成・apply・diff・customization、23件）/ `test_skill_evolve_remediation.py`（remediation データフロー統合・rejected_stats、9件）/ `test_skill_evolve_batch_guard.py`（denylist・batch guard・judgment 2相、19件）の4本へ。全81件のテストは内容不変で移設のみ（collect 数 81 一致を検証）、各ファイル500行未満。既存 `test_skill_evolve_batch_estimate.py`（#337 分割済み3件）と合わせ skill_evolve テスト群を5ファイル構成に整理。挙動変更なし。

## [1.88.0] - 2026-06-05

### Added
- **feat(subagent-guard): SubagentStop 警告を累積カウントから時間窓ベースへ変更** — `subagent_observe.py`（SubagentStop hook）の閾値超過警告を、セッション開始からの **累積** subagent 数から **直近 `subagent_window_minutes`（既定5分）以内の同一セッション subagent 生成数** に変更。累積方式は長時間の正常セッションでも閾値に達して誤検知し続け、本来狙っている「短時間バーストの暴走ループ/カスケード」だけを捕捉できていなかった。`_count_session_subagents` を `_count_recent_session_subagents` に置き換え、各記録の `timestamp` を `now - window` で window フィルタ（パース不能・欠落 timestamp は窓外扱いで誤検知を防ぐ保守側）。新規 userConfig `subagent_window_minutes`（既定5）を `plugin.json` / `marketplace.json` に追加して時間窓を可変化（`CLAUDE_PLUGIN_OPTION_subagent_window_minutes` で上書き、長め設定で従来寄りの累積的挙動にも倒せる）。警告文面（systemMessage / additionalContext）も「直近N分で」を明示するよう更新。スコープは従来どおり同一セッション内。決定論・LLM 非依存。TDD（window 外は非警告 / window 拡大で警告 / config default+override の新規4件 + 既存テストを recent timestamp へ修正、hooks 487 緑、`claude plugin validate` 緑、API surface snapshot 再生成）。

### Fixed
- **fix(evolve): `rl-evolve` の巨大 result JSON を `--output` でファイル化し stdout 一発出力による途中切断を解消** — evolve 実行中に「head -200 で切れて JSON が不完全でした。全量をファイルに保存し直します」のやり直しが多発していた。根本原因は `evolve.py:main` が result dict 全体を `print(json.dumps(..., indent=2))` で stdout に吐く一方、evolve SKILL.md は以降 15 ステップ以上でこの**単一の巨大 JSON（フェーズ全部入りで数十〜数百 KB）**を読ませる設計なのに、ファイルへリダイレクトする指示が無かったこと。Claude が Bash で実行すると Bash 出力上限で末尾が切られるか、巨大化を見越して `| head -200` を挟むかのどちらかで `indent=2` の JSON が構造の途中で切れ invalid 化 → ファイル保存にフォールバックするやり直しが毎回発生していた（Claude のミスではなく出力契約と SKILL.md のミスマッチ）。`evolve.py` に **`--output <path>`** を追加し、指定時は full JSON をそのパスへ書き、stdout には `{"output": <path>, "phases": [...], "env_tier": ...}` の **1行サマリ**だけを出す（`_summarize_result`。`phases` は `result["phases"]` 配下の実フェーズ名、env_score は result に存在しないため top-level の `env_tier` を surface）。未指定時は従来通り full JSON を stdout に出す（後方互換）。evolve SKILL.md Step 1 と Step 7 の `--confirmed-batch` 再実行を `--output /tmp/rl_evolve_out.json` 必須に変更し、「evolve.py の出力に含まれる X フェーズを確認する」全箇所を**「`/tmp/rl_evolve_out.json` を Read で参照、`| head`/`| tail` 禁止」**に統一。TDD（`--output` で full JSON 書込・stdout は小さな1行サマリで full 混入なし・未指定は後方互換、新規3件）。決定論・LLM 非依存。

## [1.87.1] - 2026-06-05

### Fixed
- **fix(audit): hook_drift の解消ガイダンス文言を「flow-chain.json は手動メンテ SoT」へ訂正（#319）** — ADR-036 / `hook_drift.py` / `sections_hook.py` の docstring と stale メッセージが「flow-chain.json は gstack の setup/upgrade で再生成される設計」を前提にしていたが、PR #315 マージ後の実環境ドッグフードで**前提が誤り**と判明。`~/.claude/skills/gstack/`（setup/bin 含む全体）を grep しても `flow-chain.json` 参照は**ゼロ**で、gstack はこのファイルを一切生成しない（手動メンテの SoT、`gstack_version` は手書きピン）。stale_pin の**検出は正しい**（ピンと実環境の乖離は事実）が、`gstack setup` を回しても解消しないため**解消ガイダンスが的外れ**だった。stale メッセージを「`gstack_version` を実環境 version に手で更新」へ、docstring 2 箇所を「手動メンテ SoT・gstack は生成しない」へ訂正し、ADR-036 に `## Update（#319）` で前提崩れの経緯と教訓（`learning_synthetic_fixture_false_confidence` — 合成 fixture は検出までしかテストできず、直し方の前提は本番データでしか炙り出せない）を追記。文言修正のみでロジック・テスト不変（既存テストは ⚠/✓/version のみ assert し「再生成」文言は assert していないため回帰なし）。決定論・LLM 非依存。

## [1.87.0] - 2026-06-05

### Added
- **feat(agent-brushup): エージェント編成ギャップ（役割重複・孤立）を決定論検出し evolve で surface（#326）** — `agent_quality.py` は各エージェント *単体* の品質（frontmatter / トリガー / 行数）しか見ておらず、エージェント *間* の関係（役割が重なって呼び分けが曖昧／どの編成にも繋がらない宙ぶらりんの定義）は不可視だった。ai-daily-report 2026-06-02 の revfactory/harness（ドメイン→エージェントチーム自動設計）の着想を、フル自動生成（手動 LLM コマンド＝evolve では発火しない）でなく **決定論の「編成ギャップ検出」を recurring ループに配線**する形で取り込む。新規 `scripts/lib/agent_team.py` が 2 軸を検出: ① **役割重複**（`detect_role_overlaps` — description の役割語を Examples ブロック除去＋ストップワード除去で集合化し `similarity.jaccard_coefficient` を SoT に全ペア Jaccard、`ROLE_OVERLAP_THRESHOLD`=0.5 以上）② **孤立**（`detect_isolated` — 他エージェント定義本文への名前出現を参照とみなし、**入次数 0 かつ出次数 0** のみ孤立とする＝ルーター/オーケストレーター（出>0）と被参照の専門家（入>0）を除外し宙ぶらりんの定義だけ拾う）。observability builder `build_agent_team_section`（新規 `scripts/lib/audit/sections_agent.py`、sections.py が hard 行数バジェット 800 直前のため分離）を `_OBSERVABILITY_BUILDERS` に登録し、markdown/構造化の両経路が消費。audit を消費する evolve が **evolve のたびに自動 surface**（手動 CLI 止まりにしない＝version≠enforcement 回避）。エージェント 2 個未満は None（編成が成立しない PJ＝対象外）、2 個以上はギャップ無しでも「✓ 評価したが編成ギャップなし」を1行残す（silence≠evaluated、ADR-028）。整理は破壊的なので surface に留め適用は `/rl-anything:agent-brushup` 経由の人間判断。実機 E2E（~/.claude/agents/ 7個）で `design-review` / `doc-writer` の孤立を検出、役割重複は誤検出ゼロを確認。決定論・LLM 非依存。TDD（役割重複検出・Examples ノイズ無視・無関係ペア非検出・孤立の入出次数判定・ルーター除外・analyze の has_gap・builder の None/clean/flag、新規 10 件）。

## [1.86.0] - 2026-06-05

### Fixed
- **fix(audit/discover): 収集層の2大偽陽性を除去（別 PJ ドッグフードで発見）** — docs-platform で evolve を回したところ remediation の `total_issues=125` のうち **104件が phantom duplicate**、`skill_triage CREATE 5件が全て既存スキル**という偽陽性で本物の issue が埋もれていた。(A) `find_artifacts` / `detect_duplicates_simple` / `_is_plugin_managed_path` が `~/.claude/skills/.gstack-backup/<name>` を実スキルと 1:1 ペアで重複検出していた（除外判定が `"gstack"` 完全一致のみで `.gstack-backup` を素通り）。`audit/_constants.py` に `EXCLUDED_SKILL_DIRS={.archive,.gstack-backup}` + `is_excluded_skill_path` を集約し収集段階で除外。(B) `skill_extractor` が採掘する `<command-name>`（invoke 成功時のみ出る＝定義上すべて既存コマンド）から loop/model（CC builtin）・review（global）・rl-anything:*（plugin）を「新規作成せよ」と CREATE 提案していた。`discover/runner._is_already_existing_skill`（`:` namespaced / known_skills=project+global の SKILL.md 実在 / `_CC_BUILTIN_COMMANDS` denylist）で除外。実機 docs-platform で issues 125→16・duplicate 104→0・CREATE 5→0 を確認。決定論・LLM 非依存。

### Changed
- **feat(auto-memory): auto_memory_runner Stop hook の `claude -p` を全廃しファイルベース2相化（[ADR-037] Phase 2）** — Stop hook 終了時に `claude --print` で memory 候補を同期生成していた唯一の残存 claude -p サイト（`_call_llm`）を撤廃。hook は corrections の生成前ゲート（`memory_gating`、LLM 不要）だけを残し、生き残りを内容ハッシュ dedup して PJ スコープキュー `DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue するだけのゼロ LLM 化（.md 書込・belief ゲート・claude -p なし）。LLM 生成（subscription 課金のインライン）・生成後ゲート（`belief_entropy`）・memory 書き込み（`_write_entry_file`/index/importance/archive）は新規 `scripts/lib/auto_memory_broker.py` の2相（`compute_dedup_key` / `enqueue` / `read_queue` / `emit_memory_requests` / `ingest_memory_results` / `clear_queue_entries`）へ移設し、evolve SKILL.md **Step 6.5（auto-memory キュー drain）** が emit→assistant インライン→ingest で消化する。キュー dedup は `(session_id, timestamp)` 列の sha256 先頭16hex を key とする in-queue dedup（毎 Stop の同一 last-5 重複を防ぐ、cursor ファイル不要）。slug は memory dir と一致させるため `rl_common.project_name_from_dir(CLAUDE_PROJECT_DIR)`（git-common-dir 方式ではない）。ingest は空応答をキューに残し（次 drain で再試行）、stored/blocked を消化。回帰ゲート `CONVERTED_MODULES` に `hooks/auto_memory_runner.py` + `scripts/lib/auto_memory_broker.py` を追加し `KNOWN_REMAINING` を `score_noise`（DEPRECATED 後方互換）のみに削減。テストの subprocess mock を全廃し enqueue/2相経路へ書き換え（hook 13件 + broker 28件 + gate 緑、no-llm-in-tests と完全整合）。決定論・LLM 非依存。[ADR-037]
- **feat(remediation): fixers_rules / fixers_quality の `claude -p` を全廃しファイルベース2相化（[ADR-037] Phase 1d-ii）** — `fix_line_limit_violation`（非 rule 圧縮・rule 分離）と `fix_split_candidate`（スキル分割提案）の3つの claude -p サイトを撤廃。全て決定論フォールバック（proposable 降格 / fixed=False、または決定論 proposal_text で fixed=True）で完走するよう書き換え。LLM 品質の回復は新規 `scripts/lib/remediation/fixers_llm.py` の emit/ingest 6関数（`emit_compression_request / ingest_compression` / `emit_separation_request / ingest_separation` / `emit_split_request / ingest_split`）が担い、`llm_broker` の build_requests/parse_responses を活用する。IO は ingest のみ、emit は IO-free・LLM-free。evolve SKILL.md Step 5.5.1 に「proposable の line_limit_violation / split_candidate に対する2相品質回復」を追記。回帰ゲート `CONVERTED_MODULES` に3モジュールを追加し `KNOWN_REMAINING` を2件に削減（残: score_noise / auto_memory_runner）。決定論・LLM 非依存。[ADR-037]
- **feat(subagent-guard): SubagentStop の閾値警告を `hookSpecificOutput.additionalContext` で Claude に届け、subagent-guard.md を実エンフォース（CC v2.1.163、[ADR-038]）** — CC v2.1.163 で Stop/SubagentStop hook が `hookSpecificOutput.additionalContext`（Claude のコンテキストへ注入）を返せるようになった。`subagent_observe.py`(SubagentStop) は subagent 数が閾値超過時に警告を出していたが、出力が `systemMessage`（**user UI 向け**で Claude には届かない）のみだったため、グローバルルール subagent-guard.md の「閾値超過警告が出たら作業を一時停止してユーザーに現状説明」が**実際には Claude 側でエンフォースされていなかった**（install≠enforcement の再演）。閾値超過出力に `additionalContext`（subagent-guard.md の行動指示＝実行中の作業を一時停止しループ/カスケードでないか確認してユーザー説明を明記）を **systemMessage と併せて両方**出すよう変更し、user 可視性（暴走検知の安全シグナル）と Claude への行動指示を両立。**Stop（session_summary.py）の additionalContext は採用せず（HOLD）**: 「keep the turn going」セマンティクスが Auto Trigger の非介入方針（ユーザー確認を取る）と、ターン継続を強制するなら介入的・しないなら既存の next-session-start surface 以下、というどちらの解釈でも衝突するため、実測を待たず却下。決定論・LLM 非依存（出力は count/threshold からの固定文字列）。TDD（additionalContext 検証テスト追加、hooks 486 件緑）。[ADR-038]
- **feat(reflect): reflect 検出系（semantic_detector / critical_instruction_extractor）の `claude -p` を全廃しファイルベース2相へ移行（[ADR-037] Phase 1d-i）** — reflect の意味検証・指示違反判定に残っていた4つの claude -p 経路を撤廃。①`semantic_detector`: corrections バッチを is_learning/extracted_learning 判定する `semantic_analyze`（claude -p ドライバ）を**削除**し、`validate_corrections` を **LLM-free 化**（決定論フォールバック＝全件 is_learning=True）。`detect_contradictions` も LLM-free 化（[]）。2相 API `emit_validation_requests`（BATCH_SIZE=20 でバッチ化、offset/size を meta 保持）/ `ingest_validation_results`（index マッチで full/partial/欠損を統一処理）/ `emit_contradiction_request` / `ingest_contradictions`（pair バリデーション）を追加。②`critical_instruction_extractor`: `_call_llm_judge`（claude -p）を**削除**し、`rephrase_to_calm` を LLM-free 化（`(原文,0.0,"reject")`）、`detect_instruction_violation` を LLM-free 化（Stage1 対立動詞＋keyword_overlap fallback のみ、Stage2 LLM judge は削除＝LLM 失敗時の既存挙動と一致）。2相 API `emit_rephrase_request`/`ingest_rephrase`（confidence 閾値で auto/human_review/reject）と `emit_violation_judge_requests`/`ingest_violation_judges`（instruction 順に短絡再生、cap 15）を追加。両者とも `llm_broker`（build_requests/parse_responses）基盤を踏襲。呼び出し元 `reflect.py`（validate_corrections/detect_contradictions）・`discover/runner.py`（detect_instruction_violation）はシグネチャ温存で無改修、決定論バッチとして完走する。LLM 品質は reflect SKILL.md **Step 5.5（2相セマンティック検証）** が emit→assistant インライン→ingest で回復（手動 CLI 止まりにしない）。回帰ゲート `CONVERTED_MODULES` に2モジュール追加 + `KNOWN_REMAINING` を網羅化（auto_memory_runner〔Phase 2〕・fixers_rules/fixers_quality〔Phase 1d-ii〕を追記し「全 claude -p caller は CONVERTED か KNOWN のどちらかに必ず載る」不変条件を明文化）。テストの subprocess mock を全廃し2相・決定論経路へ書き換え（semantic 36 + critical/e2e 29 + gate = 緑、no-llm-in-tests と完全整合）。残: Phase 1d-ii〔fixers〕・Phase 2〔auto_memory〕。[ADR-037]
- **feat(skill_evolve): evolve 系（judgment 採点 / テンプレカスタマイズ）の `claude -p` を全廃しファイルベース2相へ移行（[ADR-037] Phase 1c）** — `llm_scoring._score_judgment_complexity_llm`（判断複雑さ 1-3 採点）と `proposal._customize_template`（テンプレをスキル文脈に整形）の `subprocess.run(["claude","--print","-p",...])` を撤廃。両者とも**既に決定論フォールバックを持っていた**ため、1a/1b と同型の cache-only decouple へ素直に寄せた。`compute_llm_scores` / `evolve_skill_proposal` は **LLM-free 化**（cache-read + 決定論フォールバック）し、evolve バッチ（evolve.py Phase 3.4）と run_loop は実行を中断して Task を呼べないため必ずフォールバックで完走する。LLM 品質の採点／整形は SKILL の2相（emit → assistant inline → ingest）が後追いで cache を更新する。`llm_scoring`: `build_judgment_prompt`（Phase A）/ `emit_judgment_requests`（static・欠落・hash 不一致のみ emit、refresh=全件）/ `ingest_judgment_scores`（Phase C）/ `_parse_judgment_response`（int/str/dict 寛容）/ `_score_judgment_complexity_static`（フォールバック）を追加。judgment は `judgment_source: "static"|"llm"` フラグで cache 保存し refresh 対象を区別（external_dependency は静的解析なので常に確定保存、旧 cache はフラグ無し→"static" 扱いで次 refresh に1回だけ LLM 昇格＝収束）。`proposal`: `_customize_template` を LLM-free フォールバック（テンプレそのまま）に、fence 除去 + diff budget gate（#196,#199）を `_parse_customization_response`（Phase C）へ集約、`emit_customize_request`（Phase A）/ `ingest_customized_proposal`（Phase C）を追加、proposal 組み立てを `_assemble_proposal` へ抽出して決定論経路と2相経路で共有。SKILL は skill_evolve の inline Python スタイルに合わせ「emit→prompt 提示→再 emit（決定論・冪等）+ ingest」の2ブロックで2相を駆動（evolve-skill SKILL.md Step 2/5、evolve SKILL.md Step 3.6 に judgment refresh を追記）。assessment.py の batch_guard は LLM-free 化後もバッチ規模の承認ゲートとして残置（LLM コストは Phase B refresh / apply へ移動）。回帰ゲート `CONVERTED_MODULES` に両モジュールを追加（`KNOWN_REMAINING` は `score_noise` のみに）。テストの subprocess/`_customize_template` mock を全廃し2相経路へ書き換え + 2相・パーサ寛容性テストを追加（skill_evolve 関連 108 件緑、no-llm-in-tests と完全整合）。[ADR-037]
- **feat(constitutional/principles): scoring 軸の `claude -p` を全廃しファイルベース2相へ移行（[ADR-037] Phase 1b）** — `principles._extract_via_llm` と `constitutional._evaluate_layer`（ともに `subprocess.run(["claude","-p",...,"--model","haiku"])`）を撤廃し、`llm_broker` の3相へ分離した。**依存順序あり**: constitutional のレイヤー評価プロンプトに principles が埋め込まれるため、SKILL は **principles round（1 call）→ constitutional round（最大4 call）** の順で回す（`emit_layer_requests` が `principles_missing` を返し順序違反を検知可能に）。`principles`: `build_extraction_request`（Phase A）/ `ingest_principles`（Phase C）/ `_parse_principles_response`（パーサ）を追加、`extract_principles` を LLM-free 化（cache hit→cache / cache miss・refresh→seed-only **非永続**）。`constitutional`: `_parse_layer_response`（パーサ）+ `emit_layer_requests`（Phase A）/ `ingest_layer_responses`（Phase C）を追加、集約を `_aggregate_constitutional` へ抽出し `compute_constitutional_score`（cache-only、全 miss→None）と共有、死蔵した `_estimate_cost`/Haiku 価格定数を削除。`environment.compute_environment_fitness` は cache-only read に（`skip_llm` 据置）。audit SKILL.md に **Step 3.5（Constitutional 再評価・2相）** を追加し evolve Step 3.7 から参照。回帰ゲート `CONVERTED_MODULES` に両モジュールを追加。テストの `subprocess.run` mock を全廃し2相経路へ書き換え（constitutional 19件・principles 23件緑、no-llm-in-tests と完全整合）。フルスイープ 3855 件緑。Phase B の応答パーサ（`_parse_layer_response`/`_parse_principles_response`）は assistant が parse 済み dict/list を書いても受理する（`world_context._extract_world_dict` と同じ trust boundary 寛容性。str 専用だと ingest がクラッシュ）。[ADR-037]
- **feat(quality-monitor): 品質評価の `claude -p` を全廃しファイルベース2相へ移行 + audit パイプラインを LLM 非依存に decouple（[ADR-037] Phase 1a）** — `evaluate_skill`（`subprocess.run(["claude","-p",...])` を高頻度スキルごとにループ呼び）を撤廃し、`llm_broker` の3相に分離した: **Phase A** `emit_rescore_requests()` が再スコア対象（needs_rescore 判定済み）と CoT プロンプト（`build_cot_prompt`）を `{"requests":[{"id","prompt","meta"}], "skipped":[...]}` で生成（LLM ゼロ）→ **Phase B** assistant が各 prompt を CoT 採点しインライン応答（`claude -p` なし＝subscription 課金）→ **Phase C** `ingest_responses(requests, responses)` が `_parse_cot_response` で集約し baselines 追記・劣化検知（LLM ゼロ）。**audit orchestrator から `run_quality_monitor()` のインライン LLM 呼び出しを削除**し、`run_audit` は既存 baselines を読むだけの決定論パイプラインに（`skip_rescore` は後方互換で受理するが LLM を起動しない）。再スコアは audit SKILL.md Step 3 が2相（`--emit-requests` → インライン採点 → `--ingest`）でオーケストレーションする。CLI を `--emit-requests` / `--ingest` / `--dry-run`（LLM・書き込みなし）に再編。テストの subprocess mock を全廃し決定論化（52件緑、no-llm-in-tests と完全整合）。[ADR-037]
- **feat(world-context): 世界観生成の `claude -p` を全廃しファイルベース2相へ移行（[ADR-037] Phase 1a）** — evolve の Step 0.5 で初回に走っていた `subprocess.run(["claude","-p",...])`（world-context.json 未生成時の LLM 生成）を撤廃し、`llm_broker` の3相に分離した: **Phase A** `--emit-request` が `build_world_prompt` で生成プロンプトを JSON 出力（LLM ゼロ）→ **Phase B** assistant が prompt を読み world JSON をインライン生成し `{"world": ...}` を responses に Write（`claude -p` なし＝subscription 課金）→ **Phase C** `--save-from-response` が `build_world_context_from_response` で ctx を組み立て保存（LLM ゼロ）。応答パースは dict / JSON 文字列 / ネスト dict / 欠損を吸収し DEFAULT_WORLD_CONTEXT へフォールバック。evolve SKILL.md Step 0.5 を3ステップ（--load → 初回のみ emit→[インライン生成]→save）に書き換え。`--generate` CLI と `generate_world_context`（claude -p 内蔵）を削除。テストは subprocess mock を全廃し決定論化（33件緑、no-llm-in-tests と完全整合）。[ADR-037]

### Added
- **feat(llm-broker): claude -p 全廃のファイルベース2相パターンを共通基盤 `llm_broker.py` に抽出（[ADR-037] Phase 1a）** — score_noise PoC で確立した「Python=決定論の前処理＋ゲート ／ LLM=assistant」の3相分離を、横展開可能な単一モジュールへ汎用化した。`build_requests(items, prompt_fn) -> [{"id","prompt","meta"}]`（Phase A: id 以外を meta に保持し集約に再利用）、`parse_responses(requests, responses, parser) -> {id: parsed}`（Phase C: requests を単一ソースに全 id 走査し欠損は parser fallback で穴埋め）、採点系用 `parse_score`（bool を数値扱いしない／regex 抽出）・生成系用 `passthrough`（素通し、欠損のみ fallback）の2パーサを提供。score_noise を本基盤に dogfood リファクタ（`parse_score`/`FALLBACK_SCORE` を broker から re-export、`build_scoring_requests`/`aggregate_from_responses` が broker を内部使用、後方互換のフラット形は維持）。broker は完全 IO-free・LLM-free のため **mock 不要**（no-llm-in-tests と完全整合）。TDD（broker 15件 + score_noise 22件 = 37件緑、regression なし）。残り経路（world_context/quality_monitor → 1b 以降の scoring/evolve/reflect）への横展開の土台。[ADR-037]
- **feat(score-noise): claude -p 全廃のファイルベース2相パターンを score_noise で PoC 実装（[ADR-037]）** — 2026-06-15 の Agent SDK クレジット分離（`claude -p` non-interactive = 別枠課金、Max 20x=$200/月）に対応するため、LLM 消費を `claude -p` subprocess から interactive な assistant 側（インライン/Task subagent = subscription 課金）へ移す設計を、最小経路 `score_noise` で実地検証した。LLM 呼び出しを Python の内部から外へ追い出す「Python=決定論の前処理＋ゲート ／ LLM=assistant」のファイルベース2相を確立: **Phase A** `build_scoring_requests(content, runs)` が採点リクエスト一覧を JSON 生成（LLM ゼロ）→ **Phase B** assistant が各 prompt を Task/インラインで採点（`claude -p` を呼ばない）→ **Phase C** `aggregate_from_responses(requests, responses, runs)` が集約してノイズ統計・epsilon を算出（LLM ゼロ）。`parse_score(raw)` を旧 `_run_claude_prompt` の regex から単独化し両経路で共有（id 形式も requests を単一ソース化）。CLI `--emit-requests` / `--aggregate` で Bash 境界越しに2相を接続。`_run_claude_prompt` は `bin/rl-prompt-compare` 後方互換のため DEPRECATED コメント付きで残置。新規 LLM-free 関数のテストは **mock 不要**（no-llm-in-tests と完全整合）。E2E で `claude -p` 呼び出し 0 回を実証（Phase A→B[インライン採点]→C、integrated 検算一致）。TDD（parse_score / build_scoring_requests / aggregate_from_responses の境界・欠損穴埋め・roundtrip、新規7件 + 既存20件 regression なし）。残り7経路（llm_scoring/proposal/world_context/constitutional/principles/fixers/critical_instruction）への横展開の骨格。[ADR-037]

## [1.85.0] - 2026-06-04

### Added
- **feat(audit): 他ツール追従 hook の陳腐化を stale_pin で検出し evolve のたびに surface** — `~/.claude/hooks/suggest-gstack-next-action.py` のような **gstack のフローを参照する hook** は、gstack 本体が進化（スキル追加・rename・フロー変更）すると静的参照が腐り古いアクションを提案し続ける。ユーザーの「hook が役立っているか・陳腐化していないか rl-anything で評価したい」を受け、`second-opinion` レビューで初期の汎用 hook_drift 案（dead_ref/internal_drift/stale_pin 一括）を YAGNI・false positive リスクと判定し、**表記ゆれの無い version 突合（stale_pin）に責務を限定**して着手。新規 `scripts/lib/hook_drift.py` が `~/.gstack/flow-chain.json` の `gstack_version` と実環境の `~/.gstack/.last-setup-version` を決定論で突合し、乖離を `HookDriftReport(applicable/stale_pin/minor_gap)` で返す。builder `build_hook_drift_section`（`scripts/lib/audit/sections_hook.py`、sections.py の行数バジェット回避で独立ファイル＝eval_saturation と同型）を observability contract の `_OBSERVABILITY_BUILDERS` に 1 行登録し、markdown / 構造化の両経路（[ADR-028]）へ自動伝播 — evolve のたびに追従漏れが可視化される。gstack はグローバル（~/.gstack）のため builder は project_dir 非依存（環境グローバル系）。silence≠evaluated: gstack 未導入は None で沈黙、一致時「drift なし ✓」、実 version 不明時「判定保留 ℹ」を残す。**実環境で本物の stale_pin を検出**（flow-chain.json 1.47.0.0 vs 実環境 1.55.0.0、MINOR 8 差）。併せて hook 本体（rl-anything 管理外のグローバル環境）を即修正: FALLBACK_CHAIN を SoT と整合（`ship → /land-and-deploy → /rl-anything:spec-keeper update`）、提案 block 発火時に `~/.gstack/analytics/hook-fires.jsonl` へ `{ts, skill, suggested_next}` を append し follow-through 計測（第2フェーズ）の種をまく。dead_ref / internal_drift / 有用性評価は別 issue。決定論・LLM 非依存。TDD（check_hook_drift 6 件 + builder 3 件 + contract 登録、新規 10 件）+ 実環境 E2E。[ADR-036]
- **feat(discover): skill_extractor の generalizability_score に軌跡有効性の実証基準を反映（#306）** — SIRI ① 成功軌跡採掘（`skill_extractor`）は generalizability_score を `cluster_size_score * success_rate / specialization_factor` で機械的に算定し、閾値 `TRAJECTORY_SKILL_SCORE_THRESHOLD`（0.25）でフィルタするだけで、**何が有効な軌跡か**の根拠が薄かった。"What Makes Interaction Trajectories Effective for Training Terminal Agents?"（arXiv:2606.03461）の実証基準のうち TrajectoryRecord（user_prompt / outcome / session_id）から決定論で観測できる3特徴を新規 `scripts/lib/skill_extractor/effectiveness.py` で抽出し、score 算定に乗算ブレンドする: ① **多様性**（distinct user_prompt 比 — 同一プロンプトの機械的反復は汎用性が低い）② **反復性**（distinct session への分散度 `(distinct-1)/(total-1)` — 1 セッション連投でなく複数の独立セッションに再発するほど恒常的ニーズ）③ **成功/失敗コントラスト**（success と failure 両方を含む軌跡は学習信号が豊富、論文の contrastive trajectory 知見）。3特徴を重み 0.4/0.4/0.2 で加重平均した `effectiveness`（0–1）を `effectiveness_multiplier`（MIN_MULTIPLIER 0.6〜1.0）に変換し既存スコアへ乗算。**後方互換**: records が乏しい/signal 不在時は multiplier=1.0（中立）で従来挙動を温存し、`use_effectiveness=False` で従来式に完全復帰可能。候補 dict に `effectiveness` フィールドを surface して算定根拠を可視化。`run_discover → evolve` に配線済（#291）なので score 式の改善が自動で効き、単調な軌跡（同一プロンプト・同一セッション連投）の候補が割り引かれ triage 通過候補の質が上がる。決定論・LLM 非依存。TDD（diversity/recurrence/contrast 各境界 + effectiveness 範囲 + 単調 vs 多様の大小 + multiplier 範囲 + score 統合 + 後方互換フラグ + candidate フィールド、新規 21 件）。
- **feat(evolve-search): SkillOpt「スキルをプログラムとして訓練」を多世代 evolve-search で近似（#305）** — Microsoft SkillOpt（daily report 2026-06-04）は手書き/LLM 一発生成のスキルが劣化しやすいと問題提起し、スキルを**勾配的に訓練**すべきと主張する。既存の `evolution_operators.evolve_generation`（#256 BES 前向き進化探索）は**単一世代**のみで「世代をまたぐ訓練の反復」が無く、#305 受け入れ条件「世代ごとに subgoal fitness が単調改善し収束世代数が減る」を観測できなかった。論文コードは未公開（調査ゲート）のため、既存 BES の枠内で近似する自前実装として進めた。新規 `evolve_search(candidates, fitness_fn, generations, offspring_count, patience, epsilon, rng)`（`scripts/lib/evolution_operators.py`）が `evolve_generation` をラップして**多世代**まわす: (1) `fitness_fn` を呼び出し側から注入（モジュール自身は LLM/subprocess を呼ばない＝決定論・no-llm-in-tests 維持）、(2) **エリート保存**（親集団＋子集団を fitness 降順で上位継承）により best fitness が世代をまたいで**単調非減少**＝勾配上昇の近似（受け入れ条件①を構造的に保証）、(3) **patience 世代連続で改善幅 < epsilon なら早期停止**（受け入れ条件②＝収束世代数を減らす）、(4) `best_fitness_history` / `generations_run` / `converged` を返り値に surface。配線先は rl-loop `--evolve-search`: `run_loop.py:_evolve_variants` を単一世代 `evolve_generation` から多世代 `evolve_search` に差し替え、**subgoal_scorer (#253) の total を勾配代理 fitness として注入**（決定論・LLM 非依存なので多世代まわしても LLM コストはゼロ円、最終勝者 1 候補のみ既存 3 軸スコアラーで採点）。論文準拠版が公開されたら `fitness_fn`／演算子を差し替えるだけで多世代ループ・エリート保存・早期停止の骨格を再利用できる移行パスを ADR に明記。TDD（evolve_search 単体 9 件: 単調非減少・決定論・早期停止・空入力・generations=0・fitness_fn 適用、+ rl-loop 配線テスト更新）。[ADR-035]
- **feat(reorganize): SkillPyramid — 低レベルスキル群を上位スキルへ束ねる階層的統合提案を追加（#303）** — 従来の reorganize（split 検出）/ prune（merge 提案）は**フラット**な統廃合のみで、スキルが増えると max_skill_count（既定30）に張り付くだけで「どれを消すか」の判断に追われていた。arXiv:2606.03692「SkillPyramid」の着想で「階層（低→上位）」軸を追加。`reorganize.detect_hierarchy_candidates(clusters, line_counts)` が既存の TF-IDF 階層クラスタリング結果を入力に取り、(1) メンバーが `MIN_HIERARCHY_CLUSTER_SIZE`（既定3）個以上、(2) 過半数が低レベル（SKILL.md が `HIERARCHY_LINE_CEILING`=150 行以下、大型中心のクラスタは split/merge 側の問題なので除外）のクラスタを「階層統合候補」として検出する。出力は `parent_skill_suggestion`（centroid キーワード由来の上位スキル名提案）/ `member_skills` / `member_count` / `centroid_keywords` / `reason="hierarchical_consolidation"`。`run_reorganize` の結果に `hierarchy_candidates` / `total_hierarchy_candidates` を追加し、`issue_schema.make_hierarchy_candidate_issue`（新規 `HIERARCHY_CANDIDATE` 型）で `issues` にも合流。evolve SKILL.md Step 4 に「階層統合提案（低レベル→上位）」surface を追加し evolve のたびに発火（手動 CLI 止まりにしない、`total_hierarchy_candidates: 0` でも「該当なし ✓」を残す＝silence≠evaluated）。統合は破壊的なので提案表示に留め適用は人間判断。決定論・LLM 非依存。TDD（検出 5 件 + issue 変換 + run_reorganize 配線、新規 8 件）。
- **feat(fitness): Skill-RM — スキル軸での異種評価基準統一（報酬モデル）（#304, arXiv:2606.03980）** — 現状の fitness は coherence/telemetry/constitutional/skill_quality の「軸別」重み統合で、スキルごとに異なる成功条件（異種基準）を単一スコアで横断比較できなかった。Skill-RM はこれと**直交**する「スキル別」評価を足す: スキルごとの異種成功条件を全スキル共通な3軸 — `structure`（SKILL.md の CSO 構造品質＝書き方の成功条件）/ `success`（invoke 直後 60s 以内に同セッションの correction が無い暗黙成功率＝使われ方の成功条件）/ `validity`（invoke あたりエラー率の補数＝動作の成功条件）— へ射影し、単一報酬で横断評価する。軸合成は新たに base 引数を取れるよう拡張した `environment._normalize_weights(axes, base_weights)` を**数式単一ソース**として再利用（`SKILL_RM_BASE_WEIGHTS` を渡し、算出できた軸のみで合計1.0へ再正規化＝environment の「利用可能な軸のみで正規化」原則をそのまま継承）。「軸別」overall には混ぜず、`compute_environment_fitness` の `result["skill_rm"]` に per-skill 報酬・分布（`mean_reward` / `reward_spread` σ / `worst_skill`）を surface し、`format_environment_report` が低 reward 順でレポート出力＋最低スキルを「calibration drift の帰属候補」として明示する（どのスキルが乖離源かを rl-scorer/evolve のたびに可視化）。対象スキル0件でも report に「評価したが対象スキルなし ✓」を1行残す（silence≠evaluated）。決定論・LLM 非依存、evolve/audit のたびに発火（`scripts/rl/fitness/skill_rm.py`）。TDD（共通軸の単一報酬・`_normalize_weights` SoT 共有・部分軸の再正規化・error→validity 低下・correction→success 低下・分散と worst_skill 帰属・レポート整形、新規 13 件）。
- **feat(triage): Triage Decision Ledger — SKIP 判断に TTL・再発カウンタを持たせ「定期見直し」を evolve ループに内蔵（#308）** — `meta_quality_check` の `low_reuse AND 重複候補あり → SKIP` はステートレスに毎回ゼロ判定しており、毎日 evolve を回すたびに同じ SKIP 候補がノイズとして surface され、「繰り返し検出される」シグナルも失われていた。新規 `scripts/lib/triage_ledger.py` が判断を `DATA_DIR/triage_decisions/<slug>.jsonl` の **PJ スコープ**に永続化（slug は `optimize_history_store` と同じ worktree 安全解決＝`git rev-parse --git-common-dir` 親 basename、ADR-031 / pitfall_worktree_slug_show_toplevel）。レコードは candidate_key 単位で recommendation / reuse_rate / duplicate_of / first_seen / last_seen / times_seen / times_skipped / decided_at / ttl_days / suppressed_until を保持し、last-write-wins append + `compact()` で肥大化を抑える。3層の見直しトリガーで evolve/discover の挙動を変える: ① **抑制**（SKIP 済み & クールダウン内 & 再発閾値未満 → 個別表示せず `skip_suppressed_summary` の「SKIP 抑制 N件 ✓」1行に畳む。0件でも残す＝silence≠evaluated、ADR-028 同思想）② **再発エスカレーション**（窓内 `times_skipped >= ESCALATE_N`=3 → SKIP→REVIEW 自動昇格）③ **TTL 切れ**（`now > decided_at + ttl_days`=45日 → 🔄 1回だけ強制再評価）。`skill_triage.triage_skill` の CREATE→meta SKIP パスに `apply_ledger` を配線し、evolve.py が `triage_all_skills` 経由で **evolve のたびに自動発火**（手動 CLI 止まりにしない＝install≠enforcement）。evolve SKILL.md Step 3.8 が `skip_suppressed_summary` を必ず1行 surface。決定論・LLM 非依存。TDD（ledger read-write / per-slug 分離 / 肥大化防止 / 3層トリガー + skill_triage E2E（連続 evolve 冪等性・再発昇格・TTL）、新規）+ 副作用隔離（autouse fixture で `LEDGER_ROOT` を tmp へ）。

### Fixed
- **fix(triage): Triage Decision Ledger が `evolve --dry-run` でも台帳を永続化していた副作用を修正（#308 follow-up）** — #308 で配線した `triage_ledger.apply_ledger` は `upsert_record` を**無条件**で呼んでおり（SKIP/passthrough/TTL/escalate の6経路すべて）、`evolve.py` の `--dry-run`（「レポートのみ・変更なし」契約）が `triage_all_skills` に dry_run を伝播していなかったため、**dry-run でも `DATA_DIR/triage_decisions/<slug>.jsonl` に書き込み**、TTL・再発カウンタ（times_skipped / decided_at）が dry-run のたびに更新される状態破壊が起きていた（docs-platform で dry-run 実走時に 5 レコード生成を実測）。root cause を表層 fix（呼び出し側で消す）でなく**書き込み層**で塞ぐ: `apply_ledger(..., persist: bool = True)` を追加し、`persist=False` 時は3層判定（抑制/再発エスカレーション/TTL 切れ）を**既存レコードから計算して返すが `upsert_record` は全経路でスキップ**（判定は load 済みレコードのみに依存し当該回の書き込み予定レコードには依存しないため、観測される decision は persist の真偽で一致）。`triage_skill(..., dry_run=False)` → `apply_ledger(persist=not dry_run)`、`triage_all_skills(..., dry_run=False)` → `triage_skill(dry_run=...)`、`evolve.py` の `triage_all_skills(...)` 呼び出しに `dry_run=dry_run` を配線し、最上位 `--dry-run` を書き込み層まで貫通させた。決定論・LLM 非依存。TDD（persist gate 単体6件: 非書き込み・判定一致・連続 dry-run 非昇格・passthrough・既存レコード不変 + skill_triage→台帳 E2E 3件: dry-run 非永続/既定は永続/連続 dry-run 非昇格、新規9件）+ 実 PJ（docs-platform）E2E で `--dry-run` 後に台帳ファイル・dir とも未生成かつ triage phase は正常 surface を実測。

## [1.84.1] - 2026-06-04

### Fixed
- **fix(evolve): split↔archive の矛盾提案を本流で reconcile し archive を優先（#301 #302）** — reorganize の split 候補（SKILL.md 300 行超）と prune の archive 候補（`zero_invocations` / `retirement_candidates` / `decay_candidates`）の間に相互排他チェックが無く、**大きくて未使用のスキル**が同一 evolve run で「分割せよ」（reorganize）と「淘汰せよ」（prune）を同時に受けていた（#301 `onboard-project` / #302 `project-setup`）。`evolve_introspect` の `_detect_split_archive_contradiction` がこの矛盾を検出して issue 起票はしていたが、root cause（相互排他チェックの欠如）が未修正のため検出器が毎 evolve で同じ矛盾を再報告し続けていた。新規 `reconcile_split_archive(result)`（`scripts/lib/evolve_introspect.py`）が evolve.py の prune フェーズ直後（**Phase 4.1**、self-analysis の前）に決定論で走り、archive 候補に一致するスキルを `reorganize.split_candidates`（および派生 `issues`）から除外する（**archive 優先**＝同じ run で消す対象に分割という延命投資をしない、未使用シグナルを尊重）。除外は silent にせず `reorganize.split_suppressed_by_archive` と `phases.split_archive_reconcile.suppressed` に記録し、evolve SKILL.md Step 4 が非空時に「分割候補から除外（archive 優先）: <skills>」を surface する（silence≠evaluated）。検出器（`_detect_split_archive_contradiction`）は reconcile を通らない経路の **regression guard** として残置し、archive 判定 constant `_PRUNE_ARCHIVE_KEYS` とヘルパー `_collect_archive_skills` / `_skill_name` を reconcile と共有して policy を単一ソース化（片方だけ判定がずれるのを防止）。決定論・LLM 非依存。TDD（archive 優先除外 / reconcile 後の矛盾消失 / 非 archive 維持 / skipped・archive 0 件の no-op / retirement・decay キー対応、新規 6 件）。[ADR-034]

## [1.84.0] - 2026-06-03

### Added
- **feat(evolve): evolve 実行後の自己解析 → バグ/改善点を検出して GitHub issue を半自動起票（#299）** — evolve は他フェーズで対象 PJ を改善するが、**evolve 自身の実行結果**（提案の質・実行時エラー・改善余地）を振り返る経路が無く、パイプラインのバグや改善余地は人間が気づいて手で issue を立てるまで構造に残らなかった（「install≠enforcement」と同型の配線漏れ＝自動で回るループに載らないものは育たない）。新規 `scripts/lib/evolve_introspect.py` が evolve の `result` dict 全体を決定論で読み、3カテゴリの GitHub issue 候補を生成する: ① **self_detection**（同一スキルへの split↔archive 同時提案の矛盾／line-limit 超過ファイルへの content 追加 fix で budget を悪化させる提案）② **runtime_errors**（各フェーズが `{"error": ...}` で握り潰して result が緑に見える例外／observability 取得失敗。`_error_signature` がパス・行番号・16進 ID を落として root cause 単位に正規化）③ **improvement_opportunities**（self_evolution の systematic_flags＝系統的に却下される提案 type／calibration regression）。`run_evolve` 末尾（全フェーズ集約後）が `result["self_analysis"]` に格納するため **evolve のたびに自動発火**（手動 CLI 止まりにしない）。3カテゴリとも検出 0 件でも `summary_line` に「✓ 評価したが該当なし」を残す（silence≠evaluated）。起票は**半自動**: evolve SKILL.md **Step 11** が候補を per-item 提示 → 人間が AskUserQuestion で個別承認 → 承認分のみ `gh issue create --repo todoroki-godai/rl-anything`（検出対象はパイプライン自身のバグであり evolve がどの PJ 上で動いても起票先は固定）。重複起票防止は body 埋め込みマーカー `<!-- rl-evolve-introspect:<dedup_key> -->`（root cause 単位の最強シグナル）→ タイトル類似度（SequenceMatcher、閾値 0.80）の二段 dedup（`filter_duplicates`）で、同一 root cause の毎 evolve 重複起票を防ぐ。observability builder は project_dir しか受け取れず result の error/矛盾/rollback を読めないため builder ではなく独立モジュールとして実装（ADR-028 の判断軸を踏襲しつつ別配線）。決定論・LLM 非依存（起票判断のみ人間）。TDD（検出3カテゴリ + dedup マーカー/類似度 + 構造契約 + 起票 body マーカー roundtrip、新規 19 件）+ 実 PJ（rl-anything 自身）E2E で `self_analysis` 全キー出力・clean 時 0 件を確認。[ADR-033]

## [1.83.2] - 2026-06-03

### Fixed
- **fix(skill_triggers): `- **ラベル**: `/skill`` 形式の CLAUDE.md Skills が読めず誤検知が多発する問題を修正（#295）** — `_parse_skills_section` のリスト行パーサ `^-\s+/?([a-zA-Z0-9_:-]+)\s*[:：]` は `- /skill:` / `- skill:` 形式しか拾えず、ハイフン直後が太字の非ASCIIラベルで skill 名がコロン後ろのバッククォート内にある形式（例: `- **AWSデプロイ**: \`/aws-deploy\` - \`.claude/skills/aws-deploy/SKILL.md\``）を **CLAUDE.md が存在するのに trigger 0 件**しか返さなかった。結果 `claudemd_skills` が空集合になり「CLAUDE.md 記載スキルは除外」ロジック（`detect_untagged_reference_candidates` / `detect_missed_skills` / `triage_all_skills`）が全滅し、ユーザー呼び出し型の実行スキルを `type: reference` 付与候補や missed として誤検出していた（当初 shadow 環境のパス解決問題と推定されたが、実体 project_dir 上で再現するパーサバグが真因）。`_extract_list_item_skill` を新設し、プレーン形式に加えて行内の最初のバッククォートコマンド `` `/skill-name` `` を skill 名として拾う（過剰捕捉は exclusion 集合を広げる＝誤検知を減らす安全側にしか効かない）。併せて (1) `resolve_claude_md_path` で CLAUDE.md を実体パス基準（直下→git ルート fallback）で解決、(2) `detect_missed_skills` のメッセージを「CLAUDE.md 不在」と「在るが trigger 抽出 0」で区別（ミスリード防止）、(3) audit は「CLAUDE.md は在るが trigger 抽出 0」のとき untagged_reference 検出を suppress しつつ件数を明示 surface（`claude_md_unparseable` ゲート、誤検出を confident に出さない）。実機 `sys-bots-main`/CLAUDE.md で before 0 件 → after 12 skills を確認。TDD（パーサ 3 形式 + resolver git fallback + skip/surface ゲート、新規 12 件）。[ADR-032]

## [1.83.1] - 2026-06-03

### Fixed
- **fix(optimize-history): accept/reject 履歴（fitness calibration 母集団）の split-brain を解消し DATA_DIR の PJ スコープに集約（ADR-031）** — `history.jsonl` の読み書きが 3 経路に分裂していた: optimize/evolve-diff は `<PLUGIN_ROOT>/skills/.../generations/history.jsonl`（バージョン更新で cache dir ごとリセット）、run_loop は `<cwd>/.rl-loop/history.jsonl`（readers が読まない孤立）、readers（fitness_evolution / discover / audit aggregate_runs）は plugin generations を読む。結果、(1) プラグイン更新で母集団が seed に戻り 30 件閾値に永久未到達、(2) rl-loop の accept/reject が calibration に届かない、(3) 永続 DATA_DIR にデータが無い、が複合。実測で全 31 ファイル（全バージョン cache + dev + 全 PJ の .rl-loop）の union がユニーク 9 件・有効 3 件だけ＝実は一度も累積していなかった。新規 `optimize_history_store.py`（`token_usage_store` と同 DATA_DIR パターン）に集約し保存先を `DATA_DIR/optimize_history/<slug>.jsonl` の **PJ スコープ**へ。slug は worktree 安全に `git rev-parse --git-common-dir` の親 basename で解決（`--show-toplevel` の basename は worktree 内で worktree 名を返し二次 split-brain を生むため使わない）、git 外は `_unattributed`。読み書き 6 箇所（fitness_evolution / discover.errors / optimize.{save_history_entry,record_human_decision} / run_loop / aggregate_runs）を store 経由に差し替え、未使用の `RL_LOOP_DIR`（split 残骸）を撤去。conftest の autouse 隔離に optimize_history_store を追加し real DATA_DIR 汚染を防止。これにより atlas-breeaders 等は「他 PJ 由来の 3/30」誤表示でなく「自前の 0/30」を正直に表示する（誤認の是正）。migration は実装せず新規スタート（救える有効 3 件は BOOTSTRAP_MIN=5 未満で calibration 起動不可・逆引き misrouting リスクのため、ADR-031 Decision 5）。TDD（store 単体 9 件 + slug 経路 + split-brain 回帰 E2E + 6 箇所差し替え回帰、影響 748 件緑）。[ADR-031]
- **fix(world_context): evolve ナレーションの世界観が PJ 間で汚染される問題を修正** — `world_context.py` は `DATA_DIR/world-context.json` の単一ファイルで世界観を保持していたが、`DATA_DIR` は `CLAUDE_PLUGIN_DATA` 未設定時に全 PJ 共通（`~/.claude/rl-anything/`）のため、先に evolve した別 PJ（例: docs-platform）の世界観が後続 PJ（例: atlas-breeders）の `--load` でヒットして流用されていた（`load_world_context` が `project_slug` を照合せずファイル存在のみで返していた）。保存先を `world-contexts/world-context-<slug>.json` の **PJ 別スコープ**に分離し、`load_world_context(data_dir, slug)` / `save_world_context(..., slug)`（slug 未指定時は `ctx["project_slug"]` から導出）に slug 引数を追加。slug 指定時はグローバルへフォールバックしない（汚染源を遮断）。`--load` CLI に `--slug` を追加し、evolve SKILL.md の Step 0.5（load/generate）と Step 1（env_score save）の両方で slug を渡すよう配線。slug は `[^A-Za-z0-9._-]` を `_` 置換でサニタイズ（traversal 防止）。既存グローバルファイルは `project_slug` 基準で per-slug パスへ一度だけ移行（継続性保持）。ナレーション専用のため主機能には影響なし。TDD（PJ 分離・サニタイズ・CLI 分離の回帰 11 件追加、計 27 件緑）+ 実 CLI E2E（proj-a 取得／proj-b は exit 1 で非汚染）で確認。

## [1.83.0] - 2026-06-03

### Added
- **feat(discover): SIRI ① skill_extractor を run_discover に配線 — 成功軌跡からのスキル採掘を発火（#291）** — #238 Phase 1 で実装済みだが「どの recurring ループからも呼ばれない」休眠状態だった成功軌跡採掘モジュール `skill_extractor`（SIRI / arXiv 2606.02355 の①採掘段階）を `run_discover` に接続した（参照は SPEC.md / spec/architecture.md / 自身のテストのみで、discover/evolve/audit/hooks のいずれからも呼ばれず＝install≠enforcement と同型の配線漏れ）。`extract_skill_candidates` を **project スコープ**（`_project_transcript_dir` で CC の transcript ディレクトリ命名規則 `/`・`.`→`-` にエンコードして当該 PJ の transcript のみを walk、cross-PJ noise 防止）で発火し、`generalizability_score >= TRAJECTORY_SKILL_SCORE_THRESHOLD`（既定 0.25、noise が増えたら引き上げる lever）でフィルタして `trajectory_skill_candidates` に surface しつつ、純粋ヘルパー `_trajectory_candidates_to_missed` で triage 互換の missed_skills 形式（`skill`/`session_count`/`triggers_matched`）へ変換し既存の `missed_skill_opportunities` 合流点へ接続する（新チャネルを作らず CREATE/UPDATE 判定 + `meta_quality_check` の noise フィルタに合流）。discover は evolve Phase 2.6 が消費する recurring ループなので evolve のたびに自動発火する（手動 CLI 止まりにしない）。決定論・LLM 非依存。当初グローバル採掘で実装したが project スコープ違反で既存テストが回帰 → project スコープへ修正（[ADR-030]）。TDD（純粋ヘルパー + run_discover surface + エンコード契約 + 例外処理、7件）+ discover API snapshot 更新 + 実 PJ E2E（`trajectory_skill_candidates` surface 確認）。[ADR-030]
- **feat(audit): trigger eval 飽和度を observability contract に surface（#292, TASTE）** — `trigger_eval_generator` は sessions.jsonl → evals.json の*順生成*のみで、生成した eval が「緑なのに頑健か飽和か」を判別する経路が無かった（negative_transfer / calibration drift はスキル追加の回帰を測るが eval 自体の飽和度は測らない）。arXiv 2605.28556「TASTE」（ツール呼び出し列から難問を逆生成し既存ベンチの飽和を暴く）の視点を持ち込み、`scripts/lib/eval_saturation.py` が forward-gen eval set の飽和兆候を **eval 実行なし・LLM 非依存・決定論**で測る: `low_negative_coverage`（should_trigger=False 比率が `MIN_NEGATIVE_RATIO`=0.3 未満で positive 偏重＝trivially green）/ `easy_negatives`（negative 中の trigger 語マッチ比率が `MIN_NEAR_MISS_RATIO`=0.3 未満で over-trigger 境界を突かない、trigger 語が取れる skill のみ）/ `thin`（クエリ総数 < `MIN_QUERY_COUNT`=6 で識別力不足）。trigger 語が取れない skill では easy_negatives をスキップ（graceful degrade）。`build_eval_saturation_section`（`scripts/lib/audit/sections_eval.py`、sections.py が hard 行数 800 に達したため分離）を `_OBSERVABILITY_BUILDERS` の calibration_drift 直後に登録し、markdown 経路（report.generate_report）と構造化経路（collect_observability → evolve Step 3.8）の両方に自動伝播 — evolve のたびに「緑の eval セットが信頼できるか」が calibration drift と同セクション帯で surface される（手動確認に依存しない配線）。eval-sets 未生成の環境＝対象外(None)／飽和なし＝✓／飽和あり＝⚠ で対象スキルと理由を出し分け（silence≠evaluated）。eval-sets は環境グローバル（DATA_DIR 配下）で trigger 語は PJ 依存のため、calibration_drift と同じ環境グローバル系 builder。新規テスト 15 件。実機 eval-sets 8 件で E2E（現状飽和なし＝✓）を確認。

## [1.82.0] - 2026-06-02

### Added
- **feat(audit): negative transfer を更新コンポーネント別 ablation に拡張＋observability contract に配線（#288）** — 従来の `compute_negative_transfer` は「最初の追加スキル1点」を転移点とし after をデータ終端まで取るため、複数の更新が混ざって「どの更新が既存スキルの成功率を下げたか」を分離できなかった（ある時点で何かが起きた、までしか言えない）。arXiv 2605.30621「Harness Updating Is Not Harness Benefit」の ablation 視点で `compute_component_transfer` を新設し、各追加スキルを1つの更新コンポーネントとみなして隣接する追加イベントで before/after を区切る **isolation window**（after_i = before_{i+1}）で各コンポーネントの寄与を分離・帰属する。これにより「更新 i+1 で起きた回帰を更新 i に誤帰属しない」ことを保証（回帰ガードあり）。surface 経路を report 直書きから observability contract（`build_negative_transfer_section` を `_OBSERVABILITY_BUILDERS` に登録）へ載せ替え、evolve は audit を消費するので evolve のたびに markdown/構造化の両経路で surface される（手動確認に依存しない配線）。テレメトリ未蓄積＝対象外(None)／レコードありだが算出不可＝「算出対象なし」ℹ／回帰なし＝✓／回帰あり＝⚠ でコンポーネントと影響スキルを出し分け（silence≠evaluated）。決定論・LLM 非依存。回帰テスト 19 件 + 実 PJ 全 PJ 横断テレメトリ（138 件）でドッグフード（現状 outcome 蓄積がスパースで算出対象 0＝旧関数と同じ前提依存を確認）。[ADR-028]

## [1.81.0] - 2026-06-02

### Added
- **feat(auto_memory): belief_entropy 生成後ゲート — 低信頼 memory 要約を書込前に破棄（#285）** — auto_memory_runner が Stop hook で生成する memory 要約が、元 corrections の情報を保持(retention)せず・ソース非接地の主張を過剰に含む(drift)場合に、書込前に破棄する決定論ゲートを新設。`scripts/lib/belief_entropy.py` が `retention = |src∩sum|/|src|` / `drift = |sum\src|/|sum|` を `similarity` のトークン集合演算で近似評価し、`retention < 0.25` または `drift > 0.85` で `should_store=False`（hot-hook 原則に沿い LLM ゼロ）。粗いトークン化（日本語等）で信号が乏しい場合は `low_signal` で安全側（ブロックしない）に倒す。要約は frontmatter を剥がして body のみ評価（構造トークンによる drift 過大評価を回避）。ブロックは `belief_blocks.jsonl` に記録し、`build_belief_blocks_section` が audit/evolve の observability contract（`_OBSERVABILITY_BUILDERS`）で surface（gate 未稼働の PJ は対象外で None、稼働済みで直近 block 0 件でも ✓ を1行＝silence≠evaluated）。Belief Entropy 論文（arXiv:2605.30159）の厳密な不確実性推定でなく、それに着想を得た決定論プロキシ。docs-platform 実機で「忠実要約=保存／無関係要約=ブロック／frontmatter 剥離＝drift 0.05→0.00」と「対象外(None)→gate 発火・記録→⚠ surface」のフル配線 E2E を確認。
- **feat(audit,trigger): fitness calibration drift を observability＋proactive trigger に配線（#286）** — fitness 評価関数の score-acceptance 相関（optimize/evolve の history.jsonl）が `CORRELATION_THRESHOLD`(0.50) を割った評価関数を「再 calibration 推奨」として可視化。`fitness_evolution.detect_drifted_funcs(history)` を audit の `build_calibration_drift_section`（observability contract に登録）と trigger_engine の `_detect_calibration_drift`（session 終了時に `MIN_DATA_COUNT`(30) 以上 ∧ drift で `/rl-anything:evolve-fitness` を proactive 提案）の**共有単一ソース**として実装。accept/reject 履歴なし＝対象外(None)／データ不足＝「N/30」advisory／drift なし＝✓ を出し分け（silence≠evaluated）。全 fitness 変更は人間承認 MUST のため advisory のみ（自動適用しない）。論文「self-trained verifier」(arXiv:2605.30290) を ML-infra 非依存の rl-anything 向けに「既存 evolve-fitness の相関分析を recurring ループで再利用」へリフレーム。CONTEXT.md（用語集）に「Belief Entropy」「calibration drift」を追記。

### Changed
- **refactor(similarity): jaccard 数式を `similarity.jaccard_coefficient` に一本化** — `memory_gating` / `meta_quality` / `episodic_store` / `belief_entropy` に分散していた jaccard 係数の重複実装を canonical な公開関数（Set→Set）へ統合。各 call-site のトークン化方針は保持（memory_gating は `.lower().split()`、episodic は `similarity.tokenize`）。回帰 87 件緑で挙動不変を確認。

## [1.80.1] - 2026-05-30

### Changed
- **docs(cleanup): CC v2.1.157 の Claude 管理 worktree unlock 化を反映** — リリースノートレビュー（CC v2.1.156 → v2.1.158）の結果、v2.1.157 で「Claude 管理 worktree がエージェント終了時に unlock される」「`.claude/worktrees/` 孤児が 30 日 sweep 後も残るバグ修正」が入ったため、cleanup スキルの一時 worktree セクションを更新。`scan_removable_worktrees`（`locked` 除外）が終了済み管理 worktree を削除候補として surface する挙動と、実行中セッションは locked で保護される旨を明記。`tool_decision` の `tool_parameters` テレメトリ追加は rl-anything の observe hook が OTEL 非依存のため適用なし（観測のみ）。

## [1.80.0] - 2026-05-30

### Fixed
- **fix(evolve,spec-keeper): SKILL.md がプラグイン同梱 `scripts/lib` を相対参照し対象PJで `No such file` になっていた問題を修正** — evolve の Step 0.5（`world_context.py` ロード）と Report ナレーション（`growth_level` / `save_world_context` の `sys.path.insert(0,'scripts/lib')`）、spec-keeper の用語集 drift チェック（`glossary_drift.py`）が、同梱スクリプトを `python3 scripts/lib/xxx.py` のように**相対パス**で参照していた。スキルは**対象 PJ の cwd** で実行されるため `対象PJ/scripts/lib/...` を指し、rl-anything 以外の全 PJ で `[Errno 2] No such file or directory` になっていた（docs-platform の ev-v7 evolve で world_context ロードが毎回失敗し、agent が `find` で実パスを探して絶対パスで再実行する迂回を強いられていた実害。spec-keeper の glossary_drift も同型で対象PJでは必ず失敗）。全箇所を `${CLAUDE_PLUGIN_ROOT}/scripts/lib/...`（audit / cleanup / agent-brushup 等と同じ正準形）に統一。docs-platform の cwd を再現した before/after 実コマンドで `No such file` → 正常ロードを確認。将来の漏れを封じる回帰テスト `scripts/tests/test_skill_md_plugin_paths.py`（全 SKILL.md が同梱 scripts/lib を相対実行/import していないことを検査。対象PJ生成物の `scripts/rl/fitness/{name}.py` は対象外）を追加。

### Added
- **feat(fleet): `rl-fleet plugins` — インストール済み CC プラグインの最新性を決定論診断** — version フィールドを持たないプラグイン（例: skill-creator / code-simplifier）は `claude plugin update` がバージョン比較できず「最新」と誤判定して cache を同期しないため、marketplace source が更新されても古い cache が使われ続ける silent stale が発生していた（実際に skill-creator の SKILL.md / improve_description.py / run_loop.py が古いまま残っていた）。`installed_plugins.json`（インストール版 + installPath）・各 `marketplace.json`（最新版 + source）・cache↔source のコンテンツ差分の正本3点を突き合わせ、`ok` / `update`（新 semver あり）/ `drift`（同版だが cache 乖離＝要再インストール）/ `unknown`（外部 git source + version 無しで検証不能）を判定する。version 比較もコンテンツ比較もできなかった場合は `ok` と誤認せず `unknown` を返す（silence≠verified、coderabbit の外部 git source 実例で検証）。決定論・LLM 非依存（`scripts/lib/fleet/plugin_freshness.py`）。回帰テスト10件 + 実環境ドッグフード。

## [1.79.0] - 2026-05-29

### Fixed
- **fix(test): test_evolve_audit_flags の順序依存 flaky を根本修正（stale module 参照を解消）** — `test_run_evolve_passes_full_effect_flags_to_audit` がフルスイートでのみ FAIL（単独では PASS）していた。原因は `skills/audit/scripts/audit.py` が import 時に `sys.modules["audit"]` を本物のパッケージ（`scripts/lib/audit`）で **新しいオブジェクトに差し替える** shim であること。先行テスト（test_audit_memory_bytes / test_audit_quality_trends が `skills/audit/scripts` を sys.path 先頭に入れて import → shim 実行、test_audit_snapshot が reload）が走ると、本テストが module-level `import audit` で束縛したオブジェクトと runtime の `sys.modules["audit"]` が別オブジェクトになる。`evolve.py` の `from audit import run_audit` は後者を読むため、前者を `mock.patch.object` しても効かず `m.called == False` になっていた。テスト本体で `sys.modules["audit"]` から live オブジェクトを解決して patch するよう修正し、import 順に依存しないようにした（プロダクトコードは正しいためテスト側のみ修正）。
- **fix(fitness): `constitutional`/`chaos` の `_load_sibling` がパッケージ化された `coherence` を silent skip していた** — `coherence` は #143 で `coherence/__init__.py` パッケージへ分割されたが、`_load_sibling()` の追従が `environment.py` だけに入り、`constitutional.py` / `chaos.py` は `{name}.py` 固定パスのまま残っていた。`_fitness_dir / "coherence.py"` が存在しないため `FileNotFoundError` → `constitutional` fitness が `Constitutional Score スキップ: ... coherence.py` で **silent skip**（`evolve`/`audit` の constitutional スコアから coherence 依存部分が欠落し続けていた、install≠enforcement の silent skip 型）。`environment.py` の package 対応 `_load_sibling`（`pkg_init.exists()` 分岐 → `importlib.import_module`）を両ファイルへ移植。回帰テスト3件追加（coherence パッケージのロード + flat module の principles も引き続きロードできることを保証）。docs-platform の実 `evolve --dry-run` で skip エラー消失を確認。closes #277

### Added
- **feat(evolve): observability contract — 「必ず surface すべき observability 行」を audit↔evolve の構造化フィールドに昇格** — #272（Unmanaged Pitfalls の ✓ 行）は audit の **markdown 経路**だけを直したが、evolve は `run_audit` の 217KB markdown を `phases.audit.report` に丸ごと格納するだけで、assistant は名前付きフェーズ（fitness/skill_evolve/pitfall_hygiene…）を選択読みする運用のため、markdown 中盤に埋もれた observability 行が surface されなかった（docs-platform の evolve 実行 ev-v6 が v1.78.0 でも ✓ 行をログに出さず表面化。silence != evaluated 原則が観測性 fix 自身の配線で再発）。`scripts/lib/audit/observability.py` を新設し `_OBSERVABILITY_BUILDERS`（glossary_drift / unmanaged_pitfalls の **単一ソース**）+ `collect_observability(project_dir)` を定義。`report.py`(markdown) を個別呼び出し2つから `_OBSERVABILITY_BUILDERS` の消費に統一し、markdown 経路と構造化経路が同一ソースになるよう一本化（将来 observability 項目を足してもモグラ叩きにならない）。`run_evolve` は audit phase 直後に `result["observability"]` へ構造化格納し、SKILL.md に **Step 3.8: Observability（必ず surface する — MUST）** を新設。contract テスト7件（markdown/構造化の見出し一致を検査する単一ソース drift ガードを含む）+ API surface snapshot 更新。実 PJ docs-platform で `run_evolve(dry_run=True, skip_llm_evolve=True)` E2E 確認（`observability.unmanaged_pitfalls` に ✓ 行が surface、ev-v6 では消えていた行が構造化フィールドとして取り出せることを実証）。#272 後続。

### Changed
- **feat(evolve): 用語集 seed 作成トリガーを #278 observability contract に統合** — #273 で追加した Step 7.7（CONTEXT.md 無し時の LLM seed 提案）が docs-platform 実 evolve（ev-v6 / session f4b9fac3）で**発火しなかった**。evolve レポートはオーケストレーション・スクリプトが emit する phase 出力を起点に書かれるが、Step 7.7 は phase に裏打ちされない散文ステップで `--dry-run` の谷間に消えた（install≠enforcement の最深レイヤ）。本 PR 初版は独立 `glossary_seed` phase に格上げしたが、並行 merge された #278 が「必ず surface すべき行」を `_OBSERVABILITY_BUILDERS` 単一ソースに集約したため、seed 判定もそこへ統合（surface パターンを phase と observability の2系統に分裂させず1本化）。`build_glossary_drift_section` を拡張し、CONTEXT.md 不在 ∧ undefined jargon ≥ `SEED_MIN_CANDIDATES` のとき「用語集未作成（CONTEXT.md 不在）」seed 提案行を emit（決定論・LLM 非依存）。これで markdown と `result.observability.glossary_drift` の両経路へ自動 surface し、creation gap が evolve のたびに可視化される。独立 `glossary_seed` phase / `check_glossary_seed()` は撤去。SKILL.md Step 7.7 を observability 出力消費型に書き換え。回帰テスト（builder の seed 分岐 + contract surfacing + jargon 薄時の沈黙）。seed writer（`write_context_seed` / SEED gate, #273）は既存流用。closes #275

## [1.78.0] - 2026-05-29

### Added
- **feat(evolve): CONTEXT.md が無ければ evolve が LLM seed を提案生成（creation→detection を一本化）** — glossary drift 検出は evolve に配線済みだが、検出の前提である CONTEXT.md を**作る trigger がどこにも無く**、誰かが手で置くまで永遠に発火しない creation gap を是正（install ≠ enforcement の "もう一段上"）。evolve は per-project かつユーザーが明示的に回すループなので、Housekeeping に **Step 7.7: 用語集ブートストラップ**を新設。CONTEXT.md 無し ∧ 未登録 jargon 候補 ≥ `SEED_MIN_CANDIDATES`(=3) のときだけ AskUserQuestion で生成提案（トークン見積を提示し llm-batch-guard 準拠、silent で書かない）。承認時は LLM が SPEC/CLAUDE から各語の意味を1行生成し、決定論 writer `glossary_drift.write_context_seed`（整形のみ・LLM 非関与・既存は `FileExistsError` で非破壊）で書き出す。全行の初出列に `⚠UNVERIFIED` を付け、人間が意味確認 + 初出記入でマーカーを外すまで **drift gate に載せず** `unverified_terms` advisory で確認を促す。これにより「誤った意味の静かな混入（腐った用語集は無いより悪い）」と「SoT 全自動生成による drift 検出器の自滅」を両方回避。`GlossaryReport.unverified_terms` / `has_unverified()` を追加し audit section が未検証 advisory を surface。意味中の `|` は全角 ｜ に置換しテーブル破壊を防止。テスト6件追加（unverified パース / undefined 非二重計上 / 非破壊 / round-trip / pipe escape / audit section）。実リポジトリでドッグフード（seed→UNVERIFIED→advisory が一気通貫）。
- **feat(audit): Unmanaged Pitfalls — 該当なしでも評価結果を1行残す（観測可能性）** — `build_unmanaged_pitfalls_section` は候補ゼロ時に `None` を返してセクションごと消えていたため、ログ上「評価して該当なし」と「配線が走っていない（配線漏れの再発）」が区別できなかった（docs-platform の evolve 実行 ev-v6 で表面化）。glossary drift と同じ方針に揃え、pitfalls.md が1件でもある PJ では該当なしでも `✓ enable すべき育った pitfalls.md なし（検査 N 件…）` を必ず1行出力するよう変更（全登録済み / 未登録だが全て書きかけ / parser ロード失敗 を文面で区別）。pitfalls.md が1件も無い PJ のみ従来どおり非表示。`discover_pitfalls` で総数を取り「評価した事実」を担保。実 PJ でドッグフード（docs-platform: 検査4件すべて登録済み / rl-anything 自身: 未登録3件すべて書きかけ）。テスト2件を「✓行が出る」検証へ更新。

## [1.77.0] - 2026-05-29

### Added
- **feat(audit): 未登録 pitfalls.md を Unmanaged Pitfalls advisory で可視化 — evolve のたびに enable 漏れが surface** — #265/#266 で pitfall 自動強制（lint/commit-gate）を導入したが、各 PJ で `enable` 登録するまで hook は無反応（install ≠ enforcement・オプトイン）。育っている `pitfalls.md` があるのに未登録だと、その事実がどこにも surface しない問題を是正。`pitfall_registry.unmanaged_candidates(project_dir)` を新設（`discover_pitfalls − load_managed` の純粋集合差・stdlib のみ）、`pitfall-curate parse.py` に `count_entries(content)`（正準パーサ再利用の liveness 指標）を追加。`scripts/lib/audit/sections.py` に `build_unmanaged_pitfalls_section(project_dir)` を新設（未登録 ∧ 実エントリ≥3 の「育っている」ファイルのみを path+件数で提示し `/rl-anything:pitfall-curate` の enable へ誘導、書きかけ・空はノイズ抑制で非表示、1件も無ければ None）。`count_entries` は generic 名（core/parse）で sys.path を汚さないよう importlib でファイル指定ロード。`report.generate_report` に glossary drift と同形で配線したため、evolve は Diagnose 段で audit を消費する＝evolve だけで未登録 pitfalls.md が report に出る。非 UTF-8 ファイル混在でも全体を落とさない。実リポジトリでドッグフード（発見3件すべて0-1エントリのテンプレ→正しく非表示、誤検出ゼロ）。テスト10件追加（parse 2 / registry 3 / section 5）。
- **feat(audit): glossary drift を audit に配線 — evolve のたびに用語集の鮮度が surface** — #268 で導入した `glossary_drift` を spec-keeper update（ユーザーが滅多に回さない）にだけ繋いでいたため、`/rl-anything:evolve` を回しても発火しない設計ミス（install ≠ enforcement の再発）を是正。`scripts/lib/audit/sections.py` に `build_glossary_drift_section(project_dir)` を新設（CONTEXT.md が無い PJ では None、構造 drift は ⚠ / 未登録 jargon は advisory ℹ で表示）、`report.generate_report` に配線。evolve は Diagnose 段で audit を消費するため、evolve だけで用語集の未登録 jargon が report に出るようになった。実リポジトリでドッグフード（実 jargon 9 件 surface、CONTEXT.md 自己参照ノイズを stoplist で除去）。テスト 3 件追加。再発防止として implement スキルに「配線先チェック（新機能は recurring ループ＝evolve/audit/trigger で発火するか）」を、tech-eval スキルに「採用概念は配線先を明示し既定で recurring ループに乗せる」観点を追加。#268
- **feat(spec-keeper): CONTEXT.md 用語集（Ubiquitous Language）と drift 検出を導入** — PJ 固有 jargon を 1 語で decode する共有言語ドキュメント `CONTEXT.md` を新設（DDD の ubiquitous language）。鮮度は新規 `scripts/lib/glossary_drift.py`（決定論・LLM 非依存）が検出: テーブルをパースし `malformed`（スキーマ不一致）/ `duplicate`（重複定義）/ `missing_first_seen`（初出欠落）を **構造 drift** として gate（`has_drift`、CLI exit 1）、SoT（SPEC.md/CLAUDE.md）に出現する未登録 jargon 候補は **advisory**（`has_undefined`、gate しない — オオカミ少年化回避）。頭字語検出は ALLCAPS/CamelCase regex + stoplist で精度確保。spec-keeper の update フロー（通常更新 Step 5 / リカバリ突合表）に配線し、CONTEXT.md があれば自動でチェック。実 CONTEXT.md/SPEC.md でドッグフード（構造 drift 0・advisory 9 件の実 jargon を surface）。tech-eval `mattpocock/skills` の評価から着手。closes #268

## [1.76.1] - 2026-05-29

### Fixed
- **fix(evolve): MemTrace + slop(constitutional) を evolve のデフォルトで有効化** — `run_evolve` の Phase 3 が `run_audit(project_dir)` をフラグなしで呼んでいたため、MemTrace（#264）も slop_detector を 10% ブレンドした constitutional（#255）も「evolve するだけ」では発火せず、実装済みだが観測される挙動に現れなかった（install ≠ enforcement）。`run_audit(project_dir, memory_trace=True, constitutional_score=True)` に変更し evolve だけで両機能が効くようにした。MemTrace は決定論で LLM ゼロ、constitutional は haiku×最大4 だがレイヤ単位コンテンツハッシュキャッシュで通常 0〜1 コール（`constitutional_cache.json`）。constitutional 単独 ON のため `score_count=1` で environment fitness（≥2 で発火）は呼ばれず追加コストなし。両フラグの audit への伝播を保証する回帰テスト（`test_evolve_audit_flags.py`）を追加。

## [1.76.0] - 2026-05-29

### Added
- **feat(pitfall): pitfall-curate に enable モード追加 — skill 1発で自動強制を有効化** — 自動強制 hook の有効化を「ユーザーがコマンドを手打ちする」前提から「`/rl-anything:pitfall-curate` を呼ぶだけ」に引き上げた。`pitfall_registry.discover_pitfalls(project_dir)` で PJ 内の `pitfalls.md` を自動発見（`.git`/`node_modules`/`dist` 等のノイズ dir は降りない、決定論・ソート済み）。CLI に `status` サブコマンドを追加（発見した各ファイルの `{path, managed, state(ok/drift/danger)}` を `--json` で機械可読出力 / 人間向け一覧も）。SKILL.md に **Step 0: 自動強制の有効化** を新設し、curate 本体の前に `status` で未登録ファイルを検出 → AskUserQuestion で確認 → `enable` 実行（`danger`=index/TOC は対象外、`drift` は登録後 normalize 提案）するフローを定義。Trigger に「pitfall 自動強制 有効化 / pitfall enable / pitfalls 自動ルール」等を追加。決定論コアは LLM 非依存、テスト 5 件追加（discover 3 + status 2）。#265 の続き。
- **feat(pitfall): pitfalls.md 自動強制フロー — install + enable で以後ルールが当たる** — agent が pitfalls.md を直接手編集して後で curate すると壊れる/拒否される問題への恒久対策。`normalize --check`（lint: 書き換えず ok/drift/danger を返し ok=0/drift=1/danger=2 で exit、diff 提示）を土台に、編集時 hook `pitfall_lint`（PostToolUse Edit/Write/MultiEdit・**警告のみ非ブロッキング**）と commit 時ゲート `pitfall_commit_gate`（PreToolUse Bash・`git commit` 検知 → staged を `git show :path` で検査 → **danger は exit 2 でブロック**、drift は警告のみで通す）の二段検査を追加。どちらも自動書き換えはしない（preamble/index の silent wipe バグの反省）。`enable`/`disable` CLI サブコマンドで管理対象 pitfalls.md を `.claude/rl-anything/pitfall-managed.json` に登録するオプトイン方式（`scripts/lib/pitfall_registry.py`、決定論・LLM非依存）。登録したファイルにのみ hook が反応し、`enable` は index/TOC を「エントリファイルでない」として登録拒否する。実 git での E2E（ok→通過 / drift→警告通過 / danger→exit 2 ブロック）を確認。hook はプラグイン同梱で install 時に配布、各 PJ で `enable` を1回叩けば以後自動。[ADR-027] 参照。
- **feat(pitfall): `pitfall-curate` スキル新設 — 任意PJの pitfalls.md を育てる PJ非依存ツール** — figma-to-code で確立した pitfall 運用の型（類似 dedup / 普遍性分類 / 三段階開示の配布版生成 / 同期ゲート）を特定ドメインに依存しない形で汎用化。`scripts/pitfall_curate.py` が決定論コア（parse / `find_similar_pairs`(jaccard) / `set_classification` / `mark_superseded` / `select_distill` / `check_sync`）を提供し、普遍性分類（`Transferability`: universal/project/instance + `Generality` 1-5）と reframing 判断はスキル本体(agent)が担当するため script は LLM 非依存。`similarity.py` の jaccard/tokenize を再利用。CLI: `dedup` / `supersede` / `unclassified` / `classify-set` / `distill` / `sync --check`。既存 `pitfall_manager`（自己進化スキル専用）とは別ライフサイクルとして共存。実PJ（atlas-browser）でのドッグフードを経て、有機的に育った実フォーマット耐性を追加: セクション見出しの fuzzy match（`## Active` / `## New`→Candidate）、`Root-cause` 不在時に内容フィールドを dedup 信号に使う fallback、日本語向け CJK 文字 bigram トークン化、dedup デフォルト閾値を 0.4→0.12（日本語コーパス向け）。さらに sys-bots / docs-platform を調査し収束路線を採用: パーサが `## N.` 番号付きエントリ・`**K**: v \| **K**: v` インラインパイプ・`<!-- -->` コメントスキップに対応（sys-bots 実17件パース検証）。`seed`（正準ひな型生成）/ `normalize`（既存ファイルを正準形へ冪等変換、本文保持）サブコマンドを追加。フォーマット I/O 層を `scripts/parse.py` に分離（core.py 569→373行、file-size-budget 遵守）。さらに sys-bots/atlas の実ファイルへ適用検証中に `normalize` が H1 タイトルの説明文とファイル先頭プリアンブル散文を捨てるデータ損失を発見し修正（`_split_header` で抽出・再付与、合成 fixture では preamble/説明的 H1 が無く round-trip 緑のまま見逃していた）。エントリ本文・全メタdata は保持、セクション見出し注釈のみ既知の制限として失われる。さらに atlas が番号付き `### N.` エントリと番号なし `### サブ見出し`（`### 真の原因` 等）を混在させ、後者が幽霊エントリ化する問題を修正（`_demote_subsection_headings`: 番号付きエントリが在る文書に限り番号なし `### ` を `#### ` へ降格、番号保持で冪等、22→18 エントリに正常化）。加えて sys-bots の index `pitfalls.md`（テーブル+リンクの TOC）に normalize をかけると全足切りで wipe される事故を発見し、`normalize` に wipe ガードを追加（エントリ0件かつ実質コンテンツ>3行なら `ValueError` で中断、空ひな型は誤検出しない）。CLI は clean なエラー表示で exit 1。
- **feat(optimize): BES サブゴールスコアラー導入** — `scripts/lib/subgoal_scorer.py` を新設し、候補テキストを 5 つのサブゴール（frontmatter_preserved / trigger_coverage / correction_addressed / line_budget / slop_free）に分解して密な中間フィードバックを返す。LLM 非依存・決定論。`optimize_core.py` に `run_subgoal_scoring(content, original, corrections, max_lines) -> dict` を追加（既存 `run_custom_fitness` は変更なし）。closes #253
- **feat(memory): MemTrace 帰属診断 `memory_trace` モジュール追加** — episodic memory 検索エラーを `misretrieval`（低スコア上位返却）・`context_drift`（temporal staleness 超過）・`corruption`（検索直後 correction 発生）の3類型に分類し発生源 `event_id` に帰属させる決定論エンジン。LLM・外部 oracle 不使用。DuckDB 未インストール時は空返し。`audit/memory.py` に `build_memory_trace_audit_section` を追加（閾値: score_threshold=0.3、staleness_days=30、post_retrieval_window_sec=300）。closes #254
- **feat(fitness): slop 辞書検出器 `slop_detector.py` + `slop_patterns.json`** — 決定論 regex/ヒューリスティックで AI slop パターン（過度な肯定・不要な謝罪・無意味な要約見出し・過剰な免責・空虚な接続句）を日英 10 パターンで検出。`detect_slop(text)` は `SlopResult(slop_score, hits)` を返し、`slop_score` は 1.0=良い / 0.0=悪い の [0.0, 1.0] スコア。`constitutional.py` の overall スコアに 10% 加重ブレンド（slop hit は violations にも追記）。LLM 非依存・コストゼロ。closes #255
- **feat(rl-loop): BES 前向き進化探索 `evolution_operators.py` 導入** — `crossover`（Markdown `## ` セクション単位で決定論結合、frontmatter は parent_a を保持）・`mutate`（セクション安定ソート + 連続重複行除去 + corrections 強調）・`select_parents`（fitness-proportional ルーレット選択、全 fitness 0/負で一様フォールバック、`rng` 注入で再現可能）・`evolve_generation`（親ペア選択 → crossover→mutate で子生成）の決定論進化演算子を新設。`run_loop.py` に `--evolve-search` フラグを追加し、Step 3 評価直後に subgoal_scorer (#253) の total を fitness 信号として子候補を生成・採点して既存 variants に合流（best 選択は子も含めて評価）。`subgoal_scorer._score_slop_free` のプレースホルダを `slop_detector.detect_slop` (#255) に接続。LLM 非依存・決定論。closes #256

### Fixed
- **fix(audit): MemTrace 帰属診断を audit 出力に配線** — `build_memory_trace_audit_section`（#254）は実装済みだが orchestrator から呼ばれておらず audit 出力に現れなかった（実装漏れ）。`run_audit` に `memory_trace` パラメータと `--memory-trace` CLI フラグを追加し `generate_report` まで配線。「関数が呼ばれる」ことを保証する E2E 回帰テスト（`test_run_audit_memory_trace_wiring`）を追加。
- **fix(rl-loop): `--evolve-search` を SKILL.md に記載** — run_loop.py に実装済みだが SKILL.md のオプション表に未記載で `/rl-loop` 経由から到達できなかった（doc 漏れ）。オプション表に `--evolve-search` 行を追加。

## [1.75.0] - 2026-05-28

### Added
- **feat(fleet): `rl-fleet recall` — PJ 横断 memory recall** — 全 PJ の `~/.claude/projects/<pj>/memory/*.md` を keyword 横断検索する決定論 engine を追加（`query` / `--limit` / `--json` / `--root`）。`enumerate_memory_dirs()`（memory dir 存在ベースで plugin 有効性に依らず列挙）+ TF + frontmatter description/filename ブーストの 1 段ランク。LLM rerank / embedding は非採用（消費者の assistant 自身が reranker、コーパス極小で vector の前提が成立しないため）。frontmatter 不正は本文フォールバック + stderr 警告。MEMORY.md index 行は `[index]` タグ + スコア半減で fact 本体より下位。実装は `scripts/lib/fleet/recall.py` + `project_loader.py`、設計判断は ADR 025。

## [1.74.0] - 2026-05-28

### Added
- **feat(skill_triggers): CLAUDE.md のテーブル形式スキル定義・`## Key Skills` 等の見出しに対応** — `_parse_skills_section` が `| `/skill-name` | ... |` 形式のテーブル行と、見出しに `skills` を含む任意のセクション（`## Key Skills` 等、大文字小文字不問）からスキルを抽出するよう拡張。テーブル区切り行はスキップ。

### Fixed
- **fix(skill_triggers): テーブルヘッダ行の phantom skill 誤抽出を修正** — 英語ヘッダ（`| Skill | ... |`）が区切り行より前に処理され、スキル名 "Skill" として誤抽出される問題を `table_body_started` フラグで修正。区切り行（`|---|`）通過後の body 行のみをスキル行として扱う。
- **fix(trigger_engine): 毎発火 hook の duckdb eager import を除去しレイテンシ削減** — `trigger_engine/__init__.py` の `HAS_DUCKDB` フラグを `import duckdb`（cold ~100ms）から `importlib.util.find_spec("duckdb")`（~0.04ms、実モジュール非ロード）に変更。毎 UserPromptSubmit で発火する `correction_detect.py` の起動コストを 114ms → 73ms（-36%）に削減。実際の duckdb 利用は `state.py` 関数内 lazy import に委ねる（API 互換維持、未使用の `_duckdb` バインディング削除）。

## [1.73.0] - 2026-05-27

### Added
- **feat(hooks): CC v2.1.152 — SessionStart hookSpecificOutput.sessionTitle 対応** — `restore_state.py` がプロジェクト名+ブランチを `claude agents` のセッションタイトルとして設定するよう変更。
- **feat(hooks): MessageDisplay フック新設** — アシスタント応答ごとに文字数・コードブロック数・pitfall ヒットを `message_display.jsonl` へ記録。将来の応答アノテーション基盤として設計（passthrough、変換なし）。
- **feat(skills): audit/discover に disallowed-tools 追加** — CC v2.1.152 の `disallowed-tools` frontmatter を活用し、分析系スキルで `Edit`/`Write`/`MultiEdit` を防衛的に禁止。

### Fixed
- **fix(audit): `untagged_reference_candidates` 誤検知削減** — `_is_user_invocable_heuristic` にコードブロック/セクションヘッダ/汎用動詞シグナルを追加し、同スコア時に安全側（action 型）とみなすよう変更。SKILL.md が誤って `type: reference` 付与候補になるバグを修正。
- **fix(evolve-fitness): `HISTORY_DIR` パスバグ修正** — `fitness_evolution.py` の `HISTORY_DIR` が存在しないパスを参照しており history.jsonl が常に 0 件と認識される問題を修正（`parent.parent` → `parent.parent.parent`）。
- **fix(token_usage_ingest): cache_creation_input_tokens の nested fallback 追加** — CC v2.1.152 以前のバグ（トップレベルが 0 で `cache_creation.input_tokens` に実値が格納されていたケース）への後方互換対応。

## [1.72.0] - 2026-05-27

### Added
- **feat(memory-gating): correction 重要度スコアリングで auto_memory_runner のノイズ低減** — `memory_gating.py` に `score_correction()` を追加。`RL_GATING_DISABLED=1` で無効化可能（#238-#241）。
- **feat(fitness-history): fitness スコア自動記録 + HISTORY_DIR パス修正** — `compute_environment_fitness()` に `record=True` パラメータを追加し audit 実行時に `fitness_history_store.record_fitness_run()` を自動呼び出し（#240）。
- **feat(hypothesis-tracker): 仮説追跡ストア追加** — `hypothesis_tracker.py` を新設。仮説の記録・更新・クエリを提供（#241）。

### Fixed
- **fix(fitness_history_store): DuckDB 構文・NaN ガード・テスト品質改善** — `INSERT OR IGNORE` → `INSERT INTO ... ON CONFLICT DO NOTHING` に修正（DuckDB 標準構文）。`math.isfinite` による NaN ガード追加（NaN スコアはスキップ）。`ORDER BY id DESC` に修正（timestamp 同秒衝突でも順序安定）。`environment.py` の `_load_sibling` を coherence パッケージ（`__init__.py`）対応に修正。`test_auto_memory_runner.py` に `RL_GATING_DISABLED=1` を付与して memory-gating 追加後のテスト失敗を解消。
- **fix(evolve): Step 7 prune候補を個別調査・分類してから承認を求めるよう改善** — ゼロ呼び出しスキルを一括で「オンデマンドスキル」と決めつけて bulk 選択を提示する動作を修正。各候補について SKILL.md を Read + git log で調査し、オンデマンド型/一時目的完了型/統合済み型/日常用途・未発火型の4種別に分類して根拠をテキスト出力してから1件ずつ AskUserQuestion で承認を求めるよう変更。
- **fix(evolve): auto_fixable の修正内容を AskUserQuestion の前に表示するよう指示を強化** — `generate_auto_fix_summaries` の proposal/rationale を明示フォーマット（ファイルパス・修正内容・理由を1件ずつ列挙）でテキスト出力してから AskUserQuestion を呼ぶよう SKILL.md に明記。proposable セクションの「Q&A前に補足説明」pitfall ルールを auto_fixable にも適用。

## [1.71.0] - 2026-05-27

### Fixed
- **fix(audit): stale_ref が AWS SSM / インラインバッククォート内パスを誤検知する問題を修正** — `path_extractor.py` の `extract_paths_outside_codeblocks()` でインラインコード（`` `...` ``）内パスをマスクしてから検出するよう変更。バッククォートを前置文字として扱っていた正規表現も修正。
- **fix(audit): skill_quality_pattern_gap が日本語チェックリスト見出しを認識しない問題を修正** — `instruction_patterns.py` に `_CHECKLIST_HEADING_RE` を追加。`## 実行前チェックリスト` / `## 完了チェックリスト` / `## Checklist` 等の見出しを `checklist` パターンとして認識するよう拡張（末尾アンカーで否定見出しの誤検知も防止）。

### Added
- **feat(evolve): --confirmed-batch フラグで batch_guard_trigger の再発火を防止** — `skill_evolve_assessment()` と `run_evolve()` に `confirmed_batch: bool = False` を追加。ユーザーがインタラクティブフロー（batch_guard_trigger 発火時の AskUserQuestion）を経て確認済みの場合、`--confirmed-batch` フラグで `_MAX_AUTO_SKILLS` 閾値超過でも LLM 評価を続行できる。`evolve.py:881` の重複 print（NameError バグ）も同時修正。`TestConfirmedBatchFlag` クラス（3テスト）+ `test_confirmed_batch_bypasses_guard_in_assessment`（実バイパスロジック直接検証）追加。

## [1.70.0] - 2026-05-27

### Added
- **feat(skill-evolve): Co-ReAct-inspired rubric checkpoint visualization (#231)** — `rubric_checkpoint()` を proposal/apply フローに追加。ステップ実行時に rubric 各軸（pitfalls_template / pre_flight / sections_to_add / diff_lines）の充足度をチェックポイント表示し、visualize-as-you-go で evolve 品質の透明性を向上。`count_diff_lines` を public API として公開。
- **feat(memory): AlphaSignal-inspired importance scoring for memory files (#233)** — `memory_temporal.py` に `importance_score` 計算（base / correction / update ボーナス）と `reinforce_memory()` アトミック更新を追加。`auto_memory_runner` が新規 memory 書き込み後に `importance_score` を frontmatter に自動付与。audit レポートに low-importance memory 候補セクション（score ≤ 0.3）を追加し剪定候補を可視化。

### Changed
- **refactor(evolve): evolve ナレーションを職人の一言メモスタイルに変更** — RPG 語彙（書架・司書・歪み）を除去し、件数ベースの短文（「{N}件の兆候を確認。一つずつ見ていく。」等）に置き換え。技術出力の流れを妨げない自然なコメントへ。

## [1.69.0] - 2026-05-26

### Added
- **feat(evolve): RPG 物語ナレーション — プロジェクト固有世界観で evolve を物語化** — `scripts/lib/world_context.py` を新設。CLAUDE.md から LLM で架空世界設定（setting / protagonist_title / environment_name / issue_name / improvement_name）を生成し `~/.claude/rl-anything/world-context.json` に永続保存。2回目以降は同じ世界観を再利用して物語の継続性を保つ。`total_evolve_count` / `last_evolve_date` / `current_level` / `previous_level` を自動更新（`save_world_context(env_score=...)` でレベルアップ判定）。`scripts/tests/test_world_context.py` で18テスト（LLM呼び出しは全てモック）。`skills/evolve/SKILL.md` に Step 0.5（世界観ロード/生成）と各ステージ後のナレーション指示（Discover 後・Remediation 後・Prune 後・Report 後のレベルアップクライマックス）を追加。

### Fixed
- **fix(audit): `.archive/` 配下のスキルを rglob から除外 + max_skill_count を custom スキルのみで判定** — `find_artifacts()` の `skills_dir.rglob("SKILL.md")` が `.archive/` 配下のアーカイブ済みスキルを拾って数カウントが過剰になる問題を修正。`bloat_control.py` の `skills_count` も `classify_artifact_origin(p) == "custom"` のみカウントするよう変更。
- **fix(bloat): skills_count チェックを custom スキルのみで判定** — `bloat_control.py` の skills 数チェックが global/plugin スキルを混入してしまう問題を修正。`classify_artifact_origin` で custom のみフィルタ。

## [1.68.0] - 2026-05-26

### Added
- **feat(fitness): 日次 evolve のスキル diff 提案 accept/reject を採点付きで蓄積する** (#223) — `fitness_evolution` がサンプル不足（0/30件）でデッドフィーチャー化していた問題に対応。母集団が optimize/rl-loop に限定され「1日1回 evolve」では永遠に貯まらなかった。(a) `insufficient_data` メッセージに母集団（optimize/rl-loop に加え evolve diff 提案）を明記。(b) 採点ブリッジ `record_evolve_diff_decision()` を追加 — evolve の skill diff を accept/reject した時点で after content を `evaluate_skill_quality` で採点し `fitness_func="skill_quality"` / `source="evolve_remediation"` で history.jsonl に正規記録（混合ではなく増量＝相関が壊れない）。冪等 ingest（id 重複排除）。(c) `analyze_correlations` を `fitness_func` でグループ化（異種採点の混合防止、`by_fitness_func` 構造を返す）。SKILL.md の matched_skills accept/reject 点に採点記録手順を追記。(d) `format_correlation_report()` を追加 — `by_fitness_func` を各 fitness_func グループ独立に整形（data_points / correlation 値 / グループ単位の <0.50 警告）し、SKILL.md Step 3 のレポート表示を新形状に追随。

- **feat(evolve): batch guard をグループ単位スキップ + 永続 denylist に置き換え (#225)** — `skill_evolve_assessment()` が 10件超でRuntimeError を投げる all-or-nothing 方式を廃止。代わりに `_meta: batch_guard_trigger` sentinel を返し、evolve.py 経由で SKILL.md のインタラクティブフロー（グループ表示→AskUserQuestion→永続 denylist 保存→再実行）へ誘導する。`denylist.py`（`add_to_denylist` / `get_denied_skill_names` / `remove_from_denylist`）を新設し `~/.claude/rl-anything/skill-evolve-denylist.json` にグローバル保存。`skill_evolve_assessment()` に `skip_skills` / `skip_llm_evolve` パラメータを追加。`evolve.py` に `--skip-skills` / `--skip-llm-evolve` CLI arg を追加。

### Fixed
- **fix(prune): prune shim が分割後の旧 `scripts/lib/prune.py` を spec ロードし FileNotFoundError になる問題を修正** — パッケージ化（`scripts/lib/prune.py` → `scripts/lib/prune/`）後も `skills/prune/scripts/prune.py`（shim）が旧ファイルパスを `spec_from_file_location` に渡しており、`test_e2e_correction_flow.py` の collection が FileNotFoundError で落ちていた。discover shim 修正と同手法で `scripts/lib/prune/__init__.py` を `submodule_search_locations` 付きで明示ロードするよう変更。これで `pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/ --collect-only` が 0 error（2750 collected）で通るようになった。
- **fix(discover): discover shim の `import_module` 自己再帰で test 収集が RecursionError になる問題を修正** — `skills/discover/scripts/discover.py`（shim）がファイル名 `discover.py` のため、pytest collection 中に shim 自身のディレクトリが `sys.path` 先頭に載ると `importlib.import_module("discover")` が shim 自身を再解決して無限再帰し、`test_hooks_discover_prune.py` / `test_e2e_workflow.py` の collection が RecursionError で落ちていた。v1.66.0 で remediation shim に適用した手法と同様に、名前解決 import をやめ `importlib.util.spec_from_file_location` で `scripts/lib/discover/__init__.py` を実ファイルパス指定でロードするよう変更した。

## [1.67.0] - 2026-05-26

### Fixed
- **fix(remediation): missing_effort の type 不一致で effort frontmatter の修正が no-op になる問題を修正** — 検出側（`audit/issues.py`）が生成する LIVE な issue type は `"missing_effort"` だが、`fix_missing_effort` のフィルタ・`FIX_DISPATCH`・`VERIFY_DISPATCH` が定数 `MISSING_EFFORT_CANDIDATE = "missing_effort_candidate"` でキーされており一致しなかった。このため evolve で「effort を追加する」を選んでも修正ハンドラが対象を弾いて何も適用されなかった。定数値を LIVE type `"missing_effort"` に統一。type 不一致を弾く回帰テスト（定数=LIVE一致 / FIX・VERIFY dispatch に LIVE key 存在）を追加。既存の `fix_missing_effort` テストはバグと同じ `"missing_effort_candidate"` を渡しておりバグをマスクしていたため LIVE type に修正。

### Added
- **feat(evolve): 全 AskUserQuestion 提案ポイントに「提案詳細プロトコル」を導入** — evolve の提案が「active スキル 10件 を追加しますか？」のように件数だけ出してユーザーが判断できない問題に対応。SKILL.md 冒頭に共通プロトコルを新設し、AskUserQuestion 前に各対象を per-item 展開して「対象（具体名）・根拠（detail の実値: 閾値/confidence/reason）・変更内容（before → after）」を必ず提示するよう統一した（最大10件、超過分は誘導）。判断材料が薄かった Step 2（fitness 生成）/ Step 5.5（proposable）/ Step 7（prune custom）/ Step 7.5（pitfall 卒業）に参照を追記。`generate_proposals()` と `generate_rationale()` に `missing_effort` 分岐を追加し、件数に丸めず各スキル名・推定 effort・推定根拠を per-item で返すようにした（proposable 対応 type にも `missing_effort` を追加）。

## [1.66.0] - 2026-05-26

### Added
- **feat(remediation): auto_fixable issue を1件ずつ rationale 付きで列挙する `generate_auto_fix_summaries()` を追加** — evolve の Remediation フェーズで「auto_fixable N件を一括修正しますか？」と尋ねる前に、各 issue の「何を・なぜ・どう直すのか」を issue 単位で提示できるようにした。`generate_proposals()` が auto_fixable 専用 type（`stale_ref` / `stale_rule` / `claudemd_phantom_ref` / `claudemd_missing_section`）に対しても汎用フォールバックでなく具体的な proposal テキストを返すよう拡張。SKILL.md の auto_fixable 提示手順を「1件ずつ rationale 付き列挙 → 一括修正／個別承認／スキップ」に更新。
- **feat(evolve): テレメトリ未取得（初回導入直後）を検知して backfill を提案** — `check_data_sufficiency()` が「単なるデータ不足」と「テレメトリが完全に空（観測0・セッション0）」を区別し、後者で `telemetry_empty` / `backfill_recommended` フラグを返す。`run_evolve` の Step1 出力（`observe.action`）に `backfill_recommended` 分岐を追加し、`/rl-anything:backfill` を先に実行するよう案内する。自動実行は副作用が大きいため提案にとどめる。SKILL.md の Step1 にも分岐を明記。

### Fixed
- **fix(analyzer): `analyze_tool_usage()` が `bash_ratio` を返さず 0.0 矛盾を起こす問題を修正 (#221)** — evolve の観測で「bash_ratio 0.0%」と表示される一方、実測では 72.8% という矛盾が出ていた。根本原因は `analyze_tool_usage()` の返り値 dict に `bash_ratio` キー自体が存在せず、呼び出し側が `.get("bash_ratio", 0.0)` で常に 0.0 にフォールバックしていたこと。early-return（空ケース）と通常 return の両方に `bash_ratio = bash_calls / total_calls` を追加。
- **fix(evolve): `skills/evolve/scripts/remediation.py` shim が分割後の `scripts/lib/remediation/` パッケージを読めず ImportError になる問題を修正** — パッケージ化（旧 `remediation.py` → `remediation/`）後に shim が旧ファイルパスを参照したままで、`test_remediation_layers.py` 等が収集エラーになっていた。shim を `__init__.py` の file location 明示ロードに変更し、sys.path 先頭に shim 自身のディレクトリが来ても再帰ロードしないようにした。
- **test(evolve): `test_fix_line_limit_rule_separation` のデータ契約不整合を修正** — テスト fixture が `{lines: 7, limit: 5}` という production では生成され得ない状態（rule の行数上限は `MAX_RULE_LINES = 10` 固定で、`suggest_separation` は detail.limit でなくこの定数で超過判定する）を使っており、7 行 < 10 行のため `separation_not_applicable` で正しく fail していた。テスト内容を実際に 10 行超（12 行）とし detail を `{lines: 12, limit: 10}` に修正。shim 修復で初めて collection が通り顕在化した既存テストバグ。

## [1.65.2] - 2026-05-26

### Fixed
- **fix(audit): aggregate_usage の None キーで quality_monitor がクラッシュする問題を修正 (#217)** — `implement` 等が `skill` フィールドで自己報告するレコードに対し `aggregate_usage` が `skill_name` のみを参照して `None` キーを生成し、`quality_monitor.resolve_skill_path(None)` が `TypeError`（PosixPath / NoneType）でクラッシュしていた。`skill_name → skill → "unknown"` のフォールバックと resolve_skill_path の None ガードを追加。evolve 実行時の「品質計測スキップ」エラーを解消。
- **fix(docs): SPEC.md / CLAUDE.md / plugin.json の数値誤りを一括修正** — Observe hooks 数（15→21）・userConfig 項目数（15→17、`error_preflight_threshold` を manifest に追加）・bin/ コマンド数・スキル数の記載を実態に同期。`backfill` スキルと `bin/rl-backfill-turn-indices` の混同を解消。

### Added
- **test(bin): bin/ スクリプトの import smoke test を追加 (#216)** — `python3 bin/rl-XXX --help` を実行し stderr の ImportError/ModuleNotFoundError を検出する smoke test。publish 前に bin/ の起動不能を機械検出する。

## [1.65.1] - 2026-05-25

### Fixed
- **fix(bin): bin/ スクリプトの import エラーを一括修正 (#215)** — `bin/rl-prune`（prune/__init__.py に main を re-export）、`bin/rl-reorganize`（reorganize.py に main() 追加）、`bin/rl-loop`（run-loop.py → run_loop.py リネームで Python import 不能を解消）の3本の起動エラーを修正。ソース .py 削除済みのデッドラッパー（bin/rl-backfill / rl-backfill-analyze / rl-backfill-reclassify）を削除。

## [1.64.0-excerpt] - 2026-05-25

### Added (SPEC.md Recent Changes から移動)
- **feat(evolve-skill): bounded edit gate + LR budget + rejected pre-flight v1.64.0 (#196 #199 #200 #201)** — `_count_diff_lines()` で edit 差分行数を計測し `skill_lr_budget`(デフォルト30行、userConfig 設定可)超過時はテンプレートフォールバック。rejected 履歴(ユーザー否認3回以上)で pre-flight 警告。`reason_refs` フィールドで元セッション証拠を保持

## [1.65.0] - 2026-05-25

### Added
- **feat(backfill): constraint_decay 用 turn_index backfill スクリプト (#214)** — `bin/rl-backfill-turn-indices` + `scripts/lib/backfill_turn_indices.py` を追加。`sessions.jsonl` に `max_turn_index (= human_message_count - 1)`、`corrections.jsonl` に `turn_index`（raw session JSONL の timestamp マッチング）を一度きり backfill する。安全設計: backup-first + tmpfile atomic rename + dry-run デフォルト。テスト 18 件。実機で constraint_decay が動作することを確認済み（WARNING 5 件検出）。

## [1.64.1] - 2026-05-25

### Fixed
- **fix(triage): meta_quality に session_count=0 を渡す** — `triage_skill()` が `session_count=max(missed_session_count, 1)` を渡していたため `reuse_rate` が常に 1.0 となり `low_reuse` フラグが発火しなかった。`session_count=0`（不明）を渡すよう修正。

## [1.64.0] - 2026-05-25

### Added
- **feat(evolve-skill): bounded edit gate + LR budget + rejected pre-flight + reason_refs (#196 #199 #200 #201)** — `proposal.py` に difflib bounded edit gate を追加: LLM 出力の変更行数が `skill_lr_budget`（デフォルト 30 行）を超えた場合テンプレートにフォールバック。`trigger_engine/self_evolution.py` に `get_rejected_stats(skill_name)` を追加し remediation-outcomes.jsonl からスキル別 rejected_rate を集計。`evolve_skill_proposal()` 冒頭で rejected_rate > 30% なら `{"status": "skipped"}` を返す rejected pre-flight チェックを実装。`apply_evolve_proposal()` が `correction_ids` を受け取り SKILL.md に `<!-- reason_refs: [...] -->` を記録。plugin.json userConfig に `skill_lr_budget` フィールドを追加。
- **feat(hooks): auto_memory_runner + Stop hook L2 memory (#198 #204)** — `hooks/auto_memory_runner.py` 新規作成。Stop hook 終了時に corrections 直近 5 件から memory 候補を非同期生成（LLM 1 call 上限）。new-file-per-entry パターンで race condition 回避。MEMORY.md は append-only index。200 行超で最古エントリを archive.md に移動。`hooks/session_summary.py` に Popen バックグラウンド起動を追加。memory frontmatter v2 スキーマ（importance / detail_file）を ADR として確定。`scripts/lib/audit/memory.py` に broken detail_file リンク検出を追加。
- **feat(triage): meta-skill 品質フィルタ (#203)** — `scripts/lib/meta_quality.py` に `meta_quality_check()` を追加。再利用頻度（trigger_count / session_count < 0.1 で低頻度フラグ）と単語 Jaccard 類似度（> 0.6 で重複候補フラグ）で CREATE/REVIEW/SKIP を判定。低頻度かつ重複ありで SKIP、重複ありのみで REVIEW、それ以外は CREATE。`triage_skill()` の CREATE 判定パスに組み込み、`meta_quality` フィールドを結果に付加。LLM 不使用。
- **feat(discover): constraint decay 検出 (#197)** — `discover/patterns.py` に `detect_constraint_decay()` を追加。arXiv 2605.06445 の知見に基づき、セッション後半30%のターンに集中する correction を検出して decay_rate (0.0〜1.0) を算出。O(N+M) pre-index 最適化・30日 mtime フィルタ・ZeroDivision ガードを実装。`run_discover()` に統合し、decay_rate > 0.3 の場合 WARNING として `constraint_decay_warnings` キーで結果を返す。
- **feat(audit): per-skill 負の転移測定 (#202)** — `audit/usage.py` に `compute_negative_transfer()` を追加。arXiv 2605.23899 の知見に基づき、スキル追加イベント（`type=="skill_added"`）前後の fitness_score delta を記録し、`delta < -0.05`（デフォルト閾値）の場合に `negative_transfer=True` フラグを付与。空データ・データ不足・不完全レコードのエッジケースを全てカバー。

## [1.63.0] - 2026-05-22

### Added
- **feat(hooks): correction_detect に error_category 分類を追加** — correction_type から `behavioral` / `guardrail` / `explicit` / `unknown` への LLM コストゼロ分類。corrections.jsonl レコードに `error_category` フィールドを付与し、失敗原因のカテゴリ別解析を可能にする（AgentAtlas 軌跡分類の基盤）。
- **feat(telemetry): score_failure_distribution() を追加** — corrections.jsonl の error_category 分布を集計する新関数。`by_category` / `dominant_category` を返し、どのカテゴリの失敗が多いかを数値で把握できる。
- **feat(lib): corrections_insights モジュールを追加** — `scripts/lib/corrections_insights.py`。`count_repeated_patterns()` で繰り返し失敗パターン TOP-N を集計。lookback フィルタ・`.get()` fallback・閾値 10 件を実装。
- **feat(audit): 繰り返し失敗パターンセクションを追加** — `/rl-anything:audit` の出力に「繰り返し失敗パターン TOP-N」セクションを追加。同一 correction_type が 3 回以上繰り返されると自動列挙。
- **feat(reflect): importance_score heuristic を追加 (Mem-π)** — reflect.py が corrections の重要度スコアを計算。`confidence × max(0, 1 - elapsed_days / decay_days)` で低重要度修正をフィルタし、レビュー件数を抑制。
- **docs(skills): reflect と evolve-skill の SKILL.md を更新** — reflect/SKILL.md に Mem-π フィルタ説明を追記。evolve-skill/SKILL.md の pre-flight セクションに冪等性チェック（12-factor-agents Factor 5-6）を追記。

## [1.62.0] - 2026-05-22

### Added
- **feat(implement): /implement スキルに depends_on グラフと Ready tasks 検出を追加** — タスク表の「依存」列を task # 列記に formal 化（自由テキスト廃止）。topological sort で循環依存を検出して ERR で停止（ユーザー承認前に実施）。Ralph Loop 開始前に `Ready: T1,T3 / Blocked: T2` 形式の依存状態を表示。各タスク前に depends_on チェック（step 0）を追加し、マルチパス再評価でデッドロック検出を強化。Parallel モードはクロスレーン depends_on を検出して Standard に自動デグレード。テレメトリ: `tasks_completed → list[str]` + `tasks_count(int)` 両建てでセッション再開に対応。

### Fixed
- **fix(implement): adversarial review 対応** — 循環依存チェックをユーザー承認前に移動。センチネル `—`（em-dash のみ）を明示仕様化。マルチパス Ralph Loop でブロック解消後の再評価を保証。
- **fix(lsp): LSP ツール名を LSP_TOOLS セットに追加** — `lsp_measure.py` の LSP ツール呼び出し追跡に対応。

## [1.61.1] - 2026-05-21

### Fixed
- **episodic /review 指摘対応** — `promote_to_episodic` が DuckDB エラー時も `"promoted"` を返す不具合を修正（bool 返し + `{"status":"error"}` + exit 1）。`.db.wal` も `chmod(0o600)` に追加。英語トークンを `\b` word-boundary で substring recall 誤マッチ（git→digit 等）を防止。`find_episodic_duplicates` 内で `prune_expired()` を opportunistic 呼び出し。vacuous test 3件を意味ある assertion に修正（INSERT OR IGNORE 実証・exit code 検証・f-string SQL → parameterized）。471 tests passed

### Changed
- **SPEC.md 更新** — MemOS テーブルに Episodic 層（L1/L2 橋渡し）を追加、modules 数更新、Recent Changes 整備

## [1.61.0] - 2026-05-21

### Added
- **階層型クロスセッションメモリ (#189)** — 同じ修正が何セッションにもわたって繰り返されなくなる。`reflect` が修正を approve すると DuckDB の episodic 層（TTL 30日）に昇格し、次セッションで類似修正が現れると「N日前に対処済み: <内容>」として表示。`working` (corrections.jsonl) / `episodic` (episodic.db) / `semantic` (MEMORY.md) の3層メモリが揃った。`rl-reflect --promote-episodic` CLI で手動昇格も可能。DuckDB 未インストール時は episodic なしで通常動作。

## [1.59.0] - 2026-05-21

### Added
- **貢献スコア追跡**: `observe.py` が Skill 呼び出しの `outcome`（success/error）を `usage.jsonl` に記録。`aggregate_contribution_scores` で集計し audit レポートの Usage セクションに表示（Library Drift arXiv:2605.19576 対応）
- **Retirement 機構**: `detect_retirement_candidates` が貢献スコア閾値以下のスキルを自動でアーカイブ候補として検出。`run_prune` の返り値に `retirement_candidates` セクションを追加。クロスプロジェクトスコープで集計しグローバルスキルの誤フラグを防止
- **スキル数キャップ**: `max_skill_count`（デフォルト 30）を userConfig に追加。audit レポート Summary に「スキル数 / 推奨上限」と超過時の ⚠️ インジケータを表示
- **Pre-flight ガードレール能動化**: `correction_preflight_threshold`（デフォルト 3）を userConfig に追加。`evaluate_corrections` でスキル単位の correction 集中を検出し `/rl-anything:evolve-skill` 提案を自動出力（HASP arXiv:2605.17734 対応）

## [1.58.1] - 2026-05-21

### Fixed
- **fix(evolve): レポートのノイズ除去と推奨アクションの actionability 改善 (#184)** — stale_rule 誤検知（技術用語列 `buildspec/CDK/Terraform/Lambda` 等）を `_PATH_PATTERN` 拡張子必須化で解消。hardcoded_value 検出で global/plugin スキルを除外。`proposable_custom`/`proposable_global` フィールド追加で scope 別件数を分離表示。推奨アクション出力を 🔴/🟡/✅ 判定カード形式に変更。`classify_artifact_origin` を `audit` から直接 import するよう修正（`artifact_scope` re-export 漏れによる ImportError を解消）。

## [1.58.0] - 2026-05-21

### Added
- **feat(docs): rl-anything 説明サイトを docs/site/ に追加 (#179)** — claude.com スタイル（ライトクリームテーマ）の 4 ページ構成 HTML ドキュメントサイト。index / pipeline / reference / sources + 共有 CSS/JS。クイックスタートをストーリー仕立て・フロー図・シナリオカードで刷新。
- **feat(docs): `docs-refresh` スキルを追加 (#179)** — バージョン番号・スキル一覧・4つの柱・アーキテクチャ表を自動更新。sources.html は手動管理対象のため対象外。リリースフロー (`commit-version.md`) に組み込み済み。

### Fixed
- **fix(evolve): reorganize/prune の global スキル混入と hooks false positive を修正 (#178)** — `artifact_scope.py` 廃止・`layer_diagnose` から `artifact_scope` 依存を除去。`prune/detection.py` の orphan フィルタを `global_skills` リストで補強。
- **fix(skill-evolve): token 爆発防止 — global スキル除外 + LLM バッチ guard (#177)** — evolve-skill の global スキル（gstack / review 等）を対象外に。LLM バッチ処理前に件数・トークン見積もりをユーザーに提示。

## [1.57.0] - 2026-05-20

### Added
- **feat(fleet): `rl-fleet status` にアクティブセッション表示を追加** — `_show_active_agents()` が `claude agents --json` (CC v2.1.145+) を呼び出し、アクティブセッション数・名前を `[fleet]` 行として status 末尾に表示。失敗・空・不正 JSON は非表示でフォールバック。テスト 6件追加
- **feat(hooks): `detect-deferred-task` が CC v2.1.145 の `background_tasks` / `session_crons` に対応** — 先送り検出時に実行中バックグラウンドタスク数を reason に付加。先送りなしでも残存タスクがあれば stderr に非ブロック警告を出力。テスト 8件追加

## [1.56.0] - 2026-05-20

### Added
- **feat: コミュニティスキル import 機能を追加** — `bin/rl-fleet import <source>` で `owner/repo`・ローカルパス（絶対・相対）・URL からスキルを取得・インストール。`skill_importer.py`（parse/fetch/validate/preview/install）+ パス・トラバーサル多層防御（`_SAFE_NAME_RE` / `_SAFE_SEGMENT_RE` / `dest.resolve().relative_to()`）。`/rl-anything:import` スキルラッパー追加。テスト 22件追加
- **feat: evolve 意図確認層 (Intention Check) を regression_gate に追加** — `IntentionCheckResult` dataclass + `intention_check(candidate, original)` 関数を `regression_gate.py` に追加。evolve Step 2.5 でパッチ候補を検査。BLOCK 条件: Trigger 削除率 ≥30%・description 消失・disable-model-invocation 削除。WARN 条件: effort low↔high・Jaccard<0.5。evolve サマリに BLOCKED/WARNED を表示。テスト 7件追加
- **feat(pipeline_eval): スキル生成3型比較評価フレームワーク PipelineEvalRunner を追加** — 型1（パターン抽出）・型2（プロンプト最適化）を横断比較する `PipelineEvalRunner` クラスを新規追加。`predicted_trigger` フィールドで FP/FN を実測値から算出。`compare()` で precision/recall スコアから winner を数値決定。テスト 34件追加
- **fix(skill_importer): 実在する相対パスを LocalSource として解釈する** — `Path(spec).exists()` チェックを追加し、`skills/reflect` 形式の相対パスが `owner/repo` と誤判定されないよう修正

## [1.55.1] - 2026-05-20

### Fixed
- **fix(optimize): `llm_improve` モードで frontmatter が消失するバグを修正 (PR #175)** — `build_patch_prompt()` に YAML frontmatter 保持指示を追加（プロンプトレベル防止）。`restore_frontmatter_if_lost()` を `generate_candidate()` / `DirectPatchOptimizer.run()` の両経路に適用（セーフティネット）。`_extract_frontmatter()` に `\r\n` 正規化と `startswith("---\n")` 厳格化を追加。既存テスト 34件のモジュール参照ずれを修正、新テスト 6件追加（CRLF 対応含む）。計 55 テスト pass。

## [1.55.0] - 2026-05-19

### Added
- **FORGE: `--mode population_broadcast` を optimize.py に追加 (#173)** — n=3 候補を ThreadPoolExecutor で並行生成し、最高スコアの winner を選択。`evolution_memory.save_winner()` でパターンを永続化。`generate_candidate()` helper を optimize_core.py に追加。
- **FORGE: `evolution_memory.py` を新規追加 (#173)** — 最適化成功パターンを `~/.claude/rl-anything/evolution_memory.jsonl` に JSONL 永続化。`save_winner()` / `load_patterns()` の2関数 API、max 1000件ローテーション。
- **LBYL: `regression_gate.pre_check()` を追加 (#173)** — warn-only のリスク評価。API シグネチャ消失 / 行数 2x 超 / frontmatter 削除を事前検出。実行はブロックしない。
- **LBYL: `ConfidenceInterval` スキーマと `to_confidence_interval()` ヘルパーを追加 (#173)** — rl-scorer 出力に ±σ 信頼区間を付与。`scorer_schema.py` に `ConfidenceInterval` dataclass、`score_noise.py` に変換ヘルパーを追加。
- **ALSO: rl-loop-orchestrator に対抗的マルチエージェント評価を追加 (#173)** — `run_adversarial_agent()` で攻撃者エージェントがスキルの弱点を探索。`compute_disagreement_score()` で評価者間不一致を定量化。disagreement > 0.15 で警告を出力。
- **docs(spec): AIRA 長期ロードマップを SPEC.md に追記 (#173)** — arXiv:2605.15871 参照。スキル構造自動探索エンジンの設計構想を記録。

### Fixed
- **fix(optimize): `PopulationBroadcastOptimizer` の skill_name が "SKILL" になるバグを修正 (#173)** — SKILL.md を直接指定したとき stem が "SKILL" になる問題を `__init__` 内で親ディレクトリ名に自動補正。
- **fix(fleet): `_DEFAULT_RL_AUDIT_BIN` パスずれ修正（全PJ AUDIT ERROR 解消）(#174)** — 再発防止テスト追加。

### Changed
- **refactor(optimize): `optimize_core.py` にコアロジックを分割 (#168)** — `optimize.py` を 813 行から 456 行に削減。純粋関数を `optimize_core.py` に切り出し。

## [1.54.0] - 2026-05-19

### Added
- **AgentErrorTaxonomy 対応: stop_failure に `error_class` フィールドを追加 (closes #148)** — 全 error_type を `"tech"` に分類。behavioral 分類と `error_layer` は reflect スキルが遅延付与。
- **MemOS L1→L4 結晶化アーキテクチャを ADR-024 として明文化 (closes #149)** — corrections→evolve 4層設計の学術的根拠を確立。SPEC.md にギャップマッピング（層間矛盾検出・自動 reconsolidation・ハイブリッド検索）を追記。
- **corrections.jsonl に `preceding_tool_calls[]` を追加 (closes #150)** — 修正直前の直近5件ツール呼び出しを記録。reflect の pitfall 生成精度向上の基盤。
- **reflect: `preceding_tool_calls` と `error_class` を pitfall 生成に統合 (closes #165)** — `analyze_tool_call_patterns` に `preceding_sequences` 軸を追加。`error_class_summary` で errors.jsonl の分類サマリを出力。3PJ横断バックフィル分析で「Bash連続実行→先送り」パターンを pitfalls.md に登録。`scripts/backfill_preceding_tool_calls.py` 追加。

## [1.53.0] - 2026-05-19

### Added
- **audit レポートに LSP Setup Recommendation セクションを追加 (closes #161)** — `.lsp.json` 未設定PJで Python/TS/JS/Go/Rust ファイルを検出した場合、言語サーバー名・インストール手順・`.lsp.json` 設定例を自動提示。Read ツール呼び出し削減を促す。
- **`scripts/rl/fitness/telemetry.py` に r^comp / r^fc を telemetry fitness 5 軸に追加 (closes #67)** — SkillOS 論文の compression term (r^comp) と function-call validity (r^fc) を新関数 `score_skill_compression` / `score_fc_validity` として実装。WEIGHTS を 3 軸 (util/effect/implicit) から 5 軸 (+ compression 0.10 / fc_validity 0.05) に更新。`compute_telemetry_score` / `format_telemetry_report` に新軸を統合。
- **docs(decisions): SkillOS ADR-023 と SPEC.md 引用を追加 (closes #69)** — SkillOS 論文（Ouyang et al., 2026, arXiv:2605.06614）を ADR-023 として記録し、frozen executor + trainable curator 設計の学術的根拠を確立。

### Fixed
- **`skill_quality.py` に `evaluate_skill_quality()` を追加し skill_quality 軸を修正 (closes #68)** — `environment.py` が呼ぶ `evaluate_skill_quality()` が存在せず、skill_quality 軸がサイレントに 0.0 になっていたバグを修正。
- **LSP suggestion の rglob PermissionError クラッシュを修正** — 除外判定を相対パスに変更、JS/TS 重複インストールコマンドを除去、壊れた `.lsp.json` を警告で区別。
- **`append_jsonl` に明示的 `LOCK_UN` を追加し TOCTOU を解消 (#158)**

## [1.52.1] - 2026-05-18

### Fixed
- **`scripts/lib/frontmatter.py` の `yaml.dump` に `sort_keys=False` を追加 (closes #154)** — `update_frontmatter` 呼び出しのたびに frontmatter キーがアルファベット順に reorder される問題を修正。`post_tool_use_memory` hook が毎 Edit/Write で `update_frontmatter` を呼ぶ実装になったことで顕在化 (PR #153 adversarial review F3)。

## [1.52.0] - 2026-05-18

### Changed
- **rl_common/ パッケージから JSONL 永続化 + 偽陽性管理を `persistence.py` + `false_positive.py` に分離し Phase 13 完了 (Phase 13 / Slice 4)** — `project_name_from_dir` / `extract_worktree_info` / `append_jsonl` (~39 行) を `persistence.py`、`message_hash` / `load_false_positives` / `add_false_positive` / `cleanup_false_positives` (~93 行) を `false_positive.py` に切り出し。`FALSE_POSITIVES_FILE` は `__init__.py` を SoT として保持し、`false_positive.py` は `import rl_common` 経由で関数本体内動的 lookup（`hooks/tests/conftest.py` の `mock.patch.object(rl_common, "FALSE_POSITIVES_FILE", ...)` 互換維持）。`__init__.py` は再エクスポート専用に整理し、後方互換維持。`__init__.py` は **201 → 108 行**（−93 行、累計 548 → 108、**−80%**）。**目標 ≤200 行を達成し rl_common/ パッケージ分割 Phase 13 完了**。最終構成: `__init__.py` (108) / `detection.py` (190) / `workflow.py` (119) / `false_positive.py` (93) / `checkpoint.py` (79) / `config.py` (66) / `persistence.py` (39) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。closes #28
- **telemetry_query/ パッケージから sessions / corrections / workflows + 汎用 DuckDB JSONL ローダを `sessions_corrections_workflows.py` に分離し Phase 11 完了 (Phase 11 / Slice 3)** — `query_sessions` / `_query_sessions_table` / `_duckdb_query_file` / `query_corrections` / `_filter_corrections_by_project` / `_duckdb_query_corrections` / `query_workflows` / `_duckdb_query_workflows` (~324 行) を切り出し。`_duckdb_query_file` は `usage_errors.py` 側からも利用するため `__init__.py` 経由で再エクスポートして共有（`usage_errors.py` の `from . import _duckdb_query_file` 互換維持）。submodule 各関数は `from . import HAS_DUCKDB, DATA_DIR` 関数内 lazy lookup で `mock.patch("telemetry_query.HAS_DUCKDB", False)` 互換、`duckdb` import も関数内に移動して HAS_DUCKDB=False テストでの不要 import を回避。`__init__.py` は再エクスポート専用に整理し、`json` / `defaultdict` / `Any` / `Dict` / `List` / `Optional` 等の未使用 import を全て除去（snapshot test green、test_telemetry_query / test_telemetry / test_quality_engine / test_telemetry_query_snapshot 計 75 件パス、scripts/tests + scripts/rl/tests 計 1334 件パス、5 件失敗は pre-existing で telemetry_query 無関係）。`__init__.py` は **337 → 61 行**（−276 行、累計 652 → 61、**−91%**）。**目標 ≤200 行を達成し telemetry_query/ パッケージ分割 Phase 11 完了**。最終構成: `__init__.py` (61) / `sessions_corrections_workflows.py` (324) / `usage_errors.py` (273) / `helpers.py` (100) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。closes #28
- **coherence/ パッケージから統合スコア + audit レポートフォーマットを `coherence/aggregation.py` に分離し Phase 10 完了 (Phase 10 / Slice 4)** — `compute_coherence_score` (4 軸の WEIGHTS 重み付き平均で overall + 軸別 details の dict を組成) + `format_coherence_report` (Environment Coherence Score ヘッダ + 軸別 0-20 ブロックバー + advice_threshold 未満時の `_summarize_issues` / `_build_advice` 詳細出力) + `_summarize_issues` (低スコア軸の概要 1 行) + `_build_advice` (軸別の改善アドバイス: skill_existence / memory_paths / trigger_duplicates / skill_quality / rule_compliance / claude_md_size / hardcoded_values / duplicate_skills / near_limit / unused_skills の 10 種を日本語化) (~161 行) を切り出し。`WEIGHTS` / `THRESHOLDS` は `_weights()` / `_thresholds()` ヘルパーで `from . import X` 関数内 lazy lookup（テストの `coherence.WEIGHTS` / `coherence.THRESHOLDS` monkeypatch 互換維持）。score 関数 (`score_coverage` / `score_consistency` / `score_completeness` / `score_efficiency`) は scoring_basic / scoring_advanced から module-level import。`__init__.py` から未使用 `json` / `os` / `re` / `sys` / `Counter` / `Optional` / `Tuple` import を除去。`__init__.py` は再エクスポートで `from coherence import compute_coherence_score, format_coherence_report, _summarize_issues, _build_advice` の後方互換維持（snapshot test green、`audit/orchestrator.py` の `from fitness.coherence import compute_coherence_score, format_coherence_report` 含む外部 importer 継続動作、test_coherence + test_chaos + test_constitutional 計 38 件パス）。`__init__.py` は **198 → 64 行**（−134 行、累計 737 → 64、**−91%**）。**目標 ≤200 行を大幅に達成し coherence/ パッケージ分割 Phase 10 完了**。最終構成: `__init__.py` (64) / `aggregation.py` (161) / `scoring_advanced.py` (257) / `scoring_basic.py` (201) / `artifacts.py` (158) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。closes #28
- **skill_evolve/ パッケージから自己進化適性判定 + 変換提案 + プロジェクトルート推定を `skill_evolve/assessment.py` + `skill_evolve/proposal.py` に分離し Phase 8 完了 (Phase 8 / Slice 4)** — `evolve_skill_proposal` (テンプレート不在チェック + LLM カスタマイズ + Pre-flight/Failure-triggered Learning 必須セクション検証 + フォールバック) + `_customize_template` (Claude CLI 経由のテンプレート文脈カスタマイズ + ``` コードブロック除去) + `apply_evolve_proposal` (バックアップ作成 + SKILL.md セクション追記 + references/pitfalls.md 作成) (~140 行) を `proposal.py` に、`_find_project_dir` (`.claude/skills/<name>/` の 2 階層上推定) + `skill_evolve_assessment` (audit.find_artifacts 経由の全カスタムスキル走査 + symlink/plugin 除外 + テレメトリ + LLM + アンチパターン判定 + 検証系バイパス) + `assess_single_skill` (1 スキル版 + workflow_checkpoint オプション統合) (~266 行) を `assessment.py` に切り出し。`_plugin_root` / `compute_telemetry_scores` / `compute_llm_scores` / `is_self_evolved_skill` / `is_verification_skill` / `classify_suitability` / `detect_anti_patterns` / `_customize_template` / `ANTI_PATTERN_REJECTION_COUNT` は `__init__.py` を SoT として `from . import X` 関数本体内 lazy lookup で参照し、`mock.patch("skill_evolve.compute_telemetry_scores")` / `mock.patch("skill_evolve.compute_llm_scores")` / `mock.patch("skill_evolve._customize_template")` / `mock.patch("skill_evolve._plugin_root", tmp_path)` 等の既存テストの monkeypatch 互換を維持。`__init__.py` は再エクスポートで `from skill_evolve import skill_evolve_assessment, assess_single_skill, evolve_skill_proposal, apply_evolve_proposal, _find_project_dir, _customize_template` の後方互換維持（snapshot test green、`test_skill_evolve.py` + `test_evolve_integration.py` + `test_skill_evolve_snapshot.py` + `test_pitfall_manager.py` 計 100 件 pass、`scripts/lib/remediation/fixers_rules.py::fix_skill_evolve` の `from skill_evolve import apply_evolve_proposal, evolve_skill_proposal` 含む外部 importer 継続動作）。`__init__.py` は **462 → 106 行**（−356 行、累計 754 → 106、**−86%**）。**目標 ≤200 行を達成し skill_evolve/ パッケージ分割 Phase 8 完了**。最終構成: `__init__.py` (106) / `assessment.py` (266) / `classification.py` (150) / `proposal.py` (140) / `llm_scoring.py` (119) / `telemetry_scoring.py` (92) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。closes #28
- **rl_common/ パッケージから correction / prompt detection を `detection.py` に分離 (Phase 13 / Slice 3)** — `PROMPT_CATEGORIES` (15 カテゴリ) + `classify_prompt` + `CORRECTION_PATTERNS` (23 パターン) + `FALSE_POSITIVE_FILTERS` + `_MAX_CAPTURE_PROMPT_LENGTH` / `_MIN_SHORT_CORRECTION_LENGTH` / `_SANITIZE_XML_TAGS` / `_CONTROL_CHAR_PATTERN` + `sanitize_message` (制御文字 + XML タグ除去 + 長さ制限) + `should_include_message` (`^remember:` 即承認 + tool_result/system-reminder 等の skip pattern) + `calculate_confidence` (matched_count + has_strong + has_i_told_you による 5 段階 + 文長補正) + `detect_correction` (FP filter → load_false_positives → CORRECTION_PATTERNS 走査) + `detect_all_patterns` (~190 行) を切り出し。`detect_correction` は `load_false_positives` / `message_hash` を `import rl_common as _root` 経由で関数本体内 lazy lookup（FALSE_POSITIVES_FILE は `__init__.py` に残置のため）。`__init__.py` は再エクスポートで `from rl_common import PROMPT_CATEGORIES, CORRECTION_PATTERNS, FALSE_POSITIVE_FILTERS, classify_prompt, sanitize_message, should_include_message, calculate_confidence, detect_correction, detect_all_patterns` の後方互換維持（snapshot test green、scripts/tests + hooks/tests 1593 件パス、`skills/reflect/scripts/reflect.py` の `from rl_common import CORRECTION_PATTERNS` 含む外部 importer 継続動作）。`__init__.py` は **361 → 201 行**（−160 行、累計 548 → 201）。残るは Slice 4 で persistence / false_positive 系を分離して **目標 ≤200 行** を達成予定。closes #28
- **telemetry_query/ パッケージから usage / errors / skill counts / skill-session 集計を `usage_errors.py` に分離 (Phase 11 / Slice 2)** — `query_usage` / `query_errors` / `query_skill_counts` / `_duckdb_skill_counts` / `query_usage_by_skill_session` / `_aggregate_skill_sessions` + `TRACE_WINDOW_MINUTES` 定数 (~273 行) を切り出し。submodule からは `from . import HAS_DUCKDB, DATA_DIR, _duckdb_query_file` で関数内 lazy lookup（テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` / `mock.patch("telemetry_query.DATA_DIR", ...)` 互換）。`_duckdb_skill_counts` 内の `duckdb` import も関数内に移動（HAS_DUCKDB=False テストで実行されない経路を保証）。`__init__.py` 側は `defaultdict` / `datetime` / `timezone` の不要 import を除去し、再エクスポートで `from telemetry_query import query_usage, query_errors, query_skill_counts, query_usage_by_skill_session, _duckdb_skill_counts, _aggregate_skill_sessions, TRACE_WINDOW_MINUTES` の後方互換維持（snapshot test green、test_telemetry_query / test_telemetry / test_quality_engine / test_telemetry_query_snapshot 計 75 件パス）。`__init__.py` は **577 → 337 行**（−240 行、累計 652 → 337）。closes #28
- **trigger_engine/ パッケージから self-evolution + pending trigger + skill 変更検出を `self_evolution.py` + `pending.py` に分離し Phase 9 完了 (Phase 9 / Slice 4)** — `_evaluate_self_evolution` (remediation-outcomes.jsonl 走査、`issue_type` 別 false positive 率 ≥ `false_positive_rate_threshold`=0.3 で発火、`self_evolution_cooldown_hours`=72) + `_evaluate_approval_rate_decline` (直近 `decline_sample_size`=10 件 vs 前 10 件の承認率差 ≥ `approval_rate_decline_threshold`=0.2 で発火) (~169 行) を `self_evolution.py` に、`write_pending_trigger` / `read_and_delete_pending_trigger` (スヌーズ中は配信しない、破損時は削除) / `snooze_trigger` (`trigger-snooze.json` 書き込み) / `clear_snooze` (evolve 実行時) / `_is_snoozed` (期限切れ自動削除) / `detect_skill_changes` (`git diff --name-only HEAD -- .claude/skills/*/SKILL.md`) (~115 行) を `pending.py` に切り出し。`DATA_DIR` / `PENDING_TRIGGER_FILE` / `SNOOZE_FILE` は `from . import X` 関数内 lazy lookup で `mock.patch("trigger_engine.X")` 経路継続動作。`__init__.py` は再エクスポート専用に整理し未使用 import (`json` / `datetime` / `timedelta` / `timezone` / `Any`) を除去、`from trigger_engine import write_pending_trigger, snooze_trigger, clear_snooze, detect_skill_changes, _evaluate_self_evolution, _evaluate_approval_rate_decline` 等の後方互換維持。`__init__.py` は **304 → 68 行**（−236 行、累計 751 → 68、**−91%**）。**目標 ≤200 行を達成し trigger_engine/ パッケージ分割 Phase 9 完了**。最終構成: `__init__.py` (68) / `session_corrections.py` (222) / `state.py` (195) / `self_evolution.py` (169) / `pending.py` (115) / `file_change.py` (83) / `bloat.py` (45) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。snapshot test green、scripts/tests + scripts/lib/tests + hooks/tests の trigger 系 139 件パス。closes #28
- **pipeline_reflector/ パッケージから調整提案生成 + 永続化 + audit 用 Pipeline Health セクション生成を `pipeline_reflector/proposals.py` に分離し Phase 12 完了 (Phase 12 / Slice 3)** — `generate_adjustment_proposals` (calibration delta + control_chart risk_level + regression override → confidence 調整提案リスト生成、|delta|<0.01 はスキップ、high/regression risk に `warning` 付与) + `record_proposal` (`pipeline-proposals.jsonl` に timestamp + status="pending" で append、dry_run 対応) + `update_proposal_status` (jsonl 内の最新 pending 提案の status を逆走査で更新) + `build_pipeline_health_section` (audit レポート用テーブル + DEGRADED 推奨、データ不足時は "データ不足" メッセージ) (~168 行) を切り出し。`record_proposal` / `update_proposal_status` は `import pipeline_reflector` で `DATA_DIR` / `PROPOSALS_FILE` を関数本体内動的 lookup（テストの `monkeypatch.setattr("pipeline_reflector.X", ...)` 互換維持）。`__init__.py` から残存 `json` / `datetime` / `Any` import を整理（パッケージ docstring に Phase 12 完了時の構成 4 ファイルを記載）。`__init__.py` は再エクスポートで `from pipeline_reflector import generate_adjustment_proposals, record_proposal, update_proposal_status, build_pipeline_health_section` の後方互換維持（snapshot test green、scripts/lib/tests + skills/evolve tests 37 件 + scripts/tests 1572 件パス、`audit/orchestrator.py` の `from pipeline_reflector import build_pipeline_health_section` / `skills/evolve/scripts/evolve.py` の各 import 含む外部 importer 継続動作）。`__init__.py` は **196 → 59 行**（−137 行、累計 595 → 59、**−90%**）。**目標 ≤200 行を達成し pipeline_reflector/ パッケージ分割 Phase 12 完了**。closes #28
- **coherence/ パッケージから Completeness / Efficiency 軸スコアリングを `coherence/scoring_advanced.py` に分離 (Phase 10 / Slice 3)** — `score_completeness` (Skill 行数 + 必須セクション [Usage/Steps] + Rule 3 行制約 + CLAUDE.md 200 行制約 + ハードコード値検出) + `score_efficiency` (重複 Skill / near-limit / 未使用 Skill [usage.jsonl ベース]) + `_get_used_skills` (~257 行) を切り出し。`THRESHOLDS` (テストの `coherence.THRESHOLDS` monkeypatch 互換のため) は `_thresholds()` ヘルパーで `from . import THRESHOLDS` 関数内 lazy lookup。`hardcoded_detector` / `audit (detect_duplicates_simple, LIMITS, NEAR_LIMIT_RATIO)` も元実装通り `_ensure_paths()` 後の lazy import。`__init__.py` は再エクスポートで `from coherence import score_completeness, score_efficiency, _get_used_skills` の後方互換維持（snapshot test green、38 件パス）。`__init__.py` は **422 → 198 行**（−224 行、累計 737 → 198、**−73%**）。**目標 ≤200 行を達成**（残り集約関数 `compute_coherence_score` / `format_coherence_report` / `_summarize_issues` / `_build_advice` は Slice 4 で `aggregation.py` に分離予定）。closes #28
- **trigger_engine/ パッケージから session-end + corrections 評価器を `session_corrections.py` に分離 (Phase 9 / Slice 3)** — `evaluate_session_end` (audit_overdue / session_count / days_elapsed / bloat の 4 種類を OR 評価、reason 別 cooldown、msg_parts で日本語メッセージ組成) (~120 行) + `evaluate_corrections` (corrections.jsonl 走査、`last_run_timestamp` 以降の件数 + skill 別 top3 抽出、閾値超過で `/rl-anything:optimize <skill>` 提案) (~70 行) を `session_corrections.py` に切り出し。`_evaluate_bloat` / `_build_bloat_message` / `DATA_DIR` は `from . import X` 関数内 lazy lookup で `mock.patch("trigger_engine._evaluate_bloat" / ".DATA_DIR")` 経路継続動作。`__init__.py` は再エクスポートで `from trigger_engine import evaluate_session_end, evaluate_corrections` の後方互換維持。`__init__.py` は **496 → 304 行**（−192 行、累計 751 → 304）。snapshot test green、trigger 系 139 件パス。closes #28
- **pipeline_reflector/ パッケージから EWA キャリブレーション + 統計的管理図 + 回帰チェックを `pipeline_reflector/calibration.py` に分離 (Phase 12 / Slice 2)** — `calibrate_confidence` (issue_type 別 EWA: α = min(sample/threshold, max_alpha), calibrated = α·observed + (1-α)·current) + `load_calibration` / `save_calibration` (`confidence-calibration.json` 永続化) + `check_control_chart` (μ ± 2σ 範囲外 delta に `risk_level: high`) + `check_calibration_regression` (auto_fixable 再分類で false-positive 増加検出) (~220 行) を切り出し。`load_calibration` / `save_calibration` は `import pipeline_reflector` で `DATA_DIR` / `CALIBRATION_FILE` を関数本体内動的 lookup（テストの `monkeypatch.setattr("pipeline_reflector.X", ...)` 互換維持）。`__init__.py` から未使用 `statistics` import 除去。`__init__.py` は再エクスポートで `from pipeline_reflector import calibrate_confidence, load_calibration, save_calibration, check_control_chart, check_calibration_regression` の後方互換維持（snapshot test green、scripts/lib/tests + skills/evolve tests 37 件パス、`audit/orchestrator.py` / `skills/evolve/scripts/evolve.py` の `from pipeline_reflector import X` 含む外部 importer 継続動作）。`__init__.py` は **386 → 196 行**（−190 行、累計 595 → 196、**−67%**、目標 ≤200 行を達成）。closes #28
- **`scripts/lib/telemetry_query.py` (652行) を `scripts/lib/telemetry_query/` パッケージ化し、共通ヘルパを `helpers.py` に分離 (Phase 11 / Slice 1)** — `git mv telemetry_query.py telemetry_query/__init__.py` でパッケージ化し、`_warn_no_duckdb` / `_load_jsonl` / `_filter_by_project` / `_filter_by_time` / `_build_time_where` / `_parse_ts` (~100 行) を `helpers.py` に切り出し。`HAS_DUCKDB` / `DATA_DIR` は `__init__.py` を SoT として残し、submodule は `from . import HAS_DUCKDB` で関数内 lazy lookup する設計（既存テストの `mock.patch("telemetry_query.HAS_DUCKDB", False)` 14 箇所互換）。`__init__.py` は再エクスポートで `from telemetry_query import _warn_no_duckdb, _load_jsonl, _filter_by_project, _filter_by_time, _build_time_where, _parse_ts` の後方互換維持（snapshot test green、test_telemetry_query / test_telemetry / test_quality_engine 計 75 件パス、`scripts/rl/tests/test_environment.py` 2 件は pre-existing）。`__init__.py` は **652 → 577 行**。closes #28
- **rl_common/ パッケージから checkpoint 管理 + workflow 文脈/スキルスタック/直前スキル管理を `checkpoint.py` + `workflow.py` に分離 (Phase 13 / Slice 2)** — `find_latest_checkpoint` (project_dir フィルタ + 旧 `DATA_DIR/checkpoint.json` フォールバック + timestamp 降順) + `_load_legacy_checkpoint` + `cleanup_old_checkpoints` (TTL 超過削除) (~79 行) を `checkpoint.py` に、`workflow_context_path` / `skill_stack_path` / `read_skill_stack` / `write_skill_stack` (アトミック書き込み + 空時 unlink) / `read_workflow_context` (24h TTL + 文脈ファイル null fallback) / `last_skill_path` / `write_last_skill` / `read_last_skill` + `_WORKFLOW_CONTEXT_EXPIRE_SECONDS` (~119 行) を `workflow.py` に切り出し。`checkpoint.py` の関数は `import rl_common as _root` で `CHECKPOINTS_DIR` / `DATA_DIR` / `CHECKPOINT_TTL_HOURS` を関数本体内 lazy lookup し、`hooks/tests/conftest.py` の `mock.patch.object(rl_common, "CHECKPOINTS_DIR", ...)` 経路の互換を維持。`workflow.py` は TMPDIR 配下の一時ファイルのみ扱うため DATA_DIR には依存しない。`__init__.py` は再エクスポートで `from rl_common import find_latest_checkpoint, _load_legacy_checkpoint, cleanup_old_checkpoints, workflow_context_path, skill_stack_path, read_skill_stack, write_skill_stack, read_workflow_context, last_skill_path, write_last_skill, read_last_skill` の後方互換維持（snapshot test green、`hooks/tests/test_hooks_*.py` 系含む scripts/tests 1593 件 + hooks/tests pass、pre-existing 失敗 2 件のみ）。`__init__.py` は **500 → 361 行**（−139 行、累計 548 → 361）。closes #28
- **trigger_engine/ パッケージから FileChanged 評価 + bloat 警告ヘルパーを `file_change.py` + `bloat.py` に分離 (Phase 9 / Slice 2)** — `is_watched_file` (claude_md / skills / rules カテゴリ分類) + `evaluate_file_changed` (FileChanged hook の audit 提案、`FILE_CHANGED_COOLDOWN_SECONDS`=5分カテゴリ別 cooldown、userConfig auto_trigger gate) (~83 行) を `file_change.py` に、`_evaluate_bloat` (`scripts.bloat_control.bloat_check` 薄ラッパー、ImportError/例外時 None) + `_build_bloat_message` (memory / claude_md / rules_count / skills_count / memory_bytes 警告を日本語化) (~45 行) を `bloat.py` に切り出し。`__init__.py` は再エクスポートで `from trigger_engine import evaluate_file_changed, is_watched_file, _evaluate_bloat, _build_bloat_message` の後方互換維持、`evaluate_session_end` 内の `_evaluate_bloat` / `_build_bloat_message` 参照は `__init__.py` 同一名前空間経由で `mock.patch("trigger_engine._evaluate_bloat")` 経路継続動作。`file_change.py` 内の `FILE_CHANGED_COOLDOWN_SECONDS` は `from . import FILE_CHANGED_COOLDOWN_SECONDS` 関数内 lazy lookup（テスト patch 追従）。`__init__.py` は **591 → 496 行**（−95 行、累計 751 → 496）。snapshot test green、trigger 系 139 件パス。closes #28
- **skill_evolve/ パッケージから自己進化済み判定 + 検証系判定 + 適性分類 + アンチパターン検出 + LLM スコアキャッシュヘルパを `skill_evolve/classification.py` に分離 (Phase 8 / Slice 3)** — `_file_hash` (SHA256 ハッシュ) + `_load_cache` / `_save_cache` (LLM スコアキャッシュ I/O) + `is_self_evolved_skill` (`references/pitfalls.md` 存在 + `Failure-triggered Learning` セクション照合) + `is_verification_skill` (`VERIFICATION_SKILL_KEYWORDS` をスキル名と SKILL.md 内容に対して走査、検証系スキルの low → medium 自動昇格を担当) + `classify_suitability` (`HIGH_SUITABILITY_THRESHOLD` / `MEDIUM_SUITABILITY_THRESHOLD` 比較で 3 段階分類) + `detect_anti_patterns` (Noise Collector / Context Bloat / Band-Aid 3 種、Band-Aid は `references/` ディレクトリ内の bullet 数を `BAND_AID_THRESHOLD` と比較) (~150 行) を切り出し。`CACHE_FILE` / `DATA_DIR` / `VERIFICATION_SKILL_KEYWORDS` / `HIGH_SUITABILITY_THRESHOLD` / `MEDIUM_SUITABILITY_THRESHOLD` / `BAND_AID_THRESHOLD` は `__init__.py` を SoT として `from . import X` 関数本体内 lazy lookup で参照し、`monkeypatch.setattr("skill_evolve.CACHE_FILE", ...)` 経路の互換を維持。`__init__.py` 側の不要となった `hashlib` / `json` import を除去。`__init__.py` は再エクスポートで `from skill_evolve import _file_hash, _load_cache, _save_cache, is_self_evolved_skill, is_verification_skill, classify_suitability, detect_anti_patterns` の後方互換維持（snapshot test green、`test_skill_evolve.py` + `test_evolve_integration.py` + `test_skill_evolve_snapshot.py` + `test_pitfall_manager.py` 計 100 件 pass、`pitfall_manager` package re-export (`is_self_evolved_skill` 等) も継続動作）。`__init__.py` は **584 → 462 行**（−122 行、累計 754 → 462）。closes #28
- **coherence/ パッケージから Coverage / Consistency 軸スコアリングを `coherence/scoring_basic.py` に分離 (Phase 10 / Slice 2)** — `_COVERAGE_ITEMS` (6 項目) + `score_coverage` (claude_md / rules / skills / memory / hooks / skills_section の存在チェック) + `score_consistency` (CLAUDE.md 言及スキル実在 + MEMORY.md パス参照実在 + トリガーワード重複) + `_extract_mentioned_skills` (Skills セクション内の `- skill-name:` パターン抽出) + `_check_memory_paths` (コードブロック除外しつつ拡張子付きパス参照を抽出して project_dir 配下の存在確認) + `_PATH_PATTERN` (~201 行) を切り出し。`__init__.py` は再エクスポートで `from coherence import _COVERAGE_ITEMS, score_coverage, score_consistency, _extract_mentioned_skills, _check_memory_paths, _PATH_PATTERN` の後方互換維持（snapshot test green、38 件パス）。`__init__.py` は **601 → 422 行**（−179 行、累計 737 → 422）。closes #28
- **`scripts/rl/fitness/coherence.py` (737行) を `scripts/rl/fitness/coherence/` パッケージに分割し、アーティファクト探索ヘルパーを `coherence/artifacts.py` に分離 (Phase 10 / Slice 1)** — `coherence.py` → `coherence/__init__.py` にパッケージ化したうえで、`_ensure_paths` (遅延 sys.path 追加) / `_is_plugin_project` / `_find_project_artifacts` (プラグイン構造対応のフルアーティファクト探索) / `_find_artifacts_local` (audit互換のプロジェクト限定探索) + `_plugin_root` (~158 行) を `artifacts.py` に切り出し。`__init__.py` は再エクスポートで `from coherence import _ensure_paths, _is_plugin_project, _find_project_artifacts, _find_artifacts_local, _plugin_root` の後方互換を維持（snapshot test green、`scripts/rl/tests/test_coherence.py` を `importlib.util.spec_from_file_location` 直読みから `from fitness import coherence` 通常 import に書き換え、`test_coherence_snapshot.py` + `test_coherence.py` + `test_chaos.py` + `test_constitutional.py` 計 38 件パス）。`__init__.py` は 737 → 601 行（−136 行）。closes #28

### Added
- **`hooks/post_tool_use_memory.py` — Edit/Write 後に `.claude/memory/*.md` の `update_count` を自動インクリメント (closes #151)** — arXiv:2605.12978 対策。SKILL.md Step 7.6 の LLM 手動インクリメントに依存せず hook 層で強制。`is_memory_file` が `.claude/` を含むパスかつ `memory/*.md` に一致するか判定。`parse_memory_temporal` + `update_frontmatter` で frontmatter を読み書き（bool/非 int は 0 に正規化後 +1）。hooks.json の PostToolUse に Edit・Write エントリを追加。二重インクリメント防止のため SKILL.md Step 7.6 の手動 +1 指示を削除し hook 自動化に更新。
- **`skills/reflect/SKILL.md` に `update_count` リセット手順を追加** — `update_count >= 3` の memory を根本から書き直す際の 4 ステップ手順（元 corrections 参照 → archive → 新規作成 → audit 確認）を Step 7.6 に追記。audit の `memory_heavy_update` issue を解消するフローを明文化。
- **`memory_temporal.py` / `audit/issues.py` に `update_count` guard を追加 (closes #97)** — arXiv:2605.12978 由来。`TEMPORAL_DEFAULTS["update_count"] = 0` 追加、`parse_memory_temporal` が int 正規化付きで読み取り（bool/負値/非 int は 0）。`collect_issues` で `update_count >= 3 (MEMORY_HEAVY_UPDATE_THRESHOLD)` を `memory_heavy_update` issue として検出。`skills/reflect/SKILL.md` Step 7.6 に memory 更新時の guard + ユーザー確認フローを追加。
- **`docs/research/` に 2026-05-15 daily report 由来の論文評価 3 件を追加** — `harnessing-agentic-evolution.md` (arXiv:2605.13821, evolve パイプラインと機構同等で新規実装不要)、`cognifold.md` (arXiv:2605.13438, 認知折り畳みメモリ、MEMORY.md > 80 エントリまで保留)、`faulty-updated-memories.md` (arXiv:2605.12978, LLM 自己更新メモリの劣化警告、memory `update_count` 追加を中採用候補として Issue 化推奨)。トリアージ表で 16 件中 3 件を深掘り対象として絞り込み、残り 13 件は不適合 / 既評価 / 低関連で保留。
- **`docs/tech-eval/triage-2026-05-15-medium-verdict.md`** — 2026-05-15 triage で 🔶 中 と判定した 5 件 (aidlc-workflows / cocoindex / Interpret Agent Behavior / Executable Multi-Hop RAG / RS-Claw) を grep ベースで再検証、4 件を低に降格、1 件 (RS-Claw) のみ中維持。直感ベース triage の精度向上記録。
- **`scripts/tests/test_telemetry_query_snapshot.py`** — telemetry_query リファクタのレグレッション防止 snapshot test を追加 (Phase 11 / Slice 0)。`telemetry_query` モジュールの公開関数/クラスシグネチャ + module-level constants の dump (`telemetry_query_api_surface.txt`) と internal helper シグネチャ dump (`telemetry_query_internal_surface.txt`、`mock.patch("telemetry_query.HAS_DUCKDB", False)` 等の SoT) を fixture 化。後続の Phase 11 (telemetry_query.py 652 行 → telemetry_query/ パッケージ分割、≤200 行目標) で外部 importer (audit / discover / evolve / quality_engine / hooks / scripts/rl/tests/test_telemetry / test_environment 等) が依存する `from telemetry_query import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_pipeline_reflector_snapshot.py`** — pipeline_reflector リファクタのレグレッション防止 snapshot test を追加 (Phase 12 / Slice 0)。`pipeline_reflector` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/pipeline_reflector_api_surface.txt`）。後続の Phase 12 (pipeline_reflector.py 595 行 → pipeline_reflector/ パッケージ分割、≤200 行目標) で外部 importer (`audit/orchestrator.py` / `skills/evolve/scripts/evolve.py` / `scripts/lib/tests/test_pipeline_reflector.py` 等) が依存する `from pipeline_reflector import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_trigger_engine_snapshot.py`** — trigger_engine リファクタのレグレッション防止 snapshot test を追加 (Phase 9 / Slice 0)。`trigger_engine` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/trigger_engine_api_surface.txt`）。後続の Phase 9 (trigger_engine.py 751 行 → trigger_engine/ パッケージ分割、≤200 行目標) で外部 importer (`hooks/session_summary.py` / `hooks/file_changed.py` / `hooks/correction_detect.py` / `hooks/instructions_loaded.py` / `hooks/restore_state.py` / `skills/evolve/scripts/evolve.py` / `scripts/lib/pipeline_reflector.py` 等) が依存する `from trigger_engine import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_rl_common_snapshot.py`** — rl_common リファクタのレグレッション防止 snapshot test を追加 (Phase 13 / Slice 0)。`rl_common` モジュールの公開関数シグネチャ + module-level 定数 (`USER_CONFIG_DEFAULTS` / `CORRECTION_PATTERNS` / `PROMPT_CATEGORIES` / `FALSE_POSITIVE_FILTERS` / TTL 系) の dump を fixture 化（`scripts/tests/fixtures/rl_common_api_surface.txt`、26 関数 + 7 定数）。後続の Phase 13 (rl_common.py 548 行 → rl_common/ パッケージ分割、≤200 行目標) で広範な外部 importer (hooks/ 全般 / `scripts/lib/audit` / `scripts/lib/fleet` / `skills/reflect` / `skills/handover` / 各種 conftest の `mock.patch.object(rl_common, ...)` 等) が依存する `from rl_common import X` / `import rl_common` 互換性を byte レベルで保証する。Path 型は環境依存のため除外。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_coherence_snapshot.py`** — coherence リファクタのレグレッション防止 snapshot test を追加 (Phase 10 / Slice 0)。`fitness.coherence` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/coherence_api_surface.txt`）。後続の Phase 10 (scripts/rl/fitness/coherence.py 737 行 → coherence/ パッケージ分割、≤200 行目標) で外部 importer (`audit/orchestrator.py` / `fitness/chaos.py` / `fitness/constitutional.py` / `scripts/rl/tests/test_coherence.py` 等) が依存する `from fitness.coherence import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_skill_evolve_snapshot.py`** — skill_evolve リファクタのレグレッション防止 snapshot test を追加 (Phase 8 / Slice 0)。`skill_evolve` モジュールの公開関数/クラスシグネチャ + module-level constants + テスト/`mock.patch` が依存する private 名 (`_file_hash`/`_load_cache`/`_save_cache`/`_score_*`/`_customize_template`/`_find_project_dir`/`_plugin_root`/`CACHE_FILE`/`DATA_DIR`) の dump を fixture 化（`scripts/tests/fixtures/skill_evolve_api_surface.txt`）。後続の Phase 8 (skill_evolve/ パッケージ分割、754 行 → ≤200 行目標) で外部 importer (`from skill_evolve import X` / `mock.patch("skill_evolve.X")` / `pitfall_manager` package re-export 等) の互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_verification_catalog_snapshot.py`** — verification_catalog リファクタのレグレッション防止 snapshot test を追加 (Phase 7 / Slice 0)。`lib.verification_catalog` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/verification_catalog_api_surface.txt`）。後続の Phase 7 (verification_catalog/ パッケージ分割、828 行 → ≤200 行目標) で外部 importer (discover/runner / workflow_checkpoint / scripts/tests/test_verification_catalog_* 等) が依存する `from lib.verification_catalog import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。closes #28
- **`scripts/tests/test_tool_usage_analyzer_snapshot.py`** — tool_usage_analyzer リファクタのレグレッション防止 snapshot test を追加 (Phase 6 / Slice 0)。`tool_usage_analyzer` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/tool_usage_analyzer_api_surface.txt`）。後続の Phase 6 (tool_usage_analyzer/ パッケージ分割、867 行 → ≤200 行目標) で外部 importer (scripts/tests / scripts/lib/discover / skills/evolve 等) が依存する `from tool_usage_analyzer import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。
- **`scripts/tests/test_pitfall_manager_snapshot.py`** — pitfall_manager リファクタのレグレッション防止 snapshot test を追加 (Phase 5 / Slice 0)。`pitfall_manager` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/pitfall_manager_api_surface.txt`、18 関数 + 15 定数）。後続の Phase 5 (pitfall_manager/ パッケージ分割、1230 行 → ≤200 行目標) で外部 importer (`from pitfall_manager import X`) の互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。
- **`docs/tech-eval/` 評価記録ディレクトリ** — `/tech-eval` 後の評価結果を `<slug>.md` として手動追記する慣習を導入。`README.md` で運用ガイド、初回適用例として `pageindex.md` (VectifyAI/PageIndex 不採用、再評価トリガー: ADR 100 本超) を同梱しテンプレ構造のリファレンスとする。3-4 件溜まり共通形が見えたら skill 化検討。

### Changed
- **`scripts/lib/rl_common.py` (548 行) を `scripts/lib/rl_common/` パッケージに分割し、userConfig (CC v2.1.83 manifest.userConfig) を `config.py` に分離 (Phase 13 / Slice 1)** — `git mv scripts/lib/rl_common.py scripts/lib/rl_common/__init__.py` で package 化したうえで、`USER_CONFIG_DEFAULTS` (10 項目: auto_trigger / evolve_interval_days / audit_interval_days / min_sessions / cooldown_hours / language / growth_display / cleanup_tmp_prefixes / slow_threshold_ms / subagent_warning_threshold) + `_USER_CONFIG_PREFIX` ("CLAUDE_PLUGIN_OPTION_") + `_parse_bool` + `load_user_config` (env override + bool/int 型キャスト + 不正値サイレント fallback) + `is_user_config_explicit` (~68 行) を `config.py` に切り出し。Slice 1 では DATA_DIR / CHECKPOINTS_DIR / FALSE_POSITIVES_FILE / `ensure_data_dir` 等は `__init__.py` に残置（`hooks/tests/conftest.py` の `mock.patch.object(rl_common, "DATA_DIR", ...)` 経路を維持するため、後続 Slice で patch 戦略を整理してから移動）。`__init__.py` は再エクスポートで `from rl_common import USER_CONFIG_DEFAULTS, _USER_CONFIG_PREFIX, _parse_bool, load_user_config, is_user_config_explicit` の後方互換維持（snapshot test green、`hooks/common.py` の `from rl_common import *` 経路継続動作）。`__init__.py` は **548 → 500 行**（−48 行）。closes #28
- **pipeline_reflector/ パッケージ化 + outcome 取り込み + 行動軌跡分析 + false-positive 検出 + 自然言語診断を `pipeline_reflector/outcomes.py` に分離 (Phase 12 / Slice 1)** — `scripts/lib/pipeline_reflector.py` (595 行) を `scripts/lib/pipeline_reflector/__init__.py` に `git mv` で package 化し、`DEFAULT_SELF_EVOLUTION_CONFIG` + `load_self_evolution_config` + `_load_state` + `load_outcomes` + `analyze_trajectory` + `detect_false_positives` + `_generate_diagnosis` (~244 行) を `outcomes.py` に切り出し。パス定数 (`DATA_DIR` / `OUTCOMES_FILE` / `CALIBRATION_FILE` / `PROPOSALS_FILE`) は `__init__.py` を SoT として保持し、`outcomes.py` は関数本体内 `import pipeline_reflector` で動的 lookup（テストの `monkeypatch.setattr("pipeline_reflector.X", ...)` 互換維持）。`__init__.py` は再エクスポートで `from pipeline_reflector import DEFAULT_SELF_EVOLUTION_CONFIG, analyze_trajectory, load_outcomes, ...` の後方互換維持（snapshot test green、scripts/lib/tests + skills/evolve tests 37 件 + scripts/tests 1570 件パス、`audit/orchestrator.py` / `skills/evolve/scripts/evolve.py` の `from pipeline_reflector import X` 含む外部 importer 継続動作）。`__init__.py` は **595 → 386 行**（−209 行、累計 595 → 386）。closes #28
- **trigger_engine/ パッケージから state / config / cooldown / session カウントを `state.py` に分離 (Phase 9 / Slice 1)** — `trigger_engine.py` 751 行を `git mv` で `trigger_engine/__init__.py` に変換し、`TriggerResult` (dataclass) + `_load_state` / `_save_state` / `load_trigger_config` / `_deep_merge` / `_is_in_cooldown` / `_record_trigger` / `_count_sessions_since` / `_load_user_config_with_explicit` + 内部定数 (`_MAX_HISTORY_ENTRIES` / `DEFAULT_TRIGGER_CONFIG` / `_FIRST_RUN_MIN_SESSIONS`) (~150 行) を `state.py` に切り出し。`__init__.py` は state.py から再エクスポートで `from trigger_engine import TriggerResult, load_trigger_config, _load_state` 等の後方互換維持。テスト patch 追従のため `state.py` 内の `_load_state` / `_save_state` は `from . import EVOLVE_STATE_FILE, DATA_DIR` で package 経由の遅延参照（discover/suppression と同パターン）。`__init__.py` は **751 → 591 行**（−160 行、累計 751 → 591）。snapshot test green、scripts/tests + scripts/lib/tests + hooks/tests の trigger 系 139 件パス。closes #28
- **skill_evolve/ パッケージから LLM 2軸スコアリングを `skill_evolve/llm_scoring.py` に分離 (Phase 8 / Slice 2)** — `_EXTERNAL_DEPENDENCY_KEYWORDS` (24 パターンの正規表現リスト: API/aws/s3/lambda/cdk/cloudformation/docker/k8s/http/fetch/curl/websearch/webfetch/mcp/slack/github/deploy/remote/cloud/sns/sqs/dynamodb/bedrock) + `_count_external_keywords` + `_score_external_dependency` (1-3 段階の外部依存度スコア) + `_score_judgment_complexity_llm` (Claude CLI 経由の LLM 評価 + if/else/場合/条件 等の分岐数フォールバック) + `compute_llm_scores` (`_file_hash` + キャッシュ照合 + 新規計算時 `_save_cache` で永続化) (~119 行) を切り出し。`compute_llm_scores` は `_file_hash` / `_load_cache` / `_save_cache` を `from . import` で関数本体内 lazy import し、`mock.patch("skill_evolve.CACHE_FILE", ...)` 経路の互換を維持。`__init__.py` 側の不要となった `datetime` / `timezone` / `timedelta` / `Set` / `Tuple` import を除去。`__init__.py` は再エクスポートで `from skill_evolve import _EXTERNAL_DEPENDENCY_KEYWORDS, _count_external_keywords, _score_external_dependency, _score_judgment_complexity_llm, compute_llm_scores` の後方互換維持（snapshot test green、`test_skill_evolve.py` + `test_evolve_integration.py` + `test_skill_evolve_snapshot.py` + `test_pitfall_manager.py` 計 100 件 pass、`mock.patch("skill_evolve.compute_llm_scores")` 経路継続動作）。`__init__.py` は **684 → 584 行**（−100 行、累計 754 → 584）。closes #28
- **`scripts/lib/skill_evolve.py` (754 行) を `scripts/lib/skill_evolve/` パッケージに分割し、テレメトリ3軸スコアリングを `telemetry_scoring.py` に分離 (Phase 8 / Slice 1)** — `skill_evolve.py` → `skill_evolve/__init__.py` にパッケージ化したうえで、`TELEMETRY_LOOKBACK_DAYS` 定数 + `_score_execution_frequency` (1-3 段階の頻度スコア) + `_score_failure_diversity` (ユニーク根本原因カテゴリ数) + `_score_output_evaluability` (成功率からの逆推定) + `compute_telemetry_scores` (telemetry_query.query_usage/query_errors 突合 → スキル名フィルタ + error_categories 抽出) (~92 行) を `telemetry_scoring.py` に切り出し。`telemetry_scoring.py` は `_plugin_root` を独自に再計算（`scripts/lib/skill_evolve/telemetry_scoring.py` → `scripts/`、`.parent.parent.parent`）し `telemetry_query` を関数本体内 lazy import で循環回避。`__init__.py` 側の `_plugin_root` も package 化に伴い `.parent.parent` → `.parent.parent.parent` に補正、未使用の `os` / `timedelta` / `Tuple` import を除去。`__init__.py` は再エクスポートで `from skill_evolve import TELEMETRY_LOOKBACK_DAYS, _score_execution_frequency, _score_failure_diversity, _score_output_evaluability, compute_telemetry_scores` の後方互換維持（snapshot test green、`test_skill_evolve.py` 44 件 + `test_evolve_integration.py` + `test_skill_evolve_snapshot.py` 1 件 = 45 件 pass、`mock.patch("skill_evolve.compute_telemetry_scores")` 経路継続動作、`pitfall_manager` package re-export も継続動作）。`__init__.py` は **754 → 684 行**（−70 行）。closes #28
- **pitfall_manager/ パッケージから pitfall_hygiene オーケストレータを `pitfall_manager/runner.py` に分離し Phase 5 完了 (Phase 5 / Slice 5)** — `pitfall_hygiene` (自己進化済みスキル全走査 + 卒業判定 + 統合済み判定 + Stale Knowledge ガード + Pre-flight スクリプト化候補 + Active 上限 + TTL アーカイブ + 行数ガード + 横断分析 + 合理化防止テーブル統合) (~287 行) を切り出し。`runner.py` は `detect_archive_candidates` / `detect_integration` / `parse_pitfalls` / `get_cold_tier` / `_compute_line_guard` / `suggest_preflight_script` / `generate_rationalization_table` をサブモジュールから直接 import、`audit.find_artifacts` / `telemetry_query` は関数内 lazy import で循環回避。`PREFLIGHT_MATURITY_RATIO` / `SCRIPTIFIABLE_CATEGORIES` は runner.py 内にも保持（`__init__.py` を SoT として再エクスポート維持）。`__init__.py` は再エクスポート専用に整理し未使用 import (`json` / `re` / `shutil` / `Counter` / `datetime` / `timedelta` / `Tuple` / `Set` 等) を除去、`from pitfall_manager import pitfall_hygiene` の後方互換維持（snapshot test green、57 件 + scripts/tests 1223 件パス、`skills/evolve/scripts/evolve.py` の `from pitfall_manager import pitfall_hygiene as run_pitfall_hygiene` 含む外部 importer 継続動作）。`__init__.py` は **355 → 94 行**（−261 行、累計 1230 → 94、**−92%**）。**目標 ≤200 行を達成し pitfall_manager/ パッケージ分割 Phase 5 完了**。最終構成: `__init__.py` (94) / `detection.py` (356) / `runner.py` (287) / `recording.py` (215) / `rationalization.py` (152) / `parser.py` (138) / `preflight.py` (120) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。
- **pitfall_manager/ パッケージから行数ガード + Pre-flight スクリプト提案 + 合理化防止テーブルを `pitfall_manager/preflight.py` + `pitfall_manager/rationalization.py` に分離 (Phase 5 / Slice 4)** — `_compute_line_guard` (PITFALL_MAX_LINES 超過時に Cold 層から削除候補生成) + `_CATEGORY_TEMPLATE_MAP` (action/tool_use/output → .sh) + `suggest_preflight_script` (Pre-flight 対応 Active pitfall にテンプレートパス提案、generic.sh フォールバック) (~120 行) を `preflight.py` に、`detect_rationalization_patterns` (corrections の RATIONALIZATION_SKIP_KEYWORDS マッチで言い訳をグルーピング) + `generate_rationalization_table` (テレメトリ突合で前後 OUTCOME_WINDOW_DAYS のエラー率算出 + 既存 pitfall との Jaccard 重複エンリッチ) (~152 行) を `rationalization.py` に切り出し。`preflight.py` は `_plugin_root` を独自に再計算（`scripts/lib/pitfall_manager/preflight.py` → `scripts/`）。`__init__.py` は再エクスポートで `from pitfall_manager import _compute_line_guard, _CATEGORY_TEMPLATE_MAP, suggest_preflight_script, detect_rationalization_patterns, generate_rationalization_table` の後方互換維持（snapshot test green、57 件パス、`pitfall_hygiene` 内の `_compute_line_guard` / `suggest_preflight_script` / `generate_rationalization_table` 参照は再エクスポート経由で継続動作）。`__init__.py` は 592 → 355 行（−237 行、累計 1230 → 355）。
- **verification_catalog/ パッケージから advanced detectors (happy-path / cross-layer / IaC) + dispatch + 公開 API ランナーを `detectors_advanced.py` + `runner.py` に分離し Phase 7 完了 (Phase 7 / Slice 3)** — `_PIPELINE_CALL_PATTERN_PY/TS` / `_PIPELINE_LOOP_PATTERN` / `_PY_FUNC_DEF` / `_TS_FUNC_DEF` / `_MIN_PIPELINE_CALLS` / `_detect_pipeline_functions` / `_find_test_files` / `_test_has_function_call` / `detect_happy_path_test_gap` + クロスレイヤー regex (`_ENV_VAR_PY/TS_RE` / `_AWS_SDK_PY/TS_RE` / `_IAC_MARKERS`) + `detect_iac_project` / `detect_cross_layer_consistency` (~334 行) を `detectors_advanced.py` に、`_DETECTION_FN_DISPATCH` (5 関数) + `_run_detection_fn` (signal.alarm タイムアウト) + content-aware キーワード (`_SIDE_EFFECT_CONTENT_KEYWORDS` / `_EVIDENCE_CONTENT_KEYWORDS` / `_CROSS_LAYER_CONTENT_KEYWORDS` / `_HAPPY_PATH_CONTENT_KEYWORDS` / `_CONTENT_KEYWORDS_MAP`) + `check_verification_installed` / `get_rule_template` / `detect_verification_needs` (~148 行) を `runner.py` に切り出し。閾値定数 (`HAPPY_PATH_MIN_PATTERNS` / `MIN_CROSS_LAYER_PATTERNS` / `DETECTION_TIMEOUT_SECONDS`) と `VERIFICATION_CATALOG` は `__init__.py` を SoT として `from . import X` 関数内 lazy lookup（テストの monkeypatch 互換）。`__init__.py` は再エクスポートで `from lib.verification_catalog import detect_iac_project, detect_verification_needs, VERIFICATION_CATALOG, _DETECTION_FN_DISPATCH, _CONTENT_KEYWORDS_MAP, _detect_pipeline_functions, _find_test_files` 等の後方互換維持（snapshot test green、150 件パス）。`__init__.py` は **547 → 147 行**（−400 行、累計 828 → 147、**−82%**）。**目標 ≤200 行を達成し verification_catalog/ パッケージ分割 Phase 7 完了**。最終構成: `__init__.py` (147) / `detectors_advanced.py` (334) / `detectors_basic.py` (205) / `runner.py` (148) / `helpers.py` (108) / `templates.py` (59) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。closes #28
- **tool_usage_analyzer/ パッケージから rule/hook 候補生成 + 導入確認を `tool_usage_analyzer/codegen.py` + `tool_usage_analyzer/install_check.py` に分離し Phase 6 完了 (Phase 6 / Slice 3)** — `generate_rule_candidates` (avoid-bash-builtin.md global rule 候補) + `_HOOK_TEMPLATE` (PreToolUse hook テンプレート文字列) + `generate_hook_template` (hook スクリプト + settings.json 差分案生成) (~205 行) を `codegen.py` に、`check_artifact_installed` (汎用 hook_path/rule/content_pattern 確認) + `check_hook_installed` (check-bash-builtin.py + settings.json 登録確認) (~110 行) を `install_check.py` に切り出し。`codegen.py` は `GLOBAL_RULES_DIR` / `GLOBAL_HOOKS_DIR` / `LEGITIMATE_COMMAND_PATTERNS` を `from . import` で関数本体内 lazy import、`install_check.py` も `GLOBAL_HOOKS_DIR` を遅延参照。未使用となった `re` / `time` / `defaultdict` / `Tuple` import を `__init__.py` から除去。`__init__.py` は再エクスポートで `from tool_usage_analyzer import generate_rule_candidates, generate_hook_template, _HOOK_TEMPLATE, check_artifact_installed, check_hook_installed` の後方互換維持（snapshot test green、139 件パス）。`__init__.py` は **458 → 169 行**（−289 行、累計 867 → 169、**−81%**）。**目標 ≤200 行を達成し tool_usage_analyzer/ パッケージ分割 Phase 6 完了**。最終構成: `__init__.py` (169) / `codegen.py` (205) / `classify.py` (160) / `stall.py` (160) / `session_io.py` (154) / `install_check.py` (110) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。
- **pitfall_manager/ パッケージから Root-cause キーワード抽出 + 統合済み判定 + corrections/errors 自動抽出 + TTL アーカイブを `pitfall_manager/detection.py` に分離 (Phase 5 / Slice 3)** — `_STOP_WORDS` (frozenset 47件、日本語助詞+英語冠詞) + `extract_root_cause_keywords` (em dash 分割 + ストップワード除外) + `_split_sections_from_content` (Markdown ## 単位分割、YAML frontmatter 除外) + `detect_integration` (SKILL.md / references/ との Jaccard 突合 + integration_target 推定) + `extract_pitfall_candidates` (corrections.jsonl の stop/iya + errors.jsonl の頻出パターン → Candidate 抽出 + Occurrence-count 増分) + `detect_archive_candidates` (Graduated TTL + Active/New stale escalation) + `execute_archive` (~356 行) を切り出し。`__init__.py` は再エクスポートで `from pitfall_manager import _STOP_WORDS, extract_root_cause_keywords, _split_sections_from_content, detect_integration, extract_pitfall_candidates, detect_archive_candidates, execute_archive` の後方互換維持（snapshot test green、57 件 + scripts/tests 1223 件パス、`pitfall_hygiene` 内の `detect_integration` / `detect_archive_candidates` 参照は再エクスポート経由で継続動作）。`__init__.py` は 915 → 592 行（−323 行、累計 1230 → 592）。
- **verification_catalog/ パッケージから基本検出関数 3 種 (data-contract / side-effect / evidence) を `verification_catalog/detectors_basic.py` に分離 (Phase 7 / Slice 2)** — `detect_data_contract_verification` (モジュール間 dict 変換パターン) / `detect_side_effect_verification` (DB / MQ / 外部API アクセス) / `detect_evidence_verification` (corrections.jsonl 内の証拠要求パターン) + `_EVIDENCE_REQUEST_PATTERNS` regex (~205 行) を切り出し。`__init__.py` は再エクスポートで `from lib.verification_catalog import detect_data_contract_verification, detect_side_effect_verification, detect_evidence_verification, _EVIDENCE_REQUEST_PATTERNS` の後方互換維持（snapshot test green、150 件パス、`_DETECTION_FN_DISPATCH` の再エクスポート関数参照経路継続動作）。閾値定数 (`DATA_CONTRACT_MIN_PATTERNS` / `SIDE_EFFECT_MIN_PATTERNS` / `EVIDENCE_MIN_PATTERNS`) は `__init__.py` を SoT として `from . import X` 関数内 lazy lookup で参照（テスト側の `monkeypatch.setattr(verification_catalog, "DATA_CONTRACT_MIN_PATTERNS", ...)` 互換）。`detect_evidence_verification` の `_lib_dir` 計算は package 化で `scripts/lib/verification_catalog/` → `scripts/lib/` に補正（`.parent.parent`）。`__init__.py` は 709 → 547 行（−162 行、累計 828 → 547）。closes #28
- **tool_usage_analyzer/ パッケージから Bash コマンド分類を `tool_usage_analyzer/classify.py` に分離 (Phase 6 / Slice 2)** — `_is_cat_replaceable` (heredoc/redirect 除外) + `_get_command_head` (env/sudo スキップ + 先頭語抽出) + `classify_bash_commands` (builtin_replaceable / cli_legitimate 仕分け) + `_get_command_key` (先頭語+サブコマンド) + `detect_repeating_commands` (閾値以上のパターン抽出) + `_classify_subcategory` (vcs/github/package_manager/cloud 等) (~140 行) を切り出し。`classify_bash_commands` は `BUILTIN_REPLACEABLE_MAP` を `from . import` で関数本体内 lazy import。`__init__.py` は再エクスポートで `from tool_usage_analyzer import classify_bash_commands, detect_repeating_commands, _get_command_head` 等の後方互換維持（snapshot test green、67 件パス）。`__init__.py` は 597 → 458 行（−139 行）。
- **pitfall_manager/ パッケージから品質ゲート + 状態機械 (Candidate→New→Active→Graduated) を `pitfall_manager/recording.py` に分離 (Phase 5 / Slice 2)** — `find_matching_candidate` (Jaccard マッチ) + `record_pitfall` (Candidate→New 2段階昇格 / `is_user_correction=True` で即 Active) + `promote_to_active` (New→Active) + `graduate_pitfall` (Active→Graduated) + `_make_pitfall_entry` (status 別テンプレート生成) + `_safe_read` (破損時バックアップ+再作成) + `_write_empty_template` (~215 行) を切り出し。`__init__.py` は再エクスポートで `from pitfall_manager import find_matching_candidate, record_pitfall, promote_to_active, graduate_pitfall, _make_pitfall_entry, _safe_read, _write_empty_template` の後方互換維持（snapshot test green、56 + 1 件パス + `test_instruction_compliance_e2e.py` の `from pitfall_manager import record_pitfall` 含む外部 importer 継続動作）。`__init__.py` は 1108 → 915 行（−193 行、累計 1230 → 915）。
- **`scripts/lib/verification_catalog.py` (828行) を `scripts/lib/verification_catalog/` パッケージに分割し、共通ヘルパー + ルールテンプレートを `helpers.py` + `templates.py` に分離 (Phase 7 / Slice 1)** — `verification_catalog.py` → `verification_catalog/__init__.py` にパッケージ化したうえで、`_safe_result` / `_detect_primary_language` / `_iter_source_files` / `_is_test_file` / `_has_cross_module_pattern` + 走査制御定数 (`EXCLUDE_DIRS` / `PRIORITY_DIRS` / `LARGE_REPO_FILE_THRESHOLD`) + `_PY_IMPORT_RE` / `_PY_DICT_BUILD_RE` / `_TS_IMPORT_RE` / `_TS_OBJ_BUILD_RE` を `helpers.py` (~108 行)、`_PYTHON_RULE_TEMPLATE` / `_TYPESCRIPT_RULE_TEMPLATE` / `_SIDE_EFFECT_RULE_TEMPLATE` / `_EVIDENCE_RULE_TEMPLATE` / `_CROSS_LAYER_RULE_TEMPLATE` / `_HAPPY_PATH_RULE_TEMPLATE` + 副作用検出 regex (`_SIDE_EFFECT_DB_PATTERNS` / `_SIDE_EFFECT_MQ_PATTERNS` / `_SIDE_EFFECT_API_PATTERNS` / `_SIDE_EFFECT_CATEGORIES`) + テストファイル除外パターン (`_TEST_FILE_PATTERNS` / `_TEST_DIR_NAMES`) を `templates.py` (~59 行) に切り出し。`__init__.py` は再エクスポートで `from lib.verification_catalog import _detect_primary_language, _iter_source_files, _is_test_file, _has_cross_module_pattern, _SIDE_EFFECT_CATEGORIES` 等の後方互換維持（snapshot test green、150 件パス、`scripts/tests/test_verification_catalog_helpers.py` の private member import 含む既存テスト継続動作）。`__init__.py` は 828 → 709 行（−119 行）。closes #28
- **`scripts/lib/pitfall_manager.py` (1230行) を `scripts/lib/pitfall_manager/` パッケージに分割し、markdown パーサ + 3層コンテキスト分類を `pitfall_manager/parser.py` に分離 (Phase 5 / Slice 1)** — `pitfall_manager.py` → `pitfall_manager/__init__.py` にパッケージ化したうえで、`_PITFALL_HEADER_RE` / `_FIELD_RE` / `parse_pitfalls` / `_flush_item` / `render_pitfalls` + 3層コンテキスト (`get_hot_tier` / `get_warm_tier` / `get_cold_tier`) (~138 行) を `parser.py` に切り出し。`__init__.py` は再エクスポートで `from pitfall_manager import parse_pitfalls, render_pitfalls, get_hot_tier, get_warm_tier, get_cold_tier, _FIELD_RE, _PITFALL_HEADER_RE, _flush_item` の後方互換維持（snapshot test green、56 + 1 件パス）。`_plugin_root` 算出は package 化に伴い `.parent.parent` → `.parent.parent.parent` に補正（`scripts/` を維持）。`__init__.py` は 1230 → 1108 行（−122 行）。
- **`scripts/lib/tool_usage_analyzer.py` (867行) を `scripts/lib/tool_usage_analyzer/` パッケージに分割し、セッション JSONL からのツール抽出を `tool_usage_analyzer/session_io.py` に、停滞→リカバリ検出を `tool_usage_analyzer/stall.py` に分離 (Phase 6 / Slice 1)** — `_resolve_session_dir` / `extract_tool_calls` / `extract_tool_calls_by_session` (~140 行) を `session_io.py` に、`_classify_stall_step` / `_detect_stall_in_session` / `detect_stall_recovery_patterns` / `stall_pattern_to_pitfall_candidate` (~140 行) を `stall.py` に切り出し。`stall.py` は `_get_command_head` / `_get_command_key` / `LONG_COMMAND_PATTERNS` / `INVESTIGATION_COMMANDS` / `RECOVERY_COMMANDS` / `STALL_RECOVERY_MIN_SESSIONS` を `from . import X` で関数本体内 lazy import して循環回避（mock.patch 互換も維持）。`session_io.py` も `CLAUDE_PROJECTS_DIR` を package 経由の遅延参照。`__init__.py` は再エクスポートで `from tool_usage_analyzer import extract_tool_calls, detect_stall_recovery_patterns` 等の後方互換維持（snapshot test green、67 件パス）。`__init__.py` は 867 → 597 行（−270 行）。
- **prune/ パッケージから drift 評価 + run_prune オーケストレータを `prune/drift.py` + `prune/runner.py` に分離し Phase 4 完了 (Phase 4 / Slice 7)** — `_gather_drift_context` / `detect_reference_drift` / `_evaluate_drift` (~93 行) を `drift.py` に、`run_prune` + CLI `main` (~76 行) を `runner.py` に切り出し。`detect_reference_drift` は `_evaluate_drift` を `from . import _evaluate_drift` で package 経由の遅延参照（`mock.patch("prune._evaluate_drift", ...)` 既存テスト追従）。`run_prune` は各検出関数 (`detect_dead_globs` / `detect_zero_invocations` / `safe_global_check` / `detect_duplicates` / `detect_decay_candidates` / `detect_reference_drift` / `find_artifacts` / `cleanup_corrections` / `merge_duplicates`) を `from . import X` で全て遅延参照。`main` は public API ではないため再エクスポートしない（snapshot 互換維持、`__init__.py` の `if __name__` ブロックから `from .runner import main` で参照）。`__init__.py` は再エクスポートで `from prune import detect_reference_drift, run_prune` 等の後方互換維持（snapshot test green、84 件パス）。`__init__.py` は **262 → 147 行**（−115 行、累計 1411 → 147、**−90%**）。**目標 ≤200 行を達成し prune/ パッケージ分割 Phase 4 完了**。最終構成: `__init__.py` (147) / `archive.py` (372) / `detection.py` (346) / `skill_inspect.py` (237) / `dependency.py` (234) / `corrections.py` (112) / `drift.py` (93) / `runner.py` (76) / `config.py` (57) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。
- **prune/ パッケージから archive 操作 + 重複マージ提案を `prune/archive.py` に分離 (Phase 4 / Slice 6)** — `archive_file` (skill ディレクトリ依存検査 + タイムスタンプ付与 + meta.json 保存) + `restore_file` (meta.json から original_path 復元) + `list_archive` (meta.json glob + 降順 sort) + `determine_primary` (usage_count 比較で primary/secondary 判定) + `merge_duplicates` (duplicate_candidates + reorganize_merge_groups → pin/plugin/suppression フィルタ → proposed/skipped_*/interactive_candidate 生成) (~372 行) を切り出し。`__init__.py` は再エクスポートで `from prune import archive_file, restore_file, list_archive, determine_primary, merge_duplicates` の後方互換維持（snapshot test green、test_prune_dep_check の `monkeypatch.setattr(prune, "ARCHIVE_DIR", ...)` 含む 91 件パス）。`ARCHIVE_DIR` は `from . import ARCHIVE_DIR` で関数内 lazy 参照、`filter_merge_group_pairs` も `from . import filter_merge_group_pairs` で `mock.patch("prune.filter_merge_group_pairs", ...)` 既存テスト追従。`__init__.py` は 585 → 262 行（−323 行）。
- **prune/ パッケージから skill 依存検査 (import / path ref) を `prune/dependency.py` に分離 (Phase 4 / Slice 5)** — `SkillDependencyError` (Issue #25 対策の例外) + `_IMPORT_RE_TEMPLATE` + `_list_skill_module_names` (scripts/ 配下の Python モジュール抽出) + `_git_grep_files` (PCRE git grep + fallback None) + `_is_git_repo` + `_iter_text_files` (skip __pycache__/.git/node_modules/archive、text suffix フィルタ) + `_python_grep_files_per_module` (alternation O(files) 圧縮) + `_python_grep_files` + `_is_excluded_referrer` + `check_import_dependencies` (skill ディレクトリの外部 import / path 参照検出) (~234 行) を切り出し。`__init__.py` は再エクスポートで `from prune import check_import_dependencies, SkillDependencyError, _git_grep_files` 等の後方互換維持（snapshot test green、`scripts/tests/test_prune_dep_check.py` 含む 91 件パス）。`__init__.py` は 795 → 585 行（−210 行）。
- **prune/ パッケージから dead glob / zero invocation / global safe / duplicate / decay 検出を `prune/detection.py` に分離 (Phase 4 / Slice 4)** — `_expand_glob_pattern` (ブレース+カンマ展開) / `detect_dead_globs` (rules paths/globs マッチ無し検出) / `detect_zero_invocations` (usage.jsonl 未使用 + plugin_unused 分離) / `safe_global_check` (skill_activations.jsonl 優先 + usage-registry.jsonl フォールバック) / `detect_duplicates` (semantic_similarity_check 委譲) / `detect_decay_candidates` (decay スコア閾値判定) (~346 行) を切り出し。`__init__.py` は再エクスポートで `from prune import detect_dead_globs, detect_zero_invocations, safe_global_check, detect_duplicates, detect_decay_candidates, _expand_glob_pattern` の後方互換維持（snapshot test green、prune skill 83 件 + e2e correction flow 6 件パス）。`load_corrections` / `is_pinned` / `is_reference_skill` / `compute_decay_score` / `_enrich_candidate` 等の他モジュール関数は module-top で直接 import（mock.patch("prune.X", ...) 対象外を確認済み）。`__init__.py` は 1091 → 795 行（−296 行）。
- **prune/ パッケージから corrections.jsonl 操作を `prune/corrections.py` に分離 (Phase 4 / Slice 3)** — `load_corrections` (skill 別グループ化 + 後方互換フィールド補完) + `cleanup_corrections` (decay_days 超過の applied/skipped レコード削除、pending は保持) (~112 行) を切り出し。`__init__.py` は再エクスポートで `from prune import load_corrections, cleanup_corrections` の後方互換維持（snapshot test green、prune/decay 91 件パス、`hooks/tests/test_e2e_correction_flow.py` の `prune.load_corrections` / `skills/prune/scripts/tests/test_prune.py` の `prune.cleanup_corrections` 含む既存テスト継続動作）。`DATA_DIR` は `from . import DATA_DIR` で package 経由の遅延参照（`mock.patch("prune.DATA_DIR", ...)` 既存テスト追従）。`__init__.py` は 1178 → 1091 行（−87 行）。
- **prune/ パッケージからスキル個別検査ヘルパ群を `prune/skill_inspect.py` に分離 (Phase 4 / Slice 2)** — frontmatter 解析 (`_count_triggers` / `extract_skill_summary` / `_resolve_skill_md`) + キーワード/トリガー数ベースの推薦 (`_ARCHIVE_KEYWORDS` / `_KEEP_KEYWORDS` / `_KEEP_TRIGGER_THRESHOLD` / `suggest_recommendation` / `_enrich_candidate`) + 参照型判定 + 推定キャッシュ (`is_reference_skill` / `_estimate_skill_type` / `_load_skill_type_cache` / `_save_skill_type_cache`) + 減衰スコア / pin / skill ディレクトリ判定 (`compute_decay_score` / `is_pinned` / `_is_skill_dir`) (~237 行) を切り出し。`__init__.py` は再エクスポートで `from prune import is_reference_skill, compute_decay_score, is_pinned` 等の後方互換維持（snapshot test green、外部 importer (`skills/prune/scripts/tests/test_prune.py` の `mock.patch("prune._estimate_skill_type", ...)` / `hooks/tests/test_e2e_correction_flow.py` の `prune.compute_decay_score`/`prune.is_pinned` 含む) 継続動作、prune/decay 91 件パス）。`DATA_DIR` と `_estimate_skill_type` は `from . import X` で package 経由の遅延参照（`mock.patch("prune.DATA_DIR", ...)` / `mock.patch("prune._estimate_skill_type", ...)` 既存テスト追従）。`__init__.py` は 1365 → 1178 行（−187 行）。
- **`scripts/lib/prune.py` (1411行) を `scripts/lib/prune/` パッケージに分割し、閾値定数 + evolve-state.json ロードを `prune/config.py` に分離 (Phase 4 / Slice 1)** — `prune.py` → `prune/__init__.py` にパッケージ化したうえで、`DEFAULT_DECAY_DAYS` / `DEFAULT_DECAY_THRESHOLD` / `CORRECTION_PENALTY` / `ZERO_INVOCATION_DAYS` / `DEFAULT_MERGE_SIMILARITY_THRESHOLD` / `DEFAULT_INTERACTIVE_MERGE_THRESHOLD` / `DEFAULT_DRIFT_THRESHOLD` の閾値定数 7 個 + `load_merge_similarity_threshold` / `load_interactive_merge_threshold` / `load_decay_threshold` / `load_drift_threshold` の 4 ローダ (~60 行) を切り出し。4 ローダは `_load_state_value` 共通ヘルパに集約し DRY 化（旧版は 4 関数で同じ try/except 構造を重複）。`__init__.py` は再エクスポートで `from prune import DEFAULT_DECAY_DAYS, load_decay_threshold` 等の後方互換維持（snapshot test green、103 件パス）。`DATA_DIR` は `from . import DATA_DIR` で package 経由の遅延参照（`mock.patch.object(prune, "DATA_DIR", ...)` 既存テスト追従）。`__init__.py` は 1411 → 1365 行（−46 行）。

### Added
- **`scripts/tests/test_prune_snapshot.py`** — prune リファクタのレグレッション防止 snapshot test を追加 (Phase 4 / Slice 0)。`prune` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/prune_api_surface.txt`）。後続の Phase 4 (prune/ パッケージ分割、1411 行 → ≤200 行目標) で外部 importer (hooks/tests / scripts/tests / skills/* 等) が依存する `from prune import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。

### Changed
- **remediation/ パッケージから検証エンジン + VERIFY_DISPATCH + check_regression / rollback_fix / record_outcome を `remediation/verify.py` に分離し Phase 3 完了 (Phase 3 / Slice 7)** — `_verify_*` 17 個 (`_verify_stale_ref` / `_verify_line_limit_violation` / `_verify_stale_rule` / `_verify_claudemd_phantom_ref` / `_verify_claudemd_missing_section` / `_verify_stale_memory` / `_verify_global_rule` / `_verify_hook_scaffold` / `_verify_untagged_reference` / `_verify_skill_evolve` / `_verify_verification_rule` / `_verify_pitfall_archive` / `_verify_split_candidate` / `_verify_preflight_scriptification` / `_verify_workflow_checkpoint` / `_verify_skill_quality_pattern_gap` / `_verify_instruction_violation`) + `verify_fix` (VERIFY_DISPATCH ルックアップ) + `check_regression` (見出し / フェンス / 空ファイル / rule 行数の副作用検出) + `rollback_fix` + `record_outcome` (remediation-outcomes.jsonl 記録) (~370 行) を切り出し。`VERIFY_DISPATCH` は `_build_verify_dispatch()` で fixers_quality の `_verify_missing_effort` を package 経由で遅延参照して構築。`__init__.py` は再エクスポートで `from remediation import verify_fix, check_regression, record_outcome, VERIFY_DISPATCH` の後方互換維持（snapshot test green、外部 importer (`skills/evolve/scripts/tests/test_evolve_report_improvements.py` の `verify_fix`/`VERIFY_DISPATCH` import 含む) 継続動作）。`__init__.py` は **557 → 198 行**（−359 行、累計 2364 → 198、**−92%**）。**目標 ≤200 行を達成し remediation/ パッケージ分割 Phase 3 完了**。最終構成: `__init__.py` (198) / `fixers_rules.py` (476) / `fixers_quality.py` (443) / `fixers_basic.py` (373) / `verify.py` (367) / `confidence.py` (305) / `rationale.py` (192) / `principles.py` (181) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。
- **remediation/ パッケージから quality 系 fix 関数 + FIX_DISPATCH + generate_proposals を `remediation/fixers_quality.py` に分離 (Phase 3 / Slice 6)** — `fix_split_candidate` / `fix_preflight_scriptification` / `fix_workflow_checkpoint` / `fix_skill_quality_pattern_gap` / `fix_missing_effort` / `_verify_missing_effort` / `fix_instruction_violation` / `generate_proposals` (~390 行) を切り出し。`FIX_DISPATCH` は他 slice の fix 関数を package 経由で遅延参照する `_build_fix_dispatch()` で構築（`__init__.py` で `FIX_DISPATCH = _build_fix_dispatch()`）。`__init__.py` は再エクスポートで `from remediation import FIX_DISPATCH, fix_split_candidate, generate_proposals` 等の後方互換維持（snapshot test green、外部 importer (skills/evolve/scripts/tests/test_evolve_report_improvements.py の `FIX_DISPATCH` import 含む) 継続動作）。`__init__.py` は 945 → 557 行（−388 行、累計 2364 → 557）。
- **remediation/ パッケージから rule / line_limit / skill_evolve / verification_rule / stale_memory / pitfall_archive 系 fix 関数を `remediation/fixers_rules.py` に分離 (Phase 3 / Slice 5)** — `_is_rule_file` / `_fix_rule_by_separation` (rule の references 分離 + LLM 圧縮) / `fix_line_limit_violation` (rule は分離、それ以外は LLM 圧縮) / `fix_skill_evolve` (自己進化パターン適用) / `fix_verification_rule` (検証ルール作成) / `fix_stale_memory` (stale エントリ削除) / `fix_pitfall_archive` (Cold 層アーカイブ) (~480 行) を切り出し。`__init__.py` は再エクスポートで `from remediation import fix_line_limit_violation` 等の後方互換維持（snapshot test green、外部 importer (skills/evolve/scripts/tests/test_remediation.py の `subprocess_mod` patch 含む) 継続動作）。`PLUGIN_ROOT` import は関数内 lazy 化。`__init__.py` は 1420 → 945 行（−475 行、累計 2364 → 945）。
- **remediation/ パッケージから基本 fix 関数群を `remediation/fixers_basic.py` に分離 (Phase 3 / Slice 4)** — `fix_stale_references` / `fix_stale_rules` / `fix_claudemd_phantom_refs` / `fix_claudemd_missing_section` / `fix_global_rule` / `fix_hook_scaffold` / `fix_untagged_reference` (~370 行) を切り出し。`__init__.py` は再エクスポートで `from remediation import fix_stale_references, fix_stale_rules, ...` の後方互換維持（snapshot test green、外部 importer 継続動作）。`fix_untagged_reference` は `from plugin_root import PLUGIN_ROOT` を関数内 lazy import に変更。`__init__.py` は 1785 → 1420 行（−365 行、累計 2364 → 1420）。
- **remediation/ パッケージから rationale 生成を `remediation/rationale.py` に分離 (Phase 3 / Slice 3)** — `_RATIONALE_TEMPLATES` (24 issue type 分の修正理由テンプレート) + `generate_rationale` (issue + category → 修正理由テキスト、20+ issue type 分岐) (~170行) を切り出し。`__init__.py` は再エクスポートで `from remediation import generate_rationale` の後方互換維持（snapshot test green）。`__init__.py` は 1952 → 1785 行（−167 行、累計 2364 → 1785）。
- **remediation/ パッケージから confidence_score / impact_scope 算出 + classify_issue / classify_issues を `remediation/confidence.py` に分離 (Phase 3 / Slice 2)** — `compute_impact_scope` (file/project/global 判定) / `_load_calibration_overrides` (confidence-calibration.json 読込) / `compute_confidence_score` (issue type → 0.0-1.0 算出、20+ パターン) / `classify_issue` (FP 除外 + 動的分類 + 原則ベース昇格 + 保護スキル降格) / `classify_issues` (バッチ分類) (~250 行) を切り出し。`__init__.py` は再エクスポートで `from remediation import compute_confidence_score, classify_issue, classify_issues` 等の後方互換維持。`classify_issue` は `mock.patch("remediation.compute_confidence_score" / "remediation.is_protected_skill" / "remediation._apply_principles", ...)` 既存テスト (`test_gstack_integration.py`) 追従のため `from . import X` で package 経由の遅延参照。`__init__.py` は 2202 → 1952 行（−250 行、累計 2364 → 1952）。
- **`scripts/lib/remediation.py` (2364行) を `scripts/lib/remediation/` パッケージに分割し、原則ベース判断 + FP 除外 + 独立検証を `remediation/principles.py` に分離 (Phase 3 / Slice 1)** — `remediation.py` → `remediation/__init__.py` にパッケージ化したうえで、`REMEDIATION_PRINCIPLES` (原則ベース判断辞書) / `_apply_principles` (issue type → bonus 加算) / `FP_EXCLUSIONS` (12 種類の FP 除外パターン) / `_should_exclude_fp` (issue → FP reason 判定) / `_independent_verify` (修正前後の差分ヒューリスティクス検証) (~165行) を `remediation/principles.py` に切り出し。`__init__.py` は再エクスポートで `from remediation import REMEDIATION_PRINCIPLES, _apply_principles, FP_EXCLUSIONS, _should_exclude_fp, _independent_verify` の後方互換維持（snapshot test green、外部 importer (evolve.py / scripts/tests/test_remediation_layers.py / scripts/tests/test_remediation_fp_verify.py / scripts/tests/test_gstack_integration.py / skills/evolve/scripts/tests/test_remediation.py 等) すべて継続動作）。`__init__.py` は 2364 → 2202 行（−162 行）。

### Added
- **`scripts/tests/test_remediation_snapshot.py`** — remediation リファクタのレグレッション防止 snapshot test を追加 (Phase 3 / Slice 0)。`remediation` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/remediation_api_surface.txt`、49 シンボル + 40 定数）。後続の Phase 3 (remediation/ パッケージ分割、2364 行 → ≤200 行目標) で外部 importer (evolve.py / scripts/tests / skills/evolve/scripts/tests 等) が依存する `from remediation import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。

### Changed
- **discover/ パッケージから run_discover オーケストレータ + CLI main を `discover/runner.py` に分離し Phase 2 完了 (Phase 2 / Slice 6)** — `run_discover` (behavior/error/rejection/missed_skill/enrich/verification/tool_usage/recommended/installed/pitfall/instruction_violation/stall_recovery/workflow_checkpoint の各検出を統合する ~200行のオーケストレータ) と `main` (argparse + JSON 出力) を `discover/runner.py` (~250行) に切り出し。`__init__.py` は再エクスポートで `from discover import run_discover, main` 等の後方互換維持（`bin/rl-discover` / `skills/discover/scripts/discover.py` shim 動作継続）。各検出関数 (`detect_behavior_patterns` / `detect_error_patterns` / `_enrich_patterns` / `detect_repeated_correction_patterns` / `determine_scope` 等) は `from . import X` で package 経由の遅延参照（`mock.patch.object(discover, "detect_X", ...)` 既存テスト追従）。`__init__.py` は **318 → 97 行**（−221 行、累計 1131 → 97、**−91%**）。**目標 ≤200 行を達成し discover/ パッケージ分割 Phase 2 完了**。最終構成: `__init__.py` (97) / `runner.py` (251) / `patterns.py` (280) / `artifacts.py` (303) / `errors.py` (136) / `enrich.py` (89) / `suppression.py` (135) — 全ファイル `MAX_PYTHON_SOURCE_LINES=500` warn を大幅クリア。
- **discover/ パッケージから行動パターン検出 + Agent prompt 分類 + missed skill 検出を `discover/patterns.py` に分離 (Phase 2 / Slice 5)** — `detect_behavior_patterns` (usage/sessions ベースのパターン検出 + プラグイン/Agent 分離) / `_classify_agent_prompts` (Agent prompt のキーワード分類) / `detect_missed_skills` (CLAUDE.md トリガー × usage で未使用スキル検出) (~270行) を切り出し。`__init__.py` は再エクスポートで `from discover import detect_behavior_patterns, detect_missed_skills` 等の後方互換維持。`DATA_DIR` / `_load_classify_usage_skill` / `load_suppression_list` は package 経由で遅延参照（`mock.patch.object(discover, "DATA_DIR", ...)` 既存テスト追従）。`__init__.py` は 570 → 318 行（−252 行、累計 1131 → 318）。
- **discover/ パッケージから Jaccard 照合 (enrich) を `discover/enrich.py` に分離 (Phase 2 / Slice 4)** — `_enrich_patterns` (パターン × 既存スキルの Jaccard 係数照合、~75行) を切り出し。`__init__.py` は再エクスポートで `from discover import _enrich_patterns` の後方互換維持（snapshot test green）。`JACCARD_THRESHOLD` / `PLUGIN_ROOT` は package 経由で遅延参照。`__init__.py` は 646 → 570 行（−76 行、累計 1131 → 570）。
- **discover/ パッケージから推奨 artifact 一覧 + 導入状態判定を `discover/artifacts.py` に分離 (Phase 2 / Slice 3)** — `RECOMMENDED_ARTIFACTS` (推奨ルール/hook/skill/config 19 エントリ) / `detect_recommended_artifacts` / `detect_installed_artifacts` / `_compute_mitigation_metrics` (~280行) を切り出し。`__init__.py` は再エクスポートで `from discover import RECOMMENDED_ARTIFACTS, detect_recommended_artifacts` 等の後方互換維持。`detect_*` 関数は `from . import RECOMMENDED_ARTIFACTS as _ARTIFACTS` で package 経由の遅延参照（`mock.patch("discover.RECOMMENDED_ARTIFACTS", ...)` 既存テスト追従）。`__init__.py` は 925 → 646 行（−279 行、累計 1131 → 646）。
- **discover/ パッケージから errors / scope を `discover/errors.py` に分離 (Phase 2 / Slice 2)** — `detect_error_patterns` / `detect_repeated_correction_patterns` / `detect_rejection_patterns` / `determine_scope` + `HOOK_CANDIDATE_THRESHOLD` 定数 (~115行) を切り出し。`__init__.py` は再エクスポートで `from discover import detect_error_patterns` 等の後方互換維持（snapshot test green）。`DATA_DIR` / `HISTORY_DIR` は package 経由で遅延参照（テスト DATA_DIR 差し替え追従）。`__init__.py` は 1038 → 925 行（−113 行、累計 1131 → 925）。
- **`scripts/lib/discover.py` (1131行) を `scripts/lib/discover/` パッケージに分割し、suppression / JSONL ローダ / バリデータを `discover/suppression.py` に分離 (Phase 2 / Slice 1)** — `discover.py` → `discover/__init__.py` にパッケージ化したうえで、`load_jsonl` / `load_suppression_list` / `load_merge_suppression` / `add_merge_suppression` / `add_to_suppression_list` / `validate_skill_content` / `validate_rule_content` / `load_claude_reflect_data` / `_load_skill_tokens` / `_load_classify_usage_skill` (~100行) を `discover/suppression.py` に切り出し。`__init__.py` は再エクスポートで `from discover import load_jsonl` 等の後方互換維持（snapshot test green、外部 importer 多数すべて継続動作）。テストが `mock.patch.object(discover, "SUPPRESSION_FILE", ...)` で差し替えるパターンに追従するため `_suppression_file()` で `from . import SUPPRESSION_FILE as _f` を遅延参照。`skills/discover/scripts/discover.py` shim は `importlib.spec_from_file_location` から `importlib.import_module` ベースに更新（パッケージ化 + 自己再帰回避）。`__init__.py` は 1131 → 1038 行（−93 行）。
- **fleet/ パッケージから CLI エントリを `fleet/cli.py` に分離し Phase 1 完了 (Slice 13 dogfooding Phase 1 / Slice 6)** — `main` (argparse + サブコマンド分岐) / `_run_status` / `_run_test_guard` / `_run_discover` (~190行) を切り出し。`__init__.py` は再エクスポートで `from fleet import main` 維持（`bin/rl-fleet` は変更不要）。`__init__.py` は **306 → 117 行**（−189 行、累計 1069 → 117、**−89%**）、未使用となった `argparse` / `json` / `sys` の top-level import も削除。**目標 ≤200 行を達成し fleet/ パッケージ分割 Phase 1 完了**。最終構成: `__init__.py` (117) / `cli.py` (217) / `cli_tokens.py` (204) / `collectors.py` (231) / `audit_runner.py` (192) / `project_loader.py` (153) / `formatters.py` (136)。fleet Phase 1 design doc Slice 6。
- **fleet/ パッケージから tokens サブコマンド + 注入ロジックを `fleet/cli_tokens.py` に分離 (Slice 13 dogfooding Phase 1 / Slice 5)** — `_inject_token_metrics` (FleetRow への tokens_30d / cache_hit_pct 注入) / `_resolve_pj_id` (--pj 引数解決) / `_run_tokens` (rl-fleet tokens サブコマンド本体) (~200行) を切り出し。`__init__.py` は再エクスポートで `from fleet import _run_tokens, _resolve_pj_id, _inject_token_metrics` 等の後方互換維持。`__init__.py` は 486 → 306 行（−180 行、累計 1069 → 306）。fleet Phase 1 design doc Slice 5。
- **fleet/ パッケージから status 収集 / 永続化を `fleet/collectors.py` に分離 (Slice 13 dogfooding Phase 1 / Slice 4)** — `FleetRow` (dataclass) / `_collect_single` / `_find_duplicate_basenames` / `aggregate_subagents_by_project` / `collect_fleet_status` / `_serialize_row` / `write_fleet_run` + 定数 `_UNKNOWN_PROJECT_LABEL` / `_SUBAGENTS_DEFAULT_WINDOW_DAYS` (~230行) を切り出し。`__init__.py` は再エクスポートで `from fleet import FleetRow, collect_fleet_status, write_fleet_run, aggregate_subagents_by_project` 等の後方互換維持。テスト側 mock パスを `fleet.classify_project` / `fleet.run_audit_subprocess` / `fleet.enumerate_projects` → `fleet.collectors.X` に追従 (8箇所)。`__init__.py` は 682 → 486 行（−196 行、累計 1069 → 486）、未使用となった `ThreadPoolExecutor` / `asdict` / `dataclass` / `timedelta` / `datetime` / `timezone` の top-level import も削除。fleet Phase 1 design doc Slice 4。
- **fleet/ パッケージから audit subprocess 実行を `fleet/audit_runner.py` に分離 (Slice 13 dogfooding Phase 1 / Slice 3)** — `AuditResult` / `IssuesSummary` (dataclass) / `run_audit_subprocess` / `_parse_issues_summary` / `_terminate_process_group` / `_parse_iso` (~190行) を切り出し。`__init__.py` は再エクスポートで `from fleet import AuditResult, IssuesSummary, run_audit_subprocess` 等の後方互換維持。テスト側 (`mock.patch("fleet.subprocess.Popen")` 5箇所) は `mock.patch("fleet.audit_runner.subprocess.Popen")` に追従。`AuditResult.issues_summary` は同ファイル内で IssuesSummary を先に定義したため forward-ref 文字列が不要になり snapshot を更新（FleetRow 側と表記が一致）。`__init__.py` は 847 → 682 行（−165 行、累計 1069 → 682）、未使用となった `re` / `signal` / `subprocess` / `time` の top-level import も削除。fleet Phase 1 design doc Slice 3。
- **fleet/ パッケージから PJ 列挙 / 導入状況判定を `fleet/project_loader.py` に分離 (Slice 13 dogfooding Phase 1 / Slice 2)** — `_pj_safe_name` / `resolve_auto_memory_dir` / `enumerate_projects` / `_load_settings_with_retry` / `_is_plugin_enabled` / `_latest_activity` / `_safe_compute_level` / `classify_project` (~150行) を切り出し。`__init__.py` は再エクスポートで `from fleet import classify_project, enumerate_projects, resolve_auto_memory_dir` の後方互換維持（snapshot test green）。`__init__.py` は 964 → 847 行（−117 行、累計 1069 → 847）。fleet Phase 1 design doc Slice 2。
- **`scripts/lib/fleet.py` (1069行) を `scripts/lib/fleet/` パッケージに分割し、formatters を `fleet/formatters.py` に分離 (Slice 13 dogfooding Phase 1 / Slice 1)** — `fleet.py` → `fleet/__init__.py` にパッケージ化したうえで、`_TABLE_HEADERS` / `_format_short_int` / `_format_cell_*` 8 個 / `_format_relative` / `format_status_table` (~120行) を `fleet/formatters.py` に切り出し。`__init__.py` は再エクスポートで `from fleet import format_status_table` 等の後方互換維持（外部 importer 14 箇所すべて継続動作、snapshot test green）。`FleetRow` への参照は `from __future__ import annotations` + `TYPE_CHECKING` で循環 import 回避。`__init__.py` は 1069 → 964 行（−105 行）。fleet Phase 1 design doc Slice 1。

### Added
- **`scripts/tests/test_discover_snapshot.py`** — discover リファクタのレグレッション防止 snapshot test を追加 (Phase 2 / Slice 0)。`discover` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/discover_api_surface.txt`、19 シンボル + 8 定数）。後続の Phase 2 (discover/ パッケージ分割) で外部 importer (prune.py / evolve.py / hooks/tests / skills/discover/scripts/tests 等) が依存する `from discover import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。
- **`scripts/tests/test_fleet_snapshot.py`** — fleet リファクタのレグレッション防止 snapshot test を追加 (Slice 13 dogfooding Phase 1 / Slice 0)。`fleet` モジュールの公開関数/クラスシグネチャ + module-level constants の dump を fixture 化（`scripts/tests/fixtures/fleet_api_surface.txt`、13 シンボル + 6 定数）。後続の Phase 1 (fleet/ パッケージ分割) で外部 importer (bin/rl-fleet, prune.py, evolve.py, test_fleet_tokens.py 等) が依存する `from fleet import X` 互換性を byte レベルで保証する。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。
- **Python source 行数バジェット guard を追加 (Slice 13)** — `scripts/lib/line_limit.py` に `MAX_PYTHON_SOURCE_LINES=500` (warn) / `MAX_PYTHON_SOURCE_HARD=800` (violation) を追加。`audit.check_python_source_budgets(project_dir)` が `scripts/**.py` / `hooks/**.py` をスキャンし、warn / hard の violation を `run_audit` の violations に積む（report に "Line Limit Violations" として表示）。`__init__.py` / `conftest.py` / `tests/` 配下は除外。`.claude/rules/file-size-budget.md` で運用ルール宣言。audit.py 2046行肥大化（PR #51-#61 で 178 行へ分割）の再発予防。本機能の追加で現リポジトリでも 15 件の既存違反（`fleet.py` 1070行 / `discover.py` 1131行 等）が可視化される。
- **`docs/refactoring/audit-coverage-baseline.md`** — Phase 2 (audit/ パッケージ分割) の事前計測。`scripts/lib/audit.py` の statement カバレッジ 52.8% (631/1177)、branch カバレッジ 80 partial / 570。missing line spans の上位 30 を記録。分割境界判断材料 + 切り出し前後の回帰チェック用ベースライン。
- **`scripts/tests/test_audit_snapshot.py`** — audit リファクタのレグレッション防止 snapshot test を追加。`audit` モジュールの公開関数シグネチャ + module-level constants の dump と、`generate_report()` の empty / populated 入力に対する出力を fixture 化（`scripts/tests/fixtures/audit_*.txt`）。後続の PR0 (named constants 集約) / Phase 2 (audit/ パッケージ分割) で振る舞いが変わったら byte レベルで検知する。`HOME` / `CLAUDE_PLUGIN_DATA` を tmp に向けて完全決定論化。fixture 更新は `UPDATE_SNAPSHOTS=1 pytest` で。

### Changed
- **audit/ パッケージからオーケストレーター層を `audit/orchestrator.py` に分離 (Phase 2 第十一弾)** — `run_audit` (audit メインエントリ、~180行) / `_record_audit_completion` / `_extract_score_from_report` / `_append_audit_history` / `_check_degradation` / `_build_growth_report` (NFD Growth Report、~125行) と `_AUDIT_HISTORY_FILE` / `_MAX_AUDIT_HISTORY` / `_DEGRADATION_THRESHOLD` 定数を切り出し（~410行）。`__init__.py` は再エクスポートで `audit._AUDIT_HISTORY_FILE` / `audit.run_audit` 等の後方互換維持。`_append_audit_history` / `_check_degradation` 内で `audit.DATA_DIR` / `audit._AUDIT_HISTORY_FILE` を遅延参照（テスト patch 追従）。`__init__.py` は 572 → 178 行（更に 394 行削減、累計 2046 → 178、**-91%**）、`__init__.py` は実質 re-export 層に到達（目標 ≤200 行を達成）。Phase 2 design doc Slice 11。
- **audit/ パッケージから `generate_report` を `audit/report.py` に分離 (Phase 2 第十弾)** — 1画面レポートの最終組み立て (~140行) を切り出し。memory / quality / sections の各セクションを順序付けて Markdown に結合。`__init__.py` は再エクスポートで `from audit import generate_report` 後方互換維持。`__init__.py` は 711 → 572 行（更に 139 行削減、累計 2046 → 572、**-72%**）。Phase 2 design doc Slice 10。
- **audit/ パッケージから section ビルダー群を `audit/sections.py` に分離 (Phase 2 第九弾)** — `_format_constitutional_report` (Constitutional Score → Markdown) / `_short_int` (1.2K/3.4M 短縮表記) / `build_token_consumption_section` (PJ別トークン TOP3 + 異常検知) / `_build_test_guard_section` (LLM SDK 利用 PJ への guard 推奨) を切り出し（~155行）。`__init__.py` は再エクスポートで後方互換維持。`__init__.py` は 850 → 711 行（更に 139 行削減、累計 2046 → 711、**-65%**）。Phase 2 design doc Slice 9。
- **audit/ パッケージから scope advisory を `audit/scope.py` に分離 (Phase 2 第八弾)** — `detect_duplicates_simple` (ファイル名ベース重複検出) / `semantic_similarity_check` (TF-IDF + コサイン類似度) / `load_usage_registry` / `scope_advisory` を切り出し（~100行）。`__init__.py` は再エクスポートで `from audit import semantic_similarity_check` 等の後方互換維持。`load_usage_registry` は DATA_DIR を遅延参照（test patch 追従）。`__init__.py` は 927 → 850 行（更に 77 行削減、累計 2046 → 850、**-58%**）。Phase 2 design doc Slice 8。
- **audit/ パッケージから usage 集計を `audit/usage.py` に分離 (Phase 2 第七弾)** — `load_usage_data` / `_is_openspec_skill` / `_is_plugin_skill` / `aggregate_usage` / `aggregate_plugin_usage` + `_BUILTIN_TOOLS` 定数を切り出し（~115行）。`__init__.py` は再エクスポートで `from audit import load_usage_data` 等の後方互換維持。テストが `mock.patch.object(audit, "DATA_DIR", ...)` で差し替えるパターンに追従するため、`load_usage_data` 内で DATA_DIR を遅延参照（`from . import DATA_DIR`）。`__init__.py` は 1017 → 927 行（更に 90 行削減、累計 2046 → 927、**-55%**）、`usage.py` 単独は 85% カバレッジ。Phase 2 design doc Slice 7。
- **audit/ パッケージから artifacts 収集を `audit/artifacts.py` に分離 (Phase 2 第六弾)** — `find_artifacts` (project + global の skills/rules/memory/CLAUDE.md 列挙) と `check_line_limits` (各種上限+MEMORY.md バイトサイズチェック) を切り出し（~95行）。`__init__.py` は再エクスポートで `from audit import find_artifacts, check_line_limits` 後方互換維持。`__init__.py` は 1112 → 1017 行（更に 95 行削減、累計 2046 → 1017）、`artifacts.py` 単独は 95% カバレッジ。Phase 2 design doc Slice 6。
- **audit/ パッケージから plugin classification を `audit/classification.py` に分離 (Phase 2 第五弾)** — `_load_plugin_skill_map` / `_build_plugin_prefixes` / `classify_usage_skill` / `_load_plugin_skill_names` / `classify_artifact_origin` + module-level cache (`_plugin_skill_map_cache` / `_plugin_prefix_cache`) を切り出し（~110行）。`__init__.py` は再エクスポートで `from audit import classify_artifact_origin` 等の後方互換維持。テスト側（test_audit_project_filter / test_usage_scope / test_collect_issues / test_reorganize / test_prune の計27箇所）は `audit._plugin_skill_map_cache = X` から `audit.classification._plugin_skill_map_cache = X` に追従。`__init__.py` は 1196 → 1112 行（更に 84 行削減、累計 2046 → 1112）、`classification.py` 単独は 88% カバレッジ。Phase 2 design doc: `~/.gstack/projects/evolve-anything/todoroki-main-design-20260514-130921.md`。
- **audit/ パッケージから issues collection を `audit/issues.py` に分離 (Phase 2 第四弾)** — `collect_issues` / `detect_untagged_reference_candidates` / `_is_user_invocable_heuristic` (~210行) を切り出し。__init__ の関数 (`find_artifacts` / `check_line_limits` / `detect_duplicates_simple` / `load_usage_data` / `aggregate_usage` / `classify_artifact_origin`) は遅延 import で循環回避。`__init__.py` は 1408 → 1196 行（更に 212 行削減、累計 2046 → 1196）、`issues.py` 単独は 80% カバレッジ。
- **audit/ パッケージから quality trends を `audit/quality.py` に分離 (Phase 2 第三弾)** — `load_quality_baselines` / `generate_sparkline` / `build_quality_trends_section` を切り出し。`audit/gstack.py` の遅延 import を `from .quality import load_quality_baselines` に明示化。`__init__.py` は 1504 → 1408 行（更に 96 行削減、累計 2046 → 1408）、`quality.py` 単独は 88% カバレッジ。test pollution で flaky だった `test_load_quality_baselines_*` も `audit.quality.DATA_DIR` パッチに修正し安定化。
- **audit/ パッケージから gstack ワークフロー分析を `audit/gstack.py` に分離 (Phase 2 第二弾)** — `_load_flow_chain_phases` / `_match_gstack_phase` / `_is_gstack_skill` / `build_gstack_analytics_section` / `_load_global_retro` + 関連定数 (`_GSTACK_LIFECYCLE` / `_FALLBACK_*` / `_FLOW_CHAIN_FILE`) をまとめて切り出し。`__init__.py` は再エクスポートで後方互換維持。`load_quality_baselines` への参照は循環 import を避けるため遅延 import。`__init__.py` は 1694 → 1504 行（更に 190 行削減）、`gstack.py` 単独は 77% カバレッジ。
- **`scripts/lib/audit.py` (2046行) を `scripts/lib/audit/` パッケージに分割 (Phase 2 第一弾)** — まず `audit.py` → `audit/__init__.py` にパッケージ化し、MEMORY 検証ロジック群（`build_memory_verification_context` / `build_memory_health_section` / `build_temporal_memory_warnings` + helpers, ~342行）を `audit/memory.py` に切り出し。共有定数 (`LIMITS` / `_STOPWORDS`) は `audit/_constants.py` に集約し循環 import を回避。`from audit import X` の後方互換は再エクスポートで維持、snapshot test で公開 API 不変を保証。`__init__.py` は 2046 → 1694 行に削減、`memory.py` は単独 83% カバレッジ。CLI shim (`skills/audit/scripts/audit.py`) は `submodule_search_locations` 付きで package を明示ロードするよう更新。
- **`NEAR_LIMIT_RATIO` を `line_limit.py` に移動** — audit.py で定義されていた `NEAR_LIMIT_RATIO = 0.8` を line 系制限定数の SoT である `line_limit.py` に統合。audit.py は import 経由で再エクスポート、`from audit import NEAR_LIMIT_RATIO` の後方互換は維持。PR-1 の snapshot test が「API surface 不変」を保証。
- **`hooks/tests/test_hooks.py` (2017行) を機能別 7 ファイルに分割** — `test_hooks_workflow.py` / `_observe.py` / `_session.py` / `_discover_prune.py` / `_safety.py` / `_worktree.py` / `_misc.py`。共有 fixture (`tmp_data_dir`, `patch_data_dir`) と sys.path 設定は `hooks/tests/conftest.py` に一元化。テスト件数・挙動は不変（160 passed）。大規模リファクタの一環で、巨大テストファイルによる Read コスト削減を狙う。
- **`scripts/tests/test_verification_catalog.py` (1116行) を機能別 6 ファイルに分割** — `test_verification_catalog_structure.py` / `_helpers.py` / `_data_contract.py` / `_side_effect.py` / `_evidence.py` / `_iac_cross_layer.py`。共通 helper (`_create_py_files` / `_create_side_effect_files` / `_create_iac_project` 等) は `scripts/tests/conftest.py` に集約。テスト件数・挙動は不変（110 passed）。大規模リファクタの一環で、巨大テストファイルの Read コスト削減を狙う。

## [1.51.0] - 2026-05-14

### Added
- **`bin/rl-fleet test-guard status`** — 全 PJ 横断で no-llm-in-tests (pre-commit) と pytest-no-llm (runtime guard) の導入状況を一覧表示。LLM SDK 利用検出 + 言語検出 + テスト存在チェックで「要対応」「予防導入候補」を分けて出力。
- **audit に Test Guard セクション** — LLM SDK 利用 PJ に対し L1/L2 guard 導入を推奨する診断セクションを追加（`scripts/lib/audit.py`）。
- **3層 Test Guard アーキテクチャ完成** — L1 (静的検出 pre-commit, `todoroki-godai/no-llm-in-tests`) / L2 (pytest runtime guard, `todoroki-godai/pytest-no-llm`) / L3 (可視化, 本 PR)。L1/L2 は独立 OSS として別リポジトリに分離（CC plugin と Python ライブラリのライフサイクル分離）。

### Removed
- **CHANGELOG drift 整理** — [1.14.2] と [1.13.0] の間に misplaced されていた orphan `[Unreleased]` セクション（handover/second-opinion/critical-instruction-compliance 等 13 件、`closes #39/#42/#43/#44` の issue 番号も最近の PR と内容不一致）を削除。該当機能はすでに別バージョンで shipped 済み。

## [1.50.3] - 2026-05-13

### Fixed
- **`test_run_loop_evolve_flag_calls_try_evolve` の 12 秒 mock 漏れを修正** — `run_loop` 内部は `score_variant` ではなく `_score_variant_axes` → `_parallel_score` → `_score_single_axis(claude -p)` の経路で実 LLM を叩いていたため、`score_variant` の mock がスルーされ 3 軸 × リトライで 11-12 秒消費していた。mock 位置を `_score_variant_axes` に修正して 0.04 秒に短縮（closes #41 A）。
- **`test_fleet TestMainCLI` 2件の 13 秒問題を修正** — `fleet.main()` が `load_config` で tracked_projects を実走査 + `_inject_token_metrics` で token_usage SoR を読み込み 13s 消費していた。`_fast_main_mocks` ヘルパーで本番 IO を遮断し 0.05s に短縮。
- **`test_pipeline_reflector` の `test_sufficient_data` / `test_degraded_marker` fail を修正** — `_make_outcome` の timestamp が固定日付 `2026-03-01` で、デフォルト `lookback_days=30` の cutoff から外れ「データ不足」判定になっていた。`datetime.now() - 1day` に変更。

### Added
- **`conftest.py` に LLM 呼び出し guard を追加** — テスト中に `subprocess.run(["claude", ...])` / `subprocess.Popen(["claude", ...])` が呼ばれた瞬間に `RuntimeError` を投げる。mock 漏れによる実 LLM 課金を構造的に防止（issue #41 で 1 セッション 1.5M token 消費の主要因と判明）。integration テスト等で正当に必要な場合は `RL_ALLOW_LLM_IN_TESTS=1` で解除可能（runtime 評価）。
- **`.claude/rules/no-llm-in-tests.md` 追加 + global `~/.claude/rules/testing.md` 更新** — 単体テストで LLM を呼ばないルールを明文化。mock 位置は call graph を読んで「実際に呼ばれる関数」を選ぶことを規約化。

### Performance
- **テストスイート全体を 34.92s → 8.09s に短縮**（`scripts/* + hooks/* + bin/*` で 2036 件、4 倍速）。

## [1.50.2] - 2026-05-13

### Removed
- **`token_guard` hook を削除** — セッション累積トークン警告は Claude Code 公式の `/usage` および statusline 系コミュニティツール（ccusage 等）でカバーされている領域で、累積警告は既に消費済みのためアクション可能性も低かった。context window 占有率を監視する `ctx_guard` 一本に集約し、`token_warn_threshold` userConfig も削除（`hooks/token_guard.py`・テスト・hooks.json エントリ・rule 内参照を同時更新）。
- **`PreCompact` 時の handover 提案を削除** — `/compact` はセッション継続が前提の操作のため、handover（次セッション引き継ぎ）を勧めるのは矛盾していた。`save_state.py` の `_suggest_handover()` および対応テストを削除。

## [1.50.1] - 2026-05-13

### Fixed
- **`bin/rl-fleet tokens --pj`: pj_slug / 部分一致での解決をサポート** — TOP-N 表示が短縮 slug（例 `anything`, `receipt`）なのに `--pj` 引数は DB の `pj_id` フルパス（例 `-Users-todoroki-tools-rl-anything`）と完全一致しないと空表示になっていた。`_resolve_pj_id()` を追加して exact → slug exact → endswith → contains の優先度で解決。曖昧な場合は候補一覧と共に非ゼロ終了、未発見も非ゼロ終了するように改善。

## [1.50.0] - 2026-05-13

### Added
- **`ctx_guard` hook 追加** — `UserPromptSubmit` hook に `hooks/ctx_guard.py` を追加。token_guard とは別軸で、最新メッセージの context window 占有率（`input_tokens + cache_read_input_tokens + cache_creation_input_tokens` / window）を監視し、閾値%（デフォルト 20%）を超えると compaction 前提の代替案（`/compact`、`/handover`、Read→Grep 切り替え）を stdout に差し込む。5分クールダウン。`session_id` 未取得・閾値 0・window 0 は silent exit。
- **`userConfig` に `ctx_warn_percent` / `ctx_window_tokens` 追加** — それぞれデフォルト 20 / 1,000,000 (Opus 1M)。200K-tier モデル中心なら `ctx_window_tokens=200000` に下げる。

### Changed
- **`token_guard` のデフォルト閾値を 50,000 → 500,000 に引き上げ** — 1M context モデルでは 50K は 1〜2 ターンで超えるため非現実的（`accumulated input + output + cache_read` の合算は cache hit ターンで急速に膨らむ）。rate limit / コスト軸の警告として実害が出る前のラインに調整。`CLAUDE_PLUGIN_OPTION_token_warn_threshold` で従来値に戻すことも可能。
- **`token_warn_threshold` の userConfig description を明記** — token_guard が「累積課金」、ctx_guard が「window 占有率」と軸を分けて記述。

## [1.49.1] - 2026-05-13

### Fixed
- **`hooks/hooks.json`: `args[]` exec form を `command` 文字列形式に revert** — CC v2.1.139 で runtime は `args: string[]` (exec form) を受け付けるようになったが、`claude plugin validate` の schema が `command` フィールドを必須としており validation がコケる（→ `claude plugin tag --push` が失敗する）。v1.49.0 で全 12 hook が validation を通らない状態だったため `command` 文字列形式に戻す。CC validator が `args` をサポートするまで保留。

## [1.49.0] - 2026-05-13

### Added
- **`release-notes-review`: What's New バージョン別サマリーセクションを追加** — レポートの Part 1 先頭に `### What's New 📋` セクションを追加。未チェック差分の各バージョンについて、プロジェクト関連かどうかを問わず主要な新機能・改善・バグ修正を全体概観として紹介するようになった。Step 3 に 3.0「バージョン別サマリー生成」フェーズを追加し、突合分析（3.1）の前に全体像を整理するフローに変更。

### Changed
- **`hooks/hooks.json`: 全 hook を exec form (`args: []`) に移行** — CC v2.1.139 で追加された hook `args: string[]` 形式を採用。全 12 エントリをシェルを介さず python3 を直接起動する exec form に変換。パスのクォートが不要になりクォート漏れによるバグリスクを根本除去。

## [1.48.0] - 2026-05-12

### Added
- **LLM バッチ消費リアルタイム警告 `token_guard` hook** (closes #34) — `UserPromptSubmit` hook に `hooks/token_guard.py` を追加。セッションの `.jsonl` ファイルを byte-offset 差分読み（< 50ms）で追跡し、累積トークン消費が `token_warn_threshold`（デフォルト 50,000）を超えると警告 + 代替案リストを stdout に差し込む。5分クールダウンで重複警告を抑制。`CLAUDE_SESSION_ID` 未設定・セッションファイル不在・`/tmp` 書き込み失敗はすべて silent exit / fallback。
- **`userConfig` に `token_warn_threshold` 追加** — `CLAUDE_PLUGIN_OPTION_token_warn_threshold=100000` でプロジェクト別に閾値変更可能（#77 修正済みの仕組みを活用）。
- **`rules/llm-batch-guard.md` 追加** — LLM バッチ処理を提案・実装する前に件数と見積もりトークン数をユーザーに確認を取るルール（3行、10行制限内）。

## [1.47.1] - 2026-05-12

### Fixed
- **`rl-fleet status` がデフォルトで STALE PJ を非表示に** — 全く更新していない PJ が常に表示されノイズになる問題を修正。デフォルトは STALE を除外し末尾に件数のみ表示（`--all` で全表示）

## [1.47.0] - 2026-05-12

### Added
- **並列セッション branch drift 対策ルール追加** (#79) — 複数 Claude セッション環境で current branch が予告なく切り替わる pitfall に対応。`.claude/rules/parallel-session-guard.md` を追加: `git commit` / `git add` 前に `git branch --show-current` で確認、drift 検知時は `git checkout <想定 branch>` で戻す、長時間作業では数分おきに branch を確認する
- **インフラ変更 ship ゲートルール追加** (#40) — buildspec/CDK/Terraform/Lambda 等インフラファイル変更時に動作確認を怠ることへの対策。`.claude/rules/infra-ship-gate.md` を追加: diff にインフラファイルが含まれる場合は動作確認済みを確認してから /ship する
- **`discover`: 繰り返し corrections パターンから hook 候補を自動検出** (#41) — `detect_repeated_correction_patterns()` を追加。同じ corrections パターンが閾値（デフォルト3）回以上繰り返されたものを `hook_candidate` として `run_discover()` 結果に含める。「ルールでは防げないが hook で防げる」パターンを自動的に surface する
- **README.md スキル一覧を実態と一致させる** (#81) — "Skill Catalog (19 skills)" → "Skill Catalog (19 user-invocable skills)" に変更し、掲載ポリシー（ユーザー呼び出し型のみ）を明記。内部スキル注記に `rl-loop-orchestrator` と `genetic-prompt-optimizer` を追加して SPEC.md との不一致を解消
- **gstack 協調開発フロー設計完了** (#36/#37) — gstack + rl-anything の2ツール体制を正式決定（gstack=開発実行、rl-anything=品質進化）。`gstack-refine` スキル（`~/.claude/skills/gstack-refine/`）により Plan フェーズ出力の品質ゲートを実現。本 issue は設計・スキル作成完了にて close

## [1.46.2] - 2026-05-12

### Fixed
- **`load_user_config`: 空文字 env var が string 型 default を上書きできない非対称を修正** (#77) — `os.environ.get(..., "")` + `if not env_val` の組み合わせが未設定と空文字を区別できなかったため、`CLAUDE_PLUGIN_OPTION_cleanup_tmp_prefixes=""` で category 4 無効化を試みても silently 無視されていた。`os.environ.get(...)` + `if env_val is None` に変更し、**string 型キーのみ**空文字を意図的な override として許容するよう修正。bool / int キーへの空文字は非 string として `continue` し default fallback（`_parse_bool("") → False` で `auto_trigger` が silently 無効化されるリグレッションを防止）。`is_user_config_explicit` も `is not None` 判定に統一
- **`test_rules_exceeds_limit`: `MAX_PROJECT_RULE_LINES=10` に合わせてテスト content を 11 行以上に修正** — コメントの「MAX_RULE_LINES=3」が古い値のまま残っており、5 行 content が制限以下として passed=True になりアサーション失敗していた

## [1.46.1] - 2026-05-11

### Fixed
- **SKILL.md 内 sys.path / PLUGIN_DIR パターンの統一** (#32) — cleanup・evolve-skill の Python スニペットが `__file__` 未定義（heredoc 実行）時に cwd 依存にフォールバックしていた問題、および agent-brushup・audit・evolve-fitness・generate-fitness の Bash スニペット内 `<PLUGIN_DIR>` が shell 非展開で実行時 ImportError になっていた問題を修正。`CLAUDE_PLUGIN_ROOT` 環境変数優先・`os.getcwd()` フォールバックの統一パターン（Python）および `${CLAUDE_PLUGIN_ROOT}` 直接参照（Bash）に統一

## [1.46.0] - 2026-05-11

### Added
- **token usage tracking — PJ 別 LLM トークン消費の SoR + 環境レビュー統合** (#24) — `~/.claude/projects/<pj>/*.jsonl` の `message.usage` を DuckDB SoR (`token_usage` テーブル、PK uuid で冪等 ingest) に取り込み、`rl-fleet status` に `TOKENS_30d` / `CACHE_HIT` 列を追加。`rl-fleet tokens` サブコマンドで TOP-N / WoW スパイク / cache hit 異常検出 / PJ ドリルダウン (session/model/week) / `--backfill` を提供。`audit` レポートに "Token Consumption" セクション追加（TOP 3 + 異常 + ヒント）。subagent token は CC 仕様により親メッセージに内包されるため v1 では分離追跡しない（既知制約として SPEC.md に明記）。
  - 新規: `scripts/lib/token_usage_store.py` (DuckDB schema + idempotent batch ingest), `scripts/lib/token_usage_ingest.py` (transcript パーサ + walker), `scripts/lib/token_usage_query.py` (TOP-N / WoW / cache anomaly / pj_breakdown)
  - 拡張: `scripts/lib/fleet.py` (FleetRow に tokens_30d/cache_hit_pct + tokens サブコマンド), `scripts/lib/audit.py` (Token Consumption セクション)
  - テスト: 24 件追加（store 3 + ingest 9 + query 7 + fleet 5）

### Fixed
- **token usage ingest の write amplification + mtime 差分機能不全** (#28) — 上記 #24 の初期実装を実機検証 (M1 / 1 PJ / `--days 7`) したところ 60 秒+ 未完了で破綻。原因は `token_usage_store.append_batch` が file ごとに `_connect()/close()` を呼び DuckDB の close 時 checkpoint が O(N) で発火する write amplification、および active session の mtime ベース差分が機能しないこと
  - **redesign**: `connection()` context manager で `ingest_all_projects` 全体を 1 connection 化 (checkpoint を 1 回に集約)、`session_progress(pj_id, session_id, last_uuid, last_ts)` テーブルで jsonl 単位の差分 ingest、100 jsonl ごとに transaction commit (クラッシュ時のロスト上限)、`_normalize_record_params()` で None → 0/False 正規化を DRY 化
  - **計測**: rl-anything PJ 1 個 / `--days 7` = **41.2s** (budget 60s) / incremental = **15.7s** (budget 30s) / DB 411 bytes/row (write amplification 解消、575MB → 5MB) / parse/commit=0.20 → commit-bound 確定 (byte-offset seek は採用見送り)
  - **テスト 5 件追加**: `_normalize_record_params` / `connection()` 例外時 close / `session_progress` 差分 ingest / last_uuid drift fallback / chunk commit persistence、加えて bench `pytest -m bench_ingest` opt-in

## [1.45.0] - 2026-05-09

### Added
- **skill 削除時の import 依存検査** (#25) — `scripts/lib/prune.py` に `check_import_dependencies(skill_path, repo_root)` と `SkillDependencyError` を追加し、`archive_file()` が skill ディレクトリ（`skills/<name>`）を archive する際に他スキル/CLI からの `import` や `skills/<name>/` パス参照を `git grep` ベース（フォールバック: pure-Python）で検出するようにした。参照ありで `force=False`（デフォルト）の場合は `SkillDependencyError` を raise して archive を中断する。`force=True` で警告のみで実行可能。単一ファイル archive の既存動作は破壊しない
  - `skills/prune/SKILL.md` Step 4 に依存検査と「依存断ち切り PR を先行させる」フローを明記
  - `scripts/tests/test_prune_dep_check.py` 新設（12 件）
  - `scripts/tests/test_no_orphan_skill_refs.py` 新設（archive 済み skill 名へのオーファン参照を検査する CI smoke test、archive 不在時は skip。import 経路 `from {skill} import` / `import {skill}` も検査対象）
  - **レビュー対応**: `git grep -E` が PCRE 構文（`(?:...)` / `\s`）を解釈しない既存バグを `-P` に切り替えて修正、`import {mod}.sub` / `import {mod} as alias` を検出する正規表現拡張、`SkillDependencyError` メッセージに `force=True` バイパス手順と module 名衝突注意を追記、pure-Python フォールバックを `_iter_text_files` で共通化し O(modules×files) → O(files) に最適化、テスト fixture を git init してgit grep 経路もカバー
  - SKIP: frontmatter `imported-by` の自動更新は今回スコープ外

## [1.44.1] - 2026-05-09

### Fixed
- **rl-loop の依存欠落** — `a9fa34a` で genetic-prompt-optimizer skill 削除時に `scripts/optimize.py` も同時削除されたが、`rl-loop-orchestrator` が `DirectPatchOptimizer` / `OPTIMIZER_SCRIPT` を依然として依存しており rl-loop が機能不全だった。`optimize.py` + tests を復元（SKILL.md は復元せず、内部専用方針を維持）
- **optimize.py の result dict キー誤りで paths frontmatter 提案が発火しない問題** — main() の `result.get("target_path", "")` を `"target"` に修正（5箇所の生成元はすべて `"target"` キー）。CodeRabbit review #26 で検出、smoke test で動作確認

### Docs
- **README をバイリンガル化** — `README.ja.md` を一次 SoT、`README.md` を英訳版として分離。両ファイル冒頭に言語スイッチャーを追加
- **README の実装乖離を修正** — スキル数 23→19、Hooks 数 12→14。存在しないスキル（optimize/update/version/philosophy-review）と hook（suggest_subagent_delegation）の記載を削除。breakthrough スキル、skill_activation_log/tool_duration/post_compact hooks を追加。stop_failure イベント名を Stop → StopFailure に修正
- **SPEC.md update** — Recent Changes に v1.44.1 追記、philosophy-review 行削除、optimize の内部専用化を反映、hooks/skills カウント修正

## [1.44.0] - 2026-05-08

### Added
- **fleet MVP-D: growth-state issues_summary + subagents.jsonl token-load 集計** (#22) — `rl-fleet status` に `ISSUES` 列と `SUBAGENTS_30d` 列を追加し、PJ 横断で問題件数と直近 30 日の subagent 起動数を一覧できるようにした
  - `scripts/lib/issues_summary.py` 新設: `IssuesSummary` dataclass + `compute_issues_summary()` で 5 種カウント（line_violations / hardcoded_values / potential_duplicates / corrections_unprocessed / skill_quality_degraded_count）を集約
  - `audit.py` が audit run のついでに growth-state cache に `issues_summary` を書き込み（旧 cache は欠落 → fleet 側 "—" 表示で後方互換）
  - `fleet.py` に `aggregate_subagents_by_project()` 追加: 30 日窓フィルタ・`(unknown)` フォールバック・破損 1 行 skip・naive UTC 補正を実装
  - テスト 23 件追加（新規 9 + 拡張 14）

## [1.43.0] - 2026-05-08

### Added
- **subagent 乱立検知・抑制機能** — SubagentStop hook がセッション内 subagent 数をカウントし、閾値（デフォルト 5）に達したら `systemMessage` で警告を出力。閾値は `userConfig subagent_warning_threshold` で設定可能。`~/.claude/rules/subagent-guard.md` に Claude への抑制指示を追加（Layer 1）。closes #20
- **hooks/detect-deferred-task.py を repo に追加** — 従来 `~/.claude/hooks/` のみに存在しソース管理されていなかった先送り検出 Stop hook を repo に取り込み。`CLAUDE_PLUGIN_DATA` env var に対応し、テスト時に本番 `deferred_tasks.jsonl` を汚染しない設計に変更
- **audit: 行数違反チェックで plugin / global スキルを除外** — `~/.claude/skills/` 配下のダウンロード品（gstack 等）を行数チェック対象外に。`classify_artifact_origin(path) == "custom"` のスキルのみチェック。実環境で違反 790 件 → 0 件に削減
- **テスト隔離: detect-deferred-task hook テスト** — repo ルート `conftest.py` の `_isolate_plugin_data` autouse fixture が機能するよう hook 本体を `CLAUDE_PLUGIN_DATA` 対応に変更

### Changed
- **sessions: DuckDB SoR 完全移行** — `scripts/lib/session_store.py` 新設(Repository パターン)。`telemetry_query.query_sessions` を sessions テーブル直参照に切り替え。`discover.py` / `evolve.py` / `backfill.py` の sessions.jsonl 直読みを session_store 経由に統一。sessions.jsonl 廃止（legacy バックアップ残置）
- **行数制限: rule の上限を 10 行に統一** — `MAX_RULE_LINES` / `MAX_PROJECT_RULE_LINES` を `3` / `5` から `10` に変更し、CLAUDE.md `code-quality.md` の「rules は統合テーマごとに10行以内」と一致。`scripts/lib/audit.py` の `LIMITS["rules"]` / `LIMITS["project_rules"]` を `line_limit.py` 定数の参照に切り替え（SoT 統一）

### Fixed
- **deferred_tasks.jsonl のテストデータ混入** — `detect-deferred-task` テストが `subprocess.run` 経由で hook 本体を呼び出す際、env var 未設定で本番 `~/.claude/rl-anything/deferred_tasks.jsonl` に書き込んでいた。hook を `CLAUDE_PLUGIN_DATA` 対応にすることで repo ルート `conftest.py` の autouse fixture が効くようになり、本番ファイル汚染を解消（既存 673 件のテスト残骸もクリーンアップ）

## [1.42.0] - 2026-05-07 (CC v2.1.121/126 対応 2026-05-06)

### Removed
- **スキル6個を削除（スリム化）** — `backfill`（初期セットアップ専用）、`version`(`claude plugin list` で代替可)、`update`(`claude plugin update` で代替可)、`feedback`（低頻度 GitHub Issue 投稿）、`philosophy-review`(月次レビュー、日常不要)、`genetic-prompt-optimizer`(`/optimize` 内部呼び出し専用、ユーザー向けでない) を削除。スキル総数 23 → 17 に削減

### Fixed
- **session_store.append DuckDB ロック競合による silent data loss** — instructions_loaded と session_summary が同時に append() すると一方がロック取得に失敗し JSONL フォールバックに流れていた。最大2回リトライ後に JSONL フォールバックするよう修正
- **skill_triage_runner TRIAGE_CACHE_FILE 非アトミック書き込み** — Stop hook が連続発火すると複数インスタンスが同時に write_text() して JSON 破損が起きていた。tmp ファイル書き込み + os.replace() でアトミック化
- **skill_triage_runner._load_jsonl 1行エラーで全件空** — try/except が list comprehension 全体を囲んでいたため、1行でも不正 JSON があると全件 [] に。行単位のエラーハンドリングに変更
- **bin/rl-prompt-compare data_dir リテラルバグ** — `data_dir = "$CLAUDE_PLUGIN_DATA"` が Python 文字列リテラルのままで実際のパスが出力されなかった。os.environ.get() で修正
- **run-loop.py H_best 初期化失敗時の silent skip** — target_path が見つからず global_best_content が None のままループ継続していた。FileNotFoundError 時は即 return [] でアボート
- **run-loop.py NaN/inf スコア伝搬** — _parallel_score に math.isfinite() ガードを追加し、非有限スコアを FALLBACK_SCORE にクランプ
- **rl-prompt-compare _score_with_custom_prompt DRY 違反** — score_noise._score_single_axis と本体を重複実装していた。_run_claude_prompt を score_noise に抽出し両者から利用

### Added
- **skill_activation_log: Skill invocation_trigger を skill_activations.jsonl に記録** — CC v2.1.121 PostToolUse 全ツール対応 + v2.1.126 `skill_activated` OTel invocation_trigger attribute に対応。`workflow_context.py` のコンテキストファイル存在チェックで `nested-skill` / `top-level` を判定し `skill_activations.jsonl` に追記。evolve/audit での誤発火率分析に活用可能。`hooks/hooks.json` に PostToolUse Skill matcher 追加。テスト 7件追加
- **cleanup: claude project purge を Category 7 として追加** — CC v2.1.126 新コマンドに対応。会話履歴・タスク・ファイル変更履歴の一括削除。デフォルトスキップ・不可逆操作のためユーザー明示時のみ実行し、`--dry-run` で影響範囲確認後に個別承認
- **rl-gain: rl-anything ROI 可視化コマンド** (2026-05-03) — `rtk gain` 風の ASCII レポートで rl-anything の効果を可視化。`usage.jsonl` のスキル呼び出し記録から推定節約時間を集計し、Growth Level・Efficiency meter・スキル別 Impact をワンビューで表示。`bin/rl-gain` で直接実行可能。`scripts/lib/growth_level.py` を import しセッション数は `sessions.db` から取得。テスト 25件（正常系 E2E + subprocess smoke test 含む）
- **rl-score-noise: 採点ノイズ計測ツール** — 同一スキルを N 回採点して軸別スコアの標準偏差を算出し、H_best 比較に使う epsilon の推奨値（2σ）を出力。論文 "The Last Harness You'll Ever Build" (Sylph.AI, 2026) の知見に基づき、H_best 駆動実装の前提条件として整備。`bin/rl-score-noise <SKILL.md> [--runs N] [--json]` で実行。`scripts/lib/score_noise.py` に `compute_stats` / `recommend_epsilon` / `aggregate_runs` / `measure_noise` を実装（テスト 8件）
- **rl-loop: H_best 駆動を実装** — `global_best_content/score` をループ間で保持し、2ループ目以降は H_best をディスクに復元してから optimizer を起動。承認時のみ H_best 更新。再採点ノイズを排除するため 2ループ目以降は H_best スコアを baseline に流用（再採点なし）。論文 "The Last Harness You'll Ever Build" Algorithm 1 `E.evolve(history, H_best)` に対応
- **rl-loop: epsilon ベース verdict（IMPROVED/STABLE/REGRESSED）を追加** — 実測採点ノイズ（integrated σ = 0.012〜0.029）に基づき `SCORE_EPSILON=0.05` を設定。`_compute_verdict()` ヘルパーで判定し `history.jsonl` に記録。旧: `improvement <= 0` でスキップ → 新: `|improvement| < 0.05` は STABLE として採点ノイズ範囲と明示。テスト 7件追加
- **rl-loop: REGRESSED verdict → pitfalls 自動転記** — 採点が大きく悪化した variant（improvement < -0.05）を `references/pitfalls.md` に `source=regression` で記録。次回ループで optimizer が同方向の variant を再生成することを抑制。`_record_pitfall` の重複検出により蓄積過多を防止。テスト 3件追加（合計 19件）
- **rl-prompt-compare: Evaluator プロンプト A/B 比較ツール（C-限定版）** — 候補プロンプトを参照スキルで N 回計測し、現行プロンプトとの σ・mean drift を比較。`recommended: a/b/tie` を出力し、平均ドリフトが閾値超なら警告。`scripts/lib/scorer_prompts.py` に `_AXIS_PROMPTS` を集約し、`CLAUDE_PLUGIN_DATA/scorer_prompts/{axis}.txt` でオーバーライド可能。論文 "The Last Harness You'll Ever Build" Meta-Evolution Loop の `Λ.Evaluator prompt 進化` の限定版実装。`bin/rl-prompt-compare <SKILL.md> --axis <name> --candidate <prompt.txt>` で実行。テスト 11件追加（scorer_prompts 9件 + compare_prompt_versions 2件）
- **rl-loop: Pareto dominance チェックを実装** — 軸別スコアを保持し、`_dominates(challenger, defender, tolerance=0.05)` で「全軸で同等以上 + 1軸以上で改善」を判定。integrated 改善でも軸別劣化（例: technical を犠牲にして domain を上げる）があれば IMPROVED → STABLE に格下げ。`_score_variant_axes()` で軸別スコア取得、`history.jsonl` に `best_axes` と `pareto_dominates` を記録。テスト 5件追加（合計 24件）

### Fixed
- **_score_single_axis: リトライ機構を追加** — `claude -p` のタイムアウト/失敗時に即 FALLBACK_SCORE (0.5) を返していたため採点ノイズが肥大化していた（integrated σ 最大 0.091）。max_retries=2 でリトライすることで FALLBACK 混入を排除し、σ を 0.012〜0.029 まで低減。`run-loop.py` と `score_noise.py` の両方に適用（テスト 6件追加）。実測 epsilon 推奨値: **0.05**

### Changed
- **Phase 1: SessionStore Repository 導入 — DuckDB を SoR に** — `scripts/lib/session_store.py` 新設し sessions の永続化を集約。`append` / `count_unique_since` / `query` / `migrate_from_jsonl` / `prune_jsonl` を提供。schema は `sessions(session_id, timestamp, project, type, skill_count, error_count, raw_json)` + timestamp/session_id index。trigger_engine の DATA_DIR ハードコードバグ修正（`CLAUDE_PLUGIN_DATA` 環境変数対応）と min_sessions: 10→3 への引き下げ、`_record_trigger` で全 reasons を history 記録（cooldown 多 reason 対応）。`hooks/session_summary.py` の skill_triage を `subprocess.Popen` で非同期化（Stop hook 5 秒制限対策）。実プロジェクトデータ 45,142 行 → DuckDB sessions テーブル（ユニーク 26,316 セッション、20MB）にマイグレーション完了
- **Phase 2: SessionStore Repository を全 Read 側に展開** — DuckDB を真の SoR に
  - `delete_by_session_ids(session_ids, source=None)` を session_store に追加（backfill rerun 用）
  - `telemetry_query.query_sessions` を sessions テーブル直接参照に切り替え（sessions_file 指定時のみ後方互換 JSONL 読み）
  - `discover.py` / `evolve.py` / `backfill.py` の sessions.jsonl 直読み・直書きを session_store 経由に統一
  - dual-write 停止: `session_store.append` は HAS_DUCKDB=True 時 DuckDB のみに書き込み（HAS_DUCKDB=False のみ JSONL フォールバック）
  - `prune_jsonl()` と `_prune_sessions_jsonl()` を廃止（DuckDB が SoR となり JSONL の rolling pruning が不要に）
  - sessions.jsonl は `sessions.jsonl.legacy.20260430` にリネームしてバックアップ（参照されないが復旧用に残置）

## [1.41.0] - 2026-04-28

### Added
- **CC v2.1.121 対応: tool_duration hook + ${CLAUDE_EFFORT} スキル対応** — `hooks/tool_duration.py` 新設（Bash PostToolUse で `duration_ms` を受け取り、スロー Bash コマンドを `tool_durations.jsonl` に記録。CC v2.1.119+ 対応）。`hooks/hooks.json` の PostToolUse Bash に追加。`skills/evolve/SKILL.md` に `${CLAUDE_EFFORT}` エフォートレベル対応表を追加（low=軽量/medium=通常/high=最大化）。`skills/rl-loop-orchestrator/SKILL.md` に `${CLAUDE_EFFORT}` 対応（low=haiku単体/max=+1ループ。CC v2.1.120+）。`CLAUDE.md` Quick Start に `claude plugin prune` 追記
- **slow_threshold_ms を userConfig 化** — `CLAUDE_PLUGIN_OPTION_slow_threshold_ms` でスロー Bash 判定閾値をユーザーごとに設定可能（デフォルト: 1000ms）。`plugin.json` / `marketplace.json` userConfig に追加

### Fixed
- `tool_duration.py`: `tool_input=None` → AttributeError を防ぐ型ガード追加
- `tool_duration.py`: `duration_ms` が文字列型の場合の isinstance チェック追加
- `tool_duration.py`: records に `project` フィールドを追加（observe.py に準拠）
- `tool_duration.py`: dead code（非 Bash ブランチ）を除去し `handle_tool_duration()` に抽出

## [1.40.0] - 2026-04-27

### Added
- **breakthrough スキル: 汎用ブレイクスルー問題解決** — 「惜しいがなかなか正解にたどりつけない」問題を5フェーズで解決。行き詰まりタイプ（A:評価曖昧 / B:同質盲点 / C:方向不定 / D:情報不足 / E:視点固着）を診断し、Tutor-Student 非対称ロール・MAgICoRe Solver→Reviewer→Refiner 反復ループ・Devil's Advocate 視点転換など研究実証済み戦略を自動選択して Agent 起動まで一貫実行。`/breakthrough <問題>` または「惜しい/ブレイクスルーしない/なかなか」で自動トリガー。戦略カタログ（`references/strategies.md`）とエージェントプロンプトテンプレート（`references/agent-templates.md`）を同梱

## [1.39.0] - 2026-04-27

### Changed
- **implement スキル: 複雑性適応型ワークフロー深度** — Step 0.5 で LLM がチェックリスト判定（新規 API / 3+ モジュール跨ぎ / 外部連携）し shallow/standard/deep を自動選択。shallow は分解テーブル・準拠チェックを省略して即実装、deep は Step 1.5 インターフェース契約確認 + ADR 起票推奨を挿入。テレメトリに `depth` フィールド追加。`CLAUDE.md` の `implement.complexity_hints` で PJ 固有ヒントを上書き可能（AWS AI-DLC tech-eval 由来）

## [1.38.0] - 2026-04-27

### Added
- **memory: APEX-MEM A++ temporal validity + provenance** — APEX-MEM（arXiv:2604.14362）の追記型・有効期間・クエリ時解決の核心を既存スタックで実装。
  - `scripts/lib/memory_temporal.py`: `parse_memory_temporal()` / `is_stale()` / `is_superseded()` / `make_source_correction_id()` — frontmatter なし既存ファイルを安全に処理（後方互換）
  - `hooks/instructions_loaded.py`: `_emit_stale_memory_warnings()` — superseded/stale な memory ファイルを stdout に出力してソフト指示（token フィルタは将来の Event-Centric Rewrite に委ねる）
  - `skills/reflect/scripts/reflect.py`: `build_output()` に `source_correction_id` を追加 — session_id#timestamp 複合キーで corrections の provenance を記録
  - `scripts/lib/audit.py`: `build_temporal_memory_warnings()` — decay_days 超過 / superseded_at 過去の memory を WARN、全 sources が reflect 済み（applied）なら削除候補

## [1.37.0] - 2026-04-24

### Added
- **handover: Issue 化の自動判断** — コンパクト出力後、設計判断・未解決ブロッカー・複数の観察中タスクがある場合のみ「Issue も残しますか？」と提案。単純な作業継続では提案しない。

## [1.36.0] - 2026-04-24

### Added
- **handover: コンパクト形式をデフォルト出力に変更** — 次セッションの冒頭にそのまま貼れる3セクション形式（完了済み・次にやること・観察中）を標準出力に。`--file` でファイル保存、`--deep` で従来の詳細形式（Decisions/Discarded Alternatives）を選択可能。従来の GitHub リポ自動 Issue モードを廃止し、`--issue` フラグ明示時のみ Issue 作成。

## [1.35.0] - 2026-04-24

### Added
- **fleet: user-approved tracked projects list** — `bin/rl-fleet` の scan 対象を `~/tools/*` 固定から、ユーザーが明示承認した PJ 群に切り替え。他の配置（`~/work/`, `~/jomon/`, `~/games/` 等）にある PJ も fleet で一覧できるようになる。
  - **検出**: Claude Code native の `~/.claude/projects/-<slug>/**/*.jsonl` の `cwd` フィールドを読み取り、slug デコードの曖昧性（`-` がパス内文字 vs 分離子）を回避。subagent 配下の nested jsonl も `rglob` で拾う
  - **承認 UX**: `bin/rl-fleet discover` サブコマンドで候補を列挙、各 PJ に対して `a` (track) / `i` (ignore) / `s` (skip=次回再提案) / `q` (quit) を対話入力。結果は `~/.claude/rl-anything/fleet-config.json` に atomic に保存
  - **status 動作**: tracked_projects 設定時はそれを直接 `collect_fleet_status(projects=)` に渡し、未設定時は従来の `--root` 経由で fallback（後方互換）。新候補が検出された場合は status 末尾に hint を表示
  - **home directory 除外**: `$HOME` 自体は CC 本体の `.claude/` を持つため PJ 候補から自動除外
  - `scripts/lib/fleet_config.py` 新設（load/save/discover/filter/diff/track/ignore の 7 関数）+ 18 unit tests + `collect_fleet_status(projects=)` パラメータ追加 + 1 integration test

### Fixed
- **`bin/rl-audit --growth --skip-rescore` の hang 解消 + fleet env_score 表示** (todoroki-godai#86) — fleet が rl-anything PJ のみ `TIMEOUT` で surface する根本修正。原因 2 件:
  1. **`compute_environment_fitness` が `--skip-rescore` でも constitutional（LLM）軸を呼ぶ** — `claude -p` subprocess が 60s timeout × layer 数で fleet の 10s timeout を常に超過。`compute_environment_fitness(skip_llm=True)` を導入し、fleet 経由の `--skip-rescore` から伝播するよう `_build_growth_report(skip_llm=)` パラメータを追加。軽量軸（coherence / telemetry / skill_quality）のみで env_score を算出
  2. **`fleet.py` が `growth-state-<slug>.json` の `progress` フィールドを `env_score` と誤読** — cache 実体は `progress`（phase 進捗）と `env_score`（環境スコア）が別フィールド。`state.get("progress")` → `state.get("env_score")` に修正。テストの fixture も区別するよう更新
  3. **`growth_narrative.compute_profile` で `skill_name=None` record が strengths list に混入** — `', '.join([None])` で `sequence item 0: expected str instance, NoneType found` エラー。None 除外フィルタを追加
  - 効果: rl-anything 自身の audit 時間 60s+（hang）→ **2.7s**、fleet status で `TIMEOUT` → `0.81 Lv.8 OK` 表示。tests 5 件追加（`TestSkipLLM` 2 件 + fleet fixture 更新）

### Changed
- **Repository を todoroki-godai org → todoroki-godai user account に移行** — todoroki-godai/evolve-anything を archive し、todoroki-godai/rl-anything を新正式ロケーションに。GitHub の組織→ユーザー直接 transfer は権限上不可だったため、stale な todoroki-godai/rl-anything へ main を fast-forward push + 100 tags 同期で移行。`plugin.json` / `marketplace.json` / `README.md` インストール手順 / docs 全体の URL 参照を一括更新。旧 todoroki-godai repo は read-only で保存

## [1.34.0] - 2026-04-24
<!-- fleet Phase 1 実装: 2026-04-23 / PR #83 マージ: 2026-04-24 -->

### Added
- **リリースフロー刷新（`claude plugin tag` 導入）** — `.claude/rules/commit-version.md` を更新し、bump 時は plugin.json + marketplace.json + CHANGELOG の三者同期 + main マージ後の `claude plugin tag --push` で `rl-anything--v<version>` タグ作成を明記。過去 chore(release)/feat(vX.Y.Z) コミット 54 件分（v0.4.0〜v1.33.0）の git tag 欠損を historical backfill で復元（release-notes-review v2.1.118 で検出）
- **fleet スキル Phase 1 — `bin/rl-fleet status` CLI**: 全 PJ 横断で rl-anything の健康状態を一覧表示する「4 本目の柱」の基礎実装（issue #68）。`scripts/lib/fleet.py` に 5 つのコア関数を TDD で実装: `enumerate_projects` (`~/tools/*` を `.claude/` or `CLAUDE.md` で絞り込み、ドットディレクトリ除外) / `classify_project` (settings.json `enabledPlugins` + auto-memory 30 日 mtime ハイブリッド 3 値判定 + parse retry) / `run_audit_subprocess` (subprocess で `bin/rl-audit --growth --skip-rescore` 実行、growth-state JSON から env_score/phase/level を取得、TIMEOUT/ERROR 区別) / `format_status_table` (7 列整列 + 相対時刻フォーマット + N/A 表示) / `resolve_auto_memory_dir` (Phase 3 snapshot 準備)。`collect_fleet_status` は `ThreadPoolExecutor(max_workers=2)` で並列化し、STATUS_ENABLED の PJ のみ subprocess audit を呼ぶ最適化。fleet-run 履歴は `<DATA_DIR>/fleet-runs/<ts>.jsonl` に追記。`_DEFAULT_DATA_DIR` は `rl_common.DATA_DIR` を alias し `CLAUDE_PLUGIN_DATA` env を尊重（pre-landing review で発見した silent data mismatch バグを修正）。perf 実測: 7 PJ / 1.05s（設計目標 3s / 6 PJ を大幅クリア）。30 unit tests（refs #68）
- **`skills/release-notes-review/evals/evals.json`** — skill-creator 互換 eval データ（3 ケース）を初 commit。他スキル evals の先例として位置付け。動的生成（`scripts/lib/trigger_eval_generator.py`）とは役割が異なる（手書き回帰テストケース）

### Fixed
- **`.claude-plugin/marketplace.json` の version ドリフトを同期** — `plugin.json` (1.33.0) と `marketplace.json` plugins[0].version (0.8.0) が 33 bump 分乖離していた問題を修正。`claude plugin tag` (CC v2.1.118) が両者整合を要求するためリリースフローの前提整備
- **SPEC.md / CLAUDE.md / spec/api.md の fitness 関数数を 9個 → 8個に統一** — `scripts/rl/fitness/` の実体は 7 ファイル（`coherence` / `telemetry` / `constitutional` / `chaos` / `environment` / `skill_quality` / `plugin`）+ `default`（LLM 汎用評価、専用ファイルなし）= 8個。`config.py` と `principles.py` は supporting モジュール（閾値集約 / 原則抽出）であり fitness ではない。SPEC.md L41 の「9個組み込み」、spec/api.md L33 の「9個: ... `principles`」、CLAUDE.md listing（`plugin` 欠落）を README.md と整合させた（refs #85 Next Actions #4）
- **SPEC.md の hot 86 行 → 79 行に縮小** — L2 caution 閾値（80）超過を解消。Key Design Decisions セクションのカテゴリ別 ADR リスティング（6 行）を `spec/architecture.md#key-design-decisions-カテゴリ別サマリ` へ移動し、SPEC.md は 3 行のポインタに圧縮（refs #85 Next Actions #5）
- **`.gitignore` に scratch ファイル追加** — `.claude/agent-memory/` / `.claude/constitutional_cache.json` / `.claude/principles.json` / `release-notes-review-workspace/` を ignore 対象に追加。`claude plugin tag` の clean working tree チェック通過 + 日常作業での untracked ノイズ削減（release-notes-review v2.1.118 post-merge で検出）
- **`.gitignore` に `prompt-optimizer-bench/` 追加（暫定）** — 2026-03-07 ADR で todoroki-godai org の独立 repo として作成予定だが未実行のまま rl-anything ワーキング配下に置かれていた。独立 repo 化は tracking issue で別タスク化。暫定的に untracked ノイズを解消

## [1.33.0] - 2026-04-22

### Changed
- **`scripts/lib/audit.py` の `DATA_DIR` を `rl_common.DATA_DIR` へ統一** — `audit.py:42` でハードコードされていた `DATA_DIR = Path.home() / ".claude" / "rl-anything"` を削除し、`from rl_common import DATA_DIR` に差し替え。`rl_common.py` は既に `CLAUDE_PLUGIN_DATA` env var をサポートしているため、`audit.py` 経由でも fleet 構想（issue #68）で必要な cross-project データ切替が動作するようになる。`bloat_control.py` の `from audit import DATA_DIR` と既存テストの `patch("audit.DATA_DIR", ...)` は再エクスポート (`audit.DATA_DIR is rl_common.DATA_DIR`) によって互換維持。5 tests 追加（env 未設定/env 指定/空文字 fallback/identity/bloat_control 経路）、全 1547 tests pass

### Added
- **cleanup スキル**: `skills/cleanup/SKILL.md` + `scripts/lib/cleanup_scanner.py` — PR マージ・デプロイ後に残る後片付け（マージ済みローカルブランチ削除・remote refs prune・一時 worktree 削除・一時ディレクトリ削除・関連 Issue close 候補提案・元 PR の Test plan 残件リマインド）を、候補提示→`AskUserQuestion` で個別承認→実行で安全に処理する `/rl-anything:cleanup`。`locked` worktree・現在 checkout 中のブランチ・`main`/`master`/`develop` は削除候補から除外。スキャナは純粋関数 6 本（TDD 24 tests）(closes #69)
- **cleanup: tmp prefix を userConfig 化** — `manifest.userConfig` に `cleanup_tmp_prefixes` (string, カンマ区切り, default `"rl-anything-"`) を追加。`scripts/lib/cleanup_scanner.py::parse_prefix_config` で string → list 変換（trim / 空要素除去 / 重複排除 / `None` 許容）。SKILL.md は `load_user_config` + `parse_prefix_config` 経由で prefix を取得し、実行時に scan scope を `[cleanup] tmp scan scope: [...]` で宣言表示。scanner 側 `_DEFAULT_TMP_EXCLUDE_PATTERNS` は常時有効なので、ユーザーが `claude-` を再追加しても Claude Code runtime / MCP bridge は保護される (closes #71)

### Fixed
- **SPEC.md L75 の PR #38 記載誤り** — 「PR #38 で基盤完了」と記述していたが PR #38 は実際は v1.15.0 (FileChanged hook + MEMORY.md + userConfig) であり cross-project audit の基盤ではなかった。fleet 構想 (issue #68) として再設計する旨に修正。TODOS.md に rl-fleet Phase 3 の `resolve_auto_memory_dir` 特殊文字ケーステスト P2 エントリを追加
- **cleanup scanner: 一時ディレクトリ prefix の危険領域除外** — dogfood (#70) で `scan_tmp_dirs` デフォルト prefix (`claude-` / `gstack-` / `rl-anything-`) が `/tmp/claude-<uid>` (Claude Code runtime) や `/tmp/claude-mcp-*` (実行中 MCP bridge) を削除候補に含めるクリティカルバグを検出。SKILL.md のデフォルト prefix を `rl-anything-` のみに narrow し、scanner 側に `exclude_patterns` を追加して `claude-\d+` / `claude-mcp-*` を二重保護。userConfig 化の拡張は #71 で追跡
- **agents: Stop hook を rl-scorer/second-opinion に追加** — CC v2.1.116 で agent frontmatter `hooks:` が `--agent` 経由でも発火するようになったため、`subagent_observe.py` を Stop フックとして追加。main-thread 起動時もテレメトリが記録される

## [1.32.0] - 2026-04-17

### Added
- **agent-brushup: 自己進化プロトコル** — `create` サブコマンドで生成するエージェント scaffold に Self-Evolution Protocol セクションを必須埋め込み。global/project スコープに応じた定義ファイルパスを生成時に確定し、セッション末尾での自己診断→ユーザー承認→定義更新のサイクルをエージェントに内蔵
- **rl-anything-advisor エージェント** — プロジェクト専用エージェント（`.claude/agents/`）として追加。rl-anything 操作・スキル設計・環境診断・テレメトリ分析に特化

## [1.31.0] - 2026-04-17

### Added
- **agent-brushup: 知識陳腐化防止パターン** — `agent_quality.py` に `knowledge_hardcoding` アンチパターン（閾値3/10で low/medium 分岐）と `jit_file_references` ベストプラクティスを追加。エージェントが知識をハードコードして陳腐化するパターンを診断で検出し、JIT識別子戦略（回答前にファイルを動的確認）の採用を促す。5テスト追加 (closes #67)

## [1.30.1] - 2026-04-17

### Changed
- **implement スキル: plan ファイル名仕様を追記**: `skills/implement/SKILL.md` — CC v2.1.111 以降、plan ファイル名がプロンプト内容由来（例: `fix-auth-race-snug-otter.md`）になった仕様と最新ファイル特定コマンドを追記

## [1.30.0] - 2026-04-16

### Added
- **implement スキル: タスク境界の認知分離**: `skills/implement/SKILL.md` — context: fresh 相当の「認知汚染防止」をStandard モードに追加。タスク開始前にスコープ・インターフェース契約・完了条件を明示し、前タスクの実装詳細はメモリから参照せず Read ツールで確認するよう規定
- **ScorerOutput スキーマバリデーション**: `scripts/lib/scorer_schema.py` — rl-scorer エージェント出力の型付き検証。`frozen dataclass` による `AxisResult` / `ScorerOutput` + `validate_scorer_output()` で必須キー欠損・型不正・範囲外を `ScorerValidationError` で早期検出。`output_evaluator.py` の `_score_axis` を `parsed.get(key, 0.0)` → `parsed[key]` に変更しキー欠損を明示。28テスト

## [1.29.0] - 2026-04-16

### Added
- **TBench2-rl Week 1**: `scripts/bench/golden_extractor.py` — usage.jsonl + corrections.jsonl から GoldenCase（正例/負例ペア）を抽出する基盤を TDD で実装。GoldenCase dataclass / GoldenExtractor クラス / CLI エントリーポイント。24テスト
- **TBench2-rl スパイク**: `scripts/bench/spike_rl_scorer_output_eval.py` — rl-scorer 3軸（技術/ドメイン/構造）の LLM 出力評価転用可否を haiku で検証。結果: 転用可能（integrated 0.767 / domain 0.82 が rl-anything 固有観点を正確評価）
- **TBench2-rl Week 3**: `scripts/bench/mutation_injector.py` — harness に劣化を注入する sentinel system。3パターン（rule_delete / trigger_invert / prompt_truncate）× MutationInjector + SentinelRunner。ライブファイル非書き換え、インメモリ変換。detection_threshold=0.5 で自動判定。39テスト
- **TBench2-rl Week 2**: `scripts/bench/run_benchmark.py` + `output_evaluator.py` — golden_cases.jsonl → haiku 出力生成 → 3軸採点 → benchmark_results.jsonl。BenchmarkResult / BenchmarkRunner / OutputEvaluator / AxisScores。--max-api-calls 100 / --dry-run / score_pre・delta 差分追跡。33テスト。pytest -m bench マーカー追加

### Changed
- **release-notes-review Step 6**: 実装後レビューステップを追加。ファイル変更後に `git diff` が存在する場合、`Skill` tool で `/review` を呼び出して品質ゲートをかける（CC v2.1.108 の built-in slash command via Skill tool 対応）

## [1.28.0] - 2026-04-15

### Added
- **philosophy-review スキル**: Claude Code native セッション履歴 (`~/.claude/projects/<slug>/*.jsonl`) を Judge LLM (haiku) で評価し、`category: "philosophy"` 原則の違反例を corrections.jsonl に注入する月1手動レビュー機能。`reflect` ループに乗せて rule/memory 化判断する設計
- **philosophy seed principles**: `SEED_PRINCIPLES` (principles.py) に Karpathy 4原則 (think-before-coding / simplicity-first / surgical-changes / goal-driven-execution) を `seed: true, category: "philosophy"` で追加。コード経由で全環境配布 (ADR-020)
- **principles.py category enum 拡張**: `_build_extraction_prompt` の category enum に `philosophy` を追加。openspec の seed セクションを「数値固定」から「カテゴリ別構造」に再構造化

### Fixed
- **philosophy-review SEED フォールバック**: principles.json cache が SEED 追加前に生成されていた場合でも philosophy 原則を評価対象にできるよう、`SEED_PRINCIPLES` から直接マージ。cache の `user_defined: true` エントリは優先される
- **philosophy-review hardening (1回目)**: LLM が hallucinate した principle_id を drop、confidence を [0.0, 1.0] に clamp + 非数値を reject、`_slug_from_cwd` を Claude Code 仕様（`.`/`_` 置換+連続 dash 圧縮）に整合し実在 dir fallback を追加、token cap をブロック境界 truncation に変更、prompt injection hardening (BEGIN/END markers + data-not-instructions 宣言)、cache 破損 entry ガード
- **philosophy-review hardening (2回目)**: 先頭巨大ブロック時に後続 tail を保持（以前は head/tail 両方空の場合のみ fallback）、transcript 内の marker 文字列を `[BEGIN_MARKER]`/`[END_MARKER]` に置換し prompt 境界偽装を防止、`_sanitize_violation` が入力 dict を mutate せず shallow copy を返すよう変更

## [1.27.1] - 2026-04-13

## [1.27.1] - 2026-04-13

### Added
- **PostCompact hook**: Compact 後に PreCompact で保存した checkpoint から作業コンテキスト（ブランチ・直近コミット・未コミットファイル）を systemMessage として注入。コンテキスト復元精度が向上

### Fixed
- **usage.jsonl カラム名統一**: `observe.py` の書き込みと DuckDB クエリ層が `timestamp` を使用していたが、実データは `ts` カラムだったため `skill_evolve` フェーズで Binder Error が発生。書き込み・クエリ・テストデータを `ts` に統一 (#59, #61)
- **Skill 使用の self-report 方式に移行**: PostToolUse が Skill ツールに対して発火しない問題の回避策。`bin/rl-usage-log` コマンドを新設し、全17スキルの preamble から self-report。PostToolUse matcher を Agent のみに変更 (#62, #63)

### Moved from SPEC.md Recent Changes
- 2026-04-02: v1.24.0 — **spec-keeper README.md 5層構造** — README.md を外部向け最外層として位置づけ（init/update/status 対応）→ 詳細は [1.24.0] セクション参照
- 2026-04-07: v1.26.0 — **bin/ 移行 (ADR-019)** — bareコマンド13個追加（`rl-audit` 等）、`scripts/lib/` に移設、hooks/common.py re-exporter化、pytest P0解消 → 詳細は [1.26.0] セクション参照
- 2026-04-12: v1.27.0 — **CC v2.1.94+ 統合** — `correction_detect.py` で `explicit`/`guardrail` 系 correction 検出時に `hookSpecificOutput.sessionTitle` を JSON 出力。`implement` / `rl-loop-orchestrator` SKILL.md に CC v2.1.98+ `Monitor` tool ガイド追記（sleep ポーリング代替）→ 詳細は [1.27.0] セクション参照
- 2026-04-13: **PostCompact hook** — Compact 後に checkpoint から作業コンテキスト（ブランチ・直近コミット・未コミットファイル）を systemMessage 注入。hooks/ 14個体制

## [1.27.0] - 2026-04-11

### Added
- **correction_detect.py: hookSpecificOutput.sessionTitle 出力**: CC v2.1.94+ の UserPromptSubmit フック仕様に対応。`explicit` / `guardrail` 系の correction（`remember:`, `don't ... unless` 等）を検出した際、`[{correction_type}] {message 抜粋}` 形式のセッションタイトルを JSON 出力する。plain-text trigger message との混在を避けるため、trigger 発火時は emit しない
- **implement / rl-loop-orchestrator: Monitor tool ガイド**: CC v2.1.98+ の `Monitor` tool を、長時間バックグラウンド subagent の進捗追跡手段として SKILL.md に明記（sleep ポーリング代替）

## [1.26.0] - 2026-04-07

### Added
- **bin/ ディレクトリ**: `rl-evolve`, `rl-audit`, `rl-discover`, `rl-prune`, `rl-reorganize`, `rl-reflect`, `rl-handover`, `rl-optimize`, `rl-loop`, `rl-backfill`, `rl-backfill-analyze`, `rl-backfill-reclassify`, `rl-audit-aggregate` の bare コマンドを追加。PATH に bin/ を追加すれば `python3 <PLUGIN_DIR>/skills/...` 形式の長いコマンド不要 ([ADR-019](docs/decisions/019-plugin-bin-directory-migration.md))

### Changed
- **ライブラリ再設計 (ADR-019)**: `hooks/common.py` のロジックを `scripts/lib/rl_common.py` に移設し、`hooks/common.py` は re-exporter に変更。`scripts/lib/` 配下に `audit.py`, `discover.py`, `prune.py`, `reorganize.py`, `remediation.py` を移設（元の場所は importlib shim に変更）。共通ロジック 30→38 モジュール
- **SKILL.md コマンド更新**: 全スキルの実行コマンドを `python3 <PLUGIN_DIR>/skills/...` から bare コマンド（`rl-audit` 等）に変更
- **pytest P0 解消**: `pytest.ini` に `--import-mode=importlib` を追加し、同名テストファイルのモジュール衝突を解消。1563 tests pass

## [1.25.0] - 2026-04-03

### Changed
- **リリース**: v1.22.2-v1.24.0 の変更を main にマージ（checkpoint セッション分離 / PermissionDenied hook / spec-keeper 5層構造）

## [1.24.0] - 2026-04-02

### Added
- **spec-keeper: README.md 管理対応（5層構造）**: README.md を外部向け（人間ファースト）の最外層として位置づけ。init で情報源に追加・存在しなければ生成提案、update で外部向け変化のみ README.md に反映、status で鮮度チェックを追加。README テンプレート（MVP積み上げ型・頻繁改善型）も同梱

## [1.23.0] - 2026-04-01

### Added
- **PermissionDenied hook**: CC v2.1.89 の新フックイベント対応。auto mode でのパーミッション拒否を errors.jsonl に `type:"permission_denied"` として記録し、discover/evolve でパーミッション設定の改善提案に活用
- **グローバルエージェント maxTurns 設定**: ambiguous-intent-resolver (15), senior-engineer (20) に明示的な maxTurns を追加

### Changed
- **SKILL.md description 250文字対応**: CC v2.1.86 の `/skills` リスト表示 250文字上限に対応。6スキル（evolve-skill, generate-fitness, second-opinion, implement, release-notes-review, spec-keeper）の description を短縮
- **MEMORY.md 圧縮**: プロジェクト構造セクションからコード導出可能な実装詳細を削除（180→73行、60%削減）

## [1.22.2] - 2026-04-01

### Fixed
- **handover checkpoint セッション分離**: checkpoint.json がグローバル1ファイルだったため別プロジェクト・並行セッションのデータで汚染されていた問題を修正。`checkpoints/{session_id}.json` に分離し、`project_dir` フィールドで復元時にフィルタ。旧 checkpoint.json は後方互換で読み取り可能。48h TTL で自動 cleanup。closes #50

## [1.22.1] - 2026-03-31

### Added
- **evolve 通知スヌーズ機能**: `snooze_trigger(hours)` で通知を一時抑制。スヌーズ中は pending-trigger を配信せずファイルを保持。期限切れで自動解除、`clear_snooze()` で手動解除。closes #52

## [1.22.0] - 2026-03-31

### Added
- **implement スキル追加**: plan artifact → タスク分解 → 実装（Standard/Parallel）→ 計画準拠チェック → テレメトリ記録の構造化実装スキル。gstack plan artifact 連携（オプション）、usage.jsonl + growth-journal 記録、worktree 並列対応
- **implement backfill**: git log のリリースコミット間から実装セッションを推定し、テレメトリにバックフィル。冪等性保証付き

## [1.21.3] - 2026-03-31

### Fixed
- **deploy-lock description に PostToolUse lock 解放要件を追記**: デプロイ完了後に lock を自動解放する仕組みが必要であることを明記。lock 未解放による次回 deploy ブロック問題の防止

## [1.21.2] - 2026-03-31

### Added
- **kill-guard RECOMMENDED_ARTIFACT 追加**: deploy-lock 保持中のプロセス kill をブロックする独立エントリ。sys-bots 実運用フィードバックから追加

### Changed
- **worktree-parallel-work description 強化**: `git checkout -b` でのブランチ作成も worktree に誘導。feature-branch rule との PJ 上書き必要性を明記
- **deploy-lock を deploy コマンド専用に分離**: kill ガードは kill-guard エントリに委譲

## [1.21.1] - 2026-03-31

### Fixed
- **deploy-lock description 更新**: 実運用フィードバックを反映 — deploy コマンドだけでなく kill 系コマンドもガード対象であることを明記

## [1.21.0] - 2026-03-30

### Added
- **release-notes-review グローバル環境対応**: `~/.claude/` 配下の rules/skills/agents/settings hooks/memory もスキャン・健康診断できるように。`--env-only` で環境診断だけ実行可能。レポートは Part 1 (Release Notes) + Part 2 (Global Environment Health) の2セクション構成
- **spec-keeper プラグイン一本化**: グローバル版を廃止し、プラグイン版 `/rl-anything:spec-keeper` に統合。handover/discover のパス参照もプラグイン内に更新
- **gstack flow chain 動的化**: `~/.gstack/flow-chain.json` から audit の gstack ワークフロー分析を動的構築。ファイル不在時は fallback 値を使用

### Fixed
- **release-notes-review `--env-only` ガード**: `--env-only` 時に Step 5 バージョン記録をスキップ（リリースノート未確認時の誤記録防止）
- **adversarial review 対応**: phase 型チェック + テスト temp file 修正

### Removed
- **gstack-refine 全参照削除**: audit/discover/spec-keeper から gstack-refine 参照を削除

## [1.19.1] - 2026-03-31

### Fixed
- **handover corrections フィルタ**: `collect_handover_data()` が corrections.jsonl から読み込む際に `project_path` でフィルタリングし、別プロジェクトのデータ混入を防止 (#53)
- **handover usage フィルタ**: usage.jsonl のスキル使用記録も `project` フィールドでフィルタリング（corrections と同じバグパターン）(#53)
- **handover パス正規化**: project_path 比較に `Path.resolve()` を使用し、macOS のシンボリックリンク差異を安全に処理 (#53)
- **handover GitHub リポデフォルト Issue モード**: GitHub リポではフラグなしでもデフォルトで Issue モードを使用するよう変更。`is_github` フィールドをデフォルト出力に追加 (#53)
- **handover --issue 重複呼び出し**: `--issue` パスで `is_github_repo()` が二重に呼ばれていた問題を修正 (#53)

## [1.19.0] - 2026-03-27

### Added
- **handover Issue モード**: `--issue` フラグで GitHub Issue として引き継ぎノートを作成可能に。GitHub リポ検出時は自動提案

### Fixed
- **handover --project-dir cwd 伝播**: `_run_git()` に `cwd` パラメータを追加し、`--project-dir` が git コマンドの実行ディレクトリに正しく反映されるように修正 (#49)
- **synonym_verb テスト安定化**: LLM judge 実呼び出しを mock に変更し非決定的テストを修正

_SPEC.md Recent Changes から移動（既存エントリへの参照）:_
- _2026-03-26: v1.15.0 — [1.15.0] 参照_
- _2026-03-25: handover Deploy State — [Unreleased] 参照_
- _2026-03-27: v1.19.0 — handover Issue モード — [1.19.0] 参照_
- _2026-03-26: v1.18.0 — NFD Level System — [1.18.0] 参照_
- _2026-03-26: v1.17.2 — worktree 並行開発パターン提案 — [1.17.2] 参照_
- _2026-03-26: v1.16.0 — NFD Living Agent Identity — [1.16.0] 参照_

## [1.18.0] - 2026-03-26

### Added
- **NFD Level System**: env_score (0.0-1.0) を Lv.1-10 の 10段階レベル + 日英称号にマッピングする `growth_level.py` を追加。セッション greeting に `Lv.7 Experienced` 形式で表示
- **Fast Shipper trait**: personality_traits に「速攻派」を追加。workflows.jsonl の commit スキル使用頻度 > 2/session で判定
- **audit Growth Report にレベル表示**: `--growth` で env_score + Level + Phase を一覧表示。キャッシュに env_score/level/title を保存

### Fixed
- **η計算反転修正**: 結晶化効率 η が `events/targets` (値域 0-∞) だったのを `crystallized_rules/total_corrections` (0.0-1.0) に修正
- **evolve フェーズ降格防止**: evolve が coherence_score=0.0 でフェーズ判定→キャッシュ上書きしていた問題を修正。audit を唯一のキャッシュ更新権威に変更、evolve は journal 記録のみ
- **journal phase 精度向上**: evolve の emit_crystallization で phase をキャッシュからフォールバック取得するよう変更

### Changed
- **audit coherence_score 正確化**: `_build_growth_report()` が `compute_environment_fitness()` から実際の coherence_score を取得してフェーズ判定に使用

## [1.17.2] - 2026-03-26

### Added
- **worktree 並行開発パターン提案**: discover の RECOMMENDED_ARTIFACTS に `worktree-parallel-work`（stash+checkout 事故防止）と `deploy-lock`（同一環境への並行デプロイ防止）を追加。未導入 PJ に自動提案

## [1.17.1] - 2026-03-26

### Fixed
- **ルール行数カウント誤検出**: `count_content_lines()` が frontmatter 直後の空行をコンテンツ行としてカウントしていた問題を修正 (#47)
- **untagged_reference 分類精度向上**: CLAUDE.md Skills セクション記載スキルの除外 + コンテンツヒューリスティックによるユーザー呼び出し型スキル除外を追加 (#47)

## [1.17.0] - 2026-03-26

### Added
- **spec-keeper スキル同梱**: SPEC.md + ADR 管理スキルを rl-anything プラグインに同梱。`/rl-anything:spec-keeper init` でプロジェクトの仕様全体像を初期化、`update` で最新化
- **Progressive Disclosure レイヤーシステム**: SPEC.md の段階的開示対応。PJ 規模に応じて L1（単一ファイル ~100行）/ L2（hot + cold 2層構造）を自動昇格。Context rot 防止
- **SPEC.md L2 昇格**: rl-anything 自身の SPEC.md を L2 に昇格 — Architecture 詳細を spec/architecture.md に分離し hot 層を 166行→95行に圧縮

## [1.16.0] - 2026-03-26

### Added
- **NFD Growth Engine**: NFD 論文 (arXiv:2603.10808) の Spiral Development Model を実装 — 環境の成熟度を 4 フェーズ（Bootstrap / Initial Nurturing / Structured Nurturing / Mature Operation）で自動判定し、進捗率を可視化
- **結晶化イベント記録**: evolve/reflect が rule/skill を生成・更新するたびに growth-journal.jsonl に結晶化イベントを記録。成長ストーリーの素材に
- **セッション開始時 Growth greeting**: InstructionsLoaded hook 拡張 — セッション開始時に `GROWTH: structured_nurturing 72%` のようなフェーズ情報を stdout 出力（LLM コストゼロ、キャッシュ読み取りのみ）
- **audit --growth**: Growth Report セクション追加 — フェーズ・進捗率・結晶化ログ・環境プロファイル（得意分野・性格特性）・成長ストーリーを一画面表示
- **環境プロファイル**: テレメトリから環境の個性を自動抽出 — 5 つの性格特性（慎重派・整理好き・速攻派・フィードバッカー・探検家）をデータドリブンで判定
- **git log backfill**: 過去の evolve/reflect/remediation コミットから結晶化イベントを復元。既存ユーザーが即座に正しいフェーズ表示を得られる
- **growth_display userConfig**: プラグイン設定で Growth greeting の表示/非表示を制御可能（default: true）

## [1.15.0] - 2026-03-26

### Added
- **ファイル変更の即時検知**: CLAUDE.md や SKILL.md を編集すると、セッション終了を待たずに `/rl-anything:audit` を提案。rules ファイルも watchPaths で自動登録（CC v2.1.83 FileChanged hook）
- **MEMORY.md 25KB ガード**: CC v2.1.83 の 25KB 切り詰め上限を事前検知。audit と bloat_check がバイトサイズを監視し、80%（20KB）到達で警告
- **プラグイン設定の対話化**: plugin enable 時に evolve/audit の頻度やクールダウンを設定可能に。6項目の userConfig（CC v2.1.83 manifest.userConfig）

### Changed
- **トリガー設定の3層マージ**: デフォルト → evolve-state.json → userConfig（環境変数）の優先順位で設定を解決。明示的にセットされた値のみ上書きし、既存設定を潰さない
- **auto_trigger ゲート**: session_summary と file_changed の両方で userConfig の auto_trigger=false を尊重

## [1.14.2] - 2026-03-25

### Fixed
- **SPEC.md**: 構造突合リカバリーで未記載コンポーネントを修正 — hooks 7→11, scripts/lib 25+→27, fitness 7→8

## [1.13.0] — 2026-03-22

### SPEC.md から移動（Recent Changes ローテーション）
- 2026-03-24: gstack v0.10-v0.11 改善パターン6項目移植 — 独立検証、FP排除(12条件)、規模適応、fitness config.py集約、動的重み、/cso×fitness連携、/retro×audit cross-project、原則ベース昇格
- 2026-03-23: handover に SPEC.md 同期ステップ追加（`/spec-keeper update` を自動実行）
- 2026-03-22: v1.13.0 — 検証系スキルのテレメトリ非依存昇格
- 2026-03-22: v1.12.0 — handover スキル追加 + OpenSpec→gstack 移行 Phase 1-2
- 2026-03-20: agent-brushup スキル追加（品質診断 + upstream 監視）

### Added
- **evolve-skill**: 検証系スキル（verify/validate/check/qa等）はテレメトリが少なくても suitability を medium に自動昇格 — 失敗インパクトが大きい検証系は常に自己進化を推奨
- **handover**: Step 4 に SPEC.md 同期を追加 — SPEC.md があれば `/spec-keeper update` を自動実行し、次セッションの Next Actions を最新化

## [1.12.1] — 2026-03-22

### Fixed
- **handover**: PreCompact 提案のクールダウン（1h）を削除 — compaction 自体がレートリミッターなので毎回提案する

## [1.12.0] — 2026-03-22

### SPEC.md から移動（Recent Changes ローテーション）
- 2026-03-20: effort frontmatter 全15スキルに追加
- 2026-03-18: rl-loop --evolve フラグ + evolve-skill 独立コマンド
- 2026-03-18: Superpowers 知見 cherry-pick（合理化防止テーブル + CSO）
- 2026-03-15: pitfall ライフサイクル自動化 + プラグインスキル編集保護
- 2026-03-13: verification knowledge catalog + side-effect 検出
- 2026-03-09: self-evolution + auto-evolve/compression trigger

### Added
- **handover**: 新スキル `/rl-anything:handover` — セッション作業を構造化ノート（.claude/handovers/）に書き出し、別セッションへ引き継ぐ
- **handover**: PreCompact hook でコンテキスト圧縮前に handover を自動提案（1h クールダウン）
- **handover**: SessionStart hook で最新 handover ノートをプレビュー表示（48h staleness）
- **gstack**: audit の Workflow Analytics を OpenSpec → gstack に移行（plan→refine→ship→document→spec→retro ファネル）
- **gstack**: discover の RECOMMENDED_ARTIFACTS に gstack ツール5件追加（gstack-flow-chain, living-spec-awareness, spec-keeper, ship, gstack-refine）
- **gstack**: aggregate_plugin_usage に gstack スキル分類追加

### Removed
- **openspec**: OpenSpec スキル5件を削除（propose/apply/explore/verify/archive）— 新規ユーザーに openspec コマンドが表示されなくなる

### Added
- **agent-brushup**: 新スキル `/rl-anything:agent-brushup` — エージェント定義（~/.claude/agents/）の品質診断・改善提案・新規作成・削除候補提示
- **agent_quality**: `scan_agents()` — global/project エージェント走査（重複時 project 優先）
- **agent_quality**: `check_quality()` — 7項目品質チェック + 6アンチパターン検出 + 6ベストプラクティス照合
- **agent_quality**: `check_upstream()` — agency-agents リポジトリ更新監視（gh api、graceful degradation）
- **observe**: Agent ツール使用時に `agent_name` フィールドを usage.jsonl に記録
- **subagent_observe**: SubagentStop イベントに `agent_name` フィールドを subagents.jsonl に記録
- **skills**: 全15スキルに `effort` frontmatter 追加（CC v2.1.80対応、low/medium/high 3段階）
- **effort_detector**: `infer_effort_level()` — スキル特性から effort レベルを6段階ヒューリスティクスで推定（disable-model-invocation/Agent/行数/キーワード）
- **effort_detector**: `detect_missing_effort_frontmatter()` — プロジェクトスキル走査で effort 未設定を検出+レベル提案
- **issue_schema**: `MISSING_EFFORT_CANDIDATE` 定数 + `make_missing_effort_issue()` factory 関数
- **audit**: `collect_issues()` に effort 未設定スキル検出を統合
- **remediation**: `fix_missing_effort()` / `_verify_missing_effort()` — FIX_DISPATCH/VERIFY_DISPATCH 登録

### Fixed
- **marketplace.json**: `claude plugin validate` で未サポートの `$schema`/`description` を除去

## [1.11.0] - 2026-03-19

### Added
- **tool_usage_analyzer**: `extract_tool_calls_by_session()` — セッションtranscriptからセッション単位でBashコマンドを抽出（recencyフィルタ付き）
- **tool_usage_analyzer**: `detect_stall_recovery_patterns()` — Long→Investigation→Recovery→Longの停滞パターンをセッション横断で検出（confidence算出付き）
- **tool_usage_analyzer**: `stall_pattern_to_pitfall_candidate()` — 停滞パターンからpitfall candidate変換（Jaccard重複排除統合）
- **issue_schema**: `STALL_RECOVERY_CANDIDATE` 定数 + `make_stall_recovery_issue()` factory関数
- **discover**: `run_discover()` に `stall_recovery_patterns` フィールド追加
- **discover**: `RECOMMENDED_ARTIFACTS` に `process-stall-guard` エントリ追加
- **evolve**: Diagnose ステージに stall_recovery_patterns → issue_schema 変換を統合
- **evolve**: レポート Step 10.5「Process Stall Patterns」セクション追加
- **workflow_checkpoint**: `is_workflow_skill()` — frontmatter `type: workflow` 優先 + ヒューリスティクスフォールバックによるワークフロースキル判定
- **workflow_checkpoint**: `CHECKPOINT_CATALOG` — 4カテゴリ（infra_deploy/data_migration/external_api/secret_rotation）のチェックポイントテンプレート + `_CHECKPOINT_DETECTION_DISPATCH` による detection_fn 解決
- **workflow_checkpoint**: `detect_checkpoint_gaps()` — テレメトリ（corrections/errors）から `last_skill` フィルタでチェックポイント不足を検出（タイムアウト保護付き）
- **issue_schema**: `WORKFLOW_CHECKPOINT_CANDIDATE` 定数 + `make_workflow_checkpoint_issue()` factory 関数
- **remediation**: `fix_workflow_checkpoint()` / `_verify_workflow_checkpoint()` — FIX_DISPATCH/VERIFY_DISPATCH 登録
- **discover**: `run_discover()` に `workflow_checkpoint_gaps` フィールド追加（ワークフロースキル走査）
- **evolve**: Diagnose ステージに workflow_checkpoint_gaps → issue_schema 変換を統合
- **evolve**: レポート Step 10.4「Workflow Checkpoint Gaps」セクション追加
- **verification_catalog**: `detect_iac_project()` — IaCプロジェクト判定ゲート（CDK/Serverless/SAM/CloudFormation対応）
- **verification_catalog**: `detect_cross_layer_consistency()` — コード↔IaC間クロスレイヤー整合性検出（環境変数参照・AWS SDK使用 + detected_categories）
- **verification_catalog**: `cross-layer-consistency` カタログエントリ + content-aware install check
- **frontmatter**: `count_content_lines()` — YAML frontmatter を除外したコンテンツ行数カウント
- **path_extractor**: `extract_paths_outside_codeblocks()` 共通モジュール化（audit.py から抽出）
- **reflect_utils**: `PathsSuggestion` dataclass + `suggest_paths_frontmatter()` — correction テキストから paths frontmatter グロブパターンを自動提案
- **reflect**: `route_corrections()` に paths_suggestion 付与（globs 代替注記付き）
- **optimize**: 最適化後の paths frontmatter 提案表示
- **remediation**: `generate_proposals()` が rule_candidate issue に `paths_suggestion` フィールド付加

### Changed
- **line_limit**: `check_line_limit()` / `suggest_separation()` がルールファイルの frontmatter 除外カウントに対応
- **audit**: `check_line_limits()` がルールの frontmatter 除外カウントに対応
- **prune**: `detect_dead_globs()` を `parse_frontmatter()` ベースにリファクタ、`paths` / `globs` 両キー対応

## [1.10.0] - 2026-03-18

### Added
- **skill_evolve**: `assess_single_skill()` — 単一スキルの自己進化適性判定（5軸スコアリング + アンチパターン検出）
- **skill_evolve**: `apply_evolve_proposal()` — SKILL.md セクション追記 + references/pitfalls.md 作成 + バックアップの共通関数
- **evolve-skill**: 独立コマンド `/rl-anything:evolve-skill` — 特定スキルに自己進化パターンをピンポイント組み込み
- **rl-loop**: `--evolve` フラグ + Step 5.5 `_try_evolve_skill()` — 最適化後に自己進化パターン組み込みを提案

### Changed
- **remediation**: `fix_skill_evolve()` を `apply_evolve_proposal()` 呼び出しにリファクタ（DRY 改善、3箇所から共通関数を利用）

## [1.9.0] - 2026-03-18

### Added
- **hooks**: 長時間コマンド検出による subagent 移譲提案 hook（deploy/build/test-suite/install/push/migration の6カテゴリ、同一カテゴリ1セッション1回制限）
- **pitfall_manager**: 合理化防止テーブル自動生成 — corrections.jsonl からスキップパターン検出→テレメトリ突合テーブル生成（`detect_rationalization_patterns` + `generate_rationalization_table`）
- **fitness**: skill_quality に CSO (Claude Search Optimization) 8軸目追加 — description 要約ペナルティ/トリガー語ボーナス/行動促進ボーナス/長さペナルティ
- **verification_catalog**: evidence-before-claims パターン追加 — 「証拠提示義務」の自動検出・未導入PJへの提案
- **discover**: RECOMMENDED_ARTIFACTS に evidence-before-claims エントリ追加
- **evolve**: Housekeeping Phase 4.6 に合理化テーブル生成統合 + レポートに合理化防止テーブルセクション
- **rules**: `verify-before-claim.md`（証拠提示義務）、`root-cause-first.md`（根本原因調査優先）追加

## [1.8.0] - 2026-03-18

### Added
- **hooks**: `StopFailure` hook — APIエラー（rate limit/認証失敗等）によるセッション中断を errors.jsonl に記録
- **hooks**: `InstructionsLoaded` hook — CLAUDE.md/rules ロードを sessions.jsonl に記録（flag file dedup + stale TTL ガード）
- **hooks**: observe.py の Agent 記録に `agent_id` フィールド追加（event payload 由来）
- **hooks**: 全テレメトリレコード（usage/errors/subagents）に `worktree` 情報（name/branch）を追加
- **hooks**: `common.py` に `extract_worktree_info()` ヘルパー + `INSTRUCTIONS_LOADED_FLAG_PREFIX`/`STALE_FLAG_TTL_HOURS` 定数
- **agents**: rl-scorer に `maxTurns: 15` + `disallowedTools: [Edit, Write, Bash]` でコスト制御・安全性向上

### Changed
- **hooks**: `DATA_DIR` が `CLAUDE_PLUGIN_DATA` 環境変数を優先し、未設定時に `~/.claude/rl-anything/` にフォールバック

## [1.7.0] - 2026-03-16

### Added
- **skill_triage**: テレメトリ+trigger evalで CREATE/UPDATE/SPLIT/MERGE/OK の5択スキルライフサイクル判定（Jaccard階層クラスタリング、D10 confidence計算式）
- **trigger_eval_generator**: sessions.jsonl+usage.jsonl → skill-creator互換 evals.json 自動生成（near-miss優先、confidence_weight付き）
- **issue_schema**: `SKILL_TRIAGE_CREATE`/`UPDATE`/`SPLIT`/`MERGE` 定数 + `make_skill_triage_issue()` factory関数
- **evolve**: Diagnose Phase 2.6 に skill triage 統合（discover後、audit前）
- **discover**: `detect_missed_skills()` に `eval_set_path`/`eval_set_status` フィールド追加

## [1.6.0] - 2026-03-16

### Added
- **remediation**: `fix_stale_memory()` — MEMORY.md staleエントリの自動削除（FIX_DISPATCH登録）
- **remediation**: `fix_pitfall_archive()` — pitfall Cold層（Graduated/Candidate/New）の自動アーカイブ（cap_exceeded/line_guard対応）
- **remediation**: `fix_split_candidate()` — LLMによるスキル分割案提示（proposable、ファイル変更なし）
- **remediation**: `fix_preflight_scriptification()` — Pre-flightスクリプト化テンプレート提案（proposable）
- **remediation**: VERIFY_DISPATCH に cap_exceeded/line_guard/split_candidate/preflight_scriptification を追加
- **remediation**: `DUPLICATE_PROPOSABLE_SIMILARITY`/`DUPLICATE_PROPOSABLE_CONFIDENCE` 定数 — duplicate のsimilarityベース proposable 昇格
- **issue_schema**: `SPLIT_CANDIDATE` 定数 + `make_split_candidate_issue()` factory関数
- **pitfall_manager**: `CAP_EXCEEDED_CONFIDENCE`/`PREFLIGHT_MATURITY_RATIO` 定数
- **pitfall_manager**: `pitfall_hygiene()` に `issues`/`preflight_candidates` フィールド追加
- **reorganize**: `run_reorganize()` 出力に `issues` フィールド追加（split_candidates → issue_schema変換）

### Changed
- **pitfall_manager**: Cold層定義を拡張（Graduated + Candidate → + New）、`get_cold_tier()` 更新
- **remediation**: `compute_confidence_score()` で duplicate を similarity ベースに変更（sim≥0.75→confidence 0.60→proposable）
- **openspec-archive-change**: タスク完了率チェック追加（`ARCHIVE_COMPLETION_THRESHOLD = 0.80`、80%未満で警告）
- **evolve SKILL.md**: ファネル分析から verify フェーズを除外（propose→refine→apply→archive の4段階）

### Removed
- **openspec-verify-change**: スキル廃止（利用率7%、archive にタスク完了率チェック統合）

### Previous Unreleased
- **rl-scorer**: オーケストレーター(haiku) + 3サブエージェント並列構成に変更（technical/structural=haiku, domain=sonnet）。評価精度向上 + コスト同等
- **run-loop.py**: `score_variant()` / `get_baseline_score()` を ThreadPoolExecutor で3軸並列スコアリングに改修
- **evolve**: Step 5.6 /simplify ゲート — remediation で .py ファイル変更時に自動品質チェック（後方互換あり）
- **run-loop.py**: `_parallel_score()` / `_score_single_axis()` 関数追加、`AXIS_WEIGHTS` 定数追加

## [1.5.0] - 2026-03-15

### Added
- **pitfall_manager**: pitfall ライフサイクル自動化 — corrections/errors からの自動検出、SKILL.md 統合済み判定、TTL アーカイブ、行数ガード、Pre-flight テンプレート提案 (#30)
  - `extract_pitfall_candidates()`: corrections/errors.jsonl から pitfall Candidate を自動抽出（D6 重複排除、Occurrence-count increment）
  - `detect_integration()`: SKILL.md/references セクション単位 Jaccard 突合で統合済み判定
  - `detect_archive_candidates()`: Graduated TTL（30日）+ Active stale エスカレーション（9ヶ月）
  - `execute_archive()`: 指定タイトルの pitfall を pitfalls.md から削除
  - `suggest_preflight_script()`: Root-cause カテゴリ別テンプレート解決（action/tool_use/output/generic）
  - `_compute_line_guard()`: PITFALL_MAX_LINES（100行）超過時に Cold 層から削除候補生成
  - `extract_root_cause_keywords()`: 「—」分割 → ストップワード除外のキーワード抽出
- **skill_evolve**: 5定数追加（INTEGRATION_JACCARD_THRESHOLD, GRADUATED_TTL_DAYS, STALE_ESCALATION_MONTHS, PITFALL_MAX_LINES, ERROR_FREQUENCY_THRESHOLD）
- **discover**: `run_discover()` に pitfall_candidates 統合（corrections/errors → extract_pitfall_candidates）
- **templates**: `skills/evolve/templates/preflight/` — Pre-flight スクリプトテンプレート 4種（action.sh, tool_use.sh, output.sh, generic.sh）
- **skill_origin**: `scripts/lib/skill_origin.py` — プラグイン由来スキルの origin 判定・編集保護・代替先提案モジュール
  - `classify_skill_origin()`: installed_plugins.json + パスベースのハイブリッド判定（mtime cache invalidation）
  - `is_protected_skill()`: plugin origin のスキルを編集保護対象と判定
  - `suggest_local_alternative()`: 保護スキルのプロジェクト側代替パス（references/pitfalls.md）を提案
  - `generate_protection_warning()`: 保護スキルへの編集警告メッセージ生成
  - `format_pitfall_candidate()`: pitfall_manager Candidate フォーマット生成
  - graceful degradation: 不正JSON/未知version/存在しないパスへの安全なフォールバック
- **reflect**: `suggest_claude_file()` に last-skill コンテキスト層を追加（位置6: always/never 後、frontmatter paths 前）
  - `_resolve_skill_references_path()`: last_skill のスキル references/ パス解決（保護スキルはローカル代替先にリダイレクト）
  - `LAST_SKILL_CONFIDENCE = 0.88` 定数追加
- **remediation**: `classify_issue()` に保護スキルチェック追加 — 保護スキルへの修正は proposable に降格 + `protection_warning` 付与
- **discover**: plugin_summary に `protected: True` フィールド追加

### Changed
- **pitfall_hygiene**: 返却値に `graduation_proposals`, `archive_candidates`, `codegen_proposals`, `line_count` フィールド追加
- **audit**: `_load_plugin_skill_map()`, `classify_artifact_origin()`, `classify_usage_skill()` を `skill_origin.py` に委譲（後方互換ラッパーとして残存）

## [1.4.0] - 2026-03-15

### Added
- **release-notes-review**: リリースノート分析 & 適用提案スキル — Claude Code リリースノートをPJ環境と突合し、優先度別レポート + OpenSpec change 提案
- **line_limit**: `suggest_separation()` — rule 行数超過時に references/ への分離提案を生成（SeparationProposal dataclass、衝突回避）
- **optimize**: gate 不合格（line_limit_exceeded）時に分離提案メッセージを表示、result に `suggestion` フィールド追加
- **remediation**: `fix_line_limit_violation()` が rule ファイルは分離モード（references/ に詳細移動 + 要約書き換え）、skill は従来 LLM 圧縮
- **reflect**: `route_corrections()` で反映先 rule の行数チェック、超過時 `line_limit_warning` 付与
- **openspec**: adopt-claude-code-features 仕様策定完了 — Claude Code v2.1.x 新機能（context:fork, ${CLAUDE_SKILL_DIR}, agent model, skill hooks, PostCompact, auto-memory協調, worktree isolation, effort level, mtime staleness）の適用設計 9 Decision + 8 delta spec

## [1.3.0] - 2026-03-13

### Added
- **issue_schema**: `scripts/lib/issue_schema.py` — モジュール間 issue データ受け渡しの共有スキーマ定数 + factory 関数
  - issue type 定数（TOOL_USAGE_RULE_CANDIDATE, TOOL_USAGE_HOOK_CANDIDATE, SKILL_EVOLVE_CANDIDATE）
  - detail フィールド定数（RULE_FILENAME, HOOK_SCRIPT_PATH, SE_SKILL_NAME 等）
  - `make_rule_candidate_issue()`, `make_hook_candidate_issue()`, `make_skill_evolve_issue()` factory 関数
- **evolve**: skill_evolve assessment を Phase 3.4 に統合（remediation の前に実行）
- **discover**: RECOMMENDED_ARTIFACTS に `commit-version`・`claude-md-style`・`commit-skill` エントリ追加（未導入PJへの提案）
- **verification_catalog**: `scripts/lib/verification_catalog.py` — 検証知見カタログ（detect_verification_needs + detect_data_contract_verification）
  - VERIFICATION_CATALOG 定義、閾値定数（DATA_CONTRACT_MIN_PATTERNS=3, DETECTION_TIMEOUT_SECONDS=5, MAX_CATALOG_ENTRIES=10）
  - discover に verification_needs 検出統合、evolve Phase 3.5 に issue 変換
  - remediation に verification_rule_candidate ハンドラ追加（fix/verify/rationale/proposals）
  - issue_schema に VERIFICATION_RULE_CANDIDATE + make_verification_rule_issue() factory 追加
- **verification_catalog**: `side-effect-verification` エントリ追加（DB操作/MQ/外部API の3カテゴリ副作用検出）
  - テストファイル除外フィルタ、detected_categories 別フィールド、content-aware インストール済みチェック
  - reflect_utils に corrections ベースの副作用パターン検出 + ルーティング追加（優先度3、FP抑制複合パターン）
  - remediation の rationale テンプレートを汎用化

### Fixed
- **evolve**: discover → remediation のデータフロー断絶を修正（issue 変換のフィールド名不一致）
  - rule_candidate: path/commands/alternatives/count/rule_content → filename/target_commands/alternative_tools/total_count/content
  - hook_candidate: path/content → script_path/script_content/settings_diff/target_commands/total_count
- **tool_usage_analyzer**: hook テンプレートの出力を JSON stdout から `exit 2` + stderr に変更（Claude Code Hooks Guide 準拠）

### Changed
- **remediation**: 全 issue type 比較・detail フィールド参照を issue_schema 定数に統一
- **test**: remediation / skill_evolve テストの issue dict を定数参照 + factory 関数に移行

## [1.2.0] - 2026-03-13

### Added
- **evolve**: Mitigation Trend — ツール使用分析のトレンド表示（↑↓→ 件数差・増減率%・pp差）
  - evolve-state.json に `tool_usage_snapshot` を保存、前回との差分を算出
- **evolve**: Bash 割合に目標閾値（≤40%）と達成/未達ラベルを併記
  - BUILTIN_THRESHOLD/SLEEP_THRESHOLD も閾値表示
- **remediation**: Reference Type Auto-fix — `untagged_reference_candidates` の自動修正
  - `update_frontmatter()` で YAML frontmatter に `type: reference` を追加
  - confidence 0.90 で proposable 分類
- **remediation**: line_limit_violation の auto_fixable 拡張（1行超過 → confidence 0.95）
  - LLM 1パス圧縮による自動修正 + 失敗時 proposable 降格フォールバック
- **fitness_evolution**: Bootstrap モード（5-29件: 簡易分析、0-4件: insufficient_data）

### Changed
- **prune**: Step 3 の2段階承認フロー（一括方針選択→個別選択）を廃止し、最初から個別レビューに変更
  - 各スキルの SKILL.md を読み取り、4観点（未使用の背景/今後の使用可能性/重複・統合/参照価値）の分析テキストを出力
  - 1-2件目: アーカイブ/維持/後で判断、3件目以降: アーカイブ/維持/残り全てスキップ
  - SKILL.md Read 失敗時のフォールバック動作を追加
  - Step 2 の推薦ラベル最終判定を Step 3 の個別レビュー内で実行する形に整理

## [1.1.0] - 2026-03-13

### Added
- **discover**: evolve Step 10.2 の mitigation-awareness 機能
  - `RECOMMENDED_ARTIFACTS` に `recommendation_id` + `content_patterns` フィールド拡張
  - `sleep-polling-guard` エントリ新規追加（sleep ポーリング検出）
  - `detect_installed_artifacts()` が `mitigation_metrics`（mitigated/recent_count/content_matched）を返却
- **tool_usage_analyzer**: `check_artifact_installed()` 汎用対策検出関数
  - hook/rule 存在チェック + content_patterns 正規表現マッチ
  - 閾値定数: `BUILTIN_THRESHOLD=10`, `SLEEP_THRESHOLD=20`, `BASH_RATIO_THRESHOLD=0.40`, `COMPLIANCE_GOOD_THRESHOLD=0.90`

### Changed
- **evolve**: Step 10.2 のツール使用改善セクションを対策状態に応じた表示切替に更新
  - 対策済み → 「対策済み (artifacts) — 直近 N 件検出」
  - 未対策 → 従来通り件数と改善提案
  - 全対策済みかつ検出ゼロ → 1行表示
  - 閾値をハードコードからモジュール定数参照に移行

## [1.0.7] - 2026-03-11

### Added
- **discover**: global scope の rule/hook 自動提案機能 (#26)
  - `tool_usage_analyzer` に `generate_rule_candidates()` / `generate_hook_template()` / `check_hook_installed()` 追加
  - `RECOMMENDED_ARTIFACTS` に `avoid-bash-builtin`（rule + PreToolUse hook）追加
  - `detect_installed_artifacts()` で導入済みアーティファクトのステータス表示
- **remediation**: global scope を `proposable` に昇格（`manual_required` → ユーザー承認付き提案へ）
  - `fix_global_rule()` / `fix_hook_scaffold()` を FIX_DISPATCH に追加
  - `tool_usage_rule_candidate` / `tool_usage_hook_candidate` の confidence_score・rationale・proposals 対応

## [1.0.6] - 2026-03-11

### Fixed
- **evolve**: Step 10 推奨アクションが LLM にスキップされる問題を修正
  - 各サブステップを無条件出力に変更（該当なしでも「問題なし」等を表示）
  - セクション見出しに「スキップ厳禁」を明記

## [1.0.5] - 2026-03-11

### Added
- **evolve**: dry-run レポートに「推奨アクション」セクション（Step 10）追加
  - 10.1: reflect 未処理件数の警告と実行推奨
  - 10.2: Built-in 代替可能な Bash コマンド・sleep パターン・Bash 割合の改善提案
  - 10.3: Remediation の auto_fixable / manual_required サマリ

## [1.0.4] - 2026-03-11

### Fixed
- **semantic_detector**: フォールバックを `is_learning=False`（全件除外）→ `is_learning=True`（パススルー）に修正 (#25)
  - partial success 対応: LLM が一部のみ返却時、index マッチングで成功分を適用し残りをパススルー
  - validate_corrections の例外フォールバックも同様に修正
- **discover**: `load_claude_reflect_data()` に `reflect_status == "pending"` フィルタ追加 (#25)
  - evolve の reflect_data_count と reflect の認識を一致させる
- **optimize**: `last_skill` が None の場合の AttributeError を修正 (#24)

## [1.0.3] - 2026-03-09

### Added
- **save_state**: PreCompact hook で作業コンテキスト（git branch/log/status）を checkpoint.json に保存 (#17)
  - 定数: `_MAX_UNCOMMITTED_FILES=30`, `_MAX_RECENT_COMMITS=5`, `_GIT_TIMEOUT_SECONDS=2`
  - 合計 3.5s タイムアウトガードで hook 5000ms 制限内に収束
- **restore_state**: SessionStart hook で committed/uncommitted 分離サマリーを stdout 出力 (#17)
  - work_context なし checkpoint の後方互換性維持
- **CLAUDE.md**: Compaction Instructions セクション追加（完了タスク/スキル結果/変更ファイル/最後の指示）

## [1.0.2] - 2026-03-09

### Fixed
- **diagnose**: FP 4パターン修正 (#23)
  - stale_ref: 数値パターン除外、ファイル位置基準の相対パス解決、不在トップレベルディレクトリ除外
  - orphan_rule: `.claude/rules/` auto-load のため廃止（coherence Efficiency 軸からも削除）
  - claudemd_missing_section: セクション名マッチを `.*[Ss]kills?\b` に柔軟化（prefix 付き対応）
  - line_limit: CLAUDE.md を warning only 化、project rule 5行制限、global rule 3行維持

### Changed
- **coherence**: Efficiency 軸から orphan_rules チェックを削除
- **line_limit**: `MAX_PROJECT_RULE_LINES=5`、`CLAUDEMD_WARNING_LINES=300` 追加
- **environment**: orphan_rules 廃止に伴い constitutional が有効になるケースの期待値対応

## [1.0.1] - 2026-03-09

### Fixed
- **optimize**: SKILL.md・plugin.json・marketplace.json 等の旧GA（遺伝的アルゴリズム）記述を DirectPatchOptimizer/直接パッチ最適化に統一 (#22)

## [1.0.0] - 2026-03-09

### BREAKING CHANGES
- **evolve**: 3ステージ構成の全レイヤー自律進化パイプライン完成（Diagnose→Compile→Housekeeping）
  - v0.x 系の evolve 出力フォーマットとの互換性なし（phases 構造が大幅変更）

### Added
- **environment-fitness**: coherence+telemetry+constitutional 3層ブレンド統合 Environment Fitness (#15)
  - Coherence Score: 構造的整合性4軸（Coverage/Consistency/Completeness/Efficiency）
  - Telemetry Score: テレメトリ駆動3軸（Utilization/Effectiveness/Implicit Reward）
  - Constitutional Score: 原則×4レイヤーの LLM Judge 評価 + Chaos Testing
- **all-layer-compile**: 全レイヤー（Rules/Memory/Hooks/CLAUDE.md）の自動修正・提案生成 (#16)
  - FIX_DISPATCH/VERIFY_DISPATCH による全レイヤー dispatch
  - confidence_score/impact_scope ベースの動的3カテゴリ分類
- **self-evolution**: パイプライン自己改善ループ (#21)
  - pipeline_reflector: trajectory分析・EWA calibration・adjustment proposals
  - trigger_engine: FP蓄積+承認率低下トリガー追加
  - evolve Phase 6: self-evolution フェーズ統合
  - audit `--pipeline-health`: remediation-outcomes.jsonl 集計（LLM不使用）
  - remediation: extended metadata + calibration override
- **auto-evolve-trigger**: セッション終了・corrections蓄積時の自動 evolve/audit 提案 (#21)
- **auto-compression-trigger**: bloat_check() ベースの肥大化自動検出トリガー (#21)

## [0.21.1] - 2026-03-08

### Fixed
- **optimize**: regression gate が LLM パッチによる YAML frontmatter 消失を検出できない問題を修正 (#20)

## [0.21.0] - 2026-03-07

### BREAKING CHANGES
- **optimize**: 遺伝的アルゴリズム（世代ループ）を廃止し、直接パッチモードに置換
  - `--generations`, `--population`, `--budget`, `--cascade`, `--parallel`, `--strategy` オプションを廃止（使用時にエラーメッセージ表示）
  - `GeneticOptimizer` → `DirectPatchOptimizer` に置換
  - 6モジュール削除: strategy_router, granularity, bandit_selector, early_stopping, model_cascade, parallel

### Added
- **optimize**: corrections/context ベースの LLM 1パス直接パッチ最適化
  - `--mode auto|error_guided|llm_improve` オプション追加
  - corrections.jsonl からエラー分類し直接パッチ（error_guided モード）
  - usage 統計・audit issues・pitfalls をコンテキストに含めた汎用改善（llm_improve モード）
  - history.jsonl に `strategy`/`corrections_used` フィールド追加
  - `_extract_markdown` を複数ブロック対応に改善（最長ブロック返却）
- LLM コール数を 6〜15+ → 1回に削減

### Changed
- README.md, CLAUDE.md, docs/evolve/optimize.md の遺伝的アルゴリズム記述を直接パッチに更新
- rl-loop-orchestrator SKILL.md の説明を直接パッチに更新、API コスト目安更新

## [0.20.0] - 2026-03-07

### Added
- **optimize**: 大規模スキル向け budget_mpo パイプライン — 6モジュール+205テスト
  - strategy_router: ファイルサイズに基づく self_refine/budget_mpo 自動選択
  - granularity: 適応的粒度制御（none/h2_h3/h2_only 3段階分割）
  - bandit_selector: Thompson Sampling によるセクション選択 + LOO 重要度推定
  - model_cascade: FrugalGPT 3段カスケード（haiku→sonnet→opus）
  - early_stopping: 4条件停止（品質到達/プラトー/バジェット/収穫逓減）
  - parallel: references/ 並行最適化 + de-dup consolidation
  - optimize.py に Phase 0-3 パイプライン統合、Prefix Caching 対応
  - SKILL.md に `--budget`/`--strategy`/`--cascade`/`--parallel` オプション追加

## [0.19.6] - 2026-03-06

### Added
- **discover**: 推奨ルール/hook 未導入検出を追加 — 先送り禁止ルール+Stop hook の導入提案
- **audit**: skill/rule内ハードコード値検出 — 5パターン+許容除外+インライン抑制

## [0.19.5] - 2026-03-06

### Fixed
- **audit**: パス抽出の偽陽性を修正 — MEMORY 内の説明的スラッシュ表現（`usage/errors`, `discover/audit` 等）がファイルパスとして誤検出されなくなった

### Added
- **classify**: conversation を5サブカテゴリに細分化（approval/confirmation/question/direction/thanks）

## [0.19.4] - 2026-03-06

### Fixed
- **optimize**: 最適化結果の accept/reject 確認フローを SKILL.md に追加 — `history.jsonl` に `human_accepted` が記録されるようになり、evolve-fitness が機能する

## [0.19.3] - 2026-03-06

### Added
- **tool-usage-analysis**: discover にツール利用分析フェーズを追加
  - セッション JSONL からツール呼び出しを抽出し、Bash コマンドを3カテゴリに分類（builtin_replaceable / repeating_pattern / cli_legitimate）
  - `--tool-usage` フラグで有効化、evolve 経由では自動有効化
  - builtin_replaceable をルール候補、repeating_pattern をスキル候補として出力

## [0.19.2] - 2026-03-06

### Added
- **remediation-engine**: evolve パイプラインに Remediation フェーズ（Step 7.5）を追加
  - confidence_score / impact_scope ベースの動的3カテゴリ分類（auto_fixable / proposable / manual_required）
  - 修正理由（rationale）付きの一括承認 / 個別承認フロー
  - 陳腐化参照の自動削除、行数超過に対する修正案生成
- **remediation-verification**: 修正後の2段階検証（Fix Verification + Regression Check）
  - regression 検出時に自動ロールバック
- 修正結果を `remediation-outcomes.jsonl` に記録（dry-run 時スキップ）

## [0.19.1] - 2026-03-06

### Added
- **reference-skill-classification**: 参照型スキルを自動判定し prune の淘汰対象から除外
- **reference-drift-detection**: 参照型スキルの内容とコードベースの乖離度を評価
- **audit-untagged-warning**: ゼロ呼び出し + `type` 未設定のスキルを audit レポートで警告

## [0.19.0] - 2026-03-06

### Added
- **missed-skill-detection**: 「スキルが存在するのに使われなかった」パターンを検出・レポート
- **scope-aware-routing**: reflect の修正反映先をプロジェクト固有シグナルで自動判定

## [0.18.1] - 2026-03-06

### Fixed
- backfill: usage/workflows/sessions レコードに `project` フィールドが欠落していた問題を修正

## [0.18.0] - 2026-03-06

### Added
- **cross-project-telemetry-isolation**: observe hooks に project フィールド追加、プロジェクト単位のテレメトリ分離
- discover/audit: `--project-dir` によるプロジェクト単位フィルタリング
- **interactive-merge-proposal**: reorganize 検出の中類似度ペアに対して対話的統合提案

## [0.17.0] - 2026-03-05

### Added
- **agent-type-classification**: 組み込み Agent をメインランキングから除外し `agent_usage_summary` に分離

### Changed
- `determine_scope()` が `agent_type` フィールドを優先参照するように拡張

## [0.16.0] - 2026-03-05

### Added
- **usage-scope-classification**: プラグインスキルの動的検出とレポート分離表示
- **OpenSpec Workflow Analytics**: ファネル・完走率・フェーズ別効率・品質トレンド・最適化候補

### Changed
- audit レポートの Usage を PJ 固有スキルのみに変更、Plugin usage サマリを追加

## [0.15.6] - 2026-03-05

### Added
- **Memory Semantic Verification**: audit に LLM セマンティック検証を追加（CONSISTENT / MISLEADING / STALE 判定）
- **archive Memory Sync**: openspec-archive 時に MEMORY への影響を分析し更新ドラフトを提示

## [0.15.5] - 2026-03-05

### Added
- **audit Memory Health**: MEMORY ファイルの健康度セクション追加（陳腐化参照検出・肥大化早期警告）
- **reflect memory_update_candidates**: corrections と既存 MEMORY のキーワードマッチによる更新候補検出

## [0.15.4] - 2026-03-04

### Added
- **merge-group-filter**: reorganize 由来 merge_groups に TF-IDF コサイン類似度フィルタを適用し偽陽性を排除

## [0.15.3] - 2026-03-04

### Added
- **quality_monitor**: 高頻度スキルの品質スコアを定期計測し劣化を検知
- audit レポートに "Skill Quality Trends" セクション追加（スパークライン・DEGRADED マーカー）

## [0.15.1] - 2026-03-04

### Added
- **similarity-engine**: TF-IDF + コサイン類似度の共通計算エンジン
- corrections の矛盾検出（`detect_contradictions()`）

### Changed
- `semantic_similarity_check()` を TF-IDF 実装に置換し誤検知 465 件を解消

## [0.15.0] - 2026-03-04

### Added
- **merge-suppression**: merge 統合候補の却下を記録し次回以降の再提案を抑制

## [0.14.0] - 2026-03-04

### Added
- **smart-prune-recommendation**: prune 候補に description + 推薦ラベル（archive推奨/keep推奨/要確認）を付与
- **2段階承認フロー**: AskUserQuestion の options 上限を遵守した段階的承認 UI

## [0.13.0] - 2026-03-04

### Breaking Changes
- **scripts/ 二重管理の解消**: `scripts/*.py` を削除し `skills/*/scripts/` に一本化

### Added
- **LLM 入力サニタイズ**: corrections データのサニタイズ（500文字切り詰め、制御文字除去、XML タグ除去）
- **偽陽性フィードバック機構**: corrections の偽陽性を SHA-256 ハッシュで管理・自動フィルタリング
- **ファイルパーミッション強化**: データディレクトリ 700、JSONL 新規作成時 600

## [0.12.0] - 2026-03-04

### Added
- **Reflect スキル**: `/rl-anything:reflect` — corrections.jsonl の修正フィードバックを CLAUDE.md/rules に反映
- **discover --session-scan**: セッション JSONL のユーザーメッセージを直接分析し繰り返しパターンを検出
- **セマンティック検証デフォルト有効**: corrections のセマンティック検証をバッチ送信でデフォルト有効化
- **evolve Reflect フェーズ**: evolve パイプラインに Reflect ステップを追加

## [0.11.0] - 2026-03-04

### Added
- **Enrich Phase**: Discover のパターンを既存スキルに Jaccard 係数で照合し改善提案を生成
- **Merge サブステップ**: Prune 内で重複スキルの統合版生成→ユーザー承認→アーカイブ
- **Reorganize Phase**: TF-IDF + 階層クラスタリングでスキル群を分析し統合/分割候補を提案

## [0.10.3] - 2026-03-03

### Added
- evolve に fitness 関数チェックステップ追加: 未生成時に `generate-fitness --ask` を促す
- evolve に fitness evolution ステップ追加: accept/reject データから評価関数の改善を提案
- rules を淘汰対象から除外し情報提供のみに変更

## [0.10.2] - 2026-03-03

### Fixed
- global スキル判定を hooks データのみに限定し、backfill データでの誤判定を解消

## [0.10.1] - 2026-03-03

### Fixed
- `load_usage_registry()` が usage-registry.jsonl 不在時にフォールバック（global スキルが全て未使用扱いになる問題を修正）

## [0.10.0] - 2026-03-03

### Added
- **Correction Detection**: ユーザーの修正フィードバックをリアルタイム検出し `corrections.jsonl` に記録
- **Confidence Decay**: 時間減衰 + correction ペナルティで淘汰精度を向上
- **Pin 保護**: `.pin` ファイル配置でスキルを淘汰対象から除外
- **Multi-Target Routing**: 改善先の自動振り分け（correction > prune > claude_md > rule）
- **Backfill Corrections**: 過去トランスクリプトから修正パターンを遡及抽出

## [0.9.1] - 2026-03-03

### Fixed
- hooks が発火しない致命的バグを修正: `hooks.json` の配置場所と matcher 形式を修正

## [0.9.0] - 2026-03-03

### Added
- ワークフロー統計分析: workflows.jsonl からスキル別統計を算出
- `generate-fitness --ask`: 品質基準を対話的に質問し `fitness-criteria.md` に保存
- rl-scorer にワークフロー効率性の補助シグナル追加

## [0.8.0] - 2026-03-03

### Added
- `Task` ツール対応: 旧 Claude Code の `Task`（= 現 `Agent`）を同等に処理
- ビルトインコマンドフィルタ: `/clear`, `/compact` 等 18 コマンドをスキル起動から除外

### Changed
- ワークフロー捕捉率: 5PJ 合計 50 → 301 ワークフロー（6倍増）

## [0.7.0] - 2026-03-03

### Added
- team-driven ワークフロー検出: TeamCreate → Agent → TeamDelete パターンを追跡
- agent-burst ワークフロー検出: 300秒以内の連続 Agent 呼び出しを自動グルーピング
- `command-name` ワークフローアンカー

### Changed
- ワークフロー捕捉率: 4.2% → 26.2%

## [0.6.0] - 2026-03-03

### Added
- システムメッセージのノイズフィルタ（中断シグナル、ローカルコマンド出力、タスク通知を除外）
- `user_prompts` 収集: セッションメタに記録

### Changed
- subprocess 廃止: `backfill.py` を直接 import して実行（セキュリティ改善）

## [0.5.0] - 2026-03-03

### Added
- **出自分類**: スキル/ルールを custom / plugin / global に分類
- プラグイン由来スキルを淘汰候補から除外し `plugin_unused` として表示
- evolve レポートに Custom / Plugin / Global の出自別3セクション表示

## [0.4.1] - 2026-03-03

### Added
- `classify_prompt()` のキーワード拡充: 6 新カテゴリ + 日本語キーワード
- LLM Hybrid 再分類: キーワードで "other" に残ったプロンプトを Claude が再分類

## [0.4.0] - 2026-03-03

### Added
- プロジェクト単位のデータ分析: `--project` フィルタ

## [0.3.3] - 2026-03-03

### Added
- `/rl-anything:version` スキル: インストール済みバージョンとコミットハッシュを確認

## [0.3.2] - 2026-03-03

### Added
- Backfill データ収集範囲の拡張: セッションメタデータ（tool_sequence, duration, error_count 等）

## [0.3.1] - 2026-03-03

### Added
- Backfill ワークフロー構造抽出: ワークフロー境界検出 + workflows.jsonl 生成

## [0.3.0] - 2026-03-03

### Added
- ワークフロートレーシング: Skill 呼び出し時にワークフロー文脈を記録
- Discover に contextualized/ad-hoc/unknown の3分類追加

## [0.2.5] - 2026-03-03

### Added
- `/rl-anything:backfill` スキル: セッショントランスクリプトから usage.jsonl にバックフィル

## [0.2.4] - 2026-03-03

### Added
- SubagentStop フック: subagent 完了データを `subagents.jsonl` に記録
- PostToolUse で Agent ツール呼び出しを観測

### Fixed
- hooks.json の `$PLUGIN_DIR` を公式仕様 `${CLAUDE_PLUGIN_ROOT}` に修正

## [0.2.3] - 2026-03-03

### Fixed
- `detect_dead_globs` の誤検知: `{ts,tsx}` ブレース展開に対応

## [0.2.2] - 2026-03-02

### Fixed
- スクリプトをプラグイン公式構造に準拠する配置に修正

## [0.2.1] - 2026-03-02

### Fixed
- SKILL.md の `$PLUGIN_DIR` 記法を `<PLUGIN_DIR>` に統一

## [0.2.0] - 2026-03-02

### Added
- **Observe hooks**: PostToolUse/Stop/PreCompact/SessionStart の4フック
- **Audit**: `/rl-anything:audit` 環境健康診断
- **Prune**: `/rl-anything:prune` 未使用アーティファクト淘汰
- **Discover**: `/rl-anything:discover` 行動パターン発見
- **Evolve**: `/rl-anything:evolve` 全フェーズ統合実行
- **Evolve-fitness**: `/rl-anything:evolve-fitness` 評価関数の改善提案
- **Feedback**: `/rl-anything:feedback` フィードバック収集

## [0.1.0] - 2026-03-01

### Added
- **Genetic Prompt Optimizer**: `/rl-anything:optimize` スキル/ルールの遺伝的最適化
- **RL Loop Orchestrator**: `/rl-anything:rl-loop` 自律進化ループ
- **Generate Fitness**: `/rl-anything:generate-fitness` 適応度関数の自動生成
- **rl-scorer エージェント**: 技術品質 + ドメイン品質 + 構造品質の3軸採点
