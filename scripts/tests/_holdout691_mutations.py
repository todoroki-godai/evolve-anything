"""Mutation checks on temporary module copies; run only the holdout tools test file."""
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
source = (repo / "scripts/bench/holdout691_tools.py").read_text()
test_file = str(repo / "scripts/tests/test_holdout691_tools.py")
cases = [
    ("file_text", 'for row in _json_lines(source):\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'for row in _json_lines(source):\n                dest.write(json.dumps({**_key(row, has_text=True), "text": row["text"]}, ensure_ascii=False) + "\\n")',
     "test_keys_emit_only_five_fields_and_never_print_text"),
    ("stdout_text", 'for row in _json_lines(source):\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'for row in _json_lines(source):\n                print(row["text"]); dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     "test_keys_emit_only_five_fields_and_never_print_text"),
    ("stderr_text", 'for row in _json_lines(source):\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'for row in _json_lines(source):\n                print(row["text"], file=__import__("sys").stderr); dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     "test_keys_emit_only_five_fields_and_never_print_text"),
    ("skip_bad_json", 'raise ValueError(f"invalid JSON at line {line_no}") from exc',
     'continue', "test_keys_fail_closed_on_invalid_row"),
    ("skip_missing_text", 'for row in _json_lines(source):\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'for row in _json_lines(source):\n                if "text" not in row: continue\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     "test_keys_fail_closed_on_invalid_row"),
    ("miss_logical", "if not (hit_p or hit_l):", "if not hit_p:",
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
    ("miss_physical", "if not (hit_p or hit_l):", "if not hit_l:",
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
    ("no_exclusion", "if not (hit_p or hit_l):", "if True:",
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
    ("ignore_seed", "sample_random_plus_machinery_oversample(candidates, n, seed)",
     "sample_random_plus_machinery_oversample(candidates, n, 0)",
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
    ("readonly_bypass", "duckdb.connect(str(db_path), read_only=True)",
     "duckdb.connect(str(db_path), read_only=False)",
     "test_freeze_keeps_all_dialogue_rows_in_window_and_records_filter"),
    ("frozen_hash_bypass", "if _sha256(population) != population_sha256:",
     "if False:", "test_sample_rejects_changed_population"),
    ("freeze_guard_removed", "output = _external_output(output, root)\n    db_path",
     "output = Path(output)\n    db_path", "test_output_guard_rejects_other_locations_for_all_three_writers"),
    ("sample_guard_removed", "output = _external_output(output, root)\n    if type(n)",
     "output = Path(output)\n    if type(n)", "test_output_guard_rejects_other_locations_for_all_three_writers"),
    ("include_machinery_extra", "selected, _ = sample_random_plus_machinery_oversample(candidates, n, seed)",
     "_r, _e = sample_random_plus_machinery_oversample(candidates, n, seed); selected = _r + _e",
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
    ("verify_keys_never_fails", "if any(result.values()):", "if False:",
     "test_verify_keys_cli_counts_new_overlap_and_internal_duplicates_only"),
    ("adopted_keys_skip_bad_rows", "for row in _json_lines(path):\n            p, l = _identities(_key(row, has_text=False))",
     "for row in _json_lines(path):\n            try:\n                p, l = _identities(_key(row, has_text=False))\n            except ValueError:\n                continue",
     "test_sample_bad_key_file_fails_without_output"),
    ("key_hash_regex_removed", 'if not isinstance(text_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", text_sha256):',
     "if False:", "test_sample_bad_key_file_fails_without_output"),
    ("no_unlink_on_freeze_error", "if created:\n            output.unlink()\n        raise\n    return {\"population_rows\"",
     "if False:\n            output.unlink()\n        raise\n    return {\"population_rows\"",
     "test_freeze_removes_partial_output_on_query_error"),
    ("X1_verify_physical_blind", 'counts["physical"] += p in physical',
     'counts["physical"] += 0', "test_verify_keys_distinguishes_each_identity"),
    ("X2_verify_logical_blind", 'counts["logical"] += l in logical',
     'counts["logical"] += 0', "test_verify_keys_distinguishes_each_identity"),
    ("X5_identity_to_prefix", 'if not any(os.path.samefile(ancestor, root) for ancestor in\n               (actual_parent, *actual_parent.parents)):',
     'if not str(actual_parent).startswith(str(root)):',
     "test_output_guard_rejects_sibling_with_root_prefix"),
    ("population_refilter", 'payload = (json.dumps(row, ensure_ascii=False)',
     'if not __import__("rl_common.detection", fromlist=["should_include_message"]).should_include_message(row["text"]):\n                            continue\n                        payload = (json.dumps(row, ensure_ascii=False)',
     "test_freeze_keeps_all_dialogue_rows_in_window_and_records_filter"),
    ("sample_companion_empty", 'for row in selected:\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'for row in []:\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     "test_sample_writes_selected_keys_and_next_sample_excludes_them"),
    ("nested_keys_allowed", 'if not os.path.samefile(output.parent, root):',
     'if False:', "test_keys_must_be_directly_in_root"),
    ("cli_message_leak", 'f"holdout691: {type(exc).__name__}\\n"',
     'f"holdout691: {type(exc).__name__}: {exc}\\n"',
     "test_cli_freeze_and_sample_hide_exception_message"),
    ("ignore_second_key_file", 'key_files = sorted(Path(root).glob("*.keys.jsonl"))',
     'key_files = sorted(Path(root).glob("*.keys.jsonl"))[:1]',
     "test_sample_excludes_both_keys_uses_all_files_and_seed"),
]

driver = '''
import importlib.util, json, pathlib, sys
import pytest
path, repo, test, target = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(repo) / 'scripts/lib'))
sys.path.insert(0, str(pathlib.Path(repo) / 'scripts/bench'))
spec = importlib.util.spec_from_file_location('holdout691_tools', path)
module = importlib.util.module_from_spec(spec)
sys.modules['holdout691_tools'] = module
spec.loader.exec_module(module)
hit = [0]
def trace(frame, event, arg):
    if event == 'line' and frame.f_code.co_filename == path and frame.f_lineno == int(target):
        hit[0] += 1
    return trace
sys.settrace(trace)
status = pytest.main([test, '-n', '0', '-q', '-p', 'no:cacheprovider'])
sys.settrace(None)
print('MUTATION_PROOF', json.dumps({'status': int(status), 'target_executions': hit[0]}))
sys.exit(0 if status != 0 and hit[0] > 0 else 1)
'''

for name, before, after, selector in cases:
    assert source.count(before) == 1, name
    changed = source.replace(before, after, 1)
    assert ast.dump(ast.parse(source)) != ast.dump(ast.parse(changed)), name
    marker = after.split("\n")[0] or after.split("\n")[1]
    line = changed[:changed.index(marker)].count("\n") + 1
    with tempfile.TemporaryDirectory(prefix="holdout691_mut_") as directory:
        path = Path(directory) / "holdout691_tools.py"
        path.write_text(changed)
        process = subprocess.run([sys.executable, "-c", driver, str(path), str(repo),
                                  f"{test_file}::{selector}", str(line)],
                                 cwd=repo, capture_output=True, text=True)
    proofs = [x for x in process.stdout.splitlines() if x.startswith("MUTATION_PROOF")]
    print(name, "DIFF=yes", "PROOF=" + (proofs[-1] if proofs else "missing"),
          "RUNNER=" + str(process.returncode), flush=True)
    if process.returncode:
        print(process.stdout[-1800:])
        print(process.stderr[-1000:])
        sys.exit(1)
