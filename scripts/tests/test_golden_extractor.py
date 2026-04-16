"""golden_extractor.py のユニットテスト (TDD)。

GoldenCase dataclass と GoldenExtractor クラスのテスト。
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from golden_extractor import GoldenCase, GoldenExtractor

SYSTEM_CONTEXT_STUB = "# CLAUDE.md stub for testing"


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def usage_file(tmp_path):
    """テスト用 usage.jsonl — 3スキル・3セッション。"""
    path = tmp_path / "usage.jsonl"
    _write_jsonl(path, [
        # session-A: evolve を使用 (correction あり)
        {"skill_name": "evolve", "ts": "2026-04-01T10:00:00Z", "session_id": "sess-A", "file_path": "", "project": "rl-anything"},
        # session-B: reflect を使用 (correction なし → golden)
        {"skill_name": "reflect", "ts": "2026-04-02T10:00:00Z", "session_id": "sess-B", "file_path": "", "project": "rl-anything"},
        # session-C: evolve + audit を使用 (correction なし → golden)
        {"skill_name": "evolve", "ts": "2026-04-03T10:00:00Z", "session_id": "sess-C", "file_path": "some-skill", "project": "rl-anything"},
        {"skill_name": "audit", "ts": "2026-04-03T10:05:00Z", "session_id": "sess-C", "file_path": "", "project": "rl-anything"},
    ])
    return path


@pytest.fixture
def corrections_file(tmp_path):
    """テスト用 corrections.jsonl — session-A に2件の correction。"""
    path = tmp_path / "corrections.jsonl"
    _write_jsonl(path, [
        {"session_id": "sess-A", "correction_type": "style", "message": "wrong"},
        {"session_id": "sess-A", "correction_type": "logic", "message": "fix"},
    ])
    return path


@pytest.fixture
def extractor(usage_file, corrections_file):
    return GoldenExtractor(
        usage_file=usage_file,
        corrections_file=corrections_file,
        system_context=SYSTEM_CONTEXT_STUB,
    )


# ──────────────────────────────────────────────
# GoldenCase dataclass
# ──────────────────────────────────────────────

class TestGoldenCase:
    def test_creation(self):
        case = GoldenCase(
            skill_name="evolve",
            user_prompt="",
            system_context=SYSTEM_CONTEXT_STUB,
            correction_count=0,
            session_id="sess-X",
        )
        assert case.skill_name == "evolve"
        assert case.correction_count == 0

    def test_serialization(self):
        case = GoldenCase(
            skill_name="reflect",
            user_prompt="do reflect",
            system_context="ctx",
            correction_count=1,
            session_id="sess-Y",
        )
        d = asdict(case)
        assert set(d.keys()) == {"skill_name", "user_prompt", "system_context", "correction_count", "session_id"}
        assert d["correction_count"] == 1

    def test_is_golden(self):
        pos = GoldenCase("evolve", "", "ctx", 0, "sid")
        neg = GoldenCase("evolve", "", "ctx", 2, "sid")
        assert pos.correction_count == 0  # golden
        assert neg.correction_count > 0   # negative


# ──────────────────────────────────────────────
# GoldenExtractor: extract
# ──────────────────────────────────────────────

class TestGoldenExtractorExtract:
    def test_returns_list_of_golden_cases(self, extractor):
        cases = extractor.extract()
        assert all(isinstance(c, GoldenCase) for c in cases)

    def test_total_count(self, extractor):
        # 4 usage records but (sess-C, evolve) と (sess-C, audit) は同セッション異スキル → 4 unique (sid, skill) keys
        cases = extractor.extract()
        assert len(cases) == 4

    def test_correction_count_positive(self, extractor):
        """sess-A の correction_count は 2。"""
        cases = extractor.extract()
        a_cases = [c for c in cases if c.session_id == "sess-A"]
        assert len(a_cases) == 1
        assert a_cases[0].correction_count == 2

    def test_correction_count_zero_for_golden(self, extractor):
        """sess-B, sess-C は correction なし → 0。"""
        cases = extractor.extract()
        for c in cases:
            if c.session_id in ("sess-B", "sess-C"):
                assert c.correction_count == 0

    def test_system_context_populated(self, extractor):
        cases = extractor.extract()
        assert all(c.system_context == SYSTEM_CONTEXT_STUB for c in cases)

    def test_filter_by_skill_names(self, extractor):
        cases = extractor.extract(skill_names=["evolve"])
        assert all(c.skill_name == "evolve" for c in cases)
        assert len(cases) == 2  # sess-A evolve + sess-C evolve

    def test_filter_returns_empty_for_unknown_skill(self, extractor):
        cases = extractor.extract(skill_names=["nonexistent"])
        assert cases == []

    def test_user_prompt_from_file_path(self, usage_file, corrections_file):
        """user_prompt は usage.jsonl の file_path から取得される。"""
        ext = GoldenExtractor(
            usage_file=usage_file,
            corrections_file=corrections_file,
            system_context=SYSTEM_CONTEXT_STUB,
        )
        cases = ext.extract(skill_names=["evolve"])
        # sess-C evolve は file_path="some-skill"
        c_case = next(c for c in cases if c.session_id == "sess-C")
        assert c_case.user_prompt == "some-skill"


# ──────────────────────────────────────────────
# GoldenExtractor: empty / missing データ
# ──────────────────────────────────────────────

class TestGoldenExtractorEdgeCases:
    def test_empty_usage_returns_empty(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("")
        corrections = tmp_path / "corrections.jsonl"
        corrections.write_text("")
        ext = GoldenExtractor(usage_file=usage, corrections_file=corrections, system_context="ctx")
        assert ext.extract() == []

    def test_missing_usage_file_returns_empty(self, tmp_path):
        ext = GoldenExtractor(
            usage_file=tmp_path / "nonexistent.jsonl",
            corrections_file=tmp_path / "nonexistent2.jsonl",
            system_context="ctx",
        )
        assert ext.extract() == []

    def test_missing_corrections_file_treats_as_zero(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        _write_jsonl(usage, [
            {"skill_name": "evolve", "ts": "2026-04-01T00:00:00Z", "session_id": "s1", "file_path": ""},
        ])
        ext = GoldenExtractor(
            usage_file=usage,
            corrections_file=tmp_path / "no_corrections.jsonl",
            system_context="ctx",
        )
        cases = ext.extract()
        assert len(cases) == 1
        assert cases[0].correction_count == 0

    def test_malformed_lines_skipped(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text(
            '{"skill_name":"evolve","ts":"2026-04-01T00:00:00Z","session_id":"s1","file_path":""}\n'
            "NOT_JSON\n"
            '{"skill_name":"audit","ts":"2026-04-01T01:00:00Z","session_id":"s2","file_path":""}\n'
        )
        corrections = tmp_path / "corrections.jsonl"
        corrections.write_text("")
        ext = GoldenExtractor(usage_file=usage, corrections_file=corrections, system_context="ctx")
        cases = ext.extract()
        assert len(cases) == 2


# ──────────────────────────────────────────────
# GoldenExtractor: 検証
# ──────────────────────────────────────────────

class TestGoldenExtractorValidation:
    def test_validation_passes_for_valid_usage(self, usage_file, tmp_path):
        """有効な usage.jsonl で例外が発生しないこと。"""
        ext = GoldenExtractor(
            usage_file=usage_file,
            corrections_file=tmp_path / "empty.jsonl",
            system_context="ctx",
        )
        # init が成功すれば OK
        assert ext is not None

    def test_validation_fails_missing_skill_name(self, tmp_path):
        """skill_name フィールドが欠けている場合は AssertionError。"""
        usage = tmp_path / "usage.jsonl"
        _write_jsonl(usage, [
            {"ts": "2026-04-01T00:00:00Z", "session_id": "s1"},  # skill_name なし
        ])
        with pytest.raises(AssertionError, match="skill_name"):
            GoldenExtractor(
                usage_file=usage,
                corrections_file=tmp_path / "empty.jsonl",
                system_context="ctx",
            )

    def test_validation_fails_missing_session_id(self, tmp_path):
        """session_id フィールドが欠けている場合は AssertionError。"""
        usage = tmp_path / "usage.jsonl"
        _write_jsonl(usage, [
            {"skill_name": "evolve", "ts": "2026-04-01T00:00:00Z"},  # session_id なし
        ])
        with pytest.raises(AssertionError, match="session_id"):
            GoldenExtractor(
                usage_file=usage,
                corrections_file=tmp_path / "empty.jsonl",
                system_context="ctx",
            )

    def test_validation_fails_missing_ts(self, tmp_path):
        """ts フィールドが欠けている場合は AssertionError。"""
        usage = tmp_path / "usage.jsonl"
        _write_jsonl(usage, [
            {"skill_name": "evolve", "session_id": "s1"},  # ts なし
        ])
        with pytest.raises(AssertionError, match="ts"):
            GoldenExtractor(
                usage_file=usage,
                corrections_file=tmp_path / "empty.jsonl",
                system_context="ctx",
            )


# ──────────────────────────────────────────────
# GoldenExtractor: save
# ──────────────────────────────────────────────

class TestGoldenExtractorSave:
    def test_save_creates_file(self, extractor, tmp_path):
        output = tmp_path / "golden_cases.jsonl"
        cases = extractor.extract()
        extractor.save(cases, output)
        assert output.exists()

    def test_save_line_count(self, extractor, tmp_path):
        output = tmp_path / "golden_cases.jsonl"
        cases = extractor.extract()
        extractor.save(cases, output)
        lines = [l for l in output.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == len(cases)

    def test_save_valid_jsonl(self, extractor, tmp_path):
        output = tmp_path / "golden_cases.jsonl"
        cases = extractor.extract()
        extractor.save(cases, output)
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                assert "skill_name" in rec
                assert "correction_count" in rec
                assert "session_id" in rec
                assert "system_context" in rec
                assert "user_prompt" in rec

    def test_save_creates_parent_dirs(self, extractor, tmp_path):
        output = tmp_path / "subdir" / "nested" / "golden_cases.jsonl"
        cases = extractor.extract()
        extractor.save(cases, output)
        assert output.exists()

    def test_save_empty_list(self, tmp_path):
        usage = tmp_path / "usage.jsonl"
        usage.write_text("")
        ext = GoldenExtractor(usage_file=usage, corrections_file=tmp_path / "c.jsonl", system_context="ctx")
        output = tmp_path / "out.jsonl"
        ext.save([], output)
        assert output.exists()
        assert output.read_text().strip() == ""
