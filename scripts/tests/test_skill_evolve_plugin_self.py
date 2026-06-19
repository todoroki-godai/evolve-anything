"""#185: plugin_self スキルを skill_evolve_assessment の評価対象に含める。

プラグイン本体リポジトリ（`.claude-plugin/plugin.json` 存在）の repo 直下 skills/ は
origin=plugin_self に分類され、custom と同等に評価対象になる。インストール済み他プラグイン
（origin=plugin）は従来どおり除外し、その件数をサマリに surface する（Option B）。

LLM は呼ばない: compute_telemetry_scores / compute_llm_scores / is_self_evolved_skill /
load_user_config をすべて mock する。
"""
import sys
from pathlib import Path
from unittest import mock

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))

import skill_evolve.assessment as assessment_mod  # noqa: E402
from skill_evolve.assessment import skill_evolve_assessment  # noqa: E402


def _make_plugin_self_repo(tmp_path: Path, *skill_names: str) -> Path:
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"name": "evolve-anything"}', encoding="utf-8")
    for name in skill_names:
        skill_md = tmp_path / "skills" / name / "SKILL.md"
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        skill_md.write_text("---\nname: %s\n---\n# %s\n" % (name, name), encoding="utf-8")
    return tmp_path


def _tel(usage_count=5):
    return {
        "frequency": 2, "diversity": 2, "evaluability": 2,
        "usage_count": usage_count, "error_count": 1,
        "error_categories": ["x"],
    }


def _llm():
    return {"external_dependency": 2, "judgment_complexity": 2, "cached": True}


def _run(proj):
    """共通の mock セットで assessment を実行する。"""
    with mock.patch.object(assessment_mod, "load_user_config", lambda: {}), \
         mock.patch("skill_evolve.compute_telemetry_scores", lambda *a, **k: _tel()), \
         mock.patch("skill_evolve.compute_llm_scores", lambda *a, **k: _llm()), \
         mock.patch("skill_evolve.is_self_evolved_skill", return_value=False), \
         mock.patch("skill_evolve.is_verification_skill", return_value=False), \
         mock.patch("skill_evolve.classify_suitability", return_value="medium"):
        return skill_evolve_assessment(proj)


def test_plugin_self_skills_are_evaluated(tmp_path):
    """plugin_self スキルは評価結果に含まれる（custom 同等の対象化）。"""
    proj = _make_plugin_self_repo(tmp_path, "evolve", "reflect")
    results = _run(proj)
    evaluated = {r["skill_name"] for r in results if r.get("skill_name")}
    assert "evolve" in evaluated
    assert "reflect" in evaluated


def test_installed_plugin_skills_still_excluded_and_surfaced(tmp_path, monkeypatch):
    """origin=plugin（インストール済み他プラグイン）は除外し件数を surface する（Option B）。"""
    proj = _make_plugin_self_repo(tmp_path, "evolve")

    # 1件は plugin、もう1件は plugin_self に分類されるよう classify をスタブ。
    def _classify(p):
        if p.parent.name == "installed-other":
            return "plugin"
        return "plugin_self"

    # find_artifacts には plugin スキルも混ぜる
    real_skill = proj / "skills" / "evolve" / "SKILL.md"
    other_skill = proj / "skills" / "installed-other" / "SKILL.md"
    other_skill.parent.mkdir(parents=True, exist_ok=True)
    other_skill.write_text("# other\n", encoding="utf-8")

    monkeypatch.setattr(assessment_mod, "find_artifacts",
                        lambda proj_dir: {"skills": [real_skill, other_skill]})
    monkeypatch.setattr(assessment_mod, "classify_artifact_origin", _classify)

    with mock.patch.object(assessment_mod, "load_user_config", lambda: {}), \
         mock.patch("skill_evolve.compute_telemetry_scores", lambda *a, **k: _tel()), \
         mock.patch("skill_evolve.compute_llm_scores", lambda *a, **k: _llm()), \
         mock.patch("skill_evolve.is_self_evolved_skill", return_value=False), \
         mock.patch("skill_evolve.is_verification_skill", return_value=False), \
         mock.patch("skill_evolve.classify_suitability", return_value="medium"):
        results = skill_evolve_assessment(proj)

    evaluated = {r["skill_name"] for r in results if r.get("skill_name")}
    assert "evolve" in evaluated
    assert "installed-other" not in evaluated

    meta = [r for r in results if r.get("_meta") == "excluded_plugins"]
    assert meta, "excluded_plugins サマリが surface されるべき"
    assert meta[0]["excluded_plugin_count"] == 1
