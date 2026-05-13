"""Tests for scripts/lib/test_guard.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_guard as tg  # noqa: E402


def test_detect_languages_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert tg.detect_languages(tmp_path) == {"python"}


def test_detect_languages_js_and_python(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "requirements.txt").write_text("")
    assert tg.detect_languages(tmp_path) == {"python", "js"}


def test_detect_languages_empty(tmp_path):
    assert tg.detect_languages(tmp_path) == set()


def test_uses_llm_sdk_python_anthropic(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["anthropic>=0.40"]\n'
    )
    assert tg.uses_llm_sdk(tmp_path, {"python"}) is True


def test_uses_llm_sdk_python_no_sdk(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.0\n")
    assert tg.uses_llm_sdk(tmp_path, {"python"}) is False


def test_uses_llm_sdk_js_anthropic(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"@anthropic-ai/sdk": "^0.30"}}'
    )
    assert tg.uses_llm_sdk(tmp_path, {"js"}) is True


def test_has_precommit_hook_present(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: no-llm-in-tests\n"
    )
    assert tg.has_precommit_hook(tmp_path) is True


def test_has_precommit_hook_missing(tmp_path):
    assert tg.has_precommit_hook(tmp_path) is False


def test_has_pytest_no_llm_present(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndev-dependencies = ["pytest-no-llm"]\n'
    )
    assert tg.has_pytest_no_llm(tmp_path) is True


def test_has_tests_detects_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    assert tg.has_tests(tmp_path, {"python"}) is True


def test_has_tests_detects_pytest_dep(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pytest>=7"]\n'
    )
    assert tg.has_tests(tmp_path, {"python"}) is True


def test_has_tests_detects_jest_in_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"jest": "^29.0"}}'
    )
    assert tg.has_tests(tmp_path, {"js"}) is True


def test_has_tests_returns_false_for_empty_pj(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    assert tg.has_tests(tmp_path, {"python"}) is False


def test_has_tests_detects_test_files(tmp_path):
    (tmp_path / "test_foo.py").write_text("def test_x(): pass\n")
    assert tg.has_tests(tmp_path, {"python"}) is True


def test_collect_rows_needs_attention(tmp_path):
    pj = tmp_path / "fooapp"
    pj.mkdir()
    (pj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["anthropic", "pytest"]\n'
    )
    rows = tg.collect_test_guard_rows([pj])
    assert len(rows) == 1
    r = rows[0]
    assert r.uses_llm is True
    assert r.has_tests is True
    assert r.has_pytest_no_llm is False
    assert r.has_precommit_hook is False
    assert r.needs_attention is True
    assert r.preventive_candidate is False


def test_collect_rows_preventive_when_no_tests(tmp_path):
    pj = tmp_path / "no_tests"
    pj.mkdir()
    (pj / "pyproject.toml").write_text('[project]\ndependencies = ["anthropic"]\n')
    rows = tg.collect_test_guard_rows([pj])
    r = rows[0]
    assert r.uses_llm is True
    assert r.has_tests is False
    assert r.needs_attention is False
    assert r.preventive_candidate is True


def test_collect_rows_protected(tmp_path):
    pj = tmp_path / "protected"
    pj.mkdir()
    (pj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["anthropic", "pytest", "pytest-no-llm"]\n'
    )
    (pj / ".pre-commit-config.yaml").write_text("- id: no-llm-in-tests\n")
    rows = tg.collect_test_guard_rows([pj])
    assert rows[0].needs_attention is False
    assert rows[0].preventive_candidate is False


def test_collect_rows_no_llm_no_action(tmp_path):
    pj = tmp_path / "innocent"
    pj.mkdir()
    (pj / "requirements.txt").write_text("requests\n")
    rows = tg.collect_test_guard_rows([pj])
    assert rows[0].uses_llm is False
    assert rows[0].needs_attention is False


def test_format_table_contains_pj_name(tmp_path):
    pj = tmp_path / "myapp"
    pj.mkdir()
    (pj / "pyproject.toml").write_text(
        '[project]\ndependencies = ["anthropic", "pytest"]\n'
    )
    rows = tg.collect_test_guard_rows([pj])
    output = tg.format_test_guard_table(rows)
    assert "myapp" in output
    assert "install guard" in output


def test_format_table_shows_preventive(tmp_path):
    pj = tmp_path / "no_tests_pj"
    pj.mkdir()
    (pj / "pyproject.toml").write_text('[project]\ndependencies = ["anthropic"]\n')
    rows = tg.collect_test_guard_rows([pj])
    output = tg.format_test_guard_table(rows)
    assert "preventive" in output
