"""既知4指紋限定Codex設定修復の契約テスト（#268）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from agent_coordination.codex_cleanup import (  # noqa: E402
    apply_plan,
    audit,
    write_plan,
)
from agent_coordination.core import CoordinationError  # noqa: E402


def _config(tmp_path: Path) -> Path:
    root = tmp_path / ".codex"
    agents = root / "agents"
    agents.mkdir(parents=True)
    (root / "AGENTS.md").write_text(
        "read ~/.Codex/x and .Codex-plugin/plugin.json\n"
        "run Codex plugin validate in Codex Code\n",
        encoding="utf-8",
    )
    (agents / "safe.toml").write_text('name = "safe"\n', encoding="utf-8")
    return root


def test_audit_is_read_only_and_only_reports_known_fingerprints(tmp_path: Path) -> None:
    root = _config(tmp_path)
    before = (root / "AGENTS.md").read_bytes()
    report = audit(root)
    assert report["finding_count"] == 4
    assert (root / "AGENTS.md").read_bytes() == before
    assert report["files"][0]["findings"]["missing_codex_validate"] == 1


def test_plan_then_apply_requires_yes_and_creates_hash_backup(tmp_path: Path) -> None:
    root = _config(tmp_path)
    plan_path = tmp_path / "plan.json"
    plan = write_plan(root, plan_path)
    assert len(plan["changes"]) == 1
    with pytest.raises(CoordinationError, match="--yes"):
        apply_plan(plan_path, confirmed=False)
    result = apply_plan(plan_path, confirmed=True)
    updated = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "~/.codex/" in updated
    assert ".claude-plugin" in updated
    assert "claude plugin validate" in updated
    assert "Codex Code" not in updated
    backup = Path(result["applied"][0]["backup"])
    assert backup.exists()
    assert "Codex Code" in backup.read_text(encoding="utf-8")


def test_apply_rejects_stale_plan_without_modifying_any_file(tmp_path: Path) -> None:
    root = _config(tmp_path)
    second = root / "agents" / "bad.toml"
    second.write_text('path = "~/.Codex/x"\n', encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    write_plan(root, plan_path)
    original_first = (root / "AGENTS.md").read_text(encoding="utf-8")
    second.write_text('path = "~/.Codex/changed"\n', encoding="utf-8")
    with pytest.raises(CoordinationError, match="plan作成後"):
        apply_plan(plan_path, confirmed=True)
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == original_first
    assert not list(root.rglob("*.bak.*"))


def test_plan_is_machine_readable(tmp_path: Path) -> None:
    root = _config(tmp_path)
    path = tmp_path / "plan.json"
    write_plan(root, path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 1
    assert loaded["changes"][0]["before_sha256"]
