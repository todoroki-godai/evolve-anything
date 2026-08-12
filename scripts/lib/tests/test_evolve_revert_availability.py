"""evolve_revert._availability のユニットテスト（#402 段階4 §3）。

listing 時点（apply 前）の revert 可否判定。apply 時のみ判明する failure
（対象パス解決・hardlink 等・``_target.resolve_target`` の対象）とは別レイヤー。
決定論・LLM 非依存・純粋関数（I/O なし）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

from evolve_revert._availability import (  # noqa: E402
    REASON_BEFORE_TOO_LARGE,
    REASON_LABELS,
    REASON_LANE_UNSUPPORTED,
    REASON_PRE_EXTENSION,
    compute_revert_availability,
)


def _full_entry(**overrides) -> dict:
    """PR-1 パイプライン経由で revert フィールドが完全に付いた accept entry。"""
    base = {
        "id": "p1",
        "human_accepted": True,
        "revert_schema_version": 1,
        "revert_encoding": "zlib+base64",
        "revert_before_b64": "eJw...",
        "revert_unavailable_reason": None,
        "scope": "project",
        "repo_id": "/repo",
        "relative_path": "skills/foo/SKILL.md",
        "after_sha": "deadbeef",
    }
    base.update(overrides)
    return base


class TestComputeRevertAvailabilityAvailable:
    def test_full_entry_is_available(self):
        available, reason = compute_revert_availability(_full_entry())
        assert available is True
        assert reason is None

    def test_global_scope_is_supported(self):
        available, reason = compute_revert_availability(_full_entry(scope="global"))
        assert available is True
        assert reason is None


class TestComputeRevertAvailabilityBeforeTooLarge:
    def test_explicit_reason_wins(self):
        """emit 時の圧縮後サイズ超過（revert_unavailable_reason=before_too_large）は
        revert_schema_version はあっても revert_before_b64 が無い実際の emit 形と一致する。
        """
        entry = _full_entry(
            revert_before_b64=None, revert_unavailable_reason="before_too_large"
        )
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_BEFORE_TOO_LARGE == "before_too_large"


class TestComputeRevertAvailabilityPreExtension:
    def test_missing_schema_version_entirely(self):
        """記録拡張（PR-1）前に採用された entry、または PR-1 パイプラインを経由しない
        writer（optimize.py の save_history_entry / run_loop.py）による entry。"""
        entry = {"id": "old1", "human_accepted": True, "skill_name": "x"}
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION == "pre_extension"

    def test_schema_present_but_before_b64_missing_without_reason(self):
        """スキーマはあるが本文が無く理由も付いていない防御的フォールバック。"""
        entry = _full_entry(revert_before_b64=None, revert_unavailable_reason=None)
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION


class TestComputeRevertAvailabilityLaneUnsupported:
    def test_unsupported_scope_value(self):
        """scope が global/project 以外（現行データでは到達しない予約分岐。ADR-041 が
        remediation/rules/hooks の optimize_history 書込を対象外にしているため）。"""
        entry = _full_entry(scope="rules")
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_LANE_UNSUPPORTED == "lane_unsupported"

    def test_missing_scope_with_full_schema(self):
        entry = _full_entry(scope=None)
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_LANE_UNSUPPORTED


class TestComputeRevertAvailabilitySchemaConsistency:
    """#402-D PR1 §2.7（team-lead 裁定）: schema はあるが ``id``/``after_sha`` が
    欠けている entry は、``apply_revert`` の必須検査（``_apply.py`` の
    ``after_sha``/``id`` 必須チェック）と揃えて listing 時点でも ``pre_extension`` に
    相乗りさせる（新しい理由コードは増やさない——「available=true と表示したのに
    apply が失敗する」という柱4「信頼」を壊す隙間を塞ぐ）。
    """

    def test_missing_id_with_full_schema(self):
        entry = _full_entry()
        del entry["id"]
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION

    def test_empty_id_treated_as_missing(self):
        entry = _full_entry(id="")
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION

    def test_missing_after_sha_with_full_schema(self):
        entry = _full_entry()
        del entry["after_sha"]
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION

    def test_empty_after_sha_treated_as_missing(self):
        entry = _full_entry(after_sha="")
        available, reason = compute_revert_availability(entry)
        assert available is False
        assert reason == REASON_PRE_EXTENSION

    def test_id_and_after_sha_present_with_full_schema_is_available(self):
        """回帰防止: 本チェックが正常系（id/after_sha 両方あり）を誤って弾かないこと。"""
        available, reason = compute_revert_availability(_full_entry())
        assert available is True
        assert reason is None


class TestReasonLabels:
    def test_all_three_reason_codes_have_japanese_labels(self):
        assert set(REASON_LABELS) == {
            REASON_PRE_EXTENSION,
            REASON_LANE_UNSUPPORTED,
            REASON_BEFORE_TOO_LARGE,
        }
        for label in REASON_LABELS.values():
            assert isinstance(label, str) and label
