"""skill_extractor → discover 配線のテスト（issue #291, SIRI ①）。

成功軌跡からのスキル採掘 (`extract_skill_candidates`) が run_discover に配線され、
- generalizability_score 閾値でフィルタされて `trajectory_skill_candidates` に surface
- triage の missed_skills 形式へ変換され `missed_skill_opportunities` に合流
することを検証する。

TDD-first: 配線実装前にテストを書いている。
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import discover  # noqa: E402
from discover.runner import (  # noqa: E402
    _existing_skill_names,
    _is_already_existing_skill,
    _trajectory_candidates_to_missed,
    _project_transcript_dir,
)


def _cand(skill_name, score, *, session_count=2, prompts=None):
    return {
        "skill_name": skill_name,
        "session_count": session_count,
        "generalizability_score": score,
        "success_rate": 1.0,
        "source": "codeskill_extraction",
        "sample_prompts": prompts or [],
    }


# ── 純粋ヘルパーのテスト ──────────────────────────────────


def test_filters_below_threshold():
    """generalizability_score が閾値未満の候補は surface も merge もされない。"""
    cands = [_cand("foo", 0.1), _cand("bar", 0.9)]
    surfaced, merged = _trajectory_candidates_to_missed(cands, threshold=0.3)
    assert [c["skill_name"] for c in surfaced] == ["bar"]
    assert [m["skill"] for m in merged] == ["bar"]


def test_converts_to_missed_skills_format():
    """surfaced 候補が triage 互換の missed_skills 形式へ変換される。"""
    cands = [_cand("foo", 0.8, session_count=5, prompts=["実装して"])]
    _, merged = _trajectory_candidates_to_missed(cands, threshold=0.3)
    entry = merged[0]
    # triage は `skill` / `session_count` を参照する
    assert entry["skill"] == "foo"
    assert entry["session_count"] == 5
    assert entry["triggers_matched"] == ["実装して"]
    assert entry["source"] == "codeskill_extraction"
    assert entry["generalizability_score"] == 0.8


def test_dedups_against_existing_missed():
    """既存 missed_skill と重複する候補は merge から除外（surface は残す）。"""
    cands = [_cand("foo", 0.8), _cand("bar", 0.8)]
    surfaced, merged = _trajectory_candidates_to_missed(
        cands, threshold=0.3, existing_skills={"foo"},
    )
    assert {c["skill_name"] for c in surfaced} == {"foo", "bar"}
    assert [m["skill"] for m in merged] == ["bar"]


# ── 既存スキル除外（プラグイン namespaced / known_skills）────


def test_plugin_namespaced_candidates_excluded():
    """`:` を含むプラグインスキル名は surface / merge の双方から除外される。"""
    cands = [_cand("rl-anything:evolve", 0.95), _cand("brandnew", 0.8)]
    surfaced, merged = _trajectory_candidates_to_missed(cands, threshold=0.3)
    assert [c["skill_name"] for c in surfaced] == ["brandnew"]
    assert [m["skill"] for m in merged] == ["brandnew"]


def test_known_existing_skills_excluded():
    """既存の project / global スキル名は CREATE 候補から除外される。"""
    cands = [_cand("review", 0.95), _cand("loop", 0.9), _cand("brandnew", 0.7)]
    surfaced, merged = _trajectory_candidates_to_missed(
        cands, threshold=0.3, known_skills={"review", "loop"},
    )
    assert [c["skill_name"] for c in surfaced] == ["brandnew"]
    assert [m["skill"] for m in merged] == ["brandnew"]


def test_cc_builtin_commands_excluded():
    """CC 組み込みコマンド（loop/model 等、SKILL.md 無し）も CREATE 候補から除外。"""
    cands = [_cand("loop", 0.95), _cand("model", 0.9), _cand("brandnew", 0.7)]
    surfaced, merged = _trajectory_candidates_to_missed(cands, threshold=0.3)
    assert [c["skill_name"] for c in surfaced] == ["brandnew"]
    assert [m["skill"] for m in merged] == ["brandnew"]


def test_is_already_existing_skill_predicate():
    assert _is_already_existing_skill("rl-anything:evolve", set()) is True
    assert _is_already_existing_skill("review", {"review"}) is True
    assert _is_already_existing_skill("loop", set()) is True  # CC builtin
    assert _is_already_existing_skill("model", set()) is True  # CC builtin
    assert _is_already_existing_skill("", set()) is True
    assert _is_already_existing_skill("brandnew", {"review"}) is False


def test_existing_skill_names_collects_project_and_global(tmp_path, monkeypatch):
    """project / global の .claude/skills/<name>/SKILL.md を集め、ドット dir は除外。"""
    proj = tmp_path / "proj"
    for name in ("docs-qa", ".gstack-backup"):
        d = proj / ".claude" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x", encoding="utf-8")
    fake_home = tmp_path / "home"
    gskill = fake_home / ".claude" / "skills" / "review"
    gskill.mkdir(parents=True)
    (gskill / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    names = _existing_skill_names(proj)
    assert "docs-qa" in names
    assert "review" in names
    assert ".gstack-backup" not in names


def test_empty_candidates():
    surfaced, merged = _trajectory_candidates_to_missed([], threshold=0.3)
    assert surfaced == []
    assert merged == []


# ── project スコープのエンコード契約 ──────────────────────


def test_project_transcript_dir_encoding():
    """CC のエンコード規則（`/` と `.` を `-` に置換）に一致する。"""
    p = Path("/Users/me/proj/.claude/worktrees/issue291")
    encoded = _project_transcript_dir(p)
    assert encoded.name == "-Users-me-proj--claude-worktrees-issue291"
    assert encoded.parent == Path.home() / ".claude" / "projects"


# ── run_discover 統合（extract_skill_candidates を mock）──────


def test_run_discover_surfaces_trajectory_candidates(tmp_path):
    """run_discover が trajectory 候補を report に surface し missed に合流させる。"""
    fake = [
        _cand("newskill", 0.7, session_count=4, prompts=["やって"]),
        _cand("weak", 0.05),  # 閾値未満 → 除外
    ]
    with mock.patch(
        "skill_extractor.extract_skill_candidates", return_value=fake,
    ):
        result = discover.run_discover(project_root=tmp_path)

    assert "trajectory_skill_candidates" in result
    surfaced_names = {c["skill_name"] for c in result["trajectory_skill_candidates"]}
    assert "newskill" in surfaced_names
    assert "weak" not in surfaced_names

    merged_names = {m["skill"] for m in result.get("missed_skill_opportunities", [])}
    assert "newskill" in merged_names


def test_run_discover_handles_extractor_error(tmp_path):
    """extract_skill_candidates が例外でも run_discover は落ちず error key を残す。"""
    with mock.patch(
        "skill_extractor.extract_skill_candidates",
        side_effect=RuntimeError("boom"),
    ):
        result = discover.run_discover(project_root=tmp_path)
    assert "trajectory_skill_candidates_error" in result
    assert "boom" in result["trajectory_skill_candidates_error"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
