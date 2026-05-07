"""skill_triage_runner.py のユニットテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_LIB))

import skill_triage_runner


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", tmp_path / "skill-triage-cache.json")


# ─── _load_jsonl ────────────────────────────────────────────────

def test_load_jsonl_returns_records(tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_text('{"a": 1}\n{"b": 2}\n')
    assert skill_triage_runner._load_jsonl(f) == [{"a": 1}, {"b": 2}]


def test_load_jsonl_tolerates_corrupt_lines(tmp_path):
    f = tmp_path / "test.jsonl"
    f.write_text('{"ok": 1}\nnot-json\n{"ok": 2}\n')
    assert skill_triage_runner._load_jsonl(f) == [{"ok": 1}, {"ok": 2}]


def test_load_jsonl_missing_file(tmp_path):
    assert skill_triage_runner._load_jsonl(tmp_path / "missing.jsonl") == []


# ─── run() ──────────────────────────────────────────────────────

def _mock_triage_result(update_skills=None, create_skills=None):
    result = {}
    if update_skills:
        result["UPDATE"] = [
            {"skill": s, "confidence": 0.8, "reason": "high usage"} for s in update_skills
        ]
    if create_skills:
        result["CREATE"] = [
            {"skill": s, "confidence": 0.7, "reason": "pattern found"} for s in create_skills
        ]
    return result


def test_run_writes_cache_with_candidates(tmp_path, monkeypatch):
    mock_result = _mock_triage_result(update_skills=["evolve", "audit"], create_skills=["new-skill"])
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    cache_file = tmp_path / "skill-triage-cache.json"
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", cache_file)

    with mock.patch.dict("sys.modules", {
        "skill_triage": mock.MagicMock(triage_all_skills=mock.MagicMock(return_value=mock_result)),
        "session_store": mock.MagicMock(query=mock.MagicMock(return_value=[])),
    }):
        skill_triage_runner.run()

    assert cache_file.exists()
    cache = json.loads(cache_file.read_text())
    assert "candidates" in cache
    assert len(cache["candidates"]) > 0
    # confidence 降順ソートを確認
    confidences = [c["confidence"] for c in cache["candidates"]]
    assert confidences == sorted(confidences, reverse=True)


def test_run_caps_candidates_at_5(tmp_path, monkeypatch):
    many_skills = [f"skill-{i}" for i in range(10)]
    mock_result = _mock_triage_result(update_skills=many_skills)
    cache_file = tmp_path / "skill-triage-cache.json"
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", cache_file)

    with mock.patch.dict("sys.modules", {
        "skill_triage": mock.MagicMock(triage_all_skills=mock.MagicMock(return_value=mock_result)),
        "session_store": mock.MagicMock(query=mock.MagicMock(return_value=[])),
    }):
        skill_triage_runner.run()

    cache = json.loads(cache_file.read_text())
    assert len(cache["candidates"]) <= 5


def test_run_writes_nothing_when_no_candidates(tmp_path, monkeypatch):
    cache_file = tmp_path / "skill-triage-cache.json"
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", cache_file)

    with mock.patch.dict("sys.modules", {
        "skill_triage": mock.MagicMock(triage_all_skills=mock.MagicMock(return_value={})),
        "session_store": mock.MagicMock(query=mock.MagicMock(return_value=[])),
    }):
        skill_triage_runner.run()

    assert not cache_file.exists()


def test_run_writes_error_log_on_triage_failure(tmp_path, monkeypatch):
    cache_file = tmp_path / "skill-triage-cache.json"
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", cache_file)

    with mock.patch.dict("sys.modules", {
        "skill_triage": mock.MagicMock(triage_all_skills=mock.MagicMock(side_effect=RuntimeError("triage boom"))),
        "session_store": mock.MagicMock(query=mock.MagicMock(return_value=[])),
    }):
        skill_triage_runner.run()  # should not raise

    assert not cache_file.exists()
    error_log = tmp_path / "skill-triage-runner-error.log"
    assert error_log.exists()
    assert "triage boom" in error_log.read_text()


def test_run_silently_returns_when_imports_fail(monkeypatch):
    with mock.patch.dict("sys.modules", {"skill_triage": None, "session_store": None}):
        skill_triage_runner.run()  # should not raise


def test_cache_file_written_atomically(tmp_path, monkeypatch):
    """os.replace を使ったアトミック書き込みを確認する。"""
    cache_file = tmp_path / "skill-triage-cache.json"
    monkeypatch.setattr(skill_triage_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skill_triage_runner, "TRIAGE_CACHE_FILE", cache_file)

    mock_result = _mock_triage_result(update_skills=["evolve"])
    replaced = []
    real_replace = __import__("os").replace

    def tracking_replace(src, dst):
        replaced.append((src, dst))
        real_replace(src, dst)

    with mock.patch("os.replace", side_effect=tracking_replace):
        with mock.patch.dict("sys.modules", {
            "skill_triage": mock.MagicMock(triage_all_skills=mock.MagicMock(return_value=mock_result)),
            "session_store": mock.MagicMock(query=mock.MagicMock(return_value=[])),
        }):
            skill_triage_runner.run()

    assert len(replaced) == 1
    src, dst = replaced[0]
    assert str(dst) == str(cache_file)
    assert src != dst
