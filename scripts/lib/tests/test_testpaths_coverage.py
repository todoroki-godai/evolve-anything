"""testpaths_coverage（pytest.ini の testpaths が漏らす tests/ ディレクトリ）検出のテスト。

決定論・LLM 非依存。tmp_path に疑似プラグインツリー（pytest.ini + 複数の tests/ ディレクトリ）
を作って静的突合する。実プラグインツリーに依存しないため、別の tests/ が増減しても
このテストは安定する（#468）。

突合方針:
- 候補 = リポジトリ内で `test_*.py` を 1 件以上含む `tests/` ディレクトリ
- 収集対象 = pytest.ini の `testpaths` に列挙された各 path に含まれるディレクトリ
- uncovered = 候補のうち testpaths のどの path 配下にも入らないもの
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import testpaths_coverage  # noqa: E402
from audit.sections_testpaths import build_testpaths_coverage_section  # noqa: E402


def _make_repo(
    tmp_path: Path,
    *,
    pytest_ini: str | None,
    tests_dirs: list[str],
) -> Path:
    """疑似リポジトリツリーを作る。

    pytest_ini: pytest.ini の本文（None なら pytest.ini を置かない）
    tests_dirs: `test_x.py` を 1 件置く tests/ ディレクトリの相対パス一覧
    """
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    if pytest_ini is not None:
        (root / "pytest.ini").write_text(pytest_ini, encoding="utf-8")
    for rel in tests_dirs:
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_sample.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return root


_INI_WITH_TESTPATHS = """[pytest]
testpaths = hooks skills scripts/tests scripts/rl/tests scripts/lib/tests
"""

_INI_MISSING_LIB = """[pytest]
testpaths = hooks skills scripts/tests scripts/rl/tests
"""

_INI_NO_TESTPATHS = """[pytest]
addopts = --import-mode=importlib
"""


def test_parse_testpaths_reads_pytest_ini(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, pytest_ini=_INI_WITH_TESTPATHS, tests_dirs=[])
    paths = testpaths_coverage.parse_testpaths(root)
    assert paths == [
        "hooks",
        "skills",
        "scripts/tests",
        "scripts/rl/tests",
        "scripts/lib/tests",
    ]


def test_parse_testpaths_empty_when_absent(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, pytest_ini=_INI_NO_TESTPATHS, tests_dirs=[])
    assert testpaths_coverage.parse_testpaths(root) == []


def test_find_test_dirs_lists_dirs_with_test_files(tmp_path: Path) -> None:
    root = _make_repo(
        tmp_path,
        pytest_ini=_INI_WITH_TESTPATHS,
        tests_dirs=["scripts/lib/tests", "scripts/tests"],
    )
    found = testpaths_coverage.find_test_dirs(root)
    assert "scripts/lib/tests" in found
    assert "scripts/tests" in found


def test_detect_uncovered_when_lib_tests_missing(tmp_path: Path) -> None:
    """testpaths が scripts/lib/tests を含まないと uncovered に出る（#468 の症状）。"""
    root = _make_repo(
        tmp_path,
        pytest_ini=_INI_MISSING_LIB,
        tests_dirs=["scripts/tests", "scripts/lib/tests"],
    )
    report = testpaths_coverage.detect_uncovered_test_dirs(root)
    assert "scripts/lib/tests" in report.uncovered
    assert "scripts/tests" not in report.uncovered
    assert report.testpaths == ["hooks", "skills", "scripts/tests", "scripts/rl/tests"]


def test_detect_clean_when_all_covered(tmp_path: Path) -> None:
    root = _make_repo(
        tmp_path,
        pytest_ini=_INI_WITH_TESTPATHS,
        tests_dirs=["scripts/tests", "scripts/lib/tests"],
    )
    report = testpaths_coverage.detect_uncovered_test_dirs(root)
    assert report.uncovered == []


def test_detect_subdir_of_testpath_is_covered(tmp_path: Path) -> None:
    """testpaths に親 path があれば、その配下のネストした tests/ も covered 扱い。"""
    root = _make_repo(
        tmp_path,
        pytest_ini="[pytest]\ntestpaths = scripts\n",
        tests_dirs=["scripts/lib/tests", "scripts/rl/tests"],
    )
    report = testpaths_coverage.detect_uncovered_test_dirs(root)
    assert report.uncovered == []


def test_no_pytest_ini_returns_empty_report(tmp_path: Path) -> None:
    """pytest.ini が無い PJ（このチェック非該当）は uncovered/testpaths とも空。"""
    root = _make_repo(tmp_path, pytest_ini=None, tests_dirs=["tests"])
    report = testpaths_coverage.detect_uncovered_test_dirs(root)
    assert report.uncovered == []
    assert report.testpaths == []
    assert report.has_testpaths is False


# --- observability section builder ---


def test_section_none_when_no_testpaths(tmp_path: Path) -> None:
    root = _make_repo(tmp_path, pytest_ini=_INI_NO_TESTPATHS, tests_dirs=["tests"])
    assert build_testpaths_coverage_section(root) is None


def test_section_clean_marker_when_all_covered(tmp_path: Path) -> None:
    root = _make_repo(
        tmp_path,
        pytest_ini=_INI_WITH_TESTPATHS,
        tests_dirs=["scripts/lib/tests"],
    )
    section = build_testpaths_coverage_section(root)
    assert section is not None
    assert any("✓" in line for line in section)


def test_section_warns_with_evidence_when_uncovered(tmp_path: Path) -> None:
    root = _make_repo(
        tmp_path,
        pytest_ini=_INI_MISSING_LIB,
        tests_dirs=["scripts/tests", "scripts/lib/tests"],
    )
    section = build_testpaths_coverage_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "scripts/lib/tests" in joined
