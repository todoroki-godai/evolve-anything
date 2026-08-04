"""shrink_freeze のテスト（#379 Step 1「新設凍結」）。

縮小方針確定（#379）に伴い、縮小完了までは新 store / observability section /
advisory proposal adapter / weak_signal channel の追加を止める契約テスト。
凍結は「縮小方向（削除）」を妨げず、「拡張方向（新設）」だけを reject する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import shrink_freeze as sf  # noqa: E402


def test_freeze_active_by_default() -> None:
    assert sf.SHRINK_FREEZE_ACTIVE is True
    assert sf.is_frozen() is True


def test_assert_no_new_keys_passes_for_subset() -> None:
    # 既存キーのみ（削除方向）は凍結中でも通す。
    sf.assert_no_new_keys({"corrections.jsonl"}, sf.FROZEN_STORES, "store")
    sf.assert_no_new_keys(set(), sf.FROZEN_STORES, "store")  # 全削除も通す


def test_assert_no_new_keys_rejects_new_key_when_frozen() -> None:
    with pytest.raises(AssertionError, match="#379"):
        sf.assert_no_new_keys(
            {"corrections.jsonl", "brand_new_store.jsonl"}, sf.FROZEN_STORES, "store"
        )


def test_assert_no_new_keys_skips_when_freeze_inactive(monkeypatch) -> None:
    monkeypatch.setattr(sf, "SHRINK_FREEZE_ACTIVE", False)
    assert sf.is_frozen() is False
    # 凍結解除時は新規追加も通す（例外を投げない）。
    sf.assert_no_new_keys({"brand_new_store.jsonl"}, sf.FROZEN_STORES, "store")


class TestLiveStoreRegistryWithinFrozenSnapshot:
    def test_store_registry_no_new_names(self) -> None:
        import store_registry

        live = set(store_registry.declared_store_names())
        sf.assert_no_new_keys(live, sf.FROZEN_STORES, "store")


class TestLiveObservabilityWithinFrozenSnapshot:
    def test_observability_no_new_sections(self) -> None:
        from audit.observability import _OBSERVABILITY_BUILDERS

        live = {key for key, _ in _OBSERVABILITY_BUILDERS}
        sf.assert_no_new_keys(live, sf.FROZEN_OBSERVABILITY_SECTIONS, "observability_section")


class TestLiveAdvisoryProposalAdaptersWithinFrozenSnapshot:
    def test_advisory_proposal_adapters_no_new_detectors(self) -> None:
        import advisory_proposals

        live = set(advisory_proposals.ADVISORY_PROPOSAL_ADAPTERS)
        sf.assert_no_new_keys(
            live, sf.FROZEN_ADVISORY_PROPOSAL_ADAPTERS, "advisory_proposal_adapter"
        )


class TestLiveWeakSignalChannelsWithinFrozenSnapshot:
    def test_weak_signal_channels_no_new_channels(self) -> None:
        from correction_semantic.review_channels import (
            CONTENT_POOR_CHANNELS,
            REVIEW_CHANNELS,
        )

        live = REVIEW_CHANNELS | CONTENT_POOR_CHANNELS
        sf.assert_no_new_keys(live, sf.FROZEN_WEAK_SIGNAL_CHANNELS, "weak_signal_channel")
