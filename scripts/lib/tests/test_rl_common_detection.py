"""rl_common.detection.strip_image_placeholders のテスト（#445）。

corrections ストア入力衛生: Claude Code CLI が画像添付時に text block へ自動挿入する
``[Image #N]`` 位置マーカーを strip する単一ソース関数。utterance_archive.extractor
（upstream）と corrections 書込パスの両方がこれを共有する。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from rl_common.detection import strip_image_placeholders  # noqa: E402


def test_strips_leading_marker_same_line():
    assert strip_image_placeholders("[Image #1] Codeタブってないよ") == "Codeタブってないよ"


def test_strips_leading_marker_newline_separated():
    assert strip_image_placeholders("[Image #1]\n\n本文") == "本文"


def test_strips_multi_digit_marker():
    # #12 のような複数桁の番号にも一致する。
    assert strip_image_placeholders("[Image #12] これも") == "これも"


def test_strips_multiple_markers():
    out = strip_image_placeholders("[Image #1] [Image #2] [Image #3] 本文")
    assert "[Image" not in out
    assert "本文" in out


def test_marker_only_returns_empty():
    assert strip_image_placeholders("[Image #1]") == ""
    assert strip_image_placeholders("[Image #1]\n\n") == ""


def test_does_not_mangle_similar_but_non_matching_text():
    # "#N]" 形式に一致しない文字列は誤って触らない（過剰マッチ防止）。
    text = "[Image processing failed] というエラーが出た"
    assert strip_image_placeholders(text) == text


def test_does_not_mangle_meta_reference_to_marker_without_number():
    # マーカーへの言及だが "#数字]" が無い（曖昧な参照）は触らない。
    text = "[Image で始まる行を除外して"
    assert strip_image_placeholders(text) == text


def test_empty_and_none_passthrough():
    assert strip_image_placeholders("") == ""
    assert strip_image_placeholders(None) is None
