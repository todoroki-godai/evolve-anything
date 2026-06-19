#!/usr/bin/env python3
"""Skill-RM — スキル軸での異種評価基準統一（報酬モデル）。

arXiv:2606.03980 の Skill Reward Model 着想。タスク（スキル）ごとに異なる
成功条件（異種基準）を、すべてのスキルに共通な軸へ射影し、単一の報酬値で
横断評価する。これにより evolve-scorer / environment fitness の「軸別」重み統合
（coherence/telemetry/constitutional/skill_quality）とは直交する「スキル別」の
統一スコアが得られる。

共通軸（どのスキルにも適用できる異種基準の射影先）:
    structure : SKILL.md の構造品質（CSO スコア）— 「書き方」の成功条件
    success   : invoke 直後に correction が無い暗黙成功率 — 「使われ方」の成功条件
    validity  : invoke あたりのエラー率の補数 — 「動作」の成功条件

軸の合成は environment._normalize_weights を単一ソースとして再利用する
（動的正規化の数式を重複定義しない）。算出できない軸は除外し、残りで再正規化する
（environment の軸別統合と同じ「利用可能な軸のみで正規化」原則）。

決定論・LLM 非依存。evolve / audit のたびに発火し、calibration drift の
帰属（どのスキルが乖離を生んでいるか）に接続する。
"""
import importlib.util
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
_fitness_dir = Path(__file__).resolve().parent


def _ensure_paths():
    paths = [
        str(_plugin_root / "scripts" / "lib"),
        str(_plugin_root / "scripts"),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


# ── 共通軸のベース重み（異種基準の射影先）──────────────────────
# environment.BASE_WEIGHTS とは別系統（スキル別 vs 軸別で直交）。
SKILL_RM_BASE_WEIGHTS: Dict[str, float] = {
    "structure": 0.30,
    "success": 0.40,
    "validity": 0.30,
}

# invoke 後この秒数以内に同セッションの correction があれば失敗とみなす
# （telemetry.implicit_reward と同じ窓）。
_SUCCESS_WINDOW_SEC = 60


def _load_environment():
    """environment モジュールを importlib で安全にロードする（_normalize_weights SoT）。"""
    try:
        from . import environment as env_mod  # type: ignore
        return env_mod
    except Exception:
        pass
    spec = importlib.util.spec_from_file_location(
        "environment", _fitness_dir / "environment.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_skill_quality():
    spec = importlib.util.spec_from_file_location(
        "skill_quality", _fitness_dir / "skill_quality.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalize_skill_axes(available_axes: List[str]) -> Dict[str, float]:
    """共通軸を environment._normalize_weights（数式 SoT）で正規化する。

    SKILL_RM_BASE_WEIGHTS をベースに、算出できた軸のみで合計1.0へ再正規化する。
    """
    env = _load_environment()
    return env._normalize_weights(available_axes, SKILL_RM_BASE_WEIGHTS)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _find_all_skills(project_dir: Path) -> List[str]:
    skills_dir = project_dir / ".claude" / "skills"
    if not skills_dir.exists():
        return []
    return [p.parent.name for p in skills_dir.rglob("SKILL.md")]


def _parse_dt(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _structure_axis(project_dir: Path, skill_name: str) -> Optional[float]:
    """SKILL.md の構造品質（CSO スコア）を共通軸 [0,1] として返す。"""
    skill_dir = project_dir / ".claude" / "skills" / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        sq = _load_skill_quality()
        content = skill_md.read_text(encoding="utf-8")
        res = sq.evaluate_skill_quality(content, str(skill_dir))
        if res and "overall" in res:
            return float(res["overall"])
    except Exception:
        return None
    return None


def compute_skill_rewards(project_dir: Path, days: int = 30) -> Dict[str, Any]:
    """スキルごとの異種成功条件を共通軸へ射影し、単一報酬で横断評価する。

    Returns:
        {
          "skills": {name: {"reward": float, "axes": {...}, "weights": {...}}},
          "mean_reward": float | None,
          "reward_spread": float | None,   # stdev（dispersion）
          "skill_count": int,
          "worst_skill": str | None,       # 最低 reward のスキル（calibration drift 帰属用）
        }
    """
    _ensure_paths()
    project_dir = Path(project_dir).resolve()

    all_skills = _find_all_skills(project_dir)
    if not all_skills:
        return {
            "skills": {},
            "mean_reward": None,
            "reward_spread": None,
            "skill_count": 0,
            "worst_skill": None,
        }

    project_name = project_dir.name
    since = _iso_days_ago(days)

    # --- テレメトリを一括取得（per-skill 集計） ---
    usage: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    corrections: List[Dict[str, Any]] = []
    try:
        from telemetry_query import query_usage, query_errors, query_corrections
        usage = query_usage(project=project_name, since=since, include_unknown=True)
        errors = query_errors(project=project_name, since=since, include_unknown=True)
        corrections = query_corrections(project=project_name, since=since, include_unknown=True)
    except Exception:
        pass

    # correction をセッション別タイムスタンプでインデックス化
    corr_by_session: Dict[str, List[datetime]] = {}
    for c in corrections:
        sid = c.get("session_id", "")
        dt = _parse_dt(c.get("timestamp", ""))
        if sid and dt is not None:
            corr_by_session.setdefault(sid, []).append(dt)

    # per-skill usage / errors 集計
    usage_by_skill: Dict[str, List[Dict[str, Any]]] = {}
    for rec in usage:
        sk = rec.get("skill_name", "")
        if sk:
            usage_by_skill.setdefault(sk, []).append(rec)

    err_count_by_skill: Dict[str, int] = {}
    for e in errors:
        sk = e.get("skill_name", "")
        if sk:
            err_count_by_skill[sk] = err_count_by_skill.get(sk, 0) + 1

    skills_out: Dict[str, Any] = {}
    for skill_name in all_skills:
        axes: Dict[str, float] = {}

        # 1) structure: CSO 構造品質（テレメトリ非依存 — 常に算出可能なら入れる）
        struct = _structure_axis(project_dir, skill_name)
        if struct is not None:
            axes["structure"] = round(struct, 4)

        # 2) success: invoke 直後 window 内に correction が無い割合
        recs = usage_by_skill.get(skill_name, [])
        n_invocations = len(recs)
        if n_invocations > 0:
            success = 0
            for rec in recs:
                sid = rec.get("session_id", "")
                invoke_dt = _parse_dt(rec.get("ts", ""))
                if not sid or invoke_dt is None:
                    success += 1  # データ不足 → 成功とみなす
                    continue
                nearby = False
                for corr_dt in corr_by_session.get(sid, []):
                    diff = (corr_dt - invoke_dt).total_seconds()
                    if 0 <= diff <= _SUCCESS_WINDOW_SEC:
                        nearby = True
                        break
                if not nearby:
                    success += 1
            axes["success"] = round(success / n_invocations, 4)

        # 3) validity: invoke あたりエラー率の補数（usage がある場合のみ）
        if n_invocations > 0:
            err_count = err_count_by_skill.get(skill_name, 0)
            axes["validity"] = round(max(0.0, 1.0 - err_count / n_invocations), 4)

        if not axes:
            # 軸が1つも算出できない（未使用かつ構造評価不可）スキルはスキップ
            continue

        weights = _normalize_skill_axes(list(axes.keys()))
        reward = round(sum(axes[a] * weights[a] for a in weights), 4)
        skills_out[skill_name] = {
            "reward": reward,
            "axes": axes,
            "weights": weights,
            "invocations": n_invocations,
        }

    if not skills_out:
        return {
            "skills": {},
            "mean_reward": None,
            "reward_spread": None,
            "skill_count": 0,
            "worst_skill": None,
        }

    rewards = [v["reward"] for v in skills_out.values()]
    mean_reward = round(statistics.mean(rewards), 4)
    spread = round(statistics.pstdev(rewards), 4) if len(rewards) > 1 else 0.0
    worst_skill = min(skills_out, key=lambda k: skills_out[k]["reward"])

    return {
        "skills": skills_out,
        "mean_reward": mean_reward,
        "reward_spread": spread,
        "skill_count": len(skills_out),
        "worst_skill": worst_skill,
    }


def format_skill_rm_report(result: Dict[str, Any]) -> List[str]:
    """Skill-RM を audit / evolve レポート用にフォーマットする。

    対象スキルが無くても「評価したが対象なし」を1行残す（silence != evaluated）。
    """
    skills = result.get("skills", {})
    if not skills:
        return [
            "## Skill-RM (skill-axis reward)",
            "",
            "評価したが対象スキルなし ✓ (no skills with measurable axes)",
            "",
        ]

    lines = [
        f"## Skill-RM (skill-axis reward): mean {result['mean_reward']:.2f}",
        "",
        f"Skills evaluated: {result['skill_count']} / spread (σ): {result['reward_spread']:.2f}",
    ]
    if result.get("worst_skill"):
        worst = result["worst_skill"]
        lines.append(
            f"Lowest reward: {worst} ({skills[worst]['reward']:.2f}) "
            f"— calibration drift の帰属候補"
        )
    lines.append("")

    # reward 昇順（低いものから = 改善余地の大きい順）
    for name in sorted(skills, key=lambda k: skills[k]["reward"]):
        entry = skills[name]
        axes_str = " ".join(f"{a}={entry['axes'][a]:.2f}" for a in entry["axes"])
        lines.append(f"  {name:24s} {entry['reward']:.2f}  [{axes_str}]")

    lines.append("")
    return lines


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Skill-RM 算出（スキル軸の異種基準統一報酬）")
    parser.add_argument("project_dir", help="プロジェクトディレクトリ")
    parser.add_argument("--days", type=int, default=30, help="集計期間（日）")
    args = parser.parse_args()

    result = compute_skill_rewards(Path(args.project_dir), args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
