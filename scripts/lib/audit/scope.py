"""重複検出 + 意味的類似度 + Scope Advisory。

audit パッケージから切り出された Scope モジュール。
- detect_duplicates_simple: ファイル名ベースの簡易重複検出
- semantic_similarity_check: TF-IDF + コサイン類似度判定（threshold 0.80）
- load_usage_registry: usage-registry.jsonl 読み込み
- scope_advisory: global スキルの使用 PJ 数 → 推奨アクション
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from ._constants import is_excluded_skill_path
from .classification import classify_artifact_origin


def _is_plugin_managed_path(path: Path) -> bool:
    """プラグイン管理パス（gstack 等）／バックアップパスかどうかを判定する。

    重複候補から除外すべきパス:
    - gstack は同一スキルを複数サブディレクトリ（.hermes/.kiro/.openclaw 等）に
      意図的にコピーするため、`gstack` を含むパスは除外する
    - `.gstack-backup` / `.archive` 配下のコピー（is_excluded_skill_path）も除外する
      （`.gstack-backup` は実スキルと 1:1 でコピーされ phantom duplicate の主因）
    """
    parts = path.parts
    return any(part in ("gstack",) for part in parts) or is_excluded_skill_path(path)


def detect_duplicates_simple(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """簡易的な重複検出（ファイル名ベース）。LLM ベースの意味的類似度判定は別途実行。

    プラグイン管理パス（gstack 等）は除外する。
    意図的なコピーを誤って重複候補に含めないため。
    """
    seen: Dict[str, List[str]] = {}
    duplicates = []

    for category in ["skills", "rules"]:
        for path in artifacts.get(category, []):
            if _is_plugin_managed_path(path):
                continue
            name = path.stem if category == "rules" else path.parent.name
            # category を key に含めてスキルとルールを別 namespace で管理
            key = f"{category}:{name.lower().replace('-', '').replace('_', '')}"
            if key not in seen:
                seen[key] = []
            seen[key].append(str(path))

    for key, paths in seen.items():
        if len(paths) > 1:
            duplicates.append({"name": key, "paths": paths})

    return duplicates


def semantic_similarity_check(
    artifacts: Dict[str, List[Path]], threshold: float = 0.80
) -> List[Dict[str, Any]]:
    """TF-IDF + コサイン類似度による意味的類似度判定。閾値は 80%。

    audit-report spec の Single Source of Truth。
    prune はこの関数の結果を利用する。

    Returns:
        [{"path_a": str, "path_b": str, "similarity": float}, ...]
    """
    from similarity import compute_pairwise_similarity

    path_dict: Dict[str, str] = {}
    for path in artifacts.get("skills", []):
        if classify_artifact_origin(path) == "plugin":
            continue
        skill_name = path.parent.name
        path_dict[skill_name] = str(path)

    for path in artifacts.get("rules", []):
        if classify_artifact_origin(path) == "plugin":
            continue
        rule_stem = path.stem
        path_dict[rule_stem] = str(path)

    return compute_pairwise_similarity(path_dict, threshold)


def load_usage_registry() -> Dict[str, List[Dict[str, Any]]]:
    """Usage Registry からデータを読み込む。"""
    # DATA_DIR は audit パッケージ経由で遅延参照（test patch 追従）
    from . import DATA_DIR as _DATA_DIR

    registry_file = _DATA_DIR / "usage-registry.jsonl"
    if not registry_file.exists():
        return {}

    result: Dict[str, List[Dict[str, Any]]] = {}
    for line in registry_file.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            skill = rec.get("skill_name", "")
            if skill not in result:
                result[skill] = []
            result[skill].append(rec)
        except json.JSONDecodeError:
            continue
    return result


def scope_advisory(registry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Scope Advisory: global スキルの使用PJ数と推奨アクションを生成。"""
    advisories = []
    for skill, records in registry.items():
        projects = set(r.get("project_path", "") for r in records)
        latest = max((r.get("timestamp", "") for r in records), default="")
        advisory = {
            "skill": skill,
            "project_count": len(projects),
            "projects": list(projects),
            "last_used": latest,
            "recommendation": "keep global" if len(projects) > 1 else "consider project-scope",
        }
        advisories.append(advisory)
    return advisories
