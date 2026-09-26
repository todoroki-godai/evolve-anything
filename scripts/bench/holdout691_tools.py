#!/usr/bin/env python3
"""Freeze and sample the #691 holdout without exposing utterance text in reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

NEW_SINCE = "2026-08-12T00:00:00Z"
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MANIFEST = HERE / "holdout691_manifest.json"
KEY_FIELDS = ("source_path", "line_no", "session_id", "timestamp", "text_sha256")
ROW_FIELDS = ("source_path", "line_no", "session_id", "timestamp", "text")
DATA_ROOT = Path.home() / ".claude/evolve-anything/bench/holdout_691"
POPULATION_WHERE = ("source_kind = 'dialogue' AND source_path NOT LIKE '%/subagents/%' "
                    "AND timestamp >= ? AND timestamp < ?")
POPULATION_FILTER = {"sql_where": POPULATION_WHERE, "message_filter": "should_include_message"}
sys.path.insert(0, str(REPO / "scripts" / "lib"))


def _external_output(path: Path, root: Path = DATA_ROOT) -> Path:
    """Only an existing directory under the single holdout root may receive artifacts."""
    root, parent = Path(root), Path(path).parent
    if not root.is_dir() or not parent.is_dir():
        raise ValueError("holdout output directory does not exist")
    if root.is_symlink():
        raise ValueError("holdout root must be a real directory")
    actual_parent = parent.resolve()
    if not any(os.path.samefile(ancestor, root) for ancestor in
               (actual_parent, *actual_parent.parents)):
        raise ValueError("artifact output must be under the holdout root")
    # On case-insensitive volumes samefile alone accepts an alternate spelling.
    for ancestor in (parent, *parent.parents):
        if ancestor == ancestor.parent:
            break
        if ancestor.name not in os.listdir(ancestor.parent):
            raise ValueError("artifact output must use the holdout root's actual spelling")
        if os.path.samefile(ancestor, root):
            break
    return Path(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window(until: str) -> None:
    if not isinstance(until, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", until):
        raise ValueError("until must be a UTC timestamp")
    from datetime import datetime

    try:
        if datetime.fromisoformat(until.replace("Z", "+00:00")) <= datetime.fromisoformat(
            NEW_SINCE.replace("Z", "+00:00")
        ):
            raise ValueError("until must follow since")
    except ValueError as exc:
        raise ValueError("invalid until timestamp") from exc


def _json_lines(path: Path):
    with Path(path).open("r", encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(f"invalid JSON at line {line_no}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"invalid object at line {line_no}")
            yield value


def _key(row: dict[str, Any], *, has_text: bool) -> dict[str, Any]:
    fields = ROW_FIELDS if has_text else KEY_FIELDS
    if any(field not in row for field in fields):
        raise ValueError("missing required field")
    if (not isinstance(row["source_path"], str) or not row["source_path"]
            or type(row["line_no"]) is not int or row["line_no"] < 0
            or not isinstance(row["session_id"], str) or not row["session_id"]
            or not isinstance(row["timestamp"], str) or not row["timestamp"]):
        raise ValueError("invalid key field")
    if has_text:
        if not isinstance(row["text"], str):
            raise ValueError("invalid text field")
        text_sha256 = hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
    else:
        text_sha256 = row["text_sha256"]
        if not isinstance(text_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", text_sha256):
            raise ValueError("invalid text hash")
    return {"source_path": row["source_path"], "line_no": row["line_no"],
            "session_id": row["session_id"], "timestamp": row["timestamp"],
            "text_sha256": text_sha256}


def _identities(key: dict[str, Any]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    return ((key["source_path"], key["line_no"]),
            (key["session_id"], key["timestamp"], key["text_sha256"]))


def freeze_population(db_path: Path, output: Path, until: str, *,
                      root: Path = DATA_ROOT) -> dict[str, Any]:
    """Write the half-open dialogue window once; existing output is never replaced."""
    _window(until)
    output = _external_output(output, root)
    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError("database does not exist")
    count = 0
    max_timestamp = None
    digest = hashlib.sha256()
    created = False
    try:
        import duckdb
        from rl_common.detection import should_include_message

        with output.open("xb") as dest:
            created = True
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                cursor = con.execute(
                    "SELECT source_path, line_no, pj_slug, session_id, timestamp, text, prev_action "
                    f"FROM utterances WHERE {POPULATION_WHERE} "
                    "ORDER BY session_id, timestamp, line_no, source_path",
                    [NEW_SINCE, until],
                )
                names = ("source_path", "line_no", "pj_slug", "session_id", "timestamp",
                         "text", "prev_action")
                while batch := cursor.fetchmany(256):
                    for record in batch:
                        row = dict(zip(names, record))
                        if not should_include_message(row["text"]):
                            continue
                        payload = (json.dumps(row, ensure_ascii=False)
                                   + "\n").encode("utf-8")
                        dest.write(payload)
                        digest.update(payload)
                        count += 1
                        if max_timestamp is None or row["timestamp"] > max_timestamp:
                            max_timestamp = row["timestamp"]
            finally:
                con.close()
            snapshot = {"sha256": _sha256(db_path), "size_bytes": db_path.stat().st_size}
    except Exception:
        if created:
            output.unlink()
        raise
    return {"population_rows": count, "population_dump_sha256": digest.hexdigest(),
            "db_snapshot": snapshot, "max_timestamp": max_timestamp,
            "population_filter": POPULATION_FILTER}


def extract_keys(source: Path, output: Path) -> dict[str, Any]:
    """Stream strict JSONL to five-field key JSONL; never write or print text."""
    output = _external_output(output)
    count = 0
    created = False
    try:
        with output.open("x", encoding="utf-8") as dest:
            created = True
            for row in _json_lines(source):
                dest.write(json.dumps(_key(row, has_text=True), ensure_ascii=False) + "\n")
                count += 1
    except Exception:
        if created:
            output.unlink()
        raise
    return {"rows": count, "source_sha256": _sha256(source)}


def _read_key_sets(paths: list[Path]) -> tuple[set, set]:
    physical, logical = set(), set()
    for path in paths:
        for row in _json_lines(path):
            p, l = _identities(_key(row, has_text=False))
            physical.add(p)
            logical.add(l)
    return physical, logical


def count_duplicates(paths: list[Path]) -> dict[str, int]:
    """Count repeated physical and logical keys across all provided key files."""
    physical, logical = set(), set()
    counts = {"physical": 0, "logical": 0}
    for path in paths:
        for row in _json_lines(path):
            p, l = _identities(_key(row, has_text=False))
            counts["physical"] += p in physical
            counts["logical"] += l in logical
            physical.add(p)
            logical.add(l)
    return counts


def sample_population(population: Path, adopted_keys: list[Path], output: Path, *,
                      n: int, seed: int, population_sha256: str) -> dict[str, Any]:
    """Exclude adopted keys before calling the existing random sampler."""
    output = _external_output(output)
    if type(n) is not int or n < 1 or type(seed) is not int:
        raise ValueError("n must be positive and seed must be an integer")
    if _sha256(population) != population_sha256:
        raise ValueError("frozen population hash mismatch")
    physical, logical = _read_key_sets(adopted_keys)
    candidates = []
    excluded = {"physical": 0, "logical": 0}
    for row in _json_lines(population):
        p, l = _identities(_key(row, has_text=True))
        hit_p, hit_l = p in physical, l in logical
        excluded["physical"] += hit_p
        excluded["logical"] += hit_l
        if not (hit_p or hit_l):
            candidates.append(row)
    from a0_capture_replay import sample_random_plus_machinery_oversample

    selected, _ = sample_random_plus_machinery_oversample(candidates, n, seed)
    created = False
    try:
        with output.open("x", encoding="utf-8") as dest:
            created = True
            for row in selected:
                dest.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        if created:
            output.unlink()
        raise
    return {"remaining": len(candidates), "sampled": len(selected),
            "excluded_keys_summary": excluded}


def write_manifest(path: Path = MANIFEST, *, until: str, freeze: dict[str, Any],
                   seed_log: list[dict[str, int]], excluded_keys_summary: dict[str, int]) -> None:
    """Write only aggregate and hash values to the tracked manifest."""
    _window(until)
    if set(freeze) != {"population_rows", "population_dump_sha256", "db_snapshot"}:
        raise ValueError("invalid freeze summary")
    if (type(freeze["population_rows"]) is not int or freeze["population_rows"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", freeze["population_dump_sha256"])
            or set(freeze["db_snapshot"]) != {"sha256", "size_bytes"}
            or not re.fullmatch(r"[0-9a-f]{64}", freeze["db_snapshot"]["sha256"])
            or type(freeze["db_snapshot"]["size_bytes"]) is not int
            or any(set(item) != {"seed", "n"} or
                   type(item["seed"]) is not int or type(item["n"]) is not int for item in seed_log)
            or set(excluded_keys_summary) != {"physical", "logical"}
            or any(type(x) is not int or x < 0 for x in excluded_keys_summary.values())):
        raise ValueError("invalid manifest summary")
    data = {"since": NEW_SINCE, "until": until, "source_kind": "dialogue", **freeze,
            "seed_log": seed_log, "excluded_keys_summary": excluded_keys_summary}
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--db", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)
    freeze.add_argument("--until", required=True)
    keys = sub.add_parser("keys")
    keys.add_argument("--source", type=Path, required=True)
    keys.add_argument("--out", type=Path, required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--population", type=Path, required=True)
    sample.add_argument("--population-sha256", required=True)
    sample.add_argument("--adopted-keys", type=Path, nargs="*", default=[])
    sample.add_argument("--out", type=Path, required=True)
    sample.add_argument("--n", type=int, required=True)
    sample.add_argument("--seed", type=int, required=True)
    verify = sub.add_parser("verify-keys")
    verify.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = freeze_population(args.db, args.out, args.until)
        elif args.command == "keys":
            result = extract_keys(args.source, args.out)
        elif args.command == "sample":
            result = sample_population(args.population, args.adopted_keys, args.out, n=args.n,
                                       seed=args.seed, population_sha256=args.population_sha256)
        else:
            result = count_duplicates(args.paths)
            if any(result.values()):
                raise ValueError("duplicate keys found")
    except (ValueError, OSError, duckdb.Error) as exc:
        parser.exit(1, f"holdout691: {type(exc).__name__}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
