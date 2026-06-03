"""eval_saturation コアメトリクスのテスト（#292・決定論・LLM 非依存）。

trigger eval set の飽和兆候（緑でも頑健でない）を eval 実行なしで測る。
利用可能なデータで graceful degrade すること、各飽和シグナルの閾値挙動を検証する。
"""
import json
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import eval_saturation as es  # noqa: E402


def _write_eval_set(d: Path, skill: str, entries):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{skill}.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


def _balanced(pos: int, neg: int):
    return (
        [{"query": f"do {i}", "should_trigger": True} for i in range(pos)]
        + [{"query": f"no {i}", "should_trigger": False} for i in range(neg)]
    )


def test_not_applicable_when_dir_absent(tmp_path):
    """eval-sets dir が無い → 対象外。"""
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path / "nope")
    assert result["applicable"] is False
    assert result["evaluated"] == 0


def test_not_applicable_when_empty(tmp_path):
    """dir はあるが eval set ファイルが無い → 対象外。"""
    (tmp_path).mkdir(parents=True, exist_ok=True)
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path)
    assert result["applicable"] is False


def test_balanced_set_not_saturated(tmp_path):
    """十分な総数 + 高い negative 比率 → 飽和なし。"""
    _write_eval_set(tmp_path, "good", _balanced(7, 8))
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path)
    assert result["applicable"] is True
    assert result["evaluated"] == 1
    assert result["saturated"] == []


def test_low_negative_coverage_flagged(tmp_path):
    """positive ばかり（negative 比率が閾値未満）→ low_negative_coverage で飽和。"""
    _write_eval_set(tmp_path, "posheavy", _balanced(10, 1))
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path)
    flagged = {s["skill"]: s for s in result["saturated"]}
    assert "posheavy" in flagged
    assert "low_negative_coverage" in flagged["posheavy"]["reasons"]


def test_thin_set_flagged(tmp_path):
    """クエリ総数が少ない → thin で飽和。"""
    _write_eval_set(tmp_path, "tiny", _balanced(2, 2))
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path)
    flagged = {s["skill"]: s for s in result["saturated"]}
    assert "tiny" in flagged
    assert "thin" in flagged["tiny"]["reasons"]


def test_easy_negatives_flagged_with_triggers(tmp_path):
    """trigger 語を含む near-miss negative が少ない → easy_negatives で飽和。"""
    entries = (
        [{"query": "deploy the app now", "should_trigger": True} for _ in range(7)]
        + [{"query": f"totally unrelated chat {i}", "should_trigger": False}
           for i in range(8)]
    )
    _write_eval_set(tmp_path, "deployskill", entries)
    result = es.compute_eval_saturation(
        eval_sets_dir=tmp_path,
        triggers_by_skill={"deployskill": ["deploy", "ship"]},
    )
    flagged = {s["skill"]: s for s in result["saturated"]}
    assert "deployskill" in flagged
    assert "easy_negatives" in flagged["deployskill"]["reasons"]


def test_near_miss_negatives_not_flagged_for_easy(tmp_path):
    """negative が trigger 語を含む（境界を突く）→ easy_negatives は立たない。"""
    entries = (
        [{"query": "deploy the app now", "should_trigger": True} for _ in range(7)]
        + [{"query": f"deploy docs only {i}", "should_trigger": False}
           for i in range(8)]
    )
    _write_eval_set(tmp_path, "deployskill", entries)
    result = es.compute_eval_saturation(
        eval_sets_dir=tmp_path,
        triggers_by_skill={"deployskill": ["deploy"]},
    )
    flagged = {s["skill"]: s for s in result["saturated"]}
    assert "deployskill" not in flagged


def test_easy_negatives_skipped_without_triggers(tmp_path):
    """trigger 語が取れない skill では easy_negatives 判定を行わない（graceful degrade）。"""
    entries = (
        [{"query": "x", "should_trigger": True} for _ in range(7)]
        + [{"query": f"y {i}", "should_trigger": False} for i in range(8)]
    )
    _write_eval_set(tmp_path, "unknown", entries)
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path, triggers_by_skill={})
    flagged = {s["skill"]: s for s in result["saturated"]}
    # negative 比率は十分・総数も十分なので飽和なし
    assert "unknown" not in flagged
    assessed = {a["skill"]: a for a in result["assessed"]}
    assert assessed["unknown"]["near_miss_ratio"] is None


def test_corrupt_file_skipped(tmp_path):
    """壊れた JSON は無視され、他の eval set 評価は継続する。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    _write_eval_set(tmp_path, "good", _balanced(7, 8))
    result = es.compute_eval_saturation(eval_sets_dir=tmp_path)
    assert result["applicable"] is True
    assert result["evaluated"] == 1  # broken は除外
