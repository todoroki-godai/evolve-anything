#!/usr/bin/env python3
"""evolution_memory.py のテスト — 成功最適化パターンの JSONL 永続化 + read 層 union（#45）。

save_winner（write）は canonical 固定のまま、load_patterns（read）だけ
canonical + legacy/plugins-data を cross-dir union read する（ADR-049）。
LLM は呼ばない（no-llm-in-tests 遵守）。
"""

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import evolution_memory as em  # noqa: E402


class TestSaveAndLoad:
    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em, "DATA_DIR", tmp_path)
        em.save_winner("skill-a", "llm_improve", 0.4, 0.7, "patch a")
        loaded = em.load_patterns()
        assert len(loaded) == 1
        assert loaded[0]["skill_name"] == "skill-a"
        assert loaded[0]["score_after"] == 0.7

    def test_load_filters_by_skill_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em, "DATA_DIR", tmp_path)
        em.save_winner("skill-a", "llm_improve", 0.4, 0.7, "pa")
        em.save_winner("skill-b", "error_guided", 0.3, 0.6, "pb")
        loaded = em.load_patterns(skill_name="skill-b")
        assert [r["skill_name"] for r in loaded] == ["skill-b"]

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em, "DATA_DIR", tmp_path)
        assert em.load_patterns() == []

    def test_patch_summary_truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em, "DATA_DIR", tmp_path)
        em.save_winner("s", "llm_improve", 0.1, 0.2, "x" * 500)
        loaded = em.load_patterns()
        assert len(loaded[0]["patch_summary"]) == em._MAX_PATCH_SUMMARY_LEN


class TestLoadPatternsUnion:
    """load_patterns は canonical + legacy/plugins-data を cross-dir union read する（#45）。

    evolution_memory.jsonl が rename（rl-anything→evolve-anything）で legacy にのみ残ると、
    canonical だけ読む load_patterns は過去の成功パターンを取り逃す（pipeline_eval の
    convergence_cycles が 0 になる）。canonical=tmp/evolve-anything + 兄弟 tmp/rl-anything で
    hermetic に検証。write（save_winner）は canonical 固定のまま（ADR-049）。
    """

    @staticmethod
    def _canonical(root: Path) -> Path:
        c = root / "evolve-anything"
        c.mkdir(parents=True, exist_ok=True)
        return c

    @staticmethod
    def _write(dir_: Path, records: list) -> None:
        dir_.mkdir(parents=True, exist_ok=True)
        (dir_ / "evolution_memory.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def _rec(self, skill: str, ts: str) -> dict:
        return {
            "ts": ts,
            "skill_name": skill,
            "strategy": "llm_improve",
            "score_before": 0.4,
            "score_after": 0.7,
            "patch_summary": f"patch-{skill}",
        }

    def test_unions_canonical_and_legacy(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(em, "DATA_DIR", canonical)
        legacy = tmp_path / "rl-anything"
        self._write(canonical, [self._rec("c", "2026-06-02T00:00:00+00:00")])
        self._write(legacy, [self._rec("l", "2026-06-01T00:00:00+00:00")])
        loaded = em.load_patterns(limit=10)
        assert sorted(r["skill_name"] for r in loaded) == ["c", "l"]
        # ts 降順（新しい順）が維持される
        assert [r["skill_name"] for r in loaded] == ["c", "l"]

    def test_identical_record_deduped(self, tmp_path, monkeypatch):
        """同一レコードが両 dir に存在しても二重カウントしない。"""
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(em, "DATA_DIR", canonical)
        legacy = tmp_path / "rl-anything"
        rec = self._rec("dup", "2026-06-01T00:00:00+00:00")
        self._write(canonical, [rec])
        self._write(legacy, [dict(rec)])
        loaded = em.load_patterns(limit=10)
        assert len(loaded) == 1

    def test_skill_filter_applies_across_union(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(em, "DATA_DIR", canonical)
        legacy = tmp_path / "rl-anything"
        self._write(canonical, [self._rec("keep", "2026-06-02T00:00:00+00:00")])
        self._write(legacy, [self._rec("keep", "2026-06-01T00:00:00+00:00"),
                             self._rec("drop", "2026-06-01T00:00:00+00:00")])
        loaded = em.load_patterns(skill_name="keep", limit=10)
        assert all(r["skill_name"] == "keep" for r in loaded)
        assert len(loaded) == 2

    def test_hermetic_tmp_only_reads_canonical(self, tmp_path, monkeypatch):
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(em, "DATA_DIR", canonical)
        self._write(canonical, [self._rec("c", "2026-06-01T00:00:00+00:00")])
        loaded = em.load_patterns()
        assert [r["skill_name"] for r in loaded] == ["c"]

    def test_write_stays_canonical_only(self, tmp_path, monkeypatch):
        """save_winner は canonical のみへ書く（union read 化で write が漏れない・ADR-049）。"""
        canonical = self._canonical(tmp_path)
        monkeypatch.setattr(em, "DATA_DIR", canonical)
        legacy = tmp_path / "rl-anything"
        legacy.mkdir()
        em.save_winner("s", "llm_improve", 0.1, 0.2, "p")
        assert (canonical / "evolution_memory.jsonl").exists()
        assert not (legacy / "evolution_memory.jsonl").exists()
