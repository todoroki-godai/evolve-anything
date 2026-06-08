"""#377-1: batch_guard 見積もりの cache-aware 化 + is_fresh_llm_judgment SoT 述語のテスト。

batch_guard の estimated_tokens は worst-case（全スキル Phase B 想定）だが、
emit_judgment_requests(refresh=False) は is_fresh_llm（hash 一致 AND judgment_source==llm）の
スキルを skip するため、cache-fresh スキルの実 Phase B コストは ≈0。worst-case と
cache 反映後の見込みを併記することを検証する。

判断述語 is_fresh_llm_judgment は emit_judgment_requests の skip 条件と
同一定義の SoT（両者が drift しないことを保証する）。
"""
import json
import sys
from pathlib import Path
from unittest import mock

_lib_dir = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_lib_dir))


def _make_skill(tmp_path, name, content="# S\n\nif x: ...\n", subdir=".claude/skills"):
    d = tmp_path / subdir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(content)
    return d / "SKILL.md"


class TestIsFreshLlmJudgment:
    """is_fresh_llm_judgment 述語が emit_judgment_requests の skip 条件と一致する。"""

    def test_fresh_llm_true(self, tmp_path, monkeypatch):
        from skill_evolve import _file_hash
        from skill_evolve.llm_scoring import is_fresh_llm_judgment
        sm = _make_skill(tmp_path, "a")
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({
            "a": {"hash": _file_hash(sm), "judgment_source": "llm",
                  "judgment_complexity": 2, "external_dependency": 1}}))
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
        assert is_fresh_llm_judgment(sm.parent) is True

    def test_static_is_not_fresh(self, tmp_path, monkeypatch):
        from skill_evolve import _file_hash
        from skill_evolve.llm_scoring import is_fresh_llm_judgment
        sm = _make_skill(tmp_path, "b")
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({
            "b": {"hash": _file_hash(sm), "judgment_source": "static",
                  "judgment_complexity": 1, "external_dependency": 1}}))
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
        assert is_fresh_llm_judgment(sm.parent) is False

    def test_hash_mismatch_is_not_fresh(self, tmp_path, monkeypatch):
        from skill_evolve.llm_scoring import is_fresh_llm_judgment
        sm = _make_skill(tmp_path, "c")
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps({
            "c": {"hash": "stale-hash", "judgment_source": "llm",
                  "judgment_complexity": 2, "external_dependency": 1}}))
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
        assert is_fresh_llm_judgment(sm.parent) is False

    def test_missing_skill_md_is_not_fresh(self, tmp_path, monkeypatch):
        from skill_evolve.llm_scoring import is_fresh_llm_judgment
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("{}")
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
        assert is_fresh_llm_judgment(tmp_path / "nope") is False

    def test_accepts_explicit_cache_dict(self, tmp_path, monkeypatch):
        """cache を明示的に渡せば _load_cache を呼ばない（バッチで N 回読まないため）。"""
        from skill_evolve import _file_hash
        from skill_evolve.llm_scoring import is_fresh_llm_judgment
        sm = _make_skill(tmp_path, "d")
        cache = {"d": {"hash": _file_hash(sm), "judgment_source": "llm",
                       "judgment_complexity": 2, "external_dependency": 1}}
        # CACHE_FILE を壊しても、明示 cache で判定できる
        monkeypatch.setattr("skill_evolve.CACHE_FILE", tmp_path / "does-not-exist.json")
        assert is_fresh_llm_judgment(sm.parent, cache=cache) is True


class TestBatchGuardCacheAware:
    """batch_guard group が cache-aware の見積もりキーを含む。"""

    def _run(self, tmp_path, monkeypatch, cache):
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)

        skill_paths = [_make_skill(tmp_path, f"s-{i}") for i in range(11)]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(cache))
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)

        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None
        return skill_paths, sentinel

    def test_groups_include_cache_aware_keys(self, tmp_path, monkeypatch):
        from skill_evolve import _file_hash
        # s-0..s-4 を fresh-llm にし、残り 6 件は cache 不在（refresh 必要）
        skill_paths_pre = [
            (tmp_path / ".claude/skills" / f"s-{i}" / "SKILL.md") for i in range(5)
        ]
        # 先に作って hash を取るため、_run より前に同じパスで生成しておく
        for i in range(5):
            d = tmp_path / ".claude/skills" / f"s-{i}"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# S\n\nif x: ...\n")
        cache = {}
        for i in range(5):
            sm = tmp_path / ".claude/skills" / f"s-{i}" / "SKILL.md"
            cache[f"s-{i}"] = {"hash": _file_hash(sm), "judgment_source": "llm",
                               "judgment_complexity": 2, "external_dependency": 1}

        # _run は s-0..s-10 を作るが、既存ディレクトリは mkdir(exist_ok) 衝突するため
        # _make_skill を exist_ok 対応にする想定でなく、ここは別経路で組む。
        import importlib
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
        import skill_evolve.denylist as dl_mod
        importlib.reload(dl_mod)
        # 残り 6 件を追加生成
        for i in range(5, 11):
            d = tmp_path / ".claude/skills" / f"s-{i}"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# S\n\nif x: ...\n")
        skill_paths = [tmp_path / ".claude/skills" / f"s-{i}" / "SKILL.md" for i in range(11)]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(cache))
        monkeypatch.setattr("skill_evolve.CACHE_FILE", cache_file)
        cfg_mock = mock.MagicMock()
        cfg_mock.get.return_value = ""
        with mock.patch("skill_evolve.assessment.find_artifacts", return_value={"skills": skill_paths}), \
             mock.patch("skill_evolve.assessment.classify_artifact_origin", return_value="custom"), \
             mock.patch("skill_evolve.assessment.load_user_config", return_value=cfg_mock), \
             mock.patch("skill_evolve.denylist.DATA_DIR", tmp_path):
            from skill_evolve.assessment import skill_evolve_assessment
            result = skill_evolve_assessment(tmp_path)
        sentinel = next((r for r in result if r.get("_meta") == "batch_guard_trigger"), None)
        assert sentinel is not None
        g = next(gr for gr in sentinel["groups"] if gr["origin"] == "custom")
        assert g["skill_count"] == 11
        assert g["cache_fresh_count"] == 5
        assert g["refresh_needed_count"] == 6
        assert g["cache_fresh_count"] + g["refresh_needed_count"] == g["skill_count"]
        # cache-aware は worst-case より小さい（5件が ≈0）
        assert g["estimated_tokens_cache_aware"] < g["estimated_tokens"]
        assert g["estimated_tokens_cache_aware"] > 0

    def test_cache_aware_equals_worst_when_none_fresh(self, tmp_path, monkeypatch):
        # cache 空 → 全件 refresh 必要 → cache-aware == worst-case
        _paths, sentinel = self._run(tmp_path, monkeypatch, cache={})
        g = next(gr for gr in sentinel["groups"] if gr["origin"] == "custom")
        assert g["cache_fresh_count"] == 0
        assert g["refresh_needed_count"] == 11
        assert g["estimated_tokens_cache_aware"] == g["estimated_tokens"]
