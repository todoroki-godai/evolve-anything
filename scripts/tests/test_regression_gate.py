"""regression_gate.py のユニットテスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from regression_gate import PreCheckResult, pre_check, check_gates, GateResult, intention_check, IntentionCheckResult


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


# ---------------------------------------------------------------------------
# intention_check() tests
# ---------------------------------------------------------------------------


def test_intention_block_trigger_deletion():
    """Trigger 行を 30% 以上削除した場合 → severity="block"。"""
    original = "\n".join([
        "# My Skill",
        "Trigger: foo",
        "Trigger: bar",
        "Trigger: baz",
        "Trigger: qux",
        "Trigger: quux",
        "Some other content here",
    ])
    # Keep only 1 out of 5 Trigger lines (80% deleted → ≥30%)
    candidate = "\n".join([
        "# My Skill",
        "Trigger: foo",
        "Some other content here",
    ])
    result = intention_check(candidate, original)
    assert isinstance(result, IntentionCheckResult)
    assert result.severity == "block"
    assert "trigger" in result.reason.lower()


def test_intention_block_description_missing():
    """description: キーが original にあり candidate にない → severity="block"。"""
    original = "\n".join([
        "---",
        "name: my-skill",
        "description: |",
        "  Does something useful.",
        "---",
        "# Content",
    ])
    candidate = "\n".join([
        "---",
        "name: my-skill",
        "---",
        "# Content",
    ])
    result = intention_check(candidate, original)
    assert result.severity == "block"
    assert "description" in result.reason.lower()


def test_intention_block_disable_model_inv():
    """disable-model-invocation: true → false に変化した場合 → severity="block"。"""
    original = "\n".join([
        "---",
        "name: my-skill",
        "disable-model-invocation: true",
        "---",
        "# Content",
    ])
    candidate = "\n".join([
        "---",
        "name: my-skill",
        "disable-model-invocation: false",
        "---",
        "# Content",
    ])
    result = intention_check(candidate, original)
    assert result.severity == "block"
    assert "disable-model-invocation" in result.reason.lower()


def test_intention_block_dmi_key_deleted():
    """disable-model-invocation: true のキーを candidate から丸ごと削除した場合 → severity="block"。"""
    original = "\n".join([
        "---",
        "name: my-skill",
        "disable-model-invocation: true",
        "---",
        "# Content",
    ])
    candidate = "\n".join([
        "---",
        "name: my-skill",
        "---",
        "# Content",
    ])
    result = intention_check(candidate, original)
    assert result.severity == "block"
    assert "disable-model-invocation" in result.reason.lower()


def test_intention_warn_effort_change():
    """effort: low → effort: high に変化した場合 → severity="warn"。"""
    original = "\n".join([
        "---",
        "name: my-skill",
        "effort: low",
        "description: |",
        "  Does something useful.",
        "---",
        "# Content",
        "Trigger: foo",
        "Trigger: bar",
        "Trigger: baz",
    ])
    candidate = "\n".join([
        "---",
        "name: my-skill",
        "effort: high",
        "description: |",
        "  Does something useful.",
        "---",
        "# Content",
        "Trigger: foo",
        "Trigger: bar",
        "Trigger: baz",
    ])
    result = intention_check(candidate, original)
    assert result.severity == "warn"
    assert "effort" in result.reason.lower()


def test_intention_warn_jaccard_low():
    """ほぼ別のテキスト（Jaccard < 0.5）→ severity="warn"。"""
    original = "apple banana cherry date elderberry fig grape honeydew"
    candidate = "zebra yak xray wolf violet umbrella tangerine strawberry"
    result = intention_check(candidate, original)
    assert result.severity == "warn"
    assert "jaccard" in result.reason.lower()


def test_intention_ok():
    """変化なし → severity="ok"。"""
    text = "\n".join([
        "---",
        "name: my-skill",
        "effort: medium",
        "description: |",
        "  Does something useful.",
        "---",
        "# Content",
        "Trigger: foo",
        "## Usage",
        "Some usage text.",
    ])
    result = intention_check(text, text)
    assert result.severity == "ok"
