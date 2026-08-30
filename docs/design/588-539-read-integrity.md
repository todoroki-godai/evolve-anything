# #588 / #539 read-integrity implementation plan

Scope is limited to corrections status updates (#588) and weak-signals read health (#539).

## File-size plan

`skills/reflect/scripts/reflect.py` is already over the 800-line hard limit. The #588
change will therefore keep only compatibility wiring in that file and put physical JSONL
line identification/update logic in a focused module below 800 lines. This is not a general
`reflect.py` split and does not introduce the append-event/fold design owned by #587.

`scripts/lib/weak_signals/store.py` is below the limit. Its read result will retain the
existing list-compatible API while adding the same `readable` / `error` /
`malformed_lines` health contract used by #533, with health kept separately for every
canonical or legacy union source. Existing queue human and JSON output will surface the
degraded state; no store, observability section, adapter, or weak-signal channel is added.

## TDD and verification order

1. Add failing #588 tests with blank and malformed physical lines before the target.
2. Implement source-correction-ID re-identification and run focused tests.
3. Add failing #539 tests for missing, permission failure, partial corruption, healthy
   empty, per-source union health, and human/JSON output.
4. Implement the read-health snapshot and existing queue wiring, then run focused tests.
5. Apply the required negative and positive mutations, restore each mutation, run the full
   `python3 -m pytest -n 0` suite, and commit each meaningful issue unit.
