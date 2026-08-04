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


def test_is_frozen_reflects_flag_state_when_active(monkeypatch) -> None:
    """凍結中（SHRINK_FREEZE_ACTIVE=True）の挙動: is_frozen() が True。

    値そのもの（モジュールの現在の既定値）でなく機構を検証するため、monkeypatch で
    明示的に True にする。docstring の解除手順（``SHRINK_FREEZE_ACTIVE`` を False へ変更）を
    実行してもこのテストは落ちない（#379 レビュー指摘: 旧テストは実際の既定値を
    ``is True`` で直接 assert しており、解除手順の実行自体を妨げていた）。
    """
    monkeypatch.setattr(sf, "SHRINK_FREEZE_ACTIVE", True)
    assert sf.is_frozen() is True


def test_is_frozen_reflects_flag_state_when_inactive(monkeypatch) -> None:
    """解除中（SHRINK_FREEZE_ACTIVE=False）の挙動: is_frozen() が False。"""
    monkeypatch.setattr(sf, "SHRINK_FREEZE_ACTIVE", False)
    assert sf.is_frozen() is False


def test_assert_no_new_keys_passes_for_subset() -> None:
    # 既存キーのみ（削除方向）は凍結中でも通す。
    sf.assert_no_new_keys({"corrections.jsonl"}, sf.FROZEN_STORES, "store")
    sf.assert_no_new_keys(set(), sf.FROZEN_STORES, "store")  # 全削除も通す


def test_assert_no_new_keys_rejects_new_key_when_frozen(monkeypatch) -> None:
    # 凍結中の挙動を明示 monkeypatch で固定し、モジュールの実際の既定値（解除後は False）に
    # 依存しないようにする。
    monkeypatch.setattr(sf, "SHRINK_FREEZE_ACTIVE", True)
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
        # producer 側正準集合（weak_signals.channels）を基準にする。review_channels.py の
        # REVIEW_CHANNELS/CONTENT_POOR_CHANNELS は「y/n 確認に出すか」の消費側分類にすぎず、
        # 新 channel の producer が review_channels.py への追記を怠っても正準集合には
        # 現れる契約になっている（#379 レビュー指摘・修正3）。
        from weak_signals.channels import WEAK_SIGNAL_CHANNELS

        sf.assert_no_new_keys(
            WEAK_SIGNAL_CHANNELS, sf.FROZEN_WEAK_SIGNAL_CHANNELS, "weak_signal_channel"
        )
