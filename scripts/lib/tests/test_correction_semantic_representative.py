"""correction_semantic.representative — representative 品質ヘルパのテスト（#528-3）。

bootstrap/daily の representative に assistant の過去レポート出力が引用ごと混入して判読
困難になる問題（「ℹ️ データ蓄積待ち…」がシグナル本文に混入）を、user 発話のみ抽出で解決する。
決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import representative as r  # noqa: E402


# ── user_only_text: assistant 引用ブロックを strip ────────────────────
def test_strips_markdown_quote_block():
    text = "やっぱり、高だけにして\n> ℹ️ データ蓄積待ち（PJ≥2 条件）\n> 6/24-7/8 に判断"
    assert r.user_only_text(text) == "やっぱり、高だけにして"


def test_strips_code_fence_block():
    text = "これ直して\n```\nℹ️ データ蓄積待ち\nfoo bar\n```"
    assert r.user_only_text(text) == "これ直して"


def test_strips_assistant_emoji_prefixed_lines():
    text = "ℹ️ データ蓄積待ち（ADR-046 昇格判断）\nこの行は残す"
    assert r.user_only_text(text) == "この行は残す"


def test_strips_status_glyph_lines():
    text = "✓ 完了\n✗ 失敗\nやり直して"
    assert r.user_only_text(text) == "やり直して"


def test_pure_user_text_unchanged():
    text = "四国めたんじゃなくてつむぎにして"
    assert r.user_only_text(text) == text


def test_all_assistant_falls_back_to_stripped_original():
    # 全行が assistant 引用なら空にせず元テキストを返す（情報喪失を防ぐ）
    text = "> ℹ️ データ蓄積待ち\n> 6/24-7/8"
    out = r.user_only_text(text)
    assert out  # 空でない
    assert "データ蓄積待ち" in out


def test_empty_or_none():
    assert r.user_only_text("") == ""
    assert r.user_only_text(None) == ""


# ── #445: Claude Code の tool 実行結果マーカー（⏺/⎿）を assistant 引用として扱う ──
def test_strips_bullet_marker_line():
    # ⏺（U+2B24）は Claude Code CLI の assistant メッセージ先頭マーカー。
    text = "⏺ Ran 3 stop hooks (ctrl+o to expand)\nこれ削除したい"
    assert r.user_only_text(text) == "これ削除したい"


def test_strips_corner_marker_line():
    # ⎿ は ⏺ に続く detail 行のマーカー。
    text = "⏺ 実行結果です\n  ⎿ 詳細行\nそれ違う"
    assert r.user_only_text(text) == "それ違う"


def test_mixed_assistant_quote_and_human_comment():
    # ⏺/⎿ で始まる行はそれぞれ strip される（行頭マーカーでの判定のため、マーカー行の
    # 折り返し継続行はこの実装では残る＝既存の quote-line 判定と同じ限界）。
    text = "⏺ 実行結果です\n⎿ 詳細行\nこれよく勝手に進んじゃうので、削除したい"
    out = r.user_only_text(text)
    assert out == "これよく勝手に進んじゃうので、削除したい"
    assert "⏺" not in out and "⎿" not in out


def test_mixed_assistant_quote_wrapped_continuation_line_survives():
    # #445 実コーパス実測（1件）: ⎿ 行の折り返し継続行はマーカーを持たないため、
    # 行単位 strip では残る（既知の限界。⏺/⎿ 自体の行と human の実指摘は正しく分離できる）。
    text = (
        "⏺ Ran 3 stop hooks (ctrl+o to expand)\n"
        "  ⎿ Stop hook error: 先送り表現を検出しました\n"
        "  を即座に起動して並行処理してください。\n\n"
        "これよく勝手に進んじゃうので、削除したい"
    )
    out = r.user_only_text(text)
    assert "⏺" not in out and "⎿" not in out
    assert "これよく勝手に進んじゃうので、削除したい" in out


# ── is_assistant_only_text: 全行が assistant 発話由来か（#445 書込ゲート用） ──
def test_is_assistant_only_text_true_when_all_lines_are_assistant_markers():
    text = "⏺ はい、両方とも main にマージ済みです。\n  ⎿ 詳細"
    assert r.is_assistant_only_text(text) is True


def test_is_assistant_only_text_false_when_human_line_present():
    text = "⏺ 実行結果です\nそれ違う"
    assert r.is_assistant_only_text(text) is False


def test_is_assistant_only_text_false_for_pure_human_text():
    assert r.is_assistant_only_text("四国めたんじゃなくてつむぎにして") is False


def test_is_assistant_only_text_false_for_empty():
    assert r.is_assistant_only_text("") is False
    assert r.is_assistant_only_text(None) is False


# ── prev_action_summary: 直前 AI 行動の 1 行要約 ──────────────────────
def test_prev_action_summary_passthrough():
    assert r.prev_action_summary("Edit foo.py") == "Edit foo.py"


def test_prev_action_summary_truncates():
    long = "x" * 200
    out = r.prev_action_summary(long)
    assert len(out) <= 120


def test_prev_action_summary_none():
    assert r.prev_action_summary(None) == ""
    assert r.prev_action_summary("") == ""


# ── trim_to_idiom_sentence: 複数トピック混在発言のトリム（#253） ──────────────
def test_trim_to_idiom_sentence_multi_topic_sentence_split():
    # 「。」区切りの複数トピックのうち idiom が含まれるセンテンスだけ残す。
    text = "字幕がずれてるので直して。あと、レポート表示の形式も変えてほしいんだけど"
    idiom = "字幕がずれてる"
    assert r.trim_to_idiom_sentence(text, idiom) == "字幕がずれてるので直して。"


def test_trim_to_idiom_sentence_multi_topic_comma_shift_split():
    # 句点が無くても「、あと」等の話題転換語で分割する。
    text = "字幕がずれてるから直して、あとレポート表示の形式も変えて"
    idiom = "字幕がずれてる"
    assert r.trim_to_idiom_sentence(text, idiom) == "字幕がずれてるから直して"


def test_trim_to_idiom_sentence_single_topic_unchanged():
    text = "字幕がずれてるので直して"
    assert r.trim_to_idiom_sentence(text, "字幕がずれてる") == text


def test_trim_to_idiom_sentence_no_idiom_returns_full_text():
    text = "字幕がずれてるので直して。あと、レポート表示の形式も変えてほしいんだけど"
    assert r.trim_to_idiom_sentence(text, None) == text
    assert r.trim_to_idiom_sentence(text, "") == text


def test_trim_to_idiom_sentence_idiom_not_found_returns_full_text():
    # idiom が本文どこにも見つからない（Haiku の言い換え等）→ 安全側で全文を返す。
    text = "字幕がずれてるので直して。あと、レポート表示の形式も変えてほしいんだけど"
    assert r.trim_to_idiom_sentence(text, "存在しない言い回し") == text


def test_trim_to_idiom_sentence_ambiguous_multi_match_returns_full_text():
    # idiom が複数トピックのセグメントに同時マッチ（曖昧）→ 安全側で全文を返す。
    text = "直してほしい。あとこれも直してほしいんだけど"
    assert r.trim_to_idiom_sentence(text, "直してほしい") == text


def test_trim_to_idiom_sentence_empty_text():
    assert r.trim_to_idiom_sentence("", "何か") == ""
    assert r.trim_to_idiom_sentence(None, "何か") == ""


# ── ROUND2 回帰: 話題転換語が無い単一トピックは句点/疑問符だけでは絶対にトリムしない ──
def test_trim_to_idiom_sentence_single_topic_multi_sentence_period_unchanged():
    # 「字幕がずれています。タイムコードを基準に直してください。」は句点区切りの
    # 2文だが単一トピック（指摘＋修正手段）。話題転換語が無いのでトリムしない。
    text = "字幕がずれています。タイムコードを基準に直してください。"
    assert r.trim_to_idiom_sentence(text, "字幕がずれています") == text


def test_trim_to_idiom_sentence_single_topic_question_mark_unchanged():
    # 疑問符区切りの複数文も話題転換語が無ければトリムしない。
    text = "字幕は本当にずれていますか？至急確認してください"
    assert r.trim_to_idiom_sentence(text, "ずれていますか") == text
