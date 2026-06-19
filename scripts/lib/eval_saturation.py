"""Trigger eval set の飽和度を決定論で診断する（#292, TASTE arXiv 2605.28556）。

trigger_eval_generator は sessions.jsonl → evals.json の *順生成* のみで、生成した
eval が「緑なのに頑健か飽和か」を判別する経路が無い。TASTE（ツール呼び出し列から
難問を逆生成し既存ベンチの飽和を暴く）の視点を持ち込み、forward-gen eval set の
飽和兆候を eval 実行なし・LLM 非依存で測る。

飽和シグナル（利用可能なデータで graceful degrade する）:
- low_negative_coverage: should_trigger=False の割合が低い
  → 易しい positive ばかりで trivially green（over-trigger を暴けない）
- easy_negatives: negative のうち trigger 語を含む near-miss の割合が低い
  → 境界を突かず over-trigger を暴けない。trigger 語が取れる skill のみ評価
- thin: クエリ総数が少ない → 識別力不足

いずれの reason も立たなければ「緑＝頑健」とみなす。eval-sets ディレクトリ自体が
無い環境では applicable=False（対象外）を返す。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

# 飽和判定の閾値（決定論）。
MIN_NEGATIVE_RATIO = 0.3   # negative 比率がこれ未満 → low_negative_coverage
MIN_NEAR_MISS_RATIO = 0.3  # negative 中の trigger 語マッチ比率がこれ未満 → easy_negatives
MIN_QUERY_COUNT = 6        # 総クエリ数がこれ未満 → thin

REASON_LABELS = {
    "low_negative_coverage": "negative 比率が低い（positive 偏重で trivially green）",
    "easy_negatives": "negative が trigger 境界を突かない（over-trigger を暴けない）",
    "thin": "クエリ数が少なく識別力不足",
}


def _default_eval_sets_dir() -> Path:
    """eval-sets ディレクトリの既定パス（DATA_DIR 配下）。

    DATA_DIR を call-time で読む（import 時凍結でなく）— CLAUDE_PLUGIN_DATA を尊重し
    他の全データ（usage.jsonl 等）と同じ正準ロケーションに揃える。writer の
    trigger_eval_generator.EVAL_SETS_DIR も同じ DATA_DIR/eval-sets を指すので reader==writer。
    """
    try:
        import rl_common
        return Path(rl_common.DATA_DIR) / "eval-sets"
    except Exception:
        return Path.home() / ".claude" / "evolve-anything" / "eval-sets"


def load_eval_sets(eval_sets_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """eval-sets dir 配下の `<skill>.json` を skill→eval_set の dict で読み込む。

    壊れた JSON / リスト以外の中身は無視する（全フィールド .get() fallback）。
    """
    import json

    result: Dict[str, List[Dict[str, Any]]] = {}
    d = Path(eval_sets_dir)
    if not d.is_dir():
        return result
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        result[path.stem] = data
    return result


def assess_eval_set(
    skill: str,
    eval_set: List[Dict[str, Any]],
    triggers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """1 つの eval set の飽和度を診断する。

    Args:
        skill: スキル名
        eval_set: [{"query": str, "should_trigger": bool}, ...]
        triggers: スキルの trigger 語。None/空なら easy_negatives 判定はスキップ。

    Returns:
        {skill, total, negatives, negative_ratio, near_miss_ratio, saturated, reasons}
        near_miss_ratio は trigger 語が無い / negative が無い場合 None。
    """
    total = len(eval_set)
    negatives = [
        e for e in eval_set if isinstance(e, dict) and not e.get("should_trigger")
    ]
    n_neg = len(negatives)
    negative_ratio = (n_neg / total) if total else 0.0

    near_miss_ratio: Optional[float] = None
    trigger_words = [t.lower() for t in (triggers or []) if t]
    if trigger_words and n_neg:
        near_miss = sum(
            1
            for e in negatives
            if any(t in str(e.get("query", "")).lower() for t in trigger_words)
        )
        near_miss_ratio = near_miss / n_neg

    reasons: List[str] = []
    if total < MIN_QUERY_COUNT:
        reasons.append("thin")
    if negative_ratio < MIN_NEGATIVE_RATIO:
        reasons.append("low_negative_coverage")
    if near_miss_ratio is not None and near_miss_ratio < MIN_NEAR_MISS_RATIO:
        reasons.append("easy_negatives")

    return {
        "skill": skill,
        "total": total,
        "negatives": n_neg,
        "negative_ratio": negative_ratio,
        "near_miss_ratio": near_miss_ratio,
        "saturated": bool(reasons),
        "reasons": reasons,
    }


def compute_eval_saturation(
    eval_sets_dir: Optional[Path] = None,
    *,
    triggers_by_skill: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """環境の全 trigger eval set の飽和度を集計する（決定論・LLM 非依存）。

    Args:
        eval_sets_dir: eval-sets ディレクトリ。None なら DATA_DIR 配下の既定値。
        triggers_by_skill: skill→trigger 語。easy_negatives 判定に使用。

    Returns:
        {
            "applicable": bool,   # eval set が 1 件でもあれば True
            "evaluated": int,     # 診断した eval set 数
            "assessed": [assess_eval_set(), ...],
            "saturated": [飽和した assess のみ],
        }
    """
    d = Path(eval_sets_dir) if eval_sets_dir is not None else _default_eval_sets_dir()
    eval_sets = load_eval_sets(d)
    if not eval_sets:
        return {"applicable": False, "evaluated": 0, "assessed": [], "saturated": []}

    triggers_by_skill = triggers_by_skill or {}
    assessed = [
        assess_eval_set(skill, eval_set, triggers_by_skill.get(skill))
        for skill, eval_set in eval_sets.items()
    ]
    saturated = [a for a in assessed if a["saturated"]]
    return {
        "applicable": True,
        "evaluated": len(assessed),
        "assessed": assessed,
        "saturated": saturated,
    }
