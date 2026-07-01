"""バッチ跨ぎ符号付き advantage の EMA 累積 — MAA（#64, advisory）。

``outcome_attribution._reward_variance``（RODS #28）は **単一スナップショット** の reward
分散で「学習余地大」を判定する。1 時点の高分散は偶然の好成績/不成績の混在でも「学習余地
大」と出てしまい、通時の効きを区別できない。本モジュールは各スキルの **advantage**
（その evolve サイクル時点での baseline 比の一発成功率差）を evolve サイクル（バッチ）跨ぎ
で **符号付き EMA** 累積し、「通時で安定して効くか」を RODS と相補的に区別する。

設計:
  - store を今作れば累積が始まる **plant-the-seed 型**。最初の数サイクルは
    「サイクル不足」で graceful、3-4 サイクルで意味を持つ。
  - **advisory のみ**（順位は変えない・RODS と同位置）。magnitude 閾値での pass/fail
    判定はしない（実コーパス calibration 前に捏造しない＝#42 と同じ保守姿勢。符号と
    cycle 数のみ）。
  - read は **書込を一切しない**（dry-run 純度）。write は apply 境界（evolve --drain）
    専用で、必ず store_write barrier（ADR-049）経由。

reader テンプレは predictive_validity.py を踏襲（module-level ``DATA_DIR`` の rl_common
import + fallback、``_base(data_dir)`` helper、``from . import outcome_metrics as _om``）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import outcome_metrics as _om
from .usage import load_usage_data

# テストは ``monkeypatch.setattr(reward_ema, "DATA_DIR", tmp_path)`` で直接この
# module 属性を差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

# EMA の平滑化係数。0.3 は「直近のバッチを 3 割、過去の蓄積を 7 割」で混ぜる慣例値。
# 小さいほど過去の累積を重視（通時の安定性を厚く見る）。advisory ゆえ暫定で、実コーパス
# dry-run 蓄積後に見直す（ADR-044 の方針）。
EMA_ALPHA = 0.3

# EMA に意味を持たせる最小サイクル数。これ未満は 1〜2 バッチの偶然差で符号が反転するため
# 「サイクル不足」とし、符号を語らない（plant-the-seed の graceful 期間）。
MIN_CYCLES_FOR_SIGNAL = 3

# ストアの basename（store_registry / store_write の単一キー）。
_STORE_NAME = "reward_ema.jsonl"


def _base(data_dir: Optional[Path]) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def compute_batch_advantage(attribution: Dict[str, dict]) -> Dict[str, float]:
    """``attribute_outcomes`` の返り値から、スキル単位の符号付き advantage を算出する。

    baseline = first_try_success が None でなく degraded でないスキルの first_try_success
    平均。per-skill advantage = first_try_success - baseline（符号付き、round 4）。
    first_try_success None / degraded のスキルは baseline からも出力からも除外する。

    有効スキルが 2 未満なら ``{}``（baseline 比較が無意味 = comparability 不成立、graceful）。
    """
    valid: Dict[str, float] = {}
    for skill, attr in attribution.items():
        if attr.get("degraded"):
            continue
        fts = attr.get("first_try_success")
        if fts is None:
            continue
        valid[skill] = float(fts)

    if len(valid) < 2:
        return {}

    baseline = sum(valid.values()) / len(valid)
    return {skill: round(fts - baseline, 4) for skill, fts in valid.items()}


def fold_ema(
    prev_ema: Optional[float],
    prev_n: int,
    advantage: float,
    *,
    alpha: float = EMA_ALPHA,
) -> Tuple[float, int]:
    """前回 EMA に今回の advantage を畳み込んで (新 EMA, 新サイクル数) を返す。

    prev_ema が None（初回）: ema=advantage, n=1。
    else: ema = alpha*advantage + (1-alpha)*prev_ema, n=prev_n+1。round 4。
    """
    if prev_ema is None:
        return round(advantage, 4), 1
    ema = alpha * advantage + (1.0 - alpha) * prev_ema
    return round(ema, 4), prev_n + 1


def read_reward_ema(
    slug: str, *, data_dir: Optional[Path] = None
) -> Dict[str, dict]:
    """slug スコープの reward_ema.jsonl を読み、skill 単位に最新 EMA を返す（読み取りのみ）。

    ``_om._read_jsonl``（read-only・非作成）で読み、``pj_slug == slug`` で filter。append 順
    ＝時系列なので skill 単位に **last-append-wins** で fold する。

    Returns:
        {skill: {"ema": float, "n_batches": int, "last_advantage": float, "ts": str}}。
    ファイル不在 → {}（ファイルを作らない・書かない = dry-run 純度）。
    """
    # #112 read 層 alias fold: legacy 旧 slug タグも canonical へ畳んで当 PJ として拾う
    # （lazy import は memory_contagion と同じ audit-package idiom・alias は read 専用）。
    from store_read_union import pj_slug_match as _pj_slug_match

    base = _base(data_dir)
    out: Dict[str, dict] = {}
    for rec in _om._read_jsonl(base / _STORE_NAME):
        if not _pj_slug_match(rec.get("pj_slug"), slug):
            continue
        skill = rec.get("skill")
        if not skill:
            continue
        # append 順に上書き = 最後に見たもの（時系列で新しい）を採用。
        out[skill] = {
            "ema": rec.get("ema"),
            "n_batches": rec.get("n_batches", 0),
            "last_advantage": rec.get("advantage"),
            "ts": rec.get("ts"),
        }
    return out


def persist_reward_ema_batch(
    project_dir: str,
    *,
    slug: Optional[str] = None,
    data_dir: Optional[Path] = None,
    days: int = 30,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """apply 境界専用の書き込み — 今サイクルの advantage を畳み込み reward_ema.jsonl に追記する。

    slug 未指定なら project_dir から resolve_pj_slug で解決。usage/sessions を読んで
    ``attribute_outcomes`` で帰属し、compute_batch_advantage → 既存 EMA に fold_ema して
    各 skill 1 レコードを store_write（ADR-049 write barrier）経由で追記する。

    有効スキル < 2（compute_batch_advantage が {}）のときは
    ``{"persisted": 0, "reason": "insufficient_skills"}`` を返し、何も書かない。
    """
    if slug is None:
        from pj_slug import resolve_pj_slug
        slug = resolve_pj_slug(project_dir)

    usage = load_usage_data(days, project_root=Path(project_dir))
    sessions = _om.read_sessions(_base(data_dir))

    from .outcome_attribution import attribute_outcomes
    attribution = attribute_outcomes(usage=usage, sessions=sessions)

    batch_adv = compute_batch_advantage(attribution)
    if not batch_adv:
        return {"persisted": 0, "reason": "insufficient_skills"}

    prior = read_reward_ema(slug, data_dir=data_dir)
    ts = ts or datetime.now(timezone.utc).isoformat()

    from rl_common.store_write import store_write

    persisted_skills = []
    for skill, adv in sorted(batch_adv.items()):
        prev = prior.get(skill, {})
        ema, n = fold_ema(prev.get("ema"), prev.get("n_batches", 0), adv)
        record = {
            "pj_slug": slug,
            "skill": skill,
            "advantage": adv,
            "ema": ema,
            "n_batches": n,
            "alpha": EMA_ALPHA,
            "ts": ts,
        }
        store_write(_STORE_NAME, record)
        persisted_skills.append(skill)

    return {"persisted": len(persisted_skills), "skills": persisted_skills, "ts": ts}


def ema_stability_label(
    rec: Optional[dict], *, min_cycles: int = MIN_CYCLES_FOR_SIGNAL
) -> Dict[str, Any]:
    """1 スキルの EMA レコードを「通時の効き」ラベルに変換する（advisory）。

    rec None or n_batches < min_cycles → サイクル不足（stable=False, sign=0）。
    n >= min_cycles: sign = +1 if ema>0 / -1 if ema<0 / 0。
      ema>0 「通時で有効寄り(+{ema})」/ ema<0 「通時で低調寄り({ema})」/ 「中立」。

    magnitude 閾値での pass/fail 判定はしない（#42 と同じ保守姿勢。符号と cycle 数のみ）。
    """
    n = (rec or {}).get("n_batches", 0) or 0
    if rec is None or n < min_cycles:
        return {
            "label": f"サイクル不足({n}/{min_cycles})",
            "stable": False,
            "sign": 0,
        }

    ema = rec.get("ema") or 0.0
    if ema > 0:
        return {"label": f"通時で有効寄り(+{ema})", "stable": True, "sign": 1}
    if ema < 0:
        return {"label": f"通時で低調寄り({ema})", "stable": True, "sign": -1}
    return {"label": "中立", "stable": True, "sign": 0}
