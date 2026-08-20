"""reward_ema Agent 混入是正 CLI の契約テスト（#480）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(LIB))

import reward_ema_cleanup_cli as cleanup


def _write_records(path: Path) -> bytes:
    body = (
        '{"pj_slug":"p","skill":"evolve","ema":0.1,"n_batches":2}\n'
        '{"pj_slug":"p","skill":"Agent:general-purpose","ema":0.9,"n_batches":8}\n'
        '{"pj_slug":"q","skill":"Agent:impl-worker","ema":-0.2,"n_batches":3}\n'
        '{"pj_slug":"p","skill":"review","ema":0.4,"n_batches":1}\n'
    ).encode()
    path.write_bytes(body)
    return body


def test_scan_uses_shared_agent_label_predicate(tmp_path):
    target = tmp_path / "reward_ema.jsonl"
    _write_records(target)
    with mock.patch.object(cleanup, "is_agent_skill_label", wraps=cleanup.is_agent_skill_label) as pred:
        result = cleanup.inspect_file(target)
    assert result.total_records == 4
    assert result.removed_records == 2
    assert result.skills == {"Agent:general-purpose": 1, "Agent:impl-worker": 1}
    assert pred.call_count == 4


def test_default_is_dry_run_and_does_not_write_any_file(tmp_path, capsys):
    target = tmp_path / "reward_ema.jsonl"
    before = _write_records(target)
    before_names = sorted(p.name for p in tmp_path.iterdir())

    assert cleanup.main(["--path", str(target)]) == 0

    assert target.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == before_names
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "2 / 4 件" in out
    assert "Agent:general-purpose: 1 件" in out
    assert "Agent:impl-worker: 1 件" in out
    assert "is_agent_skill_label" in out


def test_apply_backs_up_then_removes_only_agent_records(tmp_path, capsys):
    target = tmp_path / "reward_ema.jsonl"
    before = _write_records(target)

    assert cleanup.main(["--path", str(target), "--apply"]) == 0

    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert [r["skill"] for r in records] == ["evolve", "review"]
    backups = list(tmp_path.glob("reward_ema.jsonl.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    out = capsys.readouterr().out
    assert "バックアップ:" in out
    assert "復元:" in out
    assert "cp --" in out


def test_apply_refuses_to_replace_when_backup_creation_fails(tmp_path):
    target = tmp_path / "reward_ema.jsonl"
    before = _write_records(target)
    with mock.patch.object(cleanup, "_create_backup_locked", side_effect=OSError("disk full")):
        assert cleanup.main(["--path", str(target), "--apply"]) == 1
    assert target.read_bytes() == before


def test_apply_uses_shared_lock_and_atomic_write_primitives(tmp_path):
    target = tmp_path / "reward_ema.jsonl"
    _write_records(target)
    with (
        mock.patch.object(cleanup, "file_lock", wraps=cleanup.file_lock) as lock,
        mock.patch.object(cleanup, "atomic_write_text", wraps=cleanup.atomic_write_text) as atomic,
    ):
        result = cleanup.apply_cleanup(target)
    assert result.removed_records == 2
    assert lock.call_count == 1
    assert atomic.call_count == 2  # 原本バックアップ、その後に正本置換


def test_apply_with_no_matches_is_write_free(tmp_path):
    target = tmp_path / "reward_ema.jsonl"
    target.write_text('{"skill":"evolve"}\n')
    assert cleanup.main(["--path", str(target), "--apply"]) == 0
    assert target.read_text() == '{"skill":"evolve"}\n'
    assert list(tmp_path.glob("*.backup-*")) == []


def test_malformed_json_fails_closed_without_writes(tmp_path, capsys):
    target = tmp_path / "reward_ema.jsonl"
    target.write_text('{"skill":"Agent:x"}\nnot-json\n')
    before = target.read_bytes()
    assert cleanup.main(["--path", str(target), "--apply"]) == 1
    assert target.read_bytes() == before
    assert list(tmp_path.glob("*.backup-*")) == []
    assert "JSON" in capsys.readouterr().err


def test_bin_wrapper_defaults_to_canonical_home_path(tmp_path):
    data_dir = tmp_path / ".claude" / "evolve-anything"
    data_dir.mkdir(parents=True)
    target = data_dir / "reward_ema.jsonl"
    before = _write_records(target)
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    proc = subprocess.run(
        [str(ROOT / "bin" / "evolve-reward-ema-cleanup")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.read_bytes() == before
    assert "2 / 4 件" in proc.stdout


@pytest.mark.parametrize("value", ["Agent", "agent:x", "Skill:Agent:x", " Agent:x"])
def test_near_miss_labels_are_preserved(tmp_path, value):
    target = tmp_path / "reward_ema.jsonl"
    target.write_text(json.dumps({"skill": value}) + "\n")
    result = cleanup.inspect_file(target)
    assert result.removed_records == 0
