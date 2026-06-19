"""スキルの診断スコープ判定ユーティリティ。

assessment / reorganize / prune など複数モジュールで「どのスキルを診断対象とするか」
の判断を統一するための共通関数。

ルール:
  - origin == "plugin"  → 常に除外（evolve-anything 本体スキル）
  - origin == "global"  → デフォルト除外。evolve_global_allowlist に含まれる場合のみ対象
  - origin == "custom"  → 対象（symlink は除外）
"""
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def load_global_allowlist() -> Set[str]:
    """userConfig の evolve_global_allowlist をロードして set で返す。"""
    try:
        from rl_common.config import load_user_config
        cfg = load_user_config()
        raw = cfg.get("evolve_global_allowlist", "")
        return {s.strip() for s in raw.split(",") if s.strip()}
    except Exception:
        return set()


def iter_target_skills(
    artifacts: Dict[str, List[Any]],
    *,
    allowlist: Optional[Set[str]] = None,
) -> Iterable[Path]:
    """診断対象のスキルパスを yield する。

    Args:
        artifacts: find_artifacts() の返り値
        allowlist: global スキルのうち対象に含めるスキル名セット。
                   None の場合は load_global_allowlist() を呼ぶ。
    """
    if allowlist is None:
        allowlist = load_global_allowlist()

    try:
        from audit import classify_artifact_origin
    except ImportError:
        import sys
        from plugin_root import PLUGIN_ROOT
        sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "audit" / "scripts"))
        from audit import classify_artifact_origin

    for path in artifacts.get("skills", []):
        path = Path(path)
        origin = classify_artifact_origin(path)
        if origin == "plugin":
            continue
        if origin == "global":
            if path.parent.name not in allowlist:
                continue
        # custom: symlink は除外
        if origin == "custom" and path.parent.is_symlink():
            continue
        yield path


def filter_artifacts_to_target(
    artifacts: Dict[str, List[Any]],
    *,
    allowlist: Optional[Set[str]] = None,
) -> Dict[str, List[Path]]:
    """artifacts["skills"] を診断対象のみに絞った新しい dict を返す。

    reorganize / prune など artifacts dict をそのまま受け取る関数への引き渡しに使う。
    """
    target = list(iter_target_skills(artifacts, allowlist=allowlist))
    return {**artifacts, "skills": target}
