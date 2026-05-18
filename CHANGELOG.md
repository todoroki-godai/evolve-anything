# Changelog

## [Unreleased]

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

## [1.42.0] - 2026-05-07

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
