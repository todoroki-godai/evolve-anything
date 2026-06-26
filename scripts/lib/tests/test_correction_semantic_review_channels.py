"""correction_semantic.review_channels のテスト（#99 content-rich チャネル単一ソース）。

検証観点:
- REVIEW_CHANNELS = content-rich の 3 チャネル（llm_judge / rephrase / permission_deny）。
- CONTENT_POOR_CHANNELS（esc_interrupt / manual_edit_after_ai）は REVIEW から除外。
- signal_text が channel 別に actionable テキストを返す:
  - llm_judge / rephrase: provenance.text を user 発話のみ抽出
  - permission_deny: tool_name + tool_input_summary（拒否コマンド）を合成
  - content-poor: "" を返す
決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import review_channels as rc  # noqa: E402


def test_review_channels_are_content_rich():
    assert rc.REVIEW_CHANNELS == frozenset(
        {"llm_judge", "rephrase", "permission_deny"}
    )


def test_content_poor_disjoint_from_review():
    # content-poor は個別昇格に出さない（REVIEW と交わらない）。
    assert rc.CONTENT_POOR_CHANNELS == frozenset(
        {"esc_interrupt", "manual_edit_after_ai"}
    )
    assert rc.REVIEW_CHANNELS.isdisjoint(rc.CONTENT_POOR_CHANNELS)


def test_is_review_channel():
    assert rc.is_review_channel("llm_judge") is True
    assert rc.is_review_channel("rephrase") is True
    assert rc.is_review_channel("permission_deny") is True
    assert rc.is_review_channel("esc_interrupt") is False
    assert rc.is_review_channel("manual_edit_after_ai") is False
    assert rc.is_review_channel(None) is False


def test_signal_text_llm_judge_uses_user_text():
    rec = {"channel": "llm_judge", "provenance": {"text": "PRじゃないの？", "reason": "r"}}
    assert rc.signal_text(rec) == "PRじゃないの？"


def test_signal_text_llm_judge_strips_assistant_quote():
    # user_only_text 経由で assistant 引用ブロックを除去する（#528-3）。
    rec = {
        "channel": "llm_judge",
        "provenance": {"text": "> ℹ️ assistant 出力\n本当の指摘"},
    }
    assert rc.signal_text(rec) == "本当の指摘"


def test_signal_text_rephrase_uses_text():
    rec = {"channel": "rephrase", "provenance": {"text": "もう一度直して", "prev_text": "直して"}}
    assert rc.signal_text(rec) == "もう一度直して"


def test_signal_text_permission_deny_synthesizes_command():
    rec = {
        "channel": "permission_deny",
        "provenance": {
            "tool_name": "Bash",
            "tool_input_summary": "git push --force-with-lease",
            "denial_reason": "unknown",
        },
    }
    out = rc.signal_text(rec)
    assert "Bash" in out
    assert "拒否" in out
    assert "git push --force-with-lease" in out
    # denial_reason="unknown" はノイズなので添えない。
    assert "unknown" not in out


def test_signal_text_permission_deny_adds_meaningful_reason():
    rec = {
        "channel": "permission_deny",
        "provenance": {
            "tool_name": "Bash",
            "tool_input_summary": "rm -rf /",
            "denial_reason": "destructive_command",
        },
    }
    out = rc.signal_text(rec)
    assert "destructive_command" in out


def test_signal_text_permission_deny_no_tool():
    rec = {"channel": "permission_deny", "provenance": {}}
    out = rc.signal_text(rec)
    assert out == "ツール実行を拒否"


def test_signal_text_content_poor_returns_empty():
    for ch in ("esc_interrupt", "manual_edit_after_ai"):
        rec = {"channel": ch, "provenance": {"evidence": "x", "text": ""}}
        assert rc.signal_text(rec) == ""


def test_signal_text_unknown_channel_empty():
    rec = {"channel": "verbosity", "provenance": {}}
    assert rc.signal_text(rec) == ""
