#!/usr/bin/env python3
"""skill_quality の red flags（危険サイン）節チェックのテスト（#62）。

`addyosmani/agent-skills` tech-eval で抽出した authoring パターン。各 SKILL.md が
「red flags / 危険サイン」節（このスキルをサボってはいけない危険サインの自己点検）を
持つことを品質要件にする。red flags 節があれば skill_quality overall に加点され、
その overall は environment fitness の skill_quality 軸に平均で流れる＝evolve ループに
自動で乗る。
"""
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir / "fitness"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from skill_quality import (  # noqa: E402
    RED_FLAGS_BONUS,
    check_red_flags_section,
    evaluate_skill_quality,
)


def _write_skill_md(tmp_path, *, name="test-skill", description="", body=""):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    lines = ["---", f"name: {name}"]
    if description:
        lines.append(f'description: "{description}"')
    lines.append("---")
    lines.append("")
    lines.append(body)
    skill_path.write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


# ─── check_red_flags_section ───────────────────────────────────

class TestCheckRedFlagsSection:
    def test_detects_english_heading(self):
        r = check_red_flags_section("## Red Flags\n\n- Skipping tests\n")
        assert r["present"] is True
        assert r["bonus"] == RED_FLAGS_BONUS
        assert r["note"] is None

    def test_detects_red_flag_singular(self):
        assert check_red_flags_section("### Red Flag\n\nwatch out\n")["present"] is True

    def test_detects_japanese_kikensign(self):
        assert check_red_flags_section("## 危険サイン\n\n- テストをサボる\n")["present"] is True

    def test_detects_japanese_redflag_katakana(self):
        assert check_red_flags_section("## レッドフラグ\n\n注意\n")["present"] is True

    def test_absent_returns_note_and_zero_bonus(self):
        r = check_red_flags_section("## Steps\n\nDo something.\n")
        assert r["present"] is False
        assert r["bonus"] == 0.0
        assert r["note"] is not None
        assert "red flags" in r["note"].lower() or "危険サイン" in r["note"]

    def test_empty_content(self):
        r = check_red_flags_section("")
        assert r["present"] is False

    def test_inline_mention_not_a_heading_is_not_counted(self):
        # 見出しでない本文中の "red flags" 言及は加点しない（自己点検節の有無を測る）
        r = check_red_flags_section("This skill has no red flags subsection in prose.\n")
        assert r["present"] is False


# ─── evaluate_skill_quality 統合: red flags でスコアが上がる ─────────

class TestEvaluateSkillQualityRedFlags:
    def test_red_flags_section_raises_overall(self, tmp_path):
        """同一 description で red flags 節ありの方が overall が高い（regression 固定）。"""
        desc = "Use this skill when refactoring legacy modules carefully and safely."
        with_rf = _write_skill_md(
            tmp_path / "a", name="with-rf", description=desc,
            body="## Steps\n\nDo it.\n\n## Red Flags\n\n- Skipping the regression gate\n",
        )
        without_rf = _write_skill_md(
            tmp_path / "b", name="without-rf", description=desc,
            body="## Steps\n\nDo it.\n",
        )
        content_with = (with_rf / "SKILL.md").read_text(encoding="utf-8")
        content_without = (without_rf / "SKILL.md").read_text(encoding="utf-8")
        res_with = evaluate_skill_quality(content_with, str(with_rf))
        res_without = evaluate_skill_quality(content_without, str(without_rf))
        assert res_with is not None and res_without is not None
        assert res_with["overall"] > res_without["overall"]
        assert res_with["overall"] - res_without["overall"] == pytest.approx(RED_FLAGS_BONUS, abs=1e-6)

    def test_result_includes_red_flags_subresult(self, tmp_path):
        skill_dir = _write_skill_md(
            tmp_path, name="rf", description="Use when auditing.",
            body="## 危険サイン\n\n- 手を抜く兆候\n",
        )
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        res = evaluate_skill_quality(content, str(skill_dir))
        assert "red_flags" in res
        assert res["red_flags"]["present"] is True

    def test_overall_stays_clamped_to_one(self, tmp_path):
        """bonus を足しても overall は 1.0 を超えない。"""
        skill_dir = _write_skill_md(
            tmp_path, name="rf2",
            description="Use this skill when X. Trigger: refactor. 使用タイミング: always.",
            body="## Red Flags\n\n- y\n",
        )
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        res = evaluate_skill_quality(content, str(skill_dir))
        assert res["overall"] <= 1.0
