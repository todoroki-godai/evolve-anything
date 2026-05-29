"""build_unmanaged_pitfalls_section のテスト（決定論・LLM 非依存）。

install != enforcement（オプトイン）のため、育っている pitfalls.md があっても enable
しなければ hook は無反応。audit はこの「未登録だが育っている」状態を advisory 表示し、
evolve のたびに enable 漏れを可視化する。ノイズ抑制のため実エントリ >= 3 のみ対象。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pitfall_registry as reg  # noqa: E402
from audit.sections import build_unmanaged_pitfalls_section  # noqa: E402

_GROWN = """# Pitfalls

## Active Pitfalls

### A
- **Status**: Active

### B
- **Status**: Active

### C
- **Status**: Active
"""

_THIN = """# Pitfalls

## Active Pitfalls

### only one
- **Status**: Active
"""


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_grown_unmanaged_pitfalls_are_reported(tmp_path):
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "docs/pitfalls.md" in combined
    assert "3 entries" in combined
    assert "pitfall-curate" in combined  # enable への誘導


def test_thin_pitfalls_are_filtered_out(tmp_path):
    # エントリ 1 件の書きかけ pitfalls.md はノイズなので出さない
    _write(tmp_path / "docs" / "pitfalls.md", _THIN)
    assert build_unmanaged_pitfalls_section(tmp_path) is None


def test_managed_pitfalls_are_not_reported(tmp_path):
    pf = tmp_path / "docs" / "pitfalls.md"
    _write(pf, _GROWN)
    reg.add_managed(tmp_path, pf)
    # 登録済みは advisory に出さない
    assert build_unmanaged_pitfalls_section(tmp_path) is None


def test_none_when_no_pitfalls(tmp_path):
    assert build_unmanaged_pitfalls_section(tmp_path) is None


def test_non_utf8_file_does_not_crash(tmp_path):
    # 育っている正常ファイル + 壊れた 1 ファイルが混在しても落ちず、正常分のみ出す
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    bad = tmp_path / "legacy" / "pitfalls.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "docs/pitfalls.md" in combined
    assert "legacy/pitfalls.md" not in combined
