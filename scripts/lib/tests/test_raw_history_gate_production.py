"""raw_history_gate の production tree 実測ゲート（#402 PR-2 段階4・設計正典 §5）。

段階2 は checker 本体 + fixture テストのみだった（未移行 reader が残るため必ず失敗する）。
段階4（reader migration 完了後）で実 repo_root に対して gate を有効化する契約テスト。
allowlist entry が消失した場合も fail する契約（stale_allowlist）も込みで検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import raw_history_gate as gate  # noqa: E402

_REPO_ROOT = _lib_dir.parent.parent


def test_production_tree_has_zero_violations_beyond_allowlist() -> None:
    """業務 reader 7件の移行完了後、production tree の raw read violation は0件
    （allowlist 3件のみが raw read の正当な呼び出し元として matched）。"""
    report = gate.check_raw_history_reads(_REPO_ROOT, allowlist=gate.PRODUCTION_ALLOWLIST)

    assert report.violations == []
    assert report.stale_allowlist == []
    assert report.ok is True


def test_allowlist_has_exactly_three_entries() -> None:
    """設計正典 §1 実測: allowlist は _emit.py の generation 読みと revert entry 検索
    （_apply.py / _entry.py）の3件のみ（業務 reader は全て load_effective_history へ
    移行済みのため、これ以外に raw read が存在してはならない）。"""
    assert len(gate.PRODUCTION_ALLOWLIST) == 3
    assert set(gate.PRODUCTION_ALLOWLIST) == {
        "scripts/lib/evolve_decisions/_emit.py:_read_disk_and_history",
        "scripts/lib/evolve_revert/_apply.py:apply_revert._do",
        "scripts/lib/evolve_revert/_entry.py:find_entry",
    }
