"""hook_drift（他ツール追従 hook の stale_pin 検出）のテスト。

決定論・LLM 非依存なので mock は不要。tmp_path に gstack ディレクトリを模して検査する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import hook_drift  # noqa: E402
from audit.sections_hook import build_hook_drift_section  # noqa: E402


def _make_gstack(tmp_path: Path, *, pinned: str | None, actual: str | None) -> Path:
    """flow-chain.json と .last-setup-version を持つ疑似 ~/.gstack を作る。"""
    gdir = tmp_path / ".gstack"
    gdir.mkdir()
    if pinned is not None:
        (gdir / "flow-chain.json").write_text(
            json.dumps({"gstack_version": pinned, "chain": {}}), encoding="utf-8"
        )
    if actual is not None:
        (gdir / ".last-setup-version").write_text(actual, encoding="utf-8")
    return gdir


# --- check_hook_drift -------------------------------------------------------

def test_gstack_absent_is_not_applicable(tmp_path: Path) -> None:
    """.gstack 自体が無い環境は対象外（applicable=False）。"""
    report = hook_drift.check_hook_drift(gstack_dir=tmp_path / "nonexistent")
    assert report.applicable is False
    assert report.stale_pin is False


def test_flow_chain_absent_is_not_applicable(tmp_path: Path) -> None:
    """.gstack はあるが flow-chain.json が無ければ追従対象が無く対象外。"""
    gdir = _make_gstack(tmp_path, pinned=None, actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is False


def test_versions_match_no_drift(tmp_path: Path) -> None:
    """pinned == actual なら stale_pin なし（applicable だが drift なし）。"""
    gdir = _make_gstack(tmp_path, pinned="1.55.0.0", actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is False
    assert report.minor_gap == 0


def test_stale_pin_detected_with_minor_gap(tmp_path: Path) -> None:
    """pinned が actual より古い → stale_pin、minor gap を算出。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is True
    assert report.pinned_version == "1.47.0.0"
    assert report.actual_version == "1.55.0.0"
    assert report.minor_gap == 8


def test_actual_version_unreadable_cannot_judge(tmp_path: Path) -> None:
    """.last-setup-version が無いと実 version 不明 → 判定不能（stale 断定しない）。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual=None)
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.actual_version is None
    assert report.stale_pin is False  # 不明を stale と誤検知しない


def test_unparseable_version_falls_back_to_string_compare(tmp_path: Path) -> None:
    """version が数値解析できなくても、文字列不一致なら stale とみなす（gap は 0）。"""
    gdir = _make_gstack(tmp_path, pinned="alpha", actual="beta")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is True
    assert report.minor_gap == 0


# --- build_hook_drift_section (observability builder) -----------------------

def test_builder_returns_none_when_not_applicable(tmp_path: Path, monkeypatch) -> None:
    """gstack 不在環境では builder は None（沈黙）。"""
    monkeypatch.setattr(
        hook_drift, "_default_gstack_dir", lambda: tmp_path / "nonexistent"
    )
    assert build_hook_drift_section(tmp_path) is None


def test_builder_emits_ok_line_when_clean(tmp_path: Path, monkeypatch) -> None:
    """version 一致時は『評価したが drift なし ✓』を残す（silence != evaluated）。"""
    gdir = _make_gstack(tmp_path, pinned="1.55.0.0", actual="1.55.0.0")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "✓" in body
    assert "1.55.0.0" in body


def test_builder_emits_warning_on_stale_pin(tmp_path: Path, monkeypatch) -> None:
    """stale 時は ⚠ と両 version、見直し誘導を出す。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual="1.55.0.0")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "⚠" in body
    assert "1.47.0.0" in body
    assert "1.55.0.0" in body


def test_builder_registered_in_observability_contract() -> None:
    """observability contract に hook_drift builder が登録されていること。"""
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "hook_drift" in keys
