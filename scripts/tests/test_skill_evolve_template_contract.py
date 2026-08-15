#!/usr/bin/env python3
"""自己進化パターンひな型（self-evolve-sections.md）の契約テスト。

#468（spec-keeper 採用）で codex cold review が `マージ不可`（[Must]2件）を出し、是正した。
#471: 同じ欠陥が生成元テンプレートに残ったまま残り23スキルへ横展開されると欠陥が23倍になるため、
生成元 (`skills/evolve/templates/self-evolve-sections.md` + `skill_evolve.proposal`) を直す。

このテストは実テンプレートファイル + `evolve_skill_proposal()`（決定論フォールバック経路）の
生成物を対象にする（fixture 自作テンプレではなく実ファイルを読む — verify-data-contract）。
"""
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_PATH = _PLUGIN_ROOT / "skills" / "evolve" / "templates" / "self-evolve-sections.md"


def _template_text() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


# --- [Must]1: 空 pitfalls.md の無条件読込が復活しない ---


def test_preflight_has_deterministic_gate():
    """Pre-flight Check は grep カウントによる決定論ゲートを持つ（無条件読込ではない）。"""
    text = _template_text()
    assert "grep -c '^### '" in text, "決定論的ゲート（grep -c '^### '）が無い"
    assert "0" in text and "スキップ" in text, "0件時にスキップする記述が無い"


def test_preflight_no_unconditional_read_instruction():
    """欠陥のあった無条件読込の指示文言（ゲート無しでの読込指示）が復活していない。"""
    text = _template_text()
    # #468 是正前の欠陥文言そのもの
    assert "**実行前に `references/pitfalls.md` を読み、" not in text


# --- [Must]2: pitfalls.md への直接書込み指示が復活しない（pitfall-curate 委譲 + 人間承認 MUST）---


def test_failure_triggered_learning_delegates_to_pitfall_curate():
    text = _template_text()
    assert "を手で編集しない" in text, "直接編集しない旨の明記が無い"
    assert "pitfall-curate" in text, "pitfall-curate への委譲記述が無い"


def test_failure_triggered_learning_requires_human_approval():
    text = _template_text()
    assert "人間承認" in text or "承認を得て" in text, "書込み前の人間承認 MUST が無い"


# --- [Should]4: ライフサイクル契約（保存先 / Pruned 閾値 / 実行主体）---


def test_lifecycle_has_storage_destination():
    text = _template_text()
    assert "保存先" in text, "New/Pruned 等の保存先が明記されていない"


def test_lifecycle_has_pruned_threshold():
    text = _template_text()
    assert "Avoidance-count が **5**" in text, "Pruned 閾値（Avoidance-count 5）が明記されていない"


def test_lifecycle_has_execution_owner():
    text = _template_text()
    assert "遷移を実行する主体は pitfall-curate" in text, "遷移の実行主体が明記されていない"


# --- [Nit]: 生成物末尾に余分な空行が付かない ---


def test_template_file_has_no_trailing_blank_line():
    raw = _TEMPLATE_PATH.read_bytes()
    assert not raw.endswith(b"\n\n"), "テンプレート末尾に余分な空行がある"
    assert raw.endswith(b"\n"), "テンプレート末尾に改行が無い"


# --- テンプレートはスキル名を焼き込まない（骨格の再利用性）---


def test_template_does_not_hardcode_spec_keeper():
    """テンプレートは全スキル共通の骨格。spec-keeper 固有の記述を焼き込まない。"""
    text = _template_text()
    assert "spec-keeper" not in text


def test_template_has_skill_name_placeholder():
    """スキル名はプレースホルダで持ち、生成時に差し替える。"""
    text = _template_text()
    assert "{{SKILL_NAME}}" in text


# --- 生成時にスキル名プレースホルダが実スキル名へ差し替わる（決定論フォールバック経路）---


def test_evolve_skill_proposal_substitutes_skill_name(tmp_path):
    """evolve_skill_proposal() の生成物にプレースホルダが残らず実スキル名へ置換される。"""
    from skill_evolve import evolve_skill_proposal

    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")

    proposal = evolve_skill_proposal("demo-skill", skill_dir)

    assert proposal["error"] is None
    sections = proposal["sections_to_add"]
    assert "{{SKILL_NAME}}" not in sections, "プレースホルダが置換されず残っている"
    assert "demo-skill" in sections, "実スキル名に置換されていない"
