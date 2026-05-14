"""verification_catalog 内部ヘルパー (_detect_primary_language / _iter_source_files /
_has_cross_module_pattern / _is_test_file) のテスト。

PR-B: test_verification_catalog.py から機能別に分割。
共通 helper は conftest.py を参照。
"""
from pathlib import Path

import pytest

from conftest import (
    _PY_CROSS_MODULE,
    _PY_NO_PATTERN,
    _TS_CROSS_MODULE,
    _create_py_files,
    _create_ts_files,
)

from lib.verification_catalog import (
    _detect_primary_language,
    _has_cross_module_pattern,
    _is_test_file,
    _iter_source_files,
)


class TestDetectPrimaryLanguage:
    def test_python_project(self, tmp_path):
        _create_py_files(tmp_path, 5)
        assert _detect_primary_language(tmp_path) == "python"

    def test_typescript_project(self, tmp_path):
        _create_ts_files(tmp_path, 5)
        assert _detect_primary_language(tmp_path) == "typescript"

    def test_equal_count_defaults_python(self, tmp_path):
        _create_py_files(tmp_path, 3)
        _create_ts_files(tmp_path, 3)
        assert _detect_primary_language(tmp_path) == "python"

    def test_empty_project(self, tmp_path):
        assert _detect_primary_language(tmp_path) == "python"


class TestIterSourceFiles:
    def test_excludes_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.py").write_text("x = 1")
        (tmp_path / "main.py").write_text("x = 1")

        files = list(_iter_source_files(tmp_path))
        names = [f.name for f in files]
        assert "main.py" in names
        assert "index.py" not in names

    def test_excludes_pycache(self, tmp_path):
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "cached.py").write_text("x = 1")
        (tmp_path / "real.py").write_text("x = 1")

        files = list(_iter_source_files(tmp_path))
        names = [f.name for f in files]
        assert "real.py" in names
        assert "cached.py" not in names


class TestHasCrossModulePattern:
    def test_python_match(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text(_PY_CROSS_MODULE)
        assert _has_cross_module_pattern(f) is True

    def test_python_no_match(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text(_PY_NO_PATTERN)
        assert _has_cross_module_pattern(f) is False

    def test_typescript_match(self, tmp_path):
        f = tmp_path / "mod.ts"
        f.write_text(_TS_CROSS_MODULE)
        assert _has_cross_module_pattern(f) is True

    def test_unknown_extension(self, tmp_path):
        f = tmp_path / "mod.rb"
        f.write_text("require 'foo'\nhash = {a: 1}")
        assert _has_cross_module_pattern(f) is False

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "missing.py"
        assert _has_cross_module_pattern(f) is False


class TestIsTestFile:
    def test_python_test_prefix(self):
        assert _is_test_file(Path("src/test_handler.py")) is True

    def test_python_test_suffix(self):
        assert _is_test_file(Path("src/handler_test.py")) is True

    def test_ts_test_suffix(self):
        assert _is_test_file(Path("src/handler.test.ts")) is True

    def test_tsx_test_suffix(self):
        assert _is_test_file(Path("src/component.test.tsx")) is True

    def test_tests_dir(self):
        assert _is_test_file(Path("src/__tests__/handler.py")) is True

    def test_normal_file(self):
        assert _is_test_file(Path("src/handler.py")) is False
