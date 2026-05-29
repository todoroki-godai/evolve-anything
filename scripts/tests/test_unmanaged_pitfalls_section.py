"""build_unmanaged_pitfalls_section のテスト（決定論・LLM 非依存）。

install != enforcement（オプトイン）のため、育っている pitfalls.md があっても enable
しなければ hook は無反応。audit はこの「未登録だが育っている」状態を advisory 表示し、
evolve のたびに enable 漏れを可視化する。育った未登録（実エントリ >= 3）は advisory、
それ以外でも pitfalls.md が1件でもあれば「評価したが該当なし ✓」を必ず1行残す
（観測可能性: 沈黙 = 「評価した結果なし」か「配線漏れ」か区別できない問題への対策）。
pitfalls.md が1件も無い PJ のみ None（対象外）。
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
from audit import sections  # noqa: E402
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


def test_thin_pitfalls_emit_evaluated_line(tmp_path):
    # エントリ 1 件の書きかけは advisory に出さないが、評価した事実は 1 行残す
    _write(tmp_path / "docs" / "pitfalls.md", _THIN)
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "✓" in combined
    assert "docs/pitfalls.md" not in combined  # path は出さない（advisory ではない）


def test_managed_pitfalls_emit_all_registered_line(tmp_path):
    pf = tmp_path / "docs" / "pitfalls.md"
    _write(pf, _GROWN)
    reg.add_managed(tmp_path, pf)
    # 登録済みは advisory に出さないが、「すべて登録済み ✓」を 1 行残す
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "✓" in combined
    assert "登録済み" in combined


def test_none_when_no_pitfalls(tmp_path):
    # pitfalls.md が 1 件も無い PJ は対象外（CONTEXT.md 無しと同じ）→ None
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


def test_parser_unavailable_warns_for_unmanaged(tmp_path, monkeypatch):
    # 正準パーサがロードできない時、未登録があれば liveness 判定不可を ⚠ で残し候補を列挙
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    monkeypatch.setattr(sections, "_load_count_entries", lambda: None)
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "⚠" in combined
    assert "判定不可" in combined
    assert "docs/pitfalls.md" in combined


def test_parser_unavailable_reports_all_registered(tmp_path, monkeypatch):
    # 正準パーサがロードできなくても、全登録済みなら ✓ を1行残す
    pf = tmp_path / "docs" / "pitfalls.md"
    _write(pf, _GROWN)
    reg.add_managed(tmp_path, pf)
    monkeypatch.setattr(sections, "_load_count_entries", lambda: None)
    result = build_unmanaged_pitfalls_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "✓" in combined
    assert "登録済み" in combined
