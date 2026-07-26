"""Hook runtime resolver and error-record coverage for #268."""
import json
import os
from unittest import mock

import common
import permission_denied
import pytest
import session_store
import stop_failure


def test_resolve_runtime_defaults_to_claude():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert common.resolve_runtime({}) == "claude"


def test_resolve_runtime_uses_valid_env_value():
    with mock.patch.dict(os.environ, {"EVOLVE_RUNTIME": "codex"}, clear=True):
        assert common.resolve_runtime({}) == "codex"


def test_resolve_runtime_valid_payload_precedes_env():
    with mock.patch.dict(os.environ, {"EVOLVE_RUNTIME": "claude"}, clear=True):
        assert common.resolve_runtime({"runtime": "codex"}) == "codex"


def test_resolve_runtime_ignores_invalid_payload_and_env():
    with mock.patch.dict(os.environ, {"EVOLVE_RUNTIME": "other"}, clear=True):
        assert common.resolve_runtime({"runtime": "invalid"}) == "claude"
        assert common.resolve_runtime({"runtime": ["codex"]}) == "claude"


def test_stop_failure_records_default_claude_runtime(patch_data_dir):
    stop_failure.handle_stop_failure({"session_id": "sf-default", "error": "failed"})

    record = json.loads((patch_data_dir / "errors.jsonl").read_text().strip())
    assert record["runtime"] == "claude"


def test_stop_failure_records_explicit_codex_runtime(patch_data_dir):
    stop_failure.handle_stop_failure(
        {"session_id": "sf-codex", "error": "failed", "runtime": "codex"}
    )

    record = json.loads((patch_data_dir / "errors.jsonl").read_text().strip())
    assert record["runtime"] == "codex"


def test_permission_denied_records_default_claude_runtime(patch_data_dir):
    permission_denied.handle_permission_denied(
        {"session_id": "pd-default", "tool_name": "Read"}
    )

    record = json.loads((patch_data_dir / "errors.jsonl").read_text().strip())
    assert record["runtime"] == "claude"


def test_permission_denied_records_explicit_codex_runtime(patch_data_dir):
    permission_denied.handle_permission_denied(
        {"session_id": "pd-codex", "tool_name": "Read", "runtime": "codex"}
    )

    record = json.loads((patch_data_dir / "errors.jsonl").read_text().strip())
    assert record["runtime"] == "codex"


@pytest.mark.skipif(not session_store.HAS_DUCKDB, reason="duckdb is unavailable")
def test_session_store_raw_json_roundtrip_preserves_runtime(patch_data_dir):
    session_store.append(
        {
            "session_id": "roundtrip-codex",
            "timestamp": "2026-07-26T00:00:00+00:00",
            "runtime": "codex",
        }
    )

    assert session_store.ingest() == 1
    assert session_store.query()[0]["runtime"] == "codex"
