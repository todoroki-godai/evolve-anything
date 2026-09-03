"""#595 corrections.jsonl rewrite writer の共有ロック契約。"""
import ast
import importlib
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
for import_root in (ROOT / "scripts", ROOT / "scripts/lib", ROOT / "skills/reflect/scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

WRITERS = (
    ("skills/reflect/scripts/reflect.py", "update_reflect_status"),
    ("scripts/lib/correction_semantic/promote.py", "invalidate_idiom_corrections"),
    ("scripts/lib/prune/corrections.py", "cleanup_corrections"),
    ("scripts/migrate_reflect_promoted_status.py", "migrate"),
    (
        "scripts/lib/corrections_subagent_invalidation.py",
        "invalidate_subagent_contaminated_corrections",
    ),
    ("scripts/migrate_correction_id_backfill.py", "migrate"),
    ("scripts/lib/backfill_turn_indices.py", "backfill_corrections"),
    ("scripts/lib/pj_slug_backfill.py", "backfill"),
)

LOSS_GUARDS = (
    ("skills/reflect/scripts/reflect.py", "update_reflect_status"),
    ("scripts/lib/correction_semantic/promote.py", "invalidate_idiom_corrections"),
    ("scripts/lib/prune/corrections.py", "cleanup_corrections"),
    ("scripts/migrate_reflect_promoted_status.py", "_migrate_text"),
    ("scripts/lib/corrections_subagent_invalidation.py", "invalidate_subagent_contaminated_corrections"),
    ("scripts/migrate_correction_id_backfill.py", "_migrate_unlocked"),
    ("scripts/lib/backfill_turn_indices.py", "_backfill_corrections_unlocked"),
    ("scripts/lib/pj_slug_backfill.py", "_backfill_jsonl"),
)


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


@pytest.mark.parametrize("relative_path,function_name", WRITERS)
def test_rewrite_writer_has_shared_lock_region(relative_path, function_name):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    lock_regions = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.With)
        and any(
            _call_name(item.context_expr) == "corrections_write_lock"
            for item in node.items
        )
    ]
    assert lock_regions, f"{relative_path}:{function_name} has no corrections_write_lock"

    # ロックが飾りにならず、実際の読取/変換処理を内包することを固定する。
    calls_inside = {
        name
        for region in lock_regions
        for node in ast.walk(region)
        if (name := _call_name(node)) is not None
    }
    assert calls_inside & {
        "read_text",
        "open",
        "_load_jsonl",
        "_backfill_jsonl",
        "_backfill_corrections_unlocked",
        "_migrate_unlocked",
        "_migrate_text",
    }, f"{relative_path}:{function_name} lock does not contain the read/transform path"


@pytest.mark.parametrize("relative_path,function_name", LOSS_GUARDS)
def test_rewrite_writer_has_content_loss_guard(relative_path, function_name):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    calls = {
        _call_name(node) for node in ast.walk(function) if isinstance(node, ast.Call)
    }
    assert "assert_no_unexpected_content_loss" in calls


def _write_jsonl(path: Path, records):
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "case",
    ("reflect", "idiom", "prune", "reflect_migration", "subagent", "id_migration",
     "turn_index", "pj_slug"),
)
def test_runtime_order_is_lock_read_write_unlock(case, tmp_path, monkeypatch):
    events: list[str] = []
    target = tmp_path / "corrections.jsonl"

    if case == "reflect":
        module = importlib.import_module("reflect")
        record = {"correction_id": "1" * 32, "reflect_status": "pending"}
        _write_jsonl(target, [record])
        invoke = lambda: module.update_reflect_status(
            target, [module.UpdateTarget(0, ("id", "1" * 32))], "skipped"
        )
    elif case == "idiom":
        module = importlib.import_module("correction_semantic.promote")
        _write_jsonl(target, [{"correction_id": "2" * 32, "promoted_by": "idiom_dict",
                              "idiom_key": "k"}])
        invoke = lambda: module.invalidate_idiom_corrections(
            {"k"}, corrections_path=target, dry_run=False
        )
    elif case == "prune":
        module = importlib.import_module("prune.corrections")
        _write_jsonl(target, [{"correction_id": "3" * 32, "reflect_status": "applied",
                              "timestamp": "2000-01-01T00:00:00+00:00", "decay_days": 1}])
        monkeypatch.setattr(importlib.import_module("prune"), "DATA_DIR", tmp_path)
        invoke = lambda: module.cleanup_corrections(dry_run=False)
    elif case == "reflect_migration":
        module = importlib.import_module("migrate_reflect_promoted_status")
        _write_jsonl(target, [{"correction_id": "4" * 32, "source": "reflect_confirmed",
                              "reflect_status": "applied"}])
        invoke = lambda: module.migrate(target, dry_run=False)
    elif case == "subagent":
        module = importlib.import_module("corrections_subagent_invalidation")
        _write_jsonl(target, [{"correction_id": "5" * 32,
                              "weak_signal_provenance": {"source_path": "/subagents/x"}}])
        invoke = lambda: module.invalidate_subagent_contaminated_corrections(
            target, dry_run=False
        )
    elif case == "id_migration":
        module = importlib.import_module("migrate_correction_id_backfill")
        _write_jsonl(target, [{"message": "legacy"}])
        invoke = lambda: module.migrate(target, dry_run=False)
    elif case == "turn_index":
        module = importlib.import_module("backfill_turn_indices")
        projects = tmp_path / "projects" / "p"
        projects.mkdir(parents=True)
        _write_jsonl(projects / "s.jsonl", [{"type": "user", "timestamp": "2026-01-01T00:00:00Z"}])
        _write_jsonl(target, [{"correction_id": "6" * 32, "session_id": "s",
                              "timestamp": "2026-01-02T00:00:00Z"}])
        invoke = lambda: module.backfill_corrections(
            target, tmp_path / "sessions.jsonl", tmp_path / "projects", dry_run=False
        )
    else:
        module = importlib.import_module("pj_slug_backfill")
        _write_jsonl(target, [{"correction_id": "7" * 32, "project_path": "/tmp/project"}])
        invoke = lambda: module.backfill(tmp_path, apply=True)

    @contextmanager
    def lock(_path):
        assert Path(_path).resolve() == target.resolve()
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")

    monkeypatch.setattr(module, "corrections_write_lock", lock)
    real_read = Path.read_text

    def read_spy(path, *args, **kwargs):
        if Path(path).resolve() == target.resolve():
            events.append("read")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_spy)
    if case == "id_migration":
        real_replace = module.os.replace

        def replace_spy(source, destination):
            if Path(destination).resolve() == target.resolve():
                events.append("write")
            return real_replace(source, destination)

        monkeypatch.setattr(module.os, "replace", replace_spy)
    else:
        real_write = module.atomic_write_text_preserving_mode

        def write_spy(path, text):
            if Path(path).resolve() == target.resolve():
                events.append("write")
            return real_write(path, text)

        monkeypatch.setattr(module, "atomic_write_text_preserving_mode", write_spy)

    invoke()

    assert events[0] == "lock_enter", events
    assert "read" in events and "write" in events, events
    assert events.index("lock_enter") < events.index("read") < events.index("write")
    assert events.index("write") < events.index("lock_exit")
    assert events[-1] == "lock_exit", events


def test_cleanup_dry_run_preserves_bytes_mode_and_directory(tmp_path, monkeypatch):
    module = importlib.import_module("prune.corrections")
    target = tmp_path / "corrections.jsonl"
    _write_jsonl(target, [{"correction_id": "8" * 32, "reflect_status": "applied",
                          "timestamp": "2000-01-01T00:00:00+00:00", "decay_days": 1}])
    target.chmod(0o640)
    monkeypatch.setattr(importlib.import_module("prune"), "DATA_DIR", tmp_path)
    before = (target.read_bytes(), target.stat().st_mode & 0o777, sorted(tmp_path.iterdir()))

    result = module.cleanup_corrections(dry_run=True)

    after = (target.read_bytes(), target.stat().st_mode & 0o777, sorted(tmp_path.iterdir()))
    assert result["removed"] == 1
    assert after == before


@pytest.mark.parametrize(
    "case", ("idiom", "reflect_migration", "subagent", "turn_index", "pj_slug")
)
def test_untouched_legacy_line_keeps_raw_unicode_and_whitespace(case, tmp_path):
    target = tmp_path / "corrections.jsonl"
    legacy = ' { "message" : "\\u65e5" } '
    if case == "idiom":
        module = importlib.import_module("correction_semantic.promote")
        first = {"correction_id": "a" * 32, "promoted_by": "idiom_dict", "idiom_key": "k"}
        invoke = lambda: module.invalidate_idiom_corrections(
            {"k"}, corrections_path=target, dry_run=False
        )
    elif case == "reflect_migration":
        module = importlib.import_module("migrate_reflect_promoted_status")
        first = {"correction_id": "b" * 32, "source": "reflect_confirmed",
                 "reflect_status": "applied"}
        invoke = lambda: module.migrate(target, dry_run=False)
    elif case == "subagent":
        module = importlib.import_module("corrections_subagent_invalidation")
        first = {"correction_id": "c" * 32,
                 "weak_signal_provenance": {"source_path": "/subagents/x"}}
        invoke = lambda: module.invalidate_subagent_contaminated_corrections(
            target, dry_run=False
        )
    elif case == "turn_index":
        module = importlib.import_module("backfill_turn_indices")
        projects = tmp_path / "projects" / "p"
        projects.mkdir(parents=True)
        _write_jsonl(projects / "s.jsonl", [{"type": "user", "timestamp": "2026-01-01T00:00:00Z"}])
        first = {"correction_id": "d" * 32, "session_id": "s",
                 "timestamp": "2026-01-02T00:00:00Z"}
        invoke = lambda: module.backfill_corrections(
            target, tmp_path / "sessions.jsonl", tmp_path / "projects", dry_run=False
        )
    else:
        module = importlib.import_module("pj_slug_backfill")
        first = {"correction_id": "e" * 32, "project_path": "/tmp/project"}
        invoke = lambda: module.backfill(tmp_path, apply=True)
    target.write_text(json.dumps(first, ensure_ascii=False) + "\n" + legacy + "\n", encoding="utf-8")

    invoke()

    assert legacy in target.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "case",
    ("reflect", "idiom", "prune", "reflect_migration", "subagent", "id_migration",
     "turn_index", "pj_slug"),
)
def test_append_waits_at_post_read_pre_replace_syncpoint(case, tmp_path, monkeypatch):
    """N-a-1〜8: writer の read 後に始めた追記が replace 前へ割り込まない。"""
    target = tmp_path / "corrections.jsonl"
    if case == "reflect":
        module = importlib.import_module("reflect")
        _write_jsonl(target, [{"correction_id": "1" * 32, "reflect_status": "pending"}])
        invoke = lambda: module.update_reflect_status(
            target, [module.UpdateTarget(0, ("id", "1" * 32))], "skipped"
        )
    elif case == "idiom":
        module = importlib.import_module("correction_semantic.promote")
        _write_jsonl(target, [{"correction_id": "2" * 32, "promoted_by": "idiom_dict",
                              "idiom_key": "k"}])
        invoke = lambda: module.invalidate_idiom_corrections(
            {"k"}, corrections_path=target, dry_run=False
        )
    elif case == "prune":
        module = importlib.import_module("prune.corrections")
        _write_jsonl(target, [{"correction_id": "3" * 32, "reflect_status": "applied",
                              "timestamp": "2000-01-01T00:00:00+00:00", "decay_days": 1}])
        monkeypatch.setattr(importlib.import_module("prune"), "DATA_DIR", tmp_path)
        invoke = lambda: module.cleanup_corrections(dry_run=False)
    elif case == "reflect_migration":
        module = importlib.import_module("migrate_reflect_promoted_status")
        _write_jsonl(target, [{"correction_id": "4" * 32, "source": "reflect_confirmed",
                              "reflect_status": "applied"}])
        invoke = lambda: module.migrate(target, dry_run=False)
    elif case == "subagent":
        module = importlib.import_module("corrections_subagent_invalidation")
        _write_jsonl(target, [{"correction_id": "5" * 32,
                              "weak_signal_provenance": {"source_path": "/subagents/x"}}])
        invoke = lambda: module.invalidate_subagent_contaminated_corrections(
            target, dry_run=False
        )
    elif case == "id_migration":
        module = importlib.import_module("migrate_correction_id_backfill")
        _write_jsonl(target, [{"message": "legacy"}])
        invoke = lambda: module.migrate(target, dry_run=False)
    elif case == "turn_index":
        module = importlib.import_module("backfill_turn_indices")
        projects = tmp_path / "projects" / "p"
        projects.mkdir(parents=True)
        _write_jsonl(projects / "s.jsonl", [{"type": "user", "timestamp": "2026-01-01T00:00:00Z"}])
        _write_jsonl(target, [{"correction_id": "6" * 32, "session_id": "s",
                              "timestamp": "2026-01-02T00:00:00Z"}])
        invoke = lambda: module.backfill_corrections(
            target, tmp_path / "sessions.jsonl", tmp_path / "projects", dry_run=False
        )
    else:
        module = importlib.import_module("pj_slug_backfill")
        _write_jsonl(target, [{"correction_id": "7" * 32, "project_path": "/tmp/project"}])
        invoke = lambda: module.backfill(tmp_path, apply=True)

    before_replace = threading.Event()
    may_replace = threading.Event()
    writer_errors = []
    if case == "id_migration":
        real_replace = module.os.replace

        def pausing_replace(source, destination):
            if Path(destination).resolve() == target.resolve():
                before_replace.set()
                assert may_replace.wait(5)
            return real_replace(source, destination)

        monkeypatch.setattr(module.os, "replace", pausing_replace)
    else:
        real_write = module.atomic_write_text_preserving_mode

        def pausing_write(path, text):
            if Path(path).resolve() == target.resolve():
                before_replace.set()
                assert may_replace.wait(5)
            return real_write(path, text)

        monkeypatch.setattr(module, "atomic_write_text_preserving_mode", pausing_write)

    def run_writer():
        try:
            invoke()
        except BaseException as error:
            writer_errors.append(error)

    writer = threading.Thread(target=run_writer)
    writer.start()
    assert before_replace.wait(5), f"{case}: writer did not reach pre-replace point"

    from rl_common.correction_id import append_correction_record
    append_done = threading.Event()
    append_status = []

    def run_append():
        append_status.append(
            append_correction_record(target, {"correction_id": "f" * 32, "message": "concurrent"}).status
        )
        append_done.set()

    appender = threading.Thread(target=run_append)
    appender.start()
    assert not append_done.wait(0.2), f"{case}: append bypassed the rewrite sidecar lock"
    may_replace.set()
    writer.join(5)
    appender.join(5)

    assert not writer_errors
    assert not writer.is_alive() and not appender.is_alive()
    assert append_status == ["appended"]
    assert any(
        json.loads(line).get("correction_id") == "f" * 32
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("{")
    )
