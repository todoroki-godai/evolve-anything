"""correction_semantic.provenance_weight のテスト（#431 提案3）。

フェーズ昇格カウント（growth_engine の corrections>=10）が human-source のみで
駆動され、機械ノイズ（Stop hook / backfill）で状態が動かないことを保証する。
決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import provenance_weight as pw  # noqa: E402


def test_hook_source_is_machine() -> None:
    assert pw.is_human_correction({"source": "hook"}) is False


def test_backfill_source_is_machine() -> None:
    assert pw.is_human_correction({"source": "backfill"}) is False


def test_stop_hook_correction_is_machine() -> None:
    # source 欠落でも correction_type=stop は機械生成（#431 背景の 8/9 ノイズ）
    assert pw.is_human_correction({"correction_type": "stop"}) is False


def test_reflect_confirmed_source_is_human() -> None:
    assert pw.is_human_correction({"source": "reflect_confirmed"}) is True


def test_missing_source_defaults_machine() -> None:
    # 出所不明は人間扱いしない（保守的・機械ノイズで状態を動かさない）
    assert pw.is_human_correction({}) is False


def test_count_human_corrections_excludes_machine_noise() -> None:
    records = [
        {"source": "hook", "correction_type": "iya"},
        {"source": "backfill", "correction_type": "stop"},
        {"source": "backfill", "correction_type": "stop"},
        {"source": "reflect_confirmed", "correction_type": "idiom"},
    ]
    # 機械ノイズ 3 件は数えず、human-confirmed 1 件のみ
    assert pw.count_human_corrections(records) == 1


def test_count_human_corrections_empty() -> None:
    assert pw.count_human_corrections([]) == 0


def test_known_machine_sources_enumerated() -> None:
    # correction_detect.py / backfill / migrate の source 値が機械側に含まれる
    assert "hook" in pw.MACHINE_SOURCES
    assert "backfill" in pw.MACHINE_SOURCES
    assert "reflect_confirmed" in pw.HUMAN_SOURCES
