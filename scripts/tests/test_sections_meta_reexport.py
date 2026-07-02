"""#122 Phase 4: observability builder を sections_meta へ物理移設した後の re-export 契約。

sections.py（797行）から observability advisory builder 群を sibling `sections_meta.py`
へ移設した。移設した builder / helper が
  (1) 新モジュール audit.sections_meta から、
  (2) 後方互換で従来パス audit.sections から
同一オブジェクトとして引けることを担保する（audit.py 2046→178 split と同じ re-export 契約）。
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# 移設した public builder + private helper（テストが monkeypatch する _load_count_entries を含む）
_MOVED = [
    "build_unmanaged_pitfalls_section",
    "build_belief_blocks_section",
    "build_calibration_drift_section",
    "build_negative_transfer_section",
    "build_glossary_drift_section",
    "_load_count_entries",
    "_load_fitness_evolution",
]


@pytest.mark.parametrize("name", _MOVED)
def test_moved_symbol_reexport_identity(name):
    from audit import sections, sections_meta

    assert hasattr(sections_meta, name), f"{name} が新モジュール sections_meta に存在しない"
    assert hasattr(sections, name), f"{name} が sections から後方互換 re-export されていない"
    # 複製実装でなく同一オブジェクト（re-export）であること
    assert getattr(sections, name) is getattr(sections_meta, name)


def test_report_format_builders_stay_in_sections():
    """report-format 系は sections.py に残置され sections_meta には移設しない。"""
    from audit import sections, sections_meta

    for name in (
        "build_token_consumption_section",
        "build_lsp_suggestion_section",
        "build_corrections_insights_section",
        "_build_test_guard_section",
        "_format_constitutional_report",
    ):
        assert hasattr(sections, name), f"{name} が sections から失われた"
        assert not hasattr(sections_meta, name), f"{name} は sections_meta へ移設対象外"
