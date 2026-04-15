#!/usr/bin/env python3
"""philosophy_review.py のテスト。

正常系 E2E を中心に、token cap・哲学カテゴリフィルタ・corrections 注入のスキーマを検証する。
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_test_dir = Path(__file__).resolve().parent
_skill_dir = _test_dir.parent
_script_path = _skill_dir / "scripts" / "philosophy_review.py"
_spec = importlib.util.spec_from_file_location("philosophy_review", _script_path)
philosophy_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(philosophy_review)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_principles_json(tmp_path: Path) -> Path:
    """philosophy + 既存カテゴリを混在させた principles.json"""
    data = {
        "principles": [
            {
                "id": "single-responsibility",
                "text": "各スキルは単一責務",
                "source": "seed",
                "category": "quality",
                "specificity": 0.7,
                "testability": 0.8,
                "seed": True,
            },
            {
                "id": "think-before-coding",
                "text": "曖昧なら止まって複数解釈を提示",
                "source": "karpathy-skills",
                "category": "philosophy",
                "specificity": 0.6,
                "testability": 0.5,
                "user_defined": True,
            },
            {
                "id": "simplicity-first",
                "text": "最小コードのみ",
                "source": "karpathy-skills",
                "category": "philosophy",
                "specificity": 0.7,
                "testability": 0.6,
                "user_defined": True,
            },
        ]
    }
    path = tmp_path / "principles.json"
    path.write_text(json.dumps(data))
    return path


def _make_session_jsonl(path: Path, *, lines: int = 5, msg_chars: int = 100) -> None:
    """疑似セッション jsonl を生成。user/assistant 交互。"""
    payload = []
    for i in range(lines):
        role = "user" if i % 2 == 0 else "assistant"
        msg = "x" * msg_chars
        payload.append(
            json.dumps(
                {
                    "type": role,
                    "message": {"role": role, "content": msg},
                    "sessionId": path.stem,
                    "timestamp": f"2026-04-15T00:00:0{i}Z",
                }
            )
        )
    path.write_text("\n".join(payload) + "\n")


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestLoadPhilosophyPrinciples:
    def test_filters_only_philosophy_category(self, tmp_path):
        principles_path = _make_principles_json(tmp_path)
        result = philosophy_review.load_philosophy_principles(principles_path)
        ids = {p["id"] for p in result}
        assert ids == {"think-before-coding", "simplicity-first"}

    def test_returns_empty_when_no_philosophy(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"principles": [{"id": "x", "category": "quality"}]}))
        assert philosophy_review.load_philosophy_principles(path) == []

    def test_handles_missing_file(self, tmp_path):
        assert philosophy_review.load_philosophy_principles(tmp_path / "nope.json") == []


class TestEstimateTokens:
    def test_four_chars_per_token(self):
        assert philosophy_review.estimate_tokens("x" * 400) == 100

    def test_empty_string(self):
        assert philosophy_review.estimate_tokens("") == 0


class TestExtractTranscript:
    def test_extracts_user_and_assistant_only(self, tmp_path):
        session = tmp_path / "s.jsonl"
        session.write_text(
            "\n".join(
                [
                    json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
                    json.dumps({"type": "queue-operation", "operation": "enqueue"}),
                    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "hi"}}),
                    json.dumps({"type": "attachment"}),
                ]
            )
        )
        transcript = philosophy_review.extract_transcript(session, max_tokens=10000)
        assert "hello" in transcript
        assert "hi" in transcript
        assert "queue-operation" not in transcript
        assert "attachment" not in transcript

    def test_truncates_when_over_token_cap(self, tmp_path):
        session = tmp_path / "big.jsonl"
        _make_session_jsonl(session, lines=100, msg_chars=400)  # ~100*100=10K tokens
        transcript = philosophy_review.extract_transcript(session, max_tokens=1000)
        assert philosophy_review.estimate_tokens(transcript) <= 1500  # 余裕を見たマージン
        assert "TRUNCATED" in transcript

    def test_handles_assistant_content_array(self, tmp_path):
        session = tmp_path / "s.jsonl"
        session.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "block-text"}],
                    },
                }
            )
        )
        transcript = philosophy_review.extract_transcript(session, max_tokens=10000)
        assert "block-text" in transcript


class TestInjectCorrections:
    def test_appends_to_corrections_jsonl(self, tmp_path):
        corrections = tmp_path / "corrections.jsonl"
        corrections.write_text("")
        violations = [
            {
                "session_id": "s1",
                "principle_id": "think-before-coding",
                "evidence": "曖昧な要件で着手",
                "confidence": 0.9,
            }
        ]
        n = philosophy_review.inject_corrections(violations, corrections)
        assert n == 1
        lines = corrections.read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["source"] == "philosophy-review"
        assert entry["correction_type"] == "philosophy-violation"
        assert entry["confidence"] >= 0.85
        assert entry["reflect_status"] == "pending"
        assert entry["session_id"] == "s1"
        assert "think-before-coding" in entry["message"]
        assert "曖昧な要件で着手" in entry["message"]

    def test_skips_low_confidence_violations(self, tmp_path):
        corrections = tmp_path / "c.jsonl"
        violations = [
            {"session_id": "s1", "principle_id": "x", "evidence": "e", "confidence": 0.5}
        ]
        n = philosophy_review.inject_corrections(violations, corrections)
        assert n == 0

    def test_creates_corrections_file_if_missing(self, tmp_path):
        corrections = tmp_path / "new.jsonl"
        violations = [
            {"session_id": "s", "principle_id": "p", "evidence": "e", "confidence": 0.9}
        ]
        philosophy_review.inject_corrections(violations, corrections)
        assert corrections.exists()


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------

class TestE2E:
    def _setup(self, tmp_path: Path):
        principles = _make_principles_json(tmp_path)
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        for i in range(3):
            _make_session_jsonl(sessions_dir / f"sess-{i}.jsonl", lines=4, msg_chars=50)
        corrections = tmp_path / "corrections.jsonl"
        corrections.write_text("")
        return principles, sessions_dir, corrections

    def test_e2e_success_path(self, tmp_path):
        principles, sessions_dir, corrections = self._setup(tmp_path)

        mock_response = json.dumps(
            {
                "violations": [
                    {
                        "principle_id": "think-before-coding",
                        "evidence": "曖昧なまま着手",
                        "confidence": 0.9,
                    }
                ]
            }
        )

        with mock.patch.object(philosophy_review, "_call_judge_llm", return_value=mock_response):
            result = philosophy_review.run(
                principles_path=principles,
                sessions_dir=sessions_dir,
                corrections_path=corrections,
                limit=3,
                max_tokens=10000,
                dry_run=False,
            )

        assert result["status"] == "ok"
        assert result["sessions_evaluated"] == 3
        assert result["violations_found"] == 3  # 1 violation per session × 3
        assert result["violations_injected"] == 3
        assert len(corrections.read_text().splitlines()) == 3

    def test_e2e_dry_run_no_injection(self, tmp_path):
        principles, sessions_dir, corrections = self._setup(tmp_path)
        mock_response = json.dumps(
            {"violations": [{"principle_id": "simplicity-first", "evidence": "e", "confidence": 0.9}]}
        )
        with mock.patch.object(philosophy_review, "_call_judge_llm", return_value=mock_response):
            result = philosophy_review.run(
                principles_path=principles,
                sessions_dir=sessions_dir,
                corrections_path=corrections,
                limit=3,
                max_tokens=10000,
                dry_run=True,
            )
        assert result["violations_injected"] == 0
        assert corrections.read_text() == ""  # empty

    def test_e2e_no_philosophy_principles(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"principles": [{"id": "x", "category": "quality"}]}))
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        result = philosophy_review.run(
            principles_path=path,
            sessions_dir=sessions_dir,
            corrections_path=tmp_path / "c.jsonl",
            limit=10,
            max_tokens=10000,
            dry_run=False,
        )
        assert result["status"] == "no-philosophy-principles"

    def test_e2e_no_sessions(self, tmp_path):
        principles = _make_principles_json(tmp_path)
        sessions_dir = tmp_path / "empty"
        sessions_dir.mkdir()
        result = philosophy_review.run(
            principles_path=principles,
            sessions_dir=sessions_dir,
            corrections_path=tmp_path / "c.jsonl",
            limit=10,
            max_tokens=10000,
            dry_run=False,
        )
        assert result["status"] == "no-sessions"

    def test_e2e_judge_failure_skips_session(self, tmp_path):
        principles, sessions_dir, corrections = self._setup(tmp_path)
        with mock.patch.object(philosophy_review, "_call_judge_llm", return_value=None):
            result = philosophy_review.run(
                principles_path=principles,
                sessions_dir=sessions_dir,
                corrections_path=corrections,
                limit=3,
                max_tokens=10000,
                dry_run=False,
            )
        assert result["status"] == "ok"
        assert result["sessions_evaluated"] == 3
        assert result["violations_found"] == 0
