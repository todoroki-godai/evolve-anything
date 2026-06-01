"""compute_component_transfer() のユニットテスト（#288, arXiv 2605.30621）。

更新コンポーネント（追加スキル）別に既存スキルの成功率 delta を分離して算出する
ablation 視点の検証。compute_negative_transfer（単一転移点）との差分（複数更新の分離・
誤帰属の防止）を回帰ガードする。
"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.usage import compute_component_transfer


class TestComputeComponentTransferBasic:
    def test_single_component_regression_flagged(self):
        """既存スキル ship が追加コンポーネント review の後に回帰 → review が flagged。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
            {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "outcome": "error"},
        ]
        results = compute_component_transfer(usage_data)
        assert len(results) == 1
        c = results[0]
        assert c["component"] == "review"
        assert c["added_ts"] == "2026-01-02T00:00:00Z"
        assert c["negative_transfer"] is True
        assert c["net_delta"] == pytest.approx(-1.0)
        affected = {a["skill_name"]: a for a in c["affected"]}
        assert "ship" in affected
        assert affected["ship"]["before_score"] == pytest.approx(1.0)
        assert affected["ship"]["after_score"] == pytest.approx(0.0)

    def test_clean_component_not_flagged(self):
        """回帰のないコンポーネントは negative_transfer=False。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "success"},
        ]
        results = compute_component_transfer(usage_data)
        assert len(results) == 1
        assert results[0]["component"] == "review"
        assert results[0]["negative_transfer"] is False

    def test_result_fields_present(self):
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
        ]
        results = compute_component_transfer(usage_data)
        assert len(results) == 1
        c = results[0]
        for k in ("component", "added_ts", "net_delta", "negative_transfer", "affected"):
            assert k in c
        for k in ("skill_name", "delta_score", "before_score", "after_score", "negative_transfer"):
            assert k in c["affected"][0]


class TestComponentIsolation:
    """ablation の核心: 回帰が「実際に起こした更新」へ帰属し、誤帰属しないこと。"""

    def test_regression_attributed_to_correct_component_not_earlier(self):
        """beta 追加後に起きた回帰を、より前の alpha に誤帰属しないこと。

        - baseline ship 追加 → alpha 追加（ship は success のまま）→ beta 追加（ship 回帰）。
        - 期待: alpha は回帰なし（after 区間 = [alpha, beta) で ship は success）、
                beta が回帰 flagged。
        - 単一転移点の compute_negative_transfer は after を終端まで取るため、この回帰を
          最初の追加 alpha に誤帰属してしまう（本関数はそれを isolation window で防ぐ）。
        """
        usage_data = [
            # baseline: ship
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
            # component alpha 追加 — その後も ship は success
            {"skill_name": "alpha", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-02T06:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-02T12:00:00Z", "outcome": "success"},
            # component beta 追加 — その後 ship が回帰
            {"skill_name": "beta", "ts": "2026-01-03T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-03T06:00:00Z", "outcome": "error"},
            {"skill_name": "ship", "ts": "2026-01-03T12:00:00Z", "outcome": "error"},
        ]
        results = compute_component_transfer(usage_data)
        cmap = {c["component"]: c for c in results}
        assert "alpha" in cmap
        assert cmap["alpha"]["negative_transfer"] is False, "回帰を alpha に誤帰属している"
        assert "beta" in cmap
        assert cmap["beta"]["negative_transfer"] is True
        # beta の影響に ship が出ている
        beta_skills = {a["skill_name"] for a in cmap["beta"]["affected"]}
        assert "ship" in beta_skills

    def test_net_delta_averages_multiple_affected(self):
        """net_delta は影響を受けた複数既存スキルの delta 平均。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "review", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            # added コンポーネント
            {"skill_name": "impl", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
            # ship: success→error (delta -1.0), review: success→success (delta 0.0)
            {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
            {"skill_name": "review", "ts": "2026-01-03T01:00:00Z", "outcome": "success"},
        ]
        results = compute_component_transfer(usage_data)
        assert len(results) == 1
        # 平均 (-1.0 + 0.0) / 2 = -0.5
        assert results[0]["net_delta"] == pytest.approx(-0.5)


class TestComputeComponentTransferEdgeCases:
    def test_empty_returns_empty(self):
        assert compute_component_transfer([]) == []

    def test_single_skill_returns_empty(self):
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        ]
        assert compute_component_transfer(usage_data) == []

    def test_component_without_before_after_excluded(self):
        """前後データを持つ既存スキルが無いコンポーネントは除外（affected 空）。"""
        usage_data = [
            {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
            # review 追加後に ship のレコードが無い → after なし
            {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        ]
        assert compute_component_transfer(usage_data) == []

    def test_records_without_fields_no_error(self):
        usage_data = [{"skill_name": "ship"}, {}, {"skill_name": "review", "ts": "2026-01-02T00:00:00Z"}]
        assert isinstance(compute_component_transfer(usage_data), list)

    def test_timestamp_field_fallback(self):
        usage_data = [
            {"skill_name": "ship", "timestamp": "2026-01-01T00:00:00Z", "outcome": "success"},
            {"skill_name": "review", "timestamp": "2026-01-02T00:00:00Z", "outcome": "success"},
            {"skill_name": "ship", "timestamp": "2026-01-03T00:00:00Z", "outcome": "error"},
        ]
        results = compute_component_transfer(usage_data)
        assert len(results) == 1
        assert results[0]["component"] == "review"

    def test_window_limits_records(self):
        usage_data = (
            [{"skill_name": "ship", "ts": f"2026-01-01T0{i}:00:00Z", "outcome": "success"} for i in range(5)]
            + [{"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"}]
            + [{"skill_name": "ship", "ts": f"2026-01-03T0{i}:00:00Z", "outcome": "error"} for i in range(3)]
        )
        results = compute_component_transfer(usage_data, window=3)
        assert len(results) == 1
        a = results[0]["affected"][0]
        assert a["before_score"] == pytest.approx(1.0)
        assert a["after_score"] == pytest.approx(0.0)
