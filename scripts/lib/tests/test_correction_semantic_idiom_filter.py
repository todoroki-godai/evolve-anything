"""correction_semantic.idiom_filter — 過汎用 idiom FP guard のテスト（#527）。

過汎用な短文字 idiom が confirmed 化され idiom_autopromote（#463）の FP 製造機になるのを
防ぐ決定論ゲート。実測（correction_idioms.jsonl の confirmed 中の極短 idiom）に合わせて:
1. 最小長 floor（8 文字未満を弾く）
2. 日常語 stopword（相槌・推量・断片）を弾く
3. 日付・数値など文脈固有トークンを含む断片を弾く

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import idiom_filter as f  # noqa: E402


# ── 最小長 floor ───────────────────────────────────────────────────
def test_too_short_idiom_rejected():
    # 実測の極短 idiom（issue #527 のリスト）
    assert not f.idiom_eligible("いやいや")        # 4 文字
    assert not f.idiom_eligible("じゃなくて")      # 5 文字
    assert not f.idiom_eligible("気がする")        # 4 文字
    assert not f.idiom_eligible("比率だけ")        # 4 文字


def test_floor_is_eight_chars():
    # 7 文字は弾く、8 文字以上は floor を通る（他条件次第）
    assert not f.idiom_eligible("あいうえおかき")  # 7 文字
    assert f.idiom_eligible("つむぎにしてほしい")  # 9 文字・stopword でない


def test_whitespace_stripped_before_floor():
    # 周囲空白は長さに数えない
    assert not f.idiom_eligible("  気がする  ")


def test_empty_or_none_rejected():
    assert not f.idiom_eligible("")
    assert not f.idiom_eligible(None)
    assert not f.idiom_eligible("   ")


# ── 日常語 stopword ────────────────────────────────────────────────
def test_stopword_idioms_rejected_even_if_long_enough():
    # 8 文字以上でも相槌・推量表現は弾く
    assert not f.idiom_eligible("やっぱり気がするんだよなぁ")  # 推量
    assert not f.idiom_eligible("いやいやそうじゃないよ")      # 相槌+否定のみ


def test_substantive_correction_not_a_stopword():
    # 具体的な修正内容を含む長文は通る
    assert f.idiom_eligible("四国めたんじゃなくてつむぎにして")
    assert f.idiom_eligible("P6のデザインが違うんだけど")


# ── 文脈固有トークン（日付・数値断片）────────────────────────────────
def test_date_fragment_rejected():
    assert not f.idiom_eligible("いや、2/24の")
    assert not f.idiom_eligible("2026/06/15のやつ")


def test_number_fragment_rejected():
    # 数値主体の断片（「比率だけ 80%」のような文脈固有）
    assert not f.idiom_eligible("80%にして")
    assert not f.idiom_eligible("3番目のやつ")


def test_substantive_idiom_with_no_context_token_passes():
    assert f.idiom_eligible("つむぎにしてほしいんだけど")


# ── 最大長 ceiling（過特化＝メッセージ全文を弾く・#22）──────────────────
def test_overlong_idiom_rejected():
    # #22: 冗長な（複数行・自然文）暗黙修正シグナルは confirmable_idiom に全文が入る。
    # 全文一致は実運用で二度と再発しないため standing auto-promote rule にしない。
    long_text = "あ" * (f.MAX_IDIOM_CHARS + 1)
    assert not f.idiom_eligible(long_text)


def test_at_ceiling_idiom_passes():
    # ちょうど上限の長さは通る（境界・他条件次第）。
    at_ceiling = "つ" * f.MAX_IDIOM_CHARS
    assert f.idiom_eligible(at_ceiling)


def test_multiline_fulltext_rejected():
    # 複数行の自然文（メッセージ全文）は ceiling 超で弾く。
    full = (
        "さっきのフッターのデザインなんだけど、やっぱり余白が広すぎる気がするので"
        "もう少し詰めて、あと色も少し濃くしてほしい。あと見出しのサイズも調整して、"
        "ボタンの角丸ももう少し小さくして、リンクの色は青系に統一してほしい。"
    )
    assert len(full) > f.MAX_IDIOM_CHARS
    assert not f.idiom_eligible(full)


def test_ceiling_constant_defined():
    # #22: 上限定数が定義され floor より大きい。
    assert isinstance(f.MAX_IDIOM_CHARS, int)
    assert f.MAX_IDIOM_CHARS > f.MIN_IDIOM_CHARS


def test_eligible_reason_too_long():
    long_text = "あ" * (f.MAX_IDIOM_CHARS + 1)
    assert f.eligible_reason(long_text) == "too_long"


# ── eligible_reason（surface 用の拒否理由）─────────────────────────
def test_eligible_reason_too_short():
    assert f.eligible_reason("気がする") == "too_short"


def test_eligible_reason_stopword():
    assert f.eligible_reason("やっぱり気がするんだよなぁ") == "stopword"


def test_eligible_reason_context_token():
    assert f.eligible_reason("いや、2/24の") == "context_token"


def test_eligible_reason_none_when_eligible():
    assert f.eligible_reason("四国めたんじゃなくてつむぎにして") is None


# ── filter_idioms（一括フィルタ helper）────────────────────────────
def test_filter_idioms_partitions():
    texts = ["気がする", "つむぎにしてほしいんだけど", "いや、2/24の"]
    kept, rejected = f.filter_idioms(texts)
    assert kept == ["つむぎにしてほしいんだけど"]
    assert ("気がする", "too_short") in rejected
    assert ("いや、2/24の", "context_token") in rejected
