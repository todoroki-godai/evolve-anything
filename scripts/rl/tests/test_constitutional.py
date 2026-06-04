#!/usr/bin/env python3
"""constitutional.py のテスト"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

_constitutional_path = _rl_dir / "fitness" / "constitutional.py"
_spec = importlib.util.spec_from_file_location("constitutional", _constitutional_path)
constitutional = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(constitutional)


def _make_project(tmp_path):
    """テスト用プロジェクトを作成する。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text("# Project\n\n## Skills\n\n- sample-skill: A sample\n")

    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "sample.md").write_text("# Rule\nDo this.\n")

    skill_dir = claude_dir / "skills" / "sample-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Sample Skill\n\n## Usage\n\nUse it.\n")

    mem_dir = claude_dir / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("# Memory\n\n## Notes\n\nSome notes.\n")

    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": ["echo test"]}]}}
    (claude_dir / "settings.json").write_text(json.dumps(settings))

    return tmp_path


_TEST_PRINCIPLES = [
    {
        "id": "single-responsibility",
        "text": "各スキル/ルールは単一の責務を持つ",
        "source": "seed",
        "category": "quality",
        "specificity": 0.7,
        "testability": 0.8,
        "seed": True,
    },
    {
        "id": "user-consent",
        "text": "破壊的操作の前にユーザー確認を取る",
        "source": "seed",
        "category": "safety",
        "specificity": 0.8,
        "testability": 0.9,
        "seed": True,
    },
]


def _make_load_sibling(principles_from_cache=False):
    """coherence / principles の _load_sibling モックを生成する。"""
    def _loader(name):
        if name == "coherence":
            m = mock.MagicMock()
            m.compute_coherence_score = _mock_coherence_sufficient
            m._find_artifacts_local = _mock_find_artifacts
            return m
        if name == "principles":
            m = mock.MagicMock()
            m.extract_principles = lambda pd: {
                "principles": list(_TEST_PRINCIPLES),
                "excluded_low_quality": [],
                "source_hash": "test_hash",
                "stale_cache": False,
                "from_cache": principles_from_cache,
            }
            return m
        raise ValueError(f"Unexpected sibling: {name}")
    return _loader


def _layer_responses(requests, score=0.8, fail_layers=None):
    """[ADR-037] 各レイヤー request に対する assistant 応答 JSON を生成する。"""
    pids = [p["id"] for p in _TEST_PRINCIPLES]
    fail = set(fail_layers or [])
    responses = {}
    for req in requests:
        if req["id"] in fail:
            responses[req["id"]] = "not json"  # パース失敗を模す
            continue
        evals = [
            {"principle_id": pid, "score": score, "rationale": "ok", "violations": []}
            for pid in pids
        ]
        responses[req["id"]] = json.dumps({"evaluations": evals})
    return responses


def _run_two_phase(project, score=0.8, refresh=True, fail_layers=None,
                   principles_from_cache=False):
    """emit_layer_requests → 応答生成 → ingest_layer_responses を一括実行する。"""
    loader = _make_load_sibling(principles_from_cache)
    with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
        out = constitutional.emit_layer_requests(project, refresh=refresh)
        responses = _layer_responses(out["requests"], score=score, fail_layers=fail_layers)
        result = constitutional.ingest_layer_responses(project, out["requests"], responses)
    return out, result


def _mock_coherence_low_coverage(project_dir):
    """coverage < 0.5 を返すモック。"""
    return {"overall": 0.3, "coverage": 0.3}


def _mock_coherence_sufficient(project_dir):
    """coverage >= 0.5 を返すモック。"""
    return {"overall": 0.8, "coverage": 0.8}


def _mock_principles_result(project_dir):
    """テスト用の原則リストを返すモック。"""
    return {
        "principles": list(_TEST_PRINCIPLES),
        "excluded_low_quality": [],
        "source_hash": "test_hash",
        "stale_cache": False,
        "from_cache": False,
    }


def _mock_find_artifacts(project_dir):
    """テスト用アーティファクトを返すモック。"""
    claude_dir = project_dir / ".claude"
    result = {
        "claude_md": [project_dir / "CLAUDE.md"],
        "rules": list((claude_dir / "rules").glob("*.md")) if (claude_dir / "rules").exists() else [],
        "skills": list(claude_dir.rglob("SKILL.md")) if (claude_dir / "skills").exists() else [],
        "memory": list((claude_dir / "memory").glob("*.md")) if (claude_dir / "memory").exists() else [],
    }
    return result


class TestCoverageGate:
    def test_low_coverage_returns_skip(self, tmp_path):
        """coverage < 0.5 の場合、skip_reason 付きで None overall を返す。"""
        project = _make_project(tmp_path)

        mock_coherence = mock.MagicMock()
        mock_coherence.compute_coherence_score = _mock_coherence_low_coverage

        with mock.patch.object(constitutional, "_load_sibling") as mock_load:
            mock_load.return_value = mock_coherence
            result = constitutional.compute_constitutional_score(project)

        assert result is not None
        assert result["overall"] is None
        assert result["skip_reason"] == "low_coverage"
        assert result["coverage_value"] < 0.5

    def test_sufficient_coverage_proceeds(self, tmp_path):
        """coverage >= 0.5 の場合、2相経路で評価を実行する。"""
        project = _make_project(tmp_path)
        _out, result = _run_two_phase(project, score=0.8)

        assert result is not None
        assert result["overall"] is not None
        assert 0.0 <= result["overall"] <= 1.0


class TestAllLayersFail:
    def test_all_layers_fail_returns_none(self, tmp_path):
        """全レイヤーの応答がパース失敗した場合、ingest は None を返す。"""
        project = _make_project(tmp_path)
        loader = _make_load_sibling()
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            out = constitutional.emit_layer_requests(project, refresh=True)
            all_layers = [r["id"] for r in out["requests"]]
            responses = _layer_responses(out["requests"], fail_layers=all_layers)
            result = constitutional.ingest_layer_responses(project, out["requests"], responses)

        assert result is None


class TestPartialLayerFailure:
    def test_partial_failure_uses_remaining(self, tmp_path):
        """一部レイヤーがパース失敗しても、残りのレイヤーからスコアを算出する。"""
        project = _make_project(tmp_path)
        loader = _make_load_sibling()
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            out = constitutional.emit_layer_requests(project, refresh=True)
            # 先頭レイヤーのみ失敗させる
            fail = [out["requests"][0]["id"]]
            responses = _layer_responses(out["requests"], score=0.7, fail_layers=fail)
            result = constitutional.ingest_layer_responses(project, out["requests"], responses)

        assert result is not None
        assert result["overall"] is not None
        assert result["evaluated_layers"] < result["total_layers"]


class TestScoreAggregation:
    def test_per_principle_is_mean_of_layer_scores(self, tmp_path):
        """per_principle は各レイヤースコアの平均。"""
        project = _make_project(tmp_path)
        _out, result = _run_two_phase(project, score=0.6)

        assert result is not None
        for pp in result["per_principle"]:
            # 全レイヤーで同じスコア (0.6) なので平均も 0.6
            assert abs(pp["score"] - 0.6) < 0.01


class TestScoreClamp:
    def test_clamp_above_one(self):
        assert constitutional._clamp(1.5) == 1.0

    def test_clamp_below_zero(self):
        assert constitutional._clamp(-0.5) == 0.0

    def test_clamp_normal_value(self):
        assert constitutional._clamp(0.7) == 0.7


class TestCacheSaveLoad:
    def test_cache_saves_layer_hashes(self, tmp_path):
        """ingest 後、キャッシュにレイヤーハッシュが保存される。"""
        project = _make_project(tmp_path)
        _out, _result = _run_two_phase(project, score=0.8)

        cache = constitutional._load_cache(project)
        assert cache is not None
        assert "layer_hashes" in cache
        assert "layer_results" in cache

    def test_compute_reads_cache_only(self, tmp_path):
        """[ADR-037] ingest でキャッシュ生成後、compute_constitutional_score が LLM 無しで集約する。"""
        project = _make_project(tmp_path)
        _run_two_phase(project, score=0.8)  # cache 生成

        # compute は cache 命中レイヤーのみで集約（principles/coherence は本物を使う必要があるため mock）
        loader = _make_load_sibling(principles_from_cache=True)
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            result = constitutional.compute_constitutional_score(project, refresh=False)

        assert result is not None
        assert result["overall"] is not None
        assert result["from_cache"] is True

    def test_compute_returns_none_without_cache(self, tmp_path):
        """[ADR-037] cache 未生成なら compute は None（LLM を呼ばない）。"""
        project = _make_project(tmp_path)
        loader = _make_load_sibling()
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            result = constitutional.compute_constitutional_score(project, refresh=False)
        assert result is None


class TestCostTracking:
    def test_cost_fields_present(self, tmp_path):
        """estimated_cost_usd と llm_calls_count が結果に含まれる。"""
        project = _make_project(tmp_path)
        _out, result = _run_two_phase(project, score=0.8)

        assert result is not None
        assert "estimated_cost_usd" in result
        assert "llm_calls_count" in result
        assert result["estimated_cost_usd"] >= 0
        assert result["llm_calls_count"] >= 0


class TestTwoPhaseEmitIngest:
    """[ADR-037] claude -p 全廃の2相経路。"""

    def test_emit_returns_requests_for_each_layer(self, tmp_path):
        project = _make_project(tmp_path)
        loader = _make_load_sibling()
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            out = constitutional.emit_layer_requests(project, refresh=True)
        ids = {r["id"] for r in out["requests"]}
        # _make_project は 4 レイヤーすべてを用意する
        assert ids == {"claude_md", "rules", "skills", "memory"}
        for r in out["requests"]:
            assert "_content" not in r["meta"]  # 巨大本文を meta に残さない
            assert "content_hash" in r["meta"]

    def test_emit_flags_principles_missing(self, tmp_path):
        """principles が cache 由来でない場合 principles_missing=True。"""
        project = _make_project(tmp_path)
        loader = _make_load_sibling(principles_from_cache=False)
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            out = constitutional.emit_layer_requests(project, refresh=True)
        assert out["principles_missing"] is True

    def test_emit_skips_cache_hit_layers(self, tmp_path):
        """cache 命中レイヤーは requests でなく skipped に入る。"""
        project = _make_project(tmp_path)
        _run_two_phase(project, score=0.8)  # 全レイヤーを cache 化
        loader = _make_load_sibling(principles_from_cache=True)
        with mock.patch.object(constitutional, "_load_sibling", side_effect=loader):
            out = constitutional.emit_layer_requests(project, refresh=False)
        assert out["requests"] == []
        assert set(out["skipped"]) == {"claude_md", "rules", "skills", "memory"}

    def test_emit_low_coverage_returns_skip(self, tmp_path):
        project = _make_project(tmp_path)
        m_coh = mock.MagicMock()
        m_coh.compute_coherence_score = _mock_coherence_low_coverage
        with mock.patch.object(constitutional, "_load_sibling", return_value=m_coh):
            out = constitutional.emit_layer_requests(project, refresh=True)
        assert out["requests"] == []
        assert out.get("skip_reason") == "low_coverage"

    def test_no_subprocess_import(self):
        """モジュールが subprocess を import していない（claude -p 全廃）。"""
        import inspect
        src = inspect.getsource(constitutional)
        assert "import subprocess" not in src
        assert not hasattr(constitutional, "_evaluate_layer")

    def test_parser_accepts_already_parsed_dict(self):
        """Phase B が parse 済み dict で返しても受ける（str 専用クラッシュ回避）。"""
        out = constitutional._parse_layer_response(
            {"evaluations": [{"principle_id": "p", "score": 0.8}]}
        )
        assert out is not None
        assert out["evaluations"][0]["score"] == 0.8

    def test_parser_rejects_non_str_non_dict(self):
        assert constitutional._parse_layer_response(["list"]) is None
        assert constitutional._parse_layer_response(123) is None


class TestLoadSiblingPackage:
    """_load_sibling が package 化された coherence をロードできる回帰テスト（#277）。

    coherence は #129〜#143 で coherence/__init__.py パッケージへ分割されたが、
    constitutional の _load_sibling は `{name}.py` 固定パスのままだったため
    FileNotFoundError → constitutional スコアが silent skip していた。
    environment.py の package 対応版に追従したことを保証する。
    """

    def test_load_sibling_loads_coherence_package(self):
        mod = constitutional._load_sibling("coherence")
        assert hasattr(mod, "compute_coherence_score"), (
            "coherence パッケージ (coherence/__init__.py) がロードできていない"
        )

    def test_load_sibling_still_loads_flat_module(self):
        # principles はファイルのまま（package でない）— 両方の経路を保証
        mod = constitutional._load_sibling("principles")
        assert mod is not None
