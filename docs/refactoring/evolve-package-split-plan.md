# evolve.py 段階分割 実装計画（#531 / ADR-048 の実装補強）

- Status: Implementation Plan（設計のみ・本 doc 時点でコード未着手）
- Date: 2026-06-19
- Issue: #531（`evolve.py` が file-size-budget HARD 800 行を超過、実測 **1739 行**）
- 上位決定: [ADR-048](../decisions/048-evolve-py-staged-package-split.md)（Proposed・方針と PR 順は ADR を SoT とする）
- 勝ちパターン: `learning_audit_package_split`（audit.py 2046→178 行・11 PR 連続 squash merge）
- 契約: `scripts/lib/evolve_result_schema.py`（CANONICAL / COVERED_PHASES ∪ UNCOVERED_PHASES）

本 doc は ADR-048 の方針を**現状コード（1739 行）と実測突合して検証し、ADR が明示していない
実装上の罠を埋める**。ADR と矛盾する箇所はなく、ADR の PR 順（8 本）を踏襲する。
**最大の追記は「§3 monkeypatch 束縛の罠」** — ADR の「re-export すれば import 無変更で通る」は
import パスの話で、**テストの `setattr(evolve, ...)` 束縛は re-export では救えない**。これを設計に織り込む。

---

## 1. 現状把握（実測 1739 行・関数→フェーズ群マッピング）

`run_evolve`（L619–1524, **約 905 行**）が 1 関数モノリス。フェーズは `# Phase N:` コメントで境界明示済み。

| 区分 | 関数 / フェーズ | 行範囲 | 役割 | 抽出先（案） |
|------|----------------|--------|------|------------|
| module 基盤 | import / `_plugin_root` / 2× `sys.path.insert` / `DATA_DIR` / `ENV_TIER_THRESHOLDS` / module-level `skill_evolve_assessment=None` `collect_issues=None` | 1–51 | 共通基盤・**self-mutation スロット**（§3） | `__init__.py` 先頭に残す |
| env/slug helper | `_resolve_data_dir` `_resolve_evolve_slug` `_resolve_pj_slug` `_compute_env_score_struct` `_env_score_degraded` `_apply_remediation_suppression` `_surface_constitutional_status` `_count_env_artifacts` `_tier_from_count` `_compute_env_tier` | 22–299 | env score / tier / slug | `_env.py`（約 230 行） |
| state helper | `load_evolve_state` `save_evolve_state` `count_new_sessions` `count_new_observations` `_build_trigger_summary` `compute_trend` `check_data_sufficiency` `_count_total_observations` `check_fitness_function` | 302–537 | 観測量集計・データ十分性 | `_state.py`（約 220 行） |
| capture helper | `_capture_warnings` `_TeeStderr` `_capture_audit_stderr` | 540–617 | warning/stderr sink | `_capture.py`（約 80 行） |
| **orchestration** | **`run_evolve`** | **619–1524（約 905 行）** | 全フェーズ逐次実行 | `__init__.py` に薄い本体を残し phase 群を委譲 |
| post-helper | `_emit_growth_crystallization` `_warn_insufficient_data` | 1527–1586 | 結晶化 emit / 不足警告 | `_report.py`（約 70 行） |
| CLI | `main` `_summarize_result` | 1588–1738 | argparse / drain / print-out-path / output | `cli.py`（約 160 行） |

### run_evolve 内のフェーズ境界（in-place `result` 更新の単位）

| ブロック | Phase | result への書込先 | 主な外部依存 |
|---------|-------|------------------|------------|
| A: 事前 | tier 計算 / observe(data_sufficiency) / fitness / **observe_first early-return** | `env_tier` `phases.observe` `phases.fitness` | `_count_env_artifacts` `check_data_sufficiency` `check_fitness_function`（**全て self-module の名前**） |
| B: Diagnose | discover / enrich / skill_triage(+outcome_ranking) / quality_patterns / layer_diagnose / audit(+stderr capture) / **env_score** / observability / constitutional / quality_traces / **skill_evolve** | `phases.{discover,enrich,skill_triage,quality_patterns,layer_diagnose,audit,quality_traces,skill_evolve}` `env_score` `observability` | discover, audit, skill_triage, `_compute_env_score_struct`, **`_evolve_mod.skill_evolve_assessment`**（§3） |
| C: Remediate | remediation(+suppression/partition/reconcile_surfaced) / reorganize / prune / split_archive_reconcile / skill_evolve_archive_reconcile / batch_skip 昇格 / pitfall_hygiene / rationalization_table / fitness_evolution / self_evolution | `phases.{remediation,reorganize,prune,split_archive_reconcile,skill_evolve_archive_reconcile,pitfall_hygiene,rationalization_table,fitness_evolution,self_evolution}` | remediation, reorganize, prune, **`_evolve_mod2.collect_issues`**（§3）, evolve_introspect, evolve_reconcile |
| D: Capture/post-batch | trigger_summary / `warnings` 確定 / self_analysis / **state 更新（非 dry-run）** / session ingest / growth emit / utterance ingest / weak_signals / weak_signals_ttl / correction_semantic / bootstrap / daily_review / idiom_autopromote / evolve_decisions / growth_report | `trigger_summary` `warnings` `self_analysis` + 多数の top-level key | evolve_introspect, session_store, `_emit_growth_crystallization`, weak_signals, correction_semantic, idiom_autopromote, evolve_decisions, growth_report |

**共有ローカル変数**（フェーズ間で引き回されるもの）: `result`（dict・in-place）/ `_warning_sink`（list）/
`proj_root` / `tier` / `_generated_at` / 引数 `project_dir` `dry_run` `skip_skills` `skip_llm_evolve` `confirmed_batch`。
加えて各フェーズが `result["phases"].get("discover")` 等で**前段フェーズの出力を読む**（B→C で discover/skill_evolve/skill_triage を参照、
C→D で remediation/self_evolution を参照）。→ phase 関数は `(result, ctx)` を受け、戻り値でなく `result` in-place 更新で連鎖する。

---

## 2. モジュール境界の確定（配置・命名の根拠）

### 配置: `skills/evolve/scripts/evolve/`（パッケージ化）

- 既存の import 元慣習を Grep 済み: `bin/evolve` は `sys.path.insert(0, skills/evolve/scripts)` → `from evolve import main`。
  test 21 本は `from evolve import run_evolve / compute_trend / _resolve_pj_slug / check_data_sufficiency`。
- `scripts/lib/audit.py → scripts/lib/audit/`（`__init__.py` re-export）の前例と**完全同型**。
  `evolve.py` を `evolve/__init__.py` にすれば `from evolve import X` は透過解決（sys.path は変えない）。
- **`scripts/lib/evolve/` には置かない**: 既存 `scripts/lib/evolve_*.py`（evolve_introspect 等）と名前空間が紛らわしく、
  かつ呼び出し元の sys.path は `skills/evolve/scripts` を指している。パッケージ namespace `evolve.phases_*` に閉じるのが安全
  （ADR §不採用案: フラット `from phases_diagnose import` は sys.path 汚染で他スキル module と衝突しうる）。

### モジュール構成案（各行数概算・全て HARD 800 / SOFT 500 を下回る）

| module | 内容 | 推定行数 | 依存方向 |
|--------|------|---------|----------|
| `__init__.py` | re-export ハブ + 薄い `run_evolve` 本体（A〜D を phase 関数に委譲）+ module 先頭の `sys.path.insert` ×2 + self-mutation スロット | **約 150** | 全 sub-module |
| `_env.py` | env score / tier / slug（`_resolve_*` `_compute_env_score_struct` `_env_score_degraded` `_count_env_artifacts` `_tier_from_count` `_compute_env_tier` + `DATA_DIR` `EVOLVE_STATE_FILE` `ENV_TIER_THRESHOLDS`） | 約 250 | 末端 |
| `_state.py` | state/データ十分性/fitness（`load/save_evolve_state` `count_new_*` `compute_trend` `check_data_sufficiency` `_count_total_observations` `check_fitness_function` `_build_trigger_summary`） | 約 230 | `_env`（`DATA_DIR` `EVOLVE_STATE_FILE`） |
| `_capture.py` | `_capture_warnings` `_TeeStderr` `_capture_audit_stderr` | 約 80 | 末端 |
| `_context.py` | `EvolveContext` dataclass（§4） | 約 40 | 末端 |
| `phases_diagnose.py` | run ブロック A+B（Phase 1〜3.4） | 約 270 | `_env _state _capture _context` |
| `phases_remediate.py` | run ブロック C（Phase 3.5〜6） | 約 270 | `_env _context` |
| `phases_capture.py` | run ブロック D（Phase 7〜末尾 post-batch） | 約 290 | `_env _report _context` |
| `_report.py` | `_emit_growth_crystallization` `_warn_insufficient_data` | 約 70 | `_env` |
| `cli.py` | `main` `_summarize_result` + drain / print-out-path 分岐 | 約 170 | `run_evolve`（`__init__` 経由） |

### orchestrator 最終形（`__init__.py`・目標 約 150 行）

```python
# skills/evolve/scripts/evolve/__init__.py（ゴール形）
import sys
from plugin_root import PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))                       # ← module 先頭に残す（§5 罠④）
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"))

# self-mutation スロット（§3 — test/本体が evolve.skill_evolve_assessment を束縛する契約）
skill_evolve_assessment = None
collect_issues = None

from ._env import (DATA_DIR, EVOLVE_STATE_FILE, ENV_TIER_THRESHOLDS,
                   _resolve_pj_slug, _resolve_evolve_slug, _resolve_data_dir,
                   _compute_env_score_struct, _env_score_degraded, _count_env_artifacts,
                   _tier_from_count, _compute_env_tier, _apply_remediation_suppression,
                   _surface_constitutional_status)
from ._state import (load_evolve_state, save_evolve_state, count_new_sessions,
                     count_new_observations, compute_trend, check_data_sufficiency,
                     _count_total_observations, check_fitness_function, _build_trigger_summary)
from ._capture import _capture_warnings, _TeeStderr, _capture_audit_stderr
from ._context import EvolveContext
from ._report import _emit_growth_crystallization, _warn_insufficient_data
from .cli import main, _summarize_result
from . import phases_diagnose, phases_remediate, phases_capture


def run_evolve(project_dir=None, dry_run=False, skip_skills=None,
               skip_llm_evolve=False, confirmed_batch=False, observe_first=False):
    """全フェーズの薄い orchestrator。各ブロックは phases_* に委譲し result を in-place 更新する。"""
    ctx = EvolveContext.create(project_dir, dry_run, skip_skills, skip_llm_evolve, confirmed_batch)
    result = ctx.new_result()                          # timestamp / generated_at / slug / project_dir / env_tier
    phases_diagnose.run_observe_and_fitness(result, ctx)
    if observe_first:                                  # Phase 1.6 early-return（#407）
        result["observe_first"] = True
        result["skipped_heavy_phases"] = True
        return result
    phases_diagnose.run_diagnose(result, ctx)          # Phase 2〜3.4
    phases_remediate.run_remediate(result, ctx)        # Phase 3.5〜6
    phases_capture.run_capture(result, ctx)            # Phase 7〜末尾 post-batch
    return result
```

`run_evolve` 本体が ~25 行、残りが re-export。`__init__.py` 全体で約 150 行 → HARD 800 / SOFT 500 を大きく下回る。

---

## 3. ⚠️ 最重要の罠: monkeypatch 束縛は re-export では救えない（ADR が明示していない）

ADR-048 は「re-export すれば import パスは無変更で通る」とするが、これは **import 文の解決**の話。
現状コードとテストには **module 属性の動的束縛** に依存した経路が 2 系統あり、これは別問題:

### 3-1. 本体の self-mutation（L48-49, 890-895, 924-928）

```python
# module 先頭
skill_evolve_assessment = None
collect_issues = None
# run_evolve 内（Phase 3.4 / 3.5）
import evolve as _evolve_mod
if _evolve_mod.skill_evolve_assessment is None:
    from skill_evolve import skill_evolve_assessment as _sea
    _evolve_mod.skill_evolve_assessment = _sea          # ← module グローバルを書き換える
skill_evolve_assessment = _evolve_mod.skill_evolve_assessment
```

`run_evolve` を `phases_diagnose.py` に移すと、`import evolve as _evolve_mod` は **パッケージ `evolve`（=`__init__.py`）を
指す**。よって `skill_evolve_assessment` / `collect_issues` のスロットは **`__init__.py` に置けば** self-mutation は維持できる。
→ **MUST: スロット 2 変数は `__init__.py` 先頭に残す**（`phases_*` に移さない）。phase 関数内では `import evolve as _evolve_mod`
を維持して `_evolve_mod.skill_evolve_assessment` を読み書きする（束縛先が `__init__` で一致する）。

### 3-2. テストの `monkeypatch.setattr(evolve, ...)`（差し替え束縛）

実測した差し替え対象:

| test | 差し替え | 呼ばれる場所（現状） | 移設後の解決先 |
|------|---------|--------------------|--------------|
| `test_evolve_observe_first_and_identity.py:44` | `setattr(evolve, "check_data_sufficiency", ...)` | `run_evolve` 内 `check_data_sufficiency()` | **`run_evolve` が `phases_diagnose` に移ると `phases_diagnose` の名前空間で解決** → setattr すり抜け |
| `:118 / :205` | `setattr(evolve, "run_evolve", ...)` | `main()` → `run_evolve()` | `main` が `cli.py` に移ると `cli` の名前空間で解決 → すり抜け |
| `test_evolve_output_flag.py:36/139` | `setattr(evolve, "run_evolve", ...)` | 同上 | 同上 |
| `test_evolve_print_out_path.py:32/55` | `setattr(evolve, "_resolve_evolve_slug", ...)` | `main()` → `_resolve_evolve_slug()` | 同上 |
| `test_proposable_custom_dedup.py:195/200` | `setattr(_evolve_mod, "check_data_sufficiency" / "check_fitness_function", ...)` | `run_evolve` 内 | すり抜け |

**これが silent fail の温床**: テストは緑のまま、実際には mock が効かず実関数が走る（実環境走査で遅くなる or 別挙動）。

#### 対策（設計に織り込む — 2 段構え）

1. **呼び出しを束縛経由に統一する**: phase 関数内で helper を呼ぶとき、直接名 `check_data_sufficiency()` でなく
   **`import evolve as _ev; _ev.check_data_sufficiency()`** の形にする。`run_evolve` 本体（`__init__.py`）と
   `main`（`cli.py`）で外から差し替えられる名前は、**必ず `evolve.<name>` 経由で呼ぶ**。
   → `setattr(evolve, "X", ...)` が全経路に効く（束縛が `__init__` の 1 箇所に集約される）。
2. **`main` から `run_evolve` を呼ぶ箇所**（`cli.py`）も `import evolve as _ev; _ev.run_evolve(...)` にする。
   `setattr(evolve, "run_evolve", fake)` が `cli.main` に効くため。
3. **Slice 0 で「束縛経路テスト」を新規追加**（§安全網 5）: `setattr(evolve, "run_evolve", sentinel)` 後に
   `evolve.main()` が sentinel を呼ぶこと、`setattr(evolve, "check_data_sufficiency", sentinel)` 後に
   `run_evolve` が sentinel を呼ぶことを assert。これが各抽出 PR で「束縛がすり抜けていない」回帰フェンスになる。

> このテストは**分割前（単一ファイル状態）で先に書いて緑にする**。分割で赤に転じたら束縛すり抜けの確証になる。

---

## 4. EvolveContext と「振る舞い不変」の固定（ADR の Slice 0/#5 を具体化）

`run_evolve` のローカル共有変数を束ねる dataclass。**導入 PR（#5）では dataclass を入れて
`run_evolve` のローカル変数をフィールド参照に置換するだけ・抽出はしない**（振る舞いゼロ変更）。

```python
# _context.py
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
    tier_breakdown: Dict[str, int]

    @classmethod
    def create(cls, project_dir, dry_run, skip_skills, skip_llm_evolve, confirmed_batch):
        proj_root = Path(project_dir) if project_dir else Path.cwd()
        generated_at = datetime.now(timezone.utc).isoformat()
        breakdown = _count_env_artifacts(proj_root)
        return cls(project_dir, proj_root, dry_run, skip_skills, skip_llm_evolve,
                   confirmed_batch, [], generated_at, _tier_from_count(breakdown["total"]),
                   breakdown)

    def new_result(self) -> Dict[str, Any]:
        return {"timestamp": self.generated_at, "generated_at": self.generated_at,
                "slug": _resolve_evolve_slug(self.proj_root),
                "project_dir": str(self.proj_root.resolve()), "dry_run": self.dry_run,
                "phases": {}, "env_tier": self.tier,
                "env_tier_reason": {"count": self.tier_breakdown["total"],
                                    "breakdown": self.tier_breakdown,
                                    "thresholds": dict(ENV_TIER_THRESHOLDS)}}
```

phase 関数シグネチャ統一: `def run_X(result: Dict[str, Any], ctx: EvolveContext) -> None`（戻り値なし・in-place）。

---

## 5. 安全網テスト戦略（抽出より先に整備 — MUST）

既存資産（強い）+ 新規 3 点。**Slice 0（PR #1）で全て緑にしてから抽出に入る。**

| # | 種別 | 何を守るか | 新規/既存 | 配置 |
|---|------|-----------|----------|------|
| 1 | CANONICAL 契約 `test_evolve_result_schema.py` | result の **キー構造**（`phases.*` + top-level）の drift。実 `run_evolve(dry_run=True)` 出力で検証 | **既存** | `scripts/tests/` |
| 2 | **キー集合 snapshot**（新規） | 抽出前後で result の `sorted(walk_keys(result))` が bit-identical。値は timestamp/generated_at で割れるので**キーのみ**を golden 化 | **新規・Slice 0** | `skills/evolve/scripts/tests/test_evolve_keyset_snapshot.py` |
| 3 | **束縛経路テスト**（新規・§3） | `setattr(evolve, "run_evolve"/"check_data_sufficiency"/"_resolve_evolve_slug", sentinel)` が `main`/`run_evolve` に効く（束縛すり抜けの検出） | **新規・Slice 0** | `skills/evolve/scripts/tests/test_evolve_binding_paths.py` |
| 4 | 既存 test 21 本（`from evolve import ...`） | re-export 正当性。import パス無変更で通る | **既存** | `skills/evolve/scripts/tests/` ほか |
| 5 | `bin/evolve-dogfood-gate --layer all` | dry-run SHA256 不変 / report invariants / SKILL.md コードブロック実行（実環境の繋ぎ目） | **既存** | gate |

### HOME 隔離（#457）— 新規テストで MUST

`run_evolve` を呼ぶ新規テスト（#2/#3）は **HOME 隔離必須**。`run_evolve` は後段フェーズが
`Path.home()/.claude/projects`（実環境 ≈9925 jsonl / 1.9GB）を default 走査するため、未隔離だと激遅化する。

- 配置先 `skills/evolve/scripts/tests/` 配下なら **conftest の autouse fixture が自動隔離**（確認済み: `_isolate_home_for_evolve_tests`）。
- それ以外のディレクトリに置くなら `from test_home_isolation import isolate_home`（`scripts/lib/`）を import し
  autouse fixture で `isolate_home(monkeypatch, tmp_path)` を呼ぶ（`test_evolve_result_schema.py` と同型）。
- ルート conftest の `CLAUDE_PLUGIN_DATA`(=DATA_DIR) 隔離は `Path.home()` 由来パスには効かない点に注意。

### snapshot の取り方（値でなくキー集合）

```python
def walk_keys(obj, prefix=""):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(f"{prefix}{k}")
            out |= walk_keys(v, f"{prefix}{k}.")
    return out
# golden = sorted(walk_keys(run_evolve(dry_run=True, project_dir=<合成 PJ>)))
```

list 内の dict まで walk すると要素数で揺れるので、**dict のキーのみ再帰**（list はスキップ）。
合成 PJ は tmp に最小限の `.claude/skills` `.claude/rules` `CLAUDE.md` を置くだけ（既存 `test_evolve_result_schema.py` の `_REPO` 流用でも可）。

---

## 6. PR 粒度（順序付き・8 本 / ADR-048 踏襲・各 squash merge）

各 PR は「1 module 抽出 + re-export + snapshot/契約/束縛/21 test 緑」で独立 mergeable。impl-worker に順に渡せる粒度。

| PR | 対象 | 概算 diff | 依存 | リスク | impl-worker 渡し可否 |
|----|------|----------|------|--------|---------------------|
| **#1** | `evolve.py` → `evolve/__init__.py` へリネーム（パッケージ化）。**振る舞いゼロ変更**。+ snapshot test(#2) + 束縛経路 test(#3) を新規追加し緑固定。§3-2 の対策1/2（`evolve.<name>` 経由呼び出しへの統一）も**この PR で先に入れる**（単一ファイル状態で束縛テストが緑になることを確認） | 中（リネーム + test 2 本 + 呼び出し形の置換） | なし | 🟢低 | 頭が直接 or 単独 worker（足場 PR は分割しない） |
| #2 | `_env.py` 抽出（env/slug/tier・末端） | 中（約 250 行移動 + re-export） | #1 | 🟢低 | worktree worker 可 |
| #3 | `_capture.py` 抽出（warning/stderr・末端） | 小（約 80 行） | #1 | 🟢低 | worktree worker 可（**#2 と並行可** — 互いに独立） |
| #4 | `_state.py` 抽出（state/十分性/fitness） | 中（約 230 行） | #2 | 🟡中 | worker（#2 マージ後） |
| #5 | `EvolveContext` dataclass 導入（`_context.py`）。`run_evolve` のローカル変数をフィールド参照に置換。**抽出なし・振る舞い不変** | 中 | #1-#4 | 🟡中 | 頭が直接推奨（context 設計の岐路） |
| #6 | `phases_diagnose.py` 抽出（ブロック A+B / Phase 1〜3.4）。§3-1 self-mutation スロットは `__init__` 残置を厳守 | 大（約 270 行） | #5 | 🟡中 | worker（依存順で #7/#8 と直列） |
| #7 | `phases_remediate.py` 抽出（ブロック C / Phase 3.5〜6） | 大（約 270 行） | #5（#6 とは result key 経由依存なので #6 マージ後が安全） | 🟡中 | worker（#6 後） |
| #8 | `phases_capture.py` + `_report.py` + `cli.py` 抽出（ブロック D + post-helper + CLI） | 大（約 290+70+170 行） | #5（#7 後推奨） | 🟡中 | worker（#7 後） |

完了後 `__init__.py` は re-export + 約 25 行の `run_evolve` orchestrator（全体 ~150 行 < HARD 800）。

### 各 PR の回帰フェンス（必須コマンド）

```bash
# 各 PR 必須（並行ワーカーに回すときは -n 0 で CPU 飢餓回避）
python3 -m pytest skills/evolve/scripts/tests -n 0 -q
python3 -m pytest scripts/tests/test_evolve_result_schema.py -q      # CANONICAL 契約
python3 -m pytest skills/evolve/scripts/tests/test_evolve_keyset_snapshot.py -q   # キー集合 snapshot
python3 -m pytest skills/evolve/scripts/tests/test_evolve_binding_paths.py -q     # 束縛経路（§3）
claude plugin validate                                                # パッケージ整合性

# 最初(#1)と最後(#8)は必須・中間は任意
bin/evolve-dogfood-gate --layer all
```

---

## 7. 主要リスクと軽減策

| リスク | 評価 | 軽減策 |
|--------|------|--------|
| **monkeypatch 束縛すり抜け**（§3-2）— 抽出後 `setattr(evolve, ...)` が phase/cli 名前空間に効かず test が緑のまま実関数が走る | 🔴**高**（silent fail・ADR 未明示） | (1) 差し替え対象は phase/cli で `evolve.<name>` 経由呼び出しに統一（#1 で先行）。(2) 束縛経路テスト(#3)を #1 で緑固定し各 PR で回帰フェンス化 |
| self-mutation スロット（`skill_evolve_assessment`/`collect_issues`）の束縛喪失（§3-1） | 🟡中 | スロット 2 変数は **`__init__.py` 先頭に残す**（phase へ移さない）。phase 内 `import evolve as _evolve_mod` 維持 |
| phase 抽出で result の形が変わる | 🟡中 | キー集合 snapshot(#2) + CANONICAL 契約(#1) が構造 drift を検出 |
| dry-run 書込ゲートの段違い破壊（post-batch 群 weak_signals/idiom 等は `dry_run` を最下層 write まで貫通） | 🟡中 | `ctx.dry_run` の伝播を `bin/evolve-dogfood-gate` の dry-run SHA256 不変（Layer1）で確認。#8 で `--layer all` 必須 |
| module 先頭 `sys.path.insert`×2 / `DATA_DIR` の env 優先解決(#517) が抽出で消える | 🟡中 | `sys.path.insert` と `_resolve_data_dir`/`DATA_DIR` は **`__init__.py` 先頭**に残す（import 時点で効かせる）。`_env.py` の `DATA_DIR` は `__init__` から re-export |
| `${CLAUDE_PLUGIN_ROOT}` / `plugin_root` 参照（`from plugin_root import PLUGIN_ROOT`）が抽出先で解決不能 | 🟡中 | `PLUGIN_ROOT` import も `__init__.py` 先頭に残し、`_env`/phase は `from plugin_root import PLUGIN_ROOT` を各自で行う（sys.path は `__init__` が通済み） |
| 循環 import（`__init__` → phase → `import evolve`） | 🟡中 | phase の `import evolve as _evolve_mod` は **関数内 import**（現状もそう）なので循環しない。module-level で `from . import phases_*` する `__init__` も、phase が module-level で `evolve` を import しなければ安全 |
| 並行 PR 衝突（#530 系で慢性増加・本 worktree も `feedback`） | 🟡中 | 着手期間中は evolve.py への機能追加 PR を凍結 or 分割 PR を最優先で連続 squash merge。#2/#3 のみ並行、#4 以降は直列 |
| 入れ子 worktree 隔離フォールバック（worktree 内から worker spawn で隔離されず単一 HEAD 競合） | 🟡中 | worker spawn は本体 repo root から、or pathspec 限定 commit・ブランチ切替禁止でスタック。spawn 後 `git worktree list` で隔離確認 |

---

## 8. 完了後のドキュメント更新（最後に）

- `.claude/rules/file-size-budget.md` の文脈（audit 2046→178 の反省）に evolve の実績を 1 行追記候補。
- ADR-048 の Status を Proposed → Accepted に更新（#8 マージ後）。
- `spec/components.md` の evolve 行はパッケージ化を反映（実体パスを `evolve.py` → `evolve/`）。
- CHANGELOG は各 PR で chore/refactor として追記（feat/fix でない純リファクタ）。
