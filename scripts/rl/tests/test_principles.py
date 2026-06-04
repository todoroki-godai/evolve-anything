#!/usr/bin/env python3
"""principles.py のテスト"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_rl_dir = _test_dir.parent
_plugin_root = _rl_dir.parent.parent
sys.path.insert(0, str(_rl_dir))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))

_principles_path = _rl_dir / "fitness" / "principles.py"
_spec = importlib.util.spec_from_file_location("principles", _principles_path)
principles = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(principles)


def _make_project(tmp_path):
    """テスト用プロジェクトを作成する。"""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text("# Project\n\n## Skills\n\n- sample-skill: A sample\n")

    rules_dir = claude_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "sample.md").write_text("# Rule\nDo this.\n")

    return tmp_path


def _ingest_with(project, extracted_principles):
    """[ADR-037] 2相経路: emit-request → 応答注入 → ingest_principles。

    extracted_principles が None の場合はパース失敗（不正 JSON）を模す。
    """
    req = principles.build_extraction_request(project, refresh=True)
    raw = "not json" if extracted_principles is None else json.dumps(extracted_principles)
    responses = {"principles": raw}
    return principles.ingest_principles(project, req["requests"], responses)


class TestCacheRoundtrip:
    def test_save_and_load(self, tmp_path):
        project = _make_project(tmp_path)
        data = {
            "principles": list(principles.SEED_PRINCIPLES),
            "excluded_low_quality": [],
            "source_hash": "abc123",
        }
        principles._save_cache(project, data)
        loaded = principles._load_cache(project)
        assert loaded is not None
        assert loaded["principles"] == data["principles"]
        assert loaded["source_hash"] == "abc123"

    def test_load_nonexistent_returns_none(self, tmp_path):
        project = _make_project(tmp_path)
        assert principles._load_cache(project) is None


class TestStalenessDetection:
    def test_hash_mismatch_marks_stale(self, tmp_path):
        project = _make_project(tmp_path)
        # キャッシュを保存（異なるハッシュ値で）
        data = {
            "principles": list(principles.SEED_PRINCIPLES),
            "excluded_low_quality": [],
            "source_hash": "old_hash_value",
        }
        principles._save_cache(project, data)

        result = principles.extract_principles(project, refresh=False)
        assert result["from_cache"] is True
        assert result["stale_cache"] is True

    def test_hash_match_not_stale(self, tmp_path):
        project = _make_project(tmp_path)
        current_hash = principles._compute_source_hash(project)
        data = {
            "principles": list(principles.SEED_PRINCIPLES),
            "excluded_low_quality": [],
            "source_hash": current_hash,
        }
        principles._save_cache(project, data)

        result = principles.extract_principles(project, refresh=False)
        assert result["from_cache"] is True
        assert result["stale_cache"] is False


class TestSeedPrinciples:
    def test_seeds_always_present(self, tmp_path):
        project = _make_project(tmp_path)
        llm_output = [
            {
                "id": "custom-rule",
                "text": "Custom rule",
                "source": "CLAUDE.md",
                "category": "quality",
                "specificity": 0.8,
                "testability": 0.8,
            }
        ]
        result = _ingest_with(project, llm_output)

        ids = {p["id"] for p in result["principles"]}
        for seed in principles.SEED_PRINCIPLES:
            assert seed["id"] in ids, f"Seed principle {seed['id']} missing"


class TestUserDefinedPreservation:
    def test_user_defined_preserved_on_refresh(self, tmp_path):
        project = _make_project(tmp_path)
        # user_defined 原則を含むキャッシュを作成
        cached = {
            "principles": list(principles.SEED_PRINCIPLES) + [
                {
                    "id": "my-custom-principle",
                    "text": "My custom principle",
                    "source": "user",
                    "category": "convention",
                    "specificity": 0.9,
                    "testability": 0.9,
                    "user_defined": True,
                }
            ],
            "excluded_low_quality": [],
            "source_hash": "old_hash",
        }
        principles._save_cache(project, cached)

        llm_output = [
            {
                "id": "new-principle",
                "text": "New from LLM",
                "source": "CLAUDE.md",
                "category": "quality",
                "specificity": 0.7,
                "testability": 0.7,
            }
        ]
        result = _ingest_with(project, llm_output)

        ids = {p["id"] for p in result["principles"]}
        assert "my-custom-principle" in ids


class TestQualityFiltering:
    def test_low_quality_excluded(self, tmp_path):
        project = _make_project(tmp_path)
        llm_output = [
            {
                "id": "low-quality",
                "text": "Vague rule",
                "source": "CLAUDE.md",
                "category": "quality",
                "specificity": 0.1,
                "testability": 0.1,
            },
            {
                "id": "high-quality",
                "text": "Specific rule",
                "source": "CLAUDE.md",
                "category": "quality",
                "specificity": 0.8,
                "testability": 0.8,
            },
        ]
        result = _ingest_with(project, llm_output)

        passed_ids = {p["id"] for p in result["principles"]}
        excluded_ids = {p["id"] for p in result["excluded_low_quality"]}
        assert "low-quality" in excluded_ids
        assert "high-quality" in passed_ids

    def test_seeds_bypass_quality_check(self, tmp_path):
        """seed 原則は品質スコアに関係なく常に含まれる。"""
        # seed の品質スコアが閾値未満でも通過することを確認
        test_principles = [
            {
                "id": "test-seed",
                "text": "Test seed",
                "source": "seed",
                "category": "quality",
                "specificity": 0.0,
                "testability": 0.0,
                "seed": True,
            }
        ]
        passed, excluded = principles._filter_by_quality(test_principles)
        assert len(passed) == 1
        assert len(excluded) == 0
        assert passed[0]["id"] == "test-seed"


class TestLLMFailureFallback:
    def test_parse_failure_returns_seeds_only(self, tmp_path):
        """[ADR-037] ingest 時にパース失敗したら seed-only にフォールバックする。"""
        project = _make_project(tmp_path)
        result = _ingest_with(project, None)  # 不正 JSON を注入

        assert result["from_cache"] is False
        ids = {p["id"] for p in result["principles"]}
        seed_ids = {s["id"] for s in principles.SEED_PRINCIPLES}
        assert ids == seed_ids


class TestTwoPhaseEmitIngest:
    """[ADR-037] claude -p 全廃の2相経路。"""

    def test_emit_request_on_fresh_project(self, tmp_path):
        project = _make_project(tmp_path)
        out = principles.build_extraction_request(project, refresh=True)
        assert len(out["requests"]) == 1
        req = out["requests"][0]
        assert req["id"] == "principles"
        assert "原則" in req["prompt"] or "principle" in req["prompt"].lower()
        # 巨大な source 本文は meta に残さない
        assert "_src" not in req["meta"]
        assert req["meta"]["source_hash"] == out["source_hash"]

    def test_emit_empty_when_cache_valid(self, tmp_path):
        """cache が有効（source_hash 一致）なら refresh 不要で requests=[]。"""
        project = _make_project(tmp_path)
        # 正規の cache を作る
        out = principles.build_extraction_request(project, refresh=True)
        responses = {"principles": json.dumps([{
            "id": "x", "text": "y", "source": "CLAUDE.md",
            "category": "quality", "specificity": 0.8, "testability": 0.8,
        }])}
        principles.ingest_principles(project, out["requests"], responses)

        again = principles.build_extraction_request(project, refresh=False)
        assert again["requests"] == []

    def test_emit_empty_on_empty_source(self, tmp_path):
        """source が空なら requests=[]。"""
        out = principles.build_extraction_request(tmp_path, refresh=True)
        assert out["requests"] == []

    def test_ingest_persists_cache(self, tmp_path):
        project = _make_project(tmp_path)
        result = _ingest_with(project, [{
            "id": "persisted", "text": "p", "source": "CLAUDE.md",
            "category": "quality", "specificity": 0.9, "testability": 0.9,
        }])
        # 次の extract_principles が cache から読めること
        reloaded = principles.extract_principles(project, refresh=False)
        assert reloaded["from_cache"] is True
        ids = {p["id"] for p in reloaded["principles"]}
        assert "persisted" in ids
        assert result["from_cache"] is False


class TestExtractPrinciplesLLMFree:
    """[ADR-037] extract_principles は LLM を呼ばず cache を読むだけ。"""

    def test_cache_miss_returns_seed_only_non_persisted(self, tmp_path):
        project = _make_project(tmp_path)
        result = principles.extract_principles(project, refresh=False)
        assert result["from_cache"] is False
        ids = {p["id"] for p in result["principles"]}
        seed_ids = {s["id"] for s in principles.SEED_PRINCIPLES}
        assert ids == seed_ids
        # 非永続: cache ファイルが作られていない（emit/ingest で正式抽出させるため）
        assert not (project / ".claude" / "principles.json").exists()

    def test_no_subprocess_import(self):
        """モジュールが subprocess を import していない（claude -p 全廃）。"""
        import inspect
        src = inspect.getsource(principles)
        assert "import subprocess" not in src
        assert not hasattr(principles, "_extract_via_llm")

    def test_parser_accepts_already_parsed_list(self):
        """Phase B が parse 済み list で返しても受ける（str 専用クラッシュ回避）。"""
        out = principles._parse_principles_response([{"id": "x", "text": "y"}])
        assert out == [{"id": "x", "text": "y"}]

    def test_parser_rejects_non_str_non_list(self):
        assert principles._parse_principles_response({"not": "a list"}) is None
        assert principles._parse_principles_response(123) is None


class TestComputeSourceHash:
    def test_hash_changes_when_claudemd_changes(self, tmp_path):
        project = _make_project(tmp_path)
        hash1 = principles._compute_source_hash(project)

        # CLAUDE.md を変更
        (project / "CLAUDE.md").write_text("# Updated Project\n\nNew content.\n")
        hash2 = principles._compute_source_hash(project)

        assert hash1 != hash2

    def test_hash_changes_when_rules_change(self, tmp_path):
        project = _make_project(tmp_path)
        hash1 = principles._compute_source_hash(project)

        # ルールを追加
        rules_dir = project / ".claude" / "rules"
        (rules_dir / "new-rule.md").write_text("# New Rule\nDo something else.\n")
        hash2 = principles._compute_source_hash(project)

        assert hash1 != hash2

    def test_hash_stable_for_same_content(self, tmp_path):
        project = _make_project(tmp_path)
        hash1 = principles._compute_source_hash(project)
        hash2 = principles._compute_source_hash(project)
        assert hash1 == hash2


class TestExtractionPromptCategoryEnum:
    """_build_extraction_prompt の category enum に philosophy が含まれること。"""

    def test_prompt_includes_philosophy_category(self):
        prompt = principles._build_extraction_prompt("# sample\n")
        assert "quality|safety|performance|convention|philosophy" in prompt

    def test_prompt_keeps_existing_categories(self):
        prompt = principles._build_extraction_prompt("# sample\n")
        for cat in ("quality", "safety", "performance", "convention"):
            assert cat in prompt


class TestSeedPrinciplesPhilosophy:
    """SEED_PRINCIPLES に Karpathy 4原則が philosophy カテゴリで含まれること。"""

    def test_seed_includes_karpathy_four(self):
        ids = {p["id"] for p in principles.SEED_PRINCIPLES}
        assert {
            "think-before-coding",
            "simplicity-first",
            "surgical-changes",
            "goal-driven-execution",
        }.issubset(ids)

    def test_karpathy_seeds_are_philosophy_category(self):
        karpathy_ids = {
            "think-before-coding",
            "simplicity-first",
            "surgical-changes",
            "goal-driven-execution",
        }
        for p in principles.SEED_PRINCIPLES:
            if p["id"] in karpathy_ids:
                assert p["category"] == "philosophy"
                assert p["seed"] is True

    def test_seed_total_count(self):
        # コア5 + philosophy 4 = 9
        assert len(principles.SEED_PRINCIPLES) == 9
