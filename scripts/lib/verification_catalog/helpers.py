"""verification_catalog の共通ヘルパー。

`_safe_result` / `_detect_primary_language` / `_iter_source_files` /
`_is_test_file` / `_has_cross_module_pattern` などソース走査・テストファイル
判定・モジュール間変換パターン検出を担当する。
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict

from .templates import _TEST_DIR_NAMES, _TEST_FILE_PATTERNS

logger = logging.getLogger(__name__)

# ── 走査制御定数 ──────────────────────────────────────
LARGE_REPO_FILE_THRESHOLD = 1000

EXCLUDE_DIRS = {"node_modules", ".venv", "__pycache__", ".git", ".tox", "dist", "build"}
PRIORITY_DIRS = ["scripts", "src", "lib", "skills"]

# ── モジュール間変換パターン (data-contract 用) ───────
# Python: from X import Y + dict 構築パターン
_PY_IMPORT_RE = re.compile(r"^from\s+\w+(?:\.\w+)*\s+import\s+", re.MULTILINE)
_PY_DICT_BUILD_RE = re.compile(
    r'(?:\w+\s*=\s*\{[^}]*:[^}]*\}|\.append\(\s*\{|result\[|data\[)',
    re.MULTILINE,
)

# TypeScript: import { X } from "Y" + オブジェクトリテラル
_TS_IMPORT_RE = re.compile(r'^import\s+\{[^}]+\}\s+from\s+["\']', re.MULTILINE)
_TS_OBJ_BUILD_RE = re.compile(
    r'(?:const\s+\w+\s*[:=]\s*\{|\w+\s*=\s*\{[^}]*:[^}]*\})',
    re.MULTILINE,
)


def _safe_result(error_msg: str = "") -> Dict[str, Any]:
    """エラー/タイムアウト時の安全な返り値。"""
    if error_msg:
        logger.warning("verification detection error: %s", error_msg)
    return {"applicable": False, "evidence": [], "confidence": 0.0}


def _iter_source_files(project_dir: Path):
    """走査対象ファイルを yield する。大規模リポジトリでは優先ディレクトリに限定。"""
    # ファイル数チェック（簡易）
    all_files = []
    try:
        for p in project_dir.rglob("*"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if p.is_file() and p.suffix in (".py", ".ts", ".tsx"):
                all_files.append(p)
                if len(all_files) > LARGE_REPO_FILE_THRESHOLD:
                    break
    except (PermissionError, OSError):
        pass

    if len(all_files) > LARGE_REPO_FILE_THRESHOLD:
        # 大規模リポジトリ → 優先ディレクトリのみ
        for d in PRIORITY_DIRS:
            dir_path = project_dir / d
            if not dir_path.is_dir():
                continue
            try:
                for p in dir_path.rglob("*"):
                    if any(part in EXCLUDE_DIRS for part in p.parts):
                        continue
                    if p.is_file() and p.suffix in (".py", ".ts", ".tsx"):
                        yield p
            except (PermissionError, OSError):
                continue
    else:
        yield from all_files


def _detect_primary_language(project_dir: Path) -> str:
    """プロジェクトの主要言語を .py vs .ts/.tsx ファイル数で判定する。同数時は Python。"""
    py_count = 0
    ts_count = 0
    for f in _iter_source_files(project_dir):
        if f.suffix == ".py":
            py_count += 1
        elif f.suffix in (".ts", ".tsx"):
            ts_count += 1
    return "typescript" if ts_count > py_count else "python"


def _has_cross_module_pattern(filepath: Path) -> bool:
    """ファイルがモジュール間変換パターンを持つか判定する。"""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except (PermissionError, OSError):
        return False

    if filepath.suffix == ".py":
        return bool(_PY_IMPORT_RE.search(content) and _PY_DICT_BUILD_RE.search(content))
    elif filepath.suffix in (".ts", ".tsx"):
        return bool(_TS_IMPORT_RE.search(content) and _TS_OBJ_BUILD_RE.search(content))
    return False


def _is_test_file(filepath: Path) -> bool:
    """テストファイルかどうかを判定する。"""
    if _TEST_FILE_PATTERNS.match(filepath.name):
        return True
    return any(part in _TEST_DIR_NAMES for part in filepath.parts)
