#!/usr/bin/env python3
"""#477-2 配線: evolve の remediation proposable に suppression ledger を適用する。

却下済み提案を filter_suppressed で次回 evolve から除外し、抑制件数を observability 用に
result へ残す（silence != evaluated）。dry-run でも filter は読み取りのみで適用してよいが、
書き込みは一切しない（filter_suppressed は副作用なし）。
"""
import sys
from pathlib import Path

import pytest

_scripts = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_scripts))
_lib = _scripts.parent.parent.parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

import evolve as evolve_mod  # noqa: E402
import remediation.suppression_ledger as sl  # noqa: E402


def _issue(file_, conf=0.95):
    return {
        "type": "line_limit_violation",
        "file": file_,
        "confidence_score": conf,
        "impact_scope": "file",
        "detail": {"lines": 12, "limit": 10},
    }


class TestApplyRemediationSuppression:
    def test_helper_exists(self):
        assert hasattr(evolve_mod, "_apply_remediation_suppression")

    def test_suppressed_item_removed_and_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        keep = _issue("keep.md")
        dropped = _issue("drop.md")
        sl.record_rejection(dropped, slug="proj")

        surviving, suppressed_count = evolve_mod._apply_remediation_suppression(
            [keep, dropped], slug="proj"
        )
        assert surviving == [keep]
        assert suppressed_count == 1

    def test_no_ledger_passes_all_through(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        items = [_issue("a.md"), _issue("b.md")]
        surviving, suppressed_count = evolve_mod._apply_remediation_suppression(
            items, slug="proj"
        )
        assert surviving == items
        assert suppressed_count == 0

    def test_filter_is_read_only_no_write(self, tmp_path, monkeypatch):
        root = tmp_path / "remediation_suppression"
        monkeypatch.setattr(sl, "LEDGER_ROOT", root)
        items = [_issue("a.md")]
        evolve_mod._apply_remediation_suppression(items, slug="proj")
        # filter は読み取りのみ。ledger ファイルを新規作成しない。
        assert not root.exists() or list(root.glob("*.jsonl")) == []

    def test_degrades_gracefully_on_import_failure(self, monkeypatch):
        """suppression_ledger が import できなくても全件 surface（フェーズを壊さない）。"""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *a, **k):
            if name == "remediation.suppression_ledger" or name.endswith("suppression_ledger"):
                raise ImportError("boom")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        items = [_issue("a.md")]
        surviving, suppressed_count = evolve_mod._apply_remediation_suppression(
            items, slug="proj"
        )
        assert surviving == items
        assert suppressed_count == 0


class TestReconcileSurfacedWiring:
    """#494: record_rejection の決定論 fallback（reconcile_surfaced）の配線。"""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        monkeypatch.setattr(sl, "SURFACED_ROOT", tmp_path / "remediation_surfaced")

    def test_persist_writes_surfaced_marker(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        sl.reconcile_surfaced([_issue("x.md")], slug="proj", persist=True)
        assert sl.surfaced_path("proj").exists()

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        root = tmp_path / "remediation_surfaced"
        ledger_root = tmp_path / "remediation_suppression"
        # 2 回呼んでも persist=False なら marker も ledger も作られない
        sl.reconcile_surfaced([_issue("x.md")], slug="proj", persist=False)
        sl.reconcile_surfaced([_issue("x.md")], slug="proj", persist=False)
        assert not root.exists() or list(root.glob("*.json")) == []
        assert not ledger_root.exists() or list(ledger_root.glob("*.jsonl")) == []
