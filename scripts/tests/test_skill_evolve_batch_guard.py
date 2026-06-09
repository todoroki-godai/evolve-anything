#!/usr/bin/env python3
"""skill_evolve の denylist / batch guard / judgment 2相（[ADR-037] Phase 1c）のテスト。

test_skill_evolve.py から分離。
- denylist（add/get/persist/remove）
- skill_evolve_assessment の batch_guard_trigger sentinel（10件超 / denied / skip_skills / confirmed_batch）
- _parse_judgment_response の信頼境界（int/str/dict 寛容）
- compute_llm_scores の LLM-free 静的フォールバック
- emit_judgment_requests / ingest_judgment_scores（ファイルベース2相）
batch トークン見積もり（_estimate_skill_tokens）は test_skill_evolve_batch_estimate.py。
"""
import json
import sys
from pathlib import Path
from unittest import mock

_lib_dir = Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib_dir))


# --- denylist ---


class TestDenylist:
    def _import(self, monkeypatch, tmp_path):
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        return dl_mod

    def test_denylist_load_empty(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        assert dl.get_denied_skill_names() == set()

    def test_denylist_add_and_get(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["skill-a", "skill-b"])
        denied = dl.get_denied_skill_names()
        assert "skill-a" in denied
        assert "skill-b" in denied

    def test_denylist_persist(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["persistent-skill"])
        data = dl.load_denylist()
        assert "persistent-skill" in data["skills"]
        assert "reason" in data["skills"]["persistent-skill"]
        assert "denied_at" in data["skills"]["persistent-skill"]

    def test_remove_from_denylist(self, monkeypatch, tmp_path):
        dl = self._import(monkeypatch, tmp_path)
        dl.add_to_denylist(["skill-x", "skill-y"])
        dl.remove_from_denylist(["skill-x"])
        denied = dl.get_denied_skill_names()
        assert "skill-x" not in denied
        assert "skill-y" in denied


# --- assessment batch guard ---


def _make_skill_path(tmp_path, name, subdir=".claude/skills"):
    skill_dir = tmp_path / subdir / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"# {name}\n")
    return skill_md


class TestBatchGuardAssessment:
    def test_batch_guard_returns_meta_when_over_limit(self, monkeypatch, tmp_path):
        """11件の custom スキルで batch_guard_trigger sentinel が返る。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        origins = ["custom"] * 11

        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        # #400 改善: guard は usage>0 のスキルのみ母集団に入れる。使用実績ありをモック。
        _tel = {"frequency": 1, "diversity": 1, "evaluability": 1,
                "error_count": 0, "usage_count": 1, "error_categories": {}}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=lambda p: origins[skill_paths.index(p)]), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=_tel), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None, "batch_guard_trigger sentinel が返されるべき"
        assert sentinel["total_effective"] == 11

    def test_batch_guard_groups_structure(self, monkeypatch, tmp_path):
        """sentinel の groups が origin 別に構造化される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        custom_paths = [_make_skill_path(tmp_path, f"custom-{i}") for i in range(8)]
        global_paths = [_make_skill_path(tmp_path, f"global-{i}", ".claude/skills") for i in range(4)]
        all_paths = custom_paths + global_paths

        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ",".join(f"global-{i}" for i in range(4))
        _tel = {"frequency": 1, "diversity": 1, "evaluability": 1,
                "error_count": 0, "usage_count": 1, "error_categories": {}}

        def origin_fn(p):
            name = p.parent.name
            if name.startswith("global-"):
                return "global"
            return "custom"

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": all_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=origin_fn), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=_tel), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None
        group_origins = {g["origin"] for g in sentinel["groups"]}
        assert "custom" in group_origins
        assert "global" in group_origins
        for g in sentinel["groups"]:
            assert "skills" in g
            assert "estimated_tokens" in g
            assert "skill_count" in g
            # truncate 後プロンプト長ベース（#337）。旧 47,000/skill の桁違い過大を解消。
            # 各スキルは truncate 上限（2000字 + scaffold）以下に収まる
            assert 0 < g["estimated_tokens"] < g["skill_count"] * 2_000

    def test_assessment_filters_denied(self, monkeypatch, tmp_path):
        """denylist にあるスキルは effective_targets から除外される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        dl_mod.add_to_denylist([f"skill-{i}" for i in range(5)])

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        # 5件 denied → effective 6件 → guard トリガーしない
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "denied 後は guard トリガーしないはず"

    def test_skip_skills_param(self, monkeypatch, tmp_path):
        """skip_skills を渡すと一時除外される。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        skip = {f"skill-{i}" for i in range(5)}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path, skip_skills=skip)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "skip_skills で effective が減り guard トリガーしないはず"

    def test_denied_reduces_effective_below_limit(self, monkeypatch, tmp_path):
        """denylist で effective が 10件以下になれば guard トリガーしない。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        dl_mod.add_to_denylist(["skill-10"])

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""

        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)

        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "1件 denied で effective=10 → guard トリガーしないはず"

    def test_confirmed_batch_bypasses_guard_in_assessment(self, monkeypatch, tmp_path):
        """confirmed_batch=True のとき assessment.py の guard 条件が実際にスキップされる。"""
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill_path(tmp_path, f"skill-{i}") for i in range(11)]
        origins = ["custom"] * 11
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        telemetry_ret = {
            "frequency": 1, "diversity": 1, "evaluability": 1,
            "error_count": 0, "usage_count": 1, "error_categories": {},
        }
        llm_ret = {"external_dependency": 1, "judgment_complexity": 1, "cached": False}

        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", side_effect=lambda p: origins[skill_paths.index(p)]), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path), \
             mock.patch("skill_evolve.compute_telemetry_scores", return_value=telemetry_ret), \
             mock.patch("skill_evolve.compute_llm_scores", return_value=llm_ret), \
             mock.patch("skill_evolve.is_self_evolved_skill", return_value=False):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path, confirmed_batch=True)

        # 11件あっても confirmed_batch=True なら sentinel が返らず通常評価が走る
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is None, "confirmed_batch=True では guard をスキップすべき"
        non_meta = [r for r in result if not r.get("_meta")]
        assert len(non_meta) == 11, "全 11 件が評価対象になるべき"


# ============================================================
# [ADR-037] Phase 1c: claude -p 全廃 — ファイルベース2相テスト
# ============================================================


# --- _parse_judgment_response の信頼境界（int/str/dict 寛容） ---


def test_parse_judgment_response_int():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response(2) == 2


def test_parse_judgment_response_str():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response("評価: 3 です") == 3


def test_parse_judgment_response_dict():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response({"judgment_complexity": 2}) == 2
    assert _parse_judgment_response({"score": 1}) == 1


def test_parse_judgment_response_none_and_bool_and_out_of_range():
    from skill_evolve import _parse_judgment_response
    assert _parse_judgment_response(None) is None
    assert _parse_judgment_response(True) is None  # bool は数値扱いしない
    assert _parse_judgment_response(5) is None      # 範囲外
    assert _parse_judgment_response("no digit") is None


# --- compute_llm_scores が LLM-free（cache-miss は static フォールバック） ---


def test_compute_llm_scores_cache_miss_is_static(tmp_path, monkeypatch):
    from skill_evolve import compute_llm_scores
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill\n\nif foo: ...\nelse: ...\n条件 判断 場合\n")

    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    result = compute_llm_scores("my-skill", skill_dir)
    assert result["cached"] is False
    assert result["judgment_source"] == "static"
    assert result["judgment_complexity"] in (1, 2, 3)
    # cache に static として確定保存される
    saved = json.loads(cache_file.read_text())
    assert saved["my-skill"]["judgment_source"] == "static"


# --- emit_judgment_requests / ingest_judgment_scores ---


def _make_skill(tmp_path, name, content="# S\n\nif x: ...\n"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(content)
    return d


def test_emit_judgment_requests_shape_and_meta(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests
    sd = _make_skill(tmp_path, "alpha")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

    out = emit_judgment_requests(tmp_path, [sd])
    assert len(out["requests"]) == 1
    req = out["requests"][0]
    assert req["id"] == "alpha"
    assert "判断の複雑さ" in req["prompt"]
    assert "hash" in req["meta"]
    assert "external_dependency" in req["meta"]
    assert "_content" not in req["meta"]  # 内部フィールドは meta から除去


def test_emit_judgment_skips_fresh_llm_but_not_static(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, _file_hash
    sd_llm = _make_skill(tmp_path, "llmskill")
    sd_static = _make_skill(tmp_path, "staticskill")
    cache_file = tmp_path / "cache.json"
    cache = {
        "llmskill": {"hash": _file_hash(sd_llm / "SKILL.md"),
                     "judgment_source": "llm", "judgment_complexity": 2,
                     "external_dependency": 1},
        "staticskill": {"hash": _file_hash(sd_static / "SKILL.md"),
                        "judgment_source": "static", "judgment_complexity": 1,
                        "external_dependency": 1},
    }
    cache_file.write_text(json.dumps(cache))
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

    out = emit_judgment_requests(tmp_path, [sd_llm, sd_static])
    ids = {r["id"] for r in out["requests"]}
    assert ids == {"staticskill"}  # fresh-llm は除外、static は emit

    # refresh=True なら両方 emit
    out2 = emit_judgment_requests(tmp_path, [sd_llm, sd_static], refresh=True)
    assert {r["id"] for r in out2["requests"]} == {"llmskill", "staticskill"}


def test_ingest_judgment_scores_updates_cache_as_llm(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, ingest_judgment_scores
    sd = _make_skill(tmp_path, "beta")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    out = emit_judgment_requests(tmp_path, [sd])
    responses = {"beta": "3"}
    result = ingest_judgment_scores(tmp_path, out["requests"], responses)
    assert result == {"beta": 3}
    saved = json.loads(cache_file.read_text())
    assert saved["beta"]["judgment_complexity"] == 3
    assert saved["beta"]["judgment_source"] == "llm"
    assert saved["beta"]["external_dependency"] >= 1  # meta から補完


def test_ingest_judgment_leaves_static_when_unparseable(tmp_path, monkeypatch):
    from skill_evolve import emit_judgment_requests, ingest_judgment_scores
    sd = _make_skill(tmp_path, "gamma")
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
    monkeypatch.setattr("skill_evolve.DATA_DIR", tmp_path)

    out = emit_judgment_requests(tmp_path, [sd])
    # 応答漏れ（None）→ 据え置き、cache 更新なし
    result = ingest_judgment_scores(tmp_path, out["requests"], {})
    assert result == {}
    assert not cache_file.exists()  # result 空なら save しない
