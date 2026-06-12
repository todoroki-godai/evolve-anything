"""プラグイン由来スキル判定・出自分類ロジック。

audit パッケージから切り出された Classification モジュール。
- _load_plugin_skill_map / _build_plugin_prefixes / _load_plugin_skill_names:
  installed_plugins.json → スキル名マッピングをキャッシュ付きで構築
- classify_usage_skill: usage レコードのスキル名 → プラグイン名分類
- classify_artifact_origin: ファイルパス → "plugin" / "global" / "custom"

skill_origin.py の薄いラッパー + テスト後方互換のためのインライン分岐を保持。
テストが `audit.classification._plugin_skill_map_cache` を直接セットした場合は
そちらを優先する（テスト isolation 用）。
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

from skill_origin import (
    classify_skill_origin as _so_classify_skill_origin,
    get_plugin_skill_map as _so_get_plugin_skill_map,
    build_plugin_prefixes as _so_build_plugin_prefixes,
    classify_usage_skill as _so_classify_usage_skill,
)

# テスト後方互換: audit.classification._plugin_skill_map_cache を直接セットするテスト向け
_plugin_skill_map_cache: Optional[Dict[str, str]] = None

# プラグイン名 → prefix パターンのキャッシュ
_plugin_prefix_cache: Optional[Dict[str, List[str]]] = None


def _load_plugin_skill_map() -> Dict[str, str]:
    """installed_plugins.json → {skill_name: plugin_name} マッピングを構築。

    skill_origin.py に委譲。後方互換のためラッパーとして残す。
    テストが _plugin_skill_map_cache を直接セットした場合はそちらを優先。
    """
    if _plugin_skill_map_cache is not None:
        _build_plugin_prefixes(_plugin_skill_map_cache)
        return _plugin_skill_map_cache
    mapping = _so_get_plugin_skill_map()
    _build_plugin_prefixes(mapping)
    return mapping


def _build_plugin_prefixes(mapping: Dict[str, str]) -> None:
    """skill_origin.py に委譲。後方互換ラッパー。"""
    global _plugin_prefix_cache
    _plugin_prefix_cache = _so_build_plugin_prefixes(mapping)


def classify_usage_skill(skill_name: str) -> Optional[str]:
    """usage レコードのスキル名をプラグインに分類する。

    skill_origin.py に委譲。後方互換ラッパー。
    """
    _load_plugin_skill_map()  # prefix キャッシュ初期化を保証
    return _so_classify_usage_skill(skill_name)


def _load_plugin_skill_names() -> frozenset:
    """後方互換ラッパー。テストが _plugin_skill_map_cache を直接セットした場合はそちらを優先。"""
    if _plugin_skill_map_cache is not None:
        return frozenset(_plugin_skill_map_cache.keys())
    return frozenset(_load_plugin_skill_map().keys())


def classify_artifact_origin(path: Path) -> str:
    """スキル/ルールの出自を分類する。skill_origin.py に委譲。

    テストが _plugin_skill_map_cache を直接セットした場合は、
    そのキャッシュを使ってインライン判定する（後方互換）。

    Returns:
        "plugin" — プラグイン由来
        "plugin_self" — プラグイン本体リポジトリ自身の repo 直下 skills/（#185）
        "global" — ~/.claude/skills/ 配下
        "custom" — その他（プロジェクトローカル等）
    """
    if _plugin_skill_map_cache is not None:
        # テスト後方互換: ローカルキャッシュが設定されている場合はインライン判定
        from skill_origin import _is_plugin_self_skill

        resolved = path.expanduser().resolve()
        resolved_str = str(resolved)

        plugins_dir = os.environ.get("CLAUDE_PLUGINS_DIR")
        if plugins_dir:
            plugins_path = str(Path(plugins_dir).resolve())
        else:
            plugins_path = str(Path.home() / ".claude" / "plugins" / "cache")

        if resolved_str.startswith(plugins_path):
            return "plugin"

        global_skills_path = str(Path.home() / ".claude" / "skills")
        if resolved_str.startswith(global_skills_path):
            return "global"

        if _is_plugin_self_skill(resolved):
            return "plugin_self"

        if "/.claude/skills/" in resolved_str:
            parts = resolved.parts
            try:
                skills_idx = len(parts) - 1 - list(reversed(parts)).index("skills")
                if skills_idx + 1 < len(parts):
                    skill_dir_name = parts[skills_idx + 1]
                    if skill_dir_name in _plugin_skill_map_cache:
                        return "plugin"
            except ValueError:
                pass

        return "custom"

    return _so_classify_skill_origin(path)
