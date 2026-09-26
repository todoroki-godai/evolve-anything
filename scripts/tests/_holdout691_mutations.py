import ast
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
source = (repo / 'scripts/bench/holdout691_tools.py').read_text()
test_file = str(repo / 'scripts/tests/test_holdout691_tools.py')
cases = [
    ('file_text', 'dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'dest.write(json.dumps({**_key(row, has_text=True), "text": row["text"]}, ensure_ascii=False) + "\\n")',
     'test_keys_emit_only_five_fields_and_never_print_text'),
    ('stdout_text', 'dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'print(row["text"])\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'test_keys_emit_only_five_fields_and_never_print_text'),
    ('stderr_text', 'dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'print(row["text"], file=__import__("sys").stderr)\n                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\\n")',
     'test_keys_emit_only_five_fields_and_never_print_text'),
    ('skip_bad_json', 'raise ValueError(f"invalid JSON at line {line_no}") from exc',
     'continue', 'test_keys_fail_closed_on_invalid_row'),
    ('skip_missing_text', 'for row in _json_lines(source):\n                dest.write',
     'for row in _json_lines(source):\n                if "text" not in row:\n                    continue\n                dest.write',
     'test_keys_fail_closed_on_invalid_row'),
    ('miss_logical', 'if not (hit_p or hit_l):', 'if not hit_p:',
     'test_sample_excludes_physical_and_logical_matches_and_is_seeded'),
    ('miss_physical', 'if not (hit_p or hit_l):', 'if not hit_l:',
     'test_sample_excludes_physical_and_logical_matches_and_is_seeded'),
    ('no_exclusion', 'if not (hit_p or hit_l):', 'if True:',
     'test_sample_excludes_physical_and_logical_matches_and_is_seeded'),
    ('ignore_seed', 'sample_random_plus_machinery_oversample(candidates, n, seed)',
     'sample_random_plus_machinery_oversample(candidates, n, 0)',
     'test_sample_excludes_physical_and_logical_matches_and_is_seeded'),
    ('readonly_bypass', 'duckdb.connect(str(db_path), read_only=True)',
     'duckdb.connect(str(db_path), read_only=False)',
     'test_freeze_fixed_window_read_only_once_and_manifest_has_no_text'),
    ('frozen_hash_bypass', 'if _sha256(population) != population_sha256:',
     'if False:', 'test_sample_rejects_changed_frozen_population'),
]

driver = '''
import importlib.util, json, pathlib, sys
import pytest
path, repo, test, target = sys.argv[1:]
spec = importlib.util.spec_from_file_location('holdout691_tools', path)
module = importlib.util.module_from_spec(spec)
sys.modules['holdout691_tools'] = module
spec.loader.exec_module(module)
module.REPO = pathlib.Path(repo)
hit = [0]
def trace(frame, event, arg):
    if event == 'line' and frame.f_code.co_filename == path and frame.f_lineno == int(target):
        hit[0] += 1
    return trace
sys.settrace(trace)
status = pytest.main([test, '-n', '0', '-q'])
sys.settrace(None)
print('MUTATION_PROOF', json.dumps({'status': int(status), 'target_executions': hit[0]}))
sys.exit(0 if status != 0 and hit[0] > 0 else 1)
'''

for name, before, after, selector in cases:
    assert source.count(before) == 1, name
    changed = source.replace(before, after, 1)
    assert ast.dump(ast.parse(source)) != ast.dump(ast.parse(changed)), name
    marker = after.split('\n')[0]
    line = changed[:changed.index(marker)].count('\n') + 1
    if name == 'skip_missing_text':
        line = changed[:changed.index('                    continue\n                dest.write')].count('\n') + 1
    with tempfile.TemporaryDirectory(prefix='holdout691_mut_') as directory:
        path = Path(directory) / 'holdout691_tools.py'
        path.write_text(changed)
        process = subprocess.run([sys.executable, '-c', driver, str(path), str(repo),
                                  f'{test_file}::{selector}', str(line)],
                                 cwd=repo, capture_output=True, text=True)
    proofs = [x for x in process.stdout.splitlines() if x.startswith('MUTATION_PROOF')]
    print(name, 'DIFF=yes', 'PROOF=' + (proofs[-1] if proofs else 'missing'),
          'RUNNER=' + str(process.returncode))
    if process.returncode:
        print(process.stdout[-1800:])
        print(process.stderr[-1000:])
        sys.exit(1)
