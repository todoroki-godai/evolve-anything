"""prune のリファクタ防御スナップショットテスト。

Phase 4 (prune.py 1411 行 → prune/ パッケージ分割) で
prune の公開 API surface が変わらないことを byte レベルで保証する。

fixture 更新は `UPDATE_SNAPSHOTS=1 pytest scripts/tests/test_prune_snapshot.py` で。
"""
import inspect
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import prune  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _collect_api_surface() -> str:
    lines = ["# prune module constants"]
    consts = {}
    for name in dir(prune):
        if name.startswith("_") or name == "TYPE_CHECKING":
            continue
        val = getattr(prune, name)
        if isinstance(val, (int, float, str, bool, tuple)) and not callable(val):
            consts[name] = val
    for name in sorted(consts):
        lines.append(f"{name} = {consts[name]!r}")
    lines.append("")
    lines.append("# prune public function / class signatures")
    members = []
    for name in dir(prune):
        if name.startswith("_"):
            continue
        obj = getattr(prune, name)
        mod = getattr(obj, "__module__", "")
        if callable(obj) and (mod == "prune" or mod.startswith("prune.")):
            members.append(name)
    for name in sorted(members):
        obj = getattr(prune, name)
        try:
            sig = inspect.signature(obj)
            lines.append(f"{name}{sig}")
        except (TypeError, ValueError):
            lines.append(f"{name} (no signature)")
    return "\n".join(lines) + "\n"


def _assert_snapshot(actual: str, fixture_name: str) -> None:
    fixture = _FIXTURES / fixture_name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        _FIXTURES.mkdir(exist_ok=True)
        fixture.write_text(actual)
        return
    assert fixture.exists(), (
        f"fixture missing: {fixture}. "
        f"Initial run requires UPDATE_SNAPSHOTS=1 pytest."
    )
    expected = fixture.read_text()
    assert actual == expected, (
        f"Snapshot mismatch ({fixture.name}). "
        f"If intentional, regenerate with UPDATE_SNAPSHOTS=1 pytest."
    )


def test_prune_api_surface_snapshot():
    """公開関数/クラスシグネチャ + 定数値の dump。

    Phase 4 (prune/ パッケージ分割) で公開 API が変わったら検知する。
    外部 importer (hooks/tests / scripts/tests / skills/* 等) の
    `from prune import X` 互換性を保証する SoT。
    """
    actual = _collect_api_surface()
    _assert_snapshot(actual, "prune_api_surface.txt")


# ─── _enrich_candidate: created_at / age_days フィールドのテスト ───────────

def test_enrich_candidate_includes_created_at_from_git(tmp_path):
    """`_enrich_candidate` が git リポジトリで `created_at` を返す。"""
    import subprocess
    from unittest import mock
    from prune.skill_inspect import _enrich_candidate

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: my-skill\ndescription: test\n---\n# test\n")

    fake_date = "2026-02-15"

    def fake_run(cmd, **kwargs):
        r = mock.MagicMock()
        r.returncode = 0
        r.stdout = f"{fake_date}\n"
        return r

    with mock.patch("prune.skill_inspect.subprocess.run", side_effect=fake_run):
        result = _enrich_candidate({"file": str(skill_md), "skill_name": "my-skill",
                                    "reason": "zero_invocation", "days": 30})

    assert result["created_at"] == fake_date
    assert isinstance(result["age_days"], int)
    assert result["age_days"] >= 0


def test_enrich_candidate_created_at_none_on_git_failure(tmp_path):
    """`_enrich_candidate` が git コマンド失敗時に `created_at=None`, `age_days=None` を返す。"""
    from unittest import mock
    from prune.skill_inspect import _enrich_candidate

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: my-skill\ndescription: test\n---\n")

    def fail_run(*args, **kwargs):
        raise OSError("git not found")

    with mock.patch("prune.skill_inspect.subprocess.run", side_effect=fail_run):
        result = _enrich_candidate({"file": str(skill_md), "skill_name": "my-skill",
                                    "reason": "zero_invocation", "days": 30})

    assert result["created_at"] is None
    assert result["age_days"] is None


def test_enrich_candidate_includes_invocation_count_and_last_used(tmp_path):
    """`_enrich_candidate` が `invocation_count: 0` と `last_used: null` を含む。"""
    from unittest import mock
    from prune.skill_inspect import _enrich_candidate

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: my-skill\ndescription: test\n---\n")

    with mock.patch("prune.skill_inspect.subprocess.run", return_value=mock.MagicMock(returncode=1, stdout="")):
        result = _enrich_candidate({"file": str(skill_md), "skill_name": "my-skill",
                                    "reason": "zero_invocation", "days": 30})

    assert "invocation_count" in result
    assert result["invocation_count"] == 0
    assert "last_used" in result
    assert result["last_used"] is None
