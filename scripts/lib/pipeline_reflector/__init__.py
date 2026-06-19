"""Pipeline Reflector — evolve パイプラインの自己改善モジュール。

remediation-outcomes.jsonl の分析、confidence キャリブレーション、
パイプラインパラメータ調整提案を提供する。LLM 呼び出しは診断生成時のみ。

Phase 12 で package 化（`scripts/lib/pipeline_reflector/` 配下）。
パス定数 (`DATA_DIR` / `OUTCOMES_FILE` / `CALIBRATION_FILE` / `PROPOSALS_FILE`) は
本 `__init__.py` を Single Source of Truth として保持し、サブモジュールは
関数呼び出し時に `pipeline_reflector` 名前空間から動的に lookup する
（テストの `monkeypatch.setattr("pipeline_reflector.X", ...)` 互換）。

Phase 12 完了時の構成:
- `__init__.py` — パス定数 SoT + 全公開 API 再エクスポート
- `outcomes.py` — outcome 取り込み + 軌跡分析 + FP 検出 + 自然言語診断
- `calibration.py` — EWA キャリブレーション + 管理図 + 回帰チェック
- `proposals.py` — 調整提案生成 + 永続化 + audit 用 Pipeline Health セクション
"""
from __future__ import annotations

from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "evolve-anything"
OUTCOMES_FILE = DATA_DIR / "remediation-outcomes.jsonl"
CALIBRATION_FILE = DATA_DIR / "confidence-calibration.json"
PROPOSALS_FILE = DATA_DIR / "pipeline-proposals.jsonl"


# --- Slice 1 (outcomes.py) からの再エクスポート ---

from .outcomes import (  # noqa: E402,F401
    DEFAULT_SELF_EVOLUTION_CONFIG,
    _generate_diagnosis,
    _load_state,
    analyze_trajectory,
    detect_false_positives,
    load_outcomes,
    load_self_evolution_config,
)


# --- Slice 2 (calibration.py) からの再エクスポート ---

from .calibration import (  # noqa: E402,F401
    calibrate_confidence,
    check_calibration_regression,
    check_control_chart,
    load_calibration,
    save_calibration,
)


# --- Slice 3 (proposals.py) からの再エクスポート ---

from .proposals import (  # noqa: E402,F401
    build_pipeline_health_section,
    generate_adjustment_proposals,
    record_proposal,
    update_proposal_status,
)
