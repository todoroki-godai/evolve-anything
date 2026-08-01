"""audit の icebox_reconcile observability セクションテスト（#352）。

icebox-verdicts.json を読むだけ（gh を呼ばない）。当PJが evolve-anything 本体
（`.claude-plugin/plugin.json` あり）でなければ沈黙。ファイル無し/評価対象0件も沈黙。
観測器不在（レーン2）・失効候補（レーン3）を advisory 表示する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.sections_icebox_reconcile import build_icebox_reconcile_section  # noqa: E402


def _self_project(tmp_path: Path) -> Path:
    proj = tmp_path / "project"
    (proj / ".claude-plugin").mkdir(parents=True)
    (proj / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    return proj


def _other_project(tmp_path: Path) -> Path:
    proj = tmp_path / "other"
    proj.mkdir(parents=True)
    return proj


def _write_verdicts(data_dir: Path, payload: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "icebox-verdicts.json").write_text(json.dumps(payload), encoding="utf-8")


def _verdict(number, lane, reason="reason"):
    return {"number": number, "lane": lane, "reason": reason, "value": None}


def test_silent_for_non_self_project(tmp_path, monkeypatch):
    proj = _other_project(tmp_path)
    data_dir = tmp_path / "data"
    _write_verdicts(data_dir, {"generated_at": "2026-08-01T00:00:00Z", "verdicts": [_verdict(1, "met")]})
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    assert build_icebox_reconcile_section(proj) is None


def test_silent_when_no_verdicts_file(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    assert build_icebox_reconcile_section(proj) is None


def test_silent_when_verdicts_list_empty(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    _write_verdicts(data_dir, {"generated_at": "2026-08-01T00:00:00Z", "verdicts": []})
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    assert build_icebox_reconcile_section(proj) is None


def test_shows_observer_missing_and_archive_candidates(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    _write_verdicts(
        data_dir,
        {
            "generated_at": "2026-08-01T00:00:00Z",
            "verdicts": [
                _verdict(1, "observer_missing", reason="source='x' 未実装"),
                _verdict(2, "archive_candidate", reason="凍結から200日経過"),
                _verdict(3, "met", reason="成立しました"),
                _verdict(4, None, reason="未成立"),
            ],
        },
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    lines = build_icebox_reconcile_section(proj)
    assert lines is not None
    body = "\n".join(lines)
    assert lines[0].startswith("## ")
    assert lines[-1] == ""
    assert "#1" in body and "source='x' 未実装" in body
    assert "#2" in body and "凍結から200日経過" in body
    assert "1件" in body  # 成立


def test_clean_when_no_observer_missing_or_archive(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    _write_verdicts(
        data_dir,
        {"generated_at": "2026-08-01T00:00:00Z", "verdicts": [_verdict(1, "met")]},
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    lines = build_icebox_reconcile_section(proj)
    body = "\n".join(lines)
    assert "✓" in body


def test_stale_generated_at_shown(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    _write_verdicts(
        data_dir,
        {
            "generated_at": "2020-01-01T00:00:00Z",
            "verdicts": [_verdict(1, "observer_missing")],
        },
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    lines = build_icebox_reconcile_section(proj)
    body = "\n".join(lines)
    assert "日経過" in body


def test_malformed_verdicts_file_is_silent(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "icebox-verdicts.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    assert build_icebox_reconcile_section(proj) is None


def test_truncates_long_lists(tmp_path, monkeypatch):
    proj = _self_project(tmp_path)
    data_dir = tmp_path / "data"
    verdicts = [_verdict(i, "observer_missing", reason=f"r{i}") for i in range(15)]
    _write_verdicts(data_dir, {"generated_at": "2026-08-01T00:00:00Z", "verdicts": verdicts})
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    lines = build_icebox_reconcile_section(proj)
    body = "\n".join(lines)
    assert "他 5 件" in body
