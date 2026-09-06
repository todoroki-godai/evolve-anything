import json
import sys
from pathlib import Path
from unittest import mock


HOOKS = Path(__file__).resolve().parents[1]
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import common
import correction_detect
import rl_common


def test_w1_real_writer_uses_correction_boundary_and_resolves(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with mock.patch.object(common, "DATA_DIR", data_dir), mock.patch.object(
        rl_common, "DATA_DIR", data_dir
    ), mock.patch.object(common, "store_write", side_effect=AssertionError("W1 bypassed boundary")):
        correction_detect.handle_user_prompt_submit(
            {"session_id": "s-w1", "prompt": "いや、そうじゃなくて別の方法にして"}
        )

    records = [json.loads(line) for line in (data_dir / "corrections.jsonl").read_text().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert rl_common.validate_correction_id(record["correction_id"])
    resolved = rl_common.resolve_correction_id(records, record["correction_id"])
    assert resolved.status == "found"
    assert resolved.record == record
