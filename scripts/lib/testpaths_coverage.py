"""testpaths_coverage.py — pytest.ini の testpaths が漏らす tests/ ディレクトリを決定論検出する（#468）。

LLM 非依存・静的解析のみ。背景:

CLAUDE.md「テスト」節の canonical コマンド
`python3 -m pytest hooks/ skills/ scripts/tests/ scripts/rl/tests/` が `scripts/lib/tests/`
（1111 件）を**収集していなかった**。コマンドにパスを列挙する運用は、新しい tests/ が
増えるたびに列挙更新を要し、漏れても気づけない（orphan_store #422 と同思想の「writer/reader が
あるのに突合が無い」ギャップの testpath 版）。

根治として pytest.ini に `testpaths` を宣言し bare `pytest` で全件が走るようにしたうえで、
本モジュールが「リポジトリ内に test_*.py を含む tests/ があるのに testpaths のどの path 配下にも
入らない」ディレクトリを検出して audit の observability に常設する。testpaths を拡張しても
新しい tests/ を足しても、突合は自動で追従する（手動列挙への依存を断つ）。

定義:
- 候補     = リポジトリ内で `test_*.py` を 1 件以上含む `tests/` ディレクトリ（相対パス）
- testpaths = pytest.ini `[pytest]` セクションの `testpaths` に列挙された path
- covered  = testpaths のいずれかの path と一致する／その配下にある候補
- uncovered = 候補のうち covered でないもの

突合は POSIX 相対パス文字列で行う（決定論・OS 非依存）。
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# 候補から除外するディレクトリ名（VCS / 仮想環境 / キャッシュ / worktree）。
_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".claude",  # worktree 等が入るため自リポジトリ走査では除外
}


def _default_repo_root() -> Path:
    """evolve-anything 自身のリポジトリ（= プラグイン）ルート。

    module 定数でなく関数にして呼び出し時に解決する（orphan_store の `_default_plugin_root`
    と同じ慣習）。テストは `repo_root` 引数で疑似ツリーに差し替えられる。
    """
    from plugin_root import PLUGIN_ROOT

    return PLUGIN_ROOT


@dataclass
class TestpathsCoverageReport:
    """testpaths カバレッジ検出結果。

    testpaths:     pytest.ini で宣言された testpaths（宣言順）。
    test_dirs:     リポジトリ内で見つかった候補 tests/ ディレクトリ（相対 POSIX、ソート済み）。
    uncovered:     testpaths のどの path 配下にも入らない候補（相対 POSIX、ソート済み）。
    has_testpaths: pytest.ini に testpaths 宣言があるか（無ければこのチェック非該当）。
    """

    testpaths: List[str] = field(default_factory=list)
    test_dirs: List[str] = field(default_factory=list)
    uncovered: List[str] = field(default_factory=list)
    has_testpaths: bool = False


def parse_testpaths(repo_root: Optional[Path] = None) -> List[str]:
    """pytest.ini `[pytest]` セクションの testpaths を宣言順のリストで返す。

    pytest.ini が無い・[pytest] が無い・testpaths が無い場合は空リスト。
    """
    root = repo_root if repo_root is not None else _default_repo_root()
    ini = root / "pytest.ini"
    if not ini.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(ini, encoding="utf-8")
    except (OSError, configparser.Error):
        return []
    if not parser.has_option("pytest", "testpaths"):
        return []
    raw = parser.get("pytest", "testpaths")
    # testpaths は空白（改行含む）区切り。POSIX 相対パスに正規化する。
    return [p.strip().rstrip("/") for p in raw.split() if p.strip()]


def find_test_dirs(repo_root: Optional[Path] = None) -> List[str]:
    """リポジトリ内で `test_*.py` を 1 件以上含む `tests/` ディレクトリ（相対 POSIX）を返す。"""
    root = repo_root if repo_root is not None else _default_repo_root()
    found: set[str] = set()
    for test_py in root.rglob("test_*.py"):
        parts = test_py.relative_to(root).parts
        if any(part in _EXCLUDE_DIRS for part in parts):
            continue
        parent = test_py.parent
        if parent.name != "tests":
            continue
        rel = parent.relative_to(root).as_posix()
        found.add(rel)
    return sorted(found)


def _is_covered(test_dir: str, testpaths: List[str]) -> bool:
    """test_dir が testpaths のいずれかと一致する／その配下にあるか。"""
    for tp in testpaths:
        if test_dir == tp or test_dir.startswith(tp + "/"):
            return True
    return False


def detect_uncovered_test_dirs(repo_root: Optional[Path] = None) -> TestpathsCoverageReport:
    """testpaths が収集しない（漏らす）tests/ ディレクトリを検出する（決定論）。"""
    root = repo_root if repo_root is not None else _default_repo_root()
    testpaths = parse_testpaths(root)
    has_testpaths = bool(testpaths)
    if not has_testpaths:
        # testpaths 宣言が無い PJ はこのチェック非該当（候補も突合しない）。
        return TestpathsCoverageReport(testpaths=[], test_dirs=[], uncovered=[], has_testpaths=False)
    test_dirs = find_test_dirs(root)
    uncovered = sorted(d for d in test_dirs if not _is_covered(d, testpaths))
    return TestpathsCoverageReport(
        testpaths=testpaths,
        test_dirs=test_dirs,
        uncovered=uncovered,
        has_testpaths=True,
    )
