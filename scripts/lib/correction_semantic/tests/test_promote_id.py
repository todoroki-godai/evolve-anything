import json
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parents[3]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from correction_semantic.promote import promote_signals
from rl_common import resolve_correction_id, validate_correction_id


def test_w2_real_promote_writer_persists_valid_resolvable_id(tmp_path):
    weak = tmp_path / "weak_signals.jsonl"
    corrections = tmp_path / "corrections.jsonl"
    seen = tmp_path / "seen.jsonl"
    signal = {
        "signal_key": "sig-1",
        "channel": "llm_judge",
        "session_id": "s-w2",
        "provenance": {"text": "use the safer path", "reason": "user corrected it"},
        "promoted": False,
    }
    weak.write_text(json.dumps(signal) + "\n", encoding="utf-8")

    result = promote_signals(
        ["sig-1"],
        weak_signals_path=weak,
        corrections_path=corrections,
        seen_path=seen,
        project_path="project",
    )

    assert result["promoted"] == 1
    record = json.loads(corrections.read_text(encoding="utf-8"))
    assert validate_correction_id(record["correction_id"])
    assert resolve_correction_id([record], record["correction_id"]).status == "found"
