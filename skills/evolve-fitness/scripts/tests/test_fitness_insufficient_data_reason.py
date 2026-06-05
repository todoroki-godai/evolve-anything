"""#354⑦: fitness_evolution の insufficient_data 時に理由説明を付記するテスト。

skill_evolve 提案（skill diff 以外）は採点対象外であるため、
スキル中心 PJ では accept/reject 母集団が構造的に貯まりにくい。
insufficient_data の出力に、この理由を示す説明文が含まれることを検証する。

テスト方針:
  - 母集団不足（0件・BOOTSTRAP_MIN未満）→ insufficient_data の message に説明文が含まれる
  - 説明文は「skill_evolve 提案は採点対象外」「母集団が貯まりにくい」の趣旨を含む
  - 十分な母集団（MIN_DATA_COUNT 以上）→ ready になり、説明文は不要
  - BOOTSTRAP_MIN <= data < MIN_DATA_COUNT → bootstrap になり、説明文は不要
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve-fitness" / "scripts"))

import fitness_evolution as fe


GOOD_SKILL = """---
name: my-skill
description: Use this skill to create and build a foo. Trigger when user says foo.
---

This skill builds foo objects from bar inputs.
"""


class TestInsufficientDataReasonMessage:
    """insufficient_data 時のメッセージに説明文が含まれること。"""

    def test_zero_data_includes_skill_evolve_explanation(self):
        """データ 0 件 → skill_evolve 提案は採点対象外という説明が含まれる。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"

        msg = result["message"]
        # 「skill_evolve」または「採点対象外」のキーワードが含まれる
        has_skill_evolve = "skill_evolve" in msg
        has_not_scored = "採点対象外" in msg
        assert has_skill_evolve or has_not_scored, (
            f"insufficient_data のメッセージに説明文がない: {msg!r}\n"
            "期待: 'skill_evolve' または '採点対象外' を含む文字列"
        )

    def test_zero_data_includes_accumulation_difficulty_note(self):
        """データ 0 件 → 母集団が貯まりにくい旨の説明が含まれる。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"

        msg = result["message"]
        # 「貯まりにくい」「蓄積」「母集団」のいずれかが含まれる
        has_difficulty = any(kw in msg for kw in ("貯まりにくい", "蓄積", "母集団"))
        assert has_difficulty, (
            f"insufficient_data のメッセージに母集団蓄積の難しさ説明がない: {msg!r}"
        )

    def test_below_bootstrap_min_includes_explanation(self):
        """BOOTSTRAP_MIN - 1 件（= 4件）でも説明が含まれる。"""
        history = [
            {"best_fitness": 0.5, "human_accepted": True, "fitness_func": "skill_quality"}
            for _ in range(4)
        ]
        result = fe.run_fitness_evolution(history=history)
        assert result["status"] == "insufficient_data"

        msg = result["message"]
        has_skill_evolve = "skill_evolve" in msg
        has_not_scored = "採点対象外" in msg
        assert has_skill_evolve or has_not_scored, (
            f"insufficient_data のメッセージに説明文がない: {msg!r}"
        )

    def test_skill_evolve_source_not_counted_explanation_present(self):
        """skill_evolve ソースのレコードしかない場合も insufficient_data になり説明文が出る。

        skill_evolve 提案（structure fix / rule/hook candidate 等）は採点対象外のため、
        それらだけでは decisions に数えられず insufficient_data になる。
        """
        # human_accepted が None のレコードは decisions に入らない
        history = [
            {"best_fitness": 0.5, "human_accepted": None, "fitness_func": "skill_quality",
             "source": "skill_evolve"}
            for _ in range(10)
        ]
        result = fe.run_fitness_evolution(history=history)
        assert result["status"] == "insufficient_data"

        msg = result["message"]
        has_skill_evolve = "skill_evolve" in msg
        has_not_scored = "採点対象外" in msg
        assert has_skill_evolve or has_not_scored, (
            f"insufficient_data のメッセージに説明文がない: {msg!r}"
        )

    def test_has_structural_reason_field(self):
        """insufficient_data のレスポンスに structural_reason フィールドが追加されている。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"
        assert "structural_reason" in result, (
            "insufficient_data レスポンスに structural_reason フィールドがない"
        )

    def test_structural_reason_is_skill_evolve_only(self):
        """structural_reason の値が 'skill_evolve_not_scored' である。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["structural_reason"] == "skill_evolve_not_scored"

    def test_bootstrap_mode_no_structural_reason(self):
        """bootstrap モード（5-29件）では structural_reason は不要（フィールドなし）。"""
        history = [
            {"best_fitness": 0.5 + i * 0.01, "human_accepted": i % 2 == 0,
             "fitness_func": "skill_quality"}
            for i in range(10)
        ]
        result = fe.run_fitness_evolution(history=history)
        assert result["status"] == "bootstrap"
        # bootstrap では構造的説明は不要
        assert "structural_reason" not in result, (
            "bootstrap モードで structural_reason が付いている（不要）"
        )

    def test_ready_mode_no_structural_reason(self):
        """ready モード（30件以上）では structural_reason は不要（フィールドなし）。"""
        history = [
            {"best_fitness": 0.5 + i * 0.01, "human_accepted": i % 2 == 0,
             "fitness_func": "skill_quality"}
            for i in range(30)
        ]
        result = fe.run_fitness_evolution(history=history)
        assert result["status"] == "ready"
        assert "structural_reason" not in result, (
            "ready モードで structural_reason が付いている（不要）"
        )

    def test_insufficient_data_still_shows_count(self):
        """説明文追加後も、データ件数の表示（data_count, required）が維持される。"""
        result = fe.run_fitness_evolution(history=[])
        assert result["status"] == "insufficient_data"
        assert "data_count" in result
        assert "required" in result
        assert result["data_count"] == 0
        assert result["required"] == fe.MIN_DATA_COUNT


class TestInsufficientDataMessageForEvolveSkill:
    """SKILL.md Step 8 の表示テンプレートに対応した出力フォーマットテスト。"""

    def test_message_has_count_fraction(self):
        """N/30件のフォーマットが含まれる。"""
        result = fe.run_fitness_evolution(history=[])
        msg = result["message"]
        # "0/30" のようなカウント表示
        assert "/30" in msg or str(fe.MIN_DATA_COUNT) in msg, (
            f"insufficient_data メッセージにデータ件数表示がない: {msg!r}"
        )

    def test_message_describes_how_to_accumulate(self):
        """蓄積方法（bin/rl-optimize / rl-loop）または採点対象の案内が含まれる。"""
        result = fe.run_fitness_evolution(history=[])
        msg = result["message"]
        has_optimize = "optimize" in msg.lower()
        has_rl_loop = "rl-loop" in msg or "rl_loop" in msg
        has_evolve_diff = "evolve" in msg.lower()
        assert has_optimize or has_rl_loop or has_evolve_diff, (
            f"insufficient_data のメッセージに蓄積方法の案内がない: {msg!r}"
        )
