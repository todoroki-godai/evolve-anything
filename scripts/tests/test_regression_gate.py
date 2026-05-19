"""regression_gate.py のユニットテスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from regression_gate import PreCheckResult, pre_check, check_gates, GateResult


# ---------------------------------------------------------------------------
# pre_check() tests
# ---------------------------------------------------------------------------


def test_pre_check_api_loss():
    """def foo があって candidate に消えた場合 → warnings に "API signature lost: foo"。"""
    original = "def foo(x):\n    return x\n"
    candidate = "def bar(x):\n    return x\n"
    result = pre_check(candidate, original)
    assert any("API signature lost: foo" in w for w in result.warnings)


def test_pre_check_line_explosion():
    """行数 2x 超 → warnings に "Line count explosion"。"""
    original = "line1\nline2\n"
    candidate = "\n".join([f"line{i}" for i in range(10)])  # 10 lines > 2 * 2
    result = pre_check(candidate, original)
    assert any("Line count explosion" in w for w in result.warnings)


def test_pre_check_frontmatter_deleted():
    """frontmatter 消失 → warnings に "Frontmatter deleted"。"""
    original = "---\ntitle: test\n---\ncontent"
    candidate = "content"
    result = pre_check(candidate, original)
    assert any("Frontmatter deleted" in w for w in result.warnings)


def test_pre_check_no_warning():
    """正常なケース → warnings == []。"""
    original = "def foo(x):\n    return x\n"
    candidate = "def foo(x):\n    return x + 1\n"
    result = pre_check(candidate, original)
    assert result.warnings == []


def test_pre_check_always_passes():
    """passed は常に True（warn-only）。"""
    # API loss
    original = "def foo():\n    pass\n"
    candidate = "completely different content"
    result = pre_check(candidate, original)
    assert result.passed is True

    # frontmatter deleted
    original2 = "---\ntitle: x\n---\nbody"
    candidate2 = "body"
    result2 = pre_check(candidate2, original2)
    assert result2.passed is True

    # line explosion
    original3 = "a\nb\n"
    candidate3 = "\n".join(["x"] * 20)
    result3 = pre_check(candidate3, original3)
    assert result3.passed is True


def test_pre_check_multiple_api_functions():
    """複数の def がある場合、消えた関数ごとに警告を返す。"""
    original = "def foo():\n    pass\ndef bar():\n    pass\n"
    candidate = "def baz():\n    pass\n"
    result = pre_check(candidate, original)
    warning_texts = " ".join(result.warnings)
    assert "foo" in warning_texts
    assert "bar" in warning_texts


def test_pre_check_api_present_no_warning():
    """関数名が candidate に存在する場合は警告なし。"""
    original = "def foo(x):\n    return x\n"
    candidate = "def foo(x, y=None):\n    return x\n"
    result = pre_check(candidate, original)
    assert not any("API signature lost" in w for w in result.warnings)


def test_pre_check_frontmatter_preserved_no_warning():
    """frontmatter が candidate にも存在する場合は警告なし。"""
    original = "---\ntitle: test\n---\ncontent"
    candidate = "---\ntitle: test v2\n---\nnew content"
    result = pre_check(candidate, original)
    assert not any("Frontmatter deleted" in w for w in result.warnings)


def test_pre_check_result_type():
    """戻り値が PreCheckResult dataclass であること。"""
    result = pre_check("hello", "hello")
    assert isinstance(result, PreCheckResult)
    assert isinstance(result.passed, bool)
    assert isinstance(result.warnings, list)


# ---------------------------------------------------------------------------
# 既存 check_gates() のスモークテスト（regression）
# ---------------------------------------------------------------------------


def test_check_gates_passes_basic():
    result = check_gates("hello world\n", max_lines=100)
    assert result.passed is True


def test_check_gates_fails_empty():
    result = check_gates("", max_lines=100)
    assert result.passed is False
    assert result.reason == "empty_content"


def test_check_gates_fails_line_limit():
    long_content = "\n".join(["x"] * 200)
    result = check_gates(long_content, max_lines=10)
    assert result.passed is False
    assert "line_limit_exceeded" in result.reason
