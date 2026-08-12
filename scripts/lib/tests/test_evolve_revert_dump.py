"""evolve_revert._dump のユニットテスト（#402 段階3 §2 手順3 / C14, C15）。

``--dump-before``: revert を実行せず before 本文を指定パスへ取り出すだけの操作。
``--apply`` と排他（CLI 側で強制・stage4）。出力先が既存なら既定で拒否。publish は
atomic no-clobber（``os.link``）。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decision_ids as ids  # noqa: E402
import optimize_history_store as store  # noqa: E402
from evolve_revert._dump import dump_before  # noqa: E402


def _write(dir_: Path, slug: str, records: list) -> None:
    oh = dir_ / "optimize_history"
    oh.mkdir(parents=True, exist_ok=True)
    (oh / f"{slug}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _accept_entry(before_text: str, **overrides):
    b64, _ = ids._compress_before_for_revert(before_text)
    base = {
        "id": "x1",
        "human_accepted": True,
        "revert_before_b64": b64,
        "scope": "project",
        "repo_id": None,
        "relative_path": None,
    }
    base.update(overrides)
    return base


def _setup_history(tmp_path, monkeypatch, record):
    canonical = tmp_path / "evolve-anything"
    (canonical / "optimize_history").mkdir(parents=True)
    monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")
    _write(canonical, "proj", [record])


def test_dumps_before_content_to_dest(tmp_path, monkeypatch):
    _setup_history(tmp_path, monkeypatch, _accept_entry("# before\n本文\n"))
    dest = tmp_path / "out" / "before.md"

    result = dump_before("x1", dest, slug="proj")

    assert result.ok is True
    assert dest.read_text(encoding="utf-8") == "# before\n本文\n"


def test_entry_not_found_is_rejected(tmp_path, monkeypatch):
    _setup_history(tmp_path, monkeypatch, _accept_entry("x"))
    dest = tmp_path / "out.md"

    result = dump_before("nope", dest, slug="proj")

    assert result.ok is False
    assert result.reason == "entry_not_found"
    assert not dest.exists()


def test_before_unavailable_is_rejected(tmp_path, monkeypatch):
    entry = _accept_entry("x")
    del entry["revert_before_b64"]
    entry["revert_unavailable_reason"] = ids.REVERT_REASON_BEFORE_TOO_LARGE
    _setup_history(tmp_path, monkeypatch, entry)
    dest = tmp_path / "out.md"

    result = dump_before("x1", dest, slug="proj")

    assert result.ok is False
    assert result.reason == "before_unavailable"


def test_existing_dest_is_rejected_by_default(tmp_path, monkeypatch):
    _setup_history(tmp_path, monkeypatch, _accept_entry("new-content"))
    dest = tmp_path / "out.md"
    dest.write_text("existing-content-must-survive", encoding="utf-8")

    result = dump_before("x1", dest, slug="proj")

    assert result.ok is False
    assert result.reason == "dest_exists"
    assert dest.read_text(encoding="utf-8") == "existing-content-must-survive"


def test_dest_equal_to_target_path_is_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "skills").mkdir(parents=True)
    target = repo / "skills" / "SKILL.md"
    target.write_text("current", encoding="utf-8")
    _setup_history(
        tmp_path, monkeypatch,
        _accept_entry("before-content", repo_id=str(repo), relative_path="skills/SKILL.md"),
    )

    result = dump_before("x1", target, slug="proj")

    assert result.ok is False
    assert result.reason == "dest_is_target"
    assert target.read_text(encoding="utf-8") == "current"  # 対象を壊していない


def test_publish_is_atomic_no_clobber_not_check_then_replace(tmp_path, monkeypatch):
    """C15: 「存在しないことを確認 → os.replace」でなく os.link による atomic
    no-clobber publish。確認後に作られたファイルを上書きしない不変条件をレース模擬で
    確認する。"""
    _setup_history(tmp_path, monkeypatch, _accept_entry("before-content"))
    dest = tmp_path / "out.md"

    import evolve_revert._dump as dump_module

    real_link = os.link
    call_state = {"n": 0}

    def _racy_link(src, dst, *a, **kw):
        # 「不在確認」の直後（publish 直前）に別プロセスが dest を作った、を模擬。
        call_state["n"] += 1
        if call_state["n"] == 1:
            Path(dst).write_text("raced-in-content", encoding="utf-8")
        return real_link(src, dst, *a, **kw)

    monkeypatch.setattr(dump_module.os, "link", _racy_link)

    result = dump_before("x1", dest, slug="proj")

    assert result.ok is False
    assert result.reason == "dest_exists"
    assert dest.read_text(encoding="utf-8") == "raced-in-content"  # 上書きしていない


def test_no_partial_file_left_on_failure(tmp_path, monkeypatch):
    _setup_history(tmp_path, monkeypatch, _accept_entry("before-content"))
    dest = tmp_path / "out.md"
    dest.write_text("pre-existing", encoding="utf-8")

    dump_before("x1", dest, slug="proj")

    # temp（.<name>.<uuid>.tmp）が残っていない。
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".out.md.")]
    assert leftovers == []
