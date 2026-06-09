"""constitutional レポート文言のテスト（#408-D）。

[ADR-037] で constitutional は LLM を全廃し cache 済みレイヤーのみ集約する設計。
None は「評価失敗」ではなく「cache 未生成 / 全 miss（stale）」を意味する。
旧文言「LLM 評価に失敗しました」は ADR-037 と矛盾するため撤去されたことを検証する。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit.sections import _format_constitutional_report  # noqa: E402


def test_none_is_not_reported_as_failure():
    """None は「失敗」と表示しない（ADR-037 LLM 全廃と矛盾するため撤去, #408-D）。"""
    lines = _format_constitutional_report(None)
    text = "\n".join(lines)
    assert "LLM 評価に失敗しました" not in text
    # cache stale / 全 miss の案内であること
    assert "cache" in text or "stale" in text
    assert "失敗ではありません" in text
    # refresh 手段（audit Step 3.5 の 2 相）への導線がある
    assert "refresh" in text or "再生成" in text


def test_skip_low_coverage_unchanged():
    """overall=None だが skip_reason 付き（low_coverage）は従来通り Skipped 表示。"""
    lines = _format_constitutional_report(
        {"overall": None, "skip_reason": "low_coverage", "coverage_value": 0.2}
    )
    text = "\n".join(lines)
    assert "Skipped" in text
    assert "low_coverage" in text


def test_scored_result_renders_overall():
    lines = _format_constitutional_report(
        {"overall": 0.83, "per_principle": [], "estimated_cost_usd": 0, "llm_calls_count": 0}
    )
    text = "\n".join(lines)
    assert "0.83" in text
