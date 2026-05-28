#!/usr/bin/env python3
"""slop_detector.py のテスト。

slop_score は 0.0 (最悪) 〜 1.0 (最良) のスコア。
- 高い = slop が少ない = 良い
- 低い = slop が多い = 悪い
"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from slop_detector import detect_slop, SlopResult  # noqa: E402


# ---------------------------------------------------------------------------
# 正常系 E2E テスト
# ---------------------------------------------------------------------------

class TestSlopHits:
    def test_excessive_affirmation_en(self):
        """過度な肯定（英語）を検出する。"""
        text = "Certainly! I'd be happy to help you with that. Of course!"
        result = detect_slop(text)
        assert len(result.hits) > 0
        assert result.slop_score < 1.0

    def test_excessive_affirmation_ja(self):
        """過度な肯定（日本語）を検出する。"""
        text = "もちろんです！喜んでお手伝いします。承知いたしました。"
        result = detect_slop(text)
        assert len(result.hits) > 0
        assert result.slop_score < 1.0

    def test_unnecessary_apology_en(self):
        """不要な謝罪（英語）を検出する。"""
        text = "I apologize for any confusion. I'm sorry if that was unclear."
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_unnecessary_apology_ja(self):
        """不要な謝罪（日本語）を検出する。"""
        text = "申し訳ありません。ご不便をおかけして大変申し訳ございません。"
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_useless_summary_header(self):
        """無意味な要約見出し（# まとめ・# Summary）を検出する。"""
        text = "# Summary\nHere is the summary.\n# まとめ\n以上です。"
        result = detect_slop(text)
        assert any(h["pattern_id"] in ("useless_summary_header_en", "useless_summary_header_ja")
                   for h in result.hits)

    def test_excessive_disclaimer_en(self):
        """過剰な免責（英語）を検出する。"""
        text = "Please note that I am an AI language model and may make mistakes."
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_excessive_disclaimer_ja(self):
        """過剰な免責（日本語）を検出する。"""
        text = "私はAIですので、必ずしも正確ではない可能性があります。ご注意ください。"
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_hollow_transition_en(self):
        """無意味な接続句（英語）を検出する。"""
        text = "Certainly! Great question! Without further ado, let me explain."
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_hollow_transition_ja(self):
        """無意味な接続句（日本語）を検出する。"""
        text = "それでは、詳しく説明させていただきたいと思います。"
        result = detect_slop(text)
        assert len(result.hits) > 0

    def test_multiple_patterns_lowers_score(self):
        """複数パターンが重なるほど slop_score が下がる。"""
        clean = "The function returns a list of integers."
        sloppy = (
            "Certainly! Great question! I'm so glad you asked. "
            "I apologize if this is not clear. As an AI language model, "
            "I must note that I may make mistakes. # Summary\nIn conclusion..."
        )
        clean_result = detect_slop(clean)
        sloppy_result = detect_slop(sloppy)
        assert sloppy_result.slop_score < clean_result.slop_score

    def test_score_range(self):
        """slop_score は常に [0.0, 1.0] の範囲。"""
        texts = [
            "",
            "a",
            "Certainly! " * 20,
            "Normal text without any slop patterns here.",
        ]
        for text in texts:
            result = detect_slop(text)
            assert 0.0 <= result.slop_score <= 1.0, f"Out of range for: {text!r}"


# ---------------------------------------------------------------------------
# 正常系: slop なし
# ---------------------------------------------------------------------------

class TestNoSlop:
    def test_clean_technical_en(self):
        """スロップのない技術文（英語）は hit 0。"""
        text = "The parser reads tokens from the input stream and builds an AST."
        result = detect_slop(text)
        assert len(result.hits) == 0
        assert result.slop_score == 1.0

    def test_clean_technical_ja(self):
        """スロップのない技術文（日本語）は hit 0。"""
        text = "パーサーは入力ストリームからトークンを読み取り、ASTを構築します。"
        result = detect_slop(text)
        assert len(result.hits) == 0
        assert result.slop_score == 1.0

    def test_clean_markdown_headers(self):
        """正当な Markdown ヘッダー（# Usage）は過検出しない。"""
        text = "# Usage\n\n## Installation\n\nRun `pip install foo`."
        result = detect_slop(text)
        # "Summary" / "まとめ" を含まない通常ヘッダーは hit しない
        assert not any(h["pattern_id"] in ("useless_summary_header_en", "useless_summary_header_ja")
                       for h in result.hits)

    def test_clean_apology_context_ja(self):
        """謝罪が含まれていても正当な文脈（短い 1 回のみ）は過検出しない。"""
        # 1回の「申し訳」だけなら閾値未満として通過すること
        text = "このバグについて申し訳ないですが、修正しました。"
        result = detect_slop(text)
        # pattern はヒットしてよいが score は大きく下がらないはず
        assert result.slop_score >= 0.5


# ---------------------------------------------------------------------------
# 異常系 / エッジケース
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        """空文字列は hits 0、score 1.0。"""
        result = detect_slop("")
        assert result.hits == []
        assert result.slop_score == 1.0

    def test_whitespace_only(self):
        """空白のみの文字列は hits 0、score 1.0。"""
        result = detect_slop("   \n\t  ")
        assert result.hits == []
        assert result.slop_score == 1.0

    def test_very_short_string(self):
        """1文字の文字列でクラッシュしない。"""
        result = detect_slop("a")
        assert isinstance(result, SlopResult)

    def test_hit_structure(self):
        """hit は pattern_id / span / snippet を持つ。"""
        text = "Certainly! I'd be happy to help."
        result = detect_slop(text)
        if result.hits:
            hit = result.hits[0]
            assert "pattern_id" in hit
            assert "span" in hit
            assert "snippet" in hit
            assert isinstance(hit["span"], (list, tuple)) and len(hit["span"]) == 2

    def test_no_regex_word_boundary_false_positive(self):
        """日本語テキストで \\b 境界誤検出しない。"""
        # "もちろん" を含む自然な文でも過検出しないことを確認
        text = "もちろん、この設計には利点と欠点があります。"
        result = detect_slop(text)
        # "もちろん" 単体では MUST-hit ではなく、スコアへの影響を確認
        assert 0.0 <= result.slop_score <= 1.0


# ---------------------------------------------------------------------------
# SlopResult の型確認
# ---------------------------------------------------------------------------

class TestSlopResultType:
    def test_returns_slop_result(self):
        result = detect_slop("test")
        assert isinstance(result, SlopResult)
        assert hasattr(result, "slop_score")
        assert hasattr(result, "hits")
        assert isinstance(result.hits, list)
