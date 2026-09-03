"""#595 corrections.jsonl rewrite writer の共有ロック契約。"""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]

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
