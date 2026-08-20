"""pitfall 候補に使える correction の意味境界を固定する。"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "scripts" / "lib"))

from pitfall_manager.detection import (  # noqa: E402
    is_pitfall_candidate_correction,
)
from rl_common import CORRECTION_PATTERNS  # noqa: E402


def _record(correction_type, **overrides):
    record = {
        "correction_type": correction_type,
        "source": "hook",
        "last_skill": "evolve-anything:docs-refresh",
        "message": "出力を直して",
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "correction_type",
    [
        key
        for key, metadata in CORRECTION_PATTERNS.items()
        if metadata.get("type") == "correction"
    ],
)
def test_accepts_every_direct_hot_hook_correction(correction_type):
    """type 名でなく writer の semantic class=correction を丸ごと受け入れる。"""
    assert is_pitfall_candidate_correction(_record(correction_type)) is True


@pytest.mark.parametrize(
    "correction_type",
    [
        key
        for key, metadata in CORRECTION_PATTERNS.items()
        if metadata.get("type") != "correction"
    ],
)
def test_rejects_non_corrective_hot_hook_signals(correction_type):
    """positive・prospective explicit/guardrail は失敗の根拠ではない。"""
    assert is_pitfall_candidate_correction(_record(correction_type)) is False


@pytest.mark.parametrize("source", ["reflect_confirmed", "idiom_dict"])
def test_rejects_retrospective_semantic_idioms_even_if_human_confirmed(source):
    """朝の確認結果は直前 skill への時系列帰属を持たず、候補を洪水化させない。"""
    assert is_pitfall_candidate_correction(
        _record("semantic_idiom", source=source, sentiment="correction")
    ) is False


@pytest.mark.parametrize("source", ["backfill", "migrate_learnings_queue"])
def test_rejects_machine_generated_records(source):
    assert is_pitfall_candidate_correction(_record("stop", source=source)) is False


def test_legacy_record_without_source_keeps_direct_correction_compatibility():
    assert is_pitfall_candidate_correction(_record("naoshite-request", source=None)) is True


@pytest.mark.parametrize(
    "correction_type",
    [None, "", "unknown", "修正", "x" * 100_000],
)
def test_rejects_missing_unknown_unicode_and_large_types(correction_type):
    assert is_pitfall_candidate_correction(_record(correction_type)) is False


def test_positive_control_metadata_order_does_not_change_meaning(monkeypatch):
    """陽性対照: registry の並び替えは同じ意味なので判定を変えない。"""
    reordered = dict(reversed(list(CORRECTION_PATTERNS.items())))
    monkeypatch.setattr("pitfall_manager.detection.CORRECTION_PATTERNS", reordered)
    assert is_pitfall_candidate_correction(_record("naoshite-request")) is True
    assert is_pitfall_candidate_correction(_record("perfect")) is False


def test_extractor_uses_the_single_source_predicate(monkeypatch):
    """extractor が predicate を迂回して独自 type 判定を持たない。"""
    seen = []

    def reject_all(record):
        seen.append(record["correction_type"])
        return False

    monkeypatch.setattr(
        "pitfall_manager.detection.is_pitfall_candidate_correction", reject_all
    )
    from pitfall_manager import extract_pitfall_candidates

    result = extract_pitfall_candidates([_record("naoshite-request")])
    assert seen == ["naoshite-request"]
    assert result["candidates"] == []
