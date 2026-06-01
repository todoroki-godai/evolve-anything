#!/usr/bin/env python3
"""評価関数の自己成長スクリプト。

score-acceptance 相関追跡、rejection_reason 分析、欠落軸提案、
adversarial probe を行い、fitness function の改善を提案する。

human_accepted / rejection_reason のデータは
optimize スキルの history.jsonl（SSoT）を参照する。
"""
import hashlib
import json
import math
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

HISTORY_DIR = (
    Path(__file__).parent.parent.parent  # skills/
    / "genetic-prompt-optimizer"
    / "scripts"
    / "generations"
)

MIN_DATA_COUNT = 30
BOOTSTRAP_MIN = 5
CORRELATION_WINDOW = 20
CORRELATION_THRESHOLD = 0.50
REJECTION_PATTERN_THRESHOLD = 3


# evolve diff 提案の採点記録に使う固定値（issue #223）
EVOLVE_DIFF_FITNESS_FUNC = "skill_quality"
EVOLVE_DIFF_SOURCE = "evolve_remediation"


def load_history(history_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """history.jsonl（SSoT）を読み込む。

    optimize/rl-loop の SSoT に加え、evolve diff 提案の採点記録も
    同じ history.jsonl に正規化して書き込まれる（issue #223）。
    """
    if history_file is None:
        history_file = HISTORY_DIR / "history.jsonl"
    if not history_file.exists():
        return []

    records = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _score_skill_content(after_content: str, skill_name: str) -> Optional[float]:
    """after_content を skill_quality fitness で採点する。

    evaluate_skill_quality はディスク上の SKILL.md を読むため、
    after_content を一時ディレクトリの SKILL.md に書いて採点する。
    """
    fitness_dir = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "rl" / "fitness"
    if str(fitness_dir) not in sys.path:
        sys.path.insert(0, str(fitness_dir))
    try:
        from skill_quality import evaluate_skill_quality
    except Exception:
        return None

    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / (skill_name or "skill")
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(after_content, encoding="utf-8")
        result = evaluate_skill_quality(after_content, str(skill_dir))
    if not result:
        return None
    return float(result.get("overall", 0.0))


def record_evolve_diff_decision(
    skill_name: str,
    after_content: str,
    diff_summary: str,
    human_accepted: bool,
    rejection_reason: Optional[str] = None,
    history_file: Optional[Path] = None,
    entry_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """evolve の Compile/remediation でスキル diff を accept/reject した時点で、
    after content を skill_quality で採点し history.jsonl に正規記録する（issue #223）。

    optimize/rl-loop と同一スキーマ（best_fitness/human_accepted/fitness_func）で
    記録するため母集団が「混合ではなく増量」になり相関が壊れない。

    冪等性: entry_id（未指定時は内容ハッシュ）で既存行と重複したら再書き込みしない。
    """
    if history_file is None:
        history_file = HISTORY_DIR / "history.jsonl"

    best_fitness = _score_skill_content(after_content, skill_name)

    if entry_id is None:
        digest = hashlib.sha1(
            f"{skill_name}|{after_content}|{human_accepted}".encode("utf-8")
        ).hexdigest()[:16]
        entry_id = f"evolve_diff_{digest}"

    entry = {
        "id": entry_id,
        "source": EVOLVE_DIFF_SOURCE,
        "skill_name": skill_name,
        "diff_summary": diff_summary,
        "timestamp": datetime.now().isoformat(),
        "fitness_func": EVOLVE_DIFF_FITNESS_FUNC,
        "best_fitness": best_fitness,
        "human_accepted": human_accepted,
        "rejection_reason": rejection_reason,
    }

    # 冪等 ingest: 同一 id が既にあれば書き込まない
    existing = load_history(history_file)
    if any(rec.get("id") == entry_id for rec in existing):
        return entry

    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def compute_correlation(
    scores: List[float], accepted: List[bool]
) -> Optional[float]:
    """score と accepted のピアソン相関係数を計算する。

    直近20件に満たない場合、計算をスキップし次回に持ち越す（MUST）。
    """
    if len(scores) < CORRELATION_WINDOW:
        return None

    # 直近20件のみ使用
    scores = scores[-CORRELATION_WINDOW:]
    accepted_float = [1.0 if a else 0.0 for a in accepted[-CORRELATION_WINDOW:]]

    n = len(scores)
    mean_x = sum(scores) / n
    mean_y = sum(accepted_float) / n

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(scores, accepted_float)) / n
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in scores) / n)
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in accepted_float) / n)

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


def _correlation_for_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """単一 fitness_func グループの score-acceptance 相関を計算する。

    best_fitness=None / human_accepted=None は母集団から除外する（issue #223 (a)）。
    """
    scores: List[float] = []
    accepted: List[bool] = []
    for rec in records:
        fitness = rec.get("best_fitness")
        ha = rec.get("human_accepted")
        if fitness is not None and ha is not None:
            scores.append(fitness)
            accepted.append(ha)

    corr = compute_correlation(scores, accepted)
    group: Dict[str, Any] = {
        "data_points": len(scores),
        "correlation": corr,
        "sufficient_data": len(scores) >= CORRELATION_WINDOW,
    }
    if corr is not None and corr < CORRELATION_THRESHOLD:
        group["warning"] = (
            f"score-acceptance 相関が {corr:.3f} (< {CORRELATION_THRESHOLD}) に低下。"
            "評価関数の再キャリブレーション推奨。"
        )
    return group


def analyze_correlations(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """score-acceptance 相関を fitness_func でグループ化して分析する（issue #223 (c)）。

    異種採点（skill_quality / default / coherence ...）の混合を防ぐため、
    相関は必ず同一 fitness_func 内でのみ計算する。source ラベルは記録のみで
    相関母集団の選別には使わない。
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for rec in history:
        func = rec.get("fitness_func") or "unknown"
        groups.setdefault(func, []).append(rec)

    by_fitness_func = {
        func: _correlation_for_records(recs) for func, recs in groups.items()
    }
    return {"by_fitness_func": by_fitness_func}


def detect_drifted_funcs(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """有効 decision 数と calibration drift した fitness_func 一覧を返す。

    audit observability builder（#285/#286）と trigger_engine の proactive 提案（#286）が
    共有する単一ソース。drift = score-acceptance 相関が CORRELATION_THRESHOLD を割った
    fitness_func（_correlation_for_records が warning を立てたもの）。

    Returns:
        {
          "valid_count": int,    # best_fitness/human_accepted が揃う有効 decision 数
          "sufficient": bool,    # valid_count >= MIN_DATA_COUNT
          "drifted": [           # 相関低下した fitness_func（sufficient 時のみ非空）
            {"func": str, "correlation": float | None}, ...
          ],
        }
    """
    valid = [
        r for r in history
        if r.get("best_fitness") is not None and r.get("human_accepted") is not None
    ]
    result: Dict[str, Any] = {
        "valid_count": len(valid),
        "sufficient": len(valid) >= MIN_DATA_COUNT,
        "drifted": [],
    }
    if not result["sufficient"]:
        return result
    analysis = analyze_correlations(history)
    result["drifted"] = [
        {"func": func, "correlation": group.get("correlation")}
        for func, group in analysis.get("by_fitness_func", {}).items()
        if group.get("warning")
    ]
    return result


def format_correlation_report(correlation: Dict[str, Any]) -> str:
    """analyze_correlations の by_fitness_func 形状を人間可読の文字列に整形する。

    異種 fitness_func は混ぜず、各グループ独立に
    func名 / data_points / correlation 値 / 相関<0.50 の警告有無 を出力する。
    by_fitness_func が空なら「相関データなし」を返す。
    """
    by_func = (correlation or {}).get("by_fitness_func", {})
    if not by_func:
        return "相関データなし"

    lines: List[str] = []
    for func in sorted(by_func):
        group = by_func[func]
        corr = group.get("correlation")
        data_points = group.get("data_points", 0)
        corr_str = f"{corr:.3f}" if isinstance(corr, (int, float)) else "N/A（データ不足）"
        lines.append(f"[{func}] data_points={data_points} correlation={corr_str}")
        warning = group.get("warning")
        if warning:
            lines.append(f"  ⚠ 警告: {warning}")
        elif isinstance(corr, (int, float)) and corr < CORRELATION_THRESHOLD:
            lines.append(
                f"  ⚠ 警告: 相関が {corr:.3f} (< {CORRELATION_THRESHOLD})。"
                "このグループの評価関数の再キャリブレーション推奨。"
            )
    return "\n".join(lines)


def analyze_rejection_reasons(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rejection_reason の頻度分析から欠落評価軸を検出する。"""
    counter: Counter = Counter()
    for rec in history:
        reason = rec.get("rejection_reason")
        if reason:
            counter[reason] += 1

    frequent = []
    for reason, count in counter.most_common():
        if count >= REJECTION_PATTERN_THRESHOLD:
            frequent.append({"reason": reason, "count": count})

    proposals = []
    if frequent:
        for item in frequent:
            proposals.append({
                "type": "missing_axis",
                "reason": item["reason"],
                "count": item["count"],
                "proposal": f"評価軸追加提案: '{item['reason']}' に対応する新しい軸",
            })

    return {
        "total_rejections": sum(counter.values()),
        "frequent_patterns": frequent,
        "proposals": proposals,
    }


def get_adversarial_templates() -> List[Dict[str, str]]:
    """adversarial probe 用テンプレート辞書の提供。

    実際の候補生成は Claude CLI で行う。ここではプロンプトテンプレートを返す。
    """
    return [
        {
            "name": "score_maximizer",
            "description": "スコアを最大化するが実用性が低い候補",
            "prompt_hint": "全ての評価基準のキーワードを含むが中身のない候補を生成",
        },
        {
            "name": "length_gamer",
            "description": "行数制限ギリギリの冗長な候補",
            "prompt_hint": "制限行数ちょうどの冗長な候補を生成",
        },
        {
            "name": "template_repeater",
            "description": "テンプレートをそのまま繰り返す候補",
            "prompt_hint": "既存パターンを機械的に繰り返す候補を生成",
        },
    ]


def run_fitness_evolution(history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """評価関数の改善レポートを生成する。"""
    if history is None:
        history = load_history()

    # データ十分性チェック
    decisions = [r for r in history if r.get("human_accepted") is not None]

    if len(decisions) < BOOTSTRAP_MIN:
        return {
            "status": "insufficient_data",
            "data_count": len(decisions),
            "required": MIN_DATA_COUNT,
            "message": f"データ不足: {len(decisions)}/{MIN_DATA_COUNT}件。"
                       f"あと {MIN_DATA_COUNT - len(decisions)} 件の accept/reject が必要。"
                       "母集団は optimize/rl-loop の accept/reject に加え、"
                       "evolve のスキル diff 提案の accept/reject（採点付き記録）も含む。",
        }

    if len(decisions) < MIN_DATA_COUNT:
        # Bootstrap モード: 簡易分析
        scores = [r.get("best_fitness", 0.0) for r in decisions if r.get("best_fitness") is not None]
        accepted_count = sum(1 for d in decisions if d.get("human_accepted"))
        approval_rate = accepted_count / len(decisions) if decisions else 0.0
        mean_score = sum(scores) / len(scores) if scores else 0.0

        # スコア分布
        score_distribution = {}
        if scores:
            score_distribution = {
                "min": min(scores),
                "max": max(scores),
                "mean": mean_score,
                "median": sorted(scores)[len(scores) // 2],
            }

        return {
            "status": "bootstrap",
            "data_count": len(decisions),
            "required": MIN_DATA_COUNT,
            "message": f"簡易分析モード ({len(decisions)}/{MIN_DATA_COUNT}件)",
            "bootstrap_analysis": {
                "approval_rate": approval_rate,
                "mean_score": mean_score,
                "score_distribution": score_distribution,
            },
        }

    # 完全分析
    correlation = analyze_correlations(history)
    rejections = analyze_rejection_reasons(history)
    adversarial = get_adversarial_templates()

    return {
        "status": "ready",
        "data_count": len(decisions),
        "correlation": correlation,
        "rejections": rejections,
        "adversarial_candidates": adversarial,
    }


if __name__ == "__main__":
    result = run_fitness_evolution()
    print(json.dumps(result, ensure_ascii=False, indent=2))
