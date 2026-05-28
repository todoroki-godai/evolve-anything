"""BES (Bidirectional Evolutionary Search) サブゴールスコアラー。

候補テキスト(candidate)をサブゴールに分解し密な中間フィードバックを返す。
LLM 非依存・決定論。regression_gate (hard gate) とは独立した責務。

公開 API:
    score_subgoals(candidate, original, corrections, *, max_lines) -> SubgoalScorerResult
    _aggregate_subgoals(subgoals) -> float  # テスト・内部共通ヘルパー

サブゴール一覧:
    1. frontmatter_preserved  - frontmatter 保持チェック
    2. trigger_coverage       - Trigger 行網羅率チェック
    3. correction_addressed   - corrections の keyword 反映率
    4. line_budget            - 行数上限チェック
    5. slop_free              - slop 検出フック（現在は常に pass、後日実装予定）
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# line_limit の定数を共有
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from line_limit import MAX_SKILL_LINES
except ImportError:
    MAX_SKILL_LINES = 500

# Trigger 行検出正規表現（regression_gate.py と同じパターン）
_TRIGGER_LINE_RE = re.compile(r"^\s*Trigger\s*:", re.MULTILINE | re.IGNORECASE)


# ── データクラス ──────────────────────────────────────────────────────


@dataclass
class SubgoalResult:
    """単一サブゴールの評価結果。"""

    goal: str
    score: float      # 0.0–1.0
    passed: bool
    detail: str


@dataclass
class SubgoalScorerResult:
    """score_subgoals() の返り値。"""

    total: float                                 # 全サブゴールの加重平均 (0.0–1.0)
    subgoals: List[SubgoalResult] = field(default_factory=list)


# ── サブゴール評価ロジック ─────────────────────────────────────────────


def _score_frontmatter_preserved(
    candidate: str,
    original: Optional[str],
) -> SubgoalResult:
    """frontmatter 保持サブゴール。

    - original が None / frontmatter なし → 確認不要 → passed=True (score=1.0)
    - original に frontmatter あり、candidate にもある → passed=True
    - original に frontmatter あり、candidate にない → passed=False
    """
    if original is None or not original.startswith("---"):
        return SubgoalResult(
            goal="frontmatter_preserved",
            score=1.0,
            passed=True,
            detail="original に frontmatter なし（確認不要）",
        )
    if candidate.startswith("---"):
        return SubgoalResult(
            goal="frontmatter_preserved",
            score=1.0,
            passed=True,
            detail="frontmatter 保持を確認",
        )
    return SubgoalResult(
        goal="frontmatter_preserved",
        score=0.0,
        passed=False,
        detail="frontmatter が消失しています",
    )


def _score_trigger_coverage(
    candidate: str,
    original: Optional[str],
) -> SubgoalResult:
    """Trigger 行網羅率サブゴール。

    - original に Trigger がない → 評価不要 → score=1.0
    - original の Trigger 数を分母として、candidate の保持率をスコアにする。
    - 保持率 >= 0.7 → passed=True
    """
    if original is None:
        orig_count = 0
    else:
        orig_count = len(_TRIGGER_LINE_RE.findall(original))

    if orig_count == 0:
        # original に Trigger なし → candidate に有無に関わらず満点
        cand_count = len(_TRIGGER_LINE_RE.findall(candidate))
        detail = (
            f"original に Trigger なし、candidate には {cand_count} 件"
        )
        return SubgoalResult(
            goal="trigger_coverage",
            score=1.0,
            passed=True,
            detail=detail,
        )

    cand_count = len(_TRIGGER_LINE_RE.findall(candidate))
    retention = min(cand_count / orig_count, 1.0)
    passed = retention >= 0.7
    return SubgoalResult(
        goal="trigger_coverage",
        score=retention,
        passed=passed,
        detail=f"Trigger 保持率 {cand_count}/{orig_count} = {retention:.2f}",
    )


def _score_correction_addressed(
    candidate: str,
    corrections: List[Dict[str, Any]],
) -> SubgoalResult:
    """corrections の keyword 反映サブゴール。

    corrections が空 → score=1.0 (評価不要)。
    各 correction から `extracted_learning` と `message` からキーワードを取り出し、
    candidate に含まれる割合をスコアにする。

    キーワード抽出:
      - extracted_learning があればそのまま使う（文全体でチェック）
      - message は単語に分解し 4 文字超の単語を使う
    """
    if not corrections:
        return SubgoalResult(
            goal="correction_addressed",
            score=1.0,
            passed=True,
            detail="corrections なし（評価不要）",
        )

    candidate_lower = candidate.lower()
    hit = 0
    total = 0

    for corr in corrections:
        learning = (corr.get("extracted_learning") or "").strip()
        if learning:
            total += 1
            if learning.lower() in candidate_lower:
                hit += 1

        message = (corr.get("message") or "").strip()
        if message:
            words = [w for w in re.split(r"\W+", message) if len(w) > 4]
            for word in words:
                total += 1
                if word.lower() in candidate_lower:
                    hit += 1

    if total == 0:
        # corrections はあるが抽出キーワードゼロ → 中程度のスコアで pass
        return SubgoalResult(
            goal="correction_addressed",
            score=0.5,
            passed=True,
            detail="corrections あるがキーワード抽出なし",
        )

    score = hit / total
    passed = score >= 0.5
    return SubgoalResult(
        goal="correction_addressed",
        score=score,
        passed=passed,
        detail=f"keyword hit {hit}/{total} = {score:.2f}",
    )


def _score_line_budget(
    candidate: str,
    max_lines: int,
) -> SubgoalResult:
    """行数上限サブゴール。

    空文字列の場合は lines=0 扱い → score=1.0 (別サブゴールが担当)。
    """
    if not candidate:
        return SubgoalResult(
            goal="line_budget",
            score=1.0,
            passed=True,
            detail="empty candidate (別サブゴールで検出)",
        )
    lines = candidate.count("\n") + 1
    if lines <= max_lines:
        return SubgoalResult(
            goal="line_budget",
            score=1.0,
            passed=True,
            detail=f"{lines}/{max_lines} 行以内",
        )
    return SubgoalResult(
        goal="line_budget",
        score=0.0,  # hard fail → スコアは 0.0 固定
        passed=False,
        detail=f"{lines} 行 > 上限 {max_lines} 行",
    )


def _score_slop_free(
    candidate: str,
) -> SubgoalResult:
    """slop 検出サブゴール（slop_detector に接続）。

    detect_slop(candidate).slop_score（1.0=良い / 0.0=悪い）をスコアにする。
    slop_detector が import できない / 失敗する場合は従来通り pass(1.0) に
    フォールバックする（後方互換）。slop_score >= 0.7 で passed=True。
    """
    try:
        from slop_detector import detect_slop  # type: ignore[import]

        result = detect_slop(candidate)
        score = float(result.slop_score)
        passed = score >= 0.7
        return SubgoalResult(
            goal="slop_free",
            score=score,
            passed=passed,
            detail=f"slop_score {score:.2f} (hits={len(result.hits)})",
        )
    except Exception:
        return SubgoalResult(
            goal="slop_free",
            score=1.0,
            passed=True,
            detail="slop 検出器が利用不可（フォールバック pass）",
        )


# ── アグリゲーター ────────────────────────────────────────────────────


def _aggregate_subgoals(subgoals: List[SubgoalResult]) -> float:
    """サブゴールの等重み平均を計算する。

    subgoals が空の場合は 0.0 を返す（NaN を出さない）。
    """
    if not subgoals:
        return 0.0
    return sum(sg.score for sg in subgoals) / len(subgoals)


# ── 公開 API ─────────────────────────────────────────────────────────


def score_subgoals(
    candidate: str,
    original: Optional[str],
    corrections: List[Dict[str, Any]],
    *,
    max_lines: int = MAX_SKILL_LINES,
) -> SubgoalScorerResult:
    """候補テキストをサブゴールに分解して評価し結果を返す。

    Args:
        candidate:   評価対象のテキスト（最適化候補）
        original:    元のファイル内容。None なら origin 比較系サブゴールをスキップ
        corrections: corrections.jsonl のレコードリスト
        max_lines:   行数上限（デフォルト: MAX_SKILL_LINES=500）

    Returns:
        SubgoalScorerResult(total, subgoals)
        total は 0.0–1.0 の浮動小数点数。NaN にはならない。
    """
    subgoals: List[SubgoalResult] = [
        _score_frontmatter_preserved(candidate, original),
        _score_trigger_coverage(candidate, original),
        _score_correction_addressed(candidate, corrections),
        _score_line_budget(candidate, max_lines),
        _score_slop_free(candidate),
    ]

    total = _aggregate_subgoals(subgoals)

    return SubgoalScorerResult(total=total, subgoals=subgoals)
