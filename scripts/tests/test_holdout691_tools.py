"""Only synthetic rows and temporary output roots are used here."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import holdout691_tools as tool


def row(i, **changes):
    value = dict(source_path=f"/fixture/{i}", line_no=i, session_id=f"session-{i}",
                 timestamp=f"2026-08-12T00:00:{i:02d}Z", text=f"SYNTHETIC-SECRET-{i}",
                 pj_slug="fixture", prev_action=None)
    value.update(changes)
    return value


def jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def keys(root, name, rows):
    source = jsonl(root / f"{name}.source.jsonl", rows)
    output = root / f"{name}.keys.jsonl"
    tool.extract_keys(source, output, root=root)
    return output


def sample(root, population, output, n=4, seed=13):
    return tool.sample_population(population, output, n=n, seed=seed,
                                  population_sha256=hashlib.sha256(population.read_bytes()).hexdigest(),
                                  root=root)


def cli(*args):
    return subprocess.run([sys.executable, str(Path(tool.__file__)), *map(str, args)],
                          capture_output=True, text=True)


def test_keys_emit_only_five_fields_and_never_print_text(tmp_path, capsys):
    source = jsonl(tmp_path / "source.jsonl", [row(1)])
    output = tmp_path / "a.keys.jsonl"
    result = tool.extract_keys(source, output, root=tmp_path)
    written = output.read_text()
    assert "SYNTHETIC-SECRET" not in written + str(result)
    captured = capsys.readouterr()
    assert "SYNTHETIC-SECRET" not in captured.out + captured.err
    assert json.loads(written) == {**{k: row(1)[k] for k in
                                      ("source_path", "line_no", "session_id", "timestamp")},
                                   "text_sha256": hashlib.sha256(row(1)["text"].encode()).hexdigest()}
    assert result == {"rows": 1, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


@pytest.mark.parametrize("bad", ['{bad json', '{}', json.dumps({**row(1), "text": None}), ""])
def test_keys_fail_closed_on_invalid_row(tmp_path, bad):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(row(0)) + "\n" + bad + "\n")
    output = tmp_path / "a.keys.jsonl"
    with pytest.raises(ValueError):
        tool.extract_keys(source, output, root=tmp_path)
    assert not output.exists()


def test_keys_accept_clean_duplicate_free_input(tmp_path):
    output = keys(tmp_path, "a", [row(0), row(1)])
    assert tool.count_duplicates(output, []) == {"physical": 0, "logical": 0}


def test_verify_keys_cli_counts_new_overlap_and_internal_duplicates_only(tmp_path, monkeypatch, capsys):
    existing_a = keys(tmp_path, "old-a", [row(0)])
    existing_b = keys(tmp_path, "old-b", [row(0)])
    clean = keys(tmp_path, "clean", [row(1)])
    def run_verify(new):
        monkeypatch.setattr(sys, "argv", [str(tool.__file__), "verify-keys", "--new", str(new),
                                           "--existing", str(existing_a), str(existing_b)])
        tool.main()
    run_verify(clean)
    assert json.loads(capsys.readouterr().out) == {"physical": 0, "logical": 0}
    overlap = keys(tmp_path, "overlap", [row(0), row(2)])
    with pytest.raises(SystemExit) as failure:
        run_verify(overlap)
    assert failure.value.code == 1
    internal = keys(tmp_path, "internal", [row(3), row(3)])
    with pytest.raises(SystemExit) as failure:
        run_verify(internal)
    assert failure.value.code == 1
    assert cli("verify-keys", "--new", clean, "--existing", existing_a, existing_b).returncode == 0


def test_sample_excludes_both_keys_uses_all_files_and_seed(tmp_path):
    pop = [row(i) for i in range(12)]
    pop[3]["text"] += " === report end ==="
    population = jsonl(tmp_path / "population.jsonl", pop)
    first_key = keys(tmp_path, "a0", [row(0, session_id="other", text="other")])
    second_key = keys(tmp_path, "holdout682", [row(1, source_path="/other", line_no=99)])
    first = sample(tmp_path, population, tmp_path / "sample1.jsonl")
    second = sample(tmp_path, population, tmp_path / "sample2.jsonl")
    sample(tmp_path, population, tmp_path / "sample3.jsonl", seed=14)
    assert first["remaining"] == 10
    assert first["sampled"] == 4
    assert first["excluded_keys_summary"] == {"physical": 1, "logical": 1}
    assert first["key_files"] == second["key_files"]
    assert first["key_files"] == [
        {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
         "rows": 1, "max_timestamp": row(i)["timestamp"]}
        for p, i in [(first_key, 0), (second_key, 1)]]
    assert (tmp_path / "sample1.jsonl").read_bytes() == (tmp_path / "sample2.jsonl").read_bytes()
    assert (tmp_path / "sample1.jsonl").read_bytes() != (tmp_path / "sample3.jsonl").read_bytes()
    selected = [json.loads(x) for x in (tmp_path / "sample1.jsonl").read_text().splitlines()]
    assert len(selected) == 4
    assert all(x["source_path"] not in {"/fixture/0", "/fixture/1"} for x in selected)
    assert any("=== report end ===" in x["text"] for x in pop)


def test_key_file_max_timestamp_handles_fractional_seconds(tmp_path):
    key = keys(tmp_path, "a0", [row(0, timestamp="2026-08-12T00:00:00Z"),
                                 row(1, timestamp="2026-08-12T00:00:00.500Z")])
    pop = jsonl(tmp_path / "population.jsonl", [row(2)])
    result = sample(tmp_path, pop, tmp_path / "sample.jsonl", n=1)
    assert result["key_files"] == [{"file": key.name, "sha256": hashlib.sha256(key.read_bytes()).hexdigest(),
                                    "rows": 2, "max_timestamp": "2026-08-12T00:00:00.500Z"}]


def test_sample_no_key_files_stops_without_output(tmp_path, monkeypatch):
    pop = jsonl(tmp_path / "population.jsonl", [row(0)])
    out = tmp_path / "sample.jsonl"
    with pytest.raises(ValueError, match="no adopted key files"):
        sample(tmp_path, pop, out)
    assert not out.exists()
    original = tool.sample_population
    monkeypatch.setattr(tool, "sample_population", lambda population, output, **kwargs:
                        original(population, output, root=tmp_path, **kwargs))
    monkeypatch.setattr(sys, "argv", [str(tool.__file__), "sample", "--population", str(pop),
                                   "--population-sha256", hashlib.sha256(pop.read_bytes()).hexdigest(),
                                   "--out", str(out), "--n", "1", "--seed", "1"])
    with pytest.raises(SystemExit) as failure:
        tool.main()
    assert failure.value.code == 1
    assert not out.exists()


@pytest.mark.parametrize("bad", ["bad_hash", "missing_field", "bad_json"])
def test_sample_bad_key_file_fails_without_output(tmp_path, bad):
    key = keys(tmp_path, "a0", [row(1)])
    broken = json.loads(key.read_text())
    if bad == "bad_hash":
        broken["text_sha256"] = "x"
    elif bad == "missing_field":
        del broken["session_id"]
    with key.open("a") as dest:
        dest.write(("bad json" if bad == "bad_json" else json.dumps(broken)) + "\n")
    pop = jsonl(tmp_path / "population.jsonl", [row(0)])
    out = tmp_path / "sample.jsonl"
    with pytest.raises(ValueError):
        sample(tmp_path, pop, out)
    assert not out.exists()


def test_freeze_filters_using_production_function_and_records_window(tmp_path, monkeypatch):
    db = tmp_path / "synthetic.db"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE utterances (source_path TEXT, line_no INTEGER, pj_slug TEXT, "
                "session_id TEXT, timestamp TEXT, text TEXT, prev_action TEXT, source_kind TEXT)")
    cases = [(row(0, timestamp="2026-08-12T00:00:01Z"), "dialogue"),
             (row(1, text=""), "dialogue"),
             (row(2), "long_paste"), (row(3, timestamp="2026-08-13T00:00:00Z"), "dialogue"),
             (row(4, source_path="/fixture/subagents/4"), "dialogue"),
             (row(5, timestamp="2026-08-12T00:00:01.500Z"), "dialogue"),
             (row(6, timestamp="2026-08-12T00:00:02Z", text=""), "dialogue")]
    for item, kind in cases:
        con.execute("INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [item[k] for k in ("source_path", "line_no", "pj_slug", "session_id",
                                       "timestamp", "text", "prev_action")] + [kind])
    con.close()
    original_connect = duckdb.connect
    seen = []
    def checked_connect(path, **kwargs):
        seen.append(kwargs)
        return original_connect(path, **kwargs)
    monkeypatch.setattr(duckdb, "connect", checked_connect)
    dump = tmp_path / "dump.jsonl"
    result = tool.freeze_population(db, dump, "2026-08-13T00:00:00Z", root=tmp_path)
    assert seen == [{"read_only": True}]
    assert result["population_rows"] == 2
    assert result["max_timestamp"] == "2026-08-12T00:00:02Z"
    assert result["population_filter"] == tool.POPULATION_FILTER
    assert result["population_dump_sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert [json.loads(x)["text"] for x in dump.read_text().splitlines()] == [row(0)["text"], row(5)["text"]]
    with pytest.raises(FileExistsError):
        tool.freeze_population(db, dump, "2026-08-13T00:00:00Z", root=tmp_path)
    key = keys(tmp_path, "a0", [row(9)])
    manifest = tmp_path / "manifest.json"
    summary = sample(tmp_path, dump, tmp_path / "sample.jsonl")
    tool.write_manifest(manifest, until="2026-08-13T00:00:00Z", freeze=result,
                        seed_log=[{"seed": 13, "n": 4}],
                        excluded_keys_summary=summary["excluded_keys_summary"],
                        key_files=summary["key_files"])
    data = json.loads(manifest.read_text())
    assert "SYNTHETIC-SECRET" not in manifest.read_text()
    assert data["key_files"][0]["sha256"] == hashlib.sha256(key.read_bytes()).hexdigest()
    assert data["population_filter"] == tool.POPULATION_FILTER


def test_output_guard_rejects_other_locations_for_all_three_writers(tmp_path):
    root = tmp_path / "holdout_691"
    root.mkdir()
    alias = tmp_path / "HOLDOUT_691"
    checkout = tmp_path / "shared_checkout"
    checkout.mkdir()
    db = tmp_path / "missing.db"
    source = jsonl(tmp_path / "source.jsonl", [row(0)])
    population = source
    keys(root, "a0", [row(1)])
    for parent in (tmp_path, alias, checkout, Path(tool.__file__).parent):
        with pytest.raises(ValueError, match="holdout root"):
            tool.freeze_population(db, parent / "dump.jsonl", "2026-08-13T00:00:00Z", root=root)
        with pytest.raises(ValueError, match="holdout root"):
            tool.extract_keys(source, parent / "a.keys.jsonl", root=root)
        with pytest.raises(ValueError, match="holdout root"):
            sample(root, population, parent / "sample.jsonl")
        assert not (parent / "dump.jsonl").exists()
        assert not (parent / "a.keys.jsonl").exists()
        assert not (parent / "sample.jsonl").exists()


def test_output_guard_rejects_symlink_escape_and_symlink_root(tmp_path):
    root = tmp_path / "holdout_691"
    root.mkdir()
    outside = tmp_path / "shared_checkout"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="holdout root"):
        tool.extract_keys(tmp_path / "unused", root / "escape" / "a.keys.jsonl", root=root)
    alias_root = tmp_path / "alias"
    alias_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        tool.extract_keys(tmp_path / "unused", alias_root / "a.keys.jsonl", root=alias_root)
    parent_alias = tmp_path / "parent_alias"
    parent_alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        tool.extract_keys(tmp_path / "unused", parent_alias / "holdout_691" / "a.keys.jsonl",
                          root=parent_alias / "holdout_691")


def test_cli_cannot_override_artifact_root(tmp_path):
    result = cli("keys", "--source", tmp_path / "unused", "--out", tmp_path / "a.keys.jsonl",
                 "--root", tmp_path)
    assert result.returncode != 0
    assert "unrecognized arguments: --root" in result.stderr


def test_existing_output_survives_freeze_failure(tmp_path):
    db = tmp_path / "empty.db"
    duckdb.connect(str(db)).close()
    output = tmp_path / "existing.jsonl"
    output.write_text("KEEP ME")
    with pytest.raises(FileExistsError):
        tool.freeze_population(db, output, "2026-08-13T00:00:00Z", root=tmp_path)
    assert output.read_text() == "KEEP ME"


def test_freeze_removes_partial_output_on_query_error(tmp_path):
    db = tmp_path / "empty.db"
    duckdb.connect(str(db)).close()
    output = tmp_path / "partial.jsonl"
    with pytest.raises(duckdb.Error):
        tool.freeze_population(db, output, "2026-08-13T00:00:00Z", root=tmp_path)
    assert not output.exists()


def test_sample_rejects_changed_population(tmp_path):
    pop = jsonl(tmp_path / "population.jsonl", [row(0)])
    out = tmp_path / "sample.jsonl"
    with pytest.raises(ValueError, match="hash mismatch"):
        tool.sample_population(pop, out, n=1, seed=1, population_sha256="0" * 64,
                               root=tmp_path)
    assert not out.exists()


def test_cli_failures_hide_text_and_write_manifest(tmp_path, monkeypatch, capsys):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"text":"SYNTHETIC-SECRET", broken json}\n')
    out = tmp_path / "a.keys.jsonl"
    original = tool.extract_keys
    monkeypatch.setattr(tool, "extract_keys", lambda source, output:
                        original(source, output, root=tmp_path))
    monkeypatch.setattr(sys, "argv", [str(tool.__file__), "keys", "--source", str(source),
                                   "--out", str(out)])
    with pytest.raises(SystemExit) as failure:
        tool.main()
    assert failure.value.code == 1
    captured = capsys.readouterr()
    assert "SYNTHETIC-SECRET" not in captured.out + captured.err
    assert not out.exists()
    monkeypatch.setattr(tool, "extract_keys", original)
    freeze = {"population_rows": 0, "population_dump_sha256": "0" * 64,
              "db_snapshot": {"sha256": "1" * 64, "size_bytes": 0},
              "max_timestamp": None, "population_filter": tool.POPULATION_FILTER}
    key = keys(tmp_path, "a0", [row(0)])
    freeze_path = tmp_path / "freeze.json"
    sample_path = tmp_path / "sample.json"
    freeze_path.write_text(json.dumps(freeze))
    sample_path.write_text(json.dumps({"excluded_keys_summary": {"physical": 0, "logical": 0},
                                       "key_files": [{"file": key.name, "sha256": hashlib.sha256(key.read_bytes()).hexdigest(),
                                                      "rows": 1, "max_timestamp": row(0)["timestamp"]}]}))
    manifest = tmp_path / "manifest.json"
    result = cli("write-manifest", "--out", manifest, "--until", "2026-08-13T00:00:00Z",
                 "--freeze-result", freeze_path, "--sample-result", sample_path, "--seed", 13, "--n", 1)
    assert result.returncode == 0, result.stderr
    assert json.loads(manifest.read_text())["key_files"][0]["rows"] == 1
