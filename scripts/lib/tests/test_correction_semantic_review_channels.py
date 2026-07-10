"""correction_semantic.review_channels のテスト（#99 content-rich チャネル単一ソース）。

検証観点:
- REVIEW_CHANNELS = content-rich の 4 チャネル（llm_judge / rephrase / permission_deny /
  verbosity・#171）。
- CONTENT_POOR_CHANNELS（esc_interrupt / manual_edit_after_ai）は REVIEW から除外。
- signal_text が channel 別に actionable テキストを返す:
  - llm_judge / rephrase: provenance.text を user 発話のみ抽出
  - permission_deny: tool_name + tool_input_summary（拒否コマンド）を合成
  - verbosity: provenance.note + patterns を合成（#171）
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
        {"llm_judge", "rephrase", "permission_deny", "verbosity"}
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
    assert rc.is_review_channel("verbosity") is True
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
    rec = {"channel": "totally_unknown_channel", "provenance": {}}
    assert rc.signal_text(rec) == ""


# ─────────────────────────────────────────────────────────────────
# signal_text / grouping_keywords（channel=verbosity・#171）
# provenance shape は verbosity/judge.py._emit_weak_signals 準拠:
# {"hash", "project", "patterns": [...], "note": str, "char_len": int}
# ─────────────────────────────────────────────────────────────────


def test_signal_text_verbosity_uses_note_and_patterns():
    rec = {
        "channel": "verbosity",
        "provenance": {"patterns": ["preamble", "filler"], "note": "前置きが冗長"},
    }
    out = rc.signal_text(rec)
    assert "前置きが冗長" in out
    assert "preamble" in out
    assert "filler" in out


def test_signal_text_verbosity_note_only():
    rec = {"channel": "verbosity", "provenance": {"patterns": [], "note": "水増しが多い"}}
    out = rc.signal_text(rec)
    assert "水増しが多い" in out


def test_signal_text_verbosity_patterns_only():
    rec = {"channel": "verbosity", "provenance": {"patterns": ["meta"], "note": ""}}
    out = rc.signal_text(rec)
    assert "meta" in out


def test_signal_text_verbosity_no_note_no_patterns_nonempty():
    # #99 と同じ注意点: message=channel 名の空 correction を防ぐ。note/patterns が
    # どちらも無くても signal_text は非空を返す（fallback しても channel 名の空にならない）。
    rec = {"channel": "verbosity", "provenance": {}}
    out = rc.signal_text(rec)
    assert out != ""


def test_grouping_keywords_verbosity_uses_pattern_names():
    rec = {
        "channel": "verbosity",
        "provenance": {"patterns": ["preamble", "filler"], "note": "前置きが冗長"},
    }
    kws = rc.grouping_keywords(rec)
    assert {"preamble", "filler"} <= kws


def test_grouping_keywords_verbosity_distinct_patterns_separate():
    a = {"channel": "verbosity", "provenance": {"patterns": ["preamble"], "note": "x"}}
    b = {"channel": "verbosity", "provenance": {"patterns": ["repetition"], "note": "y"}}
    ka = rc.grouping_keywords(a)
    kb = rc.grouping_keywords(b)
    union = len(ka | kb)
    assert union and len(ka & kb) / union < 0.5


def test_grouping_keywords_verbosity_identical_patterns_merge():
    a = {"channel": "verbosity", "provenance": {"patterns": ["preamble", "filler"], "note": "x"}}
    b = {"channel": "verbosity", "provenance": {"patterns": ["filler", "preamble"], "note": "y"}}
    assert rc.grouping_keywords(a) == rc.grouping_keywords(b)


# ─────────────────────────────────────────────────────────────────
# grouping_keywords（#99 F1: permission_deny の group collapse 解消）
# ─────────────────────────────────────────────────────────────────


def test_grouping_keywords_permission_deny_uses_command_tokens():
    rec = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": "git push --force"},
    }
    kws = rc.grouping_keywords(rec)
    # 固定 head「実行/拒否」でなく拒否コマンドの latin トークンで group 化する。
    assert {"git", "push", "force"} <= kws
    assert "実行" not in kws and "拒否" not in kws


def test_grouping_keywords_permission_deny_distinct_commands_separate():
    # #99 F1: 異なる拒否コマンドは共通トークン（bash）だけ共有し jaccard<0.5 で別 group。
    push = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": "git push --force"},
    }
    rm = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": "rm -rf /tmp/x"},
    }
    kp = rc.grouping_keywords(push)
    kr = rc.grouping_keywords(rm)
    union = len(kp | kr)
    assert union and len(kp & kr) / union < 0.5


def test_grouping_keywords_permission_deny_identical_commands_merge():
    a = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": "git push --force"},
    }
    b = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": "git push --force"},
    }
    assert rc.grouping_keywords(a) == rc.grouping_keywords(b)
    assert rc.grouping_keywords(a)  # 非空


def test_grouping_keywords_llm_judge_unchanged():
    # llm_judge / rephrase は従来どおり signal_text の漢字/カタカナ keyword（挙動不変）。
    from correction_semantic.bootstrap_backlog import extract_keywords

    rec = {"channel": "llm_judge", "provenance": {"text": "金額がきれてる"}}
    assert rc.grouping_keywords(rec) == extract_keywords(rc.signal_text(rec))


# ─────────────────────────────────────────────────────────────────
# grouping_keywords パス除外 / signal_text 単語境界
# （#99 F1 follow・実 dogfood で over-merge / 途中切れ発見）
# ─────────────────────────────────────────────────────────────────


def test_grouping_keywords_permission_deny_strips_path_tokens():
    # 実 dogfood: 絶対パスの segment（作業ディレクトリ）が grouping を支配し、別コマンドが
    # 同一 group に collapse していた。パス様トークン（'/' を含む語）は group 化から除く。
    rec = {
        "channel": "permission_deny",
        "provenance": {
            "tool_name": "Bash",
            "tool_input_summary": "cd /Users/foo/updater/docs-platform-drift && git push --force",
        },
    }
    kws = rc.grouping_keywords(rec)
    assert {"git", "push", "force"} <= kws  # コマンド動詞は残る
    for seg in ("users", "foo", "updater", "docs", "platform", "drift"):
        assert seg not in kws  # パス segment は group 化から除外


def test_grouping_keywords_permission_deny_same_dir_distinct_verbs_separate():
    # 同一作業ディレクトリでも別コマンド（push vs checkout/pull）は jaccard<0.5 で別 group。
    # パストークン支配だと同 dir の別コマンドが collapse する（実 dogfood で発見）。
    # fixture は実データ忠実（長い作業パス + tail パイプ）。短いパスだと共通トークンが
    # 少なく偽陰性（修正前でも分離）になるため＝synthetic fixture false confidence の罠。
    base = "cd /Users/matsukaze-takashi/updater/docs-platform-drift-semantic && "
    push = {
        "channel": "permission_deny",
        "provenance": {
            "tool_name": "Bash",
            "tool_input_summary": base + "git push --force-with-lease 2>&1 | tail -3",
        },
    }
    checkout = {
        "channel": "permission_deny",
        "provenance": {
            "tool_name": "Bash",
            "tool_input_summary": base
            + "git checkout main 2>&1 | tail -3 && git pull origin main --ff-only",
        },
    }
    kp = rc.grouping_keywords(push)
    kc = rc.grouping_keywords(checkout)
    union = len(kp | kc)
    assert union and len(kp & kc) / union < 0.5


def test_signal_text_permission_deny_truncates_at_word_boundary():
    # 120字超の長い拒否コマンドが単語途中で切れず省略記号で終わる（representative の判読性）。
    long_cmd = (
        "git checkout main && git pull origin main --ff-only && "
        "git log --oneline --decorate --graph --all "
        "--pretty=format:%h%d%s --author=matsukaze-takashi --since=2.weeks.ago"
    )
    assert len(long_cmd) > 120  # 切り詰めが発動する長さであることを保証
    rec = {
        "channel": "permission_deny",
        "provenance": {"tool_name": "Bash", "tool_input_summary": long_cmd},
    }
    out = rc.signal_text(rec)
    assert out.endswith("…")
    body = out[:-1].split(": ", 1)[1].rstrip()  # "Bash の実行を拒否: <body>"
    words = long_cmd.split()
    # body は元コマンドの単語 prefix（途中で切れた語の残骸がない）。
    assert body.split() == words[: len(body.split())]
