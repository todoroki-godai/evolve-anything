#!/usr/bin/env python3
"""report・データ不足ガイダンス系 helper（evolve パッケージ分割, refs #531）。

データ未取得/不足時の人間向けガイダンスを担う末端モジュール（他の evolve sub-module に
依存しない）。引数で完結し、DATA_DIR / EVOLVE_STATE_FILE は使わない。振る舞いは
__init__.py から移設したまま不変。

#379 Step 4: 結晶化イベント journal 記録（旧 `_emit_growth_crystallization`）は
growth-journal harness 削除に伴い削除した。growth-state cache への phase/env_score 反映は
audit orchestrator（`_build_growth_report`）が引き続き唯一の権威として担う。
"""
import sys
from typing import Any, Dict


def _warn_insufficient_data(sufficiency: Dict[str, Any]) -> None:
    """データ未取得/不足の人間向けガイダンスを stderr に出す（#336）。

    stdout は result JSON 専用の契約。ここに「テレメトリ未取得」等の非 JSON 行を
    混ぜると利用側の `json.loads` が先頭行で失敗するため、ガイダンスは必ず stderr へ。
    """
    if sufficiency.get("backfill_recommended"):
        print(f"テレメトリ未取得: {sufficiency['message']}", file=sys.stderr)
        # #486: 旧 /evolve-anything:backfill は #215 で CLI 削除済みの幻。observe hooks が
        # 進行形でセッションを記録するので、数セッション利用後に evolve を回せばよい。
        print(
            "→ observe hooks が今後のセッションを自動記録します。"
            "数セッション利用してから evolve を回してください。",
            file=sys.stderr,
        )
    else:
        print(f"データ不足: {sufficiency['message']}", file=sys.stderr)
        print("スキップ推奨。--force で強制実行可能。", file=sys.stderr)
