"""measurement_bug メタ検査のテスト（#445, advisory / Closes #185）。

複数 PJ の集計値が bit-exact 一致したら測定バグ候補として surface する。
決定（論点5）: 0 / 0.0 / None を除外した非自明値の PJ 間一致のみ検出（FP 回避・precision 優先）。

決定論・LLM 非依存。tmp の DATA_DIR に疑似 growth-state-*.json を置いて算出する。
monkeypatch は文字列ターゲットを避け、import した module オブジェクトを直接 patch する
（order-dependent 失敗の既知 pitfall 準拠）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import measurement_bug  # noqa: E402


def _write_state(data_dir: Path, pj: str, **fields) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"growth-state-{pj}.json").write_text(
        json.dumps({"phase": "bootstrap", "updated_at": "2026-06-12T00:00:00+00:00", **fields}),
        encoding="utf-8",
    )


# ---------- detect_measurement_bug（純関数） ----------

class TestDetectMeasurementBug:
    def test_three_pj_identical_nonzero_value_is_candidate(self):
        metrics_by_pj = {"a": 0.42, "b": 0.42, "c": 0.42}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert len(alarms) == 1
        a = alarms[0]
        assert a["metric"] == "env_score"
        assert a["value"] == 0.42
        assert sorted(a["projects"]) == ["a", "b", "c"]

    def test_two_pj_match_is_ignored(self):
        # AC: 1-2 PJ 一致は無視（≥3 のみ候補）
        metrics_by_pj = {"a": 0.42, "b": 0.42}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert alarms == []

    def test_zero_match_is_not_candidate(self):
        # AC: 0 / 0.0 の一致は候補にしない（未測定・データ不足で正当に揃う #423）
        metrics_by_pj = {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert alarms == []

    def test_int_zero_match_is_not_candidate(self):
        metrics_by_pj = {"a": 0, "b": 0, "c": 0}
        alarms = measurement_bug.detect_measurement_bug({"issues_total": metrics_by_pj})
        assert alarms == []

    def test_none_values_excluded(self):
        # AC: None の一致は候補にしない（明示 assert）
        metrics_by_pj = {"a": None, "b": None, "c": None}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert alarms == []

    def test_none_does_not_block_nonzero_group(self):
        # None 混在でも非ゼロ 3 PJ 一致は検出される
        metrics_by_pj = {"a": 0.55, "b": 0.55, "c": 0.55, "d": None}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert len(alarms) == 1
        assert sorted(alarms[0]["projects"]) == ["a", "b", "c"]

    def test_distinct_values_no_candidate(self):
        metrics_by_pj = {"a": 0.1, "b": 0.2, "c": 0.3}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert alarms == []

    def test_nonzero_int_match_is_candidate(self):
        # 599 が全 PJ で揃った #419 の実例（issues_total）
        metrics_by_pj = {"a": 599, "b": 599, "c": 599}
        alarms = measurement_bug.detect_measurement_bug({"issues_total": metrics_by_pj})
        assert len(alarms) == 1
        assert alarms[0]["value"] == 599

    def test_multiple_metrics_each_evaluated(self):
        alarms = measurement_bug.detect_measurement_bug(
            {
                "env_score": {"a": 0.42, "b": 0.42, "c": 0.42},
                "issues_total": {"a": 10, "b": 20, "c": 30},
            }
        )
        metrics = {a["metric"] for a in alarms}
        assert metrics == {"env_score"}

    def test_float_bit_exact_only(self):
        # 近いが非一致は候補にしない（bit-exact のみ）
        metrics_by_pj = {"a": 0.420000001, "b": 0.42, "c": 0.42}
        alarms = measurement_bug.detect_measurement_bug({"env_score": metrics_by_pj})
        assert alarms == []  # 一致は 2 PJ のみ → 無視

    def test_empty_input_returns_empty(self):
        assert measurement_bug.detect_measurement_bug({}) == []
        assert measurement_bug.detect_measurement_bug({"env_score": {}}) == []


# ---------- collect_cross_pj_metrics（growth-state walk） ----------

class TestCollectCrossPjMetrics:
    def test_walks_growth_state_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)
        # issues_summary は #419 と同じ 5 フィールド契約（total はその合計）
        summ = {"line_violations": 2, "hardcoded_values": 3}
        _write_state(tmp_path, "a", env_score=0.42, issues_summary=summ)
        _write_state(tmp_path, "b", env_score=0.42, issues_summary=summ)
        metrics = measurement_bug.collect_cross_pj_metrics()
        assert metrics["env_score"]["a"] == 0.42
        assert metrics["env_score"]["b"] == 0.42
        assert metrics["issues_total"]["a"] == 5

    def test_missing_data_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path / "nonexistent")
        metrics = measurement_bug.collect_cross_pj_metrics()
        assert metrics == {}

    def test_corrupt_state_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "growth-state-bad.json").write_text("{ not json", encoding="utf-8")
        _write_state(tmp_path, "a", env_score=0.42)
        metrics = measurement_bug.collect_cross_pj_metrics()
        assert metrics["env_score"]["a"] == 0.42
        assert "bad" not in metrics.get("env_score", {})


# ---------- build_measurement_bug_section（observability builder） ----------

class TestBuildMeasurementBugSection:
    def test_returns_none_when_under_three_pj(self, tmp_path, monkeypatch):
        from audit.sections_measurement import build_measurement_bug_section

        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)
        _write_state(tmp_path, "a", env_score=0.42)
        _write_state(tmp_path, "b", env_score=0.42)
        assert build_measurement_bug_section(tmp_path) is None

    def test_surfaces_when_three_pj_identical_nonzero(self, tmp_path, monkeypatch):
        from audit.sections_measurement import build_measurement_bug_section

        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)
        _write_state(tmp_path, "a", env_score=0.42)
        _write_state(tmp_path, "b", env_score=0.42)
        _write_state(tmp_path, "c", env_score=0.42)
        lines = build_measurement_bug_section(tmp_path)
        assert lines is not None
        combined = "\n".join(lines)
        assert "Measurement Bug" in combined
        assert "env_score" in combined
        assert "0.42" in combined

    def test_returns_none_when_all_zero(self, tmp_path, monkeypatch):
        from audit.sections_measurement import build_measurement_bug_section

        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path)
        _write_state(tmp_path, "a", env_score=0.0)
        _write_state(tmp_path, "b", env_score=0.0)
        _write_state(tmp_path, "c", env_score=0.0)
        assert build_measurement_bug_section(tmp_path) is None

    def test_returns_none_when_no_data(self, tmp_path, monkeypatch):
        from audit.sections_measurement import build_measurement_bug_section

        monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path / "empty")
        assert build_measurement_bug_section(tmp_path) is None
