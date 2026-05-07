"""_emit_pending_trigger() のユニットテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_HOOKS = Path(__file__).resolve().parent.parent
_LIB = _HOOKS.parent / "scripts" / "lib"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_LIB))

import common
import instructions_loaded


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)
    return tmp_path


def _call_with_payload(payload, data_dir):
    """read_and_delete_pending_trigger を stub して _emit_pending_trigger を呼ぶ。"""
    trigger_mod = mock.MagicMock()
    trigger_mod.read_and_delete_pending_trigger.return_value = payload
    with mock.patch.dict("sys.modules", {"trigger_engine": trigger_mod}):
        instructions_loaded._emit_pending_trigger()


# ─── pending trigger なし ────────────────────────────────────────

def test_no_output_when_no_pending_trigger(data_dir, capsys):
    _call_with_payload(None, data_dir)
    assert "AUTO_EVOLVE_TRIGGER" not in capsys.readouterr().out


def test_no_output_when_import_fails(data_dir, capsys):
    with mock.patch.dict("sys.modules", {"trigger_engine": None}):
        instructions_loaded._emit_pending_trigger()
    assert "AUTO_EVOLVE_TRIGGER" not in capsys.readouterr().out


# ─── pending trigger あり ────────────────────────────────────────

def test_outputs_trigger_message(data_dir, capsys):
    payload = {"message": "evolve 推奨", "action": "/rl-anything:evolve"}
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "AUTO_EVOLVE_TRIGGER" in out
    assert "evolve 推奨" in out


def test_outputs_recommended_command(data_dir, capsys):
    payload = {"message": "msg", "action": "/rl-anything:audit"}
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "/rl-anything:audit" in out


# ─── triage cache あり ───────────────────────────────────────────

def test_appends_candidate_from_cache(data_dir, capsys):
    payload = {"message": "推奨evolve候補はセッション開始時に表示されます。", "action": "/rl-anything:evolve"}
    cache = {
        "candidates": [
            {"skill": "audit", "action": "UPDATE", "confidence": 0.85, "reason": "使用頻度が高い"},
        ],
        "generated_at": "2026-05-05T00:00:00Z",
    }
    (data_dir / "skill-triage-cache.json").write_text(json.dumps(cache))
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "audit" in out
    assert "0.85" in out


def test_cache_file_deleted_after_read(data_dir, capsys):
    payload = {"message": "msg", "action": "/rl-anything:evolve"}
    cache = {"candidates": [{"skill": "evolve", "action": "UPDATE", "confidence": 0.9, "reason": ""}], "generated_at": ""}
    triage_file = data_dir / "skill-triage-cache.json"
    triage_file.write_text(json.dumps(cache))
    _call_with_payload(payload, data_dir)
    assert not triage_file.exists()


def test_tolerates_corrupt_cache_file(data_dir, capsys):
    payload = {"message": "msg", "action": "/rl-anything:evolve"}
    (data_dir / "skill-triage-cache.json").write_text("not-json")
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "AUTO_EVOLVE_TRIGGER" in out  # still outputs the base message


def test_tolerates_missing_cache_file(data_dir, capsys):
    payload = {"message": "msg", "action": "/rl-anything:evolve"}
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "AUTO_EVOLVE_TRIGGER" in out


def test_multiple_candidates_shown(data_dir, capsys):
    payload = {"message": "推奨evolve候補はセッション開始時に表示されます。", "action": "/rl-anything:evolve"}
    cache = {
        "candidates": [
            {"skill": "evolve", "action": "UPDATE", "confidence": 0.9, "reason": ""},
            {"skill": "audit", "action": "UPDATE", "confidence": 0.8, "reason": ""},
            {"skill": "reflect", "action": "UPDATE", "confidence": 0.7, "reason": ""},
        ],
        "generated_at": "",
    }
    (data_dir / "skill-triage-cache.json").write_text(json.dumps(cache))
    _call_with_payload(payload, data_dir)
    out = capsys.readouterr().out
    assert "上位候補" in out
