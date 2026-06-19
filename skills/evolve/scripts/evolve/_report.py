#!/usr/bin/env python3
"""report / growth・データ不足ガイダンス系 helper（evolve パッケージ分割, refs #531）。

evolve 完了時の結晶化イベント journal 記録と、データ未取得/不足時の人間向けガイダンスを
担う末端モジュール（他の evolve sub-module に依存しない）。両関数とも引数で完結し、
PLUGIN_ROOT 直参照（lazy import の sys.path 解決）のみで、DATA_DIR / EVOLVE_STATE_FILE は
使わない。振る舞いは __init__.py から移設したまま不変。

PLUGIN_ROOT は `from plugin_root import PLUGIN_ROOT`（skills/evolve/scripts が sys.path に
ある前提で __init__.py が先頭で解決済み）。本 module も冒頭で同じ import を行う。
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from plugin_root import PLUGIN_ROOT


def _emit_growth_crystallization(result: Dict[str, Any], project_dir: Optional[str]) -> None:
    """evolve 完了時に結晶化イベントを journal に記録する。

    キャッシュ (growth-state) は更新しない — audit が唯一の権威。
    journal の phase はキャッシュからフォールバック取得する。
    """
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from growth_journal import emit_crystallization
    from growth_engine import read_cache

    project_name = Path(project_dir).name if project_dir else "unknown"

    # remediation で変更されたファイルを targets として抽出
    remediation_data = result.get("phases", {}).get("remediation", {})
    classified = remediation_data.get("classified", {})
    targets: list[str] = []
    evidence_count = 0
    for category in ("auto_fixable", "proposable"):
        for issue in classified.get(category, []):
            # line-limit fix 等の非結晶化変更は除外
            issue_type = issue.get("type", "")
            if issue_type in ("line_limit_violation", "untagged_reference_candidates"):
                continue
            target = issue.get("target", issue.get("filename", ""))
            if target:
                targets.append(target)
                evidence_count += 1

    # phase をキャッシュからフォールバック取得（audit が正確な値を持つ）
    cache = read_cache(project_name)
    phase_str = cache.get("phase", "unknown") if cache else "unknown"

    emit_crystallization(
        project=project_name,
        targets=list(set(targets)),
        evidence_count=evidence_count,
        phase=phase_str,
        source="evolve",
    )


def _warn_insufficient_data(sufficiency: Dict[str, Any]) -> None:
    """データ未取得/不足の人間向けガイダンスを stderr に出す（#336）。

    stdout は result JSON 専用の契約。ここに「テレメトリ未取得」等の非 JSON 行を
    混ぜると利用側の `json.loads` が先頭行で失敗するため、ガイダンスは必ず stderr へ。
    """
    if sufficiency.get("backfill_recommended"):
        print(f"テレメトリ未取得: {sufficiency['message']}", file=sys.stderr)
        # #486: 旧 /rl-anything:backfill は #215 で CLI 削除済みの幻。observe hooks が
        # 進行形でセッションを記録するので、数セッション利用後に evolve を回せばよい。
        print(
            "→ observe hooks が今後のセッションを自動記録します。"
            "数セッション利用してから evolve を回してください。",
            file=sys.stderr,
        )
    else:
        print(f"データ不足: {sufficiency['message']}", file=sys.stderr)
        print("スキップ推奨。--force で強制実行可能。", file=sys.stderr)
