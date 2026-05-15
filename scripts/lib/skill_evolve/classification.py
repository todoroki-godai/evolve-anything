"""自己進化済み判定 + 検証系判定 + 適性分類 + アンチパターン検出
+ LLM スコアリングキャッシュヘルパ。

Phase 8 / Slice 3 で `skill_evolve.py` から切り出し。
`CACHE_FILE` / `DATA_DIR` / 閾値定数 (`HIGH_SUITABILITY_THRESHOLD` 等) は
`__init__.py` を SoT として `from . import X` 関数本体内 lazy lookup で参照
（`mock.patch("skill_evolve.CACHE_FILE", ...)` 経路の互換維持）。
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List


# --- LLM スコアリングキャッシュ ---


def _file_hash(path: Path) -> str:
    """ファイルの SHA256 ハッシュを返す。"""
    content = path.read_text(encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_cache() -> Dict[str, Any]:
    """LLMスコアリングキャッシュを読み込む。"""
    from . import CACHE_FILE  # 関数内 lazy lookup (mock.patch 互換)
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    """LLMスコアリングキャッシュを保存する。"""
    from . import CACHE_FILE, DATA_DIR  # 関数内 lazy lookup (mock.patch 互換)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- 自己進化済み判定 ---


def is_self_evolved_skill(skill_dir: Path) -> bool:
    """スキルが既に自己進化パターンを持っているか判定する。

    判定条件:
    - references/pitfalls.md が存在する
    - SKILL.md に Failure-triggered Learning セクションが存在する
    """
    pitfalls = skill_dir / "references" / "pitfalls.md"
    skill_md = skill_dir / "SKILL.md"

    if not pitfalls.exists():
        return False
    if not skill_md.exists():
        return False

    content = skill_md.read_text(encoding="utf-8")
    return bool(re.search(r"(?i)failure[- ]triggered\s+learning", content))


def is_verification_skill(skill_name: str, skill_dir: Path) -> bool:
    """検証系スキルかどうかを判定する。

    スキル名またはSKILL.md内容にVERIFICATION_SKILL_KEYWORDSが含まれればTrue。
    検証系スキルは失敗時のインパクトが大きいため、テレメトリに関係なく
    自己進化パターンの組み込みを推奨する。
    """
    from . import VERIFICATION_SKILL_KEYWORDS  # SoT は __init__.py
    name_lower = skill_name.lower()
    for kw in VERIFICATION_SKILL_KEYWORDS:
        if kw in name_lower:
            return True

    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8").lower()
            for kw in VERIFICATION_SKILL_KEYWORDS:
                if kw in content:
                    return True
        except OSError:
            pass

    return False


# --- 分類 & アンチパターン検出 ---


def classify_suitability(total_score: int) -> str:
    """合計スコアから適性を3段階分類する。"""
    from . import HIGH_SUITABILITY_THRESHOLD, MEDIUM_SUITABILITY_THRESHOLD
    if total_score >= HIGH_SUITABILITY_THRESHOLD:
        return "high"
    if total_score >= MEDIUM_SUITABILITY_THRESHOLD:
        return "medium"
    return "low"


def detect_anti_patterns(
    scores: Dict[str, int],
    skill_dir: Path,
) -> List[Dict[str, str]]:
    """評価時3パターンのアンチパターンを検出する。

    Returns:
        [{"pattern": str, "reason": str}, ...]
    """
    from . import BAND_AID_THRESHOLD
    patterns: List[Dict[str, str]] = []

    # Noise Collector: 失敗多様性=1 かつ エラーデータあり
    # エラーデータ0件（テレメトリ不在）は判定不能として除外
    if scores.get("diversity", 0) == 1 and scores.get("error_count", 0) > 0:
        patterns.append({
            "pattern": "Noise Collector",
            "reason": "失敗パターンが少ないため、スキル本体の1回修正が効果的です",
        })

    # Context Bloat: 頻度=3 かつ 判断=1
    if scores.get("frequency", 0) == 3 and scores.get("judgment_complexity", 0) == 1:
        patterns.append({
            "pattern": "Context Bloat",
            "reason": "Pre-flight のトークンコストが学習価値を超える可能性があります",
        })

    # Band-Aid: references/ 内の知見蓄積が閾値超
    # SKILL.md の手順ステップ/チェックリストは除外し、
    # references/ のみカウントすることで誤検出を防ぐ
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        troubleshoot_items = 0
        for ref_file in refs_dir.glob("*.md"):
            ref_content = ref_file.read_text(encoding="utf-8")
            troubleshoot_items += len(re.findall(
                r"^[\s]*[-*]\s+", ref_content, re.MULTILINE
            ))
        if troubleshoot_items > BAND_AID_THRESHOLD:
            patterns.append({
                "pattern": "Band-Aid",
                "reason": f"references/ 内の知見蓄積が{troubleshoot_items}件超 — 設計見直しを推奨",
            })

    return patterns
