"""スキル個別の検査・分類ヘルパ群（旧 prune.py 由来）。

- frontmatter 解析（_count_triggers, extract_skill_summary, _resolve_skill_md）
- キーワード/トリガー数ベースの推薦（suggest_recommendation, _enrich_candidate）
- 参照型スキル判定 + 推定キャッシュ（is_reference_skill, _estimate_skill_type,
  _load_skill_type_cache, _save_skill_type_cache）
- 減衰スコア / pin / skill ディレクトリ判定（compute_decay_score, is_pinned, _is_skill_dir）

prune/__init__.py から re-export される（後方互換）。
DATA_DIR と LLM 推定関数は package 経由で遅延参照する
（テスト mock.patch("prune.DATA_DIR", ...) / mock.patch("prune._estimate_skill_type", ...) 追従）。
"""
import json
import math
from pathlib import Path
from typing import Any, Dict

from frontmatter import extract_description, parse_frontmatter

from .config import CORRECTION_PENALTY, DEFAULT_DECAY_DAYS


_ARCHIVE_KEYWORDS = ["debug", "temp", "hotfix", "workaround", "test-"]
_KEEP_KEYWORDS = ["daily", "pipeline", "utility"]
_KEEP_TRIGGER_THRESHOLD = 3


def _count_triggers(skill_path: Path) -> int:
    """SKILL.md の frontmatter から Trigger 数を取得する。"""
    p = Path(skill_path)
    if p.name != "SKILL.md":
        candidate = p.parent / "SKILL.md" if p.is_file() else p / "SKILL.md"
        p = candidate
    fm = parse_frontmatter(p)
    desc = fm.get("description", "")
    if not isinstance(desc, str):
        return 0
    for line in desc.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("trigger"):
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                return len([t.strip() for t in parts[1].split(",") if t.strip()])
    return 0


def extract_skill_summary(skill_path: Path) -> str:
    """SKILL.md の frontmatter から description を抽出する。

    extract_description() のラッパー。skill_path が SKILL.md でない場合は
    同ディレクトリの SKILL.md を探す。
    """
    p = Path(skill_path)
    if p.name != "SKILL.md":
        candidate = p.parent / "SKILL.md" if p.is_file() else p / "SKILL.md"
        p = candidate
    return extract_description(p)


def _enrich_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """候補に description と recommendation を付与する。"""
    from . import extract_skill_summary, _count_triggers, suggest_recommendation  # noqa: PLC0415

    path = Path(candidate["file"])
    candidate["description"] = extract_skill_summary(path)
    candidate["trigger_count"] = _count_triggers(path)
    candidate["recommendation"] = suggest_recommendation(candidate)
    return candidate


def suggest_recommendation(skill_info: Dict[str, Any]) -> str:
    """キーワードベースの一次推薦ラベルを返す。

    Args:
        skill_info: skill_name, description, trigger_count を含む辞書

    Returns:
        "archive推奨", "keep推奨", "要確認" のいずれか
    """
    if skill_info.get("is_reference"):
        if skill_info.get("has_drift"):
            return "要確認"
        return "keep推奨"

    name = skill_info.get("skill_name", "").lower()
    desc = skill_info.get("description", "").lower()
    trigger_count = skill_info.get("trigger_count", 0)
    text = f"{name} {desc}"

    if any(kw in text for kw in _ARCHIVE_KEYWORDS):
        return "archive推奨"
    if any(kw in text for kw in _KEEP_KEYWORDS) or trigger_count >= _KEEP_TRIGGER_THRESHOLD:
        return "keep推奨"
    return "要確認"


def _load_skill_type_cache() -> Dict[str, Any]:
    """evolve-state.json から skill_type_cache を読み込む。"""
    from . import DATA_DIR  # noqa: PLC0415

    state_file = DATA_DIR / "evolve-state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            cache = state.get("skill_type_cache", {})
            if isinstance(cache, dict):
                return cache
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return {}


def _save_skill_type_cache(cache: Dict[str, Any]) -> None:
    """evolve-state.json に skill_type_cache を書き込む。"""
    from . import DATA_DIR  # noqa: PLC0415

    state_file = DATA_DIR / "evolve-state.json"
    state: Dict[str, Any] = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, TypeError):
            state = {}
    state["skill_type_cache"] = cache
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_skill_md(skill_path: Path) -> Path:
    """スキルパスから SKILL.md を解決する。"""
    p = Path(skill_path)
    if p.name != "SKILL.md":
        candidate = p.parent / "SKILL.md" if p.is_file() else p / "SKILL.md"
        return candidate
    return p


def is_reference_skill(skill_path: Path) -> bool:
    """スキルが参照型かどうかを判定する。

    優先順位: frontmatter → キャッシュ → LLM 推定。
    LLM 推定失敗時は False（action 扱い）を返す。
    """
    from . import (  # noqa: PLC0415
        _estimate_skill_type,
        _load_skill_type_cache,
        _save_skill_type_cache,
    )

    resolved = _resolve_skill_md(skill_path)

    fm = parse_frontmatter(resolved)
    skill_type = fm.get("type", "")
    if skill_type:
        return skill_type == "reference"

    skill_key = str(resolved)
    cache = _load_skill_type_cache()
    if skill_key in cache:
        entry = cache[skill_key]
        try:
            cached_mtime = entry.get("mtime", 0)
            current_mtime = resolved.stat().st_mtime if resolved.exists() else 0
            if current_mtime <= cached_mtime:
                return entry.get("type") == "reference"
        except OSError:
            pass

    try:
        if not resolved.exists():
            return False
        content = resolved.read_text(encoding="utf-8")
        estimated_type = _estimate_skill_type(content)

        try:
            current_mtime = resolved.stat().st_mtime
        except OSError:
            current_mtime = 0
        cache[skill_key] = {"type": estimated_type, "mtime": current_mtime}
        _save_skill_type_cache(cache)

        return estimated_type == "reference"
    except Exception:
        return False


def _estimate_skill_type(content: str) -> str:
    """スキル内容からタイプを推定する。

    LLM サブエージェント呼び出しのプレースホルダ。
    実際の prune スキル実行時はサブエージェントで置換される。
    ここではキーワードベースのフォールバック推定を提供。
    """
    lower = content.lower()
    reference_signals = [
        "ガイド", "guide", "仕様", "specification", "spec",
        "デザインシステム", "design system", "リファレンス", "reference",
        "評価基準", "criteria", "ルールブック", "rulebook",
        "type: reference",
    ]
    action_signals = [
        "trigger:", "トリガー", "使用タイミング",
        "steps", "手順", "実行", "execute",
        "run ", "deploy", "create", "generate",
    ]
    ref_score = sum(1 for sig in reference_signals if sig in lower)
    act_score = sum(1 for sig in action_signals if sig in lower)
    return "reference" if ref_score > act_score else "action"


def compute_decay_score(
    age_days: float,
    correction_count: int = 0,
    decay_days: float = DEFAULT_DECAY_DAYS,
) -> float:
    """confidence = base_score * exp(-age_days / decay_days) を計算する。

    base_score = max(0.0, 1.0 - CORRECTION_PENALTY * correction_count)
    """
    base_score = max(0.0, 1.0 - CORRECTION_PENALTY * correction_count)
    return base_score * math.exp(-age_days / decay_days)


def is_pinned(skill_path: Path) -> bool:
    """.pin ファイルが存在するかチェックする。"""
    skill_dir = skill_path.parent if skill_path.is_file() else skill_path
    return (skill_dir / ".pin").exists()


def _is_skill_dir(path: Path) -> bool:
    """`skills/<name>` 形式のスキルディレクトリ全体かを判定する。"""
    if not path.is_dir():
        return False
    parent = path.parent
    if parent.name != "skills":
        return False
    return (path / "SKILL.md").exists() or (path / "scripts").exists()
