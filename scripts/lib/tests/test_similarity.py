"""similarity.tokenize / jaccard_coefficient の日本語対応テスト（#447）。"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))

from similarity import jaccard_coefficient, tokenize  # noqa: E402


class TestTokenizeAsciiUnchanged:
    """日本語対応の副作用で既存の英数字トークン化が退行していないこと。"""

    def test_identifier_with_underscore_and_colon(self):
        # file_path:12 のような識別子は従来どおり単語単位で分割される
        assert tokenize("file_path:12") == {"file", "path", "12"}

    def test_camel_case_identifier_stays_one_token(self):
        # 区切り文字がない CamelCase は従来どおり1トークン（分割しない）
        assert tokenize("AskUserQuestion") == {"askuserquestion"}

    def test_double_dash_flag(self):
        assert tokenize("--dry-run") == {"dry", "run"}

    def test_whitespace_and_punctuation_split_english(self):
        assert tokenize("Hello, World! Hello again.") == {"hello", "world", "again"}


class TestTokenizeJapanese:
    """日本語（および CJK）文字列が1つの巨大トークンに潰れないこと。"""

    def test_japanese_sentence_splits_into_multiple_tokens(self):
        tokens = tokenize("日本語処理を改善する")
        # 従来は句読点・空白が無いため1トークンに潰れていた
        assert len(tokens) > 1

    def test_single_cjk_character_is_kept(self):
        # 1文字の CJK 文が空集合にならない
        assert tokenize("あ") == {"あ"}

    def test_cjk_run_produces_bigrams_not_single_chars(self):
        # 2文字以上の CJK run は隣接2文字の bigram のみを生成し、1文字トークンに
        # 過剰分割しない。1文字化すると「な」「を」等の汎用文字1つが一致するだけで
        # 無関係な文どうしが似ていると誤判定されてしまう（bigram なら語彙空間が
        # 大きく保たれ、偶然一致が起きにくい）。
        text = "日本語処理"
        tokens = tokenize(text)
        assert all(len(t) == 2 for t in tokens)
        assert len(tokens) == len(text) - 1

    def test_japanese_punctuation_acts_as_separator(self):
        # 読点・句点は区切りとして働き、文をまたいだ bigram を作らない
        tokens_a = tokenize("git diff で確認する。")
        tokens_b = tokenize("pytest で単体テストを書く。")
        # 「する」「。」のような文末表現だけで一致しない（文をまたいだ結合をしていないか）
        assert "する。pytest" not in tokens_a
        assert "する。pytest" not in tokens_b

    def test_mixed_ascii_and_japanese_boundary(self):
        tokens = tokenize("similarity.tokenizeが日本語を分割できない")
        # 英数字 run（region の "tokenize"）は従来どおり1トークンでまとまり、
        # 直後の CJK 文字（が）と結合しない
        assert "tokenize" in tokens
        assert "tokenizeが" not in tokens


class TestJaccardJapaneseCorrections:
    """実務上の correction 文で、似た文は高スコア・無関係な文は低スコアになること。

    句読点・空白を一切含まない連続した日本語文で検証する
    （句読点があると従来実装でも句単位には割れるため、issue が指摘する
    「1トークンに潰れる」最悪ケースを再現するには句読点なしの文が必要）。
    """

    JACCARD_THRESHOLD = 0.15  # episodic_store._MIN_SCORE と同一の実運用しきい値

    NEAR_DUP_A = "先送り表現を検出したので先送りせずに対応してください"
    NEAR_DUP_B = "先送り表現を検出したため先送りせず今すぐ対応する"
    UNRELATED = "単体テストではLLM呼び出しを必ずモックする"

    def test_near_duplicate_japanese_sentences_score_above_threshold(self):
        a = tokenize(self.NEAR_DUP_A)
        b = tokenize(self.NEAR_DUP_B)
        score = jaccard_coefficient(a, b)
        assert score >= self.JACCARD_THRESHOLD

    def test_unrelated_japanese_sentences_score_below_threshold(self):
        # 無関係な文どうしは、bigram にしても閾値を超えて似ていると誤判定されない
        a = tokenize(self.NEAR_DUP_A)
        c = tokenize(self.UNRELATED)
        score = jaccard_coefficient(a, c)
        assert score < self.JACCARD_THRESHOLD

    def test_identical_sentence_scores_one(self):
        a = tokenize("git diff で確認する")
        b = tokenize("git diff で確認する")
        assert jaccard_coefficient(a, b) == 1.0
