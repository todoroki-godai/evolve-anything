"""#595 corrections.jsonl writer の AST/data-flow allowlist 回帰検査。"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = ("scripts", "hooks", "skills", "bin")

APPEND_WRITERS = {
    ("hooks/correction_detect.py", "handle_user_prompt_submit"),
    ("scripts/backfill_preceding_tool_calls.py", "persist_to_corrections"),
    ("scripts/lib/correction_semantic/promote.py", "promote_signals"),
    ("scripts/migrate_reflect_queue.py", "migrate"),
}
REWRITE_WRITERS = {
    ("scripts/lib/backfill_turn_indices.py", "backfill_corrections"),
    ("scripts/lib/correction_semantic/promote.py", "invalidate_idiom_corrections"),
    (
        "scripts/lib/corrections_subagent_invalidation.py",
        "invalidate_subagent_contaminated_corrections",
    ),
    ("scripts/lib/pj_slug_backfill.py", "backfill"),
    ("scripts/lib/prune/corrections.py", "cleanup_corrections"),
    ("scripts/migrate_correction_id_backfill.py", "migrate"),
    ("scripts/migrate_reflect_promoted_status.py", "migrate"),
    ("skills/reflect/scripts/reflect.py", "update_reflect_status"),
}


@dataclass
class Scan:
    append_callers: set[tuple[str, str]]
    lock_users: set[tuple[str, str]]
    uncoordinated_sinks: set[tuple[str, str, int]]


def _call_parts(node: ast.Call) -> tuple[str, ...]:
    def parts(expr):
        if isinstance(expr, ast.Name):
            return (expr.id,)
        if isinstance(expr, ast.Attribute):
            return (*parts(expr.value), expr.attr)
        return ()
    return parts(node.func)


def _imports(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    aliases: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = tuple((node.module or "").split("."))
            for item in node.names:
                aliases[item.asname or item.name] = (*module, item.name)
        elif isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = tuple(item.name.split("."))
    return aliases


def _resolved_call(node: ast.Call, aliases: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    parts = _call_parts(node)
    if parts and parts[0] in aliases:
        return (*aliases[parts[0]], *parts[1:])
    return parts


def _is_corrections_expr(node: ast.AST, tainted: set[str]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "corrections.jsonl" in node.value
    if isinstance(node, ast.Name):
        return node.id in tainted or (
            "correction" in node.id.lower()
            and any(token in node.id.lower() for token in ("path", "file"))
        )
    if isinstance(node, ast.Attribute):
        return _is_corrections_expr(node.value, tainted) or (
            "correction" in node.attr.lower()
            and any(token in node.attr.lower() for token in ("path", "file"))
        )
    return any(_is_corrections_expr(child, tainted) for child in ast.iter_child_nodes(node))


def _write_sink(call: ast.Call, aliases: dict[str, tuple[str, ...]], tainted: set[str]) -> bool:
    name = _resolved_call(call, aliases)
    leaf = name[-1] if name else ""
    if leaf in {"store_write", "store_write_raw", "append_jsonl", "atomic_write_text"}:
        return bool(call.args) and _is_corrections_expr(call.args[0], tainted)
    if leaf in {"replace", "move", "copy", "copy2"}:
        if len(call.args) < 2:
            return False
        destination = call.args[1]
        if (
            isinstance(destination, ast.Call)
            and isinstance(destination.func, ast.Attribute)
            and destination.func.attr in {"with_name", "with_suffix"}
            and any(
                isinstance(arg, ast.JoinedStr)
                and ".bak" in ast.unparse(arg)
                for arg in destination.args
            )
        ):
            return False
        return _is_corrections_expr(destination, tainted)
    if leaf in {"write_text", "write_bytes"} and isinstance(call.func, ast.Attribute):
        return _is_corrections_expr(call.func.value, tainted)
    if leaf in {"open", "fdopen"} and call.args and _is_corrections_expr(call.args[0], tainted):
        modes = [arg.value for arg in call.args[1:2] if isinstance(arg, ast.Constant)]
        modes += [
            keyword.value.value for keyword in call.keywords
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)
        ]
        return any("w" in str(mode) or "a" in str(mode) for mode in modes)
    return False


def scan_source(source: str, path: str = "snippet.py") -> Scan:
    tree = ast.parse(source)
    aliases = _imports(tree)
    append_callers = set()
    lock_users = set()
    uncoordinated = set()
    for function in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        tainted = {
            arg.arg for arg in (*function.args.posonlyargs, *function.args.args)
            if "correction" in arg.arg.lower()
            and any(token in arg.arg.lower() for token in ("path", "file"))
        }
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if value is not None and _is_corrections_expr(value, tainted):
                    tainted.update(
                        target.id for target in targets if isinstance(target, ast.Name)
                    )
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        identity = (path, function.name)
        has_lock = any(_resolved_call(call, aliases)[-1:] == ("corrections_write_lock",) for call in calls)
        if has_lock and function.name != "append_correction_record":
            lock_users.add(identity)
        if any(_resolved_call(call, aliases)[-1:] == ("append_correction_record",) for call in calls):
            if function.name != "append_correction_record":
                append_callers.add(identity)
        for call in calls:
            if _write_sink(call, aliases, tainted) and not has_lock:
                uncoordinated.add((path, function.name, call.lineno))
    return Scan(append_callers, lock_users, uncoordinated)


def scan_repository() -> Scan:
    combined = Scan(set(), set(), set())
    for root in SOURCE_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in relative or path.name.startswith("test_"):
                continue
            result = scan_source(path.read_text(encoding="utf-8"), relative)
            combined.append_callers.update(result.append_callers)
            combined.lock_users.update(result.lock_users)
            combined.uncoordinated_sinks.update(result.uncoordinated_sinks)
    return combined


def test_repository_writer_allowlist_is_exact():
    result = scan_repository()
    assert result.append_callers == APPEND_WRITERS
    assert result.lock_users == REWRITE_WRITERS
    assert result.uncoordinated_sinks == set()


def test_alias_append_boundary_is_detected():
    result = scan_source(
        "from rl_common import append_correction_record as save\n"
        "def new_writer(path, record):\n    save(path, record)\n"
    )
    assert result.append_callers == {("snippet.py", "new_writer")}


def test_direct_and_generic_bypass_writes_are_detected():
    result = scan_source(
        "from rl_common import store_write_raw\n"
        "def direct(root):\n"
        "    target = root / 'corrections.jsonl'\n"
        "    target.write_text('lost')\n"
        "def generic(root):\n"
        "    target = root / 'corrections.jsonl'\n"
        "    store_write_raw(target, {})\n"
    )
    assert {(fn, line) for _, fn, line in result.uncoordinated_sinks} == {
        ("direct", 4), ("generic", 7)
    }
