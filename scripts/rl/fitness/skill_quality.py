#!/usr/bin/env python3
"""スキル品質スコアリング — CSO (Claude Search Optimization) 軸を含むルールベース評価。

skill_quality fitness は stdin/stdout スクリプトとして動作し、
スキルの構造品質を多軸でスコアリングする。
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from similarity import jaccard_coefficient, tokenize

# ── CSO 定数 ──────────────────────────────────────────
CSO_SUMMARY_THRESHOLD = 0.5
CSO_TRIGGER_BONUS = 0.1
CSO_MAX_TRIGGER_BONUS = 0.3
CSO_ACTION_BONUS = 0.1
CSO_MAX_DESCRIPTION_LENGTH = 1024
CSO_LENGTH_PENALTY = -0.1
CSO_WEIGHT = 0.15  # 8軸のうちの CSO の重み

# 行動促進パターン
CSO_ACTION_PATTERNS = re.compile(
    r"(?:Use (?:when|this skill when|this agent when)|"
    r"Trigger[:\s]|"
    r"トリガー[:\s]|"
    r"使用タイミング[:\s]|"
    r"以下の場合に使用)",
    re.IGNORECASE,
)

# ── red flags（危険サイン）節 ────────────────────────────
# addyosmani/agent-skills の authoring パターン（#62）。各 SKILL.md が「このスキルを
# サボってはいけない危険サイン」の自己点検節を持つことを品質要件にする。本文中の
# 見出し（markdown heading）として red flags / 危険サイン 等が存在するかを測る
# （散文中の言及は対象外＝自己点検「節」の有無を測るため heading に限定）。
RED_FLAGS_BONUS = 0.1  # red flags 節がある場合の skill_quality 加点
RED_FLAGS_HEADING = re.compile(
    r"^#{1,6}\s*.*(red\s*flags?|レッドフラグ|危険サイン|危険な兆候|赤信号)",
    re.IGNORECASE | re.MULTILINE,
)


def check_red_flags_section(content: str) -> Dict[str, Any]:
    """SKILL.md 本文に red flags（危険サイン）節があるかを検査する（#62）。

    Args:
        content: SKILL.md のテキスト内容

    Returns:
        {"present": bool, "bonus": float, "note": str | None}
        present=True なら bonus=RED_FLAGS_BONUS / note=None、
        present=False なら bonus=0.0 / note=指摘文（surface 用）。
    """
    present = bool(RED_FLAGS_HEADING.search(content or ""))
    return {
        "present": present,
        "bonus": RED_FLAGS_BONUS if present else 0.0,
        "note": None if present else (
            "red flags（危険サイン）節なし — スキルをサボってはいけない危険サインの"
            "自己点検節（## Red Flags / ## 危険サイン）を追加すると加点されます"
        ),
    }

# ── YAML frontmatter パーサー ──────────────────────────
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_frontmatter(content: str) -> Dict[str, str]:
    """YAML frontmatter からキーバリューをシンプル抽出する。"""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def _extract_first_paragraph(content: str) -> str:
    """本文（frontmatter 除去後）の最初の段落を抽出する。"""
    body = _FRONTMATTER_RE.sub("", content).strip()
    # 最初の空行までを段落とする
    lines: List[str] = []
    for line in body.splitlines():
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)
    return " ".join(lines)


def check_cso_compliance(skill_path: Path) -> Dict[str, Any]:
    """CSO チェックを実行し、スコアと詳細を返す。

    Args:
        skill_path: SKILL.md のパス

    Returns:
        {"score": float, "details": {...}, "penalties": [...], "bonuses": [...]}
    """
    try:
        content = skill_path.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return {"score": 0.0, "details": {}, "penalties": [], "bonuses": []}

    fm = _extract_frontmatter(content)
    description = fm.get("description", "")
    penalties: List[str] = []
    bonuses: List[str] = []
    score = 0.5  # ベーススコア

    if not description:
        return {
            "score": 0.0,
            "details": {"no_description": True},
            "penalties": ["description が未設定"],
            "bonuses": [],
        }

    # 1. 要約ペナルティ: description vs 本文冒頭の Jaccard 類似度
    first_paragraph = _extract_first_paragraph(content)
    if first_paragraph:
        desc_tokens = tokenize(description)
        para_tokens = tokenize(first_paragraph)
        if desc_tokens and para_tokens:
            similarity = jaccard_coefficient(desc_tokens, para_tokens)
            if similarity > CSO_SUMMARY_THRESHOLD:
                score -= 0.2
                penalties.append(
                    f"description が本文冒頭と高類似度 (Jaccard={similarity:.2f} > {CSO_SUMMARY_THRESHOLD})"
                )

    # 2. トリガー語ボーナス: description 内のトリガーワード
    try:
        from skill_triggers import extract_skill_triggers
        triggers = extract_skill_triggers(skill_path.parent.parent)
        skill_name = skill_path.parent.name
        skill_triggers_list = []
        for t in triggers:
            if t.get("skill_name") == skill_name:
                skill_triggers_list.extend(t.get("triggers", []))

        trigger_bonus = 0.0
        desc_lower = description.lower()
        matched_triggers: List[str] = []
        for trigger in skill_triggers_list:
            if trigger.lower() in desc_lower:
                trigger_bonus += CSO_TRIGGER_BONUS
                matched_triggers.append(trigger)
        trigger_bonus = min(trigger_bonus, CSO_MAX_TRIGGER_BONUS)
        if trigger_bonus > 0:
            score += trigger_bonus
            bonuses.append(
                f"トリガーワード {len(matched_triggers)} 個を含む (+{trigger_bonus:.1f})"
            )
    except (ImportError, Exception):
        pass  # skill_triggers が利用不可な場合はスキップ

    # 3. 行動促進ボーナス
    if CSO_ACTION_PATTERNS.search(description):
        score += CSO_ACTION_BONUS
        bonuses.append(f"行動促進形式を含む (+{CSO_ACTION_BONUS})")

    # 4. 長さペナルティ
    if len(description) > CSO_MAX_DESCRIPTION_LENGTH:
        score += CSO_LENGTH_PENALTY
        penalties.append(
            f"description が {len(description)} 文字 (推奨上限: {CSO_MAX_DESCRIPTION_LENGTH})"
        )

    # スコアをクランプ
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 3),
        "details": {
            "description_length": len(description),
            "has_action_pattern": bool(CSO_ACTION_PATTERNS.search(description)),
        },
        "penalties": penalties,
        "bonuses": bonuses,
    }


def evaluate_skill_quality(content: str, skill_dir: str) -> Optional[Dict[str, Any]]:
    """environment.py から呼ばれる CSO 品質評価のエントリポイント。

    Args:
        content: SKILL.md のテキスト内容
        skill_dir: SKILL.md を含むスキルディレクトリのパス

    Returns:
        {"overall": float, "cso": dict} or None（評価不可の場合）
    """
    skill_path = Path(skill_dir) / "SKILL.md"
    if not skill_path.exists():
        return None
    try:
        cso_result = check_cso_compliance(skill_path)
        # #62: red flags（危険サイン）節があれば加点（節を足すと overall が上がる）。
        # absent は減点でなく advisory note（非punitive・既存スキルのスコアは下げない）。
        rf_result = check_red_flags_section(content)
        overall = max(0.0, min(1.0, cso_result["score"] + rf_result["bonus"]))
        return {
            "overall": round(overall, 3),
            "cso": cso_result,
            "red_flags": rf_result,
        }
    except Exception:
        return None
