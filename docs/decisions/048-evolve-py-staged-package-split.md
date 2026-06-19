# ADR-048: evolve.py 段階分割 — `evolve.py`（単一ファイル）→ `evolve/` パッケージ化（re-export 後方互換）

- Status: Accepted
- Date: 2026-06-16
- Issue: #531（evolve.py が file-size-budget の HARD 上限 800 行を超過、現 1712 行）
- Related: #100（Phase 5/6/7 HARD violator 分割計画）, ADR-039（evolve result は output file へ）,
  ADR-041（evolve decision capture）, learning_audit_package_split（audit.py 2046→178 行・11 PR 連続 merge）,
  ルール: `.claude/rules/file-size-budget.md`, `scripts/lib/line_limit.py`,
  契約: `scripts/lib/evolve_result_schema.py`（CANONICAL／COVERED_PHASES 逆方向 drift 検出）

## 背景（症状）

`skills/evolve/scripts/evolve.py` が **1712 行**（2026-06-16 実測、issue 起票時 1509 行からさらに増加）で、
`MAX_PYTHON_SOURCE_HARD`（800 行・分割必須）を 2 倍以上超過。検出は `audit.check_python_source_budgets`。

単一ファイルに ~18 フェーズの orchestration が集中している（実コード構造、行は現状値）:

| 区分 | 関数 / フェーズ | 行範囲（概算） | 役割 |
|------|----------------|---------------|------|
| module-level | import / `_plugin_root` / sys.path / `DATA_DIR` / `ENV_TIER_THRESHOLDS` | 1–51 | 共通基盤 |
| helpers (env/slug) | `_resolve_data_dir` `_resolve_evolve_slug` `_resolve_pj_slug` `_compute_env_score_struct` `_env_score_degraded` `_apply_remediation_suppression` `_surface_constitutional_status` `_count_env_artifacts` `_tier_from_count` `_compute_env_tier` | 22–298 | env score / tier / slug 解決 |
| helpers (state) | `load_evolve_state` `save_evolve_state` `count_new_sessions` `count_new_observations` `_build_trigger_summary` `compute_trend` `check_data_sufficiency` `_count_total_observations` `check_fitness_function` | 299–536 | 観測量集計・データ十分性・fitness チェック |
| helpers (capture) | `_capture_warnings` `_TeeStderr` `_capture_audit_stderr` | 537–615 | warning/stderr sink |
| **orchestration** | **`run_evolve`** | **616–1498（約 884 行）** | 全フェーズの逐次実行 |
| post-helpers | `_emit_growth_crystallization` `_warn_insufficient_data` | 1501–1561 | 結晶化 emit / 不足警告 |
| CLI | `main` `_summarize_result` | 1562–1712 | argparse / drain / output |

`run_evolve` 単体が 884 行で、これ自体が HARD 上限を超える「関数内モノリス」。フェーズは
コメントで `# Phase N:` と明示済みなので境界は機械的に切り出せるが、各フェーズが
ローカル変数（`sufficiency` `discover_data` `result["phases"]` `_warning_sink` `tier`）を
共有しているため、**素朴な関数抽出では引数地獄になる**。設計上の工夫が要る。

## 影響

- レビュー負荷が高い（1700 行モノリス）。
- 並行 PR の変更衝突リスクが高い（#530 等で慢性的に増加）。
- フェーズ単位のテスト分離が困難。
- file-size-budget HARD violation が audit で常時 surface し続ける。

## 決定

`audit.py`（2046→178 行・11 PR 連続 merge・PR #51-#61）の勝ちパターンに倣い、
**`evolve.py`（単一ファイル）を `evolve/`（パッケージ）に変換**し、フェーズ群を sub-module へ抽出する。

### audit との最重要の差異（先に固定すべき制約）

audit は `scripts/lib/audit.py` → `scripts/lib/audit/`（パッケージ）化した。evolve も同型だが、
**evolve.py は `from evolve import run_evolve / _resolve_pj_slug / main` で外部（test 21 本・`bin/rl-evolve`）から
import されている**。後方互換の成否はここに懸かる。

確認済みの import 経路:

```python
# bin/rl-evolve
sys.path.insert(0, str(_evolve_scripts))   # = skills/evolve/scripts
from evolve import main                      # ← パッケージ化後も evolve/__init__.py が解決

# test 群（21 本）
from evolve import run_evolve, _resolve_pj_slug, check_data_sufficiency, ...
```

`skills/evolve/scripts/evolve.py` を `skills/evolve/scripts/evolve/__init__.py` に変換すれば、
**`from evolve import X` は `evolve/__init__.py` の re-export で透過的に解決される**（audit と全く同じ手口）。
sys.path には `skills/evolve/scripts` が入っているので、`evolve` がファイルでもパッケージでも同じ名前で解決される。

→ **`evolve.py` ファイルは最終的に `evolve/__init__.py`（薄い re-export ハブ）になる**。これがゴール形。

### 目標 module 境界マップ

`skills/evolve/scripts/evolve/` パッケージ:

| module | 抽出元フェーズ / 関数 | 推定行数 | 依存方向 |
|--------|----------------------|---------|----------|
| `__init__.py` | re-export ハブ（`run_evolve` `main` `_resolve_pj_slug` 等を旧名で公開） | 約 60–100 | 全 sub-module を import |
| `_env.py` | env score / tier / slug 解決（`_resolve_data_dir` `_resolve_*_slug` `_compute_env_score_struct` `_env_score_degraded` `_count_env_artifacts` `_tier_from_count` `_compute_env_tier` + `DATA_DIR` `ENV_TIER_THRESHOLDS`） | 約 230 | 末端（他に依存しない） |
| `_state.py` | state / データ十分性（`load_evolve_state` `save_evolve_state` `count_new_*` `compute_trend` `check_data_sufficiency` `_count_total_observations` `check_fitness_function` `_build_trigger_summary`） | 約 220 | `_env` に依存 |
| `_capture.py` | warning/stderr sink（`_capture_warnings` `_TeeStderr` `_capture_audit_stderr`） | 約 80 | 末端 |
| `phases_diagnose.py` | run_evolve 前半フェーズ群: observe / fitness / discover / enrich / skill_triage / quality_patterns / layer_diagnose / audit / constitutional / quality_traces / skill_evolve（Phase 1〜3.4） | 約 250 | `_env` `_state` `_capture` |
| `phases_remediate.py` | remediation / reorganize / prune / reconcile×2 / batch_skip 昇格 / pitfall_hygiene / rationalization / fitness_evolution / self_evolution（Phase 3.5〜6） | 約 250 | `_env` |
| `phases_capture.py` | self_analysis / state 更新 / session ingest / growth emit / utterance ingest / weak_signals / ttl / correction_semantic / bootstrap / daily_review / idiom_autopromote / evolve_decisions / growth_report（Phase 7〜末尾の post-batch 群） | 約 280 | `_env` `_emit_growth_crystallization` |
| `_report.py` | `_emit_growth_crystallization` `_warn_insufficient_data` | 約 70 | `_env` |
| `cli.py` | `main` `_summarize_result` `drain` 分岐 / argparse | 約 160 | `run_evolve`（`__init__` 経由） |

`run_evolve` 本体は `__init__.py`（または `orchestrator.py`）に残し、各 phase ブロックを
`phases_diagnose.run_diagnose_phases(result, ctx)` のような **「`result` dict と共有 context を受け取り
result を in-place 更新する」関数**に委譲する。これで 884 行の関数を 3 つの ~250 行 module に割る。

### 共有状態の引き回し方（引数地獄の回避）

`run_evolve` のローカル共有変数を `EvolveContext` dataclass に束ねて各 phase 関数に渡す:

```python
@dataclass
class EvolveContext:
    project_dir: Optional[str]
    proj_root: Path
    dry_run: bool
    skip_skills: Optional[set]
    skip_llm_evolve: bool
    confirmed_batch: bool
    warning_sink: List[Dict[str, Any]]
    generated_at: str
    tier: str
```

phase 関数シグネチャは `def run_X_phases(result: Dict, ctx: EvolveContext) -> None`（in-place 更新）に統一。
これは新 abstraction の導入なので **Slice 0（足場）で context dataclass だけ先に入れ、振る舞いを変えない**
ことを徹底する（dataclass 化と抽出を同一 PR に混ぜない）。

### 安全網（抽出より先に整備する — MUST）

audit 分割の勝ち筋の核は「snapshot test + re-export + squash merge」。evolve では既存資産が強い:

1. **`evolve_result_schema.py` の CANONICAL 契約テスト（既存）** — `run_evolve` の result dict の
   キー構造（`phases.*` + top-level）を `check_conformance` / `COVERED_PHASES ∪ UNCOVERED_PHASES` が
   enforce する。フェーズ抽出で result の形が変わっていないことを構造レベルで保証する**最強の安全網**。
   Slice 0 でこの契約テストが緑であることを起点に固定する。
2. **snapshot test（新規・Slice 0 で追加）** — `run_evolve(dry_run=True, project_dir=<合成 PJ>)` を
   HOME 隔離（#457 の `isolate_home`）で 1 回回し、`result` の **キー集合（値でなく構造）** を golden 化。
   各抽出 PR の前後でこの golden が bit-identical であることを assert する。値まで固定すると
   timestamp / generated_at で毎回割れるので、`sorted(walk_keys(result))` をスナップショット対象にする。
3. **既存 test 21 本（`from evolve import ...`）** — re-export が正しければ import パスは無変更で通る。
   各 PR で `pytest skills/evolve/scripts/tests -n 0` が全緑を回帰フェンスにする。
4. **`bin/rl-dogfood-gate --layer all`** — dry-run SHA256 不変 / report invariants / SKILL.md コードブロック実行。
   抽出が実環境の繋ぎ目を壊していないことを最終確認（最初と最後の PR で必須、中間は任意）。

### 後方互換（re-export）

`__init__.py` で旧 public 名を全て再公開する:

```python
# skills/evolve/scripts/evolve/__init__.py
from ._env import (
    DATA_DIR, ENV_TIER_THRESHOLDS, _resolve_pj_slug, _resolve_evolve_slug,
    _compute_env_score_struct, _count_env_artifacts, ...
)
from ._state import (
    load_evolve_state, save_evolve_state, check_data_sufficiency,
    check_fitness_function, compute_trend, ...
)
from .cli import main, _summarize_result
# run_evolve 本体はここに残すか orchestrator.py から re-export
```

test が touch している `_resolve_pj_slug` `check_data_sufficiency` 等の **アンダースコア付き private も
re-export する**（test が import している以上、実質 public 契約）。re-export 漏れは即 ImportError で
検出されるので安全。

## PR 分割順序（段階数）

audit は 11 PR だったが、evolve は run_evolve が 1 関数に凝集している分、**前半 helper の抽出は独立・低リスク、
phase 抽出は context dataclass 導入後に効く**。**8 段階（PR）** に切る。各 PR は **1 module 抽出ずつ・squash merge**。

| PR | 内容 | 規模 | リスク | 独立性 |
|----|------|------|--------|--------|
| **#1（着手推奨）** | `evolve.py` → `evolve/__init__.py` へリネーム（パッケージ化）+ snapshot test 追加 + CANONICAL 契約テスト緑確認。**振る舞いゼロ変更**（ファイル丸ごと `__init__.py` に移すだけ） | 小 | 🟢低 | 完全独立 |
| #2 | `_env.py` 抽出（env/slug/tier helper・末端依存なし） | 中 | 🟢低 | #1 のみ依存 |
| #3 | `_capture.py` 抽出（warning/stderr sink・末端） | 小 | 🟢低 | #1 のみ依存 |
| #4 | `_state.py` 抽出（state/データ十分性/fitness チェック） | 中 | 🟡中 | #2 に依存 |
| #5 | `EvolveContext` dataclass 導入（run_evolve のローカル変数を束ねるだけ・抽出なし） | 中 | 🟡中 | #1-#4 |
| #6 | `phases_diagnose.py` 抽出（Phase 1〜3.4） | 大 | 🟡中 | #5 |
| #7 | `phases_remediate.py` 抽出（Phase 3.5〜6） | 大 | 🟡中 | #5 |
| #8 | `phases_capture.py` + `_report.py` + `cli.py` 抽出（Phase 7〜末尾 + CLI） | 大 | 🟡中 | #5 |

完了後 `__init__.py` は re-export + 薄い `run_evolve` orchestrator のみ（目標 ~150 行以下、HARD 800 を下回る）。

### 各 PR の検証コマンド（回帰フェンス）

```bash
# 各 PR 必須（直列。並行ワーカーに回すなら -n 0 で CPU 飢餓回避）
python3 -m pytest skills/evolve/scripts/tests -n 0 -q
python3 -m pytest scripts/lib/tests/test_evolve_result_schema.py -q   # CANONICAL 契約

# パッケージ整合性
claude plugin validate

# 最初（#1）と最後（#8）は必須 / 中間は任意
bin/rl-dogfood-gate --layer all
```

## 最初に着手すべき PR

**PR #1: パッケージ化 + snapshot test 整備**。理由:

1. **最も独立・最も低リスク** — ファイルを `evolve/__init__.py` に移すだけで振る舞いを 1 行も変えない。
   re-export が正しければ既存 21 test と `bin/rl-evolve` がそのまま通る（壊れたら即 ImportError）。
2. **以降の全 PR の安全網を先に敷く** — snapshot test と CANONICAL 契約テストの緑を起点に固定すれば、
   #2 以降の抽出は「golden が割れたら抽出ミス」と機械的に判定できる。
3. audit 分割でも初手は「re-export 足場を作って既存 test を回帰フェンス化」だった（learning_audit_package_split）。

PR #1 が緑で通れば、#2/#3（末端 helper 抽出）は互いに独立なので**並行ワーカーに worktree 隔離で割れる**
（ただし #4 以降は依存チェーンがあるので順次）。

## 回帰リスクと軽減策

| リスク | 評価 | 軽減策 |
|--------|------|--------|
| re-export 漏れで test が ImportError | 🟢低 | 即座に検出される（silent fail しない）。#1 で 21 test を回帰フェンス化 |
| phase 抽出で result の形が変わる | 🟡中 | snapshot（キー集合）+ CANONICAL 契約テストが構造 drift を検出 |
| 共有ローカル変数の引き回しミス | 🟡中 | `EvolveContext` 導入（#5）を抽出と別 PR にし、振る舞い不変を先に固定 |
| dry-run 書込ゲートの段違い破壊 | 🟡中 | post-batch 群（weak_signals/idiom 等）は `dry_run` を最下層 write まで貫通する設計。抽出時に `ctx.dry_run` の伝播を `bin/rl-dogfood-gate` の dry-run 不変で確認 |
| `sys.path.insert` の副作用が抽出で消える | 🟡中 | module-level の sys.path 操作は `__init__.py` 先頭に残す（import 時点で効かせる） |
| 並行 PR との衝突（#530 系で慢性的に増加中） | 🟡中 | 着手期間中は evolve.py への機能追加 PR を凍結 or 分割 PR を最優先で連続 merge（squash） |

## 不採用案

- **一括分割（1 PR で全 module 抽出）**: レビュー不能・回帰の切り分け不能。audit でも 11 PR に割った理由と同じ。
- **`run_evolve` の phase を別ファイルに切るだけでパッケージ化しない**: `evolve.py` から
  `from phases_diagnose import ...` する形は sys.path 汚染（`skills/evolve/scripts` がフラットに全 module を
  晒す）で他スキルの module 名と衝突しうる。パッケージ namespace（`evolve.phases_diagnose`）に閉じるのが安全。
- **行数だけ削る（コメント圧縮・密化）**: budget は通っても凝集の問題（フェーズ単位テスト分離・衝突）は残る。

## 実施結果

全8 PR（#603 scaffold / #605 _env / #606 _capture / #607 _state / #608 _report / #609 _context=EvolveContext / #610 phases_diagnose / #611 phases_remediate / #612 phases_capture+cli）がマージ済み。`evolve/__init__.py` は 156 行（HARD 800 を下回る）。
