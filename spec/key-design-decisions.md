# Key Design Decisions

> このファイルは SPEC.md から分離された cold 詳細仕様です。
> 概要は [SPEC.md](../SPEC.md) を参照してください。

全52件。カテゴリ別要約は [architecture.md#key-design-decisions-カテゴリ別サマリ](architecture.md#key-design-decisions-カテゴリ別サマリ)、原文は [../docs/decisions/](../docs/decisions/) を参照。

## Frozen Executor + Trainable Curator（SkillOS 設計との同型性）

evolve-anything は **Claude Code を frozen executor**、**plugin 層を trainable curator** として
分離する設計を採用する（[ADR-023](../docs/decisions/023-skillos-frozen-executor-trainable-curator.md)）。この設計は SkillOS 論文（Ouyang et al., 2026, arXiv:2605.06614）
が独立に実証した同型アーキテクチャと一致する。

SkillOS の報酬設計から取り込んだ要素:
- **r^comp**: skill 数 / invocation 数 による圧縮ペナルティ（skill バブル防止）
- **r^fc**: skill 別エラー率から推定する valid tool call 率

evolve-anything の優位点（SkillOS 対比）:
- skill_triage の 5 択（SPLIT/MERGE を含む）vs SkillOS の 3 操作
- regression gate（`scripts/lib/regression_gate.py`）による safety 層

詳細: docs/research/skillos-tech-eval.md / [ADR-023](../docs/decisions/023-skillos-frozen-executor-trainable-curator.md)

## 4層メモリ結晶化（MemOS 対応設計）

evolve-anything の corrections→evolve パイプラインは MemOS / HiMem（arXiv:2601.06377）の
L1→L4 結晶化アーキテクチャと同型の設計を採用する（[ADR-024](../docs/decisions/024-memory-crystallization-memos-correspondence.md)）。

| MemOS 層 | evolve-anything 対応 |
|---------|-----------------|
| L1 トレース | `corrections.jsonl` / `sessions.jsonl` 等（Observe hooks が記録） |
| **Episodic 層** | `episodic.db`（DuckDB TTL 30d、`/reflect` approve で昇格。`episodic_store.py` / `episodic_retriever.py`）— L1 と L2 の橋渡し。クロスセッション短期記憶 |
| L2 ポリシー | `MEMORY.md` (auto-memory、`/reflect` で更新) |
| L3 ワールドモデル | `rules/*.md` + `CLAUDE.md`（`/evolve` で昇格） |
| L4 結晶化スキル | `.claude/skills/*.md`（`skill_triage` / `/evolve-skill` で生成） |

**ギャップマッピング（将来検討）**:

- **未実装: 層間矛盾検出** — L2（MEMORY.md）と L3（rules）の矛盾エントリを自動検出する仕組みがない
- **未実装: 自動 reconsolidation** — MemOS が定義する下向き伝播（上位層変更が下位層を更新）も未実装
- **未実装: ハイブリッド検索** — MEMORY.md は現状線形スキャン。MemOS/HiMem が提案する
  ベクトル検索 + 構造検索のハイブリッドは未実装
- **参照**: MemOS/HiMem (Zhang et al., 2026, arXiv:2601.06377)、[ADR-024](../docs/decisions/024-memory-crystallization-memos-correspondence.md)

## 直近 ADR 履歴

ADR-052 より前の履歴。直近: [ADR-051](../docs/decisions/051-result-dependent-capture-drain-migration.md) result 依存キャプチャ副作用（calibration state / tool_usage_snapshot / growth 結晶化）を `evolve --drain` の apply 境界へ「値運搬」移植 — dry-run が `--output` で書いた result JSON を drain が `--result-json` で消費して発火、result-json 欠落は3項目のみ graceful skip、Accepted、#146）。直前: [ADR-050](../docs/decisions/050-daily-evolve-pull-learning-material.md) daily-evolve を pull 型・学習素材ベース待ち判定で全 PJ 横断 evolve 待ちを決定論列挙〔push 型・corrections_unprocessed 閾値・活動量ベース主軸を却下、per-PJ last_evolve は新ストア evolve-queue-state.jsonl〕、Accepted、#78/#79/#80）。直前: [ADR-049](../docs/decisions/049-write-barrier-single-store-write-gate.md) 全ストア書込を `store_write` 単一ゲートに集約し runtime guard（既定 reject・未登録/非active 書込を `StoreWriteError` で強制）で塞ぐ — read（union 寛容）と write（canonical 厳格）を分離し共有は store_registry のみ・例外口は別名関数 `store_write_raw`、Accepted、#55）。直前: [ADR-048](../docs/decisions/048-evolve-py-staged-package-split.md) `evolve.py` 1739行を `evolve/` パッケージ（`__init__.py` 156行 + sub-module: `_env`/`_capture`/`_state`/`_report`/`_context`/`phases_diagnose`/`phases_remediate`/`phases_capture`/`cli`）へ段階分割 — 8 PR 連続 squash merge・各 PR で keyset snapshot 不変＝振る舞い中立を担保・束縛フェンスで `setattr(evolve, ...)` monkeypatch すり抜けの silent fail を構造防止、Accepted、#531）。直前: [ADR-047](../docs/decisions/047-human-confirmed-idiom-autopromote-proxy.md) confirmed idiom と同テキストの再発 weak_signal は human-confirmed proxy として機械昇格を許容 — 安全弁3つ（daily_cap / observability 常時 surface / `evolve-reflect --revoke-idiom` 巻き戻し）、#447）。直前: [ADR-046](../docs/decisions/046-outcome-metrics-v1-advisory-then-weight-promotion.md) 行動アウトカム3軸（correction 再発率 / 一発成功率 / rework 率近似）は advisory 並走 2-4 週→分布実測→重み昇格判断の段階導入。rework は既存ストアに編集対象ファイル ID が無いため tool_sequence 編集バーストの近似 proxy とし限界を明記、#423）。直前: [ADR-045](../docs/decisions/045-evolve-drain-enforcement-marker-and-sessionstart.md) evolve drain（Step 7.8）の enforcement を `evolve --drain`〔CLI 単一コマンド・tool 文脈〕 + env 非依存マーカー〔emit が dry-run でも記録〕 + SessionStart リマインド〔`undrained_applied` で適用済み未 drain を検出・store 非読込〕で担う（#402）。ingest が SKILL.md prose 依存だった `SKILL.md MUST ≠ enforcement` の穴を塞ぐ。素直な Stop hook auto-drain は #358〔DATA_DIR hook/tool split〕を踏むため不採用、全て tool 文脈に閉じて回避。second-opinion 反映、実 CLI E2E で store +1 実証。決定論・LLM 非依存）。直前: [ADR-044](../docs/decisions/044-spec-trigger-on-merge-sessionstart.md) main 着地の挙動コード変更×仕様アーティファクト未更新を SessionStart で検出し spec-keeper/ADR を1回提案（#391、`gh pr merge` 直叩き・web squash の穴を SessionStart 一択で塞ぐ）。直前: [ADR-042](../docs/decisions/042-hook-store-dir-resolver-not-datadir-unification.md) DATA_DIR が hook 文脈（env 有）と tool 文脈（env 無 fallback）で別 dir に解決され正準 dir が割れる #358 問題を、reader 正準化（marker ゲート redirect）→ `data_dir_migration` の DuckDB rebuild マージ（src+old を per-table UNION + atomic replace、書込→削除→冪等 marker）で一元化（#364/#414、`evolve-fleet migrate-data`）。素直な Stop hook auto-drain でなく全て tool 文脈に閉じて split 再発を回避。decompose した残課題（merge_db のスキーマ乖離・並行書込・行折り畳み）は follow-up #417。さらに前: [ADR-039](../docs/decisions/039-evolve-result-output-file-not-stdout.md) evolve の巨大 result JSON（実測 116KB）は `--output <path>` でファイル化し stdout には1行サマリのみ出す。SKILL が多段で参照する設計なのに stdout 一発出力していたため `head`/Bash 出力上限で `indent=2` JSON が途中切断され「JSON が不完全→保存し直し」のやり直しが多発していた出力契約ミスマッチを、コード側 `--output` + SKILL の Read 一本化で解消。未指定は従来 stdout で後方互換。決定論・LLM 非依存）。直前: [ADR-038](../docs/decisions/038-stop-hook-additional-context-subagentstop-only.md) Stop/SubagentStop の `additionalContext`（CC v2.1.163）は SubagentStop のみ採用・Stop は HOLD。SubagentStop の閾値超過警告を `systemMessage`〔user 向け〕に加え `additionalContext`〔Claude 向け〕でも出し subagent-guard.md を実エンフォース。Stop は「keep the turn going」セマンティクスが Auto Trigger 非介入方針とどちらの解釈でも衝突するため実測前に却下。決定論・LLM 非依存）。直前: [ADR-037](../docs/decisions/037-eliminate-claude-p-consolidate-llm-into-interactive-evolve.md) `claude -p` 全廃と LLM を interactive `/evolve` に集約（2026-06-15 の Agent SDK クレジット分離に対応。課金境界は起動方式＝`claude -p` non-interactive vs 対話ターミナル、公式 support で対象列挙を確認。hook=決定論データ収集のみ／`/evolve`=唯一の LLM 消費口、Python は LLM-free 化で no-llm-in-tests と整合、phased 実装。**Phase 1a-1d 実装済み**: 共通基盤 `llm_broker` 抽出 + `world_context`/`quality_monitor`（1a）+ `constitutional`/`principles`（1b、principles round→constitutional round の順依存を `principles_missing` で担保）+ `skill_evolve` の `llm_scoring`/`proposal`（1c、judgment_source flag で static/llm を識別し収束）+ `semantic_detector`/`critical_instruction_extractor`（1d-i #323）+ remediation `fixers_rules`/`fixers_quality`（1d-ii #324）+ auto_memory Stop hook（Phase 2 #327 — hook をゼロ LLM enqueuer 化し生成を `auto_memory_broker` の evolve drain へ吸収）の2相化 + audit パイプライン decouple、機構は M1〔emit→インライン Phase B→ingest〕に決着。**全 Phase 完了**、本流の claude -p caller は全廃（`KNOWN_REMAINING` = DEPRECATED な score_noise のみ）。回帰ゲート `test_no_claude_p_phase1a.py` の `CONVERTED_MODULES`/`KNOWN_REMAINING` で変換状況を明示）。直前: [ADR-036](../docs/decisions/036-hook-drift-stale-pin-first.md) 他ツール追従 hook の陳腐化検出を stale_pin に限定して observability contract に登録。汎用 hook_drift 案[dead_ref/internal_drift]は表記ゆれ false positive・YAGNI で却下し別 issue #316-#318 に分離、second-opinion レビュー反映）。直前: [ADR-034](../docs/decisions/034-split-archive-mutual-exclusion-archive-wins.md) evolve の split↔archive 矛盾を `reconcile_split_archive` が prune 直後に本流で解消（archive 優先、#301 #302）。直前: [ADR-033](../docs/decisions/033-evolve-introspect-self-analysis-issue-filing.md) evolve 完了後に `result` 全体を決定論で読み 3 カテゴリ[提案矛盾 / phase 例外 / 系統的却下]の issue 候補を生成する自己解析を、observability builder（project_dir のみ）でなく独立モジュール `evolve_introspect` として実装。`run_evolve` 末尾配線で evolve のたびに自動発火、SKILL Step 11 が人間承認後 todoroki-godai/evolve-anything へ半自動起票、body マーカーで root cause 単位 dedup、#299）。直前: [ADR-032](../docs/decisions/032-claude-md-skills-parser-coverage-and-resolution-tristate.md) CLAUDE.md Skills パーサ記法拡張 + 解決状態3分類（#295）。
