"""batch_guard のトークン見積もり（_estimate_skill_tokens）のテスト（#337）。

旧概算は固定 47,000 tokens/skill（全文×全スキル想定）で、実 Phase B プロンプトは
SKILL.md 先頭 2000字に truncate されるため実コストの約50倍に膨らんでいた。
truncate 後プロンプト長ベースの見積もりに是正したことを検証する。
"""
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_lib_dir))


def test_estimate_skill_tokens_is_truncation_bounded(tmp_path):
    """巨大 SKILL.md でも見積もりは truncate 上限で頭打ちになる（#337）。"""
    from skill_evolve.assessment import _estimate_skill_tokens, _TRUNCATE_CHARS

    big = tmp_path / "big" / "SKILL.md"
    big.parent.mkdir()
    big.write_text("x" * 50_000, encoding="utf-8")  # 巨大 SKILL.md

    est = _estimate_skill_tokens(big)
    # truncate 上限（2000字 + scaffold）由来の上界に収まり、47,000 には程遠い
    assert est < 2_000
    assert est < (_TRUNCATE_CHARS + 1_000)  # truncate が効いている


def test_estimate_skill_tokens_scales_with_small_skill(tmp_path):
    """小さな SKILL.md は truncate 上限より小さく見積もられる（#337）。"""
    from skill_evolve.assessment import _estimate_skill_tokens

    small = tmp_path / "small" / "SKILL.md"
    small.parent.mkdir()
    small.write_text("short skill", encoding="utf-8")

    est = _estimate_skill_tokens(small)
    assert est >= 1
    assert est < 500


def test_estimate_skill_tokens_missing_file_is_safe(tmp_path):
    """SKILL.md 不在でも例外でなく scaffold 分の最小見積もりを返す（#337）。"""
    from skill_evolve.assessment import _estimate_skill_tokens

    est = _estimate_skill_tokens(tmp_path / "nope" / "SKILL.md")
    assert est >= 1
