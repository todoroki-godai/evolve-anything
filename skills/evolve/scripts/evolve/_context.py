#!/usr/bin/env python3
"""EvolveContext — run_evolve のフェーズ間共有ローカルを束ねる dataclass（#531 PR 5/8）。

run_evolve の 1 関数モノリスは `project_dir` / `dry_run` / `skip_skills` / `skip_llm_evolve` /
`confirmed_batch` の引数と、初期化フェーズで作る `proj_root` / `_generated_at` / `_warning_sink` /
`tier` / `_tier_breakdown` をフェーズ間で引き回す。後続 PR（#6-#8）で phase を別 module へ抽出
するとき、これらを `(result, ctx)` シグネチャで渡せるよう先に dataclass へ束ねる。

本 PR は **dataclass 導入 + run_evolve 内のローカル参照を ctx.<field> に置換するだけ**で、
phase コードは run_evolve に残す（抽出なし・振る舞いゼロ変更）。

⚠️ 束縛フェンス（#531 §3）:
`new_result()` の `_resolve_evolve_slug` 呼びは `import evolve as _ev; _ev._resolve_evolve_slug(...)`
で **パッケージ namespace 経由**にする。`_resolve_evolve_slug` は PR#1 の束縛フェンス対象で
test_evolve_binding_paths が `setattr(evolve, "_resolve_evolve_slug", sentinel)` の効きを assert
する。`from ._env import _resolve_evolve_slug` で直接束縛すると差し替えがすり抜ける。
一方 `_count_env_artifacts` / `_tier_from_count` / `ENV_TIER_THRESHOLDS` は束縛フェンス対象外
（monkeypatch されない）なので `._env` から直接 import してよい。
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._env import ENV_TIER_THRESHOLDS, _count_env_artifacts, _tier_from_count


@dataclass
class EvolveContext:
    """run_evolve のフェーズ間共有状態（引数 + 初期化フェーズで作るローカル）。"""

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
    def create(
        cls,
        project_dir: Optional[str],
        dry_run: bool,
        skip_skills: Optional[set],
        skip_llm_evolve: bool,
        confirmed_batch: bool,
    ) -> "EvolveContext":
        """run_evolve 冒頭の初期化ロジックと bit-identical に ctx を構築する。

        - generated_at: `datetime.now(timezone.utc).isoformat()`
        - proj_root: `Path(project_dir) if project_dir else Path.cwd()`
        - tier_breakdown / tier: `_count_env_artifacts(proj_root)` → `_tier_from_count(total)`
        """
        generated_at = datetime.now(timezone.utc).isoformat()
        proj_root = Path(project_dir) if project_dir else Path.cwd()
        tier_breakdown = _count_env_artifacts(proj_root)
        tier = _tier_from_count(tier_breakdown["total"])
        return cls(
            project_dir=project_dir,
            proj_root=proj_root,
            dry_run=dry_run,
            skip_skills=skip_skills,
            skip_llm_evolve=skip_llm_evolve,
            confirmed_batch=confirmed_batch,
            warning_sink=[],
            generated_at=generated_at,
            tier=tier,
            tier_breakdown=tier_breakdown,
        )

    def new_result(self) -> Dict[str, Any]:
        """run_evolve が作る result 初期 dict をキー・値とも完全一致で構築する。"""
        # 束縛フェンス: setattr(evolve, "_resolve_evolve_slug", ...) が効くよう package 経由で呼ぶ。
        import evolve as _ev

        return {
            "timestamp": self.generated_at,
            # --- 結果の同一性 metadata（#408 A/B）: 読み手が「どの PJ・いつ・本実行か」を
            #     skill_name からの推測でなくトップレベルで機械検証できるようにする。---
            "generated_at": self.generated_at,
            "slug": _ev._resolve_evolve_slug(self.proj_root),
            "project_dir": str(self.proj_root.resolve()),
            "dry_run": self.dry_run,
            "phases": {},
            "env_tier": self.tier,
            "env_tier_reason": {
                "count": self.tier_breakdown["total"],
                "breakdown": self.tier_breakdown,
                "thresholds": dict(ENV_TIER_THRESHOLDS),
            },
        }
