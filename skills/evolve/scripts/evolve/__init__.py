#!/usr/bin/env python3
"""Evolve オーケストレーター。

Observe データ確認 → Discover → Enrich → Optimize → Reorganize → Prune(+Merge) →
Fitness Evolution → Report の全フェーズを1つのコマンドで実行する。
"""
import sys
from typing import Any, Dict, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"))

# Module-level references for testability (populated on first call)。
# #531 §3-1: self-mutation スロットはパッケージ evolve（__init__）に残す。phase 分割後も
# `import evolve as _evolve_mod; _evolve_mod.skill_evolve_assessment` の束縛先が __init__ で
# 一致するため self-mutation が維持される（_env.py 等の sub-module へ移さない）。
skill_evolve_assessment = None
collect_issues = None

# env / slug / tier 系 helper・定数は #531 PR2 で _env.py へ抽出。
# sys.path.insert（scripts/lib）後に import すること（_resolve_data_dir が rl_common を要する）。
# 全名前を re-export し `from evolve import X` の後方互換と setattr(evolve, ...) 束縛を保つ。
from ._env import (  # noqa: E402
    ENV_TIER_THRESHOLDS,
    _resolve_data_dir,
    _resolve_evolve_slug,
    _resolve_pj_slug,
    _compute_env_score_struct,
    _env_score_degraded,
    _apply_remediation_suppression,
    build_reconcile_tracked,
    _surface_constitutional_status,
    _count_env_artifacts,
    _tier_from_count,
    _compute_env_tier,
)

# warning / stderr sink ヘルパーは _capture.py に分離（PR 3/8, refs #531）。
# `from evolve import _capture_warnings, _TeeStderr, _capture_audit_stderr` の後方互換を維持。
from ._capture import _capture_warnings, _TeeStderr, _capture_audit_stderr

# report / growth・データ不足ガイダンス系 helper は _report.py に分離（#8 から先行分離, refs #531）。
# 末端 helper（引数で完結・PLUGIN_ROOT 直参照のみ）。run_evolve 内の直接呼びは
# re-export で __init__ 名前空間に名前が入るため解決される。
from ._report import _emit_growth_crystallization, _warn_insufficient_data

# run_evolve のフェーズ間共有ローカルを束ねる dataclass は _context.py に分離（PR 5/8, refs #531）。
# `from evolve import EvolveContext` の後方互換を保つ。new_result() は束縛フェンスのため
# _resolve_evolve_slug を `import evolve as _ev` 経由で呼ぶ（_context.py の docstring 参照）。
from ._context import EvolveContext

# 診断フェーズ群（Phase 1〜3.4）は phases_diagnose.py に分離（PR 6/8, refs #531）。
# phases_diagnose は __init__ を module-level import しない（循環回避・evolve 参照は関数内）ため
# ここで import しても循環しない。
from .phases_diagnose import run_diagnose_phases

# 修正フェーズ群（Phase 3.5〜6）は phases_remediate.py に分離（PR 7/8, refs #531）。
# phases_diagnose と同型で __init__ を module-level import しない（循環回避・evolve 参照は
# 関数内）ため、ここで import しても循環しない。
from .phases_remediate import run_remediate_phases

# キャプチャ／後段フェーズ群（block D: trigger_summary〜growth_report）は phases_capture.py に
# 分離（PR 8/8, refs #531）。phases_diagnose / phases_remediate と同型で __init__ を
# module-level import しない（循環回避・evolve 参照は関数内）ため循環しない。
from .phases_capture import run_capture_phases

# CLI（main / _summarize_result / __main__ guard）は cli.py に分離（PR 8/8, refs #531）。
# `evolve.main()` / `evolve._summarize_result()` の後方互換（test_evolve_output_flag /
# test_evolve_print_out_path / test_evolve_binding_paths / test_evolve_observe_first_and_identity）
# を re-export で維持する。`__main__.py`（from evolve import main）も無変更で動く。cli は
# __init__ を module-level import しない（循環回避・evolve 参照は関数内）ため循環しない。
from .cli import main, _summarize_result

# #517: DATA_DIR / EVOLVE_STATE_FILE はパッケージ（__init__）load 時に env 優先で再解決する。
# `del sys.modules["evolve"]` + reimport で CLAUDE_PLUGIN_DATA を再評価させる契約
# （test_evolve_data_dir_env）を保つため、_env から frozen 値を re-export するのではなく
# __init__ で _resolve_data_dir() を呼び直して package 属性に束縛する。解決ロジック自体は
# _env._resolve_data_dir が単一ソース。
DATA_DIR = _resolve_data_dir()
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"

# state / データ十分性 / fitness 系 helper は #531 PR4 で _state.py へ抽出。
# 全名前を re-export し `from evolve import X` の後方互換と setattr(evolve, ...) 束縛を保つ。
# _state 側は DATA_DIR / EVOLVE_STATE_FILE を module-top で掴まず、呼び出し時に
# `import evolve as _ev` で遅延参照する（#517 reimport 契約のため上の package 属性が単一ソース）。
from ._state import (  # noqa: E402
    load_evolve_state,
    save_evolve_state,
    persist_last_run_timestamp,
    persist_result_dependent_state,
    count_new_sessions,
    count_new_observations,
    _build_trigger_summary,
    compute_trend,
    check_data_sufficiency,
    _count_total_observations,
    check_fitness_function,
    annotate_fitness_generation_advice,
)


def run_evolve(
    project_dir: Optional[str] = None,
    dry_run: bool = False,
    skip_skills: Optional[set] = None,
    skip_llm_evolve: bool = False,
    confirmed_batch: bool = False,
    observe_first: bool = False,
) -> Dict[str, Any]:
    """全フェーズを実行する。

    Args:
        project_dir: プロジェクトディレクトリ
        dry_run: True の場合、レポートのみ出力し変更は行わない
        observe_first: True の場合、安価な observe + fitness ゲートだけ算出して
            重いフェーズ（discover/audit/skill_evolve/remediation/prune…）を回さず
            early-return する（#407）。SKILL Step 1 の lightweight/skip 分岐を
            「フル分析コストを払う前」に効かせるための pre-flight モード。

    Returns:
        各フェーズの結果を含む辞書
    """
    # #531 束縛フェンス: monkeypatch (setattr(evolve, ...)) と本体 self-mutation が効く束縛先を
    # パッケージ evolve（__init__）に集約する。フェーズ分割後に run_evolve が別 module へ移っても
    # 差し替え対象 helper を evolve.<name> 経由で呼べば、名前解決すり抜け（test 緑のまま実関数が
    # 走る silent fail / ADR-048 未明示の罠）を構造的に防げる。
    import evolve as _ev

    # #531 PR5: フェーズ間共有ローカル（引数 + 初期化フェーズで作る proj_root / generated_at /
    # warning_sink / tier / tier_breakdown）を EvolveContext に束ねる。振る舞いはゼロ変更で、
    # 以降 run_evolve 本体はローカル変数の代わりに ctx.<field> を参照する（phase 抽出 PR で
    # (result, ctx) シグネチャに乗せる前段）。result 初期 dict は ctx.new_result() が
    # キー・値とも完全一致で構築する（_resolve_evolve_slug は束縛フェンス経由で呼ぶ）。
    ctx = EvolveContext.create(
        project_dir, dry_run, skip_skills, skip_llm_evolve, confirmed_batch
    )
    result: Dict[str, Any] = ctx.new_result()

    # #531 PR6: 診断フェーズ群（Phase 1〜3.4）は phases_diagnose.run_diagnose_phases に抽出済み。
    # result を in-place mutate する。observe_first は ctx でなく明示引数で渡す（ctx は #4 dataclass
    # の契約・observe_first は pre-flight 制御フラグで状態でないため）。early-return は
    # result["skipped_heavy_phases"] フラグで表現し、ここで本体を打ち切る。
    run_diagnose_phases(result, ctx, observe_first=observe_first)
    if result.get("skipped_heavy_phases"):
        return result

    # #531 PR7: 修正フェーズ群（Phase 3.5〜6）は phases_remediate.run_remediate_phases に
    # 抽出済み。result を in-place mutate する（observe_first / early-return は無い）。
    run_remediate_phases(result, ctx)

    # #531 PR8: キャプチャ／後段フェーズ群（block D: trigger_summary / warnings 確定 /
    # Phase 7 Self-Analysis / state 更新 / 各種 ingest / weak_signals / correction_semantic /
    # bootstrap / daily_review / idiom_autopromote / evolve_decisions / growth_report）は
    # phases_capture.run_capture_phases に抽出済み。result を in-place mutate する。dry-run
    # 書込ゲート（#491/#513）は ctx.dry_run を最下層 write まで貫通させて維持している。
    run_capture_phases(result, ctx)

    return result
