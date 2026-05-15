"""LLM 2軸スコアリング (external_dependency / judgment_complexity)。

Phase 8 / Slice 2 で `skill_evolve.py` から切り出し。
"""
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


# 外部依存キーワード（静的解析用）
_EXTERNAL_DEPENDENCY_KEYWORDS = [
    r"\bAPI\b", r"\baws\b", r"\bs3\b", r"\blambda\b", r"\bcdk\b",
    r"\bcloudformation\b", r"\bdocker\b", r"\bkubernetes\b", r"\bk8s\b",
    r"\bhttp[s]?\b", r"\bfetch\b", r"\bcurl\b", r"\bwebsearch\b",
    r"\bwebfetch\b", r"\bmcp\b", r"\bslack\b", r"\bgithub\b",
    r"\bdeploy\b", r"\bremote\b", r"\bcloud\b", r"\bsns\b",
    r"\bsqs\b", r"\bdynamodb\b", r"\bbedrock\b",
]


def _count_external_keywords(content: str) -> int:
    """外部依存キーワードの出現数を数える。"""
    count = 0
    for pattern in _EXTERNAL_DEPENDENCY_KEYWORDS:
        count += len(re.findall(pattern, content, re.IGNORECASE))
    return count


def _score_external_dependency(content: str) -> int:
    """外部依存度スコア (1-3)。静的解析。"""
    count = _count_external_keywords(content)
    if count >= 10:
        return 3  # 外部依存多数
    if count >= 3:
        return 2  # 一部外部連携
    return 1  # ローカル完結


def _score_judgment_complexity_llm(skill_name: str, content: str) -> int:
    """判断複雑さスコア (1-3)。LLMによる評価。"""
    prompt = (
        f"以下のスキル定義の「判断の複雑さ」を1-3で評価してください。\n"
        f"1 = 決定論的（手順が固定、分岐なし）\n"
        f"2 = 数箇所の条件分岐あり\n"
        f"3 = 判断・ヒューリスティクスが多数\n\n"
        f"スキル名: {skill_name}\n"
        f"内容（先頭2000文字）:\n```\n{content[:2000]}\n```\n\n"
        f"数字のみ（1, 2, 3のいずれか）で回答してください。"
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            score = int(result.stdout.strip()[0])
            if score in (1, 2, 3):
                return score
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        pass
    # フォールバック: 条件分岐/if/else の出現数で推定
    branches = len(re.findall(r"\b(if|else|elif|when|unless|場合|条件|判断)\b", content, re.IGNORECASE))
    if branches >= 8:
        return 3
    if branches >= 3:
        return 2
    return 1


def compute_llm_scores(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """LLM 2軸のスコアを計算する（キャッシュ付き）。

    Returns:
        {"external_dependency": int, "judgment_complexity": int, "cached": bool}
    """
    # キャッシュヘルパは __init__.py に残存。
    # mock.patch("skill_evolve.CACHE_FILE", ...) 互換のため関数内 lazy import。
    from . import _file_hash, _load_cache, _save_cache

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

    content = skill_md.read_text(encoding="utf-8")
    current_hash = _file_hash(skill_md)

    cache = _load_cache()
    cached = cache.get(skill_name, {})

    if cached.get("hash") == current_hash:
        return {
            "external_dependency": cached["external_dependency"],
            "judgment_complexity": cached["judgment_complexity"],
            "cached": True,
        }

    # 新規計算
    ext_score = _score_external_dependency(content)
    judge_score = _score_judgment_complexity_llm(skill_name, content)

    # キャッシュ更新
    cache[skill_name] = {
        "hash": current_hash,
        "external_dependency": ext_score,
        "judgment_complexity": judge_score,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_cache(cache)

    return {
        "external_dependency": ext_score,
        "judgment_complexity": judge_score,
        "cached": False,
    }
