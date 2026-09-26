"""Synthetic data only: no personal corpus is opened by these tests."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import holdout691_tools as tool


def row(i, **changes):
    value = dict(source_path=f"/fixture/{i}", line_no=i, session_id=f"session-{i}",
                 timestamp=f"2026-08-12T00:00:{i:02d}Z", text=f"SYNTHETIC-SECRET-{i}",
                 pj_slug="fixture", prev_action=None)
    value.update(changes)
    return value


def jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def test_keys_emit_only_five_fields_and_never_print_text(tmp_path, capsys):
    source = jsonl(tmp_path / "source.jsonl", [row(1)])
    output = tmp_path / "keys.jsonl"
    result = tool.extract_keys(source, output)
    written = output.read_text()
    assert "SYNTHETIC-SECRET" not in written
    assert "SYNTHETIC-SECRET" not in str(result)
    captured = capsys.readouterr()
    assert "SYNTHETIC-SECRET" not in captured.out
    assert "SYNTHETIC-SECRET" not in captured.err
    assert json.loads(written) == {**{k: row(1)[k] for k in
                                      ("source_path", "line_no", "session_id", "timestamp")},
                                   "text_sha256": hashlib.sha256(row(1)["text"].encode()).hexdigest()}
    assert result["rows"] == 1
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("bad", ['{bad json', '{}', json.dumps({**row(1), "text": None}), ""])
def test_keys_fail_closed_on_invalid_row(tmp_path, bad):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(row(0)) + "\n" + bad + "\n")
    output = tmp_path / "keys.jsonl"
    with pytest.raises(ValueError):
        tool.extract_keys(source, output)
    assert not output.exists()


def test_keys_accept_clean_duplicate_free_input(tmp_path):
    source = jsonl(tmp_path / "source.jsonl", [row(0), row(1)])
    output = tmp_path / "keys.jsonl"
    assert tool.extract_keys(source, output)["rows"] == 2
    assert tool.count_duplicates([output]) == {"physical": 0, "logical": 0}


def test_duplicate_checks_both_key_types(tmp_path):
    rows = [row(0), row(1, source_path="/fixture/0", line_no=0),
            row(2, session_id="session-0", timestamp=row(0)["timestamp"], text=row(0)["text"])]
    source = jsonl(tmp_path / "source.jsonl", rows)
    keys = tmp_path / "keys.jsonl"
    tool.extract_keys(source, keys)
    assert tool.count_duplicates([keys]) == {"physical": 1, "logical": 1}


def test_sample_excludes_physical_and_logical_matches_and_is_seeded(tmp_path):
    pop = [row(i) for i in range(12)]
    population = jsonl(tmp_path / "population.jsonl", pop)
    adopted = jsonl(tmp_path / "adopted.jsonl", [row(0, session_id="other-session", text="other-text"),
                                               row(1, source_path="/other", line_no=99)])
    keys = tmp_path / "adopted_keys.jsonl"
    tool.extract_keys(adopted, keys)
    digest = hashlib.sha256(population.read_bytes()).hexdigest()
    first = tool.sample_population(population, [keys], tmp_path / "sample1.jsonl", n=4, seed=13,
                                   population_sha256=digest)
    second = tool.sample_population(population, [keys], tmp_path / "sample2.jsonl", n=4, seed=13,
                                    population_sha256=digest)
    third = tool.sample_population(population, [keys], tmp_path / "sample3.jsonl", n=4, seed=14,
                                   population_sha256=digest)
    assert first == {"remaining": 10, "sampled": 4, "excluded_keys_summary":
                     {"physical": 1, "logical": 1}}
    assert (tmp_path / "sample1.jsonl").read_bytes() == (tmp_path / "sample2.jsonl").read_bytes()
    assert (tmp_path / "sample1.jsonl").read_bytes() != (tmp_path / "sample3.jsonl").read_bytes()
    selected = [json.loads(x) for x in (tmp_path / "sample1.jsonl").read_text().splitlines()]
    assert all(x["source_path"] not in {"/fixture/0", "/fixture/1"} for x in selected)


def test_freeze_fixed_window_read_only_once_and_manifest_has_no_text(tmp_path, monkeypatch):
    db = tmp_path / "utterances.db"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE utterances (source_path TEXT, line_no INTEGER, pj_slug TEXT, "
                "session_id TEXT, timestamp TEXT, text TEXT, prev_action TEXT, source_kind TEXT)")
    for i, kind in [(0, "dialogue"), (1, "dialogue"), (2, "long_paste"),
                    (3, "dialogue"), (4, "dialogue")]:
        item = row(i)
        if i == 3:
            item["timestamp"] = "2026-08-13T00:00:00Z"
        if i == 4:
            item["source_path"] = "/fixture/subagents/4"
        con.execute("INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [item[k] for k in ("source_path", "line_no", "pj_slug", "session_id",
                                       "timestamp", "text", "prev_action")] + [kind])
    con.close()
    original_connect = tool.duckdb.connect
    seen = []

    def checked_connect(path, **kwargs):
        seen.append(kwargs)
        return original_connect(path, **kwargs)

    monkeypatch.setattr(tool.duckdb, "connect", checked_connect)
    dump = tmp_path / "dump.jsonl"
    result = tool.freeze_population(db, dump, "2026-08-13T00:00:00Z")
    assert seen == [{"read_only": True}]
    assert result["population_rows"] == 2
    assert result["population_dump_sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        tool.freeze_population(db, dump, "2026-08-13T00:00:00Z")
    manifest = tmp_path / "manifest.json"
    tool.write_manifest(manifest, until="2026-08-13T00:00:00Z", freeze=result,
                        seed_log=[{"seed": 13, "n": 2}],
                        excluded_keys_summary={"physical": 0, "logical": 0})
    assert "SYNTHETIC-SECRET" not in manifest.read_text()
    assert json.loads(manifest.read_text())["population_rows"] == 2


def test_freeze_rejects_bad_window_and_repo_output(tmp_path):
    with pytest.raises(ValueError):
        tool.freeze_population(tmp_path / "missing.db", tmp_path / "dump.jsonl",
                               "2026-08-12T00:00:00Z")
    with pytest.raises(ValueError):
        tool.extract_keys(tmp_path / "source.jsonl",
                          Path(__file__).resolve().parents[1] / "bench" / "leak.jsonl")


def test_existing_output_survives_freeze_failure(tmp_path):
    db = tmp_path / "empty.db"
    duckdb.connect(str(db)).close()
    output = tmp_path / "existing.jsonl"
    output.write_text("KEEP ME")
    with pytest.raises(FileExistsError):
        tool.freeze_population(db, output, "2026-08-13T00:00:00Z")
    assert output.read_text() == "KEEP ME"


def test_sample_rejects_changed_frozen_population(tmp_path):
    population = jsonl(tmp_path / "population.jsonl", [row(0)])
    with pytest.raises(ValueError, match="hash mismatch"):
        tool.sample_population(population, [], tmp_path / "sample.jsonl", n=1, seed=1,
                               population_sha256="0" * 64)
    assert not (tmp_path / "sample.jsonl").exists()


def test_keys_cli_reports_invalid_input_without_echoing_it(tmp_path):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"text":"SYNTHETIC-SECRET", broken json}\n')
    output = tmp_path / "keys.jsonl"
    process = subprocess.run(
        [sys.executable, str(Path(tool.__file__)), "keys", "--source", str(source),
         "--out", str(output)], capture_output=True, text=True,
    )
    assert process.returncode != 0
    assert "SYNTHETIC-SECRET" not in process.stdout + process.stderr
    assert not output.exists()
