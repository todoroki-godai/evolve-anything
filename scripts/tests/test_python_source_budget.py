"""Slice 13 — Python source 行数バジェット検出のテスト。"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

from audit.artifacts import check_python_source_budgets


def _make_py(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines, encoding="utf-8")


def test_warn_threshold(tmp_path):
    _make_py(tmp_path / "scripts" / "warn_size.py", 600)
    out = check_python_source_budgets(tmp_path)
    assert len(out) == 1
    v = out[0]
    assert v["lines"] == 601  # +1 from trailing blank
    assert v["warning_only"] is True
    assert v.get("hard") is not True
    assert v["kind"] == "python_source_budget"


def test_hard_threshold(tmp_path):
    _make_py(tmp_path / "hooks" / "huge.py", 900)
    out = check_python_source_budgets(tmp_path)
    assert len(out) == 1
    v = out[0]
    assert v["hard"] is True
    assert v.get("warning_only") is not True


def test_excludes_init_and_conftest(tmp_path):
    _make_py(tmp_path / "scripts" / "lib" / "__init__.py", 1500)
    _make_py(tmp_path / "scripts" / "tests" / "conftest.py", 1500)
    assert check_python_source_budgets(tmp_path) == []


def test_excludes_tests_dir(tmp_path):
    _make_py(tmp_path / "scripts" / "tests" / "test_big.py", 1500)
    assert check_python_source_budgets(tmp_path) == []


def test_under_threshold_no_violation(tmp_path):
    _make_py(tmp_path / "scripts" / "small.py", 100)
    assert check_python_source_budgets(tmp_path) == []
