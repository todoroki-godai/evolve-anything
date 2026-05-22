"""corrections_insights モジュールのテスト。"""
import json
import sys
from pathlib import Path

import pytest

# importlib モードでは conftest の sys.path が引き継がれないため明示的に追加
_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from corrections_insights import (
    MIN_DISPLAY_RECORDS,
    count_repeated_patterns,
    load_corrections_for_insights,
)


# ---------- fixture helper ----------

def make_corrections(tmp_path: Path, records: list[dict]) -> Path:
    """N 件の corrections レコードを tmp_path に書き込む。"""
    f = tmp_path / "corrections.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return f


RECENT_TS = "2026-05-22T00:00:00+00:00"
OLD_TS = "2020-01-01T00:00:00+00:00"


# ---------- tests: load_corrections_for_insights ----------

def test_load_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "corrections.jsonl"
    f.write_text("", encoding="utf-8")
    result = load_corrections_for_insights(corrections_file=f)
    assert result == []


def test_load_returns_records(tmp_path: Path) -> None:
    records = [{"correction_type": "iya", "message": "x", "timestamp": RECENT_TS}] * 5
    f = make_corrections(tmp_path, records)
    result = load_corrections_for_insights(corrections_file=f)
    assert len(result) == 5


def test_load_filters_by_lookback(tmp_path: Path) -> None:
    records = [
        {"correction_type": "old", "message": "o", "timestamp": OLD_TS},
        {"correction_type": "new", "message": "n", "timestamp": RECENT_TS},
    ]
    f = make_corrections(tmp_path, records)
    result = load_corrections_for_insights(lookback_days=30, corrections_file=f)
    # 古いレコードが除外され、新しいものだけ残る
    assert len(result) == 1
    assert result[0]["correction_type"] == "new"


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    f = tmp_path / "corrections.jsonl"
    f.write_text(
        '{"correction_type": "iya", "message": "ok", "timestamp": "' + RECENT_TS + '"}\n'
        "not-json\n"
        '{"correction_type": "stop", "message": "ok2", "timestamp": "' + RECENT_TS + '"}\n',
        encoding="utf-8",
    )
    result = load_corrections_for_insights(corrections_file=f)
    assert len(result) == 2


# ---------- tests: count_repeated_patterns ----------

def test_empty_file_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "corrections.jsonl"
    f.write_text("", encoding="utf-8")
    result = count_repeated_patterns(corrections_file=f)
    assert result == []


def test_below_threshold_returns_empty(tmp_path: Path) -> None:
    # MIN_DISPLAY_RECORDS=10 未満 → []
    records = [{"correction_type": "iya", "message": "test", "timestamp": RECENT_TS}] * 9
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f)
    assert result == []


def test_repeated_pattern_detected(tmp_path: Path) -> None:
    # 50 件: "iya" 30 件、"stop" 15 件、"no" 5 件
    records = (
        [{"correction_type": "iya", "message": f"msg {i}", "timestamp": RECENT_TS} for i in range(30)]
        + [{"correction_type": "stop", "message": f"stop {i}", "timestamp": RECENT_TS} for i in range(15)]
        + [{"correction_type": "no", "message": f"no {i}", "timestamp": RECENT_TS} for i in range(5)]
    )
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, top_n=3, min_count=3)
    assert len(result) == 3
    assert result[0]["correction_type"] == "iya"
    assert result[0]["count"] == 30


def test_no_error_category_field_handled(tmp_path: Path) -> None:
    # error_category フィールドなし（PR-A1 前の既存レコード）→ .get() で None にフォールバック
    records = [{"correction_type": "iya", "message": "x", "timestamp": RECENT_TS}] * 15
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, min_count=3)
    assert len(result) >= 1
    assert result[0].get("error_category") is None


def test_lookback_filters_old_records(tmp_path: Path) -> None:
    # 古いレコードは lookback_days で除外される
    old = {"correction_type": "iya", "message": "old", "timestamp": OLD_TS}
    recent = {"correction_type": "no", "message": "recent", "timestamp": RECENT_TS}
    records = [old] * 5 + [recent] * 15
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, lookback_days=30, min_count=3)
    types = [r["correction_type"] for r in result]
    assert "no" in types
    # old records は除外されているはず（5件しかないため min_count=3 を超えているが
    # lookback でフィルタ後は 0 件になるため結果に含まれない）
    assert "iya" not in types


def test_top_n_limits_results(tmp_path: Path) -> None:
    # 4 種類あっても top_n=3 なら 3 件だけ
    records = []
    for ct, n in [("iya", 20), ("stop", 15), ("no", 10), ("dont", 8)]:
        records += [{"correction_type": ct, "message": "x", "timestamp": RECENT_TS}] * n
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, top_n=3, min_count=3)
    assert len(result) == 3


def test_min_count_filters_low_frequency(tmp_path: Path) -> None:
    # min_count=3 で 2 件しかないものは除外
    records = (
        [{"correction_type": "iya", "message": "x", "timestamp": RECENT_TS}] * 20
        + [{"correction_type": "rare", "message": "y", "timestamp": RECENT_TS}] * 2
    )
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, min_count=3)
    types = [r["correction_type"] for r in result]
    assert "rare" not in types
    assert "iya" in types


def test_positive_types_excluded(tmp_path: Path) -> None:
    # "perfect" / "great-approach" / "keep-doing" は集計から除外
    records = (
        [{"correction_type": "perfect", "message": "good", "timestamp": RECENT_TS}] * 20
        + [{"correction_type": "great-approach", "message": "good2", "timestamp": RECENT_TS}] * 20
        + [{"correction_type": "iya", "message": "bad", "timestamp": RECENT_TS}] * 10
    )
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, min_count=3)
    types = [r["correction_type"] for r in result]
    assert "perfect" not in types
    assert "great-approach" not in types
    assert "iya" in types


def test_result_has_example_messages(tmp_path: Path) -> None:
    # example_messages に最大 3 件のメッセージが含まれる
    records = [
        {"correction_type": "iya", "message": f"msg_{i}", "timestamp": RECENT_TS}
        for i in range(15)
    ]
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, min_count=3)
    assert len(result) >= 1
    examples = result[0]["example_messages"]
    assert len(examples) <= 3
    assert all(isinstance(m, str) for m in examples)


def test_error_category_most_common(tmp_path: Path) -> None:
    # error_category が複数種類ある場合、最頻値が返る
    records = (
        [{"correction_type": "iya", "message": "x", "error_category": "cat_a", "timestamp": RECENT_TS}] * 8
        + [{"correction_type": "iya", "message": "y", "error_category": "cat_b", "timestamp": RECENT_TS}] * 4
        + [{"correction_type": "iya", "message": "z", "timestamp": RECENT_TS}] * 3  # no error_category
    )
    f = make_corrections(tmp_path, records)
    result = count_repeated_patterns(corrections_file=f, min_count=3)
    assert result[0]["error_category"] == "cat_a"


def test_missing_corrections_file_returns_empty(tmp_path: Path) -> None:
    non_existent = tmp_path / "no_such_file.jsonl"
    result = count_repeated_patterns(corrections_file=non_existent)
    assert result == []
